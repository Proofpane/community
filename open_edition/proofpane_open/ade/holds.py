"""Risky approvals wait: an approved high-risk action takes effect only after a delay; a notice of it goes at once to
the account's devices, on a channel apart from the page (Web Push, ade/push.py), saying, in fixed words, what kind of
approval waits and when it takes effect; and one action of the owner's cancels it until then.

An action that can be held registers its kind once (register): what is done when its hold ends (apply), and what only
once that has committed; what is done when it is cancelled (cancelled); and the setting that names its delay, with the
delay it has without one. Approving the action places a hold inside the approval's own transaction (place), so an
approval refused leaves none; once that transaction has committed, the notice goes (tell). A hold takes effect at its
time by the server's clock, when holds are first settled at or after it (settle), as the page's reads do, the polls of
a session waiting on one, and each round of the notifier. Its time is fixed when it is placed, from the deployment's
setting: nothing a session sends moves it, and nothing applies a hold before it. A kind whose apply breaks (raises,
where a refusal returns why) ends its hold failed, in a transaction of its own, with what the hold held released as a
refusal releases it (register: released): it is never tried again and again, holding a place no later action can
take. Cancelling (cancel) takes the page's sign-in alone, as a stop does, since a forged cancel can only keep something
from happening, unless the kind asks for more (register: guard), as a change to end-to-end encryption does, since
keeping a revocation from happening keeps a stolen passkey working; it settles first, so a hold whose time has come has
taken effect, or ended failed, and is past cancelling, and one cancelled never takes effect; one past its time that
settling could not apply (the store busy) is cancelled all the same. What leaves a held action unable to take effect
before its time (the passkey that signed it removed, say) ends its hold then, failed with why, in its own transaction
(end).

Linking a session that asked to be linked is held this way, and so is one that replaces a cloud link (ade/link_asks.py),
moving a session into the workspace (ade/moves.py), connecting an app through the Claude app connector (oauth.py), and
each change to end-to-end encryption made while it is on (ade/e2e_keys.py).
"""

import datetime
import os
import re
import sys
import time
import uuid

from .. import store

DEFAULT_DELAY = 180             # seconds, for a kind that names no default of its own
MOST_DELAY = 86400
# why a hold ended when its kind's apply broke: plain words, never the error's, which may hold what the action carries
BROKE = "The server failed as it applied it, so it never took effect: make it again."
_SECONDS = re.compile(r"[0-9]{1,6}")        # whole-text only: fullmatch, never match
_KINDS = {}


class HoldError(Exception):
    """A refusal, with its HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _now(now):
    return int(now if now is not None else time.time())


def utc(ts):
    """A time as a notice writes it, since the server knows no one's time zone: 2026-10-08 14:03:20 UTC."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def register(kind, apply, cancelled=None, setting=None, default=DEFAULT_DELAY, unsigned=False, guard=None,
             released=None, notice="held-other", at_least=None):
    """Make `kind` an action that can be held. apply(db, hold, now) runs in the transaction that marks the hold done,
    once its time has come, and returns None when the action took effect, or one line saying why it could not, which is
    kept on the hold; what may be done only once that transaction has committed (deleting files, say) it adds to
    hold["after"], a list of calls made then. One that raises instead breaks: nothing it did is kept, and its hold ends
    failed (BROKE) in a transaction of its own, in which released(db, hold, now), when given, frees what the hold held
    as a refusal frees it (a place only one action may hold at a time, say). cancelled(db, hold, now) runs in the
    transaction that cancels it.
    `setting` names the environment variable that gives the kind's delay in seconds; `default` is the delay without
    one. `unsigned`: its approval was confirmed, not signed, and the hold keeps nothing of which passkey, or the
    password, confirmed it (a connection keeps it on its grant, oauth.py), so a lost device's steps cancel its holds
    (cancel_all); one a passkey signed ends when that passkey is removed, by its own code (end). guard(db, hold,
    given, now), when given, runs in the transaction that cancels a hold of the kind, before
    anything is changed: it uses up what proves the owner may cancel it (`given`, the cancel's body) or raises
    HoldError, which refuses the cancel and leaves it all as it was; without one the page's sign-in alone cancels.
    `notice` is the kind of the notice of a hold of the kind (push.words): `held-...`, its words when it waits, and
    with `-now` when it was placed with no delay; or notice(db, row, at_once), which gives the kind itself from the
    hold's row (what the hold holds, say), each a kind of push.WORDS. `at_least`, another kind: this kind's delay is
    never shorter than that one's (the stricter tier of an approval waits at least as long as the other)."""
    _KINDS[kind] = {"apply": apply, "cancelled": cancelled, "setting": setting, "default": default,
                    "unsigned": unsigned, "guard": guard, "released": released, "notice": notice, "at_least": at_least}


