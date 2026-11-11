"""A session's shells: the person's own, in the session's worktree beside its program, up to four, run by the real
agent."""

import json
import os
import time

import pytest
from websockets.exceptions import InvalidStatus

from workspace_fixtures import Agent, make_repo, next_control, read_until


@pytest.fixture
def agent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"), origin="https://github.com/acme/demo.git")
    yield a
    a.close()


def _session(agent, **kw):
    r = agent.http.post("/sessions", json=dict(dict(repo="demo", program="shell", task="Fix the thing"), **kw))
    assert r.status_code == 200, r.text
    return r.json()


def _first_error(ws):
    msg = json.loads(ws.recv(timeout=10))
    assert msg["type"] == "error"
    return msg["detail"]


def _open(agent, sid, **body):
    r = agent.http.post(f"/sessions/{sid}/shell", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_session_has_no_shell_until_one_is_opened(agent):
    s = _session(agent)
    assert s["shells"] == []
    with agent.terminal(s["id"], which="shell/1") as ws:
        assert "no such shell open" in _first_error(ws)


def test_the_shell_runs_in_the_sessions_worktree_beside_its_program(agent, tmp_path):
    s = _session(agent)
    before = int(time.time())
    got = _open(agent, s["id"], cols=90, rows=33)
    assert got["opened"] == 1 and len(got["shells"]) == 1
    shell = got["shells"][0]
    assert shell == {"n": 1, "running": True, "started_at": shell["started_at"], "exit_code": None, "ended_at": None}
    assert before <= shell["started_at"] <= time.time()
    assert got["running"] is True                       # the program is untouched
    work = os.path.realpath(tmp_path / "ws" / "sessions" / s["id"] / "work")
    with agent.terminal(s["id"], which="shell/1") as ws:
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"stty size; pwd -P; echo id-$PP_SESSION_ID; echo from-the-shell > made.txt; echo done-$((1+1))\n")
        out = read_until(ws, b"done-2")
        assert b"33 90" in out and work.encode() in out and f"id-{s['id']}".encode() in out
    assert (tmp_path / "ws" / "sessions" / s["id"] / "work" / "made.txt").read_text() == "from-the-shell\n"
    changed = agent.http.get(f"/sessions/{s['id']}/diff").json()
    assert "made.txt" in json.dumps(changed)            # what the shell did is the session's change
    with agent.terminal(s["id"]) as ws:                 # the program's own terminal is another one
        assert next_control(ws) == {"type": "ready"}
        ws.send(b"echo program-$((3+3))\n")
        out = read_until(ws, b"program-6")
        assert b"done-2" not in out


def test_shells_open_side_by_side_up_to_four(agent):
    s = _session(agent)
    assert [_open(agent, s["id"])["opened"] for _ in range(4)] == [1, 2, 3, 4]
    r = agent.http.post(f"/sessions/{s['id']}/shell", json={})
    assert r.status_code == 400 and "at most 4 shells" in r.json()["detail"]
    with agent.terminal(s["id"], which="shell/1") as one, agent.terminal(s["id"], which="shell/2") as two:
        one.send(b"echo one-$((1+1))\n")
        two.send(b"echo two-$((2+2))\n")
        assert b"two-4" not in read_until(one, b"one-2")
        assert b"one-2" not in read_until(two, b"two-4")
    # closing one lets its place go; the next shell takes it
    r = agent.http.delete(f"/sessions/{s['id']}/shell/2")
    assert r.status_code == 200 and [x["n"] for x in r.json()["shells"]] == [1, 3, 4]
    assert _open(agent, s["id"])["opened"] == 2


def test_opening_one_again_while_it_runs_keeps_the_same_shell(agent):
    s = _session(agent)
    _open(agent, s["id"])
    with agent.terminal(s["id"], which="shell/1") as ws:
        ws.send(b"echo kept-$((20+1))\n")
        read_until(ws, b"kept-21")
    got = _open(agent, s["id"], n=1)
    assert got["opened"] == 1 and got["shells"][0]["running"] is True and len(got["shells"]) == 1
    with agent.terminal(s["id"], which="shell/1") as ws:  # a reconnect is shown its screen again
        read_until(ws, b"kept-21")


