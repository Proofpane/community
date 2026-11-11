import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verify_pr.extract import extract  # noqa: E402


def resolvers_of(body, title=""):
    claims, _ = extract(title, body)
    return [(c["resolver"], c["pattern"]) for c in claims]


class PositiveClaims(unittest.TestCase):
    def test_tests_pass(self):
        self.assertIn(("ci.checks_green", "tests_pass"), resolvers_of("All tests pass."))

    def test_ticked_checkbox_asserts(self):
        self.assertIn(("ci.checks_green", "tests_pass"), resolvers_of("- [x] Tests pass"))

    def test_pasted_run_summary_is_a_report_not_a_ci_claim(self):
        self.assertIn((None, "reported_test_run"), resolvers_of("Result: 120 passed, 0 failed."))
        self.assertIn((None, "reported_test_run"), resolvers_of("Hook regression: 100 passed, 1 skipped (e2e)."))

    def test_semicolon_keeps_the_qualifier(self):
        # the second clause discloses failures, so the sentence is not a claim that tests pass
        self.assertEqual(resolvers_of("Full backend suite: 5880 passed; the 7 failures are pre-existing on main."),
                         [])

    def test_distant_negation_does_not_block(self):
        body = ("21 new tests in `test_x.py`: validation caps, membership hit/miss with context, casefold, "
                "provenance (version bump changes fingerprint; no ids in audit payloads).")
        self.assertIn(("diff.tests_added", "tests_added"), resolvers_of(body))

    def test_near_negation_still_blocks(self):
        self.assertEqual(resolvers_of("We did not add new tests here."), [])

    def test_counted_tests_pass(self):
        self.assertIn(("ci.checks_green", "tests_pass"), resolvers_of("46 workflow/claims tests pass."))

    def test_no_schema_change(self):
        self.assertIn(("diff.no_schema_change", "no_schema"), resolvers_of("No schema changes."))

    def test_no_migrations(self):
        self.assertIn(("diff.no_schema_change", "no_schema"), resolvers_of("No migrations needed."))

    def test_no_new_dependencies(self):
        self.assertIn(("diff.no_dependency_change", "no_new_deps"), resolvers_of("No new dependencies."))

    def test_docs_only(self):
        self.assertIn(("diff.docs_only", "docs_only"), resolvers_of("Docs-only change."))

    def test_tests_added(self):
        self.assertIn(("diff.tests_added", "tests_added"), resolvers_of("Adds regression tests for the parser."))

    def test_issue(self):
        claims, _ = extract("", "Fixes #123")
        self.assertEqual(claims[0]["resolver"], "issue.linked")
        self.assertEqual(claims[0]["args"], {"issue": 123, "repo": None})

    def test_cross_repo_issue(self):
        claims, _ = extract("", "Closes acme/widgets#7")
        self.assertEqual(claims[0]["args"], {"issue": 7, "repo": "acme/widgets"})

    def test_title_is_read(self):
        claims, _ = extract("docs: README only, no code changes", "")
        self.assertIn("diff.docs_only", [c["resolver"] for c in claims])
        self.assertEqual(claims[0]["location"], "pr_title")

    def test_unresolvable_is_recognised(self):
        self.assertIn((None, "backwards_compatible"), resolvers_of("This is fully backwards compatible."))

    def test_local_tests_have_no_resolver(self):
        self.assertIn((None, "tests_pass_locally"), resolvers_of("All tests pass locally."))
        self.assertNotIn(("ci.checks_green", "tests_pass"), resolvers_of("All tests pass locally."))


