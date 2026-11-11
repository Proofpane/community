"""A Claude Code session moved into the workspace from a linked machine (moves.py; workspace.prepare's `moved`). The app
sends its transcript and a git bundle (PUT /moves/{seq}/transcript and /bundle), each kept whole until the session it
came for starts or is refused, at most 32 MiB, and gone a day after it came when no session asked for it; then it starts
the session with "moved". The workspace finds the repository by its origin, makes a worktree on the session's own branch
at the machine's head (from the bundle, else fetched from origin), lays the work the machine had not committed on its
working tree, puts the transcript where Claude Code keeps the worktree's conversations with its cwds made the
worktree's, and starts claude with --resume. Through a real workspace agent and real git repositories; claude is a
stand-in that writes what it was started with and whether, from the folder it runs in, it finds the conversation it is
told to resume, by the rule Claude Code names that folder with."""

import contextlib
import json
import os
import shutil
import stat
import subprocess
import time

import httpx
import pytest

from proofpane_workspace import moves, usage
from workspace_fixtures import CLAUDE_VERSION_SH, SECRET, Agent, git, make_repo


# ---- the files the app sends for a move ------------------------------------------------------------------------------

@pytest.fixture
def agent(tmp_path):
    """A workspace agent with nothing in it yet."""
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    yield a
    a.close()


def test_a_move_s_files_are_kept_whole_within_their_limits(agent):
    r = agent.http.put("/moves/7/transcript", content=b"{}\n")
    assert r.status_code == 200 and r.json() == {"move": "7", "file": "transcript", "size": 3}, r.text
    kept = os.path.join(agent.root, "moves", "7", "transcript")
    assert open(kept, "rb").read() == b"{}\n" and stat.S_IMODE(os.stat(kept).st_mode) == 0o600
    assert agent.http.put("/moves/7/transcript", content=b"again\n").json()["size"] == 6
    assert open(kept, "rb").read() == b"again\n" and os.listdir(os.path.dirname(kept)) == ["transcript"]
    for name, data, words in (("bundle", b"", "The move's bundle is empty."),
                              ("transcript", b"x" * (32 * 1024 * 1024 + 1), "A move's transcript is at most 32 MiB.")):
        r = agent.http.put(f"/moves/7/{name}", content=data)
        assert r.status_code == 400 and r.json()["detail"] == words, r.text[:200]
    assert open(kept, "rb").read() == b"again\n"
    assert agent.http.put("/moves/7/bundle", content=b"x" * (32 * 1024 * 1024)).status_code == 200
    for path in ("/moves/0/transcript", "/moves/07/transcript", f"/moves/{2 ** 63}/transcript", "/moves/x/bundle",
                 "/moves/7/notes", "/moves/7/..%2Ftranscript"):
        r = agent.http.put(path, content=b"x")
        assert r.status_code == 404, (path, r.text)
    assert httpx.put(agent.base + "/moves/8/transcript", content=b"x",
                     headers={"Authorization": "Bearer " + "n" * len(SECRET)}).status_code == 401
    assert not os.path.exists(os.path.join(agent.root, "moves", "8"))


def test_a_move_s_files_that_no_session_asked_for_go_a_day_after_they_came(agent):
    for seq in ("1", "2"):
        assert agent.http.put(f"/moves/{seq}/transcript", content=b"{}\n").status_code == 200
    old = time.time() - moves.KEEP - 60
    os.utime(os.path.join(agent.root, "moves", "1"), (old, old))
    recent = time.time() - moves.KEEP + 600
    os.utime(os.path.join(agent.root, "moves", "2"), (recent, recent))
    assert agent.http.put("/moves/3/transcript", content=b"{}\n").status_code == 200
    assert sorted(os.listdir(os.path.join(agent.root, "moves"))) == ["2", "3"]


def test_the_agent_reads_no_more_of_a_move_s_file_than_its_limit(monkeypatch):
    """The route stops reading a body once it is past the limit, before the workspace is asked to keep it: in process,
    with a stand-in workspace and a limit of 8 bytes."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agent
    asked = []
    stand_in = SimpleNamespace(touch=lambda: None, page=lambda: None,
                               move_file=lambda seq, name, data: asked.append((seq, name, data)) or {"size": len(data)})
    monkeypatch.setattr(moves, "MAX_FILE", 8)

    async def put(data, path="/moves/5/transcript"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(stand_in, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            return await c.put(path, content=data)

    assert asyncio.run(put(b"x" * 9)).status_code == 400
    assert asked == []
    assert asyncio.run(put(b"x" * 8)).json() == {"size": 8}
    assert asked == [("5", "transcript", b"x" * 8)]
    # nor is a body read for a name that is no move's file
    for path in ("/moves/07/transcript", "/moves/5/notes"):
        assert asyncio.run(put(b"x", path)).status_code == 404
    assert len(asked) == 1


def test_the_workspace_keeps_no_move_s_file_past_its_limit(tmp_path, monkeypatch):
    from proofpane_workspace.workspace import NotFound, Workspace, WorkspaceError
    monkeypatch.setattr(moves, "MAX_FILE", 8)
    w = Workspace(str(tmp_path / "ws"))
    with pytest.raises(WorkspaceError, match="at most"):
        w.move_file("5", "bundle", b"x" * 9)
    for seq, name in (("07", "bundle"), ("5", "notes"), (None, "bundle")):
        with pytest.raises(NotFound):
            w.move_file(seq, name, b"x")
    assert not os.path.exists(tmp_path / "ws" / "moves")
    # kept for the workspace's user alone whatever the umask it is written under (the agent sets 077 itself)
    before = os.umask(0o022)
    try:
        assert w.move_file("5", "bundle", b"x" * 8) == {"move": "5", "file": "bundle", "size": 8}
    finally:
        os.umask(before)
    assert stat.S_IMODE(os.stat(tmp_path / "ws" / "moves" / "5" / "bundle").st_mode) == 0o600



@pytest.mark.parametrize("linked", ["move", "moves"])
def test_a_move_s_file_is_never_written_through_a_link_in_place_of_its_folder(agent, tmp_path, linked):
    """A session's program can put a link to a folder of its choosing in place of a move's folder, or of the folder of
    moves: the file is refused, and nothing is written where the link points."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    moves_dir = os.path.join(agent.root, "moves")
    if linked == "move":
        os.makedirs(moves_dir, exist_ok=True)
        os.symlink(elsewhere, os.path.join(moves_dir, "9"))
    else:
        os.symlink(elsewhere, moves_dir)
    r = agent.http.put("/moves/9/transcript", content=b"{}\n")
    assert r.status_code == 400 and "not a folder of its own" in r.json()["detail"], r.text
    assert os.listdir(elsewhere) == []

