"""The Verification Manifest v0: constants, construction helpers and validation.

A manifest records, for one pull request at one head commit, every obligation the verifier checked, where each came
from, what it concluded and on what evidence, and the gate's decision. The decision is a convenience copy:
`rederive` recomputes it from the obligations and reports any disagreement.

What re-derivation establishes, and what it does not: that the decision follows from the obligations the manifest
lists. It cannot tell whether an obligation was left out or given the wrong class, nor whether an outcome is right.
The first two need the policy (which implicit obligations it requires, at which class); the last needs the evidence
each obligation names.

The format is closed: every object's keys are named here, evidence values are scalars of bounded length, and
anything else is invalid. Validity is a function of the RFC 8785 canonical form: a manifest is canonicalised,
re-parsed, and the re-parsed value is what is checked, so `42` and `42.0` are the same manifest, as they are in JSON.
Its integrity is `manifest_digest`, the sha256 of the canonical bytes. Manifests from the open Action are unsigned.

Every check has a rule id, listed in `VALIDATION_RULES`, and every problem is reported as `[rule-id] where: what`.
The published vectors include at least one invalid manifest per rule, so deleting a check fails a test.
"""

import datetime
import re

from . import gate
from .canonical import CanonicalizationError, _key_text, canonicalize, digest, loads_strict, printable, utf16_key

FORMAT = "proofpane.verification-manifest/0"
ORIGINS = ("claimed", "implicit", "proposed")
CLASSES = ("security", "required", "advisory")
METHODS = ("deterministic", "none")
OUTCOMES = ("pass", "fail", "unverified", "error")
LOCATIONS = ("pr_title", "pr_body")
DENY_TYPES = ("correctable", "terminal")
CHECK_RESULTS = (gate.SUCCESS, gate.FAILURE)
POLICY_SOURCES = ("base-ref", "supplied", "default")
EVIDENCE_KEYS = ("kind", "ref", "name", "app", "conclusion", "context", "state", "rule", "fingerprint", "note",
                 "added_lines", "line", "digest")
MAX_EVIDENCE_STRING = 1024

_TOP = ("format", "subject", "verifier", "policy", "obligations", "unrecognised_sentences", "decision", "issued_at")
_SUBJECT = ("kind", "repo", "pr_number", "base_sha", "head_sha", "diff_digest", "claims_digest")
_SUBJECT_REQUIRED = ("kind", "repo", "base_sha", "head_sha", "diff_digest", "claims_digest")
_VERIFIER = ("name", "version", "ruleset_fingerprint")
_POLICY = ("source", "path", "digest")
_OBLIGATION = ("id", "origin", "claim", "resolver", "class", "method", "outcome", "reason", "evidence", "deny_type")
_OBLIGATION_REQUIRED = ("id", "origin", "claim", "resolver", "class", "method", "outcome", "reason", "evidence")
_CLAIM = ("text", "location", "pattern")
_DECISION = ("verify", "claims", "rule_id", "deny_type", "because")

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_ID = re.compile(r"V-[0-9]{3,}")
_RESOLVER = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

VALIDATION_RULES = (
    "not-canonical", "top-keys", "format",
    "subject-keys", "subject-kind", "subject-repo", "subject-pr-number", "subject-base-sha", "subject-head-sha",
    "subject-diff-digest", "subject-claims-digest",
    "verifier-keys", "verifier-name", "verifier-version", "verifier-fingerprint",
    "policy-keys", "policy-source", "policy-digest", "policy-path-required", "policy-path-forbidden",
    "obligations-list", "obligation-keys", "obligation-id", "obligation-id-unique", "obligation-origin",
    "obligation-class", "obligation-method", "obligation-outcome",
    "claim-implicit-none", "claim-keys", "claim-text", "claim-location", "claim-pattern",
    "resolver-implicit", "resolver-format", "method-none", "method-deterministic", "unresolved-unverified",
    "proposed-advisory", "reason-required", "reason-type",
    "evidence-list", "evidence-item", "evidence-keys", "evidence-value",
    "deny-type-enum", "deny-type-on-fail",
    "unrecognised",
    "decision-keys", "decision-verify", "decision-claims", "decision-rule", "decision-deny", "decision-because",
    "issued-at",
    "decision-rederived",
)


def claims_digest(title, body):
    """Digest of the text the claims were extracted from: `digest({"title": title, "body": body})`, with a missing
    title or body taken as the empty string. A check job that re-reads the pull request and finds a different digest
    knows the description changed after the manifest was computed."""
    return digest({"title": title or "", "body": body or ""})


