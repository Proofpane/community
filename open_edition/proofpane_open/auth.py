"""Accounts: only from one-time invitation links; passwords with scrypt, or a passkey (passkeys.py); tokens signed
with HMAC-SHA256.

An invitation is bound to one email address. Its link carries a random token that is shown once, to the admin who
creates it, and stored only as its sha256; it expires after 24 hours and works once. Registering takes the token and
a password, and the address comes from the invitation, so knowing an invited address is not enough to register it.

A sign-in token names the account and the account's token generation when it was issued; signing out everywhere
moves the generation on, so every token issued before stops working at once (a lost device's included), the device
that asked gets a new one, and a device approved and not yet signed in is refused.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
import unicodedata
import uuid

from . import config, store

EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class AuthError(ValueError):
    """A request the caller can correct: shown to the user as it is."""


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def normalise_email(email):
    email = (email or "").strip().lower()
    if not EMAIL.fullmatch(email):
        raise AuthError("That is not an email address.")
    return email


def hash_password(password):
    if not isinstance(password, str) or not config.MIN_PASSWORD <= len(password) <= config.MAX_PASSWORD:
        raise AuthError(f"A password is {config.MIN_PASSWORD} to {config.MAX_PASSWORD} characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def check_password(password, stored):
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(_unb64(digest)), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(got, _unb64(digest))


def _generation(user_id):
    with store.connect() as db:
        row = db.execute("SELECT token_gen FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["token_gen"] if row else 0


def issue_token(user_id, now=None, gen=None, device=None):
    """A sign-in of the account for TOKEN_TTL_SECONDS, of its current generation (or `gen`, as a transaction read it).
    `device`: the id of the device sign-in it is (device_signins), so it ends when that does (devices_going)."""
    now = int(now if now is not None else time.time())
    claims = {"sub": user_id, "exp": now + config.TOKEN_TTL_SECONDS, "gen": _generation(user_id) if gen is None else gen}
    if device is not None:
        claims["dev"] = device
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(config.token_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def token_user(token, now=None):
    """The account a valid, unexpired token of the account's current generation names, as `user` gives it, or
    None; a device's sign-in, only while it has not been ended (devices_going). Every route and the terminal check a
    sign-in through here."""
    claims = _claims(token, now)
    if claims is None:
        return None
    with store.connect() as db:
        if not _live(db, claims):
            return None
    return user(claims["sub"])


def _live(db, claims):
    """Whether a token's claims (_claims) are of the account's current generation and, for a device's sign-in (its
    `dev`), of one approved by that account, collected and not ended, as `db` reads them (inside the caller's
    transaction, where it has one)."""
    gen = claims.get("gen", 0)       # a token issued before generations existed is of the first one
    if not isinstance(gen, int) or isinstance(gen, bool):
        return False
    row = db.execute("SELECT token_gen FROM users WHERE id = ?", (claims["sub"],)).fetchone()
    if gen != (row["token_gen"] if row else 0):
        return False
    device = claims.get("dev")
    if device is None:
        return True
    row = db.execute("SELECT approved_by, used_at, ended_at FROM device_signins WHERE id = ?",
                     (device,)).fetchone() if isinstance(device, str) else None
    return row is not None and row["approved_by"] == claims["sub"] and row["used_at"] is not None \
        and row["ended_at"] is None


class SignedOut(AuthError):
    """A sign-in that ended while its request was on its way (answered 401, as one that had ended before)."""


def sign_out_everywhere(user_id, now=None, device=None):
    """End every sign-in token the account holds, and, in the same transaction, refuse every device the account
    approved that has not collected its sign-in yet (it would collect one of the new generation, poll_device_signin),
    void every unused link ticket (ade/link.void_tickets) and cancel each approval of the account still held that
    names no passkey (ade/holds.cancel_all: a request to link a session), since any of them may have been made with a
    sign-in taken from a lost device; returns a new token for the device that asked, still the device sign-in it was
    (`device`, its token's `dev`), so removing the passkey that approved that device ends it as before. A change to
    end-to-end encryption is not cancelled here: its hold takes a passkey it does not remove, or the recovery code
    (ade/e2e_keys.py)."""
    from .ade import holds, link
    when = int(now if now is not None else time.time())
    holds.settle(user_id, when)         # what has taken effect has; what has not is cancelled below
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE users SET token_gen = token_gen + 1 WHERE id = ?", (user_id,))
        db.execute("UPDATE device_signins SET denied_at = ? WHERE approved_by = ? AND used_at IS NULL "
                   "AND denied_at IS NULL", (when, user_id))
        link.void_tickets(db, user_id)
        # as a lost machine's step: every signer revoked, its unused tickets voided, what was parked for it deleted
        from .ade import signers
        signers.revoke_all(db, user_id, when)
        holds.cancel_all(db, user_id, when)
    return issue_token(user_id, now, device=device)


def token_device(token):
    """The device sign-in a token is (its `dev`), or None: for sign_out_everywhere's new token."""
    claims = _claims(token)
    device = claims.get("dev") if claims else None
    return device if isinstance(device, str) else None


def read_token(token, now=None):
    """The user id a valid, unexpired token names, or None (whatever its generation: token_user checks that)."""
    claims = _claims(token, now)
    return claims["sub"] if claims else None


def _claims(token, now=None):
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    payload, mac = token.split(".")
    want = hmac.new(config.token_secret(), payload.encode("ascii", "replace"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(mac), want):
            return None
        claims = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    now = int(now if now is not None else time.time())
    if not isinstance(claims, dict) or not isinstance(claims.get("sub"), str) or not isinstance(claims.get("exp"), int):
        return None
    return claims if claims["exp"] > now else None


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invitation(email, created_by, now=None):
    """A new invitation for `email`; returns its token, which is not stored and cannot be shown again."""
    email = normalise_email(email)
    now = int(now if now is not None else time.time())
    token = secrets.token_urlsafe(32)
    with store.connect() as db:
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("That address already has an account.")
        db.execute("INSERT INTO invitations (token_hash, email, created_by, created_at, expires_at) VALUES (?,?,?,?,?)",
                   (_token_hash(token), email, created_by, now, now + config.INVITE_TTL_SECONDS))
    return token


def list_invitations():
    with store.connect() as db:
        rows = db.execute("SELECT email, created_by, created_at, expires_at, used_at FROM invitations "
                          "ORDER BY created_at DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def invitation_email(token, now=None):
    """The address an unused, unexpired invitation token is for, or None."""
    if not isinstance(token, str) or not token:
        return None
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        row = db.execute("SELECT email FROM invitations WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                         (_token_hash(token), now)).fetchone()
    return row["email"] if row else None


def register(token, password, now=None):
    """Create the account an invitation is for and use the invitation up. Returns (user_id, email)."""
    password_hash = hash_password(password)
    now = int(now if now is not None else time.time())
    user_id = uuid.uuid4().hex
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT email FROM invitations WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                         (_token_hash(token or ""), now)).fetchone()
        if row is None:
            raise AuthError("This invitation link is not valid: it may have been used already, or have expired.")
        email = row["email"]
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("That address already has an account.")
        cur = db.execute("UPDATE invitations SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                         (now, _token_hash(token)))
        if cur.rowcount != 1:
            raise AuthError("This invitation link is not valid: it may have been used already, or have expired.")
        db.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?,?,?,?)",
                   (user_id, email, password_hash, now))
    return user_id, email


def login(email, password, client=""):
    """The user id for a correct email and password; the same error whichever of the two is wrong, in about the same
    time: an address with no account is checked against a hash of nothing. Attempts are counted (below), and past the
    count none is checked."""
    try:
        email = normalise_email(email)
    except AuthError:
        raise AuthError("Wrong email or password.") from None
    with store.connect() as db:
        row = db.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    # counted alike whether an account has it or not: the answer tells nothing
    ok = counted_check(email, client, password or "", row["password_hash"] if row is not None else _dummy_hash())
    if row is None or not ok:
        raise AuthError("Wrong email or password.")
    password_right(email, client)
    return row["id"]


# ---- password attempts, counted: signing in, and the confirmation passkeys.confirm takes --------------------------
#
# Each attempt counts against the address it named and against the network it came from (an IPv4 address, an IPv6
# /64), over PASSWORD_WINDOW seconds: an address with no account under a count of its own as one with, so neither
# the 429 nor its timing tells the two apart; and each kept as a digest, never as typed. It is counted before its password is checked, under one lock,
# so attempts made at once are counted as they arrive, not after their checks; a right password takes back its
# account's count and its own attempts from its network's, so one account's sign-in does not clear another's guesses
# from the same network. Past PASSWORD_PER_NETWORK from one network, or PASSWORD_PER_ACCOUNT for one account from
# anywhere, no password is checked (no scrypt run) and the answer is 429 until the oldest counted leaves the window.
# The account's count is what holds a guesser with many addresses; it also lets anyone keep that account's password
# refused, for at most the window at a time, while its passkeys and a sign-in approved on another device go on
# working. At most SCRYPT_AT_ONCE checks run at once whatever is asked: each takes 16 MiB, and the app's machine has
# 512 MB (its image keeps glibc to MALLOC_ARENA_MAX=2 arenas, so the threads' buffers are not each kept); a check that
# waits SCRYPT_WAIT seconds for its turn (holding its thread that long) is answered busy (429), and its attempt, never
# checked, is not counted. Kept in this process's memory: a restart forgets the counts.

PASSWORD_WINDOW = 600
PASSWORD_PER_NETWORK = 10
PASSWORD_PER_ACCOUNT = 20
MAX_COUNTED = 10000             # past this many keys, the ones with nothing in the window go, at most every PRUNE_EVERY
PRUNE_EVERY = 60
SCRYPT_AT_ONCE = 4
SCRYPT_WAIT = 2.0
_failures = {}                  # ("account", digest) or ("network", net) -> [(time, digest or None)] in the window
_failures_lock = threading.Lock()
_pruned_at = [0.0]
_scrypt = threading.BoundedSemaphore(SCRYPT_AT_ONCE)
_dummy = []


class TooManyAttempts(AuthError):
    """Past the attempts counted (429)."""

    def __init__(self, wait, doing="sign in"):
        self.wait = max(1, int(wait))
        minutes = max(1, -(-self.wait // 60))
        super().__init__(f"Too many wrong passwords for this account or from this network: try again in {minutes} "
                         f"minute{'' if minutes == 1 else 's'}, or {doing} with a passkey.")


class Busy(TooManyAttempts):
    """More passwords waiting to be checked than are checked at once, for longer than SCRYPT_WAIT (429)."""

    def __init__(self):
        AuthError.__init__(self, "Too many passwords are being checked at once: try again in a moment.")
        self.wait = 2


def _digest(account):
    return hashlib.sha256(account.encode("utf-8")).hexdigest()[:32] if account is not None else None


def _password_keys(account, client):
    net = network_of(client)
    keys = [(("network", net if net is not None else str(client or "")), PASSWORD_PER_NETWORK)]
    if account is not None:
        keys.insert(0, (("account", _digest(account)), PASSWORD_PER_ACCOUNT))
    return keys


def _prune(now):
    if len(_failures) < MAX_COUNTED or now - _pruned_at[0] < PRUNE_EVERY:
        return
    _pruned_at[0] = now
    for key in [k for k, entries in _failures.items() if not entries or entries[-1][0] <= now - PASSWORD_WINDOW]:
        del _failures[key]


def attempt(account, client="", now=None, doing="sign in"):
    """Count one password attempt, before its password is checked: against the address it named (an account's or
    not) and its network. TooManyAttempts when either is past its count."""
    now = time.time() if now is None else now
    with _failures_lock:
        _prune(now)
        keys = _password_keys(account, client)
        for key, most in keys:
            entries = [e for e in _failures.get(key, ()) if e[0] > now - PASSWORD_WINDOW]
            if entries:
                _failures[key] = entries
            else:
                _failures.pop(key, None)
            if len(entries) >= most:
                raise TooManyAttempts(entries[0][0] + PASSWORD_WINDOW - now, doing)
        for key, _ in keys:
            _failures.setdefault(key, []).append((now, _digest(account)))
    return now


def _take_back(account, client, at):
    """An attempt counted at `at` that was never checked (answered busy): no longer counted."""
    entry = (at, _digest(account))
    with _failures_lock:
        for key, _ in _password_keys(account, client):
            entries = _failures.get(key) or []
            if entry in entries:
                entries.remove(entry)
            if not entries:
                _failures.pop(key, None)


def counted_check(account, client, password, stored, doing="sign in"):
    """Count the attempt (TooManyAttempts past the count), then check the password; one answered busy, never checked,
    is taken back."""
    at = attempt(account, client, doing=doing)
    try:
        return checked(password, stored)
    except Busy:
        _take_back(account, client, at)
        raise


def password_right(account, client=""):
    """A right password: the account's count cleared, and from its network's only its own attempts."""
    with _failures_lock:
        _failures.pop(("account", _digest(account)), None)
        for key, _ in _password_keys(None, client):
            kept = [e for e in _failures.get(key, ()) if e[1] != _digest(account)]
            if kept:
                _failures[key] = kept
            else:
                _failures.pop(key, None)


def checked(password, stored):
    """check_password, at most SCRYPT_AT_ONCE at a time; Busy when no turn comes within SCRYPT_WAIT seconds."""
    if not _scrypt.acquire(timeout=SCRYPT_WAIT):
        raise Busy()
    try:
        return check_password(password, stored)
    finally:
        _scrypt.release()


def _dummy_hash():
    """A hash of nothing anyone knows, to check an address with no account against."""
    if not _dummy:
        _dummy.append(hash_password(secrets.token_urlsafe(24)))
    return _dummy[0]


def user(user_id):
    """The account as the page knows it; `can_sign_in_again`: whether it has a password or a passkey to sign in with
    after signing out (the desktop server's own account has neither, and signs in only through the link it prints)."""
    with store.connect() as db:
        row = db.execute("SELECT id, email, created_at, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        keys = db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0]
    out = {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}
    out["admin"] = out["email"] in config.admins()
    out["can_sign_in_again"] = bool(row["password_hash"]) or keys > 0
    return out


# ---- signing a device in from another one (the phone app, a borrowed browser) ------------------------------------
#
# A device that has no sign-in asks for one: it gets a short code to show the person, and a secret of its own to ask
# with. The person enters the code on a device already signed in, sees what is asking (the name the device gave, when,
# and from what address, against the approving browser's own), and approves it or not; the asking device then receives
# a sign-in of that account, once. The code is kept as its sha256 and lasts ten minutes; the device's secret is kept as
# its sha256 and never leaves it. An approval is the approving account's word that the device in front of the person
# is theirs: a code read out by someone else is not. It takes the person's own word as well as the sign-in (a
# confirmation, passkeys.confirm, made for that request alone): the device's sign-in lasts 7 days, so a sign-in taken
# from a browser could otherwise sign its taker's devices in, each for longer than it lasts itself.
#
# Asking needs no sign-in, so what one network may have open at once is capped (DEVICE_SIGNINS_PER_NETWORK: an IPv4
# address, or an IPv6 /64), and what a wider one may (DEVICE_SIGNINS_PER_WIDE_NETWORK: an IPv4 /24, an IPv6 /48, which
# one holder of many addresses usually has), as well as the deployment's whole (DEVICE_SIGNINS_PENDING). A request
# refused, collected or expired is no longer open. The address is the one the server sees: behind the hosted site's proxy, the client's, as
# the PROXY protocol header Fly's proxy adds gives it to Caddy (fly.toml, Caddyfile).

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no I, O, 0 or 1, which read alike
DEVICE_NAME_MOST = 60
NEEDS_CONFIRM_TO_APPROVE = ("Confirm it is you first, with one of this account's passkeys or its password: a sign-in "
                            "alone does not sign another device in. A page from an older release cannot ask for "
                            "that: reload it.")
# what confirmed a device's approval when it was the password (device_signins.confirmed_by: a passkey's id otherwise)
BY_PASSWORD = "password"


def device_name(name):
    """The name a device gives itself, as it is kept and shown: NFC, its ends trimmed, 1 to 60 letters, marks, digits,
    punctuation, symbols and plain spaces. No control or format character (the invisible ones, and those that turn the
    text around it), no private-use or unassigned one and no other kind of space, so the approver reads what is there."""
    if isinstance(name, str):
        name = unicodedata.normalize("NFC", name).strip()
        if 1 <= len(name) <= DEVICE_NAME_MOST and all(c == " " or unicodedata.category(c)[0] in "LMNPS" for c in name):
            return name
    raise AuthError("A device names itself in 1 to 60 letters, digits, punctuation, symbols and spaces.")


def network_of(address, wide=False):
    """The network an address stands for, to compare and count by: the IPv4 address itself, or an IPv6 address's
    /64 (the one network a single client is given); with `wide`, the IPv4 /24 or the IPv6 /48 around it (what one
    holder of many addresses usually has). None for anything that is not an address (a test client)."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False)) if wide else str(ip)
    return str(ipaddress.ip_network(f"{ip}/{48 if wide else 64}", strict=False))


def _code_hash(code):
    return hashlib.sha256(re.sub(r"[\s-]", "", code or "").upper().encode("ascii", "replace")).hexdigest()


_OPEN = "used_at IS NULL AND denied_at IS NULL AND expires_at > ?"


def start_device_signin(name, client="", now=None):
    """A new request to be signed in, from the address `client`: {id, code, poll, expires_at, interval, from}. `code`
    (XXXX-XXXX) is for the person, `poll` for the device alone; `from` is the address the server saw, None if it is not
    one."""
    name = device_name(name)
    now = int(now if now is not None else time.time())
    net, wide = network_of(client), network_of(client, wide=True)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # a request that gave a sign-in is kept while that sign-in lasts, which ends with it (token_user)
        db.execute("DELETE FROM device_signins WHERE expires_at <= ? AND (used_at IS NULL OR used_at <= ?)",
                   (now, now - config.TOKEN_TTL_SECONDS))
        if net is not None:
            for column, value, most in (("asked_net", net, config.DEVICE_SIGNINS_PER_NETWORK),
                                        ("asked_wide", wide, config.DEVICE_SIGNINS_PER_WIDE_NETWORK)):
                here = db.execute(f"SELECT COUNT(*) FROM device_signins WHERE {_OPEN} AND {column} = ?", (now, value))
                if here.fetchone()[0] >= most:
                    raise AuthError("This network has as many devices waiting to be signed in as it may; approve, "
                                    "refuse or wait for one of them.")
        waiting = db.execute(f"SELECT COUNT(*) FROM device_signins WHERE {_OPEN}", (now,)).fetchone()[0]
        if waiting >= config.DEVICE_SIGNINS_PENDING:
            raise AuthError("Too many devices are waiting to be signed in; try again in a few minutes.")
        for _ in range(5):
            raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            code = f"{raw[:4]}-{raw[4:]}"
            if db.execute("SELECT 1 FROM device_signins WHERE code_hash = ?", (_code_hash(code),)).fetchone() is None:
                break
        else:
            raise AuthError("No code could be made; try again.")
        request_id, poll = uuid.uuid4().hex, secrets.token_urlsafe(32)
        asked_from = client if net is not None else None
        db.execute("INSERT INTO device_signins (id, code_hash, poll_hash, name, created_at, expires_at, asked_from, "
                   "asked_net, asked_wide) VALUES (?,?,?,?,?,?,?,?,?)",
                   (request_id, _code_hash(code), _token_hash(poll), name, now, now + config.DEVICE_SIGNIN_TTL_SECONDS,
                    asked_from, net, wide))
    return {"id": request_id, "code": code, "poll": poll, "expires_at": now + config.DEVICE_SIGNIN_TTL_SECONDS,
            "interval": config.DEVICE_SIGNIN_POLL_SECONDS, "from": asked_from}


def _open_request(db, code, now):
    row = db.execute("SELECT * FROM device_signins WHERE code_hash = ?", (_code_hash(code),)).fetchone()
    if row is None or row["expires_at"] <= now or row["used_at"] is not None or row["denied_at"] is not None \
            or row["approved_by"] is not None:
        raise AuthError("No device is waiting with that code: it may have been used, or have expired.")
    return row


def device_signin_view(code, viewer="", now=None):
    """What is asking, for the person about to approve it from the address `viewer`: {name, created_at, expires_at,
    asked_from, viewer, same_network}. The name is the device's own word; the time and the address are the server's.
    `same_network` is None when either address is unknown."""
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        row = _open_request(db, code, now)
    mine = network_of(viewer)
    same = None if mine is None or row["asked_net"] is None else mine == row["asked_net"]
    return {"name": row["name"], "created_at": row["created_at"], "expires_at": row["expires_at"],
            "asked_from": row["asked_from"], "viewer": viewer if mine is not None else None, "same_network": same}


def approval_subject(code, now=None):
    """What a confirmation that approves the device waiting with `code` is made for (passkeys.confirm's `subject`):
    that request, by its id, so the confirmation approves it and nothing else. AuthError when none waits with it."""
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        return _approval_subject(_open_request(db, code, now)["id"])


def _approval_subject(request_id):
    return "device-signin:" + request_id


def decide_device_signin(user_id, code, approve, now=None, confirmation=None, approver=None):
    """The signed-in account approves the waiting device, or refuses it. Returns {name, approved}. Approving uses up,
    in the same transaction, a confirmation (passkeys.confirm) made for this request (approval_subject): the
    password's or a passkey's, a hardware key's while the account's high security is on. One that is missing,
    expired, used, another account's or made for anything else is refused and left unused, and the request waits on.
    What confirmed it is kept (`confirmed_by`: the passkey's id, or BY_PASSWORD), so removing that passkey ends the
    device's sign-in (devices_going). The desktop server's own account, which has nothing to confirm with, approves
    with the sign-in alone. `approver`, the sign-in that approves, is checked again in the transaction (SignedOut): one
    signed out everywhere since the route read it approves nothing. Refusing takes the sign-in alone."""
    from . import passkeys
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if approver is not None:
            claims = _claims(approver, now)
            if claims is None or claims["sub"] != user_id or not _live(db, claims):
                raise SignedOut("Sign in first.")
        row = _open_request(db, code, now)
        if approve:
            try:
                used = passkeys._use_confirmation(db, user_id, confirmation, now,
                                                  subject=_approval_subject(row["id"]))
            except passkeys.PasskeyError as e:
                if str(e) == passkeys.NEEDS_CONFIRM:
                    raise passkeys.PasskeyError(e.status, NEEDS_CONFIRM_TO_APPROVE) from None
                raise
            confirmed_by = None if used is None else used.passkey_id or BY_PASSWORD
            db.execute("UPDATE device_signins SET approved_by = ?, approved_at = ?, confirmed_by = ? WHERE id = ?",
                       (user_id, now, confirmed_by, row["id"]))
        else:
            db.execute("UPDATE device_signins SET denied_at = ? WHERE id = ?", (now, row["id"]))
    return {"name": row["name"], "approved": bool(approve)}


def devices_going(db, user_id, which, args, now, end=True):
    """Inside a change's own transaction: the account's device sign-ins whose approval what `which` names confirmed
    (SQL on device_signins.confirmed_by, with `args`). Each approved that has not collected its sign-in is refused,
    and its poll says its approval was withdrawn; with `end`, each that has collected one is ended, and token_user
    refuses that sign-in from then. (refused, ended)."""
    refused = db.execute("UPDATE device_signins SET denied_at = ? WHERE approved_by = ? AND used_at IS NULL AND "
                         f"denied_at IS NULL AND {which}", (now, user_id, *args)).rowcount
    ended = db.execute("UPDATE device_signins SET ended_at = ? WHERE approved_by = ? AND used_at IS NOT NULL AND "
                       f"ended_at IS NULL AND {which}", (now, user_id, *args)).rowcount if end else 0
    return refused, ended


def poll_device_signin(request_id, poll, now=None):
    """The asking device's question: {"status": "waiting" | "refused" | "expired"}, a refusal of an approval
    withdrawn before it was collected saying so (`withdrawn`: signing out everywhere, removing the passkey that
    confirmed it, high security turned on), or, once approved, a sign-in of the approving account, {"token", "user"},
    given once and made in the transaction that marks it given, of the generation that transaction reads: a sign out
    everywhere either refused it first or ends it after. At most once every DEVICE_SIGNIN_POLL_SECONDS."""
    now = int(now if now is not None else time.time())
    if not isinstance(request_id, str) or not isinstance(poll, str):
        raise AuthError("Ask with the request's id and its secret.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM device_signins WHERE id = ?", (request_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["poll_hash"], _token_hash(poll)):
            raise AuthError("No such request.")
        if row["last_poll_at"] is not None and now - row["last_poll_at"] < config.DEVICE_SIGNIN_POLL_SECONDS:
            return {"status": "slow down"}
        db.execute("UPDATE device_signins SET last_poll_at = ? WHERE id = ?", (now, request_id))
        if row["used_at"] is not None:
            return {"status": "used"}
        if row["denied_at"] is not None:
            return {"status": "refused", "withdrawn": True} if row["approved_by"] is not None else {"status": "refused"}
        if row["expires_at"] <= now:
            return {"status": "expired"}           # approved too, a sign-in is collected within the request's time
        if row["approved_by"] is None:
            return {"status": "waiting"}
        db.execute("UPDATE device_signins SET used_at = ? WHERE id = ?", (now, request_id))
        user_id = row["approved_by"]
        gen = db.execute("SELECT token_gen FROM users WHERE id = ?", (user_id,)).fetchone()
        token = issue_token(user_id, now, gen=gen["token_gen"] if gen else 0, device=request_id)
    return {"status": "approved", "token": token, "user": user(user_id)}
