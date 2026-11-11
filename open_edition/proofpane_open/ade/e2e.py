"""End-to-end encryption of what linked sessions send: the envelope a link sends in place of a text, as the
server reads its form and, when encryption is turned on, writes one itself over a text it holds in the clear (sealing
in place, ade/e2e_keys.py). The server never opens an envelope: it holds no key that does, and this module has no
opener. ade_link.py builds the same envelope on the link's side, and the page opens it (ui/src/e2e.ts).

    "e2e1." + base64url-no-padding( header || ciphertext || tag (16) )

The header's first byte is the envelope's form:

    form 1   0x01 || key_id (16 bytes) || epk (32) || nonce (12)                        (61 bytes)      X25519
    form 2   0x02 || key_id (16) || ct (1,120) || nonce (12)                             (1,149 bytes)   X-Wing
    form 3   0x03 || key_id (16) || nonce (12)                                           (29 bytes)      a conversation's

- Form 1: `epk` is a fresh X25519 public key for each envelope; shared = X25519(its private key, EK), refused if all
  zero; k = HKDF-SHA256(ikm = shared, salt = epk || EK, info = "proofpane-e2e/1 content"), 32 bytes. EK is the
  account's content public key the envelope is sealed to (32 bytes), and key_id the first 16 bytes of sha256(EK).
- Form 2: sealed to a post-quantum content key, an X-Wing public key PK of 1,216 bytes (draft-connolly-cfrg-xwing-
  kem-11): (ct, ss) = X-Wing's encapsulation to PK; k = HKDF-SHA256(ikm = ss, salt = sha256(PK), info =
  "proofpane-e2e/2 content"); key_id the first 16 bytes of sha256(PK). A key's form is the envelope's: form 1 to a
  32-byte key, form 2 to a 1,216-byte key, never chosen otherwise.
- Form 3: a message of a conversation under its conversation key tk (32 random bytes, made for each list sent, which
  travels beside the messages in a form-2 envelope bound to the talk-key slot); key_id the first 16 bytes of
  HKDF-SHA256(tk, info = "proofpane-e2e/3 talk key id"); k = HKDF-SHA256(tk, info = "proofpane-e2e/3 talk message|"
  + the message's slot), a key of its own for each message.
- Every form: AES-256-GCM(k, nonce, plaintext, aad = header || the slot's UTF-8 bytes).
- The slot: "proofpane-e2e/1|" + account + "|" + link_id + "|" + the path's part (event_slot, history_slot, talk_slot,
  talk_key_slot; "reported" for a heartbeat's report, which its link alone seals and only the page opens). Empty
  fields are written as empty text and numbers in decimal, never "None" or "null".
- The plaintext: a JSON object per path, its RFC 8785 bytes (UTF-8, minimal escaping); a conversation key's, its 32
  raw bytes.
- Its form, which is all the server reads of one (read): the text matches ^e2e1\\.[A-Za-z0-9_-]+$, its body's length
  is not 1 modulo 4, re-encoding the decoded bytes gives the body exactly, the first byte is one of the forms the path
  takes, and the decoded length is between that form's overhead (77, 1,165 or 45 bytes) and the overhead plus the
  path's most plaintext bytes. `form` is an older release's reading, of form 1 alone, which the paths keep until each
  names the forms it takes.
"""

import base64
import binascii
import collections
import hashlib
import os
import re

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .. import paths  # noqa: F401  (puts the open packages on sys.path)
from proofpane_core.canonical import canonicalize

PREFIX = "e2e1."
VERSION = 1
HEADER = 61
TAG = 16
OVERHEAD = HEADER + TAG                 # 77: an envelope of nothing
CONTEXT = "proofpane-e2e/1"
CONTENT_INFO = b"proofpane-e2e/1 content"
# the most plaintext bytes each path takes
EVENT_PLAIN = 16400
HISTORY_PLAIN = 6000
HISTORY_WITHHELD_PLAIN = 1300
TALK_PLAIN = 16400
_BODY = re.compile(r"[A-Za-z0-9_-]+")           # fullmatch, never match

