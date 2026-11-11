"""proofpane-sign: post-quantum signatures for Proofpane's open part.

Modules:
    composite  id-MLDSA65-Ed25519-SHA512 (draft-ietf-lamps-pq-composite-sigs-19): ML-DSA-65 and Ed25519, both required
    document   a JSON value signed as its RFC 8785 canonical bytes for one named purpose, and key files

The one package under oss/ that imports anything outside the standard library: pyca/cryptography 50 or later, for
ML-DSA and Ed25519. proofpane-core stays standard library only.
"""

VERSION = "0.1.0.dev0"
