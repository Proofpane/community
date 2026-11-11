"""The app's counts of the skills the owner's sessions called, the reading side (ade/skill_counts.py):
the read of the workspace's record against a stand-in for its
`GET /skills`, each line taken once by its own id, a record made anew, the numbers a record skipped, a workspace without
the route, a failed read kept with why, a page or a line out of shape, the days kept, one read at a time, and the poll's
round that reaches each owner. What the Skills page shows from these counts is test_skill_view.py's."""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time

import pytest

from conftest import ROOT, signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, controller, fly_provider, skill_counts, workspace_client
from skills_readme import stated

NO_ROUTE = workspace_client.WorkspaceCallError(502, "The workspace answered 404.")
SESSION = "0123456789ab"
OTHER = "ba9876543210"
STORE = "5" * 32
# three days ago, to the hour: inside the days kept, and the lines' times run on from it by their numbers
T0 = int(time.time()) // 3600 * 3600 - 3 * 86400
CORE_VERSION = "1.0.0+" + "a" * 64


def served(n, **kw):
    """A line as the workspace's record serves it (skillcalls.SkillCalls.page): the hook's line, with its number, its
    session, its repository and its task."""
    row = {"n": n, "id": f"{n:016x}", "at": T0 + n, "skill": "proofpane-core:verify", "kind": "core", "path": "claude",
           "failed": False, "version": CORE_VERSION, "session": SESSION, "repo": None, "task": None}
    row.update(kw)
    return row


class Record:
    """The workspace's GET /skills as skillcalls.SkillCalls.page serves it: the lines added, numbered from 1, the
    oldest `drop`ped by the record's own limits, a page of at most `page_size` lines after `since`, the record's id,
    and what is `first`. `routes` False answers as a workspace without the route does (a 404 with no refusal in it, which
    workspace_client gives as 502 in its own words); `fail` raises what it is given; `reply` changes the page served.
    Every call is kept, with the endpoint it was made to."""

    def __init__(self):
        self.store, self.lines, self.next, self.dropped = STORE, [], 1, 0
        self.calls, self.routes, self.fail, self.page_size, self.reply = [], True, None, 1000, None
        self.timeouts = []

    def add(self, **kw):
        row = served(self.next, **kw)
        self.next += 1
        self.lines.append(row)
        return row

    def drop(self, through):
        """The record's own limits drop its oldest lines: those numbered up to `through`."""
        self.lines = [row for row in self.lines if row["n"] > through]

    def renew(self, store):
        """A record made anew (a new volume, or one put aside because it could not be read): another id, numbers from 1."""
        self.store, self.lines, self.next = store, [], 1

    def call(self, endpoint, method, path, body=None, timeout=30):
        self.calls.append((endpoint, method, path))
        self.timeouts.append(timeout)
        if not path.startswith("/skills?since="):
            raise AssertionError(f"the app asked the workspace {method} {path}")
        if not self.routes:
            raise workspace_client.WorkspaceCallError(NO_ROUTE.status, str(NO_ROUTE))
        if self.fail is not None:
            raise self.fail
        since = int(path.split("=", 1)[1])
        newer = [row for row in self.lines if row["n"] > since]
        chunk = newer[:self.page_size]
        page = {"store": self.store, "seq": chunk[-1]["n"] if chunk else self.next - 1, "more": len(newer) > len(chunk),
                "first": self.lines[0]["n"] if self.lines else None, "dropped": self.dropped,
                "lines": [dict(row) for row in chunk]}
        return self.reply(page) if self.reply else page

    def paths(self):
        return [p for _, _, p in self.calls]


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    record = Record()
    monkeypatch.setattr(workspace_client, "call", record.call)
    monkeypatch.setattr(agents, "_endpoint", lambda user: "http://workspace.test")
    return record


@pytest.fixture
def owner(client, ws):
    h = signed_up(client)
    return h, auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def calls_of(user_id):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_skill_calls WHERE user_id = ? ORDER BY at, line_id",
                                            (user_id,))]


def reads_of(user_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_skill_reads WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def read(user):
    """The round's and the page's read, as skill_counts.read does it, with the machine up."""
    return skill_counts.sync(user, "http://workspace.test")


# ---- the tables -----------------------------------------------------------------------------------------------------

TABLES = {
    "ade_skill_reads": ("user_id", "store_id", "since", "read_at", "read_behind_at", "read_error", "read_error_at",
                        "unserved_at", "polled_at", "missed", "dropped", "refused", "trimmed", "created_at",
                        "updated_at"),
    "ade_skill_calls": ("user_id", "line_id", "at", "skill", "kind", "path", "failed", "version", "session_id", "repo",
                        "task_id", "taken_at"),
}


def test_the_tables_are_made_with_their_columns(env):
    with store.connect() as db:
        for table, columns in TABLES.items():
            assert tuple(r[1] for r in db.execute(f"PRAGMA table_info({table})")) == columns, table


def test_a_store_made_before_the_skill_counts_gets_their_tables(env, tmp_path):
    path = str(tmp_path / "old.db")
    with store.connect(path):
        pass
    db = sqlite3.connect(path)
    for table in TABLES:
        db.execute(f"DROP TABLE {table}")
    db.commit()
    db.close()
    with store.connect(path) as again:
        names = {r[0] for r in again.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(TABLES) <= names


def test_a_store_made_by_an_earlier_commit_of_the_skill_counts_gets_the_column_added_since(env, tmp_path):
    """`polled_at` came after the table's first form; a store that ran that form gets it once."""
    path = str(tmp_path / "earlier.db")
    with store.connect(path) as db:
        db.execute("INSERT INTO ade_skill_reads (user_id, since, created_at, updated_at) VALUES ('u', 0, 1, 1)")
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE ade_skill_reads DROP COLUMN polled_at")
    db.commit()
    assert "polled_at" not in {r[1] for r in db.execute("PRAGMA table_info(ade_skill_reads)")}
    db.close()
    with store.connect(path) as again:
        assert "polled_at" in {r[1] for r in again.execute("PRAGMA table_info(ade_skill_reads)")}
        assert again.execute("SELECT user_id, polled_at FROM ade_skill_reads").fetchall()[0][:] == ("u", None)


def test_the_daily_forget_deletes_by_time_through_an_index_of_the_time_alone(env):
    """`DELETE ... WHERE at < ?` is across owners, so an index that leads with the owner cannot serve it:
    the statement the forget runs is looked at, as the store plans it."""
    sent = []
    with store.connect() as db:
        db.set_trace_callback(sent.append)
        skill_counts.forget(db, 1_800_000_000)
        db.set_trace_callback(None)
        (delete,) = [s for s in sent if s.startswith("DELETE FROM ade_skill_calls")]
        plan = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + delete))
    assert "ade_skill_calls_when" in plan and "SCAN" not in plan, plan


# ---- the read: each line once ---------------------------------------------------------------------------------------

