"""The two-check gate: a pure function of a manifest's obligations.

A job's exit status can express only pass or fail, so the gate maps every combination of obligation outcomes onto
two checks: `proofpane/verify`, meant to be required, and `proofpane/claims`, informational. The rules are tried in
order and the first that matches decides. The mapping is total: `decide` raises if no rule matches, and the test
suite enumerates the combinations to show that never happens.

    nothing-blocking      no security or required obligation at all   verify failure   claims failure
    any-fail              a security or required obligation failed,   verify failure   claims failure
                          or any claimed obligation failed
    needs-person          a security or required obligation is        verify failure   claims failure
                          unverified or errored
    advisory-not-vouched  an advisory obligation did not pass         verify success   claims failure
    all-pass              every obligation passed                     verify success   claims success

`nothing-blocking` exists because a passing `proofpane/verify` has to mean that something that can block was
checked and passed. Without it, a list holding only advisory obligations (for example, only claims a model proposed)
would pass the required check having vouched for nothing.

A false statement in the pull request's own description blocks whatever its class, because the fix is to correct
the description. An obligation a model proposed (`origin: proposed`) is always advisory, so it can turn
`proofpane/claims` red and can never decide `proofpane/verify`.

`because` lists every obligation that keeps the deciding check from passing, in manifest order: for a failed
`proofpane/verify`, every blocking failure and every blocking obligation still needing a person, whichever rule
matched first; for `advisory-not-vouched`, every obligation that did not pass. It is empty for `nothing-blocking`
and `all-pass`.

Deny types follow the policy engine's rule that the default is `terminal`. A failure is `correctable` only when every
failing cause is correctable and nothing also needs a person. A claimed obligation that failed is correctable unless
it is marked otherwise (the fix is to correct the description); any other obligation is correctable only when it is
marked so; a `security` failure is always terminal.
"""

SUCCESS = "success"
FAILURE = "failure"
BLOCKING = ("security", "required")


class GateError(ValueError):
    """No rule matched. The rules are total over valid obligations, so this means the input was not valid."""


def blocks_by_failing(o):
    return o["outcome"] == "fail" and (o["class"] in BLOCKING or o["origin"] == "claimed")


def blocks_until_a_person(o):
    return o["outcome"] in ("unverified", "error") and o["class"] in BLOCKING


def _nothing_blocking(obs):
    return not any(o["class"] in BLOCKING for o in obs)


def _any_fail(obs):
    return any(blocks_by_failing(o) for o in obs)


def _needs_person(obs):
    return any(blocks_until_a_person(o) for o in obs)


def _advisory_not_vouched(obs):
    return any(o["class"] == "advisory" and o["outcome"] != "pass" for o in obs)


def _all_pass(obs):
    return bool(obs) and all(o["outcome"] == "pass" for o in obs)


# (rule id, predicate over the obligations, proofpane/verify, proofpane/claims)
RULES = (
    ("nothing-blocking", _nothing_blocking, FAILURE, FAILURE),
    ("any-fail", _any_fail, FAILURE, FAILURE),
    ("needs-person", _needs_person, FAILURE, FAILURE),
    ("advisory-not-vouched", _advisory_not_vouched, SUCCESS, FAILURE),
    ("all-pass", _all_pass, SUCCESS, SUCCESS),
)
RULE_IDS = tuple(r[0] for r in RULES)


def _marked_correctable(o):
    return o.get("deny_type", "correctable" if o["origin"] == "claimed" else "terminal") == "correctable"


def _deny_type(rule_id, obs):
    if rule_id != "any-fail":
        return "terminal"
    failing = [o for o in obs if blocks_by_failing(o)]
    if any(o["class"] == "security" for o in failing):
        return "terminal"
    if any(blocks_until_a_person(o) for o in obs):
        return "terminal"  # a person is needed anyway, so the agent cannot clear this on its own
    return "correctable" if all(_marked_correctable(o) for o in failing) else "terminal"


def _because(rule_id, verify, obs):
    if rule_id in ("nothing-blocking", "all-pass"):
        return []
    if verify == FAILURE:
        return [o["id"] for o in obs if blocks_by_failing(o) or blocks_until_a_person(o)]
    return [o["id"] for o in obs if o["outcome"] != "pass"]


def decide(obligations, rules=RULES):
    """Return the decision for `obligations`:

        {"verify": "success"|"failure", "claims": "success"|"failure", "rule_id": str,
         "deny_type": "correctable"|"terminal"|None, "because": [obligation ids]}

    `rules` exists so tests can delete a rule and watch the vectors change.
    """
    for rule_id, matches, verify, claims in rules:
        if not matches(obligations):
            continue
        return {
            "verify": verify,
            "claims": claims,
            "rule_id": rule_id,
            "deny_type": _deny_type(rule_id, obligations) if verify == FAILURE else None,
            "because": _because(rule_id, verify, obligations),
        }
    raise GateError("no gate rule matched these obligations")
