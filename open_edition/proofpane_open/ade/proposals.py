"""Proposals a connected app makes through the Claude app connector (ade/connector.py): an instruction to a linked
session, an answer to its question, an answer to an approval request, a stop or a resume. A proposal does nothing by
itself. It waits on the ADE's page until the owner signs it there with a passkey (an instruction, an answer, a resume:
signed as the command it proposes, the signed payload's id the proposal's), or accepts it there (a stop, with the
sign-in; an approval's answer: approve with the account's confirmation, deny with the sign-in), or refuses it. Its app
may withdraw it; it is void once what it was for has gone; it expires at its time. It is not an event of the session
channel, so no session reads it: only the command the owner signs reaches a session.

States:

    proposed    waiting for the owner
    sent        signed and sent, or accepted and done: an instruction, an answer or a resume the owner signed from
                it; a stop, or an approval's answer, the owner accepted from it
    refused     the owner refused it (or answered the approval request the other way)
    expired     its time ended first
    withdrawn   its app withdrew it
    void        what it was for went first: its session's link revoked, its question closed, its request answered or
                ended, the session stopped or resumed another way, its app disconnected or its connection ended

A proposal past its time, or whose target has gone, is written so when proposals are next read (settle), as a command's
state is read from its columns. One is made only while its app's connection is in effect and may propose, as the grant
stands in the transaction that records it. Limits, counted from the table: WAITING_PER_APP, WAITING_PER_ACCOUNT,
WAITING_PER_LINK and WAITING_PER_COMMAND waiting at once, MADE_PER_HOUR made an hour by one app; each waits 1 to 24
hours, as its app asks, never past its approval request's end.

A proposal's words, its reason and the words on it are kept in the clear, outside what encryption seals: a day after
it is decided they are cleared, and 90 days after its row goes (prune, each round of the notifier); turning encryption
on clears them at once from every one decided (forget_decided).
"""

import datetime
import re
import secrets
import time

from .. import store
from . import link, link_commands

KINDS = ("instruction", "answer", "approval", "stop", "resume")
QUESTION = re.compile(r"question:([0-9]{1,18})")      # a question's handle, fullmatch: ASCII digits, within a seq
SIGNED = ("instruction", "answer", "resume")        # what the owner signs with a passkey, as the command it proposes
WAITING_PER_APP = 10
WAITING_PER_ACCOUNT = 20
WAITING_PER_LINK = 3
WAITING_PER_COMMAND = 1             # the page shows one proposal on a held command's item: one waits at a time
MADE_PER_HOUR = 30
LIFE_MIN, LIFE_MAX, LIFE_DEFAULT = 1, 24, 4     # hours
WHY_MAX = 500
WORDS_MAX = 300
RECENT = 86400                      # how long an app's decided proposals are listed for it
WORDS_KEPT = RECENT                 # how long a decided proposal keeps its words, its reason and the owner's refusal
ROWS_KEPT = 90 * 86400              # how long a decided proposal is kept at all, as the connector's events are
OTHER_WAY = "the owner answered the command the other way"      # a refusal's reason the server writes, not the owner


