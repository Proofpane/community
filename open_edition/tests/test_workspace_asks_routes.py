"""Agents that ask for sessions, through the workspace agent (agent.py, workspace.py, asks.py, command.py): off until
the person turns it on; then each terminal agent's program gets a token of its own, the agent's address and the
`proofpane` command; with
the token it reads its own repository's sessions and asks for a new one, which starts only when the person starts
it; the workspace's secret is not a token, nor a token the secret."""

import json
import os
import stat
import subprocess
import time

import httpx
import pytest

from workspace_fixtures import CLAUDE_VERSION_SH, Agent, make_repo

ENV_LINES = 3


def _stand_in(bin_dir, name):
    """A terminal agent that writes what it was given to ask with, then waits like an agent."""
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + (CLAUDE_VERSION_SH if name == "claude" else "") +
                    'printf "%s\\n%s\\n%s\\n" "${PP_ASK_TOKEN-unset}" "${PP_ASK_URL-unset}" '
                    '"$(command -v proofpane || echo none)" > "$PP_SESSION_DIR/ask.env.tmp"\n'
                    'for a in "$@"; do printf "%s\\0" "$a"; done > "$PP_SESSION_DIR/args"\n'
                    'mv "$PP_SESSION_DIR/ask.env.tmp" "$PP_SESSION_DIR/ask.env"\n'
                    "sleep 600\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def agent(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        _stand_in(bin_dir, name)
    home = tmp_path / "home"
    home.mkdir()
    a = Agent(str(tmp_path / "ws"), str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
    make_repo(str(tmp_path / "ws" / "repos" / "demo"))
    make_repo(str(tmp_path / "ws" / "repos" / "private"))
    a.tmp = tmp_path
    yield a
    a.close()


def _start(agent, program="codex", repo="demo", task="Fix the parser"):
    r = agent.http.post("/sessions", json={"repo": repo, "program": program, "task": task})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _given(agent, sid, wait=10):
    path = agent.tmp / "ws" / "sessions" / sid / "ask.env"
    end = time.time() + wait
    while time.time() < end:
        if path.exists():
            return path.read_text().split("\n")[:ENV_LINES]
        time.sleep(0.05)
    raise AssertionError("the program never started")


def _as(agent, token):
    return httpx.Client(base_url=agent.base, headers={"Authorization": "Bearer " + token}, timeout=30)


def test_asks_are_off_until_the_person_turns_them_on(agent):
    assert agent.http.get("/asks").json() == {"on": False, "asks": []}
    sid = _start(agent)
    assert _given(agent, sid) == ["unset", "unset", "none"]


def test_a_program_asks_with_its_own_token_and_reads_only_its_own_repository(agent):
    assert agent.http.post("/asks/settings", json={"on": True}).json()["on"] is True
    sid = _start(agent)
    other = _start(agent, program="claude", task="Another on the same repository")
    private = _start(agent, repo="private", task="A private repository's work")
    token, url, pp = _given(agent, sid)
    assert len(token) >= 40 and url == agent.base and pp.endswith("/bin/proofpane")
    _given(agent, other)
    _given(agent, private)
    with _as(agent, token) as me:
        listed = me.get("/ask/sessions").json()["sessions"]
        assert {s["id"] for s in listed} == {sid, other}            # not the private repository's session
        mine = next(s for s in listed if s["id"] == sid)
        assert mine["you"] is True and mine["program"] == "codex" and mine["state"] == "running"
        assert me.get(f"/ask/sessions/{other}").json()["task"] == "Another on the same repository"
        assert me.get(f"/ask/sessions/{other}/diff").status_code == 200
        for path in (f"/ask/sessions/{private}", f"/ask/sessions/{private}/diff", "/ask/sessions/nope"):
            r = me.get(path)
            assert r.status_code == 404 and r.json()["detail"] == "No such session on this session's repository.", path
        # a token is not the workspace's secret: nothing of the person's
        for path in ("/sessions", "/asks", f"/sessions/{sid}/diff"):
            assert me.get(path).status_code == 401, path
    # nor is the workspace's secret a token
    assert agent.http.get("/ask/sessions").status_code == 401


def test_an_ask_waits_for_the_person_who_starts_it_or_refuses_it(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    token = _given(agent, sid)[0]
    with _as(agent, token) as me:
        r = me.post("/ask/sessions", json={"program": "claude", "task": "Write the tests for the parser", "base": "main"})
        assert r.status_code == 200, r.text
        ask = r.json()
        for bad, said in ((dict(program="vim", task="x"), "Ask for one of the terminal agents"),
                          (dict(program="codex", task="run\x1b[2J this"), "A task is text"),
                          (dict(program="codex", task="a" + chr(0x202E) + "b"), "A task is text"),
                          (dict(program="codex", task="x", base="-q"), "Name the base")):
            r = me.post("/ask/sessions", json=bad)
            assert r.status_code == 400 and r.json()["detail"].startswith(said), (bad, r.text)
    waiting = agent.http.get("/asks").json()["asks"]
    label = next(r["label"] for r in agent.http.get("/repos").json()["repos"] if r["name"] == "demo")
    assert [(a["id"], a["program"], a["task"], a["base"], a["by"]["id"], a["label"]) for a in waiting] == \
        [(ask["id"], "claude", "Write the tests for the parser", "main", sid, label)]
    # nothing starts until the person starts it
    assert len(agent.http.get("/sessions").json()["sessions"]) == 1
    started = agent.http.post(f"/asks/{ask['id']}/start", json={})
    assert started.status_code == 200, started.text
    new = started.json()
    assert (new["program"], new["task"], new["asked_by"], new["repo"]) == \
        ("claude", "Write the tests for the parser", sid, "demo")
    assert agent.http.get("/asks").json()["asks"] == []
    assert agent.http.post(f"/asks/{ask['id']}/start", json={}).status_code == 404     # answered once
    # a session started from an ask reads, but may not ask in turn
    with _as(agent, _given(agent, new["id"])[0]) as it:
        assert it.get("/ask/sessions").status_code == 200
        r = it.post("/ask/sessions", json={"program": "codex", "task": "And another"})
        assert r.status_code == 400 and "may not ask for another" in r.json()["detail"]
    # an ask refused goes
    with _as(agent, token) as me:
        again = me.post("/ask/sessions", json={"program": "codex", "task": "Refuse this"}).json()
    assert agent.http.post(f"/asks/{again['id']}/refuse").json() == {"on": True, "asks": []}
    assert agent.http.post(f"/asks/{again['id']}/refuse").status_code == 404
    assert agent.http.post("/asks/not-an-id/refuse").status_code == 404


def test_a_token_is_nothing_once_its_program_stops_or_asks_are_turned_off(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    token = _given(agent, sid)[0]
    with _as(agent, token) as me:
        assert me.get("/ask/sessions").status_code == 200
        me.post("/ask/sessions", json={"program": "codex", "task": "Waiting"})
        assert len(agent.http.get("/asks").json()["asks"]) == 1
        agent.http.post("/asks/settings", json={"on": False})
        assert me.get("/ask/sessions").status_code == 401
        assert agent.http.get("/asks").json() == {"on": False, "asks": []}        # what waited went with it
        agent.http.post("/asks/settings", json={"on": True})
        assert me.get("/ask/sessions").status_code == 401          # turned off, its token went; a restart gives one
        agent.http.post(f"/sessions/{sid}/stop")
        assert me.get("/ask/sessions").status_code == 401
    assert agent.http.post("/asks/settings", json={"on": "yes"}).status_code == 400


def test_the_command_in_a_session_speaks_for_it(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    token, url, pp = _given(agent, sid)
    env = dict(os.environ, PP_ASK_TOKEN=token, PP_ASK_URL=url)
    out = subprocess.run([pp, "sessions"], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and sid in out.stdout and "(this session)" in out.stdout, out
    out = subprocess.run([pp, "ask", "--agent", "claude", "--task", "Review the parser"], env=env, capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0 and "the person decides in the app" in out.stdout, out
    assert agent.http.get("/asks").json()["asks"][0]["task"] == "Review the parser"
    out = subprocess.run([pp, "sessions"], env=dict(env, PP_ASK_TOKEN="x" * 43), capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 1 and "Not a running session's token" in out.stderr


def _args(agent, sid):
    _given(agent, sid)
    return (agent.tmp / "ws" / "sessions" / sid / "args").read_text().split("\0")[:-1]


def test_claude_and_codex_are_told_of_the_command_only_when_asks_are_on(agent):
    from proofpane_workspace import asks
    off = _start(agent, program="claude")
    assert "--append-system-prompt" not in _args(agent, off)
    agent.http.post("/asks/settings", json={"on": True})
    told = _args(agent, _start(agent, program="claude", task="Work on it"))
    assert told[:2] == ["--append-system-prompt", asks.NOTE] and told[-1] == "Work on it"
    told = _args(agent, _start(agent, program="codex", task="Work on it"))
    assert told[:2] == ["-c", "developer_instructions=" + json.dumps(asks.NOTE)] and told[-1] == "Work on it"
    assert "proofpane ask --agent AGENT --task TEXT" in asks.NOTE


def test_an_ask_and_its_answer_are_on_the_asking_session_s_record(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    with _as(agent, _given(agent, sid)[0]) as me:
        first = me.post("/ask/sessions", json={"program": "claude", "task": "Write the tests", "base": "main"}).json()
        second = me.post("/ask/sessions", json={"program": "codex", "task": "Refuse this"}).json()
    new = agent.http.post(f"/asks/{first['id']}/start", json={}).json()
    agent.http.post(f"/asks/{second['id']}/refuse")
    log = agent.http.get(f"/sessions/{sid}/gate").json()["asks"]
    assert [(e["event"], e["ask"], e["program"]) for e in log] == [
        ("asked", first["id"], "claude"), ("asked", second["id"], "codex"),
        ("started", first["id"], "claude"), ("refused", second["id"], "codex")]
    assert (log[0]["task"], log[0]["base"], log[2]["session"]) == ("Write the tests", "main", new["id"])
    assert agent.http.get(f"/sessions/{new['id']}/gate").json()["asks"] == []


def test_an_ask_from_a_repository_since_removed_is_still_listed_and_says_so_when_started(agent):
    import shutil
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    with _as(agent, _given(agent, sid)[0]) as me:
        ask = me.post("/ask/sessions", json={"program": "codex", "task": "Later"}).json()
    shutil.rmtree(agent.tmp / "ws" / "repos" / "demo")
    listed = agent.http.get("/asks").json()["asks"]
    assert [(a["id"], a["label"]) for a in listed] == [(ask["id"], "demo")]
    r = agent.http.post(f"/asks/{ask['id']}/start", json={})
    assert r.status_code in (400, 404) and r.json()["detail"]
    assert [a["id"] for a in agent.http.get("/asks").json()["asks"]] == [ask["id"]]      # back, to be answered again


def test_the_switch_is_in_the_agent_s_memory_and_no_file_turns_it_on(agent):
    # a session's programs run as the agent's user: a file they could write must not turn asks on
    (agent.tmp / "ws" / "asks.json").write_text('{"on": true}')
    assert agent.http.get("/asks").json()["on"] is False
    sid = _start(agent)
    assert _given(agent, sid)[:2] == ["unset", "unset"]


def test_a_session_sees_its_own_asks_and_how_each_was_answered(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    other = _start(agent, task="Another")
    with _as(agent, _given(agent, sid)[0]) as me, _as(agent, _given(agent, other)[0]) as them:
        first = me.post("/ask/sessions", json={"program": "claude", "task": "Review it", "base": "main"}).json()
        second = me.post("/ask/sessions", json={"program": "codex", "task": "Refuse this"}).json()
        third = me.post("/ask/sessions", json={"program": "codex", "task": "Still waiting"}).json()
        them.post("/ask/sessions", json={"program": "codex", "task": "Not mine"})
        new = agent.http.post(f"/asks/{first['id']}/start", json={}).json()
        agent.http.post(f"/asks/{second['id']}/refuse")
        got = me.get("/ask/asks").json()["asks"]
    assert [(a["id"], a["state"], a["session"], a["task"]) for a in got] == [
        (first["id"], "started", new["id"], "Review it"), (second["id"], "refused", None, "Refuse this"),
        (third["id"], "waiting", None, "Still waiting")]


def test_an_ask_no_one_answered_is_gone(tmp_path):
    from proofpane_workspace import workspace as ws
    w = ws.Workspace(str(tmp_path / "ws"))
    make_repo(os.path.join(w.repos_dir, "demo"))
    record, _ = w.prepare("demo", "shell", "Work")
    w.set_asks_on(True)
    bound = {"session": record["id"], "repo": "demo", "may_ask": True}
    ask = w.ask(bound, "claude", "Review it")
    assert [a["state"] for a in w.asks_of(bound)["asks"]] == ["waiting"]
    w.asks.take(ask["id"])                      # expired, or withdrawn: not waiting, and never answered
    assert [a["state"] for a in w.asks_of(bound)["asks"]] == ["gone"]


def test_what_a_session_s_program_writes_in_its_own_record_changes_nothing_a_token_can_do(agent):
    agent.http.post("/asks/settings", json={"on": True})
    sid = _start(agent)
    private = _start(agent, repo="private", task="A private repository's work")
    token = _given(agent, sid)[0]
    _given(agent, private)
    with _as(agent, token) as me:
        ask = me.post("/ask/sessions", json={"program": "codex", "task": "Review it"}).json()
        new = agent.http.post(f"/asks/{ask['id']}/start", json={}).json()
    child = _given(agent, new["id"])[0]
    # the programs rewrite their sessions' records: one names another repository, the other forgets whose ask it was
    for which, change in ((sid, {"repo": "private"}), (new["id"], {"asked_by": None})):
        path = agent.tmp / "ws" / "sessions" / which / "session.json"
        record = json.loads(path.read_text())
        record.update(change)
        path.write_text(json.dumps(record))
    with _as(agent, token) as me, _as(agent, child) as it:
        assert {s["id"] for s in me.get("/ask/sessions").json()["sessions"]} == {sid, new["id"]}
        assert me.get(f"/ask/sessions/{private}/diff").status_code == 404
        r = it.post("/ask/sessions", json={"program": "codex", "task": "And another"})
        assert r.status_code == 400 and "may not ask for another" in r.json()["detail"]
