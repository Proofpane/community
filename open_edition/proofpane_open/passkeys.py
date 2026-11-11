"""Passkeys (WebAuthn Level 3): an account signs in with Face ID, Touch ID or a security key, and its password stays
as the fallback. The ADE session channel's stage 2 will need a signature the server cannot make, which is a passkey,
so it is built once for both.

- A passkey is added by an account already signed in: the server makes a one-time challenge, the authenticator makes
  a key pair for this site, and the server keeps the public key. Accounts still come only from invitations.
- Adding or removing a passkey, signing out everywhere, and approving another device's sign-in need the person's own
  word as well as the sign-in: one of the account's passkeys or its password, given in the last 5 minutes and used
  once (confirm). A sign-in token is carried by whoever holds it and may have been taken; one alone could otherwise add
  a passkey of its holder's (a way in that signing out everywhere does not end), remove the owner's, sign every other
  device out and stay signed in itself, or sign its holder's own devices in for 7 days each. A confirmation is made
  for one change (its subject: a device's request to be signed in, auth.approval_subject, or a change the page names,
  CHANGE); it is spent on that change and on no other, and each change takes only one made for it, so one asked for
  one change and spent on another is refused. End-to-end encryption's touches are bound by their purpose. The desktop
  server's own account, which has neither a password nor a passkey and signs in only through the link that server
  prints on its own machine, needs no confirmation.
- Signing in: the server makes a one-time challenge for no account in particular; the person picks one of this
  site's passkeys and verifies; the server finds the passkey by its credential id, checks the signature with the key
  it kept, and issues the same sign-in token a password does.
- Attestation is asked for ("direct") and judged once, as a passkey is added (hardware_keys.py): a hardware key (a
  certified security key, by the FIDO metadata, that verifies a PIN, can derive a key, works with a phone and keeps
  its credential to itself) or a device passkey, with one line why. Both sign in alike, and both confirm a change while high security is off.
- High security, the account's own setting: while it is on, a change that takes a confirmation takes one a hardware
  key made (a password or a device passkey confirms nothing), a command to a linked session is signed by a hardware
  key, and the keys a session pins are the hardware keys alone (owner_keys). Turning it on takes a hardware key's
  confirmation, a hardware key kept and no passkey waiting to be judged, and withdraws what the other passkeys signed
  that a session could still act on; turning it off takes a hardware key's confirmation; the account's last hardware
  key is not removed while it is on. Nothing else turns it off: no password, and no way around one.
- Challenges keep no row: each one's bytes, purpose, account and expiry travel in its id under a MAC with a key of
  its own, so asking for one writes nothing and nobody can fill a table or a cap by asking; a challenge that
  completed a ceremony is recorded until it expires, so it is used once. An id opens only as it was written: base64url
  without padding, decoded strictly, so no other string (a character a lenient decoder skips, the other alphabet's
  + and /, padding, a last character's spare bits set) opens as it.
- Checked on every ceremony: the challenge (one use, 5 minutes, bound to its purpose and, for adding, the account),
  the client data's type and origin (the deployment's own; crossOrigin false or left out, nothing else), the relying
  party id's hash (its host), user presence and user verification (a passkey replaces the password here, so it must
  verify its user), the algorithm (EdDSA, ES256, RS256 of at least 2048 bits), the signature, and the signature
  counter where the authenticator keeps one (a synced passkey reports 0 every time).
- What a passkey signs: a linked session takes a signature as a step of the owner's (a command, a move, a statement
  of the encryption keys) when its challenge is the sha256 of the ASCII "proofpane-step/1", a zero byte and the
  step's canonical JSON (step_challenge), and this server checks a step the same way (check_command). The page never
  hands the authenticator a challenge this server picked as it is: it signs in, confirms and answers end-to-end
  encryption's touches over a challenge derived from it for the ceremony's purpose (derived_challenge), whose bytes
  begin "proofpane-confirm/1", and this server checks the derived one. A signature asked for in one of these
  ceremonies cannot stand for a step without a SHA-256 collision, and one made for one purpose verifies for no other;
  the confirmation it gives keeps that purpose, and is spent on that purpose's change alone (_use_confirmation).
  In older releases a step's challenge was the sha256 of its canonical JSON alone, and a page signed in and confirmed
  over the server's challenge as it was; so nothing a passkey signed then is a step now, and the options of a
  ceremony are given only when the page names its purpose, which no older page did. Adding a passkey is another
  ceremony (webauthn.create), which no step is taken from.
- A lost device: remove its passkey here, and sign out everywhere (auth.sign_out_everywhere), which ends every sign-in
  token issued before it. Each step cancels the account's approvals still held (ade/holds.py).

The CBOR reader below reads only what WebAuthn sends: definite lengths, integers, byte and text strings, arrays, maps
with integer or text keys and no repeats, and false, true and null; nesting and sizes are bounded.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import typing
import unicodedata
import urllib.parse
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import auth, config, fido_mds, hardware_keys, store

CHALLENGE_TTL = 300
TIMEOUT_MS = CHALLENGE_TTL * 1000
MAX_CLIENT_DATA = 2048                  # a browser's clientDataJSON is a few hundred bytes
MAX_CHALLENGE_ID = 600
# a challenge id (a nonce is one) as _new_challenge writes it: what it carries and its MAC, each base64url without
# padding, joined by a dot (fullmatch, never match). A lenient decoder skips any other character and reads the other
# alphabet's + and / as - and _, so an id bent so would open as the one it was bent from
_CHALLENGE_ID = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
MAX_PASSKEYS = 20
MAX_NAME = 60
# what a passkey is, from what the server read as it was added (kind_of), and the name one given none takes
# (named_by_kind), a hardware key's being its model's where the FIDO metadata names it (model_of)
HARDWARE_KIND, SYNCED, SECURITY_KEY, DEVICE_BOUND = "hardware", "synced", "security-key", "device"
KIND_NAMES = {HARDWARE_KIND: "Hardware key", SYNCED: "Synced passkey", SECURITY_KEY: "Security key",
              DEVICE_BOUND: "Device-bound passkey"}
ATTACHMENTS = ("platform", "cross-platform")      # WebAuthn's two values of a credential's authenticatorAttachment
MAX_FIELD = 16 * 1024                   # the longest base64url field a browser sends here, before decoding
ALGORITHMS = (-8, -7, -257)             # EdDSA, ES256, RS256: the order a browser tries them
RP_NAME = "Proofpane Community"
SIGN_IN_FAILED = "That passkey did not sign you in. Try again, or sign in with your password."
# a page from an older release asks for a ceremony's options naming no purpose, and would have its passkey sign the
# server's challenge as it is: it is refused before its prompt
OLD_PAGE = "This page is older than the ADE it talks to: reload it, then try again."
NEEDS_CONFIRM = ("Confirm it is you first, with one of this account's passkeys or its password: a sign-in alone does not "
                 "add, rename or remove a passkey, or sign every device out.")
CONFIRM_FAILED = "That did not confirm it: use one of this account's own passkeys, or its password."
CONFIRM_STALE = "That confirmation has expired or was used; confirm again."
CONFIRM_GONE = ("The passkey that gave that confirmation is not this account's now, or not a hardware key: confirm "
                "again.")
# a confirmation made for one purpose (a ceremony the page asked for, CONFIRM_PURPOSES), or for one change (its
# subject: a device's request to be signed in), offered for another's change
CONFIRM_OTHER = "That confirmation was made for something else: confirm this one again."
# one made for no change of its own, given for a change (every change names its own now, a device's approval too): a
# page from an older release makes such confirmations, and confirming again after a reload makes another
CONFIRM_FOR_NOTHING = ("That confirmation names no change of its own, as a page from an older release makes them: "
                       "reload the page and confirm again.")
NO_PASSKEY = "This account has no passkey to confirm with."
HARDWARE = hardware_keys.HARDWARE
NEEDS_HARDWARE = ("High security is on: confirm it with one of this account's hardware keys. A password or another "
                  "passkey does not confirm it.")
NEEDS_HARDWARE_TO_TURN = "Turning high security on or off takes one of this account's hardware keys."
NO_HARDWARE_KEY = "This account has no hardware key: add one first (two is better, one of them kept apart)."
LAST_HARDWARE_KEY = ("That is the account's last hardware key, and high security is on: add another hardware key "
                     "first, or turn high security off.")
ONLY_HARDWARE_SIGNS = "High security is on: only one of this account's hardware keys signs a command to a session."
KEY_WAITING = ("A passkey of this account still waits to be judged, and could become a hardware key without a hardware "
               "key's word: remove it, or turn high security on once it is judged.")
OLD_PASSKEY = "Added before passkeys were judged here: add it again to have it judged."
UNREAD_ATTESTATION = "Its attestation, kept here to judge it again, cannot be read."
# while end-to-end encryption is on (ade/e2e_keys.py)
NEEDS_PASSKEY = ("End-to-end encryption is on: confirm it with one of this account's passkeys, or with the recovery "
                 "code. The password does not confirm it.")
# a passkey's rename, while it is on: its name is what the revocation and the holds of encryption name it by
NEEDS_PASSKEY_ALONE = ("End-to-end encryption is on: confirm it with one of this account's passkeys. The password does "
                       "not confirm it.")
REVOKES_ONLY = ("End-to-end encryption is on: a passkey is removed, and high security turned on, from Account, "
                "Encryption, which replaces the master key so that what is removed opens nothing.")
# a confirmation says how it was made: a hardware key's is the one high security takes; a passkey's names its passkey
# (the one end-to-end encryption takes), the password's none
CONFIRMED, CONFIRMED_HARDWARE = "confirmed", "confirmed+hardware"
# a registration's challenge says what confirmed its options: a hardware key, the recovery code (ade/e2e_keys.py),
# another way (another passkey, named in it, or the password), or nothing (an account with nothing to confirm with)
REGISTER_PURPOSES = ("register+hardware", "register+recovery", "register+", "register")
# the registrations that may add a passkey while high security is on
REGISTER_HIGH = ("register+hardware", "register+recovery")
# the purpose of each ceremony whose challenge this server picks, as derived_challenge names it: signing in; and the
# confirmations, which the page asks for as a change's confirmation or as end-to-end encryption's touches (cancelling
# the change that waits, the touch that confirms adding a passkey to it, made with one that unlocks it already, the
# second touch of the passkey being added, its own confirmation, and each touch of renewing its anchors)
CEREMONY_TAG = b"proofpane-confirm/1"
# what a step's challenge hashes before its canonical JSON (step_challenge)
STEP_TAG = b"proofpane-step/1"
SIGN_IN = "sign-in"
CONFIRM = "confirm"
# (and each passkey's touch in the upgrade of encryption to post-quantum keys, which is also made for its change:
# e2e-upgrade, in CHANGE)
CONFIRM_PURPOSES = (CONFIRM, "e2e-cancel", "e2e-enrol-touch", "e2e-enrol-own", "e2e-anchor-touch", "e2e-upgrade-touch")
# the change a confirmation of CONFIRM's purpose is made for (its subject: confirm's `change`), each a kind and, where
# the change is to one thing, that thing's id (32 lowercase hex, as every id here is written): adding a passkey,
# removing one, renaming one, turning high security on or off, signing every device out, making a link ticket (for a new session, or
# to re-pair a link), revoking a link, approving a session's request to be linked (and the link it replaces), turning
# on the account's switch for apps, allowing an app's request (with ade.propose or without: "propose" or "read"),
# approving a held command, and making a ticket to pair a signer (ade/signers.py). A change to a thing that does not
# exist yet (a passkey to add, a new session's ticket, a signer's) is named by its kind alone, so its confirmation is
# spent on one such change, whichever. A device's sign-in is
# named by its code instead (auth.approval_subject), never here. A passkey's touch in the upgrade of encryption is made
# for the digest of what exists before it (e2e_keys.upgrade_touch_subject: the new master key, the statement, that
# credential, its fresh salt and its new public key), with purpose e2e-upgrade-touch.
# The tasks' and the teammate track's changes are each to one thing, named by its id. A task's, in ade/tasks.py:
# confirming an account in the
# workspace as the owner's own (its ade_account_owners row), turning a task's "Cross organisations" switch back on
# (the task), and deleting the account record of a task already deleted (the task). The teammate track's, in
# ade/task_offers.py: ticking custody's own crossing on a task (the task), offering a task to a teammate (the offer),
# asking a teammate to be one's custodian (the custody request), the custodian accepting it (that request), and
# confirming a task for custody (the task), and lifting what held a hand-off's push, which the person whose push it is
# says is no secret (the task, the owner's or the one a teammate received, and a digest of the findings lifted, so
# that one made for some findings is spent on no others: task_offers.lift_subject). An offer is written first as a draft, and
# a custody request first as asked, so that the confirmation names that one row: one made for an offer to one
# teammate, or for one custodian, is spent on no other. Deleting the account's workspace left behind at once
# (ade/storage.py, delete_workspace), of which an account has one, is named by its kind alone.
_ID = r"[0-9a-f]{32}"
CHANGE = re.compile(r"passkey-add|passkey-remove:{i}|passkey-rename:{i}|high-security-(?:on|off)|sign-out-everywhere"
                    r"|link-ticket:(?:new|{i})|link-revoke:{i}|link-approve:{i}(?::{i})?|connector-on"
                    r"|connector-allow:{i}:(?:propose|read)|request-approve:{i}|signer-ticket"
                    r"|e2e-upgrade:{i}"
                    r"|task-account-own:{i}|task-cross:{i}|task-custody-cross:{i}|task-offer:{i}|task-custody:{i}"
                    r"|task-custody-accept:{i}|task-custody-confirm:{i}|task-account-record:{i}"
                    r"|task-secret-lift:{i}:{i}|workspace-delete".replace("{i}", _ID))
BAD_CHANGE = "That is not a change a confirmation is made for: reload the page and try again."
_FLAG_UP, _FLAG_UV, _FLAG_BE, _FLAG_BS, _FLAG_AT, _FLAG_ED = 0x01, 0x04, 0x08, 0x10, 0x40, 0x80


class PasskeyError(ValueError):
    """A refusal with its HTTP status."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _now(now):
    return int(now if now is not None else time.time())


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text, what="a field"):
    if not isinstance(text, str) or not text or len(text) > MAX_FIELD or "=" in text:
        raise PasskeyError(400, f"The passkey's answer has no usable {what}.")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        raise PasskeyError(400, f"The passkey's answer has no usable {what}.") from None


