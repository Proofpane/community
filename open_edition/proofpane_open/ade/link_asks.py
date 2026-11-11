"""The session channel's asking (ade/link.py says what the channel is): a coding session, a cloud one especially, asks
to be linked by itself, and the owner approves it on the page. Nothing a session holds links it on its own: whoever
learns how to ask can make requests the owner refuses, and no more.

- Each account has an ask address (`ask-` and 20 base32 letters and digits), made the first time the page asks for it.
  It names the account to ask and grants nothing, so it is no secret. The owner can replace it: each request still
  waiting on the old one is refused, and one made to it later is a request to an address no account has.
- The session makes its own Ed25519 key pair, as for pairing, and asks with a request signed by the new key, with the
  signature every link request carries (link.message): the address, its name and kind as pair takes them, its public
  key and its session reference. The answer is the same whether or not an account has the address, and so is every
  poll after it: a request to an address no account has is kept as a waiting one with no account, and expires as one,
  so addresses cannot be probed. What the owner then does is told to the session: refused, cancelled, linked.
- The session polls with requests signed by the same key, at most once every POLL_EVERY seconds: waiting, until the
  request expires ASK_TTL seconds after it was made; refused; approved, with when the link is made; cancelled; linked,
  with the link's id and the account's passkeys to pin, as the server gives them then (link.owner_keys_of: trust on
  first use, where a pair command copied from the page names them); or failed, with why.
- The owner approves with the same confirmation a ticket takes (link._use_confirmation), or refuses. An approval is
  held (ade/holds.py): the link is made only when the hold ends (OPEN_EDITION_ADE_CONNECT_HOLD seconds, 180 unless
  set; OPEN_EDITION_ADE_CONNECT_HOLD_HIGH, 900 unless set, while the account's high security is on), a notice goes at
  once to the account's devices, and Cancel stops it until then. When the hold ends the name and the key are checked
  again: one a live link took meanwhile leaves the request failed, with why.
- A session paired by a ticket (link.pair) waits the same way: the pairing is a request approved as it
  is made (by_ticket; its `via` is ticket), with the ticket's id and what confirmed the ticket, and its hold, of the same
  kinds and lengths. A ticket that re-pairs a live link names that link (`replaces`): the link moves to the new key
  when the hold ends, and its old key works until then. Until the hold ends the new key is no link's, so every link
  route refuses it but the poll, which says where the pairing stands. When it ends it is checked again (_paired): the
  passkey that confirmed the ticket still the account's; for a re-pair, the link still live and the account's and the
  key on no other live link; else the name and the key still free. A ticket's request counts toward none of the limits
  below, which are an ask address's: a ticket is the owner's own word.
- A cloud session expires and comes back as a new one, with a new key, often under the same name. A cloud session's
  request under the name of a live cloud link can be approved as that link's replacement: the approval names the link
  by its id, and is refused unless it is the live cloud link of that name. When the hold ends, that link is revoked as a
  revoke from the page revokes it (link.end_link), in the transaction that makes the new one, and a note from the owner
  on the new link says what it replaced. Only a cloud link is replaced: a local link is the owner's machine, which keeps
  its key and does not come back new, so replacing one would only let a request take an existing machine's name.
- Limits, all counted from the table: PENDING_PER_ADDRESS requests waiting at once on one address (counted by the
  address, so the same for one no account has); from one network (an IPv4 address, an IPv6 /64, as the server sees it:
  behind the hosted site's proxy, the client's) ASKS_PER_NETWORK asks an hour and PENDING_PER_NETWORK waiting at once,
  and from a wider one (an IPv4 /24, an IPv6 /48, which one holder of many addresses usually has) ASKS_PER_WIDE_NETWORK
  an hour and PENDING_PER_WIDE_NETWORK waiting, as device sign-ins are held; and ASKS_PENDING waiting in all. A
  network's counts are its requests to any address, so an address no account has gets no count of its own. A request
  is kept a day after its last change (made, answered, linked or not), so the counts can be read and its session can
  still read the outcome; a held one until its hold ends.
- A session can never approve, refuse or cancel a request, its own or another's: those routes take the page's sign-in
  alone.
"""

import binascii
import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid

from .. import auth, passkeys, store
from . import holds, link, moves

