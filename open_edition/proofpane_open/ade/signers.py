"""The second screen's server side: signers, and
the relay that carries the steps the owner's page parks for them.

A signer is a program on the owner's own machine (ade_link.py signer and sign) that shows a step there, decoded from the
bytes it signs, and has the owner's security key sign it. It is a third kind of principal, neither the page's sign-in
nor a link's key: it signs every request with an Ed25519 key of its own, under its own scheme name,
`Authorization: ADE-Signer key_id=<hex>, ts=<unix seconds>, sig=<b64url>`, over what a link's signature covers
(link.message). A link's key verifies on no signer route, a signer's on no link route, and neither on the page's routes,
which take the sign-in alone; one key never stands for both a signer and a link (held_by_signer, and pair here).

A signer carries no authority. It may read the account's passkeys and which may sign now, the requests waiting for it
(each with what the ADE says beside it, the one question an answer names among it), the account's live links, and a
nonce for a step it composes; it may hand back an assertion for a waiting request, and post a step it composed to the
functions the page's routes call (link_commands.post_command, resume), which check the owner's signature as they check
the page's. It may not post events, take commands, read timelines, histories, feeds or checkpoints, park a request,
approve, refuse or cancel anything, read end-to-end salts or grants, or ask for a confirmation; no route of a signer's
returns an assertion. A key stolen from the machine reads the steps parked in their five minutes and the links' names,
and gets nonces; it gets no step, since every step needs the owner's touch.

The relay carries bytes and an assertion, and knows nothing of what a step does. The page parks a payload, checked as
the route that will take it checks one before a signature (its fields, its nonce given for its purpose and account and
not used, its size). A signer lists those waiting; the owner picks one there by the code their page shows, signs it,
and the signer hands the assertion back, checked over the stored bytes (passkeys.check_command) before it is kept, with
nothing committed: no nonce used, no counter moved. The page collects it, checks the payload is the one it parked, and
sends both to the route it would have used, which checks everything again and keeps it. At most 10 requests wait for an
account, and 3 from one network, so a stolen sign-in cannot fill the queue from one place; a request lives as long as
its nonce, and is deleted 10 minutes after its end.

What signed a stored step is kept beside it (ade_signed_through): the step's own route keeps the payload and the
assertion as they came, and the assertion's signature is one a signer handed back or posted, so `through` names that
signer. Whether the client data carries the marker of a program that is not a browser is read from the assertion itself
(marked); it is that program's own word, never a proof of what was shown or who read it.
"""

import base64
import binascii
import json
import re
import secrets
import time
import uuid

from .. import auth, config, passkeys, paths, store  # noqa: F401  (paths puts the open packages on sys.path)
from . import link, link_commands
from proofpane_core.canonical import CanonicalizationError, canonicalize

TICKET_TTL = 600
MAX_SIGNERS = 5
WAITING_PER_ACCOUNT = 10
WAITING_PER_NETWORK = 3
POLL_EVERY = 2                              # seconds between one signer's reads of its waiting requests, at least
KEPT_AFTER = 600                            # a request's row is deleted this long after its end
MARKER = "second-screen/1"                  # the client data's "proofpane" member a signer writes (ade_link.py)
# the purposes a page parks (every signed step a signer signs, ade_link.py's SIGN_PURPOSES: never a task's grant, which
# no signer shows, so one parked would hold a place until its nonce ended; nor a Manager's holder, which the page signs
# in the browser), those a signer gets a nonce for and composes itself (never an answer, whose question it does not
# read, nor a statement, whose key work is the page's), and those it posts as commands
PARKED = tuple(p for p in link_commands.PURPOSES if p not in (link_commands.TASK_GRANT, link_commands.MANAGER))
COMPOSED = ("instruction", "resume", link_commands.MOVE)
POSTED = ("instruction", link_commands.MOVE)
WAITING, SIGNED = "waiting", "signed"
SIGNER_NEEDS_CONFIRM = ("Confirm it is you first, with one of this account's passkeys or its password: a sign-in alone "
                        "does not pair a signer.")
REVOKED = "No live signer has this key: it was revoked, or never paired. Pair this machine again: ade_link.py signer pair."