def diff_digest(files):
    """Digest of the change as the verifier saw it: the list of `{"path", "previous_path", "status", "patch"}` rows,
    one per changed file, ordered by path in RFC 8785 key order (UTF-16 code units), then canonicalised."""
    rows = [{"path": f["path"], "previous_path": f.get("previous_path"), "status": f.get("status"),
             "patch": f.get("patch")} for f in files]
    return digest(sorted(rows, key=lambda r: utf16_key(r["path"])))


def manifest_digest(manifest):
    return digest(manifest)


def build(subject, verifier, policy, obligations, unrecognised_sentences, issued_at):
    """Assemble a manifest and fill in the gate's decision. Raises ValueError if the result is not valid.

    The obligations are normalised first, as `validate` normalises a whole manifest, so `build` and `validate` agree
    on what is valid (an integer written as 3.0 is the integer 3 to both)."""
    try:
        obligations = loads_strict(canonicalize(obligations))
    except CanonicalizationError as e:
        raise ValueError(f"invalid obligations: [not-canonical] obligations: {e}") from None
    errors = validate_obligations(obligations)
    if errors:
        raise ValueError("invalid obligations: " + "; ".join(errors))
    manifest = {
        "format": FORMAT,
        "subject": subject,
        "verifier": verifier,
        "policy": policy,
        "obligations": obligations,
        "unrecognised_sentences": unrecognised_sentences,
        "decision": gate.decide(obligations),
        "issued_at": issued_at,
    }
    errors = validate(manifest)
    if errors:
        raise ValueError("invalid manifest: " + "; ".join(errors))
    return manifest


# ---------------------------------------------------------------- validation


_shown = printable  # every quoted value is escaped and cut: it comes from the file being checked
_cut = _key_text


class _Errors(list):
    def add(self, rule, where, what):
        assert rule in VALIDATION_RULES, rule
        self.append(f"[{rule}] {where}: {what}")


def _closed(obj, allowed, required, where, rule, errors):
    if not isinstance(obj, dict):
        errors.add(rule, where, "expected an object")
        return False
    for key in obj:
        if key not in allowed:
            errors.add(rule, where, f"unknown key {_shown(key)}")
    for key in required:
        if key not in obj:
            errors.add(rule, where, f"missing {key!r}")
    return True


def _match(pattern, value, rule, where, errors):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        errors.add(rule, where, f"{_shown(value)} is not well formed")


def _enum(value, allowed, rule, where, errors):
    if not isinstance(value, (str, type(None))) or value not in allowed:
        errors.add(rule, where, f"{_shown(value)} is not one of {', '.join(str(a) for a in allowed)}")


def _text(value, rule, where, errors):
    if not isinstance(value, str) or not value:
        errors.add(rule, where, "expected a non-empty string")