ASK_TTL = 1800
POLL_EVERY = 2
PENDING_PER_ADDRESS = 3
ASKS_PER_NETWORK = 10               # in the last hour
PENDING_PER_NETWORK = 5             # waiting at once, as device sign-ins open from one network
ASKS_PER_WIDE_NETWORK = 50          # in the last hour, from a wider network
PENDING_PER_WIDE_NETWORK = 25       # waiting at once, as device sign-ins open from a wider network
ASKS_PENDING = 1000
KEEP = 86400
FAILED_SHOWN = 1800                 # how long the page shows a request whose hold ended without a link
ENDED_SHOWN = 600                   # how long the page is told how a ticket's pairing ended (ended_pairings)
FINGERPRINT_HEX = 32
ADDRESS_LETTERS = "abcdefghijklmnopqrstuvwxyz234567"
_ADDRESS = re.compile(r"ask-[a-z2-7]{20}")                 # fullmatch, never match
HOLD = "link.connect"
HOLD_SETTING = "OPEN_EDITION_ADE_CONNECT_HOLD"
HOLD_DEFAULT = 180
# while the account's high security is on, linking waits longer: long enough for a phone to show the notice, short
# enough for a working session
HOLD_HIGH = "link.connect.high"
HOLD_HIGH_SETTING = "OPEN_EDITION_ADE_CONNECT_HOLD_HIGH"
HOLD_HIGH_DEFAULT = 900
TICKET = "ticket"                  # how a request was made when a ticket made it (by_ticket); an ask's is null
REPLACED = "replaced:"             # the owner's note on a link made in place of another: its client id, before that id
# a request open for the key that asked: waiting (and not expired) or held (one parameter: now)
_OPEN = "(state = 'held' OR (state = 'pending' AND expires_at > ?))"


def _now(now):
    return int(now if now is not None else time.time())


def fingerprint(public_key_raw):
    """What the page shows of a session's key and what the session prints of its own: the first 32 hex characters (128
    bits) of the sha256 of its 32 bytes, of which a link's key_id is the first 16."""
    return hashlib.sha256(public_key_raw).hexdigest()[:FINGERPRINT_HEX]


def hold_kind(db, user_id):
    """The kind of hold linking a session waits on, by a ticket or by an approved request: HOLD_HIGH while the account's
    high security is on, else HOLD. The tier when it is placed sets its length."""
    return HOLD_HIGH if passkeys._high_security(db, user_id) else HOLD


def pair_hold(user_id):
    """The seconds a session linked now would wait, by a ticket or by an approval: the setting of the account's tier."""
    with store.connect() as db:
        return holds.delay(hold_kind(db, user_id))


# ---- the address -------------------------------------------------------------------------------------------------

def _put_address(db, user_id, now):
    for _ in range(5):
        new = "ask-" + "".join(secrets.choice(ADDRESS_LETTERS) for _ in range(20))
        try:
            db.execute("INSERT INTO ade_link_ask_addresses (user_id, address, created_at) VALUES (?,?,?) ON CONFLICT "
                       "(user_id) DO UPDATE SET address = excluded.address, created_at = excluded.created_at",
                       (user_id, new, now))
        except sqlite3.IntegrityError:      # another account's address, one chance in 2 ** 100: another
            continue
        return {"address": new, "created_at": now}
    raise RuntimeError("no ask address could be made")


def address(user_id, now=None):
    """The account's ask address, made the first time it is asked for: {address, created_at}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT address, created_at FROM ade_link_ask_addresses WHERE user_id = ?",
                         (user_id,)).fetchone()
        return dict(row) if row is not None else _put_address(db, user_id, now)


def replace_address(user_id, now=None):
    """A new ask address for the account. Each request still waiting on the old one is refused, and one made to the old
    one later is a request to an address no account has. {address, created_at, refused: how many were refused}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        made = _put_address(db, user_id, now)
        refused = db.execute("UPDATE ade_link_asks SET state = 'refused', decided_at = ?, changed_at = ? WHERE "
                             "user_id = ? AND state = 'pending' AND expires_at > ?", (now, now, user_id, now)).rowcount
    return dict(made, refused=refused)


# ---- a session's side ----------------------------------------------------------------------------------------------

