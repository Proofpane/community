"""A Claude Code session moved from a linked machine into the hosted workspace through the three real parts at once: the
app served on a local port (ade/link_commands.py, ade/moves.py), the machine's link client as the owner runs it
(ade_link.py pair, pin, history and move), and a workspace agent (proofpane_workspace/moves.py, workspace.py). Each
part's own tests drive it against stand-ins of the others (test_ade_link_moves.py, test_ade_link_client_move.py,
test_workspace_move.py); here nothing stands in but the owner's passkey, the tests' software authenticator, and the
workspace's claude, which writes what it was started with and whether it finds the conversation it is told to resume.
The repositories are real ones in tmp_path: an origin, the machine's clone with work of every kind not on origin, and
the workspace's clone. The app's hand-off of an uploaded move to the workspace runs in place, as test_ade_link_moves.py
has it, not in a thread of its own, so the move's end is read as soon as it returns; the test runs it once the link's
client is done, as a thread would run beside that client, not inside the client's request."""

import json
import os
import re
import stat
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest

import ade_link
from conftest import signed_up
from proofpane_open import paths, store  # noqa: F401  (paths puts the open packages on sys.path first)
from proofpane_open.ade import holds, moves, workspaces
from proofpane_core.canonical import canonicalize
from proofpane_workspace import usage
from test_ade_link_client import pair, run, served  # noqa: F401  (served: a fixture)
from test_ade_link_moves import Provider
from test_passkeys import Authenticator, add, as_step
from test_workspace_move import STAND_IN, _started
from workspace_fixtures import SECRET, Agent

SCRIPT = os.path.abspath(ade_link.__file__)
SESSION = "6f1c2d3e-0a1b-4c5d-8e9f-a0b1c2d3e4f5"         # Claude Code's id for the session moved
TITLE = "Make sign-in work"
ON_MACHINE = "git@git.example.test:acme/demo.git"        # the origin as the machine's clone names it: git's ssh form
SENT = "git.example.test:acme/demo.git"                  # what the machine's client sends of it: its host and path
IN_WORKSPACE = "https://git.example.test/Acme/Demo.git"  # the same repository as the workspace's clone names it
# the workspace's claude is test_workspace_move.py's stand-in, signed in as Claude Code's own status says it: the app
# asks the workspace that before it hands a move over (test_workspace_move.py posts the move to the agent itself)
SIGNED_IN = """if [ "$1" = auth ] && [ "$2" = status ]; then
  echo '{"loggedIn": true, "authMethod": "claude.ai", "email": "ana@example.test"}'; exit 0
fi
"""
CLAUDE = STAND_IN.replace("#!/bin/sh\n", "#!/bin/sh\n" + SIGNED_IN, 1)
# the machine's work, as the workspace's worktree must hold it: every file but the log git ignores
CARRIED = (".gitignore", "README.md", "docs/new.md", "notes.txt", "run.sh", "src/app.py", "staged.txt")


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch):
    # these tests may run inside a Claude Code session, whose inbox, folders, keys and repository they must not reach
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CONFIG_DIR", "ADE_OWNER_KEYS",
                 "ADE_LINK_DIR", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)


def git(*args, cwd=None):
    """git with the machine's environment (its own config), taking no optional lock, so a read writes nothing."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).stdout.strip()


def conversation(top, machine):
    """The session's transcript entries as Claude Code writes them, `top` the folder it worked in: a title, a turn in
    the repository's top and one in its src folder; then a turn in a folder whose name only begins like the
    repository's, with a tool's result naming the repository, which stay the machine's (`machine`) wherever the session
    goes."""
    return [
        {"type": "custom-title", "customTitle": TITLE, "sessionId": SESSION},
        {"parentUuid": None, "cwd": top, "sessionId": SESSION, "type": "user", "gitBranch": "sign-in",
         "entrypoint": "cli", "timestamp": "2026-10-08T10:00:00Z", "uuid": "u1",
         "message": {"role": "user", "content": "Make sign-in work, caf\u00e9 \u2014 tests too"}},
        {"parentUuid": "u1", "cwd": top + "/src", "sessionId": SESSION, "type": "assistant",
         "timestamp": "2026-10-08T10:01:00Z", "uuid": "a1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done in src/app.py."}]}},
        {"parentUuid": "a1", "cwd": machine + "-notes", "sessionId": SESSION, "type": "user",
         "timestamp": "2026-10-08T10:02:00Z", "uuid": "u2", "toolUseResult": {"cwd": machine},
         "message": {"role": "user", "content": "note it"}},
    ]


def transcript(top, machine):
    """The transcript's bytes: one entry a line, compact, its text as it is, as Claude Code writes them."""
    return b"".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                    for e in conversation(top, machine))


