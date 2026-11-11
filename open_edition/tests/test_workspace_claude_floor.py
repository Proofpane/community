"""The workspace starts no claude terminal, chat or agent's run on a Claude Code older than 2.1.139, the first whose
settings declare a hook given as a program and its arguments, the form the gate's hooks take (settings.py): what an
older one does with the gate given so was not measured, and from that reading of its settings it is taken not to run
it. Under the local provider the claude is whichever the PATH gives, and in a workspace machine the build in use; its
version is read at each start, never kept. Played by the stand-in claude, whose version each test sets."""

import json
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest

from proofpane_workspace import workspace
from test_agents_runs import STAND_IN, Workspace, put, run, until
from workspace_fixtures import SECRET, Agent, make_repo


def set_version(w, version, **config):
    """The PATH's claude written again as the stand-in at `version`: a new build there, so its file changes."""
    config = dict(config, VERSION=version)
    (w.bin / "claude").write_text(f"#!{sys.executable}\nCONFIG = {config!r}\n" + open(STAND_IN).read())
    (w.bin / "claude").chmod(0o755)


def session(w, program):
    return w.http.post("/sessions", json={"repo": "demo", "program": program, "task": "hello"})


def test_the_floor_is_the_first_build_that_takes_a_hook_s_arguments():
    assert workspace.CLAUDE_ARGS_FLOOR == (2, 1, 139)


@pytest.mark.parametrize("version", ["2.1.138", "2.0.400", "1.9.999"])
def test_an_older_claude_starts_no_claude_session_chat_or_agent_run_and_a_shell_still_starts(tmp_path, version):
    w = Workspace(tmp_path, config={"VERSION": version})
    try:
        for program in ("claude", "chat"):
            r = session(w, program)
            assert r.status_code == 400, (program, r.text)
            assert f"Claude Code {version} is older than 2.1.139" in r.json()["detail"], r.text
        assert session(w, "shell").status_code == 200
        # an agent's own run starts its chat with its first message, refused there, the run failed with why
        put(w)
        got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert got["state"] == "failed" and f"Claude Code {version} is older than 2.1.139" in got["reason"], got
    finally:
        w.stop()


def test_the_floor_itself_starts_every_kind_of_work(tmp_path):
    # the control: at 2.1.139 a claude session, a chat and an agent's run all start
    w = Workspace(tmp_path, config={"VERSION": "2.1.139"})
    try:
        for program in ("claude", "chat"):
            r = session(w, program)
            assert r.status_code == 200, (program, r.text)
        put(w)
        got = until(w, run(w)["run_id"], ("failed", "done", "blocked"))
        assert got["state"] == "done", got
    finally:
        w.stop()


def test_a_claude_terminal_started_again_on_an_older_claude_is_refused(tmp_path):
    w = Workspace(tmp_path, config={"VERSION": "2.1.274"})
    try:
        r = session(w, "claude")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert w.http.post(f"/sessions/{sid}/stop").status_code == 200
        set_version(w, "2.1.100")
        r = w.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        set_version(w, "2.1.274")                       # the control: the newer one back, it starts again
        r = w.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 200, r.text
    finally:
        w.stop()


def test_a_claude_whose_version_cannot_be_read_starts_no_session_s_work(tmp_path):
    w = Workspace(tmp_path, config={"VERSION": "unknown"})
    try:
        for program in ("claude", "chat"):
            r = session(w, program)
            # the version is what cannot be told, not that the build runs the gate
            assert r.status_code == 400 and r.json()["detail"] == (
                "Claude Code's version could not be read, so the workspace cannot tell that it is 2.1.139 or later, the "
                "first whose settings declare the form the workspace's gate is given in: update Claude Code, then "
                "start it again."), r.text
    finally:
        w.stop()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def made(w):
    """What making a session leaves: the repository's worktrees and branches, the sessions' folders and the list."""
    repo = os.path.join(w.root, "repos", "demo")
    sessions = os.path.join(w.root, "sessions")
    return (git("worktree", "list", "--porcelain", cwd=repo), git("branch", "--list", cwd=repo),
            sorted(os.listdir(sessions)) if os.path.isdir(sessions) else [], w.http.get("/sessions").json()["sessions"])


