"""Composite ML-DSA: an ML-DSA-65 signature and an Ed25519 signature over the same message, both required.

This is `id-MLDSA65-Ed25519-SHA512` as draft-ietf-lamps-pq-composite-sigs-19 (April 2026) specifies it, byte for
byte, so that the draft's published test vector checks this code (`vectors/composite/`). A signature is valid only if
both of its component signatures are, so it stays unforgeable while either scheme holds: ML-DSA-65 (FIPS 204)
against a quantum computer, Ed25519 (RFC 8032) against a flaw found in the newer scheme.

    M'          = Prefix || Label || len(ctx) || ctx || SHA-512(M)
    signature   = ML-DSA-65.Sign(M', mldsa context = Label) || Ed25519.Sign(M')     3309 + 64 bytes
    public key  = ML-DSA-65 public key || Ed25519 public key                        1952 + 32 bytes
    private key = ML-DSA-65 seed || Ed25519 private key                             32 + 32 bytes

Both components sign M', which starts with a prefix and a label no plain Ed25519 or ML-DSA message carries, so
neither component signature can be lifted out and accepted as a signature on the message by itself.

The draft is not yet an RFC. If the RFC changes the construction, its label changes with it, and a signature made
here verifies only under this label.

The primitives are pyca/cryptography's, version 50 or later, which carries ML-DSA through OpenSSL. No module of
the Python standard library implements ML-DSA, and a signer written in Python would not run in constant time.
"""

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

ALGORITHM = "id-MLDSA65-Ed25519-SHA512"
PREFIX = b"CompositeAlgorithmSignatures2025"
LABEL = b"COMPSIG-MLDSA65-Ed25519-SHA512"

MLDSA_PUBLIC, MLDSA_SEED, MLDSA_SIGNATURE = 1952, 32, 3309
ED25519_PUBLIC, ED25519_PRIVATE, ED25519_SIGNATURE = 32, 32, 64
PUBLIC_KEY_BYTES = MLDSA_PUBLIC + ED25519_PUBLIC
PRIVATE_KEY_BYTES = MLDSA_SEED + ED25519_PRIVATE
SIGNATURE_BYTES = MLDSA_SIGNATURE + ED25519_SIGNATURE
MAX_CONTEXT = 255


def _octets(value, what):
    """`value` as bytes. A str or an int is refused: bytes(5) would quietly be five zero bytes."""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{what} must be bytes, not {type(value).__name__}")
    return bytes(value)


def message_representative(message, ctx=b""):
    """M' for `message` under the application context `ctx`, which is at most 255 bytes."""
    message = _octets(message, "message")
    ctx = _octets(ctx, "ctx")
    if len(ctx) > MAX_CONTEXT:
        raise ValueError(f"the context is {len(ctx)} bytes; it may be at most {MAX_CONTEXT}")
    return PREFIX + LABEL + bytes([len(ctx)]) + ctx + hashlib.sha512(message).digest()


def generate():
    """A fresh private key of 64 bytes: a new ML-DSA-65 seed, then a new Ed25519 private key, both from the
    operating system's random source. The draft forbids reusing either component in another key."""
    return (mldsa.MLDSA65PrivateKey.generate().private_bytes_raw()
            + ed25519.Ed25519PrivateKey.generate().private_bytes_raw())


def _components(private_key):
    private_key = _octets(private_key, "private key")
    if len(private_key) != PRIVATE_KEY_BYTES:
        raise ValueError(f"a private key is {PRIVATE_KEY_BYTES} bytes, not {len(private_key)}")
    return (mldsa.MLDSA65PrivateKey.from_seed_bytes(private_key[:MLDSA_SEED]),
            ed25519.Ed25519PrivateKey.from_private_bytes(private_key[MLDSA_SEED:]))


def public_key(private_key):
    """The 1984-byte public key of a private key."""
    ml, ed = _components(private_key)
    return ml.public_key().public_bytes_raw() + ed.public_key().public_bytes_raw()


def sign(private_key, message, ctx=b""):
    """The 3373-byte composite signature of `message` under `ctx`."""
    m_prime = message_representative(message, ctx)
    ml, ed = _components(private_key)
    return ml.sign(m_prime, LABEL) + ed.sign(m_prime)


def verify(public_key_bytes, message, signature, ctx=b""):
    """True if and only if both component signatures are valid for `message` under `ctx`.

    A key or signature of the wrong length or form is an invalid signature, not an error. A context longer than 255
    bytes raises, as in the draft, and so does an argument that is not bytes."""
    m_prime = message_representative(message, ctx)
    pk = _octets(public_key_bytes, "public key")
    sig = _octets(signature, "signature")
    if len(pk) != PUBLIC_KEY_BYTES or len(sig) != SIGNATURE_BYTES:
        return False
    try:
        ml = mldsa.MLDSA65PublicKey.from_public_bytes(pk[:MLDSA_PUBLIC])
        ed = ed25519.Ed25519PublicKey.from_public_bytes(pk[MLDSA_PUBLIC:])
        ml.verify(sig[:MLDSA_SIGNATURE], m_prime, LABEL)
        ed.verify(sig[MLDSA_SIGNATURE:], m_prime)
    except (InvalidSignature, ValueError):
        return False
    return True
