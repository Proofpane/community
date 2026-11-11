"""Command line:

    python -m proofpane_verify manifest FILE [--expect-digest sha256:...] [--check verify|claims] [--json]
    python -m proofpane_verify chain FILE [--start-seq N --start-prev sha256:...] [--expect-head sha256:...] [--json]

`manifest` parses FILE strictly (it refuses duplicate keys, NaN and Infinity, nesting deeper than 64 levels, and an
integer beyond 2**53 - 1 unless it is a double's RFC 8785 spelling), checks it against the Verification Manifest v0
rules, recomputes the gate's decision from the obligations, and prints the manifest's RFC 8785 digest. Exit status 0
means the manifest is well formed, its decision is the one the gate gives, and, when `--expect-digest` is given, its
digest is that one.

`--check` also exits 1 unless that check's re-derived result is `success`. That is the decision half of what the
Action's check jobs will do; the other half, re-reading the pull request and failing as stale when its head commit,
description or reviews no longer match the manifest, is not implemented yet.

What this cannot tell you: whether the manifest lists every obligation its policy requires, at the class the policy
gives it, and whether each outcome is right. The first needs the policy, the second the evidence each obligation
names.

`chain` checks a hash chain v2 file line by line (`proofpane_core.chain`) and prints its line count and head. A window
cut from a longer chain verifies when given the seq and prev its first line continues from. Exit status 0 means every
line checks and, when given, the head and line count are the expected ones. On its own that shows only that the
file is internally consistent: anyone who can write it can rebuild it from any point. Comparing the head with one
signed by a key you obtained another way is what shows it was not altered after signing; signed exports are not
implemented yet.
"""

import argparse
import json
import re
import sys

from . import VERSION
from proofpane_core import chain, manifest  # noqa: E402  (path set up by the package)
from proofpane_core.canonical import CanonicalizationError, loads_strict  # noqa: E402


def check_manifest(path, expect_digest=None):
    """{"ok", "digest", "decision" (re-derived, or None when the manifest is not valid), "claimed_decision"
    (the manifest's own copy, whatever it says), "errors"}"""
    try:
        with open(path, encoding="utf-8") as fh:
            m = loads_strict(fh.read())
    except CanonicalizationError as e:
        # duplicate keys, NaN, hostile nesting, or a value RFC 8785 cannot represent exactly
        return {"ok": False, "digest": None, "decision": None, "claimed_decision": None,
                "errors": [f"[not-canonical] {path}: {e}"]}
    except (OSError, ValueError, RecursionError) as e:
        return {"ok": False, "digest": None, "decision": None, "claimed_decision": None,
                "errors": [f"cannot read {path}: {type(e).__name__}: {e}"]}
    errors = manifest.rederive(m)
    digest = None
    if isinstance(m, dict) and not errors:
        digest = manifest.manifest_digest(m)
        if expect_digest is not None and digest != expect_digest:
            errors.append(f"digest: expected {expect_digest}, the manifest hashes to {digest}")
    return {"ok": not errors, "digest": digest, "decision": manifest.rederived_decision(m),
            "claimed_decision": m.get("decision") if isinstance(m, dict) else None, "errors": errors}


def check_chain(path, start_seq=0, start_prev=chain.GENESIS, expect_head=None, expect_lines=None):
    """{"ok", "lines", "head", "next_seq", "errors"}"""
    try:
        result = chain.verify_file(path, start_seq, start_prev)
    except chain.ChainError as e:
        return {"ok": False, "lines": 0, "head": None, "next_seq": None, "errors": [str(e)]}
    except OSError as e:
        return {"ok": False, "lines": 0, "head": None, "next_seq": None,
                "errors": [f"cannot read {path}: {type(e).__name__}: {e}"]}
    errors = [result["problem"]] if result["problem"] else []
    if not errors and expect_head is not None and result["head"] != expect_head:
        errors.append(f"head: expected {expect_head}, the chain ends at {result['head']}")
    if not errors and expect_lines is not None and result["lines"] != expect_lines:
        errors.append(f"lines: expected {expect_lines}, the chain has {result['lines']}")
    return {"ok": not errors, "lines": result["lines"], "head": result["head"], "next_seq": result["next_seq"],
            "errors": errors}


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_SHOWN = 50  # problems printed; a hostile file can hold any number, each quoting at most a short, escaped prefix
MAX_CLAIMED = 2000  # characters of the manifest's own decision printed; a hostile file can make it any size


