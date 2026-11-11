"""An agent's triggers: a schedule, which the app keeps and fires (the workspace machine stops when idle, so it cannot),
and a hook, an address a service posts to. Each starts a run as Run now does, the machine started first when stopped,
and the run acts as the agent's unattended mode says (proofpane_workspace/agents.py).

A schedule is a cron line and a timezone (cron.py), with a prompt for its runs. A scheduler thread of its own reads the
due ones every SCHEDULE_EVERY seconds. A firing found more than its grace late (the app or the machine was down) is
recorded as missed, not run. One firing while a run of it still waits to start is not queued again.

A hook is either `github` (GitHub signs each delivery with an HMAC-SHA256 of its body, `X-Hub-Signature-256`; the
secret is kept in the app's store to check it) or `bearer` (`Authorization: Bearer <secret>`; only the secret's sha256
is kept). The secret is shown once, when the hook is made. A delivery without the right signature answers 401, starts
nothing and writes only its count; more than HOOK_MOST a minute answer 429; a body over MAX_DELIVERY answers 413. The
delivery reaches its run as a file of the worktree's, named in its first message as data, never an instruction.

Each firing is held back, and says why on the trigger, when the agent is paused or retired, its day's runs are spent,
the plan's window is exhausted until it resets, the account keeps its work elsewhere than the workspace this deployment
runs for it, as it chose (storage.py), or this month's machine hours have reached the user's share of the cap
(OPEN_EDITION_ADE_TRIGGER_HOURS, 200 by default, on the fly provider), so one user's schedules cannot take another's
room to start.

A scheduled firing's run keeps the time it was due (`due`) beside the time its round fired it (`at`). Run now fires one
schedule once, outside its times, as its scheduler's round would (`fire`): held as a firing is, by the same checks in
the same transaction, and run in its agent's unattended mode; its run says `run_now`, its next time does not move, and
it leaves the trigger's own record of its latest firing (`last_at`, `last_run_id`, `last_error`) as it was, that being,
with no record of each held and missed firing, the only record of the latest; a run now held is refused
and writes nothing. Each change of a trigger's settings is a version of the agent's (`ade_agent_versions`, target
`trigger:<id>`), its settings and never a hook's secret.

The calendar: the account's triggers in a window, each schedule's
coming times from its stored next time and then the times its scheduler will set after it (occurrences), at most
CAL_CAP of them, none while its agent is paused (from the time a pause lifts by itself); and the firings the app's
store records in the window: the runs a trigger of the account's queued, and each trigger's latest held or missed
firing, its own record, the latest only. Read from the app's store alone: it starts no machine, calls no model and
writes nothing.
"""

import calendar as _calendar

import hashlib
import hmac
import os
import secrets
import sys
import threading
import time

from .. import auth, config, store
from . import agents, cron, workspaces

KINDS = ("schedule", "hook")
HOOK_TYPES = ("github", "bearer")
PER_AGENT = 10
GRACE = 30                      # minutes a firing may be late and still run
LATE = 5 * 60                   # seconds after its time a firing is counted late (the trigger's adherence)
SCHEDULE_EVERY = 30
HOOK_WINDOW = 60
HOOK_MOST = 60
MAX_DELIVERY = 1024 * 1024
TRIGGER_HOURS = 200
CAL_CAP = 200                   # a schedule's times drawn per view, past which it is a band
CAL_DAYS = 62                   # the longest window a calendar shows
CAL_LAST = _calendar.timegm((2100, 1, 2, 0, 0, 0))    # the latest time a window may reach: 2099's end in every zone
DAY_COUNTS = 2000               # next_after calls a schedule's count of its times in each UTC day of a window may take
DUE_NEXT = 20                   # the coming firings listed next, across the account's schedules
MISSED = "missed the firing of "
TOO_BIG = "a delivery of "
_lock = threading.Lock()
_scheduler = []


class TriggerError(agents.AgentError):
    pass


def share_hours():
    try:
        return max(0, int(os.environ.get("OPEN_EDITION_ADE_TRIGGER_HOURS", TRIGGER_HOURS)))
    except ValueError:
        return TRIGGER_HOURS