class ProposalError(Exception):
    """A proposal that cannot be made or changed as asked: its HTTP status, and words the app or the page shows."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _now(now):
    return int(now if now is not None else time.time())


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---- where each stands ---------------------------------------------------------------------------------------------

def void_grant(db, grant_id, now):
    """Inside the caller's transaction: a disconnected app's waiting proposals are void."""
    db.execute("UPDATE ade_proposals SET state = 'void', decided_at = ?, said = ? WHERE grant_id = ? AND "
               "state = 'proposed'", (now, "its app was disconnected", grant_id))


def _grant_state(db, grant_id, now):
    """Where the grant of the app that made a proposal stands, as Connected apps says it (oauth.grant_view): None for
    one that is not there."""
    from .. import oauth
    g = db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (grant_id,)).fetchone()
    return None if g is None else oauth.grant_view(g, now)


def _gone(db, row, now):
    """Why what a waiting proposal is for has gone, or None: its app's connection first, revoked or ended (a revoke
    voids its proposals as it is made, but a proposal made as it was, or a connection whose 90 days end, is found
    here)."""
    app = _grant_state(db, row["grant_id"], now)
    if app is None or app["state"] == "revoked":
        return "its app was disconnected"
    if app["state"] == "ended":
        return "its app's connection ended"
    if row["link_id"] is not None:
        target = db.execute("SELECT revoked_at, stopped_at FROM ade_links WHERE id = ? AND user_id = ?",
                            (row["link_id"], row["user_id"])).fetchone()
        if target is None or target["revoked_at"] is not None:
            return "its session's link was revoked"
        if row["kind"] == "stop" and target["stopped_at"] is not None:
            return "the session was stopped another way"
        if row["kind"] == "resume" and target["stopped_at"] is None:
            return "the session was resumed another way"
    if row["kind"] == "answer":
        from .connector import plain
        q = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?",
                       (row["ref"], row["user_id"])).fetchone()
        if q is None or not link.question_open(db, q, now):
            return "the question was answered or closed"
        if plain(q["text"]) is None:
            # sealed in place as encryption was turned on: its answer's digest is no longer the server's to check,
            # and the proposal's review would show an envelope, so the owner answers it on its session's card
            return "its question was encrypted: the owner answers it on the ADE's page"
    if row["kind"] == "approval":
        r = db.execute("SELECT state, until FROM ade_requests WHERE id = ? AND user_id = ?",
                       (row["request_id"], row["user_id"])).fetchone()
        if r is None or r["state"] != "pending" or r["until"] <= now:
            return "the request was answered, or its time ended"
    return None


def settle(db, user_id, now):
    """Inside the caller's transaction: write what time and gone targets have made of the account's waiting
    proposals."""
    rows = db.execute("SELECT * FROM ade_proposals WHERE user_id = ? AND state = 'proposed'", (user_id,)).fetchall()
    for row in rows:
        if row["expires_at"] <= now:
            db.execute("UPDATE ade_proposals SET state = 'expired', decided_at = ? WHERE id = ?",
                       (row["expires_at"], row["id"]))
            continue
        why = _gone(db, row, now)
        if why:
            db.execute("UPDATE ade_proposals SET state = 'void', decided_at = ?, said = ? WHERE id = ?",
                       (now, why, row["id"]))


def _target(row):
    if row["kind"] == "answer":
        return f"question:{row['ref']}"
    if row["kind"] == "approval":
        return "approval:" + row["request_id"]
    return "link:" + row["link_id"]


def view(db, row, now):
    """A proposal as the page and the app read it: what it is, to which session, its words as the app's and its reason
    as the app's own word, where it stands; an answer's question, a resume's stop and a signed one's command."""
    names = link._names(db, row["user_id"])
    app = db.execute("SELECT client_name FROM ade_connector_grants WHERE id = ?", (row["grant_id"],)).fetchone()
    out = {"id": row["id"], "kind": row["kind"], "state": row["state"], "target": _target(row),
           "session": names.get(row["link_id"]) if row["link_id"] else None, "link_id": row["link_id"],
           "app": app["client_name"] if app else None, "grant_id": row["grant_id"], "text": row["text"],
           "decision": row["decision"], "why": row["why"], "made_at": row["created_at"],
           "expires_at": row["expires_at"], "decided_at": row["decided_at"], "seq": row["seq"],
           "edited": bool(row["edited"]), "said": row["said"], "ref": row["ref"], "request_id": row["request_id"]}
    if row["kind"] == "answer":
        from .connector import plain
        q = db.execute("SELECT text, link_id FROM ade_link_events WHERE seq = ? AND user_id = ?",
                       (row["ref"], row["user_id"])).fetchone()
        # never an envelope: a question sealed is said to be, without its words (the page has no review for it)
        out["question"] = plain(q["text"]) if q else None
        if q and out["question"] is None:
            out["question_encrypted"] = True
    if row["kind"] == "resume" and row["link_id"]:
        held = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (row["link_id"],)).fetchone()
        out["stopped_at"] = held["stopped_at"] if held else None
    if row["seq"] is not None:
        cmd = db.execute("SELECT * FROM ade_link_events WHERE seq = ?", (row["seq"],)).fetchone()
        out["command"] = {"seq": row["seq"], "state": link_commands.command_state(cmd, now)} if cmd else None
    return out


def said(v, host):
    """What a proposal's state means, in words the app reads back: done only for sent. Fixed words, the server's times
    and numbers alone: never a session's name or the owner's words, which the app reads apart (the proposal's session;
    its refused_with)."""
    at = _utc(v["decided_at"]) if v["decided_at"] else None
    if v["state"] == "proposed":
        until = {"stop": "until they accept it, the session runs on.",
                 "approval": "until they answer it there, the command waits."}.get(
            v["kind"], "until they sign it with a passkey, the session has been told nothing.")
        return f"Not done. It waits for the owner on the ADE's page, at {host}/app/#proposals: {until}"
    if v["state"] == "sent":
        if v["kind"] in ("instruction", "answer"):
            changed = ", with words they changed" if v["edited"] else ""
            return (f"Done: the owner signed it at {at}, and it was stored as command [{v['seq']}] to the "
                    f"session{changed}. What the session does with it is its own word: read its conversation.")
        if v["kind"] == "resume":
            return f"Done: the owner signed the resume at {at}; the session's hook lifts its stop when it verifies it."
        if v["kind"] == "stop":
            return (f"Done: the owner accepted it, and the session was stopped at {at}. A stop holds its next tool "
                    "calls, not what it started.")
        answered = "approved the command once" if v["decision"] == "approve" else "denied the command"
        return f"Done: the owner {answered} at {at}."
    if v["state"] == "refused":
        if v["said"] == OTHER_WAY:
            return f"Not done: {OTHER_WAY} at {at}. Nothing was sent."
        words = " The owner's words are in refused_with." if v["said"] else ""
        return f"Not done: the owner refused it at {at}. Nothing was sent.{words}"
    if v["state"] == "expired":
        return f"Not done: its time ended at {_utc(v['expires_at'])} without the owner's answer. Nothing was sent."
    if v["state"] == "withdrawn":
        return f"Not done: this app withdrew it at {at}. Nothing was sent."
    return f"Not done: {v['said'] or 'what it was for has gone'}. Nothing was sent."      # void: the server's reasons


# ---- making one ----------------------------------------------------------------------------------------------------

def _life(hours):
    if hours is None:
        return LIFE_DEFAULT * 3600
    if not isinstance(hours, int) or isinstance(hours, bool) or not LIFE_MIN <= hours <= LIFE_MAX:
        raise ProposalError(422, f"waits_hours is a whole number from {LIFE_MIN} to {LIFE_MAX}.")
    return hours * 3600


def _no_secret(value, what):
    """Refuse (422) words holding something shaped like a secret: the session channel's shapes, and a connector token,
    which no proposal carries to a session (or back to an app as the owner's)."""
    from .connector import CONNECTOR_SHAPES
    try:
        link.check_secrets(value, what)
    except link.LinkError as e:
        raise ProposalError(422, str(e)) from None
    for shape, pattern in CONNECTOR_SHAPES:
        if isinstance(value, str) and pattern.search(value):
            raise ProposalError(422, f"The {what} holds something shaped like {shape}; the ADE keeps no secret.")
    return value


def _why(value):
    """An app's reason: none, or one line of at most WHY_MAX characters, held to what its words are held to (_words)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or len(value) > WHY_MAX or "\n" in value or "\r" in value:
        raise ProposalError(422, f"why is one line of at most {WHY_MAX} characters.")
    return _words(value, "reason")


def _words(value, what):
    """An app's words, held to a command's rules (link_commands.check_text), which the page shows before the owner
    signs them, and to no more than MARKS_MAX non-spacing marks on one character: a stack of them is drawn over the
    lines around it, so the owner could read less than what they sign."""
    try:
        text = _no_secret(link_commands.check_text(value, what), what)
    except link.LinkError as e:
        raise ProposalError(422, str(e)) from None
    code = link_commands.stacked(text)
    if code is not None:
        raise ProposalError(422, f"The {what} holds U+{code:04X} with more than {link_commands.MARKS_MAX} marks on it, "
                                 "which are drawn over the lines around it.")
    return text


def _live_link(db, user_id, handle):
    link_id = handle[5:] if isinstance(handle, str) and handle.startswith("link:") else None
    row = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                     (link_id, user_id)).fetchone() if link_id else None
    if row is None:
        raise ProposalError(404, "session is a linked session's id as list_sessions gives it (link:...): no live "
                                 "linked session of the owner's has that one.")
    return row


def _has_passkey(db, user_id):
    if db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0] == 0:
        raise ProposalError(409, "The owner's account has no passkey, so nothing they would sign can be proposed; "
                                 "they can add one in Account.")


def make(grant, kind, args, now=None):
    """Record a proposal of `kind` from the app whose grant is `grant` (as oauth.authenticate gives it), its target and
    words in `args`, checked as the command it proposes is checked. Returns its view; ProposalError says why not."""
    from .connector import plain
    now = _now(now)
    user_id = grant["user_id"]
    life = _life(args.get("waits_hours"))
    why = _why(args.get("why"))
    fields = {"link_id": None, "ref": None, "request_id": None, "decision": None, "text": None}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # the grant as it stands now, in this transaction: one revoked, ended or cancelled since its token was checked
        # makes nothing, as a revoke voids what it made already (void_grant) in its own
        app = _grant_state(db, grant["id"], now)
        if app is None or app["state"] != "active" or "ade.propose" not in app["scopes"]:
            raise ProposalError(403, "This app's connection is not in effect, or may not propose: nothing was "
                                     "recorded. The owner can connect it again.")
        settle(db, user_id, now)
        expires = now + life
        if kind in ("instruction", "stop", "resume"):
            target = _live_link(db, user_id, args.get("session"))
            fields["link_id"] = target["id"]
            if kind == "instruction":
                _has_passkey(db, user_id)
                fields["text"] = _words(args.get("text"), "instruction")
            elif kind == "stop" and target["stopped_at"] is not None:
                raise ProposalError(409, "That session is stopped already.")
            elif kind == "resume":
                _has_passkey(db, user_id)
                if target["stopped_at"] is None:
                    raise ProposalError(409, "That session is not stopped.")
        elif kind == "answer":
            _has_passkey(db, user_id)
            handle = args.get("question")
            m = QUESTION.fullmatch(handle) if isinstance(handle, str) else None
            seq = int(m.group(1)) if m else None
            q = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ?", (seq, user_id)).fetchone() \
                if seq else None
            asker = q and db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                                     (q["link_id"], user_id)).fetchone()
            if not q or q["kind"] != "question" or not asker or q["to_name"] not in (link.OWNER, "all") \
                    or not link.question_open(db, q, now):
                raise ProposalError(404, "question is an open question a linked session asked the owner, as "
                                         "list_pending gives it (question:...).")
            if plain(q["text"]) is None:
                raise ProposalError(409, "That question is kept end-to-end encrypted, so it cannot be read or answered "
                                         "here: the owner answers it on the ADE's page.")
            fields.update(link_id=asker["id"], ref=seq, text=_words(args.get("text"), "answer"))
        elif kind == "approval":
            handle = args.get("approval")
            request_id = handle[9:] if isinstance(handle, str) and handle.startswith("approval:") else None
            r = db.execute("SELECT * FROM ade_requests WHERE id = ? AND user_id = ?",
                           (request_id, user_id)).fetchone() if request_id else None
            if r is None or r["state"] != "pending" or r["until"] <= now:
                raise ProposalError(404, "approval is a command waiting for the owner's answer, as list_pending gives "
                                         "it (approval:...).")
            if args.get("decision") not in ("approve", "deny"):
                raise ProposalError(422, "decision is approve or deny.")
            fields.update(request_id=r["id"], decision=args["decision"])
            expires = min(expires, r["until"])
        else:
            raise ProposalError(422, "No such kind of proposal.")
        waiting = "state = 'proposed' AND expires_at > ?"
        if db.execute(f"SELECT COUNT(*) FROM ade_proposals WHERE grant_id = ? AND {waiting}",
                      (grant["id"], now)).fetchone()[0] >= WAITING_PER_APP \
                or db.execute(f"SELECT COUNT(*) FROM ade_proposals WHERE user_id = ? AND {waiting}",
                              (user_id, now)).fetchone()[0] >= WAITING_PER_ACCOUNT:
            raise ProposalError(429, f"As many proposals wait for the owner as may ({WAITING_PER_APP} from one app, "
                                     f"{WAITING_PER_ACCOUNT} in all): the owner answers some first, or the app "
                                     "withdraws some.")
        if fields["link_id"] and db.execute(f"SELECT COUNT(*) FROM ade_proposals WHERE link_id = ? AND {waiting}",
                                            (fields["link_id"], now)).fetchone()[0] >= WAITING_PER_LINK:
            raise ProposalError(429, f"{WAITING_PER_LINK} proposals for this session wait already.")
        if fields["request_id"] and db.execute(f"SELECT COUNT(*) FROM ade_proposals WHERE request_id = ? AND "
                                               f"{waiting}", (fields["request_id"], now)).fetchone()[0] \
                >= WAITING_PER_COMMAND:
            raise ProposalError(429, "A proposal for this command waits already: the owner answers it on the command, "
                                     "or its app withdraws it.")
        if db.execute("SELECT COUNT(*) FROM ade_proposals WHERE grant_id = ? AND created_at > ?",
                      (grant["id"], now - 3600)).fetchone()[0] >= MADE_PER_HOUR:
            raise ProposalError(429, f"An app makes at most {MADE_PER_HOUR} proposals an hour.")
        proposal_id = secrets.token_hex(16)
        db.execute("INSERT INTO ade_proposals (id, user_id, grant_id, kind, link_id, ref, request_id, decision, text, "
                   "why, state, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'proposed', ?, ?)",
                   (proposal_id, user_id, grant["id"], kind, fields["link_id"], fields["ref"], fields["request_id"],
                    fields["decision"], fields["text"], why, now, expires))
        return view(db, db.execute("SELECT * FROM ade_proposals WHERE id = ?", (proposal_id,)).fetchone(), now)


# ---- the owner's word on a proposal --------------------------------------------------------------------------------

def bind_signed(db, user_id, payload, purpose, now):
    """Inside the transaction that stores a signed command or resume (link_commands): the proposal whose id is the
    signed payload's, which the owner is signing now; None for a payload no proposal of the account has. A proposal that
    does not wait, or that proposes another command (another purpose, session or question), refuses the payload, so a
    review left open cannot sign a proposal refused, withdrawn, void or expired."""
    row = db.execute("SELECT * FROM ade_proposals WHERE id = ? AND user_id = ?",
                     (payload.get("id"), user_id)).fetchone()
    if row is None:
        return None
    settle(db, user_id, now)
    row = db.execute("SELECT * FROM ade_proposals WHERE id = ?", (row["id"],)).fetchone()
    if row["state"] != "proposed":
        raise link.LinkError(409, f"That proposal is {row['state']}: sign a new command instead.", state=row["state"])
    if row["kind"] != purpose or row["link_id"] != payload.get("to") \
            or (purpose == "answer" and row["ref"] != payload.get("ref")):
        raise link.LinkError(409, "The payload's id is a proposal of another command.")
    return row


def signed(db, row, seq, text, now):
    """Inside the transaction that stored what a proposal proposed: the proposal is sent, with the command's seq and
    whether the owner changed its words before signing."""
    edited = text is not None and row["text"] is not None and text != row["text"]
    db.execute("UPDATE ade_proposals SET state = 'sent', decided_at = ?, seq = ?, edited = ? WHERE id = ?",
               (now, seq, int(edited), row["id"]))


def accept_stop(user_id, proposal_id, now=None):
    """The owner accepts a stop a connected app proposed, with the sign-in alone, as the page's own Stop takes it (a
    stop can only keep something from happening): the session is stopped and the proposal sent, in one transaction."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settle(db, user_id, now)
        row = _one(db, user_id, proposal_id)
        if row["kind"] != "stop":
            raise ProposalError(409, "Only a stop is accepted so: an instruction, an answer or a resume is signed "
                                     "with a passkey, and a held command's answer is given on the command.")
        if row["state"] != "proposed":
            raise ProposalError(409, f"That proposal is {row['state']}, not waiting.")
        link_commands.stop_in(db, user_id, row["link_id"], now)
        db.execute("UPDATE ade_proposals SET state = 'sent', decided_at = ? WHERE id = ?", (now, row["id"]))
        return view(db, _one(db, user_id, proposal_id), now)


