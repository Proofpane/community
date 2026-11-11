"""The controller's rows on the progress board: the
controller adds its requirements (the page's sub-goals) and the sessions it lists to the board under the name
`controller`, through a source that takes what the board gives, and every row it gives is one the board's check takes.
Read through `GET /api/ade/board`, the board's own route, or, to read it at a time of a test's own, through the board's
own function (progress_board.board), against the controller's records as the app takes them (test_controller.py's
helpers) and the workspace's sessions as a test gives them. Some read the controller's own route,
`GET /api/ade/controller/board`, beside the board, and one reads that route alone."""

import time

import pytest

from conftest import signed_up
from proofpane_open import auth
from proofpane_open.ade import controller, progress_board as board, workspace_routes
from test_ade_progress_board import make_task as durable_task
from test_controller import (  # noqa: F401
    C, OTHER, REQUEST, SESSION, T0, owner, plan, planned, question, setup, start, take, uid, ws)

B = "/api/ade/board"
HOUR = 3600
LONE = "e" * 12                  # a workspace session of the person's own, which no row of the controller's names


@pytest.fixture
def given(monkeypatch):
    """The workspace's sessions as a test gives them, read as the board reads them; the board's sources left as the
    app has them, the controller's among them."""
    monkeypatch.setattr(board, "SESSIONS_WAIT", 30.0)
    out = {"sessions": []}
    monkeypatch.setattr(workspace_routes, "sessions", lambda user: {"sessions": [dict(s) for s in out["sessions"]]})
    return out


def _ws(sid, title):
    """A workspace session as the list of sessions gives it, running and working."""
    return {"id": sid, "program": "claude", "title": title, "running": True, "activity": {"state": "working"},
            "created_at": T0 + 20}


def _ws_ended(sid, title):
    """A workspace session as the list of sessions gives it once its program has ended: done."""
    return {"id": sid, "program": "claude", "title": title, "running": False, "finished": True, "created_at": T0 + 20}


def _board(client, h):
    r = client.get(B, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _row(got, kind, row_id):
    found = [r for r in got["rows"] if (r["kind"], r["id"]) == (kind, row_id)]
    assert len(found) == 1, f"{kind} {row_id}: {len(found)} rows"
    return found[0]


def _said(capsys):
    """What the board said on standard error of the controller's rows: a source that failed, or a row it refused."""
    return [line for line in capsys.readouterr().err.splitlines() if "of controller" in line]


def test_a_recorded_sub_goal_and_its_sessions_appear_on_the_board_in_their_phases(client, owner, given, capsys):
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    now = int(time.time())
    run, wait, done = (start(rid, session=s, at=T0 + 20 + i) for i, s in enumerate((SESSION, OTHER, "c" * 12)))
    take(user, run, wait, done, tasks_=[{"id": run["task"], "state": "working"},
                                        {"id": wait["task"], "state": "idle", "waits_until": now + HOUR},
                                        {"id": done["task"], "state": "done", "ended_at": T0 + 90}])
    got = _board(client, h)
    req = _row(got, "requirement", rid)
    assert (req["phase"], req["state"], req["parent"], req["progress"], req["title"]) == (
        "now", "running", None, None, "Make the README say hello.")         # no steps to count: its sessions are rows
    assert req["session"] == OTHER             # its newest open session
    for task, phase, state in ((run, "now", "working"), (wait, "next", "waiting_for_a_reset"),
                               (done, "history", "done")):
        r = _row(got, "session", task["task"])
        assert (r["phase"], r["state"], r["parent"], r["session"]) == (phase, state, rid, task["session"])
    assert _row(got, "session", wait["task"])["waits_until"] == now + HOUR
    assert _row(got, "session", done["task"])["ended_at"] == T0 + 90
    assert _said(capsys) == []


def test_a_session_row_of_the_controller_s_holds_its_workspace_session_so_the_board_lists_it_once(client, owner,
                                                                                                    given):
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    given["sessions"] = [_ws(SESSION, "The controller's session"), _ws(LONE, "A session of the person's own")]
    got = _board(client, h)
    sessions = [r for r in got["rows"] if r["kind"] == "session"]
    assert [(r["id"], r["parent"], r["session"]) for r in sessions if SESSION in (r["id"], r["session"])] == [
        (s["task"], rid, SESSION)]
    # a session no row names is the board's own row, as before
    assert [(r["id"], r["parent"], r["session"]) for r in sessions if LONE in (r["id"], r["session"])] == [
        (LONE, None, LONE)]


def test_a_sub_goal_waiting_for_a_reset_is_in_next_at_its_reset_under_a_name_the_board_takes(client, owner, given,
                                                                                              capsys):
    h, user = owner
    setup(user)
    _, rid = planned(user)
    now = int(time.time())
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "idle", "waits_until": now + HOUR}])
    got = _board(client, h)
    req = _row(got, "requirement", rid)
    assert (req["phase"], req["state"], req["waits_until"]) == ("next", "waiting_for_a_reset", now + HOUR)
    assert _said(capsys) == []
    # the controller's view says it as it did: only the board's row takes the name
    assert [r["activity"] for r in client.get(C, headers=h).json()["requirements"]] == ["waiting for a reset"]


