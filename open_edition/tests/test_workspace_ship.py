"""Shipping a session (workspace.ship): its committed branch pushed to origin and a pull request opened with gh, the
existing one given back rather than a second, nothing shipped before something is committed."""

import json
import os
import stat
import subprocess

import pytest

from workspace_fixtures import Agent, git, make_repo

PR = "https://github.com/acme/demo/pull/7"


def _fake_gh(bin_dir, log):
    """`gh pr create`: the first time it records its arguments and the description it was given and prints the new
    pull request's address; after that it says, as the real one does, that one exists for the branch."""
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n"
                  'if [ "$1 $2" = "pr create" ]; then\n'
                  f'  if [ -f "{log}.made" ]; then\n'
                  f'    echo "a pull request for branch \\"x\\" into branch \\"main\\" already exists:" >&2; echo "{PR}" >&2; exit 1\n'
                  "  fi\n"
                  f'  printf "%s\\n" "$@" > "{log}.args"; cat > "{log}.body"; touch "{log}.made"\n'
                  f'  echo "{PR}"; exit 0\n'
                  "fi\n"
                  "exit 2\n")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def shipper(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _fake_gh(tmp_path / "bin", tmp_path / "gh")
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    repo = str(tmp_path / "ws" / "repos" / "demo")
    make_repo(repo, origin=str(origin))
    git("push", "-q", "origin", "main", cwd=repo)
    a.origin, a.log = str(origin), tmp_path / "gh"
    yield a
    a.close()


def _session(agent, task="Return two\n\nf now returns 2."):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": task})
    assert r.status_code == 200, r.text
    return r.json()


def _commit(work, name="app.py", text="def f():\n    return 2\n"):
    with open(os.path.join(work, name), "w") as fh:
        fh.write(text)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "change", cwd=work)


def test_nothing_ships_before_something_is_committed(shipper):
    s = _session(shipper)
    r = shipper.http.post(f"/sessions/{s['id']}/ship", json={})
    assert r.status_code == 400 and "Finish the session first" in r.json()["detail"]
    assert not (shipper.log.parent / "gh.args").exists()


def test_a_committed_branch_is_pushed_and_its_pull_request_opened(shipper, tmp_path):
    s = _session(shipper)
    work = str(tmp_path / "ws" / "sessions" / s["id"] / "work")
    _commit(work)
    r = shipper.http.post(f"/sessions/{s['id']}/ship", json={})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got == {"session": s["id"], "branch": s["branch"], "commits": 1, "url": PR, "existing": False}
    pushed = subprocess.run(["git", "rev-parse", f"refs/heads/{s['branch']}"], cwd=shipper.origin,
                            capture_output=True, text=True).stdout.strip()
    assert pushed == git("rev-parse", "HEAD", cwd=work)
    args = (tmp_path / "gh.args").read_text().split("\n")
    assert args[:6] == ["pr", "create", "--head", s["branch"], "--title", "Return two"]
    assert "--draft" not in args and "--base" not in args
    assert (tmp_path / "gh.body").read_text() == f"f now returns 2.\n\nProofpane-Session: {s['id']}\n"
    assert shipper.http.get(f"/sessions/{s['id']}").json()["shipped"]["url"] == PR


def test_a_record_that_names_another_branch_still_ships_the_session_s_own(shipper, tmp_path):
    # a session's programs can write its record: a branch written there as main must not push to origin's main
    s = _session(shipper)
    work = str(tmp_path / "ws" / "sessions" / s["id"] / "work")
    _commit(work)
    record = tmp_path / "ws" / "sessions" / s["id"] / "session.json"
    record.write_text(json.dumps(dict(json.loads(record.read_text()), branch="main")))
    main = git("rev-parse", "refs/heads/main", cwd=shipper.origin)
    r = shipper.http.post(f"/sessions/{s['id']}/ship", json={})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == s["branch"] == f"pp/{s['id']}"
    assert git("rev-parse", "refs/heads/main", cwd=shipper.origin) == main
    assert git("rev-parse", f"refs/heads/{s['branch']}", cwd=shipper.origin) == git("rev-parse", "HEAD", cwd=work)
    assert (tmp_path / "gh.args").read_text().split("\n")[:4] == ["pr", "create", "--head", s["branch"]]


def test_the_owner_s_title_description_base_and_draft_are_used(shipper, tmp_path):
    s = _session(shipper)
    _commit(str(tmp_path / "ws" / "sessions" / s["id"] / "work"))
    r = shipper.http.post(f"/sessions/{s['id']}/ship", json={"title": "Two, at last", "body": "Why: tests.",
                                                             "base": "main", "draft": True})
    assert r.status_code == 200, r.text
    args = (tmp_path / "gh.args").read_text().split("\n")
    assert args[5] == "Two, at last" and "--draft" in args and args[args.index("--base") + 1] == "main"
    assert (tmp_path / "gh.body").read_text().startswith("Why: tests.\n\nProofpane-Session: ")


def test_a_second_ship_gives_back_the_existing_pull_request(shipper, tmp_path):
    s = _session(shipper)
    _commit(str(tmp_path / "ws" / "sessions" / s["id"] / "work"))
    assert shipper.http.post(f"/sessions/{s['id']}/ship", json={}).json()["existing"] is False
    again = shipper.http.post(f"/sessions/{s['id']}/ship", json={})
    assert again.status_code == 200 and again.json()["url"] == PR and again.json()["existing"] is True


@pytest.mark.parametrize("body", [{"base": "-x"}, {"base": "a..b"}, {"title": "t" * 201}, {"body": 5}])
def test_what_the_page_sends_is_checked(shipper, tmp_path, body):
    s = _session(shipper)
    _commit(str(tmp_path / "ws" / "sessions" / s["id"] / "work"))
    assert shipper.http.post(f"/sessions/{s['id']}/ship", json=body).status_code == 400


def test_a_repository_without_an_origin_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _fake_gh(tmp_path / "bin", tmp_path / "gh")
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "No origin"}).json()
        _commit(str(tmp_path / "ws" / "sessions" / s["id"] / "work"))
        r = a.http.post(f"/sessions/{s['id']}/ship", json={})
        assert r.status_code == 400 and "no origin" in r.json()["detail"]
    finally:
        a.close()


def test_a_pull_request_gh_cannot_open_says_the_branch_was_pushed(shipper, tmp_path):
    # GitHub not signed in: gh says so after the push, which has happened; the person is told both
    gh = tmp_path / "bin" / "gh"
    gh.write_text("#!/bin/sh\n"
                  "echo 'To get started with GitHub CLI, please run:  gh auth login' >&2\n"
                  "echo 'Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.' >&2\n"
                  "exit 4\n")
    s = _session(shipper)
    work = str(tmp_path / "ws" / "sessions" / s["id"] / "work")
    _commit(work)
    r = shipper.http.post(f"/sessions/{s['id']}/ship", json={})
    assert r.status_code == 400
    said = r.json()["detail"]
    assert said.startswith(f"Pushed {s['branch']} to origin, but the pull request was not opened: ")
    assert said.endswith("GH_TOKEN environment variable with a GitHub API authentication token.")
    pushed = subprocess.run(["git", "rev-parse", f"refs/heads/{s['branch']}"], cwd=shipper.origin,
                            capture_output=True, text=True).stdout.strip()
    assert pushed == git("rev-parse", "HEAD", cwd=work)
    assert not shipper.http.get(f"/sessions/{s['id']}").json().get("shipped")