def delay(kind):
    """The seconds an approval of `kind` waits: its setting, when that is a whole number of seconds from 0 to a day,
    else the kind's default, and never less than its `at_least` kind's (register). Read at each approval, so a change
    of the setting holds the next one."""
    k = _KINDS[kind]
    raw = os.environ.get(k["setting"], "").strip() if k["setting"] else ""
    seconds = int(raw) if _SECONDS.fullmatch(raw) and int(raw) <= MOST_DELAY else k["default"]
    return max(seconds, delay(k["at_least"])) if k.get("at_least") else seconds


def _view(row):
    return {"id": row["id"], "kind": row["kind"], "subject": row["subject"], "summary": row["summary"],
            "approved_at": row["approved_at"], "takes_effect_at": row["takes_effect_at"],
            "cancelled_at": row["cancelled_at"], "done_at": row["done_at"], "failed": row["failed"],
            "told": row["told"]}


def get(db, hold_id):
    """One hold as the page shows it, or None."""
    row = db.execute("SELECT * FROM ade_holds WHERE id = ?", (hold_id,)).fetchone()
    return _view(row) if row is not None else None


def place(db, user_id, kind, subject, summary, now):
    """Inside the approval's own transaction: a hold on the approved action of `kind` on `subject` (the id of what it
    acts on), taking effect delay(kind) seconds after `now`. `summary` says what was approved, in a line the page shows;
    the notice says none of it (tell)."""
    if kind not in _KINDS:
        raise ValueError(f"{kind} is not a kind of hold")
    hold_id = uuid.uuid4().hex
    db.execute("INSERT INTO ade_holds (id, user_id, kind, subject, summary, approved_at, takes_effect_at) "
               "VALUES (?,?,?,?,?,?,?)", (hold_id, user_id, kind, subject, summary, now, now + delay(kind)))
    return get(db, hold_id)


def tell(hold, now=None, post=None):
    """The notice of a hold just placed, sent once its approval's transaction has committed: to every device of the
    account that turned notifications on, at once (push.notice), in fixed words (push.words): what kind of approval
    waits, as its kind names its notice (register: notice), and when it takes effect (or, with no delay, that it was
    approved to take effect at once), never what was approved, which the page shows. The notice is made from a plain
    read, which waits on no writer (the store's journal is a write-ahead log), and sent before anything is written, so
    a store another connection holds for writing delays it no more than it delays a read. Returns how many devices it
    was sent toward, or -1 when the hold or the account's devices could not be read, or the notice could not be made
    or started. Once the hold was read, that is kept on it for the page, in a write of its own, which, when it cannot
    be made, leaves the page showing no count; a hold that could not be read keeps none, since nothing was tried. A
    notice that cannot go never undoes the approval."""
    from . import push
    told, row = -1, None
    try:
        with store.connect() as db:
            row = db.execute("SELECT * FROM ade_holds WHERE id = ?", (hold["id"],)).fetchone()
            # with no delay, nothing is left to cancel: it was approved to take effect at once
            at_once = row["takes_effect_at"] <= row["approved_at"]
            named = _KINDS[row["kind"]]["notice"] if row["kind"] in _KINDS else "held-other"
            held = named(db, row, at_once) if callable(named) else named + ("-now" if at_once else "")
        sent = push.notice(row["user_id"], push.words(held, key="hold:" + row["id"], at=row["takes_effect_at"]), now,
                           post)
        told = -1 if sent is None else sent
    except Exception as e:  # noqa: BLE001  the approval stands; the page still shows the hold, and Cancel works
        print(f"holds: a notice could not be told: {type(e).__name__}", file=sys.stderr)
    if row is None:
        return told
    try:
        with store.connect() as db:
            db.execute("UPDATE ade_holds SET told = ? WHERE id = ?", (told, hold["id"]))
    except Exception as e:  # noqa: BLE001  the page then shows no count, as before the notice
        print(f"holds: how many devices were told could not be kept: {type(e).__name__}", file=sys.stderr)
    return told