_AUTH = re.compile(r"ADE-Signer key_id=([0-9a-f]{16}), ts=(\d{1,12}), sig=([A-Za-z0-9_-]{40,200})")
_ID = re.compile(r"[0-9a-f]{32}")                       # these: fullmatch, never match


def _now(now):
    return int(now if now is not None else time.time())


def code_of(raw):
    """The request code of a step whose canonical JSON is `raw` (bytes): the first 5 bytes of its step challenge in
    RFC 4648 base32, as two groups of four, as the page and the signer compute it (ade_link.sign_code). It matches a
    request to its prompt; 40 bits prove nothing."""
    code = base64.b32encode(passkeys.step_challenge(raw)[:5]).decode("ascii")
    return code[:4] + "-" + code[4:]


def marked(credential):
    """Whether an assertion's client data (`credential` as JSON text or an object) carries the marker a signer writes:
    the signing program's own word that it is not a browser, which an honest browser never writes and any other program
    can. False for anything that cannot be read."""
    try:
        cred = json.loads(credential) if isinstance(credential, str) else credential
        raw = link.unb64u(cred["response"]["clientDataJSON"])
        return json.loads(raw.decode("utf-8")).get("proofpane") == MARKER
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error, UnicodeDecodeError, RecursionError):
        return False


def _signature_of(credential):
    """The assertion's signature, as base64url text, or None."""
    response = credential.get("response") if isinstance(credential, dict) else None
    sig = response.get("signature") if isinstance(response, dict) else None
    return sig if isinstance(sig, str) and sig else None


# ---- the signer's own key ---------------------------------------------------------------------------------------------

def _parse_auth(header):
    m = _AUTH.fullmatch(header or "")
    if not m:
        raise link.LinkError(401, "Sign the request: Authorization: ADE-Signer key_id=..., ts=..., sig=....")
    return m.group(1), int(m.group(2)), m.group(3)


def authenticate(header, method, raw_path, raw_query, body, now=None):
    """The live signer that signed this request, as a dict; LinkError(401) otherwise."""
    now = _now(now)
    kid, ts, sig = _parse_auth(header)
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_signers WHERE key_id = ? AND revoked_at IS NULL", (kid,)).fetchone()
    if row is None:
        raise link.LinkError(401, REVOKED)
    link._verify(link.unb64u(row["public_key"]), method, raw_path, raw_query, ts, body, sig, now)
    signer = dict(row)
    if signer["last_seen_at"] is None or now - signer["last_seen_at"] >= link.SEEN_EVERY:
        with store.connect() as db:
            db.execute("UPDATE ade_signers SET last_seen_at = ? WHERE id = ?", (now, signer["id"]))
    return signer


def held_by_signer(db, public_key):
    """Whether a live signer holds `public_key` (base64url): a link and an asking session may not use it."""
    return db.execute("SELECT 1 FROM ade_signers WHERE public_key = ? AND revoked_at IS NULL",
                      (public_key,)).fetchone() is not None


# ---- tickets, pairing, the page's list, revoking ----------------------------------------------------------------------

TICKET_CHANGE = "signer-ticket"     # passkeys.CHANGE: what a signer ticket's confirmation is made for


def _ticket_confirmation(db, user_id, confirmation, now):
    """A signer is paired on the person's own word as well as the sign-in, once the account has a way to give it
    (passkeys.confirm), and a hardware key's while high security is on, as a link ticket is. The word is one made for
    a signer ticket (TICKET_CHANGE), so one given for a link's ticket or any other change makes none, and one made for
    a signer ticket is spent on no other change."""
    try:
        return passkeys._use_confirmation(db, user_id, confirmation, now, subject=TICKET_CHANGE)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, SIGNER_NEEDS_CONFIRM if str(e) == passkeys.NEEDS_CONFIRM else str(e)) from None


def _live(db, user_id):
    return db.execute("SELECT COUNT(*) FROM ade_signers WHERE user_id = ? AND revoked_at IS NULL",
                      (user_id,)).fetchone()[0]


