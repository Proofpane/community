"""The skills the owner's sessions called, counted by the app.

The workspace keeps the lines its sessions' skills hooks wrote, one for each call of a skill, in a record of its own,
and serves them numbered, a page at a time, on `GET /skills?since=N` (proofpane_workspace/skillcalls.py). This module
reads that route for an owner, in the poll's round for each owner the round reaches (agents.poll_all: the owners with a
controller or an agent, and those the app has read before) and when the owner opens the Skills view
(POST /api/ade/skills/read), which also reaches an owner with neither. Each line is taken once, by its own id: a record
the workspace made anew (one put aside because it could not be read) reads each session's file from its start again and
gives the same lines back under new numbers, which a count by number would take twice. The app reads on from the number
the last page ended at, kept with the record's own id, so that a record with another id is read from its start.

A workspace that does not serve the route (an older image) answers 404 with no refusal in it (the answer of a workspace
agent to a path it does not serve), and is read as having no lines; the view says so (`unserved_at`). Any other failure
is said on standard error and in the view (`read_error`, kept with since when until a read goes through), and the next
read tries again. Nothing a page holds stops the round: a read never raises into it.

What is counted is the sessions' word, for display: a program a session runs can write its own `skills.jsonl` and the
workspace's record too (they all run as one user), so a count is not a record that holds against a session. It can also
miss calls: a line lost to a cut of a session's file, a session removed before its file was read, a line the workspace
held out of shape (`dropped`, the workspace's count), a line this module refused as out of shape (`refused`), and a line
the workspace dropped, past its 30 days or 20,000 lines, before the app read it (`missed`: the numbers the record
skipped between two reads, which the app sees; lines dropped before its first read of a record are not counted, since
the app never had them to miss). The view says each of these.

Calls are kept KEPT_DAYS days, as an agent's runs are (agents.KEPT_DAYS): a call goes when its UTC day is more than that
many days before today's, so the oldest day shown is whole. An owner's calls are also kept to CALLS_MOST: a program a
session runs can write lines as fast as the workspace takes them, so past that many the oldest (by their time) go as a
page is taken, and the view says how many were dropped (`trimmed`). The view counts calls by UTC day, by skill (core,
business by repository, other), by the version each call used, where they were made (sessions, repositories, controller
tasks and the requirements they belong to) and how the tasks that called a skill ended (a controller task's end, from
the record the controller's sync keeps: ade/controller.py).

Every function that takes `db` runs inside the caller's transaction and commits nothing itself; the rest open their
own (the view and the detail read in one transaction, a snapshot of the store, so that the queries of one answer
agree). Refusals raise agents.AgentError(status, detail), answered by main.py as {"detail": ...}.
"""

import re
import sys
import threading
import time

from .. import store
from . import agents, controller

KEPT_DAYS = 90                      # as agents.KEPT_DAYS: the oldest day goes first
CALLS_MOST = 100_000                # calls kept for one owner: past it the oldest go, and the view says how many
PAGES = 10                          # pages read at most in one read (a page is at most PAGE lines)
PAGE = 1000                         # the workspace's page (skillcalls.PAGE): a page with more lines is not one
READ_FOR = 60                       # seconds a read goes on starting pages in
PAGE_TIMEOUT = 30                   # seconds a page's call waits on its connection (urllib's: each wait, not the reply)
SLACK = 86400                       # a line may be this far ahead of the app's clock
POLL_EVERY = 3600                   # seconds between the round's reads for an owner with no controller and no agent
WINDOWS = (7, 30, 90)               # the days the view counts over
SKILLS_MOST = 200                   # skills (and, for a business skill, repositories) listed in the view
LISTED = 20                         # sessions, tasks and requirements listed for one skill
VERSIONS_MOST = 30                  # versions listed for one skill
REPOS_MOST = 30                     # repositories listed for one skill

KINDS = ("core", "business", "other")
PATHS = ("claude", "typed")
ENDED = ("done", "failed", "stopped", "open", "unknown")      # how a task that called a skill stands (_ended_key)
_ENDS = ("done", "failed", "stopped")                         # controller.TASK_ENDED
_OPEN = ("starting", "working", "running", "waiting", "idle")  # the other controller.TASK_STATES

