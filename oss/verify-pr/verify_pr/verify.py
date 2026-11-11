"""Claims and policy in, obligations out, then a Verification Manifest v0 whose decision the core gate derives.

Obligations: every distinct recognised claim becomes one (a claim no resolver can check is
`unverified`, reason `no resolver`); the extractor merges repeats, so two sentences making the same claim with the
same resolver and arguments yield one obligation. The always-on checks are added as `implicit` obligations. The gate
is not here: `proofpane_core.gate.decide` is the one implementation, so `proofpane-verify manifest` re-derives
exactly what this module wrote.

Every string that comes from the pull request (claim text, reasons, evidence: paths, check names and conclusions,
issue references) reaches the manifest only through `dlp.safe_copy`, which withholds a text a secret rule matches and
replaces personal data by the rule's name: the manifest is an artifact that outlives an edit to the description. A
claim taken from a title or description in which a secret rule matches anywhere, in any form, is withheld whatever
its own sentence holds, since the rest of a key may sit in the next sentence or on the next line. The repository name
is copied as it is: it identifies the subject, is checked by the manifest's pattern, and comes from the fetcher rather
than from the pull request's author. Claim texts and evidence strings are then cut to a cap, with a marker, so a long
sentence or path cannot make a manifest of any size, and what is left is checked again, since a cut can end a value
where a rule recognises it.
"""

import datetime
import os

from proofpane_core import gate, manifest
from proofpane_core.canonical import digest, digest_bytes

from . import VERSION
from .extract import extract
from . import dlp
from .resolvers import IMPLICIT, RESOLVERS, UNVERIFIED, result

VERIFIER_NAME = "proofpane-verify-pr"

# A failure a resolver reports is `correctable` only where the change itself can fix it (policy engine rule: the
# default is terminal). A claim the description makes and the change contradicts is always correctable: the fix is
# to correct the description. Anything not listed here is terminal.
CORRECTABLE_WHEN_IMPLICIT = {"ci.checks_green"}


_HERE = os.path.dirname(os.path.abspath(__file__))
_RULESET_SOURCES = (
    ("verify_pr/extract.py", os.path.join(_HERE, "extract.py")),
    ("verify_pr/resolvers.py", os.path.join(_HERE, "resolvers.py")),
    ("verify_pr/dlp.py", os.path.join(_HERE, "dlp.py")),
    ("verify_pr/config.py", os.path.join(_HERE, "config.py")),
    ("verify_pr/verify.py", os.path.join(_HERE, "verify.py")),
    ("proofpane_core/gate.py", gate.__file__),
)


def ruleset_fingerprint(sources=_RULESET_SOURCES):
    """Digest over the source of every module that decides an outcome: claim extraction (patterns, hedges,
    negations), the resolvers, the secret and personal-data rules, the default configuration, obligation assembly
    and deny types, and the gate. Any edit to those files changes it, comments included; that is the price of not
    having to list every table by hand, which is how a first version of this function left most of them out. Line
    endings are normalised first, so a checkout with CRLF endings gives the same fingerprint."""
    digests = {}
    for name, path in sources:
        with open(path, encoding="utf-8", newline=None) as fh:  # universal newlines: \r\n and \r read as \n
            digests[name] = digest_bytes(fh.read().encode("utf-8"))
    return digest({"version": VERSION, "sources": digests})


# evidence values generated here rather than copied from the pull request; everything else is sanitised
_GENERATED_EVIDENCE_KEYS = ("kind", "rule", "fingerprint", "digest")


MAX_CLAIM_TEXT = 1000


def _cap(value, limit=manifest.MAX_EVIDENCE_STRING):
    """`value`, already safe to copy, cut to `limit` characters with a note of its length, and checked again: a cut
    can leave the end of a text looking like a value it did not hold before."""
    if len(value) <= limit:
        return value
    suffix = f" [cut from {len(value)} characters]"
    cut = dlp.safe_copy(value[:limit - len(suffix)] + suffix)
    return cut if len(cut) <= limit else dlp.WITHHELD_PII


def _safe_evidence(item):
    out = {}
    for key, value in item.items():
        if isinstance(value, str):
            value = _cap(value if key in _GENERATED_EVIDENCE_KEYS else dlp.safe_copy(value))
        out[key] = value
    return out


def _obligation(origin, claim, resolver, klass, outcome):
    ob = {"origin": origin, "claim": claim, "resolver": resolver, "class": klass,
          "method": "deterministic" if resolver else "none",
          "outcome": outcome["outcome"], "reason": dlp.safe_copy(outcome["reason"]),
          "evidence": [_safe_evidence(item) for item in outcome["evidence"]]}
    if outcome["outcome"] == "fail":
        correctable = origin == "claimed" or (resolver in CORRECTABLE_WHEN_IMPLICIT and klass != "security")
        ob["deny_type"] = "correctable" if correctable else "terminal"
    return ob


