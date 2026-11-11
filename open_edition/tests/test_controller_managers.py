"""A requirement's own manager, the app's side: the store's tables for the managers and their attempts, the cap, the
managers' wakes, their attempts' queue, packets and ends, and the routes and views that give them."""

import sqlite3

from proofpane_open import store

# the managers' columns added to tables of the controller's that an older store already has, each table's in order
ADDED = {
    "ade_controllers": ("managers_version", "managers_sent", "managers_held", "managers_error", "managers_error_at",
                        "room", "room_at"),
    "ade_controller_tasks": ("text", "text_cut", "text_hidden", "attempt_id", "needs_owner", "changed"),
    "ade_controller_decisions": ("requirement_id",),
}
NEW_TABLES = ("ade_controller_managers", "ade_controller_attempts")


def _columns(db, table):
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


def test_a_store_from_before_the_managers_gains_their_tables_and_columns_and_keeps_its_rows(env, tmp_path):
    """The two tables are made by CREATE TABLE IF NOT EXISTS, as the others are, and the columns are
    added (store.ADDED_COLUMNS), so that a store from before the managers gains them and nothing of it is rewritten."""
    path = str(tmp_path / "before.db")
    with store.connect(path) as db:
        db.execute("INSERT INTO ade_controller_decisions (id, user_id, n, question_id, text, at) VALUES "
                   "('d1', 'u1', 1, NULL, 'Yes.', 5)")
    old = sqlite3.connect(path)
    old.execute("DROP INDEX ade_controller_decisions_requirement")     # made after the columns (store.AFTER_MIGRATE)
    for table in NEW_TABLES:
        old.execute(f"DROP TABLE {table}")
    for table, columns in ADDED.items():
        for column in columns:
            old.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    old.commit()
    for table, columns in ADDED.items():
        assert not set(columns) & set(_columns(old, table)), table
    old.close()
    with store.connect(path) as db:
        for table, columns in ADDED.items():
            have = _columns(db, table)
            assert have[-len(columns):] == list(columns), table
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert set(NEW_TABLES) <= names
        row = db.execute("SELECT * FROM ade_controller_decisions WHERE id = 'd1'").fetchone()
        assert (row["text"], row["requirement_id"]) == ("Yes.", None)
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"ade_controller_managers_waits", "ade_controller_attempts_n", "ade_controller_attempts_state",
                "ade_controller_attempts_queued", "ade_controller_decisions_requirement"} <= indexes


# ---- the app's side of the managers, against a stand-in workspace ---------------------------------------------------

from proofpane_open.ade import agents, controller, controller_routes, push, workspace_client  # noqa: E402
from test_controller import T0, Workspace, act, own, plan, question, record, start, uid  # noqa: E402

import time  # noqa: E402

import base64  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

REQUEST = "Make the README say hello.\n\nits first line is hello.\n\nThanks\tagain"


class Managers(Workspace):
    """The stand-in workspace (test_controller.Workspace) with PUT /controller/managers: each body kept, the managers'
    version held, and the attempt answered as `answer` says (taken by default, or refused with a cause), an id sent
    again answered as the first time. Its pages list the attempts it holds (`listed`: a taken one starting until a
    test moves it on; one a test ended listed once, then let go), the room and the managers' version it holds, as
    the contract has every page do."""

    def __init__(self):
        super().__init__()
        self.managers, self.managers_held, self.answer, self.answers = [], None, None, {}
        self.managers_fail, self.listed, self.room = None, {}, {"window": "open", "until": None,
                                                                 "capped_since": None}

    def call(self, endpoint, method, path, body=None, timeout=30):
        if path == "/controller/managers" and method == "PUT":
            self.calls.append((method, path, body))
            if self.managers_fail is not None:
                raise self.managers_fail
            self.managers.append(body)
            self.managers_held = body["version"]
            a = body["attempt"]
            out = None
            if a is not None:
                if a["id"] not in self.answers:
                    cause = self.answer
                    self.answers[a["id"]] = {"id": a["id"], "taken": cause is None, "cause": cause,
                                             "why": None if cause is None else "The workspace's words."}
                    if cause is None:
                        self.listed[a["id"]] = listed({"id": a["id"], "requirement_id": a["requirement"],
                                                       "fence": a["fence"], "n": a["n"]}, "starting", session=None)
                out = self.answers[a["id"]]
            return {"held": body["version"], "attempt": out}
        got = super().call(endpoint, method, path, body, timeout)
        if path.startswith("/controller/record?since=") and isinstance(got, dict):
            got.setdefault("attempts", list(self.listed.values()))
            got.setdefault("managers_held", self.managers_held)
            got.setdefault("room", dict(self.room))
            for x in got["attempts"]:
                if x["state"] == "ended":
                    self.listed.pop(x["id"], None)
        return got


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    fake = Managers()
    monkeypatch.setattr(workspace_client, "call", fake.call)
    from proofpane_open.ade import agents
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    monkeypatch.setattr(controller_routes, "_sync_soon", lambda user: None)
    return fake


def on(user, now=T0, **settings):
    own(user["id"])
    controller.change_settings(user, dict({"account": "default"}, **settings), now=now)
    return controller.set_state(user, "on", now=now)


def managed(user, share=1, now=T0 + 5, **kw):
    """A request, and a requirement planned from it with a share, taken: (request, requirement id)."""
    request = controller.add_request(user, REQUEST, now=now)
    rid = uid()
    p = dict(plan(request["id"], "Make the README say hello.", rid=rid, **kw), share=share)
    assert controller.take(user["id"], record(p), now=now + 1)["taken"] == 1
    return request, rid


def manager(user, rid):
    with store.connect() as db:
        m = controller._manager(db, user["id"], rid)
        return dict(m, state=controller.manager_state(m, m["requirement_state"])) if m else None


def attempts(user, rid=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_controller_attempts WHERE user_id = ? ORDER BY queued_at, n",
                          (user["id"],)).fetchall()
    return [dict(r) for r in rows if rid is None or r["requirement_id"] == rid]


def sync(user, ws):
    return controller.sync(user, "http://workspace.test", call=ws.call)


def held_page(ws, *attempts_, tasks=(), more=False, **extra):
    """The next page the stand-in serves: its attempts and tasks as given, `held` the mirror's version held."""
    page = dict({"seq": len(ws.calls) + 1, "more": more, "actions": [], "tasks": list(tasks),
                 "attempts": list(attempts_)}, **extra)
    ws.pages.append(page)
    return page


def listed(a, state="running", **over):
    """An attempt as the workspace's page lists it."""
    return dict({"id": a["id"], "requirement": a["requirement_id"], "fence": a["fence"], "n": a["n"], "state": state,
                 "outcome": None, "cause": None, "waits_until": None, "refused": [], "refused_count": 0,
                 "refused_models": [], "note": None, "session": "a" * 12, "started_at": T0 + 50,
                 "ended_at": None}, **over)


def ended(a, outcome="waiting", cause=None, **over):
    return listed(a, "ended", outcome=outcome, cause=cause, ended_at=T0 + 60, **over)


def test_a_plan_with_a_share_makes_its_manager_ready_and_one_without_is_the_controller_s(env):
    user = {"id": "u1"}
    on(user, cap=3)
    _, rid = managed(user, share=2)
    m = manager(user, rid)
    assert (m["share"], m["state"], m["fence"], m["attempts"]) == (2, "ready", 0, 0)
    assert [w["kind"] for w in json.loads(m["wakes"])] == ["plan"]
    [line] = [e for e in events_of(user) if e["kind"] == "plan"]
    assert line["payload"]["share"] == 2
    request = controller.add_request(user, REQUEST, now=T0 + 20)
    rid2 = uid()
    assert controller.take(user["id"], record(plan(request["id"], "Make the README say hello.", rid=rid2)),
                           now=T0 + 21)["taken"] == 1
    assert manager(user, rid2) is None


@pytest.mark.parametrize("share", [0, 4, -1, "2", True, 2.0])
def test_a_plan_s_share_past_the_cap_in_effect_or_not_a_whole_number_is_refused(env, share):
    user = {"id": "u1"}
    on(user, cap=3)
    request = controller.add_request(user, REQUEST, now=T0 + 5)
    p = dict(plan(request["id"], "Make the README say hello."), share=share)
    got = controller.take(user["id"], record(p), now=T0 + 6)
    assert got["refused"] == 1
    [line] = [e for e in events_of(user) if e["kind"] == "plan.refused"]
    assert line["payload"]["why"] == "its share is not a whole number from 1 to the cap, 3"


def events_of(user, kind=None):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_controller_events WHERE user_id = ? ORDER BY seq",
                          (user["id"],)).fetchall()
    return [dict(r, payload=json.loads(r["payload"])) for r in rows if kind is None or r["kind"] == kind]


