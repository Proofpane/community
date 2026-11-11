import copy
import glob
import json
import os
import unittest

from _paths import CORE, VECTORS
from proofpane_core import gate, manifest  # noqa: E402
from proofpane_core.canonical import loads_strict  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return loads_strict(fh.read())


def _read_plain(path):
    # invalid vectors may hold values loads_strict refuses (that is the point), so they are read without it
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _valid():
    return sorted(glob.glob(os.path.join(VECTORS, "manifest", "valid", "*.json")))


def _invalid():
    return sorted(glob.glob(os.path.join(VECTORS, "manifest", "invalid", "*.json")))


class Vectors(unittest.TestCase):
    def test_gate_vectors_rederive(self):
        cases = _read(os.path.join(VECTORS, "gate", "cases.json"))
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            self.assertEqual(gate.decide(case["obligations"]), case["decision"], case["name"])

    def test_every_gate_rule_decides_some_published_vector(self):
        # the load-bearing-coverage idea applied to this vector set: deleting any rule changes a published outcome
        cases = _read(os.path.join(VECTORS, "gate", "cases.json"))
        for i, rule in enumerate(gate.RULES):
            rules = gate.RULES[:i] + gate.RULES[i + 1:]
            changed = []
            for case in cases:
                try:
                    got = gate.decide(case["obligations"], rules=rules)
                except gate.GateError:
                    got = None
                if got != case["decision"]:
                    changed.append(case["name"])
            self.assertTrue(changed, f"no published vector depends on {rule[0]}")

    def test_valid_manifests(self):
        paths = _valid()
        self.assertGreaterEqual(len(paths), 5)
        for path in paths:
            v = _read_plain(path)
            self.assertEqual(manifest.rederive(v["manifest"]), [], path)
            self.assertEqual(manifest.manifest_digest(v["manifest"]), v["manifest_digest"], path)

    def test_invalid_manifests_are_rejected_by_the_recorded_rule(self):
        paths = _invalid()
        self.assertTrue(paths)
        for path in paths:
            v = _read_plain(path)
            errors = manifest.rederive(v["manifest"])
            # exactly the recorded rule: each vector pins one check, so deleting any check fails some vector
            self.assertEqual(sorted({e[1:e.index("]")] for e in errors}), [v["rejected_by"]], f"{path}: {errors}")

    def test_every_validation_rule_rejects_a_published_vector(self):
        # the load-bearing-coverage idea for the validator: a check nothing depends on could be deleted unnoticed
        covered = {_read_plain(p)["rejected_by"] for p in _invalid()}
        self.assertEqual(sorted(set(manifest.VALIDATION_RULES) - covered), [])

    def test_every_reported_problem_names_a_declared_rule(self):
        for path in _invalid():
            for e in manifest.rederive(_read_plain(path)["manifest"]):
                rule = e[1:e.index("]")]
                self.assertIn(rule, manifest.VALIDATION_RULES, e)


class Normalisation(unittest.TestCase):
    def test_integral_floats_are_the_same_manifest(self):
        vectors = {os.path.basename(p)[:-5]: _read_plain(p) for p in _valid()}
        self.assertEqual(vectors["integral-floats"]["manifest_digest"], vectors["all-pass"]["manifest_digest"])

    def test_trailing_newline_is_not_well_formed(self):
        m = _read(_valid()[0])["manifest"]
        m["obligations"][0]["id"] += "\n"
        self.assertTrue(any(e.startswith("[obligation-id]") for e in manifest.rederive(m)))

    def test_unhashable_id_is_a_clean_error(self):
        m = _read(_valid()[0])["manifest"]
        m["obligations"][0]["id"] = []
        self.assertTrue(any(e.startswith("[obligation-id]") for e in manifest.rederive(m)))

    def test_rederived_decision(self):
        v = _read_plain(_valid()[0])
        self.assertEqual(manifest.rederived_decision(v["manifest"]), v["manifest"]["decision"])
        broken = copy.deepcopy(v["manifest"])
        broken["format"] = "x"
        self.assertIsNone(manifest.rederived_decision(broken))

    def test_an_integer_beyond_the_exact_range_is_not_an_integer_field(self):
        # 2**53 and 1e20 read back as doubles, so a field that must be an exact integer refuses them
        m = _read(_valid()[0])["manifest"]
        m["subject"]["pr_number"] = 2.0 ** 53
        self.assertEqual([e[:19] for e in manifest.validate(m)], ["[subject-pr-number]"])
        m = _read(_valid()[0])["manifest"]
        m["unrecognised_sentences"] = 1e20
        self.assertEqual([e[:14] for e in manifest.validate(m)], ["[unrecognised]"])

    def test_messages_do_not_repeat_what_they_quote_at_length(self):
        # a message quotes values from the file being checked, which can be any size
        m = _read(_valid()[0])["manifest"]
        m["k" * 100000] = 1
        m["obligations"][0]["evidence"] = [{"kind": "note", "x" * 100000: 1}]
        m["subject"]["repo"] = "r" * 100000
        for e in manifest.validate(m):
            self.assertLess(len(e), 300, e[:80])

    def test_messages_escape_what_they_quote(self):
        hostile = "\rOK sha256:" + "a" * 64 + "\x1b[8m"

        def as_evidence_key(m):
            m["obligations"][0]["evidence"] = [{"kind": "note", hostile: 1}]

        def as_value(m):
            m["subject"]["repo"] = hostile

        for plant, where in ((as_evidence_key, "[evidence-keys]"), (as_value, "[subject-repo]")):
            m = _read(_valid()[0])["manifest"]
            plant(m)
            errors = manifest.validate(m)
            self.assertTrue(any(e.startswith(where) for e in errors), errors)
            for e in errors:
                self.assertNotIn("\r", e)
                self.assertNotIn("\x1b", e)

    def test_build_and_validate_agree_on_integral_floats(self):
        v = _read_plain(os.path.join(VECTORS, "manifest", "valid", "all-pass.json"))
        obligations = copy.deepcopy(v["manifest"]["obligations"])
        obligations[0]["evidence"] = [{"kind": "note", "line": 3.0}]
        m = manifest.build(v["manifest"]["subject"], v["manifest"]["verifier"], v["manifest"]["policy"], obligations,
                           2, "2026-09-24T00:00:00Z")
        self.assertEqual(m["obligations"][0]["evidence"], [{"kind": "note", "line": 3}])
        self.assertEqual(manifest.validate(m), [])


