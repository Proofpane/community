"""A session linked through the Claude app connector (queue 4.325, CONNECTOR-LINK): a coding session already running,
on an account whose Claude has the ADE as a connector, names itself in a call and shows on the owner's page, in Linked
sessions, with what it says it is doing and what it asks. It needs no setting of its environment and nothing pasted,
since the connector's calls travel through Claude's own servers; and it is not a link of the session channel
(ade/link.py), of which it has none of the parts:

- No key, no ticket, no hold of its own and no pinned passkey. The app's connection (oauth.py: the grant, with its
  own hold, once) answers for every session linked through it, and a session's name is its own word, given in each
  call: nothing here tells one session of the Claude account from another, or from a chat. So the page shows the name
  as the session's own words, with the app that carried it; and one name is one session, whatever its case and spaces
  (link.name_key): among the apps, held by the one that linked it first, and a session linked with its own key has
  its name whenever it takes it, the one linked through an app under that name ending (BY_KEY).
- One way, and nothing comes back. Its words go to the owner's page: steps, notes, waitings, questions and dones,
  each held to the rules a connected app's words are held to (proposals._words: a command's rules, nothing shaped
  like a secret, at most 2 marks on one character), and a checkpoint, one JSON document rewritten against the version
  last seen. Since nothing tells its calls from another chat's, nothing a call stored is given to a later call: no
  post's words, no checkpoint's document, not the name as first spelled; an answer holds fixed words, numbers and
  times (a checkpoint's version, a question's id and whether it is open, how many posts are kept). Of the owner's,
  fixed words alone: that a question was closed on the page, and that the owner asks it to stop. The stop is the one
  thing the link asks of a session, and its answers say so: asked with the sign-in alone, not signed, in fixed
  words, and it only stops (the block's own stop is the server's word too; link_commands.py: a forged stop can only
  halt). The link carries none of the owner's own words: the owner instructs such a session in its own chat. What
  the same app's other tools read (a conversation, a proposal refused with words) is the connector's, not the link's.
- It takes nothing. No command can be addressed to it: the channel's signed instructions, answers, moves, resumes and
  signing windows name a link of ade_links, which it is not, and no route or tool here stores the owner's words for
  it. A session that must take the owner's instructions is linked with a ticket and its own key, and checks each
  signature itself (ade_link.py).
- Off until allowed. Linking is off for every connected app until the owner turns it on for that app, with the
  account's confirmation made for that change (set_links; passkeys.CHANGE's connector-links), and only for an app
  that may propose: one allowed only to read writes nothing. Turning it off, with the sign-in alone, ends every session
  linked through the app and deletes their posts and checkpoints; so does revoking the app (oauth.revoke_in), and
  its connection's end (prune). The passkey whose confirmation turned it on is kept (links_by; none for the
  password's), and removing that passkey turns it off again (passkeys_going), as it revokes a connection it allowed.
- Not while end-to-end encryption is on. Such a session cannot seal what it sends, so while the account's encryption
  is on none links or sends; turning it on ends every one of the account's and deletes their posts and checkpoints,
  in the transaction in which it takes effect (encryption_on, from e2e_keys._forget_decided). The name each gave
  stays in its ended row, as any ended session's does.
- Bounded, counted from the tables: LINKS_PER_APP sessions linked at once through one app, POSTS_PER_HOUR posts an
  hour and OPEN_QUESTIONS questions open per session, POSTS_KEPT posts kept per session (the oldest going first,
  never an open question), a checkpoint of at most link.MAX_CHECKPOINT bytes and NODES values, nested at most DEPTH
  deep. The hour's posts are counted from the posts kept, so the two limits are held together (below). A session not
  heard for IDLE_ENDS seconds ends; one that ended keeps only its name, its times and why, for ENDED_KEPT seconds: its
  posts and its checkpoint go as it ends. Each call is also one of the app's connector events, by its tool's name alone
  (ade/connector.py), and counts in the app's calls a minute and an hour.
"""

import json
import re
import sqlite3
import time
import unicodedata
import uuid

from .. import oauth, store
from . import e2e_keys, link, link_commands, proposals

KINDS = ("step", "note", "question", "waiting", "done")
LINKS_PER_APP = 20
POSTS_PER_HOUR = 300
OPEN_QUESTIONS = 20
POSTS_KEPT = 500                # a session's posts kept: with LINKS_PER_APP, what one app can make the ADE hold
QUESTIONS_READ = 40             # the questions a session is told of, by id alone: its open ones and those lately closed
CLOSED_SHOWN = 86400            # how long a question the owner closed is still told to its session
SAME_AGAIN = 300                # a post sent again within this is the one stored (a call's answer lost on the way)
HEARD_EVERY = 60                # how often a session's last call is written
IDLE_ENDS = 30 * 86400
ENDED_KEPT = 86400
POSTS_PAGE = 50
DEPTH = 6                       # a checkpoint's objects and lists inside one another
NODES = 400                     # and its values in all
# An hour's posts are counted from the posts kept, and what is kept is trimmed (_keep) of the oldest that is no open
# question. So the posts kept must always hold the hour's: they do while the hour's limit and the questions that can
# be open are together no more than what is kept, since the oldest past it is then never of the hour, nor open.
if POSTS_PER_HOUR + OPEN_QUESTIONS > POSTS_KEPT:
    raise RuntimeError("connector_link: POSTS_PER_HOUR and OPEN_QUESTIONS together must not pass POSTS_KEPT, or the "
                       "hour's limit is counted from posts already trimmed and never refuses")