# ---- the session moved -----------------------------------------------------------------------------------------------

URL = "https://git.example.com/Owner/Demo.git"      # the workspace's clone names its origin so (insteadOf: a bare here)
ON_MACHINE = "git@git.example.com:owner/demo"       # the machine names the same origin another way
SESSION = "6f1c2d3e-0a1b-4c5d-8e9f-a0b1c2d3e4f5"    # Claude Code's id for the session moved

STAND_IN = r"""#!/bin/sh
case "$1" in -v|--version) echo "2.1.274 (Claude Code)"; exit 0 ;; esac
id=""; prev=""
for a in "$@"; do [ "$prev" = "--resume" ] && id="$a"; prev="$a"; done
dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$(pwd -P | sed 's/[^A-Za-z0-9]/-/g')"
if [ -n "$id" ] && [ -f "$dir/$id.jsonl" ]; then said=found; else said="missing $dir/$id.jsonl"; fi
printf '%s\n' "$said" > "$PP_SESSION_DIR/resumed"
printf '%s\n' "$@" > "$PP_SESSION_DIR/argv.tmp" && mv "$PP_SESSION_DIR/argv.tmp" "$PP_SESSION_DIR/argv"
exec sleep 300
"""


def _seed(path):
    """The repository both clones come from: a few files, one a script, and logs ignored."""
    make_repo(str(path))
    (path / "a.txt").write_text("a\n")
    (path / "b.txt").write_text("b\n")
    (path / "run.sh").write_text("echo run\n")
    (path / ".gitignore").write_text("*.log\n")
    git("add", "-A", cwd=str(path))
    git("commit", "-q", "-m", "files", cwd=str(path))