def test_a_sync_queues_an_attempt_for_a_ready_manager_and_sends_it_with_the_managers_states(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    sync(user, ws)                      # the mirror sent: the workspace holds the owner's side at the next read
    assert attempts(user) == []         # a sync that sends a new mirror queues at the next
    sync(user, ws)
    [a] = attempts(user)
    assert (a["state"], a["n"], a["fence"]) == ("sent", 1, 1)
    body = ws.managers[-1]
    assert body["attempt"]["id"] == a["id"] and body["managers"][0]["requirement"] == rid
    packet = base64.b64decode(body["attempt"]["packet_b64"])
    assert body["attempt"]["packet_digest"] == controller.shapes.tagged_digest(controller.MANAGER_TAG, packet)
    got = json.loads(packet)
    assert got["schema"] == "ade.manager.v1" and got["attempt"] == {"id": a["id"], "n": 1, "fence": 1}
    assert [w["kind"] for w in got["information"]["woken_by"]] == ["plan"]
    m = manager(user, rid)
    assert (m["state"], m["lease"], m["fence"]) == ("running", a["id"], 1)


def running(user, ws, share=1, **kw):
    """A managed requirement whose first attempt the workspace took and runs: (requirement id, attempt)."""
    on(user, **kw)
    _, rid = managed(user, share=share)
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user, rid)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    return rid, attempts(user, rid)[0]


def run_next(user, ws, rid):
    """Syncs until the next attempt of `rid` is queued and taken, at most three (a sync that sends a new mirror queues
    at the next), and the workspace's page then listing it running: the attempt."""
    have = len(attempts(user, rid))
    for _ in range(3):
        sync(user, ws)
        if len(attempts(user, rid)) > have:
            break
    a = attempts(user, rid)[-1]
    assert a["n"] == have + 1, (a["n"], have)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    return attempts(user, rid)[-1]


def end(user, ws, a, outcome="waiting", cause=None, **over):
    """The workspace's end of attempt `a` on its next page, and the sync that takes it."""
    ws.listed[a["id"]] = ended(a, outcome, cause, **over)
    sync(user, ws)
    return next(x for x in attempts(user) if x["id"] == a["id"])


