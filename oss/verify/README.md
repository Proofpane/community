# proofpane-verify

Checks, offline, a Verification Manifest (that it is well formed, that its decision is the one the gate gives for the
obligations it lists, and that it hashes to the digest you expect) and a hash chain v2 file.

```bash
cd oss/verify          # there is no installed package yet; run it from here
python3 -m proofpane_verify manifest MANIFEST.json                       # well formed, decision re-derives
python3 -m proofpane_verify manifest MANIFEST.json --expect-digest sha256:...
python3 -m proofpane_verify manifest MANIFEST.json --check verify        # exit 1 unless proofpane/verify is success
python3 -m proofpane_verify manifest MANIFEST.json --json                # decision (re-derived) and claimed_decision
```

```bash
python3 -m proofpane_verify chain SPOOL.jsonl                             # every line checks; prints the head
python3 -m proofpane_verify chain WINDOW.jsonl --start-seq N --start-prev sha256:...   # a window of a chain
python3 -m proofpane_verify chain SPOOL.jsonl --expect-head sha256:... --expect-lines N
```

`--expect-digest`, `--start-prev` and `--expect-head` must each be `sha256:` followed by 64 lowercase hex characters.
Anything else, the empty string included, is refused with exit 2 before the file is read: an empty expected value,
which is what an unset shell variable gives, would otherwise switch the comparison off.

`chain` checks a hash chain v2 file (`proofpane_core.chain`) line by line and names the rule and line of the first
failure. On its own that shows the file is internally consistent, nothing more: whoever can write it can rebuild it.
What would show it was not altered is comparing its head with one signed by a key obtained another way, and signed
exports are not built yet.

`manifest` parses strictly (it refuses duplicate keys, NaN and Infinity, nesting deeper than 64 levels, and an
integer beyond 2**53 − 1 unless it is a double's RFC 8785 spelling), validates against the Verification Manifest v0
rules, recomputes the gate's decision from the obligations, and prints the RFC 8785 digest. `--json` reports the
re-derived decision as `decision`, and the manifest's own copy separately as `claimed_decision`, so a tampered copy is
never mistaken for the result.

Both commands print at most 50 problems, each quoting what it names escaped and cut, and then how many more there
were; `--json` lists the same 50 and gives the rest as a count (`errors_not_shown`). `--json` output is ASCII: the
manifest's own decision is shown as written, and anything outside ASCII in it is escaped, so it cannot act on the
terminal it is printed to. When that decision's JSON is longer than 2,000 characters, `claimed_decision` is the JSON
cut to 2,000 characters, as a string, and `claimed_decision_characters` its full length.

`--check` is the decision half of what the Action's two check jobs will do. The other half, re-reading the pull
request and failing as stale when its head commit, description or reviews no longer match the manifest, is not
implemented yet.

What it cannot tell you: whether the manifest lists every obligation its policy requires, at the class the policy
gives it, and whether each outcome was right. The first needs the policy, the second the evidence each obligation
names. Evidence bundles (v1 packs and v2 local bundles) come later.

Run the tests: `python3 -m unittest discover -s tests` from this directory.