FIELDS = ("n", "id", "at", "skill", "kind", "path", "failed", "version", "session", "repo", "task")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")        # activity.NAME: a skill's name, no more
_ID = re.compile(r"[0-9a-f]{16}")
_STORE = re.compile(r"[0-9a-f]{32}")
_SESSION = re.compile(r"[0-9a-f]{12}")
_TASK = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.:-]{0,79}")
_REPO = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.-]{0,99}")
_BUSINESS = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}|uncommitted|unknown")
_CORE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,31}\+(?:[0-9a-f]{64}|cut|unknown)")

NO_ROUTE = "The workspace answered 404."
NOT_ANSWERED = "The workspace did not answer, or the app could not read its answer."
NOT_FORMED = "The workspace's skill calls were not in the form the app reads."
_ANSWERED = re.compile(r"The workspace answered (\d{3})\.")


def _error(status, detail):
    return agents.AgentError(status, detail)


def _now(now):
    return int(now if now is not None else time.time())


def _count(value):
    """A whole number from 0 to 2**53 the workspace gave: a bool is not one."""
    return type(value) is int and 0 <= value < 1 << 53


# ---- the lines a page holds -----------------------------------------------------------------------------------------

def _call(row, now):
    """The call a line of a page gives, as the row the store holds (line id, time, skill, kind, path, failed, version,
    session, repository, task), or None where the line is not one the workspace's record holds: exactly its fields, each
    of its pattern, and the combinations the hook writes (a typed call is a core or a business skill's and has no
    outcome; a call Claude chose has one; a version is of the form of its kind's and an `other` skill has none)."""
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        return None
    n, at = row["n"], row["at"]
    if not (type(n) is int and n >= 1 and type(at) is int and 0 < at <= now + SLACK):
        return None
    skill, kind, path, failed, version = (row[k] for k in ("skill", "kind", "path", "failed", "version"))
    if not (isinstance(row["id"], str) and _ID.fullmatch(row["id"]) and isinstance(skill, str)
            and _NAME.fullmatch(skill) and kind in KINDS and path in PATHS):
        return None
    if (path == "typed") != (failed is None) or not (failed is None or type(failed) is bool):
        return None
    if path == "typed" and kind == "other":
        return None
    if kind == "other":
        if version is not None:
            return None
    elif not (isinstance(version, str) and (_CORE if kind == "core" else _BUSINESS).fullmatch(version)):
        return None
    session, repo, task = row["session"], row["repo"], row["task"]
    if not (isinstance(session, str) and _SESSION.fullmatch(session)):
        return None
    if repo is not None and not (isinstance(repo, str) and _REPO.fullmatch(repo)):
        return None
    if task is not None and not (isinstance(task, str) and _TASK.fullmatch(task)):
        return None
    return (row["id"], at, skill, kind, path, None if failed is None else int(failed), version, session, repo, task)


def _page(got, since, now):
    """A page of `GET /skills` as the app reads it, or None where it is not in the form the workspace's record gives:
    the record's id, `seq`, `more`, `first` (the number of the oldest line held, or none), `dropped` and at most PAGE
    lines, numbered above `since` and rising, the last of them `seq` (no lines: `seq` is the newest number the record
    has given), and `more` only after a page that has lines. Gives {"store", "seq", "more", "first", "dropped",
    "calls", "refused"}: the lines in shape as rows, and how many were not."""
    if not isinstance(got, dict):
        return None
    store_id, seq, more, first = got.get("store"), got.get("seq"), got.get("more"), got.get("first")
    dropped, lines = got.get("dropped"), got.get("lines")
    if not (isinstance(store_id, str) and _STORE.fullmatch(store_id) and _count(seq) and isinstance(more, bool)
            and (first is None or (_count(first) and first >= 1)) and _count(dropped)
            and isinstance(lines, list) and len(lines) <= PAGE):
        return None
    last = since
    for row in lines:
        n = row.get("n") if isinstance(row, dict) else None
        if type(n) is not int or n <= last:
            return None
        last = n
    if lines:
        if seq != last:
            return None
    elif more:
        return None                         # more lines follow a page that has none: the read would not move
    calls = [c for c in (_call(row, now) for row in lines) if c is not None]
    return {"store": store_id, "seq": seq, "more": more, "first": first, "dropped": dropped, "calls": calls,
            "refused": len(lines) - len(calls)}


