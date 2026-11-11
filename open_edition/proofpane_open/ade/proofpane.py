"""An account's pairing with its Proofpane organisation: the app holds one per
account, so that the usage of the account's linked sessions reaches Proofpane's meter (ade/link_usage.py sends it).
The hosted workspace's own pairing (proofpane_workspace/core_plugin.py, its Connections card) is another, the
workspace agent's; this one is the app's, and nothing of it is given to the workspace.

- Pairing, as the workspace pairs and Proofpane's daemon pairs a computer: the person makes a pairing code in their
  Proofpane organisation (Devices) and gives it on the page, with one of the addresses this deployment names
  (OPEN_EDITION_PROOFPANE_ADDRESSES, config.proofpane_addresses: unset, `https://api.proofpane.com` alone), the first
  of them when none is given. An address it does not name is refused before anything is sent, whatever its form: it
  is this server that sends the code there and later posts the usage there, so a typed address would have it send
  requests wherever it said, this machine and its private network among them. Those named are kept only where they
  have the form an address may have (https with a host and at most a port, or http to this machine, the workspace's
  own rule). The app sends {code, name, platform} to `<address>/api/v1/devices/pair`, a redirect refused and never
  followed, and keeps the device's id and token from the answer.
- Kept sealed as a repository's token is (ade/ledger.py): the device's id and token together, with AES-256-GCM under
  the key-store key, `OPEN_EDITION_KEYSTORE_KEY`, the box naming the account and the pairing as its associated data, so
  it opens only in its own row. Without that key no account is paired, and no code is sent. The device's id, its name,
  the address and when it was paired are kept beside the box as well, for the page; the token is in the box alone and
  leaves it only for the request that uses it (link_usage.py), never in an answer, a log line or a row of the app.
- One pairing per account: pairing again replaces it, and unpairing removes it, with the usage that waits to go with it
  (dropped, and said). Neither revokes anything in Proofpane, as the daemon's own unpair and the workspace's do not:
  the device the old pairing made stays there until an admin of the organisation revokes it (Devices).

What leaves the app here: the code, the device's name ("Proofpane Community at" and this deployment's host) and its
platform ("proofpane-community"), once, to the address given; and, through send_event, what link_usage.py sends.
"""

import base64
import binascii
import http.client
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import config, store
from . import ledger

DEFAULT_CLOUD = "https://api.proofpane.com"
TIMEOUT = 20                # seconds the exchange of a code may take
PLATFORM = "proofpane-community"
ANSWER_MAX = 256 * 1024
_BOX = "v1."
# the workspace's own rules for an address, a code, a device and its token (proofpane_workspace/core_plugin.py)
_CLOUD = re.compile(r"^(https://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?|http://(?:127\.0\.0\.1|localhost|\[::1\])"
                    r"(?::[0-9]{1,5})?)/?$")
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,38}[A-Z0-9]$")
_DEVICE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")
_WHEN = re.compile(r"^[0-9T:.+Z-]{10,40}$")

NO_KEY = ("This deployment keeps no key-store key, so it cannot keep a pairing's token: no account is paired with "
          "Proofpane here.")
UNOPENED = ("The pairing's token cannot be opened (the key-store key may have changed): unpair and pair again with a "
            "new code.")


