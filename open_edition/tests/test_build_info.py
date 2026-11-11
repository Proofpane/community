"""The release and the commit a build says (proofpane_open/build.py): kept in the image as it
is built, from the Dockerfile's build arguments, and never read from git as it runs; GET /api/health says the release
to anyone and the commit to a signed-in account alone, each
"unknown" where the build kept none. A value given to the build that is not of its shape stops it."""

import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

from conftest import signed_up
from proofpane_open import VERSION, build

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
COMMIT = "0123456789abcdef0123456789abcdef01234567"
SCRIPT = os.path.join(ROOT, "proofpane_open", "build.py")
STEP = 'RUN python /app/open_edition/proofpane_open/build.py "$OPEN_EDITION_RELEASE" "$OPEN_EDITION_COMMIT"'


@pytest.fixture
def kept(tmp_path, monkeypatch):
    """Where this test's build keeps them: never the checkout's own proofpane_open/build.json."""
    path = str(tmp_path / "build.json")
    monkeypatch.setattr(build, "PATH", path)
    return path


def health(client, headers=None):
    r = client.get("/api/health", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def owner(client):
    return signed_up(client)


def test_where_the_build_kept_nothing_health_says_unknown(client, kept, owner):
    assert health(client) == {"ok": True, "version": VERSION, "release": "unknown"}
    assert health(client, owner) == {"ok": True, "version": VERSION, "release": "unknown", "commit": "unknown"}


def test_health_says_the_release_to_anyone_and_the_commit_to_a_signed_in_account_alone(client, kept, owner):
    assert build.write("58a", COMMIT) == {"release": "58a", "commit": COMMIT}
    assert health(client) == {"ok": True, "version": VERSION, "release": "58a"}
    assert health(client, owner) == {"ok": True, "version": VERSION, "release": "58a", "commit": COMMIT}
    for given in ("Bearer not.a-sign-in", "Bearer ", "Basic abc", "not even a scheme"):      # none of them signs in
        assert health(client, {"Authorization": given}) == {"ok": True, "version": VERSION, "release": "58a"}, given
    build.write("57", COMMIT[:7])
    assert (health(client, owner)["release"], health(client, owner)["commit"]) == ("57", COMMIT[:7])
    # a sign-in ended by signing out everywhere is none: the release alone
    old = dict(owner)
    r = client.post("/api/account/sign-out-everywhere", headers=owner,
                    json={"confirmation": client.post("/api/account/confirm", headers=owner,
                                                      json={"password": "correct horse battery",
                                                            "change": "sign-out-everywhere"}).json()["confirmation"]})
    assert r.status_code == 200, r.text
    assert "commit" not in health(client, old)


@pytest.mark.parametrize("kept_text, said", [
    ('{"release": "58a", "commit": "' + "a" * 64 + '"}', ("58a", "a" * 64)),          # a sha-256 commit
    ('{"release": "58A", "commit": "' + COMMIT.upper() + '"}', ("unknown", "unknown")),
    ('{"release": "58ab", "commit": "' + "a" * 65 + '"}', ("unknown", "unknown")),
    ('{"release": "12345", "commit": "abcdef"}', ("unknown", "unknown")),              # five digits; six hex digits
    ('{"release": "58a\\n", "commit": "' + COMMIT + '\\n"}', ("unknown", "unknown")),
    ('{"release": "<b>58a</b>", "commit": 1234567}', ("unknown", "unknown")),
    ('{"release": 58}', ("unknown", "unknown")),
    ('["58a", "' + COMMIT + '"]', ("unknown", "unknown")),
    ("not json", ("unknown", "unknown")),
])
def test_what_the_build_kept_is_said_only_in_its_shape(client, kept, owner, kept_text, said):
    with open(kept, "w", encoding="utf-8") as fh:
        fh.write(kept_text)
    got = health(client, owner)
    assert (got["release"], got["commit"]) == said


def test_a_value_not_given_is_kept_as_unknown_and_one_out_of_shape_or_empty_is_refused(kept):
    """The build argument's default, "unknown", is kept; an empty value, which a `$(git rev-parse HEAD)` that failed
    gives, is refused as any other value not of its shape."""
    assert build.write("unknown", "unknown") == build.info() == {"release": "unknown", "commit": "unknown"}
    os.remove(kept)
    for release, commit in (("v58", COMMIT), ("58a", "HEAD"), ("58a", COMMIT[:6]), ("58a ", COMMIT), ("", COMMIT),
                            ("58a", ""), ("", "")):
        with pytest.raises(ValueError):
            build.write(release, commit)
        assert not os.path.exists(kept)                                         # nothing a refusal kept


@pytest.mark.parametrize("what", ["nested too deep", "over the limit", "a folder", "a pipe no one writes to",
                                  "a pipe holding a writer's words", "not UTF-8"])
def test_what_is_at_the_path_is_answered_unknown_whatever_it_is_and_at_once(kept, what):
    """info() answers for anything at its path: nested past the parser's depth, longer than any file
    it writes, not a regular file (a pipe is not waited on, nor read when a writer has put words in it), or not text."""
    held = []
    if what == "a pipe holding a writer's words":           # the words a build keeps, though not in a file it kept
        os.mkfifo(kept)
        held = [os.open(kept, os.O_RDONLY | os.O_NONBLOCK), os.open(kept, os.O_WRONLY | os.O_NONBLOCK)]
        os.write(held[1], json.dumps({"release": "58a", "commit": COMMIT}).encode("ascii"))
    elif what == "nested too deep":
        with open(kept, "w", encoding="utf-8") as fh:
            fh.write("[" * 100_000 + "]" * 100_000)
    elif what == "over the limit":                  # a whole object, and spaces after it to one byte over
        text = json.dumps({"release": "58a", "commit": COMMIT})
        with open(kept, "w", encoding="utf-8") as fh:
            fh.write(text + " " * (build.MAX_BYTES + 1 - len(text)))
        assert os.path.getsize(kept) == build.MAX_BYTES + 1
    elif what == "a folder":
        os.mkdir(kept)
    elif what == "a pipe no one writes to":
        os.mkfifo(kept)
    else:
        with open(kept, "wb") as fh:
            fh.write(b'{"release": "58a", "commit": "\xff"}')
    answered = []
    reader = threading.Thread(target=lambda: answered.append(build.info()), daemon=True)
    reader.start()
    reader.join(10)
    for fd in held:
        os.close(fd)
    assert answered == [{"release": "unknown", "commit": "unknown"}]


def test_a_file_at_the_limit_is_read(kept):
    text = json.dumps({"release": "58a", "commit": COMMIT})
    with open(kept, "w", encoding="utf-8") as fh:
        fh.write(text + " " * (build.MAX_BYTES - len(text)))
    assert os.path.getsize(kept) == build.MAX_BYTES
    assert build.info() == {"release": "58a", "commit": COMMIT}


def test_the_step_as_the_image_runs_it_keeps_them_where_info_reads_them(tmp_path):
    """Two arguments, as the Dockerfile gives them, in a copy laid out as the image is: the values are kept beside
    build.py, where the server's info() reads them."""
    copy = tmp_path / "proofpane_open"
    copy.mkdir()
    shutil.copy(SCRIPT, copy / "build.py")
    done = subprocess.run([sys.executable, str(copy / "build.py"), "58a", COMMIT], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    read = subprocess.run([sys.executable, "-c", "import build, json; print(json.dumps(build.info()))"],
                          cwd=copy, capture_output=True, text=True, timeout=60)
    assert json.loads(read.stdout) == {"release": "58a", "commit": COMMIT}, read.stderr
    os.remove(copy / "build.json")
    for given in ("", "HEAD"):                                  # an empty value too stops it, and keeps nothing
        done = subprocess.run([sys.executable, str(copy / "build.py"), "58a", given], capture_output=True, text=True,
                              timeout=60)
        assert done.returncode != 0 and "OPEN_EDITION_COMMIT is not a commit" in done.stderr
        assert not (copy / "build.json").exists()


def test_the_image_s_step_keeps_them_and_stops_on_one_out_of_shape(tmp_path):
    path = str(tmp_path / "build.json")
    done = subprocess.run([sys.executable, SCRIPT, "58a", COMMIT, path], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == {"release": "58a", "commit": COMMIT}
    os.remove(path)
    done = subprocess.run([sys.executable, SCRIPT, "58a", "HEAD", path], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and "OPEN_EDITION_COMMIT is not a commit" in done.stderr
    assert not os.path.exists(path)


def test_the_dockerfile_keeps_them_last_from_its_build_arguments_and_reads_no_git():
    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]
    final = lines[max(i for i, line in enumerate(lines) if line.startswith("FROM ")):]       # the image's own stage
    at = final.index(STEP)
    assert final[at - 2:at] == ["ARG OPEN_EDITION_RELEASE=unknown", "ARG OPEN_EDITION_COMMIT=unknown"]
    assert not any(line.startswith(("COPY", "ADD", "RUN")) for line in final[at + 1:])   # nothing after it changes it
    assert not any("git" in line.split() or "git " in line for line in final)
    # no other line of the stage names the two (an ENV of either name would override what the build is given)
    named = [line for line in final if "OPEN_EDITION_RELEASE" in line or "OPEN_EDITION_COMMIT" in line]
    assert named == final[at - 2:at + 1]


def test_the_build_context_holds_no_repository_and_no_checkout_s_kept_build():
    """The context lets through only what the image needs: no .git anywhere, and no build.json a run of build.py
    left in a checkout."""
    with open(os.path.join(ROOT, "Dockerfile.dockerignore"), encoding="utf-8") as fh:
        rules = [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]
    assert rules[0] == "*"
    assert not any(rule.startswith("!") and ".git" in rule for rule in rules)
    assert "open_edition/proofpane_open/build.json" in rules