_ASK = re.compile(r"ask:([1-9][0-9]{0,17})")            # fullmatch, never match
_ID = re.compile(r"[0-9a-f]{32}")

# every word a session reads of its link: fixed, each whole. Nothing a call stored is among them.
RULE = ("This link carries your session's words to the owner's page, and none of the owner's words to you. Nothing "
        "comes back through it but fixed words, numbers and times: no answer, nothing stored through it (the ADE "
        "cannot tell this session from another chat of the same Claude account), and no instruction but one, in "
        "fixed words: that the owner asks this session to stop. That is asked with their sign-in alone and is not "
        "signed, and it only stops. The owner instructs you only in this chat.")
LINKED_NEW = ("Linked: the owner's page now shows this session in Linked sessions, under the name you gave, as the "
              "session's own words. Post what you do with link_post and keep a checkpoint for the page with "
              "link_checkpoint. Neither is given back to you: keep what you need in your own notes.")
LINKED = ("A session is linked through this app under this name already, and this call is taken as its own: its "
          "checkpoint's version, its questions' ids and the number of its posts follow, none of their words. If this "
          "session did not link under this name, another session or chat of this Claude account did: link this one "
          "under another name.")
POSTED = "Posted to the owner's page."
KEPT = "The checkpoint is kept."
CHANGED = ("The checkpoint was written since the version given, by this session or by another call under its name: "
           "the version held is in version. Write the whole checkpoint again against that version.")
CLOSED_SAID = ("The owner closed this question on the ADE's page. That carries no answer: if you still need one, ask "
               "them in this chat.")
STOP_SAID = ("The owner asked on the ADE's page, at the time in asked_at, that this session stop: the one thing this "
             "link asks of a session, in these fixed words. It was asked with their sign-in alone and is not signed, "
             "and it only stops: "
             "stop what you are doing and say so in this chat. Go on only when they tell you to in this chat; if they "
             "have told you so there since that time, this request is spent.")
LINKS_OFF = ("The owner has not let sessions link through this app. They turn it on in the ADE: Account, Connected "
             "apps, this app, Let sessions link through it. Then call link_session again.")
NOT_IN_EFFECT = ("This app's connection is not in effect, or may not propose: nothing was linked or posted. The owner "
                 "can connect it again.")
E2E_ON = ("End-to-end encryption is on for this account: a session linked through the connector cannot seal what it "
          "sends, so it links and posts nothing. The owner links it with a ticket instead, in Linked sessions.")
NOT_LINKED = "No session is linked through this app under that name: link_session links it first."
ENDED = ("This session's link ended ({why}). link_session may link it again, if the owner asks you to: its answer "
         "says so, or why not.")
NAME_IS_A_LINK = ("A session linked with its own key holds that name on this account: choose another name for this "
                  "one.")
NAME_TAKEN = "A session linked through another app of this account holds that name: choose another name for this one."
READS_ONLY = ("This app may only read: an app that may propose can be let link sessions. Connect it again with "
              "Propose, then turn linking on.")
NO_APP = "No such app."
NO_LINK = "No such session."
NO_QUESTION = "No open question of that session has that number."
# why a session's link ended, as its row keeps it; its next post or checkpoint is told, where its app may still link
# (one whose app was disconnected, or whose connection ended, is told that instead: NOT_IN_EFFECT)
BY_OWNER = "the owner removed it from Linked sessions"
BY_SWITCH = "linking was turned off for its app"
BY_REVOKE = "its app was disconnected"
BY_APP_END = "its app's connection ended"
BY_E2E = "end-to-end encryption was turned on"
BY_SILENCE = "not heard for 30 days"
BY_KEY = "a session linked with its own key took its name"
BY_PASSKEY = "the passkey that turned linking on for its app was removed"


class Refused(Exception):
    """A refusal a session reads as a tool's error: its words, and what else the answer carries (`extra`: numbers)."""

    def __init__(self, said, **extra):
        super().__init__(said)
        self.extra = extra


class _Yielded(Exception):
    """Raised inside a call's transaction once the call has ended the app's session of a name a keyed link holds:
    what was written stays (_run), and the call is refused in the words given."""


def _now(now):
    return int(now if now is not None else time.time())


def _run(inside):
    """One call's transaction: what `inside(db)` returns, committed. A Refused it raises leaves nothing written; a
    _Yielded leaves what was written, the ending that the call found due, and is then a Refused in its words."""
    after = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            got = inside(db)
        except _Yielded as e:
            after = e
    if after is not None:
        raise Refused(str(after))
    return got


