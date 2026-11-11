"""A session brought up to date with the branch it started from (workspace.update): its own work committed first,
then that branch merged in; cleanly, and the commit merged is its new base, so its change is again only what it did;
or stopped by conflicts, left for its agent or the person to resolve and commit, or given up. For a clone, the branch
as its origin has it, fetched first; for a repository added from this machine, as it is in the person's folder."""

import os
import subprocess

import pytest

from workspace_fixtures import Agent, git, make_repo

WHO = {"author_name": "ana", "author_email": "ana@example.com"}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def agent(tmp_path, home):
    a = Agent(str(tmp_path / "ws"), str(home), args=["--link-repos"])
    yield a
    a.close()


def _commit(path, name, text, message):
    with open(os.path.join(path, name), "w") as fh:
        fh.write(text)
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", message, cwd=path)
    return git("rev-parse", "HEAD", cwd=path)


def _session(agent, repo="demo", **over):
    r = agent.http.post("/sessions", json=dict({"repo": repo, "program": "shell", "task": "t"}, **over))
    assert r.status_code == 200, r.text
    s = r.json()
    return s, os.path.join(agent.root, "sessions", s["id"], "work")


def test_a_session_is_brought_up_to_date_and_its_change_is_again_only_its_own(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    s, work = _session(agent)
    with open(os.path.join(work, "mine.txt"), "w") as fh:          # the agent's work, not committed
        fh.write("mine\n")
    upstream = _commit(repo, "upstream.txt", "theirs\n", "upstream")
    assert agent.http.get(f"/sessions/{s['id']}/update").json() == {"ref": "main", "behind": 1, "merging": None}
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["merged"], r["base"]) == (True, "main", 1, upstream)
    assert os.path.exists(os.path.join(work, "upstream.txt")) and os.path.exists(os.path.join(work, "mine.txt"))
    log = git("log", "--format=%s", cwd=work).splitlines()
    assert log[:2] == ["Merge main into " + s["branch"], "Work in progress, before bringing it up to date with main"]
    changed = agent.http.get(f"/sessions/{s['id']}/diff").json()
    assert changed["base"] == upstream and [f["path"] for f in changed["files"]] == ["mine.txt"]
    again = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (again["updated"], again["behind"]) == (False, 0)


def _conflicted(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    s, work = _session(agent)
    with open(os.path.join(work, "README.md"), "w") as fh:
        fh.write("the session's line\n")
    upstream = _commit(repo, "README.md", "main's line\n", "upstream")
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    return s, work, upstream, r


def test_conflicts_stop_the_merge_and_nothing_else_goes_on_until_it_is_resolved_or_given_up(agent):
    s, work, upstream, r = _conflicted(agent)
    assert r["updated"] is False and r["merging"]["conflicts"] == ["README.md"] and r["merging"]["target"] == upstream
    assert "<<<<<<<" in open(os.path.join(work, "README.md")).read()
    status = agent.http.get(f"/sessions/{s['id']}/update").json()
    assert status["merging"]["conflicts"] == ["README.md"] and status["merging"]["ref"] == "main"
    for path in (f"/sessions/{s['id']}/update", f"/sessions/{s['id']}/finish"):
        refused = agent.http.post(path, json=WHO)
        assert refused.status_code == 400 and "A merge from its branch is under way" in refused.json()["detail"], path
    assert agent.http.post(f"/sessions/{s['id']}/update/abort").json() == {"aborted": True}
    assert open(os.path.join(work, "README.md")).read() == "the session's line\n"
    after = agent.http.get(f"/sessions/{s['id']}/update").json()
    assert (after["merging"], after["behind"]) == (None, 1)
    assert agent.http.post(f"/sessions/{s['id']}/update/abort").status_code == 400      # nothing to give up now


def test_a_merge_resolved_and_committed_by_the_agent_makes_the_merged_commit_the_base(agent):
    s, work, upstream, _ = _conflicted(agent)
    with open(os.path.join(work, "README.md"), "w") as fh:          # as the agent would resolve it
        fh.write("main's line\nthe session's line\n")
    git("add", "README.md", cwd=work)
    git("commit", "-q", "--no-edit", cwd=work)
    changed = agent.http.get(f"/sessions/{s['id']}/diff").json()
    assert changed["base"] == upstream and [f["path"] for f in changed["files"]] == ["README.md"]
    assert "+the session's line" in changed["diff"] and "-main's line" not in changed["diff"]
    assert agent.http.get(f"/sessions/{s['id']}/update").json()["merging"] is None


def test_a_clone_is_brought_up_to_date_with_its_origin_fetched_first(agent, tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    make_repo(str(seed))
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    repo = os.path.join(agent.root, "repos", "demo")
    subprocess.run(["git", "clone", "-q", str(origin), repo], check=True)
    s, work = _session(agent)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    pushed = _commit(str(other), "upstream.txt", "theirs\n", "upstream")
    git("push", "-q", "origin", "main", cwd=str(other))
    # as last fetched, nothing new; fetched, one commit, from origin/main, which the clone's own main never had
    assert agent.http.get(f"/sessions/{s['id']}/update").json()["behind"] == 0
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["base"]) == (True, "origin/main", pushed)
    assert os.path.exists(os.path.join(work, "upstream.txt"))
    assert git("rev-parse", "main", cwd=repo) != pushed


def test_a_repository_from_this_mac_is_followed_as_it_is_in_the_person_s_folder(agent, tmp_path):
    mine = tmp_path / "parser"
    make_repo(str(mine), origin="https://github.com/acme/parser.git")       # never fetched: the folder is the truth
    agent.http.post("/repos/link", json={"path": str(mine)})
    s, work = _session(agent, repo="parser")
    theirs = _commit(str(mine), "upstream.txt", "theirs\n", "the person's own commit")
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["base"], r["note"]) == (True, "main", theirs, None)


