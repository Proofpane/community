"""GitHub in the workspace: the owner signs in with GitHub's own CLI in a session's terminal, git then clones their
private repositories through gh's credential helper, and the page offers their repositories to clone with one tap,
from what gh answers. A shell or the sign-in may start without a repository. A stand-in `gh` answers as the real one would: signed out until its sign-in has run."""

import json
import os
import stat
import sys
import time

import pytest

from proofpane_workspace import workspace
from workspace_fixtures import Agent, make_repo, next_control, read_until

REPOS = [
    {"full_name": "acme/secret-app", "private": True, "description": "the private one",
     "pushed_at": "2026-09-27T08:09:10Z"},
    {"full_name": "acme/demo", "private": False, "description": None, "pushed_at": "yesterday"},
    {"full_name": "../escape", "private": True},
    {"name": "no full name"},
]


# what the real gh prints for its web sign-in with its input elsewhere (seen in the image, 2026-09-28), then it waits
# for the code to be entered; the stand-in waits for a file the test makes
DEVICE = ("                printf '\\033[0;33m!\\033[0m First copy your one-time code: \\033[0;1;39mABCD-1234\\033[0m\\n'\n"
          "                echo 'Open this URL to continue in your web browser: https://github.com/login/device'\n"
          '                while [ ! -f "$HOME/.gh-authorized" ]; do sleep 0.1; done\n')


def _fake_gh(bin_dir, device=False):
    """A `gh` on the PATH: `auth login` marks the home signed in; `api user` and `api ... user/repos` answer only
    then. (A real clone through gh's credential helper needs GitHub; the agent's clone takes https only, so it is not
    stood in for here.) Given the terminal as its input, `auth login` fails: the real one asks its questions there,
    and a browser that attached later answered them with a terminal's stale replies, which ended the sign-in with
    status 1. With its input elsewhere the real one asks nothing; the Docker checks run it."""
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n"
                  'case "$1 $2" in\n'
                  '  "auth login") [ -t 0 ] && { echo "could not prompt: the input is a terminal" >&2; exit 1; }\n'
                  + (DEVICE if device else "") +
                  '                touch "$HOME/.gh-signed-in"; echo "Logged in as octocat"; exit 0 ;;\n'
                  '  "auth setup-git") exit 0 ;;\n'
                  "esac\n"
                  '[ -f "$HOME/.gh-signed-in" ] || { echo "not logged in" >&2; exit 1; }\n'
                  'case "$*" in\n'
                  '  "api user --jq .login") echo octocat; exit 0 ;;\n'
                  f"  \"api -X GET user/repos\"*) printf '%s' '{json.dumps(REPOS)}'; exit 0 ;;\n"
                  "esac\n"
                  "exit 2\n")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def ghagent(tmp_path):
    _fake_gh(tmp_path / "bin")
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    a.home = home
    yield a
    a.close()


