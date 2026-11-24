"""Deleting an account (queue 4.327; design D, docs/handoff/open-edition/design-account-holds.md, its D15). A deletion
waits: it is placed with a fresh confirmation by account_holds' rule for its maker, as a hold of the kind
account.delete (ade/holds.py), and takes effect only once its delay has passed, unless a credential the rule lets
cancel it does so first. Its delay is this module's own bound, not holds.MOST_DELAY: OPEN_EDITION_ADE_ACCOUNT_DELETE_HOLD,
whole seconds from a day to thirty days, read at each placement; anything else gives seven days. account_holds keeps
the rule and the waiting change's row; it imports nothing but the standard library's dataclasses, json and typing
(tests/test_push_words.py), so what reads and writes the database is here.

Placing it (place): with a confirmation made for "account-delete", a passkey's, or the password's while the account has
no passkey (the password never takes a passkey away); never on the desktop app's own account, which has nothing to
confirm with. The operator places the same hold on a request checked out of band (place_by_operator), the check typed
and kept with it (D16's first clause), never at once: the hold's notice is the owner's chance to cancel, so an account
nothing could cancel for gets none. Each placement is told to the account's devices (holds.tell).

Cancelling it (the generic POST /api/ade/holds/{id}/cancel, _guard): with a confirmation made for
"account-delete-cancel:<hold id>" by a credential that counted when the deletion was placed (account_holds'
cancellers, over the sets kept with it); never the sign-in alone, and neither signing out everywhere nor removing a
passkey ends it (holds.cancel_all leaves a guarded kind alone).

Taking effect (_apply, in the transaction holds.settle applies a hold in, whose own row goes with the account's): the
account's standing offers and custody end as their own ends end them (ade/task_offers.py), then every row keyed by the
account goes: each table with a user_id column, the rows under them (a repository's verifications, a link's puts and
encryption, a grant's tokens, an evaluation's items), the custody it owns, the device sign-ins it approved and the
invitation naming its address, the account itself last; an invitation it made for someone else names a deleted account
as its maker instead of its address. Its pairing goes with its row, as unpairing removes it, and nothing is revoked in
Proofpane. Other accounts' rows that name it by id are kept, as their own rules keep them, the id then naming no
account; the deployment's spending keeps its seconds, the account's id taken from them. Once that has committed: its
moves' files go; on a Fly deployment, in a thread of its own and under the account's lock, so a start under way ends
first, its machines and volumes are destroyed and what such a start wrote for it goes (their Fly ids kept in
account_deleted_fly, with nothing of the account, until a destroy succeeds, fly_provider.sweep_deleted); and the
devices it had, read before its rows went, are told it is done. What is kept of a done deletion is the hold's id and its
time, nothing else (account_deletions), so the device it was placed from can read that it is done (done); the
operator's check goes with the account. Email is not sent: Community sends none (later list 504)."""

import json
import os
import re
import sqlite3
import sys
import threading
import time

from . import account_holds, passkeys, store
from .ade import holds, push

SETTING = "OPEN_EDITION_ADE_ACCOUNT_DELETE_HOLD"
DEFAULT = 604_800                    # seven days (design D's settings table; its D15)
LEAST, MOST = 86_400, 2_592_000      # a day, thirty days
_SECONDS = re.compile(r"[0-9]{1,7}")             # whole-text only: fullmatch, never match
ROUND_EVERY = 60                    # seconds between the deletion's own rounds (start)
SWEEP_AFTER = 900                   # how long after a deletion its stragglers are swept (sweep)
KIND = "account.delete"
SUBJECT = "account-delete"
CANCEL_SUBJECT = "account-delete-cancel:{}"
SUMMARY = "Deleting this account, as you asked with a confirmation made for it."
OPERATOR_SUMMARY = "Deleting this account, as this deployment's operator placed it on a request checked so: {checked}"
_HOLD = re.compile(r"[0-9a-f]{32}")
CHECKED_MOST = 500                  # characters of the operator's check, one line of printable ones

GONE_MAKER = "a deleted account"     # an invitation's maker, where its maker's account was deleted
# the refusals, in the page's words and the command's
DESKTOP = ("This is the desktop app's own account, which has no passkey or password to confirm a deletion with, so it "
           "is not deleted from here.")
PASSKEY_FIRST = ("While the account has a passkey, deleting it is confirmed with one of its passkeys: the password "
                 "never takes a passkey away.")
