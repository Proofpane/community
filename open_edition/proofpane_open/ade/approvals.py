"""The ADE: the approval queue behind a session's hook.

A session's gated commands are held by a hook in the session's own program (`open_edition/ade_hook.py`, a Claude
Code PreToolUse hook), which asks the ADE and waits; the owner approves on any screen. Two rules hold for every
approval:

- **One approval runs the command once.** The first answer that tells a hook of an approval uses it up, whether that
  answer is a read of the request or the reply to the same command sent again: it says `approved` once, and after
  it a read says `used` and a new send makes a new request.
- **A request stays open until it is answered or its time ends**, not for the length of one hook call. A hook waits
  under a minute, so a request the owner has not reached yet is still there when the session sends the same
  command again: the same command, with the same tool, from the same hook token, session and folder, finds the same
  request, and an approval given in between is picked up by that second send. Only then: an approval is for the
  request as the owner saw it, so the same command sent from another folder or session (a shared hook token, a
  session that changed directory) is a request of its own. A request's time
  is the end of the window it was sent in when the hook names one, else an hour; never more than a day. A hook that
  keeps sending keeps its request open: each identical send renews it to that send's own end, never back, and never
  past a day from when it was made (as a device's hold is renewed). The row keeps no window
  of its own: a send in a later window, or one that names none, takes it to that send's end, which the owner's card
  shows; the hook names the same window each time it sends in it.

A hook authenticates with a hook token the owner creates in the ADE for one session: shown once, stored only as its
sha256, revocable. A hook token can create requests and read its own; it cannot approve anything, list anything else,
or act as the account on any other route. The owner's approval needs their signed-in account and their own word
beside it: a confirmation (one of the account's passkeys or its password, a hardware key's while high security is on)
made for that one request, so a sign-in alone approves nothing and a confirmation given for anything else approves
nothing. A denial takes the sign-in alone. The desktop server's own account, which has nothing to confirm with,
approves with the sign-in alone.
"""

import hashlib
import secrets
import time
import uuid

from .. import store

TOKEN_PREFIX = "adeh_"
DEFAULT_TTL = 3600
MAX_TTL = 24 * 3600
MAX_COMMAND = 8000
MAX_NAME = 100
MAX_TOKENS = 20
MAX_OPEN = 100          # open requests per hook token: a session caught in a loop cannot flood the queue


class ApprovalError(ValueError):
    """A request the caller can correct: shown as it is."""


# an approval that comes with no confirmation, from an account that has a way to make one: what a page from an older
# release sends, through either door (the command's own Approve, or an app's proposal answered as it proposed)
NEEDS_CONFIRM_TO_APPROVE = ("Confirm it is you first, with one of this account's passkeys or its password: a sign-in "
                            "alone does not approve a held command. A page from an older release cannot ask for "
                            "that: reload it.")
NEEDS_HARDWARE_TO_APPROVE = ("High security is on: approve a held command with one of this account's hardware keys. A "
                             "page from an older release cannot ask for that: reload it.")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now(now):
    return int(now if now is not None else time.time())


def _text(value, what, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ApprovalError(f"The {what} is text of at most {limit} characters.")
    return value


def create_token(user_id, name, now=None, cap=True):
    """A new hook token for one of the account's sessions. Returns (id, token); the token is not stored and cannot be
    shown again. `cap=False` is the app's own: the token a hosted workspace's sessions are given
    (fly_provider.py), made at each configuration of its machine, which the owner's MAX_TOKENS of their own does not
    refuse; it is one of the account's tokens all the same, listed and revocable as any. Nor does it count among the
    owner's MAX_TOKENS while a workspace's row names it, so the two limits are apart."""
    name = _text(name, "session's name", MAX_NAME).strip()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = uuid.uuid4().hex
    with store.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM ade_hook_tokens t WHERE t.user_id = ? AND t.revoked_at IS NULL "
                           "AND NOT EXISTS (SELECT 1 FROM ade_workspaces w WHERE w.user_id = t.user_id "
                           "AND w.hook_token_id = t.id)", (user_id,)).fetchone()[0]
        if cap and count >= MAX_TOKENS:
            raise ApprovalError(f"An account has at most {MAX_TOKENS} hook tokens; revoke one first.")
        db.execute("INSERT INTO ade_hook_tokens (id, user_id, name, token_hash, created_at) VALUES (?,?,?,?,?)",
                   (token_id, user_id, name, _hash(token), _now(now)))
    return token_id, token