def test_an_attempt_the_page_lists_running_moves_on_and_its_end_leaves_its_manager_waiting(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    assert (a["state"], a["session_id"]) == ("running", "a" * 12)
    assert [e["payload"]["attempt"] for e in events_of(user, "attempt.started")] == [a["id"]]
    got = end(user, ws, a)
    assert (got["state"], got["outcome"], got["cause"]) == ("ended", "waiting", None)
    m = manager(user, rid)
    assert (m["state"], m["lease"], m["failures"], m["waits"]) == ("waiting", None, 0, None)
    [line] = events_of(user, "attempt.ended")
    assert line["payload"] == {"attempt": a["id"], "n": 1, "fence": 1, "outcome": "waiting", "cause": None,
                               "refused": 0, "note": False, "manager": "waiting"}
    sync(user, ws)
    assert len(attempts(user)) == 1     # nothing changed: no attempt spent


def test_a_wake_during_an_attempt_is_kept_and_starts_the_next_once_it_ends(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    controller.decide(user, "Use the main branch.", now=T0 + 70)   # on none: no task open, so no manager woken
    assert manager(user, rid)["woken"] == 0
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 71)
    assert manager(user, rid)["state"] == "running"
    end(user, ws, a)
    assert manager(user, rid)["state"] == "ready"
    sync(user, ws)
    second = attempts(user, rid)[-1]
    assert (second["n"], second["fence"]) == (2, 2)
    packet = json.loads(second["packet"])
    assert [w["kind"] for w in packet["information"]["woken_by"]] == ["go"]
    assert [d["text"] for d in packet["instructions"]["decisions"]] == ["Use the main branch."]


@pytest.mark.parametrize("outcome,cause,state,for_owner,waits,failures", [
    ("waiting", None, "waiting", None, None, 0),
    ("waiting", "turn", "waiting", None, None, 0),
    ("waiting", "window", "waiting", None, "window", 1),
    ("waiting", "compacted", "ready", None, None, 2),
    ("done", None, "done", "done", None, 0),
    ("needs_owner", None, "needs_owner", "asked", None, 0),
    ("failed", "time", "ready", None, None, 2),
    ("failed", "lost", "ready", None, None, 2),
    ("stopped", "owner", "waiting", None, None, 1),
    ("stopped", "restart", "ready", None, None, 2),
    ("stopped", "account", "ready", None, None, 1),
    ("stopped", "side", "ready", None, None, 1),
    ("refused", "place", "waiting", None, "place", 1),
    ("refused", "window", "waiting", None, "window", 1),
    ("refused", "paid", "waiting", None, "window", 1),
    ("refused", "reserve", "waiting", None, "window", 1),
    ("refused", "side", "ready", None, None, 2),
    ("refused", "busy", "ready", None, None, 1),
    # the lag's refusals, given back, and the owner's, kept for the Resume
    ("refused", "off", "ready", None, None, 1),
    ("refused", "held", "ready", None, None, 1),
    ("refused", "go", "ready", None, None, 1),
    ("refused", "closed", "ready", None, None, 1),
    ("refused", "fence", "needs_owner", "refused", None, 1),
    ("refused", "packet", "needs_owner", "refused", None, 1),
    ("refused", "managed", "needs_owner", "refused", None, 1),
    ("refused", "account", "needs_owner", "refused", None, 1),
])
def test_each_end_leaves_its_manager_as_its_outcome_and_cause_say(env, ws, outcome, cause, state, for_owner, waits,
                                                                  failures):
    """Each outcome and cause, the manager's state, why it waits for its owner, what it waits for, and its
    failures in a row from one before it: one more at a failure, a compaction, a restart or a side refused; nought at an
    end the attempt reached itself; as they were at every other."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET failures = 1 WHERE user_id = ?", (user["id"],))
    if "window" in (outcome, cause):
        ws.room["window"] = "spent"     # the page's room as the workspace measured it: else the sweep wakes it
    note = "A word for you." if outcome == "needs_owner" else None
    end(user, ws, a, outcome, cause, note=note, waits_until=T0 + 9999 if waits == "window" else None)
    m = manager(user, rid)
    assert (m["state"], m["for_owner"], m["waits"]) == (state, for_owner, waits)
    gave_back = state == "ready"
    kinds = [w["kind"] for w in json.loads(m["wakes"])]
    if gave_back:
        assert kinds[0] == "plan" and kinds[-1] in ("plan", "restart")
    if (waits in ("place", "window") and outcome == "refused") or for_owner == "refused":
        # kept for the next attempt, which a change, a reset or the owner's Resume wakes
        assert kinds == ["plan"] and m["woken"] == 0
    if waits == "window":
        assert m["waits_until"] == T0 + 9999   # its reset, where the end gives one
    assert m["failures"] == failures


def test_three_failures_in_a_row_need_the_owner_and_an_end_the_attempt_reached_itself_counts_them_again(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    for i, (outcome, cause) in enumerate([("failed", "time"), ("waiting", "compacted")]):
        end(user, ws, a, outcome, cause)
        assert manager(user, rid)["failures"] == i + 1
        a = run_next(user, ws, rid)
    end(user, ws, a)                    # waiting, no cause: its own end, the count to nought
    assert manager(user, rid)["failures"] == 0
    for i in range(3):
        with store.connect() as db:
            controller._wake(db, user["id"], rid, "go", T0 + 80 + i)
        a = run_next(user, ws, rid)
        end(user, ws, a, "failed", "error")
    m = manager(user, rid)
    assert (m["state"], m["for_owner"], m["failures"]) == ("needs_owner", "failures", 3)
    n = len(attempts(user, rid))
    sync(user, ws)
    assert len(attempts(user, rid)) == n        # none spent while it needs the owner
    assert ("controller-manager:" + rid, "controller-manager") in controller.alerts(user["id"])
    controller.resume(user, rid, now=T0 + 200)
    m = manager(user, rid)
    assert (m["state"], m["for_owner"], m["failures"]) == ("ready", None, 0)
    assert ("controller-manager:" + rid, "controller-manager") not in controller.alerts(user["id"])


def test_no_attempt_is_queued_while_the_controller_is_off_its_requirement_held_or_waiting_for_the_go(env, ws):
    user = {"id": "u1"}
    on(user, confirm_plans=True)
    _, rid = managed(user)
    for _ in range(3):
        sync(user, ws)
    assert attempts(user) == []                 # waiting for the owner's go
    request = next(r for r in controller.view(user)["requests"])
    controller.go(user, request["id"], now=T0 + 30)
    controller.hold(user, rid, True, now=T0 + 31)
    for _ in range(3):
        sync(user, ws)
    assert attempts(user) == []                 # held
    controller.set_state(user, "off", now=T0 + 32)
    controller.hold(user, rid, False, now=T0 + 33)
    for _ in range(3):
        sync(user, ws)
    assert attempts(user) == []                 # off
    controller.set_state(user, "on", now=T0 + 34)
    for _ in range(2):
        sync(user, ws)
    [a] = attempts(user)
    assert [w["kind"] for w in json.loads(a["woken_by"])["wakes"]] == ["plan", "go", "release"]


def test_one_attempt_at_a_time_and_the_manager_woken_first_goes_first(env, ws):
    user = {"id": "u1"}
    on(user, cap=2)
    _, first = managed(user, now=T0 + 5)
    _, second = managed(user, now=T0 + 7)
    ws.managers_fail = workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    assert (a["requirement_id"], a["state"]) == (first, "queued")     # sent, its request lost: on no page
    for _ in range(2):
        sync(user, ws)                          # ATTEMPTS_AT_ONCE alone holds the other back while no page lists it
    assert len(attempts(user)) == 1
    ws.managers_fail = None
    sync(user, ws)                              # sent again, and taken
    ws.listed[a["id"]] = listed(a)
    for _ in range(3):
        sync(user, ws)
    assert len(attempts(user)) == 1             # its lease held: the other waits
    end(user, ws, a)
    sync(user, ws)
    assert [x["requirement_id"] for x in attempts(user)] == [first, second]


def test_past_attempts_a_day_a_ready_manager_waits_for_the_budget(env, ws, monkeypatch):
    monkeypatch.setattr(controller, "ATTEMPTS_A_DAY", 1)
    user = {"id": "u1"}
    rid, a = running(user, ws)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    end(user, ws, a)
    for _ in range(2):
        sync(user, ws)
    m = manager(user, rid)
    assert (m["state"], m["waits"], len(attempts(user))) == ("ready", "budget", 1)
    monkeypatch.setattr(controller, "ATTEMPTS_A_DAY", 2)
    sync(user, ws)
    assert len(attempts(user)) == 2 and manager(user, rid)["waits"] is None


def test_once_the_day_s_attempts_fall_below_their_most_no_manager_waits_for_the_budget(env, ws, monkeypatch):
    """Past ATTEMPTS_A_DAY each ready manager waits for the budget; once the
    count falls, none waits for it any more: the one queued, every other ready one, and one held meanwhile, which the
    queue passes by."""
    monkeypatch.setattr(controller, "ATTEMPTS_A_DAY", 1)
    user = {"id": "u1"}
    rid, a = running(user, ws, cap=3)
    _, other = managed(user, now=T0 + 40)
    _, held = managed(user, now=T0 + 45)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    end(user, ws, a)
    for _ in range(2):
        sync(user, ws)
    assert [manager(user, r)["waits"] for r in (rid, other, held)] == ["budget"] * 3
    controller.hold(user, held, True, now=T0 + 80)
    monkeypatch.setattr(controller, "ATTEMPTS_A_DAY", 2)
    for _ in range(2):                          # the first takes the hold's version to the workspace
        sync(user, ws)
    assert [x["requirement_id"] for x in attempts(user)] == [rid, other]
    assert [manager(user, r)["waits"] for r in (rid, other, held)] == [None, None, None]


def test_an_attempt_a_whole_page_no_longer_lists_is_ended_by_the_app_and_its_own_end_takes_that_s_place(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room["window"] = "spent"
    ws.listed.clear()                           # the workspace's record put aside: it holds the attempt no more
    controller.set_state(user, "stopped", now=T0 + 70)     # so that no attempt is queued meanwhile
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["state"], got["outcome"], got["cause"]) == ("ended", "failed", "unlisted")
    m = manager(user, rid)
    assert (m["state"], m["failures"]) == ("ready", 1)
    assert [w["kind"] for w in json.loads(m["wakes"])] == ["plan", "restart"]
    # the workspace's own end comes after all: its word, while no newer attempt holds the lease
    ws.listed[a["id"]] = ended(a, "waiting", None)
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["outcome"], got["cause"], got["late"]) == ("waiting", None, 0)
    m = manager(user, rid)
    assert (m["state"], m["failures"], json.loads(m["wakes"]), m["woken"]) == ("waiting", 0, [], 0)


def test_an_attempt_s_own_end_after_a_newer_one_holds_the_lease_is_kept_as_late_and_changes_no_manager(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.listed.clear()
    sync(user, ws)                              # unlisted: ended by the app, its manager ready
    second = run_next(user, ws, rid)
    before = manager(user, rid)
    ws.listed[a["id"]] = ended(a, "done", None)
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["outcome"], got["cause"], got["late"]) == ("failed", "unlisted", 1)
    assert [e["payload"]["attempt"] for e in events_of(user, "attempt.late")] == [a["id"]]
    after = manager(user, rid)
    assert (after["state"], after["lease"], after["for_owner"]) == (before["state"], second["id"], None)


def test_an_end_the_app_fails_to_take_ends_the_read_and_past_untaken_most_is_ended_by_the_app(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    with store.connect() as db:
        seq = controller._row(db, user["id"])["seq"]
    for n in range(1, controller.UNTAKEN_MOST):
        ws.pages.append({"seq": seq + 100, "more": False, "actions": [], "tasks": [],
                         "attempts": [ended(a, "waiting", None, refused=["not a cause"])]})
        sync(user, ws)
        got = attempts(user, rid)[0]
        assert (got["state"], got["untaken"]) == ("running", n)
        with store.connect() as db:
            assert controller._row(db, user["id"])["seq"] == seq      # the page comes again whole
    ws.pages.append({"seq": seq + 100, "more": False, "actions": [], "tasks": [],
                     "attempts": [ended(a, "waiting", None, refused=["not a cause"])]})
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["state"], got["outcome"], got["cause"], got["untaken"]) == ("ended", "failed", "untaken", 0)
    with store.connect() as db:
        assert controller._row(db, user["id"])["seq"] == seq + 100


def _start_of(a, rid, **over):
    """A start an attempt made, as the workspace's record gives it: its attempt, fence and the task's text."""
    return dict(dict(start(rid, session=None, at=T0 + 55), attempt=a["id"], fence=a["fence"],
                     text="Write the line."), **over)


def test_a_start_of_an_old_attempt_or_another_requirement_s_is_kept_flagged_and_moves_no_manager(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    _, other = managed(user, now=T0 + 40)
    good = _start_of(a, rid)
    old = _start_of(a, rid, fence=a["fence"] - 1)
    theirs = _start_of(a, other)
    got = controller.take(user["id"], record(good, old, theirs), now=T0 + 60)
    assert (got["taken"], got["flagged"]) == (1, 2)
    from test_controller import task_row
    assert task_row(good["task"])["flag"] is None
    assert task_row(good["task"])["text"] == "Write the line." and task_row(good["task"])["attempt_id"] == a["id"]
    assert task_row(old["task"])["flag"] == "not its manager's: its attempt's fence is old"
    assert task_row(theirs["task"])["flag"] == "not its manager's: its attempt is another requirement's"
    assert manager(user, rid)["lease"] == a["id"]


def test_the_controller_s_own_start_stop_and_continue_on_a_managed_requirement_are_flagged(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    own_start = start(rid, session=None, at=T0 + 55)
    controller.take(user["id"], record(own_start), now=T0 + 60)
    from test_controller import task_row
    assert task_row(own_start["task"])["flag"] == "started by the Manager, for a requirement its manager keeps"
    assert task_row(own_start["task"])["text"] is None
    mine = _start_of(a, rid)
    controller.take(user["id"], record(mine), now=T0 + 61)
    controller.take(user["id"], record(act("stop", mine["task"], at=T0 + 62)), now=T0 + 63)
    assert task_row(mine["task"])["flag"] == "stopped by the Manager, for a requirement its manager keeps"
    # and its continue
    other = _start_of(a, rid)
    controller.take(user["id"], record(other), now=T0 + 64)
    controller.take(user["id"], record(act("continue", other["task"], at=T0 + 65)), now=T0 + 66)
    # (its start past the share is flagged first: the continue's words are added to the flag)
    assert task_row(other["task"])["flag"].split("; ")[-1] == \
        "continued by the Manager, for a requirement its manager keeps"


def test_a_stop_by_the_manager_s_ended_attempt_after_the_next_is_queued_is_taken_and_flagged(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    mine = _start_of(a, rid)
    controller.take(user["id"], record(mine), now=T0 + 60)
    end(user, ws, a)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    second = run_next(user, ws, rid)
    stop = dict(act("stop", mine["task"], at=T0 + 75), attempt=a["id"], fence=a["fence"])
    got = controller.take(user["id"], record(stop), now=T0 + 80)
    assert got["flagged"] == 1
    from test_controller import task_row
    assert task_row(mine["task"])["flag"] == "a stop by its manager's ended attempt"
    assert manager(user, rid)["lease"] == second["id"]
    stale = dict(act("continue", mine["task"], at=T0 + 76), attempt=a["id"], fence=a["fence"])
    controller.take(user["id"], record(stale), now=T0 + 81)
    assert "not its manager's: its attempt's fence is old" in task_row(mine["task"])["flag"]


def test_an_attempt_s_stop_and_continue_leave_the_day_s_last_lines_to_the_controller(env, ws, monkeypatch):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    mine = _start_of(a, rid)
    controller.take(user["id"], record(mine), now=T0 + 60)
    used = len([e for e in events_of(user) if e["kind"] in controller.RECORD_KINDS])
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", used + controller.LINES_KEPT)
    cont = dict(act("continue", mine["task"], at=T0 + 70), attempt=a["id"], fence=a["fence"])
    got = controller.take(user["id"], record(cont), now=T0 + 71)
    assert got["refused"] == 1
    monkeypatch.setattr(controller, "RECORD_LINES_A_DAY", used + controller.LINES_KEPT + 1)
    again = dict(act("continue", mine["task"], at=T0 + 72), attempt=a["id"], fence=a["fence"])
    assert controller.take(user["id"], record(again), now=T0 + 73)["taken"] == 1


def _task_state(t, state, changed, **over):
    return dict({"id": t["task"], "state": state, "waits_until": None, "ended_at": None, "checks": None, "why": None,
                 "checking": False, "needs_owner": False, "changed": changed, "stopped_by": None}, **over)


def test_a_task_s_change_wakes_its_manager_but_a_round_alone_and_its_own_attempt_s_stop_do_not(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "working", 5)      # the stand-in's pages list it open, as the contract has it
    end(user, ws, a)
    assert manager(user, rid)["state"] == "waiting"

    def take_state(**over):
        controller.take(user["id"], record(tasks_=[_task_state(t, **over)]), now=T0 + 70)
        return manager(user, rid)
    assert take_state(state="working", changed=5)["woken"] == 0           # nothing moved
    assert take_state(state="working", changed=6)["woken"] == 0           # moved, but into a state that wakes none
    m = take_state(state="idle", changed=7)
    assert (m["woken"], [(w["kind"], w["task"]) for w in json.loads(m["wakes"])]) == (1, [("task", t["task"])])
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    assert take_state(state="idle", changed=7)["woken"] == 0             # the same change again
    assert take_state(state="idle", changed=7, needs_owner=True)["woken"] == 1     # needs its owner now
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    m = take_state(state="stopped", changed=9, ended_at=T0 + 69, stopped_by=a["id"])
    assert m["woken"] == 0                                                # its own attempt stopped it


def test_a_decision_wakes_the_manager_it_is_on_and_one_on_none_only_those_with_a_task_open(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "working", 5)
    end(user, ws, a)
    _, idle = managed(user, now=T0 + 61)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    controller.decide(user, "Keep the tests green.", now=T0 + 70)
    assert (manager(user, rid)["woken"], manager(user, idle)["woken"]) == (1, 0)
    controller.take(user["id"], record(question("Which branch?", requirement=idle)), now=T0 + 71)
    [q] = [x for x in controller.view(user)["questions"] if x["state"] == "open"]
    controller.decide(user, "Main.", q["id"], now=T0 + 72)
    assert manager(user, idle)["woken"] == 1


def test_the_sweep_wakes_a_manager_cut_off_by_its_window_once_its_reset_has_passed(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room["window"] = "spent"
    reset = int(time.time()) + 3600
    end(user, ws, a, "waiting", "window", waits_until=reset)
    assert (manager(user, rid)["state"], manager(user, rid)["waits"]) == ("waiting", "window")
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET waits_until = ? WHERE user_id = ?",
                   (int(time.time()) - controller.CONTINUE_AFTER - 1, user["id"]))
    sync(user, ws)
    m = manager(user, rid)
    assert m["waits"] is None and [w["kind"] for w in json.loads(m["wakes"]) if w["kind"] == "reset"] in (
        ["reset"], []) and m["state"] in ("ready", "running")
    assert any(w["kind"] == "reset" for w in json.loads(m["wakes"])) or attempts(user, rid)[-1]["n"] == 2


def test_a_manager_waiting_for_its_window_with_no_reset_known_is_woken_when_the_room_opens(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room["window"] = "spent"
    end(user, ws, a, "refused", "window")
    sync(user, ws)
    assert manager(user, rid)["waits"] == "window"
    ws.room["window"] = "open"
    sync(user, ws)
    m = manager(user, rid)
    assert m["waits"] is None and m["state"] in ("ready", "running")


def test_a_page_whose_room_is_not_measured_yet_meets_no_wait_for_the_window(env, ws):
    """The app's half: a workspace that has not measured its room since it
    started gives no window, which the app takes as saying nothing of it: a manager waiting for its window with no
    reset known is not woken by such a page."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room.update(window="spent", capped_since=T0 + 50)
    end(user, ws, a, "refused", "window")
    sync(user, ws)
    assert manager(user, rid)["waits"] == "window"
    ws.room.update(window=None, capped_since=None)      # the workspace started again: its room not measured yet
    sync(user, ws)
    m = manager(user, rid)
    assert (m["waits"], m["woken"]) == ("window", 0)
    # the app keeps the room of the newest page that measured one, its capped start with it
    room = controller.view(user)["controller"]["room"]
    assert (room["window"], room["capped_since"]) == ("spent", T0 + 50)


def test_a_managed_task_waiting_for_its_reset_wakes_its_manager_once_for_each_reset_and_it_resumes(env, ws):
    """A task of a manager's waiting for its
    account's reset wakes its manager (`reset`) once that reset and CONTINUE_AFTER have passed, once for each reset
    (`reset_at`), so that an attempt that saw its task stopped at the limit and ended waiting has it resumed then."""
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "working", 5)
    end(user, ws, a)
    reset = int(time.time()) - controller.CONTINUE_AFTER - 30
    # the task stopped at its account's limit, waiting for that reset, its change number not moved: the only wake that
    # can come is the sweep's
    ws.open[t["task"]] = _task_state(t, "waiting", 5, waits_until=reset)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    sync(user, ws)
    m = manager(user, rid)
    assert ([w["kind"] for w in json.loads(m["wakes"])], m["reset_at"]) == (["reset"], reset)
    b = run_next(user, ws, rid)                     # it resumes: its next attempt, given that wake
    assert [w["kind"] for w in json.loads(b["woken_by"])["wakes"]] == ["reset"]
    end(user, ws, b)
    sync(user, ws)
    assert manager(user, rid)["woken"] == 0         # the same reset wakes it no more
    ws.open[t["task"]] = _task_state(t, "waiting", 5, waits_until=reset + 5)     # a later reset of its account
    sync(user, ws)
    m = manager(user, rid)
    assert ([w["kind"] for w in json.loads(m["wakes"])], m["reset_at"]) == (["reset"], reset + 5)