def bind_decision(db, user_id, request_id, proposal_id, approve, now):
    """Inside the transaction that answers a held command (approvals.decide, which has used up the account's
    confirmation made for this request when the answer approves, and takes the sign-in alone for a denial, as the
    command's own Deny does: a denial can only keep something from happening), on which a proposal waits: answered as
    the proposal says, the proposal is sent; answered the other way, the proposal is refused."""
    settle(db, user_id, now)
    row = _one(db, user_id, proposal_id)
    if row["kind"] != "approval" or row["request_id"] != request_id:
        raise ProposalError(409, "That proposal is not one for this command.")
    if row["state"] != "proposed":
        raise ProposalError(409, f"That proposal is {row['state']}, not waiting.")
    if (row["decision"] == "approve") == bool(approve):
        db.execute("UPDATE ade_proposals SET state = 'sent', decided_at = ? WHERE id = ?", (now, row["id"]))
    else:
        db.execute("UPDATE ade_proposals SET state = 'refused', decided_at = ?, said = ? WHERE id = ?",
                   (now, OTHER_WAY, row["id"]))


# ---- reading, withdrawing, refusing --------------------------------------------------------------------------------

def _one(db, user_id, proposal_id, grant_id=None):
    if not isinstance(proposal_id, str) or len(proposal_id) != 32:
        raise ProposalError(404, "No such proposal.")
    row = db.execute("SELECT * FROM ade_proposals WHERE id = ? AND user_id = ?" + (" AND grant_id = ?" if grant_id
                     else ""), (proposal_id, user_id) + ((grant_id,) if grant_id else ())).fetchone()
    if row is None:
        raise ProposalError(404, "No such proposal.")
    return row