def test_a_reset_past_the_year_9999_leaves_its_rows_on_the_board_without_it(client, owner, given, capsys):
    h, user = owner
    setup(user, cap=5)
    _, far = planned(user)
    _, edge = planned(user)
    a, b = start(far, session=SESSION), start(edge, session=OTHER)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "idle", "waits_until": board.MOST_SECONDS + 1},
                             {"id": b["task"], "state": "idle", "waits_until": board.MOST_SECONDS}])
    given["sessions"] = [_ws(SESSION, "Far"), _ws(OTHER, "Edge")]
    got = _board(client, h)
    for kind, row_id, reset in (("requirement", far, None), ("session", a["task"], None),
                                ("requirement", edge, board.MOST_SECONDS), ("session", b["task"], board.MOST_SECONDS)):
        r = _row(got, kind, row_id)
        assert (r["phase"], r["waits_until"]) == ("next", reset), (kind, r)
    assert _row(got, "requirement", far)["state"] == "waiting_for_a_reset"
    # each session once, as the controller's row
    assert sorted(r["id"] for r in got["rows"] if r["kind"] == "session") == sorted([a["task"], b["task"]])
    assert _said(capsys) == []


def test_a_sub_goal_names_a_session_that_has_its_own_row_on_the_board(client, owner, given):
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    ids = [f"{i:012x}" for i in range(1, controller.SHOWN_TASKS + 2)]       # one more open than a sub-goal lists
    starts = [start(rid, session=sid, at=T0 + 20 + i) for i, sid in enumerate(ids)]
    take(user, *starts, tasks_=[{"id": s["task"], "state": "working"} for s in starts])
    given["sessions"] = [_ws(sid, f"Session {n}") for n, sid in enumerate(ids, 1)]
    got = _board(client, h)
    listed = {r["session"] for r in got["rows"] if r["kind"] == "session" and r["parent"] == rid}
    assert listed == set(ids[:controller.SHOWN_TASKS])
    # the newest of its sessions listed: the one it holds has a row of its own
    assert _row(got, "requirement", rid)["session"] == ids[controller.SHOWN_TASKS - 1]
    # every session on one row: those listed as the controller's, the one past them as the board's own
    assert sorted(r["session"] for r in got["rows"] if r["kind"] == "session") == ids


def test_every_row_of_the_controller_s_reaches_the_board_with_its_fields_but_the_order_the_board_numbers(
        client, owner, given, capsys):
    """Each field as the controller gives it for the board (board_rows with `board`), but `order`, which the board
    numbers in next, and `grant`, the board's own, null here. Every event's words here are short: the board cuts any past WORDS_MOST characters, as it cuts every
    row's (a start's flags can run longer, and the controller's own log keeps them whole)."""
    h, user = owner
    setup(user, cap=10)
    rid = {name: planned(user)[1] for name in ("running", "asking", "idle", "reset", "held", "planned", "open",
                                               "done", "stopped")}
    now = int(time.time())
    states = (("running", "starting", None), ("running", "working", None), ("running", "running", None),
              ("asking", "idle", None), ("idle", "idle", None), ("idle", "waiting", None),
              ("reset", "idle", now + HOUR), ("held", "waiting", now + 2 * HOUR), ("open", "done", None),
              ("open", "failed", None), ("done", "stopped", None), (None, "working", None))
    starts, listed = [], []
    for i, (name, state, reset) in enumerate(states):
        s = start(rid[name] if name else None, session=f"{i + 1:012x}", at=T0 + 20 + i)
        starts.append(s)
        listed.append(dict({"id": s["task"], "state": state}, **({"waits_until": reset} if reset else {})))
    take(user, *starts, question("Which file?", requirement=rid["asking"], at=T0 + 40), tasks_=listed)
    controller.hold(user, rid["held"], True, now=T0 + 110)
    controller.close(user, rid["done"], "done", now=T0 + 120)
    controller.close(user, rid["stopped"], "stopped", now=T0 + 130)
    got = _board(client, h)
    given_rows = controller.board_rows(user["id"], got["at"], board=True)
    # every activity a sub-goal has, and every state a session has, with one waiting for a reset named as a sub-goal is
    assert {r["state"] for r in given_rows if r["kind"] == "requirement"} == {
        "running", "waiting", "idle", "waiting_for_a_reset", "held", "planned", "open", "done", "stopped"}
    assert {r["state"] for r in given_rows if r["kind"] == "session"} == set(controller.TASK_STATES) | {
        "waiting_for_a_reset"}
    on = {(r["kind"], r["id"]): r for r in got["rows"]}
    for r in given_rows:
        shown = on.get((r["kind"], r["id"]))
        assert shown is not None, f"{r['kind']} {r['state']} is not on the board"
        # but `grant`, the board's own on a task's row: null on each of the controller's
        assert shown["grant"] is None
        assert {k: v for k, v in shown.items() if k not in ("order", "grant")} == \
            {k: v for k, v in r.items() if k != "order"}
    assert len(on) == len(given_rows)            # and nothing else: no workspace session was given
    assert _said(capsys) == []