def check(given, partial=False, kind=None, current=None):
    """A trigger's fields from the page, checked; a change (`partial`) keeps what it does not give of `current`."""
    if not isinstance(given, dict):
        raise TriggerError(400, "A trigger is an object.")
    out = {}
    kind = kind or given.get("kind")
    if kind not in KINDS:
        raise TriggerError(400, "A trigger is a schedule or a hook.")
    if not partial:
        out["kind"] = kind
    if kind == "schedule" and (not partial or "cron" in given or "tz" in given):
        current = current or {}
        try:
            schedule = cron.Schedule(given.get("cron", current.get("cron")),
                                     given.get("tz", current.get("tz")) or "UTC")
        except cron.CronError as e:
            raise TriggerError(400, str(e)) from None
        out.update(cron=schedule.line, tz=schedule.tz)
    if kind == "hook" and not partial:
        if given.get("hook_type") not in HOOK_TYPES:
            raise TriggerError(400, "A hook is signed as GitHub signs its deliveries, or with a bearer secret.")
        out["hook_type"] = given["hook_type"]
    if not partial or "prompt" in given:
        out["prompt"] = agents._text(given.get("prompt"), agents.MAX_PROMPT, "A trigger's prompt")
    if not partial or "grace_minutes" in given:
        grace = given.get("grace_minutes", GRACE)
        if not isinstance(grace, int) or isinstance(grace, bool) or not 1 <= grace <= 1440:
            raise TriggerError(400, "A schedule's grace is from 1 to 1440 minutes.")
        out["grace_minutes"] = grace
    if not partial or "enabled" in given:
        out["enabled"] = 0 if given.get("enabled") is False else 1
    return out


def view(row, now=None, skipped=None):
    out = {"id": row["id"], "agent_id": row["agent_id"], "kind": row["kind"], "enabled": bool(row["enabled"]),
           "prompt": row["prompt"], "last_at": row["last_at"], "last_run_id": row["last_run_id"],
           "last_error": row["last_error"], "missed": row["missed"]}
    if row["kind"] == "schedule":
        out.update(cron=row["cron"], tz=row["tz"], grace_minutes=row["grace_minutes"], next_at=row["next_at"],
                   on_time=row["on_time"], late=row["late"], skipped=skipped or 0)
    else:
        out.update(hook_type=row["hook_type"], refused=row["refused"], url=hook_url(row["id"]))
    return out


def hook_url(hook_id):
    return f"{config.public_url()}/api/ade/hooks/{hook_id}"


def _skipped(db, trigger_id, agent_id=None):
    """Its runs its agent's precheck skipped (of the runs kept: agents.forget); read among its agent's runs alone where
    `agent_id` is given."""
    if agent_id is not None:
        return db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE agent_id = ? AND state = 'skipped' AND trigger "
                          "LIKE ?", (agent_id, f'%"id": "{trigger_id}"%')).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE state = 'skipped' AND trigger LIKE ?",
                      (f'%"id": "{trigger_id}"%',)).fetchone()[0]


def of_agent(db, user_id, agent_id):
    rows = db.execute("SELECT * FROM ade_agent_triggers WHERE user_id = ? AND agent_id = ? ORDER BY created_at",
                      (user_id, agent_id)).fetchall()
    return [view(r, skipped=_skipped(db, r["id"])) for r in rows]


