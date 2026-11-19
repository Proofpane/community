"""The public repository's CI names every case it skips: its run of each shard's files asks pytest for a line per skip
(`-rs`), so that a case its runner skips is in its log by name and reason, where a run without it names none of the
cases it skips. The workflow is at the public repository's root, and in this repository under the export's folder, from
which the export takes it."""

import os
import re

OE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
# this repository's copy first: its own root's .github holds this repository's own CI, not the public one's
PLACES = [os.path.join(OE, "..", "docs", "handoff", "open-edition", "oss-publish", "root", ".github", "workflows",
                       "ci.yml"),
          os.path.join(OE, "..", ".github", "workflows", "ci.yml")]


def test_the_public_ci_names_each_case_it_skips():
    found = [p for p in PLACES if os.path.isfile(p)]
    assert found, PLACES
    lines = open(found[0], encoding="utf-8").read().splitlines()
    runs = [l.split() for l in lines if re.match(r"\s*pytest\s", l) and "--collect-only" not in l]
    assert len(runs) == 1, runs                         # the one run of the shard's files, read
    reports = [a[2:] for a in runs[0] if a.startswith("-r") and len(a) > 2]
    assert any(set(r) & set("sSaA") for r in reports), runs[0]   # -rs, or a report that includes the skips