def create_ticket(user_id, confirmation=None, now=None):
    """A one-time ticket to pair a signer: {ticket, expires_at, url, account, command}. Only its sha256 is kept; it uses
    up the account's confirmation where it can make one, and keeps what confirmed it, as a link ticket does."""
    now = _now(now)
    ticket = "PP-" + base64.b32encode(secrets.token_bytes(5)).decode("ascii")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _live(db, user_id) >= MAX_SIGNERS:
            raise link.LinkError(409, f"An account has at most {MAX_SIGNERS} signers: revoke one first.")
        used = _ticket_confirmation(db, user_id, confirmation, now)
        db.execute("INSERT INTO ade_signer_tickets (id, user_id, ticket_hash, created_at, expires_at, confirmed, "
                   "confirmed_by) VALUES (?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, user_id, link._ticket_hash(ticket), now, now + TICKET_TTL,
                    link.TICKET_BY_HARDWARE if used and used.kind == passkeys.CONFIRMED_HARDWARE else
                    link.TICKET_BY_OTHER if used else None, used and used.passkey_id))
    url = config.public_url()
    return {"ticket": ticket, "expires_at": now + TICKET_TTL, "url": url, "account": user_id,
            "command": f"ade_link.py signer pair --url {url} --ticket {ticket}"}


def pair(header, method, raw_path, raw_query, body, now=None):
    """Pair a signer: the ticket first, then the signature with the key in the body, which proves the sender holds it.
    {signer_id, account, url}: the client keeps the url it was given, never the one answered. A key a live link, an
    open request to be linked, or a live signer holds is refused. Under the write lock, as a link's pairing:
    the ticket read again with the time taken again, the passkey that confirmed it still the account's, and while high
    security is on, a ticket only a key still counted a hardware key confirmed (link.pair, link_asks._paired)."""
    given = now
    now = _now(now)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise link.LinkError(422, "The request is JSON.") from None
    if not isinstance(data, dict):
        raise link.LinkError(422, "The request is a JSON object.")
    ticket = data.get("ticket")
    if not isinstance(ticket, str) or not ticket.strip().isascii() or not link._TICKET.fullmatch(ticket.strip().upper()):
        raise link.LinkError(403, "That is not a ticket from the page.")
    th = link._ticket_hash(ticket.strip())
    with store.connect() as db:
        t = db.execute("SELECT * FROM ade_signer_tickets WHERE ticket_hash = ?", (th,)).fetchone()
    if t is None or t["used_at"] is not None or t["expires_at"] <= now:
        raise link.LinkError(403, "The ticket is used, has expired, or is not the page's: make a new one.")
    try:
        raw_key = link.unb64u(data.get("public_key") or "")
    except (ValueError, binascii.Error, TypeError):
        raw_key = b""
    if len(raw_key) != 32:
        raise link.LinkError(422, "The public key is 32 bytes of an Ed25519 key, in base64url.")
    kid, ts, sig = _parse_auth(header)
    if kid != link.key_id(raw_key):
        raise link.LinkError(401, "The request is not signed with the key it pairs.")
    link._verify(raw_key, method, raw_path, raw_query, ts, body, sig, now)
    name = data.get("name")
    if not isinstance(name, str) or not link._NAME.fullmatch(name) or link.spaced_badly(name):
        raise link.LinkError(422, f"A signer's name is letters, digits, spaces, dots, dashes and underscores, at most "
                                  f"{link.MAX_NAME} characters, with no two spaces together and none at its end.")
    link.check_secrets(name, "name")
    public_key = link.b64u(raw_key)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # its time taken again once the write lock is held: a wait for it moves none of the checks below
        now = _now(given)
        t = db.execute("SELECT * FROM ade_signer_tickets WHERE ticket_hash = ?", (th,)).fetchone()
        # voided (link.void_tickets), used, or expired since it was read
        if t is None or t["used_at"] is not None or t["expires_at"] <= now:
            raise link.LinkError(403, "The ticket is used, has expired, or is not the page's: make a new one.")
        # the passkey that confirmed it, when one did, still the account's: removing it voids the ticket, and this
        # holds whatever voided it or not
        if t["confirmed_by"] is not None and db.execute("SELECT 1 FROM passkeys WHERE id = ? AND user_id = ?",
                                                        (t["confirmed_by"], t["user_id"])).fetchone() is None:
            raise link.LinkError(403, "The passkey that confirmed this ticket was removed: make a new one.")
        # high security's rule, as a link's pairing applies it: a key that was a hardware key when it confirmed the
        # ticket and is no longer counted one (passkeys.reconsider) vouches for nothing now
        if passkeys._high_security(db, t["user_id"]) and (
                t["confirmed"] != link.TICKET_BY_HARDWARE
                or db.execute("SELECT 1 FROM passkeys WHERE id = ? AND user_id = ? AND tier = ?",
                              (t["confirmed_by"], t["user_id"], passkeys.HARDWARE)).fetchone() is None):
            raise link.LinkError(403, link.TICKET_NOT_HARDWARE)
        if db.execute("SELECT 1 FROM ade_links WHERE public_key = ? AND revoked_at IS NULL",
                      (public_key,)).fetchone() is not None \
                or db.execute("SELECT 1 FROM ade_link_asks WHERE public_key = ? AND (state = 'held' OR (state = "
                              "'pending' AND expires_at > ?))", (public_key, now)).fetchone() is not None:
            raise link.LinkError(409, "This key is a linked session's, or asks to be one: a signer's key is its own.")
        if held_by_signer(db, public_key):
            raise link.LinkError(409, "This key is already a live signer's.")
        if _live(db, t["user_id"]) >= MAX_SIGNERS:
            raise link.LinkError(409, f"An account has at most {MAX_SIGNERS} signers: revoke one first.")
        signer_id = uuid.uuid4().hex
        db.execute("INSERT INTO ade_signers (id, user_id, name, public_key, key_id, created_at, last_seen_at, "
                   "confirmed) VALUES (?,?,?,?,?,?,?,?)",
                   (signer_id, t["user_id"], name, public_key, kid, now, now, t["confirmed"]))
        db.execute("UPDATE ade_signer_tickets SET used_at = ? WHERE id = ?", (now, t["id"]))
    return {"signer_id": signer_id, "account": t["user_id"], "url": config.public_url()}


