"""Putting one file of a session's worktree back as it was where the session started (workspace.discard): a change
or a deletion restored, an added file removed, a committed change undone in the worktree only, and nothing outside
the worktree touched."""

import os

import pytest

from workspace_fixtures import Agent, git, make_repo


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    repo = str(tmp_path / "ws" / "repos" / "demo")
    make_repo(repo)
    with open(os.path.join(repo, "app.py"), "w") as fh:
        fh.write("def f():\n    return 1\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "app", cwd=repo)
    yield a
    a.close()


def _session(agent, tmp_path):
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Change things"}).json()
    return s["id"], tmp_path / "ws" / "sessions" / s["id"] / "work"


def _paths(agent, sid):
    return sorted(f["path"] for f in agent.http.get(f"/sessions/{sid}/diff").json()["files"])


def _discard(agent, sid, path):
    return agent.http.post(f"/sessions/{sid}/discard", json={"path": path})


def test_a_changed_a_deleted_and_an_added_file_go_back_to_the_base(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    original = (work / "README.md").read_text()
    (work / "README.md").write_text("changed\n")
    (work / "app.py").unlink()
    (work / "src").mkdir()
    (work / "src" / "new.py").write_text("x = 1\n")
    assert _paths(agent, sid) == ["README.md", "app.py", "src/new.py"]
    assert _discard(agent, sid, "README.md").json() == {"path": "README.md", "restored": True}
    assert (work / "README.md").read_text() == original
    assert _discard(agent, sid, "app.py").json() == {"path": "app.py", "restored": True}
    assert (work / "app.py").exists()
    assert _discard(agent, sid, "./src//new.py").json() == {"path": "src/new.py", "removed": True}
    assert not (work / "src" / "new.py").exists()
    assert _paths(agent, sid) == []


def test_a_committed_change_is_undone_in_the_worktree_and_stays_in_the_history(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "app.py").write_text("def f():\n    return 2\n")
    git("commit", "-q", "-am", "two", cwd=str(work))
    head = git("rev-parse", "HEAD", cwd=str(work))
    assert _discard(agent, sid, "app.py").json()["restored"] is True
    assert "return 1" in (work / "app.py").read_text()
    assert git("rev-parse", "HEAD", cwd=str(work)) == head          # the commit stays; the next Finish undoes it
    assert _paths(agent, sid) == []


@pytest.mark.parametrize("path, status", [("../x", 400), (".git/config", 400), ("missing.txt", 404), ("", 400)])
def test_what_is_not_a_file_of_the_worktree_is_refused(agent, tmp_path, path, status):
    sid, work = _session(agent, tmp_path)
    assert _discard(agent, sid, path).status_code == status


def test_a_folder_is_refused_and_nothing_outside_is_removed_through_a_link(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "folder").mkdir()
    (work / "folder" / "a.txt").write_text("a\n")
    assert _discard(agent, sid, "folder").status_code == 400
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    os.symlink(outside, work / "away")
    assert _discard(agent, sid, "away/keep.txt").status_code == 400
    assert (outside / "keep.txt").read_text() == "keep\n"