@pytest.fixture
def rig(tmp_path, request):
    """A workspace agent whose repository `demo` names its origin by URL (git's insteadOf points that at a bare
    repository here, so a fetch from origin works with no network), and the machine's clone of the same origin. With
    the parameter "linked", the agent is given its root through a symbolic link, as a root under macOS's /tmp is."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(STAND_IN)
    (bin_dir / "claude").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    _seed(tmp_path / "seed")
    origin = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(tmp_path / "seed"), str(origin), cwd=str(tmp_path))
    (tmp_path / "real").mkdir()
    root = tmp_path / "real" / "ws"
    if getattr(request, "param", None) == "linked":
        os.symlink(tmp_path / "real", tmp_path / "link")
        root = tmp_path / "link" / "ws"
    demo = tmp_path / "real" / "ws" / "repos" / "demo"
    git("clone", "-q", str(origin), str(demo), cwd=str(tmp_path))
    git("remote", "set-url", "origin", URL, cwd=str(demo))
    git("config", f"url.{origin}.insteadOf", URL, cwd=str(demo))
    mac = tmp_path / "mac"
    git("clone", "-q", str(origin), str(mac), cwd=str(tmp_path))
    a = Agent(str(root), str(home), extra_env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    a.home, a.demo, a.origin, a.mac, a.tmp = home, demo, origin, mac, tmp_path
    yield a
    a.close()


def _capture(rig):
    """What the machine's client sends, made as it makes it: HEAD; the work not committed
    (staged, unstaged and new, git's ignored files left out) as one commit made with a temporary index, the real one
    untouched; and a bundle of what the remotes lack, or None when that is nothing. git refuses to bundle a bare
    commit's id, so the bundle is made in a repository of its own whose HEAD is the tip, with the machine's
    remote-tracking refs and its objects (borrowed): its one ref is HEAD."""
    mac = str(rig.mac)
    head = git("rev-parse", "HEAD", cwd=mac)
    index = rig.tmp / "capture-index"
    shutil.copy(os.path.join(mac, ".git", "index"), index)
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    subprocess.run(["git", "add", "-A"], cwd=mac, env=env, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=mac, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()
    changes = None
    if tree != git("rev-parse", "HEAD^{tree}", cwd=mac):
        changes = git("commit-tree", tree, "-p", "HEAD", "-m", "the work not committed", cwd=mac)
    return head, changes, _bundle(rig, changes or head)


def _bundle(rig, tip):
    """A bundle of `tip` and what it needs that the Mac's remotes lack, made as the machine's client makes it, or None
    when that is nothing."""
    mac = str(rig.mac)
    bundling = rig.tmp / "bundling"
    shutil.rmtree(bundling, ignore_errors=True)
    git("init", "-q", "--bare", str(bundling), cwd=str(rig.tmp))
    (bundling / "objects" / "info" / "alternates").write_text(os.path.join(mac, ".git", "objects") + "\n")
    for line in git("for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes", cwd=mac).splitlines():
        git("update-ref", *reversed(line.split(" ", 1)), cwd=str(bundling))
    git("update-ref", "--no-deref", "HEAD", tip, cwd=str(bundling))
    out = rig.tmp / "move.bundle"
    if out.exists():
        out.unlink()
    made = subprocess.run(["git", "bundle", "create", str(out), "HEAD", "--not", "--remotes"], cwd=str(bundling),
                          capture_output=True)
    return out.read_bytes() if made.returncode == 0 else None


def _entries(machine):
    """A transcript's entries as Claude Code writes them: some with the machine's folder as their cwd, or a folder in
    it; one in a folder whose name only begins alike, one elsewhere with the folder inside a tool's result, and one
    with no cwd."""
    return [
        {"type": "summary", "summary": "Retries", "leafUuid": "u2"},
        {"parentUuid": None, "cwd": machine, "sessionId": SESSION, "type": "user", "gitBranch": "feature",
         "message": {"role": "user", "content": "Add retries, caf\u00e9 \u2014 and a \"cwd\" in the words"}, "uuid": "u1"},
        {"parentUuid": "u1", "cwd": machine + "/sub", "sessionId": SESSION, "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}]}, "uuid": "u2"},
        {"cwd": machine + "-other", "sessionId": SESSION, "type": "user", "uuid": "u3"},
        {"cwd": "/elsewhere", "sessionId": SESSION, "type": "user", "uuid": "u4", "toolUseResult": {"cwd": machine}},
    ]


def _line(entry):
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _transcript(machine):
    return b"\n".join(_line(e) for e in _entries(machine)) + b"\nnot JSON, kept as it is\n"


def _send(rig, seq, captured, transcript=None, body=None, moved=None):
    """The app's three requests for a move (`captured` as _capture gives it): its transcript (none with False), its
    bundle when there is one, and the session, `moved` changing what the move says and `body` the rest."""
    head, changes, bundle = captured
    if transcript is not False:
        r = rig.http.put(f"/moves/{seq}/transcript", content=transcript or _transcript(str(rig.mac)))
        assert r.status_code == 200, r.text
    if bundle is not None:
        r = rig.http.put(f"/moves/{seq}/bundle", content=bundle)
        assert r.status_code == 200, r.text
    given = {"id": str(seq), "session": SESSION, "remote": ON_MACHINE, "branch": "feature", "head": head,
             "changes": changes, "cwd": str(rig.mac), "bundle": bundle is not None}
    given.update(moved or {})
    return rig.http.post("/sessions", json=dict({"program": "claude", "task": "Moved from the Mac", "moved": given},
                                                **(body or {})))


def _started(rig, sid, timeout=20):
    """What the stand-in claude was started with, and whether it found the conversation it was told to resume. The
    control window's map settings, which every claude terminal is given first (`--settings` and the session's own
    map-settings.json, workspace._with_map), are checked to be that and left out of what is returned."""
    folder = os.path.join(rig.root, "sessions", sid)
    deadline = time.time() + timeout
    while not os.path.exists(os.path.join(folder, "argv")):
        assert time.time() < deadline, "claude did not start"
        time.sleep(0.05)
    with open(os.path.join(folder, "argv")) as fh, open(os.path.join(folder, "resumed")) as said:
        argv, resumed = fh.read().splitlines(), said.read().strip()
    if argv[:1] == ["--settings"]:
        assert os.path.realpath(argv[1]) == os.path.realpath(os.path.join(folder, "map-settings.json")), argv
        argv = argv[2:]
    return argv, resumed


def _status(work):
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work, capture_output=True,
                         text=True, check=True).stdout
    return sorted(line for line in out.splitlines() if line)


def _branches(path):
    return [b for b in git("branch", "--format=%(refname:short)", cwd=str(path)).splitlines() if b.startswith("pp/")]


def test_a_moved_session_continues_its_conversation_in_a_worktree_with_its_work_as_it_was(rig):
    mac = str(rig.mac)
    git("checkout", "-q", "-b", "feature", cwd=mac)
    (rig.mac / "local.txt").write_text("committed on the Mac, never pushed\n")
    git("add", "local.txt", cwd=mac)
    git("commit", "-q", "-m", "local work", cwd=mac)
    # the work not committed: a file changed, one deleted, one staged, one new in a new folder, a mode, an ignored log
    (rig.mac / "a.txt").write_text("a, changed\n")
    os.remove(rig.mac / "b.txt")
    (rig.mac / "staged.txt").write_text("staged\n")
    git("add", "staged.txt", cwd=mac)
    os.makedirs(rig.mac / "sub")
    (rig.mac / "sub" / "new.txt").write_text("new\n")
    os.chmod(rig.mac / "run.sh", 0o755)
    (rig.mac / "debug.log").write_text("ignored\n")
    captured = head, changes, bundle = _capture(rig)
    assert changes and bundle
    # its one ref HEAD, at the work not committed: the commits are taken by the ids the move names, not by its refs
    assert git("bundle", "list-heads", str(rig.tmp / "move.bundle"), cwd=str(rig.tmp)) == f"{changes} HEAD"

    r = _send(rig, 41, captured)
    assert r.status_code == 200, r.text
    view = r.json()
    sid = view["id"]
    argv, resumed = _started(rig, sid)
    # its own conversation, as one argument of its own; no task: the task is its name here
    assert argv[-2:] == ["--resume", SESSION] and "Moved from the Mac" not in argv
    assert resumed == "found"
    assert view["repo"] == "demo" and view["branch"] == f"pp/{sid}" and view["base"] == head
    assert view["moved"] == {"id": "41", "session": SESSION, "branch": "feature", "head": head, "changes": changes}

    work = os.path.join(rig.root, "sessions", sid, "work")
    assert git("rev-parse", "HEAD", cwd=work) == head
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == f"pp/{sid}"
    # the files as they were on the Mac, none of them committed nor staged: the index is head's
    assert git("diff", "--cached", "--name-only", cwd=work) == ""
    assert _status(work) == [" D b.txt", " M a.txt", " M run.sh", "?? staged.txt", "?? sub/new.txt"]
    assert open(os.path.join(work, "a.txt")).read() == "a, changed\n"
    assert open(os.path.join(work, "sub", "new.txt")).read() == "new\n"
    assert os.stat(os.path.join(work, "run.sh")).st_mode & stat.S_IXUSR
    assert not os.path.exists(os.path.join(work, "debug.log"))
    diff = rig.http.get(f"/sessions/{sid}/diff").json()
    assert sorted(f["path"] for f in diff["files"]) == ["a.txt", "b.txt", "run.sh", "staged.txt", "sub/new.txt"]

    # the transcript where Claude Code keeps the worktree's conversations: the cwds in the Mac's folder made the
    # worktree's, everything else as it was, byte for byte
    real = os.path.realpath(work)
    kept = rig.home / ".claude" / "projects" / usage.slug(real) / f"{SESSION}.jsonl"
    expected = _entries(mac)
    expected[1]["cwd"], expected[2]["cwd"] = real, real + "/sub"
    assert kept.read_bytes() == b"\n".join(_line(e) for e in expected) + b"\nnot JSON, kept as it is\n"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    # the files the app sent are gone once taken
    assert not os.path.exists(os.path.join(rig.root, "moves", "41"))


def _behind(rig):
    """origin moves on after the workspace cloned it, and the Mac pulls that: the workspace's clone lacks it."""
    other = rig.tmp / "other"
    git("clone", "-q", str(rig.origin), str(other), cwd=str(rig.tmp))
    (other / "later.txt").write_text("pushed after the workspace cloned\n")
    git("add", "later.txt", cwd=str(other))
    git("commit", "-q", "-m", "later", cwd=str(other))
    git("push", "-q", "origin", "main", cwd=str(other))
    git("pull", "-q", "--ff-only", cwd=str(rig.mac))
    later = git("rev-parse", "HEAD", cwd=str(other))
    assert subprocess.run(["git", "cat-file", "-e", later], cwd=str(rig.demo)).returncode != 0
    return later