# ---- who may link: the owner's switch, by app -----------------------------------------------------------------------

def set_links(user_id, grant_id, on, confirmation=None, now=None):
    """The owner lets sessions link through one connected app, or no longer. On takes the account's confirmation made
    for that change (connector-links:ID), as letting apps connect does, and only an app that may propose is let (409);
    the passkey that made the confirmation is kept (links_by; none for the password's), so removing it turns linking
    off again (passkeys_going). Off takes the sign-in alone, ends every session linked through the app and deletes
    their posts and checkpoints (a forged off can only unlink and delete). Each is one of the app's connector events.
    404 for an app not the account's, or one that ended. The app as Connected apps lists it (oauth.grant_view)."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        grant = db.execute("SELECT * FROM ade_connector_grants WHERE id = ? AND user_id = ? AND revoked_at IS NULL AND "
                           "ends_at > ?", (grant_id, user_id, now)).fetchone() if _is_id(grant_id) else None
        if grant is None:
            raise oauth.ConnectorError(404, NO_APP)
        if on and not grant["links_at"]:
            if "ade.propose" not in grant["scopes"].split():
                raise oauth.ConnectorError(409, READS_ONLY)
            used = oauth.confirm_in(db, user_id, confirmation, now, f"connector-links:{grant_id}")
            db.execute("UPDATE ade_connector_grants SET links_at = ?, links_by = ? WHERE id = ?",
                       (now, used.passkey_id if used else None, grant_id))
            oauth.record(db, user_id, grant_id, "links-on", now=now)
        elif not on and grant["links_at"]:
            _links_off(db, user_id, grant_id, BY_SWITCH, now)
        return oauth.grant_view(db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (grant_id,)).fetchone(),
                                now)


def _links_off(db, user_id, grant_id, why, now, said=None):
    """Inside the caller's transaction: linking off for one app, the passkey that turned it on forgotten, the sessions
    linked through it ended for `why`, and the app's event (`said`: what the event names beside it)."""
    db.execute("UPDATE ade_connector_grants SET links_at = NULL, links_by = NULL WHERE id = ?", (grant_id,))
    end_for_grant(db, grant_id, why, now)
    oauth.record(db, user_id, grant_id, "links-off", subject=said, now=now)


def passkeys_going(db, user_id, passkey_ids, now):
    """Inside the caller's transaction, as passkeys of the account go (oauth.passkeys_going, after the connections
    they allowed are revoked): linking that one of them turned on is turned off, for each app of the account still
    connected, and the sessions linked through it end (BY_PASSKEY). A lost device's passkey, removed, leaves no
    linking it turned on. Linking the password turned on stays, as a connection the password allowed does. A
    connection revoked, or past its end, is no app still connected: its switch is left as it stood, which turns
    nothing on (a connection's end is never moved), and it is not among the ids. The ids of the apps it was turned
    off for."""
    ids = [p for p in passkey_ids if p]
    if not ids:
        return []
    rows = db.execute(f"SELECT id FROM ade_connector_grants WHERE user_id = ? AND revoked_at IS NULL AND ends_at > ? "
                      f"AND links_at IS NOT NULL AND links_by IN ({','.join('?' * len(ids))}) ORDER BY allowed_at, "
                      f"id", (user_id, now, *ids)).fetchall()
    for r in rows:
        _links_off(db, user_id, r["id"], BY_PASSKEY, now, said=BY_PASSKEY)
    return [r["id"] for r in rows]


def linking_of(db, user_id, now):
    """For the account's list of passkeys, what removing each one ends, said by the page before the removal: two
    maps from a passkey's id to the names of apps still connected whose linking is on, each name the app's own word.
    The first: the apps it turned linking on for and did not allow, which stay connected with their linking turned
    off (passkeys_going). The second: the apps it allowed, whoever turned their linking on, which its removal
    disconnects (oauth.passkeys_going revokes them). Either way the sessions linked through the app end and their
    posts and checkpoints are deleted. An app a passkey both allowed and turned linking on for is in the second
    alone."""
    off, gone = {}, {}
    for r in db.execute("SELECT links_by, confirmed_by, client_name FROM ade_connector_grants WHERE user_id = ? AND "
                        "revoked_at IS NULL AND ends_at > ? AND links_at IS NOT NULL ORDER BY allowed_at, id",
                        (user_id, now)):
        if r["confirmed_by"]:
            gone.setdefault(r["confirmed_by"], []).append(r["client_name"])
        if r["links_by"] and r["links_by"] != r["confirmed_by"]:
            off.setdefault(r["links_by"], []).append(r["client_name"])
    return off, gone


def linked_among(db, grant_ids):
    """How many of these apps have linking on, a revoke leaving that switch as it stood: of how many of the apps a
    passkey's removal revoked the removal says that their sessions ended."""
    ids = [g for g in grant_ids if g]
    if not ids:
        return 0
    return db.execute(f"SELECT COUNT(*) FROM ade_connector_grants WHERE links_at IS NOT NULL AND id IN "
                      f"({','.join('?' * len(ids))})", ids).fetchone()[0]


