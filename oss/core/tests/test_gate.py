"""The gate against its two-check table. The expected verify, claims, rule, deny type and `because` are
written out again here from the table's text, not read from `gate.RULES`, and checked on every combination of up to
three obligations."""

import itertools
import unittest

from _paths import CORE  # noqa: F401
from proofpane_core import gate  # noqa: E402
from proofpane_core.manifest import CLASSES, ORIGINS, OUTCOMES  # noqa: E402

BLOCKING = ("security", "required")


def atoms():
    """Every distinct obligation as the gate sees it: origin, class, outcome, and on failures a deny-type mark."""
    out = []
    for origin in ORIGINS:
        for klass in CLASSES:
            if origin == "proposed" and klass != "advisory":
                continue  # a proposed claim is always advisory (manifest rule)
            for outcome in OUTCOMES:
                for deny in ((None, "correctable", "terminal") if outcome == "fail" else (None,)):
                    a = {"origin": origin, "class": klass, "outcome": outcome}
                    if deny:
                        a["deny_type"] = deny
                    out.append(a)
    return out


def combos(max_len):
    base = atoms()
    yield []
    for n in range(1, max_len + 1):
        for combo in itertools.product(base, repeat=n):
            yield [dict(o, id=f"V-{i:03d}") for i, o in enumerate(combo, 1)]


# ---- the table, restated


def failing_blocker(o):
    return o["outcome"] == "fail" and (o["class"] in BLOCKING or o["origin"] == "claimed")


def person_blocker(o):
    return o["outcome"] in ("unverified", "error") and o["class"] in BLOCKING


def expected(obs):
    if not any(o["class"] in BLOCKING for o in obs):
        rule, verify, claims = "nothing-blocking", "failure", "failure"
    elif any(failing_blocker(o) for o in obs):
        rule, verify, claims = "any-fail", "failure", "failure"
    elif any(person_blocker(o) for o in obs):
        rule, verify, claims = "needs-person", "failure", "failure"
    elif any(o["outcome"] != "pass" for o in obs):
        rule, verify, claims = "advisory-not-vouched", "success", "failure"
    else:
        rule, verify, claims = "all-pass", "success", "success"

    if verify == "success":
        deny = None
    elif rule != "any-fail":
        deny = "terminal"
    else:
        failing = [o for o in obs if failing_blocker(o)]
        mark = [o.get("deny_type") or ("correctable" if o["origin"] == "claimed" else "terminal") for o in failing]
        security = any(o["class"] == "security" for o in failing)
        person = any(person_blocker(o) for o in obs)
        deny = "correctable" if not security and not person and all(m == "correctable" for m in mark) else "terminal"

    if rule in ("nothing-blocking", "all-pass"):
        because = []
    elif verify == "failure":
        because = [o["id"] for o in obs if failing_blocker(o) or person_blocker(o)]
    else:
        because = [o["id"] for o in obs if o["outcome"] != "pass"]
    return {"verify": verify, "claims": claims, "rule_id": rule, "deny_type": deny, "because": because}