@pytest.fixture
def rig(tmp_path, client, served, monkeypatch, capsys):  # noqa: F811  (served: the imported fixture)
    """The three parts, ready for a move. The app, served, with the owner signed up and a passkey added. The owner's
    machine: a home, Claude Code's folder given by CLAUDE_CONFIG_DIR, a clone of an origin that is a bare repository
    here, the session's transcript (the session not running), and a link paired through the app and pinned to the
    owner's keys, its history.json listing the session's project. A workspace agent, whose repository is a clone of
    the same origin, named in another form; the account's workspace as workspaces.for_user gives it, and each hand-off
    the app starts kept for move_run to run in place. Each test puts the machine's repository in the state it moves."""
    machine, ws = tmp_path / "machine", tmp_path / "workspace"
    home, claude = machine / "home", machine / "claude"
    home.mkdir(parents=True)
    (machine / "gitconfig").write_text("[user]\n\tname = Ana\n\temail = ana@example.test\n[init]\n"
                                       "\tdefaultBranch = main\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(machine / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # a move waits on a hold before its link may take it: none here, so a settle stores it at once (signed_move)
    monkeypatch.setenv("OPEN_EDITION_ADE_MOVE_HOLD", "0")

    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", cwd=seed)
    for name, text in (("README.md", "Demo\n"), ("notes.txt", "notes\n"), ("gone.txt", "to be deleted\n"),
                       ("run.sh", "echo run\n"), (".gitignore", "*.log\n")):
        (seed / name).write_text(text)
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "first", cwd=seed)
    origin = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(origin))

    work = home / "work" / "demo"
    git("clone", "-q", str(origin), str(work))
    git("remote", "set-url", "origin", ON_MACHINE, cwd=work)
    folder = claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(work))     # named as Claude Code names it
    folder.mkdir(parents=True)
    (folder / f"{SESSION}.jsonl").write_bytes(transcript(str(work), str(work)))

    # the workspace: its clone of the origin, whose fetches reach the bare one here (no network)
    demo = ws / "root" / "repos" / "demo"
    git("clone", "-q", str(origin), str(demo))
    git("remote", "set-url", "origin", IN_WORKSPACE, cwd=demo)
    git("config", f"url.{origin}.insteadOf", IN_WORKSPACE, cwd=demo)
    (ws / "bin").mkdir()
    (ws / "bin" / "claude").write_text(CLAUDE)
    (ws / "bin" / "claude").chmod(0o755)
    (ws / "home").mkdir()
    agent = Agent(str(ws / "root"), str(ws / "home"), extra_env={"PATH": f"{ws / 'bin'}:{os.environ['PATH']}"})
    endpoint = workspaces.Endpoint(agent.base, f"ws://127.0.0.1:{agent.port}", SECRET)
    provider = Provider(SimpleNamespace(endpoint=endpoint))
    monkeypatch.setattr(workspaces, "for_user", lambda user: provider)
    # each hand-off the app starts is run in place once the client is done (move_run): run inside the client's done
    # request, it held that request open while the workspace started the session, 4 of the 8 seconds the client gives
    # a request before it asks again (measured on a machine under load)
    handed = []
    monkeypatch.setattr(moves, "_spawn", lambda fn, *args: handed.append((fn, args)))

    owner = signed_up(client)
    device = Authenticator(rp="127.0.0.1", origin=served)
    assert add(client, owner, device).status_code == 200
    link = pair(client, owner, served, capsys, machine / "links" / "mac", "mac")
    code, out, err = run(capsys, "--dir", link, "pin")
    assert code == 0 and "ADE: pinned " in out, (out, err)
    (link / "history.json").write_text(json.dumps({"projects": ["~/work"]}))
    yield SimpleNamespace(client=client, owner=owner, device=device, tmp=tmp_path, origin=origin, machine=machine,
                          work=work, link=link, link_id=json.loads((link / "config.json").read_text())["link_id"],
                          agent=agent, ws_root=ws / "root", ws_home=ws / "home", ws_repo=demo, handed=handed)
    try:
        for s in agent.http.get("/sessions").json()["sessions"]:
            agent.http.post(f"/sessions/{s['id']}/stop")       # its claude hung up before the agent stops
    finally:
        agent.close()