def settle(user_id=None, now=None):
    """Apply each hold whose time has come, of `user_id` or of every account, the earliest first, each in a
    transaction of its own that checks again that it is still due: not cancelled, not done. A hold of a kind this
    process has not registered is left for one that has. One whose kind's apply breaks (raises) ends failed (_broke),
    what it held released, so it is not tried again at every settle, holding its place; one that could not be reached
    (the store busy before its apply ran) is left as it was, to be applied at a later settle. Returns the ids of the
    holds that ended."""
    now = _now(now)
    mine = " AND user_id = ?" if user_id is not None else ""
    with store.connect() as db:
        due = [r[0] for r in db.execute(
            "SELECT id FROM ade_holds WHERE cancelled_at IS NULL AND done_at IS NULL AND takes_effect_at <= ?" + mine
            + " ORDER BY takes_effect_at, id", (now,) + ((user_id,) if user_id is not None else ())).fetchall()]
    ended = []
    for hold_id in due:
        broke = None
        try:
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM ade_holds WHERE id = ? AND cancelled_at IS NULL AND done_at IS NULL "
                                 "AND takes_effect_at <= ?", (hold_id, now)).fetchone()
                kind = _KINDS.get(row["kind"]) if row is not None else None
                if kind is None:
                    continue
                hold = dict(row, after=[])
                try:
                    failed = kind["apply"](db, hold, now)
                except Exception as e:  # noqa: BLE001  ends it below, once what it did is undone with its transaction
                    broke = e
                    raise
                db.execute("UPDATE ade_holds SET done_at = ?, failed = ? WHERE id = ?", (now, failed, hold_id))
        except Exception as e:  # noqa: BLE001  one that cannot be applied now keeps neither the others nor the read
            # the hold's id and kind and what was raised, never its message, which may hold what the action carries
            print(f"holds: {hold_id} ({row['kind'] if broke is not None else 'unread'}) could not take effect now: "
                  f"{type(e).__name__}", file=sys.stderr)
            if broke is not None and _broke(hold_id, now):
                ended.append(hold_id)
            continue
        ended.append(hold_id)
        _after(hold)
    return ended


def _broke(hold_id, now):
    """In a transaction of its own, after a hold's kind's apply raised and nothing it did was kept: the hold, still
    neither ended nor cancelled, ends failed (BROKE), and its kind's released() frees what it held, as a refusal does;
    a release that raises too is said, and the hold ends all the same. Whether it ended here."""
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM ade_holds WHERE id = ? AND cancelled_at IS NULL AND done_at IS NULL",
                             (hold_id,)).fetchone()
            if row is None:
                return False
            db.execute("UPDATE ade_holds SET done_at = ?, failed = ? WHERE id = ?", (now, BROKE, hold_id))
            kind = _KINDS.get(row["kind"])
            if kind is not None and kind["released"] is not None:
                db.execute("SAVEPOINT released")
                try:
                    kind["released"](db, dict(row), now)
                except Exception as e:  # noqa: BLE001  the hold has ended; only what it held stays held
                    db.execute("ROLLBACK TO released")
                    print(f"holds: what {hold_id} ({row['kind']}) held could not be released: {type(e).__name__}",
                          file=sys.stderr)
                db.execute("RELEASE released")
            return True
    except Exception as e:  # noqa: BLE001  left as it was, for a later settle to try again
        print(f"holds: {hold_id} could not be ended as failed: {type(e).__name__}", file=sys.stderr)
        return False


def _after(hold):
    """The calls an applied hold's kind left for once its transaction had committed (register), each on its own: one
    that fails is said, and keeps neither the others nor the settle."""
    for call in hold["after"]:
        try:
            call()
        except Exception as e:  # noqa: BLE001  the action has taken effect, and stays so
            print(f"holds: what {hold['id']} left for after it took effect failed: {type(e).__name__}", file=sys.stderr)