def test_a_session_whose_commits_are_all_on_the_remote_moves_without_a_bundle(rig):
    later = _behind(rig)
    captured = head, changes, bundle = _capture(rig)
    assert head == later and changes is None and bundle is None
    # the remote as the machine's client sends it, its host and path alone; and the repository named, the one its
    # origin finds
    r = _send(rig, 42, captured, body={"repo": "demo"}, moved={"remote": "git.example.com/owner/demo"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    work = os.path.join(rig.root, "sessions", sid, "work")
    # fetched from the workspace repository's own origin, the commit asked for by its id
    assert git("rev-parse", "HEAD", cwd=work) == later and _status(work) == []
    assert _started(rig, sid) == (["--resume", SESSION], "found")


def test_what_a_bundle_needs_and_the_clone_lacks_is_fetched_from_origin_first(rig):
    later = _behind(rig)
    (rig.mac / "mine.txt").write_text("on top of what origin has now\n")
    git("add", "mine.txt", cwd=str(rig.mac))
    git("commit", "-q", "-m", "mine", cwd=str(rig.mac))
    (rig.mac / "a.txt").write_text("a, not committed\n")
    captured = head, _, _ = _capture(rig)
    assert moves.prerequisites((rig.tmp / "move.bundle").read_bytes()) == [later]
    r = _send(rig, 43, captured)
    assert r.status_code == 200, r.text
    work = os.path.join(rig.root, "sessions", r.json()["id"], "work")
    assert git("rev-parse", "HEAD", cwd=work) == head and git("rev-parse", "HEAD~1", cwd=work) == later
    assert _status(work) == [" M a.txt"]


def test_a_commit_origin_has_on_no_branch_is_fetched_by_its_id(rig):
    # reachable on origin from a tag only, which a fetch of origin's branches does not bring
    other = rig.tmp / "tagger"
    git("clone", "-q", str(rig.origin), str(other), cwd=str(rig.tmp))
    git("checkout", "-q", "--detach", cwd=str(other))
    (other / "t.txt").write_text("tagged\n")
    git("add", "t.txt", cwd=str(other))
    git("commit", "-q", "-m", "tagged", cwd=str(other))
    tagged = git("rev-parse", "HEAD", cwd=str(other))
    git("push", "-q", "origin", f"{tagged}:refs/tags/only-a-tag", cwd=str(other))
    r = _send(rig, 50, (tagged, None, None))
    assert r.status_code == 200, r.text
    assert git("rev-parse", "HEAD", cwd=os.path.join(rig.root, "sessions", r.json()["id"], "work")) == tagged


def test_where_origin_gives_no_commit_by_its_id_each_of_its_branches_is_fetched(rig):
    """A host that gives no commit but its branches' tips when asked by id (as git's protocol version 0 does): the
    commit the Mac was on, which origin has under a later one, comes with a fetch of origin's branches."""
    git("config", "protocol.version", "0", cwd=str(rig.demo))
    other = rig.tmp / "deeper"
    git("clone", "-q", str(rig.origin), str(other), cwd=str(rig.tmp))
    for n in (1, 2):
        (other / f"n{n}.txt").write_text(f"{n}\n")
        git("add", f"n{n}.txt", cwd=str(other))
        git("commit", "-q", "-m", f"n{n}", cwd=str(other))
    git("push", "-q", "origin", "main", cwd=str(other))
    wanted = git("rev-parse", "HEAD~1", cwd=str(other))         # origin has it under main's new tip, not as one
    assert subprocess.run(["git", "cat-file", "-e", wanted], cwd=str(rig.demo)).returncode != 0
    r = _send(rig, 53, (wanted, None, None))
    assert r.status_code == 200, r.text
    assert git("rev-parse", "HEAD", cwd=os.path.join(rig.root, "sessions", r.json()["id"], "work")) == wanted


@pytest.mark.parametrize("session", [SESSION, "s" + "0123456789" * 9 + "_-x-_-x-_"])
def test_a_moved_session_started_again_resumes_the_conversation_it_came_with(rig, session):
    assert len(session) in (36, 100)
    sid = _send(rig, 44, _capture(rig), transcript=b'{"type":"summary"}\n', moved={"session": session}).json()["id"]
    folder = os.path.join(rig.root, "sessions", sid)
    assert _started(rig, sid)[0][-2:] == ["--resume", session]
    for kept_by_hooks, resumed in ((None, session), ("other-1", "other-1"), ("gone-2", session)):
        assert rig.http.post(f"/sessions/{sid}/stop").status_code == 200
        os.remove(os.path.join(folder, "argv"))
        if kept_by_hooks:
            # what its hooks named last comes first, while Claude Code still has its transcript; else the one it came with
            with open(os.path.join(folder, "activity.json"), "w") as fh:
                json.dump({"state": "idle", "at": 1, "program_session": kept_by_hooks}, fh)
        if kept_by_hooks == "other-1":
            work = os.path.realpath(os.path.join(folder, "work"))
            (rig.home / ".claude" / "projects" / usage.slug(work) / "other-1.jsonl").write_text("{}\n")
        assert rig.http.post(f"/sessions/{sid}/start", json={}).status_code == 200
        assert _started(rig, sid)[0][-2:] == ["--resume", resumed]


@pytest.mark.parametrize("rig", ["linked"], indirect=True)
def test_through_a_linked_root_the_conversation_is_named_from_the_worktree_s_real_folder(rig):
    """Claude Code names its projects folder from the folder it runs in as the system gives it, links resolved: the
    transcript goes there, and its cwds are that folder."""
    (rig.mac / "a.txt").write_text("a, not committed\n")
    r = _send(rig, 51, _capture(rig))
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert _started(rig, sid) == (["--resume", SESSION], "found")
    work = os.path.join(rig.root, "sessions", sid, "work")
    real = os.path.realpath(work)
    assert real != work and real.startswith(str(rig.tmp / "real") + os.sep)
    entries = (rig.home / ".claude" / "projects" / usage.slug(real) / f"{SESSION}.jsonl").read_bytes().split(b"\n")
    assert json.loads(entries[1])["cwd"] == real and json.loads(entries[2])["cwd"] == real + "/sub"


def test_a_moved_session_s_conversation_is_in_the_folder_of_the_account_it_runs_on(rig):
    account = rig.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()
    r = _send(rig, 45, _capture(rig), body={"account": account["id"]})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    real = os.path.realpath(os.path.join(rig.root, "sessions", sid, "work"))
    mine = os.path.join(rig.root, "agent-accounts", account["id"], "claude", "projects", usage.slug(real))
    assert os.path.isfile(os.path.join(mine, f"{SESSION}.jsonl"))
    assert not (rig.home / ".claude" / "projects" / usage.slug(real)).exists()
    assert _started(rig, sid) == (["--resume", SESSION], "found")      # its claude, given the account's folder


def test_a_move_sent_again_gets_the_session_it_started_and_never_a_second(rig):
    """The app sends a move again when the answer to its first try did not reach it: the same session comes back,
    running, its claude not started again, and the files sent again go; once that session is removed, the move is
    refused."""
    (rig.mac / "a.txt").write_text("a, not committed\n")
    captured = _capture(rig)
    first = _send(rig, 60, captured)
    assert first.status_code == 200, first.text
    sid = first.json()["id"]
    _started(rig, sid)
    os.remove(os.path.join(rig.root, "sessions", sid, "argv"))
    again = _send(rig, 60, captured)                        # its files sent again, then the session
    assert again.status_code == 200 and again.json()["id"] == sid and again.json()["running"], again.text
    assert [s["id"] for s in rig.http.get("/sessions").json()["sessions"]] == [sid]
    assert _branches(rig.demo) == [f"pp/{sid}"]
    assert not os.path.exists(os.path.join(rig.root, "moves", "60"))
    time.sleep(0.5)
    assert not os.path.exists(os.path.join(rig.root, "sessions", sid, "argv"))     # its claude was not started again
    assert rig.http.delete(f"/sessions/{sid}").status_code == 200
    r = _send(rig, 60, captured)
    assert r.status_code == 400 and f"This move started session {sid} already" in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == []
    assert not os.path.exists(os.path.join(rig.root, "moves", "60"))


def test_a_move_that_only_a_session_s_record_says_it_started_is_refused_never_answered(rig):
    """A session's record names the move it came from, and the session's programs can write it: a record that claims a
    move is never answered with, so no move gets another session than its own, but the move is refused, so it never
    makes a second one either: after the agent started again (its own memory of the moves gone), and for a record a
    program wrote."""
    from types import SimpleNamespace
    shell = rig.http.post("/sessions", json={"repo": "demo", "program": "shell", "task": "A shell"}).json()
    path = os.path.join(rig.root, "sessions", shell["id"], "session.json")
    with open(path) as fh:
        record = json.load(fh)
    record["moved"] = {"id": "62", "session": SESSION}
    with open(path, "w") as fh:
        json.dump(record, fh)
    r = _send(rig, 62, _capture(rig))
    assert r.status_code == 400 and f"This move started session {shell['id']} already" in r.json()["detail"], r.text
    assert [s["id"] for s in rig.http.get("/sessions").json()["sessions"]] == [shell["id"]]
    assert not os.path.exists(os.path.join(rig.root, "moves", "62"))

    captured = _capture(rig)
    sid = _send(rig, 61, captured).json()["id"]
    _started(rig, sid)
    rig.close()
    os.unlink(os.path.join(rig.root, "agent.json"))        # the stopped agent's, which names its port
    again = Agent(rig.root, str(rig.home), extra_env={"PATH": f"{rig.tmp / 'bin'}:{os.environ['PATH']}"})
    try:
        r = _send(SimpleNamespace(http=again.http, mac=rig.mac), 61, captured)
        assert r.status_code == 400 and f"This move started session {sid} already" in r.json()["detail"], r.text
        assert sorted(s["id"] for s in again.http.get("/sessions").json()["sessions"]) == sorted([shell["id"], sid])
        assert not os.path.exists(os.path.join(rig.root, "moves", "61"))
    finally:
        again.close()


def _second_repository(rig, url):
    make_repo(os.path.join(rig.root, "repos", "other"), origin=url)


@pytest.mark.parametrize("change, words", [
    ({"body": {"program": "codex"}}, "a claude session"),
    ({"body": {"model_provider": "kimi"}}, "on no other provider"),
    ({"body": {"base": "main"}}, "brings its own commit"),
    ({"body": {"pull": 3}}, "brings its own commit"),
    ({"body": {"teleport": "session_x"}}, "not both"),
    ({"body": {"repo": "other"}}, "the one whose origin is the machine's: demo"),
    ({"session": "-starts-with-a-dash"}, "1 to 100 letters"),
    ({"session": "has space"}, "1 to 100 letters"),
    ({"session": "x" * 101}, "1 to 100 letters"),
    ({"head": "HEAD"}, "head is a commit's id"),
    ({"changes": "main"}, "changes are a commit's id"),
    ({"cwd": "relative/folder"}, "full path on the machine"),
    ({"cwd": "/"}, "full path on the machine"),
    ({"bundle": "yes"}, "true or false"),
    ({"remote": ""}, "URL of its origin"),
    ({"remote": "https://github.com/someone/else.git"}, "The repository is not in the workspace: clone it there first."),
    ({"remote": "/Users/me/origin.git"}, "The repository is not in the workspace: clone it there first."),
    ({"transcript": False}, "its transcript was not sent"),
    ({"bundle": True}, "its bundle was not sent"),
    ({"head": "0123456789abcdef0123456789abcdef01234567"}, "is neither in what was sent nor on the repository's origin"),
])
def test_what_a_move_cannot_be_given_is_refused_and_its_files_go(rig, change, words):
    _second_repository(rig, "https://git.example.com/owner/other.git")
    change = dict(change)
    r = _send(rig, 46, _capture(rig), transcript=change.pop("transcript", None), body=change.pop("body", None),
              moved=change)
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == []
    assert os.listdir(os.path.join(rig.root, "sessions")) == []
    assert _branches(rig.demo) == []
    assert not os.path.exists(os.path.join(rig.root, "moves", "46"))


@pytest.mark.parametrize("moved, words", [
    ("yes", "comes with its move"),
    ({"id": "0"}, "seq, a whole number"),
    ({"id": "07"}, "seq, a whole number"),
    ({"id": str(2 ** 63)}, "seq, a whole number"),
    ({"id": True}, "seq, a whole number"),
])
def test_a_move_that_names_no_move_is_refused(rig, moved, words):
    r = rig.http.post("/sessions", json={"program": "claude", "task": "Moved", "moved": moved})
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == []


def test_a_bundle_git_cannot_take_or_that_builds_on_a_commit_found_nowhere_is_refused(rig):
    head = git("rev-parse", "HEAD", cwd=str(rig.mac))
    r = _send(rig, 48, (head, None, b"# v2 git bundle\nnot one at all\n"))
    assert r.status_code == 400 and r.json()["detail"] == ("The moved session's bundle is not one git can take into "
                                                           "its repository."), r.text
    # one made in another repository, built on a commit neither the workspace's clone nor origin has: a commit of its
    # own, since make_repo's first commit is the seed's own when both are made in the same second (the same tree,
    # message, author and time)
    elsewhere = rig.tmp / "elsewhere"
    make_repo(str(elsewhere))
    (elsewhere / "only-here.txt").write_text("only here\n")
    git("add", "only-here.txt", cwd=str(elsewhere))
    git("commit", "-q", "-m", "only here", cwd=str(elsewhere))
    first = git("rev-parse", "HEAD", cwd=str(elsewhere))
    (elsewhere / "z.txt").write_text("z\n")
    git("add", "z.txt", cwd=str(elsewhere))
    git("commit", "-q", "-m", "z", cwd=str(elsewhere))
    git("bundle", "create", str(rig.tmp / "elsewhere.bundle"), "main", "^" + first, cwd=str(elsewhere))
    r = _send(rig, 49, (git("rev-parse", "HEAD", cwd=str(elsewhere)), None, (rig.tmp / "elsewhere.bundle").read_bytes()))
    assert r.status_code == 400 and f"builds on commit {first[:12]}, which is neither" in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == [] and _branches(rig.demo) == []
    assert os.listdir(os.path.join(rig.root, "moves")) == []        # both moves' files gone


@pytest.mark.parametrize("name, swap, words", [("transcript", "link", "its transcript was not sent"),
                                               ("bundle", "link", "its bundle was not sent"),
                                               ("transcript", "folder", "its transcript was not sent")])
def test_a_move_s_file_swapped_for_a_link_or_a_folder_is_not_read(rig, name, swap, words):
    (rig.mac / "a.txt").write_text("a, not committed\n")
    head, changes, bundle = _capture(rig)
    for file, data in (("transcript", _transcript(str(rig.mac))), ("bundle", bundle)):
        assert rig.http.put(f"/moves/52/{file}", content=data).status_code == 200
    # what a session's program, running as the workspace's user, could do: the file swapped for a link to another, or
    # for a folder
    kept = os.path.join(rig.root, "moves", "52", name)
    os.rename(kept, kept + ".real")
    if swap == "link":
        os.symlink(kept + ".real", kept)
    else:
        os.mkdir(kept)
    r = rig.http.post("/sessions", json={"program": "claude", "task": "Moved", "moved": {
        "id": "52", "session": SESSION, "remote": ON_MACHINE, "branch": None, "head": head, "changes": changes,
        "cwd": str(rig.mac), "bundle": True}})
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == []



@pytest.mark.parametrize("linked", ["move", "moves"])
def test_a_link_in_place_of_a_move_s_folder_is_never_followed(rig, linked):
    """A session's program can put a link in place of the move's folder, or of the folder of moves, to a folder of its
    choosing that holds a transcript and a bundle: a move's files are read only from folders of the workspace's own, so
    the move is refused, and what the link points at is neither taken nor removed."""
    (rig.mac / "a.txt").write_text("a, not committed\n")
    head, changes, bundle = _capture(rig)
    for file, data in (("transcript", _transcript(str(rig.mac))), ("bundle", bundle)):
        assert rig.http.put(f"/moves/54/{file}", content=data).status_code == 200
    moves_dir = os.path.join(rig.root, "moves")
    elsewhere = rig.tmp / "elsewhere"
    if linked == "move":
        os.rename(os.path.join(moves_dir, "54"), elsewhere)
        os.symlink(elsewhere, os.path.join(moves_dir, "54"))
    else:
        os.rename(moves_dir, elsewhere)
        os.symlink(elsewhere, moves_dir)
    r = rig.http.post("/sessions", json={"program": "claude", "task": "Moved", "moved": {
        "id": "54", "session": SESSION, "remote": ON_MACHINE, "branch": None, "head": head, "changes": changes,
        "cwd": str(rig.mac), "bundle": True}})
    assert r.status_code == 400 and "its transcript was not sent" in r.json()["detail"], r.text
    assert rig.http.get("/sessions").json()["sessions"] == [] and _branches(rig.demo) == []
    held = elsewhere if linked == "move" else elsewhere / "54"
    assert sorted(os.listdir(held)) == ["bundle", "transcript"]


def test_a_pipe_swapped_in_for_the_bundle_after_its_look_is_refused_at_once_and_the_lock_is_free_after(
        tmp_path, monkeypatch):
    """A session's program can swap the bundle for a pipe, which an open with no writer waits on for ever, after the
    workspace has looked at its path: what is opened is checked, and never waited on, so the move is refused within
    seconds and the agent's lock, under which a session is made, is free after. In process, through the agent's routes,
    with a stand-in claude on the PATH and the look answered as the regular file's it was before the swap."""
    import asyncio
    from proofpane_workspace import agent
    from proofpane_workspace.workspace import Workspace
    from test_workspace_files import _within_seconds
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\n" + CLAUDE_VERSION_SH + "exec sleep 300\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    root = tmp_path / "ws"
    head = make_repo(str(root / "repos" / "demo"), origin=URL)
    w = Workspace(str(root))
    w.move_file("70", "transcript", b'{"type":"summary"}\n')
    w.move_file("70", "bundle", b"# v2 git bundle\n\n")
    bundle = root / "moves" / "70" / "bundle"
    looked = os.lstat(bundle)
    os.remove(bundle)
    os.mkfifo(bundle)
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: looked if os.fspath(p) == str(bundle) else real_lstat(p, *a, **k))
    moved = {"id": "70", "session": SESSION, "remote": ON_MACHINE, "branch": None, "head": head, "changes": None,
             "cwd": "/Users/me/demo", "bundle": True}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.make_app(w, SECRET)),
                                     base_url="http://agent", headers={"Authorization": "Bearer " + SECRET}) as c:
            refused = await c.post("/sessions", json={"program": "claude", "task": "Moved", "moved": moved})
            # a route that takes the agent's lock answers at once: the lock was let go
            locked = await asyncio.wait_for(c.post("/repos/link", json={"path": "/nowhere"}), 5)
            return refused, locked

    try:
        refused, locked = _within_seconds(lambda: asyncio.run(go()), 20)
    finally:
        # a reader left waiting on the pipe (the fault this test is for) is let go, so that the thread the agent's route
        # waits on, which the run's end would wait for in turn, can end
        with contextlib.suppress(OSError):
            os.close(os.open(bundle, os.O_WRONLY | os.O_NONBLOCK))
    assert refused.status_code == 400 and "its bundle was not sent" in refused.json()["detail"], refused.text
    assert locked.status_code == 400 and "Only the desktop app's workspace" in locked.json()["detail"], locked.text
    assert w.sessions() == [] and not os.path.exists(root / "moves" / "70")