def rp_id():
    """The relying party id: the host of the deployment's public address, which every passkey is made for."""
    return (urllib.parse.urlsplit(config.public_url()).hostname or "").lower()


def origin():
    """The origin a browser reports for the deployment's page: scheme and host, and the port if not the default."""
    parts = urllib.parse.urlsplit(config.public_url())
    default = {"https": 443, "http": 80}.get(parts.scheme)
    port = "" if parts.port in (None, default) else f":{parts.port}"
    return f"{parts.scheme}://{rp_id()}{port}"


# ---- CBOR, as much as WebAuthn uses --------------------------------------------------------------------------------

def cbor_decode(data, pos=0, depth=0):
    """(the item that starts at `pos`, the position after it)."""
    if depth > 8:
        raise PasskeyError(400, "The passkey's answer nests too deep.")
    if pos >= len(data):
        raise PasskeyError(400, "The passkey's answer ends early.")
    head = data[pos]
    major, info = head >> 5, head & 31
    pos += 1
    if info < 24:
        arg = info
    elif info <= 27:
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise PasskeyError(400, "The passkey's answer ends early.")
        arg = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    else:
        raise PasskeyError(400, "The passkey's answer uses a CBOR form WebAuthn does not.")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if arg > len(data) - pos:
            raise PasskeyError(400, "The passkey's answer ends early.")
        raw = bytes(data[pos:pos + arg])
        if major == 2:
            return raw, pos + arg
        try:
            return raw.decode("utf-8"), pos + arg
        except UnicodeDecodeError:
            raise PasskeyError(400, "The passkey's answer has text that is not UTF-8.") from None
    if major == 4:
        if arg > 64:
            raise PasskeyError(400, "The passkey's answer has too long a list.")
        items = []
        for _ in range(arg):
            item, pos = cbor_decode(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        if arg > 64:
            raise PasskeyError(400, "The passkey's answer has too long a map.")
        items = {}
        for _ in range(arg):
            key, pos = cbor_decode(data, pos, depth + 1)
            if isinstance(key, bool) or not isinstance(key, (int, str)) or key in items:
                raise PasskeyError(400, "The passkey's answer has a map key WebAuthn does not use.")
            items[key], pos = cbor_decode(data, pos, depth + 1)
        return items, pos
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], pos
    raise PasskeyError(400, "The passkey's answer uses a CBOR form WebAuthn does not.")


def cbor_whole(data):
    item, end = cbor_decode(data)
    if end != len(data):
        raise PasskeyError(400, "The passkey's answer has bytes after its end.")
    return item


# ---- keys, authenticator data, client data -----------------------------------------------------------------------

def cose_key(cose):
    """(alg, the public key) from a COSE_Key: Ed25519, P-256 or RSA of at least 2048 bits."""
    if not isinstance(cose, dict):
        raise PasskeyError(400, "The passkey's public key is not a COSE key.")
    kty, alg = cose.get(1), cose.get(3)
    try:
        if kty == 1 and alg == -8 and cose.get(-1) == 6 and isinstance(cose.get(-2), bytes) and len(cose[-2]) == 32:
            return alg, Ed25519PublicKey.from_public_bytes(cose[-2])
        if (kty == 2 and alg == -7 and cose.get(-1) == 1 and all(isinstance(cose.get(k), bytes) and len(cose[k]) == 32
                                                                for k in (-2, -3))):
            x, y = (int.from_bytes(cose[k], "big") for k in (-2, -3))
            return alg, ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        if kty == 3 and alg == -257 and isinstance(cose.get(-1), bytes) and isinstance(cose.get(-2), bytes):
            n, e = int.from_bytes(cose[-1], "big"), int.from_bytes(cose[-2], "big")
            if n.bit_length() < 2048 or e < 3 or e % 2 == 0:
                raise PasskeyError(400, "The passkey's RSA key is too weak.")
            return alg, rsa.RSAPublicNumbers(e, n).public_key()
    except PasskeyError:
        raise
    except ValueError:                      # a point not on the curve, and the like
        raise PasskeyError(400, "The passkey's public key is not a valid key.") from None
    raise PasskeyError(400, "The passkey uses a key type or algorithm this site does not take (EdDSA, ES256, RS256).")


def authenticator_data(data, attested):
    """The fields of authenticator data; with `attested`, the new credential's id and COSE key as well."""
    if not isinstance(data, bytes) or len(data) < 37:
        raise PasskeyError(400, "The passkey's authenticator data is too short.")
    flags = data[32]
    out = {"rp_hash": data[:32], "flags": flags, "count": int.from_bytes(data[33:37], "big"),
           "up": bool(flags & _FLAG_UP), "uv": bool(flags & _FLAG_UV), "backed_up": bool(flags & _FLAG_BS),
           "backup_eligible": bool(flags & _FLAG_BE)}
    pos = 37
    if attested:
        if not flags & _FLAG_AT or len(data) < pos + 18:
            raise PasskeyError(400, "The passkey's authenticator data holds no new credential.")
        pos += 16                                               # the authenticator's model id, not used
        size = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        if not 16 <= size <= 1023 or pos + size > len(data):
            raise PasskeyError(400, "The passkey's credential id is not of a usable length.")
        out["credential_id"] = data[pos:pos + size]
        out["cose"], pos = cbor_decode(data, pos + size)
    elif flags & _FLAG_AT:
        raise PasskeyError(400, "A sign-in's authenticator data holds a new credential.")
    if flags & _FLAG_ED:
        extensions, pos = cbor_decode(data, pos)
        if not isinstance(extensions, dict):
            raise PasskeyError(400, "The passkey's extensions are not a map.")
    if pos != len(data):
        raise PasskeyError(400, "The passkey's authenticator data has bytes after its end.")
    return out


def check_config():
    """Refuse to start with a public address whose host is not ASCII: every ceremony hashes the relying party id as
    ASCII, and an international name is written in its xn-- form."""
    try:
        rp_id().encode("ascii")
    except UnicodeEncodeError:
        raise RuntimeError("OPEN_EDITION_PUBLIC_URL's host must be ASCII: write an international name in its xn-- "
                           "form.") from None


def derived_challenge(purpose, challenge):
    """The challenge the page has a passkey sign in a ceremony whose challenge this server picked (`challenge`, its
    bytes), for `purpose` (SIGN_IN, or one of CONFIRM_PURPOSES): sha256 of the ASCII "proofpane-confirm/1", a zero
    byte, the purpose's ASCII, a zero byte, and the challenge's bytes. The zero after the purpose ends it, so no other
    purpose and challenge give these bytes; a step's challenge hashes "proofpane-step/1" first (step_challenge)."""
    if purpose != SIGN_IN and purpose not in CONFIRM_PURPOSES:
        raise ValueError(f"no ceremony's purpose: {purpose!r}")
    return hashlib.sha256(CEREMONY_TAG + b"\0" + purpose.encode("ascii") + b"\0" + challenge).digest()


def step_challenge(canonical):
    """The challenge a passkey signs for a step of the owner's whose canonical JSON (RFC 8785) is `canonical`, as the
    page makes it and a linked session checks it: sha256 of the ASCII "proofpane-step/1", a zero byte, and the bytes.
    The three are held to tests/vectors/step_v1.json."""
    return hashlib.sha256(STEP_TAG + b"\0" + canonical).digest()