def list_tokens(user_id):
    with store.connect() as db:
        rows = db.execute("SELECT id, name, created_at, last_used_at, revoked_at FROM ade_hook_tokens "
                          "WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def revoke_token(user_id, token_id, now=None):
    with store.connect() as db:
        done = db.execute("UPDATE ade_hook_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND "
                          "revoked_at IS NULL", (_now(now), token_id, user_id)).rowcount
    return bool(done)


def hook_identity(token, now=None):
    """{id, user_id, name} for a live hook token, else None."""
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
        return None
    with store.connect() as db:
        row = db.execute("SELECT id, user_id, name FROM ade_hook_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                         (_hash(token),)).fetchone()
        if row is not None:
            db.execute("UPDATE ade_hook_tokens SET last_used_at = ? WHERE id = ?", (_now(now), row["id"]))
    return dict(row) if row else None


def _state(row, now):
    """A request's state as of `now`: an open request (pending, or approved and not yet read) past its time is
    expired."""
    if row["state"] in ("pending", "approved") and now >= row["until"]:
        return "expired"
    return row["state"]


def _view(row, now):
    out = {k: row[k] for k in ("id", "command", "tool", "session", "cwd", "created_at", "until", "decided_at",
                               "words", "token_name")}
    out["state"] = _state(row, now)
    return out


def request(identity, command, tool="Bash", session=None, cwd=None, until=None, now=None):
    """The open request for this command and tool from this hook token, session and folder, or a new one. Returns
    its view. A session or folder not given matches only one not given (SQLite's IS), so a hook that sends neither
    finds its request as before. A pending one found is renewed to this send's end (a write), within a day of its
    making and never back; an approved one is used up, as a read would."""
    now = _now(now)
    command = _text(command, "command", MAX_COMMAND)
    tool = _text(tool or "Bash", "tool", MAX_NAME)
    session = (session or "")[:MAX_NAME] or None
    cwd = (cwd or "")[:500] or None
    if until is not None and (not isinstance(until, int) or until <= now):
        raise ApprovalError("A request's time must end in the future.")
    end = min(until if until is not None else now + DEFAULT_TTL, now + MAX_TTL)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                          "ON t.id = r.token_id WHERE r.token_id = ? AND r.command = ? AND r.tool = ? AND "
                          "r.session IS ? AND r.cwd IS ? AND r.state IN ('pending', 'approved') "
                          "ORDER BY r.created_at DESC", (identity["id"], command, tool, session, cwd)).fetchall()
        for row in rows:
            state = _state(row, now)
            if state == "pending":
                # the hook still waits on it: renewed to this send's end, forward only, within a day of its making;
                # the update repeats what it read, so a decision made in between is never undone
                renewed = min(end, row["created_at"] + MAX_TTL)
                if renewed > row["until"] and db.execute(
                        "UPDATE ade_requests SET until = ? WHERE id = ? AND state = 'pending' AND until = ?",
                        (renewed, row["id"], row["until"])).rowcount == 1:
                    row = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                                     "ON t.id = r.token_id WHERE r.id = ?", (row["id"],)).fetchone()
                return _view(row, now)
            if state == "approved":
                # the hook learns of this approval here, so this answer uses it up, exactly as a read would: without
                # it, every later send of the same command found the same approval and ran again. Only the send
                # whose update takes it may say "approved"; the transaction already makes two sends take turns, and
                # the row count holds even without it, as read_for_hook's does
                taken = db.execute("UPDATE ade_requests SET state = 'used', used_at = ? WHERE id = ? AND "
                                   "state = 'approved'", (now, row["id"])).rowcount
                if taken == 1:
                    return _view(row, now)
        open_count = db.execute("SELECT COUNT(*) FROM ade_requests WHERE token_id = ? AND state = 'pending' AND "
                                "until > ?", (identity["id"], now)).fetchone()[0]
        if open_count >= MAX_OPEN:
            raise ApprovalError(f"This hook already has {MAX_OPEN} open requests; answer or let some end first.")
        request_id = uuid.uuid4().hex
        db.execute("INSERT INTO ade_requests (id, user_id, token_id, command, tool, session, cwd, created_at, until, "
                   "state) VALUES (?,?,?,?,?,?,?,?,?, 'pending')",
                   (request_id, identity["user_id"], identity["id"], command, tool, session, cwd, now, end))
        row = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                         "ON t.id = r.token_id WHERE r.id = ?", (request_id,)).fetchone()
    return _view(row, now)