# ---- forms 2 and 3 -------------------------------------------------------------------------------
FORM_X25519 = 1
FORM_HYBRID = 2
FORM_TALK = 3
FORMS = (FORM_X25519, FORM_HYBRID, FORM_TALK)
NONCE = 12
MLKEM_PUBLIC = 1184                     # ML-KEM-768's encapsulation key (FIPS 203)
MLKEM_CIPHERTEXT = 1088
X25519_LEN = 32
HYBRID_KEY = MLKEM_PUBLIC + X25519_LEN              # 1,216: an X-Wing public key, ML-KEM-768's then X25519's
HYBRID_CT = MLKEM_CIPHERTEXT + X25519_LEN           # 1,120: an X-Wing ciphertext, ML-KEM-768's then X25519's
XWING_LABEL = bytes.fromhex("5c2e2f2f5e5c")         # X-Wing's label, the characters \./ then /^\ (draft-11)
HEADER_HYBRID = 1 + 16 + HYBRID_CT + NONCE          # 1,149
HEADER_TALK = 1 + 16 + NONCE                        # 29
OVERHEADS = {FORM_X25519: OVERHEAD, FORM_HYBRID: HEADER_HYBRID + TAG, FORM_TALK: HEADER_TALK + TAG}   # 77, 1,165, 45
CONTENT_INFO_HYBRID = b"proofpane-e2e/2 content"
TALK_KEY = 32
TALK_KEY_SEALED = OVERHEADS[FORM_HYBRID] + TALK_KEY     # 1,197: a conversation key's envelope, exactly
TALK_KEY_ID_INFO = b"proofpane-e2e/3 talk key id"
TALK_MESSAGE_INFO = "proofpane-e2e/3 talk message|"
HYBRID_KEY_WORDS = ("A post-quantum key is 1,216 bytes: an ML-KEM-768 key whose every coefficient is below 3,329, "
                    "then an X25519 key a secret can be agreed with.")
MLKEM_MISSING_WORDS = "This server's cryptography cannot use ML-KEM, which a post-quantum key needs."
# what a route answers (503) where this server has no ML-KEM: it refuses what needs it, and says so
MLKEM_REFUSED = MLKEM_MISSING_WORDS + (" Post-quantum keys cannot be made, checked or sealed to here until it can: "
                                       "install a cryptography wheel of 48.0.0 or later from PyPI, or one built "
                                       "against OpenSSL 3.5 or later.")

Shape = collections.namedtuple("Shape", "form key_id length")


class NotAnEnvelope(ValueError):
    """A text that begins as an envelope does and is not one, saying why; refused wherever it comes."""


class MLKEMMissing(Exception):
    """This machine's cryptography has no ML-KEM (pyca older than 47, or built against an OpenSSL older than 3.5), so a
    post-quantum key can be neither checked nor sealed to here: not a fault of the key, and so not a ValueError. The
    server says so and refuses what needs it."""


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def key_id(public_key_raw):
    """A content key's id: the first 16 bytes of the sha256 of its raw bytes (32 for a classical key, 1,216 for a
    post-quantum one), as 32 lowercase hex."""
    return hashlib.sha256(public_key_raw).digest()[:16].hex()


def sealed(value):
    """Whether `value` is a text that claims to be an envelope (begins e2e1.): such a text is one, or is refused."""
    return isinstance(value, str) and value.startswith(PREFIX)