def _is_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _app(db, grant, now):
    """Inside a call's transaction: the app's grant as it stands now, which must be in effect, may propose, and be let
    link sessions, on an account whose encryption is off; Refused otherwise. One revoked, ended or turned off since
    its token was checked links and posts nothing."""
    row = db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (grant["id"],)).fetchone()
    if row is None or oauth.grant_view(row, now)["state"] != "active" or "ade.propose" not in row["scopes"].split():
        raise Refused(NOT_IN_EFFECT)
    if not row["links_at"]:
        raise Refused(LINKS_OFF)
    if e2e_keys.on(db, row["user_id"]):
        raise Refused(E2E_ON)
    return row


# ---- a session's side: the three calls -------------------------------------------------------------------------------

def check_name(name):
    """The name a call gives for its session, held to a link's name's rules (link.check_new_link): letters, digits,
    spaces, dots, dashes and underscores, at most 60, no two spaces together and none at its end, not owner or all, and
    nothing shaped like a secret. Refused naming the rule, never the name."""
    try:
        link.check_new_link(name, "cloud")
    except link.LinkError as e:
        raise Refused("session is the name this session gives itself. " + str(e)) from None
    return name


def _live(db, grant, name):
    """The app's live session of that name, or None."""
    return db.execute("SELECT * FROM ade_connector_links WHERE user_id = ? AND grant_id = ? AND name_key = ? AND "
                      "ended_at IS NULL", (grant["user_id"], grant["id"], link.name_key(name))).fetchone()


def _yield_to_key(db, row, now):
    """Inside a call's transaction, for the app's live session `row` (or None): where a session linked with its own
    key now holds its name (link.holder), it ends (BY_KEY), its posts and its checkpoint going, and the call is
    refused once that is committed (_Yielded, _run). A keyed link's name is its own whenever it takes it."""
    if row is not None and link.holder(db, row["user_id"], row["name"]) is not None:
        _end(db, "id = ?", (row["id"],), BY_KEY, now)
        raise _Yielded(NAME_IS_A_LINK)


def _mine(db, grant, name, now):
    """The app's live session of that name, heard now; Refused where there is none: with whose the name is, where a
    keyed link holds it, or else with why it ended, where one of that name ended lately."""
    row = _live(db, grant, name)
    _yield_to_key(db, row, now)
    if row is None:
        if link.holder(db, grant["user_id"], name) is not None:
            raise Refused(NAME_IS_A_LINK)       # not ENDED, which says link_session links it again: it would not
        gone = db.execute("SELECT ended_why FROM ade_connector_links WHERE user_id = ? AND grant_id = ? AND name_key "
                          "= ? AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1",
                          (grant["user_id"], grant["id"], link.name_key(name))).fetchone()
        raise Refused(ENDED.format(why=gone["ended_why"]) if gone else NOT_LINKED)
    return _heard(db, row, now)


def _heard(db, row, now):
    if row["heard_at"] is None or now - row["heard_at"] >= HEARD_EVERY:
        db.execute("UPDATE ade_connector_links SET heard_at = ? WHERE id = ?", (now, row["id"]))
    return row


def session(grant, name, now=None):
    """link_session: the app's session of that name, linked now where it was not, and what its call is told of it,
    none of it words a call stored: {new, row, questions: each {seq, at, closed_at} (the newest QUESTIONS_READ of
    its open ones and those the owner closed within CLOSED_SHOWN, taken together), posts: how many are kept}. Refused
    for a name a keyed link of the account holds (ending the app's own session of that name, where it has one), one
    a session linked through another app of the account holds, or past LINKS_PER_APP."""
    now = _now(now)
    check_name(name)

    def inside(db):
        app = _app(db, grant, now)
        row, new = _live(db, grant, name), False
        _yield_to_key(db, row, now)
        if row is None:
            if db.execute("SELECT 1 FROM ade_connector_links WHERE user_id = ? AND name_key = ? AND ended_at IS NULL",
                          (app["user_id"], link.name_key(name))).fetchone():
                raise Refused(NAME_TAKEN)
            if link.holder(db, app["user_id"], name) is not None:
                raise Refused(NAME_IS_A_LINK)
            if db.execute("SELECT COUNT(*) FROM ade_connector_links WHERE grant_id = ? AND ended_at IS NULL",
                          (app["id"],)).fetchone()[0] >= LINKS_PER_APP:
                raise Refused(f"An app links at most {LINKS_PER_APP} sessions at once: the owner removes one in Linked "
                              "sessions.")
            link_id = uuid.uuid4().hex
            db.execute("INSERT INTO ade_connector_links (id, user_id, grant_id, name, name_key, created_at, heard_at) "
                       "VALUES (?,?,?,?,?,?,?)", (link_id, app["user_id"], app["id"], name, link.name_key(name), now,
                                                  now))
            row, new = db.execute("SELECT * FROM ade_connector_links WHERE id = ?", (link_id,)).fetchone(), True
        else:
            _heard(db, row, now)
        questions = db.execute(
            "SELECT seq, at, closed_at FROM (SELECT * FROM ade_connector_link_events WHERE link_id = ? AND kind = "
            "'question' AND (closed_at IS NULL OR (closed_by = 'owner' AND closed_at > ?)) ORDER BY seq DESC LIMIT ?) "
            "ORDER BY seq", (row["id"], now - CLOSED_SHOWN, QUESTIONS_READ)).fetchall()
        count = db.execute("SELECT COUNT(*) FROM ade_connector_link_events WHERE link_id = ?",
                           (row["id"],)).fetchone()[0]
        return {"new": new, "row": {k: row[k] for k in ("created_at", "checkpoint_version", "checkpoint_at",
                                                        "stop_asked_at")},
                "questions": [dict(q) for q in questions], "posts": count}
    return _run(inside)