def test_a_refused_start_leaves_nothing(tmp_path):
    # the floor is asked in the worktree once it is made (a pin there can choose the build), and a refusal unmakes the
    # session: a claude session or a chat refused on it leaves no session, worktree or branch behind
    w = Workspace(tmp_path, config={"VERSION": "2.1.100"})
    try:
        before = made(w)
        for program in ("claude", "chat"):
            assert session(w, program).status_code == 400, program
        assert made(w) == before
        assert session(w, "shell").status_code == 200       # the control: a start that is not refused makes them
        assert made(w) != before
    finally:
        w.stop()


def pin(folder, version):
    """A pin in a folder, as mise's or asdf's is, which the stand-in reads upward from where it runs (VERSION_PIN)."""
    with open(os.path.join(folder, ".claude-version"), "w", encoding="utf-8") as fh:
        fh.write(version + "\n")


def pinning(tmp_path):
    return {"VERSION": "2.1.274", "VERSION_PIN": ".claude-version",
            "VERSION_ASKS": str(tmp_path / "version-asks.jsonl")}


def asked_in(tmp_path, folder):
    """The version's reads made in `folder`, each with the session's names it was made with."""
    path = tmp_path / "version-asks.jsonl"
    found = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [a["env"] for a in found if a["cwd"] == os.path.realpath(folder)]


def test_a_pin_is_seen_where_the_session_s_claude_runs_at_its_making_and_its_start_again(tmp_path):
    # a version manager chooses its build by the folder it runs in: the floor reads the version in the session's
    # worktree with the start's own names, so a pin the repository carries refuses a new claude session or chat, and
    # one a session writes in its worktree refuses its claude terminal's start again
    w = Workspace(tmp_path, config=pinning(tmp_path))
    try:
        r = session(w, "claude")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        folder = os.path.join(w.root, "sessions", sid)
        work = os.path.join(folder, "work")
        envs = asked_in(tmp_path, work)
        assert envs and envs[-1]["PP_SESSION_ID"] == sid, envs
        assert os.path.realpath(envs[-1]["PP_SESSION_DIR"]) == os.path.realpath(folder), envs
        assert w.http.post(f"/sessions/{sid}/stop").status_code == 200
        pin(work, "2.1.100")
        r = w.http.post(f"/sessions/{sid}/start", json={})
        assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        assert len(asked_in(tmp_path, work)) == len(envs) + 1, "the start again read the version elsewhere"
        assert asked_in(tmp_path, work)[-1]["PP_SESSION_ID"] == sid, asked_in(tmp_path, work)
        os.unlink(os.path.join(work, ".claude-version"))      # the control: the pin gone, it starts again
        assert w.http.post(f"/sessions/{sid}/start", json={}).status_code == 200
        # a pin on a branch the repository carries, not in its checkout: read where the session's worktree is, not there
        repo = os.path.join(w.root, "repos", "demo")
        git("switch", "-q", "-c", "pinned", cwd=repo)
        pin(repo, "2.1.100")
        git("add", ".claude-version", cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "pin", cwd=repo)
        git("switch", "-q", "-", cwd=repo)
        assert not os.path.exists(os.path.join(repo, ".claude-version"))
        before = made(w)
        for program in ("claude", "chat"):
            r = w.http.post("/sessions", json={"repo": "demo", "program": program, "task": "hello", "base": "pinned"})
            assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        assert made(w) == before
        assert "2.1.100" not in started_versions(w), started_versions(w)
    finally:
        w.stop()


def test_a_pin_in_a_chat_s_worktree_is_seen_at_its_claude_s_next_start(tmp_path):
    w = Workspace(tmp_path, config=pinning(tmp_path))
    try:
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        results(w, sid, 1)
        work = os.path.join(w.root, "sessions", sid, "work")
        pin(work, "2.1.100")
        r = again(w, sid, "again")
        assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        assert "2.1.100" not in started_versions(w), started_versions(w)
        assert asked_in(tmp_path, work)[-1]["PP_SESSION_ID"] == sid
        os.unlink(os.path.join(work, ".claude-version"))      # the control: the pin gone, the next message starts it
        r = again(w, sid, "and again", effort="medium")
        assert r.status_code == 200, r.text
        results(w, sid, 2)
    finally:
        w.stop()


