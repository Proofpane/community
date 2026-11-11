"""proofpane-core: the pure library under Proofpane's open verifier and gate. No network, standard library only.

Modules:
    canonical  RFC 8785 JSON canonicalisation and the sha256 digests built on it
    manifest   the Verification Manifest v0: constants, construction helpers, validation
    gate       the two-check gate, a pure function of a manifest's obligations
"""

VERSION = "0.1.0.dev0"