def _answer(row, now):
    """Where a request stands, as its session reads it, built from the request's own row (and, once it is approved,
    its hold's), so a request to an address no account has reads as one its owner has not answered. Every answer names
    the name the request was made under; a ticket's pairing's names its ticket too (`ticket`, the first half of the
    sha256 the ADE keeps of it), by which a pair run again knows the pairing its ticket made (ade_link.py
    _paired_before). Linked is said only while the link the request made is live with the request's key: once that
    link is revoked, or moved to another key, the request reads "revoked", with nothing of the account."""
    out = {"fingerprint": fingerprint(link.unb64u(row["public_key"])), "server_time": now, "name": row["name"]}
    state, hold, made, live = row["state"], None, None, False
    with store.connect() as db:
        if state in ("held", "failed"):
            hold = holds.get(db, row["hold_id"])
        if row["via"] == TICKET:
            made = db.execute("SELECT ticket_hash FROM ade_link_tickets WHERE id = ?", (row["ticket_id"],)).fetchone()
        if state == "linked":
            live = db.execute("SELECT 1 FROM ade_links WHERE id = ? AND public_key = ? AND revoked_at IS NULL",
                              (row["link_id"], row["public_key"])).fetchone() is not None
    if made is not None:
        out["ticket"] = made["ticket_hash"][:32]
    if state == "pending":
        out.update({"status": "waiting", "expires_at": row["expires_at"]} if row["expires_at"] > now
                   else {"status": "expired"})
    elif state == "held":
        out.update(status="approved", takes_effect_at=hold["takes_effect_at"] if hold else None)
    elif state == "linked" and live:
        out.update(status="linked", link_id=row["link_id"], owner_keys=link.owner_keys_of(row["user_id"]))
    elif state == "linked":
        out["status"] = "revoked"                   # its link revoked since, or moved to another key
    elif state == "failed":
        out.update(status="failed", detail=hold["failed"] if hold else None)
    else:
        out["status"] = state                       # refused, cancelled
    return out