NOT_MADE = "That confirmation cannot place a deletion of this account."
WAITS_ALREADY = "A deletion of this account waits already, until {at}: cancel it in Account, or let it take effect."
DUE = ("A deletion of this account is due since {at}: it takes effect at the server's next round, and none is placed "
       "in its place.")
OTHER_WAITS = "Another change to this account waits: once it has ended, the deletion can be placed."
NOT_A_CANCELLER = ("That credential did not count when the deletion was placed, so it cannot cancel it: confirm with "
                   "one that did.")
GONE = "The deletion no longer waits."
UNREADABLE = "The waiting deletion could not be read back as it was written, so it is not cancelled."
UNREADABLE_WAITING = ("A deletion of this account waits, but it could not be read back as it was written: ask this "
                      "deployment's operator.")
UNREADABLE_APPLIED = ("The deletion's waiting change could not be read back as it was written, so it did not take "
                      "effect.")
NO_ACCOUNT = "No such account."
GONE_ACCOUNT = "This account was deleted."
NO_CHECK = ("Give the out-of-band check as typed words, one line of up to 500 printable characters, as Python's "
            "isprintable has them: no control, format or separator character but the plain space (so no tab, no "
            "non-breaking space, no zero-width space, no soft hyphen, no line or paragraph separator, no control that "
            "turns text around): --checked TEXT, kept with the deletion.")
UNTOLD = ("No device of the account has notifications on, so its owner would not be told of the deletion: ask them out "
          "of band to turn notifications on, or give --untold to place it all the same.")
NOTHING_CANCELS = ("This account has no password or passkey that could cancel a deletion, so none is placed: the "
                   "hold's notice is its owner's chance to cancel.")
# why an offer or custody of another account's ended (ade/task_offers.py), in its owner's words
OFFER_GONE = "The teammate's account was deleted."
CUSTODIAN_GONE = "The custodian's account was deleted."

# the tables under the account's rows, by the column naming their row and the table it is in; the account's own rows
# in the tables without a user_id column; and the deployment's spending, which keeps its seconds
UNDER = (("ade_verifications", "repo_id", "ade_repos"), ("ade_link_history_puts", "link_id", "ade_links"),
         ("ade_link_e2e", "link_id", "ade_links"), ("ade_connector_tokens", "grant_id", "ade_connector_grants"),
         ("ade_agent_eval_items", "eval_id", "ade_agent_evals"))
KEPT_SPEND = "ade_workspace_spend"


def _spawn(work):
    """Fly's calls after a deletion, in a thread of their own: the settle that applied it, which may be the notifier's
    round for every account, waits for none of them (a start holding the account's lock, Fly's own time)."""
    threading.Thread(target=work, name="account-delete-fly", daemon=True).start()


# the accounts this process deleted in the last SWEEP_AFTER seconds, by id, in its memory alone (never written), for
# the sweep; filled once a deletion has committed (_remember)
_recent = {}
_recent_lock = threading.Lock()


class DeletionError(Exception):
    """A refusal, with its HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _now(now):
    return int(now if now is not None else time.time())


def delay():
    """How long a deletion placed now waits, in seconds: the setting when it is whole seconds within the bound, read as
    holds.delay reads a kind's (design D's settings table: spaces around it taken off), else the default."""
    raw = os.environ.get(SETTING, "").strip()
    if _SECONDS.fullmatch(raw) and LEAST <= int(raw) <= MOST:
        return int(raw)
    return DEFAULT


# ---- placing it -------------------------------------------------------------------------------------------------------

def _placed(db, user_id):
    """The account's credentials as the rule reads them now: its tier, its password (Community's always counts), each
    passkey with whether it is a hardware key, and no recovery code (Community's accounts have none; end-to-end
    encryption's code is encryption's). Each passkey counts: one end-to-end encryption still holds back waits on its
    hold, not in the passkeys table, so every passkey there is one the account has."""
    row = db.execute("SELECT password_hash, high_security FROM users WHERE id = ?", (user_id,)).fetchone()
    keys = tuple(account_holds.Passkey(r["id"], r["tier"] == passkeys.HARDWARE) for r in db.execute(
        "SELECT id, tier FROM passkeys WHERE user_id = ? ORDER BY created_at, id", (user_id,)))
    return account_holds.Placed(account_holds.HIGH if row["high_security"] else account_holds.STANDARD,
                                True if row["password_hash"] else None, keys, None)


def _refuse_if_waiting(db, user_id, now):
    row = db.execute("SELECT p.kind, h.takes_effect_at FROM account_pending p LEFT JOIN ade_holds h ON h.id = p.hold_id "
                     "WHERE p.user_id = ?", (user_id,)).fetchone()
    if row is None:
        return
    if row["kind"] == account_holds.DELETE and row["takes_effect_at"] is not None:
        at = holds.utc(row["takes_effect_at"])
        raise DeletionError(409, (DUE if row["takes_effect_at"] <= now else WAITS_ALREADY).format(at=at))
    raise DeletionError(409, OTHER_WAITS)


def _put(db, user_id, placed, maker, body, summary, net, now):
    """Inside the placing transaction: the hold, delay() from now, and the waiting change's row under it."""
    hold = holds.place(db, user_id, KIND, user_id, summary, now, seconds=delay())
    change = account_holds.Change(maker, account_holds.takes(placed, account_holds.DELETE))
    try:
        account_holds.put_pending(db, user_id, hold["id"], account_holds.DELETE, body, placed, change, net, now)
    except sqlite3.IntegrityError:
        raise DeletionError(409, OTHER_WAITS) from None
    return hold


