"""The page's side of post-quantum envelopes, in Python, for the tests alone:
X-Wing's keys from a 32-byte seed and its decapsulation (draft-connolly-cfrg-xwing-kem-11), and the openers of forms 2
and 3. The product never decapsulates: the server holds no private key and its modules have no opener, and the page
opens in ui/src/e2eCrypto.ts. This stands for the page here, written from the design's words and not from ade/e2e.py,
so a fault in the server's sealing does not hide behind the same fault in its opener; it is held to draft-11's
vectors, Wycheproof's and the page's own (tests/test_ade_e2e_envelope_pq.py)."""

import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

LABEL = bytes.fromhex("5c2e2f2f5e5c")


class NotOpened(Exception):
    """An envelope this side refuses, saying why."""


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hkdf(ikm, info, length=32, salt=None):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info.encode("utf-8") if isinstance(
        info, str) else info).derive(ikm)


def expand(seed):
    """X-Wing's private key, a 32-byte seed, expanded: (the ML-KEM-768 private key from SHAKE256's first 64 bytes,
    d || z; the X25519 private key from its last 32; the 1,216-byte public key pk_M || pk_X)."""
    assert len(seed) == 32, "an X-Wing private key is 32 bytes"
    expanded = hashlib.shake_256(seed).digest(96)
    sk_m = MLKEM768PrivateKey.from_seed_bytes(expanded[:64])
    sk_x = X25519PrivateKey.from_private_bytes(expanded[64:])
    return sk_m, sk_x, sk_m.public_key().public_bytes_raw() + sk_x.public_key().public_bytes_raw()


def public_of(seed):
    return expand(seed)[2]


def decapsulate(seed, ct):
    """X-Wing's decapsulation: ss = SHA3-256(ss_M || ss_X || ct_X || pk_X || label), with pk_X from the seed."""
    sk_m, sk_x, pk = expand(seed)
    assert len(ct) == 1120, "an X-Wing ciphertext is 1,120 bytes"
    ss_m = sk_m.decapsulate(ct[:1088])
    ss_x = sk_x.exchange(X25519PublicKey.from_public_bytes(ct[1088:]))
    if ss_x == bytes(32):
        raise NotOpened("the X25519 secret is all zero")
    return hashlib.sha3_256(ss_m + ss_x + ct[1088:] + pk[1184:] + LABEL).digest()


def body_of(envelope):
    assert envelope.startswith("e2e1."), envelope[:10]
    return unb64u(envelope[len("e2e1."):])


def open_bytes2(seed, envelope, slot):
    """The plaintext bytes of a form-2 envelope sealed to the seed's public key and bound to `slot`."""
    data = body_of(envelope)
    pk = public_of(seed)
    if data[0] != 2:
        raise NotOpened(f"form {data[0]}, not 2")
    if data[1:17] != hashlib.sha256(pk).digest()[:16]:
        raise NotOpened("sealed to another key")
    header, sealed_bytes = data[:1149], data[1149:]
    k = hkdf(decapsulate(seed, header[17:1137]), "proofpane-e2e/2 content", salt=hashlib.sha256(pk).digest())
    try:
        return AESGCM(k).decrypt(header[1137:1149], sealed_bytes, header + slot.encode("utf-8"))
    except Exception:
        raise NotOpened("does not open") from None


def open_envelope2(seed, envelope, slot):
    return json.loads(open_bytes2(seed, envelope, slot).decode("utf-8"))


def talk_key_id(tk):
    return hkdf(tk, "proofpane-e2e/3 talk key id", 16).hex()


def open_talk_key(seed, envelope, key_slot):
    """A conversation's key from its envelope: exactly 1,197 bytes, form 2, holding 32 raw bytes."""
    if len(body_of(envelope)) != 1197:
        raise NotOpened("a conversation key's envelope is 1,197 bytes")
    tk = open_bytes2(seed, envelope, key_slot)
    if len(tk) != 32:
        raise NotOpened("a conversation's key is 32 bytes")
    return tk


def open_talk_message(tk, envelope, slot):
    """A conversation's message (form 3) under its key, bound to its slot."""
    data = body_of(envelope)
    if data[0] != 3:
        raise NotOpened(f"form {data[0]}, not 3")
    if data[1:17].hex() != talk_key_id(tk):
        raise NotOpened("sealed under another conversation's key")
    header = data[:29]
    k = hkdf(tk, "proofpane-e2e/3 talk message|" + slot)
    try:
        return json.loads(AESGCM(k).decrypt(header[17:29], data[29:], header + slot.encode("utf-8")).decode("utf-8"))
    except Exception:
        raise NotOpened("does not open") from None


def grant_eseed(mk, public_key, aad):
    """A grant's derandomized encapsulation: (its 64-byte eseed, its nonce), from HKDF-SHA256(MK, salt =
    sha256(PK), info "proofpane-e2e/2 grant ephemeral|" + the grant's AAD text), 76 bytes."""
    out = hkdf(mk, "proofpane-e2e/2 grant ephemeral|" + aad, 76, salt=hashlib.sha256(public_key).digest())
    return out[:64], out[64:]


def x25519_public(private32):
    return X25519PrivateKey.from_private_bytes(private32).public_key().public_bytes_raw()