def check_client_data(raw, kind, challenge, mismatch="The passkey's answer is not for this request."):
    if len(raw) > MAX_CLIENT_DATA:
        raise PasskeyError(400, "The passkey's client data is too long.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PasskeyError(400, "The passkey's client data is not JSON.") from None
    if not isinstance(data, dict) or data.get("type") != kind:
        raise PasskeyError(400, "The passkey's answer is for another ceremony.")
    if data.get("challenge") != b64u(challenge):
        raise PasskeyError(400, mismatch)
    # crossOrigin is a boolean, which a browser sends as false or (an older one) leaves out: nothing else, neither
    # null nor what Python holds equal to False (0, 0.0), which a check by == would take
    if data.get("origin") != origin() or data.get("crossOrigin", False) is not False or "topOrigin" in data:
        raise PasskeyError(400, "The passkey's answer comes from another site.")
    return data


def check_flags(auth, what):
    if auth["rp_hash"] != hashlib.sha256(rp_id().encode("ascii")).digest():
        raise PasskeyError(400, f"The passkey's {what} is for another site.")
    if not auth["up"] or not auth["uv"]:
        raise PasskeyError(400, f"The passkey's {what} did not verify you (Face ID, Touch ID or a PIN).")
    if auth["backed_up"] and not auth["backup_eligible"]:
        # a credential that may not be backed up says it is: no authenticator sets these flags so (WebAuthn 7.1, 7.2)
        raise PasskeyError(400, f"The passkey's {what} says it is backed up, yet not that it may be.")


def check_backup_eligible(row, auth):
    """The backup-eligible flag (BE) of a passkey's assertion must be the one it was added with: an authenticator sets
    it once, as it makes the credential (WebAuthn 6.1.3), so another value is another authenticator's, or a copy's. A
    row kept before the flag was (backup_eligible NULL) holds a hardware key's clear, since the judgment asked for it,
    and learns any other passkey's at its next use (commit_counter, _assertion)."""
    held = row["backup_eligible"]
    if held is None and row["tier"] == HARDWARE:
        held = 0
    if held is not None and bool(held) != auth["backup_eligible"]:
        raise PasskeyError(401, "the passkey's backup-eligible flag is not the one it was added with")


def _check_backup_eligible_now(db, passkey_id, auth):
    """check_backup_eligible on the row as it is in this transaction, which goes on to keep a flag first seen."""
    row = db.execute("SELECT backup_eligible, tier FROM passkeys WHERE id = ?", (passkey_id,)).fetchone()
    if row is not None:                     # one removed meanwhile moves no counter: the update finds no row
        check_backup_eligible(row, auth)


def verify_signature(alg, spki, signature, signed):
    key = serialization.load_der_public_key(spki)
    try:
        if alg == -8:
            key.verify(signature, signed)
        elif alg == -7:
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == -257:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise InvalidSignature()
    except (InvalidSignature, ValueError):
        return False
    return True


# ---- challenges --------------------------------------------------------------------------------------------------

def _challenge_key():
    # a key of its own, derived from the token secret, so no other MAC the app makes can stand for a challenge's
    return hmac.new(config.token_secret(), b"proofpane passkey challenge v1", hashlib.sha256).digest()


def _new_challenge(purpose, user_id, now, key=None, signed_for=None, subject=None):
    """(the challenge id, the challenge's bytes): nothing is written; the id carries what checking needs, `key`, the
    id of the passkey whose word it carries, where it carries one (a confirmation a passkey made), `signed_for`, the
    purpose its bytes are signed for (derived_challenge), where the page names one (a confirmation's options), and
    `subject`, the one change a confirmation or its options are for, where they are for one (confirm)."""
    challenge = secrets.token_bytes(32)
    fields = {"c": b64u(challenge), "p": purpose, "u": user_id, "e": now + CHALLENGE_TTL}
    if key is not None:
        fields["k"] = key
    if signed_for is not None:
        fields["s"] = signed_for
    if subject is not None:
        fields["f"] = subject
    payload = json.dumps(fields, separators=(",", ":")).encode("ascii")
    return f"{b64u(payload)}.{b64u(hmac.new(_challenge_key(), payload, hashlib.sha256).digest())}", challenge


def _strict_unb64u(text):
    """`text`, base64url without padding, decoded strictly: binascii.Error for a character outside the alphabet or a
    length no encoding has, and ValueError for any form but the one b64u writes of the same bytes (a last character
    whose bits past the bytes are set), so two strings never decode alike."""
    raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    if b64u(raw) != text:
        raise ValueError("not the form base64url writes these bytes in")
    return raw


def _open_challenge(challenge_id, purpose, user_id, now, with_key=False, with_signed_for=False, with_subject=False):
    """(the challenge's bytes, its expiry, with `with_key` the passkey id it carries or None, with `with_signed_for`
    the purpose its bytes are signed for, CONFIRM where it names none, and with `with_subject` the one change it is
    for or None) when the id is one this deployment made, for this purpose and account, and not expired; whether it
    was used is checked when a ceremony completes (_use_challenge)."""
    stale = PasskeyError(400, "This passkey request has expired or was used; start again.")
    # its characters checked before anything is decoded, and each part decoded strictly, in its one form: no other
    # string opens as an id this deployment made
    if not isinstance(challenge_id, str) or len(challenge_id) > MAX_CHALLENGE_ID \
            or not _CHALLENGE_ID.fullmatch(challenge_id):
        raise stale
    payload_b64, mac_b64 = challenge_id.split(".")
    try:
        payload, mac = _strict_unb64u(payload_b64), _strict_unb64u(mac_b64)
    except (ValueError, binascii.Error):
        raise stale from None
    if not hmac.compare_digest(mac, hmac.new(_challenge_key(), payload, hashlib.sha256).digest()):
        raise stale
    data = json.loads(payload)                    # the MAC holds, so it is what _new_challenge wrote
    if data["p"] != purpose or data["u"] != user_id or data["e"] <= now:
        raise stale
    opened = (base64.urlsafe_b64decode(data["c"] + "=" * (-len(data["c"]) % 4)), data["e"])
    if with_key:
        opened += (data.get("k"),)
    if with_signed_for:
        opened += (data.get("s", CONFIRM),)
    if with_subject:
        opened += (data.get("f"),)
    return opened


def _use_challenge(db, challenge, expires_at, now):
    """Record, inside the ceremony's own transaction, a challenge that completed one; a second use is refused. A
    row is kept only until the challenge expires, and only a completed ceremony makes one."""
    db.execute("DELETE FROM passkey_used_challenges WHERE expires_at <= ?", (now,))
    try:
        db.execute("INSERT INTO passkey_used_challenges (challenge_hash, expires_at) VALUES (?,?)",
                   (hashlib.sha256(challenge).hexdigest(), expires_at))
    except sqlite3.IntegrityError:
        raise PasskeyError(400, "This passkey request has expired or was used; start again.") from None


# ---- adding, listing and removing ----------------------------------------------------------------------------------

def _name(name):
    """A name given for a passkey (NFC, stripped, at most MAX_NAME characters, none a control character; 400 otherwise),
    or None where none was: the passkey is then named by what it is (named_by_kind)."""
    name = unicodedata.normalize("NFC", name if isinstance(name, str) else "").strip()
    if not name:
        return None
    if len(name) > MAX_NAME or any(unicodedata.category(c).startswith("C") for c in name):
        raise PasskeyError(400, f"A passkey's name is at most {MAX_NAME} characters, with no control characters.")
    return name


def _attachment(value):
    """The browser's word on the authenticator that made a new passkey, its credential's authenticatorAttachment:
    "platform" (this device's own) or "cross-platform" (a security key, or another device reached by its code); None
    for anything else, as for a browser that says nothing. The browser's word alone: it names a passkey and says its
    kind, and decides nothing a passkey may do."""
    return value if isinstance(value, str) and value in ATTACHMENTS else None


def kind_of(tier, backup_eligible, backed_up, attachment):
    """What a passkey is: a hardware key, by its tier (hardware_keys.judge); else synced, where its backup flags say it
    may be copied to other devices (one backed up is); else a security key, where the browser said another device
    made it; else bound to one device, as far as anything said. A row kept before the attachment was has none, so it
    is never called a security key on no word."""
    if tier == HARDWARE:
        return HARDWARE_KIND
    if backup_eligible or backed_up:
        return SYNCED
    if attachment == "cross-platform":
        return SECURITY_KEY
    return DEVICE_BOUND


def model_of(maker, why):
    """A passkey's model as the FIDO metadata names it (its maker's description), where its attestation chained to a
    root the metadata lists for that model and no report says that proves nothing; None where it did not, or one does
    (hardware_keys.UNPROVEN: unchained, or the model revoked or its attestation key compromised), or it names none."""
    return maker if maker and why not in hardware_keys.UNPROVEN else None


def _cut(text, room):
    return text if len(text) <= room else text[:room - 1].rstrip() + "…"


def named_by_kind(kind, model):
    """The name of a passkey given none: its model (model_of), cut to a name's length, else its kind's (KIND_NAMES)."""
    return _cut(model, MAX_NAME) if model else KIND_NAMES[kind]


def unique_name(db, user_id, name, passkey_id=None):
    """Inside a transaction: `name`, a name the server gave (named_by_kind), told apart from the account's other
    passkeys' names (all but `passkey_id`'s) by the first number from 2 that none has, cut to leave it room within a
    name's length. A name the person gave is theirs to repeat, and is never numbered."""
    taken = {r["name"] for r in db.execute("SELECT name FROM passkeys WHERE user_id = ? AND id IS NOT ?",
                                           (user_id, passkey_id))}
    if name not in taken:
        return name
    for n in range(2, MAX_PASSKEYS + 2):
        number = f" {n}"
        numbered = _cut(name, MAX_NAME - len(number)) + number
        if numbered not in taken:
            return numbered
    return name                             # an account keeps at most MAX_PASSKEYS: never reached


def rename_by_kind(db, passkey_id):
    """Inside a transaction: the passkey `passkey_id` named again by what it is now (named_by_kind, unique_name), when
    the server gave its name and the person has given none since; after its tier, model or why change (judge_again,
    reconsider)."""
    row = db.execute("SELECT user_id, tier, maker, tier_why, backup_eligible, backed_up, attachment, name_by_server "
                     "FROM passkeys WHERE id = ?", (passkey_id,)).fetchone()
    if row is None or not row["name_by_server"]:
        return
    name = named_by_kind(kind_of(row["tier"], row["backup_eligible"], row["backed_up"], row["attachment"]),
                         model_of(row["maker"], row["tier_why"]))
    db.execute("UPDATE passkeys SET name = ? WHERE id = ?",
               (unique_name(db, row["user_id"], name, passkey_id), passkey_id))


def _credential_ids(user_id):
    with store.connect() as db:
        return [r["credential_id"] for r in db.execute("SELECT credential_id FROM passkeys WHERE user_id = ?",
                                                       (user_id,))]


def registration_options(user, confirmation=None, now=None, recovery_proof=None):
    """What the page hands navigator.credentials.create(), base64url where WebAuthn wants bytes, asking for the
    authenticator's attestation (direct), which register judges. It uses up a confirmation (confirm) when the account
    has a password or a passkey to make one with: while its high security is on, a hardware key's, since a passkey
    added could otherwise be a hardware key of whoever holds the password. While end-to-end encryption is on
    (ade/e2e_keys.py) it takes a passkey's confirmation, never the password's, or the recovery code's proof
    (`recovery_proof`), since a passkey added there can be enrolled to unlock the account's keys; and is refused (503)
    before the passkey's ceremony where the recovery code could not cancel the addition (e2e_keys.code_may_cancel)."""
    from .ade import e2e_keys
    now = _now(now)
    have = _credential_ids(user["id"])
    if len(have) >= MAX_PASSKEYS:
        raise PasskeyError(409, f"An account keeps at most {MAX_PASSKEYS} passkeys; remove one first.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        on = e2e_keys.on(db, user["id"])
        if on:
            e2e_keys.code_may_cancel(db, user["id"])
        if on and recovery_proof is not None:
            e2e_keys.use_proof(db, user["id"], recovery_proof, "passkey", now)
            used, purpose = None, "register+recovery"
        else:
            used = _use_confirmation(db, user["id"], confirmation, now, passkey=NEEDS_PASSKEY if on else None,
                                     subject="passkey-add")
            # a challenge says what confirmed it: one given while the account had nothing to confirm with registers
            # only while it still has nothing (register), and one given with a confirmation other than a hardware key's
            # only while high security is off (register+), so options fetched before it was turned on cannot add a
            # passkey after; and it names the passkey that confirmed it, whose word holds only while that passkey does,
            # and without which (the password's) it adds none while end-to-end encryption is on (register)
            purpose = {CONFIRMED_HARDWARE: "register+hardware", CONFIRMED: "register+"}.get(used and used.kind,
                                                                                            "register")
    challenge_id, challenge = _new_challenge(purpose, user["id"], now, key=used and used.passkey_id)
    return {"challenge_id": challenge_id, "publicKey": {
        "rp": {"id": rp_id(), "name": RP_NAME},
        "user": {"id": b64u(user["id"].encode("ascii")), "name": user["email"], "displayName": user["email"]},
        "challenge": b64u(challenge),
        "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in ALGORITHMS],
        "timeout": TIMEOUT_MS,
        "attestation": "direct",
        "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True,
                                   "userVerification": "required"},
        "excludeCredentials": [{"type": "public-key", "id": c} for c in have]}}


def _transports(value):
    """The transports the browser reported for a new passkey (getTransports), as short words; anything else is
    passed over. The browser's word: it counts only toward working with a phone, and only for a model the FIDO
    metadata lists no transports for (hardware_keys.judge)."""
    if not isinstance(value, list):
        return []
    return [t for t in value if isinstance(t, str) and 0 < len(t) <= 32 and t.isascii()][:8]


def _refusal(db, user_id, purpose, confirmed_by, credential_id):
    """Inside a transaction, why the new passkey is refused (a PasskeyError) or None: the account keeps as many as it
    may, a passkey with this id is kept already, or what confirmed the options (the challenge's purpose and the
    passkey it names) does not hold now: while end-to-end encryption is on, a passkey's word or the recovery code's,
    and while high security is on, a hardware key's or the recovery code's."""
    from .ade import e2e_keys
    if db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PASSKEYS:
        return PasskeyError(409, f"An account keeps at most {MAX_PASSKEYS} passkeys; remove one first.")
    if db.execute("SELECT 1 FROM passkeys WHERE credential_id = ?", (credential_id,)).fetchone():
        return PasskeyError(409, "That passkey is already added.")
    if purpose == "register" and _needs_confirmation(db, user_id):
        return PasskeyError(403, NEEDS_CONFIRM)
    on = e2e_keys.on(db, user_id)
    if purpose == "register+recovery" and not on:
        return PasskeyError(403, NEEDS_CONFIRM)     # the recovery code confirms a passkey only while encryption is on
    if purpose != "register+recovery" and confirmed_by is None and on:
        return PasskeyError(403, NEEDS_PASSKEY)
    if purpose not in REGISTER_HIGH and _high_security(db, user_id):
        return PasskeyError(403, NEEDS_HARDWARE)
    if purpose != "register" and not _still_holds(db, user_id, confirmed_by, purpose == "register+hardware"):
        return PasskeyError(403, CONFIRM_GONE)
    return None


def register(user, challenge_id, credential, name=None, now=None, prf=None):
    """Keep the public key of a passkey the account just made, with its tier (hardware_keys.judge); returns it as the
    page lists it, with its kind (kind_of). Given no name, it is named by what it is (named_by_kind): its model, else
    synced, a security key or bound to one device, from its backup flags and the browser's word on the authenticator
    that made it (`credential`'s authenticatorAttachment, kept with it). The challenge is used up, and the cheap checks made, before the attestation is judged: one answer
    to a challenge is judged or refused, once, so a replay of it, or a passkey added already, is refused before any
    certificate is read. The judgment waits for no metadata: a key judged while there is no valid blob keeps its
    attestation, to be judged again once there is (judge_again), and asks the refresher for one (fido_mds.nudge).
    `prf` is the browser's word that the passkey gives the PRF extension (its client extension results), kept for
    end-to-end encryption to say which passkeys can unlock its keys. While end-to-end encryption is on, the passkey is
    added only when a hold ends (ade/e2e_keys.py: 180 seconds, or 24 hours when the recovery code confirmed it), with a
    notice to the account's devices at once: {held: the hold, told}."""
    from .ade import e2e_keys, holds
    now = _now(now)
    name = _name(name)
    for purpose in REGISTER_PURPOSES:
        try:
            challenge, expires_at, confirmed_by = _open_challenge(challenge_id, purpose, user["id"], now,
                                                                  with_key=True)
            break
        except PasskeyError:
            if purpose == REGISTER_PURPOSES[-1]:
                raise
    if not isinstance(credential, dict) or credential.get("type") != "public-key" \
            or not isinstance(credential.get("response"), dict):
        raise PasskeyError(400, "That is not a passkey's answer.")
    response = credential["response"]
    client_raw = unb64u(response.get("clientDataJSON"), "client data")
    check_client_data(client_raw, "webauthn.create", challenge)
    attestation_raw = unb64u(response.get("attestationObject"), "attestation")
    attestation = cbor_whole(attestation_raw)
    if not isinstance(attestation, dict) or not isinstance(attestation.get("fmt"), str) \
            or not isinstance(attestation.get("attStmt"), dict):
        raise PasskeyError(400, "The passkey's attestation object is not one.")
    auth = authenticator_data(attestation.get("authData"), attested=True)
    check_flags(auth, "new key")
    if credential.get("id") != b64u(auth["credential_id"]):
        raise PasskeyError(400, "The passkey's id does not match its authenticator data.")
    alg, key = cose_key(auth["cose"])
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    credential_id = b64u(auth["credential_id"])
    attachment = _attachment(credential.get("authenticatorAttachment"))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _use_challenge(db, challenge, expires_at, now)
        refused = _refusal(db, user["id"], purpose, confirmed_by, credential_id)
    if refused is not None:
        raise refused                       # after the transaction, so the challenge stays used
    transports = _transports(response.get("transports"))
    client_hash = hashlib.sha256(client_raw).digest()
    verdict = hardware_keys.judge(attestation, client_hash, transports, lambda: fido_mds.kept(now), now)
    kept = json.dumps({"object": b64u(attestation_raw), "client_data_hash": b64u(client_hash),
                       "transports": transports}, separators=(",", ":")) if verdict.again else None
    kind = kind_of(verdict.tier, auth["backup_eligible"], auth["backed_up"], attachment)
    by_server = name is None
    if by_server:
        name = named_by_kind(kind, model_of(verdict.maker, verdict.why))
    passkey_id = uuid.uuid4().hex
    values = {"id": passkey_id, "user_id": user["id"], "credential_id": credential_id, "public_key": b64u(spki),
              "alg": alg, "sign_count": auth["count"], "backed_up": int(auth["backed_up"]),
              "backup_eligible": int(auth["backup_eligible"]), "name": name, "created_at": now, "tier": verdict.tier,
              "aaguid": verdict.aaguid, "maker": verdict.maker, "tier_why": verdict.why,
              "judge_again": int(verdict.again), "attestation": kept, "phone_by_browser": int(verdict.by_browser),
              "attestation_certs": _certs_column(verdict), "prf": int(prf is True), "attachment": attachment,
              "name_by_server": int(by_server)}
    hold = None
    holds.settle(user["id"], now)           # a change whose time has come takes effect, not taken the place of
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        refused = _refusal(db, user["id"], purpose, confirmed_by, credential_id)  # again: it may have changed since
        if refused is not None:
            raise refused
        if by_server:                       # told apart from the account's other passkeys' names (unique_name)
            values["name"] = name = unique_name(db, user["id"], name)
        if e2e_keys.on(db, user["id"]):
            hold = e2e_keys.hold_passkey(db, values, purpose == "register+recovery", now, confirmed_by=confirmed_by)
        else:
            insert_passkey(db, values)
    if verdict.again:
        fido_mds.nudge()                    # the refresher fetches a blob, and judges this key again once it has one
    if hold is not None:
        told = holds.tell(hold, now)
        return {"held": dict(hold, told=told), "told": told, "kind": kind}
    return {"id": passkey_id, "name": name, "created_at": now, "last_used_at": None,
            "backed_up": auth["backed_up"], "tier": verdict.tier, "maker": verdict.maker, "why": verdict.why,
            "judge_again": verdict.again, "phone_by_browser": verdict.by_browser, "kind": kind}


_PASSKEY_COLUMNS = ("id", "user_id", "credential_id", "public_key", "alg", "sign_count", "backed_up", "backup_eligible",
                    "name", "created_at", "tier", "aaguid", "maker", "tier_why", "judge_again", "attestation",
                    "phone_by_browser", "attestation_certs", "prf", "attachment", "name_by_server")
# values a passkey held on a hold by a server from before they were kept lacks (ade/e2e_keys.py keeps the values whole
# until the hold ends): it is added without them, as a row from before has none
_KEPT_SINCE = frozenset({"attachment", "name_by_server"})


def check_new_passkey(db, values):
    """Inside a transaction, before a passkey is added: the account keeps fewer than MAX_PASSKEYS, and no account has
    its credential yet (409 otherwise)."""
    count = db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (values["user_id"],)).fetchone()[0]
    if count >= MAX_PASSKEYS:
        raise PasskeyError(409, f"An account keeps at most {MAX_PASSKEYS} passkeys; remove one first.")
    if db.execute("SELECT 1 FROM passkeys WHERE credential_id = ?", (values["credential_id"],)).fetchone():
        raise PasskeyError(409, "That passkey is already added.")