def _words(value, what):
    """A session's words as a connected app's are held (proposals._words): a command's rules, nothing shaped like a
    secret or a connector token, at most 2 marks on one character."""
    try:
        return proposals._words(value, what)
    except proposals.ProposalError as e:
        raise Refused(str(e)) from None


def post(grant, name, kind, text, ref=None, now=None):
    """link_post: one post of the app's session of that name, to the owner's page: {seq, kind, again, stop_asked_at}.
    A done or a note may close one of the session's own open questions (`ref`, its id as its post gave it). The same
    post sent again within SAME_AGAIN seconds, the session's newest, is the one stored (`again`)."""
    now = _now(now)
    check_name(name)
    if not isinstance(kind, str) or kind not in KINDS:
        raise Refused("kind is one of " + ", ".join(KINDS) + ".")
    text = _words(text, "text")
    closes = None
    if ref is not None:
        if kind not in ("done", "note"):
            raise Refused("Only a done or a note refers to a question, by its id (ask:...).")
        m = _ASK.fullmatch(ref) if isinstance(ref, str) else None
        if m is None:
            raise Refused("ref is one of this session's own questions' ids, as link_post gave it (ask:...).")
        closes = int(m.group(1))

    def inside(db):
        _app(db, grant, now)
        row = _mine(db, grant, name, now)
        last = db.execute("SELECT * FROM ade_connector_link_events WHERE link_id = ? ORDER BY seq DESC LIMIT 1",
                          (row["id"],)).fetchone()
        if last is not None and (last["kind"], last["text"], last["ref"]) == (kind, text, closes) \
                and now - last["at"] < SAME_AGAIN:
            return {"seq": last["seq"], "kind": kind, "again": True, "stop_asked_at": row["stop_asked_at"]}
        if closes is not None and db.execute(
                "SELECT 1 FROM ade_connector_link_events WHERE seq = ? AND link_id = ? AND kind = 'question' AND "
                "closed_at IS NULL", (closes, row["id"])).fetchone() is None:
            raise Refused("ref is not its own open question: one is closed once, by this session or by the owner.")
        if db.execute("SELECT COUNT(*) FROM ade_connector_link_events WHERE link_id = ? AND at > ?",
                      (row["id"], now - 3600)).fetchone()[0] >= POSTS_PER_HOUR:
            raise Refused(f"A session posts at most {POSTS_PER_HOUR} posts an hour.")
        if kind == "question" and db.execute(
                "SELECT COUNT(*) FROM ade_connector_link_events WHERE link_id = ? AND kind = 'question' AND closed_at "
                "IS NULL", (row["id"],)).fetchone()[0] >= OPEN_QUESTIONS:
            raise Refused(f"A session has at most {OPEN_QUESTIONS} questions open: one is closed by its own done or "
                          "note that refers to it, or by the owner on the page.")
        seq = db.execute("INSERT INTO ade_connector_link_events (user_id, link_id, kind, text, ref, at) VALUES "
                         "(?,?,?,?,?,?)", (row["user_id"], row["id"], kind, text, closes, now)).lastrowid
        if closes is not None:
            db.execute("UPDATE ade_connector_link_events SET closed_at = ?, closed_by = ? WHERE seq = ?",
                       (now, f"post:{seq}", closes))
        _keep(db, row["id"])
        return {"seq": seq, "kind": kind, "again": False, "stop_asked_at": row["stop_asked_at"]}
    return _run(inside)


def _keep(db, link_id):
    """A session's posts kept to POSTS_KEPT, the oldest going first, but never a question still open."""
    over = db.execute("SELECT COUNT(*) FROM ade_connector_link_events WHERE link_id = ?",
                      (link_id,)).fetchone()[0] - POSTS_KEPT
    if over > 0:
        db.execute("DELETE FROM ade_connector_link_events WHERE seq IN (SELECT seq FROM ade_connector_link_events "
                   "WHERE link_id = ? AND NOT (kind = 'question' AND closed_at IS NULL) ORDER BY seq LIMIT ?)",
                   (link_id, over))