def test_an_ended_shell_says_how_and_opens_again_in_its_place(agent):
    s = _session(agent)
    _open(agent, s["id"])
    with agent.terminal(s["id"], which="shell/1") as ws:
        assert next_control(ws) == {"type": "ready"}
        before = int(time.time())
        ws.send(b"exit 6\n")
        assert next_control(ws) == {"type": "exit", "code": 6}
    got = agent.http.get(f"/sessions/{s['id']}").json()["shells"][0]
    assert got["running"] is False and got["exit_code"] == 6 and before <= got["ended_at"] <= time.time()
    assert agent.http.get(f"/sessions/{s['id']}").json()["running"] is True    # the program goes on
    with agent.terminal(s["id"], which="shell/1") as ws:  # its last screen and its exit code
        assert next_control(ws) == {"type": "ready"}
        assert b"exit 6" in read_until(ws, b"exit 6")
        assert next_control(ws) == {"type": "exit", "code": 6}
    time.sleep(1.1)
    again = _open(agent, s["id"], n=1)
    assert again["opened"] == 1 and again["shells"][0]["running"] is True
    assert again["shells"][0]["started_at"] > got["started_at"]     # a new shell, told from the one that ended
    r = agent.http.post(f"/sessions/{s['id']}/shell/1/stop")
    assert r.status_code == 200 and r.json()["shells"][0]["running"] is False and r.json()["running"] is True
    assert _open(agent, s["id"])["opened"] == 2                     # a free place comes before an ended one


def test_a_session_without_a_repository_has_no_shell_to_open(agent):
    r = agent.http.post("/sessions", json={"repo": None, "program": "shell", "task": "Look around"})
    assert r.status_code == 200, r.text
    r = agent.http.post(f"/sessions/{r.json()['id']}/shell", json={})
    assert r.status_code == 400 and "without a repository" in r.json()["detail"]


def test_an_unknown_session_or_shell_has_none(agent):
    assert agent.http.post("/sessions/aaaaaaaaaaaa/shell", json={}).status_code == 404
    assert agent.http.post("/sessions/aaaaaaaaaaaa/shell/1/stop").status_code == 404
    with agent.terminal("aaaaaaaaaaaa", which="shell/1") as ws:
        assert "no such shell open" in _first_error(ws)
    s = _session(agent)
    for n in (0, 5):
        r = agent.http.post(f"/sessions/{s['id']}/shell", json={"n": n})
        assert r.status_code == 400 and "numbered 1 to 4" in r.json()["detail"]
        assert agent.http.post(f"/sessions/{s['id']}/shell/{n}/stop").status_code == 400
        with agent.terminal(s["id"], which=f"shell/{n}") as ws:
            assert "no such shell open" in _first_error(ws)


def test_the_shell_needs_the_workspaces_secret(agent):
    import httpx
    s = _session(agent)
    for method, path in (("POST", f"/sessions/{s['id']}/shell"), ("POST", f"/sessions/{s['id']}/shell/1/stop"),
                         ("DELETE", f"/sessions/{s['id']}/shell/1")):
        assert httpx.request(method, agent.base + path).status_code == 401
    _open(agent, s["id"])
    for secret in (None, "w" * 40 + "x"):
        with pytest.raises(InvalidStatus):
            agent.terminal(s["id"], secret=secret, which="shell/1")


