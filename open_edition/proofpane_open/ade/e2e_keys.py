"""The account's keys for end-to-end encryption of what linked sessions send, and the owner's statements of them.

Once an account turns encryption on, what its linked machines and sessions send the owner on the paths that carry
their words (their events to the owner, their History rows, their conversations) is stored only as envelopes
(ade/e2e.py), which only the owner's browser opens. The keys live in two levels, each sealed or wrapped by the page so
that the server holds nothing that opens anything: each credential's own key pair, its private half wrapped under a
key its passkey's PRF output gives; a recovery key pair from the recovery code; a master key sealed to each credential
and to the recovery key (a grant each); the chain of earlier master keys, each wrapped under the next; the roster of
keys a new master key may be sealed to; and the content key pairs links seal to, each private half wrapped under the
current master key. The server keeps them as the page sends them, checks their form, and serves them back.

What links seal to is stated by the owner: a statement, signed with a passkey as a command is (ade/link_commands.py,
purposes e2e-key and e2e-off, so high security's rule covers them), numbered from 1 with the sha256 of the one before
(`prev`). The server takes one only as its newest + 1 with the right prev, naming a key no statement named before, and
only in the transaction that stores what makes its key openable. Each change the owner makes, turning encryption on
among them, is held (ade/holds.py: 180 seconds; 24 hours when the recovery code confirmed it, for turning encryption
off, for a revocation that replaces the recovery code, removes no passkey, or leaves no credential but its
signer's where there were others, and for the fold, a turn-on that upgrades the keys) with a notice to the account's devices, in fixed words, that a change to
encryption waits and when it takes effect (the page shows what it touches: ade/push.py words); its body waits
whole in ade_e2e_pending, one per account, served with the change that waits, is checked again when the hold ends, and
is discarded when the hold is cancelled. A change's statement is signed, and a change without one confirmed, by a
passkey whose credential unlocks the keys and stays after it, unless the recovery code's proof comes with it.

The recovery code is the root. The page's sign-in alone cancels no held change (holds.register: guard): one function
(cancellers) says who may, and cancelling (_guard), a change placed in the place of a waiting one (_in_place_of) and the
page's view all ask it. (i) The recovery code in force cancels any change. (ii) A passkey the change does not remove
cancels it, unless the change is code-backed (made by the recovery code, or with its proof). (iii) So does a passkey it
removes, when it is not code-backed, was signed by a passkey, and leaves no passkey besides its signer. Of the
passkeys, only those that could cancel under high security's rule count anywhere: a hardware key's while it is on.
(iv) Never the password, never the sign-in alone. (v) A change takes the place of the one that waits, of any kind, only
when the passkey that signed or confirmed it could cancel that one, or the recovery code's proof comes with it; it is
refused otherwise. So a code-backed change, which only the code cancels, is neither cancelled nor displaced by a
passkey alone, and an owner holding the code wins against one stolen passkey.

The owner's routes (ade/e2e_routes.py, under /api/ade/e2e, the page's sign-in):

    GET  /api/ade/e2e                      what the page unlocks and changes the keys with (account_view)
    POST /api/ade/e2e/turn-on              turn_on: encryption turned on, or on again (held)
    POST /api/ade/e2e/rotate               rotate: a new content key (held)
    POST /api/ade/e2e/revoke               revoke: a new master key, passkeys removed, high security on (held)
    POST /api/ade/e2e/credentials          enrol: a passkey that unlocks the keys (held)
    POST /api/ade/e2e/off                  off: encryption turned off (held)
    POST /api/ade/e2e/anchors              anchors: renewed anchors (held)
    POST /api/ade/e2e/recovery-proof       proof_challenge: what the recovery code's proof answers
    GET  /api/ade/e2e/reseal               reseal_page: what a post-quantum account stores sealed classical
    POST /api/ade/e2e/reseal               reseal: a batch of it sealed again (the recovery code's proof)

A link's (ade/link_routes.py, signed with its key): GET /api/ade/link/e2e?after=SEQ&limit=N (for_link: the statements
after a seq, by seq, a page at a time), POST /api/ade/link/e2e (upload: statements past the server's newest, after a
restore, held as a change is).

The bodies, every byte field base64url without padding, every id as written below:

- statement: {payload, credential}, as a command is signed: payload {v: 2, deployment, purpose: "e2e-key", account,
  nonce, id, seq, prev, key, key_id, retires, created_at, expires_at}, or purpose "e2e-off" without key, key_id and
  retires; its nonce from POST /api/ade/link/nonce for that purpose; a life of 60 to 600 seconds.
- content_key: {key_id, public_key, mk_id, wrapped}: the stated key (key and key_id as the statement has them), the
  master key it is wrapped under, its wrap (28 to 1,024 bytes).
- credential: {passkey_id, salt (32 bytes), public_key (32), wrapped (28 to 1,024)}.
- recovery: {public_key (32), anchor (28 to 1,024)}.
- grants: [{to: a credential_id or "recovery", sealed: an envelope's form, at most 512 bytes inside}].
- roster: 28 to 131,072 bytes (ROSTER_MAX). mk_id: 32 lowercase hex. chain: {previous: mk_id, wrapped (28 to 1,024)}.
- confirmation: from POST /api/account/confirm made with a passkey, never the password; recovery_proof:
  {challenge_id, proof}, from POST /api/ade/e2e/recovery-proof.

turn-on {statement, content_key, mk_id, credential, recovery, grants, roster, seal?, replace?}; an account that has
keys from before (encryption turned off) turns on again with {statement (retires all, signed by a passkey that unlocks
the keys), content_key} under its current master key; or, while a new master key is due (a passkey that unlocked the
keys was removed, or left out by high security, while encryption was off: rekey_due), with {statement, content_key,
mk_id, chain, grants, roster, content_keys} as a revocation makes them; or with the whole body and replace: true, which
forgets the keys from before (held a day). rotate {statement (retires grace), content_key, recovery_proof?}, at most
one a day (ROTATE_SETTING). revoke {statement (retires all), content_key, mk_id, chain, grants, roster, content_keys:
[{key_id, wrapped}] every earlier content key wrapped again, but those named in abandon: [key_id] (each under a master
key the page's keys do not lead back to, so none it can open or trust: left as they are), remove: [passkey_id],
high_security, recovery?, recovery_proof?}. credentials {credential, mk_id, grant (an envelope's form), roster,
confirmation | recovery_proof}, the confirmation another enrolled passkey's, never the one being added. off {statement
(e2e-off), recovery_proof?}. anchors {anchors: [{passkey_id, salt (32 bytes, not the one it has), wrapped,
confirmation?}], recovery_anchor?: {public_key: the recovery key it is of, anchor}, mk_id, confirmation |
recovery_proof}: each renewed anchor but the confirming passkey's own carries a confirmation its own passkey made, and
anchors may be empty only beside recovery_anchor. A statement's own signature is the passkey's confirmation of the body
it is in.

Those are a classical account's forms. A post-quantum account's are the same bodies with
kem: "x-wing" in each (_era): a key statement's payload is v 3 and names a 1,216-byte key, every new public key is that
long (the content key's, a credential's, the recovery key's), every wrap is 60 bytes, a credential's entry adds its
anchor (the change's mk_id), and every grant is in form 2, exactly 1,197 bytes. revoke with upgrade: true upgrades a
classical account to them (_upgrade_plan: credentials, recovery, kept and its two proofs). A turn-on with the whole body
(a first turn-on, or replace) makes them and no other: one without kem is refused (_whole_era), so a classical account
is one whose keys were made before that, and turning it on again under its keys keeps it classical. Turning a
classical account on again with a new master key made from the current one with upgrade: true and kem (the fold)
makes that key as the upgrade does and the account post-quantum, in one change: the
upgrade's parts, every passkey that stays given a new key at its own touch (a turn-on removes none), its proofs made
for purpose turn-on, held as the upgrade is held.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .. import config, oauth, passkeys, store
from . import e2e, holds, link, link_commands, proposals, push
from proofpane_core.canonical import canonicalize

E2E_KEY, E2E_OFF = link_commands.E2E_KEY, link_commands.E2E_OFF
# the holds a change waits on: confirmed by a passkey (its statement's signature, or a confirmation); confirmed by the
# recovery code, which waits the longer; and a change that replaces the keys, the recovery code or every other
# credential, which waits as long, so the owner has the time to cancel one an attacker made. None is cancelled by the
# page's sign-in alone, nor by a lost device's steps (holds.cancel_all), which whoever holds the device can take as
# well: only by _guard's proof
HOLD, HOLD_SETTING, HOLD_DEFAULT = "e2e.change", "OPEN_EDITION_ADE_E2E_HOLD", 180
HOLD_RECOVERY = "e2e.recovery"
HOLD_LONG = "e2e.long"
HOLD_RECOVERY_SETTING, HOLD_RECOVERY_DEFAULT = "OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", 86400
GRACE = 3600                # a routine rotation retires the earlier keys an hour after it
RECOVERY = "recovery"       # a grant's `to` for the recovery key
# (turn-on: a classical account's turn-on again that upgrades it, the fold, where the recovery code is kept)
PROOF_PURPOSES = ("rotate", "revoke", "credentials", "off", "anchors", "passkey", "cancel", "turn-on")
PROOF_PREFIX = "e2e-proof:"
PROOF_INFO = b"proofpane-e2e/1 recovery proof"
# the hybrid proof of a post-quantum recovery key: its ids' purpose prefix, what its
# answer's MAC key is derived with, and what the key each id seals its secret under is derived with
PROOF2_PREFIX = "e2e-proof2:"
PROOF_INFO_HYBRID = b"proofpane-e2e/2 recovery proof"
PROOF_KEPT_INFO = b"proofpane-e2e/2 proof kept"
RESEAL = "reseal"           # a re-seal's proof, made for the digest of its batch
UPLOAD_MAX = 50             # statements a link sends in at once
FOR_LINK_PAGE = 500         # statements a link reads at once (for_link): it reads on while `more` says so
# routine rotations are at most one in this many seconds (0: no bound): a rotation is never urgent, a revocation is and
# is not bounded, and each rotation adds a statement and a content key every later revocation wraps again
ROTATE_SETTING, ROTATE_DEFAULT = "OPEN_EDITION_ADE_E2E_ROTATE_EVERY", 86400
COMPACT_TRIES, COMPACT_WAIT = 20, 0.1   # checkpoints tried after a rebuild, and the seconds between: a reader may hold
COMPACT_BUSY_MS = 100       # how long one checkpoint waits for such a reader before it says busy
COMPACT_EVERY = 60          # seconds between the sweeps that compact the database while it may hold what was sealed
WRAP_MAX = 1024             # a credential's wrap, a recovery anchor, the chain's wrap, a content key's wrap
WRAP_MIN = 28               # a 12-byte nonce and a 16-byte tag
# the roster under a master key, sealed: 20 credentials with post-quantum keys and WebAuthn's longest ids come to 63,101
# bytes, so 65,536 would leave too little
ROSTER_MAX = 131072
GRANT_MAX = 512             # the bytes sealed in a grant
# post-quantum keys: a write to a post-quantum account's keys says so (kem), each private key is a
# 32-byte X-Wing seed (or a retired classical key's 32-byte scalar) wrapped in exactly 60 bytes, and a grant of the
# master key is a form-2 envelope of its 32 bytes, exactly 1,197
KEM = "x-wing"
PQ_WRAP = 12 + 32 + 16
PQ_GRANT = e2e.TALK_KEY_SEALED
# a turn-on that makes an account's keys anew (its whole body: a first turn-on, or a start again with replace) makes them
# post-quantum, and one without kem is refused (_whole_era). True takes one without kem as older releases took it,
# making a classical account: the tests set it (tests/conftest.py), to make the classical accounts those releases made,
# which still exist and whose every path keeps its tests. Nothing in the server sets it: no setting, route or request.
CLASSICAL_WHOLE_TURN_ON = False
_HEX32 = re.compile(r"[0-9a-f]{32}")           # these: fullmatch, never match
_HEX64 = re.compile(r"[0-9a-f]{64}")
_B64U = re.compile(r"[A-Za-z0-9_-]+")

NOT_ON = "End-to-end encryption is not on for this account: turn it on first."
ON_ALREADY = "End-to-end encryption is on already."
WAITING = "A change to end-to-end encryption waits already: cancel it, or let it take effect, first."
STALE_MK = "The account's master key has changed since: read the keys again."
NO_RECOVERY = "This account has no recovery code: end-to-end encryption was never turned on."
PROOF_STALE = "That recovery proof has expired or was used: ask for a new one."
PROOF_WRONG = "That is not the recovery code's proof."
FROM_BEFORE = ("This account keeps keys from when encryption was on: turn it on again with its current master key (a "
               "content key alone), or send replace to start again, which leaves what was sealed under them "
               "unreadable.")
NO_KEYS = "This account keeps no keys from before: turning encryption on sends the whole body."
REKEY_DUE = ("A passkey that unlocked encryption was removed, or left out by high security, while it was off: turning it "
             "on again makes a new master key, as a revocation does (mk_id, chain, grants, roster, content_keys).")
NOT_ENROLLED = "Turning encryption on again is signed by a passkey that unlocks it."
RETIRES_ALL = "Turning encryption on again retires every earlier key at once (retires: all)."
KEY_STATED = "That key is stated already: make a new one."
SIGNER_REMOVED = "A passkey that signed one of its statements was removed."
MADE_BY_REMOVED = "The passkey that made it was removed."
REPLACED = "Another change to end-to-end encryption was made in its place."
REFUSED_AGAIN = "You cancelled those statements when a linked session sent them before: they are refused."
CANCEL_TAKES =("Cancelling a change to end-to-end encryption takes a touch of one of this account's passkeys that may "
                "cancel it, or the recovery code: signing in alone does not cancel it.")
CANCEL_REMOVED = ("That passkey may not cancel this change (one it removes, or one made with the recovery code, which "
                  "only the recovery code cancels): use another passkey, or the recovery code.")
NO_PLACE = ("A change waits already, and neither the passkey that made this one nor a recovery code's proof with it "
            "could cancel that one: cancel it with one that can, or let it take effect, first.")
NOT_STAYING = ("This change is signed by a passkey that unlocks end-to-end encryption and still does after it, or it "
               "comes with the recovery code's proof.")
CONFIRM_STAYING = ("This change is confirmed with a passkey that unlocks end-to-end encryption already and still does "
                   "after it (not the one being added), or with the recovery code's proof.")
UPLOAD_STAYING = "A statement sent in counts only if a passkey that unlocks end-to-end encryption signed it."
ANCHOR_OWN = ("Each renewed anchor but the confirming passkey's own comes with a confirmation its own passkey made: "
              "touch that passkey too.")
SALT_SAME = ("A renewed anchor, and a passkey's new key in the upgrade, is wrapped under a fresh salt, not the one its "
             "record has.")
CODE_CHANGED = "The recovery code has changed since: read the keys again."
ANCHOR_CODE = ("The recovery code's anchor is written again only with the recovery code's proof: a passkey's "
               "confirmation does not cover it.")
ENROL_OWN = ("A passkey is added to end-to-end encryption only with a confirmation it made itself, at its own touch: "
             "add it again, touching it.")
EVERY_CONTENT_KEY = ("A new master key wraps every content key of the account again under it, but those it leaves as "
                     "they are (abandon), each once.")
CLASSICAL_FORMS = ("This account's keys are classical: they change in the classical forms (no kem) until the account is "
                   "upgraded to post-quantum keys.")
NOT_CLASSICAL = "This account's keys are post-quantum already: there is nothing to upgrade."
CLASSICAL_AFTER = ("This account has stated a post-quantum key: no classical key is stated after it (a version-3 "
                   "statement, a post-quantum key, or the upgrade).")
ERA_CHANGED = ("The account's keys are no longer classical or post-quantum as they were when this change was made: "
               "read the keys again.")
OTHER_FORM = ("That encrypted text is sealed in a form its key does not take: form 1 to a classical key, form 2 to a "
              "post-quantum one.")

# the ceremony each touch here is made for (passkeys.CONFIRM_PURPOSES), the one the page asks for: a confirmation keeps
# it, and each change here takes only its own, so a touch made for one is spent on no other
TOUCH_ENROL, TOUCH_ENROL_OWN = "e2e-enrol-touch", "e2e-enrol-own"
TOUCH_ANCHOR, TOUCH_CANCEL = "e2e-anchor-touch", "e2e-cancel"
TOUCH_UPGRADE = "e2e-upgrade-touch"         # each passkey's touch in the upgrade, made for its change as well
COMPACT_AGAIN = 86400       # the upgrade compacts the database as it takes effect, and again this long after
UPGRADE_OWN = ("Each passkey that stays in the upgrade comes with a confirmation its own passkey made at its touch, "
               "for this upgrade: touch that passkey too, or leave it out to remove it.")
KEPT_PROOFS = ("An upgrade that keeps the recovery code proves it twice: the code in force (recovery_proof) and the "
               "post-quantum recovery key made from it (new_recovery_proof).")
UPGRADE_KEM = f"An upgrade to post-quantum keys says so: kem {KEM}."
UPGRADE_EVERY = ("The upgrade gives each enrolled passkey that stays a post-quantum key made at its own touch, and no "
                 "other: a passkey not touched is removed by it (remove).")
# the fold: a classical account's turn-on again that makes its new master key as the
# upgrade does, in one change, after which the account is post-quantum (turn_on with upgrade)
FOLD_REKEY = ("Turning encryption on again upgrades the keys only with a new master key made from the current one "
              "(mk_id, chain, grants, roster, content_keys).")
FOLD_EVERY = ("Turning encryption on again with post-quantum keys gives every passkey that unlocks it a new key, made "
              "at its own touch: touch each one, or first remove from your account a passkey that is not at hand.")
FOLD_OWN = ("Each passkey that unlocks encryption comes with a confirmation its own passkey made at its touch, for "
            "this change: touch that passkey too.")
FOLD_SUMMARY = "Turning end-to-end encryption on again with post-quantum keys was signed"


def _now(now):
    return int(now if now is not None else time.time())


# ---- the state links and the page are told ------------------------------------------------------------------------

def newest(db, user_id):
    """The account's newest statement's row, or None."""
    return db.execute("SELECT * FROM ade_e2e_statements WHERE user_id = ? ORDER BY seq DESC LIMIT 1",
                      (user_id,)).fetchone()


def is_key(row):
    """Whether a statement row (newest) says encryption is on: its purpose is e2e-key."""
    return row is not None and row["purpose"] == E2E_KEY


def on(db, user_id):
    """Whether the account's newest statement is e2e-key: encryption is on."""
    return is_key(newest(db, user_id))


def said(row):
    """The newest statement as a link is told it: {seq, key_id}, the key_id null once encryption is off; None when
    there is no statement."""
    return None if row is None else {"seq": row["seq"], "key_id": row["key_id"]}


def told(user_id):
    """said() of the account's newest statement, read now."""
    with store.connect() as db:
        return said(newest(db, user_id))


def key_state(db, user_id, key_id, now):
    """None when `key_id` is one of the account's stated keys not retired for sealing; "retired" when it is retired;
    "unknown" when the account never stated it. A key is stated once (_check_key_new); where it was stated twice all
    the same (a database from before that rule), the newest statement of it decides."""
    row = db.execute("SELECT retired_at FROM ade_e2e_statements WHERE user_id = ? AND key_id = ? AND purpose = ? ORDER "
                     "BY seq DESC LIMIT 1", (user_id, key_id, E2E_KEY)).fetchone()
    if row is None:
        return "unknown"
    return "retired" if row["retired_at"] is not None and row["retired_at"] <= now else None


def accept(db, link_row, key_id, top, now, form=e2e.FORM_X25519):
    """Inside a link's request's transaction, an envelope's key: refused (422) when the account never stated it, or
    when the envelope's `form` is not its key's (form 1 under a classical key, form 2 under a post-quantum one: a
    mismatch is refused, never read the other way); E2ERequired when it is retired for sealing while encryption is on
    (`top`, the newest statement); else recorded as the key the link last sealed to."""
    state = key_state(db, link_row["user_id"], key_id, now)
    if state == "unknown":
        raise link.LinkError(422, "That encrypted text is sealed to a key this account never stated.")
    if (form == e2e.FORM_HYBRID) != _hybrid_key(db, link_row["user_id"], key_id):
        raise link.LinkError(422, OTHER_FORM)
    if no_longer_taken(db, link_row["user_id"], state, form, top):
        raise link.E2ERequired(top)
    note_key(db, link_row["id"], key_id)


def no_longer_taken(db, user_id, state, form, top):
    """Whether a text sealed in `form` to a key in `state` (key_state) is refused as sealed to a key the account no
    longer takes (link.E2ERequired, which a link answers by sealing to the newest key, or by holding, never by sending
    in the clear): a key retired while encryption is on (`top`, the newest statement), as in older releases; and on a
    post-quantum account any text in form 1, with encryption on or off, since the upgrade retired every classical key
    for good. A link from an older release, which reads no version-3 statement and so never
    learns that encryption went off, goes on sealing to its classical key: refused, it holds."""
    return (state == "retired" and is_key(top)) or (form != e2e.FORM_HYBRID and is_pq(db, user_id))