def insert_passkey(db, values):
    """Inside a transaction: add the passkey register made (`values`, a row of passkeys), checked as check_new_passkey
    checks it: at once, or when the hold of one added while end-to-end encryption is on ends (ade/e2e_keys.py)."""
    check_new_passkey(db, values)
    if values.get("name_by_server"):        # another may have taken its name while it waited on its hold
        values["name"] = unique_name(db, values["user_id"], values["name"])
    try:
        db.execute(f"INSERT INTO passkeys ({', '.join(_PASSKEY_COLUMNS)}) VALUES "
                   f"({','.join('?' * len(_PASSKEY_COLUMNS))})",
                   tuple(values.get(c) if c in _KEPT_SINCE else values[c] for c in _PASSKEY_COLUMNS))
    except sqlite3.IntegrityError:
        raise PasskeyError(409, "That passkey is already added.") from None


def for_user(user_id):
    """The account's passkeys as the Account page lists them: no key material; the fingerprint a session pins; its
    kind (kind_of); the tier, the model's AAGUID and maker, for a device passkey why, and for a hardware key whether its working with a
    phone rests on the browser's word and why it is reported, when it is kept as a hardware key all the same
    (reconsider). One waiting for the FIDO metadata is judged again first, when a valid blob is kept, and the hardware
    keys are reconsidered against a blob new since."""
    with store.connect() as db:
        waiting = db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ? AND judge_again = 1",
                             (user_id,)).fetchone()[0]
    if waiting:
        judge_again(user_id=user_id)
    reconsider()                            # a blob put here by hand, with no refresher to bring it
    with store.connect() as db:
        rows = db.execute("SELECT id, name, created_at, last_used_at, backed_up, backup_eligible, attachment, "
                          "public_key, tier, aaguid, maker, tier_why, judge_again, phone_by_browser, tier_report "
                          "FROM passkeys WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "last_used_at": r["last_used_at"],
             "backed_up": bool(r["backed_up"]), "fingerprint": fingerprint(r["public_key"]), "tier": r["tier"],
             "aaguid": r["aaguid"], "maker": r["maker"],
             "why": None if r["tier"] == HARDWARE else r["tier_why"] or OLD_PASSKEY,
             "judge_again": bool(r["judge_again"]),
             "phone_by_browser": r["tier"] == HARDWARE and bool(r["phone_by_browser"]),
             "reported": r["tier_report"],
             "kind": kind_of(r["tier"], r["backup_eligible"], r["backed_up"], r["attachment"])} for r in rows]


