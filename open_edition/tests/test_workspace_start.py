"""How a workspace machine starts and stops its agent: the secret from a file that is deleted once read, and
the agent stopping by itself once nothing has happened for a while, which is what stops an idle machine, or once the
program that started it is gone."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import httpx
import pytest

from conftest import ROOT
from proofpane_workspace.__main__ import exit_when_idle
from workspace_fixtures import SECRET, Agent, make_repo, read_until

FILE_SECRET = "f" * 40


def _agent_with_secret_file(tmp_path, content=FILE_SECRET, extra_env=None):
    secret_file = tmp_path / "secret"
    secret_file.write_text(content + "\n")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PP_WORKSPACE_SECRET": ""}
    env.update(extra_env or {})
    return secret_file, Agent(str(tmp_path / "ws"), str(home), env, args=["--secret-file", str(secret_file)],
                              secret=content)


def test_the_secret_comes_from_a_file_that_is_then_deleted(tmp_path):
    secret_file, a = _agent_with_secret_file(tmp_path)
    try:
        assert not secret_file.exists()
        assert a.http.get("/sessions").status_code == 200
        for wrong in (SECRET, FILE_SECRET + "x"):
            r = httpx.get(a.base + "/sessions", headers={"Authorization": "Bearer " + wrong})
            assert r.status_code == 401
    finally:
        a.close()


@pytest.mark.parametrize("env_secret,content,message", [
    (SECRET, FILE_SECRET, b"not both"),
    ("", "short", b"at least 32 bytes"),
])
def test_a_secret_file_is_refused_when_it_cannot_be_the_only_secret(tmp_path, env_secret, content, message, own_home):
    secret_file = tmp_path / "secret"
    secret_file.write_text(content)
    p = subprocess.run([sys.executable, "-m", "proofpane_workspace", "--root", str(tmp_path / "ws"),
                        "--secret-file", str(secret_file)], cwd=ROOT, capture_output=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_WORKSPACE_SECRET": env_secret})
    assert p.returncode != 0 and message in p.stderr
    assert not (tmp_path / "ws" / "agent.json").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the directory's mode, so the file can always be deleted")
def test_a_secret_file_that_cannot_be_deleted_is_refused(tmp_path, own_home):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret").write_text(FILE_SECRET)
    os.chmod(locked, 0o500)
    try:
        p = subprocess.run([sys.executable, "-m", "proofpane_workspace", "--root", str(tmp_path / "ws"),
                            "--secret-file", str(locked / "secret")], cwd=ROOT, capture_output=True, timeout=30,
                           env={"PATH": os.environ["PATH"], "HOME": own_home})
        assert p.returncode != 0 and not (tmp_path / "ws" / "agent.json").exists()
    finally:
        os.chmod(locked, 0o700)


class _Server:
    should_exit = False


class _Agents:
    def __init__(self, live):
        self.live = live

    def live_runs(self):
        return self.live


class _Workspace:
    def __init__(self, ago, live=0):
        self.last_activity = time.monotonic() - ago
        self.agents = _Agents(live)

    def touch(self):
        self.last_activity = time.monotonic()


def test_the_watch_stops_the_server_after_the_idle_time_and_not_before():
    server, quiet = _Server(), _Workspace(ago=10)
    asyncio.run(asyncio.wait_for(exit_when_idle(server, quiet, idle=5, check=0.01), 5))
    assert server.should_exit is True
    server, busy = _Server(), _Workspace(ago=0)

    async def watch_briefly():
        task = asyncio.ensure_future(exit_when_idle(server, busy, idle=5, check=0.01))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()

    asyncio.run(watch_briefly())
    assert server.should_exit is False


def test_an_agent_s_live_run_is_activity_though_it_writes_nothing():
    # a run waiting on the owner's answer, or inside one long tool call, keeps the machine up (agents.py)
    server, waiting = _Server(), _Workspace(ago=10, live=1)

    async def watch_briefly():
        task = asyncio.ensure_future(exit_when_idle(server, waiting, idle=5, check=0.01))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()

    asyncio.run(watch_briefly())
    assert server.should_exit is False


def test_the_parent_watch_stops_the_server_once_its_parent_is_not_the_agent_s_and_not_before():
    from proofpane_workspace.__main__ import exit_with_parent
    server = _Server()
    asyncio.run(asyncio.wait_for(exit_with_parent(server, os.getpid(), check=0.01), 5))     # never its own parent
    assert server.should_exit is True
    server = _Server()

    async def watch_briefly():
        task = asyncio.ensure_future(exit_with_parent(server, os.getppid(), check=0.01))
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()

    asyncio.run(watch_briefly())
    assert server.should_exit is False


# what starts an agent, as the local provider and the tests do, then waits; named: it gives its pid
LAUNCHER = """import os, subprocess, sys, time
root, home, named = sys.argv[1:4]
env = {"PATH": os.environ["PATH"], "HOME": home, "SHELL": "/bin/sh", "LANG": "C", "PP_WORKSPACE_SECRET": "w" * 40}
if named:
    env["PP_WORKSPACE_PARENT"] = str(os.getpid())
