"""What needs the owner reaches them when no page is open, as Web Push.

A browser that allows notifications subscribes with a push service (Google's, Mozilla's, Apple's or Microsoft's) and
gives the ADE the endpoint, its P-256 public key and a secret. The ADE encrypts each message to that key (RFC 8291,
`aes128gcm`), so the push service carries it without being able to read it, and signs its request with a key of its
own (VAPID, RFC 8292), made on the data volume on first use like the signing key. Only an HTTPS endpoint at one of the
known push services is accepted, so a subscription cannot make the app send requests anywhere else.

A notifier runs in the app: every minute it gathers, for each account with a subscription, the approvals waiting for
it, the board alerts that need a person (silence, an unread change, a STOP), the session channel's alerts, what waits
in its workspace's sessions when the workspace runs, the connected apps' proposals, and the agents' runs and triggers
that need the owner, and sends each one once. An item that goes away and comes back is new again. At most MAX_PER_TICK
messages go to an account at once; more become one that says how many. A subscription the push service says is gone
(404, 410) is dropped.

A notice carries no content: only what kind of thing waits and where to look, in fixed words (words, from WORDS), with
a time or a count where its words say one. It goes through a push service, shows on a lock screen, and reaches every
browser the account subscribed, which may be one someone else signed in on; with end-to-end encryption on, the server
cannot read sealed words, and puts none in a notice. So no notice carries a word of a question, a session, an agent, a
board, an app or a device, nor a name, a command or an address: the page, signed in, shows them. The senders take only
what words makes, and a notice's tag, by which a browser shows a newer notice of the same thing in place of the last,
is a digest of the item's key keyed with a secret of the deployment's (an HMAC), so a browser that hears it cannot
turn it back into the key, which may hold a name, an id or a sequence number.

A change to how the account signs in (a passkey added or removed, another device approved, high security turned on or
off, every device signed out, apps let connect) is told to every device of the account at once, apart from the
notifier (notice), so a change the owner did not make shows on their own devices: a sign-in someone else holds cannot
take a subscription away, as it would have to know the browser's endpoint. An approval that waits before it takes
effect (ade/holds.py) is told the same way, and the notifier's rounds also apply each such approval whose time has
come.

Agent tasks (ade/tasks.py) and teammates (ade/task_offers.py) tell of their events the same way, each
after its transaction commits, in fixed words of their own kinds in WORDS: a task that
continued, was relayed to another of the owner's accounts or crossed into another organisation, an offer, a hand-off,
a return, a request to be a custodian and a custody suspended. A task that waits on its owner is one of the
notifier's items instead. None of their words names a task, a goal, a step, an account, a backend or a teammate.
"""

import base64
import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import config, store

PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com")
MAX_ENDPOINT = 1000
MAX_PER_TICK = 5
MAX_WORKSPACE_ITEMS = 100   # what one workspace's sessions add to a round of the notifier, at most
MAX_SUBSCRIPTIONS = 10
TTL = 12 * 3600
BOARD_EVERY = 5 * 60      # how often a connection's board is read for alerts, in seconds
RECORD_SIZE = 4096
_lock = threading.Lock()


class PushError(ValueError):
    """A subscription the ADE will not keep: shown as it is."""


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---- keys -------------------------------------------------------------------------------------------------------

def _key_path():
    return os.path.join(config.data_dir(), "vapid.pem")


def vapid_private_key():
    """The deployment's VAPID key, made on first use in the data directory, readable by its owner only."""
    path = _key_path()
    with _lock:
        if not os.path.exists(path):
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
        with open(path, "rb") as fh:
            return serialization.load_pem_private_key(fh.read(), password=None)


def _point(public_key):
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_public_b64():
    """The VAPID public key as a browser's `applicationServerKey` takes it: the uncompressed point, base64url."""
    return b64u(_point(vapid_private_key().public_key()))


def vapid_authorization(endpoint, now, key=None, subject=None):
    """The `Authorization` header for one request to `endpoint`: a JWT signed with ES256 whose audience is the push
    service's origin, good for twelve hours, and the public key it verifies with."""
    key = key or vapid_private_key()
    parts = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now) + TTL,
              "sub": subject or config.public_url()}
    signing_input = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." +
                     b64u(json.dumps(claims, separators=(",", ":")).encode()))
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64u(_point(key.public_key()))}"


