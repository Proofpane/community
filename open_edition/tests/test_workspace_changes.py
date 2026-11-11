"""A session's changes for review (workspace.changes): committed or not, new files in, ignored ones out, the session's
own index untouched."""

import os
import threading
import time

import pytest

from proofpane_workspace import workspace
from proofpane_workspace.workspace import Workspace, WorkspaceError
from workspace_fixtures import Agent, git, make_repo


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    yield a
    a.close()


def _session(agent):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "Change things"})
    assert r.status_code == 200, r.text
    return r.json()


def _work(tmp_path, sid):
    return str(tmp_path / "ws" / "sessions" / sid / "work")


def _write(work, name, text):
    path = os.path.join(work, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def _diff(agent, sid):
    r = agent.http.get(f"/sessions/{sid}/diff")
    assert r.status_code == 200, r.text
    return r.json()


def test_committed_uncommitted_and_new_files_all_show(agent, tmp_path):
    s = _session(agent)
    work = _work(tmp_path, s["id"])
    _write(work, "committed.txt", "one\ntwo\n")
    git("add", "committed.txt", cwd=work)
    git("commit", "-q", "-m", "a commit of its own", cwd=work)
    _write(work, "README.md", "demo\nmore\n")               # changed, not staged
    _write(work, "src/new.py", "print('hi')\n")              # new, untracked
    d = _diff(agent, s["id"])
    files = {f["path"]: (f["added"], f["removed"]) for f in d["files"]}
    assert files == {"committed.txt": (2, 0), "README.md": (1, 0), "src/new.py": (1, 0)}
    assert d["base"] == s["base"] and d["branch"] == s["branch"] and d["too_large"] is False
    assert d["head"] == git("rev-parse", "HEAD", cwd=work)
    for line in ("+one", "+more", "+print('hi')", "diff --git a/src/new.py b/src/new.py"):
        assert line in d["diff"]


def test_the_sessions_own_index_is_left_as_it_was(agent, tmp_path):
    s = _session(agent)
    work = _work(tmp_path, s["id"])
    _write(work, "README.md", "demo\nstaged\n")
    git("add", "README.md", cwd=work)
    _write(work, "untracked.txt", "x\n")
    before = git("status", "--porcelain", cwd=work)
    assert "?? untracked.txt" in before and "M  README.md" in before
    _diff(agent, s["id"])
    assert git("status", "--porcelain", cwd=work) == before


def test_ignored_files_and_uploads_stay_out(agent, tmp_path):
    s = _session(agent)
    work = _work(tmp_path, s["id"])
    _write(work, ".gitignore", "build/\n")
    _write(work, "build/out.bin", "binary-ish\n")
    common = git("rev-parse", "--git-common-dir", cwd=work)
    common = common if os.path.isabs(common) else os.path.join(work, common)
    with open(os.path.join(common, "info", "exclude"), "a") as fh:
        fh.write("/.proofpane-uploads/\n")
    _write(work, ".proofpane-uploads/photo.png", "png\n")
    paths = [f["path"] for f in _diff(agent, s["id"])["files"]]
    assert paths == [".gitignore"]


def test_a_rename_is_one_file(agent, tmp_path):
    s = _session(agent)
    work = _work(tmp_path, s["id"])
    git("mv", "README.md", "GUIDE.md", cwd=work)
    d = _diff(agent, s["id"])
    assert [f["path"] for f in d["files"]] == ["GUIDE.md"]
    assert "rename from README.md" in d["diff"] and "rename to GUIDE.md" in d["diff"]


def test_a_binary_file_has_no_counts(agent, tmp_path):
    s = _session(agent)
    work = _work(tmp_path, s["id"])
    with open(os.path.join(work, "blob.bin"), "wb") as fh:
        fh.write(bytes(range(256)) * 4)
    (f,) = _diff(agent, s["id"])["files"]
    assert f == {"path": "blob.bin", "added": None, "removed": None}


def test_no_change_is_an_empty_report(agent):
    d = _diff(agent, _session(agent)["id"])
    assert d["files"] == [] and d["diff"] == ""


def test_an_unknown_session_is_not_found(agent):
    assert agent.http.get("/sessions/abcdefabcdef/diff").status_code == 404


def test_a_large_change_is_reported_without_its_text(tmp_path):
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root), max_diff=64)
    record, _ = w.prepare("demo", "shell", "Grow")
    work = os.path.join(w._dir(record["id"]), "work")
    _write(work, "big.txt", "line\n" * 100)
    d = w.changes(record["id"])
    assert d["too_large"] is True and d["diff"] is None and d["files"] == [{"path": "big.txt", "added": 100, "removed": 0}]