def _view(hold, pending, body):
    """The deletion that waits, as the page shows it: when it was placed and takes effect, what made it, the
    operator's check where the operator placed it, how many devices its notice was sent toward (holds.tell: -1 when it
    could not go, None before it was tried), and which credentials may cancel it ("password", and passkeys by their
    ids)."""
    maker = pending.change.maker
    made_with = maker if maker in (account_holds.PASSWORD, account_holds.ADMIN) else "passkey"
    checked = body.get("checked") if maker == account_holds.ADMIN and isinstance(body, dict) else None
    return {"id": hold["id"], "approved_at": hold["approved_at"], "takes_effect_at": hold["takes_effect_at"],
            "made_with": made_with, "checked": checked, "told": hold["told"], "cancel_by": sorted(pending.cancellers())}


def _read(db, user_id):
    pending = account_holds.pending(db, user_id)
    if pending is None or pending.kind != account_holds.DELETE:
        return None
    hold = db.execute("SELECT * FROM ade_holds WHERE id = ? AND user_id = ? AND cancelled_at IS NULL AND done_at IS "
                      "NULL", (pending.hold_id, user_id)).fetchone()
    body = db.execute("SELECT body FROM account_pending WHERE user_id = ?", (user_id,)).fetchone()
    return _view(hold, pending, json.loads(body["body"])) if hold is not None and body is not None else None


def place(user_id, confirmation, net=None, now=None):
    """POST /api/account/delete: a deletion of the account, placed with `confirmation` (made for SUBJECT) by a maker
    account_holds' rule allows, waiting delay() seconds; its devices told once it has committed. The confirmation is
    used up only when the deletion is placed. `net`: the network it was asked from (auth.network_of, wide)."""
    now = _now(now)
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise DeletionError(401, GONE_ACCOUNT)          # its own deletion took effect as this settled
        if not passkeys._needs_confirmation(db, user_id):
            raise DeletionError(412, DESKTOP)
        _refuse_if_waiting(db, user_id, now)
        used = passkeys._use_confirmation(db, user_id, confirmation, now, subject=SUBJECT)
        maker = used.passkey_id or account_holds.PASSWORD
        placed = _placed(db, user_id)
        if not account_holds.may_make(placed, account_holds.DELETE, maker):
            raise DeletionError(403, PASSKEY_FIRST if maker == account_holds.PASSWORD and placed.passkeys else NOT_MADE)
        hold = _put(db, user_id, placed, maker, {}, SUMMARY, net, now)
    told = holds.tell(hold, now)
    with store.connect() as db:
        try:
            deletion = _read(db, user_id)
        except ValueError:                  # placed and committed; only its reading back failed
            deletion = None
    return {"deletion": deletion, "told": told}


def waiting(user_id, now=None):
    """GET /api/account/delete: the deletion that waits, as the page shows it (_view), or None; settled first, so one
    whose time has come has taken effect, and then the account is gone: 401, as to a sign-in that ended, never "none
    waits", which a page reads as cancelled."""
    holds.settle(user_id, _now(now))
    with store.connect() as db:
        if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise DeletionError(401, GONE_ACCOUNT)
        try:
            return _read(db, user_id)
        except ValueError:                  # a row read back wrong (account_holds' strict forms): said, never guessed
            raise DeletionError(409, UNREADABLE_WAITING) from None