class PairingError(ledger.LedgerError):
    """What the person can put right, or what Proofpane said: shown as it is (400, as the ledger's errors are)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is refused, never followed: the code and the token go in the body, and a redirect would carry the
    request where it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Proofpane answered with a redirect, not followed.", headers,
                                     fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def addresses():
    """The addresses this deployment pairs with (config.proofpane_addresses), those of the form an address may have:
    https with a host and at most a port, or http to this machine. Read each time, never kept."""
    named = []
    for a in config.proofpane_addresses():
        if _CLOUD.fullmatch(a) and a not in named:
            named.append(a)
    return tuple(named)


def check_cloud(address):
    """The address to pair with, its trailing slash taken off: one this deployment names (addresses()), the first of
    them for None or empty. Any other is refused before anything is sent, whatever its form."""
    named = addresses()
    address = address.strip() if isinstance(address, str) else address
    if address in (None, ""):
        if not named:
            raise PairingError("This deployment names no Proofpane to pair with (OPEN_EDITION_PROOFPANE_ADDRESSES).")
        return named[0]
    if not isinstance(address, str) or address.rstrip("/") not in named:
        raise PairingError("This deployment pairs only with " + (", ".join(named) or "no Proofpane") +
                           " (OPEN_EDITION_PROOFPANE_ADDRESSES, which its operator sets).")
    return address.rstrip("/")


def check_code(code):
    code = code.strip().upper() if isinstance(code, str) else code
    if not isinstance(code, str) or not _CODE.match(code):
        raise PairingError("A pairing code is the one Proofpane shows (Devices, Pair a device), such as ABC-DEF-GHJ.")
    return code


def device_name():
    """The name the device is given in Proofpane: this deployment's, by its host, never the account's."""
    host = urllib.parse.urlsplit(config.public_url()).hostname or "this deployment"
    return f"Proofpane Community at {host}"[:120]


def _aad(user_id, pairing_id):
    return f"proofpane-open/proofpane-pairing/v1/{user_id}/{pairing_id}".encode("utf-8")


def seal(device_id, token, user_id, pairing_id):
    """The device's id and token sealed together, for the row of `user_id`'s pairing `pairing_id` alone."""
    key = ledger.keystore_key()
    if key is None:
        raise PairingError(NO_KEY)
    nonce = secrets.token_bytes(12)
    plain = json.dumps({"device_id": device_id, "token": token}).encode("utf-8")
    return _BOX + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plain, _aad(user_id, pairing_id))).decode()


def unseal(box, user_id, pairing_id):
    """(device id, token) from a box sealed for this row; PairingError when it cannot be opened here."""
    try:
        key = ledger.keystore_key()
        if key is None or not isinstance(box, str) or not box.startswith(_BOX):
            raise ValueError
        raw = base64.b64decode(box[len(_BOX):], validate=True)
        got = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], _aad(user_id, pairing_id)).decode("utf-8"))
        device, token = got["device_id"], got["token"]
        if not (isinstance(device, str) and _DEVICE.match(device) and isinstance(token, str) and _TOKEN.match(token)):
            raise ValueError
        return device, token
    except (ValueError, KeyError, TypeError, binascii.Error, InvalidTag, UnicodeDecodeError, ledger.LedgerError):
        raise PairingError(UNOPENED) from None


def _post(url, body, timeout):
    """One POST of `body` as JSON: (status, the answer's JSON or None). An HTTPError is its status; no answer at all, or
    one this cannot read, raises OSError with the kind of failure only (an error's own text can carry what was sent)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "proofpane-community"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(ANSWER_MAX + 1)
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read(65536).decode("utf-8", "replace"))
        except (ValueError, RecursionError, OSError):
            said = None
        return e.code, said
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        why = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (str, OSError)) else e
        raise OSError(type(why).__name__) from None
    try:
        got = json.loads(raw.decode("utf-8") or "null") if len(raw) <= ANSWER_MAX else None
    except (ValueError, RecursionError, UnicodeDecodeError):
        got = None
    return status, got