def _hybrid_key(db, user_id, key_id):
    """Whether the account's stated key `key_id` is post-quantum (1,216 bytes), as its newest statement of it says."""
    row = db.execute("SELECT public_key FROM ade_e2e_statements WHERE user_id = ? AND key_id = ? AND purpose = ? ORDER "
                     "BY seq DESC LIMIT 1", (user_id, key_id, E2E_KEY)).fetchone()
    return row is not None and row["public_key"] is not None and len(row["public_key"]) > 43


def note_key(db, link_id, key_id):
    db.execute("INSERT INTO ade_link_e2e (link_id, key_id) VALUES (?, ?) ON CONFLICT (link_id) DO UPDATE SET "
               "key_id = excluded.key_id", (link_id, key_id))


def note_required(link_id, now=None):
    """When the server last told a link that encryption is required (its request refused, in a transaction apart)."""
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_e2e (link_id, last_required_at) VALUES (?, ?) ON CONFLICT (link_id) DO "
                   "UPDATE SET last_required_at = excluded.last_required_at", (link_id, _now(now)))


def check_pins(value):
    """The fingerprints of the owner keys a link pinned, as its History list reports them: a list of 32 lowercase hex
    each, at most passkeys.MAX_PASSKEYS; None when it reports none. 422 otherwise."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > passkeys.MAX_PASSKEYS \
            or not all(isinstance(p, str) and _HEX32.fullmatch(p) for p in value):
        raise link.LinkError(422, f"A link's pins are the fingerprints of the owner keys it pinned: at most "
                                  f"{passkeys.MAX_PASSKEYS}, each 32 lowercase hex characters.")
    return sorted(set(value))


def note_pins(db, link_id, pins):
    db.execute("INSERT INTO ade_link_e2e (link_id, pins) VALUES (?, ?) ON CONFLICT (link_id) DO UPDATE SET "
               "pins = excluded.pins", (link_id, json.dumps(pins)))


SEALS_FORM = ("A link's e2e_seal is the envelope forms it can seal: [1, 2, 3] where it can seal to a post-quantum key, "
              "[1] where it cannot.")
_SEALS_QUERY = re.compile(r"[0-9](,[0-9]){0,9}")


def check_seals(value):
    """The envelope forms a link says it can seal, as its History list reports them (e2e_seal), and its read of the
    statements (seals_of_query): [1, 2, 3] where its cryptography does ML-KEM, [1] where it does not; None when it
    reports none. Its word alone, which the page shows the owner before the upgrade and the server checks nothing by.
    422 otherwise."""
    if value is None:
        return None
    # its items' type before anything is made of them: a list or an object in it is no form (and no set takes it)
    if not isinstance(value, list) or not all(type(f) is int for f in value) \
            or sorted(value) not in ([e2e.FORM_X25519], sorted(e2e.FORMS)):
        raise link.LinkError(422, SEALS_FORM)
    return sorted(value)


def seals_of_query(text):
    """e2e_seal as a link sends it beside its read of the statements (GET /e2e?e2e_seal=1,2,3): the forms it can seal,
    comma-separated, as check_seals takes them; None where it sends none (a link from an older release). Every link that
    reads the statements says it there, a link that sends no History list, or holds rather than sends one, as well."""
    if text is None:
        return None
    if not isinstance(text, str) or not _SEALS_QUERY.fullmatch(text):
        raise link.LinkError(422, SEALS_FORM)
    return check_seals([int(f) for f in text.split(",")])


def note_seals(db, link_id, seals):
    db.execute("INSERT INTO ade_link_e2e (link_id, seals) VALUES (?, ?) ON CONFLICT (link_id) DO UPDATE SET "
               "seals = excluded.seals", (link_id, json.dumps(seals)))


# ---- the forms of what the page sends ------------------------------------------------------------------------------

def _object(value, what):
    if not isinstance(value, dict):
        raise link.LinkError(422, f"The {what} is a JSON object.")
    return value


def _bytes(value, what, exact=None, least=WRAP_MIN, most=WRAP_MAX):
    """The bytes of base64url text without padding written as its bytes give it: `exact` bytes, or `least` to `most`;
    422 naming `what` otherwise."""
    if isinstance(value, str) and _B64U.fullmatch(value) and len(value) % 4 != 1:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if e2e.b64u(raw) == value and (len(raw) == exact if exact is not None else least <= len(raw) <= most):
            return raw
    size = f"{exact} bytes" if exact is not None else f"{least} to {most} bytes"
    raise link.LinkError(422, f"The {what} is {size}, in base64url without padding.")


def _key(value, what, pq=False):
    """A public key the page sends, as text: for a classical account an X25519 key (32 bytes) anything can be sealed to;
    for a post-quantum one (`pq`) an X-Wing key (1,216 bytes) that passes the key check (e2e.usable_hybrid_key), so a
    post-quantum account takes no new 32-byte key. 422 naming `what` otherwise; 503
    where this server has no ML-KEM."""
    if pq:
        raw = _bytes(value, what, exact=e2e.HYBRID_KEY)
        try:
            if not e2e.ready():
                raise e2e.MLKEMMissing(e2e.MLKEM_MISSING_WORDS)
            usable = e2e.usable_hybrid_key(raw)
        except e2e.MLKEMMissing:
            raise link.LinkError(503, e2e.MLKEM_REFUSED) from None
        if not usable:
            raise link.LinkError(422, f"The {what} is not a post-quantum key anything can be sealed to: "
                                      f"{e2e.HYBRID_KEY_WORDS}")
        return value
    raw = _bytes(value, what, exact=32)
    if not e2e.usable_key(raw):
        raise link.LinkError(422, f"The {what} is not an X25519 key anything can be sealed to.")
    return value


def _key_wrap(value, what, pq=False):
    """A private key's wrap (a content key's, a credential's): 28 to 1,024 bytes for a classical account; exactly 60
    for a post-quantum one, whose every private key is 32 bytes (a seed, or a retired classical key's scalar) in its
    wrap, so the wrap of a page from an older release (76 bytes, or about 145 for a credential) is refused."""
    if pq:
        _bytes(value, what, exact=PQ_WRAP)
        return value
    return _wrap(value, what)


def is_pq(db, user_id):
    """Whether the account's keys are post-quantum (ade_e2e_state.pq): made so by a first turn-on or a start again that
    said so, or by the upgrade, and never back."""
    row = _state(db, user_id)
    return bool(row is not None and row["pq"])


def _kem(body):
    """Whether a write says its keys are post-quantum: kem "x-wing"; 422 for any other kem."""
    kem = body.get("kem")
    if kem is None:
        return False
    if kem != KEM:
        raise link.LinkError(422, f"A write's kem is {KEM}, or none.")
    return True


def _era(db, user_id, body, may_begin=False):
    """Inside a write's own transaction, before its body is read for its forms: whether its keys are post-quantum. A
    post-quantum account's every write carries kem (400, passkeys.OLD_PAGE, for one without: a page older than this
    server, which would send classical keys); a classical account's carries none, but where the write may make its keys
    post-quantum (`may_begin`: a turn-on with the whole body, the upgrade), where kem says it does (422 otherwise). 503
    for every post-quantum write where this server has no ML-KEM (e2e.ready), turning encryption off and renewing
    anchors too, though they make and check no key: the recovery code cancels any change (cancellers, rule (i)), and
    to a post-quantum key it proves itself by the hybrid proof alone, which needs ML-KEM (use_proof), so a change
    placed without it is one the code could not cancel."""
    pq, kem = is_pq(db, user_id), _kem(body)
    if pq and not kem:
        raise link.LinkError(400, passkeys.OLD_PAGE)
    if kem and not pq and not may_begin:
        raise link.LinkError(422, CLASSICAL_FORMS)
    if kem and not e2e.ready():
        raise link.LinkError(503, e2e.MLKEM_REFUSED)
    return kem


def _era_of(pq):
    """What a change's body keeps of its era: kem true for a post-quantum one; nothing for a classical one, whose body
    stays as older releases keep it."""
    return {"kem": True} if pq else {}


def _era_still(db, user_id, plan):
    """As a change's hold ends: the era it was made in is still the account's (its plan's kem: a body placed before this
    release has none, a classical one), or the change is one that makes it post-quantum (a turn-on with the whole body
    or the upgrade, which say begins, or the fold, a turn-on again with upgrade, which asks for a classical account
    itself, as the upgrade does); 409 otherwise, and 503 for a post-quantum change where this server has no ML-KEM now:
    one placed before it lost ML-KEM, which the recovery code could not cancel since (_era), fails as its hold ends.

    The fold's body says upgrade and never begins: a server put back to an older release, whose _era_still takes
    begins alone, then refuses a fold waiting as its hold ends (409), where it would otherwise write the new master
    key, its grants to keys it does not store, and leave the account's passkeys and recovery code unable to open it."""
    kem = bool(plan.get("kem"))
    if kem and not e2e.ready():
        raise link.LinkError(503, e2e.MLKEM_REFUSED)
    if kem != is_pq(db, user_id) and not (kem and (plan.get("begins") or plan.get("upgrade"))):
        raise link.LinkError(409, ERA_CHANGED)


def _whole_era(shape, kem):
    """A turn-on of `shape` whose era is `kem` (true: post-quantum), as it is asked (_turn_on_plan, right after _era and
    before its body is read for its forms) and again as its hold ends (_apply_turn_on, first): one that makes the
    account's keys anew (shape whole: a first turn-on, or a start again with replace) makes them post-quantum. One
    without kem comes only from a page from an older release, in a tab opened before this release, or from a direct
    call: 400, in the words _era answers such a page with (passkeys.OLD_PAGE). So one placed before this server
    refused it ends failed, with that reason, as its hold ends.
    Turning on again under the account's keys (shapes again and rekey) keeps its era, and is not asked here.
    CLASSICAL_WHOLE_TURN_ON takes it as before, for the tests alone."""
    if shape == "whole" and not kem and not CLASSICAL_WHOLE_TURN_ON:
        raise link.LinkError(400, passkeys.OLD_PAGE)


def code_may_cancel(db, user_id):
    """Inside a change's own transaction, as it is placed and as its hold ends: the recovery code could cancel it, as it
    may cancel any change (cancellers, rule (i)); 503 (e2e.MLKEM_REFUSED) where the account is post-quantum and this
    server has no ML-KEM, since the code then proves itself by the hybrid proof alone (use_proof). _era and _era_still
    hold the writes that carry kem to it; this holds those that carry none: a passkey added while encryption is on
    (hold_passkey, and before its ceremony passkeys.registration_options) and statements a link sends in (upload)."""
    if not e2e.ready() and is_pq(db, user_id):
        raise link.LinkError(503, e2e.MLKEM_REFUSED)


def _hex32(value, what):
    if not isinstance(value, str) or not _HEX32.fullmatch(value):
        raise link.LinkError(422, f"The {what} is 32 lowercase hex characters.")
    return value