def test_a_shell_starts_without_a_repository_in_the_workspace_s_root(ghagent, tmp_path):
    r = ghagent.http.post("/sessions", json={"program": "shell", "task": "look around"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["repo"] is None and s["branch"] is None and s["base"] is None and s["running"] is True
    with ghagent.terminal(s["id"]) as ws:
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"echo here=$(pwd)\n")
        assert f"here={os.path.realpath(tmp_path / 'ws')}".encode() in read_until(ws, b"here=/")
    r = ghagent.http.post(f"/sessions/{s['id']}/finish", json={"author_name": "a", "author_email": "a@b"})
    assert r.status_code == 400 and "no change to finish" in r.json()["detail"]
    assert ghagent.http.delete(f"/sessions/{s['id']}").json() == {"removed": s["id"], "branch": None, "branch_kept": None}


@pytest.mark.parametrize("program", ["claude", "codex"])
def test_claude_and_codex_work_in_a_repository(ghagent, program):
    r = ghagent.http.post("/sessions", json={"program": program, "task": "t"})
    assert r.status_code == 400 and ("clone one first" in r.json()["detail"] or "not installed" in r.json()["detail"])


def test_the_github_sign_in_runs_gh_s_own_flow_to_the_end(ghagent):
    assert ghagent.http.get("/github").json() == {"installed": True, "user": None, "repos": [], "capped": False}
    s = ghagent.http.post("/sessions", json={"program": "github", "task": "Sign in to GitHub"}).json()
    assert s["program"] == "github" and s["repo"] is None
    with ghagent.terminal(s["id"]) as ws:
        assert next_control(ws) == {"type": "ready"}
        out = read_until(ws, b"This terminal can be closed.")
    assert b"Logged in as octocat" in out and b"GitHub is signed in for this workspace." in out


def test_a_sign_in_that_ended_before_anyone_attached_still_shows_what_it_said(ghagent):
    # a race: the stand-in gh finishes in milliseconds, as a
    # real one does when it is already signed in; waiting for the end makes the late attach certain
    s = ghagent.http.post("/sessions", json={"program": "github", "task": "Sign in to GitHub"}).json()
    deadline = time.time() + 20
    while ghagent.http.get(f"/sessions/{s['id']}").json()["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
    with ghagent.terminal(s["id"]) as ws:
        assert next_control(ws) == {"type": "ready"}
        assert b"GitHub is signed in for this workspace." in read_until(ws, b"This terminal can be closed.")
        assert next_control(ws) == {"type": "exit", "code": 0}


@pytest.mark.parametrize("line, code", [
    (b"! First copy your one-time code: ABCD-1234\r\n", "ABCD-1234"),
    # as the real gh prints it in the image (2026-09-28): the code in bold
    (b"\x1b[0;33m!\x1b[0m First copy your one-time code: \x1b[0;1;39mWXYZ-98K6\x1b[0m\r\n", "WXYZ-98K6"),
    (b"one-time code: ABCD-12345", None), (b"one-time code: ABCDE-1234", None), (b"one-time code: abcd-1234", None),
    (b"one-time code:ABCD-1234", None), (b"first copy your code: ABCD-1234", None)])
def test_the_code_is_read_only_from_gh_s_own_line(line, code):
    found = workspace.DEVICE_CODE.findall(line)
    assert (found[-1].decode() if found else None) == code


def test_a_sign_in_s_code_is_on_its_session_while_gh_waits_and_only_then(tmp_path):
    # the page shows the code and a link, so a sign-in needs no terminal
    _fake_gh(tmp_path / "bin", device=True)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    try:
        shell = a.http.post("/sessions", json={"program": "shell", "task": "t"}).json()
        with a.terminal(shell["id"]) as ws:           # only a sign-in's output is read for a code
            ws.send(b"echo 'one-time code: ''WXYZ-9876'\n")
            read_until(ws, b"code: WXYZ-9876")
        s = a.http.post("/sessions", json={"program": "github", "task": "Sign in to GitHub"}).json()
        deadline = time.time() + 20
        while a.http.get(f"/sessions/{s['id']}").json()["device_code"] is None:
            assert time.time() < deadline
            time.sleep(0.05)
        codes = {x["program"]: x["device_code"] for x in a.http.get("/sessions").json()["sessions"]}
        assert codes == {"shell": None, "github": "ABCD-1234"}
        assert a.http.get("/github").json()["user"] is None
        (home / ".gh-authorized").touch()               # the code was entered at github.com/login/device
        while a.http.get(f"/sessions/{s['id']}").json()["running"]:
            assert time.time() < deadline
            time.sleep(0.05)
        got = a.http.get(f"/sessions/{s['id']}").json()
        assert (got["exit_code"], got["device_code"]) == (0, None)
        assert a.http.get("/github").json()["user"] == "octocat"
    finally:
        a.close()


def test_the_repositories_are_listed_with_what_the_workspace_has_cloned(ghagent, tmp_path):
    s = ghagent.http.post("/sessions", json={"program": "github", "task": "Sign in to GitHub"}).json()
    with ghagent.terminal(s["id"]) as ws:
        read_until(ws, b"This terminal can be closed.")
    got = ghagent.http.get("/github").json()
    assert got["user"] == "octocat"
    assert got["repos"] == [
        {"full_name": "acme/secret-app", "private": True, "url": "https://github.com/acme/secret-app.git",
         "description": "the private one", "cloned": False, "pushed": "2026-09-27"},
        {"full_name": "acme/demo", "private": False, "url": "https://github.com/acme/demo.git", "description": "",
         "cloned": False, "pushed": None}] and got["capped"] is False
    # a clone of it (as the Clone button makes, through gh's credential helper) is marked, and labelled as GitHub's
    make_repo(str(tmp_path / "ws" / "repos" / "secret-app"), origin="https://github.com/acme/secret-app.git")
    assert [x["cloned"] for x in ghagent.http.get("/github").json()["repos"]] == [True, False]
    assert {r["name"]: r["label"] for r in ghagent.http.get("/repos").json()["repos"]} == {"secret-app":
                                                                                         "acme/secret-app"}


PAGED = r"""#!{python}
import json, os, sys
# a signed-in gh whose user has {total} repositories, most recently pushed first, served a page at a time
if sys.argv[1:4] == ["api", "user", "--jq"]:
    print("octocat")
    sys.exit(0)
if sys.argv[1:4] == ["api", "-X", "GET"] and sys.argv[4] == "user/repos":
    fields = dict(a.split("=", 1) for a in sys.argv[5:] if "=" in a)
    size, page = int(fields["per_page"]), int(fields["page"])
    names = range((page - 1) * size, min(page * size, {total}))
    with open(os.path.join(os.environ["HOME"], "pages-asked"), "a") as fh:
        fh.write(fields["page"] + "\n")
    print(json.dumps([{{"full_name": "acme/repo-%04d" % i, "private": i % 2 == 0,
                        "pushed_at": "2026-09-%02dT00:00:00Z" % (28 - i % 28)}} for i in names]))
    sys.exit(0)
sys.exit(2)
"""


@pytest.mark.parametrize("total, pages, capped", [(103, ["1", "2"], False), (1000, [str(p) for p in range(1, 11)], True),
                                                  (1500, [str(p) for p in range(1, 11)], True), (0, ["1"], False)])
def test_every_page_of_repositories_up_to_ten_is_listed_and_a_cap_is_said(tmp_path, total, pages, capped):
    # an account can have more repositories than one page; the page lets a person find one by name
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(PAGED.format(python=sys.executable, total=total))
    (bin_dir / "gh").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    try:
        got = a.http.get("/github").json()
    finally:
        a.close()
    assert (got["user"], len(got["repos"]), got["capped"]) == ("octocat", min(total, 1000), capped)
    assert (home / "pages-asked").read_text().split() == pages
    if total:
        assert got["repos"][0] == {"full_name": "acme/repo-0000", "private": True, "url":
                                   "https://github.com/acme/repo-0000.git", "description": "", "cloned": False,
                                   "pushed": "2026-09-28"}


def test_without_gh_the_page_is_told_so(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("git", "sh"):
        for d in os.environ["PATH"].split(":"):
            if os.path.exists(os.path.join(d, tool)):
                os.symlink(os.path.join(d, tool), bare / tool)
                break
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": str(bare)})
    try:
        assert a.http.get("/github").json() == {"installed": False, "user": None, "repos": [], "capped": False}
        r = a.http.post("/sessions", json={"program": "github", "task": "Sign in to GitHub"})
        assert r.status_code == 400 and "gh is not installed" in r.json()["detail"]
    finally:
        a.close()


def test_the_app_passes_github_s_answer_and_a_session_without_a_repository(client, monkeypatch, tmp_path, env):
    from conftest import signed_up
    home = tmp_path / "apphome"
    home.mkdir()
    _fake_gh(tmp_path / "appbin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{tmp_path / 'appbin'}:{os.environ['PATH']}")
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    from proofpane_open.ade import workspaces
    try:
        h = signed_up(client)
        assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
        assert client.get("/api/ade/workspace/github", headers=h).json() == {"installed": True, "user": None,
                                                                             "repos": [], "capped": False}
        r = client.post("/api/ade/workspace/sessions", headers=h, json={"program": "github", "task": "Sign in"})
        assert r.status_code == 200 and r.json()["repo"] is None, r.text
        r = client.post("/api/ade/workspace/sessions", headers=h, json={"program": "claude", "task": "t"})
        assert r.status_code == 400
    finally:
        workspaces.shutdown()