subprocess.Popen([sys.executable, "-m", "proofpane_workspace", "--root", root], env=env,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(600)
"""


def _ended(pid, timeout):
    """Whether process `pid` is gone within `timeout` seconds; one that ended and is not yet reaped counts."""
    deadline = time.time() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except OSError:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


@pytest.mark.parametrize("named", [True, False], ids=["named", "its_parent_at_start"])
def test_the_agent_stops_once_the_program_that_started_it_is_gone(tmp_path, named):
    """The program that started the agent (the app, or a test run) killed outright, with no chance to stop it: the
    agent, left to init, stops by itself rather than run on for good with nobody to answer, as one left by a test run
    killed in the middle of a test once did, found hours later."""
    home = tmp_path / "home"
    home.mkdir()
    info = tmp_path / "ws" / "agent.json"
    launcher = subprocess.Popen([sys.executable, "-c", LAUNCHER, str(tmp_path / "ws"), str(home), "1" if named else ""],
                                cwd=ROOT)
    pid = None
    try:
        deadline = time.time() + 30
        while not info.exists():
            assert launcher.poll() is None and time.time() < deadline, "the agent did not start"
            time.sleep(0.05)
        got = json.loads(info.read_text())
        pid = got["pid"]
        while True:                                     # serving: the program that started it is gone from here
            try:
                if httpx.get(f"http://127.0.0.1:{got['port']}/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert time.time() < deadline, "the agent did not answer"
            time.sleep(0.05)
        launcher.kill()
        launcher.wait()
        assert _ended(pid, 20), "the agent outlived the program that started it"
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait()
        if pid is not None and not _ended(pid, 0):
            os.kill(pid, signal.SIGKILL)


def test_an_agent_whose_named_parent_is_gone_stops_before_it_starts(tmp_path, own_home):
    """PP_WORKSPACE_PARENT names a process that is not the agent's parent: the program that started it went before the
    agent looked, and the agent stops at once, its root untouched."""
    went = subprocess.Popen([sys.executable, "-c", "pass"])
    went.wait()
    p = subprocess.run([sys.executable, "-m", "proofpane_workspace", "--root", str(tmp_path / "ws")], cwd=ROOT,
                       env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_WORKSPACE_SECRET": SECRET,
                            "PP_WORKSPACE_PARENT": str(went.pid)}, capture_output=True, timeout=20)
    assert p.returncode != 0 and b"the program that started the agent is gone" in p.stderr
    assert not (tmp_path / "ws").exists()


@pytest.mark.parametrize("there", [True, False], ids=["the_file", "no_file_there"])
def test_an_agent_whose_starter_is_gone_deletes_the_secret_file_it_was_given(tmp_path, there, own_home):
    """Reading the secret file is what deletes it, and under the local provider it also holds the key the runs' records
    are signed with, the same at every start. An agent that finds the program that started it gone stops before it
    reads it, so it deletes the file unread as it stops, its root untouched; a file already gone stops it the same
    way."""
    secret_file = tmp_path / "secret-0123456789abcdef"
    if there:
        secret_file.write_text(FILE_SECRET + "\nrecords-key=" + "ab" * 32)
        secret_file.chmod(0o600)
    went = subprocess.Popen([sys.executable, "-c", "pass"])
    went.wait()
    p = subprocess.run([sys.executable, "-m", "proofpane_workspace", "--root", str(tmp_path / "ws"),
                        "--secret-file", str(secret_file)], cwd=ROOT,
                       env={"PATH": os.environ["PATH"], "HOME": own_home, "PP_WORKSPACE_PARENT": str(went.pid)},
                       capture_output=True, timeout=20)
    assert p.returncode != 0 and b"the program that started the agent is gone" in p.stderr, p.stderr
    assert b"Traceback" not in p.stderr
    assert not secret_file.exists()
    assert not (tmp_path / "ws").exists()


def test_an_agent_that_does_not_start_in_time_is_ended_before_its_test_is_told(tmp_path):
    """A test's agent that has not said where it listens within its time is ended, then the test told why: read from
    first, to the end of what it writes, it held the test for good (one still starting never ends by itself) and was
    left running."""
    import threading
    from conftest import processes_naming
    home = tmp_path / "home"
    home.mkdir()
    fifo = tmp_path / "secret"
    os.mkfifo(fifo)                         # the agent opens it for its secret as it starts: no writer ever comes
    told = []

    def start():
        try:
            Agent(str(tmp_path / "ws"), str(home), {"PP_WORKSPACE_SECRET": ""}, args=["--secret-file", str(fifo)],
                  start_timeout=1)
        except RuntimeError as e:
            told.append(str(e))
    starting = threading.Thread(target=start, daemon=True)
    starting.start()
    starting.join(30)
    held = starting.is_alive()
    left = processes_naming(str(tmp_path))
    for pid, _ in left:                     # whatever happened, nothing stays, and a held start is let go
        os.kill(pid, signal.SIGKILL)
    starting.join(10)
    assert not held, "the test was held reading what an agent still starting wrote"
    assert left == [], "the agent that did not start was left running"
    assert told and told[0].startswith("the agent did not start")


def _wait_exit(proc, timeout):
    try:
        return proc.wait(timeout)
    except subprocess.TimeoutExpired:
        return None


def _agent(tmp_path, idle="2"):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), args=["--idle-exit", idle])
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    return a


def test_an_idle_agent_stops_by_itself_even_while_a_page_reads_its_lists(tmp_path):
    a = _agent(tmp_path)
    try:
        codes, deadline = [], time.time() + 20
        while a.proc.poll() is None and time.time() < deadline:
            try:
                codes += [a.http.get("/sessions").status_code, a.http.get("/repos").status_code]
            except httpx.TransportError:
                break
            time.sleep(0.2)
        stopped_at = time.time()
        assert _wait_exit(a.proc, 10) == 0
        assert stopped_at < deadline                # it stopped while it was being read, not after
        assert codes and set(codes) == {200}        # and what it was read with was answered
    finally:
        a.close()


def test_a_request_that_changes_something_keeps_the_agent_running(tmp_path):
    a = _agent(tmp_path)
    try:
        codes, until = [], time.time() + 5
        while time.time() < until:
            codes.append(a.http.post("/repos", json={"url": "not an address"}).status_code)
            time.sleep(0.3)
        assert a.proc.poll() is None and set(codes) == {400}      # a refused change is still someone acting
        assert _wait_exit(a.proc, 15) == 0
    finally:
        a.close()


def test_input_to_a_terminal_keeps_the_agent_running_with_no_output(tmp_path):
    a = _agent(tmp_path)
    try:
        s = a.http.post("/sessions", json=dict(repo="demo", program="shell", task="t")).json()
        with a.terminal(s["id"]) as ws:
            ws.send(b"stty -echo; echo quiet-$((1+1)); exec cat > /dev/null\n")
            read_until(ws, b"quiet-2")
            until = time.time() + 5
            while time.time() < until:
                ws.send(b"x")
                time.sleep(0.3)
            assert a.proc.poll() is None
        assert _wait_exit(a.proc, 15) == 0
    finally:
        a.close()


def test_a_program_that_is_working_keeps_the_agent_running(tmp_path):
    a = _agent(tmp_path)
    try:
        s = a.http.post("/sessions", json=dict(repo="demo", program="shell", task="t")).json()
        with a.terminal(s["id"]) as ws:
            ws.send(b"while :; do echo tick; sleep 0.3; done\n")
            read_until(ws, b"tick")
            assert _wait_exit(a.proc, 5) is None          # well past the idle time, and still running
            ws.send(b"\x03")                               # stop the loop: from here nothing happens
        assert _wait_exit(a.proc, 15) == 0
    finally:
        a.close()



def test_the_agent_listens_before_it_says_where():
    """The socket the agent writes into agent.json already takes connections: one made the moment the file appears
    waits in the queue until the server accepts it, on Linux and on macOS alike."""
    import socket
    from proofpane_workspace.__main__ import listening_socket
    sock = listening_socket(0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
        socket.create_connection(sock.getsockname(), timeout=2).close()
    finally:
        sock.close()