@pytest.mark.parametrize("parents", ["another", "two", "none"])
def test_work_not_committed_that_is_no_commit_on_head_is_refused(rig, parents):
    """The work not committed comes as a commit whose only parent is head, as the machine makes it: one on another
    commit, a merge, or one with no parent is refused before anything is made."""
    mac = str(rig.mac)
    head = git("rev-parse", "HEAD", cwd=mac)
    (rig.mac / "a.txt").write_text("a, not committed\n")
    git("add", "a.txt", cwd=mac)
    tree = git("write-tree", cwd=mac)
    on = {"another": ["-p", "HEAD~1"], "two": ["-p", "HEAD", "-p", "HEAD~1"], "none": []}[parents]
    changes = git("commit-tree", tree, *on, "-m", "the work not committed", cwd=mac)
    r = _send(rig, 55, (head, changes, _bundle(rig, changes)))
    assert r.status_code == 400 and r.json()["detail"] == "The work not committed is not a commit on head.", r.text
    assert rig.http.get("/sessions").json()["sessions"] == [] and _branches(rig.demo) == []
    assert os.listdir(os.path.join(rig.root, "sessions")) == []

@pytest.mark.parametrize("on_account", [False, True])
def test_a_move_whose_conversation_cannot_be_placed_leaves_nothing_behind(rig, on_account):
    # Claude Code's projects folder is a file: the transcript cannot go there, after the worktree was made
    body = {}
    folder = rig.home / ".claude"
    if on_account:
        account = rig.http.post("/accounts", json={"kind": "claude", "name": "Work"}).json()
        body["account"] = account["id"]
        folder = os.path.join(rig.root, "agent-accounts", account["id"], "claude")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "projects"), "w") as fh:
        fh.write("not a folder\n")
    (rig.mac / "a.txt").write_text("a, not committed\n")
    r = _send(rig, 47, _capture(rig), body=body)
    assert r.status_code == 400 and "could not be put where Claude Code keeps it" in r.json()["detail"], r.text
    kept = os.path.join(rig.root, "agent-accounts.json")      # no account bound to the session that was not made
    assert on_account == os.path.exists(kept)
    assert not on_account or json.load(open(kept))["sessions"] == {}
    assert rig.http.get("/sessions").json()["sessions"] == []
    assert os.listdir(os.path.join(rig.root, "sessions")) == []
    assert _branches(rig.demo) == []
    listed = git("worktree", "list", "--porcelain", cwd=str(rig.demo))
    assert [line for line in listed.splitlines() if line.startswith("worktree ")] == [
        "worktree " + os.path.realpath(rig.demo)]
    assert not os.path.exists(os.path.join(rig.root, "moves", "47"))


