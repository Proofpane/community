"""Agents' runs in the workspace (agents.py): a kept chat run again, through a real agent with the real Claude
Agent SDK driving the stand-in claude (chat_stand_in.py)."""

import asyncio
import json
import os
import sys
import time

import pytest

from proofpane_workspace import chat as chats
from workspace_fixtures import Agent, git, make_repo

NEEDS_SDK = "needs the Claude Agent SDK (requirements-dev.txt installs it)"
pytestmark = pytest.mark.skipif(not chats.available(), reason=NEEDS_SDK)
STAND_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_stand_in.py")
AGENT = "a1a2a3a4a5a6"
OTHER = "b1b2b3b4b5b6"


class Workspace:
    """A workspace agent with the stand-in claude, a repository, and its folder.

    Its runs are chats, which need the Claude Agent SDK: without it, a test that makes one is skipped here, in whichever
    module it is (eleven others build on this one), rather than wait out its timeouts on runs that fail at once."""

    def __init__(self, tmp_path, config=None):
        if not chats.available():
            pytest.skip(NEEDS_SDK)
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(exist_ok=True)
        head = f"#!{sys.executable}\n" + (f"CONFIG = {config!r}\n" if config else "")
        (self.bin / "claude").write_text(head + open(STAND_IN).read())
        (self.bin / "claude").chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.root = str(tmp_path / "ws")
        if not os.path.isdir(os.path.join(self.root, "repos", "demo")):
            make_repo(os.path.join(self.root, "repos", "demo"))
        self.start()

    def start(self):
        info = os.path.join(self.root, "agent.json")
        if os.path.exists(info):
            os.unlink(info)                 # as the local provider does before a start: the last one's port is gone
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        self.http = self.agent.http

    def stop(self):
        self.agent.close()


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    yield w
    w.stop()


def put(ws, brief="hello", agent_id=AGENT, **over):
    body = dict({"id": agent_id, "name": "Triage", "brief": brief, "repo": "demo",
                 "author": {"name": "Ana", "email": "ana@example.org"}}, **over)
    r = ws.http.put(f"/agents/{agent_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run(ws, agent_id=AGENT, prompt=None):
    r = ws.http.post(f"/agents/{agent_id}/runs", json={"trigger": {"kind": "manual", "prompt": prompt}})
    assert r.status_code == 200, r.text
    return r.json()


def view(ws, run_id):
    (got,) = [r for r in ws.http.get("/agents/runs?since=0").json()["runs"] if r["run_id"] == run_id]
    return got


def until(ws, run_id, states, timeout=30):
    deadline = time.time() + timeout
    while True:
        got = view(ws, run_id)
        if got["state"] in states:
            return got
        assert time.time() < deadline, got
        time.sleep(0.05)


def log(ws, run_id):
    return ws.http.get(f"/sessions/{run_id}/chat").json()


def kinds(ws, run_id):
    return [(e["kind"], e.get("text") or e.get("state") or e.get("summary") or e.get("subtype"))
            for e in log(ws, run_id)["events"]]


def starts(ws):
    with open(ws.home / "stand-in-runs.jsonl") as fh:
        return [json.loads(line) for line in fh]


def settings_of(start):
    return json.loads(start["args"][start["args"].index("--settings") + 1])


# ---- an agent, and a run of it ------------------------------------------------------------------------------------

def test_a_run_is_a_chat_with_the_brief_first_in_the_agent_s_own_worktree(ws):
    put(ws, "hello")
    r = run(ws, prompt="look at the open issues")
    assert r["state"] == "queued" and r["trigger"]["prompt"] == "look at the open issues"
    done = until(ws, r["run_id"], ("done",))
    work = os.path.join(ws.root, "agents", AGENT, "work")
    first = log(ws, r["run_id"])["events"][0]
    assert first["kind"] == "user" and first["text"].startswith("hello\n\nlook at the open issues\n\n")
    assert "ask_owner" in first["text"]
    (start,) = starts(ws)
    assert os.path.realpath(start["cwd"]) == os.path.realpath(work)
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=work) == f"pp/agent-{AGENT}"
    assert done["base"] == git("rev-parse", "HEAD", cwd=os.path.join(ws.root, "repos", "demo"))
    # not a session of the list, but a session to every route a pane uses
    assert ws.http.get("/sessions").json()["sessions"] == []
    assert ws.http.get(f"/sessions/{r['run_id']}").json()["chat"]["state"] in ("stopped", "idle")
    assert ws.http.delete(f"/sessions/{r['run_id']}").status_code == 400


def test_a_run_s_claude_has_background_tasks_off_the_gate_s_mark_and_its_agent_s_memory(ws):
    put(ws, "hello", keep_worktree=False)
    until(ws, run(ws)["run_id"], ("done",))
    (start,) = starts(ws)
    env = set(start["env"])
    assert {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "PP_AGENT_RUN", "PP_AGENT_ID", "PP_AGENT_DIR"} <= env
    memory = settings_of(start)["autoMemoryDirectory"]
    assert os.path.realpath(memory) == os.path.realpath(os.path.join(ws.root, "agents", AGENT, "memory"))
    assert os.path.isdir(memory) and not os.path.realpath(memory).startswith(os.path.realpath(start["cwd"]) + os.sep)
    # with each run in a fresh worktree, the memory is still the agent's one folder
    assert os.path.realpath(start["cwd"]).endswith("/work") and "/runs/" in start["cwd"]
    servers = json.loads(start["args"][start["args"].index("--mcp-config") + 1])["mcpServers"]
    assert servers == {"proofpane": {"type": "sdk", "name": "proofpane"}}


