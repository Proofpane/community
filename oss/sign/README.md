# proofpane-sign

Post-quantum signatures for Proofpane's open part: every signature is an ML-DSA-65 signature and an Ed25519
signature over the same message, and it is valid only if both are. It stays unforgeable while either scheme holds:
ML-DSA-65 (FIPS 204) against a quantum computer, Ed25519 (RFC 8032) against a flaw found in the newer scheme.

The one package under `oss/` that imports anything outside the standard library: pyca/cryptography 50 or later,
which carries ML-DSA through OpenSSL. No standard-library module implements ML-DSA, and a signer written in Python
would not run in constant time. `proofpane-core` stays standard library only; `verify-pr/tests/test_boundary.py`
allows `cryptography` under `sign/` and nowhere else.

| Module | What it does |
|---|---|
| `proofpane_sign.composite` | `id-MLDSA65-Ed25519-SHA512` exactly as draft-ietf-lamps-pq-composite-sigs-19 (April 2026) specifies it: the message representative `M' = Prefix ‖ Label ‖ len(ctx) ‖ ctx ‖ SHA-512(M)`, both components signing `M'` (ML-DSA-65 with the label as its context), keys and signatures as the concatenation of the components (public key 1,984 bytes, private key 64, signature 3,373). A key or signature of the wrong length or form verifies as invalid rather than raising |
| `proofpane_sign.document` | A JSON value signed as its RFC 8785 canonical bytes (`proofpane_core.canonical`) for one named purpose, which becomes the composite context `proofpane/<purpose>`, so a signature made for one purpose never verifies for another. The signature is a detached, closed object: `alg`, `purpose`, `key` (`sha256:` of the public key), `sig` (base64). Key files: the private key is written to a new file, mode 0600, never over an existing one |

## Vectors

`vectors/composite/id-MLDSA65-Ed25519-SHA512.json` is the draft's own test case for this algorithm (Appendix E),
made by the draft's reference implementation, with the draft's URL and the sha256 of the text it was taken from,
and, as its `legend`, section 6.d's text of the IETF Trust's Legal Provisions Relating to IETF Documents, its
year 2026.
`tools/extract_composite_vector.py` writes it from that text. The tests verify its two signatures (one with an empty
context, one with the draft's context), refuse each under the other's context, and check that its private key gives
its public key and signs for it.

## What the tests pin, and what they cannot

Near-miss implementations, each written to look right, and the tests run against them:
a verifier that checks only one component, one that accepts either,
no pre-hash, no ML-DSA context on one side, on both sides consistently (caught only by the draft's vector), the
context length or the context left out of `M'`, no prefix, the components or the key halves in the other order,
text or integers accepted as bytes, the purpose left out of the context, a `json.dumps` look-alike in place of RFC
8785 (on one side and on both), a world-readable or overwritable key file, a public key file whose id is not
checked, an open signature format, lenient base64, a looser purpose pattern: every one fails the tests.

Three checks are defence in depth that no test can tell apart from their absence, because a later check refuses
the same inputs: the explicit length check in `composite.verify` (the component primitives refuse a wrong length),
and the `purpose` label and `key` id checks in `document.verify_document` (the context and the signature itself
refuse a different purpose or key).

Not done: a verifier in pure Python for offline checking without pyca/cryptography; the draft becoming an RFC, when
the label and the vector may change.
