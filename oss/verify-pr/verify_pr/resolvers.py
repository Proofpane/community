"""The v0 resolvers. Each is a pure function of (bundle, config, args) that returns an outcome dict:

    {"outcome": "pass" | "fail" | "unverified" | "error", "reason": str | None, "evidence": [ ... ]}

Each checks only what its obligation names. `unverified` and `error` always carry a reason.
Evidence never repeats matched secret or personal text: only the rule, the file and the line. Everything a resolver
returns is still passed through `dlp.safe_copy` before it reaches a manifest (verify.py), because paths, check names
and issue references come from the pull request too.
"""

import re

from . import dlp
from .config import path_matches
from .extract import markdown_lines

PASS = "pass"
FAIL = "fail"
UNVERIFIED = "unverified"


def result(outcome, reason=None, evidence=None):
    if outcome in (UNVERIFIED, "error") and not reason:
        raise ValueError("unverified and error outcomes need a reason")
    return {"outcome": outcome, "reason": reason, "evidence": evidence or []}


def _paths(bundle):
    for f in bundle.get("files", []):
        yield f["path"]
        if f.get("previous_path"):
            yield f["previous_path"]


_HUNK = re.compile(r"@@ -[0-9]{1,10}(?:,[0-9]{1,10})? \+([0-9]{1,10})(?:,[0-9]{1,10})? @@")


def _parse(f):
    """{"added": [(line number in the new file, text)], "removed": [text], "complete": bool} for one changed file.

    The patch is split on "\n" only, as a unified diff ends its lines, dropping one "\r" before it (a file with CRLF
    endings): a carriage return, form feed or U+2028 inside a line is part of it. File headers (`---`, `+++`,
    `diff`, `index`) come only before the first `@@`; after it, a line starting `+++` is an added line whose text
    starts `++`. `complete` is False when the bundle does not hold every added line: no patch while the file may add
    lines (GitHub leaves the patch out of a diff it finds too large), a non-empty patch with no hunk, a hunk header
    that does not parse, or a count of added lines other than the `additions` the bundle records."""
    patch, additions = f.get("patch"), f.get("additions")
    added, removed = [], []
    if patch is None:
        return {"added": added, "removed": removed, "complete": additions == 0}
    new_no, in_hunk, well_formed = 0, False, True
    for raw in patch.split("\n"):
        if raw.endswith("\r"):
            raw = raw[:-1]
        if raw.startswith("@@"):
            in_hunk = True
            m = _HUNK.match(raw)
            if not m:
                well_formed = False
                continue
            new_no = int(m.group(1)) - 1
        elif not in_hunk:
            continue
        elif raw.startswith("+"):
            new_no += 1
            added.append((new_no, raw[1:]))
        elif raw.startswith("-"):
            removed.append(raw[1:])
        elif not raw.startswith("\\"):  # "\ No newline at end of file" is not a line of the file
            new_no += 1
    complete = well_formed and (in_hunk or patch == "") and (additions is None or additions == len(added))
    return {"added": added, "removed": removed, "complete": complete}


def _added_lines(f):
    return _parse(f)["added"]


def _removed_lines(f):
    return _parse(f)["removed"]


def _unscanned(bundle):
    """1-based positions of the changed files whose added lines the bundle does not hold (see `_parse`): a check must
    not pass over them."""
    return [i for i, f in enumerate(bundle.get("files", []), 1) if not _parse(f)["complete"]]


def _missing_files(bundle):
    """How many changed files the bundle does not list: GitHub lists at most 3,000, and `changed_files` says how many
    there are."""
    n = bundle.get("changed_files")
    if isinstance(n, bool) or not isinstance(n, int):
        return 0
    return max(0, n - len(bundle.get("files", [])))


def _not_listed(bundle):
    missing = _missing_files(bundle)
    return f"{missing} changed file(s) are not listed in the bundle (GitHub lists at most 3,000)" if missing else None


# ---------------------------------------------------------------- CI


_FAILED = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}