def test_the_worktree_is_kept_between_runs_and_each_run_s_change_is_its_own(ws):
    put(ws, "run echo one > a.txt")
    first = run(ws)["run_id"]
    _answer(ws, first)
    one = until(ws, first, ("done",))
    put(ws, "run echo two > b.txt")
    second = run(ws)["run_id"]
    _answer(ws, second)
    two = until(ws, second, ("done",))
    work = os.path.join(ws.root, "agents", AGENT, "work")
    assert open(os.path.join(work, "a.txt")).read() == "one\n"            # the first run's file, kept
    assert two["base"] == one["head"] and one["head"] != one["base"]
    for run_id, name in ((first, "a.txt"), (second, "b.txt")):
        r = ws.http.post(f"/sessions/{run_id}/finish", json={"author_name": "Ana", "author_email": "ana@example.org"})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["committed"] is False and f"+++ b/{name}" in got["diff"] and got["files"] == 1
    message = git("log", "-1", "--format=%an <%ae>%n%B", cwd=work)
    assert message.startswith("Ana <ana@example.org>\nTriage: run ") and f"Proofpane-Run: {second}" in message


def _answer(ws, run_id, decision="allow"):
    deadline = time.time() + 30
    while True:
        pending = log(ws, run_id)["pending"]
        if pending:
            break
        assert time.time() < deadline
        time.sleep(0.05)
    assert view(ws, run_id)["state"] == "waiting"
    r = ws.http.post(f"/sessions/{run_id}/chat/answer", json={"id": pending[0], "decision": decision})
    assert r.status_code == 200, r.text


def test_one_run_at_a_time_per_agent(ws):
    put(ws, "run sleep 2")
    first, second = run(ws)["run_id"], run(ws)["run_id"]
    _answer(ws, first)
    time.sleep(0.5)                                     # time enough for a second start, were it allowed
    assert view(ws, second)["state"] == "queued"
    one = until(ws, first, ("done",))
    _answer(ws, second)
    two = until(ws, second, ("done",))
    assert two["started_at"] >= one["ended_at"]


def test_a_question_for_the_owner_waits_with_no_claude_and_the_answer_is_the_next_message(ws):
    put(ws, "ask Which branch should I base this on?")
    r = run(ws)
    waiting = until(ws, r["run_id"], ("waiting",))
    assert waiting["question"] == "Which branch should I base this on?"
    assert ("question", "Which branch should I base this on?") in kinds(ws, r["run_id"])
    tool_end = [e for e in log(ws, r["run_id"])["events"] if e["kind"] == "tool_end"][0]
    assert tool_end["summary"].startswith("Asked. The owner is not watching this run")
    deadline = time.time() + 10
    while ws.http.get(f"/sessions/{r['run_id']}").json()["running"]:       # its claude stopped while it waits
        assert time.time() < deadline
        time.sleep(0.05)
    sent = ws.http.post(f"/agents/runs/{r['run_id']}/message", json={"text": "hello"})
    assert sent.status_code == 200, sent.text
    done = until(ws, r["run_id"], ("done",))
    assert done["question"] is None
    assert ("user", "hello") in kinds(ws, r["run_id"])
    assert len(starts(ws)) == 2                                          # claude started again, with the conversation
    sdk = [e for e in log(ws, r["run_id"])["events"] if e["kind"] == "session"][0]["sdk_session"]
    assert f"--resume={sdk}" in starts(ws)[1]["args"]


def test_a_run_with_a_sub_agent_ends_only_after_its_report(ws):
    put(ws, "subagent check the tests")
    done = until(ws, run(ws)["run_id"], ("done",))
    events = log(ws, done["run_id"])["events"]
    names = [(e["kind"], e.get("summary") or e.get("text")) for e in events if e["kind"] in ("tool_end", "result")]
    assert names[0] == ("tool_end", "Sub-agent report: check the tests") and names[-1][0] == "result"
    # its requests, each once though written on two lines, with the sub-agent's own file
    assert done["requests"] == 3
    assert done["tokens"] == {"input_tokens": 30, "output_tokens": 15, "cache_read_input_tokens": 9,
                              "cache_creation_input_tokens": 6}


def test_an_error_result_fails_the_run(ws):
    put(ws, "fail")
    failed = until(ws, run(ws)["run_id"], ("failed",))
    assert failed["reason"] == "the turn ended with an error (error_during_execution)"


def test_a_run_s_cost_is_its_turns_own(tmp_path):
    w = Workspace(tmp_path)
    try:
        put(w, "ask First?")
        r = run(w)["run_id"]
        until(w, r, ("waiting",))
        w.http.post(f"/agents/runs/{r}/message", json={"text": "ask Second?"})
        deadline = time.time() + 30
        while view(w, r)["question"] != "Second?":
            assert time.time() < deadline
            time.sleep(0.05)
        until(w, r, ("waiting",))
        w.http.post(f"/agents/runs/{r}/message", json={"text": "hello"})
        done = until(w, r, ("done",))
        # three turns of 0.0123 each, each in a claude of its own whose running total started over
        assert done["cost_est_usd"] == pytest.approx(0.0369) and done["steps"] == 3
    finally:
        w.stop()


def test_a_claude_that_brings_its_total_back_is_not_counted_twice(tmp_path):
    w = Workspace(tmp_path, config={"VERSION": "2.1.277", "RESTORES": True})
    try:
        put(w, "ask First?")
        r = run(w)["run_id"]
        until(w, r, ("waiting",))
        w.http.post(f"/agents/runs/{r}/message", json={"text": "hello"})
        assert until(w, r, ("done",))["cost_est_usd"] == pytest.approx(0.0246)
    finally:
        w.stop()