# ---- the read -------------------------------------------------------------------------------------------------------

def _row(db, user_id):
    return db.execute("SELECT * FROM ade_skill_reads WHERE user_id = ?", (user_id,)).fetchone()


def _ensure(db, user_id, now):
    db.execute("INSERT OR IGNORE INTO ade_skill_reads (user_id, since, created_at, updated_at) VALUES (?, 0, ?, ?)",
               (user_id, now, now))


def users_to_poll(db, now=None):
    """The owners the app has read the workspace's skill calls for before, whose last look is at least POLL_EVERY
    seconds old, or who have none: the round reads each again (agents.poll_all), besides the owners it reads for their
    controller or their agents, whom it reads in every round. A look is a whole read (`read_at`), an answer that the
    workspace serves no route (`unserved_at`), or one that found the machine down or failed (`polled_at`, kept by
    `read`). An hour is far inside what the workspace's own record keeps (30 days), so an owner's calls reach the app
    whether or not its page is open, and the round looks at the machine of an owner with neither a controller nor an
    agent once an hour, not every 30 seconds, whatever the look found. A read that stopped short (`behind`) keeps none
    of the three, and sets `read_behind_at`: an owner whose last read stopped short after the last look of any of the
    three kinds is due again in the next round, whatever the hour says, until a read has caught up."""
    floor = _now(now) - POLL_EVERY
    looked = "MAX(COALESCE(read_at, 0), COALESCE(unserved_at, 0), COALESCE(polled_at, 0))"
    return [r[0] for r in db.execute(
        f"SELECT user_id FROM ade_skill_reads WHERE {looked} <= ? OR COALESCE(read_behind_at, 0) > {looked} "
        "ORDER BY user_id", (floor,)).fetchall()]