class Clock:
    """`time` as skill_counts sees it: `time()` is what the test set, everything else is the real module's."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def test_a_whole_read_is_stamped_with_the_time_it_began_not_the_time_it_ended(client, owner, ws, monkeypatch):
    """What a later read first finds was not in the record when this read began, so the hour runs from its start (a line
    the record took while this read ran is the next read's; stamped at the end, an hour of the next read's would be lost
    to the time this one took)."""
    h, user = owner
    ws.add()
    clock = Clock(float(int(time.time())))
    monkeypatch.setattr(skill_counts, "time", clock)
    real = ws.call

    def slow(endpoint, method, path, body=None, timeout=30):
        got = real(endpoint, method, path, body, timeout)
        clock.now += 600                                 # the read goes on for ten minutes after the page came
        return got
    monkeypatch.setattr(workspace_client, "call", slow)
    began = int(clock.now)
    assert read(user) == {"state": "read", "taken": 1}
    assert int(clock.now) == began + 600
    assert reads_of(user["id"])["read_at"] == began


def test_a_first_read_takes_each_line_once_and_a_second_takes_none(client, owner, ws):
    h, user = owner
    for _ in range(3):
        ws.add()
    assert read(user) == {"state": "read", "taken": 3}
    assert [c["line_id"] for c in calls_of(user["id"])] == [f"{n:016x}" for n in (1, 2, 3)]
    assert read(user) == {"state": "read", "taken": 0}
    assert len(calls_of(user["id"])) == 3
    row = reads_of(user["id"])
    assert (row["store_id"], row["since"], row["read_error"], row["unserved_at"]) == (STORE, 3, None, None)
    assert row["read_at"] is not None and row["read_behind_at"] is None


def test_each_page_is_asked_with_the_wait_the_manual_states_and_the_bound_on_a_read_holds(client, owner, ws):
    """A page's call waits up to PAGE_TIMEOUT seconds on its connection and no page is begun after READ_FOR seconds of a
    read: the round's bound that the manual states (about READ_FOR + PAGE_TIMEOUT seconds an owner, one after another),
    each number asked of the stand-in the app's read calls."""
    h, user = owner
    for _ in range(3):
        ws.add()
    ws.page_size = 1
    assert read(user) == {"state": "read", "taken": 3}
    assert len(ws.timeouts) >= 3 and set(ws.timeouts) == {skill_counts.PAGE_TIMEOUT}


def test_a_read_goes_on_from_the_number_the_last_one_ended_at(client, owner, ws):
    h, user = owner
    ws.add()
    ws.add()
    read(user)
    ws.add()
    assert read(user) == {"state": "read", "taken": 1}
    assert ws.paths() == ["/skills?since=0", "/skills?since=2"]


def test_each_line_is_kept_with_what_the_record_gave_of_it(client, owner, ws):
    h, user = owner
    ws.add(skill="deploy", kind="business", path="claude", failed=True, version="b" * 40, session=OTHER,
           repo="alpha", task="t-1")
    ws.add(skill="plain", kind="other", path="claude", failed=False, version=None)
    ws.add(skill="proofpane-core:plan", kind="core", path="typed", failed=None)
    read(user)
    got = calls_of(user["id"])
    assert [(c["skill"], c["kind"], c["path"], c["failed"], c["version"], c["session_id"], c["repo"], c["task_id"])
            for c in got] == [
        ("deploy", "business", "claude", 1, "b" * 40, OTHER, "alpha", "t-1"),
        ("plain", "other", "claude", 0, None, SESSION, None, None),
        ("proofpane-core:plan", "core", "typed", None, CORE_VERSION, SESSION, None, None)]
    assert [c["at"] for c in got] == [T0 + 1, T0 + 2, T0 + 3]


def test_the_pages_of_a_record_are_read_a_page_at_a_time_to_its_end(client, owner, ws):
    h, user = owner
    ws.page_size = 2
    for _ in range(5):
        ws.add()
    assert read(user) == {"state": "read", "taken": 5}
    assert ws.paths() == ["/skills?since=0", "/skills?since=2", "/skills?since=4"]


def test_a_read_takes_at_most_its_pages_and_the_next_goes_on(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "PAGES", 2)
    ws.page_size = 2
    for _ in range(5):
        ws.add()
    assert read(user) == {"state": "behind", "taken": 4}
    row = reads_of(user["id"])
    assert (row["since"], row["read_at"]) == (4, None) and row["read_behind_at"] is not None
    assert read(user) == {"state": "read", "taken": 1}
    row = reads_of(user["id"])
    assert row["since"] == 5 and row["read_at"] is not None
    assert len(calls_of(user["id"])) == 5


def test_no_page_is_begun_after_the_time_a_read_may_go_on(client, owner, ws, monkeypatch):
    """The first page is always asked for; one begun past READ_FOR is not (a call's time-out bounds each wait, not the
    whole read, and holds the poll's round for every owner while it lasts)."""
    h, user = owner
    monkeypatch.setattr(skill_counts, "READ_FOR", 0)
    ws.page_size = 1
    for _ in range(3):
        ws.add()
    assert read(user) == {"state": "behind", "taken": 1}
    assert ws.paths() == ["/skills?since=0"]


def test_a_line_is_taken_once_by_its_own_id_whatever_numbers_the_record_gives_it(client, owner, ws):
    """A record the workspace made anew (one put aside because it could not be read) reads each session's file from its
    start again, and gives the same lines back under new numbers: a count by number would take them twice."""
    h, user = owner
    first = [ws.add(skill="a"), ws.add(skill="b")]
    read(user)
    ws.renew("6" * 32)
    for row in first:                               # the same lines (their ids), numbered from 1 again
        ws.add(**{k: row[k] for k in ("id", "at", "skill")})
    ws.add(skill="c")                               # and one the new record has that the old had not
    assert read(user) == {"state": "read", "taken": 1}
    assert sorted(c["skill"] for c in calls_of(user["id"])) == ["a", "b", "c"]


def test_a_record_with_another_id_is_read_from_its_start(client, owner, ws):
    h, user = owner
    for _ in range(3):
        ws.add()
    read(user)
    ws.renew("6" * 32)
    ws.add(skill="new", id="9" * 16)
    assert read(user) == {"state": "read", "taken": 1}
    # asked from the old number first, which is not the new record's page; then from its start
    assert ws.paths() == ["/skills?since=0", "/skills?since=3", "/skills?since=0"]
    row = reads_of(user["id"])
    assert (row["store_id"], row["since"]) == ("6" * 32, 1)


def test_the_first_page_of_a_record_the_app_has_not_read_is_taken_whole_with_no_second_ask(client, owner, ws):
    h, user = owner
    ws.add()
    read(user)
    assert ws.paths() == ["/skills?since=0"]


def test_a_record_that_counts_from_less_than_the_app_read_to_is_read_from_its_start(client, owner, ws):
    """The same id, and a newest number below the one the app read to: the app does not trust its number, reads from the
    start and takes each line once by its id; the numbers it finds missing are not counted missed."""
    h, user = owner
    for _ in range(3):
        ws.add()
    read(user)
    ws.lines, ws.next = [], 2                       # the id is kept, the numbers begin again, at 2
    ws.add(skill="new", id="f" * 16)
    assert read(user) == {"state": "read", "taken": 1}
    assert ws.paths() == ["/skills?since=0", "/skills?since=3", "/skills?since=0"]
    row = reads_of(user["id"])
    assert (row["since"], row["missed"]) == (2, 0)  # 2 is the oldest held, read from 0: nothing of it counted missed


# ---- the numbers a record skipped -----------------------------------------------------------------------------------

def test_the_numbers_the_record_skipped_between_two_reads_are_counted_missed(client, owner, ws):
    """Lines the workspace dropped, past its 30 days or 20,000 lines, before the app read them: the numbers between the
    one the app read to and the oldest the record holds."""
    h, user = owner
    for _ in range(3):
        ws.add()
    read(user)
    for _ in range(4):
        ws.add()                                    # 4, 5, 6, 7
    ws.drop(5)                                      # 4 and 5 are gone before the app reads them
    assert read(user) == {"state": "read", "taken": 2}
    assert reads_of(user["id"])["missed"] == 2
    assert len(calls_of(user["id"])) == 5
    ws.add()
    ws.drop(7)                                      # 8 is the only one held, the app read to 7: nothing skipped
    read(user)
    assert reads_of(user["id"])["missed"] == 2