def _wait_for_notices(timeout=30):
    """The notices a command started (push.notice sends from threads of its own), sent before its process ends."""
    for thread in threading.enumerate():
        if thread.name == "push-notice" and thread is not threading.current_thread():
            thread.join(timeout)


def place_by_operator(email, checked, untold=False, now=None):
    """`python -m proofpane_open delete-account EMAIL --checked TEXT [--untold]`: the same hold on the account of
    `email`, made by the admin, on a request checked out of band, `checked` (one line, typed) kept in the waiting
    change's body and in the hold's summary; never at once. account_holds' check takes an admin-made deletion as it
    stands (it takes everything and carries no code's proof), and its cancellers are every credential that counts, so
    the owner can cancel it; an account with none gets none. The hold's notice is the owner's chance to cancel: where
    no device of the account has notifications on, none is placed but with `untold` (UNTOLD). The command waits up to 30
    seconds for the notices to the account's devices to go (_wait_for_notices)."""
    now = _now(now)
    text = (checked or "").strip()
    # one line of printable characters: no line or paragraph separator, no control that turns text around
    if not (1 <= len(text) <= CHECKED_MOST and text.isprintable()):
        raise DeletionError(422, NO_CHECK)
    from . import auth
    try:
        address = auth.normalise_email(email or "")
    except auth.AuthError as e:
        raise DeletionError(422, str(e)) from None
    # no settle here: a deletion whose time has come is the server's to apply, under its own lock and with its threads,
    # never this command's process, which would end before its notice and its destroy (DUE says so instead)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM users WHERE email = ?", (address,)).fetchone()
        if row is None:
            raise DeletionError(404, NO_ACCOUNT)
        user_id = row["id"]
        _refuse_if_waiting(db, user_id, now)
        placed = _placed(db, user_id)
        if not account_holds.counting(placed):
            raise DeletionError(409, NOTHING_CANCELS)
        if not untold and db.execute("SELECT 1 FROM ade_push_subscriptions WHERE user_id = ? LIMIT 1",
                                     (user_id,)).fetchone() is None:
            raise DeletionError(409, UNTOLD)
        hold = _put(db, user_id, placed, account_holds.ADMIN, {"checked": text}, OPERATOR_SUMMARY.format(checked=text),
                    None, now)
    told = holds.tell(hold, now)
    _wait_for_notices()
    return {"hold": hold["id"], "takes_effect_at": hold["takes_effect_at"], "told": told}


# ---- cancelling it ----------------------------------------------------------------------------------------------------

def _guard(db, hold, given, now):
    """Inside the transaction that cancels the deletion's hold (holds.cancel), before anything is changed: a
    confirmation made for this cancel, used up, by a credential among the deletion's cancellers as it was placed. The
    sign-in alone cancels nothing: holds.HoldError otherwise, the hold left as it was and the confirmation unused."""
    try:
        cancel_by = account_holds.pending_cancellers(db, hold["user_id"], hold["id"])
    except ValueError:
        raise holds.HoldError(409, UNREADABLE) from None
    if cancel_by is None:
        raise holds.HoldError(409, GONE)
    try:
        used = passkeys._use_confirmation(db, hold["user_id"], given.get("confirmation"), now,
                                          subject=CANCEL_SUBJECT.format(hold["id"]))
    except passkeys.PasskeyError as e:
        raise holds.HoldError(e.status, str(e)) from None
    who = None if used is None else used.passkey_id or account_holds.PASSWORD
    if who not in cancel_by:
        raise holds.HoldError(403, NOT_A_CANCELLER)


def _cancelled(db, hold, now):
    """Cancelled, or ended failed (holds' released): the waiting change's row goes, so the account's one place is
    free."""
    db.execute("DELETE FROM account_pending WHERE user_id = ? AND hold_id = ?", (hold["user_id"], hold["id"]))


def _notice(db, row, at_once):
    # a deletion always waits a day at least; one placed with no delay would be told as any other approval is
    return "held-other-now" if at_once else "held-account-delete"


# ---- taking effect ----------------------------------------------------------------------------------------------------