def read(user_id, proposal_id, now=None, grant_id=None):
    """One of the account's proposals, where it stands now; with `grant_id`, only one that app made."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settle(db, user_id, now)
        return view(db, _one(db, user_id, proposal_id, grant_id), now)


def withdraw(grant, proposal_id, now=None):
    """The app withdraws one of its own proposals that still waits."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settle(db, grant["user_id"], now)
        row = _one(db, grant["user_id"], proposal_id, grant["id"])
        if row["state"] != "proposed":
            raise ProposalError(409, f"Only a proposal that waits can be withdrawn; this one is {row['state']}.")
        db.execute("UPDATE ade_proposals SET state = 'withdrawn', decided_at = ? WHERE id = ?", (now, row["id"]))
        return view(db, _one(db, grant["user_id"], proposal_id), now)


def _owner_words(words):
    if words is None or words == "":
        return None
    if not isinstance(words, str) or len(words) > WORDS_MAX or "\n" in words or "\r" in words or "\x00" in words:
        raise ProposalError(422, f"A refusal's words are one line of at most {WORDS_MAX} characters.")
    return _no_secret(words.strip(), "refusal") or None


def refuse(user_id, proposal_id, words=None, now=None):
    """The owner refuses a waiting proposal, with words its app can read back if they want."""
    now = _now(now)
    words = _owner_words(words)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settle(db, user_id, now)
        row = _one(db, user_id, proposal_id)
        if row["state"] != "proposed":
            raise ProposalError(409, f"That proposal is {row['state']}, not waiting.")
        db.execute("UPDATE ade_proposals SET state = 'refused', decided_at = ?, said = ? WHERE id = ?",
                   (now, words, row["id"]))
        return view(db, _one(db, user_id, proposal_id), now)