def test_a_machine_stop_blocks_a_live_run_and_holds_its_agent_until_the_owner_resumes_it(tmp_path):
    w = Workspace(tmp_path)
    put(w, "wait")
    live = run(w)["run_id"]
    until(w, live, ("working",))
    queued = run(w)["run_id"]                                          # behind the live one
    w.stop()
    left = os.path.join(w.root, "agents", AGENT, f"servers-{live}.json")
    with open(left, "w") as fh:                                        # a run's servers, left by the stop
        fh.write("{}")
    w.start()
    try:
        blocked = until(w, live, ("blocked",))
        assert (blocked["reason"], blocked["cause"]) == ("machine stopped", "machine")
        assert not os.path.exists(left)
        assert blocked["hold"].startswith("The machine stopped while its run worked")
        # what it was looking at went with the agent that stopped: its agent waits for the owner
        assert until(w, queued, ("stopped",))["reason"] == "the agent was paused or retired"
        assert w.http.get(f"/agents/{AGENT}").json()["paused"] is True
        put(w, "hello")                         # a mirror the app sent before it knew: still held
        assert w.http.get(f"/agents/{AGENT}").json()["paused"] is True
        put(w, "hello", paused=True)            # the app knows,
        put(w, "hello")                         # and the owner resumed it
        assert until(w, run(w)["run_id"], ("done",))["state"] == "done"
    finally:
        w.stop()


def test_a_message_is_kept_while_the_turn_works_and_refused_once_the_run_ended(ws):
    put(ws, "run sleep 1")
    r = run(ws)["run_id"]
    _answer(ws, r)
    sent = ws.http.post(f"/agents/runs/{r}/message", json={"text": "hello"})
    assert sent.status_code == 200 and sent.json()["queued"] in (0, 1)
    done = until(ws, r, ("done",))
    assert [k for k in kinds(ws, r) if k[0] in ("queued", "user")][-2:] in (
        [("queued", "hello"), ("user", "hello")], [("user", "hello"), ("user", "hello")])
    assert done["steps"] == 2
    refused = ws.http.post(f"/agents/runs/{r}/message", json={"text": "hello"})
    assert refused.status_code == 400 and "has ended" in refused.json()["detail"]
    refused = ws.http.post(f"/sessions/{r}/chat", json={"text": "hello"})
    assert refused.status_code == 400 and "has ended" in refused.json()["detail"]


def test_stop_ends_a_run_as_stopped(ws):
    put(ws, "wait")
    r = run(ws)["run_id"]
    until(ws, r, ("working",))
    stopped = ws.http.post(f"/agents/runs/{r}/stop").json()
    assert (stopped["state"], stopped["reason"]) == ("stopped", "stopped by you")


def test_pause_and_retire_hold_runs(ws):
    put(ws, "hello", paused=True)
    r = ws.http.post(f"/agents/{AGENT}/runs", json={})
    assert r.status_code == 400 and "paused" in r.json()["detail"]
    put(ws, "hello")
    until(ws, run(ws)["run_id"], ("done",))
    got = ws.http.post(f"/agents/{AGENT}/retire").json()
    assert got["retired"] is True and not os.path.exists(os.path.join(ws.root, "agents", AGENT, "work"))
    assert ws.http.post(f"/agents/{AGENT}/runs", json={}).status_code == 400


def test_an_agent_without_a_repository_works_in_a_scratch_one_never_listed(ws):
    put(ws, "hello", repo=None)
    done = until(ws, run(ws)["run_id"], ("done",))
    assert done["base"] and ws.http.get("/repos").json()["repos"][0]["name"] == "demo"
    assert len(ws.http.get("/repos").json()["repos"]) == 1


def test_the_poll_reads_only_what_changed(ws):
    put(ws, "hello")
    r = run(ws)["run_id"]
    done = until(ws, r, ("done",))
    got = ws.http.get(f"/agents/runs?since={done['seq']}").json()
    assert got["runs"] == [] and got["seq"] >= done["seq"]
    assert ws.http.get("/agents/runs?since=x").status_code == 400


