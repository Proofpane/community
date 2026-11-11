"""The worktree's files for the page (workspace.files, workspace.file and workspace.paths): read only, inside the
worktree, links named and never followed, .git never shown."""

import os

import pytest

from proofpane_workspace import workspace as wsmod
from proofpane_workspace.workspace import MAX_VIEW, Workspace, WorkspaceError, NotFound
from workspace_fixtures import Agent, make_repo


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Look around")
    w.sid = record["id"]
    w.work = os.path.join(w._dir(record["id"]), "work")
    return w


def _write(work, rel, data):
    path = os.path.join(work, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)


def test_the_root_lists_folders_first_and_never_git(ws):
    _write(ws.work, "src/app.py", "x = 1\n")
    _write(ws.work, "b.txt", "b\n")
    got = ws.files(ws.sid, "")
    names = [(e["name"], e["type"]) for e in got["entries"]]
    assert names == [("src", "dir"), ("b.txt", "file"), ("README.md", "file")]
    assert all(e["name"] != ".git" for e in got["entries"]) and got["path"] == "" and got["truncated"] is False
    assert [e["size"] for e in got["entries"]] == [None, 2, 5]


def test_a_folder_and_a_file_inside_it(ws):
    _write(ws.work, "src/pkg/mod.py", "def f():\n    return 1\n")
    assert [e["name"] for e in ws.files(ws.sid, "src/pkg")["entries"]] == ["mod.py"]
    f = ws.file(ws.sid, "src/pkg/mod.py")
    assert f["text"] == "def f():\n    return 1\n" and f["binary"] is False and f["too_large"] is False
    assert f["path"] == "src/pkg/mod.py" and f["size"] == 22


def test_a_binary_file_and_a_large_one_are_named_without_their_bytes(ws):
    _write(ws.work, "img.bin", b"\x89PNG\x00\x01\x02")
    _write(ws.work, "big.txt", "a" * (MAX_VIEW + 1))
    b = ws.file(ws.sid, "img.bin")
    assert b["binary"] is True and b["text"] is None
    big = ws.file(ws.sid, "big.txt")
    assert big["too_large"] is True and big["text"] is None and big["size"] == MAX_VIEW + 1


@pytest.mark.parametrize("rel", ["..", "../..", "src/../../x", ".git", ".git/config", "a/\0b"])
def test_paths_outside_what_the_page_may_see_are_refused(ws, rel):
    with pytest.raises(WorkspaceError):
        ws.files(ws.sid, rel)
    with pytest.raises(WorkspaceError):
        ws.file(ws.sid, rel)