def test_a_session_s_making_checks_the_claude_its_start_runs(tmp_path, monkeypatch):
    # the claude checked at a session's making is its argv's, which its terminal starts, not one looked up again
    from proofpane_workspace.workspace import Workspace as Space
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    for name, version in (("claude", "2.1.274"), ("older", "2.1.100")):
        head = f"#!{sys.executable}\nCONFIG = {{'VERSION': {version!r}}}\n"
        (bin_dir / name).write_text(head + open(STAND_IN).read())
        (bin_dir / name).chmod(0o755)
    make_repo(str(root / "repos" / "demo"))
    ws = Space(str(root))
    looked = iter([str(bin_dir / "claude")] + [str(bin_dir / "older")] * 10)
    monkeypatch.setattr(ws, "_claude", lambda: next(looked))
    asked = []
    real = ws.claude_floor_refusal

    def checked(claude, cwd, env):
        asked.append(claude)
        return real(claude, cwd, env)
    monkeypatch.setattr(ws, "claude_floor_refusal", checked)
    record, argv = ws.prepare("demo", "claude", "hello")
    assert asked == [argv[0]] == [str(bin_dir / "claude")], (asked, argv)


def test_the_floor_reads_the_version_with_the_names_its_start_gives(tmp_path, monkeypatch):
    # the session's own, its account's folder, and another provider's names for a claude on that provider's model
    from proofpane_workspace import providers
    from proofpane_workspace.workspace import Workspace as Space
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    ws = Space(str(root))
    monkeypatch.setattr(ws, "_account_env", lambda record: {"CLAUDE_CONFIG_DIR": f"/accounts/{record['id']}"})
    env = ws._claude_start_env("a" * 12, "chat")
    assert env["PP_SESSION_ID"] == "a" * 12 and env["PP_SESSION_DIR"] == os.path.join(ws.sessions_dir, "a" * 12)
    assert env["CLAUDE_CONFIG_DIR"] == "/accounts/" + "a" * 12
    name = sorted(providers.CATALOG)[0]
    on = ws._claude_start_env("b" * 12, "claude", name)
    assert all(on[k] == v for k, v in providers.CATALOG[name]["env"].items()), (name, on)
    assert not set(providers.CATALOG[name]["env"]) & set(ws._claude_start_env("b" * 12, "chat", name))


def test_a_making_whose_floor_check_fails_leaves_nothing(tmp_path, monkeypatch):
    # the check runs once the worktree is made: whatever stops it there, the session is unmade
    from proofpane_workspace.workspace import Workspace as Space, WorkspaceError
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nCONFIG = {{}}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    repo = str(root / "repos" / "demo")
    make_repo(repo)
    ws = Space(str(root))
    before = (git("worktree", "list", "--porcelain", cwd=repo), git("branch", "--list", cwd=repo))

    def broken(*args, **kwargs):
        raise WorkspaceError("the account's record cannot be read")
    monkeypatch.setattr(ws, "_claude_start_env", broken)
    with pytest.raises(WorkspaceError, match="the account's record cannot be read"):
        ws.prepare("demo", "claude", "hello")
    assert (git("worktree", "list", "--porcelain", cwd=repo), git("branch", "--list", cwd=repo)) == before
    assert not os.path.isdir(ws.sessions_dir) or os.listdir(ws.sessions_dir) == []


def test_a_claude_terminal_started_again_starts_the_claude_its_check_read(tmp_path):
    # the start route reads the version, then launch starts the claude it read, not one looked up again: a claude put
    # first on the PATH while the version is read is not the one started
    w = TwoFolders(tmp_path, config={"VERSION": "2.1.280"})
    try:
        r = session(w, "claude")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert w.http.post(f"/sessions/{sid}/stop").status_code == 200
        set_version(w, "2.1.280", VERSION_DELAY=3)
        asked = w.home / "stand-in-version-asked"
        runs = len(started_versions(w))
        got = {}
        t = threading.Thread(target=lambda: got.update(r=w.http.post(f"/sessions/{sid}/start", json={})))
        t.start()
        deadline = time.monotonic() + 20
        while not asked.exists():
            assert time.monotonic() < deadline, "the version was never asked"
            time.sleep(0.02)
        first = w.first / "claude"
        first.write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION': '2.1.100'}}\n" + open(STAND_IN).read())
        first.chmod(0o755)
        t.join(60)
        assert got["r"].status_code == 200, got["r"].text
        while len(started_versions(w)) <= runs:
            assert time.monotonic() < deadline + 30, "the terminal's claude never started"
            time.sleep(0.05)
        assert started_versions(w)[runs:] == ["2.1.280"], started_versions(w)
    finally:
        w.stop()


