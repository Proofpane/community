"""The live progress board (ade/progress_board.py, GET /api/ade/board): the person's durable tasks in every state,
each in its phase, the workspace's sessions that no row holds, and the rows another module adds, every row in the
board's shape (board.FIELDS); next numbered as one queue; rows newest change first
within each phase; a task's events in the board's own words, the newest fifty, never a payload's free text; and the
workspace's sessions read with a bounded wait, as the summary reads them."""

import json
import os
import secrets
import threading
import time

import pytest
from fastapi import HTTPException

from conftest import signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, progress_board as board, task_offers, tasks, workspace_client, workspace_routes
# a task driven through tasks.py's own code and the app's routes, against test_agent_tasks.py's fake workspace: its
# fixtures (ws, owner) and helpers, so that the board is read off what tasks.py writes, not off rows written here
from test_agent_tasks import (ACC as TASKS_ACC, make_agent as tasks_agent, make_task as tasks_task, owner, own,  # noqa: F401
                              press, record, refuse, row as task_row, ws)
from workspace_fixtures import make_repo

B = "/api/ade/board"
HOUR = 3600
ACC = "a" * 12
MARK = "MARK-of-words-no-board-may-carry"
HERE = os.path.dirname(os.path.abspath(__file__))


def _uid(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def _read(client, h):
    r = client.get(B, headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def _rows(got, kind=None):
    return {r["id"]: r for r in got["rows"] if kind is None or r["kind"] == kind}


def _settled(uid):
    """Until the read of `uid`'s sessions under way has ended (at most 10 seconds): a board answers after its own
    wait, which a read on a loaded machine can run past."""
    for _ in range(500):
        if board._reads[uid].done():
            return
        time.sleep(0.02)
    raise AssertionError("the read of the workspace's sessions did not end")


@pytest.fixture
def stopped(monkeypatch):
    """No workspace sessions: its route refuses as it does for a workspace that does not run; and no other module's
    rows."""
    def refuse(user):
        raise HTTPException(status_code=409, detail="The workspace is not running: start it first.")
    monkeypatch.setattr(workspace_routes, "sessions", refuse)
    monkeypatch.setattr(board, "_SOURCES", {})


@pytest.fixture
def sessions(monkeypatch):
    """The workspace's sessions as a test gives them, each read counted. The fake answers at once, so a board waits for
    it as long as a loaded machine could take (a test of the bound sets its own)."""
    monkeypatch.setattr(board, "SESSIONS_WAIT", 30.0)
    given = {"sessions": [], "reads": 0}

    def read(user):
        given["reads"] += 1
        return {"sessions": [dict(s) for s in given["sessions"]]}
    monkeypatch.setattr(workspace_routes, "sessions", read)
    monkeypatch.setattr(board, "_SOURCES", {})
    return given


@pytest.fixture
def local(monkeypatch, tmp_path):
    home = tmp_path / "home"     # no git identity of the machine's user in a test
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(board, "_SOURCES", {})
    monkeypatch.setattr(board, "SESSIONS_WAIT", 30.0)       # a real workspace on a loaded machine
    from proofpane_open.ade import workspaces
    yield workspaces
    workspaces.shutdown()


def make_task(uid, state, *, cause=None, waits_until=None, steps=3, done=0, doing=False, backend=None, created=None,
              ended=None, auto_resume=1, title="A task", deleted=None, agent=None, returned=0):
    """A task of `uid`'s straight into the store, as tasks.py leaves one in `state`, with its first journal event: its
    first `done` steps done, and with `doing` the next one under way; on `agent`, else on an agent of its own that
    works (an agent the store does not have holds its tasks back, as tasks.py's _firing_hold reads one)."""
    tid = secrets.token_hex(16)
    agent = agent or make_agent(uid)
    created = created if created is not None else int(time.time()) - 5 * HOUR
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_tasks (id, user_id, agent_id, title, goal, goal_digest, state, cause, "
                   "waits_until, backend, home_grp, cross_groups, auto_resume, created_at, updated_at, ended_at, "
                   "deleted_at, returned) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)",
                   (tid, uid, agent, title, "the goal", "0" * 64, state, cause, waits_until,
                    json.dumps(backend or {"kind": "account", "id": "default"}), "personal", auto_resume, created,
                    created, ended, deleted, returned))
        for n in range(1, steps + 1):
            db.execute("INSERT INTO ade_agent_task_steps (task_id, n, user_id, objective, state) VALUES (?,?,?,?,?)",
                       (tid, n, uid, f"step {n}", "done" if n <= done else "doing" if doing and n == done + 1
                        else "todo"))
        tasks.journal(db, uid, tid, "task.created", {"agent": "x" * 12, "steps": steps}, now=created)
    return tid


def make_run(uid, tid, attempt, *, backend=None, started=None, state="working", reason=None, queued=None, step=1,
             lease=False, sent=0, agent="x" * 12, loop=0):
    """A run of a task's attempt, `attempt` within its `step`; with `lease`, the attempt that holds the task's lease;
    `sent` once its agent sent it to the workspace; `loop` one of the improve loop's, which no day's limit counts."""
    run_id = secrets.token_hex(6)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, reason, queued_at, started_at, "
                   "updated_at, task_id, task_step, task_attempt, backend, sent, loop) VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, agent, uid, state, reason, queued, started, started or 0, tid, step, attempt,
                    json.dumps(backend or {"kind": "account", "id": "default"}), sent, loop))
        if lease:
            db.execute("UPDATE ade_agent_tasks SET lease_run = ? WHERE id = ?", (run_id, tid))
    return run_id


def make_agent(uid, retired=None, paused=0, paused_until=None, runs_a_day=None, at=None):
    """An agent of `uid`'s; `runs_a_day` its limit of runs a day (agents.limits_of's max_runs_per_day); made, and last
    changed, at `at`, else now (a trigger keeps an agent's change time moving on: store.py's ade_agents_changed)."""
    agent_id = secrets.token_hex(6)
    now = int(time.time()) if at is None else at
    limits = json.dumps({"max_runs_per_day": runs_a_day} if runs_a_day is not None else {})
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at, retired_at, paused, "
                   "paused_until, limits) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (agent_id, uid, "Worker", "Do it.", now, now, retired, paused, paused_until, limits))
    return agent_id


def unknown_effect(uid, tid, state="unknown"):
    """An effect of a task's command: `unknown`, or settled (done, not_done)."""
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_effects (id, user_id, task_id, call_id, state, created_at, updated_at) VALUES "
                   "(?,?,?,?,?,?,?)", (secrets.token_hex(8), uid, tid, secrets.token_hex(4), state, now, now))


def journal(uid, tid, kind, payload, at, run_id=None):
    with store.connect() as db:
        tasks.journal(db, uid, tid, kind, payload, run_id=run_id, now=at)


# ---- the route ------------------------------------------------------------------------------------------------------

