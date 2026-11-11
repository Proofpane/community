import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import unittest

VERIFY = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
CORE_VECTORS = os.path.normpath(os.path.join(VERIFY, "..", "core", "vectors", "manifest"))
CHAIN_VECTORS = os.path.normpath(os.path.join(VERIFY, "..", "core", "vectors", "chain"))
sys.path.insert(0, VERIFY)

from proofpane_verify.__main__ import main  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ManifestCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def unwrap(self, vector_path):
        with open(vector_path, encoding="utf-8") as fh:
            v = json.load(fh)
        path = os.path.join(self.tmp.name, os.path.basename(vector_path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(v["manifest"], fh)
        return path, v

    def test_valid_vectors_pass_with_their_digest(self):
        paths = sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))
        self.assertTrue(paths)
        for vp in paths:
            path, v = self.unwrap(vp)
            code, out, _ = run("manifest", path, "--expect-digest", v["manifest_digest"])
            self.assertEqual(code, 0, vp)
            self.assertIn(v["manifest_digest"], out)

    def test_invalid_vectors_fail(self):
        paths = sorted(glob.glob(os.path.join(CORE_VECTORS, "invalid", "*.json")))
        self.assertTrue(paths)
        for vp in paths:
            path, v = self.unwrap(vp)
            code, _, err = run("manifest", path)
            self.assertEqual(code, 1, vp)
            self.assertIn(f"[{v['rejected_by']}] ", err, vp)

    def test_an_empty_expected_digest_through_the_api_is_a_mismatch(self):
        # the command line refuses "" before this is reached; a caller of the function gets a mismatch, not a pass
        from proofpane_verify.__main__ import check_manifest
        path, _ = self.unwrap(sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))[0])
        self.assertFalse(check_manifest(path, expect_digest="")["ok"])

    def test_wrong_expected_digest_fails(self):
        path, _ = self.unwrap(sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))[0])
        code, _, err = run("manifest", path, "--expect-digest", "sha256:" + "0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("digest: expected", err)

    def test_duplicate_keys_refused(self):
        path = os.path.join(self.tmp.name, "dup.json")
        with open(path, "w") as fh:
            fh.write('{"format": 1, "format": 2}')
        code, _, err = run("manifest", path)
        self.assertEqual(code, 1)
        self.assertIn("duplicate object key", err)

    def test_check_mirrors_the_named_check(self):
        vectors = {os.path.basename(p)[:-5]: p for p in glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json"))}
        path, _ = self.unwrap(vectors["all-pass"])
        self.assertEqual(run("manifest", path, "--check", "verify")[0], 0)
        self.assertEqual(run("manifest", path, "--check", "claims")[0], 0)
        path, _ = self.unwrap(vectors["false-claim"])
        code, _, err = run("manifest", path, "--check", "verify")
        self.assertEqual(code, 1)
        self.assertIn("proofpane/verify: failure (rule any-fail", err)
        # without --check a well-formed failing manifest is still a valid manifest
        self.assertEqual(run("manifest", path)[0], 0)

    def test_deep_nesting_is_a_clean_failure(self):
        path = os.path.join(self.tmp.name, "deep.json")
        with open(path, "w") as fh:
            fh.write("[" * 100000 + "]" * 100000)
        code, _, err = run("manifest", path)
        self.assertEqual(code, 1)
        self.assertIn("nested deeper", err)

    def test_json_reports_the_rederived_decision_not_the_copy(self):
        vectors = {os.path.basename(p)[:-5]: p for p in glob.glob(os.path.join(CORE_VECTORS, "invalid", "*.json"))}
        path, v = self.unwrap(vectors["decision-rederived"])
        code, out, _ = run("manifest", path, "--json")
        result = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(result["claimed_decision"]["verify"], "failure")  # what the tampered copy says
        self.assertEqual(result["decision"]["verify"], "success")  # what the gate gives
        path, _ = self.unwrap(vectors["format"])
        self.assertIsNone(json.loads(run("manifest", path, "--json")[1])["decision"])  # invalid: nothing vouched

    def test_json_output(self):
        path, v = self.unwrap(sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))[0])
        code, out, _ = run("manifest", path, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["digest"], v["manifest_digest"])

    def test_json_output_is_ascii_whatever_the_manifest_holds(self):
        # a claimed decision short enough to be shown as it is: what it holds outside ASCII is escaped
        with open(sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))[0], encoding="utf-8") as fh:
            m = json.load(fh)["manifest"]
        m["decision"]["rule_id"] = "r\u00e9gle \u202e"
        path = os.path.join(self.tmp.name, "claimed.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False)
        code, out, _ = run("manifest", path, "--json")
        self.assertEqual(code, 1)
        self.assertTrue(out.isascii())
        self.assertEqual(json.loads(out)["claimed_decision"]["rule_id"], "r\u00e9gle \u202e")

    def test_a_hostile_file_cannot_make_the_output_long_or_steer_a_terminal(self):
        with open(sorted(glob.glob(os.path.join(CORE_VECTORS, "valid", "*.json")))[0], encoding="utf-8") as fh:
            m = json.load(fh)["manifest"]
        m["obligations"] = [dict(m["obligations"][0], id=f"bad {i}", claim={"text": "x" * 20000, "location": "?"})
                            for i in range(300)]
        m["decision"]["rule_id"] = "\u202e\u009b2J" * 5000  # right-to-left override, a C1 control, and bulk
        path = os.path.join(self.tmp.name, "hostile.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False)
        code, out, err = run("manifest", path)
        self.assertEqual(code, 1)
        lines = err.splitlines()
        self.assertEqual(len(lines), 51)
        self.assertRegex(lines[-1], r"^FAIL and [0-9]+ more problems$")
        self.assertLess(len(err), 50 * 400)
        code, out, _ = run("manifest", path, "--json")
        result = json.loads(out)
        self.assertEqual(len(result["errors"]), 50)
        self.assertGreater(result["errors_not_shown"], 0)
        self.assertTrue(out.isascii())
        # the manifest's own decision, copied into the result, is bounded too
        self.assertLess(len(out), 60 * 400)
        self.assertEqual(len(result["claimed_decision"]), 2000)
        self.assertGreater(result["claimed_decision_characters"], 2000)


class ChainCommand(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(CHAIN_VECTORS, "index.json"), encoding="utf-8") as fh:
            self.index = {e["file"]: e for e in json.load(fh)}

    def path(self, name):
        return os.path.join(CHAIN_VECTORS, name)

    def test_valid_chains_pass_with_their_head(self):
        for name, e in self.index.items():
            if not e["valid"]:
                continue
            code, out, err = run("chain", self.path(name), "--start-seq", str(e["start_seq"]), "--start-prev",
                                 e["start_prev"], "--expect-head", e["head"], "--expect-lines", str(e["lines"]))
            self.assertEqual(code, 0, (name, err))
            self.assertIn(e["head"], out)

    def test_invalid_chains_fail_naming_the_rule(self):
        for name, e in self.index.items():
            if e["valid"] or "max_line_bytes" in e:  # the size vector needs a reduced limit the CLI does not take
                continue
            code, _, err = run("chain", self.path(name))
            self.assertEqual(code, 1, name)
            self.assertIn(f"[{e['rejected_by']}] line {e['line']}:", err, name)

    def test_a_wrong_head_or_count_fails(self):
        e = self.index["three.jsonl"]
        self.assertEqual(run("chain", self.path("three.jsonl"), "--expect-head", "sha256:" + "1" * 64)[0], 1)
        self.assertEqual(run("chain", self.path("three.jsonl"), "--expect-lines", str(e["lines"] + 1))[0], 1)

    def test_an_empty_or_malformed_expected_value_is_refused_not_ignored(self):
        # an unset shell variable passes "" and must not switch the comparison off
        e = self.index["three.jsonl"]
        upper = "sha256:" + e["head"][7:].upper()  # the prefix as written, the hex in upper case
        for argv in (("chain", self.path("three.jsonl"), "--expect-head", ""),
                     ("chain", self.path("three.jsonl"), "--expect-head", upper),
                     ("chain", self.path("three.jsonl"), "--start-prev", upper),
                     ("chain", self.path("three.jsonl"), "--expect-head", e["head"] + "0"),
                     ("manifest", self.path("three.jsonl"), "--expect-digest", ""),
                     ("manifest", self.path("three.jsonl"), "--expect-digest", upper)):
            with self.assertRaises(SystemExit) as cm:
                run(*argv)
            self.assertEqual(cm.exception.code, 2, argv)

    def test_an_empty_expected_head_through_the_api_is_a_mismatch(self):
        from proofpane_verify.__main__ import check_chain
        self.assertFalse(check_chain(self.path("three.jsonl"), expect_head="")["ok"])

    def test_a_window_needs_its_starting_point(self):
        e = self.index["window.jsonl"]
        self.assertEqual(run("chain", self.path("window.jsonl"))[0], 1)
        self.assertEqual(run("chain", self.path("window.jsonl"), "--start-seq", str(e["start_seq"]),
                             "--start-prev", e["start_prev"])[0], 0)

    def test_bad_arguments_and_missing_files_are_clean_failures(self):
        with self.assertRaises(SystemExit) as cm:  # argparse refuses it before anything is read
            run("chain", self.path("three.jsonl"), "--start-prev", "nonsense")
        self.assertEqual(cm.exception.code, 2)
        code, _, err = run("chain", self.path("no-such-file.jsonl"))
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)

    def test_json_output(self):
        e = self.index["three.jsonl"]
        code, out, _ = run("chain", self.path("three.jsonl"), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "lines": e["lines"], "head": e["head"],
                                           "next_seq": e["next_seq"], "errors": []})

    def test_json_output_is_ascii_whatever_the_file_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "keys.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"\u00e9t\u00e9\u202e":1}\n')
            code, out, _ = run("chain", path, "--json")
        self.assertEqual(code, 1)
        self.assertTrue(out.isascii())
        self.assertIn("\u00e9t\u00e9", json.loads(out)["errors"][0])


if __name__ == "__main__":
    unittest.main()
