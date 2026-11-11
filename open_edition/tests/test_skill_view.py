"""What the Skills page reads from the app's counts (ade/skill_counts.py view and detail; ade/skill_routes.py):
the calls counted by UTC day and by skill (core, business by
repository, other), where they were made, by version, and how the tasks that called a skill ended; one skill's detail;
one owner's counts never another's; and the routes, each the signed-in owner's alone. The read of the workspace's record
is test_skill_counts.py's."""

import contextlib
import datetime
import time

import pytest

from conftest import signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, skill_counts
from test_skill_counts import owner, ws  # noqa: F401  (fixtures: an owner, and the stand-in workspace)

S1, S2, S3 = "111111111111", "222222222222", "333333333333"
NOW = 1_800_000_000 // 86400 * 86400 + 15 * 3600          # 15:00 UTC
TODAY = NOW // 86400
V1, V2, V3 = "1.0.0+" + "a" * 64, "1.0.0+" + "b" * 64, "1.0.0+" + "c" * 64
C1, C2 = "1" * 40, "2" * 40


def day(n, hour=12):
    """`hour` o'clock UTC, `n` days before today."""
    return (TODAY - n) * 86400 + hour * 3600


def text(n):
    return datetime.datetime.fromtimestamp((TODAY - n) * 86400, datetime.timezone.utc).strftime("%Y-%m-%d")


_ids = iter(range(1, 10 ** 6))