def test_the_poll_is_given_runs_a_page_at_a_time_oldest_change_first(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    monkeypatch.setattr(agentry, "PAGE", 2)
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    made = [agents.queue(AGENT)["run_id"] for _ in range(5)]
    seen, since, pages = [], 0, 0
    while True:
        got = agents.since(since)
        pages += 1
        assert len(got["runs"]) <= 2 and [r["seq"] for r in got["runs"]] == sorted(r["seq"] for r in got["runs"])
        seen += [r["run_id"] for r in got["runs"]]
        assert got["seq"] == (got["runs"][-1]["seq"] if got["more"] else agents._seq)
        since = got["seq"]
        if not got["more"]:
            break
    assert (seen, pages) == (made, 3)
    agents._change(made[0], lambda r: r["run"].update(last_text="x" * 9000))
    assert agents.since(since)["runs"][0]["last_text"] == "x" * 2000


def test_a_start_that_fails_in_a_way_of_its_own_fails_its_run_and_is_tried_once(tmp_path):
    """A start runs in a task that pumps the next as it ends: one that raised and left its run queued would be picked
    again at once, and again, for as long as the agent ran."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    tries = []

    async def broken(run_id):
        tries.append(run_id)
        raise OSError("the disk said no")
    agents._start = broken

    async def go():
        agents._pump_lock = asyncio.Lock()
        agents.started = True
        agents.pump()
        for _ in range(50):
            await asyncio.sleep(0.01)
    asyncio.run(go())
    failed = agents.read(r)["run"]
    assert (failed["state"], failed["cause"], failed["reason"]) == ("failed", "start", "the disk said no")
    assert tries == [r] and not agents._starting


def test_a_run_stopped_while_its_worktree_is_made_never_runs_its_precheck(tmp_path):
    """Stopped before its precheck began (its worktree still being made): the repository's precheck code is never run
    after the owner said stop, nor does the start mark it working after them; it stays stopped."""
    import asyncio
    import threading
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            accounts=SimpleNamespace(for_session=lambda kind, account: None),
                            _bind=lambda run_id, on: None)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "true"})
    r = agents.queue(AGENT)["run_id"]
    making, ran = threading.Event(), []

    def slow_prepare(run_id, agent):
        making.set()
        time.sleep(0.5)
        return "demo", "0" * 40
    agents._prepare = slow_prepare
    agents._baseline = lambda run_id, agent: None
    agents._precheck = lambda run_id, agent: ran.append(run_id) or (0, "")

    async def go():
        agents._pump_lock = asyncio.Lock()
        agents.started = True
        agents.pump()
        while not making.is_set():
            await asyncio.sleep(0.01)
        await agents.stop(r)
        while r in agents._starting:
            await asyncio.sleep(0.01)
    asyncio.run(go())
    stopped = agents.read(r)
    assert (stopped["run"]["state"], stopped["run"]["reason"], ran) == ("stopped", "stopped by you", [])
    assert stopped["base"] == "0" * 40 and r not in agents.working          # what its start made, written as it left


def test_a_precheck_begun_just_after_a_stop_is_ended_too(tmp_path):
    """The stop landing after the start looked (still queued) and before the precheck's process began: one look for
    its processes finds none, so they are looked for while its start lasts."""
    import asyncio
    import threading
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry, processes
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            accounts=SimpleNamespace(for_session=lambda kind, account: None),
                            _bind=lambda run_id, on: None)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "true"})
    r = agents.queue(AGENT)["run_id"]
    agents._prepare = lambda run_id, agent: ("demo", "0" * 40)
    agents._baseline = lambda run_id, agent: None
    asked, began = threading.Event(), []

    def precheck(run_id, agent):
        asked.set()
        time.sleep(0.5)                                 # the stop lands here, before its process begins
        # begun as the precheck's own process is (agents._precheck): on a Mac in a session the run owns
        p = processes.start(["sleep", "30"], run_id, env=dict(os.environ, **{processes.MARK: run_id}))
        began.append(p)
        return p.wait(), ""
    agents._precheck = precheck

    async def go():
        agents._pump_lock = asyncio.Lock()
        agents.started = True
        agents.pump()
        while not asked.is_set():
            await asyncio.sleep(0.01)
        await agents.stop(r)
        t0 = time.monotonic()
        while r in agents._starting:
            assert time.monotonic() - t0 < 10, "the precheck begun after the stop ran on"
            await asyncio.sleep(0.05)
    try:
        asyncio.run(go())
    finally:
        for p in began:
            p.kill()
    assert agents.read(r)["run"]["state"] == "stopped" and began[0].returncode != 0


def test_a_precheck_that_takes_too_long_is_ended_with_what_it_left(tmp_path, monkeypatch):
    """Past its time the precheck's shell is ended, and what it left running with it: on a Mac by the session it
    began, which the run owns (its sleep, Apple's, shows ps no mark), elsewhere by the mark."""
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    from test_agents_watch import _gone
    monkeypatch.setattr(agentry, "PRECHECK_TIMEOUT", 1)
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            accounts=SimpleNamespace(for_session=lambda kind, account: None),
                            _bind=lambda run_id, on: None)
    agents = agentry.Agents(space)
    pidfile = tmp_path / "pid"
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo",
                "precheck": f"sleep 300 >/dev/null 2>&1 & echo $! > '{pidfile}'; sleep 300"})
    r = agents.queue(AGENT)["run_id"]
    monkeypatch.setattr(agents, "work", lambda run_id, agent: str(tmp_path))
    left = None
    try:
        t0 = time.monotonic()
        assert agents._precheck(r, agents.agent(AGENT)) == (124, "it took longer than 1 seconds")
        # at its time, not when what it started lets its output go: its second sleep holds the shell's pipes
        assert time.monotonic() - t0 < 10, "the precheck's end waited for what it started"
        left = int(pidfile.read_text())
        assert _gone(left, timeout=5), "what the precheck left ran on past its time"
    finally:
        if left:
            try:
                os.kill(left, 9)
            except OSError:
                pass


def _bare_agents(tmp_path):
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            accounts=SimpleNamespace(for_session=lambda kind, account: None),
                            _bind=lambda run_id, on: None)
    return agentry.Agents(space)


@pytest.mark.parametrize("platform, asks", [("darwin", True), ("linux", False)])
def test_a_run_s_claude_is_asked_to_lead_a_session_of_its_own_on_a_mac_alone(tmp_path, monkeypatch, platform, asks):
    """On a Mac a run's chat names the file its claude's wrapper writes which process it is to (processes.reported);
    elsewhere it names none, and its claude stays in the agent's session, as it was."""
    agents = _bare_agents(tmp_path)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    monkeypatch.setattr(sys, "platform", platform)
    env = agents.chat_options(r)["env"]
    assert env.get("PP_AGENT_SESSION") == (os.path.join(agents.run_dir(r), "claude-process") if asks else None)