def for_page(user_id):
    """The account's live signers, for the page: {signers: [{id, name, created_at, last_seen_at}]}, oldest first."""
    with store.connect() as db:
        rows = db.execute("SELECT id, name, created_at, last_seen_at FROM ade_signers WHERE user_id = ? AND "
                          "revoked_at IS NULL ORDER BY created_at, rowid", (user_id,)).fetchall()
    return {"signers": [dict(r) for r in rows]}


def revoke(user_id, signer_id, now=None):
    """Revoke one of the account's live signers: its key works nowhere from now. {revoked_at}; 404 otherwise."""
    now = _now(now)
    with store.connect() as db:
        done = db.execute("UPDATE ade_signers SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                          (now, signer_id, user_id)).rowcount
    if not done:
        raise link.LinkError(404, "No such signer.")
    return {"revoked_at": now}


def revoke_all(db, user_id, now):
    """Inside signing out everywhere's transaction (auth.sign_out_everywhere), as a lost machine's step: every signer of
    the account revoked, its unused signer tickets voided, and every request parked for a signer deleted, with any
    assertion it holds."""
    db.execute("UPDATE ade_signers SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, user_id))
    db.execute("DELETE FROM ade_signer_tickets WHERE user_id = ? AND used_at IS NULL", (user_id,))
    db.execute("DELETE FROM ade_sign_requests WHERE user_id = ?", (user_id,))


# ---- the relay: the page's side --------------------------------------------------------------------------------------

def _prune(db, now):
    db.execute("DELETE FROM ade_sign_requests WHERE expires_at <= ?", (now - KEPT_AFTER,))


def _network(client):
    """What a parked request counts by: the network of the address it came from (auth.network_of), or, for an address
    the server cannot read (a test client), the address itself under a count of its own, as asking to be linked does."""
    net = auth.network_of(client)
    return net if net is not None else "unread:" + str(client or "")[:200]


def park(user_id, data, client="", now=None):
    """Park a step the page composed for a signer: {payload, title?}, the payload checked as the route that will take it
    checks one before a signature (link_commands: its fields, its form, its nonce given for its purpose and this
    account, not used, and its size), a move's title held to what a title is. {id, code, expires_at}: the request lives
    as long as its nonce, or its payload where that ends first. The same payload parked again is the same request."""
    now = _now(now)
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise link.LinkError(422, "A request to sign is {payload, title?}.")
    purpose = payload.get("purpose")
    if purpose not in PARKED:
        raise link.LinkError(422, "A request's purpose is " + ", ".join(PARKED) + ".")
    try:
        raw = canonicalize(payload)
    except (CanonicalizationError, TypeError, ValueError) as e:
        raise link.LinkError(422, f"The payload has no canonical form: {e}") from None
    if len(raw) > link_commands.PAYLOAD_MAX:
        raise link.LinkError(422, f"A payload is at most {link_commands.PAYLOAD_MAX} bytes.")
    link_commands._check_payload(payload, user_id, purpose, now)
    try:
        challenge, nonce_expires = passkeys.open_nonce(payload["nonce"], user_id, purpose, now)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None
    issued = nonce_expires - passkeys.CHALLENGE_TTL
    if not issued - 2 <= payload["created_at"] <= issued + link_commands.NONCE_DRIFT:
        raise link.LinkError(422, f"The payload's created_at is not the time its nonce was given (within "
                                  f"{link_commands.NONCE_DRIFT} seconds after it).")
    title = data.get("title")
    if title is not None:
        if purpose != link_commands.MOVE:
            raise link.LinkError(422, "Only a move carries a title beside its payload.")
        title = link._shown(link._text(title, "session's title", link.HISTORY_TITLE), "session's title")
    text = raw.decode("utf-8")
    expires = min(nonce_expires, payload["expires_at"])
    net = _network(client)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _prune(db, now)
        same = db.execute("SELECT id, code, expires_at FROM ade_sign_requests WHERE user_id = ? AND payload = ? AND "
                          "expires_at > ?", (user_id, text, now)).fetchone()
        if same is not None:
            return dict(same)
        if passkeys.nonce_used(db, challenge, now):
            raise link.LinkError(409, "The payload's nonce was used already: compose the step again.")
        open_ = "user_id = ? AND expires_at > ?"
        if db.execute(f"SELECT COUNT(*) FROM ade_sign_requests WHERE {open_}", (user_id, now)).fetchone()[0] \
                >= WAITING_PER_ACCOUNT:
            raise link.LinkError(429, f"At most {WAITING_PER_ACCOUNT} requests wait for a signer: cancel one, or let "
                                      "one end.")
        if db.execute(f"SELECT COUNT(*) FROM ade_sign_requests WHERE {open_} AND parked_network = ?",
                      (user_id, now, net)).fetchone()[0] >= WAITING_PER_NETWORK:
            raise link.LinkError(429, f"At most {WAITING_PER_NETWORK} requests from one network wait for a signer: "
                                      "cancel one, or let one end.")
        request_id = uuid.uuid4().hex
        code = code_of(raw)
        db.execute("INSERT INTO ade_sign_requests (id, user_id, purpose, payload, code, title, created_at, expires_at, "
                   "state, parked_from, parked_network) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (request_id, user_id, purpose, text, code, title, now, expires, WAITING, str(client or "")[:200],
                    net))
    return {"id": request_id, "code": code, "expires_at": expires}


def _brief(row):
    """A request as the page lists it: what it is and where it came from, never its code, which the page shows only
    beside a request it parked itself (it computes it from the payload it composed)."""
    payload = json.loads(row["payload"])
    return {"id": row["id"], "purpose": row["purpose"], "to": payload.get("to"), "created_at": row["created_at"],
            "expires_at": row["expires_at"], "parked_from": row["parked_from"], "state": row["state"]}


def requests_for_page(user_id, now=None):
    """Every request of the account that has not ended, waiting or signed, oldest first: {requests}."""
    now = _now(now)
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_sign_requests WHERE user_id = ? AND expires_at > ? ORDER BY created_at, "
                          "rowid", (user_id, now)).fetchall()
    return {"requests": [_brief(r) for r in rows]}