def test_a_session_from_a_commit_follows_the_repository_s_current_branch_and_one_on_none_has_nothing(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    first = make_repo(repo)
    s, _ = _session(agent, base=first)
    _commit(repo, "upstream.txt", "theirs\n", "upstream")
    assert agent.http.get(f"/sessions/{s['id']}/update").json()["ref"] == "main"
    git("checkout", "-q", "--detach", "HEAD", cwd=repo)
    status = agent.http.get(f"/sessions/{s['id']}/update").json()
    assert status["ref"] is None and "no branch to bring it up to date with" in status["why"]
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400


def test_an_update_names_who_asked_for_it(agent):
    make_repo(os.path.join(agent.root, "repos", "demo"))
    s, _ = _session(agent)
    for body in ({}, {"author_name": "ana", "author_email": "no-at"}, {"author_name": "a\nb", "author_email": "a@b"}):
        assert agent.http.post(f"/sessions/{s['id']}/update", json=body).status_code == 400, body


def _origin(tmp_path, name="origin.git"):
    """A bare repository as an origin, seeded with one commit on main: (its path, a clone to push from)."""
    seed = tmp_path / f"seed-{name}"
    make_repo(str(seed))
    origin = tmp_path / name
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    pusher = tmp_path / f"pusher-{name}"
    subprocess.run(["git", "clone", "-q", str(origin), str(pusher)], check=True)
    return origin, pusher


def test_a_session_whose_start_is_not_in_what_it_follows_is_not_updated(agent):
    # a session from a commit of another branch follows the repository's current branch, which lacks it: merged in,
    # main's other changes would have been counted as the session's own
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    git("checkout", "-q", "-b", "side", cwd=repo)
    side = _commit(repo, "side.txt", "side\n", "on a side branch")
    git("checkout", "-q", "main", cwd=repo)
    s, _ = _session(agent, base=side)
    _commit(repo, "upstream.txt", "theirs\n", "upstream")
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400 and "does not have the commit this session started from" in r.json()["detail"]
    assert agent.http.get(f"/sessions/{s['id']}/diff").json()["base"] == side


def test_a_session_from_a_pull_request_follows_its_head_fetched_again_and_not_a_rewritten_one(agent, tmp_path):
    origin, pusher = _origin(tmp_path)
    head = _commit(str(pusher), "pr.txt", "the pull request\n", "the pull request")
    git("push", "-q", "origin", "HEAD:refs/pull/7/head", cwd=str(pusher))
    repo = os.path.join(agent.root, "repos", "demo")
    subprocess.run(["git", "clone", "-q", str(origin), repo], check=True)
    git("remote", "set-url", "origin", "https://github.com/acme/demo.git", cwd=repo)
    git("config", f"url.{origin}.insteadOf", "https://github.com/acme/demo.git", cwd=repo)
    s, work = _session(agent, pull=7)
    assert agent.http.get(f"/sessions/{s['id']}/diff").json()["base"] == head
    later = _commit(str(pusher), "pr2.txt", "more of it\n", "the author pushed again")
    git("push", "-q", "origin", "HEAD:refs/pull/7/head", cwd=str(pusher))
    _commit(str(pusher), "main.txt", "main\n", "main moved too")
    git("push", "-q", "origin", "HEAD:main", cwd=str(pusher))
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["base"]) == (True, "#7", later), r
    assert os.path.exists(os.path.join(work, "pr2.txt")) and not os.path.exists(os.path.join(work, "main.txt"))
    # the author rewrites it: the session's start is not in it, so it is not merged in
    git("reset", "-q", "--hard", "HEAD~3", cwd=str(pusher))
    _commit(str(pusher), "other.txt", "rewritten\n", "rewritten")
    git("push", "-q", "--force", "origin", "HEAD:refs/pull/7/head", cwd=str(pusher))
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400 and "does not have the commit this session started from" in r.json()["detail"]