def ask(header, method, raw_path, raw_query, body, client="", now=None):
    """A session asks to be linked: the address first (a malformed one costs no signature work), then the signature
    with the key in the body, which proves the sender holds it, then its name, kind and reference, as pair takes them,
    then the limits. `client` is the address the request came from. Returns where the request stands (_answer); the
    same request made again (the same key, address, name, kind and reference, while it waits or is held, or once it is
    linked while its link is live with that key) is answered as it stands, so an answer lost on the way costs
    nothing."""
    now = _now(now)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise link.LinkError(422, "The request is JSON.") from None
    if not isinstance(data, dict):
        raise link.LinkError(422, "The request is a JSON object.")
    given = data.get("address")
    # ASCII only, as the page writes it: a spelling Unicode maps onto one (a Kelvin sign for a k) is not an address
    if not isinstance(given, str) or not given.strip().isascii() or not _ADDRESS.fullmatch(given.strip().lower()):
        raise link.LinkError(422, "That is not an ask address: ask- and 20 letters and digits, as the page shows it.")
    addr = given.strip().lower()
    try:
        raw_key = link.unb64u(data.get("public_key") or "")
    except (ValueError, binascii.Error, TypeError):
        raw_key = b""
    if len(raw_key) != 32:
        raise link.LinkError(422, "The public key is 32 bytes of an Ed25519 key, in base64url.")
    kid, ts, sig = link._parse_auth(header)
    if kid != link.key_id(raw_key):
        raise link.LinkError(401, "The request is not signed with the key it asks with.")
    link._verify(raw_key, method, raw_path, raw_query, ts, body, sig, now)
    name, kind = data.get("name"), data.get("kind")
    link.check_new_link(name, kind)
    session_ref = link._text(data.get("session_ref"), "session reference", link.MAX_REF, required=False)
    public_key = link.b64u(raw_key)
    net, wide = auth.network_of(client), auth.network_of(client, wide=True)
    # counted by its network and the wider one around it; an address the server cannot read (a test client) under
    # counts of its own
    counted = net if net is not None else "unread:" + str(client or "")[:200]
    widely = wide if wide is not None else counted
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # a day from its last change, so a request linked after a long hold is still read by its session's next poll
        db.execute("DELETE FROM ade_link_asks WHERE COALESCE(changed_at, created_at) <= ? AND state <> 'held'",
                   (now - KEEP,))
        from .signers import held_by_signer
        if held_by_signer(db, public_key):
            raise link.LinkError(409, "This key is a signer's: a linked session's key is its own.")
        live = db.execute("SELECT id FROM ade_links WHERE public_key = ? AND revoked_at IS NULL",
                          (public_key,)).fetchone()
        same = None if live is not None else db.execute(
            f"SELECT * FROM ade_link_asks WHERE key_id = ? AND public_key = ? AND {_OPEN} ORDER BY created_at DESC "
            "LIMIT 1", (kid, public_key, now)).fetchone()
        if live is not None:
            # a session whose client stopped before it read that it was linked (a command timeout shorter than the
            # hold) asks again: the request that made this key's link is answered as it stands, since the asker holds
            # the key; any other ask of a key on a live link is refused
            row = db.execute("SELECT * FROM ade_link_asks WHERE key_id = ? AND public_key = ? AND address = ? AND "
                             "name = ? AND kind = ? AND session_ref IS ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                             (kid, public_key, addr, name, kind, session_ref)).fetchone()
            if row is None or row["link_id"] != live["id"]:             # a request has a link_id once it is linked
                raise link.LinkError(409, "This key is already on a live link.")
        elif same is not None:
            if (same["address"], same["name"], same["kind"], same["session_ref"]) != (addr, name, kind, session_ref):
                raise link.LinkError(409, "This key has asked already, with another address, name, kind or "
                                          "reference: wait for that request to end.")
            row = same
        else:
            if db.execute("SELECT COUNT(*) FROM ade_link_asks WHERE state = 'pending' AND expires_at > ?",
                          (now,)).fetchone()[0] >= ASKS_PENDING:
                raise link.LinkError(429, "Too many sessions are asking to be linked; try again in a few minutes.")
            # a network's requests to any address, so an address no account has gets no count of its own; a ticket's
            # pairing (by_ticket), the owner's own word, is no ask and counts toward none of them
            for column, value, hourly, waiting, which in (
                    ("asked_net", counted, ASKS_PER_NETWORK, PENDING_PER_NETWORK, "This network"),
                    ("asked_wide", widely, ASKS_PER_WIDE_NETWORK, PENDING_PER_WIDE_NETWORK,
                     "The wider network around this address (its /24, or its /48)")):
                if db.execute(f"SELECT COUNT(*) FROM ade_link_asks WHERE {column} = ? AND created_at > ? AND via IS "
                              "NULL", (value, now - 3600)).fetchone()[0] >= hourly:
                    raise link.LinkError(429, f"{which} has asked {hourly} times in the last hour, as many times as it "
                                              "may.")
                if db.execute(f"SELECT COUNT(*) FROM ade_link_asks WHERE {column} = ? AND state = 'pending' AND "
                              "expires_at > ?", (value, now)).fetchone()[0] >= waiting:
                    raise link.LinkError(429, f"{which} has {waiting} requests waiting, as many as it may: one must be "
                                              "approved, refused or expire first.")
            if db.execute("SELECT COUNT(*) FROM ade_link_asks WHERE address = ? AND state = 'pending' AND "
                          "expires_at > ?", (addr, now)).fetchone()[0] >= PENDING_PER_ADDRESS:
                raise link.LinkError(429, f"This address has {PENDING_PER_ADDRESS} requests waiting, as many as it may: "
                                          "one must be approved, refused or expire first.")
            owner = db.execute("SELECT user_id FROM ade_link_ask_addresses WHERE address = ?", (addr,)).fetchone()
            ask_id = uuid.uuid4().hex
            db.execute("INSERT INTO ade_link_asks (id, address, user_id, name, kind, public_key, key_id, session_ref, "
                       "asked_from, asked_net, asked_wide, created_at, expires_at, state, changed_at) VALUES "
                       "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ask_id, addr, owner["user_id"] if owner is not None else None, name, kind, public_key, kid,
                        session_ref, client if net is not None else None, counted, widely, now, now + ASK_TTL,
                        "pending", now))
            row = db.execute("SELECT * FROM ade_link_asks WHERE id = ?", (ask_id,)).fetchone()
    return _answer(row, now)


