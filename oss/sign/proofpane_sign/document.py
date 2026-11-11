"""Signed Proofpane documents: a JSON value signed as its RFC 8785 canonical bytes, for one named purpose.

A signature is a small JSON object that travels beside the document and leaves the document as it is:

    {"alg": "id-MLDSA65-Ed25519-SHA512", "purpose": "<purpose>",
     "key": "sha256:<hex of the 1984-byte public key>", "sig": "<base64 of the 3373-byte composite signature>"}

The purpose goes into the composite signature's application context as `proofpane/<purpose>`, so a signature made
for one purpose never verifies for another. The verifier names the purpose it expects; the `purpose` field is only
a label for people and is checked against that expectation, never trusted in its place. The format is closed: a
signature object with any other key is refused.
"""

import base64
import binascii
import hashlib
import json
import os
import re

from proofpane_core.canonical import CanonicalizationError, canonicalize

from proofpane_sign import composite

PURPOSE = re.compile(r"[a-z][a-z0-9.-]{0,62}")
CONTEXT_PREFIX = b"proofpane/"
SIGNATURE_KEYS = frozenset({"alg", "purpose", "key", "sig"})
KEY_FILE_KEYS = {"private": frozenset({"alg", "private_key"}), "public": frozenset({"alg", "public_key", "key"})}


def _context(purpose):
    if not isinstance(purpose, str) or not PURPOSE.fullmatch(purpose):
        raise ValueError(f"a purpose is 1 to 63 characters of a-z, 0-9, '.' and '-', starting with a letter: {purpose!r}")
    return CONTEXT_PREFIX + purpose.encode("ascii")


def key_id(public_key):
    """`sha256:<hex>` of a composite public key: how a signature names the key it was made with."""
    public_key = bytes(public_key)
    if len(public_key) != composite.PUBLIC_KEY_BYTES:
        raise ValueError(f"a public key is {composite.PUBLIC_KEY_BYTES} bytes, not {len(public_key)}")
    return "sha256:" + hashlib.sha256(public_key).hexdigest()


def sign_document(value, private_key, purpose):
    """The detached signature of the JSON value `value` for `purpose`. Raises if `value` has no canonical form."""
    ctx = _context(purpose)
    signature = composite.sign(private_key, canonicalize(value), ctx)
    return {"alg": composite.ALGORITHM, "purpose": purpose, "key": key_id(composite.public_key(private_key)),
            "sig": base64.b64encode(signature).decode("ascii")}


def verify_document(value, signature, public_key, purpose):
    """True if and only if `signature` is a well-formed signature of `value`, for `purpose`, by `public_key`.

    Anything else, a malformed signature object, a value with no canonical form, another purpose or another key,
    is False; only a malformed expected purpose raises, since that is the caller's mistake."""
    ctx = _context(purpose)
    if not isinstance(signature, dict) or set(signature) != SIGNATURE_KEYS:
        return False
    if signature["alg"] != composite.ALGORITHM or signature["purpose"] != purpose:
        return False
    try:
        if signature["key"] != key_id(public_key):
            return False
        sig = base64.b64decode(signature["sig"], validate=True) if isinstance(signature["sig"], str) else None
        message = canonicalize(value)
    except (ValueError, TypeError, binascii.Error, CanonicalizationError):
        return False
    if sig is None:
        return False
    return composite.verify(public_key, message, sig, ctx)


def _write_new(path, data):
    """Write `data` to a file that must not exist yet, readable by its owner only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


def write_private_key(path, private_key):
    """Save a private key to a new file, mode 0600. An existing file is never overwritten."""
    if len(bytes(private_key)) != composite.PRIVATE_KEY_BYTES:
        raise ValueError(f"a private key is {composite.PRIVATE_KEY_BYTES} bytes")
    _write_new(path, {"alg": composite.ALGORITHM,
                      "private_key": base64.b64encode(bytes(private_key)).decode("ascii")})


def write_public_key(path, public_key):
    _write_new(path, {"alg": composite.ALGORITHM, "key": key_id(public_key),
                      "public_key": base64.b64encode(bytes(public_key)).decode("ascii")})


def _read(path, kind):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or set(data) != KEY_FILE_KEYS[kind] or data["alg"] != composite.ALGORITHM:
        raise ValueError(f"{path} is not a {composite.ALGORITHM} {kind} key file")
    raw = base64.b64decode(data[f"{kind}_key"], validate=True)
    want = composite.PRIVATE_KEY_BYTES if kind == "private" else composite.PUBLIC_KEY_BYTES
    if len(raw) != want:
        raise ValueError(f"{path}: a {kind} key is {want} bytes, not {len(raw)}")
    if kind == "public" and data["key"] != key_id(raw):
        raise ValueError(f"{path}: the key id does not match the public key")
    return raw


def read_private_key(path):
    return _read(path, "private")


def read_public_key(path):
    return _read(path, "public")