def collect(user_id, request_id, now=None):
    """One request, as the page polls it: {id, state, expires_at}, with, once a signer signed it, the payload's
    canonical text, the assertion, and the signer's name. 404 for one that is not this account's, or has ended."""
    now = _now(now)
    with store.connect() as db:
        row = db.execute("SELECT r.*, s.name AS signer_name FROM ade_sign_requests r LEFT JOIN ade_signers s ON "
                         "s.id = r.signer_id WHERE r.id = ? AND r.user_id = ? AND r.expires_at > ?",
                         (request_id, user_id, now)).fetchone()
    if row is None:
        raise link.LinkError(404, "No such request: it was cancelled, or its time ended.")
    out = {"id": row["id"], "state": row["state"], "expires_at": row["expires_at"]}
    if row["state"] == SIGNED:
        out.update(payload=row["payload"], credential=json.loads(row["credential"]), signer=row["signer_name"])
    return out


def cancel(user_id, request_id):
    """Cancel one of the account's requests, with any assertion it holds. {cancelled: 1}; 404 otherwise."""
    with store.connect() as db:
        done = db.execute("DELETE FROM ade_sign_requests WHERE id = ? AND user_id = ?", (request_id, user_id)).rowcount
    if not done:
        raise link.LinkError(404, "No such request.")
    return {"cancelled": done}