def test_resume_answers_409_where_it_may_not_and_with_words_reaches_that_manager_s_packet_alone(env, ws):
    user = {"id": "u1"}
    on(user, cap=3)
    request = controller.add_request(user, REQUEST, now=T0 + 3)
    plain = uid()
    controller.take(user["id"], record(plan(request["id"], "Make the README say hello.", rid=plain)), now=T0 + 4)
    with pytest.raises(controller.agents.AgentError) as e:
        controller.resume(user, plain)
    assert e.value.status == 409
    _, rid = managed(user, now=T0 + 6)
    _, other = managed(user, now=T0 + 8)
    sync(user, ws)
    sync(user, ws)
    a = attempts(user, rid)[0]
    with pytest.raises(controller.agents.AgentError) as e:
        controller.resume(user, rid)
    assert e.value.status == 409 and "running" in str(e.value)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    end(user, ws, a, "needs_owner", None, note="Which file?")
    with pytest.raises(controller.agents.AgentError) as e:
        controller.resume(user, rid, "x" * (controller.DECISION_MAX + 1))
    assert e.value.status == 400
    view = controller.resume(user, rid, "The README at the root.", now=T0 + 90)
    assert view["manager"]["state"] == "ready" and view["manager"]["note"]["text"] == "Which file?"
    [d] = [d for d in controller.view(user)["decisions"] if d["requirement"] == rid]
    assert d["text"] == "The README at the root."
    assert [e["payload"]["from"] for e in events_of(user, "manager.resumed")] == ["needs_owner"]
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, user["id"])[0])
        body = controller.mirror_body(db, user["id"])[0]
    assert "The README at the root." not in json.dumps(packet) and "The README at the root." not in json.dumps(body)
    assert packet["instructions"]["more_decisions"] == 0
    # the other manager, woken first, goes first: its packet never holds the words; then this one's next attempt is
    # given them under instructions, with what woke it, and never the note
    o = run_next(user, ws, other)
    assert "The README at the root." not in o["packet"]
    end(user, ws, o)
    second = run_next(user, ws, rid)
    got = json.loads(second["packet"])
    assert got["instructions"]["decisions"][0]["text"] == "The README at the root."
    assert [w["kind"] for w in got["information"]["woken_by"]] == ["resume", "decision"]
    assert "Which file?" not in json.dumps([x["packet"] for x in attempts(user)])