def create(user, agent_id, given, now=None):
    """A new trigger for the agent; a hook's secret is in what comes back, this once."""
    fields = check(given)
    now = int(now if now is not None else time.time())
    secret = None
    with store.connect() as db:
        row = agents._row(db, user["id"], agent_id)
        if row["retired_at"]:
            raise TriggerError(409, "This agent is retired.")
        if db.execute("SELECT COUNT(*) FROM ade_agent_triggers WHERE agent_id = ?", (agent_id,)).fetchone()[0] \
                >= PER_AGENT:
            raise TriggerError(409, f"An agent has at most {PER_AGENT} triggers.")
        trigger_id = secrets.token_hex(6)
        if fields["kind"] == "hook":
            secret = secrets.token_urlsafe(32)
            fields["secret"] = secret if fields["hook_type"] == "github" else _digest(secret)
        else:
            fields["next_at"] = cron.Schedule(fields["cron"], fields["tz"]).next_after(now)
        fields.update(id=trigger_id, user_id=user["id"], agent_id=agent_id, created_at=now, updated_at=now)
        db.execute("INSERT INTO ade_agent_triggers (" + ", ".join(fields) + ") VALUES (" +
                   ", ".join("?" for _ in fields) + ")", tuple(fields.values()))
        got = db.execute("SELECT * FROM ade_agent_triggers WHERE id = ?", (trigger_id,)).fetchone()
        record(db, got, now)
    out = view(got)
    if secret:
        out["secret"] = secret
    return out


def update(user, agent_id, trigger_id, given, now=None):
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        row = _trigger(db, user["id"], agent_id, trigger_id)
        fields = check(given, partial=True, kind=row["kind"], current=dict(row))
        if not fields:
            raise TriggerError(400, "Say what to change.")
        if row["kind"] == "schedule" and ("cron" in fields or fields.get("enabled") == 1):
            fields["next_at"] = cron.Schedule(fields.get("cron", row["cron"]),
                                              fields.get("tz", row["tz"])).next_after(now)
        fields["updated_at"] = now
        db.execute("UPDATE ade_agent_triggers SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
                   (*fields.values(), trigger_id))
        got = db.execute("SELECT * FROM ade_agent_triggers WHERE id = ?", (trigger_id,)).fetchone()
        if settings_of(got) != settings_of(row):        # a version for a change, as a brief's (agents.update)
            record(db, got, now)
        return view(got)


def delete(user, agent_id, trigger_id, now=None):
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        row = _trigger(db, user["id"], agent_id, trigger_id)
        record(db, row, now, removed=True)
        db.execute("DELETE FROM ade_agent_triggers WHERE id = ?", (trigger_id,))
    return {"deleted": trigger_id}


def settings_of(row):
    """A trigger's settings as a version keeps them: what its owner sets, never a hook's secret nor what its firings
    write (its times, counts and last error)."""
    keep = ("kind", "enabled", "prompt") + (("cron", "tz", "grace_minutes") if row["kind"] == "schedule"
                                            else ("hook_type",))
    return {k: bool(row[k]) if k == "enabled" else row[k] for k in keep}


def record(db, row, now, removed=False):
    """A change of the trigger `row`, kept as a version of its agent's (target `trigger:<id>`, the owner its author):
    its settings after the change, or none when it is removed."""
    from . import improve
    improve.version(db, row["agent_id"], row["user_id"], f"trigger:{row['id']}", None if removed else settings_of(row),
                    "owner", now)


def _trigger(db, user_id, agent_id, trigger_id):
    row = db.execute("SELECT * FROM ade_agent_triggers WHERE id = ? AND user_id = ? AND agent_id = ?",
                     (trigger_id, user_id, agent_id)).fetchone() if isinstance(trigger_id, str) else None
    if row is None:
        raise TriggerError(404, "No such trigger.")
    return row