def _raw(value):
    """The bytes of a text in an envelope's written form: e2e1., then base64url without padding that reads one way
    only; NotAnEnvelope naming what is wrong otherwise."""
    if not sealed(value):
        raise NotAnEnvelope("An encrypted text begins e2e1.")
    body = value[len(PREFIX):]
    if not _BODY.fullmatch(body) or len(body) % 4 == 1:
        raise NotAnEnvelope("An encrypted text is e2e1. and base64url without padding.")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, binascii.Error):
        raise NotAnEnvelope("An encrypted text is e2e1. and base64url without padding.") from None
    if b64u(raw) != body:
        # trailing bits that are not zero: another text for the same bytes, which the page would read the same
        raise NotAnEnvelope("An encrypted text is e2e1. and base64url without padding, written as the bytes give it.")
    return raw


def form(value, most):
    """(the key_id it is sealed to, its decoded length) for an envelope of form 1 of at most `most` plaintext bytes,
    read from its form alone; NotAnEnvelope naming what is wrong otherwise. An older release's reading: `read` takes the
    forms a path names."""
    raw = _raw(value)
    if not OVERHEAD <= len(raw) <= OVERHEAD + most:
        raise NotAnEnvelope(f"An encrypted text here holds at most {most} bytes.")
    if raw[0] != VERSION:
        raise NotAnEnvelope("An encrypted text here is of version 1.")
    return raw[1:17].hex(), len(raw)


def _either(forms):
    names = [str(f) for f in forms]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def read(value, most, forms):
    """An envelope's Shape (its form, the key_id it names as 32 hex, its decoded length), read from its form alone,
    for an envelope of one of `forms` holding at most `most` plaintext bytes; NotAnEnvelope naming what is wrong
    otherwise. Each path names the forms it takes, with no default (a conversation's messages are form 3 beside their
    key, and nothing else is); a reader that only asks whether a text is sealed names all three (FORMS), so none is
    given out as text."""
    raw = _raw(value)
    if raw[0] not in forms or raw[0] not in OVERHEADS:
        raise NotAnEnvelope(f"An encrypted text here is of form {_either(forms)}.")
    if not OVERHEADS[raw[0]] <= len(raw) <= OVERHEADS[raw[0]] + most:
        raise NotAnEnvelope(f"An encrypted text here holds at most {most} bytes.")
    return Shape(raw[0], raw[1:17].hex(), len(raw))


def read_talk_key(value):
    """A conversation key's envelope read from its form alone: form 2 and exactly 1,197 bytes (its 32 bytes inside),
    its Shape; NotAnEnvelope otherwise."""
    shape = read(value, TALK_KEY, (FORM_HYBRID,))
    if shape.length != TALK_KEY_SEALED:
        raise NotAnEnvelope(f"A conversation's key is sealed in exactly {TALK_KEY_SEALED} bytes.")
    return shape


def plaintext(value):
    """What is sealed: a JSON object as RFC 8785 bytes."""
    return canonicalize(value)


def _field(value):
    return "" if value is None else str(value)


def slot(account, link_id, *parts):
    """What an envelope is bound to: the account, the link that sent it, and the path's part."""
    return "|".join((CONTEXT, account, link_id) + tuple(_field(p) for p in parts))


def event_slot(account, link_id, kind, mirror, ref, client_id):
    """An event to the owner: its kind, the mirror's word or nothing, the seq it refers to or nothing, its client_id."""
    return slot(account, link_id, "event", kind, mirror, ref, client_id)


def history_slot(account, link_id, session_id, withheld):
    """A row of a History list: the session's id, and 1 for a withheld row (which seals its project alone), else 0."""
    return slot(account, link_id, "history", session_id, 1 if withheld else 0)


def talk_slot(account, link_id, session_id, index, count, earlier, who, at, ms):
    """A message of a conversation: its place, the list's length, how many came before, whose, and its times."""
    return slot(account, link_id, "talk", session_id, index, count, earlier, who, at, ms)


def talk_key_slot(account, link_id, session_id, count, earlier):
    """A conversation's key, made for one list: its session's id, the list's length and how many came before. The
    fourth part names the kind, so no slot of one kind equals another's."""
    return slot(account, link_id, "talk-key", session_id, count, earlier)