@pytest.mark.parametrize("url", [
    "https://github.com/Owner/Repo.git", "https://github.com/owner/repo", "https://user:token@github.com:443/owner/repo/",
    "git@github.com:Owner/Repo.git", "github.com:owner/repo", "git@github.com:/owner/repo.git",
    "ssh://git@github.com/owner/repo.git", "ssh://git@GitHub.com:22/Owner/Repo", "git://github.com/owner/repo.git",
    "http://GITHUB.COM/owner/repo.git/", "  https://github.com/owner/repo.git  ", "github.com/owner/repo",
    "GitHub.com/Owner/Repo.git/",
])
def test_a_repository_s_url_is_one_name_whatever_its_form(url):
    assert moves.remote_name(url) == "github.com/owner/repo"


def test_different_repositories_have_different_names():
    names = [moves.remote_name(u) for u in ("https://github.com/owner/repo", "https://github.com/owner/repo2",
                                            "https://github.com/other/repo", "https://gitlab.com/owner/repo",
                                            "https://gitlab.com/group/owner/repo.git")]
    assert len(set(names)) == 5 and names[-1] == "gitlab.com/group/owner/repo"


@pytest.mark.parametrize("url", [
    "/srv/git/repo.git", "file:///srv/git/repo.git", "../repo", "./a:b/c", "C:\\repos\\x", "https://github.com/repo",
    "https://github.com/owner/../repo", "ftp://github.com/owner/repo", "", "github.com", "https://github.com/a/b\n",
    "https://[::1]/owner/repo", "repos/owner/repo", ".github.com/owner/repo", None, 7,
])
def test_what_names_no_repository_on_a_host_has_no_name(url):
    assert moves.remote_name(url) is None