# ---- encryption (RFC 8291) --------------------------------------------------------------------------------------

def _hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(plaintext, ua_public_b64, auth_b64, as_private=None, salt=None):
    """The `aes128gcm` body that carries `plaintext` to the browser whose key is `ua_public_b64`, as one record.
    `as_private` and `salt` are fresh for every message; a test passes fixed ones to check against RFC 8291."""
    ua_public = unb64u(ua_public_b64)
    auth = unb64u(auth_b64)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or secrets.token_bytes(16)
    as_public = _point(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth, ecdh_secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    if len(record) + 86 > RECORD_SIZE:
        raise PushError("A message is at most about 4 KB.")
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + record


# ---- subscriptions ----------------------------------------------------------------------------------------------

def check_endpoint(url):
    """`url` when it is an HTTPS endpoint of a known push service, else PushError."""
    if not isinstance(url, str) or len(url) > MAX_ENDPOINT:
        raise PushError("That is not a push service's address.")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    known = host in PUSH_HOSTS or any(host.endswith(s) for s in PUSH_SUFFIXES)
    try:
        port = parts.port  # a port that is not a number from 0 to 65535 raises here
    except ValueError:
        port = -1
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not known:
        raise PushError("Notifications go only to Google's, Mozilla's, Apple's or Microsoft's push service, over HTTPS.")
    return url


def _check_keys(p256dh, auth):
    try:
        point = unb64u(p256dh)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        secret = unb64u(auth)
    except (ValueError, TypeError):
        raise PushError("The subscription's keys are not a browser's.") from None
    if len(point) != 65 or len(secret) != 16:
        raise PushError("The subscription's keys are not a browser's.")


def subscribe(user_id, endpoint, p256dh, auth, now=None):
    """Keep one browser's subscription for the account; the same endpoint subscribing again replaces its keys."""
    check_endpoint(endpoint)
    _check_keys(p256dh, auth)
    now = int(now if now is not None else time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT user_id FROM ade_push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        if row is not None and row["user_id"] != user_id:
            db.execute("DELETE FROM ade_push_subscriptions WHERE endpoint = ?", (endpoint,))
            row = None
        if row is None:
            count = db.execute("SELECT COUNT(*) FROM ade_push_subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_SUBSCRIPTIONS:
                raise PushError(f"An account has at most {MAX_SUBSCRIPTIONS} devices with notifications.")
            db.execute("INSERT INTO ade_push_subscriptions (id, user_id, endpoint, p256dh, auth, created_at, failures) "
                       "VALUES (?,?,?,?,?,?,0)", (uuid.uuid4().hex, user_id, endpoint, p256dh, auth, now))
        else:
            db.execute("UPDATE ade_push_subscriptions SET p256dh = ?, auth = ?, failures = 0 WHERE endpoint = ?",
                       (p256dh, auth, endpoint))


def unsubscribe(user_id, endpoint):
    with store.connect() as db:
        return bool(db.execute("DELETE FROM ade_push_subscriptions WHERE user_id = ? AND endpoint = ?",
                               (user_id, endpoint)).rowcount)


def subscriptions(user_id):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM ade_push_subscriptions WHERE user_id = ? ORDER BY created_at",
                          (user_id,)).fetchall()
    return [dict(r) for r in rows]


def list_for(user_id):
    """The account's devices as the page shows them: the push service and when, never the keys."""
    return [{"service": urllib.parse.urlsplit(s["endpoint"]).hostname, "created_at": s["created_at"],
             "last_sent_at": s["last_sent_at"], "endpoint_hash": hashlib.sha256(s["endpoint"].encode()).hexdigest()[:16]}
            for s in subscriptions(user_id)]


# ---- the words of a notice --------------------------------------------------------------------------------------

TITLE = "Proofpane Community"
WAITS = "Takes effect at {at} unless you cancel it."
# held for no time: told as the approval is placed, before it is applied (holds.settle), which may still refuse it, so
# the words say what was approved and when, never that it took effect
IN_EFFECT = "Approved at {at}, to take effect at once: this deployment holds no approval."

# Every notice's words, by its kind: (title, body, the page it opens). A body says at most a time ({at}, as holds.utc
# writes it) or a count ({count}); nothing else is ever put into one (words).
WORDS = {
    # the notifier's items (items_for), each sent once
    "approval": ("A command waits for you", "Open Approvals.", "/app/#approvals"),
    "board": ("A board needs you", "Open the ADE.", "/#ade"),
    "link-question": ("A question waits for you", "Open Linked sessions to read it.", "/app/#channel"),
    # a session's open waiting to the owner that refers to no command: it says only that one waits on them
    "link-waiting": ("A session waits for you", "Open Linked sessions.", "/app/#channel"),
    "link-look": ("A linked session needs a look", "Open Linked sessions.", "/app/#channel"),
    "link-ask": ("A session asks to be linked", "Open Linked sessions to see it.", "/app/#channel"),
    # a linked session's proposal of a message for another: never who proposed it,
    # for whom, or a word of it; a session's own prompt is told as a question is ("link-question")
    "link-proposal": ("A proposed message waits for you", "Open Approvals to read it.", "/app/#approvals"),
    "workspace": ("A session in your workspace waits for you", "Open the ADE.", "/app/"),
    "workspace-ask": ("A session in your workspace waits for you", "Open Approvals.", "/app/#approvals"),
    "proposal": ("A proposal waits for you", "Open Approvals.", "/app/#proposals"),
    "agent-waits": ("An agent waits for you", "Open Agents.", "/app/#agents"),
    "agent-failed": ("An agent's run failed", "Open Agents.", "/app/#agents"),
    "agent-look": ("An agent needs a look", "Open Agents.", "/app/#agents"),
    "schedule-missed": ("An agent's schedule did not start a run", "Open Agents.", "/app/#agents"),
    # the controller's (ade/controller.py): its open question, and a session it started outside its bounds or an action
    # of its the app did not take; the control view is the main panel's default
    "controller-question": ("The Manager asks you something", "Open the control view to read it.", "/app/"),
    "controller-look": ("The Manager needs a look", "Open the control view.", "/app/"),
    # a requirement's own manager: one done or needing its owner, one that
    # has waited WAIT_TOLD for a place, and a start of the controller's own the cap refused that long ago
    "controller-manager": ("A requirement's manager needs you", "Open the control view.", "/app/"),
    "controller-manager-waits": ("A requirement's manager waits for a place",
                                 "Open the control view: free a place, then Resume it.", "/app/"),
    "controller-cap-waits": ("The Manager waits for a place",
                             "Open the control view: free a place, then tell the Manager.", "/app/"),
    "hook-missed": ("An agent's hook did not start a run", "Open Agents.", "/app/#agents"),
    "more": (TITLE, "{count} more things need you in the ADE.", "/#ade"),
    "test": (TITLE, "Notifications reach this device.", "/app/"),
    # a change to how the account signs in, told at once (notice)
    "device-signed-in": ("A device was signed in", "Not you? Open Account and sign out everywhere.", "/app/#account"),
    "passkey-added": ("A passkey was added to your account", "Not you? Open Account, remove it, then sign out "
                      "everywhere.", "/app/#account"),
    "passkey-removed": ("A passkey was removed from your account", "Not you? Open Account and sign out everywhere.",
                        "/app/#account"),
    "passkey-removed-apps": ("A passkey was removed from your account", "The app connections it allowed were revoked. "
                             "Not you? Open Account and sign out everywhere.", "/app/#apps"),
    "passkey-renamed": ("A passkey of your account was renamed", "Not you? Open Account and sign out everywhere.",
                        "/app/#account"),
    "high-security-on": ("High security was turned on", "Not you? Open Account and sign out everywhere.",
                         "/app/#account"),
    "high-security-off": ("High security was turned off", "Not you? Open Account and sign out everywhere.",
                          "/app/#account"),
    "signed-out": ("Every device was signed out", "The device that asked was signed in again. Not you? Sign in and "
                   "sign out everywhere again.", "/app/#account"),
    "signed-out-apps": ("Every device was signed out", "The device that asked was signed in again; connected apps keep "
                        "working until you revoke them. Not you? Sign in and sign out everywhere again.", "/app/#apps"),
    "apps-on": ("Letting apps connect was turned on", "Each app still needs your Allow. Not you? Turn it off in "
                "Connected apps.", "/app/#apps"),
    "app-disconnected": ("A connected app was disconnected", "Open Connected apps.", "/app/#apps"),
    "hardware-key-reported": ("A hardware key was reported", "Open Account.", "/app/#account"),
    # stored texts sealed again to a post-quantum key with the recovery code's proof (ade/e2e_keys.py reseal), told at
    # once: it rewrites what is stored, and only the code makes it
    "e2e-resealed": ("Stored texts were sealed again", "{count} sealed again to your post-quantum key, with your "
                     "recovery code. Not you? Your recovery code is known: open Account.", "/app/#account"),
    # an agent's task (ade/tasks.py), told at once after its change commits (notice): an automatic
    # continuation, a relay step to another of the owner's accounts, and one into another organisation; and a task
    # that needs its owner, one of the notifier's items (tasks.alerts, through agents.alerts). None names the task,
    # its goal, a step, an account or a backend: the page, signed in, shows them
    "task-continued": ("A task of yours continued", "Open Agents.", "/app/#agents"),
    "task-relayed": ("A task of yours continued on another of your accounts", "Open Agents.", "/app/#agents"),
    "task-crossed": ("A task of yours continued in another organisation", "Open Agents.", "/app/#agents"),
    "task-needs-you": ("A task of yours needs a decision", "Open Agents.", "/app/#agents"),
    # the teammate track (ade/task_offers.py), told at once to the account it concerns: an offer to a
    # teammate, a task handed to a custodian, a task come back to its owner, a request to be a custodian, and custody
    # suspended by a change to the custodian's receiving agent. None names the teammate, the task, a step or an account
    "task-offered": ("A teammate offered you a task", "Open Agents.", "/app/#agents"),
    "task-handed": ("A task was handed to you", "Open Agents.", "/app/#agents"),
    "task-returned": ("A task of yours came back from your teammate", "Open Agents.", "/app/#agents"),
    "custody-asked": ("A teammate asks you to be their custodian", "Open Account.", "/app/#account"),
    "custody-suspended": ("Your custody was suspended", "Open Account.", "/app/#account"),
    # an approval held before it takes effect (ade/holds.py), told at once: what it holds, as its kind names it
    # (holds.register: notice), and when it takes effect, or, held for no time, that it is in effect
    "held-link": ("A link waits to be made", WAITS, "/app/#channel"),
    "held-link-now": ("A link was approved", IN_EFFECT, "/app/#channel"),
    "held-link-replace": ("A link waits to replace another", WAITS, "/app/#channel"),
    "held-link-replace-now": ("A link to replace another was approved", IN_EFFECT, "/app/#channel"),
    # a session paired by a ticket, and a linked machine moved to a new key by one (ade/link.py pair)
    "held-pair": ("A session waits to be linked", "Linked at {at} unless you cancel it in Linked sessions.",
                  "/app/#channel"),
    "held-pair-now": ("A session was approved to be linked", IN_EFFECT, "/app/#channel"),
    "held-pair-again": ("A linked machine waits to move to a new key",
                        "Moved at {at} unless you cancel it in Linked sessions.", "/app/#channel"),
    "held-pair-again-now": ("A linked machine was approved to move to a new key", IN_EFFECT, "/app/#channel"),
    "held-move": ("A move waits", WAITS, "/app/#channel"),
    "held-move-now": ("A move was signed", IN_EFFECT, "/app/#channel"),
    "held-app": ("An app's connection waits", WAITS, "/app/#apps"),
    "held-app-now": ("An app was allowed to connect", IN_EFFECT, "/app/#apps"),
    "held-e2e": ("A change to encryption waits", WAITS, "/app/#account"),
    # one made with the recovery code, which only the code cancels (ade/e2e_keys.py cancellers)
    "held-e2e-code": ("A change to encryption waits", WAITS + " Made with your recovery code: only the recovery code "
                      "cancels it.", "/app/#account"),
    "held-e2e-now": ("A change to encryption was approved", IN_EFFECT, "/app/#account"),
    # a task's holds: an account confirmed as the owner's own (task.account_own) and a task's "Cross
    # organisations" switch turned back on (task.cross), in ade/tasks.py; custody's own crossing ticked (task.crossing)
    # and an offer or a custody hand-off (task.offer), in ade/task_offers.py. None names the account, the task or the
    # teammate. Each kind's notice is a callable that gives held-other-now for a hold of no time, so none has a -now
    # of its own
    "held-account": ("An account waits to join your tasks' relay", WAITS, "/app/"),
    "held-task-cross": ("A task waits to cross organisations again", WAITS, "/app/#agents"),
    "held-task-crossing": ("A task's custody crossing waits", WAITS, "/app/#agents"),
    "held-task-offer": ("An offer of a task waits", WAITS, "/app/#agents"),
    "held-task-custody": ("A task of yours goes to your custodian", "Goes to them at {at} unless you cancel it.",
                          "/app/#agents"),
    "held-other": ("An approval waits", WAITS, "/app/"),
    "held-other-now": ("An approval was given", IN_EFFECT, "/app/"),
}
_FACTS = re.compile(r"\{([a-z]+)\}")
_ID = re.compile(r"[0-9a-f]{12}")      # a workspace session's id, and an ask's (the workspace agent's): fullmatch
# a chat's question's id, as the chat gives it (Claude Code's tool_use_id, or ask-N): ASCII, so a key made with it is
# always text words takes
_CHAT_ASK = re.compile(r"[A-Za-z0-9_-]{1,80}")


_MADE = object()        # what words puts in each notice it makes: send and notice take no other


@dataclasses.dataclass(frozen=True)
class Words:
    """A notice as words made it, and all that a sender takes (send, notice, notify): its title, its body, its tag and
    the page it opens. One made otherwise lacks words' mark (`made`), and nothing sends it. The mark is no argument of
    the constructor, so a copy made with other fields (dataclasses.replace, copy.replace) is made without it."""
    title: str
    body: str
    tag: str
    url: str
    made: object = dataclasses.field(default=None, repr=False, compare=False, init=False)


def _made(message):
    """Whether `message` is a notice words made."""
    return isinstance(message, Words) and message.made is _MADE


def _tag_key():
    """The key of notices' tags (words), derived from the deployment's token secret for this use alone."""
    return hmac.new(config.token_secret(), b"proofpane push notice tag v1", hashlib.sha256).digest()


def words(kind, key=None, at=None, count=None, session=None):
    """The notice of `kind` (WORDS), from its fixed words alone, with no more than these facts of the server's, each
    where its words take it: `at`, a time; `count`, how many; `session`, the id of the workspace session a workspace
    notice opens. Its tag is an HMAC of `key`, what the notice is about as the notifier keeps it, under a key derived
    from the deployment's token secret (_tag_key), so a browser shows a newer notice of the same thing in place of the
    last, and one that hears it cannot learn the key, which may hold a name, an id or a sequence number, by trying keys;
    with no key the tag is new. A kind that is not one, a fact its words do not take or lack, and a fact of another
    form are refused (ValueError): nothing else reaches a notice."""
    from .holds import utc
    if kind not in WORDS:
        raise ValueError("That is not a kind of notice.")        # never said back: it may be words that are not one
    title, body, url = WORDS[kind]
    takes = set(_FACTS.findall(body)) | ({"session"} if kind == "workspace" else set())
    given = {name for name, value in (("at", at), ("count", count), ("session", session)) if value is not None}
    if given != takes:
        raise ValueError(f"A {kind} notice takes {', '.join(sorted(takes)) or 'no fact'}.")
    for value in (at, count):
        if value is not None and (type(value) is not int or value < 0):       # never a bool, never text
            raise ValueError("A notice's time or count is a whole number.")
    if session is not None and not (isinstance(session, str) and _ID.fullmatch(session)):
        raise ValueError("A notice opens a workspace session by its id alone.")
    if key is not None and not isinstance(key, str):
        raise ValueError("A notice's key is text.")
    digest = hmac.new(_tag_key(), key.encode("utf-8"), hashlib.sha256).hexdigest() if key is not None else \
        uuid.uuid4().hex
    made = Words(title, body.format(at=utc(at) if at is not None else None, count=count), "ade-" + digest[:16],
                 url + (f"#session={session}" if session is not None else ""))
    object.__setattr__(made, "made", _MADE)
    return made


# ---- sending ----------------------------------------------------------------------------------------------------

def _default_post(url, headers, body):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def send(subscription, message, now=None, post=None):
    """Encrypt `message`, a notice words made, to one subscription and post it: its title, body, tag and page.
    Anything else is refused (TypeError), so nothing but the fixed words reaches a push service. Returns the push
    service's status, or None when it could not be reached. A subscription it says is gone is dropped."""
    if not _made(message):
        raise TypeError("A notice is what push.words makes.")
    now = int(now if now is not None else time.time())
    post = post or _default_post
    sent = {"title": message.title, "body": message.body, "tag": message.tag, "url": message.url}
    body = encrypt(json.dumps(sent, separators=(",", ":")).encode("utf-8"), subscription["p256dh"],
                   subscription["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL),
               "Urgency": "high", "Authorization": vapid_authorization(check_endpoint(subscription["endpoint"]), now)}
    status = post(subscription["endpoint"], headers, body)
    with store.connect() as db:
        if status in (404, 410):
            db.execute("DELETE FROM ade_push_subscriptions WHERE id = ?", (subscription["id"],))
        elif status is not None and 200 <= status < 300:
            db.execute("UPDATE ade_push_subscriptions SET last_sent_at = ?, failures = 0 WHERE id = ?",
                       (now, subscription["id"]))
        else:
            db.execute("UPDATE ade_push_subscriptions SET failures = failures + 1 WHERE id = ?", (subscription["id"],))
    return status


# ---- told at once -----------------------------------------------------------------------------------------------

def _spawn(work):
    threading.Thread(target=work, name="push-notice", daemon=True).start()


def notice(user_id, message, now=None, post=None):
    """Tell every device of the account once, at once, apart from the notifier: `message`, a notice words made
    (anything else is refused, TypeError), of a change to how the account signs in, of an approval held before it
    takes effect (ade/holds.py), or of an agent task's or a teammate's event (ade/tasks.py and
    ade/task_offers.py, after their transactions commit). Sent from a thread of its own, so the request that made the
    change does not wait on the push services; a device that cannot be reached is not tried again. A caller calls it
    with no transaction of its own open, since the thread writes ade_push_subscriptions as it sends. Returns how many
    devices it is sent toward (not how many it reaches), or None when the account's devices could not be read or its
    thread could not start: telling of a change never fails the request that made it."""
    if not _made(message):
        raise TypeError("A notice is what push.words makes.")
    try:
        devices = subscriptions(user_id)
    except Exception as e:  # noqa: BLE001  the change is made; telling of it never fails the request that made it
        print(f"push: the account's devices could not be read: {type(e).__name__}", file=sys.stderr)
        return None

    def work():
        for sub in devices:
            try:
                send(sub, message, now, post)
            except Exception as e:  # noqa: BLE001  one device failing never stops the others
                print(f"push: a notice could not be told: {type(e).__name__}", file=sys.stderr)
    if devices:
        try:
            _spawn(work)
        except Exception as e:  # noqa: BLE001  the change is made; telling of it never fails the request that made it
            print(f"push: a notice could not be told: {type(e).__name__}", file=sys.stderr)
            return None
    return len(devices)


# ---- the notifier -----------------------------------------------------------------------------------------------

# the agents by their programs' names, as the connector names a session's program (ade/connector.py)
AGENT_NAMES = {"claude": "Claude Code", "gemini": "Gemini CLI", "codex": "Codex", "opencode": "OpenCode", "grok": "Grok Build",
               "qwen": "Qwen Code", "kimi": "Kimi Code", "mcode": "MiniMax Code"}


def _item(key, kind, **facts):
    """One of the notifier's items: its key, and its notice, in the words of `kind` (words)."""
    return key, words(kind, key=key, **facts)


def workspace_asks(user_id):
    """What waits for the owner in the account's sessions, when the account's workspace already runs: a chat's
    questions (proofpane_workspace/chat.py) and a terminal agent that says, through its own hooks, that it waits
    (activity.py: a permission, or its input), once for each time it says so, each opening the app at its session; and
    a new session an agent asked for (asks.py), which opens Approvals. Each in the fixed words of a session that waits
    (words), with nothing the agent, its program or the person wrote. Nothing is started for them, and a GET is no
    activity to the agent, so a workspace still stops when it is idle. A workspace that is off here, not the account's,
    stopped or not answering says nothing this round."""
    from .. import auth
    from . import workspace_client, workspaces
    try:
        user = auth.user(user_id)
        if not user:
            return []
        p = workspaces.for_user(user)
        endpoint = p.endpoint(user)
        if endpoint is None:
            return []
        sessions = workspace_client.call(endpoint, "GET", "/sessions", timeout=20).get("sessions") or []
    except workspaces.WorkspaceAccess:
        return []
    except Exception as e:  # noqa: BLE001  a workspace that cannot be read never stops the other items
        print(f"push: the workspace of {user_id} could not be read: {type(e).__name__}", file=sys.stderr)
        return []
    waiting = []        # (key, session) of each that waits, as the workspace lists them
    for s in sessions:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not _ID.fullmatch(s["id"]):
            continue
        chat = s.get("chat")
        if isinstance(chat, dict):
            for a in chat.get("asks") or []:
                if isinstance(a, dict) and isinstance(a.get("id"), str) and _CHAT_ASK.fullmatch(a["id"]):
                    waiting.append((f"chat-ask:{s['id']}:{a['id']}", s["id"]))
        activity = s.get("activity")
        if s.get("running") is True and isinstance(activity, dict) and activity.get("state") == "waiting" \
                and isinstance(activity.get("at"), int) and not isinstance(activity.get("at"), bool):
            waiting.append((f"waiting:{s['id']}:{activity['at']}", s["id"]))
    try:
        asked = workspace_client.call(endpoint, "GET", "/asks", timeout=20).get("asks") or []
    except Exception:  # noqa: BLE001  an agent from before asks, or one not answering now: none this round
        asked = []
    asks = [a["id"] for a in (asked if isinstance(asked, list) else [])
            if isinstance(a, dict) and isinstance(a.get("id"), str) and _ID.fullmatch(a["id"])]
    # a workspace that lists more than a round can tell (MAX_PER_TICK) adds at most MAX_WORKSPACE_ITEMS to it, so one
    # that lists thousands costs the round no more than that
    waiting = waiting[:MAX_WORKSPACE_ITEMS]
    return [_item(key, "workspace", session=session) for key, session in waiting] + \
        [_item("session-ask:" + a, "workspace-ask") for a in asks[:MAX_WORKSPACE_ITEMS - len(waiting)]]


def items_for(user_id, board_alerts, now):
    """What needs the account now, each (key, notice): a key that stays the same while it lasts, and its notice in fixed
    words (words). The approvals waiting for it, the board alerts that need a person, the session channel's alerts
    (link.alerts), what waits in its workspace's sessions (workspace_asks), the connected apps' proposals, and the
    agents' runs and triggers that need the owner; each source gives a key and the kind of its notice, never words of
    its own."""
    from . import agents, approvals, controller, link, proposals, triggers
    items = [_item("approval:" + r["id"], "approval") for r in approvals.list_for_owner(user_id, now=now)
             if r["state"] == "pending"]
    for repo_id, alert in board_alerts:
        items.append(_item(":".join(str(x) for x in ("alert", repo_id, alert.get("kind"), alert.get("channel"),
                                                     alert.get("file"))), "board"))
    items.extend(_item(key, kind) for key, kind in link.alerts(user_id, now))
    items.extend(workspace_asks(user_id))
    items.extend(_item(key, kind) for key, kind in proposals.alerts(user_id, now))
    items.extend(_item(key, kind) for key, kind in agents.alerts(user_id, now))
    items.extend(_item(key, kind) for key, kind in triggers.alerts(user_id))
    items.extend(_item(key, kind) for key, kind in controller.alerts(user_id, now))
    return items


def notify(user_id, items, now=None, post=None):
    """Send each item not sent before (its notice, as items_for makes it) to every device of the account, and forget the
    items that are gone, so one that comes back is new again. Returns how many messages were sent."""
    now = int(now if now is not None else time.time())
    keys = [k for k, _ in items]
    with store.connect() as db:
        seen = {r["key"] for r in db.execute("SELECT key FROM ade_push_seen WHERE user_id = ?", (user_id,))}
        # the items gone since, each by its own key: a statement with a variable for every item would fail on a list
        # longer than SQLite's limit of variables
        db.executemany("DELETE FROM ade_push_seen WHERE user_id = ? AND key = ?",
                       [(user_id, k) for k in seen - set(keys)])
    new = [(k, m) for k, m in items if k not in seen]
    if not new:
        return 0
    if len(new) > MAX_PER_TICK:
        messages = [m for _, m in new[:MAX_PER_TICK - 1]] + [words("more", key="more",
                                                                   count=len(new) - MAX_PER_TICK + 1)]
    else:
        messages = [m for _, m in new]
    sent = 0
    for sub in subscriptions(user_id):
        for m in messages:
            if send(sub, m, now, post) in (200, 201, 202):
                sent += 1
    with store.connect() as db:
        db.executemany("INSERT OR IGNORE INTO ade_push_seen (user_id, key, sent_at) VALUES (?,?,?)",
                       [(user_id, k, now) for k, _ in new])
    return sent


_board_cache = {}


def board_alerts(user_id, now, build=None):
    """The alerts that need a person on each of the account's boards, each board read at most every BOARD_EVERY
    seconds. A board that cannot be read (a token GitHub refused, the rate limit) is left out this time."""
    from datetime import datetime, timezone
    from . import board, ledger
    out = []
    for conn in ledger.list_for(user_id):
        cached = _board_cache.get(conn["id"])
        if cached and now - cached[0] < BOARD_EVERY:
            alerts = cached[1]
        else:
            try:
                if build is not None:
                    b = build(conn)
                else:
                    source = ledger.source_for(user_id, conn["id"])
                    b = board.build_board(conn, source, datetime.fromtimestamp(now, tz=timezone.utc))
                alerts = [a for a in b["alerts"] if a.get("level") == "alert"]
            except Exception as e:  # noqa: BLE001  one board failing never stops the others
                print(f"push: the board of {conn['id']} could not be read: {type(e).__name__}", file=sys.stderr)
                continue
            _board_cache[conn["id"]] = (now, alerts)
        out.extend((conn["id"], a) for a in alerts)
    return out


def tick(now=None, post=None, build=None):
    """One round of the notifier, for every account with a subscription. Returns {user id: messages sent}, None for an
    account whose round failed, which stops no other account's. Each held approval whose time has come takes effect
    first, whoever has a subscription, so one takes effect within a round of its time even while nothing reads it; and
    every account's connected apps' proposals are pruned (proposals.prune), so their words go within a round of their
    day."""
    now = int(now if now is not None else time.time())
    from . import holds, proposals
    try:
        holds.settle(now=now)
    except Exception as e:  # noqa: BLE001  the notifier never stops for it: the next read or round settles again
        print(f"push: the holds could not be settled: {type(e).__name__}", file=sys.stderr)
    try:
        # connected apps' proposals: the words of those decided a day ago cleared, rows of 90 days ago deleted
        proposals.prune(now)
    except Exception as e:  # noqa: BLE001  the next round prunes again
        print(f"push: the proposals could not be pruned: {type(e).__name__}", file=sys.stderr)
    with store.connect() as db:
        users = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM ade_push_subscriptions")]
    sent = {}
    for u in users:
        try:
            sent[u] = notify(u, items_for(u, board_alerts(u, now, build), now), now, post)
        except Exception as e:  # noqa: BLE001  one account's items, its workspace's among them, never stop another's
            print(f"push: the round of {u} failed: {type(e).__name__}", file=sys.stderr)
            sent[u] = None
    return sent


def start(every=None):
    """Run the notifier in a thread of this process, every `every` seconds (OPEN_EDITION_ADE_PUSH_EVERY, default
    60; 0 turns it off). The app runs one process, so there is one notifier."""
    every = int(os.environ.get("OPEN_EDITION_ADE_PUSH_EVERY", "60") if every is None else every)
    if every <= 0:
        return None

    def loop():
        while True:
            time.sleep(every)
            try:
                tick()
            except Exception as e:  # noqa: BLE001  the notifier never takes the app down
                print(f"push: a round failed: {type(e).__name__}: {e}", file=sys.stderr)

    thread = threading.Thread(target=loop, name="ade-push", daemon=True)
    thread.start()
    return thread
