# proofpane-core

The pure library under Proofpane's open verifier and gate: no network, Python standard library only.

| Module | What it does |
|---|---|
| `proofpane_core.canonical` | RFC 8785 canonical JSON and the `sha256:` digests built on it. Refuses what it cannot represent exactly: non-finite numbers, Python integers beyond 2**53 − 1, lone surrogates, non-string keys, non-JSON types, nesting deeper than 64 levels. `loads_strict` reads text or UTF-8 bytes by the same rules, refuses duplicate keys, and reads an integer literal beyond 2**53 − 1 only when it is a double's RFC 8785 spelling, so whatever `canonicalize` writes reads back and nothing is rounded silently |
| `proofpane_core.manifest` | The Verification Manifest v0: closed format (every key named, evidence values bounded scalars), validation with a rule id per check, `claims_digest`, `diff_digest`, `manifest_digest`, and `rederive`, which recomputes the decision from the obligations. Validity is judged on the value re-parsed from the canonical form |
| `proofpane_core.gate` | The two-check gate: a pure function of the obligations, mapping every combination onto `proofpane/verify` (meant to be required) and `proofpane/claims` (informational) |
| `proofpane_core.chain` | Hash chain v2 for local evidence: one canonical JSON line per event, each carrying the previous line's hash; a locked, fsynced append that several processes can share; verification in bounded memory with a rule id per check; and `recover`, which moves a torn last write aside and records on the chain that it did |

The format's shape is also published as a JSON Schema, `schema/verification-manifest-0.schema.json`; the cross-field
rules it cannot express are listed in its `$comment`, and a test keeps its closed sets equal to the code's.

## Vectors