def work_of_every_kind(work):
    """The machine's repository as the main test moves it: a branch with a commit origin lacks, then the work not
    committed, of every kind: a change staged with more on top of it, a file changed, one deleted, one whose mode
    changed, a new file staged, a new file in a new folder, and a log git ignores."""
    git("checkout", "-q", "-b", "sign-in", cwd=work)
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("def sign_in():\n    return True\n")
    git("add", "src/app.py", cwd=work)
    git("commit", "-qm", "sign-in, not pushed", cwd=work)
    (work / "README.md").write_text("Demo\nstaged\n")
    git("add", "README.md", cwd=work)
    (work / "README.md").write_text("Demo\nstaged\nand more, not staged\n")
    (work / "notes.txt").write_text("notes\nchanged, not staged\n")
    (work / "gone.txt").unlink()
    (work / "run.sh").chmod(0o755)
    (work / "staged.txt").write_text("new, staged\n")
    git("add", "staged.txt", cwd=work)
    (work / "docs").mkdir()
    (work / "docs" / "new.md").write_text("new, not tracked\n")
    (work / "build.log").write_text("ignored\n")


def listed(rig, capsys):
    """The machine's History list sent by its client (ade_link.py history): the session listed in the app, by its
    title, not withheld."""
    code, out, err = run(capsys, "--dir", rig.link, "history")
    assert (code, out) == (0, "ADE: 1 of this machine's sessions listed in the ADE's History\n"), err
    (row,) = rig.client.get("/api/ade/link/history", headers=rig.owner).json()["sessions"]
    assert (row["id"], row["title"], row["withheld"], row["project"], row["link"]) == (
        SESSION, TITLE, False, "~/work/demo", "mac")