def _kind(slot_text):
    """A slot's kind, its fourth part (event, history, talk, talk-key): the account's and the link's ids hold no |."""
    parts = slot_text.split("|")
    return parts[3] if len(parts) > 3 else None


def seal(public_key_raw, slot_text, value, ephemeral=None, nonce=None):
    """The envelope of `value` (a JSON object) sealed to the content key whose raw bytes are `public_key_raw`, bound
    to `slot_text`: form 1 to a classical key (32 bytes), form 2 to a post-quantum one (1,216 bytes). `ephemeral` (an
    X25519 private key) and `nonce` are fresh for each envelope; a test passes fixed ones to check against the vectors.
    ValueError for a key of another length, a post-quantum key that fails its check, a shared secret that is all
    zero, or a conversation's message or key to a post-quantum key, which seal_talk seals whole instead (a
    conversation is never form-2 messages). MLKEMMissing where this machine has no ML-KEM."""
    if len(public_key_raw) == HYBRID_KEY:
        if _kind(slot_text) in ("talk", "talk-key"):
            raise ValueError("A post-quantum account's conversation is sealed whole, under a key of its own (seal_talk).")
        return _seal_hybrid(public_key_raw, slot_text, plaintext(value), ephemeral, nonce)
    ek = X25519PublicKey.from_public_bytes(public_key_raw)
    ephemeral = ephemeral or X25519PrivateKey.generate()
    nonce = nonce or os.urandom(12)
    if len(nonce) != 12:
        raise ValueError("an envelope's nonce is 12 bytes")
    epk = ephemeral.public_key().public_bytes_raw()
    shared = ephemeral.exchange(ek)             # cryptography refuses an all-zero result (ValueError)
    if shared == bytes(32):
        raise ValueError("the shared secret is all zero")
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + public_key_raw, info=CONTENT_INFO).derive(shared)
    header = bytes([VERSION]) + bytes.fromhex(key_id(public_key_raw)) + epk + nonce
    sealed_bytes = AESGCM(k).encrypt(nonce, plaintext(value), header + slot_text.encode("utf-8"))
    return PREFIX + b64u(header + sealed_bytes)


def usable_key(public_key_raw):
    """Whether 32 raw bytes are an X25519 public key a link can seal to: one whose shared secret with a fresh key is
    not all zero (a point of low order gives zero with every key)."""
    try:
        return len(public_key_raw) == 32 and X25519PrivateKey.generate().exchange(
            X25519PublicKey.from_public_bytes(public_key_raw)) != bytes(32)
    except ValueError:
        return False


# ---- X-Wing (draft-connolly-cfrg-xwing-kem-11), encapsulation only ------------------------------------------------

def _hybrid_parts(public_key_raw):
    """The ML-KEM-768 and X25519 public keys of a post-quantum key: 1,216 bytes, the first 1,184 passing FIPS 203's
    encapsulation-key check (every coefficient below 3,329), the last 32 a key a secret can be agreed with. ValueError
    in the ADE's words otherwise (pyca's own words say the key has the wrong length, even for a coefficient too
    large); MLKEMMissing where this machine has no ML-KEM, whether pyca lacks the module or its OpenSSL the
    algorithm."""
    if not isinstance(public_key_raw, (bytes, bytearray)) or len(public_key_raw) != HYBRID_KEY:
        raise ValueError(HYBRID_KEY_WORDS)
    try:
        from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PublicKey
    except ImportError:
        raise MLKEMMissing(MLKEM_MISSING_WORDS) from None
    raw = bytes(public_key_raw)
    try:
        pk_m = MLKEM768PublicKey.from_public_bytes(raw[:MLKEM_PUBLIC])
    except UnsupportedAlgorithm:
        raise MLKEMMissing(MLKEM_MISSING_WORDS) from None
    except ValueError:
        raise ValueError(HYBRID_KEY_WORDS) from None
    if not usable_key(raw[MLKEM_PUBLIC:]):
        raise ValueError(HYBRID_KEY_WORDS)
    return pk_m, X25519PublicKey.from_public_bytes(raw[MLKEM_PUBLIC:])