def poll(header, method, raw_path, raw_query, body, now=None):
    """Where the request stands (_answer), for the session that made it: the poll is signed by the key of the newest
    request made with it. The account's holds whose time has come are settled first, so one that has ended has made
    its link. At most one poll every POLL_EVERY seconds (429); nothing a poll carries moves a hold."""
    now = _now(now)
    kid, ts, sig = link._parse_auth(header)
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_link_asks WHERE key_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                         (kid,)).fetchone()
    if row is None:
        raise link.LinkError(401, "No request was made with this key: ask first.")
    link._verify(link.unb64u(row["public_key"]), method, raw_path, raw_query, ts, body, sig, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM ade_link_asks WHERE id = ?", (row["id"],)).fetchone()
        if row is None:
            raise link.LinkError(401, "No request was made with this key: ask first.")
        if row["last_poll_at"] is not None and now - row["last_poll_at"] < POLL_EVERY:
            raise link.LinkError(429, f"Ask at most once every {POLL_EVERY} seconds.")
        db.execute("UPDATE ade_link_asks SET last_poll_at = ? WHERE id = ?", (now, row["id"]))
    if row["user_id"] is not None and row["state"] == "held":
        holds.settle(row["user_id"], now)
        with store.connect() as db:
            row = db.execute("SELECT * FROM ade_link_asks WHERE id = ?", (row["id"],)).fetchone()
    return _answer(row, now)


# ---- the owner's side ----------------------------------------------------------------------------------------------

def _word(row, now):
    """Where a request stands, as the owner is told it."""
    if row["state"] == "pending":
        return "waiting" if row["expires_at"] > now else "expired"
    return {"held": "approved, and held", "linked": "linked", "failed": "approved, and the link could not be made"}.get(
        row["state"], row["state"])


def _view(row, old):
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "fingerprint": fingerprint(link.unb64u(row["public_key"])), "asked_from": row["asked_from"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "session_ref": row["session_ref"],
            "state": row["state"], "hold_id": row["hold_id"], "takes_effect_at": row["takes_effect_at"],
            "told": row["told"], "failed": row["failed"], "via": row["via"], "ticket_id": row["ticket_id"],
            "replaces": None if old is None else {
                "id": old["id"], "name": old["name"], "created_at": old["created_at"],
                "last_seen_at": old["last_seen_at"], "fingerprint": fingerprint(link.unb64u(old["public_key"])),
                "revoked_at": old["revoked_at"]}}


def for_owner(user_id, now=None):
    """The account's requests, as the page shows them, oldest first: each waiting for its answer, each approved (a
    ticket's pairing among them, `via` ticket, with its ticket's id) whose hold runs (with the hold's time, and how many
    devices its notice went to), and each whose hold ended in the last FAILED_SHOWN seconds without a link, with why.
    Each with `replaces`: for one waiting, the live cloud link it may replace (_replacement); for one approved, the link
    its approval replaces, or the link its ticket re-pairs; else null."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute(
            "SELECT a.*, h.takes_effect_at, h.told, h.failed FROM ade_link_asks a LEFT JOIN ade_holds h ON "
            "h.id = a.hold_id WHERE a.user_id = ? AND ((a.state = 'pending' AND a.expires_at > ?) OR a.state = 'held' "
            "OR (a.state = 'failed' AND h.done_at > ?)) ORDER BY a.created_at, a.id",
            (user_id, now, now - FAILED_SHOWN)).fetchall()
        out = []
        for r in rows:
            if r["state"] == "pending":
                old, why = _replacement(db, r)
                old = old if why is None else None
            else:
                old = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ?",
                                 (r["replaces"], user_id)).fetchone() if r["replaces"] else None
            out.append(_view(r, old))
    return out


def ended_pairings(user_id, now=None):
    """The account's ticket pairings that ended in the last ENDED_SHOWN seconds, linked or cancelled, by their ticket's
    id, oldest first: how the page's ticket box learns what became of its pairing once the pairing is no longer listed,
    whether or not it saw it listed (one held for no time, or one that ended between two reads), and what the links
    alone cannot tell it (a link linked again with its own key has that key whether its re-pair was linked or
    cancelled). One that failed is listed with why (for_owner), and is not among these."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute("SELECT ticket_id, state FROM ade_link_asks WHERE user_id = ? AND via = ? AND state IN "
                          "('linked', 'cancelled') AND changed_at > ? ORDER BY changed_at, id",
                          (user_id, TICKET, now - ENDED_SHOWN)).fetchall()
    return [{"ticket_id": r["ticket_id"], "state": r["state"]} for r in rows]


def _replacement(db, row):
    """The live link a waiting request may replace, and why it may not: (that link, None) when the account's live link
    of the request's name (link.holder) is a cloud link, its name is the request's exactly, the request is a cloud
    session's and its key is on no live link; else (the live link of its name, or None, and why not). A cloud session
    expires and comes back as a new one, with a new key, often under the same name; a local link is the owner's
    machine, which keeps its key and does not come back new, so replacing one would only let a request take an existing
    machine's name. Messages are addressed by exact name, so a replacement keeps the name as it is: one that changed its
    case or spacing would cut the link off from them."""
    holder = link.holder(db, row["user_id"], row["name"])
    try:
        link.check_free(db, row["user_id"], None, row["public_key"])
    except link.LinkError as e:
        return holder, str(e)
    if holder is None:
        return None, f"No live link is named {row['name']}, so there is none to replace: approve the request as it is."
    if holder["kind"] != "cloud":
        return holder, (f"The live link named {holder['name']} is a local one, which is never replaced: revoke it "
                        "first, or refuse the request.")
    if row["kind"] != "cloud":
        return holder, (f"Only a cloud session's request replaces a link, and this one is local: revoke the link named "
                        f"{holder['name']} first, or refuse the request.")
    if holder["name"] != row["name"]:
        why = (f"The live link is named “{holder['name']}”, which differs from this request's name only in "
               "case or spacing")
        # a name from before the rules on spaces cannot be asked under: such a link is revoked first
        return holder, why + (", and a name spaced so cannot be asked for: revoke the link first, or refuse the "
                              "request." if link.spaced_badly(holder["name"]) else
                              ": to replace it, the session asks under the link's own name.")
    return holder, None