def _digest(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# ---- firing --------------------------------------------------------------------------------------------------------

def _held(db, user, agent, trigger, now):
    """Why a firing is held back now, or None."""
    if agent["retired_at"]:
        return "the agent is retired"
    if agents._paused(agent, now):
        return "the agent is paused"
    start_of_day = now - now % 86400
    today = db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE agent_id = ? AND loop = 0 AND queued_at >= ?",
                       (agent["id"], start_of_day)).fetchone()[0]
    most = agents.limits_of(agent)["max_runs_per_day"]
    if today >= most:
        return f"its day's runs are spent ({today} of {most}): the next firing after midnight UTC runs"
    # held while the backend its run would use, its agent's account, is exhausted, never because another backend is
    # (a run from an older release counts as its agent's own account); an agent with no account, by the account its
    # latest run ran on
    from . import tasks
    window = tasks.held_for_schedules(db, user["id"], agent["account"], now, agent_id=agent["id"])
    if window is not None:
        return window
    if trigger["kind"] == "schedule" and db.execute(
            "SELECT 1 FROM ade_agent_runs WHERE agent_id = ? AND state = 'queued' AND trigger LIKE ?",
            (agent["id"], f'%"id": "{trigger["id"]}"%')).fetchone():
        return "a run of it waits to start already"
    try:
        provider = workspaces.for_user(user)
    except workspaces.WorkspaceAccess as e:
        return str(e)
    # its run would start the workspace, which is never started for an account whose work lives elsewhere, as it
    # chose (storage.py): held here, so no run is queued only to be blocked at its start
    from . import storage
    refused = storage.refusal(user, db)
    if refused is not None:
        return refused
    try:
        hours = provider.month_hours(user, now)
    except workspaces.WorkspaceAccess as e:
        return str(e)
    if hours is not None and hours >= share_hours():
        return (f"this month's machine time, {hours:.0f} hours, has reached the {share_hours()} a user's schedules and "
                "hooks may use")
    return None


def fire(user, trigger, now, payload=None, due=None, run_now=False, as_latest=True):
    """Start a run for the trigger, as Run now does: (the run's id, or None with why it was held). A scheduled
    firing's run keeps the time it was due (`due`); one of Run now (`run_now`) says so. Each firing is written as the
    trigger's latest (`as_latest`), but for Run now's, which writes only the run it starts (run_now)."""
    fired = {"kind": trigger["kind"], "prompt": trigger["prompt"], "at": now, "id": trigger["id"]}
    if due is not None:
        fired["due"] = due
    if run_now:
        fired["run_now"] = True
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")           # firings at once take turns: each counts the day with the others'
        agent = db.execute("SELECT * FROM ade_agents WHERE id = ? AND user_id = ?",
                           (trigger["agent_id"], user["id"])).fetchone()
        why = "the agent is gone" if agent is None else _held(db, user, agent, trigger, now)
        run_id = None
        if why is None:
            try:
                run_id = agents.new_run(db, agent, fired, now, payload)
            except agents.AgentError as e:
                why = str(e)
        if as_latest:
            db.execute("UPDATE ade_agent_triggers SET last_at = ?, last_run_id = COALESCE(?, last_run_id), "
                       "last_error = ?, updated_at = ? WHERE id = ?", (now, run_id, why, now, trigger["id"]))
    if run_id:
        agents._deliver_soon(user, start=True)
    return run_id, why


def tick(now=None):
    """One round of the scheduler: each due schedule fires, or is recorded missed when it is later than its grace;
    either way its next time is set. The tasks' round runs first (tasks.sweep: an automatic resume at a passed reset,
    a lease whose attempt ended in the store), and never keeps the schedules from theirs."""
    from . import tasks
    now = int(now if now is not None else time.time())
    try:
        tasks.sweep(now)
    except Exception as e:  # noqa: BLE001  the schedules fire all the same
        print(f"triggers: the tasks' round failed: {type(e).__name__}", file=sys.stderr)
    with store.connect() as db:
        due = db.execute("SELECT * FROM ade_agent_triggers WHERE kind = 'schedule' AND enabled = 1 AND next_at IS NOT "
                         "NULL AND next_at <= ?", (now,)).fetchall()
    fired = []
    for t in due:
        try:
            following = cron.Schedule(t["cron"], t["tz"]).next_after(now)
        except cron.CronError:
            following = None
        with store.connect() as db:
            # taken by this round only once, whatever another round does meanwhile
            if db.execute("UPDATE ade_agent_triggers SET next_at = ? WHERE id = ? AND next_at = ?",
                          (following, t["id"], t["next_at"])).rowcount != 1:
                continue
        user = auth.user(t["user_id"])
        if user is None:
            continue
        if now - t["next_at"] > t["grace_minutes"] * 60:
            with store.connect() as db:
                db.execute("UPDATE ade_agent_triggers SET missed = missed + 1, last_error = ?, updated_at = ? WHERE "
                           "id = ?", (MISSED + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t["next_at"])) +
                                      ": the app or the machine was down", now, t["id"]))
            fired.append((t["id"], None, "missed"))
            continue
        run_id, why = fire(user, t, now, due=t["next_at"])
        if run_id:
            # its adherence: on time, or late, started more than LATE after its time
            with store.connect() as db:
                db.execute(f"UPDATE ade_agent_triggers SET {'late = late' if now - t['next_at'] > LATE else 'on_time = on_time'}"
                           " + 1 WHERE id = ?", (t["id"],))
        fired.append((t["id"], run_id, why))
    return fired