def _cutoff(now):
    """The first second kept: the start of the UTC day KEPT_DAYS days before today's."""
    return (now // 86400 - KEPT_DAYS) * 86400


def forget(db, now):
    """Calls whose UTC day is more than KEPT_DAYS days before today's go, the oldest day first."""
    db.execute("DELETE FROM ade_skill_calls WHERE at < ?", (_cutoff(now),))


def _trim(db, uid):
    """The owner's calls past CALLS_MOST go, the oldest first (by their time, then their id). Returns how many went."""
    over = db.execute("SELECT COUNT(*) FROM ade_skill_calls WHERE user_id = ?", (uid,)).fetchone()[0] - CALLS_MOST
    if over <= 0:
        return 0
    return db.execute("DELETE FROM ade_skill_calls WHERE user_id = ? AND line_id IN (SELECT line_id FROM "
                      "ade_skill_calls WHERE user_id = ? ORDER BY at, line_id LIMIT ?)", (uid, uid, over)).rowcount


_lock = threading.Lock()
_syncing = set()
_again = set()


def sync(user, endpoint, call=None):
    """With the machine up: read the workspace's record of skill calls since the number the app read to, a page at a
    time (at most PAGES, and none begun READ_FOR seconds after the read began), and take each page's lines once. One
    read per account at a time: one asked for while another runs returns at once ("busy"), and the one running goes
    round once more when it is done. Returns {"state", "taken"}: `read` (the record's end was reached), `behind` (it
    was not: pages or time ran out, or a failure came after a page that said more follow), `unserved` (a workspace
    without the route), `failed` (the reason is in the view), `busy`; and the lines taken."""
    uid = user["id"]
    with _lock:
        if uid in _syncing:
            _again.add(uid)
            return {"state": "busy", "taken": 0}
        _syncing.add(uid)
    out = {"state": "failed", "taken": 0}
    try:
        while True:
            got = _sync(user, endpoint, call)
            out = {"state": got["state"], "taken": out["taken"] + got["taken"]}
            with _lock:
                if uid not in _again:
                    _syncing.discard(uid)
                    return out
                _again.discard(uid)
    except BaseException:
        with _lock:
            _syncing.discard(uid)
            _again.discard(uid)
        raise


def _no_route(e):
    """Whether a call's failure is a 404 with no refusal in it: the answer of a workspace agent to a path it does not
    serve, which workspace_client gives as 502 with the status in its own words. A 404 one of the workspace's own
    routes gives, with its reason, is a refusal (workspace_client.REFUSALS) and is never this."""
    from . import workspace_client
    return (isinstance(e, workspace_client.WorkspaceCallError) and not isinstance(e, workspace_client.NotAnswering)
            and e.status == 502 and str(e) == NO_ROUTE)


def _failure(e):
    """A call the workspace failed, in the app's own words, with the status it answered: never the words of a refusal,
    which a program in the workspace may have chosen."""
    from . import workspace_client
    if isinstance(e, workspace_client.NotAnswering):
        return NOT_ANSWERED
    if e.status in workspace_client.REFUSALS:
        return f"The workspace refused the read of its skill calls ({e.status})."
    m = _ANSWERED.fullmatch(str(e))
    return f"The workspace answered {m[1] if m else e.status}."


def _read_failed(db, row, error, now):
    """The read's outcome on the owner's row: why it failed, kept with since when until a read goes through."""
    if error is None:
        db.execute("UPDATE ade_skill_reads SET read_error = NULL, read_error_at = NULL WHERE user_id = ?",
                   (row["user_id"],))
    elif row["read_error"] != error:
        db.execute("UPDATE ade_skill_reads SET read_error = ?, read_error_at = ? WHERE user_id = ?",
                   (error, now, row["user_id"]))


def _store_page(uid, page, gap):
    """One page's lines taken once, and where the read stands, in one transaction: a crash between them takes no line
    twice (a line is kept once by its id whatever happens) and loses none. Returns how many lines were new. The owner's
    calls past CALLS_MOST are trimmed in the same transaction, and counted."""
    now = int(time.time())
    with store.connect() as db:
        _ensure(db, uid, now)
        cutoff = _cutoff(now)
        new = 0
        for c in page["calls"]:
            if c[1] < cutoff:
                continue                    # already past what is kept
            new += db.execute("INSERT OR IGNORE INTO ade_skill_calls (user_id, line_id, at, skill, kind, path, failed, "
                              "version, session_id, repo, task_id, taken_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (uid, *c, now)).rowcount
        trimmed = _trim(db, uid) if new else 0
        db.execute("UPDATE ade_skill_reads SET store_id = ?, since = ?, missed = missed + ?, dropped = ?, "
                   "refused = refused + ?, trimmed = trimmed + ?, updated_at = ? WHERE user_id = ?",
                   (page["store"], page["seq"], gap, page["dropped"], page["refused"], trimmed, now, uid))
    return new


def _restart(uid, store_id):
    """The record the workspace now holds is another one (its id is not the one the app read): the app reads it from its
    start."""
    now = int(time.time())
    with store.connect() as db:
        _ensure(db, uid, now)
        db.execute("UPDATE ade_skill_reads SET store_id = ?, since = 0, updated_at = ? WHERE user_id = ?",
                   (store_id, now, uid))


def _sync(user, endpoint, call):
    """One read (the docstring of sync). A page the app cannot take (not in the form the record gives) ends the read
    with a reason; nothing a page holds is taken half."""
    from . import workspace_client
    call = call or workspace_client.call
    uid = user["id"]
    with store.connect() as db:
        row = _row(db, uid)
    held = row["store_id"] if row is not None else None
    since = row["since"] if row is not None else 0
    known = held is not None
    started = int(time.time())
    pages = taken = 0
    whole, error, unserved = False, None, False
    try:
        for i in range(PAGES):
            if i and time.time() - started >= READ_FOR:
                break
            got = call(endpoint, "GET", f"/skills?since={since}", timeout=PAGE_TIMEOUT)
            page = _page(got, since, int(time.time()))
            if page is None:
                error = NOT_FORMED
                break
            if page["store"] != held:
                # a record the app has not read, or one the workspace made anew: its numbers begin again, so a page
                # asked from the number of the old one is not the page to take
                held, known = page["store"], False
                if since:
                    since = 0
                    _restart(uid, held)
                    continue
            elif page["seq"] < since:
                # this record counts from less than the app read to: read it from its start, as one the app has not
                # read (no numbers counted as skipped: a line it took before is taken once all the same, by its id)
                since, known = 0, False
                continue
            first = page["first"]
            # numbers the record skipped between this read and the last, which it dropped before the app read them: only
            # of a record the app had read (the lines dropped before its first read it never had to miss)
            gap = first - since - 1 if known and first is not None and first > since + 1 else 0
            taken += _store_page(uid, page, gap)
            pages += 1
            since, known = page["seq"], True
            if not page["more"]:
                whole = True
                break
    except workspace_client.WorkspaceCallError as e:
        unserved = _no_route(e)
        error = None if unserved else _failure(e)
    except Exception as e:  # noqa: BLE001  nothing a page holds stops the round
        error = f"The app failed reading the workspace's skill calls ({type(e).__name__})."
    if error is not None:
        print(f"skills: reading the workspace's skill calls failed: {error}", file=sys.stderr)
    now = int(time.time())
    with store.connect() as db:
        _ensure(db, uid, now)
        if whole:
            # what a later read first finds was not in the record when this read began
            db.execute("UPDATE ade_skill_reads SET read_at = ?, unserved_at = NULL WHERE user_id = ?", (started, uid))
        elif pages:
            db.execute("UPDATE ade_skill_reads SET read_behind_at = ? WHERE user_id = ?", (now, uid))
        if unserved:
            db.execute("UPDATE ade_skill_reads SET unserved_at = ? WHERE user_id = ?", (now, uid))
        _read_failed(db, _row(db, uid), error, now)
    state = ("unserved" if unserved else "failed" if error is not None else "read" if whole else "behind")
    return {"state": state, "taken": taken}


def read(user, call=None):
    """What the round and the Skills view do for one owner: with the machine up, the read of its skill calls (sync),
    never starting the machine ("not_running" otherwise), never raising. A look that finds the machine down, or fails,
    is kept (`polled_at`, for an owner the app has a row for) so that the round looks again in an hour, not in the next
    round (users_to_poll)."""
    try:
        endpoint = agents._endpoint(user)
        out = {"state": "not_running", "taken": 0} if endpoint is None else sync(user, endpoint, call)
    except Exception as e:  # noqa: BLE001  the poll tries again
        print(f"skills: a read failed: {type(e).__name__}: {e}", file=sys.stderr)
        out = {"state": "failed", "taken": 0}
    if out["state"] in ("not_running", "failed"):
        _polled(user["id"])
    return out


def _polled(uid):
    """Keep the time of a look that found nothing to read, on the owner's row if it has one (none is made: the app has
    no reason to remember an owner it never read for). Never raises: the time is a kindness to the round."""
    try:
        with store.connect() as db:
            db.execute("UPDATE ade_skill_reads SET polled_at = ? WHERE user_id = ?", (int(time.time()), uid))
    except Exception as e:  # noqa: BLE001  the round looks again, sooner than it need
        print(f"skills: the time of a look was not kept: {type(e).__name__}", file=sys.stderr)


# ---- what the page reads --------------------------------------------------------------------------------------------

def _window(days, now):
    """The first second of the window (today's UTC day and the `days` - 1 before it), the first second after it (the end
    of today's UTC day: a call a line dated ahead of the app's clock gives is in no day the page shows, so in none of its
    counts, until its day comes), and the UTC day number of its first and last day."""
    if days not in WINDOWS:
        raise _error(400, "A window is of the last 7, 30 or 90 days.")
    today = now // 86400
    return (today - days + 1) * 86400, (today + 1) * 86400, today - days + 1, today


def _day_text(day):
    return time.strftime("%Y-%m-%d", time.gmtime(day * 86400))


def _ended_key(state):
    """How a task stands: done, failed or stopped as it ended; open as any other state the controller names; unknown for
    one the app has no record of (the controller's sync has not taken it, or its record is past what is kept)."""
    if state in _ENDS:
        return state
    return "open" if state in _OPEN else "unknown"


def _ended(rows):
    out = dict.fromkeys(ENDED, 0)
    for state, n in rows:
        out[_ended_key(state)] += n
    return out


def _reads_view(row):
    if row is None:
        return None
    return {"at": row["read_at"], "behind_at": row["read_behind_at"], "error": row["read_error"],
            "error_at": row["read_error_at"], "unserved_at": row["unserved_at"], "missed": row["missed"],
            "dropped": row["dropped"], "refused": row["refused"], "trimmed": row["trimmed"],
            "since_at": row["created_at"]}


# a business skill is a repository's own, so its row is one per repository; a core or another skill is one row
_GROUP = "CASE WHEN c.kind = 'business' THEN c.repo END"
_TASK_JOIN = "LEFT JOIN ade_controller_tasks t ON t.user_id = c.user_id AND t.id = c.task_id"


def view(user, days=30, now=None):
    """The Skills view: the owner's calls in the last `days` days (7, 30 or 90), counted by UTC day and by skill (a
    business skill by repository), and how the app's reads of the workspace stand. Reads the store alone."""
    now = _now(now)
    floor, ceil, first_day, today = _window(days, now)
    uid = user["id"]
    with store.connect() as db:
        db.execute("BEGIN")             # one snapshot for the queries below: a trim or a forget cannot split them
        reads = _row(db, uid)
        totals = db.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(failed = 1), 0) AS failed, "
            "COALESCE(SUM(path = 'typed'), 0) AS typed, "
            "COUNT(DISTINCT kind || char(0) || skill || char(0) || COALESCE(CASE WHEN kind = 'business' THEN repo END, '')) "
            "AS skills, COUNT(DISTINCT session_id) AS sessions, MIN(at) AS first_at, "
            "MAX(at) AS last_at FROM ade_skill_calls WHERE user_id = ? AND at >= ? AND at < ?", (uid, floor, ceil)).fetchone()
        per_day = {(r["d"], r["kind"]): r["n"] for r in db.execute(
            "SELECT at / 86400 AS d, kind, COUNT(*) AS n FROM ade_skill_calls WHERE user_id = ? AND at >= ? AND at < ? "
            "GROUP BY d, kind", (uid, floor, ceil))}
        main = db.execute(
            f"SELECT c.kind AS kind, c.skill AS skill, {_GROUP} AS grp, COUNT(*) AS calls, "
            "COALESCE(SUM(c.failed = 1), 0) AS failed, COALESCE(SUM(c.path = 'typed'), 0) AS typed, "
            "COUNT(DISTINCT c.session_id) AS sessions, COUNT(DISTINCT c.repo) AS repos, "
            "COUNT(DISTINCT c.task_id) AS tasks, COUNT(DISTINCT t.requirement_id) AS requirements, "
            "COUNT(DISTINCT c.version) AS versions, MIN(c.at) AS first_at, MAX(c.at) AS last_at "
            f"FROM ade_skill_calls c {_TASK_JOIN} WHERE c.user_id = ? AND c.at >= ? AND c.at < ? "
            "GROUP BY c.kind, c.skill, grp ORDER BY calls DESC, c.skill, grp", (uid, floor, ceil)).fetchall()
        latest = {}
        for r in db.execute(
                f"SELECT c.kind AS kind, c.skill AS skill, {_GROUP} AS grp, c.version AS version, MAX(c.at) AS last_at "
                "FROM ade_skill_calls c WHERE c.user_id = ? AND c.at >= ? AND c.at < ? GROUP BY c.kind, c.skill, grp, "
                "c.version", (uid, floor, ceil)):
            key = (r["kind"], r["skill"], r["grp"])
            if key not in latest or r["last_at"] > latest[key][0]:
                latest[key] = (r["last_at"], r["version"])
        ended = {}
        for r in db.execute(
                f"SELECT c.kind AS kind, c.skill AS skill, {_GROUP} AS grp, t.state AS state, "
                f"COUNT(DISTINCT c.task_id) AS n FROM ade_skill_calls c {_TASK_JOIN} "
                "WHERE c.user_id = ? AND c.at >= ? AND c.at < ? AND c.task_id IS NOT NULL GROUP BY c.kind, c.skill, grp, "
                "t.state", (uid, floor, ceil)):
            ended.setdefault((r["kind"], r["skill"], r["grp"]), []).append((r["state"], r["n"]))
    skills = []
    for r in main[:SKILLS_MOST]:
        key = (r["kind"], r["skill"], r["grp"])
        skills.append({"kind": r["kind"], "skill": r["skill"], "repo": r["grp"] if r["kind"] == "business" else None,
                       "calls": r["calls"], "failed": r["failed"], "typed": r["typed"], "sessions": r["sessions"],
                       "repos": r["repos"], "tasks": r["tasks"], "requirements": r["requirements"],
                       "versions": r["versions"], "latest_version": latest[key][1], "first_at": r["first_at"],
                       "last_at": r["last_at"], "ended": _ended(ended.get(key, ()))})
    return {"days": days, "from_day": _day_text(first_day), "to_day": _day_text(today), "now": now,
            "read": _reads_view(reads),
            "totals": {"calls": totals["calls"], "failed": totals["failed"], "typed": totals["typed"],
                       "skills": totals["skills"], "sessions": totals["sessions"], "first_at": totals["first_at"],
                       "last_at": totals["last_at"]},
            "by_day": [dict(day=_day_text(d), **{k: per_day.get((d, k), 0) for k in KINDS})
                       for d in range(first_day, today + 1)],
            "skills": skills, "more": max(0, len(main) - SKILLS_MOST)}


