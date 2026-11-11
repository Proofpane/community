"""The calendar of an account's agent triggers (triggers.py, agents_routes.py): each schedule's coming times in a
window, worked out by cron.py's own next_after from the stored
next time and capped per schedule; the firings the app's store records, its runs a trigger queued and each trigger's
latest held or missed firing; the time each scheduled firing was due, kept on its run; a run now of one schedule, run
as the schedule would run it, unattended; each change of a trigger kept as a version. A refresh reads the app's store
alone: it starts no machine and writes nothing, and one account never sees another's triggers."""

import calendar as cal
import json
import time

import pytest

from conftest import ADMIN, signed_up
from proofpane_open import auth, store
from proofpane_open.ade import agents, cron, triggers, workspaces

A = "/api/ade/agents"
HOUR = 3600
DAY = 86400


def utc(*parts):
    return cal.timegm(tuple(parts) + (0,) * (6 - len(parts)))


LAST = utc(2100, 1, 2)          # the latest time a window may reach (triggers.CAL_LAST): 2099's end in every zone


@pytest.fixture
def owner(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_EDITION_ADE", "local")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    sent = []
    # a start asked for (a run's), and a mirror alone, each recorded
    monkeypatch.setattr(agents, "_deliver_soon", lambda user, start=False: sent.append(start) or False)
    h = signed_up(client)
    yield h, sent


def make(client, h, **over):
    r = client.post(A, headers=h, json=dict({"name": "Triage", "brief": "hello", "repo": "demo"}, **over))
    assert r.status_code == 200, r.text
    return r.json()


def trigger(client, h, agent_id, **given):
    r = client.post(f"{A}/{agent_id}/triggers", headers=h, json=given)
    assert r.status_code == 200, r.text
    return r.json()


def window(client, h, start, end):
    r = client.get(f"{A}/calendar", headers=h, params={"from": start, "to": end})
    assert r.status_code == 200, r.text
    return r.json()


def schedule_of(answer, trigger_id):
    got = [s for s in answer["schedules"] if s["id"] == trigger_id]
    assert len(got) == 1, [s["id"] for s in answer["schedules"]]
    return got[0]


def set_next(trigger_id, next_at):
    with store.connect() as db:
        db.execute("UPDATE ade_agent_triggers SET next_at = ? WHERE id = ?", (next_at, trigger_id))


def seed_run(agent_id, user_id, trigger_id, state, queued_at, kind="schedule", reason=None, due=None, **extra):
    run_id = "%012x" % (abs(hash((trigger_id, state, queued_at))) % (16 ** 12))
    fired = {"kind": kind, "prompt": None, "at": queued_at, "id": trigger_id}
    if due is not None:
        fired["due"] = due
    with store.connect() as db:
        db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, trigger, queued_at, reason, "
                   "brief_version, loop, updated_at) VALUES (?,?,?,?,?,?,?,1,0,?)",
                   (run_id, agent_id, user_id, state, json.dumps(fired), queued_at, reason, queued_at))
        for column, value in extra.items():
            db.execute(f"UPDATE ade_agent_runs SET {column} = ? WHERE run_id = ?", (value, run_id))
    return run_id


def chain(line, tz, start, end):
    """The times next_after gives, one call from each, in [start, end): what the calendar must equal."""
    s = cron.Schedule(line, tz)
    t, out = s.next_after(start - 1), []
    while t is not None and t < end:
        out.append(t)
        t = s.next_after(t)
    return out


def snapshot():
    """Every row of every table of the app's store, so that a write anywhere shows."""
    with store.connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                           "'sqlite_%' ORDER BY name")]
        return {t: sorted(repr(tuple(r)) for r in db.execute(f'SELECT * FROM "{t}"')) for t in tables}