def _shows_itself(text):
    """One name or text of a checkpoint: Refused naming the character, never the text."""
    for ch in text:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise Refused(f"The checkpoint holds a lone surrogate (U+{code:04X}).")
        if (code < 0x20 and ch not in "\n\t") or 0x7F <= code <= 0x9F:
            raise Refused(f"The checkpoint holds a control character (U+{code:04X}).")
    code = link_commands.hidden_character(text)
    if code is not None:
        raise Refused(f"The checkpoint holds U+{code:04X}, which is not shown as itself.")
    code = link_commands.drawn_over(text)
    if code is not None:
        raise Refused(f"The checkpoint holds U+{code:04X}, a mark drawn over or around the character before it.")
    code = link_commands.stacked(text)
    if code is not None:
        raise Refused(f"The checkpoint holds U+{code:04X} with more than {link_commands.MARKS_MAX} marks on it.")
    if unicodedata.normalize("NFC", text) != text:
        raise Refused("The checkpoint holds text that is not in Unicode normal form C.")


def _no_secret(text):
    """One name or text of a checkpoint, or its JSON as it is stored, held to the shapes a session's words are held
    to (proposals._no_secret: the session channel's, and a connector token's): Refused naming the shape, never the
    text."""
    try:
        proposals._no_secret(text, "checkpoint")
    except proposals.ProposalError as e:
        raise Refused(str(e)) from None


def _room(text, count):
    """One more name or text of a checkpoint counted before it is read: Refused once those counted so far are
    together longer, in characters, than a checkpoint may be in bytes (each character is at least a byte of the JSON,
    which only adds to them, so such a document is too large whatever else it holds). So the names and texts read
    for a secret's shape are together no longer than a checkpoint may be, however long the request: on a long run
    the shapes of a signed web token and of a private key take the square of the text's length."""
    count[1] += len(text)
    if count[1] > link.MAX_CHECKPOINT:
        raise Refused(f"A checkpoint is at most {link.MAX_CHECKPOINT} bytes.")


def _shown(value, depth=0, count=None):
    """A checkpoint's document, each of whose names and texts shows itself (_shows_itself: no control character but a
    newline and a tab, no lone surrogate, no character that changes how a text reads without showing itself, no mark
    drawn over or around the character before it, at most 2 marks on one character, as a connected app's words are
    held) and holds nothing shaped like a secret, read as the text it is and not as the JSON that will store it
    (_no_secret: in JSON a line break or a tab before a shape is a backslash and a letter, and the shape begins no
    word); nested at most DEPTH deep; of at most NODES values, which are its texts, numbers, lists and objects at
    every depth, the document itself and the names not counted (a name has one value, so names are bounded with
    them). Each name and text is counted before it is read (_room): once those counted are together longer than a
    checkpoint may be, the document is refused for its size and no more of it is read."""
    count = [0, 0] if count is None else count      # its values, and the characters of its names and texts so far
    if depth:
        count[0] += 1
        if count[0] > NODES:
            raise Refused(f"A checkpoint holds at most {NODES} values.")
    if isinstance(value, str):
        _room(value, count)
        _shows_itself(value)
        _no_secret(value)
    elif isinstance(value, (dict, list)):
        if depth >= DEPTH:
            raise Refused(f"A checkpoint is nested at most {DEPTH} deep.")
        for item in (value.items() if isinstance(value, dict) else ((None, v) for v in value)):
            if item[0] is not None:
                _room(item[0], count)
                _shows_itself(item[0])
                _no_secret(item[0])
            _shown(item[1], depth + 1, count)
    elif isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise Refused("A checkpoint holds numbers JSON has.")
    return value


def checkpoint(grant, name, document, expected_version, now=None):
    """link_checkpoint: the session's checkpoint rewritten when `expected_version` is the one held: {version,
    stop_asked_at}; else Refused, with the version held and no word of the document held. Its names and texts each
    show themselves and hold nothing shaped like a secret, nested at most DEPTH deep (_shown, read before anything
    serialises it); at most link.MAX_CHECKPOINT bytes of JSON, which is read for a secret's shape once more whole."""
    now = _now(now)
    check_name(name)
    if not isinstance(document, dict) or not isinstance(expected_version, int) or isinstance(expected_version, bool):
        raise Refused("A checkpoint is {document, expected_version}: the document a JSON object, the version the whole "
                      "number link_session or the last link_checkpoint gave.")
    _shown(document)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > link.MAX_CHECKPOINT:
        raise Refused(f"A checkpoint is at most {link.MAX_CHECKPOINT} bytes.")
    _no_secret(text)        # and the whole as it is stored: a shape whose parts stand in two of its texts

    def inside(db):
        _app(db, grant, now)
        row = _mine(db, grant, name, now)
        if expected_version != row["checkpoint_version"]:
            raise Refused(CHANGED, version=row["checkpoint_version"])
        version = row["checkpoint_version"] + 1
        db.execute("UPDATE ade_connector_links SET checkpoint = ?, checkpoint_version = ?, checkpoint_at = ? WHERE "
                   "id = ?", (text, version, now, row["id"]))
        return {"version": version, "stop_asked_at": row["stop_asked_at"]}
    return _run(inside)