def usable_hybrid_key(public_key_raw):
    """Whether raw bytes are a post-quantum key a link can seal to (_hybrid_parts' check). MLKEMMissing passes
    through: without ML-KEM no key can be called usable or not, and the caller says why it refuses."""
    try:
        _hybrid_parts(public_key_raw)
    except ValueError:
        return False
    return True


def mlkem_here():
    """Whether this machine's cryptography does ML-KEM-768: a key made and encapsulated to (one built against an
    OpenSSL older than 3.5 has none). The server asks at start, and refuses what needs it where it has none."""
    try:
        from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PrivateKey
        ss, ct = MLKEM768PrivateKey.generate().public_key().encapsulate()
        return len(ss) == 32 and len(ct) == MLKEM_CIPHERTEXT
    except Exception:
        return False


_READY = None           # mlkem_here's answer, once asked


def ready():
    """Whether this server does ML-KEM-768, as mlkem_here found when first asked (the app asks at its start, in
    e2e_keys.start). Every path of the server that needs it asks this first and refuses with MLKEM_REFUSED (503) where
    it does not: a post-quantum key taken, checked or sealed to, a post-quantum turn-on, the upgrade, sealing in place
    for a post-quantum account and the hybrid recovery proof. It never seals form 1 in
    its place."""
    global _READY
    if _READY is None:
        _READY = mlkem_here()
    return _READY


def encapsulate(public_key_raw, ephemeral=None, _mlkem=None):
    """X-Wing's encapsulation to a post-quantum key: (ct, ss), ct = ct_M (1,088 bytes) || ct_X (32), ss =
    SHA3-256(ss_M || ss_X || ct_X || pk_X || its label). pyca draws ML-KEM's randomness itself, having no derandomized
    encapsulation; `ephemeral` (an X25519 private key) and `_mlkem` (a function of the ML-KEM key giving (ss_M, ct_M))
    are for the tests alone, which seal the page's derandomized vectors again with them. ValueError for a key that
    fails its check, or an all-zero X25519 secret."""
    pk_m, pk_x = _hybrid_parts(public_key_raw)
    try:
        ss_m, ct_m = (_mlkem or (lambda key: key.encapsulate()))(pk_m)
    except UnsupportedAlgorithm:
        raise MLKEMMissing(MLKEM_MISSING_WORDS) from None
    if len(ss_m) != 32 or len(ct_m) != MLKEM_CIPHERTEXT:
        raise ValueError("ML-KEM-768 gave a secret or a ciphertext of another length")
    ephemeral = ephemeral or X25519PrivateKey.generate()
    ct_x = ephemeral.public_key().public_bytes_raw()
    ss_x = ephemeral.exchange(pk_x)             # cryptography refuses an all-zero result (ValueError)
    if ss_x == bytes(32):
        raise ValueError("the shared secret is all zero")
    ss = hashlib.sha3_256(ss_m + ss_x + ct_x + bytes(public_key_raw[MLKEM_PUBLIC:]) + XWING_LABEL).digest()
    return ct_m + ct_x, ss


def _seal_hybrid(public_key_raw, slot_text, plain, ephemeral=None, nonce=None, _mlkem=None):
    """A form-2 envelope of the bytes `plain` to the post-quantum key `public_key_raw`, bound to `slot_text`."""
    nonce = nonce or os.urandom(NONCE)
    if len(nonce) != NONCE:
        raise ValueError("an envelope's nonce is 12 bytes")
    ct, ss = encapsulate(public_key_raw, ephemeral, _mlkem)
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(public_key_raw).digest(),
             info=CONTENT_INFO_HYBRID).derive(ss)
    header = bytes([FORM_HYBRID]) + bytes.fromhex(key_id(public_key_raw)) + ct + nonce
    return PREFIX + b64u(header + AESGCM(k).encrypt(nonce, bytes(plain), header + slot_text.encode("utf-8")))