def obligations_for(bundle, cfg):
    claims, unrecognised = extract(bundle.get("title"), bundle.get("body"))
    tainted = {location for location, key in (("pr_title", "title"), ("pr_body", "body"))
               if isinstance(bundle.get(key), str) and dlp.has_secret(bundle[key])}
    obligations = []
    for c in claims:
        text = dlp.WITHHELD_SECRET if c["location"] in tainted else dlp.safe_copy(c["text"], (c["source"],))
        claim = {"text": _cap(text, MAX_CLAIM_TEXT), "location": c["location"], "pattern": c["pattern"]}
        if c["resolver"] is None:
            obligations.append(_obligation("claimed", claim, None, "advisory", result(UNVERIFIED, "no resolver")))
        else:
            outcome = RESOLVERS[c["resolver"]](bundle, cfg, c["args"])
            obligations.append(_obligation("claimed", claim, c["resolver"], "required", outcome))
    for resolver, klass in IMPLICIT:
        if resolver == "claims.required_statements" and not cfg.get("required_statements"):
            continue
        obligations.append(_obligation("implicit", None, resolver, klass, RESOLVERS[resolver](bundle, cfg)))

    # A claim that tests pass is not vouched for while the tests themselves may have been weakened. The reason is the
    # weakening check's own: it may be that tests were deleted or skipped, or only that the bundle does not show them.
    weakened = next((o for o in obligations
                     if o["resolver"] == "diff.tests_not_weakened" and o["outcome"] == UNVERIFIED), None)
    if weakened:
        for o in obligations:
            if o["resolver"] == "ci.checks_green" and o["outcome"] == "pass":
                o.update(result(UNVERIFIED, f"diff.tests_not_weakened is unverified: {weakened['reason']}",
                                o["evidence"]))
    ordered = []
    for i, o in enumerate(obligations, 1):
        ordered.append({"id": f"V-{i:03d}", **o})
    return ordered, unrecognised


def _policy(cfg_source, cfg):
    """`digest` is over the effective configuration: the built-in defaults with the supplied file merged over them.
    It changes when either changes, and it is not the digest of the file's bytes."""
    if cfg_source in (None, "defaults"):
        return {"source": "default", "path": None, "digest": digest(cfg)}
    return {"source": "supplied", "path": os.path.basename(cfg_source), "digest": digest(cfg)}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_BUNDLE_LISTS = {"files": dict, "check_runs": dict, "statuses": dict, "reviews": dict}


def bundle_problems(bundle):
    """What is wrong with a bundle's shape, as short texts; empty when the resolvers can read it."""
    if not isinstance(bundle, dict):
        return [f"a bundle is a JSON object, not {type(bundle).__name__}"]
    problems = []
    for key in ("repo", "base_sha", "head_sha"):
        if not isinstance(bundle.get(key), str):
            problems.append(f"{key} must be a string")
    for key in ("title", "body"):
        if bundle.get(key) is not None and not isinstance(bundle[key], str):
            problems.append(f"{key} must be a string or null")
    if "number" in bundle and (isinstance(bundle["number"], bool) or not isinstance(bundle["number"], int)):
        problems.append("number must be an integer")
    n = bundle.get("changed_files")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
        problems.append("changed_files must be a non-negative integer or null")
    for key, kind in _BUNDLE_LISTS.items():
        items = bundle.get(key, [])
        if not isinstance(items, list) or not all(isinstance(x, kind) for x in items):
            problems.append(f"{key} must be a list of objects")
    issues = bundle.get("issues", {})
    if not isinstance(issues, dict) or not all(isinstance(v, dict) for v in issues.values()):
        problems.append("issues must be an object whose values are objects")
    errors = bundle.get("issue_errors", {})
    if not isinstance(errors, dict) or not all(isinstance(v, str) for v in errors.values()):
        problems.append("issue_errors must be an object whose values are strings")
    for i, f in enumerate(bundle.get("files", []) if isinstance(bundle.get("files", []), list) else []):
        if not isinstance(f, dict):
            continue
        if not isinstance(f.get("path"), str):
            problems.append(f"files[{i}].path must be a string")
        for key in ("previous_path", "patch", "status"):
            if f.get(key) is not None and not isinstance(f[key], str):
                problems.append(f"files[{i}].{key} must be a string or null")
        n = f.get("additions")
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
            problems.append(f"files[{i}].additions must be a non-negative integer or null")
    return problems


def verify(bundle, cfg, cfg_source=None, issued_at=None):
    """Build the manifest for one bundle. `cfg_source` is where the configuration came from (a file name, or None
    for the built-in defaults); it is recorded in the manifest's `policy`, never the full path. Raises ValueError
    for a bundle the resolvers cannot read."""
    problems = bundle_problems(bundle)
    if problems:
        raise ValueError("malformed bundle: " + "; ".join(problems[:5]))
    obligations, unrecognised = obligations_for(bundle, cfg)
    subject = {"kind": "git-change", "repo": bundle.get("repo"),
               "base_sha": bundle.get("base_sha"), "head_sha": bundle.get("head_sha"),
               "diff_digest": manifest.diff_digest(bundle.get("files", [])),
               "claims_digest": manifest.claims_digest(bundle.get("title"), bundle.get("body"))}
    if bundle.get("number"):
        subject["pr_number"] = bundle["number"]
    verifier = {"name": VERIFIER_NAME, "version": VERSION, "ruleset_fingerprint": ruleset_fingerprint()}
    return manifest.build(subject, verifier, _policy(cfg_source, cfg), obligations, unrecognised,
                          issued_at or _now())


def claim_counts(m):
    """Counts of the four claim results, over claimed obligations only."""
    counts = {"checked_false": 0, "checked_true": 0, "could_not_decide": 0, "no_resolver": 0}
    for o in m["obligations"]:
        if o["origin"] != "claimed":
            continue
        if o["resolver"] is None:
            counts["no_resolver"] += 1
        elif o["outcome"] == "fail":
            counts["checked_false"] += 1
        elif o["outcome"] == "pass":
            counts["checked_true"] += 1
        else:
            counts["could_not_decide"] += 1
    return counts