def test_a_transcript_s_cwds_in_the_machine_s_folder_are_made_the_worktree_s_and_nothing_else_changes():
    machine, work = "/Users/me/demo", "/w/sessions/abc/work"
    lines = [
        b'{"cwd":"/Users/me/demo","type":"user","n":1.5,"big":12345678901234567890}',
        b'{"type":"user","cwd":"/Users/me/demo/sub/deeper","message":{"content":"caf\xc3\xa9"}}',
        b'{"cwd":"/Users/me/demo/","type":"user"}',
        b'{"cwd":"/Users/me/demonstration","type":"user"}',
        b'{"cwd": "/elsewhere", "toolUseResult": {"cwd": "/Users/me/demo"}}',
        b'{"cwd":"/Users/me/demo","text":"\\ud83d alone"}',
        b'{"cwd":"/Users/me/demo", broken',
        b'\xff\xfe not text "cwd"',
        b'["cwd", "/Users/me/demo"]',
        b"",
    ]
    out = moves.rewrite(b"\n".join(lines), machine + "/", work).split(b"\n")
    assert out[0] == b'{"cwd":"/w/sessions/abc/work","type":"user","n":1.5,"big":12345678901234567890}'
    assert out[1] == b'{"type":"user","cwd":"/w/sessions/abc/work/sub/deeper","message":{"content":"caf\xc3\xa9"}}'
    assert out[2] == b'{"cwd":"/w/sessions/abc/work/","type":"user"}'
    assert out[3:5] == lines[3:5]                 # a folder whose name only begins alike; a cwd elsewhere
    assert out[5] == b'{"cwd":"/w/sessions/abc/work","text":"\\ud83d alone"}'    # half a character kept as its escape
    assert out[6:] == lines[6:]                   # not JSON, not text, not an object; the end kept as it was