def cancel_all(user_id):
    """Cancel every request of the account, with any assertion each holds: {cancelled: how many}."""
    with store.connect() as db:
        return {"cancelled": db.execute("DELETE FROM ade_sign_requests WHERE user_id = ?", (user_id,)).rowcount}


# ---- the relay: the signer's side ------------------------------------------------------------------------------------

def _allow(user_id):
    """The credential ids that may sign a step now: the account's hardware keys while high security is on
    (passkeys.signing_credentials), else every passkey of the account. Never empty while it has a passkey."""
    named = passkeys.signing_credentials(user_id)
    return named or [k["credential_id"] for k in passkeys.owner_keys(user_id)]


def _context(db, user_id, row, payload):
    """What the ADE says beside a parked step, all of it its word: the link it names ({id: {name, stopped_at}}), an
    answer's one question ({text}, or {sealed: true}, or {sealed: "in place"}), an app's proposal it is ({name,
    words}), a move's title (the one parked beside it, else its link's list's), and whether high security is on."""
    from . import e2e
    out = {"high_security": passkeys._high_security(db, user_id)}
    to = payload.get("to")
    target = db.execute("SELECT id, name, stopped_at FROM ade_links WHERE id = ? AND user_id = ? AND revoked_at IS "
                        "NULL", (to, user_id)).fetchone() if isinstance(to, str) else None
    if target is not None:
        out["links"] = {target["id"]: {"name": target["name"], "stopped_at": target["stopped_at"]}}
    if payload.get("purpose") == "answer" and target is not None:
        q = db.execute("SELECT * FROM ade_link_events WHERE seq = ? AND user_id = ? AND link_id = ? AND kind = "
                       "'question'", (payload.get("ref"), user_id, target["id"])).fetchone()
        if q is not None:
            out["question"] = ({"sealed": "in place"} if q["sealed_in_place"] else
                               {"sealed": True} if q["text"].startswith(e2e.PREFIX) else {"text": q["text"]})
    proposal = db.execute("SELECT p.text, g.client_name FROM ade_proposals p LEFT JOIN ade_connector_grants g ON "
                          "g.id = p.grant_id WHERE p.id = ? AND p.user_id = ?", (payload.get("id"), user_id)).fetchone()
    if proposal is not None:
        out["app"] = {"name": proposal["client_name"], "words": proposal["text"]}
    if payload.get("purpose") == link_commands.MOVE:
        title = row["title"]
        if title is None and target is not None:
            listed = db.execute("SELECT title, sealed FROM ade_link_history WHERE link_id = ? AND user_id = ? AND "
                                "session_id = ?", (target["id"], user_id, payload.get("session"))).fetchone()
            title = listed["title"] if listed is not None and not listed["sealed"] else None
        if title:
            out["title"] = title
    return out