def test_a_precheck_cut_short_by_any_other_error_ends_its_shell(tmp_path, monkeypatch):
    """Not its time limit but another error while it is read (as subprocess.run does): its shell is ended before the
    error goes on, never left to be waited for."""
    from proofpane_workspace import processes
    agents = _bare_agents(tmp_path)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "sleep 30"})
    r = agents.queue(AGENT)["run_id"]
    monkeypatch.setattr(agents, "work", lambda run_id, agent: str(tmp_path))
    started, real = [], processes.start

    def start(argv, run_id, **kw):
        # the precheck's own process alone is read with an error: ps, which a Mac's start runs, is read as it is
        p = real(argv, run_id, **kw)

        def broken(*a, **k):
            raise RuntimeError("read cut short")
        p.communicate = broken
        started.append(p)
        return p
    monkeypatch.setattr(processes, "start", start)
    t0 = time.monotonic()
    try:
        with pytest.raises(RuntimeError):
            agents._precheck(r, agents.agent(AGENT))
        assert time.monotonic() - t0 < 10, "the precheck's shell was waited for, not ended"
        assert started and started[0].poll() is not None
    finally:
        for p in started:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_a_task_attempt_s_precheck_starts_as_it_did_and_owns_no_session(tmp_path, monkeypatch):
    """A task's attempt keeps its own way of ending what it started (task_runs.py): the agents'
    precheck it runs starts in the agent's own session, on a Mac too, and the attempt owns no session by it."""
    from proofpane_workspace import processes
    agents = _bare_agents(tmp_path)
    pidfile = tmp_path / "pid"
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo",
                "precheck": f"sleep 60 >/dev/null 2>&1 & echo $! > '{pidfile}'"})
    r = agents.queue(AGENT)["run_id"]
    agents.task_runs.task_of[r] = "t1t2t3t4t5t6"
    monkeypatch.setattr(agents, "work", lambda run_id, agent: str(tmp_path))
    left = None
    try:
        assert agents._precheck(r, agents.agent(AGENT)) == (0, "")
        left = int(pidfile.read_text())
        assert os.getsid(left) == os.getsid(0) and r not in processes._SESSIONS
    finally:
        if left:
            try:
                os.kill(left, 9)
            except OSError:
                pass


@pytest.mark.parametrize("stopped_first", [False, True])
def test_a_retirement_waits_for_a_stopped_start_s_watch_before_its_own_writes(tmp_path, monkeypatch, stopped_first):
    """Its precheck slow to end once told: the retirement's own writes (the agent's record) wait until that start has
    looked at its watched places, which would otherwise read them as the precheck's change."""
    import asyncio
    import threading
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            accounts=SimpleNamespace(for_session=lambda kind, account: None),
                            _bind=lambda run_id, on: None)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "precheck": "true"})
    r = agents.queue(AGENT)["run_id"]
    agents._prepare = lambda run_id, agent: ("demo", "0" * 40)
    agents._baseline = lambda run_id, agent: None
    asked, told, seen = threading.Event(), threading.Event(), []
    monkeypatch.setattr(agentry.processes, "end", lambda run_id, wait=2.0: told.set() or 0)

    def precheck(run_id, agent):
        asked.set()
        told.wait(10)
        time.sleep(1)                                   # a second to end, once told
        return -15, ""
    agents._precheck = precheck
    agents._seen = lambda run_id: seen.append(agents.agent(AGENT)["retired"]) or ([], [])

    async def go():
        agents._pump_lock = asyncio.Lock()
        agents.started = True
        agents.pump()
        while not asked.is_set():
            await asyncio.sleep(0.01)
        if stopped_first:
            await agents.stop(r)                        # stopped by the owner first: not the retirement's own stop
        await agents.retire(AGENT)
    asyncio.run(go())
    assert seen == [False]
    assert agents.read(r)["run"]["reason"] == ("stopped by you" if stopped_first else "the agent was retired")


def test_a_critical_anomaly_found_after_its_run_ended_asks_nothing(tmp_path):
    """Its turn's end measured after the owner stopped it: a critical rule found then neither asks the owner nor
    interrupts; the run stays as they left it."""
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo", "anomaly_pause": True})
    r = agents.queue(AGENT)["run_id"]
    found = [{"rule": "tool_repetition", "level": "critical", "evidence": "npm test 8 times"}]
    agents._change(r, lambda rec: rec["run"].update(state="stopped", reason="stopped by you"))
    agents._anomalies(r, found)
    stopped = agents.read(r)["run"]
    assert (stopped.get("question"), stopped.get("questions") or 0, len(stopped["anomalies"])) == (None, 0, 1)
    agents._change(r, lambda rec: rec["run"].update(state="working"))
    agents.ending.add(r)                                # working, its end under way: nothing asked either
    agents._anomalies(r, found)
    assert (agents.read(r)["run"].get("question"), agents.read(r)["run"].get("paused_for")) == (None, None)
    agents.ending.discard(r)                            # while it works: it asks
    agents._anomalies(r, found)
    assert agents.read(r)["run"]["question"].startswith("The run met the rule tool_repetition")