def test_lines_dropped_before_the_app_ever_read_a_record_are_not_missed(client, owner, ws):
    h, user = owner
    for _ in range(5):
        ws.add()
    ws.drop(3)                                      # the record's oldest is 4 when the app first reads it
    read(user)
    assert reads_of(user["id"])["missed"] == 0
    ws.renew("6" * 32)
    for _ in range(5):
        ws.add()
    ws.drop(3)                                      # nor those before the app's first read of a record made anew
    read(user)
    assert reads_of(user["id"])["missed"] == 0


def test_missed_numbers_add_up_over_reads_and_survive_a_record_made_anew(client, owner, ws):
    h, user = owner
    ws.add()
    read(user)
    ws.add()
    ws.add()
    ws.drop(2)                                      # 2 skipped, 3 held (app read to 1)
    read(user)
    assert reads_of(user["id"])["missed"] == 1
    ws.add()
    ws.add()
    ws.drop(4)                                      # 4 skipped (app read to 3), 5 held
    read(user)
    assert reads_of(user["id"])["missed"] == 1 + 1
    ws.renew("6" * 32)
    ws.add(id="9" * 16)
    read(user)
    assert reads_of(user["id"])["missed"] == 2


def test_the_workspace_s_own_count_of_lines_out_of_shape_is_kept(client, owner, ws):
    h, user = owner
    ws.add()
    ws.dropped = 4
    read(user)
    assert reads_of(user["id"])["dropped"] == 4
    ws.dropped = 6
    ws.add()
    read(user)
    assert reads_of(user["id"])["dropped"] == 6


# ---- a workspace without the route, and a read that fails -----------------------------------------------------------

def test_a_workspace_that_does_not_serve_the_route_is_read_as_having_no_lines(client, owner, ws, capsys):
    h, user = owner
    ws.routes = False
    assert read(user) == {"state": "unserved", "taken": 0}
    row = reads_of(user["id"])
    assert row["unserved_at"] is not None and row["read_error"] is None and row["read_at"] is None
    assert "skills:" not in capsys.readouterr().err         # not a failure: an older image has no such route
    ws.routes = True
    ws.add()
    assert read(user) == {"state": "read", "taken": 1}
    row = reads_of(user["id"])
    assert row["unserved_at"] is None and row["read_at"] is not None


FAILURES = [
    (workspace_client.WorkspaceCallError(400, "the workspace's own words, which a program there chose"),
     "The workspace refused the read of its skill calls (400)."),
    (workspace_client.WorkspaceCallError(404, "no such thing, with a reason"),
     "The workspace refused the read of its skill calls (404)."),
    (workspace_client.WorkspaceCallError(409, "busy"), "The workspace refused the read of its skill calls (409)."),
    (workspace_client.WorkspaceCallError(412, "late"), "The workspace refused the read of its skill calls (412)."),
    (workspace_client.WorkspaceCallError(502, "The workspace answered 500.", answered=500),
     "The workspace answered 500."),
    (workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING), skill_counts.NOT_ANSWERED),
    (RuntimeError("secret words of an error"),
     "The app failed reading the workspace's skill calls (RuntimeError)."),
]


@pytest.mark.parametrize("failure,words", FAILURES)
def test_a_failed_read_is_said_in_the_apps_own_words_and_kept_with_since_when(client, owner, ws, capsys, failure, words):
    h, user = owner
    ws.add()
    ws.fail = failure
    assert read(user) == {"state": "failed", "taken": 0}
    row = reads_of(user["id"])
    assert row["read_error"] == words and row["read_error_at"] is not None
    assert row["unserved_at"] is None
    err = capsys.readouterr().err
    assert words in err and "secret words" not in err and "program there chose" not in err
    # the same failure again keeps its first time
    with store.connect() as db:
        db.execute("UPDATE ade_skill_reads SET read_error_at = 7 WHERE user_id = ?", (user["id"],))
    read(user)
    assert reads_of(user["id"])["read_error_at"] == 7
    # a read that goes through clears it
    ws.fail = None
    assert read(user)["state"] == "read"
    row = reads_of(user["id"])
    assert (row["read_error"], row["read_error_at"]) == (None, None) and row["read_at"] is not None


def test_a_failure_after_a_page_that_said_more_follow_is_behind_and_failed_and_keeps_that_page(client, owner, ws,
                                                                                                    monkeypatch):
    h, user = owner
    ws.page_size = 1
    for _ in range(3):
        ws.add()
    real = ws.call

    def second_fails(endpoint, method, path, body=None, timeout=30):
        if len(ws.calls) == 1:
            ws.calls.append((endpoint, method, path))
            raise workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING)
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", second_fails)
    assert read(user) == {"state": "failed", "taken": 1}
    row = reads_of(user["id"])
    assert row["since"] == 1 and row["read_behind_at"] is not None and row["read_at"] is None
    assert row["read_error"] == skill_counts.NOT_ANSWERED
    assert len(calls_of(user["id"])) == 1


# ---- a page, and a line, out of shape -------------------------------------------------------------------------------

def good_page(**kw):
    page = {"store": STORE, "seq": 1, "more": False, "first": 1, "dropped": 0, "lines": [served(1)]}
    page.update(kw)
    return page


def lines(*numbers):
    return [served(n) for n in numbers]


PAGES_NOT_FORMED = [
    ("not an object", lambda p: ["not", "a", "page"]),
    ("none", lambda p: None),
    ("a store that is not 32 hex characters", lambda p: dict(p, store="short")),
    ("a store in upper case", lambda p: dict(p, store="F" * 32)),
    ("no store", lambda p: {k: v for k, v in p.items() if k != "store"}),
    ("a negative seq", lambda p: dict(p, seq=-1)),
    ("a seq that is a bool", lambda p: dict(p, seq=True)),
    ("a seq that is a string", lambda p: dict(p, seq="1")),
    ("a seq that is a float", lambda p: dict(p, seq=1.0)),
    ("a seq past 2**53", lambda p: dict(p, seq=1 << 60)),
    ("a more that is not a bool", lambda p: dict(p, more=1)),
    ("a more that is none", lambda p: dict(p, more=None)),
    ("a first of zero", lambda p: dict(p, first=0)),
    ("a first that is a bool", lambda p: dict(p, first=True)),
    ("a first that is a string", lambda p: dict(p, first="1")),
    ("a negative dropped", lambda p: dict(p, dropped=-1)),
    ("a dropped that is a bool", lambda p: dict(p, dropped=False)),
    ("a dropped that is none", lambda p: dict(p, dropped=None)),
    ("lines that are not a list", lambda p: dict(p, lines={})),
    ("no lines key", lambda p: {k: v for k, v in p.items() if k != "lines"}),
    ("more lines than a page", lambda p: dict(p, seq=skill_counts.PAGE + 1, lines=lines(*range(1, skill_counts.PAGE + 2)))),
    ("a line that is not an object", lambda p: dict(p, lines=["x"])),
    ("a first line numbered 0, not above the number asked from", lambda p: dict(p, seq=0, lines=lines(0))),
    ("lines numbered twice", lambda p: dict(p, seq=2, lines=lines(1, 1, 2))),
    ("lines out of order", lambda p: dict(p, seq=3, lines=lines(2, 1, 3))),
    ("a line number that is a string", lambda p: dict(p, lines=[dict(served(1), n="1")])),
    ("a line number that is a bool", lambda p: dict(p, lines=[dict(served(1), n=True)])),
    ("a seq that is not the last line's number", lambda p: dict(p, seq=2)),
    ("more lines to come after a page with none", lambda p: dict(p, more=True, seq=0, lines=[])),
]