def test_with_no_claude_a_chat_s_start_is_refused_and_its_wrapper_is_not_written(tmp_path, monkeypatch):
    # none there is refused, so the wrapper never falls back to whatever claude the PATH gives when it is run
    from proofpane_workspace.workspace import Workspace as Space
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    ws = Space(str(root))
    ws._chat_wrapper = str(root / "chat-claude")

    class Chat:
        # an owner's chat, as chat.py's Chat is (its is_run False), which _chat_start reads for the core plugin, and
        # its session's id, which it reads for the owner's controller's (none here)
        cwd, env, is_run, session_id = str(root), {"PATH": f"{bin_dir}:/usr/bin:/bin"}, False, "0123456789ab"
    assert ws._chat_start(Chat()) == "Claude Code is not installed in this workspace."
    assert not os.path.exists(ws._chat_wrapper)
    # the control: one put on the PATH, the start goes on, the wrapper naming it
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nCONFIG = {{}}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    assert ws._chat_start(Chat()) is None
    named = open(ws._chat_wrapper, encoding="utf-8").read()
    assert repr(str(bin_dir / "claude")) in named
    # an older one the next start would take: refused, and the wrapper still names the one that passed
    (bin_dir / "older").write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION': '2.1.100'}}\n" + open(STAND_IN).read())
    (bin_dir / "older").chmod(0o755)
    monkeypatch.setattr(ws, "_claude", lambda: str(bin_dir / "older"))
    # the words the person reads, whole: they say the floor comes from a reading of Claude Code's settings, an older
    # build not having been tried
    assert ws._chat_start(Chat()) == (
        "Claude Code 2.1.100 is older than 2.1.139, the first whose settings declare the form the workspace's gate is "
        "given in (a program and its arguments); what an older one does with the gate given so was not tried, so it may "
        "not run it: update Claude Code, then start it again.")
    assert open(ws._chat_wrapper, encoding="utf-8").read() == named


def test_a_chat_starts_the_claude_it_checked_not_the_one_a_later_look_gives(tmp_path, monkeypatch):
    """_chat_start checks the claude a start takes
    and writes the chat wrapper to name that one. Here the first look gives a claude that passes, and every later look an
    older one: the start goes on, and the wrapper names the claude checked, never the one a second look would give."""
    from proofpane_workspace.workspace import Workspace as Space
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    ws = Space(str(root))
    ws._chat_wrapper = str(root / "chat-claude")
    for name, config in (("claude", "{}"), ("older", "{'VERSION': '2.1.100'}")):
        (bin_dir / name).write_text(f"#!{sys.executable}\nCONFIG = {config}\n" + open(STAND_IN).read())
        (bin_dir / name).chmod(0o755)
    looks = []

    def look():
        looks.append(len(looks))
        return str(bin_dir / ("claude" if len(looks) == 1 else "older"))
    monkeypatch.setattr(ws, "_claude", look)

    class Chat:
        # an owner's chat, as chat.py's Chat is (its is_run False), which _chat_start reads for the core plugin, and
        # its session's id, which it reads for the owner's controller's (none here)
        cwd, env, is_run, session_id = str(root), {"PATH": f"{bin_dir}:/usr/bin:/bin"}, False, "0123456789ab"
    assert ws._chat_start(Chat()) is None and looks              # checked, and passed
    named = open(ws._chat_wrapper, encoding="utf-8").read()
    # the older claude's own path, not the word: a Mac's temporary folders are under /private/var/folders
    assert repr(str(bin_dir / "claude")) in named and str(bin_dir / "older") not in named


def test_a_version_not_said_in_time_is_refused_with_try_again(tmp_path, monkeypatch):
    from proofpane_workspace import claude_cli
    from proofpane_workspace.workspace import Workspace as Space
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    (bin_dir / "claude").write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION_DELAY': 5}}\n" + open(STAND_IN).read())
    (bin_dir / "claude").chmod(0o755)
    ws = Space(str(root))
    monkeypatch.setattr(claude_cli, "TIMEOUT", 1)
    said = ws.claude_floor_refusal(str(bin_dir / "claude"), str(root), {"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert said == ("Claude Code did not say its version within 1 seconds, so the workspace cannot tell that it is "
                    "2.1.139 or later, the first whose settings declare the form the workspace's gate is given in: try "
                    "again."), said


def results(w, sid, n, timeout=30):
    deadline = time.time() + timeout
    while True:
        log = w.http.get(f"/sessions/{sid}/chat").json()
        if sum(e["kind"] == "result" for e in log["events"]) >= n:
            return log
        assert time.time() < deadline, log
        time.sleep(0.05)


def again(w, sid, text, effort="low"):
    """A chat's next message, with its claude started again for it: an effort other than its own starts claude
    afresh."""
    assert w.http.post(f"/sessions/{sid}/chat/settings", json={"effort": effort}).status_code == 200
    return w.http.post(f"/sessions/{sid}/chat", json={"text": text})


def started_versions(w):
    path = w.home / "stand-in-runs.jsonl"
    if not path.exists():
        return []                       # no claude has started past its version yet
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line)["version"] for line in fh]