class NegativeClaims(unittest.TestCase):
    def test_unticked_checkbox_asserts_nothing(self):
        self.assertEqual(resolvers_of("- [ ] Tests pass"), [])

    def test_negated(self):
        self.assertEqual(resolvers_of("Tests do not pass yet."), [])

    def test_hedged(self):
        self.assertEqual(resolvers_of("Tests should pass once CI is fixed."), [])

    def test_real_failures_block(self):
        self.assertEqual(resolvers_of("3 passed, 1 failed."), [])

    def test_no_tests_added_is_not_tests_added(self):
        self.assertNotIn("diff.tests_added", [r for r, _ in resolvers_of("No tests added.")])

    def test_code_fence_ignored(self):
        self.assertEqual(resolvers_of("```\nall tests pass\n```"), [])

    def test_html_comment_ignored(self):
        self.assertEqual(resolvers_of("<!-- confirm that all tests pass -->"), [])

    def test_an_unclosed_comment_is_left_as_text(self):
        # as the regular expression the linear scan replaced left it
        self.assertEqual(resolvers_of("All tests pass. <!-- no new dependencies"),
                         [("ci.checks_green", "tests_pass"), ("diff.no_dependency_change", "no_new_deps")])

    def test_a_marker_needs_the_space_markdown_requires(self):
        # "#12" is an issue reference, not a heading, and "4111." starts a number, not an ordered list
        for body in ("#12 all tests pass", "4111.1111 all tests pass"):
            claims, _ = extract("", body)
            self.assertEqual([c["text"] for c in claims], [body])
        claims, _ = extract("", "# All tests pass\n1. No new dependencies")
        self.assertEqual([c["text"] for c in claims], ["All tests pass", "No new dependencies"])

    def test_schema_without_change_word(self):
        self.assertEqual(resolvers_of("There is no schema validation here."), [])


class EdgeCases(unittest.TestCase):
    def test_an_issue_number_longer_than_ten_digits_is_not_a_reference(self):
        claims, _ = extract("", "Fixes #1234567890. Fixes #12345678901.")
        self.assertEqual([c["args"]["issue"] for c in claims if c["resolver"] == "issue.linked"], [1234567890])

    def test_a_break_inside_a_number_is_not_a_sentence_break(self):
        from verify_pr.extract import _sentences
        got = [s for s, _, _ in _sentences("Reach me on +44 20. 7946 0958 today. Version 2. Tests pass.", "pr_body")]
        self.assertEqual(got, ["Reach me on +44 20. 7946 0958 today.", "Version 2.", "Tests pass."])

    def test_a_line_ends_only_where_markdown_ends_one(self):
        for sep in ("\u2028", "\x0b", "\x0c", "\x85"):
            claims, _ = extract("", f"All tests{sep}pass.")
            self.assertEqual([c["pattern"] for c in claims], ["tests_pass"], repr(sep))
        for sep in ("\n", "\r\n", "\r"):
            claims, _ = extract("", f"- [ ] tests pass{sep}- [x] no schema change")
            self.assertEqual([c["pattern"] for c in claims], ["no_schema"], repr(sep))

    def test_at_most_twenty_issue_references_are_read(self):
        from verify_pr.extract import MAX_ISSUE_CLAIMS
        claims, _ = extract("", " ".join(f"Fixes #{n}." for n in range(1, 31)))
        self.assertEqual([c["args"]["issue"] for c in claims if c["resolver"] == "issue.linked"], list(range(1, 21)))
        self.assertEqual(MAX_ISSUE_CLAIMS, 20)


class Counting(unittest.TestCase):
    def test_repeats_do_not_inflate(self):
        claims, _ = extract("All tests pass", "All tests pass.\n- [x] tests pass\nCI is green.")
        tests = [c for c in claims if c["resolver"] == "ci.checks_green" and c["args"].get("tests_claim")]
        self.assertEqual(len(tests), 1)
        # "CI is green" is a different claim from "tests pass", and is kept
        self.assertEqual(len([c for c in claims if c["resolver"] == "ci.checks_green"]), 2)

    def test_unrecognised_sentences_counted(self):
        _, n = extract("", "This change reworks the parser. It is much cleaner now. Ok.")
        self.assertEqual(n, 2)


class RoundFive(unittest.TestCase):
    def test_a_sentence_can_end_before_a_digit(self):
        # only "digit." before a digit is kept together; a word's period before a digit still ends a sentence
        claims, _ = extract("", "Old tests failed. 5 new tests added.")
        self.assertEqual([(c["resolver"], c["text"]) for c in claims], [("diff.tests_added", "5 new tests added.")])

    def test_the_issue_cap_counts_distinct_references(self):
        claims, _ = extract("", "Fixes #1. " * 25 + "Fixes #2.")
        self.assertEqual([c["args"]["issue"] for c in claims if c["resolver"] == "issue.linked"], [1, 2])


if __name__ == "__main__":
    unittest.main()