def test_the_controller_s_rows_are_its_own_name_s_and_reach_only_its_owner_s_board(client, owner, given):
    """Two people, each with a controller and a sub-goal of their own: each board holds its owner's alone."""
    h, user = owner
    assert board._SOURCES.get("controller") is controller.board_source
    setup(user)
    _, rid = planned(user)
    other_h = signed_up(client, email="someone@example.test")
    other = auth.user(client.get("/api/auth/me", headers=other_h).json()["id"])
    setup(other)
    _, theirs = planned(other)
    s = start(theirs, session=OTHER)
    take(other, s, tasks_=[{"id": s["task"], "state": "working"}])
    mine = {(r["kind"], r["id"]) for r in _board(client, h)["rows"]}
    their = {(r["kind"], r["id"]) for r in _board(client, other_h)["rows"]}
    assert (mine, their) == ({("requirement", rid)}, {("requirement", theirs), ("session", s["task"])})


def test_the_controller_s_rows_are_as_of_the_board_s_own_time(owner, given):
    """The board gives its sources its own time, by which a row's phase is judged: a session waiting for a reset at
    T0 + 200 is in next at T0 + 150, named waiting for a reset as its sub-goal is, and an hour later, its reset passed,
    in now with the state its record gives and no time, whatever the clock says when the source reads it (a session
    whose reset has passed keeps its own state on the board)."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "idle", "waits_until": T0 + 200}])
    for at, phase, state, reset in ((T0 + 150, "next", "waiting_for_a_reset", T0 + 200),
                                    (int(time.time()), "now", "idle", None)):
        got = board.board(user, now=at)
        assert got["at"] == at
        rows = {(r["kind"], r["id"]): r for r in got["rows"]}
        session, req = rows[("session", s["task"])], rows[("requirement", rid)]
        assert (session["phase"], session["state"], session["waits_until"]) == (phase, state, reset), at
        assert (req["state"], req["waits_until"]) == (state, reset), at


def test_a_sub_goal_waits_in_next_by_its_making_beside_a_task_with_no_known_time(client, owner, given):
    """The controller once numbered its next rows oldest first, which the board read as a
    queue of the controller's own and put ahead of every row of its own without a known time, a task being offered
    for hours among them. With no queue of its own, a sub-goal waits by its making, as the board's other rows do."""
    h, user = owner
    setup(user)
    _, rid = planned(user)                                  # planned at T0 + 10, about an hour ago
    task = durable_task(user["id"], "offering", created=T0 - 2 * HOUR)
    got = _board(client, h)
    assert (_row(got, "task", task)["order"], _row(got, "requirement", rid)["order"]) == (1, 2)


def test_a_sub_goal_s_account_is_its_newest_open_session_s_else_the_controller_s(client, owner, given):
    """Every other test runs on account default, so a sub-goal's account given always
    as the controller's own passed them."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    _, alone = planned(user)
    elsewhere = "b" * 12
    s = start(rid, session=SESSION, account=elsewhere)
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    got = _board(client, h)
    assert _row(got, "session", s["task"])["account"] == {"kind": "claude", "id": elsewhere}
    assert _row(got, "requirement", rid)["account"] == {"kind": "claude", "id": elsewhere}
    assert _row(got, "requirement", alone)["account"] == {"kind": "claude", "id": "default"}


def test_a_sub_goal_names_the_larger_id_of_two_sessions_started_or_ended_in_one_second(client, owner, given):
    """The newest open session, else the one that ended last, by its time and then by
    the larger id; a choice by the time alone, or the first listed, takes the smaller."""
    h, user = owner
    setup(user, cap=5)
    _, opened = planned(user)
    _, ended = planned(user)
    a, b = (start(opened, tid=t * 32, session=t * 12, at=T0 + 20) for t in "12")
    c, d = (start(ended, tid=t * 32, session=t * 12, at=T0 + 21) for t in "34")
    take(user, a, b, c, d, tasks_=[{"id": a["task"], "state": "working"}, {"id": b["task"], "state": "working"},
                                   {"id": c["task"], "state": "done", "ended_at": T0 + 90},
                                   {"id": d["task"], "state": "done", "ended_at": T0 + 90}])
    got = _board(client, h)
    assert (_row(got, "requirement", opened)["session"], _row(got, "requirement", ended)["session"]) == (
        "2" * 12, "4" * 12)


def test_a_sub_goal_whose_sessions_ended_in_one_second_names_one_it_lists(client, owner, given):
    """Past the 20 a sub-goal lists, sessions that ended in one second (the app's own
    end of those a record no longer lists makes them so) are listed by the smaller id, so the newest of all by id is
    past the list, and a sub-goal naming it held it off the board. The list gives each session as ended, as its task
    is."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    starts = [start(rid, tid=f"{i:032x}", session=f"{i:012x}", at=T0 + 20)
              for i in range(1, controller.SHOWN_TASKS + 6)]
    take(user, *starts, tasks_=[{"id": s["task"], "state": "done", "ended_at": T0 + 90} for s in starts])
    given["sessions"] = [_ws_ended(s["session"], f"Session {n}") for n, s in enumerate(starts, 1)]
    got = _board(client, h)
    assert _row(got, "requirement", rid)["session"] == f"{controller.SHOWN_TASKS:012x}"   # the larger id listed
    assert sorted(r["session"] for r in got["rows"] if r["kind"] == "session") == [s["session"] for s in starts]


