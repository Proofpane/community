"""Command line.

    python -m verify_pr run BUNDLE.json [--config CONFIG.json] [--out MANIFEST.json]
    python -m verify_pr summarize DIR [--config CONFIG.json] [--configs DIR] [--out SUMMARY.json]

`summarize` runs every *.json bundle under DIR and reports, per repository and in total, the four claim counts, how
often each check would be red, and every checked-false claim for hand confirmation.
`--configs DIR` takes a per-repository configuration from DIR/<owner>__<repo>.json when one exists. A bundle that
cannot be verified is listed under `not_verified` with the reason, a JSON file that is not a bundle under `skipped`,
and the run carries on: one bad file never costs the rest of a sample, and none disappears without a trace.

Files are read with the strict reader of proofpane-core (nesting, duplicate keys, numbers that would round), hidden
ones included, and every text taken from the pull request, error messages included, goes out through
`dlp.safe_copy`; an error message is also kept to one escaped line. The repository name is the exception, as in
`verify.py`.
"""

import argparse
import glob
import json
import os
import re
import sys

from proofpane_core.canonical import loads_strict

from .config import load_config
from .dlp import one_line, safe_copy
from .verify import claim_counts, verify


def _config_for(repo, args):
    if args.configs and repo:
        path = os.path.join(args.configs, repo.replace("/", "__") + ".json")
        if os.path.exists(path):
            return load_config(path), path
    return load_config(args.config), args.config or "defaults"


def _read_bundle(path):
    with open(path, "rb") as f:
        return loads_strict(f.read())


def _describe(e):
    """An exception as one line: redacted before it is cut, so a value across the cut is still whole when the rules
    look at it, and checked again after, since a cut can end a text where a rule recognises it."""
    return safe_copy(one_line(safe_copy(f"{type(e).__name__}: {e}")))


# JSON leaves these unescaped: C1 controls, which some terminals act on, the line and paragraph separators, and the
# controls that reorder how text is displayed
_UNSAFE = re.compile("[\x7f-\x9f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def _dumps(obj):
    """JSON for a terminal or a file: text from the pull request stays readable, and nothing in it can act on the
    terminal it is printed to or change the order in which it is shown."""
    return _UNSAFE.sub(lambda m: f"\\u{ord(m.group(0)):04x}", json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_run(args):
    try:
        bundle = _read_bundle(args.bundle)
        if not isinstance(bundle, dict):
            raise ValueError(f"a bundle is a JSON object, not {type(bundle).__name__}")
        cfg, cfg_source = _config_for(bundle.get("repo"), args)
        manifest = verify(bundle, cfg, cfg_source)
    except Exception as e:  # noqa: BLE001  one message and exit 2, never a traceback that may quote the bundle
        print(f"cannot verify {args.bundle}: {_describe(e)}", file=sys.stderr)
        return 2
    text = _dumps(manifest)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    d = manifest["decision"]
    print(f"proofpane/verify: {d['verify']}   proofpane/claims: {d['claims']}   ({d['rule_id']})", file=sys.stderr)
    return 0


def cmd_summarize(args):
    per_repo, false_claims, prs, errors, skipped = {}, [], [], [], []
    # hidden files and directories too: a bundle must not be left out because of its name
    for path in sorted(glob.glob(os.path.join(args.dir, "**", "*.json"), recursive=True, include_hidden=True)):
        rel = os.path.relpath(path, args.dir)
        try:
            bundle = _read_bundle(path)
            if not isinstance(bundle, dict) or "number" not in bundle:
                skipped.append({"path": rel, "reason": "not a bundle: no pull request number"})
                continue
            cfg, cfg_source = _config_for(bundle.get("repo"), args)
            m = verify(bundle, cfg, cfg_source)
        except Exception as e:  # noqa: BLE001  one bad bundle is recorded; it does not cost the rest of the sample
            errors.append({"path": rel, "error": _describe(e)})
            continue
        counts = claim_counts(m)
        repo = bundle["repo"]
        agg = per_repo.setdefault(repo, {"prs": 0, "prs_with_checked_false": 0, "verify_red": 0, "claims_red": 0,
                                         "config": cfg_source,
                                         "counts": {k: 0 for k in counts}})
        agg["prs"] += 1
        agg["verify_red"] += m["decision"]["verify"] == "failure"
        agg["claims_red"] += m["decision"]["claims"] == "failure"
        agg["prs_with_checked_false"] += counts["checked_false"] > 0
        for k, v in counts.items():
            agg["counts"][k] += v
        for o in m["obligations"]:
            if o["origin"] == "claimed" and o["outcome"] == "fail":
                false_claims.append({"repo": repo, "number": bundle["number"], "claim": o["claim"]["text"],
                                     "resolver": o["resolver"], "evidence": o["evidence"],
                                     "hand_check": "TODO: confirmed false | misfire, with one line of reason"})
        prs.append({"repo": repo, "number": bundle["number"], "title": safe_copy(bundle.get("title")), "counts": counts,
                    "decision": m["decision"],
                    "implicit": {o["resolver"]: o["outcome"] + (f" ({o['reason']})" if o["reason"] else "")
                                 for o in m["obligations"] if o["origin"] == "implicit"},
                    "claims": [{"text": o["claim"]["text"], "resolver": o["resolver"], "outcome": o["outcome"],
                                "reason": o["reason"]} for o in m["obligations"] if o["origin"] == "claimed"],
                    "unrecognised_sentences": m["unrecognised_sentences"]})
    total = {"prs": sum(a["prs"] for a in per_repo.values()),
             "prs_with_checked_false": sum(a["prs_with_checked_false"] for a in per_repo.values()),
             "verify_red": sum(a["verify_red"] for a in per_repo.values()),
             "claims_red": sum(a["claims_red"] for a in per_repo.values()),
             "counts": {k: sum(a["counts"][k] for a in per_repo.values())
                        for k in ("checked_false", "checked_true", "could_not_decide", "no_resolver")}}
    total["bundles_not_verified"] = len(errors)
    total["files_skipped"] = len(skipped)
    out = {"total": total, "per_repo": per_repo, "checked_false_for_hand_confirmation": false_claims, "prs": prs,
           "not_verified": errors, "skipped": skipped}
    text = _dumps(out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="verify_pr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("bundle")
    s = sub.add_parser("summarize")
    s.add_argument("dir")
    for p in (r, s):
        p.add_argument("--config")
        p.add_argument("--configs")
        p.add_argument("--out")
    args = ap.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_summarize(args)


if __name__ == "__main__":
    sys.exit(main())