def ci_checks_green(bundle, cfg, args=None):
    """Every expected check that applies to this change concluded success. An expected check may carry `paths`
    (it applies only when the change touches one) and `kind: "test"`. A claim that tests pass needs at least one
    applicable test check: a green secret scan says nothing about tests. When the bundle does not list every changed
    file, a check scoped to paths cannot be ruled out, so the result is at best `unverified`."""
    changed = list(_paths(bundle))
    scoped_out = [e for e in cfg.get("expected_checks") or []
                  if e.get("paths") and not any(path_matches(p, e["paths"]) for p in changed)]
    expected = [e for e in cfg.get("expected_checks") or [] if e not in scoped_out]
    expected_statuses = cfg.get("expected_statuses") or []
    if not expected and not expected_statuses:
        return result(UNVERIFIED, "no expected check applies to this change")
    if (args or {}).get("tests_claim") and not any(e.get("kind") == "test" for e in expected):
        return result(UNVERIFIED, "no test check applies to the changed paths")
    fails, pending, evidence = [], [], []
    runs = bundle.get("check_runs", [])
    for exp in expected:
        # pinned: only runs from the named app count; a same-named run from anywhere else is ignored
        mine = [r for r in runs if r.get("name") == exp["name"] and r.get("app") == exp["app"]]
        if not mine:
            pending.append(f"missing: {exp['name']} ({exp['app']})")
            continue
        run = mine[-1]
        evidence.append({"kind": "check-run", "name": run["name"], "app": run["app"],
                         "conclusion": run.get("conclusion")})
        if run.get("status") != "completed":
            pending.append(f"still running: {exp['name']}")
        elif run.get("conclusion") == "success":
            continue
        elif run.get("conclusion") in _FAILED:
            fails.append(f"{exp['name']}: {run.get('conclusion')}")
        else:  # skipped, neutral: the tests did not run
            pending.append(f"{exp['name']}: {run.get('conclusion')}, the check did not run")
    statuses = bundle.get("statuses", [])
    for exp in expected_statuses:
        mine = [s for s in statuses if s.get("context") == exp["context"] and s.get("creator") == exp["creator"]]
        if not mine:
            pending.append(f"missing status: {exp['context']}")
            continue
        state = mine[-1].get("state")
        evidence.append({"kind": "status", "context": exp["context"], "state": state})
        if state == "success":
            continue
        if state in ("failure", "error"):
            fails.append(f"{exp['context']}: {state}")
        else:
            pending.append(f"{exp['context']}: {state}")
    if fails:
        return result(FAIL, "; ".join(fails), evidence)
    if pending:
        return result(UNVERIFIED, "pending: " + "; ".join(pending), evidence)
    if scoped_out and _not_listed(bundle):
        return result(UNVERIFIED, _not_listed(bundle) + "; a check scoped to paths may apply to them", evidence)
    return result(PASS, None, evidence)


# ---------------------------------------------------------------- diff


def _listed_or_unverified(bundle):
    """PASS when every changed file is listed; otherwise the absence of a match among the listed ones decides
    nothing."""
    note = _not_listed(bundle)
    return result(UNVERIFIED, note) if note else result(PASS)


def diff_no_schema_change(bundle, cfg, args=None):
    hits = [p for p in _paths(bundle) if path_matches(p, cfg["schema_globs"])]
    if hits:
        return result(FAIL, None, [{"kind": "changed-path", "ref": p} for p in hits[:5]])
    return _listed_or_unverified(bundle)


def diff_no_dependency_change(bundle, cfg, args=None):
    hits = [p for p in _paths(bundle) if path_matches(p, cfg["dependency_files"])]
    if hits:
        # v0 limit: any change to a manifest counts, even one that touches only scripts or metadata
        return result(FAIL, None, [{"kind": "changed-path", "ref": p} for p in hits[:5]])
    return _listed_or_unverified(bundle)