def put(user_id, at, skill="s", kind="other", path="claude", failed=0, version=None, session=S1, repo=None, task=None):
    """One call, as the app holds it."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_calls (user_id, line_id, at, skill, kind, path, failed, version, session_id, "
                   "repo, task_id, taken_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (user_id, f"{next(_ids):016x}", at, skill, kind, path, failed, version, session, repo, task, at))


def task(user_id, task_id, state="done", requirement=None, ended_at=None):
    with store.connect() as db:
        db.execute("INSERT INTO ade_controller_tasks (id, user_id, requirement_id, state, started_at, ended_at, "
                   "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (task_id, user_id, requirement, state, 1, ended_at, 1, 1))


def requirement(user_id, requirement_id, title="Fix the login", hidden=0):
    with store.connect() as db:
        db.execute("INSERT INTO ade_controller_requirements (id, user_id, request_id, title, title_hidden, words, repos, "
                   "planned_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (requirement_id, user_id, "r", title, hidden, "words", "[]", 1, 1, 1))


def by_key(view):
    return {(r["kind"], r["skill"], r["repo"]): r for r in view["skills"]}


# ---- one snapshot ----------------------------------------------------------------------------------------------------

class _Interleaved:
    """A connection of the store that, before it runs the first query whose text holds `marker`, lets `action` run
    (another connection's write, committed), as the app's own trim or its daily forget can between the queries of a
    view."""

    def __init__(self, db, marker, action):
        self._db, self._marker, self._action, self._done = db, marker, action, False

    def execute(self, sql, *args):
        if not self._done and self._marker in sql:
            self._done = True
            self._action()
        return self._db.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._db, name)


def _wipe_between(monkeypatch, marker):
    """The owner's calls are all deleted (and the delete committed) just before the first query that holds `marker`."""
    real = store.connect

    @contextlib.contextmanager
    def connect(path=None):
        with real(path) as db:
            def wipe():
                with real() as other:
                    other.execute("DELETE FROM ade_skill_calls")
            yield _Interleaved(db, marker, wipe)
    monkeypatch.setattr(store, "connect", connect)


def test_a_view_is_one_snapshot_though_the_calls_are_trimmed_between_its_queries(client, owner, monkeypatch):
    """The view asks the store several times and puts the answers together. The calls of a skill that all go between two
    of its queries (the 100,000-call bound, or the daily forget) were once a KeyError and a 500; they are in the one
    snapshot it reads."""
    h, user = owner
    put(user["id"], day(1), skill="gone")
    _wipe_between(monkeypatch, "c.version AS version, MAX(c.at) AS last_at")      # each skill's latest version's query
    got = skill_counts.view(user, 7, now=NOW)
    assert [(r["skill"], r["calls"], r["latest_version"]) for r in got["skills"]] == [("gone", 1, None)]
    assert got["totals"]["calls"] == 1


def test_a_detail_is_one_snapshot_too_its_lists_are_of_the_calls_its_head_counted(client, owner, monkeypatch):
    h, user = owner
    put(user["id"], day(1), skill="gone", session=S1)
    _wipe_between(monkeypatch, "c.at / 86400 AS d, COUNT(*) AS n")                   # the query after the head
    got = skill_counts.detail(user, "other", "gone", days=7, now=NOW)
    assert got["calls"] == 1 and [s["session"] for s in got["sessions"]] == [S1]


# ---- an owner with no calls -----------------------------------------------------------------------------------------

def test_an_owner_with_no_calls_sees_empty_counts_and_every_day_of_the_window(client, owner):
    h, user = owner
    got = skill_counts.view(user, 7, now=NOW)
    assert got["days"] == 7 and got["skills"] == [] and got["more"] == 0 and got["read"] is None
    assert got["totals"] == {"calls": 0, "failed": 0, "typed": 0, "skills": 0, "sessions": 0, "first_at": None,
                             "last_at": None}
    assert [d["day"] for d in got["by_day"]] == [text(n) for n in range(6, -1, -1)]
    assert all((d["core"], d["business"], d["other"]) == (0, 0, 0) for d in got["by_day"])
    assert (got["from_day"], got["to_day"], got["now"]) == (text(6), text(0), NOW)


@pytest.mark.parametrize("days", [0, 1, 6, 8, 29, 31, 91, -7, 365])
def test_a_window_other_than_the_three_is_refused(client, owner, days):
    h, user = owner
    with pytest.raises(agents.AgentError) as e:
        skill_counts.view(user, days, now=NOW)
    assert (e.value.status, str(e.value)) == (400, "A window is of the last 7, 30 or 90 days.")


@pytest.mark.parametrize("days,length", [(7, 7), (30, 30), (90, 90)])
def test_each_window_counts_that_many_utc_days_ending_today(client, owner, days, length):
    h, user = owner
    got = skill_counts.view(user, days, now=NOW)
    assert len(got["by_day"]) == length
    assert (got["by_day"][0]["day"], got["by_day"][-1]["day"]) == (text(days - 1), text(0))


# ---- by UTC day -----------------------------------------------------------------------------------------------------

def test_calls_are_counted_by_the_utc_day_they_were_made_and_by_kind(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(0, 0), "a", "core", version=V1)
    put(uid, day(0, 23), "a", "core", version=V1)
    put(uid, day(0, 5), "b", "business", version=C1, repo="alpha")
    put(uid, day(2, 12), "c", "other")
    put(uid, day(6, 12), "c", "other")
    got = skill_counts.view(user, 7, now=NOW)
    counts = {d["day"]: (d["core"], d["business"], d["other"]) for d in got["by_day"]}
    assert counts == {text(6): (0, 0, 1), text(5): (0, 0, 0), text(4): (0, 0, 0), text(3): (0, 0, 0), text(2): (0, 0, 1),
                      text(1): (0, 0, 0), text(0): (2, 1, 0)}
    assert got["totals"]["calls"] == 5


def test_the_window_runs_from_the_first_second_of_its_first_day_to_now(client, owner):
    h, user = owner
    uid = user["id"]
    first = (TODAY - 6) * 86400
    put(uid, first - 1, "before")
    put(uid, first, "first")
    put(uid, NOW, "now")
    got = skill_counts.view(user, 7, now=NOW)
    assert sorted(r["skill"] for r in got["skills"]) == ["first", "now"]
    again = skill_counts.view(user, 30, now=NOW)
    assert sorted(r["skill"] for r in again["skills"]) == ["before", "first", "now"]


def test_a_day_with_no_calls_is_a_row_of_zeros_not_a_gap(client, owner):
    h, user = owner
    put(user["id"], day(0), "a")
    put(user["id"], day(29), "a")
    got = skill_counts.view(user, 30, now=NOW)
    assert len(got["by_day"]) == 30
    assert sum(d["other"] for d in got["by_day"]) == 2
    assert [d["other"] for d in got["by_day"] if d["day"] not in (text(0), text(29))] == [0] * 28


# ---- by skill -------------------------------------------------------------------------------------------------------

def seed_skills(uid):
    # a core skill, used in two sessions and two repositories, in two releases and by one task
    put(uid, day(3), "proofpane-core:verify", "core", version=V1, session=S1, repo="alpha", task="t1")
    put(uid, day(2), "proofpane-core:verify", "core", version=V1, session=S1, repo=None, task="t1", failed=1)
    put(uid, day(1), "proofpane-core:verify", "core", path="typed", failed=None, version=V2, session=S2, repo="beta")
    # a business skill of two repositories
    put(uid, day(2), "deploy", "business", version=C1, session=S1, repo="alpha")
    put(uid, day(1), "deploy", "business", version=C2, session=S1, repo="alpha")
    put(uid, day(1), "deploy", "business", version=C1, session=S3, repo="beta")
    # another skill, with no version and no repository
    put(uid, day(0), "plain", "other", session=S3)
    task(uid, "t1", "done", requirement="r1")
    requirement(uid, "r1", "Fix the login")


def test_each_skill_is_a_row_with_its_counts_where_it_was_made_and_its_versions(client, owner):
    h, user = owner
    seed_skills(user["id"])
    got = skill_counts.view(user, 30, now=NOW)
    rows = by_key(got)
    assert set(rows) == {("core", "proofpane-core:verify", None), ("business", "deploy", "alpha"),
                         ("business", "deploy", "beta"), ("other", "plain", None)}
    verify = rows[("core", "proofpane-core:verify", None)]
    assert {k: verify[k] for k in ("calls", "failed", "typed", "sessions", "repos", "tasks", "requirements", "versions",
                                   "latest_version", "first_at", "last_at")} == {
        "calls": 3, "failed": 1, "typed": 1, "sessions": 2, "repos": 2, "tasks": 1, "requirements": 1, "versions": 2,
        "latest_version": V2, "first_at": day(3), "last_at": day(1)}
    assert verify["ended"] == {"done": 1, "failed": 0, "stopped": 0, "open": 0, "unknown": 0}
    alpha = rows[("business", "deploy", "alpha")]
    assert (alpha["calls"], alpha["versions"], alpha["latest_version"], alpha["repos"], alpha["sessions"]) == (
        2, 2, C2, 1, 1)
    beta = rows[("business", "deploy", "beta")]
    assert (beta["calls"], beta["versions"], beta["latest_version"], beta["sessions"]) == (1, 1, C1, 1)
    plain = rows[("other", "plain", None)]
    assert (plain["calls"], plain["versions"], plain["latest_version"], plain["repos"], plain["tasks"]) == (
        1, 0, None, 0, 0)
    # skills are counted as the rows below are: a business skill of two repositories is two
    assert got["totals"] == {"calls": 7, "failed": 1, "typed": 1, "skills": 4, "sessions": 3, "first_at": day(3),
                             "last_at": day(0)}
    assert got["totals"]["skills"] == len(got["skills"])


def test_the_skills_are_listed_by_calls_then_name_then_repository(client, owner):
    h, user = owner
    seed_skills(user["id"])
    got = skill_counts.view(user, 30, now=NOW)
    assert [(r["kind"], r["skill"], r["repo"], r["calls"]) for r in got["skills"]] == [
        ("core", "proofpane-core:verify", None, 3), ("business", "deploy", "alpha", 2),
        ("business", "deploy", "beta", 1), ("other", "plain", None, 1)]


def test_a_business_skill_is_a_row_for_each_repository_but_a_core_skill_is_one_row(client, owner):
    h, user = owner
    uid = user["id"]
    for repo in ("alpha", "beta", None):
        put(uid, day(1), "deploy", "business", version=C1, repo=repo)
        put(uid, day(1), "proofpane-core:plan", "core", version=V1, repo=repo)
        put(uid, day(1), "plain", "other", repo=repo)
    got = skill_counts.view(user, 30, now=NOW)
    rows = [(r["kind"], r["skill"], r["repo"], r["calls"], r["repos"]) for r in got["skills"]]
    assert sorted(rows, key=str) == sorted([
        ("business", "deploy", "alpha", 1, 1), ("business", "deploy", "beta", 1, 1), ("business", "deploy", None, 1, 0),
        ("core", "proofpane-core:plan", None, 3, 2), ("other", "plain", None, 3, 2)], key=str)


def test_a_skill_name_used_as_two_kinds_is_two_rows(client, owner):
    """A skill a worktree held and then did not (a business one, then another skill of the name) is counted as each."""
    h, user = owner
    put(user["id"], day(2), "same", "business", version=C1, repo="alpha")
    put(user["id"], day(1), "same", "other")
    got = skill_counts.view(user, 30, now=NOW)
    assert sorted((r["kind"], r["calls"]) for r in got["skills"]) == [("business", 1), ("other", 1)]


def test_the_latest_version_is_the_one_of_the_newest_call(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(1, 9), "proofpane-core:verify", "core", version=V2)
    put(uid, day(1, 12), "proofpane-core:verify", "core", version=V3)
    put(uid, day(2, 12), "proofpane-core:verify", "core", version=V1)
    row = skill_counts.view(user, 30, now=NOW)["skills"][0]
    assert (row["latest_version"], row["versions"]) == (V3, 3)


def test_only_the_calls_in_the_window_count_for_a_skill_and_its_latest_version(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(20), "proofpane-core:verify", "core", version=V1)
    put(uid, day(2), "proofpane-core:verify", "core", version=V2)
    narrow = skill_counts.view(user, 7, now=NOW)["skills"][0]
    wide = skill_counts.view(user, 30, now=NOW)["skills"][0]
    assert (narrow["calls"], narrow["versions"], narrow["latest_version"], narrow["first_at"]) == (1, 1, V2, day(2))
    assert (wide["calls"], wide["versions"], wide["latest_version"], wide["first_at"]) == (2, 2, V2, day(20))


def test_no_more_rows_than_the_view_lists_and_the_rest_are_counted(client, owner, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "SKILLS_MOST", 2)
    for i, name in enumerate(("a", "b", "c", "d", "e")):
        for _ in range(5 - i):
            put(user["id"], day(1), name, "other")
    got = skill_counts.view(user, 30, now=NOW)
    assert [r["skill"] for r in got["skills"]] == ["a", "b"] and got["more"] == 3
    assert got["totals"]["skills"] == 5 and got["totals"]["calls"] == 15


# ---- how the tasks that called a skill ended ------------------------------------------------------------------------

def test_how_the_tasks_that_called_a_skill_ended_by_task_not_by_call(client, owner):
    h, user = owner
    uid = user["id"]
    states = {"t-done": "done", "t-failed": "failed", "t-stopped": "stopped", "t-work": "working", "t-wait": "waiting",
              "t-idle": "idle", "t-start": "starting", "t-run": "running"}
    for task_id, state in states.items():
        task(uid, task_id, state)
        put(uid, day(1), "x", "other", task=task_id)
    put(uid, day(1), "x", "other", task="t-done")           # a second call of the same task
    put(uid, day(1), "x", "other", task="t-gone")           # a task the app has no record of
    put(uid, day(1), "x", "other", task=None)               # a call in no task
    row = skill_counts.view(user, 30, now=NOW)["skills"][0]
    assert row["calls"] == 11 and row["tasks"] == 9
    assert row["ended"] == {"done": 1, "failed": 1, "stopped": 1, "open": 5, "unknown": 1}


def test_a_state_the_app_does_not_name_is_unknown_not_open(client, owner):
    h, user = owner
    task(user["id"], "t", "somethingelse")
    put(user["id"], day(1), "x", "other", task="t")
    assert skill_counts.view(user, 30, now=NOW)["skills"][0]["ended"]["unknown"] == 1


def test_the_requirements_a_skill_was_called_for_are_counted_by_requirement(client, owner):
    h, user = owner
    uid = user["id"]
    task(uid, "t1", "done", requirement="r1")
    task(uid, "t2", "done", requirement="r1")
    task(uid, "t3", "failed", requirement="r2")
    task(uid, "t4", "done", requirement=None)
    for t in ("t1", "t2", "t3", "t4"):
        put(uid, day(1), "x", "other", task=t)
    row = skill_counts.view(user, 30, now=NOW)["skills"][0]
    assert (row["tasks"], row["requirements"]) == (4, 2)


def test_a_task_of_another_owner_with_the_same_id_is_not_this_owner_s(client, owner):
    h, user = owner
    other = signed_up(client, "admin2@example.com")
    other_user = auth.user(client.get("/api/auth/me", headers=other).json()["id"])
    task(user["id"], "t", "done")
    task(other_user["id"], "t", "failed", requirement="theirs")
    put(user["id"], day(1), "x", "other", task="t")
    row = skill_counts.view(user, 30, now=NOW)["skills"][0]
    assert row["ended"]["done"] == 1 and row["ended"]["failed"] == 0 and row["requirements"] == 0


# ---- one owner's counts are never another's -------------------------------------------------------------------------

def test_one_owner_sees_none_of_another_owner_s_calls(client, owner):
    h, user = owner
    other = signed_up(client, "admin2@example.com")
    other_user = auth.user(client.get("/api/auth/me", headers=other).json()["id"])
    put(user["id"], day(1), "mine", "other")
    put(other_user["id"], day(1), "theirs", "other", session=S2)
    put(other_user["id"], day(1), "mine", "other", session=S3)
    mine = skill_counts.view(user, 30, now=NOW)
    assert [(r["skill"], r["calls"]) for r in mine["skills"]] == [("mine", 1)]
    assert mine["totals"]["calls"] == 1 and mine["totals"]["sessions"] == 1
    assert skill_counts.detail(user, "other", "mine", now=NOW)["calls"] == 1
    with pytest.raises(agents.AgentError):
        skill_counts.detail(user, "other", "theirs", now=NOW)


# ---- how the app's reads stand --------------------------------------------------------------------------------------

def test_the_view_says_how_the_reads_stand(client, owner):
    h, user = owner
    with store.connect() as db:
        db.execute("INSERT INTO ade_skill_reads (user_id, store_id, since, read_at, read_behind_at, read_error, "
                   "read_error_at, unserved_at, missed, dropped, refused, trimmed, created_at, updated_at) VALUES "
                   "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (user["id"], "5" * 32, 9, 100, 90, "words", 95, 80, 4, 3, 2, 6, 70, 71))
    assert skill_counts.view(user, 7, now=NOW)["read"] == {
        "at": 100, "behind_at": 90, "error": "words", "error_at": 95, "unserved_at": 80, "missed": 4, "dropped": 3,
        "refused": 2, "trimmed": 6, "since_at": 70}


# ---- the window ends with today ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("days", [7, 30, 90])
def test_a_call_dated_after_today_is_in_no_count_of_the_window(client, owner, days):
    """A line can be dated up to a day ahead of the app's clock (a skewed clock, a program writing lines of its own). The
    window ends with today's UTC day, as its columns do, so the totals, the rows, the versions and the tasks never hold
    more than the columns."""
    h, user = owner
    uid = user["id"]
    verify = "proofpane-core:verify"
    task(uid, "t1", "done")
    task(uid, "t2", "failed")
    put(uid, day(0, 10), verify, "core", version=V1, task="t1")
    put(uid, (TODAY + 1) * 86400 - 1, verify, "core", version=V1)             # the last second of today
    put(uid, (TODAY + 1) * 86400, verify, "core", version=V2, task="t2")      # the first second of tomorrow
    put(uid, NOW + 86300, "ahead")                                            # a day ahead, nearly
    got = skill_counts.view(user, days, now=NOW)
    assert got["totals"]["calls"] == 2 and got["totals"]["last_at"] == (TODAY + 1) * 86400 - 1
    assert got["totals"]["skills"] == 1
    (row,) = got["skills"]
    assert (row["skill"], row["calls"], row["versions"], row["latest_version"], row["tasks"]) == (verify, 2, 1, V1, 1)
    assert row["ended"]["done"] == 1 and row["ended"]["failed"] == 0 and row["last_at"] == (TODAY + 1) * 86400 - 1
    assert sum(d["core"] + d["business"] + d["other"] for d in got["by_day"]) == 2
    assert got["by_day"][-1]["core"] == 2
    one = skill_counts.detail(user, "core", verify, days=days, now=NOW)
    assert (one["calls"], [v["version"] for v in one["versions"]], [x["task"] for x in one["tasks"]]) == (2, [V1], ["t1"])
    assert one["ended"]["failed"] == 0 and sum(d["calls"] for d in one["by_day"]) == 2
    with pytest.raises(agents.AgentError):
        skill_counts.detail(user, "other", "ahead", days=days, now=NOW)


def test_the_total_of_skills_counts_a_name_once_for_each_kind_it_is_listed_in(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(1), "deploy", "business", version=C1)      # called in no repository: its row has no repository to tell it by
    put(uid, day(1, 11), "deploy", "other")                 # the same name, called where a person's own skill of it runs first
    got = skill_counts.view(user, 30, now=NOW)
    assert {(r["kind"], r["skill"]) for r in got["skills"]} == {("business", "deploy"), ("other", "deploy")}
    assert got["totals"]["skills"] == 2


def test_a_task_the_app_has_no_record_of_is_counted_not_known_in_the_detail_too(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(1), "deploy", "business", version=C1, repo="alpha", task="gone")
    put(uid, day(1, 13), "deploy", "business", version=C1, repo="alpha", task="t-done")
    task(uid, "t-done", "done")
    one = skill_counts.detail(user, "business", "deploy", repo="alpha", by_repo=True, days=30, now=NOW)
    assert one["ended"] == {"done": 1, "failed": 0, "stopped": 0, "open": 0, "unknown": 1}
    assert {x["task"]: x["state"] for x in one["tasks"]} == {"gone": None, "t-done": "done"}


# ---- one skill ------------------------------------------------------------------------------------------------------

def seed_detail(uid):
    put(uid, day(3, 10), "proofpane-core:verify", "core", version=V1, session=S1, repo="alpha", task="t1")
    put(uid, day(3, 11), "proofpane-core:verify", "core", version=V1, session=S1, repo="alpha", task="t1", failed=1)
    put(uid, day(1, 9), "proofpane-core:verify", "core", version=V2, session=S2, repo="beta", task="t2")
    put(uid, day(1, 10), "proofpane-core:verify", "core", path="typed", failed=None, version=V2, session=S3)
    put(uid, day(0, 1), "proofpane-core:other", "core", version=V1)          # another skill: never in this one's
    task(uid, "t1", "done", requirement="r1", ended_at=day(3, 12))
    task(uid, "t2", "working", requirement="r2")
    requirement(uid, "r1", "Fix the login")
    requirement(uid, "r2", "Tidy\u202eup\nthe docs")


def test_one_skill_s_calls_by_day_version_place_task_and_requirement(client, owner):
    h, user = owner
    seed_detail(user["id"])
    got = skill_counts.detail(user, "core", "proofpane-core:verify", days=7, now=NOW)
    assert (got["kind"], got["skill"], got["repo"], got["days"]) == ("core", "proofpane-core:verify", None, 7)
    assert (got["calls"], got["failed"], got["typed"]) == (4, 1, 1)
    assert (got["first_at"], got["last_at"]) == (day(3, 10), day(1, 10))
    assert [(d["day"], d["calls"], d["failed"]) for d in got["by_day"]] == [
        (text(6), 0, 0), (text(5), 0, 0), (text(4), 0, 0), (text(3), 2, 1), (text(2), 0, 0), (text(1), 2, 0),
        (text(0), 0, 0)]
    assert got["versions"] == [{"version": V2, "calls": 2, "first_at": day(1, 9), "last_at": day(1, 10)},
                               {"version": V1, "calls": 2, "first_at": day(3, 10), "last_at": day(3, 11)}]
    assert got["repos"] == [{"repo": "alpha", "calls": 2}, {"repo": None, "calls": 1}, {"repo": "beta", "calls": 1}]
    assert got["sessions"] == [{"session": S3, "calls": 1, "first_at": day(1, 10), "last_at": day(1, 10)},
                               {"session": S2, "calls": 1, "first_at": day(1, 9), "last_at": day(1, 9)},
                               {"session": S1, "calls": 2, "first_at": day(3, 10), "last_at": day(3, 11)}]
    assert got["tasks"] == [{"task": "t2", "calls": 1, "last_at": day(1, 9), "state": "working", "requirement": "r2",
                             "ended_at": None},
                            {"task": "t1", "calls": 2, "last_at": day(3, 11), "state": "done", "requirement": "r1",
                             "ended_at": day(3, 12)}]
    assert got["requirements"] == [{"requirement": "r1", "title": "Fix the login", "calls": 2, "tasks": 1},
                                   {"requirement": "r2", "title": "Tidy up the docs", "calls": 1, "tasks": 1}]
    assert got["ended"] == {"done": 1, "failed": 0, "stopped": 0, "open": 1, "unknown": 0}


def test_a_requirement_title_is_a_models_text_shown_on_one_line_with_no_hidden_character(client, owner):
    h, user = owner
    put(user["id"], day(1), "x", "other", task="t")
    task(user["id"], "t", "done", requirement="r")
    requirement(user["id"], "r", "a\u200bb\u202ec\nd" + "e" * 200)
    title = skill_counts.detail(user, "other", "x", now=NOW)["requirements"][0]["title"]
    assert "\u200b" not in title and "\u202e" not in title and "\n" not in title and len(title) <= 120


def test_a_requirement_the_app_has_no_record_of_has_no_title(client, owner):
    h, user = owner
    put(user["id"], day(1), "x", "other", task="t")
    task(user["id"], "t", "done", requirement="gone")
    got = skill_counts.detail(user, "other", "x", now=NOW)
    assert got["requirements"] == [{"requirement": "gone", "title": None, "calls": 1, "tasks": 1}]


def test_the_detail_of_a_business_skill_is_of_one_repository_or_of_all(client, owner):
    h, user = owner
    uid = user["id"]
    put(uid, day(2), "deploy", "business", version=C1, repo="alpha")
    put(uid, day(1), "deploy", "business", version=C2, repo="alpha")
    put(uid, day(1), "deploy", "business", version=C1, repo="beta")
    put(uid, day(1), "deploy", "business", version=C1, repo=None)
    whole = skill_counts.detail(user, "business", "deploy", now=NOW)
    assert (whole["calls"], whole["repo"]) == (4, None)
    alpha = skill_counts.detail(user, "business", "deploy", repo="alpha", by_repo=True, now=NOW)
    assert (alpha["calls"], alpha["repo"], [v["version"] for v in alpha["versions"]]) == (2, "alpha", [C2, C1])
    none = skill_counts.detail(user, "business", "deploy", repo=None, by_repo=True, now=NOW)
    assert (none["calls"], none["repo"], none["repos"]) == (1, None, [{"repo": None, "calls": 1}])
    # a repository is of a business skill alone: given for a core skill it changes nothing
    put(uid, day(1), "proofpane-core:plan", "core", version=V1, repo="alpha")
    put(uid, day(1), "proofpane-core:plan", "core", version=V1, repo="beta")
    assert skill_counts.detail(user, "core", "proofpane-core:plan", repo="alpha", by_repo=True, now=NOW)["calls"] == 2


def test_the_detail_lists_are_bounded_and_newest_first(client, owner, monkeypatch):
    h, user = owner
    monkeypatch.setattr(skill_counts, "LISTED", 2)
    monkeypatch.setattr(skill_counts, "VERSIONS_MOST", 2)
    monkeypatch.setattr(skill_counts, "REPOS_MOST", 2)
    uid = user["id"]
    for i in range(4):
        put(uid, day(4 - i), "proofpane-core:verify", "core", version=f"1.0.0+{str(i) * 64}", session=f"{i}" * 12,
            repo=f"repo{i}", task=f"t{i}")
        task(uid, f"t{i}", "done", requirement=f"r{i}")
    got = skill_counts.detail(user, "core", "proofpane-core:verify", now=NOW)
    assert [s["session"] for s in got["sessions"]] == ["3" * 12, "2" * 12]
    assert [t["task"] for t in got["tasks"]] == ["t3", "t2"]
    assert [v["version"] for v in got["versions"]] == [f"1.0.0+{'3' * 64}", f"1.0.0+{'2' * 64}"]
    assert len(got["repos"]) == 2 and len(got["requirements"]) == 2
    assert got["calls"] == 4


ALL = ("sessions", "tasks", "requirements", "versions", "repos")
NONE = dict.fromkeys(ALL, False)


@pytest.mark.parametrize("given, cut", [
    pytest.param((3, 4, 4, 3, 3), dict.fromkeys(ALL, True), id="every list one past its limit"),
    pytest.param((2, 3, 3, 2, 2), NONE, id="every list exactly at its limit"),
    pytest.param((3, 1, 1, 1, 1), {**NONE, "sessions": True}, id="sessions alone"),
    pytest.param((1, 4, 1, 1, 1), {**NONE, "versions": True}, id="versions alone"),
    pytest.param((1, 1, 4, 1, 1), {**NONE, "repos": True}, id="repositories alone"),
    pytest.param((1, 1, 1, 3, 1), {**NONE, "tasks": True}, id="tasks alone, on one requirement"),
    pytest.param((1, 1, 1, 3, 3), {**NONE, "tasks": True, "requirements": True}, id="tasks and requirements")])
def test_the_detail_says_which_of_its_lists_are_cut_and_none_at_exactly_its_limit(client, owner, monkeypatch, given, cut):
    """A list cut with no word reads as all that the skill was called in (a skill called in 50 sessions
    listed 20 with no sign of the 30 more). The limits are made small here: LISTED 2, VERSIONS_MOST 3, REPOS_MOST 3."""
    h, user = owner
    for name, value in (("LISTED", 2), ("VERSIONS_MOST", 3), ("REPOS_MOST", 3)):
        monkeypatch.setattr(skill_counts, name, value)
    uid, now = user["id"], int(time.time())
    sessions, versions, repos, tasks, requirements = given
    for t in range(tasks):
        task(uid, f"t{t}", "done", requirement=f"r{t % requirements}")
    for r in range(requirements):
        requirement(uid, f"r{r}")
    for i in range(max(given)):
        put(uid, now - 600 + i, "proofpane-core:verify", "core", version=f"1.0.0+{str(i % versions) * 64}",
            session=f"{i % sessions}" * 12, repo=f"repo{i % repos}", task=f"t{i % tasks}")
    got = skill_counts.detail(user, "core", "proofpane-core:verify", now=now)
    assert got["more"] == cut
    assert (len(got["sessions"]), len(got["tasks"]), len(got["requirements"]), len(got["versions"]), len(got["repos"])) == (
        min(sessions, 2), min(tasks, 2), min(requirements, 2), min(versions, 3), min(repos, 3))
    route = client.get("/api/ade/skills/detail?kind=core&skill=proofpane-core:verify", headers=h)
    assert route.status_code == 200 and route.json()["more"] == cut


@pytest.mark.parametrize("args", [("mystery", "x"), ("other", ""), ("other", "-x"), ("other", "a b"), ("other", "a" * 81),
                                  ("other", "no-calls"), (None, "x")])
def test_a_skill_that_is_not_one_or_has_no_calls_in_the_window_is_not_found(client, owner, args):
    h, user = owner
    put(user["id"], day(1), "x", "other")
    with pytest.raises(agents.AgentError) as e:
        skill_counts.detail(user, *args, now=NOW)
    assert e.value.status == 404


def test_a_repository_that_is_not_one_is_not_found(client, owner):
    h, user = owner
    put(user["id"], day(1), "deploy", "business", version=C1, repo="alpha")
    with pytest.raises(agents.AgentError) as e:
        skill_counts.detail(user, "business", "deploy", repo="-x", by_repo=True, now=NOW)
    assert e.value.status == 404


def test_the_detail_s_window_is_refused_when_it_is_not_one_of_the_three(client, owner):
    h, user = owner
    put(user["id"], day(1), "x", "other")
    with pytest.raises(agents.AgentError) as e:
        skill_counts.detail(user, "other", "x", days=14, now=NOW)
    assert e.value.status == 400


def test_a_call_outside_the_window_is_not_in_the_detail(client, owner):
    h, user = owner
    put(user["id"], day(20), "x", "other")
    with pytest.raises(agents.AgentError):
        skill_counts.detail(user, "other", "x", days=7, now=NOW)
    assert skill_counts.detail(user, "other", "x", days=30, now=NOW)["calls"] == 1


# ---- the routes -----------------------------------------------------------------------------------------------------

def test_every_route_needs_a_signed_in_account(client):
    assert client.get("/api/ade/skills").status_code == 401
    assert client.get("/api/ade/skills/detail?kind=other&skill=x").status_code == 401
    assert client.post("/api/ade/skills/read").status_code == 401


def test_the_routes_need_an_account_the_provider_gives_a_workspace(client, monkeypatch):
    h = signed_up(client)
    monkeypatch.delenv("OPEN_EDITION_ADE", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    for r in (client.get("/api/ade/skills", headers=h), client.get("/api/ade/skills/detail?kind=other&skill=x", headers=h),
              client.post("/api/ade/skills/read", headers=h)):
        assert r.status_code == 404, r.text


def test_the_view_route_answers_the_signed_in_owner_s_counts_for_the_window_asked(client, owner):
    h, user = owner
    now = int(time.time())
    put(user["id"], now - 60, "x", "other")
    put(user["id"], now - 20 * 86400, "old", "other")
    mine = client.get("/api/ade/skills", headers=h)
    assert mine.status_code == 200 and mine.json()["days"] == 30
    assert sorted(r["skill"] for r in mine.json()["skills"]) == ["old", "x"]
    week = client.get("/api/ade/skills?days=7", headers=h).json()
    assert week["days"] == 7 and [r["skill"] for r in week["skills"]] == ["x"]
    r = client.get("/api/ade/skills?days=5", headers=h)
    assert (r.status_code, r.json()) == (400, {"detail": "A window is of the last 7, 30 or 90 days."})
    assert client.get("/api/ade/skills?days=abc", headers=h).status_code == 422


def test_the_routes_answer_one_owner_s_counts_to_that_owner_alone(client, owner, monkeypatch):
    h, user = owner
    monkeypatch.setenv("OPEN_EDITION_ADMINS", "admin@example.com,admin2@example.com")    # a workspace is an admin's
    other = signed_up(client, "admin2@example.com")
    now = int(time.time())
    put(user["id"], now - 60, "mine", "other")
    assert [r["skill"] for r in client.get("/api/ade/skills", headers=h).json()["skills"]] == ["mine"]
    assert client.get("/api/ade/skills", headers=other).json()["skills"] == []
    assert client.get("/api/ade/skills/detail?kind=other&skill=mine", headers=h).status_code == 200
    assert client.get("/api/ade/skills/detail?kind=other&skill=mine", headers=other).status_code == 404


def test_the_detail_route_takes_a_core_skill_s_name_with_its_colon_and_a_repository_or_none(client, owner):
    h, user = owner
    now = int(time.time())
    put(user["id"], now - 60, "proofpane-core:verify", "core", version=V1, repo="alpha")
    put(user["id"], now - 50, "deploy", "business", version=C1, repo="alpha")
    put(user["id"], now - 40, "deploy", "business", version=C1, repo=None)
    core = client.get("/api/ade/skills/detail?kind=core&skill=proofpane-core:verify", headers=h)
    assert core.status_code == 200 and core.json()["calls"] == 1
    whole = client.get("/api/ade/skills/detail?kind=business&skill=deploy", headers=h).json()
    alpha = client.get("/api/ade/skills/detail?kind=business&skill=deploy&repo=alpha", headers=h).json()
    nowhere = client.get("/api/ade/skills/detail?kind=business&skill=deploy&repo=", headers=h).json()
    assert (whole["calls"], alpha["calls"], alpha["repo"], nowhere["calls"], nowhere["repo"]) == (2, 1, "alpha", 1, None)
    assert client.get("/api/ade/skills/detail?kind=business&skill=nothing", headers=h).status_code == 404
    assert client.get("/api/ade/skills/detail?kind=business", headers=h).status_code == 422
    assert client.get("/api/ade/skills/detail?kind=business&skill=" + "a" * 101, headers=h).status_code == 422


def test_the_read_route_reads_the_workspace_s_record_and_says_how_it_went(client, owner, ws):
    h, user = owner
    ws.add()
    ws.add(skill="plain", kind="other", version=None)
    r = client.post("/api/ade/skills/read", headers=h)
    assert (r.status_code, r.json()) == (200, {"state": "read", "taken": 2})
    again = client.post("/api/ade/skills/read", headers=h).json()
    assert again == {"state": "read", "taken": 0}
    assert ws.paths() == ["/skills?since=0", "/skills?since=2"]
    ws.routes = False
    assert client.post("/api/ade/skills/read", headers=h).json() == {"state": "unserved", "taken": 0}
    ws.routes = True
    ws.fail = RuntimeError("broken")
    assert client.post("/api/ade/skills/read", headers=h).json() == {"state": "failed", "taken": 0}


def test_the_read_route_with_the_machine_down_starts_nothing(client, owner, ws, monkeypatch):
    h, user = owner
    monkeypatch.setattr(agents, "_endpoint", lambda user: None)
    r = client.post("/api/ade/skills/read", headers=h)
    assert (r.status_code, r.json()) == (200, {"state": "not_running", "taken": 0}) and ws.calls == []


def test_the_view_route_reads_nothing_from_the_workspace(client, owner, ws):
    h, user = owner
    ws.add()
    assert client.get("/api/ade/skills", headers=h).status_code == 200
    assert client.get("/api/ade/skills/detail?kind=other&skill=x", headers=h).status_code == 404
    assert ws.calls == []