def test_a_session_the_controller_no_longer_lists_is_the_board_s_own_row(owner, given):
    """Only the sessions the controller lists are rows of its own. A session started
    for no sub-goal that ended before the last week, and one under a sub-goal closed before it with no session open,
    are listed by the controller no more; while the workspace lists each, the board shows it as its own row of that
    session, once, by the workspace's title and with no parent. Read at the board's own time, as a test of a week. The
    list gives each session as ended, as its task is."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    under, stray = start(rid, session="1" * 12), start(None, session="2" * 12)
    take(user, under, stray, tasks_=[{"id": s["task"], "state": "done", "ended_at": T0 + 90} for s in (under, stray)])
    controller.close(user, rid, "done", now=T0 + 120)
    given["sessions"] = [_ws_ended("1" * 12, "Under a sub-goal"), _ws_ended("2" * 12, "For no sub-goal")]
    later = T0 + 120 + controller.KEPT_CLOSED + 60
    for at, held in ((T0 + 200, True), (later, False)):
        rows = board.board(user, now=at)["rows"]
        for s, title in ((under, "Under a sub-goal"), (stray, "For no sub-goal")):
            own = [r for r in rows if r["kind"] == "session" and r["session"] == s["session"]]
            assert len(own) == 1, (at, s["session"], own)
            r = own[0]
            if held:                                         # the controller's row, holding the workspace session
                assert (r["kind"], r["id"], r["title"]) == ("session", s["task"], "demo on pp/abc"), at
            else:                                            # the board's own row of the workspace session
                assert (r["kind"], r["id"], r["title"], r["parent"]) == ("session", s["session"], title, None), at
        sub_goal = [r for r in rows if (r["kind"], r["id"]) == ("requirement", rid)]
        assert [r["session"] for r in sub_goal] == (["1" * 12] if held else []), at


def test_the_controller_s_own_route_gives_its_rows_unchanged_by_the_board(client, owner, given):
    """The board's changes are the board's alone. The controller's own route keeps
    its own rows, the activity's words, Next numbered oldest first, a sub-goal's sessions done counted and a reset as
    the record gave it, as the controller's contract gives them; the board has its own."""
    h, user = owner
    setup(user, cap=5)
    _, waiting = planned(user)
    _, alone = planned(user, now=T0 + 6, at=T0 + 11)
    now = int(time.time())
    a, b = start(waiting, session=SESSION), start(waiting, session=OTHER, at=T0 + 21)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "idle", "waits_until": board.MOST_SECONDS + 1},
                             {"id": b["task"], "state": "done", "ended_at": T0 + 90}])
    route = {(r["kind"], r["id"]): r for r in client.get(C + "/board", headers=h).json()["rows"]}
    on_board = {(r["kind"], r["id"]): r for r in _board(client, h)["rows"]}
    req, alone_row = route[("requirement", waiting)], route[("requirement", alone)]
    assert (req["state"], req["progress"], req["waits_until"]) == (
        "waiting for a reset", {"done": 1, "of": 2}, board.MOST_SECONDS + 1)
    assert route[("session", a["task"])]["waits_until"] == board.MOST_SECONDS + 1
    # Next numbered by when each was made, oldest first
    assert (req["order"], alone_row["order"], route[("session", a["task"])]["order"]) == (1, 2, 3)
    req = on_board[("requirement", waiting)]
    assert (req["state"], req["progress"], req["waits_until"]) == ("waiting_for_a_reset", None, None)
    assert on_board[("session", a["task"])]["waits_until"] is None


def test_a_controller_off_or_stopped_leaves_none_of_its_rows_in_next(client, owner, given):
    """Next on the board is what goes on without its owner, and a controller off or
    stopped is to start and continue nothing (its chat is open only while it is on and not stopped; a start or a
    continue made then is flagged). A sub-goal waiting for a reset, its session, a sub-goal planned,
    one held and one open whose sessions all ended are in next while it is on, and in now, with no time, while it is
    off or stopped."""
    h, user = owner
    setup(user, cap=5)
    _, resetting = planned(user)
    _, planned_only = planned(user, now=T0 + 6, at=T0 + 11)
    _, held = planned(user, now=T0 + 7, at=T0 + 12)
    _, closed = planned(user, now=T0 + 8, at=T0 + 13)
    _, emptied = planned(user, now=T0 + 9, at=T0 + 14)
    now = int(time.time())
    s, ended = start(resetting, session=SESSION), start(closed, session=OTHER)
    gone = start(emptied, session="d" * 12, at=T0 + 21)
    take(user, s, ended, gone, tasks_=[{"id": s["task"], "state": "idle", "waits_until": now + HOUR},
                                       {"id": ended["task"], "state": "done", "ended_at": T0 + 90},
                                       {"id": gone["task"], "state": "done", "ended_at": T0 + 95}])
    controller.hold(user, held, True, now=T0 + 110)
    controller.close(user, closed, "done", now=T0 + 120)
    rows = (("requirement", resetting, "waiting_for_a_reset"), ("session", s["task"], "waiting_for_a_reset"),
            ("requirement", planned_only, "planned"), ("requirement", held, "held"), ("requirement", emptied, "open"))
    for state in ("on", "stopped", "off", "on"):
        controller.set_state(user, state, now=T0 + 200)
        got = _board(client, h)
        for kind, row_id, row_state in rows:
            r = _row(got, kind, row_id)
            assert (r["phase"], r["state"]) == ("next" if state == "on" else "now", row_state), (state, kind, row_state)
            if state != "on":
                assert r["waits_until"] is None, (state, kind)
        resets = {_row(got, kind, row_id)["waits_until"] for kind, row_id in (rows[0][:2], rows[1][:2])}
        assert resets == ({now + HOUR} if state == "on" else {None}), state
        # what ended stays in history, whatever the controller's state
        assert (_row(got, "requirement", closed)["phase"], _row(got, "session", ended["task"])["phase"]) == (
            "history", "history"), state