def test_a_link_is_named_and_never_read_through(ws, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("not for the page\n")
    os.symlink(str(secret), os.path.join(ws.work, "leak.txt"))
    (entry,) = [e for e in ws.files(ws.sid, "")["entries"] if e["name"] == "leak.txt"]
    assert entry["type"] == "link"
    f = ws.file(ws.sid, "leak.txt")
    assert f["link"] is True and f["text"] is None


def test_a_linked_folder_that_leads_outside_is_refused(ws, tmp_path):
    out = tmp_path / "elsewhere"
    out.mkdir()
    (out / "private.txt").write_text("no\n")
    os.symlink(str(out), os.path.join(ws.work, "door"))
    with pytest.raises(WorkspaceError):
        ws.files(ws.sid, "door")
    with pytest.raises(WorkspaceError):
        ws.file(ws.sid, "door/private.txt")


def test_what_is_not_there_is_not_found(ws):
    with pytest.raises(NotFound):
        ws.file(ws.sid, "nope.txt")
    with pytest.raises(NotFound):
        ws.files(ws.sid, "nope")


def test_a_session_without_a_repository_has_no_files(tmp_path):
    w = Workspace(str(tmp_path / "ws"))
    record, _ = w.prepare(None, "shell", "A shell")
    with pytest.raises(WorkspaceError):
        w.files(record["id"], "")
    with pytest.raises(WorkspaceError):
        w.paths(record["id"])


def test_every_file_git_sees_is_listed_for_finding_one_by_name(ws):
    _write(ws.work, "src/app.py", "x = 1\n")
    _write(ws.work, "dir with space/n" + chr(0xE9) + ".txt", "hi\n")
    _write(ws.work, ".gitignore", "build/\n*.log\n")
    _write(ws.work, "build/out.js", "ignored\n")
    _write(ws.work, "debug.log", "ignored\n")
    os.remove(os.path.join(ws.work, "README.md"))           # tracked, and gone from the worktree
    got = ws.paths(ws.sid)
    assert got == {"session": ws.sid, "truncated": False,
                   "paths": [".gitignore", "dir with space/n" + chr(0xE9) + ".txt", "src/app.py"]}


def test_a_long_list_is_cut_and_says_so(ws, monkeypatch):
    for i in range(5):
        _write(ws.work, f"f{i}.txt", "x\n")
    monkeypatch.setattr(wsmod, "MAX_PATHS", 3)
    got = ws.paths(ws.sid)
    assert got["truncated"] is True and got["paths"] == ["README.md", "f0.txt", "f1.txt"]


def test_a_gone_worktree_has_no_paths(ws):
    import shutil
    shutil.rmtree(ws.work)
    with pytest.raises(NotFound):
        ws.paths(ws.sid)


def test_the_agent_serves_them(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = a.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Files"}).json()
        work = tmp_path / "ws" / "sessions" / s["id"] / "work"
        (work / "dir with space").mkdir()
        (work / "dir with space" / "n.txt").write_text("hi\n")
        listed = a.http.get(f"/sessions/{s['id']}/files", params={"path": "dir with space"})
        assert listed.status_code == 200 and [e["name"] for e in listed.json()["entries"]] == ["n.txt"]
        got = a.http.get(f"/sessions/{s['id']}/file", params={"path": "dir with space/n.txt"})
        assert got.status_code == 200 and got.json()["text"] == "hi\n"
        assert a.http.get(f"/sessions/{s['id']}/file", params={"path": "../x"}).status_code == 400
        assert a.http.get(f"/sessions/{s['id']}/paths").json()["paths"] == ["README.md", "dir with space/n.txt"]
    finally:
        a.close()


def _within_seconds(fn, seconds=5):
    """What `fn` gives or raises, on a thread with a deadline: a read that waits fails the test (queue.Empty) rather than
    holding it, and its thread, a daemon, never holds the run's end."""
    import queue
    import threading
    out = queue.Queue()

    def run():
        try:
            out.put((True, fn()))
        except BaseException as e:      # noqa: BLE001 (given back to the test as it was raised)
            out.put((False, e))
    threading.Thread(target=run, daemon=True).start()
    ok, value = out.get(timeout=seconds)
    if not ok:
        raise value
    return value


@pytest.mark.parametrize("swap", ["pipe", "link", "folder"])
def test_a_file_swapped_after_its_look_is_never_waited_on_or_read_through(ws, tmp_path, monkeypatch, swap):
    # a session's program can swap a file for a pipe (an open with no writer waits), a link out of the worktree or a
    # folder after the workspace has looked at the path; what is opened is checked, not what was looked at, and the
    # descriptor of a refused one is closed
    _write(ws.work, "notes.txt", "mine\n")
    _write(ws.work, "pic.png", b"\x89PNG\r\n")
    looked = {name: os.lstat(os.path.join(ws.work, name)) for name in ("notes.txt", "pic.png")}
    secret = tmp_path / "outside.txt"
    secret.write_text("not the worktree's\n")
    for name in looked:
        path = os.path.join(ws.work, name)
        os.remove(path)
        os.mkfifo(path) if swap == "pipe" else os.symlink(secret, path) if swap == "link" else os.mkdir(path)
    real_lstat = os.lstat
    monkeypatch.setattr(wsmod.os, "lstat", lambda p, *a, **k: looked.get(os.path.basename(p)) or real_lstat(p, *a, **k))
    open_before = len(os.listdir("/dev/fd"))
    for read in (lambda: ws.file(ws.sid, "notes.txt"), lambda: ws.image(ws.sid, "pic.png")):
        with pytest.raises(WorkspaceError, match="not a file"):
            _within_seconds(read)
    assert len(os.listdir("/dev/fd")) == open_before


def test_a_file_grown_past_the_limit_after_its_look_is_too_large_not_read(ws, monkeypatch):
    _write(ws.work, "log.txt", "small\n")
    looked = os.lstat(os.path.join(ws.work, "log.txt"))
    _write(ws.work, "log.txt", "a" * (MAX_VIEW + 1))
    real_lstat = os.lstat
    monkeypatch.setattr(wsmod.os, "lstat", lambda p, *a, **k: looked if p.endswith("log.txt") else real_lstat(p, *a, **k))
    got = ws.file(ws.sid, "log.txt")
    assert got["too_large"] is True and got["text"] is None


def test_a_save_over_a_file_swapped_for_a_pipe_is_refused_and_leaves_the_next_save_free(ws, monkeypatch):
    # the save reads what is there under its lock: a pipe that held that read would hold every save after it
    import hashlib
    _write(ws.work, "notes.txt", "mine\n")
    _write(ws.work, "other.txt", "other\n")
    looked = os.lstat(os.path.join(ws.work, "notes.txt"))
    os.remove(os.path.join(ws.work, "notes.txt"))
    os.mkfifo(os.path.join(ws.work, "notes.txt"))
    real_lstat = os.lstat
    monkeypatch.setattr(wsmod.os, "lstat", lambda p, *a, **k: looked if p.endswith("notes.txt") else real_lstat(p, *a, **k))
    with pytest.raises(WorkspaceError, match="not a file"):
        _within_seconds(lambda: ws.save(ws.sid, "notes.txt", "theirs\n", "0" * 64))
    base = hashlib.sha256(b"other\n").hexdigest()
    assert _within_seconds(lambda: ws.save(ws.sid, "other.txt", "changed\n", base))["text"] == "changed\n"


def test_no_folder_on_the_way_is_followed_when_one_was_swapped_for_a_link_after_the_look(ws, tmp_path, monkeypatch):
    # the look (_within, with realpath) is answered before the swap; the read and the save then walk the path folder by
    # folder (dir_fd, O_NOFOLLOW), so a folder that became a link out of the worktree is never read or written through
    import hashlib
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("not the worktree's\n")
    (outside / "f.png").write_bytes(b"\x89PNG\r\n")
    os.symlink(outside, os.path.join(ws.work, "sub"))
    monkeypatch.setattr(ws, "_within", lambda *a: True)
    with pytest.raises(WorkspaceError, match="a link is not followed"):
        ws.file(ws.sid, "sub/f.txt")
    with pytest.raises(WorkspaceError, match="a link is not followed"):
        ws.image(ws.sid, "sub/f.png")
    with pytest.raises(WorkspaceError, match="a link is not followed"):
        ws.save(ws.sid, "sub/f.txt", "theirs\n", hashlib.sha256(b"not the worktree's\n").hexdigest())
    with pytest.raises(WorkspaceError, match="a link is not followed"):
        ws.files(ws.sid, "sub")
    assert (outside / "f.txt").read_text() == "not the worktree's\n" and sorted(os.listdir(outside)) == ["f.png", "f.txt"]


def test_a_worktree_swapped_for_a_link_is_not_read_through(ws, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.txt").write_text("elsewhere\n")
    os.rename(ws.work, ws.work + "-real")
    os.symlink(elsewhere, ws.work)
    with pytest.raises(WorkspaceError, match="a link is not followed"):
        ws.file(ws.sid, "notes.txt")