def _obligation(o, i, seen, errors):
    """Each check runs only on a key that is present (absence is reported once, by the keys rule) and a consistency
    check only when the fields it relates hold legal values (an illegal value is reported once, by its own rule)."""
    where = f"obligations[{i}]"
    if not _closed(o, _OBLIGATION, _OBLIGATION_REQUIRED, where, "obligation-keys", errors):
        return
    if "id" in o:
        oid = o["id"]
        _match(_ID, oid, "obligation-id", f"{where}.id", errors)
        if isinstance(oid, str):
            if oid in seen:
                errors.add("obligation-id-unique", f"{where}.id", f"{_shown(oid)} is used twice")
            seen.add(oid)
    for key, allowed, rule in (("origin", ORIGINS, "obligation-origin"), ("class", CLASSES, "obligation-class"),
                               ("method", METHODS, "obligation-method"), ("outcome", OUTCOMES, "obligation-outcome")):
        if key in o:
            _enum(o[key], allowed, rule, f"{where}.{key}", errors)
    origin = o.get("origin") if o.get("origin") in ORIGINS else None
    klass = o.get("class") if o.get("class") in CLASSES else None
    method = o.get("method") if o.get("method") in METHODS else None
    outcome = o.get("outcome") if o.get("outcome") in OUTCOMES else None
    if "claim" in o and origin is not None:
        claim = o["claim"]
        if origin == "implicit":
            if claim is not None:
                errors.add("claim-implicit-none", f"{where}.claim", "an implicit obligation has no claim")
        elif _closed(claim, _CLAIM, ("text", "location"), f"{where}.claim", "claim-keys", errors):
            if "text" in claim:
                _text(claim["text"], "claim-text", f"{where}.claim.text", errors)
            if "location" in claim:
                _enum(claim["location"], LOCATIONS, "claim-location", f"{where}.claim.location", errors)
            if "pattern" in claim and not isinstance(claim["pattern"], str):
                errors.add("claim-pattern", f"{where}.claim.pattern", "expected a string")
    if "resolver" in o:
        if o["resolver"] is not None:
            _match(_RESOLVER, o["resolver"], "resolver-format", f"{where}.resolver", errors)
        if o["resolver"] is None:
            if origin == "implicit":
                errors.add("resolver-implicit", f"{where}.resolver", "an implicit obligation needs a resolver")
            if method is not None and method != "none":
                errors.add("method-none", f"{where}.method", "a claim with no resolver has method 'none'")
            if outcome is not None and outcome != "unverified":
                errors.add("unresolved-unverified", f"{where}.outcome",
                           "a claim with no resolver can only be 'unverified'")
        elif isinstance(o["resolver"], str) and _RESOLVER.fullmatch(o["resolver"]):
            if method is not None and method != "deterministic":
                errors.add("method-deterministic", f"{where}.method", "a resolved obligation is 'deterministic' in v0")
    if origin == "proposed" and klass is not None and klass != "advisory":
        errors.add("proposed-advisory", f"{where}.class", "a proposed claim is always advisory")
    if "reason" in o:
        reason = o["reason"]
        if outcome in ("unverified", "error"):
            _text(reason, "reason-required", f"{where}.reason", errors)
        elif reason is not None and not isinstance(reason, str):
            errors.add("reason-type", f"{where}.reason", "expected a string or null")
    if "evidence" in o:
        evidence = o["evidence"]
        if not isinstance(evidence, list):
            errors.add("evidence-list", f"{where}.evidence", "expected a list")
        else:
            for j, item in enumerate(evidence):
                ew = f"{where}.evidence[{j}]"
                if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not item.get("kind"):
                    errors.add("evidence-item", ew, "expected an object with a non-empty 'kind'")
                    continue
                for key, value in item.items():
                    if key not in EVIDENCE_KEYS:
                        errors.add("evidence-keys", f"{ew}.{_cut(key)}", f"not one of {', '.join(EVIDENCE_KEYS)}")
                    elif not (value is None or isinstance(value, (bool, int))
                              or (isinstance(value, str) and len(value) <= MAX_EVIDENCE_STRING)):
                        errors.add("evidence-value", f"{ew}.{key}",
                                   f"expected a string of at most {MAX_EVIDENCE_STRING} characters, an integer, "
                                   "a boolean or null")
    if "deny_type" in o:
        _enum(o["deny_type"], DENY_TYPES, "deny-type-enum", f"{where}.deny_type", errors)
        if o["deny_type"] in DENY_TYPES and outcome is not None and outcome != "fail":
            errors.add("deny-type-on-fail", f"{where}.deny_type", "only a failed obligation carries a deny type")


def validate_obligations(obligations):
    """The obligation rules alone, for `build`, which needs well-formed obligations before it can run the gate."""
    errors = _Errors()
    if not isinstance(obligations, list) or not obligations:
        errors.add("obligations-list", "obligations", "expected a non-empty list")
        return list(errors)
    seen = set()
    for i, o in enumerate(obligations):
        _obligation(o, i, seen, errors)
    return list(errors)


def _normalise(manifest):
    """The manifest as its canonical form reads back with `loads_strict`, which is how the offline verifier reads a
    file, or the reason it has no canonical form."""
    try:
        return loads_strict(canonicalize(manifest)), None
    except CanonicalizationError as e:
        return None, f"[not-canonical] manifest: {e}"