def test_a_chat_open_when_an_older_claude_replaces_its_own_starts_it_no_more_and_stays_readable(tmp_path):
    w = Workspace(tmp_path, config={"VERSION": "2.1.274"})
    try:
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        results(w, sid, 1)
        set_version(w, "2.1.100")
        # a new effort starts its claude again with the next message: refused at that start, the chat failed with why
        r = again(w, sid, "again")
        assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        # its log and its place in the list, read as before: the floor stops a start, not a reading; and so after the
        # workspace's own restart, which makes the chat afresh
        for restarted in (False, True):
            if restarted:
                w.stop()
                w.start()
            log = w.http.get(f"/sessions/{sid}/chat").json()
            assert sum(e["kind"] == "result" for e in log["events"]) == 1, log
            assert log["state"] == ("stopped" if restarted else "failed"), log
            (listed,) = [s for s in w.http.get("/sessions").json()["sessions"] if s["id"] == sid]
            assert "error" not in listed and listed["chat"]["state"] == log["state"], listed
        # the control: the newer one back, the next message starts it
        set_version(w, "2.1.274")
        r = w.http.post(f"/sessions/{sid}/chat", json={"text": "and again"})
        assert r.status_code == 200, r.text
        results(w, sid, 2)
    finally:
        w.stop()


class TwoFolders(Workspace):
    """The PATH with a folder before the one with the stand-in, empty to begin with."""

    def start(self):
        self.first = self.tmp / "first"
        self.first.mkdir(exist_ok=True)
        info = os.path.join(self.root, "agent.json")
        if os.path.exists(info):
            os.unlink(info)
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.first}:{self.bin}:{os.environ['PATH']}"})
        self.http = self.agent.http


def test_a_chat_s_start_checks_the_claude_a_start_takes_now_and_starts_that_one(tmp_path):
    # at each start of a chat's claude, the claude a start takes now (the PATH's first) is checked and the wrapper
    # written again to name it: a newer claude put first on the PATH while the chat is open is the one its next start
    # runs, an older one left behind it never runs, and an older one put first is refused
    w = TwoFolders(tmp_path, config={"VERSION": "2.1.274"})
    try:
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        results(w, sid, 1)
        set_version(w, "2.1.100")                               # the claude it started, older now
        first = w.first / "claude"
        first.write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION': '2.1.280'}}\n" + open(STAND_IN).read())
        first.chmod(0o755)
        r = again(w, sid, "again")
        assert r.status_code == 200, r.text
        results(w, sid, 2)
        assert started_versions(w)[-1] == "2.1.280" and "2.1.100" not in started_versions(w), started_versions(w)
        first.write_text(f"#!{sys.executable}\nCONFIG = {{'VERSION': '2.1.120'}}\n" + open(STAND_IN).read())
        r = again(w, sid, "and again", effort="medium")
        assert r.status_code == 400 and "Claude Code 2.1.120 is older than 2.1.139" in r.json()["detail"], r.text
        assert "2.1.120" not in started_versions(w), started_versions(w)
    finally:
        w.stop()


@pytest.mark.parametrize("stopped,refused", [(True, False), (False, True), (True, True)],
                         ids=["stopped", "refused", "stopped-and-refused"])