@pytest.mark.parametrize("name,change", PAGES_NOT_FORMED, ids=[n for n, _ in PAGES_NOT_FORMED])
def test_a_page_not_in_the_form_the_record_gives_is_taken_in_no_part(client, owner, ws, capsys, name, change):
    h, user = owner
    ws.reply = lambda page: change(good_page())
    assert read(user) == {"state": "failed", "taken": 0}
    assert calls_of(user["id"]) == []
    row = reads_of(user["id"])
    assert (row["read_error"], row["since"], row["store_id"]) == (skill_counts.NOT_FORMED, 0, None)
    assert skill_counts.NOT_FORMED in capsys.readouterr().err


MISSING = object()
LINES_OUT_OF_SHAPE = [
    ("an extra field", {"extra": 1}),
    ("a missing task", {"task": MISSING}),
    ("a missing version", {"version": MISSING}),
    ("an id of the wrong length", {"id": "abc"}),
    ("an id in upper case", {"id": "A" * 16}),
    ("an id that is a number", {"id": 12345}),
    ("a time that is a bool", {"at": True}),
    ("a time of zero", {"at": 0}),
    ("a negative time", {"at": -5}),
    ("a time that is a float", {"at": 1.5}),
    ("a time more than a day ahead", {"at": int(time.time()) + 2 * 86400}),
    ("an empty skill name", {"skill": ""}),
    ("a skill name that starts with a dash", {"skill": "-x"}),
    ("a skill name of 81 characters", {"skill": "a" * 81}),
    ("a skill name with a space", {"skill": "a b"}),
    ("a skill name that is a number", {"skill": 5}),
    ("an unknown kind", {"kind": "mystery"}),
    ("an unknown kind with a commit as its version", {"kind": "mystery", "version": "b" * 40}),
    ("a kind that is none", {"kind": None}),
    ("an unknown path", {"path": "phone"}),
    ("a typed call with an outcome", {"path": "typed", "failed": False}),
    ("a call Claude chose with no outcome", {"failed": None}),
    ("an outcome that is a number", {"failed": 1}),
    ("an outcome that is a string", {"failed": "no"}),
    ("a typed call of another skill", {"path": "typed", "failed": None, "kind": "other", "version": None}),
    ("another skill with a version", {"kind": "other", "version": "x"}),
    ("a core skill with a commit as its version", {"version": "b" * 40}),
    ("a core skill with no version", {"version": None}),
    ("a business skill with a core version", {"kind": "business", "version": CORE_VERSION}),
    ("a business skill with a commit in upper case", {"kind": "business", "version": "B" * 40}),
    ("a business skill with no version", {"kind": "business", "version": None}),
    ("a session of the wrong form", {"session": "ABCDEF123456"}),
    ("a short session", {"session": "short"}),
    ("no session", {"session": None}),
    ("a repository that starts with a dash", {"repo": "-x"}),
    ("a repository that is a number", {"repo": 5}),
    ("a task that is a space", {"task": " "}),
    ("a task of 81 characters", {"task": "a" * 81}),
]


def with_change(row, change):
    out = dict(row)
    for key, value in change.items():
        if value is MISSING:
            out.pop(key, None)
        else:
            out[key] = value
    return out


@pytest.mark.parametrize("name,change", LINES_OUT_OF_SHAPE, ids=[n for n, _ in LINES_OUT_OF_SHAPE])
def test_a_line_out_of_shape_is_refused_and_counted_and_the_others_taken(client, owner, ws, name, change):
    h, user = owner
    ws.add()
    ws.lines.append(with_change(served(2), change))
    ws.next = 3
    ws.add(skill="after")
    assert read(user) == {"state": "read", "taken": 2}
    assert sorted(c["skill"] for c in calls_of(user["id"])) == ["after", "proofpane-core:verify"]
    row = reads_of(user["id"])
    assert (row["refused"], row["since"]) == (1, 3)


GOOD_LINES = [
    ("a core skill's typed call", {"path": "typed", "failed": None}),
    ("a business skill at an uncommitted folder", {"kind": "business", "version": "uncommitted"}),
    ("a business skill of unknown version", {"kind": "business", "version": "unknown"}),
    ("a business skill at a 64 character commit", {"kind": "business", "version": "c" * 64}),
    ("a core skill whose folder was too large to digest", {"version": "1.0.0+cut"}),
    ("a core skill whose folder could not be read", {"version": "1.0.0+unknown"}),
    ("another skill", {"kind": "other", "version": None}),
    ("a failed call", {"failed": True}),
    ("a call in a repository and a task", {"repo": "alpha.beta-1", "task": "task:1.2"}),
    ("a business skill's typed call", {"kind": "business", "path": "typed", "failed": None, "version": "d" * 40}),
]


@pytest.mark.parametrize("name,change", GOOD_LINES, ids=[n for n, _ in GOOD_LINES])
def test_every_form_the_hook_writes_is_taken(client, owner, ws, name, change):
    h, user = owner
    ws.lines.append(with_change(served(1), change))
    ws.next = 2
    assert read(user) == {"state": "read", "taken": 1}
    assert reads_of(user["id"])["refused"] == 0


def test_a_line_past_the_days_kept_is_not_taken_and_is_not_refused_either(client, owner, ws):
    h, user = owner
    old = skill_counts._cutoff(int(time.time())) - 1
    ws.add(at=old)
    ws.add(at=old + 1)
    ws.add(at=old + 2)
    assert read(user) == {"state": "read", "taken": 2}
    assert [c["at"] for c in calls_of(user["id"])] == [old + 1, old + 2]
    row = reads_of(user["id"])
    assert (row["refused"], row["since"]) == (0, 3)


# ---- the days kept --------------------------------------------------------------------------------------------------