class Build(unittest.TestCase):
    def setUp(self):
        self.base = _read_plain(_valid()[0])["manifest"]

    def test_build_fills_the_decision(self):
        m = manifest.build(self.base["subject"], self.base["verifier"], self.base["policy"],
                           self.base["obligations"], 0, "2026-09-24T00:00:00Z")
        self.assertEqual(m["decision"], gate.decide(m["obligations"]))

    def test_build_refuses_an_invalid_manifest(self):
        bad = copy.deepcopy(self.base["obligations"])
        bad[0]["outcome"] = "unverified"  # without a reason
        for obligations in (bad, [{"id": "V-001"}], "abc", [None], []):
            with self.assertRaises(ValueError, msg=repr(obligations)):
                manifest.build(self.base["subject"], self.base["verifier"], self.base["policy"], obligations, 0,
                               "2026-09-24T00:00:00Z")

    def test_digest_changes_with_any_field(self):
        m = copy.deepcopy(self.base)
        before = manifest.manifest_digest(m)
        m["unrecognised_sentences"] += 1
        self.assertNotEqual(manifest.manifest_digest(m), before)

    def test_claims_digest_tracks_the_description(self):
        self.assertNotEqual(manifest.claims_digest("t", "a"), manifest.claims_digest("t", "b"))
        self.assertEqual(manifest.claims_digest(None, None), manifest.claims_digest("", ""))

    def test_diff_digest_ignores_file_order(self):
        a = [{"path": "a", "status": "modified", "patch": "x"}, {"path": "b", "status": "added", "patch": "y"}]
        self.assertEqual(manifest.diff_digest(a), manifest.diff_digest(list(reversed(a))))


class SchemaAgreesWithTheValidator(unittest.TestCase):
    def setUp(self):
        self.schema = _read(os.path.join(CORE, "schema", "verification-manifest-0.schema.json"))
        self.ob = self.schema["$defs"]["obligation"]["properties"]

    def test_closed_sets(self):
        self.assertEqual(self.schema["properties"]["format"]["const"], manifest.FORMAT)
        self.assertEqual(tuple(self.ob["origin"]["enum"]), manifest.ORIGINS)
        self.assertEqual(tuple(self.ob["class"]["enum"]), manifest.CLASSES)
        self.assertEqual(tuple(self.ob["method"]["enum"]), manifest.METHODS)
        self.assertEqual(tuple(self.ob["outcome"]["enum"]), manifest.OUTCOMES)
        self.assertEqual(tuple(self.ob["deny_type"]["enum"]), manifest.DENY_TYPES)
        decision = self.schema["properties"]["decision"]["properties"]
        self.assertEqual(tuple(decision["rule_id"]["enum"]), gate.RULE_IDS)
        self.assertEqual(tuple(self.schema["properties"]["policy"]["properties"]["source"]["enum"]),
                         manifest.POLICY_SOURCES)

    def test_evidence_keys(self):
        items = self.ob["evidence"]["items"]
        self.assertEqual(tuple(items["properties"]), manifest.EVIDENCE_KEYS)
        self.assertFalse(items["additionalProperties"])
        self.assertEqual(self.schema["$defs"]["evidence_value"]["oneOf"][0]["maxLength"], manifest.MAX_EVIDENCE_STRING)

    def test_required_keys(self):
        self.assertEqual(tuple(self.schema["required"]), manifest._TOP)
        self.assertEqual(tuple(self.schema["$defs"]["obligation"]["required"]), manifest._OBLIGATION_REQUIRED)
        self.assertEqual(set(self.ob), set(manifest._OBLIGATION))


if __name__ == "__main__":
    unittest.main()