def test_a_requirement_whose_plan_the_workspace_no_longer_holds_needs_its_owner_and_resume_is_refused(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    ws.answer = "managed"
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    assert (a["outcome"], a["cause"]) == ("refused", "managed")
    m = manager(user, rid)
    assert (m["state"], m["for_owner"]) == ("needs_owner", "refused")
    with pytest.raises(controller.agents.AgentError) as e:
        controller.resume(user, rid)
    assert e.value.status == 409
    assert [e["payload"]["cause"] for e in events_of(user, "attempt.refused")] == ["managed"]


def test_an_attempt_refused_for_another_running_is_given_its_wakes_back_and_queued_again(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    ws.answer = "busy"
    sync(user, ws)
    sync(user, ws)
    m = manager(user, rid)
    assert (m["state"], [w["kind"] for w in json.loads(m["wakes"])]) == ("ready", ["plan"])
    ws.answer = None
    for _ in range(2):
        sync(user, ws)
    assert [x["state"] for x in attempts(user)] == ["ended", "sent"]


def test_an_attempt_s_packet_gives_the_owner_s_words_as_instructions_and_every_model_s_word_as_information(env, ws):
    """The instructions hold the owner's words alone (the requirement's words, acceptance and
    checks, and the decisions on it and on none, never one on another requirement); a task's text an attempt wrote
    reaches the next attempt under information, marked as a model's, as does the title; a question answered is beside
    its decision under information; no note is ever in a packet."""
    user = {"id": "u1"}
    on(user, cap=3)
    request = controller.add_request(user, REQUEST, now=T0 + 3,
                                     checks=[{"repo": "demo", "name": "unit", "command": "pytest -q"}])
    rid, other = uid(), uid()
    for r, at in ((rid, T0 + 4), (other, T0 + 5)):
        p = dict(plan(request["id"], "Make the README say hello.", rid=r, acceptance="its first line is hello.",
                      at=at), share=2, checks=[1])
        assert controller.take(user["id"], record(p), now=at + 1)["taken"] == 1
    controller.take(user["id"], record(question("Which line?", requirement=rid),
                                       question("And there?", requirement=other)), now=T0 + 7)
    qs = {q["requirement"]: q for q in controller.view(user)["questions"]}
    controller.decide(user, "The first line.", qs[rid]["id"], now=T0 + 8)
    controller.decide(user, "Never this one.", qs[other]["id"], now=T0 + 9)
    controller.decide(user, "Keep commits small.", now=T0 + 10)
    sync(user, ws)
    sync(user, ws)
    a = attempts(user, rid)[0]
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    t = _start_of(a, rid, text="Write hello on line one. Then stop.")
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "idle", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "idle", 5)
    end(user, ws, a, "waiting", None, note="A note for the owner alone.")
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    o = run_next(user, ws, other)       # woken by its plan before this one's go: it goes first
    assert "Never this one." in o["packet"] and "The first line." not in o["packet"]
    end(user, ws, o)
    b = run_next(user, ws, rid)
    got = json.loads(b["packet"])
    assert got["header"] == controller.MANAGER_HEADER and got["share"] == 2 and got["requirement"] == rid
    ins = got["instructions"]
    assert ins["by"] == "owner" and ins["words"] == {"text": "Make the README say hello.", "by": "owner"}
    assert ins["acceptance"] == {"text": "its first line is hello.", "by": "owner"}
    assert ins["checks"]["commands"] == [{"id": 1, "repo": "demo", "name": "unit", "command": "pytest -q"}]
    assert [d["text"] for d in ins["decisions"]] == ["Keep commits small.", "The first line."]
    info = got["information"]
    assert info["title"] == {"text": "Say hello", "named_by": "model"}
    assert info["texts"] == [{"task": t["task"], "text": "Write hello on line one. Then stop.", "by": "model"}]
    assert [q["text"] for q in info["answered_questions"]] == ["Which line?"]
    assert [x["n"] for x in info["attempts"]] == [1] and info["attempts"][0]["outcome"] == "waiting"
    flat = json.dumps(ins)
    assert "Write hello" not in flat and "Never this one." not in json.dumps(got)
    assert "A note for the owner alone." not in json.dumps([x["packet"] for x in attempts(user)])
    assert len(controller._canon({k: got[k] for k in ("schema", "nonce", "header", "attempt", "requirement", "share",
                                                         "controller")})) <= controller.BUDGETS["fixed"]


def test_a_task_s_text_whose_cut_would_end_in_a_secret_s_shape_is_cut_shorter(env):
    text = "x" * (controller.TASK_TEXT_SHOWN - 10) + " ghp_" + "A" * 36
    got = controller._text_shown({"id": "t", "text": text, "text_cut": 0, "text_hidden": 0})
    assert got["cut"] is True and len(got["text"].encode()) <= controller.TASK_TEXT_SHOWN
    assert controller.tasks.secret_shape(got["text"]) is None


def test_a_packet_that_cannot_be_built_queues_nothing_and_its_manager_needs_the_owner(env, ws, monkeypatch):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    monkeypatch.setattr(controller, "PACKET_MAX", 100)
    sync(user, ws)
    sync(user, ws)
    assert attempts(user) == []
    m = manager(user, rid)
    assert (m["state"], m["for_owner"]) == ("needs_owner", "packet")


def test_what_woke_an_attempt_comes_first_however_many_tasks_fill_its_packet(env, ws, monkeypatch):
    """The observed part gives what woke the attempt before its
    tasks, so that however many tasks fill it, what the attempt is to act on is not crowded out: the tasks are cut."""
    import secrets
    user = {"id": "u1"}
    on(user, cap=10)
    _, rid = managed(user, share=10)
    for i in range(20):                             # twenty tasks of its requirement, which the pages list working
        st = start(rid, session=secrets.token_hex(6), at=T0 + 7 + i)
        controller.take(user["id"], record(st), now=T0 + 30)
        ws.open[st["task"]] = _task_state(st, "working", 1)
    for i in range(controller.WAKES_KEPT + 3):
        with store.connect() as db:
            controller._wake(db, user["id"], rid, "task", T0 + 6 + i, task=uid())
    monkeypatch.setitem(controller.BUDGETS, "observed", 2600)
    more = manager(user, rid)["more_wakes"]         # the wakes past WAKES_KEPT, counted
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    info = json.loads(a["packet"])["information"]
    assert (len(info["woken_by"]), info["more_woken"]) == (controller.WAKES_KEPT, more)   # every kept wake given
    assert info["woken_by"][0]["kind"] == "plan"
    assert info["more_tasks"] > 0 and len(info["tasks"]) + info["more_tasks"] == 20       # the tasks cut instead


def test_the_controller_s_packet_gives_a_managed_requirement_s_state_and_counts_and_none_of_its_tasks(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "idle", 5)]), now=T0 + 60)
    with store.connect() as db:
        packet = json.loads(controller.build_packet(db, user["id"])[0])
    [item] = packet["information"]["requirements"]
    assert (item["share"], item["manager"]) == (2, "running")
    assert item["counts"]["idle"] == 1 and sum(item["counts"].values()) == 1
    assert "tasks" not in item and "more_tasks" not in item
    assert "Write the line." not in json.dumps(packet)


def test_the_page_shows_a_requirement_s_share_and_manager_a_task_s_text_and_its_attempts(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "idle", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "idle", 5)
    end(user, ws, a, "needs_owner", None, note="Look at the README.")
    view = controller.requirement_view(user, rid)
    assert view["share"] == 2
    m = view["manager"]
    assert (m["state"], m["for_owner"], m["attempts"], m["failures"]) == ("needs_owner", "asked", 1, 0)
    assert m["last"]["outcome"] == "needs_owner"
    assert (m["note"]["text"], m["note"]["by"]) == ("Look at the README.", "manager")
    [task] = view["tasks"]
    assert task["text"] == {"text": "Write the line.", "cut": False, "hidden_removed": 0, "by": "manager"}
    assert task["attempt"] == a["id"]
    listed_ = controller.attempts_of(user, rid)["attempts"]
    assert [x["id"] for x in listed_] == [a["id"]] and "packet" not in listed_[0]
    one = controller.attempt_of(user, rid, a["id"])
    assert one["packet"]["schema"] == "ade.manager.v1" and one["note"]["text"] == "Look at the README."
    with pytest.raises(controller.agents.AgentError):
        controller.attempt_of(user, rid, uid())


def test_the_board_shows_a_managed_requirement_needing_its_owner_or_done_in_now(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a, "needs_owner", None, note="Look.")
    [row] = [r for r in controller.board_rows(user["id"]) if r["id"] == rid]
    assert (row["phase"], row["state"]) == ("now", "manager_needs_you")
    controller.resume(user, rid, now=T0 + 90)
    b = run_next(user, ws, rid)
    end(user, ws, b, "done", None)
    [row] = [r for r in controller.board_rows(user["id"], board=True) if r["id"] == rid]
    assert (row["phase"], row["state"]) == ("now", "manager_done")
    words = [e["words"] for e in row["events"]]
    assert "Its manager's attempt 2 ended: done" in words