def pair(user_id, code, address=None, now=None):
    """Pair `user_id`'s account: the code exchanged for a device and its token, kept sealed (replacing a pairing kept,
    whose waiting usage is dropped). Checked before anything is sent: the code's form, the address's, and that this
    deployment can keep a token, so a code is never spent for nothing. Returns status_of()."""
    code = check_code(code)
    cloud = check_cloud(address)
    if ledger.keystore_key() is None:
        raise PairingError(NO_KEY)
    try:
        status, got = _post(cloud + "/api/v1/devices/pair", {"code": code, "name": device_name(),
                                                             "platform": PLATFORM}, TIMEOUT)
    except OSError as e:
        raise PairingError(f"Proofpane could not be reached ({e}).") from None
    detail = got.get("detail") if isinstance(got, dict) else None
    detail = detail[:300] if isinstance(detail, str) else None
    if 300 <= status < 400:
        raise PairingError("Proofpane answered with a redirect, which is not followed.")
    if status in (400, 404):
        raise PairingError(f"Proofpane did not take the code: {detail or status}. Make a new one in Proofpane "
                           "(Devices) and give it within ten minutes.")
    if not 200 <= status < 300:
        raise PairingError(f"Proofpane answered {status}{': ' + detail if detail else ''}.")
    device = got.get("device_id") if isinstance(got, dict) else None
    token = got.get("token") if isinstance(got, dict) else None
    if not (isinstance(device, str) and _DEVICE.match(device) and isinstance(token, str) and _TOKEN.match(token)):
        raise PairingError("Proofpane's answer held no device and token this deployment can keep.")
    name = got.get("name") if isinstance(got.get("name"), str) and got["name"].strip() else device_name()
    when = got.get("paired_at") if isinstance(got.get("paired_at"), str) and _WHEN.match(got["paired_at"]) else \
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(now if now is not None else time.time())
    pairing_id = uuid.uuid4().hex
    box = seal(device, token, user_id, pairing_id)
    from . import link_usage
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        link_usage.drop_waiting(db, user_id, link_usage.UNPAIRED, now)
        db.execute("INSERT OR REPLACE INTO ade_proofpane_pairings (user_id, id, cloud_url, device_id, name, paired_at, "
                   "box, created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (user_id, pairing_id, cloud, device, name.strip()[:120], when, box, now))
    return status_of(user_id)


def unpair(user_id, now=None):
    """The account's pairing removed, and the usage that waits to go with it dropped; nothing is revoked in Proofpane.
    False when there was none."""
    now = int(now if now is not None else time.time())
    from . import link_usage
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        gone = db.execute("DELETE FROM ade_proofpane_pairings WHERE user_id = ?", (user_id,)).rowcount
        if gone:
            link_usage.drop_waiting(db, user_id, link_usage.UNPAIRED, now)
    return bool(gone)


def status_of(user_id):
    """What the page is told of the account's pairing: whether this deployment can pair at all, the addresses it pairs
    with, and the pairing's address, device id and name, and when it was paired; never its token or its box."""
    try:
        can = ledger.keystore_key() is not None
    except ledger.LedgerError:
        can = False
    with store.connect() as db:
        r = db.execute("SELECT cloud_url, device_id, name, paired_at FROM ade_proofpane_pairings WHERE user_id = ?",
                       (user_id,)).fetchone()
    pairing = None if r is None else {"address": r["cloud_url"], "device_id": r["device_id"], "name": r["name"],
                                      "paired_at": r["paired_at"]}
    return {"can_pair": can, "pairing": pairing, "addresses": list(addresses())}


def device_for(user_id, path=None):
    """The account's pairing with its device's id and token opened, for the one request that uses them:
    {pairing_id, cloud_url, device_id, token}; None when the account has none. PairingError when its box cannot be
    opened here. `path`: the database a forward running after its request keeps (store.connect)."""
    with store.connect(path) as db:
        r = db.execute("SELECT id, cloud_url, box FROM ade_proofpane_pairings WHERE user_id = ?",
                       (user_id,)).fetchone()
    if r is None:
        return None
    device, token = unseal(r["box"], user_id, r["id"])
    return {"pairing_id": r["id"], "cloud_url": r["cloud_url"], "device_id": device, "token": token}


def send_event(cloud_url, body, timeout):
    """One row POSTed to `<cloud_url>/api/v1/mcp/event`, as the workspace's plugin posts its rows: (True, None) when
    Proofpane took it, else (False, why), why in fixed words with no part of what was sent."""
    try:
        status, got = _post(cloud_url + "/api/v1/mcp/event", body, timeout)
    except OSError as e:
        return False, f"Proofpane could not be reached ({e})"
    if 200 <= status < 300 and isinstance(got, dict):
        return True, None
    if 300 <= status < 400:
        return False, "Proofpane answered with a redirect, which is not followed"
    if status in (401, 403):
        return False, (f"Proofpane refused the device ({status}): it was revoked, paused or given a new token there; "
                       "unpair and pair again with a new code")
    if 200 <= status < 300:
        return False, f"Proofpane answered {status} with something that is not a JSON object"
    return False, f"Proofpane answered {status}"
