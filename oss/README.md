# oss/

The part of Proofpane that is published as open source.

Two rules hold for everything under this directory:

- **It imports nothing outside `oss/`.** Only the Python standard library and packages that live here, with one
  exception: `sign/` imports pyca/cryptography, because no standard-library module implements ML-DSA.
  `verify-pr/tests/test_boundary.py` fails if any file under `oss/` imports anything else, or imports
  `cryptography` outside `sign/`.
- **It is open source.** Everything under `oss/` is under the Apache License 2.0 (`LICENSE`; `NOTICE` names what it
  carries from elsewhere).

Contents:

- `core/`: `proofpane-core`, the pure library: RFC 8785 canonicalisation, the Verification Manifest v0 and its
  two-check gate, and hash chain v2 for local evidence, with published vectors.
- `verify/`: `proofpane-verify`, the offline checker: validates a manifest and re-derives its decision, and checks a
  chain v2 file.
- `sign/`: `proofpane-sign`, post-quantum signatures: ML-DSA-65 and Ed25519 together, both required
  (draft-ietf-lamps-pq-composite-sigs-19), and signed JSON documents bound to a purpose.
- `verify-pr/`: Verify for pull requests: claims and changes in, a manifest out.

Run every suite: `python3 oss/run_tests.py` (standard library only, except the `sign` suite, which needs
pyca/cryptography 50 or later).