def test_a_wait_for_the_owner_leaves_a_run_being_ended_or_ended_as_its_end_writes_it(tmp_path):
    """The owner's stop came while the run's claude was stopped to wait for them: the wait's own end does not make it
    waiting, neither while the run's end is under way nor after it."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    agents._change(r, lambda rec: rec["run"].update(state="working", question="Which tag?"))
    agents.ending.add(r)                                # its end under way
    asyncio.run(agents._wait_for_owner(r))
    assert agents.read(r)["run"]["state"] == "working"
    agents.ending.discard(r)
    agents._change(r, lambda rec: rec["run"].update(state="stopped", reason="stopped by you"))
    asyncio.run(agents._wait_for_owner(r))
    assert agents.read(r)["run"]["state"] == "stopped"
    agents._change(r, lambda rec: rec["run"].update(state="working"))      # and a live run does wait
    asyncio.run(agents._wait_for_owner(r))
    assert agents.read(r)["run"]["state"] == "waiting"


def test_a_failed_acceptance_check_of_a_run_stopped_meanwhile_asks_nothing(tmp_path):
    """Its last words read (a thread) while the owner stopped it: a failed check then neither asks the owner (hitl)
    nor retries; the run stays as its end leaves it, while that end is under way or after."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo",
                "acceptance": {"required_keys": ["labels"], "on_fail": "hitl"}})
    r = agents.queue(AGENT)["run_id"]
    agents._last_words = lambda run_id: "no labels here"
    for state, ending in (("stopped", False), ("working", True)):
        agents._change(r, lambda rec: rec["run"].update(state=state, question=None, questions=0))
        if ending:
            agents.ending.add(r)
        asyncio.run(agents._accept(r))
        agents.ending.discard(r)
        got = agents.read(r)["run"]
        assert (got["state"], got.get("question"), got["acceptance"]["pass"]) == (state, None, False), state
    asyncio.run(agents._accept(r))                      # live and not ending: it asks
    assert agents.read(r)["run"]["question"].startswith("Its final message failed its acceptance check")


def test_a_run_queued_before_its_brief_was_edited_records_the_brief_it_started_with(ws):
    put(ws, "wait")
    first = run(ws)["run_id"]
    until(ws, first, ("working",))
    queued = run(ws)["run_id"]                          # behind it: one run at a time per agent
    assert view(ws, queued)["brief_version"] == 1
    put(ws, "hello", brief_version=2)                   # the owner's edit, as the app mirrors it
    ws.http.post(f"/agents/runs/{first}/stop")
    done = until(ws, queued, ("done",))
    assert done["brief_version"] == 2 and log(ws, queued)["events"][0]["text"].startswith("hello")


def test_a_run_whose_end_is_under_way_is_sent_nothing_its_first_message_included(tmp_path):
    """Its record says live until its end's last write: a message then, the workspace's own as much as the owner's,
    would start a claude that end has already looked past."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    sent = []

    async def chat_send(run_id, text, sender="owner"):
        agents.may_send(run_id)                         # as the workspace's chat_send asks first
        sent.append(run_id)
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={}, chat_send=chat_send)
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    agents._change(r, lambda rec: rec["run"].update(state="working"))
    agents.ending.add(r)
    with pytest.raises(agentry.AgentError, match="ending"):
        agents.may_send(r)
    asyncio.run(agents._send_first(r, agents.agent(AGENT)))
    assert sent == []
    agents.ending.discard(r)
    agents.may_send(r)                                  # live, and no end under way: sent to
    asyncio.run(agents._send_first(r, agents.agent(AGENT)))
    assert sent == [r]


def test_a_delivered_wait_whose_caller_cannot_be_changed_gives_its_place_in_the_cap_back(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    caller = agents.queue(AGENT)["run_id"]
    agents.working.add(caller)                          # as the pump counts a waiter whose answer it delivers

    def cannot(run_id, change):
        raise OSError("the disk said no")
    agents._change = cannot
    asyncio.run(agents._deliver_wait(caller, {"child": "e1e2e3e4e5e6", "waited": 0.0, "since": None}))
    assert caller not in agents.working


def _in_process(tmp_path, chats=None):
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats=chats if chats is not None else {})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    agents._change(r, lambda rec: rec["run"].update(state="working", question="Which tag?"))
    return agents, r


def test_an_owner_s_message_answers_an_open_question_whether_the_run_waits_on_it_yet_or_not(tmp_path):
    """The question is open from the moment it is asked: a message the owner sends while its turn's end is still read
    (the run working, not yet waiting) answers it, as one sent once it waits does."""
    agents, r = _in_process(tmp_path)
    assert agents.may_send(r) is True
    agents.sent(r, "owner", answering=True)
    run = agents.read(r)["run"]
    assert (run.get("question"), run.get("paused_for"), run["answered"]) == (None, None, 1)
    assert agents.may_send(r) is False                  # answered: the next message is a turn of its own


def test_a_turn_s_end_leaves_its_decision_to_a_turn_begun_meanwhile(tmp_path):
    """An answer sent while the turn's end was read began the next turn: that end neither makes the run wait on the
    question nor ends it; the next turn's end decides."""
    import asyncio
    from types import SimpleNamespace
    chat = SimpleNamespace(state="running", last_words=None, cost=0.0, client=object())
    chats = {}
    agents, r = _in_process(tmp_path, chats)
    chats[r] = chat
    agents._measure = lambda run_id, c: None
    decided = []

    async def decide(run_id):
        decided.append(run_id)
    agents._wait_for_owner = agents._accept = decide
    asyncio.run(agents._turn_ended(r, {"turns": 1}, steps=1))
    assert decided == []
    chat.state = "idle"                                 # no turn begun: it waits on its question
    asyncio.run(agents._turn_ended(r, {"turns": 1}, steps=1))
    assert decided == [r]