def test_the_board_needs_the_account_s_sign_in(client):
    assert client.get(B).status_code == 401
    assert client.get(B, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_a_board_with_nothing_on_it_is_its_time_and_no_rows(client, stopped):
    h = signed_up(client)
    before = int(time.time())
    got = _read(client, h)
    assert set(got) == {"at", "rows"} and got["rows"] == []
    assert before <= got["at"] <= int(time.time())


def test_another_account_s_tasks_and_a_deleted_task_are_never_on_the_board(client, stopped):
    mine = signed_up(client)
    other = signed_up(client, "other@example.com")
    kept = make_task(_uid(client, mine), "running")
    make_task(_uid(client, mine), "running", deleted=int(time.time()))
    make_task(_uid(client, other), "running")
    assert list(_rows(_read(client, mine))) == [kept]


# ---- tasks, each state in its phase -----------------------------------------------------------------------------------

def test_a_task_in_every_state_is_a_row_in_section_5_s_shape_and_its_phase(client, stopped, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", raising=False)      # on, as a deployment leaves it
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    soon, gone = now + HOUR, now - HOUR
    made = {
        "ready": make_task(uid, "ready"),
        "queued": make_task(uid, "running"),
        "running": make_task(uid, "running", done=1),
        "step_done": make_task(uid, "suspended", cause="step_done", done=2),
        "no_step": make_task(uid, "suspended", cause="no_step"),
        "failed": make_task(uid, "suspended", cause="failed"),
        "stopped_attempt": make_task(uid, "suspended", cause="stopped"),
        "reset_ahead": make_task(uid, "suspended", cause="window", waits_until=soon),
        "reset_passed_resumes": make_task(uid, "suspended", cause="window", waits_until=gone),
        "reset_passed_by_hand": make_task(uid, "suspended", cause="window", waits_until=gone, auto_resume=0),
        "held_by_reserve": make_task(uid, "suspended", cause="window"),
        "needs_owner": make_task(uid, "needs_owner", cause="effect"),
        "offering": make_task(uid, "offering"),
        "handed_over": make_task(uid, "handed_over"),
        "returning": make_task(uid, "returning"),
        "done": make_task(uid, "done", done=3, ended=now - 60),
        "stopped": make_task(uid, "stopped", ended=now - 120),
    }
    make_run(uid, made["queued"], 1, state="queued", queued=now - 30, lease=True)        # not started yet
    make_run(uid, made["running"], 1, queued=now - 90, started=now - 60, lease=True)     # under way
    rows = _rows(_read(client, h))
    assert len(rows) == len(made)
    for r in rows.values():
        assert tuple(r) == board.FIELDS                                  # the board's fields, in their order
        assert r["kind"] == "task" and r["parent"] is None and r["session"] is None and r["title"] == "A task"
        assert r["account"] == {"kind": "claude", "id": "default"}
    phase = {name: rows[tid]["phase"] for name, tid in made.items()}
    # now: what runs, here or with a teammate, or waits on its owner (a task made and not started waits for Start; a
    # known time that only its owner's Continue goes on with); next: what waits to start or to go on without its
    # owner, in its order
    assert phase == {"ready": "now", "queued": "next", "running": "now", "step_done": "now", "no_step": "now",
                     "failed": "now", "stopped_attempt": "now", "reset_ahead": "next", "reset_passed_resumes": "next",
                     "reset_passed_by_hand": "now", "held_by_reserve": "now", "needs_owner": "now", "offering": "next",
                     "handed_over": "now", "returning": "now", "done": "history", "stopped": "history"}
    for name, tid in made.items():
        r = rows[tid]
        assert r["state"] == (name if name in tasks.STATES else "running" if name == "queued" else "suspended")
        # the known time a task suspended for its window waits for: in next, when it goes on; in now, from when the
        # owner's Continue is what it waits for (its own Auto-resume off); an end only in history
        assert r["waits_until"] == ({"reset_ahead": soon, "reset_passed_resumes": gone,
                                     "reset_passed_by_hand": gone}.get(name))
        assert r["ended_at"] == ({"done": now - 60, "stopped": now - 120}.get(name))
        assert (r["order"] is not None) == (r["phase"] == "next")
    assert rows[made["running"]]["progress"] == {"done": 1, "of": 3}
    assert rows[made["step_done"]]["progress"] == {"done": 2, "of": 3}
    assert rows[made["done"]]["progress"] == {"done": 3, "of": 3}
    assert {rows[t]["state"] for t in made.values()} == set(tasks.STATES)


def test_the_deployment_s_auto_resume_off_leaves_a_reset_with_its_owner_before_and_after_it(client, stopped,
                                                                                           monkeypatch):
    """Nothing goes on with a task at its reset while the deployment's Auto-resume is off, whatever the task's own
    switch says (tasks.py's _auto_resume): the owner continues it then, so it is in now, with the time from which his
    Continue is what it waits for, its known time."""
    monkeypatch.setenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", "off")
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    passed = make_task(uid, "suspended", cause="window", waits_until=now - HOUR)
    ahead = make_task(uid, "suspended", cause="window", waits_until=now + HOUR)
    rows = _rows(_read(client, h))
    assert (rows[passed]["phase"], rows[passed]["waits_until"], rows[passed]["order"]) == ("now", now - HOUR, None)
    assert (rows[ahead]["phase"], rows[ahead]["waits_until"], rows[ahead]["order"]) == ("now", now + HOUR, None)


def test_a_reset_is_next_only_while_the_app_goes_on_with_the_task_by_itself(client, stopped, monkeypatch):
    """Next holds a task waiting for its known time only while tasks.py's _auto_resume would take it: both Auto-resume
    switches on, and nothing holding it back (its agent retired or paused with no end, the task marked returned, an
    effect of it unknown). Held back, it waits on its owner, in now, with no time: Continue is refused while what holds
    it holds, for good where its agent is retired or the task marked returned. Its own Auto-resume off alone leaves it
    in now with the time from which the owner's Continue is what it waits for, its reset, or its agent's pause's end
    where that is later (Continue is refused before it), but not its agent's runs for today spent, which hold only what
    the app continues by itself (_firing_hold), never the owner's press. A pause that ends at a time only makes it
    later: still next, its time the pause's end where that is later. Before the reset as after it."""
    monkeypatch.delenv("OPEN_EDITION_ADE_TASK_AUTO_RESUME", raising=False)
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    working, retired = make_agent(uid), make_agent(uid, retired=now - 10)
    paused, paused_a_while = make_agent(uid, paused=1), make_agent(uid, paused=1, paused_until=now + 2 * HOUR)
    spent = make_agent(uid, runs_a_day=1)
    make_run(uid, make_task(uid, "stopped", agent=spent), 1, state="stopped", queued=now, agent=spent)  # today's one
    made, expected = {}, {}
    for until in (now + HOUR, now - HOUR):
        after_pause = max(until, now + 2 * HOUR)
        for case, agent, fields, phase, waits in (
                ("goes on", working, {}, "next", until),
                ("its switch off", working, {"auto_resume": 0}, "now", until),
                ("its switch off, paused a while", paused_a_while, {"auto_resume": 0}, "now", after_pause),
                ("its switch off, its agent's day spent", spent, {"auto_resume": 0}, "now", until),
                ("agent retired", retired, {}, "now", None),
                ("agent paused", paused, {}, "now", None),
                ("goes on after its pause", paused_a_while, {}, "next", after_pause),
                ("returned", working, {"returned": 1}, "now", None),
                ("unknown effect", working, {}, "now", None),
                ("unknown effect, its switch off", working, {"auto_resume": 0}, "now", None),
                # an agent the store does not have holds it, as tasks.py's _firing_hold and _queue read one
                ("agent missing", "f" * 12, {}, "now", None)):
            made[(case, until)] = make_task(uid, "suspended", cause="window", waits_until=until, agent=agent, **fields)
            expected[(case, until)] = (phase, waits)
            if case.startswith("unknown effect"):
                unknown_effect(uid, made[(case, until)])
    rows = _rows(_read(client, h))
    for key, tid in made.items():
        assert (rows[tid]["phase"], rows[tid]["waits_until"]) == expected[key], key
    # the rule alone, as task_phase, held_back and goes_on_by_itself read a task's row
    task = {"state": "suspended", "cause": "window", "waits_until": now, "auto_resume": 1, "returned": 0}
    assert board.task_phase(task, by_itself=board.goes_on_by_itself(task, True, False, False)) == "next"
    assert not board.held_back(task, False, False)
    for change, held, unknown in (({}, True, False), ({}, False, True), ({"returned": 1}, False, False)):
        t = dict(task, **change)
        assert board.held_back(t, held, unknown)
        assert board.task_phase(t, by_itself=board.goes_on_by_itself(t, True, held, unknown)) == "now"
    t = dict(task, auto_resume=0)
    assert not board.held_back(t, False, False) and not board.goes_on_by_itself(t, True, False, False)
    assert board.task_phase(task, by_itself=board.goes_on_by_itself(task, False, False, False)) == "now"
    assert board.task_phase(dict(task, waits_until=None), by_itself=True) == "now"       # held for Continue
    assert board.task_phase(dict(task, cause="stopped"), by_itself=True) == "now"


def test_a_queued_attempt_waits_on_its_owner_where_its_agent_will_not_start_it(client, stopped):
    """An attempt queued and not sent waits, unsent, while its agent is paused (agents._send_now): with no end to the
    pause, which only its owner lifts, it is in now; paused until a time, in next, its time and its place the pause's
    end. One sent already the workspace starts in its turn unless its agent is paused by then, which the app tells it,
    and it stops one at once whose agent is retired (task_runs.start, and the workspace's retire); the task then waits
    for its owner, whose Continue is refused while its agent is paused, and for good once it is retired, End being its
    way out then (tasks._continue_after_end, _queue). So a sent attempt whose agent is paused, however long, or retired,
    is in now. An agent's runs for today spent hold no attempt queued: its agent sends it (agents._send_now counts none),
    and the workspace counts none against an attempt sent to it (only a run another run dispatches). An agent the
    store does not have sends nothing: now."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    working, paused, retired = make_agent(uid), make_agent(uid, paused=1), make_agent(uid, retired=now - 10)
    paused_a_while = make_agent(uid, paused=1, paused_until=now + HOUR)
    spent = make_agent(uid, runs_a_day=1)
    made = {}
    for case, agent, sent, queued in (
            ("works", working, 0, now - 60), ("works, sent", working, 1, now - 50), ("paused", paused, 0, now - 60),
            ("paused, sent", paused, 1, now - 60), ("paused a while", paused_a_while, 0, now - 70),
            ("paused a while, sent", paused_a_while, 1, now - 60), ("retired, sent", retired, 1, now - 60),
            ("its day spent", spent, 0, now - 40), ("its day spent, sent", spent, 1, now - 35),
            ("agent missing", "f" * 12, 0, now - 30)):
        made[case] = make_task(uid, "running", agent=agent)
        make_run(uid, made[case], 1, state="queued", queued=queued, lease=True, sent=sent, agent=agent)
    # due before the paused attempt goes on: before it in next, though the attempt was queued first
    sooner = make_task(uid, "suspended", cause="window", waits_until=now + 1800, agent=working)
    got_board = _read(client, h)
    rows = _rows(got_board)
    got = {case: (rows[tid]["phase"], rows[tid]["waits_until"]) for case, tid in made.items()}
    # the workspace counts no day's runs against an attempt it starts (proofpane_workspace's agents.py counts them for
    # its own dispatch alone): a sent one whose agent's runs for today are spent goes on as the rest
    assert got == {"works": ("next", None), "works, sent": ("next", None), "paused": ("now", None),
                   "paused, sent": ("now", None), "paused a while": ("next", now + HOUR),
                   "paused a while, sent": ("now", None), "retired, sent": ("now", None),
                   "its day spent": ("next", None), "its day spent, sent": ("next", None),
                   "agent missing": ("now", None)}
    assert rows[made["paused"]]["order"] is None
    queue = [r["id"] for r in sorted((r for r in got_board["rows"] if r["phase"] == "next"), key=lambda r: r["order"])]
    assert queue == [made["works"], made["works, sent"], made["its day spent"], made["its day spent, sent"], sooner,
                     made["paused a while"]]
    # a run not queued (working, or waiting for its owner's answer at the workspace) is under way: now
    for state in ("working", "waiting"):
        tid = make_task(uid, "running", agent=working)
        make_run(uid, tid, 1, state=state, queued=now - 60, started=now - 30, lease=True, agent=working)
        assert _rows(_read(client, h))[tid]["phase"] == "now", state


def test_a_queued_attempt_its_agent_holds_says_what_holds_it(client, stopped):
    """A task whose attempt is queued and waits on its owner because of its agent reads as running (its state), so the
    board says what holds it, a line of its own from the agent's record: retired, at its retirement (the workspace
    stops one sent, and Continue is refused for good; the app's own retire stops one not sent at once, so that case is
    the board's rule alone here: the next test takes the app's path); paused with no end and the attempt not sent, at
    the agent's last change (it is not sent while the pause lasts, nor at once after it: a resume starts no workspace);
    paused and the attempt sent, the same time (the workspace stops it rather than start it while the pause lasts).
    Nothing for an agent the store does not have, nor for an attempt that goes on (next)."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    # each agent last changed as its pause or its retirement left it
    working, paused = make_agent(uid), make_agent(uid, paused=1, at=now - 20)
    retired = make_agent(uid, retired=now - 10, at=now - 8)      # changed since: its retirement's time said
    paused_a_while = make_agent(uid, paused=1, paused_until=now + HOUR, at=now - 15)
    made = {}
    for case, agent, sent in (("works", working, 0), ("paused", paused, 0), ("paused, sent", paused, 1),
                              ("paused a while", paused_a_while, 0), ("paused a while, sent", paused_a_while, 1),
                              ("retired", retired, 0), ("retired, sent", retired, 1), ("agent missing", "f" * 12, 0)):
        made[case] = make_task(uid, "running", agent=agent, created=now - HOUR)
        make_run(uid, made[case], 1, state="queued", queued=now - 60, lease=True, sent=sent, agent=agent)
    rows = _rows(_read(client, h))
    sent_paused = board.HELD_PAUSED_SENT
    assert {case: [(e["at"], e["kind"], e["words"]) for e in rows[tid]["events"] if e["kind"].startswith("agent.")]
            for case, tid in made.items()} == {
        "works": [], "paused": [(now - 20, "agent.paused", board.HELD_PAUSED)],
        "paused, sent": [(now - 20, "agent.paused", sent_paused)], "paused a while": [],
        "paused a while, sent": [(now - 15, "agent.paused", sent_paused)],
        "retired": [(now - 10, "agent.retired", board.HELD_RETIRED)],
        "retired, sent": [(now - 10, "agent.retired", board.HELD_RETIRED)], "agent missing": []}
    assert board.HELD_PAUSED == "Its agent is paused: this attempt is not sent while the pause lasts"
    assert sent_paused == ("Its agent is paused: the workspace stops this attempt rather than start it while the pause "
                           "lasts, and the task then waits for you")
    assert board.HELD_RETIRED == "Its agent was retired: this attempt does not run, and End is the task's way out"
    # in time order among the journal's events, not after them all
    journal(uid, made["paused"], "attempt.queued", {"run": "r" * 12, "fence": 1, "backend": "account:default",
                                                    "chosen_by": "owner"}, now - 60)
    journal(uid, made["paused"], "task.switched", {"switch": "relay", "on": False, "hold": None}, now - 5)
    assert [(e["at"], e["kind"]) for e in _rows(_read(client, h))[made["paused"]]["events"]] == [
        (now - HOUR, "task.created"), (now - 60, "attempt.queued"), (now - 20, "agent.paused"),
        (now - 5, "task.switched")]


def test_a_task_whose_agent_was_retired_says_so_whatever_it_waits_in(client, stopped):
    """The app's own retire stops an attempt queued and not sent at once (agents.retire), so its task runs nothing
    until the scheduler's round settles that end and it waits; Start and Continue are refused for good after it
    (tasks._queue). A task in its owner's hands whose agent is retired, whatever it waits in, says so in a line of the
    board's own at its retirement, End being its way out; not one with a teammate, nor one ended."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    agent = make_agent(uid, at=now - 300)
    made = {"queued": make_task(uid, "running", agent=agent, created=now - HOUR)}
    run = make_run(uid, made["queued"], 1, state="queued", queued=now - 60, lease=True, sent=0, agent=agent)
    for state in ("ready", "suspended", "needs_owner", "offering", "handed_over", "done"):
        made[state] = make_task(uid, state, agent=agent, created=now - HOUR,
                                ended=now - 30 if state == "done" else None)
    agents.retire(auth.user(uid), agent)
    with store.connect() as db:
        retired_at = db.execute("SELECT retired_at FROM ade_agents WHERE id = ?", (agent,)).fetchone()[0]
        assert db.execute("SELECT state FROM ade_agent_runs WHERE run_id = ?", (run,)).fetchone()[0] == "stopped"
    rows = _rows(_read(client, h))
    line = [(retired_at, "agent.retired", "Its agent was retired: End is the task's way out")]
    assert {key: [(e["at"], e["kind"], e["words"]) for e in rows[tid]["events"] if e["kind"].startswith("agent.")]
            for key, tid in made.items()} == {"queued": line, "ready": line, "suspended": line, "needs_owner": line,
                                               "offering": [], "handed_over": [], "done": []}
    assert board.RETIRED == "Its agent was retired: End is the task's way out"


def test_the_board_s_own_lines_are_dated_no_later_than_the_board(client, stopped):
    """An agent's change time can run ahead of the clock (store.py's trigger ade_agents_changed puts it a second past
    the agent's last mirror): the board's line from it is dated at the board's own time, never after it."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    ahead = make_agent(uid, paused=1, at=now + 5)
    tid = make_task(uid, "running", agent=ahead, created=now - HOUR)
    make_run(uid, tid, 1, state="queued", queued=now - 60, lease=True, sent=0, agent=ahead)
    got = board.board({"id": uid}, now=now)
    assert got["at"] == now
    assert [e for e in _rows(got)[tid]["events"] if e["kind"] == "agent.paused"] == [
        {"at": now, "kind": "agent.paused", "words": board.HELD_PAUSED}]


def test_a_settled_effect_holds_nothing_back(client, stopped):
    """Only an effect not known holds a task back (tasks.py's unknown_effects): one settled either way does not."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    made = {state: make_task(uid, "suspended", cause="window", waits_until=now + HOUR) for state in
            ("done", "not_done", "unknown")}
    for state, tid in made.items():
        unknown_effect(uid, tid, state)
    rows = _rows(_read(client, h))
    assert {state: rows[tid]["phase"] for state, tid in made.items()} == \
        {"done": "next", "not_done": "next", "unknown": "now"}


def test_a_task_on_a_model_key_names_no_account_and_one_without_steps_no_progress(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    keyed = make_task(uid, "running", backend={"kind": "key", "provider": "anthropic"})
    other = make_task(uid, "running", backend={"kind": "account", "id": ACC}, steps=0)
    rows = _rows(_read(client, h))
    assert rows[keyed]["account"] is None
    assert rows[other]["account"] == {"kind": "claude", "id": ACC} and rows[other]["progress"] is None


def test_a_task_started_says_when_its_first_attempt_started(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "running", created=now - 5 * HOUR)    # the test's own time: make_task's may be a second later
    make_run(uid, tid, 2, started=now - 100)
    make_run(uid, tid, 1, started=now - 900)
    make_run(uid, tid, 3)                       # queued: not started yet
    waiting = make_task(uid, "ready")
    rows = _rows(_read(client, h))
    assert rows[tid]["started_at"] == now - 900 and rows[waiting]["started_at"] is None
    assert rows[tid]["created_at"] == now - 5 * HOUR


# ---- the order --------------------------------------------------------------------------------------------------------

def test_next_is_one_queue_in_the_order_things_go_on_then_a_source_s_order_then_the_oldest(client, stopped,
                                                                                       monkeypatch):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    young = make_task(uid, "offering", created=now - 100)
    old = make_task(uid, "offering", created=now - 9000)
    # its attempt queued, it goes as its agent sends it: first, whenever the task was made
    queued = make_task(uid, "running", created=now - 20)
    make_run(uid, queued, 1, state="queued", queued=now - 10, lease=True)
    late = make_task(uid, "suspended", cause="window", waits_until=now + 2 * HOUR)
    early = make_task(uid, "suspended", cause="window", waits_until=now + HOUR)
    given = [board.row("requirement", "req-two", "Second", phase="next", state="queued", order=2, created_at=now),
             board.row("requirement", "req-one", "First", phase="next", state="queued", order=1, created_at=now),
             board.row("requirement", "req-soonest", "Soonest", phase="next", state="waiting",
                       waits_until=now + 1800, created_at=now),
             # its making not known: last of the rest, not first
             board.row("requirement", "req-unknown", "Unknown", phase="next", state="queued"),
             board.row("requirement", "req-now", "Now", phase="now", state="running", order=1, created_at=now)]
    monkeypatch.setitem(board._SOURCES, "controller", lambda user, at: given)
    got = _read(client, h)
    queue = sorted((r for r in got["rows"] if r["phase"] == "next"), key=lambda r: r["order"])
    assert [r["id"] for r in queue] == [queued, "req-soonest", early, late, "req-one", "req-two", old, young,
                                        "req-unknown"]
    assert [r["order"] for r in queue] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert _rows(got)["req-now"]["order"] is None              # an order is a next row's alone


def test_next_is_in_the_order_each_goes_on_its_agent_s_pause_or_spent_day_making_it_later(client, stopped):
    """A task waiting for a known time goes on at that time, or later where its agent holds it then (tasks.py's
    _firing_hold): paused until a time, at the pause's end; its runs for today spent, at midnight UTC, the runs counted
    by when they were queued, the improve loop's and yesterday's not among them. Its time and its place in next are
    when it goes on; an attempt queued goes at its queueing, in the order queued (agents._send_now). The board is read
    at one time, `now`, so that the day cannot turn between the test's clock and the board's."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    day = now - now % tasks.DAY
    midnight = day + tasks.DAY
    working = make_agent(uid)
    paused = make_agent(uid, paused=1, paused_until=now + 5 * HOUR)
    spent = make_agent(uid, runs_a_day=1)
    paused_first = make_task(uid, "suspended", cause="window", waits_until=now + HOUR, agent=paused)
    second = make_task(uid, "suspended", cause="window", waits_until=now + 2 * HOUR, agent=working)
    passed_spent = make_task(uid, "suspended", cause="window", waits_until=now - HOUR, agent=spent)
    tomorrow_spent = make_task(uid, "suspended", cause="window", waits_until=midnight + 6 * HOUR, agent=spent)
    # its one run today, queued and stopped before it started: counted, as _firing_hold counts by the queueing
    make_run(uid, passed_spent, 1, state="stopped", queued=now, agent=spent)
    # a run of the improve loop's, or one queued yesterday and started today, spends none of today's runs; their resets
    # have passed, so a run counted wrongly would move them to midnight whatever the hour
    looping, yesterday = make_agent(uid, runs_a_day=1), make_agent(uid, runs_a_day=1)
    loop_only = make_task(uid, "suspended", cause="window", waits_until=now - 2 * HOUR, agent=looping)
    yesterday_only = make_task(uid, "suspended", cause="window", waits_until=now - 3 * HOUR, agent=yesterday)
    make_run(uid, loop_only, 1, state="done", queued=now, started=now, agent=looping, loop=1)
    make_run(uid, yesterday_only, 1, state="done", queued=day - 60, started=day + 1, agent=yesterday)
    queued_later, queued_sooner = make_task(uid, "running"), make_task(uid, "running")
    make_run(uid, queued_later, 1, state="queued", queued=now - 10, lease=True)
    make_run(uid, queued_sooner, 1, state="queued", queued=now - 20, lease=True)
    got = board.board({"id": uid}, now=now)
    rows = _rows(got)
    waits = {"paused_first": now + 5 * HOUR, "second": now + 2 * HOUR, "passed_spent": midnight,
             "tomorrow_spent": midnight + 6 * HOUR, "loop_only": now - 2 * HOUR, "yesterday_only": now - 3 * HOUR}
    tids = {"paused_first": paused_first, "second": second, "passed_spent": passed_spent,
            "tomorrow_spent": tomorrow_spent, "loop_only": loop_only, "yesterday_only": yesterday_only,
            "queued_sooner": queued_sooner, "queued_later": queued_later}
    assert {name: rows[tids[name]]["waits_until"] for name in waits} == waits
    assert all(rows[tid]["phase"] == "next" for tid in tids.values())
    goes = dict(waits, queued_sooner=now - 20, queued_later=now - 10)
    queue = [r["id"] for r in sorted((r for r in got["rows"] if r["phase"] == "next"), key=lambda r: r["order"])]
    assert queue == [tids[n] for n in sorted(goes, key=goes.get)]


def test_the_rows_are_history_then_now_then_next_each_newest_change_first(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    a = make_task(uid, "running", created=now - 5000)
    b = make_task(uid, "running", created=now - 4000)
    c = make_task(uid, "done", created=now - 3000, ended=now - 10)
    d = make_task(uid, "stopped", created=now - 2000, ended=now - 20)
    e = make_task(uid, "offering", created=now - 1000)
    journal(uid, a, "task.switched", {"switch": "relay", "on": False, "hold": None}, now - 5)   # a changed last
    got = _read(client, h)
    assert [r["id"] for r in got["rows"]] == [c, d, a, b, e]
    assert [r["phase"] for r in got["rows"]] == ["history", "history", "now", "now", "next"]


# ---- events -------------------------------------------------------------------------------------------------------------

def test_a_task_s_events_are_its_newest_fifty_oldest_first_the_rest_counted(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "running", created=now - 1000)
    for i in range(60):
        journal(uid, tid, "task.switched", {"switch": "relay", "on": i % 2 == 0, "hold": None}, now - 900 + i)
    r = _rows(_read(client, h))[tid]
    assert len(r["events"]) == board.EVENTS_MAX == 50 and r["more_events"] == 11     # 61 with task.created
    assert [e["at"] for e in r["events"]] == [now - 900 + i for i in range(10, 60)]    # the newest, oldest first
    assert r["events"][-1] == {"at": now - 841, "kind": "task.switched", "words": "Relay turned off"}
    assert r["events"][0]["words"] == "Relay turned on"


def test_an_event_names_its_attempt_and_its_account_as_the_owner_named_it(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_account_owners (id, user_id, account, name) VALUES (?,?,?,?)",
                   (secrets.token_hex(8), uid, ACC, "Work"))
    tid = make_task(uid, "running", created=now - 500)
    run1 = make_run(uid, tid, 1, backend={"kind": "account", "id": "default"}, started=now - 400)
    run2 = make_run(uid, tid, 1, backend={"kind": "account", "id": ACC}, started=now - 200, step=2)
    journal(uid, tid, "attempt.queued", {"run": run1, "fence": 1, "backend": {"kind": "account", "id": "default"},
                                         "packet_digest": "0" * 64, "chosen_by": "owner"}, now - 450, run1)
    journal(uid, tid, "attempt.started", {"run": run1, "fence": 1}, now - 400, run1)
    journal(uid, tid, "attempt.ended", {"run": run1, "fence": 1, "state": "failed", "cause": None,
                                        "reason": None}, now - 300, run1)
    journal(uid, tid, "task.continued", {"from": {"kind": "account", "id": "default"},
                                         "to": {"kind": "account", "id": ACC}, "by": "relay"}, now - 250)
    journal(uid, tid, "attempt.started", {"run": run2, "fence": 2}, now - 200, run2)
    journal(uid, tid, "task.continued", {"from": {"kind": "account", "id": ACC},
                                         "to": {"kind": "key", "provider": "anthropic"}, "by": "owner"}, now - 100)
    words = [e["words"] for e in _rows(_read(client, h))[tid]["events"]]
    # an attempt is counted within its step (tasks.py's _queue), so its words name the step too
    assert words == ["Made, with 3 steps", "Step 1, attempt 1 queued on Default, at your press",
                     "Step 1, attempt 1 started on Default", "Step 1, attempt 1 failed",
                     "The relay moved it from Default to Work", "Step 2, attempt 1 started on Work",
                     "Moved from Work to your Anthropic key, at your press"]


def test_every_kind_of_the_task_journal_has_words_of_its_own():
    for kind in tasks.JOURNAL_KINDS:
        words = board.task_words(kind, {}, None, {})
        assert words and words != kind, kind


def test_the_words_follow_the_payload_s_states_and_causes():
    w = board.task_words
    assert w("task.suspended", {"state": "needs_owner", "cause": "effect"}, None, {}) == \
        "Waits for you: a command's effect is not known"
    # a time: an account's reset, or midnight UTC for its agent's day of runs (the same entry for both); none: its
    # accounts out of room or held back by a weekly reserve, with Auto-resume off (HELD_BY_A_RESERVE)
    assert w("task.suspended", {"state": "suspended", "cause": "window", "until": 1760700000}, None, {}) == \
        board.WINDOW_UNTIL == ("Suspended until a known time: an account's reset, or midnight UTC once its agent's "
                               "runs for the day are spent")
    assert w("task.suspended", {"state": "suspended", "cause": "window", "until": None}, None, {}) == \
        board.WINDOW_HELD == ("Held until you press Continue: its accounts are out of room or held back by a weekly "
                              "reserve")
    # step_done is also a task back from a teammate with no step done (tasks.take_return): no step named done
    assert w("task.suspended", {"state": "suspended", "cause": "step_done"}, None, {}) == \
        "It waits for you before it goes on"
    assert w("task.suspended", {"state": "suspended", "cause": None}, None, {}) == "Waits for Continue"
    # also written where nothing was stopped: Continue refused (an agent retired or paused: tasks.py's
    # _held_continuation, _go_asked), a continuation refused, an offer asked for at an attempt's end: no stop named
    assert w("task.suspended", {"state": "suspended", "cause": "stopped"}, None, {}) == "It waits for you"
    assert w("task.suspended", {"state": "suspended", "cause": "no_step"}, None, {}) == \
        "Its attempt ended without finishing its step"
    assert w("task.suspended", {"state": "suspended", "cause": "failed"}, None, {}) == "Its attempt failed"
    assert w("checkpoint.ready", {"kind": "step", "step": 2, "n": 4}, None, {}) == "Step 2: done, its checkpoint saved"
    assert w("checkpoint.ready", {"kind": "progress", "step": 2, "n": 3}, None, {}) == "Step 2: progress saved"
    assert w("checkpoint.ready", {"kind": "zero", "step": 1, "n": 0}, None, {}) == "Its starting point recorded"
    assert w("effect.resolved", {"outcome": "not_happened", "by": "page"}, None, {}) == \
        "An unknown effect resolved: it did not happen (by you)"
    assert w("effect.resolved", {"outcome": "happened", "by": "workspace"}, None, {}) == \
        "An unknown effect resolved: it happened (by the workspace)"
    assert w("effect.resolved", {"outcome": "happened"}, None, {}) == "An unknown effect resolved: it happened"
    # task.resolved for an effect is written once, when its last unknown effect is settled, with that one's outcome
    for outcome in ("happened", "not_happened"):
        assert w("task.resolved", {"what": "effect", "outcome": outcome}, None, {}) == \
            "Every unknown effect of it settled"
    # task.returned is written when the way back starts (the task returning), not once the work is back: custody's own
    # return told apart by its `why`, compared and never shown; the teammate's Give back and the end of their side
    # alike, which its payload does not tell apart (task_offers._start_way_back)
    returned = {"offer": "o" * 16, "fence": 3}
    assert w("task.returned", dict(returned, why=task_offers.CUSTODY_RETURN), None, {}) == \
        "Custody started it back: one of your own accounts has room again"
    assert w("task.returned", dict(returned, why=None), None, {}) == "On its way back from your teammate"
    assert w("task.returned", dict(returned, why="a reason of another way back"), None, {}) == \
        "On its way back from your teammate"                    # custody's is told by its own words, not by any
    assert w("task.continued", {"from": {"kind": "account", "id": "default"},
                                "to": {"kind": "account", "id": "default"}, "by": "auto_resume"}, None, {}) == \
        "Auto-resume continued it on Default"
    assert w("task.continued", {"from": "account:default", "to": f"account:{ACC}", "by": "auto_resume"}, None,
             {ACC: "Work"}) == "Auto-resume moved it from Default to Work"
    assert w("task.continued", {"from": "account:default", "to": "account:default", "by": "owner"}, None, {}) == \
        "Continued on Default, at your press"
    assert w("task.continued", {"to": "account:default", "by": "something new"}, None, {}) == "Continued on Default"
    assert w("task.switched", {"switch": "cross_groups", "on": True}, None, {}) == "Cross organisations turned on"
    # turning Cross organisations or custody's crossing on is held first: the switch reads off until the hold ends
    assert w("task.switched", {"switch": "cross_groups", "on": True, "hold": "h" * 32}, None, {}) == \
        "Cross organisations asked on: its turning on is held"
    assert w("task.switched", {"switch": "custody_cross", "on": True, "hold": "h" * 32}, None, {}) == \
        "Custody's crossing asked on: its turning on is held"
    assert w("task.switched", {"switch": "cross_groups", "on": False, "hold": None}, None, {}) == \
        "Cross organisations turned off"
    assert w("task.crossed", {"from_grp": "personal", "to_grp": "org:abc", "home": None}, None, {}) == \
        "It crossed into an organisation's seats"
    assert w("task.crossed", {"from_grp": "org:abc", "to_grp": "personal", "home": None}, None, {}) == \
        "It crossed into personal plans"
    assert w("task.crossed", {"from_grp": "org:abc", "to_grp": "personal", "home": "room"}, None, {}) == \
        "Back to its home group's accounts"
    assert w("task.crossed", {"from_grp": "personal", "to_grp": "elsewhere", "home": None}, None, {}) == \
        "It crossed into another group's accounts"
    regrouped = {"account": "account:" + ACC, "from_grp": "org:abc", "from_kind": "team", "to_grp": "org:abc",
                 "to_kind": "team"}
    assert w("task.account_regrouped", dict(regrouped, to_grp="org:def"), None, {}) == \
        "Its account was confirmed into another group"
    assert w("task.account_regrouped", dict(regrouped, to_kind="enterprise"), None, {}) == \
        "Its account's seat was read as another kind"
    assert w("task.account_regrouped", regrouped, None, {}) == "Its organisation was read again"
    # confirmed from personal plans into a team's seats: another group, and another kind too; the group is said
    assert w("task.account_regrouped", dict(regrouped, from_grp="personal", from_kind="pro", to_kind="team"), None,
             {}) == "Its account was confirmed into another group"
    assert w("task.resolved", {"what": "worktree", "outcome": "discard"}, None, {}) == \
        "You dropped its worktree's change"
    assert w("task.done", {"by": "owner"}, None, {}) == "Ended as done, by you"
    assert w("attempt.started", {}, {"task_attempt": 3, "backend": json.dumps({"kind": "key", "provider": "kimi"})},
             {}) == "Attempt 3 started on your Kimi key"
    assert w("attempt.started", {}, None, {}) == "An attempt started on an account"
    assert w("attempt.ended", {"state": "done"}, {"task_attempt": 3, "task_step": 2, "backend": None}, {}) == \
        "Step 2, attempt 3 finished"
    assert w("task.created", {"steps": 1}, None, {}) == "Made, with 1 step"


def test_a_wait_said_before_the_events_shown_still_counts(client, stopped):
    """The board compares a task's record with its journal's newest wait read from the whole journal: a wait that fifty
    newer events have pushed out of those shown is still said, and no line restates it after them. Here an owner settles
    sixty unknown effects one by one, which leaves the task waiting for them (tasks.resolve journals each and touches
    the record, and journals task.resolved after the last)."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "needs_owner", cause="effect", created=now - HOUR)
    journal(uid, tid, "task.suspended", {"state": "needs_owner", "cause": "effect", "until": None}, now - 3000)
    for n in range(60):
        journal(uid, tid, "effect.resolved", {"effect": f"e{n}", "outcome": "happened", "by": "page"}, now - 2000 + n)
    journal(uid, tid, "task.resolved", {"what": "effect", "outcome": "happened"}, now - 1000)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET updated_at = ? WHERE id = ?", (now - 1000, tid))
    r = _rows(_read(client, h))[tid]
    assert (len(r["events"]), r["more_events"]) == (board.EVENTS_MAX, 13)
    assert "task.waits" not in [e["kind"] for e in r["events"]]
    assert r["events"][-1]["words"] == "Every unknown effect of it settled"


def test_a_task_that_waited_more_than_once_is_said_by_its_newest_wait(client, stopped):
    """The record is compared with the journal's newest wait, not an older one: a task that waited twice, each wait
    journaled as tasks.py's _suspend journals it, gets no line of the board's own."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "ready", created=now - HOUR)
    with store.connect() as db:
        tasks._suspend(db, tasks.get(db, uid, tid), "stopped", "Stopped.", now - 1800)
        tasks._suspend(db, tasks.get(db, uid, tid), "window", "Its accounts are out of room.", now - 600,
                       until=now + HOUR)
    r = _rows(_read(client, h))[tid]
    assert [e["kind"] for e in r["events"]] == ["task.created", "task.suspended", "task.suspended"]
    assert r["events"][-1]["words"] == board.WINDOW_UNTIL


def test_an_offer_s_end_says_what_the_task_waits_for_after_the_end_s_own_event(client, stopped):
    """task_offers._end_offer journals an offer's end and puts the owner's task back (_back) in the same second: the
    board's line saying what the task waits for comes after that event, the newest."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    said = {}
    for case, needs in (("failed, held for its owner", True), ("declined, never waited", False)):
        tid = make_task(uid, "offering", created=now - HOUR)
        journal(uid, tid, "task.offered", {"offer": "o" * 16, "fence": 1, "why": None}, now - 600)
        offer = case[0] * 32
        with store.connect() as db:
            db.execute("INSERT INTO ade_agent_task_offers (id, user_id, task_id, kind, state, fence, hold_id, "
                       "created_at, updated_at, expires_at) VALUES (?,?,?,'pressed','offered',1,?,?,?,?)",
                       (offer, uid, tid, case[0] * 32, now - 600, now - 600, now + HOUR))
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM ade_agent_task_offers WHERE id = ?", (offer,)).fetchone()
            task_offers._end_offer(db, row, "failed" if needs else "declined", "why", now, needs_owner=needs)
        said[case] = [(e["at"], e["kind"], e["words"]) for e in _rows(_read(client, h))[tid]["events"]][-2:]
    assert said == {
        "failed, held for its owner": [(now, "task.offer_failed", "Its offer failed"),
                                       (now, "task.waits", "Waits for you: a hand-off")],
        "declined, never waited": [(now, "task.offer_declined", "Your teammate declined it"),
                                   (now, "task.waits", "It waits for you")]}


def test_a_custody_hand_off_that_ended_waits_for_a_time_already_come(client, stopped):
    """A custody hand-off that ends untaken puts the owner's task back waiting for its window, at once where an account
    of its relay set is open or none has a reset known (task_offers._wait_again): a time no later than the change. The
    board's line says that time has come, not that it waits for a reset; and the record is compared with the journal's
    newest wait, a stop here, not with an older wait for its window."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "offering", created=now - 3 * HOUR)
    journal(uid, tid, "task.suspended", {"state": "suspended", "cause": "window", "until": now - HOUR}, now - 2 * HOUR)
    journal(uid, tid, "task.suspended", {"state": "suspended", "cause": "stopped", "until": None}, now - HOUR)
    journal(uid, tid, "task.offered", {"offer": "o" * 16, "fence": 1, "why": None}, now - 1800)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task_offers._back(db, {"hold_id": "h" * 32, "user_id": uid, "task_id": tid, "kind": "custody", "id": "o" * 16},
                          "Custody ended.", now)
    # beside it, a record waiting for its window at a time ahead of its change, with no wait journaled: a reset
    ahead = make_task(uid, "suspended", cause="window", waits_until=now + HOUR, created=now - HOUR)
    rows = _rows(_read(client, h))
    r = rows[tid]
    assert (r["state"], r["events"][-1]) == ("suspended", {"at": now, "kind": "task.waits", "words": board.WINDOW_DUE})
    assert board.WINDOW_DUE == "Suspended for its window, its time to go on already come"
    assert rows[ahead]["events"][-1] == {"at": now - HOUR, "kind": "task.waits", "words": board.WINDOW_UNTIL}


def test_a_binding_s_words_say_what_it_changed(client, stopped):
    """task.bound names the binding fields a custody hand-off or a way back changed, as task_offers._bind_as_custody
    returns them and its callers journal them: bound to an organisation, its crossings ended, or both, never more."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    said = {}
    for case, (source, target) in {"bound": (("org:abc", 0), (None, 0)), "ended": ((None, 1), (None, 0)),
                                   "both": (("org:abc", 1), (None, 0)),
                                   "bound to another": (("org:abc", 0), ("org:def", 0))}.items():
        made = {}
        for side, (org, ended) in (("source", source), ("target", target)):
            made[side] = make_task(uid, "ready", created=now - HOUR)
            with store.connect() as db:
                db.execute("UPDATE ade_agent_tasks SET bound_org = ?, crossings_ended = ? WHERE id = ?",
                           (org, ended, made[side]))
        with store.connect() as db:
            bound = task_offers._bind_as_custody(db, uid, made["target"], uid, made["source"], now)
        journal(uid, made["target"], "task.bound", {"offer": "o" * 16, "fence": 1, "bound": bound}, now)
        said[case] = (bound, _rows(_read(client, h))[made["target"]]["events"][-1]["words"])
    assert said == {
        "bound": ({"bound_org": "org:abc"}, "Bound to its organisation"),
        "ended": ({"crossings_ended": 1}, "Its crossings ended: it crosses nowhere by itself"),
        "both": ({"bound_org": "org:abc", "crossings_ended": 1},
                 "Bound to its organisation, and its crossings ended: it crosses nowhere by itself"),
        "bound to another": ({"crossings_ended": 1}, "Its crossings ended: it crosses nowhere by itself")}
    assert board.task_words("task.bound", {"offer": "o" * 16, "fence": 1}, None, {}) == "Its binding changed"


def test_each_cause_a_task_needs_its_owner_for_has_its_own_words():
    """Every cause tasks.py holds a task for its owner with, in the board's words, one by one; `returned` both ways: on
    the owner's task, their teammate's work came back; on a task received from a teammate and marked returned, it went
    back to that teammate (tasks.py's _hold_returned and _auto_resume, the same cause)."""
    said = {cause: board.task_words("task.suspended", {"state": "needs_owner", "cause": cause}, None, {})
            for cause in board._NEEDS}
    assert said == {
        "effect": "Waits for you: a command's effect is not known",
        "worktree": "Waits for you: its worktree is not as recorded",
        "branch": "Waits for you: its branch moved",
        "resume": "Waits for you: its attempt could not go on",
        "settings": "Waits for you: a setting named another credential",
        "watch": "Waits for you: a watched place changed",
        "window": "Waits for you: no account it may take has a known reset",
        "secret": "Waits for you: its text holds something shaped like a secret",
        "identity": "Waits for you: its account's identity changed",
        "moves": "Waits for you: it moved too often in an hour",
        "handoff": "Waits for you: a hand-off",
        "packet": "Waits for you: its packet is past its bound",
        "ungated": "Waits for you: a call ran outside the gate",
        "returned": "Waits for you: your teammate's work came back, to look at first"}
    assert board.task_words("task.suspended", {"state": "needs_owner", "cause": "returned"}, None, {},
                            returned=True) == "Waits for you: it went back to the teammate who handed it to you: End it"
    assert board.task_words("task.suspended", {"state": "needs_owner", "cause": "effect"}, None, {},
                            returned=True) == "Waits for you: a command's effect is not known"
    assert board.task_words("task.suspended", {"state": "needs_owner", "cause": "new"}, None, {}) == \
        "Waits for you: a decision"


def test_a_received_task_sent_back_says_it_went_back_on_the_board(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    mine = make_task(uid, "needs_owner", cause="returned", created=now - 100)
    received = make_task(uid, "needs_owner", cause="returned", created=now - 100, returned=1)
    for tid in (mine, received):
        journal(uid, tid, "task.suspended", {"state": "needs_owner", "cause": "returned", "until": None}, now - 50)
    rows = _rows(_read(client, h))
    assert rows[mine]["events"][-1]["words"] == "Waits for you: your teammate's work came back, to look at first"
    assert rows[received]["events"][-1]["words"] == \
        "Waits for you: it went back to the teammate who handed it to you: End it"
    # journaled as tasks.py's _hold_returned and _auto_resume journal it: the board adds no line of its own
    assert [e["kind"] for e in rows[received]["events"]] == ["task.created", "task.suspended"]


def test_what_a_task_waits_for_is_said_from_its_record_where_its_journal_does_not_say_it(client, stopped):
    """The way back holds a received task for its user (task_offers._start_way_back), and an offer's end puts the owner's
    task back (task_offers._back), with tasks.set_state, which writes no journal line: the board says what the record
    says the task waits for, in a task.suspended event's words, at the time the record last changed; it adds nothing
    where the journal's newest wait says the same, as for an offer that ends with the task waiting as it waited."""
    owner_h, mate_h = signed_up(client), signed_up(client, "mate@example.com")
    ou, mu = _uid(client, owner_h), _uid(client, mate_h)
    now = int(time.time())
    received = {}
    for state in ("suspended", "ready"):
        owners = make_task(ou, "handed_over", title="Owner's task", created=now - HOUR)
        theirs = make_task(mu, state, title="Received task", created=now - HOUR,
                           **({"cause": "window", "waits_until": now + HOUR} if state == "suspended" else {}))
        if state == "suspended":
            journal(mu, theirs, "task.suspended", {"state": "suspended", "cause": "window", "until": now + HOUR},
                    now - 600)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task_offers._start_way_back(db, {"id": "o" * 16, "user_id": ou, "task_id": owners, "teammate_user": mu,
                                             "their_task": theirs, "fence": 1}, now, why=task_offers.CUSTODY_RETURN)
        received[state] = theirs
    rows = _rows(_read(client, mate_h))
    for state, theirs in received.items():
        assert (rows[theirs]["state"], rows[theirs]["phase"]) == ("needs_owner", "now"), state
        assert rows[theirs]["events"][-1] == {
            "at": now, "kind": "task.waits",
            "words": "Waits for you: it went back to the teammate who handed it to you: End it"}, state
    # an offer that ends holding the owner's task for them (a moved branch, a secret shape): a hand-off
    held = make_task(ou, "offering", created=now - HOUR)
    # a pressed offer that ends with the task waiting as it waited before it was offered (_suspended_as), its end
    # journaled after that wait as _end_offer journals it
    again = make_task(ou, "offering", created=now - HOUR)
    journal(ou, again, "task.suspended", {"state": "suspended", "cause": "window", "until": now + HOUR}, now - 900)
    # one offered before it ever waited: it waits as one stopped does (_suspended_as)
    fresh = make_task(ou, "offering", created=now - HOUR)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task_offers._back(db, {"hold_id": "h" * 32, "user_id": ou, "task_id": held, "kind": "pressed"},
                          "Your branch moved.", now, needs_owner=True)
        for tid, hold in ((again, "i"), (fresh, "j")):
            task_offers._back(db, {"hold_id": hold * 32, "user_id": ou, "task_id": tid, "kind": "pressed"},
                              "Your teammate declined it.", now)
    journal(ou, again, "task.offer_declined", {"offer": "o" * 16}, now)
    rows = _rows(_read(client, owner_h))
    assert rows[held]["events"][-1] == {"at": now, "kind": "task.waits", "words": "Waits for you: a hand-off"}
    assert (rows[again]["state"], rows[again]["waits_until"]) == ("suspended", now + HOUR)
    assert [e["kind"] for e in rows[again]["events"]] == ["task.created", "task.suspended", "task.offer_declined"]
    assert (rows[fresh]["state"], rows[fresh]["events"][-1]) == \
        ("suspended", {"at": now, "kind": "task.waits", "words": "It waits for you"})


def test_no_payload_s_free_text_and_no_run_s_reason_reaches_the_board(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "needs_owner", cause="resume", title="Mine")
    run = make_run(uid, tid, 1, started=now - 100, reason=MARK)
    for kind, payload in (
            ("attempt.ended", {"run": run, "fence": 1, "state": "blocked", "cause": MARK, "reason": MARK}),
            ("resume.blocked", {"run": run, "fence": 1, "check": MARK, "found": MARK}),
            ("checkpoint.rejected", {"run": run, "fence": 1, "why": MARK}),
            ("effect.unknown", {"effect": "e1", "tool": MARK, "kind": MARK, "target": MARK}),
            ("checkpoint.ready", {"checkpoint": MARK, "step": 1, "n": 1, "kind": MARK, "head": MARK, "tree": MARK,
                                  "check": MARK}),
            ("task.suspended", {"state": "needs_owner", "cause": MARK, "until": None}),
            ("task.resolved", {"what": MARK, "outcome": MARK}),
            ("task.switched", {"switch": MARK, "on": True, "hold": MARK}),
            ("task.continued", {"from": MARK, "to": MARK, "by": MARK})):
        journal(uid, tid, kind, payload, now - 50, run)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_tasks SET reason = ? WHERE id = ?", (MARK, tid))
    got = _read(client, h)
    assert MARK not in json.dumps(got)
    # the journal's ten, and the board's own line saying what the record says it waits for, its cause's words and
    # never its reason: the journal's newest wait (a cause it does not know) says otherwise
    events = _rows(got)[tid]["events"]
    assert len(events) == 11
    assert [e["words"] for e in events if e["kind"] == "task.waits"] == ["Waits for you: its attempt could not go on"]


# ---- sessions ---------------------------------------------------------------------------------------------------------

def test_a_session_s_state_is_the_one_store_ts_s_session_state_gives_it():
    with open(os.path.join(HERE, "fixtures", "session-states.json"), encoding="utf-8") as f:
        cases = json.load(f)
    assert len(cases) >= 26
    for case in cases:
        assert board.session_state(case["record"]) == case["state"], case["name"]


def _session(sid, **fields):
    record = {"id": sid, "repo": "demo", "program": "claude", "task": "Fix the parser\nand its tests", "base": "b" * 40,
              "branch": f"pp/{sid}", "created_at": 1760700000, "finished": None, "running": True, "exit_code": None,
              "ended_at": None, "chat": None, "activity": None, "account": None, "model_provider": None}
    record.update(fields)
    return record


def test_the_workspace_s_sessions_are_rows_as_the_list_shows_them(client, sessions):
    h = signed_up(client)
    s = ["%012x" % n for n in range(1, 11)]
    sessions["sessions"] = [
        _session(s[0], activity={"state": "working", "at": 1760700100}, account=ACC),
        _session(s[1], program="chat", chat={"state": "needs_you", "asks": []}, task="Ask me", title="Named"),
        _session(s[2], program="shell", task=""),
        _session(s[3], model_provider="deepseek"),
        _session(s[4], running=False, finished={"head": "c" * 7, "at": 1760700500}, ended_at=1760700600,
                 shipped={"url": "https://github.com/o/r/pull/7", "at": 1760700550, "commits": 2}),
        _session(s[5], running=False, exit_code=-15, ended_at=1760700300),
        _session(s[6], closed=True),
        _session(s[7], program="claude-sign-in", sign_in=True),
        _session(s[8], program="codex", running=False, exit_code=3, ended_at=1760700200),
        _session(s[9], program="codex", account=ACC),
        _session("not-an-id"),
    ]
    rows = _rows(_read(client, h), "session")
    assert set(rows) == {s[0], s[1], s[2], s[3], s[4], s[5], s[8], s[9]}   # a closed one and a sign-in left out
    for sid, r in rows.items():
        assert tuple(r) == board.FIELDS and r["session"] == sid and r["parent"] is None and r["progress"] is None
        assert r["order"] is None and r["waits_until"] is None and r["created_at"] == r["started_at"] == 1760700000
    assert (rows[s[0]]["title"], rows[s[0]]["state"], rows[s[0]]["phase"]) == ("Fix the parser", "working", "now")
    assert rows[s[0]]["account"] == {"kind": "claude", "id": ACC}
    assert (rows[s[1]]["title"], rows[s[1]]["state"], rows[s[1]]["account"]) == \
        ("Named", "waiting", {"kind": "claude", "id": "default"})
    assert (rows[s[2]]["title"], rows[s[2]]["state"], rows[s[2]]["account"]) == ("Shell", "running", None)
    assert rows[s[3]]["account"] is None                                  # another provider's model: its key
    assert (rows[s[4]]["state"], rows[s[4]]["phase"], rows[s[4]]["ended_at"]) == ("done", "history", 1760700600)
    assert [e["words"] for e in rows[s[4]]["events"]] == ["Started", "Finished", "Shipped as a pull request",
                                                          "Its program ended"]
    assert (rows[s[5]]["state"], rows[s[5]]["ended_at"]) == ("stopped", 1760700300)
    assert rows[s[5]]["events"][-1]["words"] == "Its program was stopped"
    assert (rows[s[8]]["state"], rows[s[8]]["account"]) == ("failed", {"kind": "codex", "id": "default"})
    assert rows[s[9]]["account"] == {"kind": "codex", "id": ACC}         # an account added for Codex, Codex's
    assert rows[s[8]]["events"][-1]["words"] == "Its program failed"
    assert rows[s[0]]["ended_at"] is None and [e["words"] for e in rows[s[0]]["events"]] == ["Started"]


def test_a_session_s_title_is_its_task_s_first_line_and_a_line_of_blanks_is_none(client, sessions):
    """store.ts's toSession: its own name, else its task's first line, trimmed, else its program's name: a first line of
    blanks gives no title of its own."""
    h = signed_up(client)
    s = ["%012x" % n for n in range(50, 53)]
    sessions["sessions"] = [_session(s[0], task="  Fix the parser  \nand its tests"),
                            _session(s[1], task="   \nthe rest"), _session(s[2], program="shell", task=" \t ")]
    rows = _rows(_read(client, h), "session")
    assert {sid: rows[sid]["title"] for sid in s} == {s[0]: "Fix the parser", s[1]: "claude", s[2]: "Shell"}


def test_a_time_out_of_the_board_s_range_is_left_out_of_a_session_s_row_and_the_row_kept(client, sessions):
    """A session's programs can write its record (workspace.py): a time there that is not Unix seconds the board takes
    (whole seconds, from 0 to MOST_SECONDS, the end of the year 9999, which Python's dates and a page's Date both hold;
    a page's Date throws past about the year 275760) is left out of its row, never sent, and the row is kept, not
    refused as a whole."""
    h = signed_up(client)
    s = ["%012x" % n for n in range(60, 66)]
    past = board.MOST_SECONDS + 1
    sessions["sessions"] = [
        _session(s[0], created_at=past, landed={"into": "main", "head": "c" * 7, "at": 9000000000000}),
        _session(s[1], program="chat", chat={"state": "idle", "asks": []}, finished={"head": "c" * 7, "at": past}),
        _session(s[2], running=False, exit_code=0, ended_at=past, finished={"head": "c" * 7, "at": 1760700500}),
        _session(s[3], created_at=1760700000.5, shipped={"url": "u", "at": True}),
        _session(s[4], created_at=board.MOST_SECONDS),
        _session(s[5], created_at=-5),
    ]
    got = _read(client, h)
    rows = _rows(got, "session")
    assert set(rows) == set(s)
    assert (rows[s[0]]["created_at"], rows[s[0]]["started_at"], rows[s[0]]["events"]) == (None, None, [])
    assert (rows[s[1]]["phase"], rows[s[1]]["ended_at"]) == ("history", None)
    assert [e["words"] for e in rows[s[1]]["events"]] == ["Started"]
    assert rows[s[2]]["ended_at"] == 1760700500                  # its end not a time: its finishing's
    assert (rows[s[3]]["created_at"], rows[s[3]]["events"]) == (None, [])
    assert rows[s[4]]["created_at"] == board.MOST_SECONDS
    assert (rows[s[5]]["created_at"], rows[s[5]]["events"]) == (None, [])     # before 1970: left out, the row kept

    def numbers(value):
        if isinstance(value, dict):
            return [n for v in value.values() for n in numbers(v)]
        if isinstance(value, list):
            return [n for v in value for n in numbers(v)]
        return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []
    assert max(numbers(got["rows"])) == board.MOST_SECONDS


def test_a_session_another_row_holds_is_not_listed_again(client, sessions, monkeypatch):
    h = signed_up(client)
    held, free = "1" * 12, "2" * 12
    sessions["sessions"] = [_session(held), _session(free)]
    monkeypatch.setitem(board._SOURCES, "controller", lambda user, now: [
        board.row("session", "task-1", "The controller's task", phase="now", state="working", parent="req-1",
                  session=held)])
    rows = _rows(_read(client, h))
    assert set(rows) == {"task-1", free}
    assert rows["task-1"]["session"] == held


def test_a_workspace_that_does_not_run_gives_no_sessions_and_forgets_its_last(client, sessions, monkeypatch):
    h = signed_up(client)
    sessions["sessions"] = [_session("3" * 12)]
    assert set(_rows(_read(client, h))) == {"3" * 12}

    def refuse(user):
        raise HTTPException(status_code=409, detail="The workspace is not running: start it first.")
    monkeypatch.setattr(workspace_routes, "sessions", refuse)
    assert _read(client, h)["rows"] == []
    assert _uid(client, h) not in board._last_read


def test_the_board_waits_for_the_workspace_s_sessions_at_most_its_bound(client, sessions, monkeypatch):
    h = signed_up(client)
    uid = _uid(client, h)
    sessions["sessions"] = [_session("4" * 12)]
    assert set(_rows(_read(client, h))) == {"4" * 12}                    # read once, within the board's own wait
    _settled(uid)
    monkeypatch.setattr(board, "SESSIONS_WAIT", 0.3)
    gate, calls = threading.Event(), []

    def slow(user):
        calls.append(1)
        gate.wait(10)
        return {"sessions": [_session("5" * 12)]}
    monkeypatch.setattr(workspace_routes, "sessions", slow)
    began = time.monotonic()
    first = _read(client, h)
    assert time.monotonic() - began < 6                                # not the 10 seconds the read takes
    assert set(_rows(first)) == {"4" * 12}                               # the last read stands in
    under_way = board._reads[uid]
    second = _read(client, h)
    assert set(_rows(second)) == {"4" * 12}
    assert board._reads[uid] is under_way                                # no second read while one is under way
    gate.set()
    _settled(uid)
    assert len(calls) == 1
    assert set(_rows(_read(client, h))) == {"5" * 12}


def test_a_slow_first_read_gives_no_sessions_rather_than_wait(client, sessions, monkeypatch):
    monkeypatch.setattr(board, "SESSIONS_WAIT", 0.2)
    h = signed_up(client)
    gate = threading.Event()

    def slow(user):
        gate.wait(10)
        return {"sessions": [_session("6" * 12)]}
    monkeypatch.setattr(workspace_routes, "sessions", slow)
    try:
        assert _read(client, h)["rows"] == []
    finally:
        gate.set()


def test_a_last_read_older_than_late_kept_does_not_stand_in(client, sessions, monkeypatch):
    h = signed_up(client)
    uid = _uid(client, h)
    sessions["sessions"] = [_session("7" * 12)]
    assert set(_rows(_read(client, h))) == {"7" * 12}
    _settled(uid)
    monkeypatch.setattr(board, "SESSIONS_WAIT", 0.2)
    when, kept = board._last_read[uid]
    board._last_read[uid] = (when - board.LATE_KEPT - 1, kept)
    gate = threading.Event()
    monkeypatch.setattr(workspace_routes, "sessions", lambda user: gate.wait(10) or {"sessions": []})
    try:
        assert _read(client, h)["rows"] == []
    finally:
        gate.set()


def test_a_session_made_in_a_running_workspace_is_on_the_board(client, local, env):
    h = signed_up(client)
    assert client.post("/api/ade/workspace/start", headers=h).status_code == 200
    make_repo(str(env / "workspaces" / _uid(client, h) / "repos" / "demo"))
    r = client.post("/api/ade/workspace/sessions", headers=h, json={"repo": "demo", "program": "shell", "task": "Look"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    row = _rows(_read(client, h))[sid]
    assert (row["kind"], row["title"], row["state"], row["phase"], row["account"]) == \
        ("session", "Look", "running", "now", None)
    assert row["events"][0]["words"] == "Started"


# ---- other modules' rows ----------------------------------------------------------------------------------------------

def test_a_module_s_rows_are_on_the_board_checked_and_one_that_fails_leaves_the_rest(client, stopped, monkeypatch,
                                                                                    capfd):
    h = signed_up(client)
    now = int(time.time())
    good = board.row("requirement", "req-1", "Ship the parser", phase="now", state="working",
                     progress={"done": 1, "of": 4}, account={"kind": "claude", "id": "default"}, created_at=now - 60,
                     started_at=now - 50, events=[board.event(now - 50, "requirement.started", "Planned")])
    bad = dict(good, id="req-2", phase="later")
    extra = dict(good, id="req-3", note="a field the board does not have")

    def broken(user, at):
        raise RuntimeError(MARK)
    monkeypatch.setitem(board._SOURCES, "controller", lambda user, at: [good, bad, extra, dict(good)])
    monkeypatch.setitem(board._SOURCES, "broken", broken)
    rows = _rows(_read(client, h))
    assert rows == {"req-1": good}
    err = capfd.readouterr().err
    assert "the rows of broken could not be read: RuntimeError" in err and MARK not in err
    assert "a row of controller is out of shape" in err and "is on the board already: requirement req-1" in err


def test_add_rows_takes_a_named_function_and_replaces_one_of_the_same_name(monkeypatch):
    monkeypatch.setattr(board, "_SOURCES", {})
    first, second = (lambda user, now: []), (lambda user, now: [])
    board.add_rows("controller", first)
    board.add_rows("controller", second)
    assert board._SOURCES == {"controller": second}
    for name, source in (("", first), ("x", None), (None, first)):
        with pytest.raises(TypeError):
            board.add_rows(name, source)


@pytest.mark.parametrize("change", [
    {"kind": "goal"}, {"phase": "later"}, {"row_id": ""}, {"row_id": "has space"}, {"title": None},
    {"state": "Running"}, {"order": 0}, {"order": True}, {"progress": {"done": 3, "of": 2}},
    {"progress": {"done": 1}}, {"account": {"kind": "gemini", "id": "default"}}, {"account": {"kind": "claude", "id": "x"}},
    {"session": "short"}, {"created_at": -1}, {"started_at": 1.5}, {"ended_at": "today"}, {"waits_until": True},
    {"events": [{"at": 1, "kind": "x"}]}, {"events": [{"at": None, "kind": "a.b", "words": "w"}]},
    {"events": [{"at": 1, "kind": "Bad Kind", "words": "w"}]}, {"more_events": -1}, {"parent": "has space"},
    # a name is matched whole: a trailing line break is no part of it
    {"row_id": "req-1\n"}, {"state": "running\n"}, {"parent": "req-0\n"}, {"session": "0123456789ab\n"},
    {"account": {"kind": "claude", "id": "default\n"}}, {"events": [{"at": 1, "kind": "a.b\n", "words": "w"}]},
    # a time past the year 9999 (MOST_SECONDS + 1), which a page's Date cannot draw
    {"created_at": 253402300800}, {"waits_until": 9000000000000},
    {"events": [{"at": 253402300800, "kind": "a.b", "words": "w"}]},
])
def test_row_refuses_what_is_out_of_shape(change):
    fields = {"kind": "requirement", "row_id": "req-1", "title": "T", "phase": "now", "state": "working"}
    fields.update(change)
    with pytest.raises(ValueError):
        board.row(fields.pop("kind"), fields.pop("row_id"), fields.pop("title"), **fields)


OK_GRANT = {"version": 3, "digest": "0123456789ab", "ask_current": False}


@pytest.mark.parametrize("grant", [
    dict(OK_GRANT, version=0), dict(OK_GRANT, version=True), dict(OK_GRANT, version="3"),
    dict(OK_GRANT, version=2 ** 53), dict(OK_GRANT, digest="0123456789AB"), dict(OK_GRANT, digest="0" * 64),
    dict(OK_GRANT, digest="0123456789ab\n"), dict(OK_GRANT, ask_current=None), dict(OK_GRANT, ask_current=1),
    dict(OK_GRANT, surplus=1), {"version": None, "digest": "0123456789ab", "ask_current": None},
    {"version": None, "digest": None, "ask_current": True}, "version 3",
])
def test_a_row_s_grant_out_of_shape_is_refused(grant):
    """A task row's grant: a version from 1, the first 12 lowercase hex digits of its
    digest and whether it pins the goal as it stands, or all three null; anything else is out of shape."""
    with pytest.raises(ValueError):
        board.row("task", "t1", "T", phase="now", state="running", grant=grant)


def test_a_row_s_grant_is_a_task_s_in_its_one_shape():
    assert board.row("task", "t1", "T", phase="now", state="running", grant=OK_GRANT)["grant"] == OK_GRANT
    none = {"version": None, "digest": None, "ask_current": None}
    assert board.row("task", "t1", "T", phase="now", state="running", grant=none)["grant"] == none
    assert board.row("task", "t1", "T", phase="now", state="running")["grant"] is None
    # a row that is not a task's carries none
    for kind in ("requirement", "session"):
        with pytest.raises(ValueError):
            board.row(kind, "r1", "T", phase="now", state="working", grant=OK_GRANT)
        assert board.row(kind, "r1", "T", phase="now", state="working")["grant"] is None


def test_row_cuts_a_long_title_and_words_and_keeps_the_newest_events():
    events = [{"at": i, "kind": "a.b", "words": "w" * 400 if i == 59 else f"e{i}"} for i in range(60)]
    r = board.row("requirement", "r", "T" * 300, phase="next", state="queued", order=3, events=events,
                  more_events=5)
    assert len(r["title"]) == board.TITLE_MOST and r["title"].endswith("\u2026")
    assert [e["at"] for e in r["events"]] == list(range(10, 60)) and r["more_events"] == 15
    assert len(r["events"][-1]["words"]) == board.WORDS_MOST
    assert r["order"] == 3 and board.row("requirement", "r", "T", phase="now", state="x", order=3)["order"] is None


# ---- a step under way, and an account never named --------------------------------------------------------------------

def test_a_step_under_way_is_not_counted_as_done(client, stopped):
    h = signed_up(client)
    uid = _uid(client, h)
    tid = make_task(uid, "running", steps=3, done=1, doing=True)
    assert _rows(_read(client, h))[tid]["progress"] == {"done": 1, "of": 3}


def test_an_account_its_owner_never_named_is_named_by_its_id():
    other = "b" * 12
    run = {"task_attempt": 1, "backend": json.dumps({"kind": "account", "id": other})}
    assert board.task_words("attempt.started", {}, run, {}) == f"Attempt 1 started on {other}"
    assert board.task_words("attempt.started", {}, run, {other: "  "}) == f"Attempt 1 started on {other}"
    assert board.task_words("attempt.started", {}, dict(run, backend=json.dumps({"kind": "account", "id": "default"})),
                            {}) == "Attempt 1 started on Default"


# ---- a task through tasks.py's own code, and more on events, sessions and accounts' names ----------------------------

def test_a_task_driven_through_tasks_py_is_in_now_then_next_then_now_then_next_then_history(client, owner, ws,
                                                                                           stopped):
    """A task made, started, worked, refused by its account's window and stopped through the app's routes and
    tasks.py's own code (test_agent_tasks.py's fake workspace), never written straight into the store: made, it waits
    for Start (now); started, its attempt is queued (next); working (now); refused, it waits for its reset, which
    Auto-resume will go on with (next); ended as stopped by its owner (history). Each step's newest event in the
    board's words."""
    h, user = owner
    own(user, TASKS_ACC["A"])                                   # the owner's name for it: "acct aa"
    task_id = tasks_task(client, h, tasks_agent(client, h, TASKS_ACC["A"]))["id"]

    def now_row():
        return _rows(_read(client, h))[task_id]
    made = now_row()
    assert (made["phase"], made["state"], made["progress"], made["order"]) == ("now", "ready", {"done": 0, "of": 2},
                                                                               None)
    assert [e["words"] for e in made["events"]] == ["Made, with 2 steps"]
    assert press(client, h, task_id, "start").status_code == 200
    run_id = task_row(task_id)["lease_run"]
    queued = now_row()
    assert (queued["phase"], queued["state"], queued["order"]) == ("next", "running", 1)
    assert queued["events"][-1]["words"] == "Step 1, attempt 1 queued on acct aa, at your press"
    record(user, run_id, "working")
    working = now_row()
    assert (working["phase"], working["state"], working["started_at"] is not None) == ("now", "running", True)
    assert working["events"][-1]["words"] == "Step 1, attempt 1 started on acct aa"
    reset = int(time.time()) + 2 * HOUR
    refuse(user, run_id, resets=reset)
    waiting = now_row()
    assert (waiting["phase"], waiting["state"], waiting["waits_until"]) == ("next", "suspended", reset)
    assert [e["words"] for e in waiting["events"]][-2:] == ["Step 1, attempt 1 was held", board.WINDOW_UNTIL]
    assert press(client, h, task_id, "end", outcome="stopped").status_code == 200
    ended = now_row()
    assert (ended["phase"], ended["state"], ended["ended_at"] is not None) == ("history", "stopped", True)
    assert ended["events"][-1]["words"] == "Ended, by you"


def test_an_event_out_of_shape_is_left_out_and_its_row_kept(client, stopped, capfd):
    """One journal event the board cannot show (its time not whole seconds) leaves its task's row on the board without
    it, said on standard error, and not counted among the earlier ones; the rest of the board answers."""
    h = signed_up(client)
    uid = _uid(client, h)
    now = int(time.time())
    tid = make_task(uid, "running", created=now - 100)
    other = make_task(uid, "running", created=now - 200)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_task_events (user_id, task_id, kind, run_id, fence, at, payload) VALUES "
                   "(?,?,?,?,?,?,?)", (uid, tid, "task.switched", None, None, now - 50.5,
                                       json.dumps({"switch": "relay", "on": True, "hold": None})))
    rows = _rows(_read(client, h))
    assert [e["words"] for e in rows[tid]["events"]] == ["Made, with 3 steps"] and rows[tid]["more_events"] == 0
    assert other in rows
    assert f"board: an event of task {tid} could not be shown" in capfd.readouterr().err


def test_each_person_s_board_names_an_account_with_that_person_s_own_name_for_it(client, stopped):
    """Every person has an account named default: each board names it as its own person named it, never as another
    did (the owner's names read for that person alone)."""
    a, b = signed_up(client), signed_up(client, "other@example.com")
    ua, ub = _uid(client, a), _uid(client, b)
    now = int(time.time())
    with store.connect() as db:
        for uid, name in ((ua, "Mine"), (ub, "Theirs")):
            db.execute("INSERT INTO ade_account_owners (id, user_id, account, name) VALUES (?,?,?,?)",
                       (secrets.token_hex(8), uid, "default", name))
    ta, tb = make_task(ua, "running", created=now - 100), make_task(ub, "running", created=now - 100)
    for uid, tid in ((ua, ta), (ub, tb)):
        run = make_run(uid, tid, 1, started=now - 50)
        journal(uid, tid, "attempt.started", {"run": run, "fence": 1}, now - 50, run)
    assert _rows(_read(client, a))[ta]["events"][-1]["words"] == "Step 1, attempt 1 started on Mine"
    assert _rows(_read(client, b))[tb]["events"][-1]["words"] == "Step 1, attempt 1 started on Theirs"


def test_one_person_s_last_read_of_sessions_never_stands_in_for_another_s(client, sessions, monkeypatch):
    a, b = signed_up(client), signed_up(client, "other@example.com")
    sessions["sessions"] = [_session("8" * 12)]
    assert set(_rows(_read(client, a))) == {"8" * 12}
    _settled(_uid(client, a))
    monkeypatch.setattr(board, "SESSIONS_WAIT", 0.2)
    gate = threading.Event()
    monkeypatch.setattr(workspace_routes, "sessions", lambda user: gate.wait(10) or {"sessions": []})
    try:
        assert _read(client, b)["rows"] == []           # b's first read is slow: none, never a's last
    finally:
        gate.set()


def test_a_failed_call_to_the_workspace_lets_the_last_read_stand_in_for_late_kept(client, sessions, monkeypatch):
    """A call to the workspace that fails (WorkspaceCallError: it did not answer as it should) is not a workspace that
    stopped: the last read stands in while it is no older than LATE_KEPT, as for a slow read, and is kept."""
    h = signed_up(client)
    uid = _uid(client, h)
    sessions["sessions"] = [_session("9" * 12)]
    assert set(_rows(_read(client, h))) == {"9" * 12}
    _settled(uid)

    def fail(user):
        raise workspace_client.WorkspaceCallError(502, "The workspace is not answering.")
    monkeypatch.setattr(workspace_routes, "sessions", fail)
    assert set(_rows(_read(client, h))) == {"9" * 12}
    assert uid in board._last_read
    _, kept = board._last_read[uid]
    board._last_read[uid] = (time.monotonic() - 55, kept)      # LATE_KEPT is a minute: 55 seconds old stands in
    assert set(_rows(_read(client, h))) == {"9" * 12}
    board._last_read[uid] = (time.monotonic() - 61, kept)
    assert _read(client, h)["rows"] == []


def test_a_sign_in_is_left_out_running_or_ended_and_a_program_the_person_started_is_listed(client, sessions):
    """store.ts's isSignIn: a sign-in program the workspace started as one, or one that has ended (its record's
    sign_in not kept after a restart); the person's own program of the same name, still running, is a session."""
    h = signed_up(client)
    s = ["%012x" % n for n in range(20, 26)]
    sessions["sessions"] = [
        _session(s[0], program="claude-sign-in", sign_in=True),
        _session(s[1], program="claude-sign-in", running=False, sign_in=False, exit_code=0, ended_at=1760700100),
        _session(s[2], program="github", sign_in=False),
        _session(s[3], program="codex-sign-in", running=False, exit_code=0, ended_at=1760700100),
        # a field's truth as JavaScript reads it (store.ts): an empty list running is running, an empty object closed
        # is closed
        _session(s[4], program="github", sign_in=False, running=[]),
        _session(s[5], closed={}),
    ]
    assert set(_rows(_read(client, h), "session")) == {s[2], s[4]}


def test_a_session_s_events_are_in_time_order_and_its_end_in_words_by_how_its_program_ended(client, sessions):
    h = signed_up(client)
    s = ["%012x" % n for n in range(30, 35)]
    sessions["sessions"] = [
        # shipped before it finished: the events by their times, not by their kinds
        _session(s[0], created_at=100, finished={"head": "c" * 7, "at": 300}, shipped={"url": "u", "at": 200}),
        _session(s[1], created_at=100, running=False, exit_code=0, ended_at=400),
        _session(s[2], created_at=100, running=False, exit_code=-15, ended_at=400),
        _session(s[3], created_at=100, running=False, exit_code=2, ended_at=400),
        # an exit code that is not a number: failed, as session_state reads it, and its end said so
        _session(s[4], created_at=100, running=False, exit_code=False, ended_at=400),
    ]
    rows = _rows(_read(client, h), "session")
    assert [(e["at"], e["words"]) for e in rows[s[0]]["events"]] == [(100, "Started"), (200, "Shipped as a pull request"),
                                                                     (300, "Finished")]
    ends = {sid: (rows[sid]["state"], rows[sid]["events"][-1]["words"]) for sid in s[1:]}
    assert ends == {s[1]: ("done", "Its program ended"), s[2]: ("stopped", "Its program was stopped"),
                    s[3]: ("failed", "Its program failed"), s[4]: ("failed", "Its program failed")}


def test_a_finished_chat_s_end_is_when_it_finished(client, sessions):
    """A session's end is its program's where its record has one, else its finishing: a chat that finished while its
    program runs ends at its finishing (its program is the app's own chat, which says no exit of its own), and one
    whose program has ended too ends then, that end not said in words."""
    h = signed_up(client)
    sid, stopped_chat = "4" * 11 + "f", "4" * 11 + "e"
    sessions["sessions"] = [_session(sid, program="chat", chat={"state": "idle", "asks": []},
                                     finished={"head": "c" * 7, "at": 1760700500}),
                            _session(stopped_chat, program="chat", running=False, chat={"state": "idle", "asks": []},
                                     finished={"head": "c" * 7, "at": 1760700500}, ended_at=1760700600)]
    rows = _rows(_read(client, h))
    r = rows[sid]
    assert (r["phase"], r["state"], r["ended_at"]) == ("history", "done", 1760700500)
    assert [e["words"] for e in rows[stopped_chat]["events"]] == ["Started", "Finished"]
    assert rows[stopped_chat]["ended_at"] == 1760700600


def test_an_account_s_name_in_an_event_is_cut_to_its_bound():
    run = {"task_attempt": 1, "task_step": 1, "backend": json.dumps({"kind": "account", "id": ACC})}
    words = board.task_words("attempt.started", {}, run, {ACC: "N" * 200})
    assert words == "Step 1, attempt 1 started on " + "N" * (board.NAME_MOST - 1) + "\u2026"

