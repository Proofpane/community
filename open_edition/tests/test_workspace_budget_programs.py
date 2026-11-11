"""The budget check, the workspace's side, for every program a session runs (the ADE asks Proofpane's budget check
before a session starts). While the workspace is paired, a session of every program that runs an agent asks the check at
its first start and at each start again: claude and codex, and gemini, opencode, grok, qwen, kimi and mcode, and an
agent of the person's own; a shell and the programs' sign-ins, which spend nothing, ask nothing, and a chat asks at each
start of its claude (test_workspace_budget_check.py). Beside them: the note of a check Proofpane did not answer, said by
a claude terminal started again (its refusal is tested there, and again here on
the way); an ask's Start that the check refuses, which puts the ask back to be answered again; the owner's controller's
continue refused; and the notes its start and its continue say. Proofpane is the stand-in (proofpane_stand_in.py) and
every program a stand-in too (STAND_IN below, and the controller's tests' stand-in terminal): no real agent, no real
Proofpane, and nothing beyond this machine."""

import asyncio
import os
import sys
import time

import httpx
import pytest

from proofpane_stand_in import DEVICE, TOKEN, Proofpane, budget_answer, proofpane  # noqa: F401  (a fixture)
from proofpane_workspace import controller as ctl
from proofpane_workspace import workspace as wsmod
from test_controller_workspace import NOW, _limit, _passed, _status, _Term, actions, mirror, plan
from test_controller_workspace import ws  # noqa: F401  (a fixture)
from test_workspace_budget_check import CHECK, NOT_MADE, REFUSED, WAIT, Ws, checks, pair, quiet, worktrees
from test_workspace_budget_check import sdk  # noqa: F401  (a fixture)
from workspace_fixtures import Agent, make_repo

OTHERS = ("gemini", "opencode", "grok", "qwen", "kimi", "mcode")      # the terminal agents beside claude and codex
OWN = "own-agent"               # the program of an agent of the person's own, found on the workspace's PATH
CUSTOM = {"name": "Own agent", "command": f"{OWN} --fast", "task": "argument"}
# each program a test starts, by the name its stand-in writes its starts under (STAND_IN)
RUNS = dict({p: p for p in ("claude", "codex") + OTHERS}, custom=OWN)
ASKED = "Write the tests for the parser"
# every program a session here runs, as these tests run it: claude's version where the gate's floor asks it; otherwise
# each start written down in the home, under the program's name, by its session, and the token its session was given
# to ask with, if any, written in its session's folder; then running until stopped
STAND_IN = '''import os, sys, time
if sys.argv[1:2] in (["-v"], ["--version"]):
    print("2.1.274 (Claude Code)")
    sys.exit(0)
with open(os.path.join(os.environ["HOME"], "started-" + os.path.basename(sys.argv[0])), "a") as fh:
    fh.write(os.environ.get("PP_SESSION_ID", "") + "\\n")
token = os.environ.get("PP_ASK_TOKEN")
if token:
    path = os.path.join(os.environ["PP_SESSION_DIR"], "ask.token")
    with open(path + ".tmp", "w") as fh:
        fh.write(token)
    os.replace(path + ".tmp", path)
while True:
    time.sleep(0.1)
'''