def diff_docs_only(bundle, cfg, args=None):
    paths = list(_paths(bundle))
    if not paths:
        return result(UNVERIFIED, "no changed files in the bundle")
    others = [p for p in paths if not path_matches(p, cfg["doc_globs"])]
    if others:
        return result(FAIL, None, [{"kind": "changed-path", "ref": p} for p in others[:5]])
    return _listed_or_unverified(bundle)


_SKIP = re.compile(r"@(?:pytest\.mark\.)?skip\w*|@unittest\.skip\w*|\bpytest\.skip\(|\b(?:it|test|describe)\.skip\(|"
                   r"\bx(?:it|describe|test)\(|\bt\.Skip(?:Now|f)?\(|@Disabled\b|@Ignore\b|\[Ignore\]|"
                   r"\bskipTest\(|#\[ignore\]")
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)|^\s*(?:it|test|describe)\(\s*['\"`]([^'\"`]+)|"
                       r"^\s*func\s+(Test\w+)|^\s*(?:public\s+)?void\s+(test\w+)\s*\(")


def _unscanned_tests(bundle, cfg):
    """1-based positions of test files that are not removed and whose added lines the bundle does not hold."""
    files = bundle.get("files", [])
    return [i for i in _unscanned(bundle)
            if files[i - 1].get("status") != "removed" and path_matches(files[i - 1]["path"], cfg["test_globs"])]


def _tests_unseen(bundle, cfg):
    """Why the bundle cannot show what the test files do, or None."""
    unseen = _unscanned_tests(bundle, cfg)
    if unseen:
        return (f"{len(unseen)} test file(s) are not in the bundle whole (no patch where lines may have been added, "
                "or one that does not account for every added line); they were not read"), \
            [{"kind": "not-scanned", "ref": f"changed path {i}"} for i in unseen[:10]]
    if _not_listed(bundle):
        return _not_listed(bundle), []
    return None, []


def diff_tests_added(bundle, cfg, args=None):
    evidence = []
    for f in bundle.get("files", []):
        if f.get("status") == "removed" or not path_matches(f["path"], cfg["test_globs"]):
            continue
        real = [(n, t) for n, t in _added_lines(f) if t.strip() and not _SKIP.search(t)]
        if real:
            evidence.append({"kind": "test-path", "ref": f["path"], "added_lines": len(real)})
    if evidence:
        return result(PASS, None, evidence[:5])
    why, unseen = _tests_unseen(bundle, cfg)
    if why:
        return result(UNVERIFIED, why, unseen)
    return result(FAIL, None, [{"kind": "note", "ref": "no test path has an added line other than a skip marker"}])


def _approved_at_head(bundle, cfg):
    head = bundle.get("head_sha")
    reviewers = set(cfg.get("reviewers") or [])
    for r in bundle.get("reviews", []):
        if r.get("state") == "APPROVED" and r.get("user") in reviewers and r.get("commit_id") == head:
            return r.get("user")
    return None


def diff_tests_not_weakened(bundle, cfg, args=None):
    """Implicit. Deleting or skipping the failing test is the cheapest way to make "tests pass" true, and renaming it
    to a path no test glob matches is as cheap: a renamed file is judged by its old path as well as its new one. A
    copy leaves its old path in place, so a copied file is judged by its new path only."""
    found = []
    for f in bundle.get("files", []):
        path, old = f["path"], (f.get("previous_path") if f.get("status") != "copied" else None)
        is_test = path_matches(path, cfg["test_globs"])
        if is_test and f.get("status") == "removed":
            found.append({"kind": "test-file-removed", "ref": path})
            continue
        if old and not is_test and path_matches(old, cfg["test_globs"]):
            found.append({"kind": "test-file-removed", "ref": old})  # renamed out of the test paths
            continue
        config = [p for p in (path, old) if p and path_matches(p, cfg["test_config_globs"])]
        if config:
            found.append({"kind": "test-config-changed", "ref": config[0]})
        if not is_test:
            continue
        for n, text in _added_lines(f):
            if _SKIP.search(text):
                found.append({"kind": "skip-marker-added", "ref": f"{path}:{n}"})
        removed = {next(g for g in m.groups() if g) for m in map(_TEST_DEF.match, _removed_lines(f)) if m}
        added = {next(g for g in m.groups() if g) for m in (_TEST_DEF.match(t) for _, t in _added_lines(f)) if m}
        for name in sorted(removed - added):
            found.append({"kind": "test-case-removed", "ref": f"{path}::{name}"})
    if not found:
        why, unseen = _tests_unseen(bundle, cfg)
        return result(UNVERIFIED, why, unseen) if why else result(PASS)
    who = _approved_at_head(bundle, cfg)
    if who:
        return result(PASS, None, found[:10] + [{"kind": "approval", "ref": who}])
    return result(UNVERIFIED, "tests deleted, skipped or reconfigured; needs a named reviewer's approval on the "
                              "head commit", found[:10])


# ---------------------------------------------------------------- secrets and personal data


def _description_lines(bundle):
    """[(location, [(line number, text), ...])] for the pull request's title and description."""
    return [(location, list(enumerate(markdown_lines(bundle.get(key) or ""), 1)))
            for location, key in (("pr_title", "title"), ("pr_body", "body"))]


def _path_lines(bundle):
    return list(enumerate(_paths(bundle), 1))


def diff_no_secrets(bundle, cfg, args=None):
    """Implicit, security. Added lines, changed paths, and the title and description: a secret pasted into the
    description is as exposed as one in the code, and survives in any copy made before the description is edited.
    Each text is examined in every form `dlp.forms` gives: as written, with inline markdown removed, and with every
    `*`, backtick, `~~` and HTML tag removed. A finding names the rule and where it is, and never repeats the match;
    a path finding names the path by its position, not by its text."""
    suppressed = set(cfg.get("suppress_secret_fingerprints") or [])
    found, seen = [], set()

    def scan(lines, ref_of):
        for i, (n, text) in enumerate(lines):
            matches = dlp.secret_matches(text)
            materials = [(rule, matched) for rule, matched, _, _ in matches if rule != "private-key-block"]
            keys = [matched for rule, matched, _, _ in matches if rule == "private-key-block"]
            if keys:  # one text per key, whichever form found its header, so one suppression covers one key
                materials += [("private-key-block", block) for block in dlp.key_blocks(lines, i) or keys[:1]]
            for rule, material in materials:
                fp = dlp.fingerprint(rule, material)
                key = (rule, ref_of(n), fp)
                if fp in suppressed or key in seen:
                    continue
                seen.add(key)
                found.append({"kind": "secret", "rule": rule, "ref": key[1], "fingerprint": fp})

    for location, lines in _description_lines(bundle):
        scan(lines, lambda n, location=location: f"{location}:{n}")
    scan(_path_lines(bundle), lambda n: f"changed path {n}")
    for f in bundle.get("files", []):
        scan(list(_added_lines(f)), lambda n, path=f["path"]: f"{path}:{n}")
    if found:
        return result(FAIL, None, found[:10])
    return _unscanned_result(bundle)


def _unscanned_result(bundle):
    missing = _unscanned(bundle)
    if missing:
        return result(UNVERIFIED, f"{len(missing)} changed file(s) are not in the bundle whole (no patch where "
                                  "lines may have been added, as GitHub omits it for a large diff, or one that does "
                                  "not account for every added line); they were not scanned",
                      [{"kind": "not-scanned", "ref": f"changed path {i}"} for i in missing[:10]])
    return _listed_or_unverified(bundle)


def diff_no_pii(bundle, cfg, args=None):
    """Implicit, advisory. Added lines and changed paths, examined in every form `dlp.forms` gives. Suppression is by
    rule and path in the base-ref allowlist, not by fingerprint: a hash of an address or a number can be reversed by
    guessing."""
    found, seen = [], set()

    def suppressed(rule, path):
        return any(s.get("rule") == rule and path_matches(path, [s.get("path", "")])
                   for s in cfg.get("suppress_pii") or [])

    for i, path in _path_lines(bundle):
        for rule, _ in dlp.pii_matches(path):
            if not suppressed(rule, path) and (rule, f"changed path {i}") not in seen:
                seen.add((rule, f"changed path {i}"))
                found.append({"kind": "pii", "rule": rule, "ref": f"changed path {i}"})
    for f in bundle.get("files", []):
        for n, text in _added_lines(f):
            for rule, _ in dlp.pii_matches(text):
                ref = f"{f['path']}:{n}"
                if not suppressed(rule, f["path"]) and (rule, ref) not in seen:
                    seen.add((rule, ref))
                    found.append({"kind": "pii", "rule": rule, "ref": ref})
    if found:
        return result(FAIL, None, found[:10])
    return _unscanned_result(bundle)


# ---------------------------------------------------------------- references and review


def issue_linked(bundle, cfg, args=None):
    """The referenced issue exists. A reference to a pull request is not one to an issue; a lookup that failed for any
    reason other than GitHub saying the issue is not there decides nothing. Reads the fetcher's `issue_errors`, and
    the shapes an earlier fetcher wrote (a pull request with `exists: true`, an error inside the record)."""
    args = args or {}
    key = f"{args['repo']}#{args['issue']}" if args.get("repo") else str(args["issue"])
    info = (bundle.get("issues") or {}).get(key)
    if info is None:
        error = (bundle.get("issue_errors") or {}).get(key)
        if error:
            return result(UNVERIFIED, f"issue {key} could not be read ({error})")
        return result(UNVERIFIED, f"issue {key} was not fetched")
    if info.get("is_pull_request"):
        return result(FAIL, None, [{"kind": "issue", "ref": key, "note": "a pull request, not an issue"}])
    if info.get("exists") is True:
        return result(PASS, None, [{"kind": "issue", "ref": key}])
    if info.get("exists") is False:
        return result(FAIL, None, [{"kind": "issue", "ref": key, "note": "does not exist"}])
    return result(UNVERIFIED, f"issue {key} could not be read" + (f" ({info['error']})" if info.get("error") else ""))


def policy_change_reviewed(bundle, cfg, args=None):
    watched = list(cfg["policy_paths"]) + list(cfg["workflow_globs"]) + \
        [p.rstrip("/") + "/**" for p in cfg.get("local_actions") or []]
    hits = [p for p in _paths(bundle) if path_matches(p, watched)]
    if not hits:
        return _listed_or_unverified(bundle)
    who = _approved_at_head(bundle, cfg)
    ev = [{"kind": "changed-path", "ref": p} for p in hits[:5]]
    if who:
        return result(PASS, None, ev + [{"kind": "approval", "ref": who}])
    return result(UNVERIFIED, "policy, allowlist or workflow changed; needs a named reviewer's approval on the head "
                              "commit", ev)


def claims_required_statements(bundle, cfg, args=None):
    text = f"{bundle.get('title') or ''}\n{bundle.get('body') or ''}"
    missing = [s["id"] for s in cfg.get("required_statements") or [] if not re.search(s["pattern"], text, re.I)]
    if missing:
        return result(UNVERIFIED, "not stated: " + ", ".join(missing))
    return result(PASS)


RESOLVERS = {
    "ci.checks_green": ci_checks_green,
    "diff.no_schema_change": diff_no_schema_change,
    "diff.no_dependency_change": diff_no_dependency_change,
    "diff.docs_only": diff_docs_only,
    "diff.tests_added": diff_tests_added,
    "diff.tests_not_weakened": diff_tests_not_weakened,
    "diff.no_secrets": diff_no_secrets,
    "diff.no_pii": diff_no_pii,
    "issue.linked": issue_linked,
    "policy.change_reviewed": policy_change_reviewed,
    "claims.required_statements": claims_required_statements,
}

# The always-on obligations and their class. ci.checks_green is also always on.
IMPLICIT = [
    ("ci.checks_green", "required"),
    ("diff.tests_not_weakened", "required"),
    ("diff.no_secrets", "security"),
    ("diff.no_pii", "advisory"),
    ("policy.change_reviewed", "security"),
    ("claims.required_statements", "required"),  # only when the policy lists statements
]