def put(db, user_id, line_id, at, skill="s", kind="other"):
    db.execute("INSERT INTO ade_skill_calls (user_id, line_id, at, skill, kind, path, failed, version, session_id, "
               "taken_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (user_id, line_id, at, skill, kind, "claude", 0, None, SESSION, at))


def test_a_call_goes_when_its_utc_day_is_more_than_ninety_days_before_todays(client, owner):
    """The oldest day goes first and whole, so that the oldest day shown is complete: a call stays while its UTC day is
    within KEPT_DAYS days of today's, though it is some hours older than that by the clock."""
    h, user = owner
    now = 1_800_000_000 // 86400 * 86400 + 15 * 3600          # 15:00 UTC
    cutoff = (now // 86400 - 90) * 86400
    assert now - cutoff > 90 * 86400
    with store.connect() as db:
        put(db, user["id"], "a" * 16, cutoff - 1)
        put(db, user["id"], "b" * 16, cutoff)
        put(db, user["id"], "c" * 16, cutoff + 86399)
        put(db, user["id"], "d" * 16, now)
        skill_counts.forget(db, now)
        left = sorted(r[0] for r in db.execute("SELECT line_id FROM ade_skill_calls"))
    assert left == ["b" * 16, "c" * 16, "d" * 16]


def test_the_polls_forget_drops_old_calls_and_a_failing_one_stops_nothing_else(client, owner, monkeypatch, capsys):
    h, user = owner
    now = int(time.time())
    with store.connect() as db:
        put(db, user["id"], "a" * 16, skill_counts._cutoff(now) - 1)
        put(db, user["id"], "b" * 16, now)
        agents.forget(db, now)
        assert [r[0] for r in db.execute("SELECT line_id FROM ade_skill_calls")] == ["b" * 16]

    def broken(db, now):
        raise RuntimeError("broken")
    monkeypatch.setattr(skill_counts, "forget", broken)
    with store.connect() as db:
        agents.forget(db, now)                      # said, and the rest of the prune ran
    assert "agents: skill_counts' prune failed: RuntimeError" in capsys.readouterr().err


# ---- the most calls kept for one owner ------------------------------------------------------------------------------

def test_an_owner_s_calls_past_the_most_kept_go_oldest_first_and_are_counted(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 5)
    for _ in range(8):
        ws.add()
    assert read(user) == {"state": "read", "taken": 8}
    assert [c["line_id"] for c in calls_of(user["id"])] == [f"{n:016x}" for n in range(4, 9)]
    assert reads_of(user["id"])["trimmed"] == 3
    assert skill_counts.view(user, 7)["read"]["trimmed"] == 3
    assert skill_counts.view(user, 7)["totals"]["calls"] == 5


def test_the_calls_dropped_are_the_oldest_by_their_time_not_by_their_number_or_by_when_they_were_taken(client, owner, ws,
                                                                                                       monkeypatch):
    """Numbers and times do not run together (a session's clock, a late line): the line numbered 2 is the oldest, and of
    the two lines of one time the lower id (4) goes before the higher."""
    h, user = owner
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 3)
    ws.add(at=T0 + 50)                              # 1
    ws.add(at=T0 + 10)                              # 2: the oldest
    ws.add(at=T0 + 40)                              # 3
    ws.add(at=T0 + 20)                              # 4
    ws.add(at=T0 + 20)                              # 5: the same time as 4, a later id
    read(user)
    assert [(c["line_id"], c["at"]) for c in calls_of(user["id"])] == [
        (f"{5:016x}", T0 + 20), (f"{3:016x}", T0 + 40), (f"{1:016x}", T0 + 50)]
    assert reads_of(user["id"])["trimmed"] == 2


def test_a_tie_in_time_drops_the_lower_line_id_first(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 2)
    for n in (3, 1, 2):
        ws.add(id=f"{n:016x}", at=T0 + 7)
    read(user)
    assert [c["line_id"] for c in calls_of(user["id"])] == [f"{2:016x}", f"{3:016x}"]
    assert reads_of(user["id"])["trimmed"] == 1


def test_what_is_dropped_adds_up_over_reads_and_a_read_that_takes_nothing_new_drops_nothing(client, owner, ws,
                                                                                            monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 4)
    for _ in range(6):
        ws.add()
    read(user)
    assert reads_of(user["id"])["trimmed"] == 2
    assert read(user) == {"state": "read", "taken": 0}
    assert reads_of(user["id"])["trimmed"] == 2 and len(calls_of(user["id"])) == 4
    for _ in range(3):
        ws.add()
    read(user)
    assert reads_of(user["id"])["trimmed"] == 5 and len(calls_of(user["id"])) == 4
    assert calls_of(user["id"])[-1]["line_id"] == f"{9:016x}"


def test_calls_up_to_the_most_are_all_kept_and_nothing_is_counted_dropped(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 6)
    for _ in range(6):
        ws.add()
    read(user)
    assert len(calls_of(user["id"])) == 6 and reads_of(user["id"])["trimmed"] == 0


def test_one_owner_s_drops_never_touch_another_s_calls_even_with_the_same_line_ids(client, owner, ws, monkeypatch):
    """The other owner holds the oldest calls of all, under the line ids 1 to 5; this owner's calls have the same ids and
    run the other way in time (the id 5 is the oldest), so a drop that looks at another owner's calls, in the choosing or
    in the deleting, drops the wrong ones or too many."""
    h, user = owner
    other = signed_up(client, "admin2@example.com")
    other_user = auth.user(client.get("/api/auth/me", headers=other).json()["id"])
    for n in range(1, 6):
        ws.add(at=T0 - 1000 + n)
    read(other_user)
    monkeypatch.setattr(skill_counts, "CALLS_MOST", 3)
    ws.lines, ws.next = [], 1
    for n in range(1, 6):
        ws.add(at=T0 + 6 - n)
    read(user)
    assert [c["line_id"] for c in calls_of(user["id"])] == [f"{n:016x}" for n in (3, 2, 1)]
    assert reads_of(user["id"])["trimmed"] == 2
    assert [c["line_id"] for c in calls_of(other_user["id"])] == [f"{n:016x}" for n in (1, 2, 3, 4, 5)]
    assert reads_of(other_user["id"])["trimmed"] == 0


def test_the_most_kept_for_an_owner_is_a_hundred_thousand(client, owner, ws):
    """The real size: an owner at the most keeps that many, takes the new line and loses the oldest."""
    h, user = owner
    assert skill_counts.CALLS_MOST == 100_000
    with store.connect() as db:
        db.executemany("INSERT INTO ade_skill_calls (user_id, line_id, at, skill, kind, path, failed, version, "
                       "session_id, taken_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       [(user["id"], f"{(1 << 40) + i:016x}", T0 - 100_000 + i, "s", "other", "claude", 0, None, SESSION,
                         T0) for i in range(100_000)])
    ws.add()
    assert read(user) == {"state": "read", "taken": 1}
    with store.connect() as db:
        count, oldest = db.execute("SELECT COUNT(*), MIN(at) FROM ade_skill_calls WHERE user_id = ?",
                                   (user["id"],)).fetchone()
        newest = db.execute("SELECT line_id FROM ade_skill_calls WHERE user_id = ? ORDER BY at DESC LIMIT 1",
                            (user["id"],)).fetchone()[0]
    assert (count, oldest, newest) == (100_000, T0 - 100_000 + 1, f"{1:016x}")
    assert reads_of(user["id"])["trimmed"] == 1


# ---- one read at a time ---------------------------------------------------------------------------------------------

def test_a_read_asked_for_while_one_runs_returns_busy_and_the_one_running_goes_round_once_more(client, owner, ws,
                                                                                                monkeypatch):
    h, user = owner
    ws.add()
    inner = []
    real = ws.call

    def reading_again(endpoint, method, path, body=None, timeout=30):
        if not inner:
            inner.append(skill_counts.sync(user, endpoint))      # another asks while this one runs
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", reading_again)
    got = read(user)
    assert inner == [{"state": "busy", "taken": 0}]
    assert got == {"state": "read", "taken": 1}
    assert ws.paths() == ["/skills?since=0", "/skills?since=1"]      # once more, from where the first read ended
    assert skill_counts._syncing == set() and skill_counts._again == set()


def test_a_read_that_raises_frees_the_account_for_the_next(client, owner, ws, monkeypatch):
    h, user = owner

    class Stop(BaseException):
        pass

    def boom(user, endpoint, call):
        raise Stop()
    monkeypatch.setattr(skill_counts, "_sync", boom)
    with pytest.raises(Stop):
        read(user)
    assert skill_counts._syncing == set() and skill_counts._again == set()


# ---- the read, as the round and the page make it --------------------------------------------------------------------

def test_a_read_with_the_machine_down_asks_nothing_and_starts_nothing(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    assert skill_counts.read(user) == {"state": "not_running", "taken": 0}
    assert ws.calls == [] and reads_of(user["id"]) is None


def test_a_read_never_raises(client, owner, ws, monkeypatch, capsys):
    h, user = owner
    monkeypatch.setattr(skill_counts, "sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broken")))
    assert skill_counts.read(user) == {"state": "failed", "taken": 0}
    assert "skills: a read failed: RuntimeError: broken" in capsys.readouterr().err

    def no_endpoint(user):
        raise ValueError("no machine")
    monkeypatch.setattr(agents, "_endpoint", no_endpoint)
    assert skill_counts.read(user) == {"state": "failed", "taken": 0}