def test_a_plan_waiting_for_the_owner_s_go_is_in_now_until_the_go(client, owner, given):
    """With plans shown to the owner first, a sub-goal planned and not yet covered by
    a go waits on its owner, so it is in now; the go puts it in next, where the controller starts it."""
    h, user = owner
    setup(user, confirm_plans=True)
    request, rid = planned(user)
    r = _row(_board(client, h), "requirement", rid)
    assert (r["phase"], r["state"], r["waits_until"]) == ("now", "planned", None)
    assert [x["waits_for_go"] for x in client.get(C, headers=h).json()["requirements"]] == [True]
    controller.go(user, request["id"], now=T0 + 120)
    assert _row(_board(client, h), "requirement", rid)["phase"] == "next"


def test_a_session_waiting_for_its_reset_waits_in_next_beside_its_sub_goal(client, owner, given):
    """A session waiting at its prompt for its account's reset, which the session list
    calls waiting, is one the controller continues after that reset, and its sub-goal counts it as waiting
    for a reset: on the board both are in next at that reset, under one state. With no reset known it waits on its
    owner, in now, as does its sub-goal. The controller's own route keeps the list's state, in next at the reset."""
    h, user = owner
    setup(user, cap=5)
    _, resetting = planned(user)
    _, needing = planned(user, now=T0 + 6, at=T0 + 11)
    now = int(time.time())
    a, b = start(resetting, session=SESSION), start(needing, session=OTHER)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "waiting", "waits_until": now + HOUR},
                             {"id": b["task"], "state": "waiting"}])
    got = _board(client, h)
    rows = [_row(got, kind, row_id) for kind, row_id in (("requirement", resetting), ("session", a["task"]),
                                                          ("requirement", needing), ("session", b["task"]))]
    assert [(r["phase"], r["state"], r["waits_until"]) for r in rows] == [
        ("next", "waiting_for_a_reset", now + HOUR), ("next", "waiting_for_a_reset", now + HOUR),
        ("now", "idle", None), ("now", "waiting", None)]
    routed = next(x for x in client.get(C + "/board", headers=h).json()["rows"] if x["id"] == a["task"])
    assert (routed["phase"], routed["state"], routed["waits_until"]) == ("next", "waiting", now + HOUR)


@pytest.mark.parametrize("state", ("starting", "working", "running"))
def test_a_session_that_runs_is_in_now_beside_its_sub_goal_whatever_reset_its_record_gives(client, owner, given,
                                                                                           state):
    """A session starting, working or running while its record still
    gives a reset to come runs, and its sub-goal is running, so on the board both are in now, with no time. The
    controller's own route keeps it in next at that reset."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    now = int(time.time())
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": state, "waits_until": now + HOUR}])
    got = _board(client, h)
    assert [(r["phase"], r["state"], r["waits_until"]) for r in (_row(got, "requirement", rid),
                                                                 _row(got, "session", s["task"]))] == [
        ("now", "running", None), ("now", state, None)]
    routed = next(x for x in client.get(C + "/board", headers=h).json()["rows"] if x["id"] == s["task"])
    assert (routed["phase"], routed["waits_until"]) == ("next", now + HOUR)


def test_a_sub_goal_is_made_when_it_was_planned_and_waits_in_next_by_it(client, owner, given):
    """A sub-goal's making is when it was planned, not when the app recorded the
    plan, and the board places it in next by that time among its own rows with no known time."""
    h, user = owner
    setup(user, cap=5)
    _, first = planned(user)                                    # planned at T0 + 10, recorded at T0 + 100
    _, last = planned(user, now=T0 + 50, at=T0 + 60)
    task = durable_task(user["id"], "offering", created=T0 + 30)
    got = _board(client, h)
    assert [_row(got, "requirement", first)["created_at"], _row(got, "requirement", last)["created_at"]] == [
        T0 + 10, T0 + 60]
    assert [_row(got, kind, row_id)["order"] for kind, row_id in (("requirement", first), ("task", task),
                                                                  ("requirement", last))] == [1, 2, 3]


def test_a_session_with_no_repository_has_a_title_on_the_board(client, owner, given):
    """A start whose repository the app kept as none is titled with its branch, or,
    with no branch either, with its program's name, as the board names a workspace session with no title; the
    controller's own route keeps its own title."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    branched, bare = start(rid, session=SESSION, repo=None), start(rid, session=OTHER, repo=None, branch=None)
    take(user, branched, bare, tasks_=[{"id": s["task"], "state": "working"} for s in (branched, bare)])
    got = _board(client, h)
    assert (_row(got, "session", branched["task"])["title"], _row(got, "session", bare["task"])["title"]) == (
        "pp/abc", "claude")
    routed = {r["id"]: r["title"] for r in client.get(C + "/board", headers=h).json()["rows"]}
    assert (routed[branched["task"]], routed[bare["task"]]) == ("on pp/abc", "")


