"""Saving a file of a session's worktree from the page's editor (workspace.save), through the real agent: only over
the version the page opened, never through a link or outside the worktree, replaced whole with its mode kept."""

import hashlib
import os

import pytest

from workspace_fixtures import Agent, git, make_repo


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    repo = tmp_path / "ws" / "repos" / "demo"
    make_repo(str(repo))
    (repo / "app.py").write_text("def f():\n    return 1\n")
    (repo / "run.sh").write_text("#!/bin/sh\necho hi\n")
    os.chmod(repo / "run.sh", 0o755)
    git("add", "-A", cwd=str(repo))
    git("commit", "-q", "-m", "files", cwd=str(repo))
    yield a
    a.close()


def _session(agent, tmp_path):
    s = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Edit"}).json()
    return s["id"], tmp_path / "ws" / "sessions" / s["id"] / "work"


def _open(agent, sid, path):
    r = agent.http.get(f"/sessions/{sid}/file", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


def _save(agent, sid, path, text, base):
    return agent.http.put(f"/sessions/{sid}/file", params={"path": path}, json={"text": text, "base": base})


def test_a_file_is_saved_over_the_version_that_was_opened(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    opened = _open(agent, sid, "app.py")
    assert opened["editable"] is True and opened["sha"] == hashlib.sha256(b"def f():\n    return 1\n").hexdigest()
    r = _save(agent, sid, "app.py", "def f():\n    return 2\n", opened["sha"])
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "def f():\n    return 2\n" and r.json()["sha"] != opened["sha"]
    assert (work / "app.py").read_text() == "def f():\n    return 2\n"
    changed = agent.http.get(f"/sessions/{sid}/diff").json()
    assert [f["path"] for f in changed["files"]] == ["app.py"]
    assert [n for n in os.listdir(work) if n.startswith(".pp-save-")] == []


def test_a_file_changed_since_it_was_opened_is_not_written_over(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    opened = _open(agent, sid, "app.py")
    (work / "app.py").write_text("def f():\n    return 3  # the agent's\n")
    r = _save(agent, sid, "app.py", "def f():\n    return 2\n", opened["sha"])
    assert r.status_code == 412 and "changed since you opened it" in r.json()["detail"]
    assert (work / "app.py").read_text() == "def f():\n    return 3  # the agent's\n"
    again = _open(agent, sid, "app.py")                          # saving over it is a choice made on what is there
    assert _save(agent, sid, "app.py", "mine\n", again["sha"]).status_code == 200
    assert (work / "app.py").read_text() == "mine\n"


def test_a_saved_file_keeps_its_mode(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    os.chmod(work / "run.sh", 0o751)
    opened = _open(agent, sid, "run.sh")
    assert _save(agent, sid, "run.sh", "#!/bin/sh\necho bye\n", opened["sha"]).status_code == 200
    assert os.stat(work / "run.sh").st_mode & 0o777 == 0o751


def test_a_link_a_folder_a_missing_file_and_paths_outside_are_refused(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n")
    os.symlink(outside, work / "link.txt")
    (work / "folder").mkdir()
    sha = hashlib.sha256(b"keep\n").hexdigest()
    assert _save(agent, sid, "link.txt", "x", sha).status_code == 400
    assert outside.read_text() == "keep\n"
    assert _save(agent, sid, "folder", "x", sha).status_code == 400
    assert _save(agent, sid, "nope.txt", "x", sha).status_code == 404
    for path in ("../outside.txt", ".git/config", ""):
        assert _save(agent, sid, path, "x", sha).status_code == 400, path
    os.symlink(tmp_path, work / "up")
    assert _save(agent, sid, "up/outside.txt", "x", sha).status_code == 400
    assert outside.read_text() == "keep\n"


@pytest.mark.parametrize("body", [{"text": 5, "base": "a" * 64}, {"text": "x", "base": "short"},
                                  {"text": "x"}, {"text": "x", "base": "A" * 64}])
def test_a_save_says_what_it_replaces_with_text(agent, tmp_path, body):
    sid, _ = _session(agent, tmp_path)
    r = agent.http.put(f"/sessions/{sid}/file", params={"path": "app.py"}, json=body)
    assert r.status_code == 400


def test_a_large_file_saves_and_one_too_large_does_not(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    opened = _open(agent, sid, "app.py")
    big = "x = 1\n" * 50000                                       # 300 KB: more than the agent's usual 64 KB request
    r = _save(agent, sid, "app.py", big, opened["sha"])
    assert r.status_code == 200, r.text
    r = _save(agent, sid, "app.py", "y" * (512 * 1024 + 1), r.json()["sha"])
    assert r.status_code == 400 and "at most 512 KB" in r.json()["detail"]
    assert (work / "app.py").read_text() == big


def test_a_file_that_is_not_utf8_is_shown_but_not_offered_for_editing(agent, tmp_path):
    sid, work = _session(agent, tmp_path)
    (work / "latin.txt").write_bytes(b"caf\xe9\n")
    got = _open(agent, sid, "latin.txt")
    assert got["editable"] is False and got["text"] is not None


def test_a_save_answers_with_what_it_wrote_even_when_the_agent_writes_right_after(tmp_path, monkeypatch):
    from proofpane_workspace import workspace as module
    from proofpane_workspace.workspace import Workspace
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Edit")
    path = os.path.join(w._dir(record["id"]), "work", "README.md")
    opened = w.file(record["id"], "README.md")
    real = module.os.replace

    def replace_then_the_agent_writes(src, dst, **at):
        real(src, dst, **at)            # by name in the file's folder (src_dir_fd, dst_dir_fd), as the save renames
        if dst in (path, "README.md"):
            with open(path, "w") as fh:
                fh.write("the agent's\n")
    monkeypatch.setattr(module.os, "replace", replace_then_the_agent_writes)
    got = w.save(record["id"], "README.md", "mine\n", opened["sha"])
    assert got["sha"] == hashlib.sha256(b"mine\n").hexdigest() and got["text"] == "mine\n"
    # so the next save, from what the page holds, finds the agent's change and is refused, not written over it
    monkeypatch.setattr(module.os, "replace", real)
    with pytest.raises(module.Conflict):
        w.save(record["id"], "README.md", "mine again\n", got["sha"])