def test_a_session_without_a_repository_has_no_change(tmp_path):
    w = Workspace(str(tmp_path / "ws"))
    record, _ = w.prepare(None, "shell", "Just a shell")
    with pytest.raises(WorkspaceError):
        w.changes(record["id"])


# each session's change size in the list (_size_of): worked out off the listing, kept a while, and fresh after a look

def _listed(w, sid, until=lambda d: d is not None, wait=15):
    deadline = time.monotonic() + wait
    while True:
        d = next(r for r in w.sessions() if r["id"] == sid)["diff"]
        if until(d) or time.monotonic() > deadline:
            return d
        time.sleep(0.05)


def _idle(w, wait=15):
    deadline = time.monotonic() + wait
    while w._sizer and time.monotonic() < deadline:
        time.sleep(0.02)
    return not w._sizer


def _grown(tmp_path):
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Grow")
    work = os.path.join(w._dir(record["id"]), "work")
    _write(work, "README.md", "more\n")             # its one line replaced: one added, one removed
    _write(work, "src/new.py", "a\nb\n")            # a new file, two lines
    return w, record["id"], work


def test_the_list_carries_each_session_s_change_size_worked_out_without_waiting(tmp_path):
    w, sid, work = _grown(tmp_path)
    assert next(r for r in w.sessions() if r["id"] == sid)["diff"] is None    # the listing never waits on git
    assert _listed(w, sid) == {"files": 2, "added": 3, "removed": 1, "uncommitted": True}


def test_a_look_at_the_changes_is_the_size_the_list_shows_at_once(tmp_path):
    w, sid, work = _grown(tmp_path)
    assert _listed(w, sid)["files"] == 2 and _idle(w)
    _write(work, "third.txt", "x\n")
    w.changes(sid)
    assert next(r for r in w.sessions() if r["id"] == sid)["diff"] == {"files": 3, "added": 4, "removed": 1, "uncommitted": True}
    assert w._sizer is False                                                  # nothing to work out: it is fresh


def test_a_size_is_kept_a_while_and_worked_out_again_once_due(tmp_path, monkeypatch):
    w, sid, work = _grown(tmp_path)
    assert _listed(w, sid)["files"] == 2 and _idle(w)
    _write(work, "third.txt", "x\n")
    assert next(r for r in w.sessions() if r["id"] == sid)["diff"]["files"] == 2   # kept: not worked out again yet
    assert w._sizer is False and not w._sizes_wanted
    # due now: a size is kept for the longer of SIZE_FRESH_ENDED seconds and SIZE_PATIENCE times as long as it took,
    # so a first one slow to work out is kept past this test's wait. One made to take a second fails the test as
    # one run did (assert 2 == 3); that it was so slow, on a machine busy with a whole suite's run, is inferred
    monkeypatch.setattr(workspace, "SIZE_FRESH_ENDED", 0)
    monkeypatch.setattr(workspace, "SIZE_PATIENCE", 0)
    assert _listed(w, sid, until=lambda d: d and d["files"] == 3)["files"] == 3


def test_a_size_being_worked_out_is_not_asked_for_again(tmp_path, monkeypatch):
    w, sid, _ = _grown(tmp_path)
    started, go = threading.Event(), threading.Event()
    calls = []
    real = w._change_size

    def slow(session_id):
        calls.append(session_id)
        started.set()
        go.wait(15)
        return real(session_id)
    monkeypatch.setattr(w, "_change_size", slow)
    w.sessions()
    assert started.wait(15)
    for _ in range(3):
        w.sessions()                                    # listed again while it is worked out
    assert not w._sizes_wanted
    go.set()
    assert _listed(w, sid) is not None and _idle(w)
    assert calls == [sid]


def test_a_size_that_cannot_be_worked_out_is_none_and_not_tried_on_every_listing(tmp_path, monkeypatch):
    w, sid, _ = _grown(tmp_path)
    calls = []

    def fails(session_id):
        calls.append(session_id)
        raise RuntimeError("git is gone")
    monkeypatch.setattr(w, "_change_size", fails)
    w.sessions()
    deadline = time.monotonic() + 15
    while sid not in w._sizes and time.monotonic() < deadline:
        time.sleep(0.05)
    assert w._sizes[sid][1] is None
    for _ in range(3):
        assert next(r for r in w.sessions() if r["id"] == sid)["diff"] is None
    assert calls == [sid]