def _waiting(db, user_id, ask_id, now):
    row = db.execute("SELECT * FROM ade_link_asks WHERE id = ? AND user_id = ?", (ask_id, user_id)).fetchone()
    if row is None:
        raise link.LinkError(404, "No such request.")
    if row["state"] != "pending" or row["expires_at"] <= now:
        raise link.LinkError(409, f"That request is not waiting for an answer: it is {_word(row, now)}.",
                             state=_word(row, now))
    return row


def approve(user_id, ask_id, confirmation=None, replace=None, now=None):
    """The owner approves a request waiting for the account's answer, with the same confirmation a ticket takes
    (link._use_confirmation, so a rule on which ways may confirm covers both): the request is held, and the link is
    made when its hold ends (_link_it); the account's devices are told at once. With `replace`, the id of the live cloud
    link of the request's name, the approval replaces that link (_replacement), which is revoked as the new one is
    made. 404 for a request that is not the account's; 409 for one not waiting, for one whose name or key a live link
    has, and for a replace that is not the live cloud link of its name; a refusal leaves the confirmation unused.
    {hold, told: how many devices the notice goes to}. One request at a time for a key, as pair has it: 409 while another
    request of the request's key is open (a ticket's pairing made as this one expired, say). Its time is taken once the
    write lock is held, so a wait for the lock moves neither the request's expiry nor the hold's end."""
    given = now
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        now = _now(given)
        row = _waiting(db, user_id, ask_id, now)
        if db.execute(f"SELECT 1 FROM ade_link_asks WHERE key_id = ? AND public_key = ? AND id <> ? AND {_OPEN}",
                      (row["key_id"], row["public_key"], row["id"], now)).fetchone():
            raise link.LinkError(409, "This request's key waits to be linked by another request already: wait for "
                                      "that to end.")
        old = None
        if replace is None:
            link.check_free(db, user_id, row["name"], row["public_key"])
        else:
            old, why = _replacement(db, row)
            if why is None and old["id"] != replace:
                why = (f"The live link named {old['name']} is not the one this approval replaces: read Linked sessions "
                       "again.")
            if why is not None:
                raise link.LinkError(409, why)
        # made for this request, and for the link it replaces if any: one given for another request, or for this
        # one replacing another link or none, approves nothing
        link._use_confirmation(db, user_id, confirmation, now,
                               f"link-approve:{row['id']}" + (f":{old['id']}" if old is not None else ""))
        summary = (f"Linking \u201c{row['name']}\u201d ({row['kind']}) was approved" if old is None else
                   f"Replacing the link \u201c{row['name']}\u201d ({row['kind']}) was approved")
        hold = holds.place(db, user_id, hold_kind(db, user_id), row["id"], summary, now)
        db.execute("UPDATE ade_link_asks SET state = 'held', decided_at = ?, hold_id = ?, replaces = ?, changed_at = ? "
                   "WHERE id = ?", (now, hold["id"], old["id"] if old is not None else None, now, ask_id))
    told = holds.tell(hold, now)
    return {"hold": dict(hold, told=told), "told": told}


def refuse(user_id, ask_id, now=None):
    """The owner refuses a request waiting for the account's answer; its session is told it was refused. 404 for a
    request that is not the account's, 409 for one not waiting. {refused: its id}."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _waiting(db, user_id, ask_id, now)
        db.execute("UPDATE ade_link_asks SET state = 'refused', decided_at = ?, changed_at = ? WHERE id = ?",
                   (now, now, ask_id))
    return {"refused": ask_id}


def alerts(user_id, now=None):
    """The notifier's items, each (key, the kind of its notice: push.words): each request waiting for the account's
    answer, said once, in fixed words: never what it calls itself, where it asked from or its key, which the page
    shows."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute("SELECT id FROM ade_link_asks WHERE user_id = ? AND state = 'pending' AND expires_at > ? "
                          "ORDER BY created_at, id", (user_id, now)).fetchall()
    return [(f"link-ask:{r['id']}", "link-ask") for r in rows]