def run_now(user, agent_id, trigger_id, now=None):
    """One firing of a schedule now, outside its times, as its scheduler's round would fire it: held as a firing is
    held, run in its agent's unattended mode (the run's kind is the schedule's, never a manual run's), the machine
    started for it as Run now starts one; its next time does not move, and the trigger's own record of its latest
    firing stays as it was (fire's `as_latest`): refused when held, it writes nothing."""
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        t = _trigger(db, user["id"], agent_id, trigger_id)
    if t["kind"] != "schedule":
        raise TriggerError(409, "A hook runs when its service posts to it.")
    if not t["enabled"]:
        raise TriggerError(409, "This schedule is off: turn it on to run it now.")
    run_id, why = fire(user, t, now, run_now=True, as_latest=False)
    if run_id is None:
        raise TriggerError(409, why)
    return {"run_id": run_id}


def start_scheduler(every=SCHEDULE_EVERY):
    """The scheduler's own thread, once per process."""
    with _lock:
        if _scheduler:
            return
        def loop():
            while True:
                time.sleep(every)
                try:
                    tick()
                except Exception as e:  # noqa: BLE001  a round failing never stops the next
                    print(f"agents: the scheduler's round failed: {type(e).__name__}: {e}", file=sys.stderr)
        t = threading.Thread(target=loop, name="agents-scheduler", daemon=True)
        t.start()
        _scheduler.append(t)


# ---- hooks ---------------------------------------------------------------------------------------------------------

def deliver(hook_id, headers, body, now=None):
    """A delivery to a hook: (status, answer). Nothing is started without the right signature."""
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        t = db.execute("SELECT * FROM ade_agent_triggers WHERE id = ? AND kind = 'hook'",
                       (hook_id,)).fetchone() if isinstance(hook_id, str) and len(hook_id) == 12 else None
    if t is None or not t["enabled"]:
        return 404, {"detail": "No such hook."}
    if not _signed(t, headers, body):
        with store.connect() as db:
            db.execute("UPDATE ade_agent_triggers SET refused = refused + 1 WHERE id = ?", (t["id"],))
        return 401, {"detail": "The delivery's signature is not this hook's."}
    with store.connect() as db:
        # taken in one statement, so deliveries at once each see the others' (the read and the write never apart)
        taken = db.execute(
            "UPDATE ade_agent_triggers SET window_count = CASE WHEN ? - COALESCE(window_start, 0) >= ? THEN 1 ELSE "
            "window_count + 1 END, window_start = CASE WHEN ? - COALESCE(window_start, 0) >= ? THEN ? ELSE window_start "
            "END WHERE id = ? AND (? - COALESCE(window_start, 0) >= ? OR window_count < ?)",
            (now, HOOK_WINDOW, now, HOOK_WINDOW, now, t["id"], now, HOOK_WINDOW, HOOK_MOST)).rowcount
    if taken != 1:
        return 429, {"detail": f"At most {HOOK_MOST} deliveries a minute."}
    if len(body) > MAX_DELIVERY:
        with store.connect() as db:
            db.execute("UPDATE ade_agent_triggers SET last_error = ?, updated_at = ? WHERE id = ?",
                       (TOO_BIG + f"{len(body)} bytes was refused: at most {MAX_DELIVERY}", now, t["id"]))
        return 413, {"detail": f"A delivery is at most {MAX_DELIVERY // 1024} KB."}
    if (headers.get("x-github-event") or "") == "ping":
        return 200, {"pong": True}
    user = auth.user(t["user_id"])
    if user is None:
        return 404, {"detail": "No such hook."}
    run_id, why = fire(user, t, now, payload=body)
    if run_id is None:
        return 409, {"detail": why}
    return 202, {"run_id": run_id}