def test_a_chat_stopped_or_refused_while_its_claude_s_floor_is_asked_starts_no_claude(tmp_path, monkeypatch, stopped,
                                                                                      refused):
    """The floor is asked with the client already the chat's, so a stop meanwhile is one during the connect (an
    agent's run's end stops only a chat with a client): nothing connects, and the start ends as a stopped one, even
    where the floor refused it too; a refusal alone connects nothing either, and says why."""
    import asyncio
    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    from proofpane_workspace import chat as chats
    connected = []

    class Client:
        def __init__(self, options):
            pass

        async def connect(self):
            connected.append(1)

        async def disconnect(self):
            pass
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)

    async def start():
        loop = asyncio.get_running_loop()
        made = {}

        def before_start():
            if stopped:
                asyncio.run_coroutine_threadsafe(made["chat"].stop(), loop).result(10)
            return "Claude Code 2.1.100 is older than 2.1.139" if refused else None
        made["chat"] = chats.Chat("a" * 12, str(tmp_path), str(tmp_path), {}, str(tmp_path / "chat-claude"),
                                  before_start=before_start)
        with pytest.raises(chats.ChatStopped if stopped else chats.ChatError) as raised:
            await made["chat"]._connect()
        return made["chat"], str(raised.value)
    chat, said = asyncio.run(start())
    assert connected == [] and chat.client is None and chat.connecting is None
    assert ("stopped while claude started" in said) if stopped else ("2.1.100 is older" in said)