def test_a_read_with_the_machine_up_reads_through_the_app_s_own_call(client, owner, ws):
    h, user = owner
    ws.add()
    assert skill_counts.read(user) == {"state": "read", "taken": 1}
    assert ws.calls == [("http://workspace.test", "GET", "/skills?since=0")]


def test_an_owner_read_for_before_is_listed_for_the_round_once_an_hour_from_its_last_whole_read(client, owner, ws):
    """What a read saves is what the workspace's record holds and the app has not taken, whether or not
    the owner has a call in the app's table, so the round reads each owner it has read for once an hour, by the time of
    its last whole read, and not by a call of the last thirty days."""
    h, user = owner
    now = 1_800_000_000
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, now) == []                       # none read for yet
        skill_counts._ensure(db, user["id"], now)
        assert skill_counts.users_to_poll(db, now) == [user["id"]]             # read for, never whole: due
        # the hour is spelled out, not read from the constant: the manual says an hour
        for age, listed in ((1800, False), (3599, False), (3600, True), (3601, True)):
            db.execute("UPDATE ade_skill_reads SET read_at = ? WHERE user_id = ?", (now - age, user["id"]))
            assert (skill_counts.users_to_poll(db, now) == [user["id"]]) is listed, age
    assert skill_counts.POLL_EVERY == 3600


def test_an_owner_with_no_call_in_the_app_s_table_is_listed_all_the_same_and_one_never_read_for_is_not(client, owner):
    h, user = owner
    other = make_user(client, "admin2@example.com")
    third = make_user(client, "admin3@example.com")
    now = 1_800_000_000
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], now)
        db.execute("UPDATE ade_skill_reads SET read_at = ? WHERE user_id = ?", (now - 7200, user["id"]))
        put(db, other["id"], "a" * 16, now - 60)                       # calls, and no read of the app's: not its owner yet
        assert db.execute("SELECT COUNT(*) FROM ade_skill_calls WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0
        assert skill_counts.users_to_poll(db, now) == [user["id"]]
        assert third["id"] not in skill_counts.users_to_poll(db, now)


def test_an_answer_that_the_workspace_serves_no_route_counts_as_a_read_for_the_hour(client, owner):
    h, user = owner
    now = 1_800_000_000
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], now)
        db.execute("UPDATE ade_skill_reads SET read_at = ? WHERE user_id = ?", (now - 7200, user["id"]))
        assert skill_counts.users_to_poll(db, now) == [user["id"]]
        for age, listed in ((3599, False), (3600, True)):
            db.execute("UPDATE ade_skill_reads SET unserved_at = ? WHERE user_id = ?", (now - age, user["id"]))
            assert (skill_counts.users_to_poll(db, now) == [user["id"]]) is listed, age


def test_a_read_that_stopped_short_leaves_the_owner_due_in_the_next_round(client, owner, ws, monkeypatch):
    h, user = owner
    for n in range(5):
        ws.add()
    ws.page_size = 2
    with monkeypatch.context() as m:
        m.setattr(skill_counts, "PAGES", 1)                        # one page of two: the read is behind, not whole
        assert skill_counts.read(user)["state"] == "behind"
    row = reads_of(user["id"])
    assert row["read_behind_at"] is not None and row["polled_at"] is None
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == [user["id"]]
    assert skill_counts.read(user)["state"] == "read"              # a whole read, and the hour begins
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == []
        assert skill_counts.users_to_poll(db, int(time.time()) + skill_counts.POLL_EVERY + 1) == [user["id"]]


def test_a_read_that_stopped_short_is_due_again_though_a_whole_read_came_within_the_hour(client, owner):
    """A read that stops short sets only `read_behind_at`, and the hour counted from the last whole read once hid it, so
    a catch-up that the docstring and the manual said came in the next round waited up to an hour. An owner whose last
    read stopped short after the last look of any kind is due; a look after it settles it."""
    h, user = owner
    now = 1_800_000_000
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], now)

        def due(**columns):
            db.execute("UPDATE ade_skill_reads SET " + ", ".join(f"{c} = ?" for c in columns) + " WHERE user_id = ?",
                       [*columns.values(), user["id"]])
            return skill_counts.users_to_poll(db, now) == [user["id"]]
        none = dict(read_at=None, unserved_at=None, polled_at=None, read_behind_at=None)
        assert not due(**{**none, "read_at": now - 600})                  # a whole read ten minutes ago: not due
        assert due(read_behind_at=now - 5)                                # a read after it stopped short: due
        assert not due(read_at=now - 1)                                   # a whole read after that: caught up
        assert due(read_at=now - 600)                                     # (behind again, newer than the whole read)
        assert not due(polled_at=now - 1)                                 # a failed look, or a machine down, after it
        assert due(polled_at=None)
        assert not due(unserved_at=now - 1)                               # an answer that no route is served, after it
        assert not due(**{**none, "read_at": now - 5, "read_behind_at": now - 5})   # in one second: not older
        assert due(read_behind_at=now - 4)                                # a second after it: due


@pytest.mark.parametrize("age, listed", [(1800, False), (3599, False), (3600, True), (3601, True)])
def test_an_owner_looked_at_less_than_an_hour_ago_is_not_due_whatever_the_look_found(client, owner, age, listed):
    """An owner whose machine is down, or whose reads fail, has no whole read to wait an hour from, so each
    round once looked at its machine again. What the look found is kept as `polled_at`, and the hour runs from it."""
    h, user = owner
    now = 1_800_000_000
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], now)
        db.execute("UPDATE ade_skill_reads SET polled_at = ? WHERE user_id = ?", (now - age, user["id"]))
        assert (skill_counts.users_to_poll(db, now) == [user["id"]]) is listed


def test_a_look_that_finds_the_machine_down_is_kept_for_the_hour_and_makes_no_row(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    assert skill_counts.read(user) == {"state": "not_running", "taken": 0}
    assert reads_of(user["id"]) is None                           # an owner the app has no row for is given none
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], int(time.time()) - 7200)
    before = int(time.time())
    assert skill_counts.read(user) == {"state": "not_running", "taken": 0}
    row = reads_of(user["id"])
    assert row["polled_at"] is not None and before <= row["polled_at"] <= int(time.time())
    assert (row["read_at"], row["unserved_at"], row["read_error"]) == (None, None, None)
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == []
        assert skill_counts.users_to_poll(db, int(time.time()) + skill_counts.POLL_EVERY) == [user["id"]]


@pytest.mark.parametrize("fail", [RuntimeError("the workspace broke"),
                                  workspace_client.NotAnswering(502, workspace_client.NOT_ANSWERING),
                                  workspace_client.WorkspaceCallError(500, "no")])
def test_a_read_that_failed_is_not_tried_again_for_the_hour(client, owner, ws, fail):
    h, user = owner
    ws.add()
    ws.fail = fail
    before = int(time.time())
    assert skill_counts.read(user)["state"] == "failed"
    row = reads_of(user["id"])
    assert row["read_error"] is not None and before <= row["polled_at"] <= int(time.time())
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == []
        assert skill_counts.users_to_poll(db, int(time.time()) + skill_counts.POLL_EVERY) == [user["id"]]


def test_an_endpoint_that_cannot_be_made_is_a_failed_look_kept_for_the_hour_too(client, owner, ws, monkeypatch, capsys):
    h, user = owner
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], int(time.time()) - 7200)

    def no_endpoint(user):
        raise ValueError("no machine")
    monkeypatch.setattr(agents, "_endpoint", no_endpoint)
    assert skill_counts.read(user) == {"state": "failed", "taken": 0}
    assert reads_of(user["id"])["polled_at"] is not None
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == []


def test_a_look_that_finds_the_machine_down_stamps_that_owner_only(client, owner, ws, monkeypatch):
    h, user = owner
    other = make_user(client, "bystander@example.com")
    with store.connect() as db:
        for u in (user, other):
            skill_counts._ensure(db, u["id"], int(time.time()) - 7200)
    monkeypatch.setattr(agents, "_endpoint", lambda u: None)
    skill_counts.read(user)
    assert reads_of(user["id"])["polled_at"] is not None and reads_of(other["id"])["polled_at"] is None


def test_a_read_that_finds_another_running_is_no_look_and_leaves_the_owner_due(client, owner, ws, monkeypatch):
    h, user = owner
    with store.connect() as db:
        skill_counts._ensure(db, user["id"], int(time.time()) - 7200)
    monkeypatch.setattr(skill_counts, "_syncing", {user["id"]})          # a read of this owner is running
    assert skill_counts.read(user) == {"state": "busy", "taken": 0}
    assert reads_of(user["id"])["polled_at"] is None
    with store.connect() as db:
        assert skill_counts.users_to_poll(db, int(time.time())) == [user["id"]]


def test_a_look_whose_time_cannot_be_kept_stops_nothing(client, owner, ws, monkeypatch, capsys):
    h, user = owner
    monkeypatch.setattr(agents, "_endpoint", lambda u: None)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(skill_counts.store, "connect", broken)
    assert skill_counts.read(user) == {"state": "not_running", "taken": 0}
    assert "the time of a look was not kept: OperationalError" in capsys.readouterr().err


def test_a_whole_read_and_an_answer_of_no_route_are_not_looks_of_the_kind_polled_at_keeps(client, owner, ws):
    """Their own times (`read_at`, `unserved_at`) hold the hour; `polled_at` stays what only a look that found nothing
    to read sets."""
    h, user = owner
    ws.add()
    assert skill_counts.read(user)["state"] == "read" and reads_of(user["id"])["polled_at"] is None
    ws.routes = False
    assert skill_counts.read(user)["state"] == "unserved" and reads_of(user["id"])["polled_at"] is None


# ---- the chain, with nothing in a neighbour's place ----------------------------------------------------------------

def test_the_hook_the_record_and_the_app_s_read_work_as_one_chain(client, owner, tmp_path):
    """Each part is tested with a stand-in for its neighbours (the hook writes a file, the record takes
    files, this app reads a `Record` that is a copy of the record's page), so a change that fits each stand-in and not
    the others would leave every one of them green. Here the real hook, run as `skills_settings` gives it, writes a
    session's file; the real record takes it and pages it; the app's own read counts the page and its view shows it."""
    from proofpane_workspace import settings as ws_settings
    from proofpane_workspace.skillcalls import SkillCalls
    h, user = owner
    core = "proofpane-core:ai-risk-assessment"
    session_dir = tmp_path / "sessions" / SESSION
    session_dir.mkdir(parents=True)
    made = ws_settings.skills_settings(sys.executable, os.path.join(ROOT, "proofpane_workspace"), (2, 1, 304))["hooks"]
    events = [
        ("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": core}}),
        ("PostToolUseFailure", {"hook_event_name": "PostToolUseFailure", "tool_name": "Skill",
                                "tool_input": {"skill": core}}),
        ("UserPromptExpansion", {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command",
                                 "command_name": core}),
        ("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": "elsewhere"}})]
    for name, event in events:
        (hook,) = [x for group in made[name] for x in group["hooks"]]
        done = subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PP_SESSION_DIR": str(session_dir)},
                              timeout=30)
        assert done.returncode == 0 and done.stdout == "", name
    record = SkillCalls(str(tmp_path / "ws" / "skills"))
    assert record.take(SESSION, str(session_dir), "demo", None) == 4

    def call(endpoint, method, path, body=None, timeout=30):
        assert (method, path.split("?")[0]) == ("GET", "/skills")
        return record.page(int(path.split("since=")[1]))
    assert skill_counts.sync(user, "http://workspace.test", call) == {"state": "read", "taken": 4}
    kept = {(c["skill"], c["kind"], c["path"], c["failed"], c["repo"], c["session_id"]) for c in calls_of(user["id"])}
    assert kept == {(core, "core", "claude", 0, "demo", SESSION), (core, "core", "claude", 1, "demo", SESSION),
                    (core, "core", "typed", None, "demo", SESSION), ("elsewhere", "other", "claude", 0, "demo", SESSION)}
    got = skill_counts.view(user, days=7)
    rows = {(r["kind"], r["skill"]): r for r in got["skills"]}
    assert (got["totals"]["calls"], got["totals"]["failed"], got["totals"]["typed"], got["totals"]["skills"]) == (4, 1, 1, 2)
    assert (rows[("core", core)]["calls"], rows[("core", core)]["failed"], rows[("core", core)]["typed"]) == (3, 1, 1)
    assert rows[("other", "elsewhere")]["calls"] == 1
    assert re.fullmatch(r"1\.\d+\.\d+\+[0-9a-f]{64}", rows[("core", core)]["latest_version"] or "")
    assert skill_counts.sync(user, "http://workspace.test", call) == {"state": "read", "taken": 0}      # none twice


# ---- one owner's lines are never another's --------------------------------------------------------------------------

def test_one_owner_s_lines_are_never_another_s_and_their_ids_do_not_collide(client, owner, ws):
    h, user = owner
    other = signed_up(client, "admin2@example.com")
    other_user = auth.user(client.get("/api/auth/me", headers=other).json()["id"])
    ws.add(skill="mine")
    read(user)
    ws.lines, ws.next = [], 1
    ws.add(skill="theirs")                          # the same line id, 0000000000000001, in another owner's workspace
    read(other_user)
    assert [c["skill"] for c in calls_of(user["id"])] == ["mine"]
    assert [c["skill"] for c in calls_of(other_user["id"])] == ["theirs"]
    assert reads_of(user["id"])["since"] == 1 and reads_of(other_user["id"])["since"] == 1


# ---- the poll's round -----------------------------------------------------------------------------------------------

@pytest.fixture
def round_(client, ws, monkeypatch):
    """The round with the controller's sync and the agents' sync stood in for, each recording when it ran, and a
    workspace for each owner (the endpoint is the owner's id)."""
    order = []
    monkeypatch.setattr(agents, "_endpoint", lambda user: "ws:" + user["id"])
    monkeypatch.setattr(agents, "sync_controller", lambda user, endpoint=None: order.append(("controller", user["id"])))
    monkeypatch.setattr(agents, "sync", lambda user, background=False: order.append(("agents", user["id"])))
    real = ws.call

    def call(endpoint, method, path, body=None, timeout=30):
        order.append(("skills", endpoint[3:]))
        return real(endpoint, method, path, body, timeout)
    monkeypatch.setattr(workspace_client, "call", call)
    return order


def make_user(client, email):
    h = signed_up(client, email)
    return auth.user(client.get("/api/auth/me", headers=h).json()["id"])