def _flag(value, what, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise link.LinkError(422, f"The request's {what} is true or false.")
    return value


def _content_key(value, payload, pq=False):
    """The content key a statement states, as the page wraps it: {key_id, public_key, mk_id, wrapped}; for a
    post-quantum account (`pq`) a post-quantum key, which a version-3 statement states, wrapped in 60 bytes."""
    v = _object(value, "content key")
    if v.get("public_key") != payload["key"] or v.get("key_id") != payload["key_id"]:
        raise link.LinkError(422, "The content key is the one its statement states (key and key_id).")
    _key(v["public_key"], "content key", pq)
    _key_wrap(v.get("wrapped"), "content key's wrap", pq)
    return {"key_id": v["key_id"], "public_key": v["public_key"], "mk_id": _hex32(v.get("mk_id"), "content key's "
                                                                                                 "master key id"),
            "wrapped": v["wrapped"]}


def _passkey(db, user_id, passkey_id):
    row = db.execute("SELECT * FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, user_id)).fetchone() \
        if isinstance(passkey_id, str) else None
    if row is None:
        raise link.LinkError(422, "That is not one of this account's passkeys.")
    return row


def _may_enrol(db, user_id, row):
    """While high security is on, only a hardware key unlocks the keys (409 for another)."""
    if passkeys._high_security(db, user_id) and row["tier"] != passkeys.HARDWARE:
        raise link.LinkError(409, "High security is on: only a hardware key unlocks end-to-end encryption.")


def _credential(db, user_id, value, pq=False, mk_id=None):
    """A credential the page enrols: its passkey (the account's, one it may enrol), the salt its PRF output is asked
    for, its public key and its own wrap; for a post-quantum account (`pq`) a post-quantum key, a 60-byte wrap of its
    seed, and its anchor, the master key the wrap names in its AAD, which is `mk_id`, the one the change names."""
    v = _object(value, "credential")
    row = _passkey(db, user_id, v.get("passkey_id"))
    _may_enrol(db, user_id, row)
    _bytes(v.get("salt"), "credential's salt", exact=32)
    _key(v.get("public_key"), "credential's public key", pq)
    _key_wrap(v.get("wrapped"), "credential's wrap", pq)
    out = {"passkey_id": row["id"], "credential_id": row["credential_id"], "name": row["name"], "salt": v["salt"],
           "public_key": v["public_key"], "wrapped": v["wrapped"]}
    if pq:
        out["anchor"] = _hex32(v.get("anchor"), "credential's anchor")
        if out["anchor"] != mk_id:
            raise link.LinkError(422, "A post-quantum credential's anchor is the master key the change names.")
    elif v.get("anchor") is not None:
        raise link.LinkError(422, CLASSICAL_FORMS)
    return out


def _recovery(value, pq=False):
    """The recovery key from the code, and its anchor; a post-quantum key for a post-quantum account (`pq`)."""
    v = _object(value, "recovery key")
    _key(v.get("public_key"), "recovery key", pq)
    _bytes(v.get("anchor"), "recovery anchor")
    return {"public_key": v["public_key"], "anchor": v["anchor"]}


def _sealed(value, what, pq=False):
    """A grant of the master key, read for its form alone: for a classical account a form-1 envelope of at most
    GRANT_MAX bytes; for a post-quantum one a form-2 envelope of its 32 bytes, exactly PQ_GRANT."""
    try:
        if pq:
            if e2e.read(value, GRANT_MAX, (e2e.FORM_HYBRID,)).length != PQ_GRANT:
                raise e2e.NotAnEnvelope("")
        else:
            e2e.form(value, GRANT_MAX)
    except e2e.NotAnEnvelope:
        raise link.LinkError(422, f"The {what} is a form-2 envelope of the master key's 32 bytes, {PQ_GRANT} bytes "
                                  "as it decodes (e2e1. and base64url)." if pq else
                             f"The {what} is sealed as an envelope is (e2e1. and base64url), at most {GRANT_MAX} bytes "
                             "inside.") from None
    return value


def _grants(value, pq=False):
    """{to: sealed} from the page's list; each `to` once; each in its account's form (_sealed)."""
    if not isinstance(value, list) or not value or len(value) > passkeys.MAX_PASSKEYS + 1:
        raise link.LinkError(422, "The grants are a list, one for each key the master key is sealed to.")
    out = {}
    for g in value:
        g = _object(g, "grant")
        to = g.get("to")
        if not isinstance(to, str) or not to or to in out:
            raise link.LinkError(422, "Each grant is to a credential or to the recovery key, once.")
        out[to] = _sealed(g.get("sealed"), "grant", pq)
    if RECOVERY not in out:
        raise link.LinkError(422, "A master key is granted to the recovery key as well.")
    return out


def _wrap(value, what, most=WRAP_MAX):
    _bytes(value, what, most=most)
    return value


def _stored(raw, credential, signer):
    """A statement as a body keeps it until its hold ends: its canonical text, its assertion, its passkey."""
    return {"payload": raw.decode("utf-8"), "credential": json.dumps(credential, separators=(",", ":")),
            "passkey_id": signer["id"]}


# ---- the master key, the pending change, the confirmations --------------------------------------------------------

def current(db, user_id):
    """The current master key's id (the roster's), or None."""
    row = db.execute("SELECT mk_id FROM ade_e2e_roster WHERE user_id = ?", (user_id,)).fetchone()
    return row["mk_id"] if row is not None else None


def _pending(db, user_id):
    return db.execute("SELECT * FROM ade_e2e_pending WHERE user_id = ?", (user_id,)).fetchone()


def _free(db, user_id):
    """Nothing waits (a link's statements sent in take no change's place): 409 otherwise."""
    if _pending(db, user_id) is not None:
        raise link.LinkError(409, WAITING)


def _in_place_of(db, user_id, by, by_code):
    """Inside a change's own transaction, once what signed or confirmed it is checked: the change that waits, whose place
    this one takes, or None when none waits. It may (v) when the recovery code's proof comes with this one (`by_code`)
    and the code cancels that one, or when the passkey that signed or confirmed this one (`by`) could cancel it
    (cancellers); 409 otherwise, so no one takes the place of a change they could not have cancelled. The caller ends it
    (_replace) as it places its own. (Called after holds.settle, so a change whose time has come takes effect first.)
    A waiting upgrade to post-quantum keys is a revocation here as everywhere (no new rule enters cancellers):
    nothing changes beside it while it waits, since a change either takes its place and ends
    it or is refused, and a stolen passkey that places one upgrade after another never keeps the owner from putting a
    revocation of it in their place."""
    waiting = _pending(db, user_id)
    if waiting is None:
        return None
    may, code = cancellers(db, user_id, waiting)
    if not ((by_code and code) or (by is not None and by in may)):
        raise link.LinkError(409, NO_PLACE)
    return waiting


def _replace(db, user_id, waiting, now):
    """The change that waited (_in_place_of) ends, failed: another was made in its place."""
    if waiting is not None:
        _end_pending(db, user_id, waiting, REPLACED, now)


def _staying(db, user_id, passkey_id):
    """Whether `passkey_id` unlocks the keys and goes on doing so as they stand: its credential enrolled, its passkey
    the account's, and a hardware key while high security is on (_stale)."""
    return isinstance(passkey_id, str) and _enrolled(db, user_id, passkey_id) is not None \
        and passkey_id not in _stale(db, user_id)


def _next_of(top):
    """(seq, prev) of the statement after `top` (None for the first)."""
    if top is None:
        return 1, ""
    return top["seq"] + 1, hashlib.sha256(top["payload"].encode("utf-8")).hexdigest()


def _check_next(db, user_id, payload):
    seq, prev = _next_of(newest(db, user_id))
    if payload["seq"] != seq or payload["prev"] != prev:
        raise link.LinkError(409, f"A statement is the account's next: seq {seq}, and prev the sha256 of statement "
                                  f"{seq - 1}" + (" (none: this is the first)." if seq == 1 else "."), seq=seq,
                             prev=prev)


def _check_key_new(db, user_id, payload):
    """A key statement names a key no statement of the account named before (409): a key retired is never stated
    again, so it cannot be made one links seal to once more. Nor does a version-2 key statement come after a
    version-3 one, or on a post-quantum account (409): what links seal to never goes back to a classical key, as a
    link with state refuses one too."""
    if payload["purpose"] != E2E_KEY:
        return
    if db.execute("SELECT 1 FROM ade_e2e_statements WHERE user_id = ? AND key_id = ?",
                  (user_id, payload["key_id"])).fetchone():
        raise link.LinkError(409, KEY_STATED)
    if payload["v"] != link_commands.STATEMENT_HYBRID and _hybrid_stated(db, user_id):
        raise link.LinkError(409, CLASSICAL_AFTER)


def _hybrid_stated(db, user_id):
    """Whether the account's keys are post-quantum, or any statement of it names a post-quantum key (a database put
    back to before an upgrade keeps the statements links sent in since: its flag alone would say classical)."""
    return is_pq(db, user_id) or db.execute(
        "SELECT 1 FROM ade_e2e_statements WHERE user_id = ? AND purpose = ? AND length(public_key) > 43 LIMIT 1",
        (user_id, E2E_KEY)).fetchone() is not None


def _ready(db, user_id, payload):
    """For a statement held while encryption is on: it is on, and the statement is the next, naming a new key. (A
    change that waits is taken the place of, or refuses this one: _in_place_of.)"""
    if not on(db, user_id):
        raise link.LinkError(409, NOT_ON)
    _check_next(db, user_id, payload)
    _check_key_new(db, user_id, payload)


def _proof_secret():
    # a key of its own, derived from the token secret, so no other MAC or key the app makes stands for this one
    return hmac.new(config.token_secret(), b"proofpane e2e recovery proof v1", hashlib.sha256).digest()


def _proof_key(nonce):
    """The server's X25519 key for one recovery proof, made again from its nonce whenever it is needed: no row."""
    return X25519PrivateKey.from_private_bytes(hmac.new(_proof_secret(), nonce, hashlib.sha256).digest())


def proof_challenge(user_id, purpose, now=None, public_key=None, digest=None):
    """What the page answers to prove it holds the recovery code, for one change of `purpose`, good for 5 minutes and
    once (use_proof). For a classical recovery key, the classical proof: {challenge_id, server_key: S, nonce, purpose,
    expires_at}, which the page answers with HMAC-SHA256(HKDF-SHA256(X25519(rk, S), salt empty, info "proofpane-e2e/1
    recovery proof"), nonce || purpose), the nonce's bytes then the purpose's. For a post-quantum one (the account's,
    or `public_key`, the new recovery key an upgrade that keeps the code sends, purpose revoke, or turn-on for the fold,
    on a classical account alone), the hybrid proof: {challenge_id, server_ct, nonce,
    purpose, expires_at, kem}, server_ct an X-Wing encapsulation to that key, whose secret ss the id carries sealed
    (_proof2_id); the page answers HMAC-SHA256(HKDF-SHA256(ss, salt empty, info "proofpane-e2e/2 recovery proof"),
    nonce || purpose). Purpose reseal takes `digest`, the batch's (reseal_digest), which the id carries and the proof is
    then good for alone."""
    if purpose not in PROOF_PURPOSES + (RESEAL,):
        raise link.LinkError(422, "A recovery proof's purpose is " + ", ".join(PROOF_PURPOSES + (RESEAL,)) + ".")
    if (purpose == RESEAL) != (digest is not None) or (digest is not None and not (
            isinstance(digest, str) and _HEX64.fullmatch(digest))):
        raise link.LinkError(422, "A recovery proof for re-sealing names its batch's digest (64 lowercase hex), and "
                                  "no other proof names one.")
    now = _now(now)
    with store.connect() as db:
        row = db.execute("SELECT public_key FROM ade_e2e_recovery WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise link.LinkError(409, NO_RECOVERY)
        pq = is_pq(db, user_id)
    if purpose == RESEAL and not pq:
        raise link.LinkError(409, RESEAL_NOT_PQ)        # a classical account has nothing to seal again to
    if public_key is not None:
        if purpose not in ("revoke", "turn-on") or pq:
            raise link.LinkError(422, "A recovery proof for a new recovery key is the upgrade's, purpose revoke (or "
                                      "turn-on, as encryption is turned on again), on a classical account.")
        _key(public_key, "new recovery key", pq=True)
        target = public_key
    elif pq:
        target = row["public_key"]
    else:
        challenge_id, nonce = passkeys._new_challenge(PROOF_PREFIX + purpose, user_id, now)
        return {"challenge_id": challenge_id,
                "server_key": e2e.b64u(_proof_key(nonce).public_key().public_bytes_raw()),
                "nonce": e2e.b64u(nonce), "purpose": purpose, "expires_at": now + passkeys.CHALLENGE_TTL}
    if not e2e.ready():
        raise link.LinkError(503, e2e.MLKEM_REFUSED)
    raw = _bytes(target, "recovery key", exact=e2e.HYBRID_KEY)
    try:
        ct, ss = e2e.encapsulate(raw)
    except ValueError:
        raise link.LinkError(422, f"The recovery key is not a post-quantum key: {e2e.HYBRID_KEY_WORDS}") from None
    except e2e.MLKEMMissing:
        raise link.LinkError(503, e2e.MLKEM_REFUSED) from None
    nonce = secrets.token_bytes(32)
    expires_at = now + passkeys.CHALLENGE_TTL
    fields = {"c": e2e.b64u(nonce), "p": PROOF2_PREFIX + purpose, "u": user_id, "e": expires_at,
              "r": hashlib.sha256(raw).hexdigest()}
    if digest is not None:
        fields["d"] = digest
    return {"challenge_id": _proof2_id(fields, nonce, ss), "server_ct": e2e.b64u(ct), "nonce": e2e.b64u(nonce),
            "purpose": purpose, "expires_at": expires_at, "kem": KEM}


def _proof2_key(nonce):
    """The key a hybrid proof's id seals its shared secret under: a key of its own for each id, from the deployment's
    proof secret (in the environment, never the database) and the id's own nonce."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=PROOF_KEPT_INFO).derive(_proof_secret())


def _proof2_id(fields, nonce, ss):
    """A hybrid proof's challenge id: its fields' canonical JSON in base64url, a dot, then the secret ss sealed with
    AES-256-GCM under _proof2_key(nonce), nonce its first 12 bytes, the fields' bytes its AAD. Nothing is written: the
    id carries what checking needs, and the seal both hides ss (the page sees the id, and one that carried ss in the
    clear would let a sign-in alone pass the proof) and makes every field one this deployment wrote."""
    text = canonicalize(fields)
    return e2e.b64u(text) + "." + e2e.b64u(AESGCM(_proof2_key(nonce)).encrypt(nonce[:12], ss, text))


def _open_proof2(challenge_id, purpose, user_id, now):
    """(nonce, expiry, the sha256 hex of the recovery key it was made for, its digest or None, ss) of a hybrid proof's
    id this deployment made for `purpose` and this account, not expired; PROOF_STALE (403) for anything else."""
    stale = link.LinkError(403, PROOF_STALE)
    if not isinstance(challenge_id, str) or len(challenge_id) > 2048 or challenge_id.count(".") != 1:
        raise stale
    head, sealed = challenge_id.split(".")
    try:
        text, box = passkeys._strict_unb64u(head), passkeys._strict_unb64u(sealed)
        fields = json.loads(text)
        nonce = passkeys._strict_unb64u(fields["c"])
        # its fields as this deployment wrote them: a head RFC 8785 cannot write (NaN, 1e400, an integer past 2^53, a
        # lone surrogate) is no id it made, and is refused as stale, not raised
        written = isinstance(fields, dict) and canonicalize(fields) == text
    except (ValueError, KeyError, TypeError, RecursionError, binascii.Error):
        raise stale from None
    if not written or len(nonce) != 32:
        raise stale
    try:
        ss = AESGCM(_proof2_key(nonce)).decrypt(nonce[:12], box, text)
    except InvalidTag:
        raise stale from None
    if fields.get("p") != PROOF2_PREFIX + purpose or fields.get("u") != user_id or fields.get("e", 0) <= now:
        raise stale
    return nonce, fields["e"], fields.get("r"), fields.get("d"), ss


def use_proof(db, user_id, proof, purpose, now, public_key=None, digest=None):
    """Inside the change's own transaction: the recovery code's proof for `purpose` ({challenge_id, proof}, from
    proof_challenge) checked against the account's recovery key, or `public_key` (an upgrade's new one), and used up;
    403 for a wrong or stale one. A post-quantum key takes the hybrid proof alone, made for that very key (its sha256
    in the id), so a proof made before the key changed no longer passes, and the classical proof is refused for it; a
    classical key takes the classical proof alone. `digest`: the re-seal batch's, which the proof's id must name."""
    if not isinstance(proof, dict) or not isinstance(proof.get("challenge_id"), str) \
            or not isinstance(proof.get("proof"), str):
        raise link.LinkError(422, "A recovery proof is {challenge_id, proof}.")
    row = db.execute("SELECT public_key FROM ade_e2e_recovery WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise link.LinkError(409, NO_RECOVERY)
    if public_key is not None or is_pq(db, user_id):
        if not e2e.ready():
            raise link.LinkError(503, e2e.MLKEM_REFUSED)
        nonce, expires_at, made_for, named, ss = _open_proof2(proof["challenge_id"], purpose, user_id, now)
        raw = _bytes(public_key if public_key is not None else row["public_key"], "recovery key",
                     exact=e2e.HYBRID_KEY)
        if made_for != hashlib.sha256(raw).hexdigest() or named != digest:
            raise link.LinkError(403, PROOF_WRONG)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=PROOF_INFO_HYBRID).derive(ss)
    else:
        if digest is not None:
            raise link.LinkError(403, PROOF_WRONG)      # a classical account re-seals nothing: it has no hybrid key
        try:
            nonce, expires_at = passkeys._open_challenge(proof["challenge_id"], PROOF_PREFIX + purpose, user_id, now)
        except passkeys.PasskeyError:
            raise link.LinkError(403, PROOF_STALE) from None
        shared = _proof_key(nonce).exchange(X25519PublicKey.from_public_bytes(_bytes(row["public_key"], "recovery key",
                                                                                     exact=32)))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=PROOF_INFO).derive(shared)
    want = hmac.new(key, nonce + purpose.encode("utf-8"), hashlib.sha256).digest()
    try:
        got = _bytes(proof["proof"], "proof", exact=32)
    except link.LinkError:
        got = b""
    if not hmac.compare_digest(want, got):
        raise link.LinkError(403, PROOF_WRONG)
    try:
        passkeys._use_challenge(db, nonce, expires_at, now)
    except passkeys.PasskeyError:
        raise link.LinkError(403, PROOF_STALE) from None


def _confirm(db, user_id, body, purpose, touch, now):
    """A change without a statement of its own: a passkey's confirmation made for `touch` (one of the TOUCH_
    ceremonies), never the password's, or the recovery code's proof for `purpose`. (The hold it waits on: HOLD, or
    HOLD_RECOVERY, the longer, when the recovery code confirmed it; what made it: the passkey that confirmed it, or
    RECOVERY.)"""
    if body.get("recovery_proof") is not None:
        use_proof(db, user_id, body["recovery_proof"], purpose, now)
        return HOLD_RECOVERY, RECOVERY
    try:
        used = passkeys._use_confirmation(db, user_id, body.get("confirmation"), now, passkey=passkeys.NEEDS_PASSKEY,
                                          purpose=touch)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None
    return HOLD, used.passkey_id


def _signed_or_proof(db, user_id, body, purpose, now):
    """A change with a statement: the statement's signature is the passkey's confirmation; a recovery proof given
    beside it is checked too, and makes the hold the longer. The hold it waits on: HOLD, or HOLD_RECOVERY."""
    if body.get("recovery_proof") is None:
        return HOLD
    use_proof(db, user_id, body["recovery_proof"], purpose, now)
    return HOLD_RECOVERY


def _place(db, user_id, change, body, kind, summary, now, made_by=None, by_code=False):
    """Inside the change's own transaction: its hold, of `kind` (HOLD, HOLD_RECOVERY or HOLD_LONG), and its body kept
    whole until the hold ends, with what made it (`made_by`: the passkey that signed or confirmed it, RECOVERY, or
    None for a link's) and whether the recovery code's proof came with it (`by_code`: code-backed). Its notice is in
    fixed words (holds.tell, _notice): for a code-backed one, that only the recovery code cancels it (cancellers). What
    the change does is not in it: the page shows that, from the change's body (Encryption)."""
    hold = holds.place(db, user_id, kind, user_id, summary, now)
    db.execute("INSERT INTO ade_e2e_pending (user_id, hold_id, kind, body, created_at, made_by, by_code) VALUES "
               "(?,?,?,?,?,?,?)", (user_id, hold["id"], change, json.dumps(body), now, made_by, int(bool(by_code))))
    return hold


def _told(hold, now):
    told_devices = holds.tell(hold, now)
    return {"hold": dict(hold, told=told_devices), "told": told_devices}


# ---- storing what a body holds --------------------------------------------------------------------------------------

def _store_statement(db, user_id, stored, now):
    """A statement kept (_stored), as the account's next naming a new key (409 otherwise), and the earlier keys retired
    for sealing as it says: all of them now, or each not retired yet an hour from now."""
    payload = json.loads(stored["payload"])
    _check_next(db, user_id, payload)
    _check_key_new(db, user_id, payload)
    db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, key_id, public_key, retires, payload, "
               "credential, passkey_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (user_id, payload["seq"], payload["id"], payload["purpose"], payload.get("key_id"), payload.get("key"),
                payload.get("retires"), stored["payload"], stored["credential"], stored["passkey_id"], now))
    if payload["purpose"] == E2E_KEY:
        until = now if payload["retires"] == "all" else now + GRACE
        db.execute("UPDATE ade_e2e_statements SET retired_at = ? WHERE user_id = ? AND seq < ? AND purpose = ? AND "
                   "(retired_at IS NULL OR retired_at > ?)", (until, user_id, payload["seq"], E2E_KEY, until))


def _new_content_key(db, user_id, content, now):
    if db.execute("SELECT 1 FROM ade_e2e_content_keys WHERE user_id = ? AND key_id = ?",
                  (user_id, content["key_id"])).fetchone():
        raise link.LinkError(409, "That content key is stated already: make a new one.")
    db.execute("INSERT INTO ade_e2e_content_keys (user_id, key_id, public_key, mk_id, wrapped, created_at) VALUES "
               "(?,?,?,?,?,?)", (user_id, content["key_id"], content["public_key"], content["mk_id"],
                                 content["wrapped"], now))


def _enrol(db, user_id, cred, now):
    db.execute("INSERT INTO ade_e2e_credentials (user_id, passkey_id, credential_id, salt, public_key, wrapped, "
               "created_at, anchor) VALUES (?,?,?,?,?,?,?,?)",
               (user_id, cred["passkey_id"], cred["credential_id"], cred["salt"], cred["public_key"], cred["wrapped"],
                now, cred.get("anchor")))
    db.execute("UPDATE passkeys SET prf = 1 WHERE id = ?", (cred["passkey_id"],))


def _grant(db, user_id, mk_id, to, sealed, now):
    db.execute("INSERT INTO ade_e2e_grants (user_id, mk_id, recipient, sealed, created_at) VALUES (?,?,?,?,?)",
               (user_id, mk_id, to, sealed, now))


def _roster(db, user_id, mk_id, sealed, now):
    db.execute("INSERT INTO ade_e2e_roster (user_id, mk_id, sealed, created_at) VALUES (?,?,?,?) ON CONFLICT "
               "(user_id) DO UPDATE SET mk_id = excluded.mk_id, sealed = excluded.sealed, "
               "created_at = excluded.created_at", (user_id, mk_id, sealed, now))


def _recovery_key(db, user_id, rec, now):
    db.execute("INSERT INTO ade_e2e_recovery (user_id, public_key, anchor, created_at) VALUES (?,?,?,?) ON CONFLICT "
               "(user_id) DO UPDATE SET public_key = excluded.public_key, anchor = excluded.anchor, "
               "created_at = excluded.created_at", (user_id, rec["public_key"], rec["anchor"], now))


def _forget(db, user_id):
    """The account's keys from before, every one (a turn-on that starts again); its statements stay."""
    for table in ("ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_chain", "ade_e2e_roster",
                  "ade_e2e_content_keys"):
        db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))


def _enrolled(db, user_id, passkey_id):
    return db.execute("SELECT * FROM ade_e2e_credentials WHERE user_id = ? AND passkey_id = ?",
                      (user_id, passkey_id)).fetchone()


def _state(db, user_id):
    return db.execute("SELECT * FROM ade_e2e_state WHERE user_id = ?", (user_id,)).fetchone()