def read_for_hook(identity, request_id, now=None):
    """The request's state for the hook that made it; None if it is not that hook's. Reading an approval uses it
    up: this read answers `approved` and every later one `used`."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                         "ON t.id = r.token_id WHERE r.id = ? AND r.token_id = ?", (request_id, identity["id"])).fetchone()
        if row is None:
            return None
        view = _view(row, now)
        if view["state"] == "approved":
            taken = db.execute("UPDATE ade_requests SET state = 'used', used_at = ? WHERE id = ? AND state = "
                               "'approved'", (now, request_id)).rowcount
            if taken != 1:
                view["state"] = "used"
    return view


def list_for_owner(user_id, now=None, limit=30):
    """Every open request of the account, newest first, then the latest `limit` others. The open ones are read on
    their own, so however many answered requests there are, none that waits for the owner is left off the list."""
    now = _now(now)
    select = "SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t ON t.id = r.token_id "
    with store.connect() as db:
        waiting = db.execute(select + "WHERE r.user_id = ? AND r.state = 'pending' AND r.until > ? "
                             "ORDER BY r.created_at DESC", (user_id, now)).fetchall()
        others = db.execute(select + "WHERE r.user_id = ? AND NOT (r.state = 'pending' AND r.until > ?) "
                            "ORDER BY r.created_at DESC LIMIT ?", (user_id, now, int(limit))).fetchall()
    return [_view(r, now) for r in waiting] + [_view(r, now) for r in others]


def waiting(user_id, limit, now=None):
    """The account's requests that wait for the owner, newest first, at most `limit` of them; and how many wait. Read
    so, however many wait, only `limit` rows are."""
    now = _now(now)
    with store.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM ade_requests WHERE user_id = ? AND state = 'pending' AND until > ?",
                           (user_id, now)).fetchone()[0]
        rows = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t ON t.id = "
                          "r.token_id WHERE r.user_id = ? AND r.state = 'pending' AND r.until > ? ORDER BY "
                          "r.created_at DESC, r.id LIMIT ?", (user_id, now, int(limit))).fetchall()
    return [_view(r, now) for r in rows], total


def _use_approval(db, user_id, request_id, confirmation, now, proposed):
    """Inside the decision's transaction: use up a confirmation (passkeys.confirm) made for approving this request
    (passkeys.CHANGE's request-approve), when the account has a way to make one; a hardware key's while high security is
    on. One that is missing, used, or made for anything else is refused and left unused, and the request waits on. An
    answer to an app's proposal (`proposed`) goes through the connector's code (oauth.confirm_in), whose other refusals
    keep its words. A missing one is refused in a held command's words on either door, with what a page from an older
    release, which sends none, needs: a reload."""
    from .. import oauth, passkeys
    subject = f"request-approve:{request_id}"
    try:
        if proposed:
            return oauth.confirm_in(db, user_id, confirmation, now, subject)
        return passkeys._use_confirmation(db, user_id, confirmation, now, subject=subject)
    except (passkeys.PasskeyError, oauth.ConnectorError) as e:
        if confirmation or str(e) not in (passkeys.NEEDS_CONFIRM, oauth.NEEDS_CONFIRM, passkeys.NEEDS_HARDWARE):
            raise
        words = NEEDS_HARDWARE_TO_APPROVE if str(e) == passkeys.NEEDS_HARDWARE else NEEDS_CONFIRM_TO_APPROVE
        raise type(e)(e.status, words) from None


def decide(user_id, request_id, approve, words=None, now=None, proposal=None, confirmation=None):
    """The owner's answer to one of their pending requests. Only a pending request that has not expired is answered;
    anything else is refused, so an approval can never be given twice or after its time. Approving uses up, in the same
    transaction, the account's `confirmation` made for this request (_use_approval), whichever door it comes through;
    denying takes the sign-in alone, since it only keeps something from happening. With `proposal`, the owner answers a
    connected app's proposal on this request in the same transaction (ade/proposals.py): as it proposed, or the other
    way, refusing it."""
    now = _now(now)
    words = (words or "").strip()[:2000] or None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                         "ON t.id = r.token_id WHERE r.id = ? AND r.user_id = ?", (request_id, user_id)).fetchone()
        if row is None:
            return None
        if _state(row, now) != "pending":
            raise ApprovalError(f"That request is {_state(row, now)}, not waiting for an answer.")
        if approve:
            # an approval is the person's own word as well as the sign-in, made for this request, whichever door
            # it comes through: the command's own Approve, or an app's proposal answered as it proposed
            _use_approval(db, user_id, request_id, confirmation, now, proposal is not None)
        if proposal is not None:
            from . import proposals
            proposals.bind_decision(db, user_id, request_id, proposal, approve, now)
        db.execute("UPDATE ade_requests SET state = ?, decided_at = ?, words = ? WHERE id = ? AND state = 'pending'",
                   ("approved" if approve else "denied", now, words, request_id))
        row = db.execute("SELECT r.*, t.name AS token_name FROM ade_requests r JOIN ade_hook_tokens t "
                         "ON t.id = r.token_id WHERE r.id = ?", (request_id,)).fetchone()
    return _view(row, now)