def test_the_round_reads_the_skill_calls_of_each_owner_it_reaches_and_of_those_it_read_for_before(client, ws, round_):
    controlled = make_user(client, "controlled@example.com")
    agent_owner = make_user(client, "agent@example.com")
    counted = make_user(client, "counted@example.com")
    nothing = make_user(client, "nothing@example.com")
    controller.change_settings(controlled, {"cap": 3}, now=T0)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("a1a2a3a4a5a6", agent_owner["id"], "Tidy", "Tidy up.", T0, T0))
        db.execute("INSERT INTO ade_skill_reads (user_id, since, created_at, updated_at) VALUES (?, 0, ?, ?)",
                   (counted["id"], T0, T0))                       # read for before, with no call in the table: due
    everyone = sorted([controlled["id"], agent_owner["id"], counted["id"]])
    agents.poll_all()
    assert sorted(w for k, w in round_ if k == "skills") == everyone
    assert nothing["id"] not in [w for _, w in round_]
    # the owner read for is read again an hour after its last whole read, not in every round; the owners the round
    # reaches by their controller or their agents are read in every one
    round_.clear()
    agents.poll_all()
    assert sorted(w for k, w in round_ if k == "skills") == sorted([controlled["id"], agent_owner["id"]])
    round_.clear()
    agents.poll_all(now=time.time() + skill_counts.POLL_EVERY + 1)
    assert sorted(w for k, w in round_ if k == "skills") == everyone


def test_the_round_gives_its_own_clock_to_the_list_of_owners_it_reads_for(client, ws, round_):
    counted = make_user(client, "counted@example.com")
    now = int(time.time())
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_reads (user_id, since, created_at, updated_at, read_at) VALUES (?, 0, ?, ?, ?)",
                   (counted["id"], T0, T0, now - 60))              # a whole read a minute ago
    agents.poll_all(now=now)
    assert [w for k, w in round_ if k == "skills"] == []
    agents.poll_all(now=now + skill_counts.POLL_EVERY)             # an hour on, by the round's own clock
    assert [w for k, w in round_ if k == "skills"] == [counted["id"]]


def test_the_round_reads_the_skill_calls_after_the_controllers_sync_and_before_the_agents(client, ws, round_):
    """The controller's sync is first, so that the owner's Stop never waits on anything else; the agents' sync, which
    can run long, comes after the read of the skill calls."""
    user = make_user(client, "all@example.com")
    controller.change_settings(user, {"cap": 3}, now=T0)
    with store.connect() as db:
        db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   ("a1a2a3a4a5a6", user["id"], "Tidy", "Tidy up.", T0, T0))
    agents.poll_all()
    assert [k for k, _ in round_] == ["controller", "skills", "agents"]


def test_a_failing_read_stops_nothing_of_the_agents_sync_nor_the_other_owners(client, ws, round_, monkeypatch):
    first = make_user(client, "first@example.com")
    second = make_user(client, "second@example.com")
    for u in (first, second):
        with store.connect() as db:
            db.execute("INSERT INTO ade_skill_reads (user_id, since, created_at, updated_at) VALUES (?, 0, ?, ?)",
                       (u["id"], T0, T0))
            db.execute("INSERT INTO ade_agents (id, user_id, name, brief, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                       (("a" if u is first else "b") * 12, u["id"], "Tidy", "Tidy up.", T0, T0))
    ws.fail = RuntimeError("the workspace broke")
    agents.poll_all()
    assert sorted(w for k, w in round_ if k == "agents") == sorted([first["id"], second["id"]])
    assert sorted(w for k, w in round_ if k == "skills") == sorted([first["id"], second["id"]])
    assert reads_of(first["id"])["read_error"] == "The app failed reading the workspace's skill calls (RuntimeError)."


def test_a_machine_that_is_down_is_not_read_in_the_round_and_nothing_is_started(client, ws, round_, monkeypatch):
    user = make_user(client, "down@example.com")
    controller.change_settings(user, {"cap": 3}, now=T0)
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    agents.poll_all()
    assert ws.calls == [] and reads_of(user["id"]) is None


def test_the_round_looks_at_a_machine_that_is_down_once_an_hour_and_not_in_every_round(client, ws, round_, monkeypatch):
    """An owner read for before, with neither a controller nor an agent, whose machine is down was once looked
    at in every round of 30 seconds (the cost of a machine's state each time, a Fly call for one that is not cached)."""
    user = make_user(client, "down@example.com")
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_reads (user_id, since, created_at, updated_at) VALUES (?, 0, ?, ?)",
                   (user["id"], T0, T0))
    asked = []
    monkeypatch.setattr(agents, "_endpoint", lambda user: asked.append(user["id"]))   # no endpoint: the machine is down
    agents.poll_all()
    assert asked == [user["id"]]
    agents.poll_all()
    agents.poll_all()
    assert asked == [user["id"]]                                  # not again inside the hour
    agents.poll_all(now=time.time() + skill_counts.POLL_EVERY + 1)
    assert asked == [user["id"], user["id"]]


def test_the_standing_text_of_the_error_never_carries_an_address(client, owner, ws):
    """The words kept for a failure are the app's own: no address, no path, no header of the workspace's."""
    h, user = owner
    ws.fail = workspace_client.NotAnswering(502, "http://10.0.0.7:9999/skills?since=0 refused the connection")
    read(user)
    error = reads_of(user["id"])["read_error"]
    assert re.fullmatch(r"[A-Za-z0-9 .,()'-]+", error) and "10.0.0.7" not in error and "/skills" not in error


# the numbers the manual states, and the slack


def test_a_line_may_be_dated_a_day_ahead_of_the_app_s_clock_and_not_a_second_more():
    now = T0 + 10_000
    assert skill_counts._call(served(1, at=now + 86400), now) is not None
    assert skill_counts._call(served(2, at=now + 86400 + 1), now) is None
    assert skill_counts.SLACK == 86400


def test_the_numbers_the_manual_states_of_the_app_s_counts_are_the_code_s():
    """The manual says how many pages a read takes and for how long, how many calls and days the app keeps, how often
    the round looks at an owner, and how long each list of a skill's detail is: each is read back from the code
    (twelve changes of such numbers had passed every test of their part)."""
    assert stated(r"\(at most ([0-9]+) pages and none begun after [0-9]+ seconds of a read\)") == skill_counts.PAGES
    assert stated(r"none begun after ([0-9]+) seconds of a read") == skill_counts.READ_FOR
    assert stated(r"kept to ([0-9,]+) \(`skill_counts.CALLS_MOST`\)") == skill_counts.CALLS_MOST
    assert stated(r"Calls are kept ([0-9]+) days") == skill_counts.KEPT_DAYS == agents.KEPT_DAYS
    assert stated(r"once (an) hour from its last whole read") * 3600 == skill_counts.POLL_EVERY
    assert stated(r"at most once every ([0-9]+) seconds for an owner") == fly_provider.STATE_TTL
    assert stated(r"the ([0-9]+) sessions and tasks last called") == skill_counts.LISTED
    assert stated(r"the ([0-9]+) requirements and [0-9]+ repositories") == skill_counts.LISTED
    assert stated(r"the [0-9]+ requirements and ([0-9]+) repositories with the most calls") == skill_counts.REPOS_MOST
    assert stated(r"the ([0-9]+) versions last used") == skill_counts.VERSIONS_MOST
    assert stated(r"lists the ([0-9]+) skills of the window with the most calls") == skill_counts.SKILLS_MOST
    assert stated(r"with the ([0-9,]+) calls the app keeps at most") == skill_counts.CALLS_MOST
    assert stated(r"a page waits up to ([0-9]+) seconds on the connection") == skill_counts.PAGE_TIMEOUT
    assert stated(r"slowly holds the round for about ([0-9]+) seconds") == \
        skill_counts.READ_FOR + skill_counts.PAGE_TIMEOUT
    assert stated(r"one that does not answer at all for up to ([0-9]+) \(") == skill_counts.PAGE_TIMEOUT