def detail(user, kind, skill, repo=None, by_repo=False, days=30, now=None):
    """One skill's calls in the last `days` days: by UTC day, by version, where they were made (repositories, sessions,
    controller tasks and their requirements) and how the tasks that called it ended. A business skill is a repository's:
    with `by_repo`, only the calls made in `repo` (none: the calls made in no repository), as its row in the view is;
    without, all of its calls. Each list is cut (the LISTED sessions and tasks last called, the LISTED requirements and the
    REPOS_MOST repositories with the most calls, the VERSIONS_MOST versions last called), and `more` says which were.
    Reads the store alone."""
    now = _now(now)
    floor, ceil, first_day, today = _window(days, now)
    if kind not in KINDS or not (isinstance(skill, str) and _NAME.fullmatch(skill)):
        raise _error(404, "No such skill.")
    if by_repo and kind == "business" and not (repo is None or (isinstance(repo, str) and _REPO.fullmatch(repo))):
        raise _error(404, "No such skill.")
    uid = user["id"]
    where, args = "c.user_id = ? AND c.at >= ? AND c.at < ? AND c.kind = ? AND c.skill = ?", [uid, floor, ceil, kind, skill]
    if by_repo and kind == "business":
        where += " AND c.repo IS ?"
        args.append(repo)
    with store.connect() as db:
        db.execute("BEGIN")             # one snapshot, as the view's: the lists below are of the calls the head counted
        head = db.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(c.failed = 1), 0) AS failed, "
            "COALESCE(SUM(c.path = 'typed'), 0) AS typed, "
            f"MIN(c.at) AS first_at, MAX(c.at) AS last_at FROM ade_skill_calls c WHERE {where}", args).fetchone()
        if not head["calls"]:
            raise _error(404, "No calls of that skill in this window.")
        per_day = {r["d"]: (r["n"], r["failed"]) for r in db.execute(
            "SELECT c.at / 86400 AS d, COUNT(*) AS n, COALESCE(SUM(c.failed = 1), 0) AS failed FROM ade_skill_calls c "
            f"WHERE {where} GROUP BY d", args)}
        versions = db.execute(
            "SELECT c.version AS version, COUNT(*) AS calls, MIN(c.at) AS first_at, MAX(c.at) AS last_at "
            f"FROM ade_skill_calls c WHERE {where} GROUP BY c.version ORDER BY last_at DESC, c.version LIMIT ?",
            args + [VERSIONS_MOST + 1]).fetchall()
        repos = db.execute(
            f"SELECT c.repo AS repo, COUNT(*) AS calls FROM ade_skill_calls c WHERE {where} GROUP BY c.repo "
            "ORDER BY calls DESC, c.repo LIMIT ?", args + [REPOS_MOST + 1]).fetchall()
        sessions = db.execute(
            "SELECT c.session_id AS session, COUNT(*) AS calls, MIN(c.at) AS first_at, MAX(c.at) AS last_at "
            f"FROM ade_skill_calls c WHERE {where} GROUP BY c.session_id ORDER BY last_at DESC, c.session_id LIMIT ?",
            args + [LISTED + 1]).fetchall()
        tasks = db.execute(
            "SELECT c.task_id AS task, COUNT(*) AS calls, MAX(c.at) AS last_at, t.state AS state, "
            "t.requirement_id AS requirement, t.ended_at AS ended_at "
            f"FROM ade_skill_calls c {_TASK_JOIN} WHERE {where} AND c.task_id IS NOT NULL GROUP BY c.task_id "
            "ORDER BY last_at DESC, c.task_id LIMIT ?", args + [LISTED + 1]).fetchall()
        ended = _ended((r["state"], r["n"]) for r in db.execute(
            f"SELECT t.state AS state, COUNT(DISTINCT c.task_id) AS n FROM ade_skill_calls c {_TASK_JOIN} "
            f"WHERE {where} AND c.task_id IS NOT NULL GROUP BY t.state", args))
        requirements = db.execute(
            "SELECT t.requirement_id AS requirement, r.title AS title, COUNT(*) AS calls, "
            "COUNT(DISTINCT c.task_id) AS tasks "
            f"FROM ade_skill_calls c {_TASK_JOIN} LEFT JOIN ade_controller_requirements r ON r.user_id = c.user_id "
            f"AND r.id = t.requirement_id WHERE {where} AND t.requirement_id IS NOT NULL "
            "GROUP BY t.requirement_id ORDER BY calls DESC, t.requirement_id LIMIT ?", args + [LISTED + 1]).fetchall()
    # each list was asked for one more than it shows, which says whether it is cut
    more = {"versions": len(versions) > VERSIONS_MOST, "repos": len(repos) > REPOS_MOST,
            "sessions": len(sessions) > LISTED, "tasks": len(tasks) > LISTED, "requirements": len(requirements) > LISTED}
    versions, repos, sessions = versions[:VERSIONS_MOST], repos[:REPOS_MOST], sessions[:LISTED]
    tasks, requirements = tasks[:LISTED], requirements[:LISTED]
    return {"kind": kind, "skill": skill, "repo": repo if by_repo and kind == "business" else None, "days": days,
            "from_day": _day_text(first_day), "to_day": _day_text(today), "calls": head["calls"],
            "failed": head["failed"], "typed": head["typed"], "first_at": head["first_at"],
            "last_at": head["last_at"],
            "by_day": [{"day": _day_text(d), "calls": per_day.get(d, (0, 0))[0], "failed": per_day.get(d, (0, 0))[1]}
                       for d in range(first_day, today + 1)],
            "versions": [{"version": r["version"], "calls": r["calls"], "first_at": r["first_at"],
                          "last_at": r["last_at"]} for r in versions],
            "repos": [{"repo": r["repo"], "calls": r["calls"]} for r in repos],
            "sessions": [{"session": r["session"], "calls": r["calls"], "first_at": r["first_at"],
                          "last_at": r["last_at"]} for r in sessions],
            "tasks": [{"task": r["task"], "calls": r["calls"], "last_at": r["last_at"], "state": r["state"],
                       "requirement": r["requirement"], "ended_at": r["ended_at"]} for r in tasks],
            "requirements": [{"requirement": r["requirement"],
                              "title": None if r["title"] is None else controller._shown(r["title"]),
                              "calls": r["calls"], "tasks": r["tasks"]} for r in requirements],
            "ended": ended, "more": more}