def _validate_normalised(m):
    """The same two principles as `_obligation`: absence is reported once, by the keys rule, and a consistency check
    runs only when the values it relates are legal."""
    errors = _Errors()
    if not _closed(m, _TOP, _TOP, "manifest", "top-keys", errors):
        return errors
    if "format" in m and m["format"] != FORMAT:
        errors.add("format", "format", f"expected {FORMAT!r}")
    if "subject" in m and _closed(m["subject"], _SUBJECT, _SUBJECT_REQUIRED, "subject", "subject-keys", errors):
        subject = m["subject"]
        if "kind" in subject and subject["kind"] != "git-change":
            errors.add("subject-kind", "subject.kind", "expected 'git-change'")
        for key, pattern, rule in (("repo", _REPO, "subject-repo"), ("base_sha", _SHA, "subject-base-sha"),
                                   ("head_sha", _SHA, "subject-head-sha"),
                                   ("diff_digest", _DIGEST, "subject-diff-digest"),
                                   ("claims_digest", _DIGEST, "subject-claims-digest")):
            if key in subject:
                _match(pattern, subject[key], rule, f"subject.{key}", errors)
        if "pr_number" in subject:
            n = subject["pr_number"]
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                errors.add("subject-pr-number", "subject.pr_number", "expected a positive integer")
    if "verifier" in m and _closed(m["verifier"], _VERIFIER, _VERIFIER, "verifier", "verifier-keys", errors):
        verifier = m["verifier"]
        if "name" in verifier:
            _text(verifier["name"], "verifier-name", "verifier.name", errors)
        if "version" in verifier:
            _text(verifier["version"], "verifier-version", "verifier.version", errors)
        if "ruleset_fingerprint" in verifier:
            _match(_DIGEST, verifier["ruleset_fingerprint"], "verifier-fingerprint", "verifier.ruleset_fingerprint",
                   errors)
    if "policy" in m and _closed(m["policy"], _POLICY, _POLICY, "policy", "policy-keys", errors):
        policy = m["policy"]
        if "source" in policy:
            _enum(policy["source"], POLICY_SOURCES, "policy-source", "policy.source", errors)
        if "digest" in policy:
            _match(_DIGEST, policy["digest"], "policy-digest", "policy.digest", errors)
        if "path" in policy:
            if policy.get("source") in ("base-ref", "supplied"):
                _text(policy["path"], "policy-path-required", "policy.path", errors)
            elif policy.get("source") == "default" and policy["path"] is not None:
                errors.add("policy-path-forbidden", "policy.path", "the built-in default policy has no path")
    if "obligations" in m:
        errors.extend(validate_obligations(m["obligations"]))
    if "unrecognised_sentences" in m:
        n = m["unrecognised_sentences"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            errors.add("unrecognised", "unrecognised_sentences", "expected a non-negative integer")
    if "decision" in m and _closed(m["decision"], _DECISION, _DECISION, "decision", "decision-keys", errors):
        decision = m["decision"]
        for key, allowed, rule in (("verify", CHECK_RESULTS, "decision-verify"),
                                   ("claims", CHECK_RESULTS, "decision-claims"),
                                   ("rule_id", gate.RULE_IDS, "decision-rule")):
            if key in decision:
                _enum(decision[key], allowed, rule, f"decision.{key}", errors)
        if "deny_type" in decision:
            if decision.get("verify") == gate.FAILURE:
                _enum(decision["deny_type"], DENY_TYPES, "decision-deny", "decision.deny_type", errors)
            elif decision.get("verify") == gate.SUCCESS and decision["deny_type"] is not None:
                errors.add("decision-deny", "decision.deny_type", "a passing verify check has no deny type")
        if "because" in decision:
            because = decision["because"]
            if not isinstance(because, list) or not all(isinstance(x, str) for x in because):
                errors.add("decision-because", "decision.because", "expected a list of obligation ids")
    if "issued_at" in m:
        issued = m["issued_at"]
        well_formed = isinstance(issued, str) and _TIMESTAMP.fullmatch(issued)
        try:
            if well_formed:
                datetime.datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            well_formed = False
        if not well_formed:
            errors.add("issued-at", "issued_at", f"{_shown(issued)} is not a UTC timestamp like 2026-09-24T00:00:00Z")
    return errors


def validate(manifest):
    """Return a list of problems; an empty list means the manifest is well formed. The decision is checked for
    shape here and for correctness by `rederive`."""
    m, problem = _normalise(manifest)
    if problem:
        return [problem]
    return list(_validate_normalised(m))


def rederive(manifest):
    """Validate the manifest and recompute its decision from its obligations. Returns the list of problems; an empty
    list means the manifest is well formed and its decision is the one the gate gives."""
    m, problem = _normalise(manifest)
    if problem:
        return [problem]
    errors = _validate_normalised(m)
    if errors:
        return list(errors)
    try:
        expected = gate.decide(m["obligations"])
    except gate.GateError as e:
        return [f"[decision-rederived] decision: {e}"]
    if expected != m["decision"]:
        return [f"[decision-rederived] decision: the copy says {_shown(m['decision'], 400)}, the gate gives "
                f"{_shown(expected, 400)}"]
    return []


def rederived_decision(manifest):
    """The decision the gate gives for a manifest that validates, or None."""
    m, problem = _normalise(manifest)
    if problem or _validate_normalised(m):
        return None
    try:
        return gate.decide(m["obligations"])
    except gate.GateError:
        return None