def awaiting_judgment():
    """How many passkeys wait for a valid FIDO metadata blob to be judged."""
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM passkeys WHERE judge_again = 1").fetchone()[0]


def judge_again(now=None, user_id=None):
    """Judge each passkey added while there was no valid FIDO metadata (of `user_id`, or every account's) against the
    blob kept, when one is valid: its kept attestation, as it was given, at this time. Its attestation is dropped once
    it is judged. Returns how many were judged."""
    now = _now(now)
    blob = fido_mds.kept(now)
    if blob is None:
        return 0
    with store.connect() as db:
        rows = db.execute("SELECT id, attestation FROM passkeys WHERE judge_again = 1"
                          + (" AND user_id = ?" if user_id is not None else ""),
                          (user_id,) if user_id is not None else ()).fetchall()
    judged = 0
    for r in rows:
        try:
            held = json.loads(r["attestation"])
            attestation = cbor_whole(unb64u(held["object"], "attestation"))
            authenticator_data(attestation["authData"], attested=True)       # as register read it
            verdict = hardware_keys.judge(attestation, unb64u(held["client_data_hash"], "client data hash"),
                                          _transports(held.get("transports")), lambda: blob, now)
        except (PasskeyError, ValueError, KeyError, TypeError, RecursionError):
            verdict = hardware_keys.device(UNREAD_ATTESTATION)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            done = db.execute("UPDATE passkeys SET tier = ?, aaguid = COALESCE(?, aaguid), maker = ?, tier_why = ?, "
                              "judge_again = ?, attestation = CASE WHEN ? THEN attestation END, "
                              "phone_by_browser = ?, attestation_certs = ? WHERE id = ? AND judge_again = 1",
                              (verdict.tier, verdict.aaguid, verdict.maker, verdict.why, int(verdict.again),
                               int(verdict.again), int(verdict.by_browser), _certs_column(verdict),
                               r["id"])).rowcount
            if done:
                rename_by_kind(db, r["id"])     # a name the server gave follows what it is found to be
            judged += done
    return judged


def _certs_column(verdict):
    # the sha256 of each certificate of a hardware key's attestation, which a later report may be about (reconsider)
    return json.dumps(list(verdict.certs)) if verdict.tier == HARDWARE and verdict.certs else None