def _shown(result):
    """The result as printed: at most MAX_SHOWN problems, and how many were left out; the manifest's own decision as
    it is when its JSON is at most MAX_CLAIMED characters, and otherwise that JSON cut, as a string, with its length."""
    out = dict(result)
    errors = result["errors"]
    out["errors"] = errors[:MAX_SHOWN]
    if len(errors) > MAX_SHOWN:
        out["errors_not_shown"] = len(errors) - MAX_SHOWN
    claimed = json.dumps(result.get("claimed_decision"), ensure_ascii=True)
    if len(claimed) > MAX_CLAIMED:
        out["claimed_decision"] = claimed[:MAX_CLAIMED - 3] + "..."
        out["claimed_decision_characters"] = len(claimed)
    return out


def _report(result):
    for e in result["errors"][:MAX_SHOWN]:
        print(f"FAIL {e}", file=sys.stderr)
    if len(result["errors"]) > MAX_SHOWN:
        print(f"FAIL and {len(result['errors']) - MAX_SHOWN} more problems", file=sys.stderr)


def _digest_arg(value):
    """An expected digest must be one: an empty or malformed value would otherwise switch the comparison off, which is
    what an unset shell variable does."""
    if not _DIGEST.fullmatch(value):
        raise argparse.ArgumentTypeError(f"expected sha256:<64 lowercase hex>, got {value!r}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofpane-verify")
    parser.add_argument("--version", action="version", version=f"proofpane-verify {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("manifest", help="check a Verification Manifest and re-derive its decision")
    p.add_argument("file")
    p.add_argument("--expect-digest", type=_digest_arg)
    p.add_argument("--check", choices=("verify", "claims"),
                   help="also exit 1 unless this check's re-derived result is success")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    c = sub.add_parser("chain", help="check a hash chain v2 file and print its head")
    c.add_argument("file")
    c.add_argument("--start-seq", type=int, default=0, help="the seq the first line continues from (a window)")
    c.add_argument("--start-prev", default=chain.GENESIS, type=_digest_arg,
                   help="the hash the first line continues from (a window)")
    c.add_argument("--expect-head", type=_digest_arg)
    c.add_argument("--expect-lines", type=int)
    c.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    if args.command == "chain":
        result = check_chain(args.file, args.start_seq, args.start_prev, args.expect_head, args.expect_lines)
        if args.json:
            print(json.dumps(_shown(result), indent=2, ensure_ascii=True))
        elif result["ok"]:
            print(f"OK {result['lines']} lines, head {result['head']}, next seq {result['next_seq']}")
        else:
            _report(result)
        return 0 if result["ok"] else 1

    result = check_manifest(args.file, args.expect_digest)
    if result["ok"] and args.check:
        outcome = result["decision"][args.check]
        result["check"] = {"name": f"proofpane/{args.check}", "result": outcome}
        if outcome != "success":
            result["ok"] = False
            result["errors"].append(f"proofpane/{args.check}: {outcome} (rule {result['decision']['rule_id']}, "
                                    f"because {', '.join(result['decision']['because']) or 'nothing'})")
    if args.json:  # ASCII only: the manifest's own decision is shown as written, and may hold any character
        print(json.dumps(_shown(result), indent=2, ensure_ascii=True))
    elif result["ok"]:
        d = result["decision"]
        print(f"OK {result['digest']}")
        print(f"proofpane/verify: {d['verify']}  proofpane/claims: {d['claims']}  rule: {d['rule_id']}")
    else:
        _report(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