class Space(Ws):
    """A workspace agent (workspace_fixtures.Agent) with a repository `demo`, each terminal agent's program and the
    person's own agent's a STAND_IN; what was started and what was made, as Ws reads them."""

    def __init__(self, tmp_path):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name in RUNS.values():
            (self.bin / name).write_text(f"#!{sys.executable}\n" + STAND_IN)
            (self.bin / name).chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.root = str(tmp_path / "ws")
        make_repo(os.path.join(self.root, "repos", "demo"))
        self.agent = Agent(self.root, str(self.home), {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        self.http = self.agent.http

    def start_session(self, program="claude", **more):
        body = {"repo": "demo", "program": program, "task": "Look at the open issues"}
        if program == "custom":
            body["custom"] = CUSTOM
        return self.http.post("/sessions", json=dict(body, **more))

    def stop_session(self, sid):
        assert self.http.post(f"/sessions/{sid}/stop").status_code == 200

    def start_again(self, sid):
        return self.http.post(f"/sessions/{sid}/start", json={})

    def ask_token(self, sid, timeout=15):
        """The token session `sid`'s program was given to ask with, once its program has written it."""
        path = os.path.join(self.root, "sessions", sid, "ask.token")
        deadline = time.time() + timeout
        while not os.path.exists(path):
            assert time.time() < deadline, f"session {sid}'s program wrote no token to ask with"
            time.sleep(0.05)
        with open(path) as fh:
            return fh.read()


@pytest.fixture
def space(tmp_path):
    made = Space(tmp_path)
    yield made
    made.stop()


def _within(coro, seconds=20):
    """`coro` run to its end on a loop of its own, failing the test after `seconds` rather than hanging it."""
    return asyncio.run(asyncio.wait_for(coro, seconds))


def _refusal(coro):
    """The words of the ControllerError `coro` ends in."""
    with pytest.raises(ctl.ControllerError) as e:
        _within(coro)
    return str(e.value)


def _controller_on(w):
    """The owner's side taken, the controller on (test_controller_workspace's `on`): its mirror."""
    m = mirror()
    _within(w.controller.take(m))
    return m


def _task_waiting(w, m):
    """A task the controller started (start_task's answer) whose session stopped at its account's limit and whose
    window has reset since: waiting, to be continued (as test_controller_workspace's _waiting and _passed make one)."""
    got = _within(w.controller.start_task({"requirement": plan(w, m), "repo": "demo",
                                           "task": "Edit README.md so its first line is hello."}))
    _status(w, got["session"], 100, NOW + 3600)
    _limit(w, got["session"], "rate_limit", at=int(time.time()) + 1)
    w.controller.refresh()
    t = w.controller.record["tasks"][got["task"]]
    assert t["state"] == "waiting", t
    _passed(w, (got, t))
    return got, t


# ---- every program that runs an agent: gemini, opencode, grok, qwen, kimi, mcode and the person's own ----------------

def test_the_programs_whose_starts_are_checked_are_every_program_but_a_shell_the_sign_ins_and_a_chat():
    # a chat asks at each start of its claude instead (Workspace._chat_start); a program added to PROGRAMS is placed in
    # BUDGET_CHECKED, or among the ones that spend nothing, or this fails
    assert sorted(wsmod.BUDGET_CHECKED) == sorted(set(wsmod.PROGRAMS) - {"shell", "chat"} - set(wsmod.SIGN_INS))


def test_a_spent_scope_refuses_gemini_opencode_grok_qwen_kimi_mcode_and_an_own_agent_in_the_product_s_words(space,
                                                                                                       proofpane):
    pair(space.home, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription",))
    programs = OTHERS + ("custom",)
    for program in programs:
        r = space.start_session(program)
        assert (r.status_code, r.json().get("detail")) == (400, REFUSED), (program, r.text)
    time.sleep(0.5)
    assert space.sessions() == [] and space.http.get("/sessions").json()["sessions"] == []      # no record
    assert len(worktrees(space.root)) == 1                                                       # no worktree
    assert space.started() == {}                                                                 # no program started
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}] * len(programs)                   # one check each


def test_a_spent_scope_refuses_a_gemini_terminal_and_an_own_agent_started_again_and_leaves_them_stopped(space,
                                                                                                    proofpane):
    pair(space.home, proofpane)
    made = {}
    for program in ("gemini", "custom"):
        r = space.start_session(program)
        assert r.status_code == 200, (program, r.text)
        made[program] = r.json()["id"]
        space.wait_started(RUNS[program], made[program])
        space.stop_session(made[program])
    Proofpane.budget = budget_answer(refused=("subscription",))
    for program, sid in made.items():
        r = space.start_again(sid)
        assert (r.status_code, r.json().get("detail")) == (400, REFUSED), (program, r.text)
    time.sleep(0.5)
    for program, sid in made.items():
        assert space.http.get(f"/sessions/{sid}").json()["running"] is False
        assert space.started()[RUNS[program]] == [sid]                       # started once, never again


def test_a_gemini_terminal_and_an_own_agent_whose_check_could_not_be_made_start_and_say_so_at_each_start(space,
                                                                                                        proofpane):
    pair(space.home, proofpane)
    Proofpane.budget = "down"
    note = NOT_MADE.format("Proofpane answered 503")
    programs = ("gemini", "custom")
    for program in programs:
        r = space.start_session(program)
        assert r.status_code == 200, (program, r.text)
        view = r.json()
        assert view["running"] is True and view.get("budget_notice") == note, (program, view)
        sid = view["id"]
        space.wait_started(RUNS[program], sid)
        space.stop_session(sid)
        r = space.start_again(sid)                          # started again: asked again, and said again
        assert r.status_code == 200 and r.json().get("budget_notice") == note, (program, r.text)
        space.wait_started(RUNS[program], sid, n=2)
    assert len(checks()) == 2 * len(programs)               # each one's first start and its start again


def test_control_unpaired_or_with_room_a_gemini_terminal_and_an_own_agent_start_and_say_nothing_of_the_check(
        space, proofpane):
    Proofpane.budget = budget_answer(refused=("subscription", "own_key", "gateway"))     # were it asked, all refused
    for paired in (False, True):
        if paired:
            pair(space.home, proofpane)
            Proofpane.budget = budget_answer(room=0.0)      # every kind allowed, its room spent to the cent: it goes on
        for program in ("gemini", "custom"):
            r = space.start_session(program)
            assert r.status_code == 200, (paired, program, r.text)
            view = r.json()
            assert view["running"] is True and "budget_notice" not in view, (paired, program, view)
            space.wait_started(RUNS[program], view["id"])
        if not paired:
            assert Proofpane.asked == []


def test_control_a_shell_and_the_programs_sign_ins_start_whatever_the_budget_says_and_ask_nothing(space, proofpane):
    pair(space.home, proofpane)
    Proofpane.budget = budget_answer(refused=("subscription", "own_key", "gateway"))     # were it asked, all refused
    made = {}
    for program in ("shell", "claude-sign-in", "codex-sign-in"):
        r = space.http.post("/sessions", json={"repo": None, "program": program, "task": "Sign in or look around"})
        assert r.status_code == 200, (program, r.text)
        view = r.json()
        assert view["running"] is True and "budget_notice" not in view, (program, view)
        made[program] = view["id"]
    space.stop_session(made["shell"])
    r = space.start_again(made["shell"])                    # a shell started again asks nothing either
    assert r.status_code == 200 and "budget_notice" not in r.json(), r.text
    assert checks() == []


# ---- a claude terminal started again (Workspace.claude_floor_for, the start route) -----------------------------------

def test_a_claude_terminal_started_again_is_refused_by_a_spent_scope_and_says_a_check_proofpane_did_not_answer(
        space, proofpane, monkeypatch):
    pair(space.home, proofpane)
    r = space.start_session("claude")
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    space.wait_started("claude", sid)
    space.stop_session(sid)
    # a spent scope: refused in the product's own words, its program not started
    Proofpane.budget = budget_answer(refused=("subscription",))
    r = space.start_again(sid)
    assert (r.status_code, r.json().get("detail")) == (400, REFUSED), r.text
    time.sleep(0.5)
    assert space.http.get(f"/sessions/{sid}").json()["running"] is False and space.started()["claude"] == [sid]
    # Proofpane not answering within the wait: the start goes on once the wait is over, and launch's view says why
    quiet()
    monkeypatch.setattr(Proofpane, "SLOW", WAIT + 3)
    Proofpane.slow = (CHECK,)
    r = space.start_again(sid)
    assert r.status_code == 200, r.text
    assert r.json().get("budget_notice") == NOT_MADE.format(f"no answer came in {WAIT} seconds"), r.json()
    space.wait_started("claude", sid, n=2)
    # said once, by the start whose check it was: started again with room, nothing is said
    space.stop_session(sid)
    quiet()
    r = space.start_again(sid)
    assert r.status_code == 200 and "budget_notice" not in r.json(), r.text
    space.wait_started("claude", sid, n=3)
    assert checks() == [{"device_id": DEVICE, "token": TOKEN}] * 4         # its first start, and each start again


# ---- an ask's Start (agent.py's ask_start) ---------------------------------------------------------------------------

def test_an_ask_s_start_refused_by_a_spent_scope_puts_the_ask_back_to_be_answered_again_and_makes_no_session(
        space, proofpane):
    assert space.http.post("/asks/settings", json={"on": True}).json()["on"] is True
    pair(space.home, proofpane)
    r = space.start_session("codex")
    assert r.status_code == 200, r.text
    by = r.json()["id"]
    with httpx.Client(base_url=space.agent.base, headers={"Authorization": "Bearer " + space.ask_token(by)},
                      timeout=30) as me:
        r = me.post("/ask/sessions", json={"program": "claude", "task": ASKED, "base": "main"})
    assert r.status_code == 200, r.text
    ask = r.json()["id"]
    Proofpane.budget = budget_answer(refused=("subscription",))
    r = space.http.post(f"/asks/{ask}/start", json={})
    assert (r.status_code, r.json().get("detail")) == (400, REFUSED), r.text
    # put back, as it was, to be answered again
    assert [(a["id"], a["program"], a["task"], a["by"]["id"]) for a in space.http.get("/asks").json()["asks"]] == \
        [(ask, "claude", ASKED, by)]
    # and nothing of a session made for it: no record, no worktree, no program started
    time.sleep(0.5)
    assert space.sessions() == [by] and [s["id"] for s in space.http.get("/sessions").json()["sessions"]] == [by]
    assert len(worktrees(space.root)) == 2                  # the repository's own checkout and the asking session's
    assert space.started().get("claude", []) == []
    # answered again once the scope has room: its session starts, and the ask is answered
    quiet()
    r = space.http.post(f"/asks/{ask}/start", json={})
    assert r.status_code == 200, r.text
    new = r.json()
    assert (new["program"], new["task"], new["asked_by"]) == ("claude", ASKED, by)
    assert space.http.get("/asks").json()["asks"] == []
    space.wait_started("claude", new["id"])
    assert len(checks()) == 3                               # the asking session's start, and each Start of the ask


# ---- the owner's controller: its continue (claude_floor_for) and its start (prepare) ---------------------------------
# In this process, as test_workspace_budget_check.py runs the controller: the Agent SDK's stand-in (`sdk`) in place of
# the real one, and the controller's tests' stand-in terminal (`ws`), so no claude is started.

def test_a_spent_scope_refuses_the_controller_s_continue_in_the_product_s_words_before_it_stops_or_starts_anything(
        ws, tmp_path, proofpane, sdk):
    pair(tmp_path / "wshome", proofpane)
    try:
        m = _controller_on(ws)
        got, t = _task_waiting(ws, m)
        terms = list(_Term.seen)
        Proofpane.budget = budget_answer(refused=("subscription",))
        assert _refusal(ws.controller.continue_task({"task": got["task"]})) == REFUSED
        # its program at the limit not stopped, nothing started: the task waits, to be continued
        assert _Term.seen == terms and terms[-1].running is True
        assert t["state"] == "waiting" and "continuing" not in t and actions(ws, "continue") == []
        # and is continued once the scope has room
        quiet()
        out = _within(ws.controller.continue_task({"task": got["task"]}))
        assert out["state"] == "working" and "budget_notice" not in out, out
        assert terms[-1].running is False and _Term.seen[-1].argv[-1] == ctl.FIXED_LINE
    finally:
        ws.usage_report.stop()


def test_the_controller_s_start_and_continue_whose_check_could_not_be_made_go_on_and_say_so_in_their_answers(
        ws, tmp_path, proofpane, sdk):
    pair(tmp_path / "wshome", proofpane)
    note = NOT_MADE.format("Proofpane answered 503")
    try:
        m = _controller_on(ws)
        Proofpane.budget = "down"
        got, t = _task_waiting(ws, m)
        assert got.get("budget_notice") == note, got                  # start_task's answer
        assert [term.running for term in _Term.seen] == [True]        # its terminal started
        out = _within(ws.controller.continue_task({"task": got["task"]}))
        assert out.get("budget_notice") == note, out                  # continue_task's
        assert out["state"] == "working" and _Term.seen[-1].argv[-1] == ctl.FIXED_LINE
    finally:
        ws.usage_report.stop()
