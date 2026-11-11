"""A cloud session of the person's Claude account continued in a workspace session (Claude Code's own --teleport;
workspace.prepare's `teleport`): claude starts with --teleport and the cloud session's id (none: Claude Code's own
picker), and the session takes the branch teleport checked out as its own, once. Through a real workspace agent, with a
stand-in claude that does to its worktree what teleport does: fetch the cloud session's branch and check it out."""

import json
import os
import threading
import time

import pytest

from proofpane_workspace import workspace as workspace_module
from proofpane_workspace.workspace import Workspace
from workspace_fixtures import Agent, git, make_repo

STAND_IN = """#!/bin/sh
case "$1" in -v|--version) echo "2.1.274 (Claude Code)"; exit 0 ;; esac
printf '%s\\n' "$@" > "$PP_SESSION_DIR/argv.tmp" && mv "$PP_SESSION_DIR/argv.tmp" "$PP_SESSION_DIR/argv"
case " $* " in
  *" --teleport "*)
    if [ "$(git rev-parse --abbrev-ref HEAD)" != "claude/cloud-work" ]; then
      case " $* " in *" session_dirty "*) echo kept > own.txt && git add own.txt && git -c user.name=x -c user.email=x@x \\
        commit -q -m "its own" ;; esac
      git fetch -q origin claude/cloud-work && git checkout -q -b claude/cloud-work FETCH_HEAD
    fi ;;
esac
exec sleep 300
"""