def stop_asked(grant, name):
    """When the owner asked the app's live session of that name to stop, or None: for a call that was refused, which
    is told of the stop as a call that was taken is (ade/connector.py). A read alone: the session is not heard by it,
    and a name no link may take names no session."""
    try:
        check_name(name)
    except Refused:
        return None
    with store.connect() as db:
        row = _live(db, grant, name)
    return row["stop_asked_at"] if row is not None else None


# ---- the owner's side: the page --------------------------------------------------------------------------------------

def for_owner(user_id, now=None):
    """The account's live sessions linked through its apps, as Linked sessions lists them, oldest first: each with its
    name, said to be the session's own word (named_by), the app that carried it (its id and the name the app calls
    itself), when it linked and was last heard (only when it calls: nothing keeps it heard between calls), its
    checkpoint, how many posts it has and its newest, its open questions, and when the owner asked it to stop. One
    whose name a session linked with its own key has taken is not listed, and is ended as this read finds it (BY_KEY,
    in a transaction of its own after the read; where that cannot take the write lock, at the next read, call or
    clearing). {links, server_time}."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute("SELECT s.*, g.client_name FROM ade_connector_links s JOIN ade_connector_grants g ON g.id = "
                          "s.grant_id WHERE s.user_id = ? AND s.ended_at IS NULL ORDER BY s.created_at, s.name",
                          (user_id,)).fetchall()
        out, taken = [], []
        for r in rows:
            if link.holder(db, user_id, r["name"]) is not None:
                taken.append(r["id"])   # a keyed link took its name: not listed, and ended once this read is done
                continue
            count = db.execute("SELECT COUNT(*) FROM ade_connector_link_events WHERE link_id = ?",
                               (r["id"],)).fetchone()[0]
            last = db.execute("SELECT * FROM ade_connector_link_events WHERE link_id = ? ORDER BY seq DESC LIMIT 1",
                              (r["id"],)).fetchone()
            asked = db.execute("SELECT * FROM ade_connector_link_events WHERE link_id = ? AND kind = 'question' AND "
                               "closed_at IS NULL ORDER BY seq", (r["id"],)).fetchall()
            out.append({"id": r["id"], "name": r["name"], "named_by": "session",
                        "app": {"id": r["grant_id"], "name": r["client_name"]},
                        "linked_at": r["created_at"], "heard_at": r["heard_at"],
                        "checkpoint": json.loads(r["checkpoint"]) if r["checkpoint"] else None,
                        "checkpoint_version": r["checkpoint_version"], "checkpoint_at": r["checkpoint_at"],
                        "posts": count, "last": _post_view(last) if last else None,
                        "questions": [{"seq": q["seq"], "at": q["at"], "text": q["text"]} for q in asked],
                        "stop_asked_at": r["stop_asked_at"]})
    if taken:
        try:
            _yield_all(user_id, taken, now)
        except sqlite3.OperationalError:    # the write lock not to be had: the read answers, and the ending waits for
            pass                            # the next read, call or clearing
    return {"links": out, "server_time": now}


def _yield_all(user_id, ids, now):
    """In a transaction of its own, after a read found them: each of the account's live sessions among `ids` whose
    name a keyed link holds, read again here, ended (BY_KEY), its posts and its checkpoint going."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for link_id in ids:
            row = db.execute("SELECT * FROM ade_connector_links WHERE id = ? AND user_id = ? AND ended_at IS NULL",
                             (link_id, user_id)).fetchone()
            if row is not None and link.holder(db, user_id, row["name"]) is not None:
                _end(db, "id = ?", (link_id,), BY_KEY, now)


def _post_view(row):
    return {"seq": row["seq"], "at": row["at"], "kind": row["kind"], "text": row["text"], "ref": row["ref"],
            "closed_at": row["closed_at"], "closed_by": row["closed_by"]}


def _owned(db, user_id, link_id):
    """The account's live session of that id; 404 for any other, another account's among them."""
    row = db.execute("SELECT * FROM ade_connector_links WHERE id = ? AND user_id = ? AND ended_at IS NULL",
                     (link_id, user_id)).fetchone() if _is_id(link_id) else None
    if row is None:
        raise oauth.ConnectorError(404, NO_LINK)
    return row


def posts(user_id, link_id, before=None):
    """One session's posts, newest first, a page at a time: {posts, before: where the next page starts, or None}."""
    upto = 2 ** 62 if before is None else max(0, min(int(before), 2 ** 62))
    with store.connect() as db:
        row = _owned(db, user_id, link_id)
        rows = db.execute("SELECT * FROM ade_connector_link_events WHERE link_id = ? AND seq < ? ORDER BY seq DESC "
                          "LIMIT ?", (row["id"], upto, POSTS_PAGE)).fetchall()
    return {"posts": [_post_view(r) for r in rows], "before": rows[-1]["seq"] if len(rows) == POSTS_PAGE else None}