def _end_offers_and_custody(db, user_id, now):
    """The account's standing offers and custody, ended as their own ends end them: its offers withdrawn and its
    custody ended, as their owner ends them; another account's offer to it, not yet accepted, and custody it holds or
    was asked for, ended by the app, the owner's task back. A task already handed to it stays where its owner's way
    back (Take back) returns it."""
    from .ade import task_offers
    for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE user_id = ? AND state IN ('draft', 'held', "
                          "'offered')", (user_id,)).fetchall():
        task_offers._end_offer(db, row, "withdrawn", task_offers.WITHDRAWN, now)
    for row in db.execute("SELECT * FROM ade_task_custody WHERE owner_user = ? AND state IN ('asked', 'accepted', "
                          "'suspended')", (user_id,)).fetchall():
        task_offers._end_custody(db, row, "ended", "owner", task_offers.CUSTODY_ENDED_BY["owner"], now)
    for row in db.execute("SELECT * FROM ade_agent_task_offers WHERE teammate_user = ? AND user_id != ? AND state IN "
                          "('draft', 'held', 'offered')", (user_id, user_id)).fetchall():
        task_offers._end_offer(db, row, "failed", OFFER_GONE, now)      # a draft is deleted, as its own end does
    for row in db.execute("SELECT * FROM ade_task_custody WHERE custodian_user = ? AND owner_user != ? AND state IN "
                          "('asked', 'accepted', 'suspended')", (user_id, user_id)).fetchall():
        task_offers._end_custody(db, row, "ended", "app", CUSTODIAN_GONE, now)


def keyed_tables(db):
    """Every table with a user_id column, as the store has them now: a table added later is swept with the rest."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                      "'sqlite_%' ORDER BY name")]
    return [t for t in names if "user_id" in {c[1] for c in db.execute(f"PRAGMA table_info({t})")}]


def _remove_rows(db, user_id, email):
    for table, column, parent in UNDER:
        db.execute(f"DELETE FROM {table} WHERE {column} IN (SELECT id FROM {parent} WHERE user_id = ?)", (user_id,))
    db.execute("DELETE FROM ade_task_custody WHERE owner_user = ?", (user_id,))
    db.execute("DELETE FROM device_signins WHERE approved_by = ?", (user_id,))
    db.execute("DELETE FROM invitations WHERE email = ?", (email,))
    db.execute("UPDATE invitations SET created_by = ? WHERE created_by = ?", (GONE_MAKER, email))
    # another account's offer or custody naming it keeps the id, as its own rules keep it, and loses the address its
    # owner typed for it, which is the deleted person's
    db.execute("UPDATE ade_agent_task_offers SET address = NULL WHERE teammate_user = ?", (user_id,))
    db.execute("UPDATE ade_task_custody SET address = NULL WHERE custodian_user = ?", (user_id,))
    for table in keyed_tables(db):
        if table == KEPT_SPEND:
            db.execute(f"UPDATE {table} SET user_id = NULL WHERE user_id = ?", (user_id,))
        else:
            db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _apply(db, hold, now):
    """The deletion's time has come: in this transaction, what it ends ended and every row keyed by the account gone;
    what may be done only once that has committed is left in hold["after"] (the module's docstring). None; or why it
    did not take effect: its waiting change gone, or not this hold's, or its account's row gone (GONE), or its waiting
    change read back wrong (UNREADABLE_APPLIED)."""
    from .ade import fly_provider, moves
    user_id = hold["user_id"]
    pending = db.execute("SELECT hold_id, kind, placed, maker, by_code, takes_away FROM account_pending WHERE user_id = ?",
                         (user_id,)).fetchone()
    if pending is None or pending["hold_id"] != hold["id"] or pending["kind"] != account_holds.DELETE:
        return GONE
    try:
        account_holds.read_pending(pending)     # as the cancel reads it: a row it would refuse deletes nothing either
    except ValueError:
        _cancelled(db, hold, now)               # its place freed, as a refusal's is
        return UNREADABLE_APPLIED
    account = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if account is None:
        return GONE
    devices = [dict(r) for r in db.execute("SELECT * FROM ade_push_subscriptions WHERE user_id = ? ORDER BY created_at",
                                           (user_id,))]
    seqs = [r[0] for r in db.execute("SELECT seq FROM ade_link_moves WHERE user_id = ?", (user_id,))]
    workspace = db.execute("SELECT app FROM ade_workspaces WHERE user_id = ?", (user_id,)).fetchone()
    _keep_fly_ids(db, user_id, now)
    _end_offers_and_custody(db, user_id, now)
    _remove_rows(db, user_id, account["email"])
    db.execute("INSERT INTO account_deletions (hold_id, deleted_at) VALUES (?, ?)", (hold["id"], now))
    hold["after"].append(lambda: _remember(user_id, now))
    hold["after"].append(lambda: moves.delete_files(seqs))
    # always, on a Fly deployment: a start under way may make a machine for it after this, its row not yet written
    app = workspace["app"] if workspace is not None else None
    hold["after"].append(lambda: _spawn(lambda: fly_provider.destroy_deleted(user_id, app, now)))
    hold["after"].append(lambda: push.notice_to(devices, push.words("account-deleted", at=now), now))
    return None


# ---- the deletion's own round -----------------------------------------------------------------------------------------

def due(now=None):
    """Each deletion whose time has come, applied (holds.settle, for its account): the notifier's round settles every
    hold each minute, but OPEN_EDITION_ADE_PUSH_EVERY can turn it off, and a deletion must not wait on a request of its
    account's own, which may never come. The accounts it settled."""
    now = _now(now)
    with store.connect() as db:
        accounts = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM ade_holds WHERE kind = ? AND cancelled_at IS "
                                             "NULL AND done_at IS NULL AND takes_effect_at <= ?", (KIND, now))]
    for user_id in accounts:
        holds.settle(user_id, now)
    return accounts