def refuse_all(user_id, grant_id, words=None, now=None):
    """The owner refuses every waiting proposal of one app: {refused: how many}."""
    now = _now(now)
    words = _owner_words(words)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        settle(db, user_id, now)
        done = db.execute("UPDATE ade_proposals SET state = 'refused', decided_at = ?, said = ? WHERE user_id = ? AND "
                          "grant_id = ? AND state = 'proposed'", (now, words, user_id, grant_id)).rowcount
    return {"refused": done}


def waiting(user_id, now=None):
    """The account's waiting proposals, oldest first, as the page lists them (every few seconds: with none waiting,
    read without taking the database's write lock)."""
    now = _now(now)
    with store.connect() as db:
        if db.execute("SELECT 1 FROM ade_proposals WHERE user_id = ? AND state = 'proposed' LIMIT 1",
                      (user_id,)).fetchone() is None:
            return []
        db.execute("BEGIN IMMEDIATE")
        settle(db, user_id, now)
        rows = db.execute("SELECT * FROM ade_proposals WHERE user_id = ? AND state = 'proposed' ORDER BY created_at, "
                          "id", (user_id,)).fetchall()
        return [view(db, r, now) for r in rows]


def decided(grant, now=None):
    """One app's proposals decided in the last day, newest first, at most 20."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_proposals WHERE grant_id = ? AND state <> 'proposed' AND decided_at > ? "
                          "ORDER BY decided_at DESC, id LIMIT 20", (grant["id"], now - RECENT)).fetchall()
        return [view(db, r, now) for r in rows]


# ---- what is kept, and for how long --------------------------------------------------------------------------------

_HAS_WORDS = "(text IS NOT NULL OR why IS NOT NULL OR said IS NOT NULL)"


def prune(now=None):
    """Every account's proposals, as the notifier's round leaves them (push.tick): one past its time is written
    expired, whether or not anything read it; one decided more than WORDS_KEPT ago (its app read it back for that day)
    loses its words, its reason and the owner's or the server's words on it (text, why, said), which are in the clear;
    one decided more than ROWS_KEPT ago is deleted. {expired, cleared, deleted}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        expired = db.execute("UPDATE ade_proposals SET state = 'expired', decided_at = expires_at WHERE state = "
                             "'proposed' AND expires_at <= ?", (now,)).rowcount
        cleared = db.execute(f"UPDATE ade_proposals SET text = NULL, why = NULL, said = NULL WHERE state <> 'proposed' "
                             f"AND decided_at <= ? AND {_HAS_WORDS}", (now - WORDS_KEPT,)).rowcount
        deleted = db.execute("DELETE FROM ade_proposals WHERE state <> 'proposed' AND decided_at <= ?",
                             (now - ROWS_KEPT,)).rowcount
    return {"expired": expired, "cleared": cleared, "deleted": deleted}


def forget_decided(db, user_id, now):
    """Inside the transaction in which the account's encryption is turned on (ade/e2e_keys.py, as its hold ends, after
    sealing in place): every proposal of the account that no longer waits loses its words, its reason and the words on
    it (text, why, said) at once, as a day after it would; settled first, so one that what was sealed in place has made
    void (an answer to a question sealed so) loses them too. The words of one that still waits are the owner's to read
    before signing it. The number cleared."""
    settle(db, user_id, now)
    return db.execute(f"UPDATE ade_proposals SET text = NULL, why = NULL, said = NULL WHERE user_id = ? AND state <> "
                      f"'proposed' AND {_HAS_WORDS}", (user_id,)).rowcount


def alerts(user_id, now=None):
    """The notifier's items, each (key, the kind of its notice: push.words): each waiting proposal, once, in fixed words:
    never its words, its app's name or the session it is for (a lock screen shows a notice), which the page shows."""
    return [(f"proposal:{v['id']}", "proposal") for v in waiting(user_id, now)]