def test_a_long_wait_for_a_place_and_the_controller_s_capped_start_are_told_in_fixed_words(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    end(user, ws, a, "waiting", None, refused=["cap"], refused_count=1)
    m = manager(user, rid)
    assert (m["state"], m["waits"]) == ("waiting", "cap")
    sync(user, ws)
    assert manager(user, rid)["told_at"] is None        # not yet an hour
    since = int(time.time()) - controller.WAIT_TOLD
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET waits_since = ? WHERE user_id = ?", (since, user["id"]))
    assert not any(k.startswith("controller-manager-waits") for k, _ in controller.alerts(user["id"]))
    sync(user, ws)                                      # the sweep marks it told (`told_at`), once for this wait
    assert manager(user, rid)["told_at"] >= since
    got = controller.alerts(user["id"])
    assert (f"controller-manager-waits:{rid}:{since}", "controller-manager-waits") in got
    ws.room["capped_since"] = int(time.time()) - controller.WAIT_TOLD
    sync(user, ws)
    got = controller.alerts(user["id"])
    assert (f"controller-cap-waits:{ws.room['capped_since']}", "controller-cap-waits") in got
    for kind in ("controller-manager", "controller-manager-waits", "controller-cap-waits"):
        title, body, _ = push.WORDS[kind]
        assert rid not in title + body and "Say hello" not in title + body
        push.words(kind, key="k")


def test_the_managers_route_s_failure_is_kept_told_and_the_attempt_sent_again(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    ws.managers_fail = workspace_client.WorkspaceCallError(502, "The workspace answered 404.")
    sync(user, ws)
    sync(user, ws)
    with store.connect() as db:
        c = controller._row(db, user["id"])
    assert c["managers_error"] == controller.MANAGERS_NO_ROUTE
    assert any(k.startswith("controller-look:") for k, _ in controller.alerts(user["id"]))
    [a] = attempts(user)
    assert (a["state"], a["sent_at"] is not None) == ("queued", True)
    with store.connect() as db:
        since = controller._row(db, user["id"])["managers_error_at"]
    # a workspace without the route is told once, not again each day it lasts, as another failure is
    later = since + controller.TOLD + 1
    assert not any(k.startswith("controller-look:") for k, _ in controller.alerts(user["id"], later))
    ws.managers_fail = workspace_client.WorkspaceCallError(502, "The workspace answered 500.")
    sync(user, ws)
    with store.connect() as db:
        c = controller._row(db, user["id"])
    assert c["managers_error"] == "The workspace answered 500."
    assert any(k.startswith("controller-look:") for k, _ in controller.alerts(user["id"],
                                                                              c["managers_error_at"] + controller.TOLD))
    ws.managers_fail = None
    sync(user, ws)
    assert [x["state"] for x in attempts(user)] == ["sent"]
    with store.connect() as db:
        assert controller._row(db, user["id"])["managers_error"] is None


def test_an_attempt_never_sent_is_ended_by_the_app_at_its_requirement_s_close_and_never_sent(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    sync(user, ws)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        c = controller._row(db, user["id"])
        controller._queue(db, user["id"], c["version"], False, T0 + 50)
    [a] = attempts(user)
    assert (a["state"], a["sent_at"]) == ("queued", None)
    controller.close(user, rid, "stopped", now=T0 + 60)
    sync(user, ws)
    [a] = attempts(user)
    assert (a["state"], a["outcome"], a["cause"]) == ("ended", "stopped", "closed")
    assert all(b["attempt"] is None for b in ws.managers)
    assert manager(user, rid)["state"] == "stopped"


def test_the_managers_route_at_its_largest_is_within_managers_max(env, ws):
    """The managers' route as _managers_body builds it: REQUIREMENTS_OPEN_MOST open managers
    at their longest values (a state of `needs_owner`, waits `sessions`, every number at 2**53 less one) and the attempt
    waiting to be taken, its packet at PACKET_MAX, within MANAGERS_MAX."""
    user = {"id": "u1"}
    on(user)
    big = (1 << 53) - 1
    with store.connect() as db:
        controller._ensure(db, user["id"], T0)
        db.execute("UPDATE ade_controllers SET managers_version = ? WHERE user_id = ?", (big, user["id"]))
        rids = [uid() for _ in range(controller.REQUIREMENTS_OPEN_MOST)]
        for rid in rids:
            db.execute("INSERT INTO ade_controller_requirements (id, user_id, request_id, title, words, repos, "
                       "planned_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (rid, user["id"], uid(), "t", "w", "[]", T0, T0, T0))
            db.execute("INSERT INTO ade_controller_managers (user_id, requirement_id, share, fence, waits, waits_until, "
                       "attempts, for_owner, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (user["id"], rid, controller.CAP_MOST, big, "sessions", big, big, "failures", T0, T0))
        db.execute("INSERT INTO ade_controller_attempts (user_id, id, requirement_id, n, fence, state, packet, "
                   "packet_digest, queued_at, updated_at) VALUES (?,?,?,?,?,'queued',?,?,?,?)",
                   (user["id"], uid(), rids[0], big, big, "x" * controller.PACKET_MAX,
                    controller.shapes.tagged_digest(controller.MANAGER_TAG, b"x" * controller.PACKET_MAX), T0, T0))
        body, attempt_id = controller._managers_body(db, user["id"], T0)
    assert body is not None and attempt_id is not None and len(body["managers"]) == controller.REQUIREMENTS_OPEN_MOST
    assert {m["state"] for m in body["managers"]} == {"needs_owner"}
    assert controller._json_size(body) <= controller.MANAGERS_MAX


def test_a_closed_requirement_s_manager_is_stopped_for_good_and_keeps_no_wake(env, ws):
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    controller.close(user, rid, "done", now=T0 + 20)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
        assert controller._wake(db, user["id"], rid, "go", T0 + 21) is False
    m = manager(user, rid)
    assert (m["state"], m["woken"], json.loads(m["wakes"])) == ("stopped", 0, [])
    for _ in range(2):
        sync(user, ws)
    assert attempts(user) == [] and all(x["requirement"] != rid for b in ws.managers for x in b["managers"])


def test_no_attempt_is_queued_while_the_newest_page_lists_one_running(env, ws):
    """An attempt the app ended by its own inference may still run in the workspace."""
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    ghost = {"id": uid(), "requirement_id": uid(), "fence": 1, "n": 1}
    ws.listed[ghost["id"]] = listed(ghost)                  # one the app does not hold, still running there
    for _ in range(3):
        sync(user, ws)
    assert attempts(user) == []
    del ws.listed[ghost["id"]]
    for _ in range(2):
        sync(user, ws)
    assert len(attempts(user)) == 1


def test_a_cut_page_ends_no_attempt_it_does_not_list(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    # a page cut before it (more) lists none of the attempts; the next, not cut, lists it running still
    ws.pages.append({"seq": 10 ** 6, "more": True, "actions": [], "tasks": [], "attempts": []})
    sync(user, ws)
    assert attempts(user, rid)[0]["state"] == "running"


def test_a_start_past_its_manager_s_share_is_kept_and_flagged_and_one_within_it_is_not(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    first, second, third = (_start_of(a, rid) for _ in range(3))
    got = controller.take(user["id"], record(first, second, third), now=T0 + 60)
    assert (got["taken"], got["flagged"]) == (2, 1)
    from test_controller import task_row
    assert [task_row(t["task"])["flag"] for t in (first, second)] == [None, None]
    assert task_row(third["task"])["flag"] == "one more than the 2 sessions its manager may have open at once"


def test_an_attempt_the_app_has_sent_is_sent_again_whatever_its_conditions_now_and_never_ended_by_the_app(env, ws):
    """The workspace may have taken it; its answer, or a page that lists it, says what became of it."""
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    ws.managers_fail = workspace_client.WorkspaceCallError(502, "The workspace answered 500.")
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    assert (a["state"], a["sent_at"] is not None) == ("queued", True)
    controller.close(user, rid, "stopped", now=T0 + 60)
    ws.managers_fail = None
    sync(user, ws)
    [a] = attempts(user)
    assert a["state"] == "sent" and ws.managers[-1]["attempt"]["id"] == a["id"]


def test_no_attempt_is_queued_until_the_workspace_holds_the_owner_s_side_as_the_app_has_it(env, ws):
    """The newest page's `held` is the app's present version, so that the workspace
    judges the attempt by what the app judged it by; a sync that sends a new mirror queues at the next."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a)
    sync(user, ws)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)       # ready: its state moves the app's version on
        version = controller._row(db, user["id"])["version"]
    assert ws.held is not None and ws.held != version            # the workspace holds the side before the wake
    sync(user, ws)
    assert len(attempts(user)) == 1
    sync(user, ws)
    assert len(attempts(user)) == 2


def test_the_workspace_s_own_end_in_an_inferred_end_s_place_takes_back_the_failure_the_inference_counted(env, ws):
    """The manager is left as the workspace's own end leaves it; one that leaves the failures in a row as
    they were (the owner's Stop of its session) leaves them as before the inference."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.listed.clear()
    controller.set_state(user, "stopped", now=T0 + 70)
    sync(user, ws)
    assert manager(user, rid)["failures"] == 1
    ws.listed[a["id"]] = ended(a, "stopped", "owner")
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["outcome"], got["cause"]) == ("stopped", "owner")
    m = manager(user, rid)
    assert (m["failures"], m["state"]) == (0, "waiting")


def test_the_managers_route_goes_at_the_first_sync_and_again_only_when_their_version_moves(env, ws):
    """Sent where the managers' version is not the one the workspace holds, or an attempt waits; so the
    first sync sends it, with no manager, which is what tells the workspace that plans take a share."""
    user = {"id": "u1"}
    on(user)
    sync(user, ws)
    assert [(b["managers"], b["attempt"]) for b in ws.managers] == [([], None)]
    sync(user, ws)
    assert len(ws.managers) == 1                  # held, and nothing moved
    _, rid = managed(user)
    controller.set_state(user, "off", now=T0 + 30)       # so that no attempt is queued: the new manager alone moved it
    sync(user, ws)
    assert [x["requirement"] for x in ws.managers[-1]["managers"]] == [rid] and ws.managers[-1]["attempt"] is None


def test_a_round_of_a_managed_task_s_checks_wakes_its_manager_and_reaches_its_next_packet(env, ws):
    """The `checks` wake, and a managed requirement's checks' results reaching its manager: the round the app takes is
    in the next attempt's packet, its number and whether it passed."""
    user = {"id": "u1"}
    on(user, cap=3)
    request = controller.add_request(user, REQUEST, now=T0 + 3,
                                     checks=[{"repo": "demo", "name": "unit", "command": "pytest -q"}])
    rid = uid()
    p = dict(plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 4), share=2, checks=[1])
    assert controller.take(user["id"], record(p), now=T0 + 5)["taken"] == 1
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "idle", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "idle", 5)
    end(user, ws, a)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    rnd = {"round": 1, "head": "a" * 40, "at": T0 + 65, "passed": True, "results": [{"id": 1, "outcome": "passed"}]}
    controller.take(user["id"], record(tasks_=[_task_state(t, "idle", 5, checks=rnd)]), now=T0 + 66)
    m = manager(user, rid)
    assert (m["woken"], [w["kind"] for w in json.loads(m["wakes"])]) == (1, ["checks"])
    ws.open[t["task"]] = _task_state(t, "idle", 5, checks=rnd)
    b = run_next(user, ws, rid)
    [task] = json.loads(b["packet"])["information"]["tasks"]
    assert task["checks"] == {"round": 1, "passed": True}


@pytest.mark.parametrize("by_its_own", [True, False], ids=["its-own-attempt", "the-owner"])
def test_a_round_its_own_attempt_s_stop_cut_short_wakes_nothing_and_is_kept(env, ws, by_its_own):
    """A task an attempt of
    its own manager stopped wakes nothing, nor does the round that stop cut short, which comes on the page that gives the
    stop; the round is kept all the same, for the next attempt's packet. The same stop by the owner wakes it."""
    user = {"id": "u1"}
    on(user, cap=3)
    request = controller.add_request(user, REQUEST, now=T0 + 3,
                                     checks=[{"repo": "demo", "name": "unit", "command": "pytest -q"}])
    rid = uid()
    p = dict(plan(request["id"], "Make the README say hello.", rid=rid, at=T0 + 4), share=2, checks=[1])
    assert controller.take(user["id"], record(p), now=T0 + 5)["taken"] == 1
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "idle", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "idle", 5)
    end(user, ws, a)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET woken = 0, woken_at = NULL, wakes = '[]' WHERE user_id = ?",
                   (user["id"],))
    rnd = {"round": 1, "head": "a" * 40, "at": T0 + 65, "passed": False,
           "results": [{"id": 1, "outcome": "not run", "why": "its task was stopped"}]}
    stop = _task_state(t, "stopped", 6, checks=rnd, ended_at=T0 + 65, stopped_by=a["id"] if by_its_own else None)
    controller.take(user["id"], record(tasks_=[stop]), now=T0 + 66)
    m = manager(user, rid)
    if by_its_own:
        assert (m["woken"], json.loads(m["wakes"])) == (0, [])
    else:
        assert m["woken"] == 1 and "checks" in [w["kind"] for w in json.loads(m["wakes"])]
    from test_controller import task_row
    assert json.loads(task_row(t["task"])["checks"])["round"] == 1


def test_a_managers_route_past_its_bytes_is_not_sent_and_its_attempt_is_not_held_as_sent(env, ws, monkeypatch):
    """Its bounds keep it within MANAGERS_MAX (the route at its largest, above); one past it, were it made, is said and
    not sent, and its attempt stays one never sent, which the app may end where its conditions stop holding."""
    user = {"id": "u1"}
    on(user)
    _, rid = managed(user)
    most = controller.MANAGERS_MAX
    monkeypatch.setattr(controller, "MANAGERS_MAX", 100)
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    assert (a["state"], a["sent_at"]) == ("queued", None) and ws.managers == []
    monkeypatch.setattr(controller, "MANAGERS_MAX", most)
    sync(user, ws)
    [a] = attempts(user)
    assert a["state"] == "sent" and a["sent_at"] is not None


@pytest.mark.parametrize("cause,waits,met", [("off", "on", "on"), ("stopped", "on", "on"), ("end_all", "on", "on"),
                                             ("held", "release", "release")])
def test_a_stop_by_the_owner_s_side_waits_for_it_to_be_undone_and_its_undoing_wakes_the_manager(env, ws, cause, waits,
                                                                                             met):
    """A stopped manager: waiting for `on` while the controller is not on, for `release` while held; the On,
    or the Release, then meeting the wait and waking it."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    if cause == "held":
        controller.hold(user, rid, True, now=T0 + 65)
    else:
        controller.set_state(user, "off" if cause == "off" else "stopped", now=T0 + 65)
    end(user, ws, a, "stopped", cause)
    m = manager(user, rid)
    assert (m["state"], m["waits"]) == ("ready", waits)
    for _ in range(2):
        sync(user, ws)
    assert len(attempts(user)) == 1                     # its wait holds it
    if met == "release":
        controller.hold(user, rid, False, now=T0 + 80)
    else:
        controller.set_state(user, "on", now=T0 + 80)
    m = manager(user, rid)
    assert m["waits"] is None and met in [w["kind"] for w in json.loads(m["wakes"])]
    run_next(user, ws, rid)


def test_an_off_with_an_on_before_its_end_came_leaves_the_manager_ready_with_no_wait(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    controller.set_state(user, "off", now=T0 + 65)
    controller.set_state(user, "on", now=T0 + 66)
    end(user, ws, a, "stopped", "off")
    m = manager(user, rid)
    assert (m["state"], m["waits"]) == ("ready", None)


def test_an_attempt_refused_for_its_share_waits_for_a_change_and_one_refused_by_the_pacing_says_so(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a, "waiting", None, refused=["share"], refused_count=2)
    m = manager(user, rid)
    assert (m["state"], m["waits"]) == ("waiting", None)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    b = run_next(user, ws, rid)
    end(user, ws, b, "waiting", None, refused=["share", "pace"], refused_count=2, refused_models=["opus"])
    m = manager(user, rid)
    assert (m["waits"], json.loads(m["waits_models"])) == ("pace", ["opus"])


def test_resume_of_a_waiting_manager_and_words_past_their_bytes(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a)
    assert manager(user, rid)["state"] == "waiting"
    with pytest.raises(controller.agents.AgentError) as e:
        controller.resume(user, rid, "\x01" * 1500)        # 1,500 characters, each 5 bytes as the packet holds it
    assert e.value.status == 400 and "past 6144" in str(e.value)
    assert controller.resume(user, rid, now=T0 + 90)["manager"]["state"] == "ready"


def test_an_attempt_s_packet_counts_the_managers_and_the_controller_waiting_for_a_place_in_the_cap(env, ws):
    user = {"id": "u1"}
    on(user, cap=3)
    _, one = managed(user, now=T0 + 5)
    _, two = managed(user, now=T0 + 7)
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET waits = 'cap', waits_since = ?, woken = 0, woken_at = NULL, "
                   "wakes = '[]' WHERE user_id = ? AND requirement_id = ?", (T0 + 8, user["id"], two))
        # the one queued waits for the cap too, a change having woken it: it is not counted among those it may free
        # a place for
        db.execute("UPDATE ade_controller_managers SET waits = 'cap', waits_since = ? WHERE user_id = ? AND "
                   "requirement_id = ?", (T0 + 8, user["id"], one))
    ws.room["capped_since"] = T0 + 9
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    assert a["requirement_id"] == one
    assert json.loads(a["packet"])["information"]["cap"] == {"cap": 3, "open": 0, "waiting": 2}


def test_a_wake_given_back_goes_behind_one_that_came_before_it(env, ws):
    """A wake given back is kept as one that came when the app took that end, so that a manager whose
    attempt was cut short goes behind those woken before it."""
    user = {"id": "u1"}
    on(user, cap=3)
    _, first = managed(user, now=T0 + 5)
    sync(user, ws)
    sync(user, ws)
    [a] = attempts(user)
    ws.listed[a["id"]] = listed(a)
    sync(user, ws)
    _, second = managed(user, now=T0 + 40)          # woken by its plan while the first's attempt runs
    end(user, ws, a, "failed", "error")             # the first's wakes given back, after the second's plan
    with store.connect() as db:
        woke = {r["requirement_id"]: r["woken_at"] for r in
                db.execute("SELECT requirement_id, woken_at FROM ade_controller_managers WHERE user_id = ?",
                           (user["id"],))}
    assert woke[second] < woke[first]
    for _ in range(3):
        sync(user, ws)
    assert [x["requirement_id"] for x in attempts(user)] == [first, second]


def test_an_attempt_s_packet_is_let_go_once_five_newer_were_queued(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws)
    for i in range(controller.ATTEMPTS_SHOWN):
        end(user, ws, a)
        with store.connect() as db:
            controller._wake(db, user["id"], rid, "go", T0 + 70 + i)
        a = run_next(user, ws, rid)
    got = attempts(user, rid)
    assert [x["packet"] is None for x in got] == [True] + [False] * controller.ATTEMPTS_SHOWN
    assert all(x["packet_digest"] for x in got)


def test_a_task_its_own_manager_s_attempt_stopped_wakes_nothing_while_that_attempt_runs(env, ws):
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
    controller.take(user["id"], record(tasks_=[_task_state(t, "stopped", 6, ended_at=T0 + 61, stopped_by=a["id"])]),
                    now=T0 + 62)
    assert manager(user, rid)["woken"] == 0
    end(user, ws, a)
    assert manager(user, rid)["state"] == "waiting"


def test_a_task_its_attempt_started_first_seen_idle_wakes_its_manager(env, ws):
    """A start the page gives with its task already idle (it went idle within one
    read, or the app was down) wakes its manager, as its change number moved since the app last took it, which it
    never had; else its later pages, giving the same change number, would wake none."""
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    t = _start_of(a, rid)
    ws.open[t["task"]] = _task_state(t, "idle", 5)
    ws.listed[a["id"]] = ended(a, "waiting", None)
    ws.pages.append(record(t, tasks_=[_task_state(t, "idle", 5)], seq=10 ** 6,
                           more=False) | {"attempts": [ended(a, "waiting", None)]})
    sync(user, ws)
    m = manager(user, rid)
    assert [(w["kind"], w["task"]) for w in json.loads(m["wakes"])] == [("task", t["task"])]
    assert m["state"] in ("ready", "running")


def test_a_late_end_s_note_is_kept_for_the_owner_beside_the_inferred_end(env, ws):
    """The workspace's own end of an attempt the app had ended by its inference,
    after a newer one holds the lease, keeps its note on the attempt, which the owner is shown; its manager goes by the
    inferred end."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.listed.clear()
    sync(user, ws)
    second = run_next(user, ws, rid)
    ws.listed[a["id"]] = ended(a, "needs_owner", None, note="Look at the README first.")
    sync(user, ws)
    got = attempts(user, rid)[0]
    assert (got["outcome"], got["cause"], got["late"], got["note"]) == ("failed", "unlisted", 1,
                                                                       "Look at the README first.")
    view = controller.requirement_view(user, rid)
    assert view["manager"]["note"]["text"] == "Look at the README first."
    assert view["manager"]["state"] == "running" and manager(user, rid)["lease"] == second["id"]


def test_a_note_reaches_no_packet_no_mirror_and_no_managers_route(env, ws):
    """The note reaches the owner alone: not the controller's packet, the mirror, the
    managers' route's body, nor any attempt's packet (a task's first prompt is the workspace's, its test there)."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a, "needs_owner", None, note="Only for you.")
    controller.resume(user, rid, now=T0 + 90)
    run_next(user, ws, rid)
    with store.connect() as db:
        packet = controller.build_packet(db, user["id"])[0].decode()
        mirror = json.dumps(controller.mirror_body(db, user["id"])[0])
    assert "Only for you." not in packet + mirror + json.dumps(ws.managers) + json.dumps(ws.mirrors)
    assert "Only for you." not in json.dumps([x["packet"] for x in attempts(user)])


def test_resume_refuses_words_by_a_decision_s_rules_in_words_that_agree_with_them(env, ws):
    """Resume's words are refused by the rules a decision's are, in words of their own that agree with "Your
    words"."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a)
    for text, words in (("   ", f"Your words are text of at most {controller.DECISION_MAX} characters."),
                        ("a\ud800b", "Your words hold a character that is not one.")):
        with pytest.raises(agents.AgentError) as refused:
            controller.resume(user, rid, text, now=T0 + 80)
        assert (refused.value.status, str(refused.value)) == (400, words)
    with pytest.raises(agents.AgentError) as refused:
        controller.resume(user, rid, "ghp_" + "a" * 36, now=T0 + 80)
    assert str(refused.value).startswith("Your words hold something shaped like ")


def test_a_change_queues_no_attempt_for_a_manager_waiting_for_its_window(env, ws):
    """A manager waiting for its
    window is not queued for a change that wakes it meanwhile, so that its attempt is not refused at the take, whose
    answer has no reset, losing the one it waits for."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room["window"] = "spent"
    end(user, ws, a, "waiting", "window", waits_until=T0 + 9999)
    with store.connect() as db:
        controller._wake(db, user["id"], rid, "go", T0 + 70)
    for _ in range(3):
        sync(user, ws)
    m = manager(user, rid)
    assert (len(attempts(user)), m["waits"], m["waits_until"], m["woken"]) == (1, "window", T0 + 9999, 1)


def test_a_place_freed_wakes_no_manager_waiting_for_one(env, ws):
    """A manager whose attempt was refused a
    place in the cap waits for a change of its own; another's task ending, which frees a place, wakes it not, that wake
    being a dispatcher's, and there is none: the owner is told of the wait instead."""
    user = {"id": "u1"}
    rid, a = running(user, ws, cap=3)
    _, other = managed(user, now=T0 + 40)
    t = _start_of(a, rid)
    controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
    ws.open[t["task"]] = _task_state(t, "working", 5)
    end(user, ws, a)
    b = run_next(user, ws, other)
    end(user, ws, b, "waiting", None, refused=["cap"], refused_count=1)
    assert (manager(user, other)["waits"], manager(user, other)["woken"]) == ("cap", 0)
    ws.open[t["task"]] = _task_state(t, "stopped", 6, ended_at=T0 + 90)       # the owner stops it: a place frees
    sync(user, ws)
    assert manager(user, rid)["woken"] == 1                                       # its own manager's change
    assert (manager(user, other)["waits"], manager(user, other)["woken"]) == ("cap", 0)


def test_an_attempt_refused_for_the_workspace_s_sessions_leaves_its_manager_waiting_for_sessions(env, ws):
    """The `sessions` wait, the app's half: an attempt whose calls
    the workspace refused for MAX_SESSIONS leaves its manager waiting for `sessions`."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    end(user, ws, a, "waiting", None, refused=["sessions"], refused_count=1)
    m = manager(user, rid)
    assert (m["state"], m["waits"]) == ("waiting", "sessions")


@pytest.mark.parametrize("ended_first", [True, False], ids=["ended-before-the-close", "ended-after-it"])
def test_an_attempt_s_packet_is_let_go_at_its_requirement_s_close(env, ws, ended_first):
    """An attempt's packet is let go at its requirement's
    close: one ended before it by the close, one still running by its own end."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    if ended_first:
        end(user, ws, a)
        assert attempts(user, rid)[0]["packet"] is not None
    controller.close(user, rid, "done", now=T0 + 80)
    if not ended_first:
        assert attempts(user, rid)[0]["packet"] is not None      # still running: kept until its end
        end(user, ws, a, "stopped", "closed")
    assert attempts(user, rid)[0]["packet"] is None


def test_an_attempt_s_end_for_the_controller_s_account_says_what_happened(env):
    """The journal's words for an attempt's end for the controller's account say which: a stop for its change, a
    refusal for an account the workspace does not have."""
    words = controller.ATTEMPT_WORDS
    assert words["attempt.ended"]({"n": 3, "outcome": "stopped", "cause": "account"}) == \
        "Its manager's attempt 3 ended: stopped: the Manager's account changed"
    assert words["attempt.ended"]({"n": 3, "outcome": "refused", "cause": "account"}) == \
        "Its manager's attempt 3 ended: refused: the Manager's account is not one the workspace has"
    assert words["attempt.refused"]({"n": 3, "cause": "account"}) == \
        "The workspace did not take its manager's attempt 3: the Manager's account is not one the workspace has"
    assert words["attempt.ended"]({"n": 3, "outcome": "stopped", "cause": "closed"}) == \
        "Its manager's attempt 3 ended: stopped: the requirement was closed"


def test_the_owner_s_change_of_account_lets_every_wait_for_the_window_go(env, ws):
    """A manager waits for its window, the controller's account's; the
    owner's change of the account lets that wait go, the window being the old account's, and wakes the manager
    (`reset`), so that the queue does not hold it, nor the workspace's busy keep the machine up for that reset."""
    user = {"id": "u1"}
    rid, a = running(user, ws)
    ws.room["window"] = "spent"
    end(user, ws, a, "waiting", "window", waits_until=T0 + 9999)
    m = manager(user, rid)
    assert (m["waits"], m["waits_until"], m["woken"]) == ("window", T0 + 9999, 0)
    own(user["id"], account="0123456789ab")
    controller.change_settings(user, {"account": "0123456789ab"}, now=T0 + 80)
    m = manager(user, rid)
    assert (m["waits"], m["waits_until"], m["woken"]) == (None, None, 1)
    assert [w["kind"] for w in json.loads(m["wakes"])] == ["plan", "reset"]
    sync(user, ws)
    [x] = ws.managers[-1]["managers"]
    assert (x["waits"], x["waits_until"]) == (None, None)       # the route the workspace's busy reads


@pytest.mark.parametrize("task_open", [False, True], ids=["nothing-open", "a-task-open"])
def test_a_manager_waiting_with_nothing_open_and_nothing_waited_for_is_told_after_an_hour(env, ws, task_open):
    """A manager whose attempt ended waiting with no task
    open and nothing in `waits` has nothing of its own to wake it: the owner is told after WAIT_TOLD, once the sweep has
    marked it; one with a task open, whose change will wake it, is not."""
    user = {"id": "u1"}
    rid, a = running(user, ws, share=2, cap=3)
    if task_open:
        t = _start_of(a, rid)
        controller.take(user["id"], record(t, tasks_=[_task_state(t, "working", 5)]), now=T0 + 60)
        ws.open[t["task"]] = _task_state(t, "working", 5)
    end(user, ws, a)
    m = manager(user, rid)
    assert (m["state"], m["waits"], m["woken"]) == ("waiting", None, 0)
    since = int(time.time()) - controller.WAIT_TOLD
    with store.connect() as db:
        db.execute("UPDATE ade_controller_managers SET waits_since = ? WHERE user_id = ?", (since, user["id"]))
    sync(user, ws)
    told = (f"controller-manager-waits:{rid}:{since}", "controller-manager-waits") in controller.alerts(user["id"])
    assert (manager(user, rid)["told_at"] is not None, told) == (not task_open, not task_open)