def reconsider(now=None):
    """Against the valid blob kept, when it is new since the last time (its serial number above the one recorded),
    whether each hardware key's model is now reported revoked or compromised (hardware_keys.reported): such a key
    becomes a device passkey, saying why, and its account's devices are told a hardware key was reported. A key is never
    made a hardware key here. While an account's high security is on and every hardware key it has is reported, none is
    made a device passkey, since the changes only a hardware key makes (turning high security off among them) would be
    locked: each is marked reported instead (tier_report), the page says so and to add another hardware key and remove
    it, and the devices are told the same as for another. Returns how many keys were made device passkeys."""
    from .ade import push
    now = _now(now)
    blob = fido_mds.kept(now)
    if blob is None:
        return 0
    told, made = [], 0
    if _reconsidered(store_db=None) >= blob.no:
        return 0                            # the usual case, with no write lock taken: the page lists keys often
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _reconsidered(db) >= blob.no:    # another request or the refresher was first
            return 0
        found = {}
        for r in db.execute("SELECT id, user_id, name, aaguid, attestation_certs FROM passkeys WHERE tier = ?",
                            (HARDWARE,)).fetchall():
            why = hardware_keys.reported(blob.entries.get(r["aaguid"]), tuple(_certs(r["attestation_certs"])))
            if why:
                found.setdefault(r["user_id"], []).append((r, why))
        for user_id, hits in found.items():
            if _high_security(db, user_id) and len(hits) >= _hardware_keys(db, user_id):
                for r, why in hits:
                    db.execute("UPDATE passkeys SET tier_report = ? WHERE id = ?", (why, r["id"]))
                    told.append(user_id)
                continue
            for r, why in hits:
                db.execute("UPDATE passkeys SET tier = ?, tier_why = ?, tier_report = NULL WHERE id = ?",
                           (hardware_keys.DEVICE, why, r["id"]))
                rename_by_kind(db, r["id"])     # a name the server gave follows what it now is
                told.append(user_id)
                made += 1
        db.execute("INSERT INTO passkey_reconsidered (id, no, at) VALUES (1, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                   "no = excluded.no, at = excluded.at", (blob.no, now))
    # in fixed words (push.words), never the passkey's name or the report's: the page says which, why, and what to do;
    # once for each account, however many of its keys were reported, so a lock screen shows no count of them
    for user_id in dict.fromkeys(told):
        push.notice(user_id, push.words("hardware-key-reported"), now)
    return made


def _reconsidered(store_db):
    """The serial number of the last blob the hardware keys were reconsidered against, or -1, read in `store_db`'s
    transaction or a connection of its own."""
    if store_db is None:
        with store.connect() as db:
            return _reconsidered(db)
    row = store_db.execute("SELECT no FROM passkey_reconsidered WHERE id = 1").fetchone()
    return row["no"] if row is not None else -1


def _certs(column):
    try:
        held = json.loads(column) if column else []
    except ValueError:
        return []
    return [c for c in held if isinstance(c, str)] if isinstance(held, list) else []


def blob_arrived(now=None):
    """What the refresher does with a valid blob it has (fido_mds.tick): judge the passkeys waiting for one, then
    reconsider the hardware keys against it when it is new."""
    judge_again(now)
    reconsider(now)


def remove(user_id, passkey_id, confirmation=None, now=None):
    """Remove one of the account's passkeys, using up a confirmation (confirm) in the same transaction: a passkey that
    is not there leaves the confirmation unused. What it signed goes with it, in the same transaction: its commands
    and moves no session has taken are withdrawn (a move's row says so, ade/moves.py), a move it signed that waits on
    its hold is never stored (its hold ends, failed, saying why), and a link whose stored resume it signed is stopped
    again, for the owner to resume with another passkey, however old that resume (_withdraw_signed); a ticket it
    confirmed that no session has used is voided (ade/link.py void_tickets); and each approval of the account still
    held that names no passkey (a request to link a session, whose confirmation does not record which way gave it) is
    cancelled (ade/holds.cancel_all), since it may have been confirmed with that passkey; and every app connection it
    allowed, in effect or waiting, is revoked (oauth.passkeys_going); and each version of a task's grant it signed is
    withdrawn (ade/task_grants.passkeys_going). So a lost device's passkey, once removed, leaves nothing it signed or
    confirmed for a session to act on, nothing it approved to take effect, no grant it signed standing, and no app it
    connected. While high security is on, the account's last hardware key is not removed. While end-to-end encryption
    is on, a passkey is removed only by a revocation (ade/e2e_keys.py), which replaces the master key so the removed
    one opens nothing: 409 here. {removed, withdrawn, stopped, cancelled, disconnected: the connections revoked, apps:
    what that says of the account's connected apps (oauth.lost_device_words)}."""
    from . import oauth
    from .ade import e2e_keys, holds
    now = _now(now)
    holds.settle(user_id, now)          # what has taken effect has; what has not is cancelled below
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if e2e_keys.on(db, user_id):
            raise PasskeyError(409, REVOKES_ONLY)
        _use_confirmation(db, user_id, confirmation, now, subject=f"passkey-remove:{passkey_id}")
        withdrawn, stopped, disconnected = remove_in(db, user_id, passkey_id, now)
        cancelled = holds.cancel_all(db, user_id, now)
    return {"removed": passkey_id, "withdrawn": withdrawn, "stopped": stopped, "cancelled": cancelled,
            "disconnected": disconnected, "apps": oauth.lost_device_words(len(disconnected))}


def remove_in(db, user_id, passkey_id, now):
    """Inside a change's own transaction, confirmed already: remove one of the account's passkeys and withdraw what it
    signed (_withdraw_signed, and the versions of its tasks' grants: ade/task_grants.passkeys_going), void the unused
    tickets it confirmed, revoke the app connections it allowed (oauth.passkeys_going), and end a change to end-to-end
    encryption that waits with a statement it signed (ade/e2e_keys.passkey_removed); 404 for one that is not there, 409
    for the last hardware key while high security is on. (number withdrawn, the links stopped again, the connections
    revoked)."""
    from . import oauth
    from .ade import e2e_keys, link, moves, task_grants
    row = db.execute("SELECT tier FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, user_id)).fetchone()
    if row is None:
        raise PasskeyError(404, "No such passkey.")
    if row["tier"] == HARDWARE and _high_security(db, user_id) and _hardware_keys(db, user_id) <= 1:
        raise PasskeyError(409, LAST_HARDWARE_KEY)
    db.execute("DELETE FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, user_id))
    e2e_keys.passkey_removed(db, user_id, passkey_id, now)
    withdrawn, stopped = _withdraw_signed(db, user_id, now, "= ?", (passkey_id,), moves.WITHDRAWN)
    # and the versions of its tasks' grants it signed, so that none of them stands
    task_grants.passkeys_going(db, user_id, "= ?", (passkey_id,), task_grants.REMOVED, now)
    link.void_tickets(db, user_id, "confirmed_by = ?", (passkey_id,))
    # and the devices its word approved: refused if they have not collected their sign-ins, ended if they have
    devices_approved_by(db, user_id, [passkey_id], now)
    disconnected = oauth.passkeys_going(db, user_id, [passkey_id], oauth.REMOVED_WHY, now)
    return withdrawn, stopped, disconnected


def rename(user_id, passkey_id, name, confirmation=None, now=None):
    """One of the account's passkeys given another name (_name's rules), or, given none, named by what it is again
    (named_by_kind, from what was read as it was added, told apart from the others' names: unique_name), and so named
    again whenever what it is changes (rename_by_kind), until the person names it. It uses up a confirmation made for
    that passkey's rename (confirm) in the same transaction, as adding or removing one does: the owner removes, and
    revokes from encryption, by these names, so a sign-in alone, which a lost device may still hold, does not swap
    them; while end-to-end encryption is on, a passkey's word, never the password's, and under high security a
    hardware key's. Its tier and kind are the server's and do not change. A refusal (400 for a name, 404 for one the
    account does not have) leaves the confirmation unused. Returns it as the page lists it (for_user)."""
    from .ade import e2e_keys
    if not isinstance(name, str):
        raise PasskeyError(400, "A passkey's new name is text.")
    given = _name(name)
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT tier, maker, tier_why, backup_eligible, backed_up, attachment FROM passkeys "
                         "WHERE id = ? AND user_id = ?", (passkey_id, user_id)).fetchone()
        if row is None:
            raise PasskeyError(404, "No such passkey.")
        _use_confirmation(db, user_id, confirmation, now,
                          passkey=NEEDS_PASSKEY_ALONE if e2e_keys.on(db, user_id) else None,
                          subject=f"passkey-rename:{passkey_id}")
        new = given if given is not None else unique_name(db, user_id, named_by_kind(
            kind_of(row["tier"], row["backup_eligible"], row["backed_up"], row["attachment"]),
            model_of(row["maker"], row["tier_why"])), passkey_id)
        db.execute("UPDATE passkeys SET name = ?, name_by_server = ? WHERE id = ? AND user_id = ?",
                   (new, int(given is None), passkey_id, user_id))
    for k in for_user(user_id):
        if k["id"] == passkey_id:
            return k
    raise PasskeyError(404, "No such passkey.")                # removed in the moment since


def _withdraw_signed(db, user_id, now, which, args, reason):
    """Inside a change's own transaction, what the account's passkeys matching `which` (SQL after "passkey_id", with
    `args`) signed and a session could still act on: their commands and moves no session has taken are withdrawn, and
    each move one of them signed that waits on its hold ends, a move saying why in `reason` (ade/moves.py); and each
    live link whose stored resume one of them signed is stopped again, to be resumed with another passkey, however old
    that resume is: a resume never expires for the stop it was made for, and a new stop clears the stored one, so a
    stored resume is the one lifting the link's stop now. (number withdrawn, held moves ended included; the links
    stopped again)."""
    from .ade import moves
    withdrawn = db.execute(
        f"UPDATE ade_link_events SET withdrawn_at = ? WHERE user_id = ? AND passkey_id {which} AND link_id = 'owner' "
        "AND kind IN ('instruction', 'answer', 'move') AND taken_at IS NULL AND withdrawn_at IS NULL AND "
        "expires_at > ?", (now, user_id, *args, now)).rowcount
    withdrawn += moves.withdrawn_signed(db, user_id, now, which, args, reason)
    stopped = [r["id"] for r in db.execute(f"SELECT id FROM ade_links WHERE user_id = ? AND resume_passkey_id {which} "
                                           "AND revoked_at IS NULL", (user_id, *args)).fetchall()]
    for link_id in stopped:
        db.execute("UPDATE ade_links SET stopped_at = ?, stop_acked_at = NULL, resume_payload = NULL, "
                   "resume_signature = NULL, resume_passkey_id = NULL WHERE id = ?", (now, link_id))
    return withdrawn, stopped


# ---- signing in ----------------------------------------------------------------------------------------------------

def signin_options(purpose, now=None):
    """The options the page signs in with, through navigator.credentials.get() over the challenge derived from this
    one for SIGN_IN (derived_challenge), which the page names as `purpose`: a challenge for no account in particular, so
    the browser offers this site's passkeys and nothing says whether an address has an account. A page that names no
    purpose is from an older release (OLD_PAGE)."""
    if purpose is None:
        raise PasskeyError(400, OLD_PAGE)
    if purpose != SIGN_IN:
        raise PasskeyError(422, f"A sign-in's options are asked for with the purpose {SIGN_IN}.")
    challenge_id, challenge = _new_challenge("signin", None, _now(now))
    return {"challenge_id": challenge_id, "publicKey": {
        "challenge": b64u(challenge), "rpId": rp_id(), "timeout": TIMEOUT_MS, "userVerification": "required",
        "allowCredentials": []}}


def _assertion(credential, challenge, expires_at, now, purpose, owner=None):
    """The passkey row a WebAuthn assertion proves, made over the challenge derived from `challenge` (the bytes this
    server picked) for `purpose` (derived_challenge), its counter moved on in the transaction that records `challenge`
    used; with `owner`, the passkey must be that account's. PasskeyError on anything wrong."""
    if not isinstance(credential, dict) or credential.get("type") != "public-key" \
            or not isinstance(credential.get("response"), dict) or not isinstance(credential.get("id"), str):
        raise PasskeyError(400, "That is not a passkey's answer.")
    response = credential["response"]
    client_raw = unb64u(response.get("clientDataJSON"), "client data")
    auth_raw = unb64u(response.get("authenticatorData"), "authenticator data")
    signature = unb64u(response.get("signature"), "signature")
    handle = response.get("userHandle")
    with store.connect() as db:
        row = db.execute("SELECT * FROM passkeys WHERE credential_id = ?", (credential["id"],)).fetchone()
    if row is None:
        raise PasskeyError(401, "unknown credential")
    if owner is not None and row["user_id"] != owner:
        raise PasskeyError(401, "a passkey of another account")
    # a handle, given, must name the passkey's own account; with no account named before the ceremony (signing in) it
    # must be given (WebAuthn L3 7.2)
    if (handle is None and owner is None) or \
            (handle is not None and unb64u(handle, "user handle") != row["user_id"].encode("ascii")):
        raise PasskeyError(401, "the user handle is absent or names another account")
    check_client_data(client_raw, "webauthn.get", derived_challenge(purpose, challenge))
    auth = authenticator_data(auth_raw, attested=False)
    check_flags(auth, "sign-in")
    if not verify_signature(row["alg"], unb64u(row["public_key"], "key"), signature,
                            auth_raw + hashlib.sha256(client_raw).digest()):
        raise PasskeyError(401, "bad signature")
    held = row["sign_count"]
    if (held or auth["count"]) and auth["count"] <= held:
        raise PasskeyError(401, "the signature counter went back: a copy of this passkey may exist")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _check_backup_eligible_now(db, row["id"], auth)
        done = db.execute("UPDATE passkeys SET sign_count = ?, last_used_at = ?, backed_up = ?, "
                          "backup_eligible = COALESCE(backup_eligible, ?) WHERE id = ? AND sign_count = ?",
                          (auth["count"], now, int(auth["backed_up"]), int(auth["backup_eligible"]), row["id"],
                           held)).rowcount
        if not done:
            raise PasskeyError(401, "another sign-in used this counter first")
        _use_challenge(db, challenge, expires_at, now)
    return row


def sign_in(challenge_id, credential, now=None):
    """The user id a passkey's signature proves, or PasskeyError(401) with one wording whatever was wrong."""
    now = _now(now)
    challenge, expires_at = _open_challenge(challenge_id, "signin", None, now)
    try:
        row = _assertion(credential, challenge, expires_at, now, SIGN_IN)
    except PasskeyError:
        raise PasskeyError(401, SIGN_IN_FAILED) from None
    return row["user_id"]


# ---- confirming a change: one of the account's own passkeys, or its password ---------------------------------------

def _needs_confirmation(db, user_id):
    """Whether the account has a password or a passkey to confirm a change with (the desktop server's own has none)."""
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is not None and row["password_hash"]:
        return True
    return db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0] > 0


def _high_security(db, user_id):
    row = db.execute("SELECT high_security FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row is not None and row["high_security"])


def _hardware_keys(db, user_id):
    return db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ? AND tier = ?", (user_id, HARDWARE)).fetchone()[0]


def high_security(user_id):
    """Whether the account's high security is on."""
    with store.connect() as db:
        return _high_security(db, user_id)


def confirm_ways(user_id):
    """What the page may ask the person for: {"needed": whether a change needs a confirmation, "password": whether
    the account has one, "passkeys": how many passkeys it has, "hardware": how many of them are hardware keys,
    "high_security": whether it is on, when only a hardware key confirms, "e2e": whether end-to-end encryption is on,
    when the password confirms no change to a passkey}."""
    from .ade import e2e_keys
    with store.connect() as db:
        row = db.execute("SELECT password_hash, high_security FROM users WHERE id = ?", (user_id,)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user_id,)).fetchone()[0]
        hardware = _hardware_keys(db, user_id)
        on = bool(e2e_keys.on(db, user_id))
    password = bool(row is not None and row["password_hash"])
    return {"needed": password or count > 0, "password": password, "passkeys": count, "hardware": hardware,
            "high_security": bool(row is not None and row["high_security"]), "e2e": on}


class Confirmed(typing.NamedTuple):
    """A confirmation used up: its kind (CONFIRMED or CONFIRMED_HARDWARE) and the passkey that made it (None for the
    password)."""
    kind: str
    passkey_id: typing.Optional[str]


def _use_confirmation(db, user_id, confirmation, now, hardware=None, passkey=None, purpose=CONFIRM, subject=None):
    """Inside the change's own transaction: use up `confirmation`, which must be this account's, from confirm, under 5
    minutes old and not used before, when the account has a way to make one; made for `purpose`, the ceremony the
    page asked for this change (CONFIRM_PURPOSES: CONFIRM, a change's own, for every change but end-to-end
    encryption's touches; the password's is CONFIRM's), so a touch given for one purpose is spent on no other's change;
    and made for the change's `subject` (confirm), or for none where the change names none, so a
    confirmation made for one change is spent on no other. While its high security is on, or for a change that needs
    one whatever it is (`hardware`, the refusal's words), it must be one a hardware key made; for a change that needs a
    passkey's (`passkey`, the refusal's words: end-to-end encryption's, ade/e2e_keys.py), one a passkey made, never the
    password's. One of another kind, purpose or subject is refused and left unused. One a passkey made holds only
    while that passkey is still the account's and, for a hardware key's, still a hardware key. Returns what it used
    (Confirmed), or None when none was needed."""
    if hardware is None and _high_security(db, user_id):
        hardware = NEEDS_HARDWARE
    if hardware is None and passkey is None and not _needs_confirmation(db, user_id):
        return None
    if not confirmation:
        raise PasskeyError(403, hardware or passkey or NEEDS_CONFIRM)
    try:
        held, expires_at, key, signed_for, made_for = _open_challenge(
            confirmation, CONFIRMED_HARDWARE, user_id, now, with_key=True, with_signed_for=True, with_subject=True)
        kind = CONFIRMED_HARDWARE
    except PasskeyError:
        try:
            held, expires_at, key, signed_for, made_for = _open_challenge(
                confirmation, CONFIRMED, user_id, now, with_key=True, with_signed_for=True, with_subject=True)
            kind = CONFIRMED
        except PasskeyError:
            raise PasskeyError(403, CONFIRM_STALE) from None
        if hardware:
            raise PasskeyError(403, hardware) from None
    if passkey and key is None:                     # the password's, which names no passkey
        raise PasskeyError(403, passkey)
    if signed_for != purpose or made_for != subject:
        # one made for no change of its own, given for one that names its change (every change but encryption's
        # touches), is what a page from an older release makes: it is told to reload
        raise PasskeyError(403, CONFIRM_FOR_NOTHING if signed_for == purpose and made_for is None else CONFIRM_OTHER)
    if not _still_holds(db, user_id, key, kind == CONFIRMED_HARDWARE):
        raise PasskeyError(403, CONFIRM_GONE)
    try:
        _use_challenge(db, held, expires_at, now)
    except PasskeyError:
        raise PasskeyError(403, CONFIRM_STALE) from None
    return Confirmed(kind, key)


def _still_holds(db, user_id, key, hardware):
    """Whether the word of the passkey `key` (None: the password's) still stands: the passkey is still the account's
    and, where `hardware`, still a hardware key. A hardware key's word always names its key."""
    if key is None:
        return not hardware
    row = db.execute("SELECT tier FROM passkeys WHERE id = ? AND user_id = ?", (key, user_id)).fetchone()
    return row is not None and (not hardware or row["tier"] == HARDWARE)


def use_passkey_confirmation(db, user_id, confirmation, now, refused, only_by=None, not_by_words=None, *, purpose,
                             subject=None):
    """Inside the change's own transaction: use up `confirmation` (_use_confirmation), which must be one that one of the
    account's own passkeys made for `purpose` (one of end-to-end encryption's touches, which each caller names), never
    the password's (`refused`, the refusal's words), and, given `only_by` (passkey ids), one that a passkey of those
    made (`not_by_words`, the refusal's words otherwise); made for `subject`, the change it is for, where the touch
    names one (the upgrade's: e2e-upgrade), and for none otherwise. Returns the id of the passkey that made it. A
    refusal raises, and the caller's transaction, uncommitted, leaves the confirmation unused. (End-to-end
    encryption's holds are cancelled with one from a passkey e2e_keys.cancellers names.)"""
    used = _use_confirmation(db, user_id, confirmation, now, passkey=refused, purpose=purpose, subject=subject)
    if only_by is not None and used.passkey_id not in only_by:
        raise PasskeyError(403, not_by_words or refused)
    return used.passkey_id


def use_confirmation(user_id, confirmation, subject, now=None):
    """_use_confirmation in a transaction of its own, for a change made elsewhere (auth.sign_out_everywhere: `subject`
    is the change it is made for, CHANGE's)."""
    now = _now(now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _use_confirmation(db, user_id, confirmation, now, subject=subject)


def change_subject(change):
    """The subject a confirmation for `change` is made for (CHANGE: the page names the change it asks a confirmation
    for, and each change spends only one made for it): `change` itself when it is one of CHANGE's forms, written as
    CHANGE writes it; PasskeyError(422) for anything else."""
    if not isinstance(change, str) or not CHANGE.fullmatch(change):
        raise PasskeyError(422, BAD_CHANGE)
    return change


def confirm_options(user, purpose, hardware=False, now=None, subject=None):
    """The options the page confirms a change with, with one of this account's own passkeys, through
    navigator.credentials.get() over the challenge derived from this one for `purpose` (one of CONFIRM_PURPOSES: the
    page names the ceremony it asks for, and the challenge id keeps it, so confirm checks the challenge derived for that
    purpose and no other): a challenge for this account, its passkeys the ones offered; its hardware keys alone while
    high security is on, or when the page asks for a hardware key's confirmation (`hardware`, to turn high security
    on). With `subject`, the confirmation it gives is for that one change (confirm). A page that names no purpose is
    from an older release (OLD_PAGE)."""
    if purpose is None:
        raise PasskeyError(400, OLD_PAGE)
    if purpose not in CONFIRM_PURPOSES:
        raise PasskeyError(422, "A confirmation's purpose is " + ", ".join(CONFIRM_PURPOSES) + ".")
    with store.connect() as db:
        only = hardware or _high_security(db, user["id"])
        have = [r["credential_id"] for r in db.execute(
            "SELECT credential_id FROM passkeys WHERE user_id = ?" + (" AND tier = ?" if only else ""),
            (user["id"], HARDWARE) if only else (user["id"],))]
    if not have:
        raise PasskeyError(409, NO_HARDWARE_KEY if only else NO_PASSKEY)
    challenge_id, challenge = _new_challenge("confirm", user["id"], _now(now), signed_for=purpose, subject=subject)
    return {"challenge_id": challenge_id, "publicKey": {
        "challenge": b64u(challenge), "rpId": rp_id(), "timeout": TIMEOUT_MS, "userVerification": "required",
        "allowCredentials": [{"type": "public-key", "id": c} for c in have]}}


def confirm(user, challenge_id=None, credential=None, password=None, now=None, client="", subject=None):
    """{confirmation, hardware}: proof, for 5 minutes and one use, that the person at the page just gave the account's
    password or signed with one of its own passkeys, saying whether a hardware key made it; PasskeyError(403) with one
    wording whatever was wrong. A passkey signs the challenge derived for the purpose its options were asked for
    (confirm_options), never the challenge itself. A password attempt counts as one at sign-in does (auth.attempt),
    and past the count none is checked. While high security is on, a password is not checked at all and a device
    passkey's signature confirms nothing: either is refused. With `subject` (a device's request to be signed in,
    auth.approval_subject), the confirmation is for that one change, and _use_confirmation spends it on nothing else;
    a passkey's is for what its options were asked for, which `subject`, given too, must name."""
    now = _now(now)
    if password is not None:
        if high_security(user["id"]):
            raise PasskeyError(403, NEEDS_HARDWARE)
        account = user.get("email") or user["id"]          # counted as signing in counts it: by its address
        with store.connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        stored = row["password_hash"] if row is not None and row["password_hash"] else auth._dummy_hash()
        if not auth.counted_check(account, client, password, stored, doing="confirm") or row is None:
            raise PasskeyError(403, CONFIRM_FAILED)
        auth.password_right(account, client)
        # the password confirms a change, none of the touches; for `subject` if the page named one
        made_by_hardware, key, purpose, made_for = False, None, CONFIRM, subject
    else:
        challenge, expires_at, purpose, made_for = _open_challenge(challenge_id, "confirm", user["id"], now,
                                                                   with_signed_for=True, with_subject=True)
        if subject is not None and subject != made_for:
            # checked before the answer is, so a request that names another change uses up nothing; the passkey may
            # well have confirmed, but what its options were asked for
            raise PasskeyError(403, CONFIRM_OTHER)
        try:
            row = _assertion(credential, challenge, expires_at, now, purpose, owner=user["id"])
        except PasskeyError:
            raise PasskeyError(403, CONFIRM_FAILED) from None
        made_by_hardware, key = row["tier"] == HARDWARE, row["id"]
        if not made_by_hardware and high_security(user["id"]):
            raise PasskeyError(403, NEEDS_HARDWARE)
    # the confirmation names the passkey that made it, so it holds only while that passkey does, the purpose it was
    # made for, so it is spent on that purpose's change alone, and the change it is for, if one, so it is spent on that
    # alone (_use_confirmation)
    confirmation, _ = _new_challenge(CONFIRMED_HARDWARE if made_by_hardware else CONFIRMED, user["id"], now, key=key,
                                     signed_for=purpose, subject=made_for)
    return {"confirmation": confirmation, "hardware": made_by_hardware}


def set_high_security(user_id, on, confirmation=None, now=None):
    """Turn the account's high security on or off, using up a hardware key's confirmation (confirm) in the same
    transaction, which leaves it unused when the change is refused. Turning it on needs a hardware key kept, and no
    passkey waiting to be judged (one added without a hardware key's word could become a hardware key later); it
    withdraws, in the same transaction, what the account's other passkeys signed that a session could still act on, as
    removing them would (_withdraw_signed), voids the unused link tickets no hardware key confirmed, and cancels each
    approval of the account still held that names no passkey (ade/holds.cancel_all: a request to link a session),
    which the password or a device passkey may have confirmed. While end-to-end encryption is on, it is turned on only
    by a revocation (ade/e2e_keys.py), which leaves the other passkeys out of the new master key: 409 here.
    {high_security, withdrawn, stopped, cancelled}."""
    from .ade import e2e_keys, holds
    if not isinstance(on, bool):
        raise PasskeyError(422, "Say whether high security is on: {on: true or false}.")
    now = _now(now)
    withdrawn, stopped, cancelled = 0, [], []
    if on:
        holds.settle(user_id, now)      # what has taken effect has; what has not is cancelled below
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if on and e2e_keys.on(db, user_id):
            raise PasskeyError(409, REVOKES_ONLY)
        if on:
            check_high_security(db, user_id)
        _use_confirmation(db, user_id, confirmation, now, hardware=NEEDS_HARDWARE_TO_TURN,
                          subject="high-security-on" if on else "high-security-off")
        if on:
            withdrawn, stopped, cancelled = high_security_in(db, user_id, now)
        else:
            db.execute("UPDATE users SET high_security = 0 WHERE id = ?", (user_id,))
    return {"high_security": on, "withdrawn": withdrawn, "stopped": stopped, "cancelled": cancelled}


def check_high_security(db, user_id):
    """Inside a transaction, before high security is turned on: a hardware key kept, and no passkey waiting to be
    judged (409 otherwise)."""
    if not _hardware_keys(db, user_id):
        raise PasskeyError(409, NO_HARDWARE_KEY)
    if db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ? AND judge_again = 1", (user_id,)).fetchone()[0]:
        raise PasskeyError(409, KEY_WAITING)


def high_security_in(db, user_id, now, after=None):
    """Inside a change's own transaction, confirmed and checked already (check_high_security): high security on; what
    the account's other passkeys signed withdrawn (the versions of its tasks' grants among it:
    ade/task_grants.passkeys_going), the unused link tickets no hardware key confirmed voided, each approval still held
    that names no passkey cancelled, the caller having settled what has taken effect, and the other passkeys'
    credentials no longer unlocking end-to-end encryption (ade/e2e_keys.high_security_turned_on). `after`: as
    holds.cancel_all takes it, for a held change applied later than its own time. (number withdrawn, the links stopped
    again, the holds cancelled)."""
    from .ade import e2e_keys, holds, link, moves, task_grants
    db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (user_id,))
    e2e_keys.high_security_turned_on(db, user_id)
    withdrawn, stopped = _withdraw_signed(
        db, user_id, now, "NOT IN (SELECT id FROM passkeys WHERE user_id = ? AND tier = ?)", (user_id, HARDWARE),
        moves.HIGH_SECURITY)
    # and the versions of its tasks' grants they signed: only a hardware key signs one now
    task_grants.passkeys_going(db, user_id, "NOT IN (SELECT id FROM passkeys WHERE user_id = ? AND tier = ?)",
                               (user_id, HARDWARE), task_grants.HIGH_SECURITY, now)
    # a ticket no hardware key confirmed would link a session that high security never vouched for
    link.void_tickets(db, user_id, "(confirmed IS NULL OR confirmed <> ?)", (link.TICKET_BY_HARDWARE,))
    # and a device's approval no hardware key confirmed would sign one in, for 7 days, after it is on
    devices_not_by_hardware(db, user_id, now)
    # so would an approval still held: it does not record what confirmed it, so none may take effect after
    cancelled = holds.cancel_all(db, user_id, now, after)
    return withdrawn, stopped, cancelled


def devices_approved_by(db, user_id, passkey_ids, now):
    """Inside a change's own transaction, as passkeys go (removed, or a revocation that removes them placed): each
    device whose approval one of them confirmed is refused if it has not collected its sign-in, and its sign-in ended
    if it has (auth.devices_going)."""
    ids = tuple(passkey_ids)
    return auth.devices_going(db, user_id, f"confirmed_by IN ({','.join('?' * len(ids))})", ids, now)


def devices_not_by_hardware(db, user_id, now):
    """Inside a change's own transaction, as high security goes on: each device approved and not yet signed in
    (auth.devices_going) whose approval no hardware key of the account confirmed is refused. One signed in already
    stays: turning high security on ends no sign-in."""
    return auth.devices_going(db, user_id, "(confirmed_by IS NULL OR confirmed_by NOT IN (SELECT id FROM passkeys "
                              "WHERE user_id = ? AND tier = ?))", (user_id, HARDWARE), now, end=False)


# ---- the owner's word on a payload the page composed (the session channel's commands, ade/link_commands.py) --------
#
# A command to a linked session is a JSON payload the page composes and the owner signs with one of the account's
# passkeys: the WebAuthn challenge is the sha256 of the payload's RFC 8785 bytes, so the signature binds the words. The
# standard wants the challenge's randomness from the relying party; here it comes from a nonce the server gave out,
# sealed under its MAC with the account and the purpose, which the payload carries whole and the server records as
# used. A session checks the same assertion itself, offline, against keys it pinned (ade_link.py show).

NONCE_PREFIX = "cmd:"
CANONICAL_DRIFT = "the page and the server do not agree on the canonical form"


def fingerprint(public_key):
    """The first 32 hex characters (128 bits) of the sha256 of a passkey's SubjectPublicKeyInfo DER bytes: what the
    Account page shows beside the passkey and what a session pins (ade_link.py pin)."""
    return hashlib.sha256(unb64u(public_key, "key")).hexdigest()[:32]


def owner_keys(user_id):
    """The account's passkeys as a linked session pins them: credential id, algorithm, public key, name, fingerprint.
    While high security is on, its hardware keys alone: those a session verifies a command against (ade_link.py
    pin drops a key the server no longer lists)."""
    with store.connect() as db:
        only = _high_security(db, user_id)
        rows = db.execute("SELECT credential_id, alg, public_key, name FROM passkeys WHERE user_id = ?"
                          + (" AND tier = ?" if only else "") + " ORDER BY created_at",
                          (user_id, HARDWARE) if only else (user_id,)).fetchall()
    return [dict(r, fingerprint=fingerprint(r["public_key"])) for r in rows]


def signing_credentials(user_id):
    """The credential ids a command's signature is asked of (the page's allowCredentials): the account's hardware
    keys while high security is on, else none named, which lets the browser offer any of its passkeys."""
    with store.connect() as db:
        if not _high_security(db, user_id):
            return []
        return [r["credential_id"] for r in db.execute(
            "SELECT credential_id FROM passkeys WHERE user_id = ? AND tier = ? ORDER BY created_at",
            (user_id, HARDWARE))]


def _may_sign(db, passkey_id, user_id):
    """Whether the passkey may sign a command: any of the account's while high security is off, a hardware key's
    while it is on."""
    if not _high_security(db, user_id):
        return True
    row = db.execute("SELECT tier FROM passkeys WHERE id = ?", (passkey_id,)).fetchone()
    return row is not None and row["tier"] == HARDWARE


def new_nonce(user_id, purpose, now=None):
    """A nonce for one signed payload of `purpose`, good for 300 seconds; nothing is written until a payload uses it."""
    nonce, _ = _new_challenge(NONCE_PREFIX + purpose, user_id, _now(now))
    return nonce


def open_nonce(nonce, user_id, purpose, now):
    """The nonce's challenge bytes and expiry when this server gave it to this account for this purpose and it has not
    expired; PasskeyError(400) otherwise. Whether it was used is checked by use_nonce."""
    try:
        return _open_challenge(nonce, NONCE_PREFIX + purpose, user_id, now)
    except PasskeyError:
        raise PasskeyError(400, "the nonce is not one this server gave this account for this purpose, or it expired") \
            from None


def use_nonce(db, nonce, user_id, purpose, keep_until, now):
    """Inside the transaction that stores the payload: record the nonce as used, kept until `keep_until` (the payload's
    own expiry) so a replay is refused for as long as the payload could be acted on."""
    challenge, expires_at = open_nonce(nonce, user_id, purpose, now)
    try:
        _use_challenge(db, challenge, max(keep_until, expires_at), now)
    except PasskeyError:
        raise PasskeyError(400, "the nonce was used already") from None


def nonce_used(db, challenge, now):
    """Whether a payload stored already used the nonce whose challenge is `challenge` (open_nonce's bytes), asked
    without using it: the relay (ade/signers.py) parks no step its route would refuse for that."""
    return db.execute("SELECT 1 FROM passkey_used_challenges WHERE challenge_hash = ? AND expires_at > ?",
                      (hashlib.sha256(challenge).hexdigest(), now)).fetchone() is not None


def check_command(user_id, canonical, credential, counted=True):
    """The passkey of `user_id` whose assertion signs `canonical`, a payload's RFC 8785 bytes: the client data's
    challenge is the base64url of their step challenge (step_challenge). Returns (the passkey row, the authenticator
    data); the caller moves the counter on (commit_counter) in the transaction that stores the payload. PasskeyError
    names the check that failed. `counted` False leaves out the counter's check, for a statement signed long ago and
    sent in again after a restore (ade/e2e_keys.upload), whose count the passkey has passed since."""
    if not isinstance(credential, dict) or credential.get("type") != "public-key" \
            or not isinstance(credential.get("response"), dict) or not isinstance(credential.get("id"), str):
        raise PasskeyError(400, "That is not a passkey's answer.")
    response = credential["response"]
    client_raw = unb64u(response.get("clientDataJSON"), "client data")
    auth_raw = unb64u(response.get("authenticatorData"), "authenticator data")
    signature = unb64u(response.get("signature"), "signature")
    handle = response.get("userHandle")
    with store.connect() as db:
        row = db.execute("SELECT * FROM passkeys WHERE credential_id = ?", (credential["id"],)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise PasskeyError(401, "the credential is not one of this account's passkeys")
    if handle is not None and unb64u(handle, "user handle") != user_id.encode("ascii"):
        raise PasskeyError(401, "the user handle names another account")
    check_client_data(client_raw, "webauthn.get", step_challenge(canonical), mismatch=CANONICAL_DRIFT)
    auth = authenticator_data(auth_raw, attested=False)
    check_flags(auth, "signature")
    if not verify_signature(row["alg"], unb64u(row["public_key"], "key"), signature,
                            auth_raw + hashlib.sha256(client_raw).digest()):
        raise PasskeyError(401, "the signature does not verify with that passkey")
    check_backup_eligible(row, auth)
    if counted and (row["sign_count"] or auth["count"]) and auth["count"] <= row["sign_count"]:
        raise PasskeyError(401, "the signature counter went back: a copy of this passkey may exist")
    return row, auth


def commit_counter(db, row, auth, now):
    """Move the passkey's counter on inside the payload's own transaction; of two uses of one count, the second loses.
    While the account's high security is on, a passkey that is not a hardware key is refused here, in the transaction
    that would store what it signed, so high security turned on after check_command still holds."""
    if not _may_sign(db, row["id"], row["user_id"]):
        raise PasskeyError(403, ONLY_HARDWARE_SIGNS)
    _check_backup_eligible_now(db, row["id"], auth)
    done = db.execute("UPDATE passkeys SET sign_count = ?, last_used_at = ?, backed_up = ?, "
                      "backup_eligible = COALESCE(backup_eligible, ?) WHERE id = ? AND sign_count = ?",
                      (auth["count"], now, int(auth["backed_up"]), int(auth["backup_eligible"]), row["id"],
                       row["sign_count"])).rowcount
    if not done:
        raise PasskeyError(401, "another signature used this counter first")