# ---- a conversation's key, and its messages (form 3) ---------------------------------------------------------------

def new_talk_key():
    """A conversation's key: 32 random bytes, made for each list sent."""
    return os.urandom(TALK_KEY)


def _talk_key(talk_key):
    if not isinstance(talk_key, (bytes, bytearray)) or len(talk_key) != TALK_KEY:
        raise ValueError("a conversation's key is 32 bytes")
    return bytes(talk_key)


def talk_key_id(talk_key):
    """A conversation key's id, which names it and commits to it: the first 16 bytes of HKDF-SHA256(tk, info
    "proofpane-e2e/3 talk key id"), with no salt, as 32 lowercase hex."""
    return HKDF(algorithm=hashes.SHA256(), length=16, salt=None, info=TALK_KEY_ID_INFO).derive(_talk_key(talk_key)).hex()


def seal_talk_key(public_key_raw, key_slot_text, talk_key, ephemeral=None, nonce=None, _mlkem=None):
    """A conversation's key sealed to the post-quantum content key: a form-2 envelope of its 32 raw bytes, exactly
    1,197 bytes, bound to its talk-key slot (talk_key_slot). A classical key carries none: its conversations stay one
    form-1 envelope per message."""
    if len(public_key_raw) != HYBRID_KEY:
        raise ValueError("A conversation's key is sealed to a post-quantum key alone.")
    return _seal_hybrid(public_key_raw, key_slot_text, _talk_key(talk_key), ephemeral, nonce, _mlkem)


def seal_talk_message(talk_key, slot_text, value, nonce=None):
    """A message of a conversation (form 3) under its conversation key: AES-256-GCM under HKDF-SHA256(tk, info
    "proofpane-e2e/3 talk message|" + its slot), with no salt, so each message has a key of its own; bound to its slot
    (talk_slot) through the AAD as well."""
    tk = _talk_key(talk_key)
    nonce = nonce or os.urandom(NONCE)
    if len(nonce) != NONCE:
        raise ValueError("an envelope's nonce is 12 bytes")
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
             info=(TALK_MESSAGE_INFO + slot_text).encode("utf-8")).derive(tk)
    header = bytes([FORM_TALK]) + bytes.fromhex(talk_key_id(tk)) + nonce
    return PREFIX + b64u(header + AESGCM(k).encrypt(nonce, plaintext(value), header + slot_text.encode("utf-8")))


def seal_talk(public_key_raw, account, link_id, session_id, earlier, messages):
    """A conversation sealed whole for a post-quantum content key, as the server seals one in place: a
    fresh key for this list, sealed in form 2 to its talk-key slot, and each message in form 3 under it, bound to its
    place, its plaintext {"text": ...} as a link seals it. `messages`: dicts with who, text, and the times at and ms
    when there are; none may be sealed already, since a conversation is never a mix. Returns (the key's envelope, its
    key_id, the messages with each text sealed)."""
    if len(public_key_raw) != HYBRID_KEY:
        raise ValueError("A conversation is sealed whole to a post-quantum key alone.")
    if any(sealed(m.get("text")) for m in messages):
        raise ValueError("A conversation sealed whole holds no message sealed already: it is never a mix.")
    tk = new_talk_key()
    count = len(messages)
    key = seal_talk_key(public_key_raw, talk_key_slot(account, link_id, session_id, count, earlier), tk)
    out = [dict(m, text=seal_talk_message(tk, talk_slot(account, link_id, session_id, i, count, earlier, m["who"],
                                                          m.get("at"), m.get("ms")), {"text": m["text"]}))
           for i, m in enumerate(messages)]
    return key, talk_key_id(tk), out