def test_a_shell_counts_among_the_programs_that_run_at_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PP_WORKSPACE_MAX_SESSIONS": "2"})
    try:
        make_repo(str(tmp_path / "ws" / "repos" / "demo"))
        s = _session(a)
        assert a.http.post(f"/sessions/{s['id']}/shell", json={}).status_code == 200
        r = a.http.post("/sessions", json=dict(repo="demo", program="shell", task="t"))
        assert r.status_code == 400 and "At most 2" in r.json()["detail"]
        a.http.post(f"/sessions/{s['id']}/shell/1/stop")
        assert _session(a)["running"] is True
        r = a.http.post(f"/sessions/{s['id']}/shell", json={})
        assert r.status_code == 400 and "At most 2" in r.json()["detail"]
    finally:
        a.close()


def _gone(pid, within=15):
    deadline = time.time() + within
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _shell_pid(agent, s, tmp_path, n=1):
    with agent.terminal(s["id"], which=f"shell/{n}") as ws:
        ws.send(b"trap '' HUP; echo $$ > ../shell-pid-" + str(n).encode() + b"; echo ready-$((5+5))\n")
        read_until(ws, b"ready-10")
    return int((tmp_path / "ws" / "sessions" / s["id"] / f"shell-pid-{n}").read_text())


def test_removing_the_session_hangs_up_its_shells(agent, tmp_path):
    s = _session(agent)
    _open(agent, s["id"])
    _open(agent, s["id"])
    pids = [_shell_pid(agent, s, tmp_path, n) for n in (1, 2)]
    assert agent.http.delete(f"/sessions/{s['id']}").status_code == 200
    assert all(_gone(pid) for pid in pids), "a session's shell outlived its session"
    assert not (tmp_path / "ws" / "sessions" / s["id"]).exists()


def test_closing_the_session_hangs_up_its_shells_and_keeps_their_places(agent, tmp_path):
    """A close stops the session's program and its own shells, as Stop in each shell's tab does: nothing
    of it runs hidden, and each shell's place stays, to open again in it."""
    s = _session(agent)
    _open(agent, s["id"])
    _open(agent, s["id"])
    pids = [_shell_pid(agent, s, tmp_path, n) for n in (1, 2)]
    r = agent.http.post(f"/sessions/{s['id']}/close")
    assert r.status_code == 200 and r.json()["closed"] is True, r.text
    assert all(_gone(pid) for pid in pids), "a closed session's shell went on running"
    shells = agent.http.get(f"/sessions/{s['id']}").json()["shells"]
    assert [(x["n"], x["running"]) for x in shells] == [(1, False), (2, False)]


def test_stopping_the_agent_hangs_up_the_sessions_shells(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home))
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    s = _session(a)
    a.http.post(f"/sessions/{s['id']}/shell", json={})
    pid = _shell_pid(a, s, tmp_path)
    a.close()
    assert _gone(pid), "a session's shell that ignores a hangup outlived the agent"


def test_closing_a_shell_hangs_it_up(agent, tmp_path):
    s = _session(agent)
    _open(agent, s["id"])
    pid = _shell_pid(agent, s, tmp_path)
    r = agent.http.delete(f"/sessions/{s['id']}/shell/1")
    assert r.status_code == 200 and r.json()["shells"] == []
    assert _gone(pid), "a closed shell went on running"


def test_closing_a_shell_leaves_one_opened_in_its_place_meanwhile(tmp_path):
    import asyncio
    from proofpane_workspace.workspace import Workspace
    root = tmp_path / "ws"
    make_repo(str(root / "repos" / "demo"))
    w = Workspace(str(root))
    record, _ = w.prepare("demo", "shell", "Look")

    async def run():
        _, n = w.shell_start(record["id"])
        old = w.shells[record["id"]][n]
        from types import SimpleNamespace
        newer, stop = SimpleNamespace(running=False, started_at=1, exit_code=0, ended_at=2), old.stop

        async def stop_while_another_opens():
            await stop()
            w.shells[record["id"]][n] = newer      # opened in its place while the old one was hung up
        old.stop = stop_while_another_opens
        await w.shell_close(record["id"], n)
        return w.shells[record["id"]].get(n) is newer
    assert asyncio.run(run())