def _agent(tmp_path, upstream_moved=False, hook=None):
    """A workspace agent whose repository's origin holds a cloud session's branch, claude/cloud-work: forked from the
    clone's own main, or with `upstream_moved` from a main origin had moved on to since the clone (so the two differ).
    `hook`, a post-checkout hook's script, put in the repository (its worktrees share it)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(STAND_IN)
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    demo = tmp_path / "ws" / "repos" / "demo"
    first = make_repo(str(demo))
    origin = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(demo), str(origin), cwd=str(tmp_path))
    cloud = tmp_path / "cloud"                                          # where the cloud session worked, and pushed
    git("clone", "-q", str(origin), str(cloud), cwd=str(tmp_path))
    if upstream_moved:
        (cloud / "upstream.txt").write_text("merged on main since the clone\n")
        git("add", "upstream.txt", cwd=str(cloud))
        git("commit", "-q", "-m", "upstream moved on", cwd=str(cloud))
        git("push", "-q", "origin", "main", cwd=str(cloud))
    forked = git("rev-parse", "HEAD", cwd=str(cloud))
    git("checkout", "-q", "-b", "claude/cloud-work", cwd=str(cloud))
    (cloud / "cloud.txt").write_text("done in the cloud\n")
    git("add", "cloud.txt", cwd=str(cloud))
    git("commit", "-q", "-m", "the cloud session's work", cwd=str(cloud))
    git("push", "-q", "origin", "claude/cloud-work", cwd=str(cloud))
    git("remote", "add", "origin", str(origin), cwd=str(demo))
    if hook:
        path = demo / ".git" / "hooks" / "post-checkout"
        path.write_text(hook)
        path.chmod(0o755)
    a = Agent(str(tmp_path / "ws"), str(home), extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    a.first, a.forked = first, forked
    return a


@pytest.fixture
def agent(tmp_path):
    a = _agent(tmp_path)
    yield a
    a.close()


def _start(agent, teleport, **more):
    return agent.http.post("/sessions", json=dict({"repo": "demo", "program": "claude", "task": "From the cloud",
                                                   "teleport": teleport}, **more))


def _argv(agent, sid, timeout=20):
    path = os.path.join(agent.root, "sessions", sid, "argv")
    deadline = time.time() + timeout
    while not os.path.exists(path):
        assert time.time() < deadline, "claude did not start"
        time.sleep(0.05)
    return open(path).read().splitlines()


def _adopted(agent, sid, timeout=20):
    deadline = time.time() + timeout
    while True:
        view = agent.http.get(f"/sessions/{sid}").json()
        if view.get("adopted"):
            return view
        assert time.time() < deadline, view
        time.sleep(0.1)


def test_a_cloud_session_named_by_its_link_is_teleported_and_its_branch_becomes_the_session_s(agent):
    r = _start(agent, "https://claude.ai/code/session_abc123?from=app")
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["teleport"] == "session_abc123" and r.json()["branch"] == f"pp/{sid}"
    assert _argv(agent, sid)[-2:] == ["--teleport", "session_abc123"]     # the id alone, as one argument; no task
    view = _adopted(agent, sid)
    # its own branch, moved to the cloud session's work and checked out; its change is that work
    assert view["branch"] == f"pp/{sid}" and view["base"] == agent.first
    assert view["teleported_from"] == "claude/cloud-work"
    work = os.path.join(agent.root, "sessions", sid, "work")
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == f"pp/{sid}"
    assert git("log", "-1", "--format=%s", cwd=work) == "the cloud session's work"
    listed = {s["id"]: s for s in agent.http.get("/sessions").json()["sessions"]}
    assert listed[sid]["adopted"] is True


def test_the_id_a_cloud_session_sees_itself_by_is_given_as_claude_ai_s(agent):
    r = _start(agent, "cse_abc-1")
    assert r.status_code == 200, r.text
    assert _argv(agent, r.json()["id"])[-2:] == ["--teleport", "session_abc-1"] and r.json()["teleport"] == "session_abc-1"


def test_without_an_id_claude_code_s_own_picker_opens(agent):
    r = _start(agent, "")
    assert r.status_code == 200, r.text
    assert _argv(agent, r.json()["id"])[-1] == "--teleport"
    assert r.json()["teleport"] == ""


def test_a_session_whose_own_branch_has_work_does_not_take_another(agent):
    r = _start(agent, "session_dirty")
    sid = r.json()["id"]
    _argv(agent, sid)
    work = os.path.join(agent.root, "sessions", sid)
    deadline = time.time() + 20
    while "claude/cloud-work" not in git("branch", "--list", "claude/cloud-work",
                                         cwd=os.path.join(agent.root, "repos", "demo")):
        assert time.time() < deadline
        time.sleep(0.1)
    view = agent.http.get(f"/sessions/{sid}").json()
    assert view["branch"] == f"pp/{sid}" and not view.get("adopted")      # held to its own, as any session
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=os.path.join(work, "work")) == "claude/cloud-work"


@pytest.mark.parametrize("body,words", [
    ({"program": "codex"}, "a claude session"),
    ({"model_provider": "kimi"}, "on no other provider"),
    ({"base": "main"}, "brings its own branch"),
    ({"teleport": "not-an-id"}, "named by its id"),
    ({"teleport": "https://claude.ai/code/session_x/../evil"}, "named by its id"),
    ({"teleport": "session_x --dangerously-skip-permissions"}, "named by its id"),
    ({"repo": None, "teleport": "session_x"}, "checkout of its repository"),
])
def test_what_teleport_cannot_be_given_is_refused_before_anything_starts(agent, body, words):
    r = _start(agent, body.pop("teleport", "session_x"), **body)
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert agent.http.get("/sessions").json()["sessions"] == []


def _cloud_branch_fetched(agent, timeout=20):
    deadline = time.time() + timeout
    while "claude/cloud-work" not in git("branch", "--list", "claude/cloud-work",
                                         cwd=os.path.join(agent.root, "repos", "demo")):
        assert time.time() < deadline, "teleport did not check out the cloud session's branch"
        time.sleep(0.1)


def test_a_clone_behind_origin_counts_only_the_cloud_session_s_work_as_the_session_s(tmp_path):
    """The cloud session forked from a main that origin moved on to since the workspace cloned it: the session's base
    is that fork point, fetched from origin, not the clone's own older main, so what origin merged meanwhile is not
    counted, finished or shipped as the session's work."""
    agent = _agent(tmp_path, upstream_moved=True)
    try:
        sid = _start(agent, "session_abc").json()["id"]
        view = _adopted(agent, sid)
        assert view["base"] == agent.forked != agent.first
        done = agent.http.post(f"/sessions/{sid}/finish", json={"author_name": "Owner", "author_email": "o@x.com"})
        assert done.status_code == 200, done.text
        assert done.json()["files"] == 1 and "cloud.txt" in done.json()["diff"]
        assert "upstream.txt" not in done.json()["diff"]
    finally:
        agent.close()