def test_a_wait_for_the_owner_finds_its_question_answered_first_and_leaves_the_run_as_its_answer_left_it(tmp_path):
    import asyncio
    from types import SimpleNamespace
    chat = SimpleNamespace(state="running", client=object())
    chats = {}
    agents, r = _in_process(tmp_path, chats)
    chats[r] = chat
    asyncio.run(agents._wait_for_owner(r))              # its turn begun: an answer is under way
    assert agents.read(r)["run"]["state"] == "working" and chat.client is not None
    chat.state = "idle"
    agents.sent(r, "owner", answering=True)             # answered, its turn not begun yet
    asyncio.run(agents._wait_for_owner(r))
    assert agents.read(r)["run"]["state"] == "working" and chat.client is not None


@pytest.mark.parametrize("on", ["owner", "run"])
def test_a_message_to_a_run_waits_while_its_claude_is_stopped_to_wait(tmp_path, on):
    """Sent while the run's claude is being stopped to wait (for the owner, or on a run it started), a message waits
    for the stop, as the workspace's chat_send takes the run's send lock, and is then what starts it again: before,
    it went with the claude being stopped."""
    import asyncio
    from types import SimpleNamespace
    chat = SimpleNamespace(state="idle", client=object())

    async def slow_stop():
        await asyncio.sleep(0.3)
        chat.client, chat.state = None, "stopped"
    chat.stop = slow_stop
    chats = {}
    agents, r = _in_process(tmp_path, chats)
    chats[r] = chat
    if on == "run":
        agents._change(r, lambda rec: rec["run"].update(question=None))
        agents._run_waits[r] = {"child": "e1e2e3e4e5e6", "due": False, "stopped": False, "since": None,
                                "waited": 0.0, "task": None}
    seen = []

    async def go():
        stopping = asyncio.create_task(agents._wait_for_owner(r) if on == "owner" else agents._wait_for_run(r))
        await asyncio.sleep(0.05)                       # its claude's stop under way
        async with agents.send_lock(r):
            seen.append((agents.read(r)["run"]["state"], chat.client is None))
        await stopping
    asyncio.run(go())
    assert seen == [("waiting" if on == "owner" else "working", True)]
    if on == "run":
        assert agents._run_waits[r]["stopped"] is True


def test_the_workspace_sends_a_message_to_a_run_under_the_run_s_send_lock():
    """The workspace's chat_send holds the run's send lock while it sends, which a stop to wait takes too; a plain
    chat's message takes none."""
    import asyncio
    from types import SimpleNamespace
    from proofpane_workspace.workspace import Workspace
    lock, held = asyncio.Lock(), []

    async def send(session_id, text, sender):
        held.append((session_id, lock.locked()))
    # the controller's answer for an attempt's chat of a requirement's manager, whose owner's message is refused:
    # neither of these is one
    fake = SimpleNamespace(agents=SimpleNamespace(is_run=lambda sid: sid == "r", send_lock=lambda sid: lock),
                           _chat_send=send, controller=SimpleNamespace(is_attempt=lambda sid: False))
    asyncio.run(Workspace.chat_send(fake, "r", "v1.4.2"))
    asyncio.run(Workspace.chat_send(fake, "s", "hello"))
    assert held == [("r", True), ("s", False)]


def _asking(tmp_path, state="working"):
    """An in-process agent with one run that asked the owner, and a stand-in for its chat."""
    from types import SimpleNamespace
    from proofpane_workspace import agents as agentry
    stops = []

    async def stop():
        await asyncio.sleep(0.2)                        # its claude taking a moment to stop
        chat.client = None
        stops.append(1)
    chat = SimpleNamespace(state="idle", client=object(), last_words=None, cost=0.0, sdk_session=None, stop=stop)
    space = SimpleNamespace(root=str(tmp_path), sessions_dir=str(tmp_path / "sessions"), chat_settings=lambda s: s,
                            chats={})
    agents = agentry.Agents(space)
    agents.put({"id": AGENT, "name": "Triage", "brief": "hello", "repo": "demo"})
    r = agents.queue(AGENT)["run_id"]
    agents._change(r, lambda rec: rec["run"].update(state=state, question="Which tag?"))
    space.chats[r] = chat
    return agents, r, chat, stops


def test_an_owner_s_message_before_the_run_waits_is_its_answer(tmp_path):
    """The turn that asked is still being read (the run not waiting yet) when the owner answers: it is the answer,
    and the question closes."""
    agents, r, _, _ = _asking(tmp_path)
    assert agents.may_send(r) is True
    agents.sent(r, "owner", answering=True)
    run = agents.read(r)["run"]
    assert (run.get("question"), run.get("paused_for"), run["answered"]) == (None, None, 1)


def test_a_turn_s_end_read_after_an_answer_began_the_next_turn_leaves_that_turn_alone(tmp_path):
    agents, r, chat, stops = _asking(tmp_path)
    chat.state = "running"                              # the owner's answer began its next turn meanwhile
    called = []
    agents._measure = lambda run_id, c: None

    async def noted(run_id):
        called.append(run_id)
    agents._wait_for_owner = agents._accept = noted
    asyncio.run(agents._turn_ended(r, {"turns": 1}))
    assert (called, stops, agents.read(r)["run"]["state"]) == ([], [], "working")


def test_a_wait_for_the_owner_answered_first_stops_nothing(tmp_path):
    agents, r, chat, stops = _asking(tmp_path)
    agents.sent(r, "owner", answering=True)             # answered before the wait took its claude's stop
    asyncio.run(agents._wait_for_owner(r))
    assert (stops, agents.read(r)["run"]["state"]) == ([], "working")