def test_a_session_from_origin_s_branch_follows_origin_s_and_not_the_person_s_unpushed_one(agent, tmp_path):
    origin, pusher = _origin(tmp_path)
    mine = tmp_path / "mine"
    subprocess.run(["git", "clone", "-q", str(origin), str(mine)], check=True)
    agent.http.post("/repos/link", json={"path": str(mine)})
    _commit(str(mine), "unpushed.txt", "not pushed\n", "the person's own, not pushed")
    s, work = _session(agent, repo="mine", base="origin/main")
    teammate = _commit(str(pusher), "teammate.txt", "theirs\n", "a teammate's")
    git("push", "-q", "origin", "HEAD:main", cwd=str(pusher))
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["base"]) == (True, "origin/main", teammate), r
    assert os.path.exists(os.path.join(work, "teammate.txt")) and not os.path.exists(os.path.join(work, "unpushed.txt"))
    assert [f["path"] for f in agent.http.get(f"/sessions/{s['id']}/diff").json()["files"]] == []


def test_giving_up_a_merge_its_agent_already_finished_settles_it_and_says_so(agent):
    s, work, upstream, _ = _conflicted(agent)
    with open(os.path.join(work, "README.md"), "w") as fh:
        fh.write("both\n")
    git("add", "README.md", cwd=work)
    git("commit", "-q", "--no-edit", cwd=work)
    assert agent.http.post(f"/sessions/{s['id']}/update/abort").json() == {"aborted": False, "finished": True}
    assert agent.http.get(f"/sessions/{s['id']}/diff").json()["base"] == upstream


@pytest.mark.parametrize("what", ["merge", "rebase", "other branch"])
def test_the_agent_s_own_work_half_done_is_never_updated_over_or_finished(agent, what):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    s, work = _session(agent)
    with open(os.path.join(work, "README.md"), "w") as fh:
        fh.write("the session's line\n")
    git("commit", "-q", "-am", "the session's", cwd=work)
    _commit(repo, "README.md", "main's line\n", "main changes the same line")
    if what == "merge":
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "merge", "main"], cwd=work, capture_output=True)
        says = "in the middle of a merge"
    elif what == "rebase":
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "rebase", "main"], cwd=work, capture_output=True)
        says = "in the middle of a rebase"
    else:
        git("checkout", "-q", "-b", "elsewhere", cwd=work)
        says = "on a branch other than its own"
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400 and f"Its worktree is {says}" in r.json()["detail"], r.text
    if what != "other branch":
        r = agent.http.post(f"/sessions/{s['id']}/finish", json=WHO)
        assert r.status_code == 400 and "in the middle of" in r.json()["detail"], r.text