def test_a_conversation_is_written_whole_for_the_workspace_s_user_alone_whatever_the_umask(tmp_path):
    before = os.umask(0o022)
    try:
        moves.write_whole(str(tmp_path / "projects" / "-w" / "s.jsonl"), b"{}\n")
    finally:
        os.umask(before)
    kept = tmp_path / "projects" / "-w" / "s.jsonl"
    assert kept.read_bytes() == b"{}\n" and stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert os.listdir(tmp_path / "projects" / "-w") == ["s.jsonl"]


def test_a_bundle_s_prerequisites_are_read_from_its_header(tmp_path):
    repo = tmp_path / "r"
    first = make_repo(str(repo))
    (repo / "x.txt").write_text("x\n")
    git("add", "x.txt", cwd=str(repo))
    git("commit", "-q", "-m", "second", cwd=str(repo))
    git("bundle", "create", str(tmp_path / "b"), "main", "^" + first, cwd=str(repo))
    assert moves.prerequisites((tmp_path / "b").read_bytes()) == [first]
    git("bundle", "create", str(tmp_path / "whole"), "main", cwd=str(repo))
    assert moves.prerequisites((tmp_path / "whole").read_bytes()) == []
    assert moves.prerequisites(b"-" + first.encode() + b" x\n\nPACK") == [] and moves.prerequisites(b"") == []