def test_the_wrapper_written_by_many_at_once_is_always_whole_and_leaves_nothing_beside_it(tmp_path, monkeypatch):
    # chats starting together each write the wrapper again, from threads (_chat_start): every write puts a whole
    # wrapper in place, none fails on another's, and none leaves its own file behind, even one that fails
    from proofpane_workspace import chat as chats
    path = str(tmp_path / "chat-claude")
    names = [f"/opt/claude-{n}/claude" for n in range(8)]
    wholes = {chats.WRAPPER.format(python=sys.executable, keep=sorted(set(["PATH"]) | set(chats.WRAPPER_NAMES)),
                                   claude=name) for name in names}
    failed = []

    def writer(name):
        try:
            for _ in range(60):
                chats.write_wrapper(path, ["PATH"], claude=name)
                with open(path, encoding="utf-8") as fh:
                    assert fh.read() in wholes
        except BaseException as e:  # noqa: BLE001  told below
            failed.append(repr(e))
    threads = [threading.Thread(target=writer, args=(name,)) for name in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert failed == [] and os.listdir(tmp_path) == ["chat-claude"], (failed, os.listdir(tmp_path))

    def no_room(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(chats.os, "replace", no_room)
    with pytest.raises(OSError):
        chats.write_wrapper(path, ["PATH"], claude=names[0])
    assert os.listdir(tmp_path) == ["chat-claude"]


def test_a_newer_claude_put_on_the_path_while_the_workspace_runs_is_seen_at_the_next_start(tmp_path):
    w = Workspace(tmp_path, config={"VERSION": "2.1.138"})
    try:
        assert session(w, "chat").status_code == 400
        set_version(w, "2.1.274")
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        # and an older one put back is refused again
        set_version(w, "2.1.100")
        assert session(w, "claude").status_code == 400
    finally:
        w.stop()


def shim(tmp_path, version):
    """A version manager's shim (mise, Volta): the PATH's claude stays one file, whichever build it runs, so neither its
    path nor its size nor its times tell a new build; its version is read from where it points."""
    (tmp_path / "shim-version").write_text(version)
    return {"VERSION_FILE": str(tmp_path / "shim-version")}


def test_an_older_build_behind_an_unchanged_shim_is_refused_at_the_next_start(tmp_path):
    w = Workspace(tmp_path, config=shim(tmp_path, "2.1.280"))
    try:
        before = os.stat(w.bin / "claude")
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        results(w, sid, 1)
        shim(tmp_path, "2.1.100")
        for program in ("claude", "chat"):
            r = session(w, program)
            assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        r = again(w, sid, "again")
        assert r.status_code == 400 and "Claude Code 2.1.100 is older than 2.1.139" in r.json()["detail"], r.text
        after = os.stat(w.bin / "claude")
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
        assert "2.1.100" not in started_versions(w), started_versions(w)
    finally:
        w.stop()


def test_a_version_that_could_not_be_read_is_read_again_at_the_next_start(tmp_path):
    # a read that failed (a claude half installed, say) is not kept: the next start asks again
    w = Workspace(tmp_path, config=shim(tmp_path, "unknown"))
    try:
        r = session(w, "chat")
        assert r.status_code == 400 and "Claude Code's version could not be read" in r.json()["detail"], r.text
        shim(tmp_path, "2.1.280")
        r = session(w, "chat")
        assert r.status_code == 200, r.text
    finally:
        w.stop()


def test_the_version_s_read_holds_up_no_other_request(tmp_path):
    # read off the event loop at each start, and at a claude terminal's start again before the agent's lock: a claude
    # slow to say its version holds up its own start and no other request, not even one that takes that lock (a new
    # session's), at a claude terminal's start again and at a chat's start
    w = Workspace(tmp_path, config={"VERSION": "2.1.274"})
    other = httpx.Client(base_url=w.agent.base, headers={"Authorization": "Bearer " + SECRET}, timeout=30)
    try:
        r = session(w, "claude")
        assert r.status_code == 200, r.text
        terminal = r.json()["id"]
        assert w.http.post(f"/sessions/{terminal}/stop").status_code == 200
        r = session(w, "chat")
        assert r.status_code == 200, r.text
        chat = r.json()["id"]
        results(w, chat, 1)
        # what the claude offers, read once first: a chat's settings check a new effort against it, and its first read
        # is the agent's own, made on the event loop (Workspace.chat_settings), apart from the floor's
        assert w.http.get("/capabilities").status_code == 200
        set_version(w, "2.1.274", VERSION_DELAY=4)
        asked = w.home / "stand-in-version-asked"
        starts = [lambda: w.http.post(f"/sessions/{terminal}/start", json={}), lambda: again(w, chat, "again")]
        for start in starts:
            n = len(asked.read_text().splitlines()) if asked.exists() else 0
            got = {}
            t = threading.Thread(target=lambda: got.update(r=start()))
            t.start()
            deadline = time.monotonic() + 20
            while not (asked.exists() and len(asked.read_text().splitlines()) > n):
                assert time.monotonic() < deadline, "the version was never asked"
                time.sleep(0.02)
            began = time.monotonic()
            assert other.get("/sessions").status_code == 200
            r = other.post("/sessions", json={"repo": "demo", "program": "shell", "task": "while the version is read"})
            assert r.status_code == 200, r.text
            assert time.monotonic() - began < 2.5, "a request waited on the version's read"
            t.join(60)
            assert got["r"].status_code == 200, got["r"].text
    finally:
        other.close()
        w.stop()


def test_in_a_workspace_machine_an_older_build_in_use_starts_no_claude_session_or_chat(tmp_path, monkeypatch):
    # the agent takes up at its start the build its update state names, when that is a file in Claude Code's versions
    # folder the state says passed the updater's check (claude_update.in_use); the workspace's user can write that
    # state, and nothing is run to take the build up, so the floor is asked in a workspace machine too: a build below
    # 2.1.139 the state names so is refused at each start
    from proofpane_workspace import claude_update
    from proofpane_workspace.workspace import Workspace as Space, WorkspaceError
    home, bin_dir, root = tmp_path / "home", tmp_path / "bin", tmp_path / "ws"
    for folder in (home, bin_dir, root):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    image = bin_dir / "claude"                                  # the image's own, 2.1.274
    image.write_text(f"#!{sys.executable}\nCONFIG = {{}}\n" + open(STAND_IN).read())
    image.chmod(0o755)
    builds = {}
    os.makedirs(claude_update.native_dir(str(home)))
    for version in ("2.1.100", "2.1.280"):
        builds[version] = os.path.join(claude_update.native_dir(str(home)), version)
        with open(builds[version], "w") as fh:
            fh.write(f"#!{sys.executable}\nCONFIG = {{'VERSION': {version!r}}}\n" + open(STAND_IN).read())
        os.chmod(builds[version], 0o755)
    (root / "claude-update.json").write_text(json.dumps({"mode": "automatic", "active": builds["2.1.100"],
                                                         "checked": claude_update.CHECK_FORM}))
    make_repo(str(root / "repos" / "demo"))
    ws = Space(str(root), claude_updates=True)
    assert ws.claude_path == builds["2.1.100"]
    for program in ("claude", "chat"):
        with pytest.raises(WorkspaceError, match="Claude Code 2.1.100 is older than 2.1.139"):
            ws.prepare("demo", program, "hello")
    assert ws.prepare("demo", "shell", "hello")[0]["program"] == "shell"
    # the control: a build at 2.1.280 taken up the same way, and the same starts are made
    (root / "claude-update.json").write_text(json.dumps({"mode": "automatic", "active": builds["2.1.280"],
                                                         "checked": claude_update.CHECK_FORM}))
    space = Space(str(root), claude_updates=True)
    assert space.claude_path == builds["2.1.280"]
    assert space.prepare("demo", "chat", "hello")[0]["program"] == "chat"
