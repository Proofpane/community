"""Write the published gate and manifest vectors. Run from oss/core:  python3 tools/gen_vectors.py

The gate vectors pin the decision for a named set of obligation lists, one or more per rule; another implementation
of the gate can check itself against them. The manifest vectors are complete manifests: the valid ones must
re-derive cleanly and hash to the recorded digest, the invalid ones must be rejected with the recorded reason.

Expected values are computed by the gate at generation time and reviewed by hand; the independent statement of the
table is the property suite in tests/test_gate.py, which does not read these files.
"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)
sys.path.insert(0, CORE)

from proofpane_core import gate, manifest  # noqa: E402
from proofpane_core.canonical import digest  # noqa: E402

OUT = os.path.join(CORE, "vectors")


def ob(i, origin, klass, outcome, resolver="ci.checks_green", reason=None, deny=None, text="all tests pass"):
    o = {"id": f"V-{i:03d}", "origin": origin,
         "claim": None if origin == "implicit" else {"text": text, "location": "pr_body"},
         "resolver": resolver, "class": klass, "method": "deterministic" if resolver else "none",
         "outcome": outcome, "reason": reason, "evidence": []}
    if deny:
        o["deny_type"] = deny
    return o


GATE_CASES = [
    ("empty", []),
    ("proposed-only", [ob(1, "proposed", "advisory", "error", resolver="diff.no_schema_change",
                          reason="resolver crashed", text="no schema change")]),
    ("advisory-only-all-pass", [ob(1, "implicit", "advisory", "pass", resolver="diff.no_pii")]),
    ("fail-with-a-person-needed-too", [ob(1, "implicit", "security", "unverified", resolver="policy.change_reviewed",
                                          reason="needs a named reviewer's approval on the head commit"),
                                       ob(2, "implicit", "required", "fail", deny="correctable")]),
    ("all-pass", [ob(1, "implicit", "required", "pass"), ob(2, "claimed", "required", "pass")]),
    ("false-claim-required", [ob(1, "claimed", "required", "fail", deny="correctable"),
                              ob(2, "implicit", "required", "pass")]),
    ("false-claim-advisory-still-blocks", [ob(1, "claimed", "advisory", "fail", deny="correctable"),
                                           ob(2, "implicit", "required", "pass")]),
    ("secret-found", [ob(1, "implicit", "security", "fail", resolver="diff.no_secrets")]),
    ("secret-and-false-claim", [ob(1, "claimed", "required", "fail", deny="correctable"),
                                ob(2, "implicit", "security", "fail", resolver="diff.no_secrets")]),
    ("ci-red-no-deny-type", [ob(1, "implicit", "required", "fail")]),
    ("ci-pending", [ob(1, "implicit", "required", "unverified", reason="pending: backend")]),
    ("workflow-changed-unapproved", [ob(1, "implicit", "security", "unverified", resolver="policy.change_reviewed",
                                        reason="needs a named reviewer's approval on the head commit")]),
    ("resolver-error", [ob(1, "implicit", "required", "error", reason="GitHub API returned 502")]),
    ("fail-beats-needs-person", [ob(1, "implicit", "required", "unverified", reason="pending: backend"),
                                 ob(2, "claimed", "required", "fail", deny="correctable")]),
    ("unresolvable-claim", [ob(1, "claimed", "advisory", "unverified", resolver=None, reason="no resolver",
                               text="fully backwards compatible"), ob(2, "implicit", "required", "pass")]),
    ("pii-in-fixture", [ob(1, "implicit", "advisory", "fail", resolver="diff.no_pii"),
                        ob(2, "implicit", "required", "pass")]),
    ("proposed-claim-false", [ob(1, "proposed", "advisory", "fail", resolver="diff.no_schema_change",
                                 text="no schema change"), ob(2, "implicit", "required", "pass")]),
    ("proposed-claim-no-resolver", [ob(1, "proposed", "advisory", "unverified", resolver=None, reason="no resolver",
                                       text="faster"), ob(2, "implicit", "required", "pass")]),
    # `because` follows the manifest's order, not the ids' order
    ("because-in-manifest-order", [ob(3, "implicit", "required", "fail", deny="correctable"),
                                   ob(1, "implicit", "required", "pass"),
                                   ob(2, "claimed", "advisory", "fail", deny="correctable")]),
]


def subject():
    return {"kind": "git-change", "repo": "example/widgets", "pr_number": 42,
            "base_sha": "1" * 40, "head_sha": "2" * 40,
            "diff_digest": manifest.diff_digest([{"path": "src/app.py", "status": "modified",
                                                  "patch": "@@ -1 +1 @@\n-a\n+b"}]),
            "claims_digest": manifest.claims_digest("Fix the parser", "All tests pass.")}


VERIFIER = {"name": "proofpane-verify-pr", "version": "0.1.0", "ruleset_fingerprint": digest({"vectors": 1})}
POLICY = {"source": "base-ref", "path": ".proofpane/policy.json", "digest": digest({"default": "required"})}
ISSUED = "2026-09-24T00:00:00Z"


def build(obligations):
    return manifest.build(subject(), VERIFIER, POLICY, obligations, 2, ISSUED)


VALID = {
    "all-pass": [ob(1, "claimed", "required", "pass"), ob(2, "implicit", "security", "pass",
                                                          resolver="diff.no_secrets")],
    "false-claim": [ob(1, "claimed", "required", "fail", deny="correctable", text="no schema change",
                       resolver="diff.no_schema_change"), ob(2, "implicit", "required", "pass")],
    "needs-person": [ob(1, "implicit", "security", "unverified", resolver="policy.change_reviewed",
                        reason="needs a named reviewer's approval on the head commit")],
    "nothing-blocking": [ob(1, "proposed", "advisory", "pass", resolver="diff.no_schema_change",
                            text="no schema change")],
}
# an evidence string exactly at the cap is valid (one character more is the evidence-value vector)
VALID["evidence-at-cap"] = copy.deepcopy(VALID["all-pass"])
VALID["evidence-at-cap"][0]["evidence"] = [{"kind": "note", "note": "x" * manifest.MAX_EVIDENCE_STRING}]


def invalid_cases():
    """One invalid manifest per validation rule: {rule id: (manifest, rule id expected among the errors)}."""
    good = build(VALID["all-pass"])

    def mutate(fn):
        m = copy.deepcopy(good)
        fn(m)
        return m

    def ob0(**kw):
        return lambda m: m["obligations"][0].update(**kw)

    def ob1(**kw):
        return lambda m: m["obligations"][1].update(**kw)

    def at(section, **kw):
        return lambda m: m[section].update(**kw)

    def claim(**kw):
        return lambda m: m["obligations"][0]["claim"].update(**kw)

    def drop(*keys):
        def fn(m):
            target = m
            for k in keys[:-1]:
                target = target[k]
            del target[keys[-1]]
        return fn

    def both(*fns):
        def fn(m):
            for f in fns:
                f(m)
        return fn

    cases = {
        "not-canonical": lambda m: m.update(unrecognised_sentences=2 ** 60),
        "top-keys": lambda m: m.update(extra=1),
        "format": lambda m: m.update(format="proofpane.verification-manifest/1"),
        "subject-keys": at("subject", extra=1),
        "subject-kind": at("subject", kind="git-tag"),
        "subject-repo": at("subject", repo="no-slash"),
        "subject-pr-number": at("subject", pr_number=0),
        "subject-base-sha": at("subject", base_sha="xyz"),
        "subject-head-sha": at("subject", head_sha="2" * 40 + "\n"),
        "subject-diff-digest": at("subject", diff_digest="sha256:xyz"),
        "subject-claims-digest": at("subject", claims_digest="sha256:" + "A" * 64),
        "verifier-keys": at("verifier", extra=1),
        "verifier-name": at("verifier", name=""),
        "verifier-version": at("verifier", version=""),
        "verifier-fingerprint": at("verifier", ruleset_fingerprint="md5:abc"),
        "policy-keys": at("policy", extra=1),
        "policy-source": at("policy", source="remote"),
        "policy-digest": at("policy", digest=None),
        "policy-path-required": at("policy", source="supplied", path=None),
        "policy-path-forbidden": at("policy", source="default"),
        "obligations-list": lambda m: m.update(obligations=[]),
        "obligation-keys": ob0(extra=1),
        "obligation-id": ob0(id="V-1"),
        "obligation-id-unique": ob1(id="V-001"),
        "obligation-origin": ob0(origin="inferred"),
        "obligation-class": ob0(**{"class": "critical"}),
        "obligation-method": ob0(method="model"),
        "obligation-outcome": ob0(outcome="skipped"),
        "claim-implicit-none": ob1(claim={"text": "x", "location": "pr_body"}),
        "claim-keys": claim(extra=1),
        "claim-text": claim(text=""),
        "claim-location": claim(location="commit"),
        "claim-pattern": claim(pattern=5),
        "resolver-implicit": ob1(resolver=None, method="none", outcome="unverified", reason="x"),
        "resolver-format": ob0(resolver="CI.Checks"),
        "method-none": ob0(resolver=None, outcome="unverified", reason="no resolver"),
        "method-deterministic": ob0(method="none"),
        "unresolved-unverified": ob0(resolver=None, method="none"),
        "proposed-advisory": ob0(origin="proposed"),
        "reason-required": ob0(outcome="unverified"),
        "reason-type": ob0(reason=5),
        "evidence-list": ob0(evidence="none"),
        "evidence-item": ob0(evidence=[{"ref": "x"}]),
        "evidence-keys": ob0(evidence=[{"kind": "note", "not_a_named_key": "x"}]),
        "evidence-value": ob0(evidence=[{"kind": "note", "ref": "x" * 1025}]),
        "deny-type-enum": ob0(outcome="fail", deny_type="maybe"),
        "deny-type-on-fail": ob0(deny_type="correctable"),
        "unrecognised": lambda m: m.update(unrecognised_sentences=-1),
        "decision-keys": at("decision", extra=1),
        "decision-verify": at("decision", verify="passed"),
        "decision-claims": at("decision", claims="ok"),
        "decision-rule": at("decision", rule_id="some-rule"),
        "decision-deny": at("decision", verify="failure"),
        "decision-because": at("decision", because="V-001"),
        "issued-at": lambda m: m.update(issued_at="2026-13-45T25:61:61Z"),
        "decision-rederived": both(at("decision", verify="failure", deny_type="terminal")),
    }
    # second vectors for rules a looser check would still half-enforce: a short hex sha, a short hex digest, a
    # non-integral float in evidence (an integral one is an integer after canonicalisation, and allowed)
    more = {
        "subject-base-sha-short-hex": (at("subject", base_sha="abc1234"), "subject-base-sha"),
        "subject-diff-digest-short-hex": (at("subject", diff_digest="sha256:" + "0" * 63), "subject-diff-digest"),
        "evidence-value-float": (ob0(evidence=[{"kind": "note", "added_lines": 1.5}]), "evidence-value"),
        # checks a plausible narrower version would miss
        "issued-at-unpadded": (lambda m: m.update(issued_at="2026-9-4T0:0:0Z"), "issued-at"),
        "evidence-item-empty-kind": (ob0(evidence=[{"kind": ""}]), "evidence-item"),
        "evidence-value-object": (ob0(evidence=[{"kind": "note", "note": {"text": "x"}}]), "evidence-value"),
        "evidence-value-list": (ob0(evidence=[{"kind": "note", "note": ["x"]}]), "evidence-value"),
        "evidence-value-beyond-exact": (ob0(evidence=[{"kind": "note", "line": 2.0 ** 53}]), "evidence-value"),
        "reason-required-error": (ob0(outcome="error"), "reason-required"),
        "subject-pr-number-bool": (at("subject", pr_number=True), "subject-pr-number"),
        "subject-pr-number-beyond-exact": (at("subject", pr_number=2.0 ** 53), "subject-pr-number"),
        "unrecognised-bool": (lambda m: m.update(unrecognised_sentences=True), "unrecognised"),
        # a consistency check runs only on legal values: each of these pairs an illegal value with the field a check
        # relates it to, and only the illegal value is reported
        "guard-deny-type-on-illegal-outcome": (ob0(outcome="skipped", deny_type="correctable"), "obligation-outcome"),
        "guard-deny-type-illegal-on-pass": (ob0(deny_type="maybe"), "deny-type-enum"),
        "guard-method-none-on-illegal-method": (ob0(resolver=None, method="model", outcome="unverified",
                                                    reason="no resolver"), "obligation-method"),
        "guard-unresolved-on-illegal-outcome": (ob0(resolver=None, method="none", outcome="skipped"),
                                                "obligation-outcome"),
        "guard-proposed-on-illegal-class": (ob0(origin="proposed", **{"class": "critical"}), "obligation-class"),
        "guard-claim-on-illegal-origin": (ob0(origin="inferred", claim={"text": "", "location": "pr_body"}),
                                          "obligation-origin"),
        "guard-decision-deny-on-illegal-verify": (at("decision", verify="passed", deny_type="terminal"),
                                                  "decision-verify"),
        "guard-method-deterministic-on-malformed-resolver": (ob0(resolver="CI.Checks", method="none"),
                                                             "resolver-format"),
        "guard-method-deterministic-on-non-string-resolver": (ob0(resolver=123, method="none"), "resolver-format"),
        # checks inside a rule that a vector per rule id did not reach
        "decision-deny-on-success": (at("decision", deny_type="terminal"), "decision-deny"),
        "decision-because-items": (at("decision", because=[5]), "decision-because"),
        "claim-text-not-string": (claim(text=5), "claim-text"),
        "evidence-item-not-object": (ob0(evidence=["x"]), "evidence-item"),
        "evidence-item-kind-not-string": (ob0(evidence=[{"kind": 5}]), "evidence-item"),
    }
    # a missing required key, for every required key of every closed object: absence is reported once, by the
    # object's keys rule, and not again as a side effect of a value check
    required = [((), "top-keys", manifest._TOP), (("subject",), "subject-keys", manifest._SUBJECT_REQUIRED),
                (("verifier",), "verifier-keys", manifest._VERIFIER), (("policy",), "policy-keys", manifest._POLICY),
                (("obligations", 0), "obligation-keys", manifest._OBLIGATION_REQUIRED),
                (("obligations", 0, "claim"), "claim-keys", ("text", "location")),
                (("decision",), "decision-keys", manifest._DECISION)]
    for where, rule, keys in required:
        for key in keys:
            name = "missing-" + "-".join(str(w) for w in where if not isinstance(w, int)) + ("-" if where else "") + key
            more[name.replace("_", "-")] = (drop(*where, key), rule)
    # the same where the outcome needs a reason, so a value check that ran anyway would have something to report
    more["missing-obligations-reason-when-required"] = (both(ob0(outcome="unverified"), drop("obligations", 0, "reason")),
                                                        "obligation-keys")
    missing = set(manifest.VALIDATION_RULES) - set(cases)
    assert not missing, f"no invalid vector for {sorted(missing)}"
    out = {rule: (mutate(fn), rule) for rule, fn in cases.items()}
    out.update({name: (mutate(fn), rule) for name, (fn, rule) in more.items()})
    return out


def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1, ensure_ascii=False, sort_keys=True)
        fh.write("\n")


def main():
    for sub in ("valid", "invalid"):  # a renamed vector must not leave its old file behind
        d = os.path.join(OUT, "manifest", sub)
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if f.endswith(".json"):
                os.remove(os.path.join(d, f))
    cases = [{"name": name, "obligations": obs, "decision": gate.decide(obs)} for name, obs in GATE_CASES]
    write(os.path.join(OUT, "gate", "cases.json"), cases)
    for name, obs in VALID.items():
        m = build(obs)
        write(os.path.join(OUT, "manifest", "valid", f"{name}.json"),
              {"manifest": m, "manifest_digest": manifest.manifest_digest(m)})
    # the same manifest with integers written as integral floats: identical canonical bytes, so identical digest
    m = build(VALID["all-pass"])
    m["subject"]["pr_number"] = float(m["subject"]["pr_number"])
    m["unrecognised_sentences"] = float(m["unrecognised_sentences"])
    write(os.path.join(OUT, "manifest", "valid", "integral-floats.json"),
          {"manifest": m, "manifest_digest": manifest.manifest_digest(m)})
    for name, (m, reason) in invalid_cases().items():
        write(os.path.join(OUT, "manifest", "invalid", f"{name}.json"), {"manifest": m, "rejected_by": reason})
    print(f"gate cases: {len(cases)}, valid manifests: {len(VALID) + 1}, invalid manifests: {len(invalid_cases())}")


if __name__ == "__main__":
    main()