def _keep_fly_ids(db, user_id, now):
    """Inside the caller's transaction: the machine and volume ids the account's workspace rows hold, kept in
    account_deleted_fly (Fly's app and id alone) before the rows go, for the cleanup and the meter to destroy."""
    for kind, column in (("machines", "machine_id"), ("volumes", "volume_id")):
        db.execute(f"INSERT OR IGNORE INTO account_deleted_fly (app, kind, fly_id, created_at) SELECT app, ?, {column}, "
                   f"? FROM ade_workspaces WHERE user_id = ? AND {column} IS NOT NULL AND {column} != ''",
                   (kind, now, user_id))


def _remember(user_id, at):
    with _recent_lock:
        _recent[user_id] = at


def sweep(now=None):
    """For SWEEP_AFTER seconds after this process deleted an account, its id's rows written since, deleted from each
    table keyed by an account (the deployment's spending kept, its id taken off): what work that read the account before
    its deletion committed wrote after it (a request whose own settle applied it, then wrote; the agents' poll; the
    notifier). Only the ids this process deleted are swept, kept in its memory alone and forgotten after the window; a
    row of any other id no account has is left as it is. Each table is read without a lock, and only one holding such a
    row is written, in a transaction of its own. How many rows."""
    now = _now(now)
    with _recent_lock:
        for user_id in [u for u, at in _recent.items() if at < now - SWEEP_AFTER]:
            del _recent[user_id]
        ids = list(_recent)
    if not ids:
        return 0
    with store.connect() as db:
        tables = keyed_tables(db)
    gone = 0
    for user_id in ids:
        for table in tables:
            with store.connect() as db:
                if db.execute(f"SELECT 1 FROM {table} WHERE user_id = ? LIMIT 1", (user_id,)).fetchone() is None:
                    continue
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if table == "ade_workspaces":           # a start's row and saves since: its Fly ids kept first
                    _keep_fly_ids(db, user_id, now)
                if table == KEPT_SPEND:
                    gone += db.execute(f"UPDATE {table} SET user_id = NULL WHERE user_id = ?", (user_id,)).rowcount
                else:
                    gone += db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,)).rowcount
    return gone


def round_once(now=None):
    """One of the deletion's own rounds: the deletions due, then the sweep."""
    due(now)
    return sweep(now)


def start(every=ROUND_EVERY):
    """The deletion's own round every `every` seconds, in a thread of this process while it serves (the app's
    lifespan; the tests, which never enter it, call round_once themselves)."""
    def loop():
        while True:
            time.sleep(every)
            try:
                round_once()
            except Exception as e:  # noqa: BLE001  the round never takes the app down; the next one tries again
                print(f"account deletion's round: {type(e).__name__}", file=sys.stderr)
    worker = threading.Thread(target=loop, name="account-delete-round", daemon=True)
    worker.start()
    return worker


def done(hold_id):
    """GET /api/account/deletion/{hold_id}: {"deleted_at"} for a deletion done under that hold, or None (one that waits,
    was cancelled, or never was)."""
    if not isinstance(hold_id, str) or not _HOLD.fullmatch(hold_id):
        return None
    with store.connect() as db:
        row = db.execute("SELECT deleted_at FROM account_deletions WHERE hold_id = ?", (hold_id,)).fetchone()
    return {"deleted_at": row["deleted_at"]} if row is not None else None


holds.register(KIND, _apply, _cancelled, guard=_guard, released=_cancelled, notice=_notice, default=DEFAULT)