def test_a_repository_s_hook_does_not_run_when_the_session_takes_teleport_s_work(tmp_path):
    """The adoption's own checkout runs no hook of the repository's: one that would fail it, after the checkout was
    done, would leave the session never taking its work."""
    marker = tmp_path / "hook-ran"
    # not the worktree's own making (from no commit), nor teleport's checkout of its branch: the move to pp/<id>
    hook = ("#!/bin/sh\ncase \"$1\" in 0000000000000000000000000000000000000000) exit 0;; esac\n"
            f"case \"$(git symbolic-ref -q HEAD)\" in refs/heads/pp/*) touch {marker}; exit 1;; esac\nexit 0\n")
    agent = _agent(tmp_path, hook=hook)
    try:
        sid = _start(agent, "session_abc").json()["id"]
        view = _adopted(agent, sid)
        assert view["teleported_from"] == "claude/cloud-work"
        assert not marker.exists()
    finally:
        agent.close()


def test_a_finish_right_after_teleport_takes_its_work_first(agent):
    """A finish asked for before any read of the session looked (none does here) takes teleport's work first, waiting
    for it, and commits on the session's own branch."""
    sid = _start(agent, "session_abc").json()["id"]
    _cloud_branch_fetched(agent)
    work = os.path.join(agent.root, "sessions", sid, "work")
    deadline = time.time() + 20
    while git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) != "claude/cloud-work":
        assert time.time() < deadline
        time.sleep(0.05)
    done = agent.http.post(f"/sessions/{sid}/finish", json={"author_name": "Owner", "author_email": "o@x.com"})
    assert done.status_code == 200, done.text
    assert done.json()["branch"] == f"pp/{sid}" and "cloud.txt" in done.json()["diff"]
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == f"pp/{sid}"


def test_a_teleport_session_whose_work_was_not_taken_is_neither_finished_nor_shipped(agent):
    """Its own branch had work of its own, so teleport's branch is not taken (above): finishing would commit on
    teleport's branch, and shipping would push it under the session's own name, with its base where it started."""
    sid = _start(agent, "session_dirty").json()["id"]
    _argv(agent, sid)
    _cloud_branch_fetched(agent)
    for what, body in (("finish", {"author_name": "Owner", "author_email": "o@x.com"}), ("ship", {})):
        r = agent.http.post(f"/sessions/{sid}/{what}", json=body)
        assert r.status_code == 400 and "which Claude Code's teleport checked out" in r.json()["detail"], (what, r.text)
    work = os.path.join(agent.root, "sessions", sid, "work")
    assert git("status", "--porcelain", cwd=work) == ""          # nothing committed, nothing changed


