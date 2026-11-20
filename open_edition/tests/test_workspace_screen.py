"""A program's last screen outlives the agent (terminals.SavedScreen): a workspace that slept, or an agent that
restarted, shows each program's last output and how it ended, read only, until the program is started again."""

import asyncio
import hashlib
import json
import os
import signal
import sys
import time

from proofpane_workspace import terminals
from proofpane_workspace.workspace import Workspace
from workspace_fixtures import Agent, make_repo, next_control, read_until


def _agent(tmp_path, **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    info = tmp_path / "ws" / "agent.json"
    if info.exists():
        info.unlink()                       # the last agent's address, not this one's
    # an agent program to run: a stand-in `opencode` that is a shell (a shell session's own screen is not kept)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "opencode").write_text("#!/bin/sh\nexec /bin/sh\n")
    os.chmod(bin_dir / "opencode", 0o755)
    return Agent(str(tmp_path / "ws"), str(home), dict(env, PATH=f"{bin_dir}:{os.environ['PATH']}"))


def _session(agent, program="opencode"):
    r = agent.http.post("/sessions", json={"repo": "demo", "program": program, "task": "Run it"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_programs_last_screen_outlives_the_agent(tmp_path):
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a = _agent(tmp_path)
    try:
        s = _session(a)
        with a.terminal(s["id"]) as ws:
            assert next_control(ws) == {"type": "ready"}
            ws.send(b"echo kept-$((40+2)); exit 3\n")
            assert b"kept-42" in read_until(ws, b"kept-42")
            assert next_control(ws) == {"type": "exit", "code": 3}
    finally:
        a.close()
    saved = tmp_path / "ws" / "sessions" / s["id"] / "screen"
    assert oct(os.stat(saved).st_mode & 0o777) == "0o600"
    b = _agent(tmp_path)
    try:
        got = b.http.get(f"/sessions/{s['id']}").json()
        assert got["running"] is False and got["exit_code"] == 3 and got["ended_at"]
        with b.terminal(s["id"]) as ws:                 # its last screen, then how it ended; no input taken
            assert next_control(ws) == {"type": "ready"}
            assert b"kept-42" in read_until(ws, b"kept-42")
            assert next_control(ws) == {"type": "exit", "code": 3}
        r = b.http.post(f"/sessions/{s['id']}/start", json={})   # started again, a new screen
        assert r.status_code == 200 and r.json()["running"] is True and r.json()["exit_code"] is None
    finally:
        b.close()


def test_a_screen_saved_while_running_outlives_an_agent_killed_outright(tmp_path):
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a = _agent(tmp_path, PP_WORKSPACE_SCREEN_SAVE_EVERY="0.2")
    s = _session(a)
    with a.terminal(s["id"]) as ws:
        ws.send(b"echo $$ > ../shell-pid; echo running-$((5+5))\n")
        read_until(ws, b"running-10")
    time.sleep(1.0)                                     # a save or two
    a.proc.kill()                                       # no hang-up, no last save
    a.proc.wait(10)
    pid = int((tmp_path / "ws" / "sessions" / s["id"] / "shell-pid").read_text())
    try:
        os.kill(pid, signal.SIGKILL)                    # the program the killed agent left behind
    except ProcessLookupError:
        pass
    a.http.close()
    b = _agent(tmp_path)
    try:
        got = b.http.get(f"/sessions/{s['id']}").json()
        assert got["running"] is False and got["exit_code"] is None and got["ended_at"]
        with b.terminal(s["id"]) as ws:
            assert next_control(ws) == {"type": "ready"}
            assert b"running-10" in read_until(ws, b"running-10")
            assert next_control(ws) == {"type": "exit", "code": None}
    finally:
        b.close()


def test_a_shell_session_s_screen_is_not_kept(tmp_path):
    # where a person types and reads their own secrets (gh auth token): it is not written to disk
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    a = _agent(tmp_path)
    try:
        s = _session(a, "shell")
        with a.terminal(s["id"]) as ws:
            ws.send(b"echo secret-$((1+1)); exit 0\n")
            read_until(ws, b"secret-2")
    finally:
        a.close()
    assert not (tmp_path / "ws" / "sessions" / s["id"] / "screen").exists()


def test_a_sign_in_screen_is_not_kept(tmp_path):
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Sign in")
    w._update(record["id"], lambda r: r.update(program="github"))

    async def run():
        w.launch(record["id"], argv=["/bin/sh", "-c", "echo one-time-code-ABCD; exit 0"])
        term = w.terminals[record["id"]]
        while term.running:
            await asyncio.sleep(0.05)
    asyncio.run(run())
    assert not os.path.exists(os.path.join(w._dir(record["id"]), "screen"))


def test_a_code_hidden_in_a_terminal_is_starred_wherever_it_is_printed_whole_after(tmp_path):
    # SIGNIN-DROP (queue 4.322): once a terminal is told to keep no copy of a code (Terminal.hide, as a sign-in's is
    # before it is typed), each later printing of it whole is kept as `*`, one for each byte: one cut by two reads, one
    # inside a program's escapes, one next to other visible characters; what was printed before is kept as it was
    code = b"Zq7Xv3Rk9Pw2Lm5Tn8Hb4Jd6"
    program = ("import sys, time\n"
               "c = sys.argv[1]\n"
               "def w(s):\n"
               "    sys.stdout.write(s)\n"
               "    sys.stdout.flush()\n"
               "w('before ' + c + '\\n')\n"
               "sys.stdin.readline()\n"
               "w(c[:10])\n"
               "time.sleep(0.5)\n"
               "w(c[10:] + '\\n')\n"
               "w('\\x1b[1m' + c + '\\x1b[0m\\n')\n"
               "w('x' + c + 'y\\n')\n")

    async def run():
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "C"}
        term = terminals.Terminal([sys.executable, "-c", program, code.decode()], str(tmp_path), env)
        try:
            deadline = time.monotonic() + 20
            while b"before " + code not in term.backlog:
                assert time.monotonic() < deadline, bytes(term.backlog)
                await asyncio.sleep(0.05)
            term.hide(len(code), hashlib.sha256(code).digest())
            await term.write(b"go\r")
            while term.running:
                assert time.monotonic() < deadline, bytes(term.backlog)
                await asyncio.sleep(0.05)
            return bytes(term.backlog)
        finally:
            await term.stop()

    kept = asyncio.run(run())
    stars = b"*" * len(code)
    assert kept.count(code) == 1 and b"before " + code in kept, kept         # printed before: kept as it was
    assert kept.count(stars) == 3, kept
    assert b"\x1b[1m" + stars + b"\x1b[0m" in kept and b"x" + stars + b"y" in kept, kept


def test_a_saved_screen_that_cannot_be_read_is_none(tmp_path):
    path = tmp_path / "screen"
    for data in (b"", b"not json\nbytes", b"[1]\nbytes", b"{\"started_at\": 1"):
        path.write_bytes(data)
        assert terminals.SavedScreen.load(str(path)) is None and terminals.SavedScreen.meta(str(path)) is None
    assert terminals.SavedScreen.load(str(tmp_path / "missing")) is None
    path.write_bytes(json.dumps({"started_at": 5, "ended_at": None, "exit_code": 2, "saved_at": 9}).encode() + b"\nout")
    saved = terminals.SavedScreen.load(str(path))
    assert (saved.backlog, saved.ended_at, saved.exit_code, saved.running) == (b"out", 9, 2, False)
    assert terminals.SavedScreen.meta(str(path)) == {"started_at": 5, "ended_at": 9, "exit_code": 2}