def remove(user_id, link_id, now=None):
    """The owner takes a session off Linked sessions, with the sign-in alone (it can only unlink and delete): it ends,
    its posts and its checkpoint go for good, and its name is free again. Its session's next post is told why;
    link_session may link it again (while the name is still free and linking still on for its app)."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned(db, user_id, link_id)
        _end(db, "id = ?", (row["id"],), BY_OWNER, now)
    return {"removed": link_id}


def close_question(user_id, link_id, seq):
    """The owner closes one of a session's open questions on the page: it carries no words, and the session reads only
    that it was closed (CLOSED_SAID). {seq, closed_at}; 404 for anything but an open question of that session."""
    now = _now(None)
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 < seq <= link.SEQ_MAX:
        raise oauth.ConnectorError(404, NO_QUESTION)    # a number SQLite does not store is no question, never a 500
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned(db, user_id, link_id)
        done = db.execute("UPDATE ade_connector_link_events SET closed_at = ?, closed_by = 'owner' WHERE seq = ? AND "
                          "link_id = ? AND kind = 'question' AND closed_at IS NULL", (now, seq, row["id"])).rowcount
        if not done:
            raise oauth.ConnectorError(404, NO_QUESTION)
    return {"seq": seq, "closed_at": now}


def stop(user_id, link_id, on=True, now=None):
    """The owner asks a session to stop, or withdraws that, with the sign-in alone: a request in fixed words
    (STOP_SAID) the session reads in the answer to its next call under its name, taken or refused (ade/connector.py),
    since nothing here can hold it; asked twice, the first time stands. {stop_asked_at}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _owned(db, user_id, link_id)
        at = (row["stop_asked_at"] or now) if on else None
        db.execute("UPDATE ade_connector_links SET stop_asked_at = ? WHERE id = ?", (at, row["id"]))
    return {"stop_asked_at": at}


# ---- ending, and clearing --------------------------------------------------------------------------------------------

def _end(db, which, args, why, now):
    """Inside the caller's transaction: the live sessions matching `which` (SQL after "AND", with `args`) ended, saying
    why; their posts deleted and their checkpoints with them, so an ended session keeps only its name, its times and
    why. The number of rows changed or deleted."""
    ids = [r[0] for r in db.execute(f"SELECT id FROM ade_connector_links WHERE ended_at IS NULL AND {which}", args)]
    changed = 0
    for link_id in ids:
        changed += db.execute("DELETE FROM ade_connector_link_events WHERE link_id = ?", (link_id,)).rowcount
        changed += db.execute("UPDATE ade_connector_links SET ended_at = ?, ended_why = ?, checkpoint = NULL, "
                              "checkpoint_version = 0, checkpoint_at = NULL, stop_asked_at = NULL WHERE id = ?",
                              (now, why, link_id)).rowcount
    return changed


def end_for_grant(db, grant_id, why, now):
    """Inside the caller's transaction: every session linked through one app ended (a revoke, oauth.revoke_in; linking
    turned off for it, set_links)."""
    return _end(db, "grant_id = ?", (grant_id,), why, now)


def encryption_on(db, user_id, now):
    """Inside the transaction in which the account's encryption is turned on, or on again (e2e_keys._forget_decided):
    every session of the account linked through a connected app ended, its posts and its checkpoint deleted, since
    such a session cannot seal what it sends and nothing here seals in place. The number of rows changed or deleted,
    for which the database is then due for compaction."""
    return _end(db, "user_id = ?", (user_id,), BY_E2E, now)


def prune(now=None):
    """What is cleared on the connector's timer (oauth.prune), in a transaction of its own: a session not heard for
    IDLE_ENDS seconds ended; one whose app's connection is not a live one ended (a revoke ends them as it is made; a
    connection's 90 days ending, or its row gone, is found here); one whose name a session linked with its own key
    took, and which has not called since, ended; and an ended one deleted ENDED_KEPT seconds after it ended.
    {ended, deleted}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        ended = _end(db, "COALESCE(heard_at, created_at) <= ?", (now - IDLE_ENDS,), BY_SILENCE, now)
        ended += _end(db, "grant_id NOT IN (SELECT id FROM ade_connector_grants WHERE revoked_at IS NULL AND ends_at "
                          "> ?)", (now,), BY_APP_END, now)
        for r in db.execute("SELECT id, user_id, name FROM ade_connector_links WHERE ended_at IS NULL").fetchall():
            if link.holder(db, r["user_id"], r["name"]) is not None:
                ended += _end(db, "id = ?", (r["id"],), BY_KEY, now)
        gone = [r[0] for r in db.execute("SELECT id FROM ade_connector_links WHERE ended_at IS NOT NULL AND ended_at "
                                         "<= ?", (now - ENDED_KEPT,))]
        for link_id in gone:
            db.execute("DELETE FROM ade_connector_link_events WHERE link_id = ?", (link_id,))
            db.execute("DELETE FROM ade_connector_links WHERE id = ?", (link_id,))
    return {"ended": ended, "deleted": len(gone)}