def test_a_session_without_a_repository_has_no_size_and_starts_no_work(tmp_path):
    w = Workspace(str(tmp_path / "ws"))
    record, _ = w.prepare(None, "shell", "Just a shell")
    assert next(r for r in w.sessions() if r["id"] == record["id"])["diff"] is None
    assert w._sizer is False and not w._sizes_wanted


def test_an_edit_that_keeps_a_file_s_size_in_the_same_second_still_shows(tmp_path):
    # git trusts a file that looks unchanged (here: the same size and time, all it is told to compare) unless its entry
    # is as new as the index file itself; the copy of the index the changes are read through keeps the index's time
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Same size")
    work = os.path.join(w._dir(record["id"]), "work")
    git("config", "core.checkStat", "minimal", cwd=work)  # compared: the size, and the time to the whole second
    git("config", "core.trustCtime", "false", cwd=work)
    path = os.path.join(work, "README.md")
    before = os.stat(path)
    _write(work, "README.md", "more\n")                    # "demo\n" was: the same size
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    index = git("rev-parse", "--git-path", "index", cwd=work)
    index = index if os.path.isabs(index) else os.path.join(work, index)
    os.utime(index, ns=(before.st_atime_ns, before.st_mtime_ns))       # written in the same instant
    while time.time() < before.st_mtime // 1 + 1.05:                   # and read in a later second
        time.sleep(0.05)
    assert [f["path"] for f in w.changes(record["id"])["files"]] == ["README.md"]
    assert w._change_size(record["id"]) == {"files": 1, "added": 1, "removed": 1, "uncommitted": True}


def test_a_size_slow_to_work_out_is_kept_the_longer(tmp_path, monkeypatch):
    w, sid, work = _grown(tmp_path)
    monkeypatch.setattr(workspace, "SIZE_FRESH_ENDED", 0)
    assert _listed(w, sid)["files"] == 2 and _idle(w)
    with w._size_lock:                                     # as if it had taken ten seconds
        when, size, _ = w._sizes[sid]
        w._sizes[sid] = (when, size, 10.0)
    _write(work, "third.txt", "x\n")
    for _ in range(5):
        assert next(r for r in w.sessions() if r["id"] == sid)["diff"]["files"] == 2
    assert w._sizer is False and not w._sizes_wanted       # 300 s before it is due again



def test_neither_a_size_nor_a_look_at_the_changes_writes_to_the_repository_s_objects(tmp_path):
    w, sid, work = _grown(tmp_path)
    with open(os.path.join(work, "big.bin"), "wb") as fh:
        fh.write(os.urandom(256 * 1024))                   # new, not ignored, rewritten between looks
    w.changes(sid)                                          # the empty blob, once, for the files only intended
    count = lambda: int(next(l for l in git("count-objects", "-v", cwd=work).splitlines() if l.startswith("count:")).split()[1])
    before = count()
    for _ in range(3):
        with open(os.path.join(work, "big.bin"), "wb") as fh:
            fh.write(os.urandom(256 * 1024))
        w.changes(sid)
        w._change_size(sid)
    assert count() == before


def test_a_size_reads_the_base_a_finished_update_left(tmp_path):
    w, sid, work = _grown(tmp_path)
    head = git("rev-parse", "HEAD", cwd=work)
    w._update(sid, lambda r: r.update(merging={"id": "m1", "ref": "refs/heads/main", "target": head, "at": 1}))
    assert w._change_size(sid)["files"] == 2
    assert "merging" not in w._read(sid)                   # settled, as a look at the changes settles it


def test_a_look_at_the_changes_meanwhile_is_not_overwritten_by_a_size_begun_before_it(tmp_path, monkeypatch):
    w, sid, _ = _grown(tmp_path)
    started, go = threading.Event(), threading.Event()

    def slow(session_id):
        started.set()
        go.wait(15)
        return {"files": 99, "added": 99, "removed": 99}      # what it read before the look below
    monkeypatch.setattr(w, "_change_size", slow)
    w.sessions()
    assert started.wait(15)
    w.changes(sid)                                          # the person looks meanwhile: the size they saw is kept
    go.set()
    assert _idle(w)
    assert next(r for r in w.sessions() if r["id"] == sid)["diff"] == {"files": 2, "added": 3, "removed": 1, "uncommitted": True}


def test_a_size_says_whether_anything_is_left_to_commit(tmp_path):
    w, sid, work = _grown(tmp_path)
    assert w._change_size(sid)["uncommitted"] is True
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "all of it", cwd=work)
    size = w._change_size(sid)
    assert size == {"files": 2, "added": 3, "removed": 1, "uncommitted": False}      # changed, and all committed
    assert git("status", "--porcelain", cwd=work) == ""