def test_a_merge_its_commit_was_refused_for_is_given_up_and_git_s_words_said(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    hook = os.path.join(repo, ".git", "hooks", "commit-msg")
    with open(hook, "w") as fh:
        fh.write('#!/bin/sh\nif grep -q "^Merge" "$1"; then echo "this repository takes no merge commits" >&2; exit 1; fi\n')
    os.chmod(hook, 0o755)
    s, work = _session(agent)
    _commit(repo, "upstream.txt", "theirs\n", "upstream")
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400 and "this repository takes no merge commits" in r.json()["detail"], r.text
    status = agent.http.get(f"/sessions/{s['id']}/update").json()
    assert (status["merging"], status["behind"]) == (None, 1)
    assert subprocess.run(["git", "rev-parse", "--verify", "--quiet", "MERGE_HEAD"], cwd=work).returncode != 0


def test_a_branch_gone_from_origin_is_said_and_not_merged_as_last_fetched(agent, tmp_path):
    origin, pusher = _origin(tmp_path)
    git("push", "-q", "origin", "HEAD:refs/heads/wip", cwd=str(pusher))
    repo = os.path.join(agent.root, "repos", "demo")
    subprocess.run(["git", "clone", "-q", str(origin), repo], check=True)
    git("fetch", "-q", "origin", cwd=repo)
    s, _ = _session(agent, base="origin/wip")
    git("push", "-q", "origin", ":refs/heads/wip", cwd=str(pusher))
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO)
    assert r.status_code == 400 and "origin has no origin/wip now" in r.json()["detail"], r.text


def test_a_tag_named_as_the_branch_does_not_hide_the_branch(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    git("tag", "main", cwd=repo)
    s, _ = _session(agent)
    upstream = _commit(repo, "upstream.txt", "theirs\n", "upstream")
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["ref"], r["base"]) == (True, "main", upstream), r


def test_a_branch_its_agent_merged_itself_moves_the_base_so_its_change_is_its_own(agent):
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    s, work = _session(agent)
    upstream = _commit(repo, "upstream.txt", "theirs\n", "upstream")
    with open(os.path.join(work, "mine.txt"), "w") as fh:
        fh.write("mine\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "mine", cwd=work)
    git("merge", "-q", "--no-edit", "main", cwd=work)
    r = agent.http.post(f"/sessions/{s['id']}/update", json=WHO).json()
    assert (r["updated"], r["behind"]) == (False, 0)
    changed = agent.http.get(f"/sessions/{s['id']}/diff").json()
    assert changed["base"] == upstream and [f["path"] for f in changed["files"]] == ["mine.txt"]


def test_while_an_update_waits_on_origin_the_workspace_goes_on(agent, tmp_path):
    # an origin that takes the connection and says nothing: the fetch waits, outside the agent's lock
    import socket
    import threading
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held = []

    def take():
        try:
            while True:
                held.append(server.accept()[0])
        except OSError:
            pass                            # the server closed, at the end
    threading.Thread(target=take, daemon=True).start()
    repo = os.path.join(agent.root, "repos", "demo")
    make_repo(repo)
    git("remote", "add", "origin", f"http://127.0.0.1:{server.getsockname()[1]}/demo.git", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    s, _ = _session(agent)
    done = {}
    t = threading.Thread(target=lambda: done.update(r=agent.http.post(f"/sessions/{s['id']}/update", json=WHO)))
    t.start()
    try:
        import time as _time
        deadline = _time.time() + 10
        while not held and _time.time() < deadline:
            _time.sleep(0.05)
        assert held, "the fetch never reached the origin"
        r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "meanwhile"}, timeout=5)
        assert r.status_code == 200, r.text
    finally:
        for conn in held:
            conn.close()
        server.close()
        t.join(30)
    assert done["r"].status_code == 200 and "origin could not be reached" in done["r"].json()["note"], done["r"].text