def _set_state(db, user_id, **values):
    """The account's row of ade_e2e_state, made if it has none, with `values` (its columns) set."""
    db.execute("INSERT INTO ade_e2e_state (user_id) VALUES (?) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    for column, value in values.items():
        db.execute(f"UPDATE ade_e2e_state SET {column} = ? WHERE user_id = ?", (value, user_id))


def _stale(db, user_id):
    """The passkeys of the account's enrolled credentials that no longer unlock its keys as they stand: the passkey
    gone, or not a hardware key while high security is on."""
    high = passkeys._high_security(db, user_id)
    return [r["passkey_id"] for r in db.execute(
        "SELECT c.passkey_id, p.tier FROM ade_e2e_credentials c LEFT JOIN passkeys p ON p.id = c.passkey_id WHERE "
        "c.user_id = ?", (user_id,)).fetchall() if r["tier"] is None or (high and r["tier"] != passkeys.HARDWARE)]


def rekey_due(db, user_id):
    """Whether turning encryption on again must make a new master key: a passkey that unlocked the keys was removed,
    or left out by high security, since the master key was made (credentials_dropped), or an enrolled credential no
    longer unlocks the keys as they stand (_stale)."""
    row = _state(db, user_id)
    return bool(row is not None and row["rekey_due"]) or bool(_stale(db, user_id))


def credentials_dropped(db, user_id, passkey_ids):
    """Inside the transaction that removes passkeys or turns high security on (passkeys.remove_in, high_security_in):
    the credential of each of these passkeys, which no longer unlocks the keys, is deleted with its grants; and the
    master key, which it could open, is due to be replaced before encryption is turned on again (rekey_due): its
    authenticator still makes its PRF output. A revocation that does this makes the new master key in the same
    transaction (_new_master_key)."""
    ids = list(passkey_ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    creds = db.execute(f"SELECT credential_id FROM ade_e2e_credentials WHERE user_id = ? AND passkey_id IN ({marks})",
                       (user_id, *ids)).fetchall()
    if not creds:
        return
    for c in creds:
        db.execute("DELETE FROM ade_e2e_grants WHERE user_id = ? AND recipient = ?", (user_id, c["credential_id"]))
    db.execute(f"DELETE FROM ade_e2e_credentials WHERE user_id = ? AND passkey_id IN ({marks})", (user_id, *ids))
    _set_state(db, user_id, rekey_due=1)


def high_security_turned_on(db, user_id):
    """Inside the transaction that turns high security on (passkeys.high_security_in): every credential but the hardware
    keys' no longer unlocks the keys (credentials_dropped)."""
    credentials_dropped(db, user_id, [r["id"] for r in db.execute(
        "SELECT id FROM passkeys WHERE user_id = ? AND tier <> ?", (user_id, passkeys.HARDWARE)).fetchall()])


# ---- turning it on -------------------------------------------------------------------------------------------------

def _turn_on_plan(db, user_id, body, payload, replace):
    """What a turn-on's body holds, each part read for its form, by its shape and in its era (_era: kem, the account's
    keys post-quantum, or made so). whole: the first turn-on, or one that starts again (replace): the master key's id,
    the credential of the passkey that signs it, the recovery key, the grants to those two and the roster; its keys are
    post-quantum, which makes a classical account's so (begins), and one without kem is refused before its forms are
    read (_whole_era). rekey: a new master key made from the current one as a revocation makes it (mk_id, chain,
    grants, roster, content_keys). again: the content key alone, under the current master key. Those two keep the
    account's era, but the fold: rekey with upgrade, a classical account's new master key made as the upgrade makes it
    (_upgrade_plan: kem, each passkey that stays given a post-quantum key at its own
    touch, the recovery key made post-quantum from the code kept or a new one), which makes the account post-quantum as
    its hold ends. Its body says upgrade and never begins (_era_still says why). (the plan; the fold's touches, by
    passkey, or none)"""
    has = {k for k in ("mk_id", "credential", "recovery", "grants", "roster", "content_keys") if body.get(k) is not None}
    upgrade = _flag(body.get("upgrade"), "upgrade")
    if body.get("chain") is not None:
        if replace or "credential" in has or ("recovery" in has and not upgrade):
            raise link.LinkError(422, "Turning encryption on with a new master key made from the current one names no "
                                      "credential or recovery key of its own, and does not start again.")
        if upgrade and is_pq(db, user_id):
            raise link.LinkError(409, NOT_CLASSICAL)
        pq = _era(db, user_id, body, may_begin=upgrade)
        if upgrade and not pq:
            raise link.LinkError(422, UPGRADE_KEM)
        plan = _revocation_body(dict(body, remove=[], high_security=False,
                                     recovery=body.get("recovery") if upgrade else None), payload, pq)
        touches = {}
        if upgrade:
            plan, touches = _upgrade_plan(db, user_id, body, plan)
        return dict(plan, shape="rekey"), touches
    if upgrade:
        raise link.LinkError(422, FOLD_REKEY)
    if not (replace or has):
        pq = _era(db, user_id, body)
        return {"shape": "again", "content_key": _content_key(body.get("content_key"), payload, pq), **_era_of(pq)}, {}
    pq = _era(db, user_id, body, may_begin=True)
    _whole_era("whole", pq)
    content = _content_key(body.get("content_key"), payload, pq)
    mk_id = _hex32(body.get("mk_id"), "master key's id")
    if content["mk_id"] != mk_id:
        raise link.LinkError(422, "The content key is wrapped under the master key being turned on.")
    cred = _credential(db, user_id, body.get("credential"), pq, mk_id)
    grants = _grants(body.get("grants"), pq)
    if set(grants) != {cred["credential_id"], RECOVERY}:
        raise link.LinkError(422, "Turning encryption on grants the master key to the credential in use and to the "
                                  "recovery key, and to nothing else.")
    return {"shape": "whole", "replace": replace, "content_key": content, "mk_id": mk_id, "credential": cred,
            "recovery": _recovery(body.get("recovery"), pq), "grants": grants,
            "roster": _wrap(body.get("roster"), "roster", most=ROSTER_MAX),
            **({"kem": True, "begins": True} if pq else {})}, {}


def _turn_on_checks(db, user_id, plan, payload, signer_id):
    """The checks of a turn-on, made as it is asked and again as its hold ends: encryption is off; its statement is
    the account's next and names a new key, retiring every earlier key at once after encryption was on before or when
    it starts again; and by its shape: the whole body only for an account with no keys, or with replace, its credential
    the signer's own and one it may enrol; the content key alone only under the current master key, signed by a passkey
    that unlocks the keys, and only while no new master key is due (rekey_due); a new master key only as a revocation
    may make one, signed by a passkey whose credential stays, and for the fold as the upgrade may make one too
    (_upgrade_checks: the account classical, a new key for every passkey that stays, each under a fresh salt, and the
    recovery key the code was proved against still the account's): it removes no passkey, as no turn-on does, so one
    not at hand is removed from the account first (FOLD_EVERY), which takes effect at once while encryption is off."""
    if on(db, user_id):
        raise link.LinkError(409, ON_ALREADY)
    _check_next(db, user_id, payload)
    _check_key_new(db, user_id, payload)
    if (newest(db, user_id) is not None or plan.get("replace")) and payload["retires"] != "all":
        raise link.LinkError(422, RETIRES_ALL)
    mk = current(db, user_id)
    if plan["shape"] == "whole":
        if mk is not None and not plan["replace"]:
            raise link.LinkError(409, FROM_BEFORE)
        row = _passkey(db, user_id, plan["credential"]["passkey_id"])
        _may_enrol(db, user_id, row)
        if row["id"] != signer_id:
            raise link.LinkError(422, "The credential enrolled as encryption is turned on is the passkey that signed "
                                      "its statement.")
        return
    if mk is None:
        raise link.LinkError(409, NO_KEYS)
    if plan["shape"] == "again":
        if rekey_due(db, user_id):
            raise link.LinkError(409, REKEY_DUE)
        if "content_key" in plan and plan["content_key"]["mk_id"] != mk:      # a link's statement sent in has none
            raise link.LinkError(409, STALE_MK)
        if _enrolled(db, user_id, signer_id) is None:
            raise link.LinkError(409, NOT_ENROLLED)
        return
    stay = _revocation(db, user_id, dict(plan, remove=[], high_security=False))
    if signer_id not in stay.values():
        raise link.LinkError(409, NOT_ENROLLED)
    if plan.get("upgrade"):
        _upgrade_checks(db, user_id, plan, stay, FOLD_EVERY)


def turn_on(user_id, data, now=None):
    """Turn encryption on with the page's body (_turn_on_plan says its shapes), held as every change is: 180 seconds,
    or a day with replace, which forgets the keys from before; with a notice to the account's devices, and Cancel
    (_guard: a turn-on removes no passkey). Nothing of it is kept before its hold ends, when it is checked again and
    made whole (_apply_turn_on). With `seal` (true unless false), what links sent in the clear on the paths encryption
    covers is then sealed in place to the new key, and the database compacted after. {hold, told}.

    The fold (rekey with upgrade) is held by the upgrade's rules, not a
    turn-on's: it changes the recovery key, so it waits a day (HOLD_LONG), or the code's day (HOLD_RECOVERY) when the
    recovery code's proof comes with it (recovery_proof, purpose turn-on, which a code kept must carry with
    new_recovery_proof, _upgrade_proofs), and only the code then cancels it (code-backed). Otherwise any of the
    account's passkeys and the code cancel it (cancellers: a turn-on removes none). It takes the place of a waiting
    change, and a change takes its place, only as rule (v) allows any change to (_in_place_of)."""
    now = _now(now)
    body = _object(data, "request")
    payload, raw, credential, signer, auth = link_commands.check_statement(user_id, body.get("statement"), (E2E_KEY,),
                                                                           now)
    seal_now = _flag(body.get("seal"), "seal", default=True)
    replace = _flag(body.get("replace"), "replace")
    holds.settle(user_id, now)                  # a change whose time has come takes effect, not taken the place of
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        plan, touches = _turn_on_plan(db, user_id, body, payload, replace)
        fold = bool(plan.get("upgrade"))
        kind, by_code = (HOLD_LONG if replace else HOLD), False
        if fold:
            by_code = _signed_or_proof(db, user_id, body, "turn-on", now) == HOLD_RECOVERY
            kind = HOLD_RECOVERY if by_code else HOLD_LONG
        waiting = _in_place_of(db, user_id, signer["id"], by_code)
        _turn_on_checks(db, user_id, plan, payload, signer["id"])
        if fold:
            plan = _upgrade_proofs(db, user_id, body, plan, by_code, now, "turn-on")
            _upgrade_touches(db, user_id, plan, touches, signer["id"], raw, now, FOLD_OWN)
        link_commands.commit_statement(db, user_id, payload, signer, auth, now)
        _replace(db, user_id, waiting, now)
        hold = _place(db, user_id, "turn-on", dict(plan, statement=_stored(raw, credential, signer), seal=seal_now),
                      kind, FOLD_SUMMARY if fold else "Turning end-to-end encryption on was signed", now,
                      made_by=signer["id"], by_code=by_code)
    return _told(hold, now)


def _apply_turn_on(db, hold, body, now):
    """A turn-on's hold has ended: checked again (_whole_era first, so one with the whole body placed without kem
    before this server refused it takes no effect and says so; then _turn_on_checks), its keys stored as its shape
    says, its statement kept, and, with seal, what links sent in the clear sealed in place (_seal_after: the database
    due for compaction, when something was, which a thread of its own begins once this has committed, the request that
    settled it not waiting); then, whether or not it seals, the words of every connector proposal already decided
    cleared (_forget_decided). The fold writes the new master key as a revocation does, then the post-quantum recovery
    key and anchor, each passkey's new key, and the account post-quantum, as the upgrade does (_apply_upgrade: the
    database compacted now and a day later)."""
    user_id = hold["user_id"]
    stored = body["statement"]
    payload = json.loads(stored["payload"])
    _whole_era(body["shape"], body.get("kem"))
    _turn_on_checks(db, user_id, body, payload, stored["passkey_id"])
    _era_still(db, user_id, body)
    if body["shape"] == "whole":
        if body["replace"]:
            _forget(db, user_id)
        _enrol(db, user_id, body["credential"], now)
        _recovery_key(db, user_id, body["recovery"], now)
        for to, grant in body["grants"].items():
            _grant(db, user_id, body["mk_id"], to, grant, now)
        _roster(db, user_id, body["mk_id"], body["roster"], now)
        _set_state(db, user_id, rekey_due=0)
        if body.get("begins"):
            _set_state(db, user_id, pq=1)       # never back: a post-quantum account's every write carries kem (_era)
    elif body["shape"] == "rekey":
        _new_master_key(db, user_id, body, now)
        if body.get("upgrade"):
            _recovery_key(db, user_id, body["recovery"], now)
            _apply_upgrade(db, user_id, body, hold, now)
    _new_content_key(db, user_id, body["content_key"], now)
    _store_statement(db, user_id, stored, now)
    if body["seal"]:
        _seal_after(db, hold, user_id, payload["key"], now)
    _forget_decided(db, hold, user_id, now)


def _seal_after(db, hold, user_id, key, now):
    """Inside the transaction that turns encryption on, or on again: what links sent in the clear sealed in place to the
    statement's key (`key`, as the statement has it). The database is due for compaction only when something was sealed,
    or a conversation or a report's lists dropped: only then does it hold texts that were written over. A post-quantum
    key is sealed to only where this server has ML-KEM (503 otherwise, and nothing is sealed in form 1 in its place)."""
    raw = link_commands.statement_key(key)
    if len(raw) == e2e.HYBRID_KEY and not e2e.ready():
        raise link.LinkError(503, e2e.MLKEM_REFUSED)
    sealed = seal_in_place(db, user_id, raw, now)
    _set_state(db, user_id, sealed=json.dumps(sealed), sealed_at=now)
    if sealed["events"] + sealed["rows"] + sealed["talks"] + sealed.get("dropped", 0) + sealed.get("reports", 0):
        _compaction_due(db, hold, user_id)


def _forget_decided(db, hold, user_id, now):
    """Inside the transaction that turns encryption on, or on again, after sealing in place or where it seals nothing:
    the words of the account's connector proposals already decided (their words, their reasons and the words on them),
    which stay outside what encryption seals, cleared at once rather than a day after each was decided
    (proposals.forget_decided); one that still waits keeps them for the owner to read. Where any were cleared, they
    were written over, so the database is due for compaction, as after sealing in place."""
    if proposals.forget_decided(db, user_id, now):
        _compaction_due(db, hold, user_id)


def _compaction_due(db, hold, user_id):
    """Inside a transaction that wrote texts over: the database due for compaction (a fresh token in compaction_due),
    which a thread of its own begins once this has committed (compact_soon, asked once however many wrote over), the
    request that settled the change not waiting."""
    _set_state(db, user_id, compaction_due=secrets.token_hex(16))
    if compact_soon not in hold["after"]:
        hold["after"].append(compact_soon)


# ---- the changes held while encryption is on ----------------------------------------------------------------------

def _rotation_bound(db, user_id, now):
    """Routine rotations are at most one in ROTATE_SETTING's seconds (a day by default; 0, none): 429 for one sooner
    after the last that took effect. Never a revocation's bound: a key that must go now is replaced by one."""
    every = _seconds(ROTATE_SETTING, ROTATE_DEFAULT)
    if not every:
        return
    last = db.execute("SELECT MAX(created_at) FROM ade_e2e_statements WHERE user_id = ? AND purpose = ? AND retires = "
                      "?", (user_id, E2E_KEY, "grace")).fetchone()[0]
    if last is not None and now - last < every:
        raise link.LinkError(429, f"A routine rotation is made at most once in {_span(every)}, and the last took effect "
                                  f"less than that ago. To replace a key that must go now, replace the keys (a "
                                  f"revocation), which has no such bound.")


def _seconds(setting, default):
    """A setting of whole seconds from 0 to a year, read now; the default otherwise."""
    raw = os.environ.get(setting, "").strip()
    return int(raw) if raw.isdigit() and int(raw) <= 365 * 86400 else default


def _span(seconds):
    return f"{seconds // 3600} hours" if seconds >= 3600 and seconds % 3600 == 0 else f"{seconds} seconds"


def rotate(user_id, data, now=None):
    """A routine rotation: a new content key wrapped under the current master key, and its statement (retires
    grace: the earlier keys are retired for sealing an hour after it takes effect); at most one a day (_rotation_bound).
    Signed by a passkey that unlocks the keys, unless the recovery code's proof comes with it. Held. {hold, told}."""
    now = _now(now)
    body = _object(data, "request")
    payload, raw, credential, signer, auth = link_commands.check_statement(user_id, body.get("statement"), (E2E_KEY,),
                                                                           now)
    if payload["retires"] != "grace":
        raise link.LinkError(422, "A routine rotation retires the earlier keys after a grace (retires: grace); a "
                                  "revocation retires them all.")
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        pq = _era(db, user_id, body)
        content = _content_key(body.get("content_key"), payload, pq)
        _ready(db, user_id, payload)
        if content["mk_id"] != current(db, user_id):
            raise link.LinkError(409, STALE_MK)
        if db.execute("SELECT 1 FROM ade_e2e_content_keys WHERE user_id = ? AND key_id = ?",
                      (user_id, content["key_id"])).fetchone():
            raise link.LinkError(409, "That content key is stated already: make a new one.")
        _rotation_bound(db, user_id, now)
        _may_sign(db, user_id, signer["id"])
        kind = _signed_or_proof(db, user_id, body, "rotate", now)
        by_code = kind == HOLD_RECOVERY
        if not by_code and not _staying(db, user_id, signer["id"]):
            raise link.LinkError(403, NOT_STAYING)
        waiting = _in_place_of(db, user_id, signer["id"], by_code)
        link_commands.commit_statement(db, user_id, payload, signer, auth, now)
        _replace(db, user_id, waiting, now)
        hold = _place(db, user_id, "rotate", {"statement": _stored(raw, credential, signer), "content_key": content,
                                              **_era_of(pq)},
                      kind, "A new content key for end-to-end encryption was signed", now, made_by=signer["id"],
                      by_code=by_code)
    return _told(hold, now)


def _signer_still(db, hold, passkey_id, stay=None):
    """As a signed change's hold ends, unless it is code-backed: its signer still unlocks the keys and stays (in `stay`,
    the credentials a revocation leaves; as they stand otherwise): 403 otherwise, so nothing of it is kept. (Whether
    it may still sign at all, _may_sign, is asked first.)"""
    if hold["by_code"]:
        return
    if not (passkey_id in stay.values() if stay is not None else _staying(db, hold["user_id"], passkey_id)):
        raise link.LinkError(403, NOT_STAYING)


def _apply_rotate(db, hold, body, now):
    if body["content_key"]["mk_id"] != current(db, hold["user_id"]):
        raise link.LinkError(409, STALE_MK)
    _era_still(db, hold["user_id"], body)
    _may_sign(db, hold["user_id"], body["statement"]["passkey_id"])
    _signer_still(db, hold, body["statement"]["passkey_id"])
    _new_content_key(db, hold["user_id"], body["content_key"], now)
    _store_statement(db, hold["user_id"], body["statement"], now)


def _revocation(db, user_id, plan):
    """The checks of a revocation, made as it is asked and again as its hold ends: the passkeys it removes are the
    account's, it keeps a hardware key while high security is or will be on and no passkey waiting to be judged when
    it turns it on, its grants go to every credential that stays and to the recovery key, and to nothing else, and
    every content key the account has is wrapped again, or left as it is (abandon: one under a master key the page's
    keys do not lead back to, which it cannot open, so one such row never blocks every revocation), each once. The
    credentials that stay ({credential_id: passkey_id})."""
    if plan["previous"] != current(db, user_id):
        raise link.LinkError(409, STALE_MK)
    if db.execute("SELECT 1 FROM ade_e2e_chain WHERE user_id = ? AND (mk_id = ? OR previous = ?)",
                  (user_id, plan["mk_id"], plan["mk_id"])).fetchone() or plan["mk_id"] == plan["previous"]:
        raise link.LinkError(422, "A revocation makes a new master key.")
    gone = set(plan["remove"])
    keys = {r["id"]: r for r in db.execute("SELECT id, tier, judge_again FROM passkeys WHERE user_id = ?", (user_id,))}
    for passkey_id in gone:
        if passkey_id not in keys:
            raise link.LinkError(404, "No such passkey.")
    kept = {k: r for k, r in keys.items() if k not in gone}
    high = plan["high_security"] or passkeys._high_security(db, user_id)
    if high and not any(r["tier"] == passkeys.HARDWARE for r in kept.values()):
        raise link.LinkError(409, passkeys.NO_HARDWARE_KEY if plan["high_security"] else passkeys.LAST_HARDWARE_KEY)
    if plan["high_security"] and any(r["judge_again"] for r in kept.values()):
        raise link.LinkError(409, passkeys.KEY_WAITING)
    stay = {r["credential_id"]: r["passkey_id"] for r in db.execute(
        "SELECT credential_id, passkey_id FROM ade_e2e_credentials WHERE user_id = ?", (user_id,))
        if r["passkey_id"] in kept and (not high or kept[r["passkey_id"]]["tier"] == passkeys.HARDWARE)}
    if set(plan["grants"]) != set(stay) | {RECOVERY}:
        # every credential that stays, or one left without a grant would be left out without being removed
        raise link.LinkError(422, "A revocation grants the new master key to every credential that stays enrolled and "
                                  "to the recovery key, and to nothing else.")
    have = {r["key_id"] for r in db.execute("SELECT key_id FROM ade_e2e_content_keys WHERE user_id = ?", (user_id,))}
    abandon = set(plan.get("abandon", []))
    if set(plan["content_keys"]) & abandon or set(plan["content_keys"]) | abandon != have:
        raise link.LinkError(422, EVERY_CONTENT_KEY)
    if plan["content_key"]["key_id"] in have:
        raise link.LinkError(409, "That content key is stated already: make a new one.")
    return stay


def revoke(user_id, data, now=None):
    """A revocation: a new master key, chained from the current one, granted to the credentials that stay (minus those
    removed, and minus every one but the hardware keys when it turns high security on) and to the recovery key, or to a
    new recovery code's; every content key wrapped again under it, a new content key, and a statement that retires
    every earlier key at once; the passkeys it removes removed, and high security turned on if it says so, which takes
    a hardware key's signature, as the switch itself takes a hardware key's word. Signed by a passkey that unlocks the
    keys and stays after it, unless the recovery code's proof comes with it. Held: a day when it replaces the recovery
    code, removes no passkey, or leaves no credential but the signer's where there were others, so the owner has the
    time to cancel one an attacker made; when it takes effect the old grants and wraps are gone. It takes the place of
    the change that waits as (v) allows (_in_place_of), so the owner puts a revocation of a stolen passkey in place of
    the thief's own at once, and with the recovery code's proof in place of anything a passkey made. With upgrade, it
    is the upgrade of a classical account to post-quantum keys (_upgrade_plan). Its
    body is read for its forms in the account's era (_era). {hold, told}."""
    now = _now(now)
    body = _object(data, "request")
    payload, raw, credential, signer, auth = link_commands.check_statement(user_id, body.get("statement"), (E2E_KEY,),
                                                                           now)
    if payload["retires"] != "all":
        raise link.LinkError(422, "A revocation retires every earlier key at once (retires: all).")
    upgrade = _flag(body.get("upgrade"), "upgrade")
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if upgrade and is_pq(db, user_id):
            raise link.LinkError(409, NOT_CLASSICAL)
        pq = _era(db, user_id, body, may_begin=upgrade)
        if upgrade and not pq:
            raise link.LinkError(422, UPGRADE_KEM)
        plan = _revocation_body(body, payload, pq)
        touches = {}
        if upgrade:
            plan, touches = _upgrade_plan(db, user_id, body, plan)
            plan = dict(plan, begins=True)          # as an older release keeps it, whose _era_still reads begins alone
        if plan["high_security"] and signer["tier"] != passkeys.HARDWARE:
            raise link.LinkError(403, passkeys.NEEDS_HARDWARE_TO_TURN)
        _ready(db, user_id, payload)
        others = {r["passkey_id"] for r in db.execute("SELECT passkey_id FROM ade_e2e_credentials WHERE user_id = ?",
                                                      (user_id,))} - {signer["id"]}
        stay = _revocation(db, user_id, plan)
        if upgrade:
            _upgrade_checks(db, user_id, plan, stay)
        _may_sign(db, user_id, signer["id"])
        kind = _signed_or_proof(db, user_id, body, "revoke", now)       # a recovery proof given with it, checked
        by_code = kind == HOLD_RECOVERY
        if upgrade:
            plan = _upgrade_proofs(db, user_id, body, plan, by_code, now)
            _upgrade_touches(db, user_id, plan, touches, signer["id"], raw, now)
        if not by_code and signer["id"] not in stay.values():
            raise link.LinkError(403, NOT_STAYING)
        waiting = _in_place_of(db, user_id, signer["id"], by_code)
        gone = removed_by(db, user_id, "revoke", plan)
        if kind == HOLD and (plan["recovery"] is not None or not gone
                             or (others and not set(stay.values()) - {signer["id"]})):
            kind = HOLD_LONG
        link_commands.commit_statement(db, user_id, payload, signer, auth, now)
        _replace(db, user_id, waiting, now)
        hold = _place(db, user_id, "revoke", dict(plan, statement=_stored(raw, credential, signer)), kind,
                      "The keys of end-to-end encryption are being upgraded to post-quantum keys" if upgrade else
                      "The keys of end-to-end encryption are being replaced", now, made_by=signer["id"],
                      by_code=by_code)
        # the app connections the passkeys it removes allowed end now, not when its hold does (oauth.py)
        oauth.passkeys_going(db, user_id, plan["remove"], oauth.REVOCATION_WHY, now)
        # and so do the link tickets they confirmed, every ticket no hardware key confirmed when it turns high security
        # on, and each approval or pairing still held, as removing a passkey and turning high security on do with
        # encryption off (a removal's placement cancels it): a pairing that a lost passkey's
        # ticket made links nothing while the revocation waits on its own hold
        if plan["remove"]:
            link.void_tickets(db, user_id, f"confirmed_by IN ({','.join('?' * len(plan['remove']))})",
                              tuple(plan["remove"]))
            # the devices their word approved too: refused if not signed in yet, their sign-ins ended if they are
            passkeys.devices_approved_by(db, user_id, plan["remove"], now)
        if plan["high_security"]:
            link.void_tickets(db, user_id, "(confirmed IS NULL OR confirmed <> ?)", (link.TICKET_BY_HARDWARE,))
            passkeys.devices_not_by_hardware(db, user_id, now)
        if plan["remove"] or plan["high_security"]:
            holds.cancel_all(db, user_id, now)
    return _told(hold, now)


def _names(db, user_id, passkey_ids):
    """The passkeys' names, as a hold's summary gives them, in the account's order: “Phone”, “Laptop”; "" for none."""
    ids = list(passkey_ids)
    if not ids:
        return ""
    rows = db.execute(f"SELECT name FROM passkeys WHERE user_id = ? AND id IN ({','.join('?' * len(ids))}) ORDER BY "
                      f"created_at, id", (user_id, *ids)).fetchall()
    return ", ".join(f"“{r['name'] or 'a passkey'}”" for r in rows)


def _end_pending(db, user_id, row, why, now):
    """Inside a change's own transaction: the change that waits (`row`) ends now, failed with `why`, its body discarded
    (holds.end: never taking effect, past cancelling)."""
    db.execute("DELETE FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?", (user_id, row["hold_id"]))
    holds.end(db, [row["hold_id"]], why, now)


def _revocation_body(body, payload, pq=False):
    """A revocation's body read for its forms, in its era (`pq`: post-quantum keys, 60-byte wraps, form-2 grants)."""
    content = _content_key(body.get("content_key"), payload, pq)
    mk_id = _hex32(body.get("mk_id"), "new master key's id")
    if content["mk_id"] != mk_id:
        raise link.LinkError(422, "The new content key is wrapped under the new master key.")
    chain = _object(body.get("chain"), "chain")
    remove = body.get("remove", [])
    if remove is None:
        remove = []
    if not isinstance(remove, list) or len(remove) > passkeys.MAX_PASSKEYS \
            or not all(isinstance(p, str) and p for p in remove) or len(set(remove)) != len(remove):
        raise link.LinkError(422, "A revocation's remove is a list of the account's passkeys' ids, each once.")
    rewrapped = body.get("content_keys")
    if not isinstance(rewrapped, list):
        raise link.LinkError(422, "A revocation's content_keys are every earlier content key, wrapped again.")
    wraps = {}
    for k in rewrapped:
        k = _object(k, "content key")
        key_id = _hex32(k.get("key_id"), "content key's id")
        if key_id in wraps:
            raise link.LinkError(422, "Each content key is wrapped again once.")
        # a retired classical key too: on a post-quantum account its 32-byte scalar, in 60 bytes
        wraps[key_id] = _key_wrap(k.get("wrapped"), "content key's wrap", pq)
    abandon = body.get("abandon", [])
    if abandon is None:
        abandon = []
    if not isinstance(abandon, list) or not all(isinstance(k, str) and _HEX32.fullmatch(k) for k in abandon) \
            or len(set(abandon)) != len(abandon):
        raise link.LinkError(422, "A revocation's abandon is a list of the content keys it leaves as they are, by "
                                  "key_id, each once.")
    return {"mk_id": mk_id, "previous": _hex32(chain.get("previous"), "chain's previous master key id"),
            "chain": _wrap(chain.get("wrapped"), "chain's wrap"), "grants": _grants(body.get("grants"), pq),
            "roster": _wrap(body.get("roster"), "roster", most=ROSTER_MAX), "content_key": content,
            "content_keys": wraps, "abandon": abandon, "remove": remove,
            "high_security": _flag(body.get("high_security"), "high_security"),
            "recovery": _recovery(body["recovery"], pq) if body.get("recovery") is not None else None, **_era_of(pq)}


# ---- the upgrade to post-quantum keys ---------------------------------------------

def upgrade_touch_subject(mk_id, statement, credential_id, salt, public_key):
    """What a passkey's touch in the upgrade is made for (passkeys.CHANGE's e2e-upgrade): "e2e-upgrade:" and the first
    16 bytes, as 32 lowercase hex, of the sha256 of the RFC 8785 bytes of {credential_id: the passkey's credential id as
    the account's view gives it, mk_id: the new master key's id, public_key: the credential's new key and salt: its
    fresh salt, both as base64url, statement: the lowercase hex sha256 of the version-3 statement's canonical bytes
    (`statement`)}: what exists before its touch. Its wrap cannot be bound, being made from the touch's output."""
    digest = hashlib.sha256(canonicalize({"credential_id": credential_id, "mk_id": mk_id, "public_key": public_key,
                                          "salt": salt, "statement": hashlib.sha256(statement).hexdigest()})).digest()
    return "e2e-upgrade:" + digest[:16].hex()


def _upgrade_plan(db, user_id, body, plan):
    """The upgrade's own parts beside a revocation's, read for their forms: `credentials`, one entry for each enrolled
    passkey that stays ({passkey_id, salt: a fresh one, 32 bytes, public_key: its fresh post-quantum key, wrapped: its
    seed's wrap, 60 bytes, anchor: the new master key's id, confirmation: its own touch, unless it signed the
    statement}); the recovery key, always (a post-quantum key from the code, the one kept or a new one); and `kept`,
    whether that code is the one in force. (the plan with them and upgrade, kept as the change's body: no confirmation
    in it; each entry's confirmation, by passkey). The revocation adds begins, as an older release keeps it; the fold, a
    turn-on again, adds none (_era_still)."""
    if plan["recovery"] is None:
        raise link.LinkError(422, "The upgrade makes the recovery key post-quantum too: it carries recovery, from the "
                                  "code kept or a new one.")
    given = body.get("credentials")
    if not isinstance(given, list) or not given or len(given) > passkeys.MAX_PASSKEYS:
        raise link.LinkError(422, "The upgrade's credentials are a list: each enrolled passkey that stays, with its "
                                  "post-quantum key made at its touch.")
    creds, touches = {}, {}
    for c in given:
        c = _object(c, "credential")
        passkey_id = c.get("passkey_id")
        if not isinstance(passkey_id, str) or passkey_id in creds:
            raise link.LinkError(422, "Each credential of the upgrade names one passkey, once.")
        _bytes(c.get("salt"), "credential's salt", exact=32)
        _key(c.get("public_key"), "credential's public key", pq=True)
        _key_wrap(c.get("wrapped"), "credential's wrap", pq=True)
        if _hex32(c.get("anchor"), "credential's anchor") != plan["mk_id"]:
            raise link.LinkError(422, "A post-quantum credential's anchor is the master key the change names.")
        confirmation = c.get("confirmation")
        if confirmation is not None and not isinstance(confirmation, str):
            raise link.LinkError(422, "A credential's confirmation is the one POST /api/account/confirm gave.")
        creds[passkey_id] = {"salt": c["salt"], "public_key": c["public_key"], "wrapped": c["wrapped"],
                             "anchor": c["anchor"]}
        touches[passkey_id] = confirmation
    kept = _flag(body.get("kept"), "kept")
    return dict(plan, upgrade=True, credentials=creds, kept=kept), touches


def _upgrade_checks(db, user_id, plan, stay, every=UPGRADE_EVERY):
    """The checks of an upgrade beside a revocation's (_revocation, whose credentials that stay are `stay`), made as it
    is asked and again as its hold ends: the account's keys are classical; its credentials are exactly those that stay
    (each enrolled passkey not removed: one not touched in the sitting is removed by it; `every`, the words otherwise:
    the fold's, which removes none), each under a fresh salt, not the one its record has; and, where it keeps the
    recovery code, the account's recovery key is still the one the code was proved against as it was placed
    (kept_against: a used proof cannot be checked again a day later)."""
    if is_pq(db, user_id):
        raise link.LinkError(409, NOT_CLASSICAL)
    if set(plan["credentials"]) != set(stay.values()):
        raise link.LinkError(422, every)
    for passkey_id, c in plan["credentials"].items():
        row = _enrolled(db, user_id, passkey_id)
        if row is None or c["salt"] == row["salt"]:
            raise link.LinkError(422, SALT_SAME)
    if plan.get("kept_against") is not None and plan["kept_against"] != _recovery_public(db, user_id):
        raise link.LinkError(409, CODE_CHANGED)


def _upgrade_proofs(db, user_id, body, plan, by_code, now, purpose="revoke"):
    """Where the upgrade keeps the recovery code in force (kept): its proof came with it (the classical one, against the
    classical recovery key: `by_code`, checked already as the change's recovery_proof), and the hybrid proof of the new
    recovery key the page derived from it (new_recovery_proof: the one who sends it holds that key), made for the
    change's `purpose` (revoke, or turn-on for the fold), used up now; and the classical recovery key the first was
    made against, kept with the change (kept_against), since neither proof can be checked again when its hold ends. A
    new code (kept false) takes no new_recovery_proof. The plan with it."""
    new_proof = body.get("new_recovery_proof")
    if not plan["kept"]:
        if new_proof is not None:
            raise link.LinkError(422, "A new_recovery_proof proves the code kept: an upgrade with a new code (kept "
                                      "false) carries none.")
        return plan
    if not by_code or new_proof is None:
        raise link.LinkError(403, KEPT_PROOFS)
    use_proof(db, user_id, new_proof, purpose, now, public_key=plan["recovery"]["public_key"])
    return dict(plan, kept_against=_recovery_public(db, user_id))


def _upgrade_touches(db, user_id, plan, touches, signer_id, statement, now, words=UPGRADE_OWN):
    """Each passkey that stays in the upgrade, but the statement's signer (whose touch is its signature), comes with a
    confirmation its own passkey made, at its own touch, for the upgrade's ceremony (TOUCH_UPGRADE) and for what exists
    before that touch (upgrade_touch_subject), used up as the change is placed: so the key a credential names is never
    one put in for a passkey nobody touched in the sitting, nor one a touch made for another change. 403 otherwise, in
    `words` (the fold's, which removes no passkey left out, are FOLD_OWN). The fold's touches are the upgrade's
    ceremony too: each is bound to its statement's digest, and a statement is taken by one change alone."""
    for passkey_id, c in plan["credentials"].items():
        if passkey_id == signer_id:
            continue
        row = _enrolled(db, user_id, passkey_id)
        subject = upgrade_touch_subject(plan["mk_id"], statement, row["credential_id"], c["salt"], c["public_key"])
        try:
            passkeys.use_passkey_confirmation(db, user_id, touches.get(passkey_id), now, words,
                                              only_by=[passkey_id], not_by_words=words, purpose=TOUCH_UPGRADE,
                                              subject=subject)
        except passkeys.PasskeyError as e:
            raise link.LinkError(e.status, str(e)) from None


def _apply_upgrade(db, user_id, plan, hold, now):
    """Inside the revocation's transaction as the upgrade's hold ends, once the revocation is written: each credential
    that stays with its fresh salt, key, wrap and anchor; the account post-quantum, never back; and the database
    compacted now and again a day later (COMPACT_AGAIN), by which time live links have sent their History lists and
    conversations again under the new key."""
    for passkey_id, c in plan["credentials"].items():
        db.execute("UPDATE ade_e2e_credentials SET salt = ?, public_key = ?, wrapped = ?, anchor = ? WHERE user_id = ? "
                   "AND passkey_id = ?", (c["salt"], c["public_key"], c["wrapped"], c["anchor"], user_id, passkey_id))
    _set_state(db, user_id, pq=1, compact_again_at=now + COMPACT_AGAIN)
    _compaction_due(db, hold, user_id)


def _apply_revoke(db, hold, plan, now):
    user_id = hold["user_id"]
    _era_still(db, user_id, plan)
    _may_sign(db, user_id, plan["statement"]["passkey_id"])
    stay = _revocation(db, user_id, plan)
    if plan.get("upgrade"):
        _upgrade_checks(db, user_id, plan, stay)
    _signer_still(db, hold, plan["statement"]["passkey_id"], stay)
    _store_statement(db, user_id, plan["statement"], now)
    try:
        for passkey_id in plan["remove"]:
            passkeys.remove_in(db, user_id, passkey_id, now)
        # those whose time is after this revocation's own (holds.cancel_all's after): a settle that runs late applies the
        # holds that ended meanwhile next, which it would have cancelled on time
        if plan["remove"]:
            # as removing a passkey from Account does: each approval still held that names no passkey may have been
            # confirmed with one removed here, so none takes effect
            holds.cancel_all(db, user_id, now, hold["takes_effect_at"])
        if plan["high_security"] and not passkeys._high_security(db, user_id):
            passkeys.check_high_security(db, user_id)
            passkeys.high_security_in(db, user_id, now, hold["takes_effect_at"])
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None
    _new_master_key(db, user_id, plan, now)
    _new_content_key(db, user_id, plan["content_key"], now)
    if plan["recovery"] is not None:
        _recovery_key(db, user_id, plan["recovery"], now)
    if plan.get("upgrade"):
        _apply_upgrade(db, user_id, plan, hold, now)


def _new_master_key(db, user_id, plan, now):
    """Inside a revocation's transaction, or a turn-on's that makes a new master key, checked already (_revocation):
    every credential that no longer unlocks the keys as they stand dropped (_stale), the grants made again to those
    that stay and to the recovery key, the chain's new link, the roster, and every content key wrapped again under the
    new master key, but those it leaves as they are (abandon); which is then no longer due (rekey_due)."""
    for passkey_id in _stale(db, user_id):
        db.execute("DELETE FROM ade_e2e_credentials WHERE user_id = ? AND passkey_id = ?", (user_id, passkey_id))
    db.execute("DELETE FROM ade_e2e_grants WHERE user_id = ?", (user_id,))
    for to, grant in plan["grants"].items():
        _grant(db, user_id, plan["mk_id"], to, grant, now)
    db.execute("INSERT INTO ade_e2e_chain (user_id, mk_id, previous, wrapped, created_at) VALUES (?,?,?,?,?)",
               (user_id, plan["mk_id"], plan["previous"], plan["chain"], now))
    _roster(db, user_id, plan["mk_id"], plan["roster"], now)
    for key_id, wrapped in plan["content_keys"].items():
        db.execute("UPDATE ade_e2e_content_keys SET mk_id = ?, wrapped = ? WHERE user_id = ? AND key_id = ?",
                   (plan["mk_id"], wrapped, user_id, key_id))
    _set_state(db, user_id, rekey_due=0)


def _confirmed(db, user_id, body, purpose, touch, now):
    """A change without a statement of its own, confirmed (_confirm): by the recovery code's proof (code-backed), or by
    a passkey that unlocks the keys already and stays (403 otherwise: never the passkey a change adds). (kind of hold,
    made_by, by_code)."""
    kind, made_by = _confirm(db, user_id, body, purpose, touch, now)
    by_code = made_by == RECOVERY
    if not by_code and not _staying(db, user_id, made_by):
        raise link.LinkError(403, CONFIRM_STAYING)
    return kind, made_by, by_code


def enrol(user_id, data, now=None):
    """A passkey that unlocks the keys: its credential (its own wrap made while it was touched), the current master key
    sealed to it (a grant), and the roster rewritten to name it; one the account has, not enrolled yet, a hardware key
    while high security is on. The credential comes with a confirmation the passkey being added made itself
    (`confirmation` in it: the page's second touch of it), used up as the change is placed, so the key a credential
    names is never one put in for a passkey nobody touched (403 otherwise). Confirmed by a passkey that unlocks the keys
    already (never the one added), or by the recovery code's proof. Held. {hold, told}."""
    now = _now(now)
    body = _object(data, "request")
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not on(db, user_id):
            raise link.LinkError(409, NOT_ON)
        pq = _era(db, user_id, body)
        mk_id = _hex32(body.get("mk_id"), "master key's id")
        cred = _credential(db, user_id, body.get("credential"), pq, mk_id)
        if _enrolled(db, user_id, cred["passkey_id"]) is not None:
            raise link.LinkError(409, "That passkey unlocks end-to-end encryption already.")
        if mk_id != current(db, user_id):
            raise link.LinkError(409, STALE_MK)
        plan = {"credential": cred, "mk_id": mk_id, "grant": _sealed(body.get("grant"), "grant", pq),
                "roster": _wrap(body.get("roster"), "roster", most=ROSTER_MAX), "own_touch": True, **_era_of(pq)}
        kind, made_by, by_code = _confirmed(db, user_id, body, "credentials", TOUCH_ENROL, now)
        try:
            passkeys.use_passkey_confirmation(db, user_id, body["credential"].get("confirmation"), now, ENROL_OWN,
                                              only_by=[cred["passkey_id"]], not_by_words=ENROL_OWN,
                                              purpose=TOUCH_ENROL_OWN)
        except passkeys.PasskeyError as e:
            raise link.LinkError(e.status, str(e)) from None
        waiting = _in_place_of(db, user_id, made_by, by_code)
        _replace(db, user_id, waiting, now)
        hold = _place(db, user_id, "credentials", plan, kind,
                      f"“{cred['name']}” was enrolled to unlock end-to-end encryption", now, made_by=made_by,
                      by_code=by_code)
    return _told(hold, now)


def _confirmer_still(db, hold):
    """As a confirmed change's hold ends: unless it is code-backed, the passkey that confirmed it still unlocks the keys
    and stays (403 otherwise, so nothing of it is kept)."""
    if not hold["by_code"] and not _staying(db, hold["user_id"], hold["made_by"]):
        raise link.LinkError(403, CONFIRM_STAYING)


def _apply_enrol(db, hold, plan, now):
    """A passkey's enrolment's hold has ended: the master key it names still current, the passkey that confirmed it
    still one that unlocks the keys (unless the code's proof did), the passkey added still one the account may add, and
    placed with its own touch (one placed under an earlier rule, without it, is refused)."""
    user_id = hold["user_id"]
    if plan["mk_id"] != current(db, user_id):
        raise link.LinkError(409, STALE_MK)
    _era_still(db, user_id, plan)
    _confirmer_still(db, hold)
    if plan.get("own_touch") is not True:
        raise link.LinkError(409, ENROL_OWN)
    row = _passkey(db, user_id, plan["credential"]["passkey_id"])
    _may_enrol(db, user_id, row)
    if _enrolled(db, user_id, row["id"]) is not None:
        raise link.LinkError(409, "That passkey unlocks end-to-end encryption already.")
    _enrol(db, user_id, plan["credential"], now)
    _grant(db, user_id, plan["mk_id"], plan["credential"]["credential_id"], plan["grant"], now)
    _roster(db, user_id, plan["mk_id"], plan["roster"], now)


def off(user_id, data, now=None):
    """Encryption turned off: a signed e2e-off statement, signed by a passkey that unlocks the keys unless the recovery
    code's proof comes with it. Held a day, as large a change as there is: once it takes effect links send in the clear
    again. Nothing sealed is opened. {hold, told}."""
    now = _now(now)
    body = _object(data, "request")
    payload, raw, credential, signer, auth = link_commands.check_statement(user_id, body.get("statement"), (E2E_OFF,),
                                                                           now)
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        pq = _era(db, user_id, body)
        _ready(db, user_id, payload)
        _may_sign(db, user_id, signer["id"])
        kind = _signed_or_proof(db, user_id, body, "off", now)
        by_code = kind == HOLD_RECOVERY
        if not by_code and not _staying(db, user_id, signer["id"]):
            raise link.LinkError(403, NOT_STAYING)
        waiting = _in_place_of(db, user_id, signer["id"], by_code)
        link_commands.commit_statement(db, user_id, payload, signer, auth, now)
        _replace(db, user_id, waiting, now)
        hold = _place(db, user_id, "off", {"statement": _stored(raw, credential, signer), **_era_of(pq)},
                      HOLD_LONG if kind == HOLD else kind, "Turning end-to-end encryption off was signed", now,
                      made_by=signer["id"], by_code=by_code)
    return _told(hold, now)


def _apply_off(db, hold, body, now):
    _era_still(db, hold["user_id"], body)
    _may_sign(db, hold["user_id"], body["statement"]["passkey_id"])
    _signer_still(db, hold, body["statement"]["passkey_id"])
    _store_statement(db, hold["user_id"], body["statement"], now)


def _recovery_public(db, user_id):
    row = db.execute("SELECT public_key FROM ade_e2e_recovery WHERE user_id = ?", (user_id,)).fetchone()
    return row["public_key"] if row is not None else None


def anchors(user_id, data, now=None):
    """Renewed anchors: enrolled credentials' own wraps made again, each naming the current master key as its anchor,
    under the key its passkey's PRF output gives for a fresh salt (32 bytes, never the one its record has), which is
    kept with it and served with it; and the recovery code's anchor made again so too (recovery_anchor: {public_key,
    anchor}), sealed under a key of the code's, of the recovery key it names, which must be the account's as it is asked
    and as its hold ends. One of them at least. Confirmed by a passkey that unlocks the keys, or by the recovery code's
    proof; and each renewed anchor but the confirming passkey's own comes with a confirmation its own passkey made
    (`confirmation` in its entry), so no passkey rewrites another's record without that one's touch. The code's anchor
    is written again only with the code's proof (403 otherwise): the server cannot tell an anchor that opens from one
    that does not, and one that does not leaves the code unable to unlock, so only the code writes its own. Held (a
    day, as a change the code made), its summary naming the passkeys. On a post-quantum account each renewed wrap is of
    the credential's seed, 60 bytes, and names its anchor (`anchor`, the current master key's id), which its record
    keeps. {hold, told}."""
    now = _now(now)
    body = _object(data, "request")
    given = body.get("anchors")
    code = body.get("recovery_anchor")
    if not isinstance(given, list) or len(given) > passkeys.MAX_PASSKEYS or not (given or code is not None):
        raise link.LinkError(422, "Renewed anchors are a list of {passkey_id, salt, wrapped, anchor?, confirmation?}, "
                                  "and the recovery code's {public_key, anchor}: one of them at least.")
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        pq = _era(db, user_id, body)
        mk_id = _hex32(body.get("mk_id"), "master key's id")
        renewed, touched = {}, {}
        for a in given:
            a = _object(a, "anchor")
            if not isinstance(a.get("passkey_id"), str) or a["passkey_id"] in renewed:
                raise link.LinkError(422, "Each renewed anchor names one enrolled passkey, once.")
            _bytes(a.get("salt"), "renewed anchor's salt", exact=32)
            renewed[a["passkey_id"]] = {"salt": a["salt"], "wrapped": _key_wrap(a.get("wrapped"), "credential's wrap",
                                                                                pq)}
            if pq:
                if _hex32(a.get("anchor"), "renewed anchor's anchor") != mk_id:
                    raise link.LinkError(422, "A post-quantum credential's anchor is the master key the change names.")
                renewed[a["passkey_id"]]["anchor"] = a["anchor"]
            elif a.get("anchor") is not None:
                raise link.LinkError(422, CLASSICAL_FORMS)
            touched[a["passkey_id"]] = a.get("confirmation")
        recovery = None
        if code is not None:
            code = _object(code, "recovery code's renewed anchor")
            recovery = {"public_key": _key(code.get("public_key"), "recovery key", pq),
                        "anchor": _wrap(code.get("anchor"), "recovery anchor")}
        if not on(db, user_id):
            raise link.LinkError(409, NOT_ON)
        if mk_id != current(db, user_id):
            raise link.LinkError(409, STALE_MK)
        for passkey_id, a in renewed.items():
            row = _enrolled(db, user_id, passkey_id)
            if row is None:
                raise link.LinkError(422, "A renewed anchor is of a passkey that unlocks end-to-end encryption.")
            if a["salt"] == row["salt"]:
                raise link.LinkError(422, SALT_SAME)
        if recovery is not None and recovery["public_key"] != _recovery_public(db, user_id):
            raise link.LinkError(409, CODE_CHANGED)
        if recovery is not None and body.get("recovery_proof") is None:
            raise link.LinkError(403, ANCHOR_CODE)
        kind, made_by, by_code = _confirmed(db, user_id, body, "anchors", TOUCH_ANCHOR, now)
        for passkey_id in renewed:
            if passkey_id != made_by:
                try:
                    passkeys.use_passkey_confirmation(db, user_id, touched[passkey_id], now, ANCHOR_OWN,
                                                      only_by=[passkey_id], not_by_words=ANCHOR_OWN,
                                                      purpose=TOUCH_ANCHOR)
                except passkeys.PasskeyError as e:
                    raise link.LinkError(e.status, str(e)) from None
        waiting = _in_place_of(db, user_id, made_by, by_code)
        _replace(db, user_id, waiting, now)
        names = _names(db, user_id, renewed)
        hold = _place(db, user_id, "anchors", {"mk_id": mk_id, "anchors": renewed, "recovery": recovery, **_era_of(pq)},
                      kind, "Renewed anchors for end-to-end encryption" + (f": {names}" if names else ""), now,
                      made_by=made_by, by_code=by_code)
    return _told(hold, now)


def _apply_anchors(db, hold, plan, now):
    """Renewed anchors' hold has ended: the master key they name still current, the passkey that confirmed them still
    one that unlocks the keys (unless the code's proof did), each passkey still enrolled, and the recovery code the one
    the renewed anchor is of (a body from before it was renewed has none), and the code's anchor written by the code's
    proof alone (one a passkey confirmed, under an earlier rule, is refused)."""
    user_id = hold["user_id"]
    if plan["mk_id"] != current(db, user_id):
        raise link.LinkError(409, STALE_MK)
    _era_still(db, user_id, plan)
    _confirmer_still(db, hold)
    recovery = plan.get("recovery")
    if recovery is not None and not hold["by_code"]:
        raise link.LinkError(409, ANCHOR_CODE)
    if recovery is not None and recovery["public_key"] != _recovery_public(db, user_id):
        raise link.LinkError(409, CODE_CHANGED)
    for passkey_id, renewed in plan["anchors"].items():
        if _enrolled(db, user_id, passkey_id) is None:
            raise link.LinkError(409, "A passkey of the renewed anchors no longer unlocks end-to-end encryption.")
        db.execute("UPDATE ade_e2e_credentials SET salt = ?, wrapped = ?, anchor = COALESCE(?, anchor) WHERE "
                   "user_id = ? AND passkey_id = ?", (renewed["salt"], renewed["wrapped"], renewed.get("anchor"),
                                                      user_id, passkey_id))
    if recovery is not None:
        db.execute("UPDATE ade_e2e_recovery SET anchor = ? WHERE user_id = ? AND public_key = ?",
                   (recovery["anchor"], user_id, recovery["public_key"]))


def hold_passkey(db, values, recovery, now, confirmed_by=None):
    """Inside passkeys.register's transaction, while encryption is on: the passkey it made (`values`, a row of
    passkeys) waits on a hold, added when the hold ends; its options were confirmed by a passkey (`confirmed_by`), or by
    the recovery code (`recovery`: code-backed). It takes the place of the change that waits as (v) allows
    (_in_place_of): so an owner left with only the recovery code adds a passkey whatever a stolen one keeps placing.
    503 where the code could not cancel it (code_may_cancel)."""
    user_id = values["user_id"]
    code_may_cancel(db, user_id)
    waiting = _in_place_of(db, user_id, None if recovery else confirmed_by, recovery)
    _replace(db, user_id, waiting, now)
    return _place(db, user_id, "passkey", {"passkey": values}, HOLD_RECOVERY if recovery else HOLD,
                  f"A passkey was added to your account: “{values['name']}”", now,
                  made_by=RECOVERY if recovery else confirmed_by, by_code=recovery)


def _apply_passkey(db, hold, body, now):
    """A passkey's addition takes effect as its hold ends, but where the recovery code could no longer have cancelled
    it (code_may_cancel: this server lost ML-KEM since)."""
    code_may_cancel(db, hold["user_id"])
    values = dict(body["passkey"])
    try:
        passkeys.insert_passkey(db, values)
    except passkeys.PasskeyError as e:
        raise link.LinkError(e.status, str(e)) from None


def upload(link_row, data, now=None):
    """Statements past the server's newest that a link holds, sent in after the server was restored from a backup:
    each one canonical, signed by one of the account's passkeys, and chained from the server's newest by seq and prev.
    One the server holds already, byte for byte, is passed over; another at a seq it holds is a fork (409). What is new
    is held as a change is, with its notice, since one the owner cancelled is signed as well; one the owner cancelled
    when it was sent in before is refused (409); one whose key is not a key anything can be sealed to (an X25519 key in
    a version-2 statement, an X-Wing key in a version-3 one) is refused (422), and so is a classical key stated after a
    post-quantum one, in the server's chain or among those sent in (409, CLASSICAL_AFTER: what links seal to never goes
    back); one signed by a passkey that does not unlock the keys is refused (403), and while high security is
    on, one a passkey other than a hardware key signed (403), here and when
    the hold ends, as it is when the owner signs one. A key statement that turns encryption on again is held to what
    turning it on again is (_turn_on_checks) as its hold ends, and seals in place what was sent in the clear. Nothing
    of its content keys comes with it (what was sealed under a key the backup lacks cannot be opened). It takes the
    place of no change: 409 while one waits. 503 where the recovery code could not cancel it (code_may_cancel), here
    and as its hold ends. {held, hold, e2e}."""
    now = _now(now)
    user_id = link_row["user_id"]
    items = data.get("statements") if isinstance(data, dict) else None
    if not isinstance(items, list) or not 0 < len(items) <= UPLOAD_MAX:
        raise link.LinkError(422, f"Statements sent in are {{statements: [{{payload, credential}}]}}, at most "
                                  f"{UPLOAD_MAX}.")
    got = []
    for item in items:
        item = _object(item, "statement")
        payload = item.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                raise link.LinkError(422, "A statement's payload is its canonical text, or the object.") from None
        raw, signer = link_commands.check_uploaded(user_id, payload, item.get("credential"))
        if isinstance(item.get("payload"), str) and item["payload"].encode("utf-8") != raw:
            raise link.LinkError(422, "A statement's payload text is its canonical form.")
        if payload["purpose"] == E2E_KEY:
            _key(payload["key"], "statement's key", pq=payload["v"] == link_commands.STATEMENT_HYBRID)
        got.append((payload["seq"], raw, item["credential"], signer))
    got.sort(key=lambda g: g[0])
    hold = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        top = newest(db, user_id)
        fresh = []
        for seq, raw, signature, signer in got:
            held = db.execute("SELECT payload FROM ade_e2e_statements WHERE user_id = ? AND seq = ?",
                              (user_id, seq)).fetchone()
            if held is not None:
                if held["payload"].encode("utf-8") != raw:
                    raise link.LinkError(409, f"The server holds another statement at seq {seq}: the two are a fork.")
                continue
            _may_sign(db, user_id, signer["id"])
            if not _staying(db, user_id, signer["id"]):
                raise link.LinkError(403, UPLOAD_STAYING)
            if db.execute("SELECT 1 FROM ade_e2e_refused WHERE user_id = ? AND digest = ?",
                          (user_id, hashlib.sha256(raw).hexdigest())).fetchone():
                raise link.LinkError(409, REFUSED_AGAIN)
            fresh.append({"payload": raw.decode("utf-8"), "credential": json.dumps(signature, separators=(",", ":")),
                          "passkey_id": signer["id"]})
        if fresh:
            last = top
            hybrid = _hybrid_stated(db, user_id)
            for stored in fresh:
                payload = json.loads(stored["payload"])
                seq, prev = _next_of(last)
                if payload["seq"] != seq or payload["prev"] != prev:
                    raise link.LinkError(409, f"The statements sent in do not chain from the server's newest: "
                                              f"statement {payload['seq']} is not the one after {seq - 1}.")
                if payload["purpose"] == E2E_KEY:
                    if payload["v"] != link_commands.STATEMENT_HYBRID and hybrid:
                        raise link.LinkError(409, CLASSICAL_AFTER)
                    hybrid = hybrid or payload["v"] == link_commands.STATEMENT_HYBRID
                last = {"seq": payload["seq"], "payload": stored["payload"]}
            _free(db, user_id)
            code_may_cancel(db, user_id)
            hold = _place(db, user_id, "upload", {"statements": fresh}, HOLD,
                          f"A linked session sent {len(fresh)} statement(s) of your encryption keys the server lacks",
                          now)
        stated = said(top)
    if hold is None:
        return {"held": 0, "hold": None, "e2e": stated}
    out = _told(hold, now)
    return {"held": len(fresh), "hold": out["hold"], "e2e": stated}


def _may_sign(db, user_id, passkey_id):
    """While high security is on, a statement counts only if a hardware key signed it (403): one a link sends in, and,
    as its hold ends, one the owner signed."""
    if not passkeys._may_sign(db, passkey_id, user_id):
        raise link.LinkError(403, passkeys.ONLY_HARDWARE_SIGNS)


def _apply_upload(db, hold, body, now):
    """Statements sent in, kept in order as their hold ends, each still signed by a passkey that may sign and unlocks
    the keys; one that turns encryption on again held to what turning it on again is (_turn_on_checks: no new master
    key due, its signer one that unlocks the keys, every earlier key retired), what links sent in the clear sealed in
    place to its key, and the decided proposals' words cleared, as turning it on does; none where the recovery code
    could no longer have cancelled them (code_may_cancel: this server lost ML-KEM since)."""
    user_id = hold["user_id"]
    code_may_cancel(db, user_id)
    for stored in body["statements"]:
        _may_sign(db, user_id, stored["passkey_id"])
        if not _staying(db, user_id, stored["passkey_id"]):
            raise link.LinkError(403, UPLOAD_STAYING)
        payload = json.loads(stored["payload"])
        turning_on = payload["purpose"] == E2E_KEY and not on(db, user_id)
        if turning_on:
            _turn_on_checks(db, user_id, {"shape": "again"}, payload, stored["passkey_id"])
        _store_statement(db, user_id, stored, now)
        if turning_on:
            _seal_after(db, hold, user_id, payload["key"], now)
            _forget_decided(db, hold, user_id, now)


_APPLY = {"turn-on": _apply_turn_on, "rotate": _apply_rotate, "revoke": _apply_revoke, "credentials": _apply_enrol,
          "off": _apply_off, "anchors": _apply_anchors, "passkey": _apply_passkey, "upload": _apply_upload}


def _apply(db, hold, now):
    """A change's hold has ended: its body, checked again, takes effect in this transaction, or nothing of it does and
    the reason is kept on the hold. Either way it no longer waits."""
    row = db.execute("SELECT * FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?",
                     (hold["user_id"], hold["id"])).fetchone()
    if row is None:
        return "The change no longer waits."
    db.execute("DELETE FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?", (hold["user_id"], hold["id"]))
    db.execute("SAVEPOINT e2e_change")
    try:
        # what made it, and whether it is code-backed, for the checks made again (the hold's `after` list the same)
        _APPLY[row["kind"]](db, dict(hold, made_by=row["made_by"], by_code=code_backed(row)), json.loads(row["body"]),
                            now)
    except link.LinkError as e:
        db.execute("ROLLBACK TO e2e_change")
        db.execute("RELEASE e2e_change")
        return str(e)
    db.execute("RELEASE e2e_change")
    return None


def _cancelled(db, hold, now):
    """The owner cancelled a change's hold: its body is discarded; and a link's statements sent in after a restore are
    remembered (their sha256), so the same are refused if sent in again (upload)."""
    row = db.execute("SELECT kind, body FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?",
                     (hold["user_id"], hold["id"])).fetchone()
    if row is not None and row["kind"] == "upload":
        for stored in json.loads(row["body"])["statements"]:
            db.execute("INSERT INTO ade_e2e_refused (user_id, digest, created_at) VALUES (?,?,?) ON CONFLICT "
                       "(user_id, digest) DO NOTHING",
                       (hold["user_id"], hashlib.sha256(stored["payload"].encode("utf-8")).hexdigest(), now))
    db.execute("DELETE FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?", (hold["user_id"], hold["id"]))


def _discard(db, hold, now):
    """A change whose apply broke (holds.settle): its body is discarded, as a refusal's is (_apply), so the one change
    that waits is free for the next, a revocation among them."""
    db.execute("DELETE FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?", (hold["user_id"], hold["id"]))


def passkey_removed(db, user_id, passkey_id, now):
    """Inside the transaction that removes one of the account's passkeys (passkeys.remove_in): its credential no longer
    unlocks the keys (credentials_dropped); and a change waiting on its hold that the passkey made (signed or
    confirmed), or a link's statements sent in after a restore that it signed, ends now, failed, its body discarded, as
    a move it signed ends (holds.end). While encryption is on, a passkey is removed only by a revocation, whose own body
    waits no longer by then."""
    credentials_dropped(db, user_id, [passkey_id])
    row = _pending(db, user_id)
    if row is None:
        return
    statements = json.loads(row["body"]).get("statements", []) if row["kind"] == "upload" else []
    if any(s["passkey_id"] == passkey_id for s in statements):
        _end_pending(db, user_id, row, SIGNER_REMOVED, now)
    elif row["made_by"] == passkey_id:
        _end_pending(db, user_id, row, MADE_BY_REMOVED, now)


def removed_by(db, user_id, change, body):
    """R(C), what a change removes: the passkeys a revocation names, and, when it really turns high security on (it is
    off as the change is judged), every one but the hardware keys, which it leaves out of the new master key; a flag
    that repeats high security already on removes none. Another change removes no passkey."""
    if change != "revoke":
        return set()
    gone = set(body["remove"])
    if body["high_security"] and not passkeys._high_security(db, user_id):
        gone |= {r["id"] for r in db.execute("SELECT id FROM passkeys WHERE user_id = ? AND tier <> ?",
                                             (user_id, passkeys.HARDWARE))}
    return gone


def code_backed(row):
    """Whether a waiting change (its ade_e2e_pending row) is code-backed: made by the recovery code (made_by recovery),
    or with the recovery code's proof (by_code)."""
    return row is not None and (row["made_by"] == RECOVERY or bool(row["by_code"]))


def replaces_code(change, body):
    """Whether a change replaces the recovery code: a revocation that makes a new one (an upgrade that keeps the code
    in force makes a new key from it, and replaces no code), the fold that makes a new one (a turn-on again with
    upgrade, which keeps the code as the upgrade does), or a turn-on that starts again (replace), which forgets it. The
    page says so with the change that waits; it is no part of who may cancel it, since the code in force cancels any
    change (cancellers)."""
    fold = change == "turn-on" and bool(body.get("upgrade"))
    return ((change == "revoke" or fold) and body.get("recovery") is not None and not body.get("kept")) or \
        (change == "turn-on" and bool(body.get("replace")))


def cancellers(db, user_id, row):
    """Who may cancel the change that waits (`row`, its ade_e2e_pending row, or None for one whose body is gone), as
    cancelling it (_guard), a change placed in its place (_in_place_of) and the page's view (account_view) all take it:
    (the ids of the account's passkeys whose confirmation cancels it, in the account's order; whether the recovery
    code's proof does). The recovery code is the root:
    (i) The recovery code in force cancels any change, a code-backed one and one that replaces the code included: while
        the change waits it has replaced nothing, so that is the account's recovery key now.
    (ii) If the change is not code-backed (code_backed), a passkey it does not remove (R, removed_by).
    (iii) If it is not code-backed, was signed by a passkey, and leaves no passkey besides its signer, a passkey in R too:
        so neither of two who each hold one passkey can make a change the other cannot stop. The code does not enter
        this test.
    Only passkeys that could cancel under high security's rule count, here and in R and in "no passkey besides its
    signer": while it is on, the hardware keys alone. (iv) Never the password, never the sign-in alone."""
    change, body, made_by = (row["kind"], json.loads(row["body"]), row["made_by"]) if row is not None else ("", {}, None)
    high = passkeys._high_security(db, user_id)
    keys = [k["id"] for k in db.execute("SELECT id, tier FROM passkeys WHERE user_id = ? ORDER BY created_at, id",
                                        (user_id,)).fetchall() if not high or k["tier"] == passkeys.HARDWARE]
    by_code = db.execute("SELECT 1 FROM ade_e2e_recovery WHERE user_id = ?", (user_id,)).fetchone() is not None
    if code_backed(row):
        return [], by_code
    gone = removed_by(db, user_id, change, body) & set(keys)
    signed = made_by is not None and made_by != RECOVERY
    alone = signed and all(k in gone for k in keys if k != made_by)
    return [k for k in keys if alone or k not in gone], by_code


def _guard(db, hold, given, now):
    """Inside the transaction that cancels a change's hold (holds.cancel), before anything is changed: the owner's
    proof, used up, as `given` carries it, from one cancellers says may cancel it: the recovery code's proof for
    purpose cancel, or a confirmation a passkey made for TOUCH_CANCEL (never the password's; a hardware key's while high
    security is on). The page's sign-in alone, which whoever holds a stolen device has, cancels nothing:
    holds.HoldError(403) otherwise, the hold left as it was, and the confirmation or proof unused."""
    may, by_code = cancellers(db, hold["user_id"], _pending_of(db, hold))
    try:
        if given.get("recovery_proof") is not None:
            if not by_code:
                raise link.LinkError(409, NO_RECOVERY)
            use_proof(db, hold["user_id"], given["recovery_proof"], "cancel", now)
        else:
            passkeys.use_passkey_confirmation(db, hold["user_id"], given.get("confirmation"), now, CANCEL_TAKES,
                                              only_by=may, not_by_words=CANCEL_REMOVED, purpose=TOUCH_CANCEL)
    except (link.LinkError, passkeys.PasskeyError) as e:
        raise holds.HoldError(e.status, str(e)) from None


def _pending_of(db, hold):
    return db.execute("SELECT * FROM ade_e2e_pending WHERE user_id = ? AND hold_id = ?",
                      (hold["user_id"], hold["id"])).fetchone()


def _notice(db, row, at_once):
    """The kind of a change's notice (push.words): one in effect at once, or one that waits, which says, made with the
    recovery code's proof, that only the code cancels it (cancellers)."""
    if at_once:
        return "held-e2e-now"
    pending = db.execute("SELECT by_code FROM ade_e2e_pending WHERE hold_id = ?", (row["id"],)).fetchone()
    return "held-e2e-code" if pending is not None and pending["by_code"] else "held-e2e"


holds.register(HOLD, _apply, _cancelled, setting=HOLD_SETTING, default=HOLD_DEFAULT, guard=_guard, released=_discard,
               notice=_notice)
holds.register(HOLD_RECOVERY, _apply, _cancelled, setting=HOLD_RECOVERY_SETTING, default=HOLD_RECOVERY_DEFAULT,
               guard=_guard, released=_discard, notice=_notice)
holds.register(HOLD_LONG, _apply, _cancelled, setting=HOLD_RECOVERY_SETTING, default=HOLD_RECOVERY_DEFAULT,
               guard=_guard, released=_discard, notice=_notice)


# ---- sealing in place -----------------------------------------------------------------------------------------------

def _clear_events(db, user_id):
    """The events links sent the owner in the clear on the paths encryption covers: (rows a slot can be made for,
    how many cannot, their client_id not 32 hex)."""
    names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM ade_links WHERE user_id = ?", (user_id,))}
    rows, left = [], 0
    for r in db.execute("SELECT seq, link_id, client_id, kind, to_name, ref, text, mirror, session_id, said_at FROM "
                        "ade_link_events WHERE user_id = ? AND link_id <> ? AND kind IN (%s) AND substr(text, 1, 5) "
                        "<> ?" % ",".join("?" * len(link.KINDS)), (user_id, link.OWNER, *link.KINDS, e2e.PREFIX)):
        if r["link_id"] not in names or r["to_name"] not in (link.OWNER, names[r["link_id"]]):
            continue                        # to all or to another session: those sessions read it
        if link.fixed_text(r["kind"], r["client_id"], r["text"], r["mirror"], r["session_id"], r["said_at"]):
            continue
        if not link.SEALED_CLIENT_ID.fullmatch(r["client_id"]):
            left += 1
            continue
        rows.append(r)
    return rows, left


def _clear_rows(db, user_id):
    rows = db.execute("SELECT * FROM ade_link_history WHERE user_id = ? AND sealed IS NULL", (user_id,)).fetchall()
    return [r for r in rows if r["title"] or r["project"] or r["branch"] or r["repositories"]]


def _clear_talks(db, user_id):
    out = []
    for t in db.execute("SELECT * FROM ade_link_history_talks WHERE user_id = ?", (user_id,)).fetchall():
        messages = json.loads(t["messages"])
        if any(not e2e.sealed(m.get("text")) for m in messages):
            out.append((t, messages))
    return out


# an envelope of form 1, by its written form: its first decoded byte 0x01 makes its first base64url character A and its
# second one of Q to f (the byte's low two bits, 01, then four of the next byte's)
FORM1_GLOB = "e2e1.A[Q-Za-f]*"
FORM1_SECOND = frozenset("QRSTUVWXYZabcdef")


def _form1(value):
    """Whether `value` is written as an envelope of form 1 (FORM1_GLOB)."""
    return isinstance(value, str) and value.startswith("e2e1.A") and len(value) > 6 and value[6] in FORM1_SECOND


def classical_texts(db, user_id):
    """The texts the account still stores sealed to a classical key (form 1), by where they are: its events, its
    History rows (listed or not), and its conversations holding one ({events, rows, talks, total}). The connector's
    proposals hold none: an app's words are refused where they begin e2e1. (link_commands.check_text), and a question
    is read from its event, never copied."""
    events = db.execute("SELECT COUNT(*) FROM ade_link_events WHERE user_id = ? AND text GLOB ?",
                        (user_id, FORM1_GLOB)).fetchone()[0]
    rows = db.execute("SELECT COUNT(*) FROM ade_link_history WHERE user_id = ? AND sealed GLOB ?",
                      (user_id, FORM1_GLOB)).fetchone()[0]
    talks = sum(1 for t in db.execute("SELECT messages FROM ade_link_history_talks WHERE user_id = ?", (user_id,))
                if any(_form1(m.get("text")) for m in json.loads(t["messages"]) if isinstance(m, dict)))
    return {"events": events, "rows": rows, "talks": talks, "total": events + rows + talks}


def to_seal(db, user_id):
    """How much sealing in place would seal now: {events, left: events that cannot be (their client_id not 32 hex),
    rows, talks}. `talks` are the conversations holding a message in the clear; a post-quantum turn-on deletes those of
    them that hold a message sealed already, which it cannot seal whole (seal_in_place)."""
    events, left = _clear_events(db, user_id)
    return {"events": len(events), "left": left, "rows": len(_clear_rows(db, user_id)),
            "talks": len(_clear_talks(db, user_id))}


def _row_plain(r):
    """What a History row seals, as a link seals it (ade_link.py): its project alone for a withheld row; else its
    title, project, branch and repositories (a list), the repositories dropped from the end until the row fits."""
    if r["withheld"]:
        return {"project": r["project"]}
    repositories = json.loads(r["repositories"]) if r["repositories"] else []
    value = {"title": r["title"], "project": r["project"], "branch": r["branch"], "repositories": repositories}
    while value["repositories"] and len(e2e.plaintext(value)) > e2e.HISTORY_PLAIN:
        value["repositories"] = value["repositories"][:-1]
    return value


def seal_in_place(db, user_id, public_key_raw, now):
    """Inside a turn-on's transaction, as its hold ends: every text links sent in the clear on the paths encryption
    covers sealed where it is, to the content key `public_key_raw`, with the slot it would have had (ade/e2e.py):
    events to the owner (but the two fixed texts, which stay in the clear, and those whose client_id is not 32 hex,
    counted), History rows and conversations; each event sealed so is marked (sealed_in_place), which its view serves.
    To a post-quantum key a conversation is sealed whole, under a key of its own: one that also holds a message sealed
    already (in form 1, to an earlier key) is never a mix of forms, so it is deleted, its clear words with it, and
    counted as dropped; its link sends it again under the new key if it still runs and lists that session. Only a
    client that sends a conversation half sealed makes one, which put_talk takes while encryption is off on a
    classical account. A heartbeat's report (link.heartbeat) holding its lists in the clear keeps its numbers alone:
    its lists are dropped, not sealed, as its link sends them sealed with its next heartbeat if it still runs. The
    account's head is recorded first, as _drop_notes keeps it. {events, left, rows, talks, dropped and reports where
    any was}."""
    head = link._head(db, user_id)
    db.execute("INSERT INTO ade_link_heads (user_id, seq) VALUES (?, ?) ON CONFLICT (user_id) DO UPDATE SET "
               "seq = MAX(seq, excluded.seq)", (user_id, head))
    events, left = _clear_events(db, user_id)
    for r in events:
        envelope = e2e.seal(public_key_raw, e2e.event_slot(user_id, r["link_id"], r["kind"], r["mirror"], r["ref"],
                                                           r["client_id"]),
                            {"text": r["text"], "session": r["session_id"], "said_at": r["said_at"]})
        # marked: a question's session keeps the digest of the words it asked in the clear, not of this envelope,
        # so its answer carries that digest (link_commands checks it no further, the server holding no words now)
        db.execute("UPDATE ade_link_events SET text = ?, sealed_in_place = 1 WHERE seq = ?", (envelope, r["seq"]))
    rows = _clear_rows(db, user_id)
    for r in rows:
        envelope = e2e.seal(public_key_raw, e2e.history_slot(user_id, r["link_id"], r["session_id"], r["withheld"]),
                            _row_plain(r))
        db.execute("UPDATE ade_link_history SET sealed = ?, title = '', project = NULL, branch = NULL, repositories = "
                   "NULL WHERE link_id = ? AND session_id = ?", (envelope, r["link_id"], r["session_id"]))
    talks = _clear_talks(db, user_id)
    dropped = 0
    for t, messages in talks:
        count = len(messages)
        if len(public_key_raw) == e2e.HYBRID_KEY:
            # a post-quantum key's conversation is sealed whole, under a key of its own (form 3 beside a form-2 key):
            # one that holds a message sealed already (to an earlier key, in form 1) cannot be, and is never a mix,
            # so it goes, and its link sends it again under the new key (its digest names the key)
            if any(e2e.sealed(m.get("text")) for m in messages):
                db.execute("DELETE FROM ade_link_history_talks WHERE link_id = ? AND session_id = ?",
                           (t["link_id"], t["session_id"]))
                dropped += 1
                continue
            key, key_id, messages = e2e.seal_talk(public_key_raw, user_id, t["link_id"], t["session_id"],
                                                  t["earlier"], messages)
            db.execute("UPDATE ade_link_history_talks SET messages = ?, key = ?, key_id = ? WHERE link_id = ? AND "
                       "session_id = ?", (json.dumps(messages), key, key_id, t["link_id"], t["session_id"]))
            continue
        for i, m in enumerate(messages):
            if not e2e.sealed(m.get("text")):
                m["text"] = e2e.seal(public_key_raw, e2e.talk_slot(user_id, t["link_id"], t["session_id"], i, count,
                                                                   t["earlier"], m["who"], m.get("at"), m.get("ms")),
                                     {"text": m["text"]})
        db.execute("UPDATE ade_link_history_talks SET messages = ? WHERE link_id = ? AND session_id = ?",
                   (json.dumps(messages), t["link_id"], t["session_id"]))
    reports = 0
    for r in db.execute("SELECT id, reported FROM ade_links WHERE user_id = ? AND reported IS NOT NULL",
                        (user_id,)).fetchall():
        held = json.loads(r["reported"])
        if "tasks" in held or "background" in held:
            kept = {k: v for k, v in held.items() if k not in ("tasks", "background")}
            db.execute("UPDATE ade_links SET reported = ? WHERE id = ?", (link._report_words_json(kept), r["id"]))
            reports += 1
    out = {"events": len(events), "left": left, "rows": len(rows), "talks": len(talks) - dropped}
    if dropped:
        out["dropped"] = dropped
    if reports:
        out["reports"] = reports
    return out


# ---- re-sealing what is stored classical --------------------------------------------
#
# Only the unlocked page can seal again: the server holds no key. The page reads what a post-quantum account stores
# sealed to a classical key (form 1) a page at a time (reseal_page), opens each, seals it again with the same slot to
# the account's newest key (a conversation whole), and sends it back in batches (reseal). The
# server swaps a value only where what it stores is still what was read (its sha256), the new one is in form 2 under the
# newest key (a conversation: form 3 beside a form-2 key under it) and the row's slot fields are as they were. It cannot
# tell that the new value holds the same text, and sealing needs only the public key, so a batch takes the recovery
# code's proof, made for that batch alone (reseal_digest): a passkey or a sign-in cannot rewrite what is stored. It is
# not held: only the code makes it, and only the code would cancel it. Each swap is logged by its hashes alone
# (ade_e2e_reseal_log), the account's devices are told, and the database is compacted after.

RESEAL_TABLES = ("events", "rows", "talks")
RESEAL_PAGE = 100                   # items a page of GET /reseal holds at most
RESEAL_PAGE_BYTES = 512 * 1024      # and their values' bytes, past its first item
RESEAL_MAX = 200                    # items a batch of POST /reseal holds at most (and the body's own cap)
RESEAL_CHUNK = 200                  # rows each table is read in at once
RESEAL_NOT_PQ = "Only a post-quantum account's stored texts are sealed again: upgrade its keys first."
RESEAL_OFF = "End-to-end encryption is not on for this account: nothing is sealed again."
RESEAL_GONE = ("That stored text is not one to seal again: it was sealed again, sent again or deleted since it was "
               "read, or it is a question that may yet be answered, whose answer is signed over its text as stored. "
               "Read them again.")
RESEAL_CHANGED = "What is stored there changed since it was read: read the stored texts again."
RESEAL_WHERE = "A re-seal item's where is one GET /api/ade/e2e/reseal gave: {table, id, field?}."
_RESEAL_ID = re.compile(r"[0-9a-f]{32}:[A-Za-z0-9][A-Za-z0-9_-]{0,99}")     # a row's or talk's: LINK_ID:SESSION_ID
_RESEAL_AFTER = re.compile(r"0:([1-9][0-9]{0,15})|([12]):([0-9a-f]{32}:[A-Za-z0-9][A-Za-z0-9_-]{0,99})")


def _stored_sha256(value):
    """The sha256 (hex) of a stored value as GET /reseal serves it: a text's UTF-8, a conversation's RFC 8785 JSON. The
    page computes the same (storedDigest)."""
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else canonicalize(value)).hexdigest()


def reseal_digest(items):
    """What a re-seal batch's recovery proof is made for: the lowercase hex sha256 of the RFC 8785 JSON of [[where,
    old_sha256, the sha256 of new], ...], in the order sent, new's over its UTF-8 for a text and its RFC 8785 JSON for a
    conversation (the page's resealBinding)."""
    return hashlib.sha256(canonicalize([[i["where"], i["old_sha256"], _stored_sha256(i["new"])]
                                        for i in items])).hexdigest()


def _reseal_target(db, user_id):
    """Inside a re-seal: the key_id of the account's newest key, the one everything is sealed again to. 409 for a
    classical account (RESEAL_NOT_PQ) or one whose encryption is off (RESEAL_OFF)."""
    if not is_pq(db, user_id):
        raise link.LinkError(409, RESEAL_NOT_PQ)
    top = newest(db, user_id)
    if not is_key(top):
        raise link.LinkError(409, RESEAL_OFF)
    if len(_bytes(top["public_key"], "content key", least=0, most=e2e.HYBRID_KEY)) != e2e.HYBRID_KEY:
        raise link.LinkError(409, RESEAL_NOT_PQ)        # a classical key stated last: no post-quantum key to seal to
    return top["key_id"]


def _reseal_event_ok(db, row):
    """Whether an event sealed in form 1 is one to seal again: any, but a question its link sealed that may yet be
    answered, whose answer is signed over the envelope as stored (link_commands; its link keeps that digest): sealing
    it again would leave it unanswerable. That is one still open, and one an answer closed that its link has not taken,
    which opens again when the answer is withdrawn or expires (link.question_open); it goes once it is closed for good,
    by an answer taken or by the owner's note. One the server sealed in place is answered over its words, and goes."""
    if row["kind"] != "question" or row["sealed_in_place"]:
        return True
    if row["closed_by"] is None:
        return False
    closer = db.execute("SELECT kind, taken_at FROM ade_link_events WHERE seq = ?", (row["closed_by"],)).fetchone()
    return closer is None or closer["kind"] != "answer" or closer["taken_at"] is not None


def _reseal_events(db, user_id, after):
    """(seq, item) of each event sealed in form 1 to seal again (_reseal_event_ok), after seq `after`, by seq."""
    last = after or 0
    while True:
        chunk = db.execute("SELECT * FROM ade_link_events WHERE user_id = ? AND seq > ? AND text GLOB ? ORDER BY seq "
                           "LIMIT ?", (user_id, last, FORM1_GLOB, RESEAL_CHUNK)).fetchall()
        for r in chunk:
            last = r["seq"]
            if _reseal_event_ok(db, r):
                yield r["seq"], {"where": {"table": "events", "id": r["seq"], "field": "text"}, "value": r["text"],
                                 "slot": e2e.event_slot(user_id, r["link_id"], r["kind"], r["mirror"], r["ref"],
                                                        r["client_id"])}
        if len(chunk) < RESEAL_CHUNK:
            return


def _reseal_rows(db, user_id, after):
    """((link_id, session_id), item) of each History row, listed or not, sealed in form 1, after `after`, in that
    order."""
    last = after or ("", "")
    while True:
        chunk = db.execute("SELECT link_id, session_id, sealed, withheld FROM ade_link_history WHERE user_id = ? AND "
                           "(link_id, session_id) > (?, ?) AND sealed GLOB ? ORDER BY link_id, session_id LIMIT ?",
                           (user_id, *last, FORM1_GLOB, RESEAL_CHUNK)).fetchall()
        for r in chunk:
            last = (r["link_id"], r["session_id"])
            yield last, {"where": {"table": "rows", "id": f"{r['link_id']}:{r['session_id']}", "field": "sealed"},
                         "value": r["sealed"],
                         "slot": e2e.history_slot(user_id, r["link_id"], r["session_id"], r["withheld"])}
        if len(chunk) < RESEAL_CHUNK:
            return


def _talk_value(t, messages):
    """A conversation as GET /reseal serves it, and its old_sha256 is of: what its slots are made of, and its messages
    as stored."""
    return {"link_id": t["link_id"], "session_id": t["session_id"], "earlier": t["earlier"], "messages": messages}


def _reseal_talks(db, user_id, after):
    """((link_id, session_id), item) of each conversation holding a message sealed in form 1, after `after`, in that
    order: whole, with no slot (the page makes a conversation's slots, and its key's, from its value)."""
    last = after or ("", "")
    while True:
        chunk = db.execute("SELECT link_id, session_id, messages, earlier FROM ade_link_history_talks WHERE "
                           "user_id = ? AND (link_id, session_id) > (?, ?) ORDER BY link_id, session_id LIMIT ?",
                           (user_id, *last, RESEAL_CHUNK)).fetchall()
        for t in chunk:
            last = (t["link_id"], t["session_id"])
            messages = json.loads(t["messages"])
            if any(_form1(m.get("text")) for m in messages if isinstance(m, dict)):
                yield last, {"where": {"table": "talks", "id": f"{t['link_id']}:{t['session_id']}"},
                             "value": _talk_value(t, messages)}
        if len(chunk) < RESEAL_CHUNK:
            return


def _reseal_after(after):
    """Where a page of GET /reseal begins: (table, key) of the last item the page before gave, from its `next`; (0,
    None) for the first page. 422 for any other text."""
    if after is None or after == "":
        return 0, None
    m = _RESEAL_AFTER.fullmatch(after) if isinstance(after, str) else None
    if m is None:
        raise link.LinkError(422, "A re-seal page's after is the next the page before it gave.")
    if m.group(1) is not None:
        return 0, int(m.group(1))
    return int(m.group(2)), tuple(m.group(3).split(":", 1))


def _reseal_next(table, key):
    return f"0:{key}" if table == 0 else f"{table}:{key[0]}:{key[1]}"


def reseal_page(user_id, after=None, now=None):
    """GET /api/ade/e2e/reseal: the next page of what a post-quantum account stores sealed in form 1, after the cursor
    `after` (the page before's next): {items, next}, next null on the last. Each item is {where, value, slot} for a
    text (an event's, by seq, then a History row's, listed or not, by link and session), its slot built from its row,
    which the page seals again with; and {where, value} for a conversation holding one (last): value {link_id,
    session_id, earlier, messages}, from which the page makes its slots. At most RESEAL_PAGE items and RESEAL_PAGE_BYTES
    of values a page (its first whatever its size). A question its link sealed that may yet be answered is left out
    (_reseal_event_ok). 409 for a classical account or one whose encryption is off."""
    now = _now(now)
    table, key = _reseal_after(after)
    holds.settle(user_id, now)
    with store.connect() as db:
        _reseal_target(db, user_id)
        readers = (lambda k: _reseal_events(db, user_id, k), lambda k: _reseal_rows(db, user_id, k),
                   lambda k: _reseal_talks(db, user_id, k))
        items, size, last = [], 0, None
        for t in range(table, len(RESEAL_TABLES)):
            for k, item in readers[t](key if t == table else None):
                n = len(item["value"]) if isinstance(item["value"], str) else len(canonicalize(item["value"]))
                if items and (len(items) >= RESEAL_PAGE or size + n > RESEAL_PAGE_BYTES):
                    return {"items": items, "next": _reseal_next(*last)}
                items.append(item)
                size, last = size + n, (t, k)
    return {"items": items, "next": None}


def _reseal_item(value):
    """One item of a re-seal batch, read for its form: (table, key, the item). Its where is one GET gave ({table:
    events, id: seq, field: text}, {table: rows, id: LINK_ID:SESSION_ID, field: sealed} or {table: talks, id:
    LINK_ID:SESSION_ID}), its old_sha256 64 lowercase hex, and its new a text, or a conversation's {messages, key,
    key_id}. 422 otherwise."""
    item = _object(value, "re-seal item")
    if set(item) != {"where", "old_sha256", "new"}:
        raise link.LinkError(422, "A re-seal item is {where, old_sha256, new}.")
    where = item["where"]
    table = where.get("table") if isinstance(where, dict) else None
    if table == "events":
        ok = set(where) == {"table", "id", "field"} and where["field"] == "text" and type(where["id"]) is int \
            and 0 < where["id"] < 2 ** 53
        key = where["id"] if ok else None
    elif table in ("rows", "talks"):
        ok = set(where) == ({"table", "id", "field"} if table == "rows" else {"table", "id"}) \
            and (table == "talks" or where["field"] == "sealed") and isinstance(where["id"], str) \
            and bool(_RESEAL_ID.fullmatch(where["id"]))
        key = tuple(where["id"].split(":", 1)) if ok else None
    else:
        ok = False
    if not ok:
        raise link.LinkError(422, RESEAL_WHERE)
    if not isinstance(item["old_sha256"], str) or not _HEX64.fullmatch(item["old_sha256"]):
        raise link.LinkError(422, "A re-seal item's old_sha256 is the sha256 of the value as read, 64 lowercase hex.")
    if table == "talks" and not (isinstance(item["new"], dict) and set(item["new"]) == {"messages", "key", "key_id"}):
        raise link.LinkError(422, "A conversation sealed again is {messages, key, key_id}.")
    if table != "talks" and not isinstance(item["new"], str):
        raise link.LinkError(422, "A text sealed again is an envelope's text.")
    return table, key, item


def _held(stored, cap):
    """The plaintext bytes a value sealed again may hold where `stored` was: the path's cap, or as many as the stored
    form-1 envelope holds where that is more. One the server sealed in place is held to no cap (4,000 characters of an
    event's text in the clear can come to 24,000 bytes as JSON), and must not be left classical for good."""
    if not _form1(stored):
        return cap
    return max(cap, len(e2e._raw(stored)) - e2e.OVERHEADS[e2e.FORM_X25519])


def _message_held(stored):
    """The plaintext bytes a conversation's message sealed again may hold where `stored` was: for one sealed, as _held
    has it (TALK_PLAIN, or what its form-1 envelope holds where that is more); for one in the clear, which a
    conversation holds where a client sent it half sealed and a turn-on did not seal in place, and which is sealed for
    the first time as the conversation is sealed again, the cap, or its words as they are sealed ({"text"} as RFC 8785
    bytes) where that is more: put_talk takes 4,100 characters, which can come to more than 16,400 bytes."""
    if e2e.sealed(stored):
        return _held(stored, e2e.TALK_PLAIN)
    return max(e2e.TALK_PLAIN, len(e2e.plaintext({"text": stored})))


def _talk_size(messages):
    """A stored conversation's size, each message as it comes to sealed: a sealed one as it decodes, one in the clear
    (_message_held) as form 3 holds its words, their {"text"} as RFC 8785 bytes and the form's 45 bytes. Sealed again, a
    conversation may come to that and its key's envelope where that is more than link.TALK_SEALED_TOTAL: sealing in
    place seals what was sent in the clear past that total (20 messages of 4,000 four-byte characters come to 321,760
    bytes in form 1), and a conversation near it grows by its key's 1,197 bytes while each of up to 37 messages shrinks
    by 32 alone; held to the total, either would stay classical for good. So the same words sealed again always fit: a
    message in form 1 is 32 bytes shorter in form 3, and one in the clear comes to what is counted for it."""
    return sum(len(e2e._raw(m["text"])) if e2e.sealed(m.get("text"))
               else e2e.OVERHEADS[e2e.FORM_TALK] + len(e2e.plaintext({"text": m.get("text")}))
               for m in messages if isinstance(m, dict))


def _sealed_again(value, most, forms, key_id, what):
    """The Shape of a value sealed again (e2e.read): one of `forms`, holding at most `most` plaintext bytes, naming
    `key_id`; 422 naming `what` otherwise."""
    try:
        shape = e2e.read(value, most, forms)
    except e2e.NotAnEnvelope as e:
        raise link.LinkError(422, f"{what} is not sealed again in its form: {e}") from None
    if shape.key_id != key_id:
        raise link.LinkError(422, f"{what} is not sealed again under the key it must be.")
    return shape


def _reseal_swap(db, user_id, table, key, item, target):
    """Inside a re-seal's transaction: one item checked against what is stored (404 where it is not a stored text to
    seal again, 409 where it changed since it was read) and its new value against its form (422). (The update that
    swaps it, the sha256 of its new value.)"""
    gone, changed = link.LinkError(404, RESEAL_GONE), link.LinkError(409, RESEAL_CHANGED)
    new = item["new"]
    if table == "events":
        row = db.execute("SELECT * FROM ade_link_events WHERE user_id = ? AND seq = ?", (user_id, key)).fetchone()
        if row is None or not _form1(row["text"]) or not _reseal_event_ok(db, row):
            raise gone
        if _stored_sha256(row["text"]) != item["old_sha256"]:
            raise changed
        _sealed_again(new, _held(row["text"], e2e.EVENT_PLAIN), (e2e.FORM_HYBRID,), target, "An event")
        return ("UPDATE ade_link_events SET text = ? WHERE user_id = ? AND seq = ?", (new, user_id, key)), \
            _stored_sha256(new)
    if table == "rows":
        row = db.execute("SELECT sealed, withheld FROM ade_link_history WHERE user_id = ? AND link_id = ? AND "
                         "session_id = ?", (user_id, *key)).fetchone()
        if row is None or not _form1(row["sealed"]):
            raise gone
        if _stored_sha256(row["sealed"]) != item["old_sha256"]:
            raise changed
        _sealed_again(new, _held(row["sealed"], e2e.HISTORY_WITHHELD_PLAIN if row["withheld"] else e2e.HISTORY_PLAIN),
                      (e2e.FORM_HYBRID,), target, "A History row")
        return ("UPDATE ade_link_history SET sealed = ? WHERE user_id = ? AND link_id = ? AND session_id = ?",
                (new, user_id, *key)), _stored_sha256(new)
    t = db.execute("SELECT link_id, session_id, messages, earlier FROM ade_link_history_talks WHERE user_id = ? AND "
                   "link_id = ? AND session_id = ?", (user_id, *key)).fetchone()
    stored = json.loads(t["messages"]) if t is not None else []
    if t is None or not any(_form1(m.get("text")) for m in stored if isinstance(m, dict)):
        raise gone
    if _stored_sha256(_talk_value(t, stored)) != item["old_sha256"]:
        raise changed
    try:
        shape = e2e.read_talk_key(new["key"])
    except e2e.NotAnEnvelope as e:
        raise link.LinkError(422, f"A conversation's key is not sealed again in its form: {e}") from None
    if shape.key_id != target:
        raise link.LinkError(422, "A conversation's key is not sealed again under the key it must be.")
    talk_key_id = _hex32(new["key_id"], "conversation's key_id")
    messages = new["messages"]
    if not isinstance(messages, list) or len(messages) != len(stored):
        raise link.LinkError(422, "A conversation is sealed again whole: each of its messages, in their order.")
    total, out = e2e.TALK_KEY_SEALED, []
    for before, after in zip(stored, messages):
        # its slot's fields as they were: its place (the list's order and length), whose, and its times
        if not isinstance(after, dict) or set(after) != set(before) \
                or any(after[f] != before[f] for f in before if f != "text"):
            raise link.LinkError(422, "A conversation sealed again keeps each message's place, whose it is and its "
                                      "times as they were.")
        total += _sealed_again(after["text"], _message_held(before.get("text")), (e2e.FORM_TALK,), talk_key_id,
                               "A message").length
        out.append(dict(before, text=after["text"]))
    most = max(link.TALK_SEALED_TOTAL, _talk_size(stored) + e2e.TALK_KEY_SEALED)
    if total > most:
        raise link.LinkError(422, f"A conversation's envelopes come to at most {most} bytes: {link.TALK_SEALED_TOTAL}, "
                                  "or what it holds now and its key's where that is more.")
    return ("UPDATE ade_link_history_talks SET messages = ?, key = ?, key_id = ? WHERE user_id = ? AND link_id = ? AND "
            "session_id = ?", (json.dumps(out), new["key"], talk_key_id, user_id, *key)), _stored_sha256(new)


def reseal(user_id, data, now=None):
    """POST /api/ade/e2e/reseal {items: [{where, old_sha256, new}], recovery_proof, kem}: a batch of stored texts
    sealed again by the page (reseal_page), all or none. The recovery code's proof (purpose reseal) was made for this
    batch's digest (reseal_digest) and is used up; each item names a stored text to seal again (404 otherwise) whose
    sha256 is still old_sha256 (409 otherwise), and its new value is in form 2 under the account's newest key, a
    conversation form 3 under a key of its own in form 2 under it, each message's slot fields as they were (422
    otherwise). Each swap is logged by its hashes (ade_e2e_reseal_log), the database is due for compaction, and the
    account's devices are told. 409 for a classical account or one whose encryption is off. {swapped}."""
    now = _now(now)
    body = _object(data, "re-seal")
    items = body.get("items")
    if not isinstance(items, list) or not items or len(items) > RESEAL_MAX:
        raise link.LinkError(422, f"A re-seal batch is a list of 1 to {RESEAL_MAX} items, each {{where, old_sha256, "
                                  "new}.")
    parsed = [_reseal_item(i) for i in items]
    if len({(t, k) for t, k, _ in parsed}) != len(parsed):
        raise link.LinkError(422, "A re-seal batch names each stored text once.")
    try:
        digest = reseal_digest(items)
    except (TypeError, ValueError):
        raise link.LinkError(422, "A re-seal batch is JSON that RFC 8785 writes.") from None
    holds.settle(user_id, now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        target = _reseal_target(db, user_id)
        _era(db, user_id, body)
        use_proof(db, user_id, body.get("recovery_proof"), RESEAL, now, digest=digest)
        swaps = [_reseal_swap(db, user_id, t, k, item, target) for t, k, item in parsed]
        for ((sql, params), new_sha256), (_, _, item) in zip(swaps, parsed):
            db.execute(sql, params)
            db.execute("INSERT INTO ade_e2e_reseal_log (user_id, at, place, old_sha256, new_sha256) VALUES "
                       "(?,?,?,?,?)", (user_id, now, canonicalize(item["where"]).decode("utf-8"), item["old_sha256"],
                                       new_sha256))
        # what was stored before is written over (secure_delete): its pages leave the file and its log once compacted
        _set_state(db, user_id, compaction_due=secrets.token_hex(16))
    compact_soon()
    push.notice(user_id, push.words("e2e-resealed", count=len(parsed)), now)
    return {"swapped": len(parsed)}


_COMPACTING = threading.Lock()      # one compaction at a time in this process: after a sealing, or the sweep's


def _wal_size(path):
    try:
        return os.path.getsize(path + "-wal")
    except FileNotFoundError:
        return 0


def compact(tries=None, wait=None, path=None):
    """After sealing in place, once its transaction has committed: the database (`path`, config.db_path by default)
    rebuilt (VACUUM), so no page of the old texts stays in its free space, and its write-ahead log checkpointed and
    truncated, which the checkpoint says it did (busy 0) and the log's file shows (empty). A reader still holding an
    older state keeps the checkpoint busy: each try waits for it a tenth of a second (COMPACT_BUSY_MS), not the
    connection's ten, and it is tried again, `tries` times `wait` seconds apart. Whether it completed."""
    tries = COMPACT_TRIES if tries is None else tries
    wait = COMPACT_WAIT if wait is None else wait
    path = path or config.db_path()
    try:
        with store.connect(path) as db:
            db.execute("VACUUM")
            db.execute(f"PRAGMA busy_timeout = {COMPACT_BUSY_MS}")
            for _ in range(tries):
                if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0 and _wal_size(path) == 0:
                    return True
                time.sleep(wait)
    except sqlite3.Error as e:
        print(f"e2e: the database could not be compacted after sealing in place: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False
    print("e2e: the database could not be compacted after sealing in place: a reader kept its log; tried again later",
          file=sys.stderr)
    return False


def compact_if_due(tries=None, wait=None, path=None, now=None):
    """Compact the database (compact) while what an account's turning on wrote over (sealing in place, the decided
    proposals' words) may still be in it (ade_e2e_state's compaction_due), and on success clear each flag that was there
    before it began: one set meanwhile stays, for the next. An upgrade's second compaction is due from its time on
    (compact_again_at, by `now`'s clock). One at a time (_COMPACTING): a second waits for the first, then finds what is
    left. None when none is due; else whether it completed. Run in a thread of its own once that has committed
    (compact_soon), and by the sweep, at the app's start and every minute after (start); never in a request."""
    path = path or config.db_path()
    with _COMPACTING:
        with store.connect(path) as db:
            # an upgrade's second compaction, a day after it took effect (COMPACT_AGAIN): due now as any other is
            db.execute("UPDATE ade_e2e_state SET compaction_due = lower(hex(randomblob(16))), compact_again_at = NULL "
                       "WHERE compact_again_at IS NOT NULL AND compact_again_at <= ?", (_now(now),))
            due = [(r["user_id"], r["compaction_due"]) for r in db.execute(
                "SELECT user_id, compaction_due FROM ade_e2e_state WHERE compaction_due IS NOT NULL").fetchall()]
        if not due:
            return None
        if not compact(tries, wait, path):
            return False
        with store.connect(path) as db:
            for user_id, token in due:
                db.execute("UPDATE ade_e2e_state SET compaction_due = NULL WHERE user_id = ? AND compaction_due = ?",
                           (user_id, token))
        try:
            with store.connect(path) as db:     # the flags' own page, written after the rebuild, out of the log too
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass                                # it holds no sealed text: the next checkpoint takes it
        return True


def compact_soon():
    """Once a turn-on that wrote texts over has committed (_compaction_due leaves this for after its transaction):
    compact_if_due in a thread of its own, on the database it wrote, so the request that settled the hold waits for
    none of it. A failure is said, and the sweep (start) tries again. The thread."""
    path = config.db_path()

    def run():
        try:
            compact_if_due(path=path)
        except Exception as e:  # noqa: BLE001  a thread of its own: the sweep takes what it leaves
            print(f"e2e: a compaction after sealing in place failed: {type(e).__name__}: {e}", file=sys.stderr)
    worker = threading.Thread(target=run, name="ade-e2e-compact", daemon=True)
    worker.start()
    return worker


def start(every=COMPACT_EVERY):
    """Run compact_if_due in a thread of this process, at once and then every `every` seconds, while it serves (the
    app's lifespan; the tests, which never enter it, call it themselves): a compaction a reader kept from completing,
    one that failed, or one the process stopped before, is done then. It asks first whether this server does ML-KEM
    (e2e.ready), so that answer is the one found at start."""
    e2e.ready()

    def loop():
        while True:
            try:
                compact_if_due()
            except Exception as e:  # noqa: BLE001  the sweep never takes the app down
                print(f"e2e: a compaction sweep failed: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(every)
    worker = threading.Thread(target=loop, name="ade-e2e", daemon=True)
    worker.start()
    return worker


# ---- what is served -------------------------------------------------------------------------------------------------

def _statement_view(r, signed=False):
    view = {"seq": r["seq"], "id": r["id"], "purpose": r["purpose"], "key_id": r["key_id"],
            "public_key": r["public_key"], "retires": r["retires"], "payload": r["payload"],
            "digest": hashlib.sha256(r["payload"].encode("utf-8")).hexdigest(), "passkey_id": r["passkey_id"],
            "created_at": r["created_at"], "retired_at": r["retired_at"]}
    if signed:
        view["credential"] = json.loads(r["credential"]) if r["credential"] else None
    return view


def _served_body(body):
    """A waiting change's body as GET serves it: whole, but the assertion (`credential`) of each statement in it (a
    change's `statement`, the `statements` a link sent in), which the statements served withhold too (_statement_view).
    A statement placed with a change is kept with it, to be stored as its hold ends; nothing the page checks before
    then is in the assertion. A change's own `credential`, an enrolment's, is the passkey's record and
    no assertion: it stays."""
    if not isinstance(body, dict):
        return body
    served = dict(body)
    if isinstance(body.get("statement"), dict):
        served["statement"] = _unsigned(body["statement"])
    if isinstance(body.get("statements"), list):
        served["statements"] = [_unsigned(s) if isinstance(s, dict) else s for s in body["statements"]]
    return served


def _unsigned(statement):
    return {k: v for k, v in statement.items() if k != "credential"}


def account_view(user_id, now=None):
    """What the page unlocks the keys with and changes them from, once holds whose time has come are settled: whether
    encryption is on, the statements (but their signatures), the current master key's id, the credentials, the recovery
    key, the grants, the chain, the roster, the content keys, the account's passkeys and which can enrol, each live
    link's key, its last "required" and its pins (null until it reports them), the change that waits, who may cancel it
    (cancellers), what made it, whether it is code-backed, what it removes (removed_by), whether it replaces the
    recovery code, and its body; how much turning on would seal in place, whether turning on again makes a new master key (said only while encryption is
    off), whether the database may still hold what was sealed in place, what the last sealing in place sealed, and the
    holds' lengths. Every table is read in one read transaction."""
    now = _now(now)
    holds.settle(user_id, now)
    with store.connect() as db:
        # every table as of one moment: a change committed meanwhile (a revocation's new master key, its grants and its
        # chain) is read whole or not at all, never as a master key that does not lead back to its anchor
        db.execute("BEGIN")
        statements = db.execute("SELECT * FROM ade_e2e_statements WHERE user_id = ? ORDER BY seq",
                                (user_id,)).fetchall()
        mk = current(db, user_id)
        creds = db.execute("SELECT * FROM ade_e2e_credentials WHERE user_id = ? ORDER BY created_at, passkey_id",
                           (user_id,)).fetchall()
        enrolled = {c["passkey_id"] for c in creds}
        recovery = db.execute("SELECT * FROM ade_e2e_recovery WHERE user_id = ?", (user_id,)).fetchone()
        grants = db.execute("SELECT * FROM ade_e2e_grants WHERE user_id = ? ORDER BY mk_id, recipient",
                            (user_id,)).fetchall()
        chain = db.execute("SELECT * FROM ade_e2e_chain WHERE user_id = ? ORDER BY created_at, mk_id",
                           (user_id,)).fetchall()
        roster = db.execute("SELECT * FROM ade_e2e_roster WHERE user_id = ?", (user_id,)).fetchone()
        content = db.execute("SELECT * FROM ade_e2e_content_keys WHERE user_id = ? ORDER BY created_at, key_id",
                             (user_id,)).fetchall()
        high = passkeys._high_security(db, user_id)
        keys = db.execute("SELECT id, credential_id, name, tier, prf, created_at, maker, tier_why, backup_eligible, "
                          "backed_up, attachment FROM passkeys WHERE user_id = ? ORDER BY created_at, id",
                          (user_id,)).fetchall()
        links = db.execute("SELECT l.id, l.name, e.key_id, e.last_required_at, e.pins, e.seals FROM ade_links l LEFT "
                           "JOIN ade_link_e2e e ON e.link_id = l.id WHERE l.user_id = ? AND l.revoked_at IS NULL "
                           "ORDER BY l.created_at, l.name", (user_id,)).fetchall()
        pending = _pending(db, user_id)
        waiting = holds.get(db, pending["hold_id"]) if pending is not None else None
        cancel_by, cancel_by_code = cancellers(db, user_id, pending) if pending is not None else ([], False)
        gone = removed_by(db, user_id, pending["kind"], json.loads(pending["body"])) if pending is not None else set()
        top = statements[-1] if statements else None
        sealing = to_seal(db, user_id) if not is_key(top) else None
        state = _state(db, user_id)
        # a new master key is due only for turning encryption on again: while it is on, a revocation makes one
        due = rekey_due(db, user_id) if mk is not None and not is_key(top) else False
        pq = bool(state is not None and state["pq"])
        classical = classical_texts(db, user_id) if pq else None
    return {
        "on": is_key(top), "newest": said(top), "statements": [_statement_view(r) for r in statements],
        "mk_id": mk,
        # whether the account's keys are post-quantum, and whether this server can make, check and seal to such keys
        # (its ML-KEM, found at start): without it the page offers no post-quantum turn-on and no upgrade, and a
        # post-quantum account takes no change at all (_era)
        "pq": pq, "mlkem": e2e.ready(),
        "credentials": [{"passkey_id": c["passkey_id"], "credential_id": c["credential_id"], "salt": c["salt"],
                         "public_key": c["public_key"], "wrapped": c["wrapped"], "anchor": c["anchor"],
                         "created_at": c["created_at"]}
                        for c in creds],
        "recovery": {"public_key": recovery["public_key"], "anchor": recovery["anchor"],
                     "created_at": recovery["created_at"]} if recovery is not None else None,
        "grants": [{"mk_id": g["mk_id"], "to": g["recipient"], "sealed": g["sealed"], "created_at": g["created_at"]}
                   for g in grants],
        "chain": [{"mk_id": c["mk_id"], "previous": c["previous"], "wrapped": c["wrapped"],
                   "created_at": c["created_at"]} for c in chain],
        "roster": {"mk_id": roster["mk_id"], "sealed": roster["sealed"], "created_at": roster["created_at"]}
        if roster is not None else None,
        "content_keys": [{"key_id": k["key_id"], "public_key": k["public_key"], "mk_id": k["mk_id"],
                          "wrapped": k["wrapped"], "created_at": k["created_at"]} for k in content],
        "high_security": high,
        # each with its kind and its model where its attestation proved one, so the page says what it is as Account
        # does (passkeys.kind_of, model_of); the page names the model of a hardware key alone
        "passkeys": [{"id": k["id"], "credential_id": k["credential_id"], "name": k["name"], "tier": k["tier"],
                      "prf": bool(k["prf"]), "created_at": k["created_at"], "enrolled": k["id"] in enrolled,
                      "can_enrol": k["id"] not in enrolled and (not high or k["tier"] == passkeys.HARDWARE),
                      "kind": passkeys.kind_of(k["tier"], k["backup_eligible"], k["backed_up"], k["attachment"]),
                      "maker": passkeys.model_of(k["maker"], k["tier_why"])}
                     for k in keys],
        # each live link's pins as it last reported them with its History list: null for one that never has, so the page
        # tells that from one that pins none ([]), which refuses every signed command
        # and the envelope forms it said it can seal, with its History list or as it read the statements (null until it
        # says): its word alone, which the page lists before the upgrade so the owner updates a link that cannot seal to
        # a post-quantum key first
        "links": [{"link_id": r["id"], "name": r["name"], "key_id": r["key_id"],
                   "last_required_at": r["last_required_at"],
                   "pins": json.loads(r["pins"]) if r["pins"] is not None else None,
                   "seals": json.loads(r["seals"]) if r["seals"] is not None else None}
                  for r in links],
        # on a post-quantum account, the texts still sealed to a classical key (form 1), wherever they are stored: what
        # re-sealing seals again, and a question its link sealed that may yet be answered, which it
        # leaves until that question is closed for good (_reseal_event_ok)
        "classical_texts": classical,
        # the change that waits, and what cancels it (cancellers): the passkeys whose confirmation does, and whether the
        # recovery code's proof does; what made it (the passkey that signed or confirmed it, RECOVERY, or None for a
        # link's), and whether it is code-backed; the account's passkeys it removes (R, removed_by), in the account's
        # order, and whether it replaces the recovery code, said and never part of who may cancel it; and its body whole
        # (public keys, wraps and statements, nothing secret) but each statement's assertion, so a tab can check
        # before its hold ends that what it holds still opens
        "pending": dict(waiting, change=pending["kind"], cancel_by=cancel_by, cancel_by_code=cancel_by_code,
                        made_by=pending["made_by"], by_code=code_backed(pending),
                        removes=[k["id"] for k in keys if k["id"] in gone],
                        replaces_code=replaces_code(pending["kind"], json.loads(pending["body"])),
                        body=_served_body(json.loads(pending["body"])))
        if waiting is not None else None,
        "to_seal": sealing,
        # turning on again makes a new master key (rekey_due); the database may still hold what was sealed in place,
        # or the decided proposals' words turning on cleared (compaction_due); and what the last sealing in place
        # sealed, and when
        "rekey_due": due,
        "compaction_due": bool(state is not None and state["compaction_due"]),
        "sealed": dict(json.loads(state["sealed"]), at=state["sealed_at"])
        if state is not None and state["sealed"] else None,
        "holds": {"change": holds.delay(HOLD), "recovery": holds.delay(HOLD_RECOVERY), "long": holds.delay(HOLD_LONG)},
    }


def for_link(link_row, now=None, after=0, limit=FOR_LINK_PAGE, seals=None):
    """The account's statements in seq order, each with its canonical payload and the passkey's assertion over it,
    for a link to verify against the owner keys it pinned, once holds whose time has come are settled: a page, those
    after seq `after`, at most `limit` (1 to FOR_LINK_PAGE), with `more` while others follow them; and the newest as
    said(). `seals`: the forms the link says it can seal, as its query gives them (seals_of_query), kept as its History
    list's are (note_seals). {statements, more, e2e}."""
    now = _now(now)
    if isinstance(after, bool) or not isinstance(after, int) or after < 0 \
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= FOR_LINK_PAGE:
        raise link.LinkError(422, f"after is a statement's seq, 0 or more, and limit 1 to {FOR_LINK_PAGE}.")
    seals = seals_of_query(seals)
    holds.settle(link_row["user_id"], now)
    with store.connect() as db:
        if seals is not None:
            note_seals(db, link_row["id"], seals)
        rows = db.execute("SELECT * FROM ade_e2e_statements WHERE user_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                          (link_row["user_id"], after, limit + 1)).fetchall()
        top = newest(db, link_row["user_id"])
    return {"statements": [_statement_view(r, signed=True) for r in rows[:limit]], "more": len(rows) > limit,
            "e2e": said(top)}