# ---- a ticket's pairing --------------------------------------------------------------------------------------------

def by_ticket(db, ticket, name, kind, public_key, session_ref, client, now):
    """Inside link.pair's transaction, the ticket and the signature checked and the ticket used up: the pairing as a
    request approved as it is made (`via` TICKET), with the ticket's id and what confirmed the ticket, the name, kind,
    key and session reference, and the address it came from (`client`, as ask keeps it), and its hold placed
    (hold_kind). A ticket that re-pairs a live link names that link (`replaces`); `name` and `kind` are then the link's
    own. The link is made, or moved to the new key, when the hold ends (_paired). (The request's row, the hold.)"""
    net, wide = auth.network_of(client), auth.network_of(client, wide=True)
    counted = net if net is not None else "unread:" + str(client or "")[:200]
    ask_id = uuid.uuid4().hex
    summary = (f"Linking “{name}” ({kind}) by a ticket" if ticket["link_id"] is None else
               f"Moving the link “{name}” to a new key, by a ticket")
    hold = holds.place(db, ticket["user_id"], hold_kind(db, ticket["user_id"]), ask_id, summary, now)
    # a ticket names no ask address, and was never waiting for an answer: expired as it was made, and decided then
    db.execute("INSERT INTO ade_link_asks (id, address, user_id, name, kind, public_key, key_id, session_ref, asked_from, "
               "asked_net, asked_wide, created_at, expires_at, state, decided_at, hold_id, replaces, changed_at, via, "
               "ticket_id, confirmed, confirmed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (ask_id, "", ticket["user_id"], name, kind, public_key, link.key_id(link.unb64u(public_key)),
                session_ref, client if net is not None else None, counted, wide if wide is not None else counted, now,
                now, "held", now, hold["id"], ticket["link_id"], now, TICKET, ticket["id"], ticket["confirmed"],
                ticket["confirmed_by"]))
    return db.execute("SELECT * FROM ade_link_asks WHERE id = ?", (ask_id,)).fetchone(), hold


def _paired(db, row, now):
    """At the end of a ticket's pairing's hold: checked again, then done as link.pair did it at once in older releases.
    The passkey that confirmed the ticket, when one did, is still the account's (its removal cancels the hold, unless
    the hold was due as it was removed); for a ticket that re-pairs a link, that link is still live and the account's
    and the new key on no other live link, and the link moves to it; else the name and the key are still free, and a
    new link is made. Its id; LinkError, saying why, when it cannot be."""
    if row["confirmed_by"] is not None and db.execute("SELECT 1 FROM passkeys WHERE id = ? AND user_id = ?",
                                                      (row["confirmed_by"], row["user_id"])).fetchone() is None:
        raise link.LinkError(409, "The passkey that confirmed its ticket was removed.")
    # high security's rule, as pair applies it: its turning on cancels such a pairing, but a settle that runs after
    # both ends applies the two in one; and a key that was a hardware key when it confirmed the ticket and is no longer
    # counted one (passkeys.reconsider) vouches for nothing now
    if passkeys._high_security(db, row["user_id"]) and (
            row["confirmed"] != link.TICKET_BY_HARDWARE
            or db.execute("SELECT 1 FROM passkeys WHERE id = ? AND user_id = ? AND tier = ?",
                          (row["confirmed_by"], row["user_id"], passkeys.HARDWARE)).fetchone() is None):
        raise link.LinkError(403, link.TICKET_NOT_HARDWARE)
    if row["replaces"] is None:
        return link.make_link(db, row["user_id"], row["name"], row["kind"], row["public_key"], row["session_ref"], now)
    moved = db.execute("SELECT id FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                       (row["replaces"], row["user_id"])).fetchone()
    if moved is None:
        raise link.LinkError(409, "The link its ticket re-pairs was revoked.")
    other = db.execute("SELECT id FROM ade_links WHERE public_key = ? AND revoked_at IS NULL",
                       (row["public_key"],)).fetchone()
    if other is not None and other["id"] != moved["id"]:
        raise link.LinkError(409, "This key is already on another live link.")
    db.execute("UPDATE ade_links SET public_key = ?, key_id = ?, session_ref = ? WHERE id = ?",
               (row["public_key"], row["key_id"], row["session_ref"], moved["id"]))
    return moved["id"]


# ---- the hold of an approval ---------------------------------------------------------------------------------------

def _link_it(db, hold, now):
    """An approval's hold has ended: the link is made, its name and key checked again (a live link may have taken
    either meanwhile, through a ticket or another request), or the request is left failed, the reason kept on the
    hold. An approval that replaces a link revokes that link first (_replace), and a note from the owner on the new
    link says so, so the account's events keep the revoke as part of the replacement. A ticket's pairing is checked
    and done as _paired says."""
    row = db.execute("SELECT * FROM ade_link_asks WHERE id = ? AND user_id = ?",
                     (hold["subject"], hold["user_id"])).fetchone()
    if row is None or row["state"] != "held":
        return "The request is no longer held."
    try:
        if row["via"] == TICKET:
            old, link_id = None, _paired(db, row, now)
        else:
            old = _replace(db, row, hold, now) if row["replaces"] is not None else None
            link_id = link.make_link(db, row["user_id"], row["name"], row["kind"], row["public_key"],
                                     row["session_ref"], now)
    except link.LinkError as e:
        db.execute("UPDATE ade_link_asks SET state = 'failed', changed_at = ? WHERE id = ?", (now, row["id"]))
        return str(e)
    if old is not None:
        db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, to_id, text, needs_turn, "
                   "received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (row["user_id"], link.OWNER, REPLACED + old["id"], "note", row["name"], link_id,
                    f"Linked in place of the link named {row['name']} (linked {holds.utc(old['created_at'])}, key "
                    f"{fingerprint(link.unb64u(old['public_key']))}): the owner approved the replacement, and that "
                    "link was revoked as this one was made.", 0, now))
    db.execute("UPDATE ade_link_asks SET state = 'linked', link_id = ?, changed_at = ? WHERE id = ?",
               (link_id, now, row["id"]))
    return None