# ---- the times -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, start, end, local", [
    # 02:30 each night: on 27 September 2026 the clock goes from 02:00 to 03:00 and no 02:30 comes
    ("30 2 * * *", utc(2026, 9, 24, 12), utc(2026, 9, 30, 12),
     ["2026-09-25 02:30", "2026-09-26 02:30", "2026-09-28 02:30", "2026-09-29 02:30", "2026-09-30 02:30"]),
    # each hour: on 4 April 2027 02:00 comes twice, and fires once, at its first
    ("0 * * * *", utc(2027, 4, 3, 11), utc(2027, 4, 3, 17),
     ["2027-04-04 00:00", "2027-04-04 01:00", "2027-04-04 02:00", "2027-04-04 03:00", "2027-04-04 04:00"]),
])
def test_occurrences_are_next_after_called_from_each_through_a_change_of_clock(line, start, end, local):
    import datetime
    from zoneinfo import ZoneInfo
    tz = "Pacific/Auckland"
    t = {"kind": "schedule", "enabled": 1, "cron": line, "tz": tz,
         "next_at": cron.Schedule(line, tz).next_after(start - 1)}
    got, capped = triggers.occurrences(t, start, end, limit=1000, now=start)
    assert got == chain(line, tz, start, end) and capped is False
    assert [datetime.datetime.fromtimestamp(x, ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M") for x in got] == local


def test_the_first_coming_time_is_the_stored_one_and_none_comes_before_it():
    now = utc(2026, 11, 2, 9, 20)
    t = {"kind": "schedule", "enabled": 1, "cron": "0 * * * *", "tz": "UTC", "next_at": utc(2026, 11, 2, 10)}
    # a window from five hours before now: the hours before the stored time are past, not coming
    got, _ = triggers.occurrences(t, now - 5 * HOUR, now + 3 * HOUR, now=now)
    assert got == [utc(2026, 11, 2, 10), utc(2026, 11, 2, 11), utc(2026, 11, 2, 12)]
    # a stored time the line would not give (the scheduler uses it all the same) is the first, the rest from it
    t["next_at"] = utc(2026, 11, 2, 9, 45)
    got, _ = triggers.occurrences(t, now, now + 3 * HOUR, now=now)
    assert got == [utc(2026, 11, 2, 9, 45), utc(2026, 11, 2, 10), utc(2026, 11, 2, 11), utc(2026, 11, 2, 12)]


def test_a_stored_time_passed_is_the_first_and_the_times_its_late_round_skips_are_none():
    # the scheduler was down: its stored 08:00 is still the one it uses (fired late, or recorded missed past its grace),
    # and its round then sets the next time from its own, so 09:00 never fires and is never shown as coming
    now = utc(2026, 11, 2, 9, 20)
    t = {"kind": "schedule", "enabled": 1, "cron": "0 * * * *", "tz": "UTC", "next_at": utc(2026, 11, 2, 8)}
    got, _ = triggers.occurrences(t, now - 5 * HOUR, now + 3 * HOUR, now=now)
    assert got == [utc(2026, 11, 2, 8), utc(2026, 11, 2, 10), utc(2026, 11, 2, 11), utc(2026, 11, 2, 12)]
    following = cron.Schedule("0 * * * *", "UTC").next_after(now)       # what tick sets after a late round
    assert following == got[1]


def test_a_window_holds_a_time_at_its_start_and_none_at_its_end():
    t = {"kind": "schedule", "enabled": 1, "cron": "0 * * * *", "tz": "UTC", "next_at": utc(2026, 11, 2, 8)}
    now = utc(2026, 11, 2, 7)
    assert triggers.occurrences(t, utc(2026, 11, 2, 10), utc(2026, 11, 2, 12), now=now)[0] == [
        utc(2026, 11, 2, 10), utc(2026, 11, 2, 11)]
    assert triggers.occurrences(t, utc(2026, 11, 2, 10), utc(2026, 11, 2, 12) + 1, now=now)[0] == [
        utc(2026, 11, 2, 10), utc(2026, 11, 2, 11), utc(2026, 11, 2, 12)]
    assert triggers.occurrences(t, utc(2026, 11, 2, 10) + 1, utc(2026, 11, 2, 12), now=now)[0] == [
        utc(2026, 11, 2, 11)]
    # the stored time itself: outside a window that ends at it, inside one a second longer and one that starts at it
    assert triggers.occurrences(t, utc(2026, 11, 2, 6), utc(2026, 11, 2, 8), now=now)[0] == []
    assert triggers.occurrences(t, utc(2026, 11, 2, 6), utc(2026, 11, 2, 8) + 1, now=now)[0] == [utc(2026, 11, 2, 8)]
    assert triggers.occurrences(t, utc(2026, 11, 2, 8), utc(2026, 11, 2, 9), now=now)[0] == [utc(2026, 11, 2, 8)]


def test_a_schedule_past_its_cap_is_capped_and_one_at_its_cap_is_not():
    start = utc(2026, 11, 2)
    t = {"kind": "schedule", "enabled": 1, "cron": "* * * * *", "tz": "UTC", "next_at": start}
    assert triggers.CAL_CAP == 200
    got, capped = triggers.occurrences(t, start, start + 200 * 60, now=start)
    assert len(got) == 200 and capped is False
    got, capped = triggers.occurrences(t, start, start + 201 * 60, now=start)
    assert got == [start + 60 * i for i in range(200)] and capped is True
    got, capped = triggers.occurrences(t, start, start + DAY, now=start)
    assert len(got) == 200 and capped is True


def test_a_schedule_off_or_with_no_next_time_has_no_coming_times():
    start = utc(2026, 11, 2)
    t = {"kind": "schedule", "enabled": 0, "cron": "0 * * * *", "tz": "UTC", "next_at": start}
    assert triggers.occurrences(t, start, start + DAY) == ([], False)
    t.update(enabled=1, next_at=None)
    assert triggers.occurrences(t, start, start + DAY) == ([], False)
    assert triggers.occurrences({"kind": "hook", "enabled": 1, "next_at": None}, start, start + DAY) == ([], False)


# ---- the window's answer -------------------------------------------------------------------------------------------

def test_the_window_lists_the_account_s_schedules_with_their_coming_times_and_capped(client, owner):
    h, _ = owner
    made = make(client, h, limits={"max_runs_per_day": 24})
    hourly = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC", prompt="sweep")
    busy = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    halves = trigger(client, h, made["id"], kind="schedule", cron="*/30 * * * *", tz="UTC")
    hook = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    start = utc(2026, 11, 2)                               # a UTC day whole: 24 hours, 48 halves
    for t in (hourly, busy, halves):
        set_next(t["id"], start)
    user = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    got = triggers.calendar(user, start, start + DAY, now=start - HOUR)
    assert got["cap"] == 200 and (got["from"], got["to"], got["now"]) == (start, start + DAY, start - HOUR)
    # the route gives the same document for the same window, read at its own time
    assert window(client, h, start, start + DAY)["schedules"][0].keys() == got["schedules"][0].keys()
    s = schedule_of(got, hourly["id"])
    assert s["coming"] == chain("0 * * * *", "UTC", start, start + DAY) and s["capped"] is False
    assert (s["agent_id"], s["agent"], s["kind"], s["cron"], s["tz"], s["enabled"], s["next_at"]) == (
        made["id"], "Triage", "schedule", "0 * * * *", "UTC", True, start)
    assert s["unattended_mode"] == "dontAsk" and s["max_runs_per_day"] == 24
    # more times in a UTC day than the agent's day's runs: marked so
    assert s["more_than_a_day_runs"] is False and schedule_of(got, halves["id"])["more_than_a_day_runs"] is True
    b = schedule_of(got, busy["id"])
    assert len(b["coming"]) == 200 and b["capped"] is True
    # a hook has no times of its own; it is listed, for its firings
    k = schedule_of(got, hook["id"])
    assert k["kind"] == "hook" and k["coming"] == [] and "secret" not in json.dumps(got)
    # what the page may offer: Run now for a schedule that is on, never for a hook
    assert s["actions"] == ["switch", "change", "run_now"] and k["actions"] == ["switch", "change"]


def test_held_missed_and_waiting_firings_come_from_the_rows_the_store_keeps(client, owner):
    h, _ = owner
    made = make(client, h)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    other = trigger(client, h, made["id"], kind="schedule", cron="30 * * * *", tz="UTC")
    start = utc(2026, 11, 2)
    waiting = seed_run(made["id"], user_id, t["id"], "waiting", start + HOUR, due=start + HOUR,
                       reason="it asks its owner a question", started_at=start + HOUR + 600)
    done = seed_run(made["id"], user_id, t["id"], "done", start + 2 * HOUR + 120, due=start + 2 * HOUR)
    failed = seed_run(made["id"], user_id, t["id"], "failed", start + 3 * HOUR, due=start + 3 * HOUR,
                      reason="its run failed")
    blocked = seed_run(made["id"], user_id, t["id"], "blocked", start + 4 * HOUR, due=start + 4 * HOUR,
                       reason="refused at the start")
    stopped = seed_run(made["id"], user_id, t["id"], "stopped", start + 5 * HOUR, reason="its owner stopped it")
    skipped = seed_run(made["id"], user_id, t["id"], "skipped", start + 6 * HOUR)
    queued = seed_run(made["id"], user_id, t["id"], "queued", start + 7 * HOUR)
    working = seed_run(made["id"], user_id, t["id"], "working", start + 8 * HOUR)
    seed_run(made["id"], user_id, t["id"], "done", start - 60)                      # before the window
    seed_run(made["id"], user_id, t["id"], "done", start + DAY)                     # at its end, outside
    seed_run(made["id"], user_id, "aaaaaaaaaaaa", "done", start + 9 * HOUR)         # no trigger of the account's
    seed_run(made["id"], user_id, t["id"], "done", start + 10 * HOUR, kind="manual")  # by hand, not a firing
    seed_run(made["id"], user_id, t["id"], "working", start + 12 * HOUR, kind="review", loop=1)  # the improve loop's
    with store.connect() as db:
        db.execute("UPDATE ade_agent_triggers SET last_at = ?, last_error = ? WHERE id = ?",
                   (start + 11 * HOUR, "the agent is paused", t["id"]))
        db.execute("UPDATE ade_agent_triggers SET missed = 2, last_error = ? WHERE id = ?",
                   ("missed the firing of 2026-11-02 12:30 UTC: the app or the machine was down", other["id"]))
    got = window(client, h, start, start + DAY)
    marks = {(f["trigger_id"], f["run_id"]): f for f in got["firings"]}
    by_run = {f["run_id"]: f for f in got["firings"] if f["run_id"]}
    assert set(by_run) == {waiting, done, failed, blocked, stopped, skipped, queued, working}
    assert [(by_run[r]["state"], by_run[r]["at"]) for r in (waiting, done, failed, blocked, stopped, skipped, queued,
                                                             working)] == [
        ("waits for a person", start + HOUR), ("ran", start + 2 * HOUR + 120), ("failed", start + 3 * HOUR),
        ("failed", start + 4 * HOUR), ("stopped", start + 5 * HOUR), ("skipped", start + 6 * HOUR),
        ("queued", start + 7 * HOUR), ("running", start + 8 * HOUR)]
    assert by_run[waiting]["reason"] == "it asks its owner a question" and by_run[waiting]["run_state"] == "waiting"
    # when its run started, the latest time the store keeps before a wait (it keeps none of a wait's own start)
    assert (by_run[waiting]["started_at"], by_run[stopped]["started_at"]) == (start + HOUR + 600, None)
    # where a record gives the time it was due, its mark says how late it was
    assert (by_run[done]["due"], by_run[done]["late_seconds"]) == (start + 2 * HOUR, 120)
    assert (by_run[stopped]["due"], by_run[stopped]["late_seconds"]) == (None, None)
    # the latest held and the latest missed, from the trigger's own record, the latest only
    assert (t["id"], None) in marks and (other["id"], None) in marks, sorted(marks, key=str)
    held = marks[(t["id"], None)]
    assert (held["state"], held["at"], held["reason"]) == ("held", start + 11 * HOUR, "the agent is paused")
    missed = marks[(other["id"], None)]
    assert (missed["state"], missed["due"], missed["at"]) == ("missed", utc(2026, 11, 2, 12, 30), None)
    assert schedule_of(got, other["id"])["counts"]["missed"] == 2
    # nothing else: no gap drawn, no time worked out again from the line
    assert len(got["firings"]) == 10
    # a run live is a working or waiting one of the account's agents: not the queued one, those ended, nor the loop's
    assert got["live"] == 2


def test_a_held_or_missed_firing_at_the_window_s_end_is_outside_it_and_one_at_its_start_inside(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    other = trigger(client, h, made["id"], kind="schedule", cron="30 * * * *", tz="UTC")
    start = utc(2026, 11, 2)
    with store.connect() as db:
        db.execute("UPDATE ade_agent_triggers SET last_at = ?, last_error = ? WHERE id = ?",
                   (start + DAY, "the agent is paused", t["id"]))
        db.execute("UPDATE ade_agent_triggers SET last_error = ? WHERE id = ?",
                   ("missed the firing of 2026-11-03 00:00 UTC: the app or the machine was down", other["id"]))
    assert window(client, h, start, start + DAY)["firings"] == []
    got = window(client, h, start + DAY, start + 2 * DAY)
    assert sorted((f["trigger_id"], f["state"], f["at"], f["due"]) for f in got["firings"]) == sorted([
        (t["id"], "held", start + DAY, None), (other["id"], "missed", None, start + DAY)])


def test_a_trigger_s_firings_past_the_cap_are_cut_at_the_cap_and_say_so(client, owner):
    h, _ = owner
    made = make(client, h)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    many = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    some = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    start = utc(2026, 11, 2)
    with store.connect() as db:
        for trigger_id, n, offset in ((many["id"], 201, 0), (some["id"], 200, 30)):
            for i in range(n):
                at = start + 60 * i + offset
                db.execute("INSERT INTO ade_agent_runs (run_id, agent_id, user_id, state, trigger, queued_at, "
                           "brief_version, loop, updated_at) VALUES (?,?,?,'done',?,?,1,0,?)",
                           (f"{trigger_id[:6]}{i:06d}", made["id"], user_id,
                            json.dumps({"kind": "schedule", "prompt": None, "at": at, "id": trigger_id}), at, at))
    got = window(client, h, start, start + DAY)
    drawn = {tid: [f["at"] for f in got["firings"] if f["trigger_id"] == tid] for tid in (many["id"], some["id"])}
    # the first CAL_CAP of the 201 in the window, and the view says more were there; 200 are all drawn
    assert drawn[many["id"]] == [start + 60 * i for i in range(200)]
    assert schedule_of(got, many["id"])["past_capped"] is True
    assert len(drawn[some["id"]]) == 200 and schedule_of(got, some["id"])["past_capped"] is False


def test_a_hook_s_delivery_refused_for_its_size_is_no_firing_held(client, owner):
    h, _ = owner
    made = make(client, h)
    hook = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    start = utc(2026, 11, 2)
    signed = {"authorization": f"Bearer {hook['secret']}"}
    client.post(f"{A}/{made['id']}/pause", headers=h)
    assert triggers.deliver(hook["id"], signed, b"{}", now=start + HOUR)[0] == 409          # held: the agent is paused
    assert [(f["state"], f["at"], f["reason"]) for f in window(client, h, start, start + DAY)["firings"]] == [
        ("held", start + HOUR, "the agent is paused")]
    # a delivery refused for its size starts nothing and has no time of its own: its record replaces the held one
    assert triggers.deliver(hook["id"], signed, b"x" * (triggers.MAX_DELIVERY + 1), now=start + 2 * HOUR)[0] == 413
    got = window(client, h, start, start + DAY)
    assert got["firings"] == [] and schedule_of(got, hook["id"])["last_error"].startswith(
        f"a delivery of {triggers.MAX_DELIVERY + 1} bytes was refused")


def test_another_account_s_triggers_and_firings_never_appear(client, owner, monkeypatch):
    h, _ = owner
    mine = make(client, h)
    t = trigger(client, h, mine["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    other_h = signed_up(client, "second-admin@example.com")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},second-admin@example.com")
    theirs = make(client, other_h, name="Theirs")
    u = trigger(client, other_h, theirs["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    other_id = client.get("/api/auth/me", headers=other_h).json()["id"]
    start = t["next_at"]
    seed_run(theirs["id"], other_id, u["id"], "waiting", start + HOUR)
    mine_got = window(client, h, start, start + DAY)
    assert [s["id"] for s in mine_got["schedules"]] == [t["id"]] and mine_got["firings"] == []
    assert [d["trigger_id"] for d in mine_got["due_next"]] == [t["id"]] * len(mine_got["due_next"])
    theirs_got = window(client, other_h, start, start + DAY)
    assert [s["id"] for s in theirs_got["schedules"]] == [u["id"]] and len(theirs_got["firings"]) == 1
    # nor do their live runs set this account's page reading faster
    assert (mine_got["live"], theirs_got["live"]) == (0, 1)


def test_a_refresh_starts_no_machine_and_writes_nothing(client, owner, monkeypatch):
    h, sent = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="* * * * *", tz="UTC")
    trigger(client, h, made["id"], kind="hook", hook_type="github")
    set_next(t["id"], int(time.time()) - 60)          # due: a refresh that ran the scheduler's round would fire it
    started = []
    monkeypatch.setattr(workspaces.LocalProvider, "ensure", lambda self, user: started.append(user["id"]))
    from proofpane_open.ade import workspace_client
    monkeypatch.setattr(workspace_client, "call", lambda *a, **k: started.append("call"))
    sent.clear()
    before = snapshot()
    for _ in range(3):
        window(client, h, t["next_at"] - DAY, t["next_at"] + DAY)
    assert started == [] and sent == [] and snapshot() == before


def test_the_next_firings_across_schedules_with_what_would_hold_each_now(client, owner):
    h, _ = owner
    a = make(client, h)
    b = make(client, h, name="Second")
    t = trigger(client, h, a["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    u = trigger(client, h, b["id"], kind="schedule", cron="30 * * * *", tz="UTC")
    off = trigger(client, h, b["id"], kind="schedule", cron="45 * * * *", tz="UTC")
    client.put(f"{A}/{b['id']}/triggers/{off['id']}", headers=h, json={"enabled": False})
    client.post(f"{A}/{b['id']}/pause", headers=h)
    got = window(client, h, t["next_at"], t["next_at"] + HOUR)
    nxt = got["due_next"]
    assert len(nxt) == triggers.DUE_NEXT == 20
    assert [d["at"] for d in nxt] == sorted(d["at"] for d in nxt)
    assert {d["trigger_id"] for d in nxt} == {t["id"], u["id"]}
    assert nxt[0]["at"] == min(t["next_at"], u["next_at"])
    assert schedule_of(got, u["id"])["held_now"] == "the agent is paused"
    assert schedule_of(got, t["id"])["held_now"] is None
    # Run now only where nothing would hold a firing now: a press of the paused agent's would be refused
    assert (schedule_of(got, t["id"])["actions"], schedule_of(got, u["id"])["actions"]) == (
        ["switch", "change", "run_now"], ["switch", "change"])
    # a schedule that is off fires nothing: nothing would hold it, it has no time to come, and Run now is not offered
    o = schedule_of(got, off["id"])
    assert (o["held_now"], o["coming"], o["actions"]) == (None, [], ["switch", "change"])


def test_the_next_firings_start_from_a_stored_time_passed_then_from_now(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    set_next(t["id"], utc(2026, 11, 2, 8))
    user = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    got = triggers.calendar(user, utc(2026, 11, 2), utc(2026, 11, 3), now=utc(2026, 11, 2, 9, 20))
    # the stored 08:00 is the one the scheduler's next round takes, late; that round sets the next time from its own
    # time, so 09:00 never comes
    assert [d["at"] for d in got["due_next"]] == [utc(2026, 11, 2, 8)] + [utc(2026, 11, 2, 10) + i * HOUR
                                                                         for i in range(19)]
    assert schedule_of(got, t["id"])["coming"][:3] == [utc(2026, 11, 2, 8), utc(2026, 11, 2, 10), utc(2026, 11, 2, 11)]


def test_more_times_in_a_utc_day_than_its_agent_s_runs_is_counted_by_whole_utc_days(client, owner):
    h, _ = owner
    made = make(client, h, limits={"max_runs_per_day": 30})
    half = trigger(client, h, made["id"], kind="schedule", cron="*/30 * * * *", tz="UTC")     # 48 a UTC day
    hourly = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")      # 24 a UTC day
    mondays = trigger(client, h, made["id"], kind="schedule", cron="*/30 * * * 1", tz="UTC")  # 48 on a Monday, else none
    user = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    day = utc(2026, 11, 2)                                                                   # a Monday
    for start, end, now in ((day, day + DAY, day - HOUR),                                    # a whole UTC day
                            (utc(2026, 11, 1, 11), utc(2026, 11, 2, 11), day - HOUR),        # New Zealand's day
                            (day + 15 * HOUR, day + DAY, day + 15 * HOUR)):                  # the rest of one, at 15:00
        got = triggers.calendar(user, start, end, now=now)
        # the Monday counted whole however little of it the window shows, not the 24 hours from the window's start
        marks = tuple(schedule_of(got, x["id"])["more_than_a_day_runs"] for x in (half, hourly, mondays))
        assert marks == (True, False, True), (start, end)
    # counted with next_after alone and at most DAY_COUNTS calls of it: past them, not known
    dense = {"cron": "*/2 * * * *", "tz": "UTC"}                                             # 720 a UTC day
    assert triggers.DAY_COUNTS == 2000
    assert triggers._more_than_a_day(dense, day, day + 2 * DAY, 1000) is False               # 1,442 calls
    assert triggers._more_than_a_day(dense, day, day + 3 * DAY, 1000) is None
    assert triggers._more_than_a_day(dense, day, day + 3 * DAY, 719) is True


def test_a_paused_agent_s_schedule_has_no_coming_times_until_its_pause_lifts(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    start = utc(2026, 11, 2)
    set_next(t["id"], start)
    user = auth.user(client.get("/api/auth/me", headers=h).json()["id"])
    client.post(f"{A}/{made['id']}/pause", headers=h)                    # paused by its owner, until resumed
    s = schedule_of(triggers.calendar(user, start, start + DAY, now=start - HOUR), t["id"])
    assert (s["agent_paused"], s["agent_paused_until"], s["coming"], s["capped"]) == (True, None, [], False)
    with store.connect() as db:                                          # a pause that lifts by itself at 06:00
        db.execute("UPDATE ade_agents SET paused_until = ? WHERE id = ?", (start + 6 * HOUR, made["id"]))
    s = schedule_of(triggers.calendar(user, start, start + DAY, now=start - HOUR), t["id"])
    assert s["agent_paused_until"] == start + 6 * HOUR and s["coming"] == [start + i * HOUR for i in range(6, 24)]
    # once it has lifted, the agent runs as before
    s = schedule_of(triggers.calendar(user, start, start + DAY, now=start + 7 * HOUR), t["id"])
    assert (s["agent_paused"], s["coming"][:2]) == (False, [start, start + 8 * HOUR])


def test_what_would_hold_a_firing_now_counts_its_agent_s_queue_full(client, owner):
    h, _ = owner
    made = make(client, h, limits={"queue_max": 1})
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    seed_run(made["id"], user_id, "bbbbbbbbbbbb", "queued", int(time.time()), kind="manual")   # a run by hand, waiting
    # the improve loop's, bounded by its own budget, is no run of the agent's queue (agents.new_run)
    seed_run(made["id"], user_id, "cccccccccccc", "queued", int(time.time()), kind="review", loop=1)
    s = schedule_of(window(client, h, t["next_at"] - HOUR, t["next_at"] + HOUR), t["id"])
    refused = client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h)
    # the calendar says what the route answers, and offers no Run now
    assert (refused.status_code, refused.json().get("detail")) == (409, s["held_now"])
    assert s["held_now"] == "This agent has 1 runs queued already, its most." and "run_now" not in s["actions"]
    # and the loop's next run is not refused for that queue
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_agents WHERE id = ?", (made["id"],)).fetchone()
        try:
            ran = agents.new_run(db, row, {"kind": "review"}, int(time.time()), loop=True)
        except agents.AgentError as e:
            ran = e
    assert isinstance(ran, str) and len(ran) == 12, ran


@pytest.mark.parametrize("params, status", [
    ({"from": 1000, "to": 1000}, 400),
    ({"from": 2000, "to": 1000}, 400),
    ({"from": -1, "to": 1000}, 400),
    ({"from": 0, "to": 62 * DAY + 1}, 400),             # longer than CAL_DAYS, 62 days
    ({"from": LAST - DAY, "to": LAST + 1}, 400),       # past the latest time a window may reach
    ({"from": "soon", "to": 1000}, 422),
])
def test_a_window_is_from_before_to_and_at_most_its_days(client, owner, params, status):
    h, _ = owner
    assert client.get(f"{A}/calendar", headers=h, params=params).status_code == status
    assert triggers.CAL_DAYS == 62 and client.get(f"{A}/calendar", headers=h,
                                                  params={"from": 0, "to": 62 * DAY}).status_code == 200
    assert triggers.CAL_LAST == LAST and client.get(f"{A}/calendar", headers=h,
                                                    params={"from": LAST - DAY, "to": LAST}).status_code == 200
    # December 2099 in New York, five hours behind UTC, whose month ends after 2100 begins in UTC
    assert client.get(f"{A}/calendar", headers=h, params={"from": utc(2099, 12, 1, 5),
                                                          "to": utc(2100, 1, 1, 5)}).status_code == 200


# ---- the time each firing was due ----------------------------------------------------------------------------------

def test_a_scheduled_firing_keeps_the_time_it_was_due_beside_the_time_it_ran(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC", prompt="sweep")
    rounds = triggers.tick(now=t["next_at"] + 120)
    assert len(rounds) == 1, rounds
    _, run_id, why = rounds[0]
    assert why is None
    with store.connect() as db:
        fired = json.loads(db.execute("SELECT trigger FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert fired == {"kind": "schedule", "prompt": "sweep", "at": t["next_at"] + 120, "id": t["id"],
                     "due": t["next_at"]}
    got = window(client, h, t["next_at"] - HOUR, t["next_at"] + HOUR)
    assert len(got["firings"]) == 1, got["firings"]
    mark = got["firings"][0]
    assert (mark["run_id"], mark["due"], mark["at"], mark["late_seconds"]) == (run_id, t["next_at"],
                                                                                t["next_at"] + 120, 120)


# ---- run now -------------------------------------------------------------------------------------------------------

def test_run_now_fires_the_schedule_once_as_its_scheduler_would_unattended(client, owner):
    h, sent = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 3 * * *", tz="UTC", prompt="the night's sweep")
    sent.clear()
    before = int(time.time())
    r = client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    with store.connect() as db:
        run = db.execute("SELECT * FROM ade_agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        row = db.execute("SELECT * FROM ade_agent_triggers WHERE id = ?", (t["id"],)).fetchone()
    fired = json.loads(run["trigger"])
    # the schedule's own kind, never a manual run: its agent's unattended mode runs it
    assert {k: fired.get(k) for k in ("kind", "prompt", "id", "run_now")} == {
        "kind": "schedule", "prompt": "the night's sweep", "id": t["id"], "run_now": True}
    assert "due" not in fired and fired["at"] >= before and run["state"] == "queued"
    assert sent == [True]                                   # the machine started for it, as Run now does
    # its next time stays, and so does the trigger's own record of its latest firing, here none yet
    assert row["next_at"] == t["next_at"]
    assert (row["last_at"], row["last_run_id"], row["last_error"]) == (None, None, None)
    assert (row["on_time"], row["late"], row["missed"]) == (0, 0, 0)
    got = window(client, h, before - HOUR, before + HOUR)
    mine = [f for f in got["firings"] if f["run_id"] == run_id]
    assert len(mine) == 1, got["firings"]
    mark = mine[0]
    assert mark["run_now"] is True and mark["due"] is None


def test_run_now_is_held_as_a_firing_is_and_refused_for_what_is_no_schedule_of_the_account(client, owner,
                                                                                             monkeypatch):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 3 * * *", tz="UTC")
    hook = trigger(client, h, made["id"], kind="hook", hook_type="bearer")
    client.post(f"{A}/{made['id']}/pause", headers=h)
    r = client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h)
    assert (r.status_code, r.json().get("detail")) == (409, "the agent is paused")
    client.post(f"{A}/{made['id']}/resume", headers=h)
    client.put(f"{A}/{made['id']}/triggers/{t['id']}", headers=h, json={"enabled": False})
    assert client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h).status_code == 409
    assert client.post(f"{A}/{made['id']}/triggers/{hook['id']}/run", headers=h).status_code == 409
    other_h = signed_up(client, "second-admin@example.com")
    monkeypatch.setenv("OPEN_EDITION_ADMINS", f"{ADMIN},second-admin@example.com")
    assert client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=other_h).status_code == 404
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_agent_runs").fetchone()[0] == 0


def test_run_now_leaves_the_trigger_s_own_record_of_its_latest_firing_and_refused_writes_nothing(client, owner):
    h, sent = owner
    made = make(client, h)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC")
    # the app down past the grace: the next round records the firing missed, the trigger's latest (the only record)
    assert [why for _, _, why in triggers.tick(now=t["next_at"] + 31 * 60)] == ["missed"]

    def latest():
        with store.connect() as db:
            return tuple(db.execute("SELECT last_at, last_run_id, last_error, missed, on_time, late FROM "
                                    "ade_agent_triggers WHERE id = ?", (t["id"],)).fetchone())

    before, told = latest(), triggers.alerts(user_id)
    assert before[2].startswith(triggers.MISSED) and told
    # "that one was missed, run it now": its run starts, and the miss is still the trigger's latest firing
    sent.clear()
    r = client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h)
    assert r.status_code == 200 and sent == [True]
    assert latest() == before and triggers.alerts(user_id) == told
    got = window(client, h, int(time.time()) - HOUR, t["next_at"] + HOUR)
    assert sorted((f["state"], f["run_now"], f["due"]) for f in got["firings"]) == [
        ("missed", False, t["next_at"]), ("queued", True, None)]
    # held: its agent paused, a press is refused with why, and writes nothing, so draws nothing and tells nothing
    client.post(f"{A}/{made['id']}/pause", headers=h)
    snap = snapshot()
    r = client.post(f"{A}/{made['id']}/triggers/{t['id']}/run", headers=h)
    assert (r.status_code, r.json().get("detail")) == (409, "the agent is paused")
    assert snapshot() == snap and triggers.alerts(user_id) == told


# ---- each change a version -----------------------------------------------------------------------------------------

def versions(trigger_id):
    with store.connect() as db:
        return [(r["version"], json.loads(r["value"]) if r["value"] is not None else None, r["author"])
                for r in db.execute("SELECT * FROM ade_agent_versions WHERE target = ? ORDER BY version",
                                    (f"trigger:{trigger_id}",))]


def test_each_change_of_a_trigger_is_a_version_and_a_hook_s_secret_is_in_none(client, owner):
    h, _ = owner
    made = make(client, h)
    t = trigger(client, h, made["id"], kind="schedule", cron="0 * * * *", tz="UTC", prompt="sweep")
    client.put(f"{A}/{made['id']}/triggers/{t['id']}", headers=h, json={"cron": "30 * * * *", "enabled": False})
    # a write that changes none of its settings is no change, and no version (as a brief's)
    assert client.put(f"{A}/{made['id']}/triggers/{t['id']}", headers=h, json={"prompt": "sweep"}).status_code == 200
    client.delete(f"{A}/{made['id']}/triggers/{t['id']}", headers=h)
    assert versions(t["id"]) == [
        (1, {"kind": "schedule", "enabled": True, "prompt": "sweep", "cron": "0 * * * *", "tz": "UTC",
             "grace_minutes": 30}, "owner"),
        (2, {"kind": "schedule", "enabled": False, "prompt": "sweep", "cron": "30 * * * *", "tz": "UTC",
             "grace_minutes": 30}, "owner"),
        (3, None, "owner")]
    hook = trigger(client, h, made["id"], kind="hook", hook_type="github", prompt="the delivery")
    assert versions(hook["id"]) == [(1, {"kind": "hook", "enabled": True, "prompt": "the delivery",
                                         "hook_type": "github"}, "owner")]
    off = trigger(client, h, made["id"], kind="schedule", cron="15 * * * *", tz="UTC")
    client.put(f"{A}/{made['id']}/triggers/{off['id']}", headers=h, json={"enabled": False})
    client.post(f"{A}/{made['id']}/retire", headers=h)
    assert versions(hook["id"])[-1] == (2, {"kind": "hook", "enabled": False, "prompt": "the delivery",
                                            "hook_type": "github"}, "owner")
    # retiring turns off what was on, so a trigger already off has no version of it
    assert [v for v, _, _ in versions(off["id"])] == [1, 2]
    # and the calendar offers nothing on a retired agent's triggers, whose every firing is held
    got = schedule_of(window(client, h, int(time.time()) - DAY, int(time.time()) + DAY), hook["id"])
    assert (got["agent_retired"], got["enabled"], got["actions"]) == (True, False, [])
    with store.connect() as db:
        kept = [r[0] for r in db.execute("SELECT value FROM ade_agent_versions")]
    assert not any(hook["secret"] in (v or "") for v in kept)