def requests_for_signer(signer, now=None):
    """The requests waiting for a signature for the signer's account, oldest first, each with its id, purpose, canonical
    payload, code, times, where it was parked from, the credential ids that may sign it (allow) and what the ADE says
    beside it (context). A signer reads them every POLL_EVERY seconds at most (429 otherwise)."""
    now = _now(now)
    user_id = signer["user_id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        polled = db.execute("SELECT polled_at FROM ade_signers WHERE id = ?", (signer["id"],)).fetchone()["polled_at"]
        if polled is not None and now - polled < POLL_EVERY:
            raise link.LinkError(429, f"A signer reads its requests every {POLL_EVERY} seconds at most.",
                                 retry_after=POLL_EVERY - (now - polled))
        db.execute("UPDATE ade_signers SET polled_at = ? WHERE id = ?", (now, signer["id"]))
        _prune(db, now)
        rows = db.execute("SELECT * FROM ade_sign_requests WHERE user_id = ? AND state = ? AND expires_at > ? ORDER BY "
                          "created_at, rowid", (user_id, WAITING, now)).fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            out.append({"id": row["id"], "purpose": row["purpose"], "payload": row["payload"], "code": row["code"],
                        "created_at": row["created_at"], "expires_at": row["expires_at"],
                        "parked_from": row["parked_from"], "context": _context(db, user_id, row, payload)})
    allow = _allow(user_id)
    for request in out:
        request["allow"] = allow
    return {"requests": out}


def _record(db, user_id, payload_id, credential, signer_id, now):
    """Keep that the assertion `credential` over the payload `payload_id` came through `signer_id`."""
    sig = _signature_of(credential)
    if sig is not None and isinstance(payload_id, str) and _ID.fullmatch(payload_id):
        db.execute("INSERT OR IGNORE INTO ade_signed_through (user_id, payload_id, signature, signer_id, created_at) "
                   "VALUES (?,?,?,?,?)", (user_id, payload_id, sig, signer_id, now))


def _stored_signature(credential):
    """The signature of an assertion as stored (JSON text) or given (an object), as base64url text, or None."""
    try:
        return _signature_of(json.loads(credential) if isinstance(credential, str) else credential)
    except (ValueError, RecursionError):
        return None


def relayed(db, user_id, steps):
    """{(payload id, signature): the signer's name} for those of `steps`, (payload id, the assertion as stored) pairs,
    whose assertion a signer handed back or posted, read at once: what the page may say of each, "relayed by your
    signer". A signer revoked since is still named: it says how the step came, not what may sign now."""
    wanted = {}
    for payload_id, credential in steps:
        sig = _stored_signature(credential)
        if sig is not None and isinstance(payload_id, str) and _ID.fullmatch(payload_id):
            wanted.setdefault(payload_id, set()).add(sig)
    if not wanted:
        return {}
    ids = sorted(wanted)
    rows = db.execute("SELECT t.payload_id, t.signature, s.name FROM ade_signed_through t JOIN ade_signers s ON "
                      f"s.id = t.signer_id WHERE t.user_id = ? AND t.payload_id IN ({','.join('?' * len(ids))})",
                      (user_id, *ids)).fetchall()
    return {(r["payload_id"], r["signature"]): r["name"] for r in rows if r["signature"] in wanted[r["payload_id"]]}


def through(db, user_id, payload_id, credential):
    """The name of the signer that handed back or posted the assertion `credential` (as stored: JSON text) over the
    payload `payload_id`, or None: what the page may say of a stored step, "relayed by your signer"."""
    return relayed(db, user_id, [(payload_id, credential)]).get((payload_id, _stored_signature(credential)))


def signed_facts(passkey_id, credential, payload_id, names):
    """What the page may say of one of the owner's stored steps, from the server's own records:
    the passkey that signed it (its id: the page names it from the account's list), whether the stored client data
    carries the marker (marked: the signing program's own word, never a fact about what was shown or where), and the
    signer it came through, from `names` (relayed), or None."""
    return {"passkey_id": passkey_id, "marked": marked(credential),
            "relayed_by": names.get((payload_id, _stored_signature(credential)))}


def payload_id_of(text):
    """The id of a payload stored as canonical JSON text, or None."""
    try:
        payload = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):
        return None
    return payload.get("id") if isinstance(payload, dict) else None


def resume_facts(link_row, names):
    """signed_facts of a link's last resume (ade_links' resume_* columns), or None when it has none."""
    if not link_row["resume_signature"]:
        return None
    return signed_facts(link_row["resume_passkey_id"], link_row["resume_signature"],
                        payload_id_of(link_row["resume_payload"]), names)