def test_a_session_of_the_controller_s_has_its_state_and_title_whatever_the_list_says(client, owner, given):
    """A session the controller lists is its row, with the state the app's last sync
    read and the controller's title; the list's own state and title, read live, are not on the board for it. A list
    that says the session waits on its owner, or one that gives no sessions at all, leaves that row and its sub-goal as
    the controller's record has them."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    waiting = dict(_ws(SESSION, "The list's own title"), activity={"state": "waiting"})
    for listed in ([waiting], []):
        given["sessions"] = listed
        got = _board(client, h)
        rows = [(r["kind"], r["id"], r["state"], r["title"]) for r in got["rows"]
                if r["kind"] == "session" and SESSION in (r["id"], r["session"])]
        assert rows == [("session", s["task"], "working", "demo on pp/abc")], listed
        assert _row(got, "requirement", rid)["state"] == "running", listed


def test_two_tasks_of_the_controller_s_naming_one_session_are_two_rows(client, owner, given):
    """Each of the controller's tasks is a row of its own id, so two that name one
    workspace session are two rows, each holding it, and the board has no row of its own for that session."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    a, b = start(rid, session=SESSION), start(rid, session=SESSION, at=T0 + 21)
    take(user, a, b, tasks_=[{"id": a["task"], "state": "done", "ended_at": T0 + 90},
                             {"id": b["task"], "state": "working"}])
    given["sessions"] = [_ws(SESSION, "The controller's session")]
    got = _board(client, h)
    rows = sorted((r["id"], r["session"], r["phase"]) for r in got["rows"]
                  if r["kind"] == "session" and SESSION in (r["id"], r["session"]))
    assert rows == sorted([(a["task"], SESSION, "history"), (b["task"], SESSION, "now")])


def test_the_controller_s_own_route_lists_next_in_the_order_it_numbers(client, owner, given):
    """On the controller's own route Next is numbered oldest first
    by when each row was made, and listed in that order whatever changed last or first; the board
    numbers its own. Three sub-goals made in turn, the first changed after the second and the third after the first:
    listed by their newest change, or by their oldest, they would be in another order."""
    h, user = owner
    setup(user, cap=5)
    _, first = planned(user)                                    # planned at T0 + 10, all three recorded at T0 + 100
    _, second = planned(user, now=T0 + 6, at=T0 + 11)
    _, third = planned(user, now=T0 + 7, at=T0 + 12)
    for rid, at in ((first, T0 + 150), (third, T0 + 160)):
        controller.hold(user, rid, True, now=at - 5)
        controller.hold(user, rid, False, now=at)
    rows = [r for r in client.get(C + "/board", headers=h).json()["rows"] if r["phase"] == "next"]
    assert [(r["id"], r["state"], r["order"]) for r in rows] == [
        (first, "planned", 1), (second, "planned", 2), (third, "planned", 3)]


def test_a_session_started_for_no_sub_goal_is_on_the_board_as_the_controller_s_others(client, owner, given):
    """A session the controller started for no sub-goal is placed, timed and titled
    on the board as its others are: waiting for a reset, in next at it while the controller is on and in now with no
    time while it is stopped or off; a reset past the year 9999 given as none; with no repository and no branch, titled
    with its program's name."""
    h, user = owner
    setup(user, cap=5)
    now = int(time.time())
    resetting = start(None, session=SESSION)
    far = start(None, session=OTHER, at=T0 + 21)
    bare = start(None, session="c" * 12, repo=None, branch=None, at=T0 + 22)
    take(user, resetting, far, bare, tasks_=[
        {"id": resetting["task"], "state": "idle", "waits_until": now + HOUR},
        {"id": far["task"], "state": "idle", "waits_until": board.MOST_SECONDS + 1},
        {"id": bare["task"], "state": "working"}])
    for state in ("on", "stopped", "off"):
        controller.set_state(user, state, now=T0 + 200)
        got = _board(client, h)
        rows = [_row(got, "session", s["task"]) for s in (resetting, far, bare)]
        waiting = "next" if state == "on" else "now"
        assert [(r["phase"], r["state"], r["waits_until"], r["parent"]) for r in rows] == [
            (waiting, "waiting_for_a_reset", now + HOUR if state == "on" else None, None),
            (waiting, "waiting_for_a_reset", None, None),
            ("now", "working", None, None)], state
        assert rows[2]["title"] == "claude", state