def _replace(db, row, hold, now):
    """At the end of an approval's hold that replaces a link: that link revoked as a revoke from the page revokes it
    (link.end_link), when it is still live, still has the request's name exactly and is still a cloud link. Its row, or
    None when it is not that (revoked meanwhile, say), when the new link is made only if the name is free. The new key
    is checked first, so a key a live link took meanwhile revokes nothing. The files of the old link's moves go once the
    hold's transaction has committed (holds: hold["after"]), as a revoke's go once its own has."""
    link.check_free(db, row["user_id"], None, row["public_key"])
    old = db.execute("SELECT * FROM ade_links WHERE id = ? AND user_id = ? AND kind = 'cloud' AND revoked_at IS NULL",
                     (row["replaces"], row["user_id"])).fetchone()
    if old is None or old["name"] != row["name"]:
        return None
    gone = link.end_link(db, row["user_id"], old["id"], now)
    hold["after"].append(lambda: moves.delete_files(gone))
    return old


def _cancelled(db, hold, now):
    """The owner cancelled an approval's hold: its session is told it was cancelled."""
    db.execute("UPDATE ade_link_asks SET state = 'cancelled', changed_at = ? WHERE id = ? AND state = 'held'",
               (now, hold["subject"]))


def _broke(db, hold, now):
    """An approval's hold whose linking broke (holds.settle): the request is left failed, as one that cannot be linked
    is (_link_it), so its key may ask again and its session reads how it ended."""
    db.execute("UPDATE ade_link_asks SET state = 'failed', changed_at = ? WHERE id = ? AND state = 'held'",
               (now, hold["subject"]))


def _notice(db, row, at_once):
    """The kind of an approval's notice (push.words): a link waits to be made, or, for one that replaces a live link,
    to replace another; for a ticket's pairing, a session waits to be linked, or a linked machine to move to a new key;
    never which, which the page shows."""
    ask = db.execute("SELECT replaces, via FROM ade_link_asks WHERE id = ?", (row["subject"],)).fetchone()
    again = ask is not None and ask["replaces"] is not None
    if ask is not None and ask["via"] == TICKET:
        kind = "held-pair-again" if again else "held-pair"
    else:
        kind = "held-link-replace" if again else "held-link"
    return kind + ("-now" if at_once else "")


# an approval keeps nothing of which passkey, or the password, confirmed it, so a lost device's steps cancel it
# (holds.cancel_all), and a ticket's pairing too, whatever confirmed its ticket (which _paired checks again at the end)
# (the high tier's wait never shorter than the other's: a setting that would make it so takes the other's)
for _kind, _setting, _default, _floor in ((HOLD, HOLD_SETTING, HOLD_DEFAULT, None),
                                          (HOLD_HIGH, HOLD_HIGH_SETTING, HOLD_HIGH_DEFAULT, HOLD)):
    holds.register(_kind, _link_it, _cancelled, setting=_setting, default=_default, unsigned=True, released=_broke,
                   notice=_notice, at_least=_floor)