- `vectors/jcs/`: 2,531 number vectors (2,472 distinct doubles) and 302 objects whose canonical forms were written by a second implementation
  (`tools/gen_jcs_vectors.mjs`, Node's `JSON.stringify` and default sort), not by this code. It includes the two
  examples from RFC 8785 sections 3.2.2 and 3.2.3.
- `vectors/gate/cases.json`: 19 named obligation lists and the decision each must get, at least one per gate rule,
  one of them with ids out of order so that `because` must follow the manifest's order.
- `vectors/chain/`: chain files as they would be stored, torn writes and bad encodings included, with `index.json`
  giving how to verify each and what must happen: 7 valid chains with their line count and head, and 32 invalid ones
  with the rule that must refuse them and the line it fails on, at least one for each of the 15 rules
  (`chain.CHAIN_RULES`). `tools/check_chain_vectors.mjs` is a second implementation, in Node, that checks the valid
  ones and that every invalid one is refused; the tests run it when `node` is installed. It does by hand four things
  the format requires and a JavaScript reader does not do by default: it keeps a byte-order mark so as to refuse it,
  refuses lone surrogates, in keys as in values, refuses nesting deeper than 64 and accepts 64, and reads a year as
  written. The checker as it was before those steps mismatches five vectors: it accepts a line with a byte-order
  mark, one with a lone surrogate in a value, one with a lone surrogate in a key and one nested 65 deep, and refuses
  a valid line dated in the year 50. A sixth, a valid line nested exactly 64 deep, catches a checker that stops one
  level early. The size rule's vector sets a reduced line limit, recorded in its entry, so it needs no megabyte file.
- `vectors/manifest/`: complete manifests. The 6 valid ones must re-derive and hash to the recorded digest; one of
  them writes its integers as integral floats and must hash like the one that does not, and one carries an evidence
  string exactly at the length cap. The 119 invalid ones cover the 56 validation rules
  (`manifest.VALIDATION_RULES`), at least one each, and each is rejected by the rule it names and by no other rule:
  a check reports a missing key once, through its keys rule, and a consistency check runs only on legal values. The
  63 beyond one per rule pin checks a plausible narrower version would miss. 37 remove a required key: every
  required key of every closed object once, and `reason` a second time where the outcome needs one, so absence is
  reported by the keys rule and never again by a value check. 9 pair an illegal value with a field a consistency
  check relates it to, so the check must not run on it (two of them a resolver that is not a string and one that is
  malformed, with a method only a legal resolver is checked against). The other
  17: a short hex commit sha and a short hex diff digest; a non-integral float, an object, a list and 2**53 as
  evidence values; an unpadded timestamp; an empty and a non-string evidence kind, and an evidence item that is not
  an object; an `error` outcome with no reason; a boolean and 2**53 as the pull request number, and a boolean as the
  unrecognised-sentence count; a deny type on a passing decision; a claim text that is not a string; a `because`
  item that is not a string.

`tools/gen_vectors.py` writes the gate and manifest vectors, `tools/gen_chain_vectors.py` the chain vectors. Their expected values come from the gate itself; the
independent statement of the table is `tests/test_gate.py`, which states it as properties and checks every list of
up to three obligations: 75,895 lists, counting the empty one, built from 42 obligation shapes (the seven origin and
class pairs a manifest allows, a proposed claim being always advisory; for each, the outcomes `pass`, `unverified`
and `error`, and `fail` with no deny-type mark, with `correctable` and with `terminal`). The gate's decision depends
on fewer distinctions than that. A seeded sample of 20,000 lists of four to ten obligations, with ids in shuffled
order, is checked against the same restatement.

## What the tests establish, and what they do not

- The canonical form agrees with an independent implementation on every number and object vector, and each refusal
  has a test. What `canonicalize` writes for those vectors, `loads_strict` reads back to the same bytes, and the two
  count nesting the same way. The path to a position is built only when an error names it, so the work grows with
  the size of the value, key sorting aside, and not with its square.
- On every list of up to three obligations, and on the sample of longer ones, the gate gives the verify and claims
  results, rule, deny type and `because` that the test states again from the table's text. The tests also check,
  over every list of up to two or three obligations, that no `unverified` or `error` obligation leaves
  `proofpane/claims` green, that a `proposed` claim never changes `proofpane/verify`, that `proofpane/verify` never
  passes without a `security` or `required` obligation, and that `because` follows the order of the obligations.
- Deleting any one gate rule changes at least one published gate vector, and every validation rule rejects at least
  one published manifest that no other rule rejects. This is the load-bearing-coverage idea (Load-Bearing Coverage:
  Mechanically Checking Whether a Conformance Suite Can Be Passed Without Doing the Work,
  https://doi.org/10.5281/zenodo.21844893) applied to these vector sets only, by this package's own tests; the
  published criterion's tool has not been run on them or on the rest of this repository's suites.
- v0 is not frozen until the first public release. Until then its rules can change under the same format id, as the
  gate's first rule once did (a list with no `security` or `required` obligation now fails `proofpane/verify`).
- A chain that verifies is internally consistent: every line is canonical, carries its own digest, and continues
  the one before it. That is all it shows on its own, because anyone who can write the file can rebuild it from any
  line on. Against a head signed by a key the verifier obtained another way, it would show the record was not altered
  after signing; signing and export are not built yet.
- Recovery: the torn bytes are copied and synced, with their directory entry, before they are cut. The copy is named
  for the line that will record it, `<chain>.torn-<seq>-<12 hex of the hash it continues from>-<12 hex of its
  digest>`, and carries a `.pending` suffix until that line is synced, when it is renamed. A recovery cut short is
  completed by the next call: before the cut, by `recover` (`append` refuses a file that ends in a torn write);
  after it, by either, which records a pending copy made for the chain's next line, or renames one whose record is
  already the last line. The tests kill a recovering process at each of its five fsyncs (after the copy, after its
  directory entry, after the cut, after the record, after the rename) and check that the next `recover` records the
  torn bytes exactly once, under the name the record gives; after the cut, the next `append` does too. They also
  cover a copy shorter or longer than the bytes it names while those are still in the chain (written again), a copy
  that does not match its name, or a pending copy that does not match its record, once the bytes are gone (refused,
  file unchanged), names that only begin like a copy's (ignored), a copy an earlier chain at the same path left
  waiting (not recorded by the new chain, which reaches that seq from a different line), a first line recovered and
  recorded before the chain was moved away (not recorded again), and a record naming a file this module would not
  name (nothing renamed). Three limits, the first pinned by a test so that a fix shows. Every chain at a path starts
  from the same point, so a copy made for a first line is named for a point any of them reaches: a recovery of a
  first line stopped at any of its first four fsyncs, with the chain then moved or removed before any other call,
  leaves a copy the next chain at that path records as its own, and if it stopped before the cut, the moved chain
  still holds the torn bytes and its own recovery records them again; two chains at one path whose first lines tore
  into the same bytes share a copy's name, the second copy is not renamed while the first exists, and a third chain
  records it. Only copies named for the chain's next line are looked for, so if two copies wait for one line (the
  write of a recovery's own record tore) and the process stops after recording the first, the second is left
  pending and never recorded. And the digest a record carries, and the 48 bits of it in the copy's name, are
  unsalted: torn bytes that are few or predictable can be found from them by trying candidates, so a chain shared
  without its side files still discloses that much. A new file's directory entry is synced before its first line is
  written. That these orderings survive a real power loss is not tested: the tests fail an `fsync` or stop a process,
  they do not cut power.
- Error messages quote values and keys escaped and cut to a bounded length, so no one message can hold more than a
  short prefix of what it quotes or start a new line in a log; a value too large to print (an integer past Python's
  digit limit) is described rather than quoted. A hostile file can still produce one message per problem it holds:
  `proofpane-verify` prints at most 50 of them and says how many it left out.
- Concurrent appends: eight processes appending forty events each leave one chain of 320 lines that verifies, with
  each process's events in their own order. With the lock removed, the same test failed on each of six runs, by
  hand; that control is not a committed test, because a race is not deterministic.
- `rederive` shows that a manifest's decision follows from the obligations it lists. It cannot show that no
  obligation the policy requires was left out, or that an outcome is right.
- Nothing here says whether an obligation's outcome is right. That is each resolver's job, and its evidence.

Run: `python3 -m unittest discover -s tests` from this directory.