def signed_move(rig):
    """A move of the session into the workspace, composed as the page composes one from its nonce, signed with the
    owner's passkey and posted through the owner's route, its hold settled: the seq of the move stored."""
    got = rig.client.post("/api/ade/link/nonce", headers=rig.owner, json={"purpose": "move"}).json()
    p = {"v": 2, "deployment": got["deployment"], "purpose": "move", "account": got["account"], "nonce": got["nonce"],
         "id": uuid.uuid4().hex, "to": rig.link_id, "session": SESSION, "created_at": got["server_time"],
         "expires_at": got["server_time"] + 600}
    credential = rig.device.get(as_step(canonicalize(p)))
    r = rig.client.post("/api/ade/link/commands", headers=rig.owner, json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    # the move waits on its hold (none in these tests): settled here, it is stored, and only then may the machine take it
    held = r.json()["held"]
    assert held["id"] in holds.settle()
    with store.connect() as db:
        return db.execute("SELECT seq FROM ade_link_held_moves WHERE hold_id = ?", (held["id"],)).fetchone()["seq"]


def move_run(rig):
    """The link client's move run as the owner runs it by hand, a process of its own; then each hand-off to the
    workspace the app started meanwhile, run in place: (the client's exit code, out, err)."""
    p = subprocess.run([sys.executable, SCRIPT, "--dir", str(rig.link), "move"], capture_output=True, text=True,
                       timeout=120, cwd=str(rig.machine))
    handed_off(rig)
    return p.returncode, p.stdout, p.stderr


def handed_off(rig):
    """Each hand-off to the workspace the app started, run in place, the oldest first."""
    while rig.handed:
        fn, args = rig.handed.pop(0)
        fn(*args)


def move_row(seq):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_moves WHERE seq = ?", (seq,)).fetchone())


def owners_view(rig):
    (view,) = rig.client.get("/api/ade/link/moves", headers=rig.owner,
                             params={"link_id": rig.link_id, "session": SESSION}).json()["moves"]
    return view


def every_byte(top):
    """Every folder and file under `top`, its .git included (the index, the refs, the objects), each file's mode and
    bytes: what a move must leave of the machine's repository exactly as it was."""
    found = {}
    for here, dirs, names in os.walk(top):
        found[os.path.relpath(here, top) + os.sep] = None
        for name in names:
            path = os.path.join(here, name)
            with open(path, "rb") as fh:
                found[os.path.relpath(path, top)] = (stat.S_IMODE(os.lstat(path).st_mode), fh.read())
    return found


def files_of(top):
    """The files of a working tree but its .git (a folder, or a worktree's file), each one's bytes."""
    found = {}
    for here, dirs, names in os.walk(top):
        if here == str(top):
            dirs[:] = [x for x in dirs if x != ".git"]
            names = [x for x in names if x != ".git"]
        for name in names:
            with open(os.path.join(here, name), "rb") as fh:
                found[os.path.relpath(os.path.join(here, name), top)] = fh.read()
    return found


def listing(path):
    return sorted(os.listdir(path)) if os.path.exists(path) else []


def status(work):
    """git status of a working tree, a line a file, each line whole (its first two columns say staged or not)."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work, check=True,
                         capture_output=True, text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).stdout
    return sorted(line for line in out.splitlines() if line)


def test_a_session_moves_from_the_machine_into_the_workspace_and_resumes_there_with_its_work_as_it_was(rig, capsys):
    work_of_every_kind(rig.work)
    listed(rig, capsys)
    seq = signed_move(rig)
    head = git("rev-parse", "HEAD", cwd=rig.work)
    before = every_byte(rig.work)
    code, out, err = move_run(rig)
    assert code == 0, (out, err)

    # the app: started in the workspace, with the session the workspace made
    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("started", None), row["reason"]
    done = json.loads(row["done"])
    changes = done["changes"]
    assert re.fullmatch(r"[0-9a-f]{40}", changes) and changes != head
    assert done == {"remote": SENT, "branch": "sign-in", "head": head, "changes": changes, "cwd": str(rig.work),
                    "bundle": True}
    assert out == (f"ADE: move [{seq}] of session {SESSION}: sent to the ADE: its transcript "
                   f"({row['transcript_size']} bytes) and its repository's state (sign-in at {head[:12]}, the work not "
                   f"committed as {changes[:12]}; a bundle of {row['bundle_size']} bytes)\n")
    (session,) = rig.agent.http.get("/sessions").json()["sessions"]
    sid = session["id"]
    assert row["workspace_session"] == sid
    view = owners_view(rig)
    assert (view["state"], view["workspace_session"], view["head"], view["changes"]) == ("started", sid, head, changes)

    # the workspace: a claude session of the repository whose origin is the machine's, at the machine's head, named
    # by the session's title and where it came from, resuming its own conversation
    assert (session["repo"], session["program"], session["branch"], session["base"]) == ("demo", "claude",
                                                                                          f"pp/{sid}", head)
    assert session["moved"] == {"id": str(seq), "session": SESSION, "branch": "sign-in", "head": head,
                                "changes": changes}
    assert session["task"].startswith(TITLE + "\n") and "Moved here from mac" in session["task"]
    assert _started(SimpleNamespace(root=str(rig.ws_root)), sid) == (["--resume", SESSION], "found")

    # its worktree holds the machine's work exactly: the commit origin lacks as its head, every file as it was on the
    # machine (the deleted one gone, the ignored log not carried), nothing staged
    worktree = rig.ws_root / "sessions" / sid / "work"
    assert git("rev-parse", "HEAD", cwd=worktree) == head
    assert git("log", "-1", "--format=%s", cwd=worktree) == "sign-in, not pushed"
    assert set(files_of(rig.work)) == set(CARRIED) | {"build.log"}
    assert files_of(worktree) == {name: (rig.work / name).read_bytes() for name in CARRIED}
    assert os.stat(worktree / "run.sh").st_mode & stat.S_IXUSR
    assert subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree).returncode == 0
    assert status(worktree) == [" D gone.txt", " M README.md", " M notes.txt", " M run.sh", "?? docs/new.md",
                                "?? staged.txt"]

    # its conversation where Claude Code keeps the worktree's, the cwds in the machine's repository made the worktree's
    real = os.path.realpath(worktree)
    kept = rig.ws_home / ".claude" / "projects" / usage.slug(real) / f"{SESSION}.jsonl"
    assert kept.read_bytes() == transcript(real, str(rig.work))
    assert row["transcript_size"] == len(transcript(str(rig.work), str(rig.work)))

    # no file of the move is left: the app's (its moves folder holds only the empty one uploads come through), the
    # workspace's, the machine's; and the machine's repository is as it was
    kept_by_app = os.path.dirname(moves.folder(seq))
    assert listing(kept_by_app) == ["incoming"] and listing(os.path.join(kept_by_app, "incoming")) == []
    assert listing(rig.ws_root / "moves") == []
    assert [n for n in listing(rig.link) if n.startswith("move-") or n.endswith(".tmp")] == []
    assert every_byte(rig.work) == before


def test_a_session_the_machine_s_history_json_excepts_since_it_was_listed_is_not_sent_and_its_move_fails(rig,
                                                                                                         capsys):
    work_of_every_kind(rig.work)
    listed(rig, capsys)
    seq = signed_move(rig)
    # the owner excepts the session's project on the machine after its list went: the app still lists the session,
    # and the move is the machine's to judge
    (rig.link / "history.json").write_text(json.dumps({"projects": ["~/work"], "except": ["~/work/demo"]}))
    before = every_byte(rig.work)
    code, out, err = move_run(rig)
    reason = ade_link.MOVE_HELD["session"]
    assert "history.json does not list it" in reason
    assert (code, out) == (2, f"ADE: move [{seq}] of session {SESSION}: not sent: {reason}; the ADE was told\n"), err

    row, view = move_row(seq), owners_view(rig)
    assert (row["state"], row["reason"]) == ("failed", reason) and row["taken_at"] is not None
    assert (row["transcript_size"], row["bundle_size"], row["uploaded_at"], row["done"], row["workspace_session"]) \
        == (None, None, None, None, None)
    assert (view["state"], view["reason"]) == ("failed", reason)
    assert not os.path.exists(moves.folder(seq))
    assert rig.agent.http.get("/sessions").json()["sessions"] == [] and listing(rig.ws_root / "moves") == []
    assert every_byte(rig.work) == before


def test_a_session_built_on_a_commit_only_another_remote_has_moves_with_that_commit(rig, capsys):
    """The machine's clone has a second remote, the repository its origin is a fork of, whose main has a commit origin
    lacks, and the session's branch starts there: the workspace fetches from origin alone (the move names no other
    remote), so the bundle carries what origin lacks, that commit with it."""
    upstream, ahead = rig.tmp / "upstream.git", rig.tmp / "ahead"
    git("clone", "-q", "--bare", str(rig.origin), str(upstream))
    git("clone", "-q", str(upstream), str(ahead))
    (ahead / "later.txt").write_text("on upstream, not on origin\n")
    git("add", "later.txt", cwd=ahead)
    git("commit", "-qm", "later, on upstream only", cwd=ahead)
    git("push", "-q", "origin", "main", cwd=ahead)
    git("remote", "add", "upstream", str(upstream), cwd=rig.work)
    git("fetch", "-q", "upstream", cwd=rig.work)
    git("checkout", "-q", "-b", "fix", "upstream/main", cwd=rig.work)
    (rig.work / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=rig.work)
    git("commit", "-qm", "the fix, not pushed", cwd=rig.work)
    (rig.work / "notes.txt").write_text("notes\nchanged, not staged\n")
    listed(rig, capsys)
    seq = signed_move(rig)
    head = git("rev-parse", "HEAD", cwd=rig.work)
    code, out, err = move_run(rig)
    assert code == 0, (out, err)

    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("started", None), row["reason"]
    (session,) = rig.agent.http.get("/sessions").json()["sessions"]
    worktree = rig.ws_root / "sessions" / session["id"] / "work"
    assert git("rev-parse", "HEAD", cwd=worktree) == head
    assert git("log", "-2", "--format=%s", cwd=worktree).splitlines() == ["the fix, not pushed",
                                                                         "later, on upstream only"]
    assert files_of(worktree) == files_of(rig.work) and status(worktree) == [" M notes.txt"]


def test_a_session_whose_commits_are_all_on_origin_moves_with_no_bundle_its_head_fetched_from_origin(rig, capsys):
    """Nothing not committed, and the machine's commit pushed to origin after the workspace cloned it: no bundle goes,
    none is handed over, and the workspace fetches the head from origin."""
    (rig.work / "notes.txt").write_text("notes\npushed\n")
    git("commit", "-qam", "pushed after the workspace cloned", cwd=rig.work)
    git("push", "-q", str(rig.origin), "HEAD:main", cwd=rig.work)        # by its path: the address it names is not real
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=rig.work)   # as a push to origin leaves it
    head = git("rev-parse", "HEAD", cwd=rig.work)
    assert subprocess.run(["git", "cat-file", "-e", head], cwd=rig.ws_repo).returncode != 0
    listed(rig, capsys)
    seq = signed_move(rig)
    code, out, err = move_run(rig)
    assert code == 0 and out.endswith("nothing not committed; no bundle: its origin holds every commit it needs)\n"), \
        (out, err)

    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("started", None), row["reason"]
    assert json.loads(row["done"]) == {"remote": SENT, "branch": "main", "head": head, "changes": None,
                                       "cwd": str(rig.work), "bundle": False}
    assert row["bundle_size"] is None
    (session,) = rig.agent.http.get("/sessions").json()["sessions"]
    worktree = rig.ws_root / "sessions" / session["id"] / "work"
    assert git("rev-parse", "HEAD", cwd=worktree) == head and status(worktree) == []
    assert _started(SimpleNamespace(root=str(rig.ws_root)), session["id"]) == (["--resume", SESSION], "found")


def test_a_done_the_app_took_whose_answer_was_lost_is_told_as_sent_and_the_move_starts(rig, capsys, monkeypatch):
    """The app took the client's done and its answer did not come back. The client asks again, as it asks again for
    any request with no answer, and the app answers that the move is no longer its link's to send (409, and where it
    stands: uploaded); the client tells the move as sent, as it is, never as failed. The client runs in this process
    here, so that its done's answer can be lost on the way."""
    listed(rig, capsys)
    seq = signed_move(rig)
    real, lost = ade_link.Link.request, []

    def answer_lost(self, method, path, *args, **kwargs):
        answer = real(self, method, path, *args, **kwargs)
        if (method, path) == ("POST", f"/moves/{seq}/done") and not lost:
            lost.append(answer)                                  # the app took it; its answer goes no further
            raise ade_link.Unreachable("the connection closed before the answer came")
        return answer
    monkeypatch.setattr(ade_link.Link, "request", answer_lost)
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)
    code, out, err = run(capsys, "--dir", rig.link, "move")
    handed_off(rig)
    assert lost == [{"seq": seq, "state": "uploaded"}]
    assert (code, out) == (0, f"ADE: move [{seq}] of session {SESSION}: sent to the ADE, which has it (uploaded) from "
                              "this link's earlier done\n"), err
    assert ade_link._moves(ade_link.Link(str(rig.link)))[str(seq)]["state"] == "done"
    row = move_row(seq)
    assert (row["state"], row["reason"]) == ("started", None), row["reason"]