def submit(signer, request_id, data, now=None):
    """A signer hands back the assertion for one waiting request: {credential}, checked over the stored bytes
    (passkeys.check_command: one of the account's passkeys, the step challenge of those bytes, the client data, the
    flags, the signature, the counter not gone back) and, while high security is on, one of its hardware keys, before it
    is kept; nothing is committed (no nonce used, no counter moved): the step's own route does that. {state}; 404 for a
    request that is not the account's or has ended, 409 for one not waiting."""
    now = _now(now)
    user_id = signer["user_id"]
    credential = data.get("credential") if isinstance(data, dict) else None
    if not isinstance(credential, dict):
        raise link.LinkError(422, "An assertion handed back is {credential}.")
    kept = json.dumps(credential, separators=(",", ":"))
    if len(kept) > link_commands.ASSERTION_MAX:
        raise link.LinkError(422, f"A passkey's answer is at most {link_commands.ASSERTION_MAX} bytes.")
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_sign_requests WHERE id = ? AND user_id = ? AND expires_at > ?",
                         (request_id, user_id, now)).fetchone()
    if row is None:
        raise link.LinkError(404, "No such request: it was cancelled, or its time ended.")
    if row["state"] != WAITING:
        raise link.LinkError(409, "That request is signed already.")
    try:
        passkey, _ = passkeys.check_command(user_id, row["payload"].encode("utf-8"), credential)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, f"The assertion does not hold: {e}.") from None
    if passkeys.high_security(user_id) and passkey["tier"] != passkeys.HARDWARE:
        raise link.LinkError(403, passkeys.ONLY_HARDWARE_SIGNS)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        done = db.execute("UPDATE ade_sign_requests SET state = ?, credential = ?, signer_id = ?, signed_at = ? WHERE "
                          "id = ? AND user_id = ? AND state = ? AND expires_at > ?",
                          (SIGNED, kept, signer["id"], now, request_id, user_id, WAITING, now)).rowcount
        if not done:
            raise link.LinkError(409, "That request was signed, cancelled or ended meanwhile.")
        _record(db, user_id, json.loads(row["payload"]).get("id"), credential, signer["id"], now)
    return {"state": SIGNED}


def sessions_for_signer(signer):
    """The account's live links, to compose for: {links: [{id, name, kind, stopped_at}]}, oldest first. Nothing else of
    them: no checkpoint, timeline, history or question."""
    with store.connect() as db:
        rows = db.execute("SELECT id, name, kind, stopped_at FROM ade_links WHERE user_id = ? AND revoked_at IS NULL "
                          "ORDER BY created_at, rowid", (signer["user_id"],)).fetchall()
    return {"links": [dict(r) for r in rows]}


def owner_keys_for_signer(signer):
    """The account's passkeys as a session pins them (passkeys.owner_keys), the credential ids that may sign now
    (_allow), and whether high security is on: {account, passkeys, credentials, high_security}."""
    user_id = signer["user_id"]
    return {"account": user_id, "passkeys": passkeys.owner_keys(user_id), "credentials": _allow(user_id),
            "high_security": passkeys.high_security(user_id)}


def nonce_for_signer(signer, data, now=None):
    """A nonce for a step the signer composes: an instruction, a resume or a move (link_commands.nonce)."""
    purpose = data.get("purpose") if isinstance(data, dict) else None
    if purpose not in COMPOSED:
        raise link.LinkError(422, "A signer composes an instruction, a resume or a move.")
    return link_commands.nonce(signer["user_id"], purpose, now)


def _signed_body(data):
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict) \
            or not isinstance(data.get("credential"), dict):
        raise link.LinkError(422, "A signed request is {payload, credential}.")
    return data["payload"], data["credential"]


def post_for_signer(signer, data, now=None):
    """A step the signer composed and the owner signed there: an instruction or a move, to the function the page's
    route calls (link_commands.post_command), which checks it as it checks the page's. What came through the signer is
    kept beside it (through)."""
    payload, credential = _signed_body(data)
    if payload.get("purpose") not in POSTED:
        raise link.LinkError(422, "A signer posts an instruction or a move; a resume goes to /links/{id}/resume.")
    answer = link_commands.post_command(signer["user_id"], data, now)
    with store.connect() as db:
        _record(db, signer["user_id"], payload.get("id"), credential, signer["id"], _now(now))
    return answer


def resume_for_signer(signer, link_id, data, now=None):
    """A resume the signer composed and the owner signed there, to the function the page's route calls
    (link_commands.resume)."""
    payload, credential = _signed_body(data)
    answer = link_commands.resume(signer["user_id"], link_id, data, now)
    with store.connect() as db:
        _record(db, signer["user_id"], payload.get("id"), credential, signer["id"], _now(now))
    return answer
