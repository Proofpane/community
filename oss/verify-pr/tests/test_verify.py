"""End to end: bundle in, Verification Manifest v0 out, and the manifest re-derives under proofpane-core."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verify_pr import dlp  # noqa: E402
from verify_pr.config import load_config  # noqa: E402
from verify_pr.verify import claim_counts, ruleset_fingerprint, verify  # noqa: E402
from proofpane_core import manifest  # noqa: E402  (path set up by verify_pr)

HEAD, BASE = "a" * 40, "b" * 40
CHECK = {"name": "test", "app": "github-actions", "status": "completed", "conclusion": "success"}


def bundle(**kw):
    b = {"repo": "o/r", "number": 7, "title": "", "body": "", "base_sha": BASE, "head_sha": HEAD,
         "files": [], "check_runs": [CHECK], "reviews": [], "issues": {}}
    b.update(kw)
    return b


def cfg(**kw):
    return load_config(overrides={"expected_checks": [{"name": "test", "app": "github-actions", "kind": "test"}],
                                  **kw})


class CommandLine(unittest.TestCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        from verify_pr.__main__ import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_summarize_records_a_bad_bundle_and_carries_on(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            good = bundle(body="All tests pass.")
            bad = dict(good, number=8)
            del bad["repo"]
            for name, b in (("good.json", good), ("bad.json", bad)):
                with open(os.path.join(tmp, name), "w") as fh:
                    json.dump(b, fh)
            code, out, _ = self.run_cli("summarize", tmp)
        summary = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((summary["total"]["prs"], summary["total"]["bundles_not_verified"]), (1, 1))
        self.assertEqual(summary["not_verified"][0]["path"], "bad.json")

    def test_run_fails_cleanly_on_a_bad_bundle(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.json")
            with open(path, "w") as fh:
                json.dump(bundle(head_sha="x"), fh)
            code, _, err = self.run_cli("run", path)
        self.assertEqual(code, 2)
        self.assertIn("cannot verify", err)

    def write_bundles(self, tmp, bundles):
        import json
        for name, b in bundles.items():
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(b if isinstance(b, str) else json.dumps(b))

    def test_summarize_carries_on_past_any_kind_of_bad_file(self):
        import json
        import tempfile
        good = bundle(body="All tests pass.")
        files = {"a.json": good, "b.json": dict(good, number=8, files=[{"path": "x.py", "patch": 5}]),
                 "c.json": "[" * 100000 + "]" * 100000, "d.json": dict(good, number=9),
                 "e.json": {"repo": "o/r", "title": "not a bundle"}}
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, files)
            code, out, _ = self.run_cli("summarize", tmp)
        summary = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((summary["total"]["prs"], summary["total"]["bundles_not_verified"],
                          summary["total"]["files_skipped"]), (2, 2, 1))
        self.assertEqual(sorted(e["path"] for e in summary["not_verified"]), ["b.json", "c.json"])
        self.assertEqual(summary["skipped"][0]["path"], "e.json")

    def test_summarize_records_an_unexpected_error_too(self):
        import json
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": bundle(body="All tests pass.")})
            with mock.patch("verify_pr.__main__.verify", side_effect=RuntimeError("boom")):
                code, out, _ = self.run_cli("summarize", tmp)
        self.assertEqual((code, json.loads(out)["not_verified"][0]["error"]), (0, "RuntimeError: boom"))

    def test_run_exits_2_on_anything_it_cannot_verify(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            no_patch = {"path": "a.sql", "status": "modified", "patch": None}
            self.write_bundles(tmp, {"list.json": "[]", "deep.json": "[" * 100000 + "]" * 100000,
                                     "shape.json": bundle(files=[5]), "ok.json": bundle(),
                                     "count.json": bundle(files=[dict(no_patch, additions="3")]),
                                     "negative.json": bundle(files=[dict(no_patch, additions=-1)])})
            for name in ("list.json", "deep.json", "shape.json", "count.json", "negative.json"):
                code, _, err = self.run_cli("run", os.path.join(tmp, name))
                self.assertEqual(code, 2, name)
                self.assertIn("cannot verify", err, name)
                self.assertNotIn("Traceback", err, name)
                if name == "shape.json":  # the message says what is wrong, not only that something is
                    self.assertIn("files must be a list of objects", err)
                if name in ("count.json", "negative.json"):
                    self.assertIn("files[0].additions must be a non-negative integer or null", err, name)
            with mock.patch("verify_pr.__main__.verify", side_effect=RuntimeError("boom")):
                code, _, err = self.run_cli("run", os.path.join(tmp, "ok.json"))
            self.assertEqual((code, "RuntimeError: boom" in err), (2, True))
            # whatever an exception says, the error is one line with nothing a terminal would act on
            with mock.patch("verify_pr.__main__.verify", side_effect=RuntimeError("a\nb\x1b[31mc\rd")):
                code, _, err = self.run_cli("run", os.path.join(tmp, "ok.json"))
            self.assertEqual((code, err.count("\n"), "\x1b" in err, "\r" in err), (2, 1, False, False))

    def test_an_error_message_does_not_repeat_what_it_quotes(self):
        import tempfile
        token = "ghp_" + "A1b2C3d4" * 5
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": bundle(repo=token)})  # the manifest refuses it, quoting the value
            code, _, err = self.run_cli("run", os.path.join(tmp, "a.json"))
        self.assertEqual(code, 2)
        self.assertNotIn(token, err)

    def test_a_bundle_with_a_repeated_key_is_not_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": '{"title": "x", "title": "All tests pass."}'})
            code, _, err = self.run_cli("run", os.path.join(tmp, "a.json"))
        self.assertEqual(code, 2)
        self.assertIn("duplicate object key", err)

    def test_the_summary_does_not_repeat_a_secret_in_a_title(self):
        import json
        import tempfile
        token = "ghp_" + "A1b2C3d4" * 5
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": bundle(title=f"Rotate {token}")})
            code, out, _ = self.run_cli("summarize", tmp)
        self.assertEqual(code, 0)
        self.assertNotIn(token, out)

    def test_summarize_reads_hidden_files_and_quotes_nothing_from_an_error(self):
        import json
        import tempfile
        token = "ghp_" + "A1b2C3d4" * 5
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".cache"))
            self.write_bundles(tmp, {"a.json": bundle(body="All tests pass."), ".b.json": dict(bundle(), number=8),
                                     os.path.join(".cache", "c.json"): dict(bundle(), number=9),
                                     "d.json": dict(bundle(), number=10, repo=token)})
            code, out, _ = self.run_cli("summarize", tmp)
        summary = json.loads(out)
        self.assertEqual((code, summary["total"]["prs"], summary["total"]["bundles_not_verified"]), (0, 3, 1))
        self.assertNotIn(token, out)

    def test_an_error_is_one_line_whatever_the_bundle_holds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": '{"x\\nsecond line\\u001b[31m": 1, "\\ud800": 2}',
                                     "b.json": bundle(issues={"12": "yes"})})
            for name, expect in (("a.json", "cannot verify"), ("b.json", "issues must be an object")):
                code, _, err = self.run_cli("run", os.path.join(tmp, name))
                self.assertEqual(code, 2, name)
                self.assertIn(expect, err, name)
                self.assertEqual(err.count("\n"), 1, err)  # one line: the message
                self.assertNotIn("\x1b", err, name)


    def test_an_error_is_redacted_before_it_is_cut(self):
        # an address across the 500th character: cut first, only its start would be left, which no rule recognises
        import tempfile
        from unittest import mock
        message = "x" * 470 + " jane.doe@corp-mail.io has the rest"
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"ok.json": bundle(body="All tests pass.")})
            with mock.patch("verify_pr.__main__.verify", side_effect=RuntimeError(message)):
                code, _, err = self.run_cli("run", os.path.join(tmp, "ok.json"))
        self.assertEqual(code, 2)
        self.assertNotIn("jane.doe", err)

    def test_an_error_cut_to_one_line_is_checked_again(self):
        # the cut at 500 characters ends this message on an address it did not hold whole
        import tempfile
        from unittest import mock
        message = "x" * (497 - len("RuntimeError: ") - len(" jane@corp.io")) + " jane@corp.io1 and more"
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"ok.json": bundle(body="All tests pass.")})
            with mock.patch("verify_pr.__main__.verify", side_effect=RuntimeError(message)):
                code, _, err = self.run_cli("run", os.path.join(tmp, "ok.json"))
        self.assertEqual(code, 2)
        self.assertNotIn("jane@corp.io", err)

    def test_what_is_printed_cannot_act_on_a_terminal_or_reorder_text(self):
        import json
        import tempfile
        body = "All tests pass \u202eevil\u202c and \u009b2J and a\u2028b, \u4e2d\u6587 kept."
        with tempfile.TemporaryDirectory() as tmp:
            self.write_bundles(tmp, {"a.json": bundle(body=body)})
            for argv in (("run", os.path.join(tmp, "a.json")), ("summarize", tmp)):
                code, out, _ = self.run_cli(*argv)
                self.assertEqual(code, 0)
                for ch in ("\u202e", "\u202c", "\u009b", "\u2028"):
                    self.assertNotIn(ch, out, (argv[0], hex(ord(ch))))
                self.assertIn("\u4e2d\u6587", out)  # ordinary text stays readable
                json.loads(out)
        # every character the output escapes, each on its own
        for ch in ("\x7f", "\x85", "\x9f", "\u061c", "\u200e", "\u200f", "\u2028", "\u2029",
                   "\u202a", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"):
            with tempfile.TemporaryDirectory() as tmp:
                self.write_bundles(tmp, {"a.json": bundle(body=f"All tests pass {ch} here.")})
                for argv in (("run", os.path.join(tmp, "a.json")), ("summarize", tmp)):
                    code, out, _ = self.run_cli(*argv)
                    self.assertEqual(code, 0)
                    self.assertNotIn(ch, out, (argv[0], hex(ord(ch))))
                    self.assertIn(f"\\u{ord(ch):04x}", out, (argv[0], hex(ord(ch))))


class RoundFive(unittest.TestCase):
    def test_a_green_test_check_is_held_back_for_the_weakening_checks_own_reason(self):
        tests_hidden = {"path": "tests/test_a.py", "status": "modified", "patch": None, "additions": 3}
        removed = {"path": "tests/test_a.py", "status": "removed", "patch": None, "additions": 0}
        for files, extra, reason in (
                ([tests_hidden], {}, "1 test file(s) are not in the bundle whole"),
                ([], {"changed_files": 3100}, "3100 changed file(s) are not listed in the bundle"),
                ([removed], {}, "tests deleted, skipped or reconfigured")):
            m = verify(bundle(body="All tests pass.", files=files, **extra), cfg(), issued_at="2026-09-25T00:00:00Z")
            greens = [o for o in m["obligations"] if o["resolver"] == "ci.checks_green"]
            self.assertEqual(len(greens), 2)
            for o in greens:
                self.assertEqual(o["outcome"], "unverified")
                self.assertTrue(o["reason"].startswith("diff.tests_not_weakened is unverified: " + reason),
                                o["reason"])

    def test_a_bundle_with_malformed_issue_errors_is_refused(self):
        for value in ([], {"5": 403}, None):
            with self.assertRaises(ValueError):
                verify(bundle(issue_errors=value), cfg(), issued_at="2026-09-25T00:00:00Z")


class ReadByTheOfflineVerifier(unittest.TestCase):
    """What verify-pr writes, proofpane-verify accepts: the two packages share one gate and one manifest format."""

    def test_round_trip(self):
        import contextlib
        import io
        import json
        import tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "verify"))
        from proofpane_verify.__main__ import main
        m = verify(bundle(title="Add index", body="No schema changes.",
                          files=[{"path": "migrations/0001.sql", "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"}]),
                   cfg())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                ok = main(["manifest", path, "--expect-digest", manifest.manifest_digest(m)])
                blocked = main(["manifest", path, "--check", "verify"])
        self.assertEqual((ok, blocked), (0, 1), err.getvalue())


class Manifests(unittest.TestCase):
    def test_false_schema_claim(self):
        b = bundle(title="Add index", body="No schema changes. All tests pass.",
                   files=[{"path": "migrations/0042_add_index.sql", "status": "added",
                           "patch": "@@ -0,0 +1 @@\n+CREATE INDEX i ON t(c);\n"}])
        m = verify(b, cfg(), issued_at="2026-09-24T00:00:00Z")
        claimed = {o["resolver"]: o["outcome"] for o in m["obligations"] if o["origin"] == "claimed"}
        self.assertEqual(claimed, {"diff.no_schema_change": "fail", "ci.checks_green": "pass"})
        d = m["decision"]
        self.assertEqual((d["verify"], d["rule_id"], d["deny_type"]), ("failure", "any-fail", "correctable"))
        self.assertEqual(manifest.rederive(m), [])

    def test_tests_pass_is_not_vouched_for_when_a_test_was_deleted(self):
        b = bundle(body="All tests pass.", files=[{"path": "tests/test_flaky.py", "status": "removed", "patch": ""}])
        m = verify(b, cfg())
        claimed = [o for o in m["obligations"] if o["origin"] == "claimed"]
        self.assertEqual(claimed[0]["outcome"], "unverified")
        self.assertEqual((m["decision"]["verify"], m["decision"]["rule_id"]), ("failure", "needs-person"))
        self.assertEqual(manifest.rederive(m), [])

    def test_red_ci_alone_is_correctable(self):
        red = dict(CHECK, conclusion="failure")
        m = verify(bundle(check_runs=[red]), cfg())
        ci = [o for o in m["obligations"] if o["resolver"] == "ci.checks_green"][0]
        self.assertEqual((ci["outcome"], ci["deny_type"]), ("fail", "correctable"))
        self.assertEqual((m["decision"]["rule_id"], m["decision"]["deny_type"]), ("any-fail", "correctable"))

    def test_secret_is_terminal(self):
        b = bundle(files=[{"path": "app.py", "status": "modified",
                           "patch": "@@ -1 +1 @@\n+key = 'AKIAABCDEFGHIJKLMNOP'\n"}])
        m = verify(b, cfg())
        secrets = [o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]
        self.assertEqual((secrets["outcome"], secrets["deny_type"]), ("fail", "terminal"))
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", str(m))
        self.assertEqual(m["decision"]["deny_type"], "terminal")

    def test_clean_change_passes_both_checks(self):
        m = verify(bundle(body="Adds regression tests. All tests pass.",
                          files=[{"path": "tests/test_parser.py", "status": "modified",
                                  "patch": "@@ -1 +1,2 @@\n x\n+def test_new(): pass\n"}]), cfg())
        self.assertEqual((m["decision"]["verify"], m["decision"]["claims"]), ("success", "success"))

    def test_policy_source_is_recorded_without_local_paths(self):
        m = verify(bundle(), cfg(), cfg_source="/home/someone/configs/o__r.json")
        self.assertEqual((m["policy"]["source"], m["policy"]["path"]), ("supplied", "o__r.json"))
        self.assertEqual(verify(bundle(), cfg())["policy"]["source"], "default")

    def test_subject_digests(self):
        b = bundle(title="t", body="All tests pass.")
        m = verify(b, cfg())
        self.assertEqual(m["subject"]["claims_digest"], manifest.claims_digest("t", "All tests pass."))
        self.assertEqual(m["subject"]["pr_number"], 7)
        self.assertEqual(m["verifier"]["ruleset_fingerprint"], ruleset_fingerprint())

    def test_claim_counts(self):
        m = verify(bundle(body="No schema changes. This is fully backwards compatible."), cfg())
        self.assertEqual(claim_counts(m), {"checked_false": 0, "checked_true": 1, "could_not_decide": 0,
                                           "no_resolver": 1})

    def test_a_secret_in_the_description_is_flagged_and_never_copied(self):
        token = "ghp_" + "A1b2C3d4" * 5
        m = verify(bundle(body=f"All tests pass with GITHUB_TOKEN={token} set."), cfg())
        self.assertNotIn(token, str(m))
        secrets = [o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]
        self.assertEqual((secrets["outcome"], secrets["evidence"][0]["ref"]), ("fail", "pr_body:1"))
        claim = [o for o in m["obligations"] if o["origin"] == "claimed"][0]["claim"]["text"]
        self.assertEqual(claim, dlp.WITHHELD_SECRET)  # withheld whole, not redacted in place
        self.assertEqual((m["decision"]["verify"], m["decision"]["deny_type"]), ("failure", "terminal"))

    def test_personal_data_in_a_claim_is_redacted_but_placeholders_are_kept(self):
        m = verify(bundle(body="All tests pass for jane.doe@acme.io and you@example.com."), cfg())
        text = [o for o in m["obligations"] if o["origin"] == "claimed"][0]["claim"]["text"]
        self.assertIn("[redacted:email]", text)
        self.assertIn("you@example.com", text)
        self.assertNotIn("jane.doe@acme.io", text)

    def test_a_secret_in_the_title_alone_is_flagged(self):
        token = "ghp_" + "A1b2C3d4" * 5
        m = verify(bundle(title=f"Rotate {token}"), cfg())
        secrets = [o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]
        self.assertEqual((secrets["outcome"], secrets["evidence"][0]["ref"]), ("fail", "pr_title:1"))
        self.assertNotIn(token, str(m))

    def test_personal_data_is_redacted_in_every_kind_of_claim(self):
        # a title claim, a body claim, and a claim no resolver checks
        m = verify(bundle(title="All tests pass for 123-45-6789",
                          body="Fully backwards compatible for +1 415 555 0100. No new dependencies for "
                               "card 4111 1111 1111 1111."), cfg())
        texts = [o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"]
        self.assertEqual(len(texts), 3)
        for value in ("123-45-6789", "415 555 0100", "4111 1111 1111 1111"):
            self.assertNotIn(value, str(m))
        self.assertTrue(all("[redacted:" in t for t in texts), texts)
        self.assertNotIn(dlp.WITHHELD_PII, texts)  # a number in the next sentence is not this sentence's

    def test_issue_references_and_paths_are_sanitised_everywhere(self):
        token = "ghp_" + "A1b2C3d4" * 5
        files = [{"path": "fixtures/jane.doe@corp.io.eml", "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"},
                 {"path": "keys/AKIAIOSFODNN7EXAMPLE.txt", "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"}]
        m = verify(bundle(title="Docs only", body=f"Fixes {token}/x#12. Fixes 123-45-6789/y#3.", files=files),
                   cfg())
        text = str(m)
        for value in (token, "123-45-6789", "jane.doe@corp.io", "AKIAIOSFODNN7EXAMPLE"):
            self.assertNotIn(value, text)
        secrets = [o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]
        self.assertIn("changed path 2", [e["ref"] for e in secrets["evidence"]])
        pii = [o for o in m["obligations"] if o["resolver"] == "diff.no_pii"][0]
        self.assertEqual(pii["outcome"], "fail")

    def test_a_long_path_is_cut_not_a_reason_to_write_no_manifest(self):
        long_path = "docs/" + "d" * 1100 + ".md"
        m = verify(bundle(title="Docs only", files=[{"path": long_path, "status": "added",
                                                     "patch": "@@ -0,0 +1 @@\n+x\n"},
                                                    {"path": "src/a.py", "status": "modified",
                                                     "patch": "@@ -1 +1 @@\n+y\n"}]), cfg())
        self.assertEqual(manifest.rederive(m), [])
        refs = [e["ref"] for o in m["obligations"] for e in o["evidence"] if "ref" in e]
        self.assertTrue(all(len(r) <= manifest.MAX_EVIDENCE_STRING for r in refs))
        m = verify(bundle(files=[{"path": "tests/" + "t" * 1100 + "/test_a.py", "status": "modified",
                                  "patch": "@@ -1 +1,2 @@\n+@pytest.mark.skip\n def test_x():\n"}]), cfg())
        weakened = [o for o in m["obligations"] if o["resolver"] == "diff.tests_not_weakened"][0]
        self.assertEqual(weakened["outcome"], "unverified")
        self.assertTrue(weakened["evidence"][0]["ref"].endswith("characters]"))

    def test_a_phone_number_at_the_start_of_a_line_is_not_a_list_marker(self):
        m = verify(bundle(body="+1 415 555 0100 is the on-call number; all tests pass."), cfg())
        texts = [o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"]
        self.assertTrue(texts)
        self.assertNotIn("415 555 0100", str(m))

    def test_a_number_at_the_start_of_a_line_is_not_a_list_marker(self):
        m = verify(bundle(body="4111.1111.1111.1111 is the test card and all tests pass."), cfg())
        texts = [o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"]
        self.assertEqual(texts, ["[redacted:card-number] is the test card and all tests pass."])
        self.assertNotIn("1111.1111.1111", str(m))

    def test_what_the_first_rules_missed_is_not_copied_end_to_end(self):
        key = "AIza" + "Sy" + "Dx9Kq2Lm7Np4Rt8Vw1Yz3Bc6Df0Gh5JkQ"[:33]
        self.assertEqual(len(key), 39)
        m = verify(bundle(body="All tests pass; ask jane@corp.io--she owns CI."), cfg())
        self.assertNotIn("jane@corp.io", str(m))
        m = verify(bundle(body=f"All tests pass with {key}-- see the runbook."), cfg())
        self.assertNotIn(key, str(m))
        self.assertEqual([o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]["outcome"], "fail")

    def test_a_claim_near_a_key_is_withheld_though_its_own_sentence_holds_none(self):
        # a claim's source is its own sentence; the rest of a key can be in the next sentence or line
        body_key = "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu"
        for body in (f"Old key: -----BEGIN RSA PRIVATE KEY-----. {body_key} and all tests pass.",
                     f"Rotated:\n-----BEGIN RSA PRIVATE KEY-----\n{body_key} all tests pass\n"
                     "-----END RSA PRIVATE KEY-----",
                     "Title is clean. All tests pass. Key: " + "AKIA" + "IOSFODNN7EXAMPLE"):
            m = verify(bundle(body=body), cfg())
            texts = [o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"]
            self.assertTrue(texts, body[:30])
            self.assertEqual(set(texts), {dlp.WITHHELD_SECRET}, body[:30])
            self.assertNotIn("MIIBOgIBAAJBAKj34", str(m))
        # a key's header in the title withholds the title's claims too
        m = verify(bundle(title=f"Old key -----BEGIN RSA PRIVATE KEY-----. {body_key} all tests pass."), cfg())
        texts = [o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"]
        self.assertEqual(texts, [dlp.WITHHELD_SECRET])
        self.assertNotIn("MIIBOgIBAAJBAKj34", str(m))
        # the title and the description are judged apart
        m = verify(bundle(title="All tests pass", body="Key: " + "AKIA" + "IOSFODNN7EXAMPLE"), cfg())
        self.assertEqual([o["claim"]["text"] for o in m["obligations"] if o["origin"] == "claimed"], ["All tests pass"])

    def test_a_cut_that_ends_a_text_on_a_value_is_checked_again(self):
        # "jane@corp.io1" is not an address; cut after "io", it would be
        suffix = " [cut from 1500 characters]"
        head = "All tests pass "
        text = head + "x" * (1000 - len(suffix) - len(head) - len(" jane@corp.io")) + " jane@corp.io1 "
        text += "y" * (1500 - len(text))
        self.assertEqual(dlp.safe_copy(text), text)
        m = verify(bundle(body=text), cfg())
        self.assertNotIn("jane@corp.io", str(m))
        self.assertEqual(manifest.rederive(m), [])

    def test_a_bundle_says_how_many_files_changed_as_a_count(self):
        for bad in ("3", -1, True, 2.5):
            with self.assertRaisesRegex(ValueError, "changed_files must be a non-negative integer or null"):
                verify(bundle(changed_files=bad), cfg())
        verify(bundle(changed_files=None), cfg())

    def test_a_huge_issue_number_is_not_a_reason_to_write_no_manifest(self):
        m = verify(bundle(body="All tests pass. Fixes #" + "9" * 4301), cfg())
        self.assertEqual(manifest.rederive(m), [])

    def test_hostile_descriptions_cost_linear_time(self):
        import time
        for body in ("all " * 16000, "<!--" * 16000):
            start = time.perf_counter()
            verify(bundle(body=body), cfg())
            self.assertLess(time.perf_counter() - start, 3.0, body[:8])

    def test_a_pattern_repeated_in_one_sentence_costs_linear_time(self):
        # each match looked at the whole sentence once: 18 s for GitHub's 65,536-character limit. Four times the
        # input must cost about four times as much, not sixteen; the best of three runs, against timing noise
        import time
        from verify_pr.extract import extract

        def best(body):
            runs = []
            for _ in range(3):
                start = time.perf_counter()
                extract("", body)
                runs.append(time.perf_counter() - start)
            return min(runs)
        for pattern in ("tests pass ", "ci is green ", "fixes #1 ", "no new dependencies ", "adds tests "):
            small, large = best((pattern * 2000)[:16384]), best((pattern * 8000)[:65536])
            self.assertLess(large, 3.0, pattern)
            self.assertLess(large / max(small, 1e-4), 8, (pattern, small, large))

    def test_many_issue_references_cannot_make_a_manifest_of_any_size(self):
        import json
        body = ", ".join(f"fixes #{i}" for i in range(1, 2001))
        m = verify(bundle(body=body), cfg())
        claimed = [o for o in m["obligations"] if o["resolver"] == "issue.linked"]
        self.assertEqual(len(claimed), 20)
        self.assertTrue(all(len(o["claim"]["text"]) <= 1000 for o in claimed))
        self.assertTrue(claimed[0]["claim"]["text"].endswith(f"[cut from {len(body)} characters]"))
        self.assertLess(len(json.dumps(m)), 100000)
        self.assertEqual(manifest.rederive(m), [])

    def test_a_long_path_is_sanitised_before_it_is_cut(self):
        # cutting first would keep the start of a token and lose the end that the rule needs to see it: the path is
        # longer than the cap, and built so the cut falls 36 characters into the token, which the rule needs 40 of
        token = "ghp_" + "A1b2C3d4" * 5
        start = manifest.MAX_EVIDENCE_STRING - len(" [cut from 1039 characters]") - 36
        path = "src/" + "d" * (start - 5) + "/" + token + "/" + "y" * 30 + ".py"
        self.assertEqual((len(path), path.index(token)), (1039, start))
        self.assertGreater(len(path), manifest.MAX_EVIDENCE_STRING)
        m = verify(bundle(title="Docs only", files=[{"path": path, "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"}]),
                   cfg())
        self.assertNotIn("A1b2C3d4", str(m))

    def test_check_names_and_conclusions_are_sanitised_too(self):
        run = dict(CHECK, conclusion="failure: ask jane@corp.io")
        status = {"context": "ci/ghp_" + "A1b2C3d4" * 5, "state": "success", "creator": "bot"}
        c = cfg(expected_statuses=[{"context": status["context"], "creator": "bot"}])
        m = verify(bundle(body="All tests pass.", check_runs=[run], statuses=[status]), c)
        self.assertNotIn("jane@corp.io", str(m))
        self.assertNotIn("A1b2C3d4" * 5, str(m))

    def test_an_added_line_that_starts_with_plus_plus_is_scanned(self):
        patch = "@@ -0,0 +1,2 @@\n+x\n+++ secret AKIAIOSFODNN7EXAMPLE\n"
        m = verify(bundle(files=[{"path": "a.txt", "status": "added", "patch": patch}]), cfg())
        self.assertEqual([o for o in m["obligations"] if o["resolver"] == "diff.no_secrets"][0]["outcome"], "fail")

    def test_a_file_with_added_lines_and_no_patch_is_not_scanned_and_says_so(self):
        files = [{"path": "big.sql", "status": "modified", "patch": None, "additions": 5000},
                 {"path": "logo.png", "status": "added", "patch": None, "additions": 0},
                 {"path": "old.sql", "status": "modified", "patch": None}]  # no count: not known to add nothing
        m = verify(bundle(files=files), cfg())
        for resolver in ("diff.no_secrets", "diff.no_pii"):
            o = [o for o in m["obligations"] if o["resolver"] == resolver][0]
            self.assertEqual((o["outcome"], [e["ref"] for e in o["evidence"]]),
                             ("unverified", ["changed path 1", "changed path 3"]))

    def test_ruleset_fingerprint_covers_the_deciding_sources(self):
        from verify_pr import verify as v
        names = {name for name, _ in v._RULESET_SOURCES}
        self.assertEqual(names, {"verify_pr/extract.py", "verify_pr/resolvers.py", "verify_pr/dlp.py",
                                 "verify_pr/config.py", "verify_pr/verify.py", "proofpane_core/gate.py"})
        for name, path in v._RULESET_SOURCES:
            self.assertTrue(os.path.isfile(path), path)
            self.assertEqual(os.path.basename(path), os.path.basename(name), name)  # each name reads its own file

    def test_ruleset_fingerprint_follows_content_not_line_endings(self):
        import shutil
        import tempfile
        from verify_pr import verify as v
        with tempfile.TemporaryDirectory() as tmp:
            copies = []
            for name, path in v._RULESET_SOURCES:
                dst = os.path.join(tmp, name.replace("/", "__"))
                shutil.copyfile(path, dst)
                copies.append((name, dst))
            base = ruleset_fingerprint(copies)
            self.assertEqual(base, ruleset_fingerprint())
            for _, dst in copies:  # the same sources checked out with CRLF endings
                with open(dst, "rb") as fh:
                    data = fh.read()
                with open(dst, "wb") as fh:
                    fh.write(data.replace(b"\n", b"\r\n"))
            self.assertEqual(ruleset_fingerprint(copies), base)
            config = dict(copies)["verify_pr/config.py"]
            with open(config, "a", encoding="utf-8") as fh:
                fh.write("# an edit\n")
            self.assertNotEqual(ruleset_fingerprint(copies), base)

    def test_a_malformed_bundle_is_refused_not_written(self):
        with self.assertRaises(ValueError):
            verify(bundle(head_sha="not-a-sha"), cfg())


if __name__ == "__main__":
    unittest.main()