def test_a_message_waits_while_the_run_s_claude_is_stopped_to_wait_for_the_owner(tmp_path):
    """Sent while the run's claude was being stopped to wait, it went with that claude; now it waits for the stop,
    and finds the run waiting, which its answer then starts again."""
    agents, r, _, stops = _asking(tmp_path)

    async def go():
        waiting = asyncio.create_task(agents._wait_for_owner(r))
        await asyncio.sleep(0.05)                       # its claude being stopped
        async with agents.send_lock(r):                 # as the workspace's chat_send takes it
            state = agents.read(r)["run"]["state"]
        await waiting
        return state
    assert asyncio.run(go()) == "waiting" and stops == [1]


def test_a_message_waits_while_the_run_s_claude_is_stopped_to_wait_on_its_run(tmp_path):
    agents, r, _, stops = _asking(tmp_path)
    wait = {"child": "e1e2e3e4e5e6", "due": False, "stopped": False, "since": None, "waited": 0.0}
    agents._run_waits[r] = wait

    async def go():
        waiting = asyncio.create_task(agents._wait_for_run(r))
        await asyncio.sleep(0.05)                       # its claude being stopped
        async with agents.send_lock(r):
            stopped = wait["stopped"]
        await waiting
        return stopped
    assert asyncio.run(go()) is True and stops == [1]


def test_an_agent_s_storage_cap_removes_its_oldest_run_folders_first(ws):
    put(ws, "run head -c 1500000 /dev/zero > \"$PP_SESSION_DIR/big\"", limits={"storage_mb": 1})
    first = run(ws)["run_id"]
    _answer(ws, first)
    one = until(ws, first, ("done",))
    assert one["bytes"] >= 1500000
    put(ws, "hello", limits={"storage_mb": 1})
    until(ws, run(ws)["run_id"], ("done",))
    deadline = time.time() + 10
    while not view(ws, first)["pruned"]:
        assert time.time() < deadline
        time.sleep(0.05)
    folder = os.path.join(ws.root, "agents", AGENT, "runs", first)
    assert os.listdir(folder) == ["session.json"]


def test_an_agent_s_record_is_checked(ws):
    for body, why in [({"id": AGENT, "name": "", "brief": "x", "repo": "demo"}, "A name"),
                      ({"id": AGENT, "name": "x", "brief": "x" * 4001, "repo": "demo"}, "A brief"),
                      ({"id": AGENT, "name": "x", "brief": "x", "repo": "../etc"}, "repository"),
                      ({"id": AGENT, "name": "x", "brief": "x", "chat_settings": {"allow": ["rm -rf /"]}}, "Allow"),
                      ({"id": AGENT, "name": "x", "brief": "x", "limits": {"max_steps": 0}}, "max_steps"),
                      ({"id": OTHER, "name": "x", "brief": "x"}, "the address")]:
        r = ws.http.put(f"/agents/{AGENT}", json=body)
        assert r.status_code == 400 and why in r.json()["detail"], (body, r.text)
    assert ws.http.get(f"/agents/{AGENT}").status_code == 404
    assert ws.http.post(f"/agents/runs/{'f' * 12}/stop").status_code == 404


def test_a_change_made_between_runs_is_not_the_next_run_s(ws):
    put(ws, "hello")
    until(ws, run(ws)["run_id"], ("done",))
    work = os.path.join(ws.root, "agents", AGENT, "work")
    with open(os.path.join(work, "owner.txt"), "w") as fh:            # the owner's edit from the Files tab, say
        fh.write("mine\n")
    second = run(ws)["run_id"]
    until(ws, second, ("done",))
    got = ws.http.post(f"/sessions/{second}/finish", json={"author_name": "Ana", "author_email": "a@example.org"}).json()
    assert got["files"] == 0 and got["diff"] == ""
    assert "Changes made between runs" in git("log", "--format=%s", cwd=work)


def test_a_run_takes_the_app_s_id_and_the_same_one_sent_again_is_the_same_run(ws):
    put(ws, "hello")
    r = ws.http.post(f"/agents/{AGENT}/runs", json={"run_id": "0123456789ab", "trigger": {"kind": "manual"}})
    assert r.status_code == 200 and r.json()["run_id"] == "0123456789ab"
    again = ws.http.post(f"/agents/{AGENT}/runs", json={"run_id": "0123456789ab", "trigger": {"kind": "manual"}})
    assert again.status_code == 200 and again.json()["run_id"] == "0123456789ab"
    until(ws, "0123456789ab", ("done",))
    assert len(ws.http.get("/agents/runs?since=0").json()["runs"]) == 1
    put(ws, "hello", agent_id=OTHER)
    taken = ws.http.post(f"/agents/{OTHER}/runs", json={"run_id": "0123456789ab"})
    assert taken.status_code == 400 and "another run" in taken.json()["detail"]
    for bad in ("../../x", "ABCDEF012345", 12):
        assert ws.http.post(f"/agents/{AGENT}/runs", json={"run_id": bad}).status_code == 400


def test_a_run_whose_claude_is_slow_to_start_holds_no_other_run_s_start(tmp_path):
    """Its first message goes out in a task of its own, never under the pump's lock: one claude's start (seconds for a
    real one, a minute when one dies as it connects) holds no other run's."""
    w = Workspace(tmp_path, config={"INIT_DELAY": 6})
    try:
        put(w, "hello")
        put(w, "hello", agent_id=OTHER, name="Labeler")
        slow = run(w)["run_id"]
        until(w, slow, ("working",))
        other = run(w, OTHER)["run_id"]
        t0 = time.time()
        while view(w, other)["state"] == "queued":
            assert time.time() - t0 < 3, "the other run waited behind the first run's claude starting"
            time.sleep(0.05)
        for r in (slow, other):
            until(w, r, ("done",), timeout=60)
    finally:
        w.stop()