class TableProperties(unittest.TestCase):
    def test_every_combination_of_up_to_three(self):
        n = 0
        for obs in combos(3):
            n += 1
            self.assertEqual(gate.decide(obs), expected(obs), obs)  # also totality: decide raises on no match
        self.assertEqual(n, 1 + 42 + 42 ** 2 + 42 ** 3)

    def test_longer_lists_sampled(self):
        # the table is total for any length by construction (the last two rows are complements once the first three
        # miss); a seeded sample of longer lists checks the gate against the restatement there too
        import random
        rng = random.Random(20260924)
        base = atoms()
        for _ in range(20000):
            obs = [dict(rng.choice(base), id=f"V-{i:03d}") for i in rng.sample(range(1, 100), rng.randint(4, 10))]
            self.assertEqual(gate.decide(obs), expected(obs), obs)

    def test_nothing_unverified_turns_claims_green(self):
        for obs in combos(2):
            if any(o["outcome"] in ("unverified", "error") for o in obs):
                self.assertEqual(gate.decide(obs)["claims"], "failure", obs)

    def test_a_proposed_claim_never_decides_verify(self):
        for obs in combos(3):
            rest = [o for o in obs if o["origin"] != "proposed"]
            self.assertEqual(gate.decide(obs)["verify"], gate.decide(rest)["verify"], obs)

    def test_verify_never_passes_without_something_blocking(self):
        for obs in combos(2):
            if not any(o["class"] in BLOCKING for o in obs):
                self.assertEqual(gate.decide(obs)["verify"], "failure", obs)

    def test_a_false_claim_blocks_whatever_its_class(self):
        for klass in CLASSES:
            obs = [{"id": "V-001", "origin": "claimed", "class": klass, "outcome": "fail"},
                   {"id": "V-002", "origin": "implicit", "class": "required", "outcome": "pass"}]
            d = gate.decide(obs)
            self.assertEqual((d["verify"], d["rule_id"], d["deny_type"]),
                             ("failure", "any-fail", "terminal" if klass == "security" else "correctable"))

    def test_because_follows_the_order_of_the_obligations(self):
        # the enumeration numbers ids in list order, so it cannot tell manifest order from sorted order; reversing
        # the ids can
        for obs in combos(2):
            renamed = [dict(o, id=f"V-{len(obs) - i:03d}") for i, o in enumerate(obs)]
            because = gate.decide(renamed)["because"]
            order = [o["id"] for o in renamed]
            self.assertEqual(because, [x for x in order if x in because], renamed)
            self.assertEqual(because, expected(renamed)["because"], renamed)

    def test_a_person_needed_elsewhere_makes_a_failure_terminal_and_is_named(self):
        # a workflow change awaiting approval, and CI red: the agent must not be told it can fix this alone
        obs = [{"id": "V-001", "origin": "implicit", "class": "security", "outcome": "unverified"},
               {"id": "V-002", "origin": "implicit", "class": "required", "outcome": "fail",
                "deny_type": "correctable"}]
        d = gate.decide(obs)
        self.assertEqual((d["rule_id"], d["deny_type"], d["because"]), ("any-fail", "terminal", ["V-001", "V-002"]))


class ATableMissingProposedFailures(unittest.TestCase):
    """A table whose third row is "any implicit advisory obligation fail, or any advisory obligation unverified or
    error" misses a combination the enumeration finds: a proposed claim that a resolver found false. No row matches
    it, so such a table is not total."""

    @staticmethod
    def original_rows(obs):
        rows = [
            any(failing_blocker(o) for o in obs),
            any(person_blocker(o) for o in obs),
            any((o["origin"] == "implicit" and o["class"] == "advisory" and o["outcome"] == "fail")
                or (o["class"] == "advisory" and o["outcome"] in ("unverified", "error")) for o in obs),
            all(o["outcome"] == "pass" for o in obs),
        ]
        return [i for i, hit in enumerate(rows, 1) if hit]

    def test_the_gap(self):
        gap = [{"id": "V-001", "origin": "proposed", "class": "advisory", "outcome": "fail"}]
        self.assertEqual(self.original_rows(gap), [])

    def test_it_was_the_only_gap(self):
        missed = [obs for obs in combos(2) if obs and not self.original_rows(obs)]
        self.assertTrue(missed)
        for obs in missed:
            self.assertTrue(any(o["origin"] == "proposed" and o["outcome"] == "fail" for o in obs), obs)


class EveryRuleIsLoadBearing(unittest.TestCase):
    """Deleting any rule must change the decision on at least one combination."""

    def test_deleting_each_rule_changes_a_decision(self):
        cases = list(combos(2))
        baseline = [gate.decide(obs) for obs in cases]
        for i, rule in enumerate(gate.RULES):
            rules = gate.RULES[:i] + gate.RULES[i + 1:]
            changed = 0
            for obs, want in zip(cases, baseline):
                try:
                    got = gate.decide(obs, rules=rules)
                except gate.GateError:
                    got = None
                changed += got != want
            self.assertGreater(changed, 0, f"deleting {rule[0]} changed nothing")


if __name__ == "__main__":
    unittest.main()