def test_a_session_with_no_repository_is_titled_by_what_its_record_gave(client, owner, given):
    """With no repository and no branch, a session is titled with its program's name,
    whichever program it is; with no program the record took either, with its workspace session's id; with no session
    the record took, with its own id."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    codex = start(rid, session=SESSION, repo=None, branch=None, program="codex")
    unnamed = start(rid, session=OTHER, repo=None, branch=None, program="!")
    lost = start(rid, session="not a session", repo=None, branch=None, program="!")
    take(user, codex, unnamed, lost, tasks_=[{"id": s["task"], "state": "working"} for s in (codex, unnamed, lost)])
    got = _board(client, h)
    assert [_row(got, "session", s["task"])["title"] for s in (codex, unnamed, lost)] == [
        "codex", OTHER, lost["task"]]


def test_a_sub_goal_no_go_covers_is_in_now_whatever_else_it_is_doing(client, owner, given):
    """A sub-goal planned while plans wait for the owner's go, which no go covers,
    waits on its owner whatever its activity: held, or with every session it started (before the go, which the app
    flags) ended."""
    h, user = owner
    setup(user, cap=5, confirm_plans=True)
    _, held = planned(user)
    _, ended = planned(user, now=T0 + 6, at=T0 + 11)
    s = start(ended, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "done", "ended_at": T0 + 90}])
    controller.hold(user, held, True, now=T0 + 110)
    got = _board(client, h)
    rows = [_row(got, "requirement", r) for r in (held, ended)]
    assert [(r["phase"], r["state"]) for r in rows] == [("now", "held"), ("now", "open")]


def test_whether_a_plan_waits_for_the_go_is_the_switch_s_when_it_was_made(client, owner, given):
    """A plan made while plans waited for the owner's go waits for one, in now, after
    the switch is turned off, and a plan made while they did not is in next after the switch is turned on: the switch in
    effect when the plan was made decides, as the requirement keeps it."""
    h, user = owner
    setup(user, cap=5, confirm_plans=True)
    request = controller.add_request(user, REQUEST, now=T0 + 1)
    controller.change_settings(user, {"confirm_plans": False}, now=T0 + 10)
    waiting, free = uid(), uid()
    take(user, plan(request["id"], "Make the README say hello.", rid=waiting, at=T0 + 5),
         plan(request["id"], "Make the README say hello.", rid=free, at=T0 + 15), now=T0 + 20)
    for confirm in (False, True):
        controller.change_settings(user, {"confirm_plans": confirm}, now=T0 + 30)
        got = _board(client, h)
        assert (_row(got, "requirement", waiting)["phase"], _row(got, "requirement", free)["phase"]) == (
            "now", "next"), confirm


def test_a_sub_goal_whose_sessions_all_ended_names_the_one_that_ended_last_whenever_it_started(client, owner, given):
    """With no session open, a sub-goal names the listed session that ended last, by
    its end and not its start: the session started first and ended last is named, though the other started after it."""
    h, user = owner
    setup(user, cap=5)
    _, rid = planned(user)
    first, second = start(rid, session="1" * 12, at=T0 + 20), start(rid, session="2" * 12, at=T0 + 30)
    take(user, first, second, tasks_=[{"id": first["task"], "state": "done", "ended_at": T0 + 90},
                                      {"id": second["task"], "state": "done", "ended_at": T0 + 60}])
    assert _row(_board(client, h), "requirement", rid)["session"] == "1" * 12


def test_a_held_sub_goal_stays_in_next_while_one_of_its_sessions_runs(client, owner, given):
    """Held is among next's states, and the controller reads a sub-goal's
    hold before a session that runs (_activity), so a held sub-goal is in next, held, while its session is in now,
    working."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    controller.hold(user, rid, True, now=T0 + 110)
    got = _board(client, h)
    assert [(r["phase"], r["state"]) for r in (_row(got, "requirement", rid), _row(got, "session", s["task"]))] == [
        ("next", "held"), ("now", "working")]


def test_a_session_with_a_repository_and_no_branch_is_titled_by_its_repository(client, owner, given):
    """A start with a repository and no branch is titled with its repository on the
    board, as on the controller's own route."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION, branch=None)
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    assert _row(_board(client, h), "session", s["task"])["title"] == "demo"
    assert next(r for r in client.get(C + "/board", headers=h).json()["rows"] if r["id"] == s["task"])["title"] == (
        "demo")


def test_a_session_titled_by_its_program_is_named_as_the_board_names_one(client, owner, given):
    """With no repository and no branch, a session is titled with its program's name
    as the board names a workspace session with no title, a shell as "Shell"."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    s = start(rid, session=SESSION, repo=None, branch=None, program="shell")
    take(user, s, tasks_=[{"id": s["task"], "state": "working"}])
    assert _row(_board(client, h), "session", s["task"])["title"] == "Shell"