def end(db, hold_ids, failed, now):
    """Inside the transaction of what leaves held actions unable to take effect before their time (the passkey that
    signed them removed, say): each of these holds that is neither ended nor cancelled ends now, failed, `failed` its
    one line of why. It never takes effect, and it is past cancelling."""
    for hold_id in hold_ids:
        db.execute("UPDATE ade_holds SET done_at = ?, failed = ? WHERE id = ? AND cancelled_at IS NULL AND "
                   "done_at IS NULL", (now, failed, hold_id))


def cancel(user_id, hold_id, now=None, given=None):
    """Cancel a hold of the account that has not ended: it never takes effect, and its kind's cancelled() runs in the
    same transaction. A kind with a guard (register) is cancelled only when its guard takes `given`, the cancel's body
    (a confirmation, say); a refusal leaves the hold as it was. A second cancel answers as the first. 404 for a hold
    that is not the account's; 409 for one that has ended: one whose time has come, which settling first has applied
    or ended failed, and one ended before its time (end). One past its time that settling could not reach (the store
    busy) has not ended, and is cancelled: a cancel is never refused because a settle failed."""
    now = _now(now)
    settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_holds WHERE id = ? AND user_id = ?", (hold_id, user_id)).fetchone()
        if row is None:
            raise HoldError(404, "No such hold.")
        if row["cancelled_at"] is not None:
            return _view(row)
        if row["done_at"] is not None:
            said = f"it could not take effect ({row['failed'].rstrip('.')})" if row["failed"] else "it has taken effect"
            if row["done_at"] < row["takes_effect_at"]:
                raise HoldError(409, f"It ended at {utc(row['done_at'])}, before its time: {said}.")
            raise HoldError(409, f"Its time came at {utc(row['takes_effect_at'])}: {said}.")
        kind = _KINDS.get(row["kind"])
        if kind is not None and kind["guard"] is not None:
            kind["guard"](db, dict(row), given or {}, now)
        db.execute("UPDATE ade_holds SET cancelled_at = ? WHERE id = ?", (now, hold_id))
        if kind is not None and kind["cancelled"] is not None:
            kind["cancelled"](db, dict(row), now)
        return get(db, hold_id)


def cancel_all(db, user_id, now, after=None):
    """Inside the caller's transaction: each of the account's holds whose time has not come, of a kind whose approval
    names no passkey (register: unsigned), cancelled as cancel does it, its kind's cancelled() run; the ids. The caller
    settles first (settle), so one whose time has come has taken effect, and stays so. A lost device's steps do this
    (removing a passkey, signing out everywhere): such an approval, which may have been made with a sign-in and a
    confirmation taken from that device, never takes effect. `after`, for a held change applied by a settle that runs
    later than the change's own time: the holds whose time is after that one, not after the settle's, so a hold that
    ended between the two, which the same settle would apply next, is cancelled as it would have been on time."""
    # a kind with a guard is cancelled only through it (cancel), whatever else it says
    kinds = [kind for kind, k in _KINDS.items() if k["unsigned"] and k["guard"] is None]
    if not kinds:
        return []
    ids = []
    for row in db.execute(f"SELECT * FROM ade_holds WHERE user_id = ? AND cancelled_at IS NULL AND done_at IS NULL AND "
                          f"takes_effect_at > ? AND kind IN ({','.join('?' * len(kinds))}) ORDER BY takes_effect_at, "
                          "id", (user_id, now if after is None else min(after, now), *kinds)).fetchall():
        db.execute("UPDATE ade_holds SET cancelled_at = ? WHERE id = ?", (now, row["id"]))
        kind = _KINDS.get(row["kind"])
        if kind is not None and kind["cancelled"] is not None:
            kind["cancelled"](db, dict(row), now)
        ids.append(row["id"])
    return ids


def running(user_id, now=None):
    """The account's holds neither ended nor cancelled, the soonest first, once those whose time has come are
    settled."""
    now = _now(now)
    settle(user_id, now)
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_holds WHERE user_id = ? AND cancelled_at IS NULL AND done_at IS NULL "
                          "ORDER BY takes_effect_at, id", (user_id,)).fetchall()
    return [_view(r) for r in rows]