def test_a_read_of_a_teleport_session_never_waits_on_its_adoption(tmp_path, monkeypatch):
    """The page reads the sessions every few seconds on the agent's event loop: a read starts the adoption in a thread
    of its own and returns, and reads within ADOPT_EVERY seconds start no other."""
    w = Workspace(str(tmp_path / "ws"))
    started, release = [], threading.Event()

    def slow(session_id, wait=False):
        started.append(session_id)
        release.wait(10)
    monkeypatch.setattr(w, "_adopt", slow)
    record = {"id": "s1", "teleport": "", "repo": "demo"}
    began = time.monotonic()
    for _ in range(3):
        assert w._adopted("s1", record) is record
    assert time.monotonic() - began < 1
    deadline = time.time() + 5
    while not started:
        assert time.time() < deadline
        time.sleep(0.01)
    time.sleep(0.2)
    assert started == ["s1"]
    monkeypatch.setattr(workspace_module, "ADOPT_EVERY", 0)
    w._adopt_next.clear()
    w._adopted("s1", record)
    deadline = time.time() + 5
    while len(started) < 2:
        assert time.time() < deadline
        time.sleep(0.01)
    release.set()
    assert w._adopted("s1", dict(record, adopted=True)) is not None and len(started) == 2


@pytest.mark.parametrize("task,rename,named", [
    (workspace_module.TELEPORT_TASK, None, "claude/cloud-work"),     # the page's own task: the branch names it
    ("From the cloud", None, None),                                  # a task the person typed stays its name
    (workspace_module.TELEPORT_TASK, "Mine", "Mine"),                # so does a name given before the work is taken
])
def test_a_session_under_the_page_s_task_is_named_after_the_branch_it_continues(agent, task, rename, named):
    """A teleport session under the task the page gives when none is typed (TELEPORT_TASK), and never
    renamed, takes the branch teleport checked out as its name once its work is taken, so that two such sessions are
    told apart in the list; a task the person typed, and a name given by Rename, stay."""
    sid = _start(agent, "session_abc", task=task).json()["id"]
    if rename:
        given = agent.http.post(f"/sessions/{sid}/title", json={"title": rename})
        assert given.status_code == 200, given.text
        assert not given.json().get("adopted"), "the work was taken before the rename: the case shows nothing"
    view = _adopted(agent, sid)
    assert view["teleported_from"] == "claude/cloud-work"
    assert view.get("title") == named, view
    listed = {s["id"]: s for s in agent.http.get("/sessions").json()["sessions"]}
    assert listed[sid].get("title") == named


def test_a_session_whose_work_was_taken_before_such_sessions_were_named_is_named_at_its_next_read(agent):
    """For a session adopted before such sessions were named: its record holds the branch it
    continues but no name, so its next read names it once, as an adoption now does; a name given after stays, and a
    name cleared by Rename puts its task back, as Rename says, never the branch again."""
    sid = _start(agent, "session_abc", task=workspace_module.TELEPORT_TASK).json()["id"]
    _adopted(agent, sid)
    path = os.path.join(agent.root, "sessions", sid, "session.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    for key in ("title", "named_after_branch"):                       # as an earlier adoption left it
        record.pop(key, None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    assert agent.http.get(f"/sessions/{sid}").json().get("title") == "claude/cloud-work"
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh).get("title") == "claude/cloud-work"       # written, not only shown
    assert agent.http.post(f"/sessions/{sid}/title", json={"title": "Mine"}).status_code == 200
    listed = {s["id"]: s for s in agent.http.get("/sessions").json()["sessions"]}
    assert listed[sid].get("title") == "Mine"
    cleared = agent.http.post(f"/sessions/{sid}/title", json={"title": ""})
    assert cleared.status_code == 200 and not cleared.json().get("title"), cleared.text
    listed = {s["id"]: s for s in agent.http.get("/sessions").json()["sessions"]}
    assert not listed[sid].get("title") and listed[sid]["task"] == workspace_module.TELEPORT_TASK


def test_the_page_gives_a_continued_session_the_workspace_s_own_task():
    """The page's task for a session continued from claude.ai, when none is typed, is TELEPORT_TASK word for word,
    and is what it sends: otherwise the naming above never applies."""
    page = os.path.join(os.path.dirname(__file__), "..", "ui", "src", "components", "NewSession.tsx")
    text = open(page, encoding="utf-8").read()
    assert f'export const TELEPORT_TASK = "{workspace_module.TELEPORT_TASK}";' in text
    assert "task.trim() || TELEPORT_TASK" in text