def _signed(t, headers, body):
    if t["hook_type"] == "github":
        given = headers.get("x-hub-signature-256") or ""
        want = "sha256=" + hmac.new(t["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(given.encode(), want.encode())
    given = headers.get("authorization") or ""
    if not given.startswith("Bearer "):
        return False
    return hmac.compare_digest(_digest(given[len("Bearer "):]).encode(), t["secret"].encode())


# ---- what the owner is told ----------------------------------------------------------------------------------------

def alerts(user_id):
    """A trigger whose last firing was held, missed or refused at the start, told once per reason: (key, the kind of its
    notice: push.words), a schedule's or a hook's, in fixed words, never its agent's name or the reason, which the page
    shows."""
    with store.connect() as db:
        rows = db.execute("SELECT t.id, t.kind, t.last_error, t.last_run_id FROM ade_agent_triggers t JOIN ade_agents a "
                          "ON a.id = t.agent_id WHERE t.user_id = ? AND t.enabled = 1", (user_id,)).fetchall()
        started = {r["run_id"]: r for r in db.execute(
            "SELECT run_id, state, cause, reason FROM ade_agent_runs WHERE user_id = ? AND cause = 'start'",
            (user_id,))}
    out = []
    for r in rows:
        why = r["last_error"]
        run = started.get(r["last_run_id"])
        if not why and run is not None and run["state"] in ("blocked", "failed"):
            why = run["reason"]
        if why:
            key = f"agent-trigger:{r['id']}:" + hashlib.sha256(why.encode()).hexdigest()[:12]
            out.append((key, "schedule-missed" if r["kind"] == "schedule" else "hook-missed"))
    return out


# ---- the calendar -------------------------------------------

# a run's state as the calendar names it; a working run is running
STATES = {"queued": "queued", "working": "running", "waiting": "waits for a person", "done": "ran",
          "failed": "failed", "blocked": "failed", "stopped": "stopped", "skipped": "skipped"}


def _after_stored(trigger, now):
    """Where a schedule's times after its stored next time are found from: the stored time itself, or now where it has
    passed (its round, late, sets the next time from its own time: tick), so that a time its next round, were it taken
    now, would skip is not given as coming."""
    return max(trigger["next_at"], now)


def occurrences(trigger, start, end, limit=CAL_CAP, now=None):
    """A schedule's coming times in [start, end), as (times, capped): its stored next time first, the time its scheduler
    will use, then the times its scheduler will set after it, next_after (cron.py) from the stored time (or from now,
    where that has passed) and called again from each, never a second reading of its line; for a window that starts
    later, next_after from just before its start, which is the time that walk reaches. At most `limit`, and `capped`
    when the window holds more. No times for a hook, a schedule that is off, or one with no next time."""
    if trigger["kind"] != "schedule" or not trigger["enabled"] or trigger["next_at"] is None:
        return [], False
    try:
        schedule = cron.Schedule(trigger["cron"], trigger["tz"])
    except cron.CronError:
        return [], False
    now = int(now if now is not None else time.time())
    out = [trigger["next_at"]] if start <= trigger["next_at"] < end else []
    t = schedule.next_after(max(_after_stored(trigger, now), start - 1))
    while t is not None and t < end:
        if len(out) == limit:
            return out, True
        out.append(t)
        t = schedule.next_after(t)
    return out, False


def _missed_at(why):
    """The time a missed firing was due, as the scheduler named it in `last_error` (MISSED, then a UTC minute), or
    None."""
    if not isinstance(why, str) or not why.startswith(MISSED):
        return None
    try:
        return _calendar.timegm(time.strptime(why[len(MISSED):len(MISSED) + 20], "%Y-%m-%d %H:%M UTC"))
    except ValueError:
        return None


def _more_than_a_day(trigger, start, end, most):
    """Whether a UTC day that the window [start, end) touches holds more of the schedule's line's times than its
    agent's day's runs (True), none does (False), or, where counting them would take more than DAY_COUNTS calls of
    next_after, not known (None). Each such day is counted whole, from its start, with next_after, whatever part of it
    the window shows and whenever it is read, as the hold counts a UTC day's runs whole (_held); a count stops at the
    first day past `most`. A stored time the line would not give is not one of its times."""
    try:
        schedule = cron.Schedule(trigger["cron"], trigger["tz"])
    except cron.CronError:
        return None
    day, calls = start - start % 86400, 0
    while day < end:
        if calls >= DAY_COUNTS:
            return None
        t, n = schedule.next_after(day - 1), 0
        calls += 1
        while t is not None and t < day + 86400:
            n += 1
            if n > most:
                return True
            if calls >= DAY_COUNTS:
                return None
            t = schedule.next_after(t)
            calls += 1
        if t is None:
            return False                # no time after it: no later day holds one
        day = max(day + 86400, t - t % 86400)
    return False


def _window(start, end):
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (start, end)) \
            or not 0 <= start < end <= CAL_LAST:
        raise TriggerError(400, "A window is from a time to a later one, in unix seconds.")
    if end - start > CAL_DAYS * 86400:
        raise TriggerError(400, f"A window is at most {CAL_DAYS} days.")


def calendar(user, start, end, now=None):
    """The account's triggers in the window [start, end) (unix seconds, at most CAL_DAYS long): each with its coming
    times (occurrences) and what would hold a firing of it now; the firings the app's store records in the window,
    the runs a trigger of the account's queued (with the time each was due, where its run keeps it) and each trigger's
    latest held or missed firing, its own record, the latest only; and the next DUE_NEXT coming firings across the
    account's schedules. Read from the app's store alone: it starts no machine, calls no model and writes nothing."""
    _window(start, end)
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        agents_of = {a["id"]: a for a in db.execute("SELECT * FROM ade_agents WHERE user_id = ?", (user["id"],))}
        rows = [r for r in db.execute("SELECT * FROM ade_agent_triggers WHERE user_id = ? ORDER BY created_at, id",
                                      (user["id"],)) if r["agent_id"] in agents_of]
        ids = {r["id"] for r in rows}
        firings, found = [], {}
        for agent_id in sorted({r["agent_id"] for r in rows}):
            for run in db.execute("SELECT run_id, state, reason, trigger, queued_at, started_at FROM ade_agent_runs "
                                  "WHERE agent_id = ? AND user_id = ? AND loop = 0 AND queued_at >= ? AND "
                                  "queued_at < ? ORDER BY queued_at, run_id", (agent_id, user["id"], start, end)):
                fired = agents._json(run["trigger"], {})
                if fired.get("kind") not in KINDS or fired.get("id") not in ids:
                    continue            # by hand, a task's, the improve loop's, or a trigger since removed
                found[fired["id"]] = found.get(fired["id"], 0) + 1
                if found[fired["id"]] > CAL_CAP:
                    continue
                due = fired.get("due")
                due = due if isinstance(due, int) and not isinstance(due, bool) else None
                firings.append({"trigger_id": fired["id"], "agent_id": agent_id, "record": "run",
                                "run_id": run["run_id"], "at": run["queued_at"], "started_at": run["started_at"],
                                "due": due, "late_seconds": run["queued_at"] - due if due is not None else None,
                                "run_now": fired.get("run_now") is True,
                                "state": STATES.get(run["state"], run["state"]), "run_state": run["state"],
                                "reason": run["reason"]})
        schedules, held_now = [], {}
        for r in rows:
            agent = agents_of[r["agent_id"]]
            why = r["last_error"]
            mark = {"trigger_id": r["id"], "agent_id": r["agent_id"], "record": "trigger", "run_id": None,
                    "started_at": None, "late_seconds": None, "run_now": False, "run_state": None, "reason": why}
            if why and why.startswith(MISSED):
                due = _missed_at(why)
                if due is not None and start <= due < end:
                    firings.append(dict(mark, state="missed", at=None, due=due))
            elif why and not (r["kind"] == "hook" and why.startswith(TOO_BIG)) and r["last_at"] is not None \
                    and start <= r["last_at"] < end:
                # a held firing writes its reason and its time; a delivery refused for its size, no time of its own
                firings.append(dict(mark, state="held", at=r["last_at"], due=None))
            # what would hold a firing now: _held's checks, and its agent's queue full, which new_run refuses
            held_now[r["id"]] = None if not r["enabled"] else (
                _held(db, user, agent, r, now) or agents.queue_full(db, agent))
            paused = agents._paused(agent, now)
            lifts = agent["paused_until"] if paused else None
            if paused and not lifts:
                coming, capped = [], False      # paused by its owner: nothing comes until it is resumed
            else:
                # a pause that lifts by itself at a time holds the times before it
                after = max(start, lifts) if lifts else start
                coming, capped = occurrences(r, after, end, now=now) if after < end else ([], False)
            most = agents.limits_of(agent)["max_runs_per_day"]
            s = {"id": r["id"], "agent_id": agent["id"], "agent": agent["name"], "kind": r["kind"],
                 "enabled": bool(r["enabled"]), "prompt": r["prompt"], "agent_paused": paused,
                 "agent_paused_until": lifts,
                 "agent_retired": bool(agent["retired_at"]), "unattended_mode": agent["unattended_mode"],
                 "max_runs_per_day": most, "held_now": held_now[r["id"]], "coming": coming, "capped": capped,
                 "past_capped": found.get(r["id"], 0) > CAL_CAP, "last_at": r["last_at"],
                 "last_run_id": r["last_run_id"], "last_error": why,
                 "counts": {"missed": r["missed"], "on_time": r["on_time"], "late": r["late"],
                            "refused": r["refused"], "skipped": _skipped(db, r["id"], r["agent_id"])}}
            # what the page may offer: the trigger's own routes, Run now for a schedule that is on and that nothing
            # would hold now (else its route refuses it); none for a retired agent's, whose firings are all held
            actions = [] if agent["retired_at"] else ["switch", "change"] + (
                ["run_now"] if r["kind"] == "schedule" and r["enabled"] and held_now[r["id"]] is None else [])
            if r["kind"] == "schedule":
                s.update(cron=r["cron"], tz=r["tz"], grace_minutes=r["grace_minutes"], next_at=r["next_at"],
                         more_than_a_day_runs=_more_than_a_day(r, start, end, most), actions=actions)
            else:
                s.update(cron=None, tz=None, grace_minutes=None, next_at=None, hook_type=r["hook_type"],
                         more_than_a_day_runs=None, actions=actions)
            schedules.append(s)
        live = db.execute("SELECT COUNT(*) FROM ade_agent_runs WHERE user_id = ? AND loop = 0 AND state IN ('working', "
                          "'waiting')", (user["id"],)).fetchone()[0]
    nexts = []
    for r in rows:
        if r["kind"] != "schedule" or not r["enabled"] or r["next_at"] is None:
            continue
        try:
            schedule = cron.Schedule(r["cron"], r["tz"])
        except cron.CronError:
            continue
        # the stored time, then the times the scheduler will set after it, as occurrences gives them
        nexts.append((r["next_at"], r["id"]))
        t = schedule.next_after(_after_stored(r, now))
        for _ in range(DUE_NEXT - 1):
            if t is None:
                break
            nexts.append((t, r["id"]))
            t = schedule.next_after(t)
    by_id = {r["id"]: r for r in rows}
    due_next = [{"trigger_id": tid, "agent_id": by_id[tid]["agent_id"],
                 "agent": agents_of[by_id[tid]["agent_id"]]["name"], "at": t, "held_now": held_now[tid]}
                for t, tid in sorted(nexts)[:DUE_NEXT]]
    firings.sort(key=lambda f: (f["at"] if f["at"] is not None else f["due"], f["trigger_id"], f["run_id"] or ""))
    return {"from": start, "to": end, "now": now, "cap": CAL_CAP, "days": CAL_DAYS, "live": live,
            "schedules": schedules, "firings": firings, "due_next": due_next}