def test_a_closed_sub_goal_s_session_waiting_for_its_reset_waits_in_next(client, owner, given):
    """Closing a sub-goal stops none of its sessions, and the app flags a continue
    only while the controller is not on or before the session's reset, so the controller continues a closed sub-goal's
    session after its reset: the sub-goal is in history and the session in next at its reset."""
    h, user = owner
    setup(user)
    _, rid = planned(user)
    now = int(time.time())
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "waiting", "waits_until": now + HOUR}])
    controller.close(user, rid, "done", now=T0 + 120)
    got = _board(client, h)
    assert [(r["phase"], r["state"], r["waits_until"]) for r in (_row(got, "requirement", rid),
                                                                 _row(got, "session", s["task"]))] == [
        ("history", "done", None), ("next", "waiting_for_a_reset", now + HOUR)]


def test_a_session_started_before_the_go_waits_for_its_reset_in_next_while_its_sub_goal_waits_for_the_go(
        client, owner, given):
    """A start waits for the owner's go, a continue does not (the app flags a
    continue only while the controller is not on or before the session's reset), so a session started before the go,
    which the app flags, is in next at its reset while its sub-goal waits for the go in now, keeping its activity as
    its state."""
    h, user = owner
    setup(user, confirm_plans=True)
    _, rid = planned(user)
    now = int(time.time())
    s = start(rid, session=SESSION)
    take(user, s, tasks_=[{"id": s["task"], "state": "idle", "waits_until": now + HOUR}])
    got = _board(client, h)
    assert [(r["phase"], r["state"], r["waits_until"]) for r in (_row(got, "requirement", rid),
                                                                 _row(got, "session", s["task"]))] == [
        ("now", "waiting_for_a_reset", None), ("next", "waiting_for_a_reset", now + HOUR)]


def test_a_session_the_workspace_runs_again_after_the_controller_s_task_in_it_ended_is_its_own_row_too(
        client, owner, given):
    """The store keeps a task ended once it has ended (but for an end the app inferred
    itself), so a later page giving it as working changes nothing. While the workspace runs that session again, no row
    of the controller's holds it: the controller's row stays in history and the session is its own row in now, though
    its sub-goal names it. While the list shows a session ended, the controller's row is its only one, as before; and
    a session of the controller's waiting for a reset, in next, holds its session the list shows running."""
    h, user = owner
    setup(user, cap=5)
    now = int(time.time())
    _, again_rid = planned(user)
    _, ended_rid = planned(user)
    _, waiting_rid = planned(user)
    again, ended = start(again_rid, session=SESSION), start(ended_rid, session=OTHER, at=T0 + 21)
    waiting = start(waiting_rid, session="c" * 12, at=T0 + 22)
    take(user, again, ended, waiting, tasks_=[{"id": again["task"], "state": "stopped", "ended_at": T0 + 60},
                                              {"id": ended["task"], "state": "done", "ended_at": T0 + 60},
                                              {"id": waiting["task"], "state": "waiting", "waits_until": now + HOUR}])
    take(user, tasks_=[{"id": again["task"], "state": "working"},
                       {"id": ended["task"], "state": "done", "ended_at": T0 + 60},
                       {"id": waiting["task"], "state": "waiting", "waits_until": now + HOUR}], now=T0 + 150)
    given["sessions"] = [_ws(SESSION, "Started again"), _ws_ended(OTHER, "Ended"), _ws("c" * 12, "At its limit")]
    got = _board(client, h)
    rows = sorted((r["id"], r["session"], r["parent"], r["phase"], r["state"], r["title"]) for r in got["rows"]
                  if r["kind"] == "session")
    assert rows == sorted([(again["task"], SESSION, again_rid, "history", "stopped", "demo on pp/abc"),
                           (SESSION, SESSION, None, "now", "working", "Started again"),
                           (ended["task"], OTHER, ended_rid, "history", "done", "demo on pp/abc"),
                           (waiting["task"], "c" * 12, waiting_rid, "next", "waiting_for_a_reset", "demo on pp/abc")])
    assert _row(got, "requirement", again_rid)["session"] == SESSION


def test_a_held_or_asking_sub_goal_s_session_waiting_for_its_reset_waits_in_next(client, owner, given):
    """Neither a hold nor an open question holds back a continue, so the session of a
    held sub-goal, or of one asking its owner, that waits for a reset still to come is in next at that reset while the
    controller is on, as one of an open sub-goal is; the held sub-goal is in next, and the asking one waits on its
    owner in now."""
    h, user = owner
    setup(user, cap=5)
    now = int(time.time())
    _, held = planned(user)
    _, asking = planned(user)
    a, b = start(held, session=SESSION), start(asking, session=OTHER, at=T0 + 21)
    take(user, a, b, question("Which file?", requirement=asking, at=T0 + 40),
         tasks_=[{"id": a["task"], "state": "idle", "waits_until": now + HOUR},
                 {"id": b["task"], "state": "waiting", "waits_until": now + HOUR}])
    controller.hold(user, held, True, now=T0 + 110)
    got = _board(client, h)
    assert [(r["phase"], r["state"], r["waits_until"]) for r in (
        _row(got, "requirement", held), _row(got, "session", a["task"]),
        _row(got, "requirement", asking), _row(got, "session", b["task"]))] == [
        ("next", "held", None), ("next", "waiting_for_a_reset", now + HOUR),
        ("now", "waiting", None), ("next", "waiting_for_a_reset", now + HOUR)]
