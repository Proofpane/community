import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verify_pr.config import load_config, path_matches  # noqa: E402
from verify_pr import dlp  # noqa: E402
from verify_pr import resolvers as R  # noqa: E402

HEAD = "h" * 40


def bundle(files=(), **kw):
    b = {"repo": "o/r", "number": 1, "title": "", "body": "", "head_sha": HEAD, "files": list(files),
         "check_runs": [], "statuses": [], "reviews": [], "issues": {}}
    b.update(kw)
    return b


def f(path, patch="", status="modified"):
    return {"path": path, "status": status, "patch": patch}


class Patches(unittest.TestCase):
    def test_file_headers_come_only_before_the_first_hunk(self):
        patch = "diff --git a/x b/x\nindex 1..2\n--- a/x\n+++ b/x\n@@ -1 +1,3 @@\n x\n+y\n+++ z\n"
        self.assertEqual(list(R._added_lines(f("x", patch))), [(2, "y"), (3, "++ z")])
        self.assertEqual(list(R._removed_lines(f("x", patch))), [])


class OddPatches(unittest.TestCase):
    """A patch split on characters other than "\\n", a patch with no hunk read as empty, a hunk header whose
    number could not be converted, and the test checks passing over files the bundle does not hold."""
    cfg = load_config()
    KEY = "AKIA" + "IOSFODNN7EXAMPLE"

    def test_a_line_is_split_on_newlines_only(self):
        for cp in (0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029):
            added = dict(f("src/a.py", f"@@ -1 +1 @@\n-x\n+x = 1{chr(cp)}KEY = '{self.KEY}'\n"), additions=1)
            self.assertEqual(R.diff_no_secrets(bundle([added]), self.cfg)["outcome"], "fail", hex(cp))
        crlf = dict(f("src/a.py", "@@ -1 +1,2 @@\r\n x\r\n+y\r\n"), additions=1)
        self.assertEqual(R._parse(crlf), {"added": [(2, "y")], "removed": [], "complete": True})

    def test_a_patch_the_parser_cannot_account_for_is_not_scanned(self):
        for patch, additions in (("+" + self.KEY + "\n", 1),                 # no hunk at all
                                 ("+" + self.KEY + "\n", None),              # no hunk, and no count to compare
                                 ("@@ -1 +" + "9" * 5000 + " @@\n+x\n", 1),  # a header that does not parse
                                 ("@@ -0,0 +1 @@\n+x\n", 2)):               # fewer added lines than GitHub counted
            b = bundle([dict(f("a.txt", patch), additions=additions)])
            self.assertEqual(R.diff_no_secrets(b, self.cfg)["outcome"], "unverified", patch[:20])
            self.assertEqual(R.diff_no_pii(b, self.cfg)["outcome"], "unverified", patch[:20])
        self.assertTrue(R._parse(dict(f("a.txt", ""), additions=0))["complete"])  # nothing to scan
        self.assertTrue(R._parse({"path": "logo.png", "patch": None, "additions": 0})["complete"])

    def test_a_test_file_without_its_patch_is_not_read_as_adding_or_weakening_nothing(self):
        src = dict(f("src/app.py", "@@ -0,0 +1 @@\n+x\n"), additions=1)
        big = {"path": "tests/test_app.py", "status": "modified", "patch": None, "additions": 4200}
        b = bundle([src, big])
        for resolver in (R.diff_tests_added, R.diff_tests_not_weakened):
            r = resolver(b, self.cfg)
            self.assertEqual((r["outcome"], r["evidence"]), ("unverified", [{"kind": "not-scanned",
                                                                            "ref": "changed path 2"}]), resolver)
        seen = dict(f("tests/test_b.py", "@@ -0,0 +1 @@\n+def test_b(): pass\n"), additions=1)
        self.assertEqual(R.diff_tests_added(bundle([src, big, seen]), self.cfg)["outcome"], "pass")  # decisive anyway
        removed = {"path": "tests/test_old.py", "status": "removed", "patch": None, "additions": 0}
        self.assertEqual(R.diff_tests_not_weakened(bundle([big, removed]), self.cfg)["outcome"], "unverified")
        self.assertEqual([e["kind"] for e in R.diff_tests_not_weakened(bundle([big, removed]), self.cfg)["evidence"]],
                         ["test-file-removed"])

    def test_a_removed_test_file_without_its_patch_decides_nothing_about_added_tests(self):
        removed = {"path": "tests/test_old.py", "status": "removed", "patch": None, "additions": None}
        src = dict(f("src/app.py", "@@ -0,0 +1 @@\n+x\n"), additions=1)
        self.assertEqual(R.diff_tests_added(bundle([src, removed]), self.cfg)["outcome"], "fail")

    def test_files_the_bundle_does_not_list_leave_every_absence_undecided(self):
        listed = dict(f("docs/a.md", "@@ -0,0 +1 @@\n+x\n"), additions=1)
        b = bundle([listed], changed_files=3001)
        for resolver in (R.diff_no_schema_change, R.diff_no_dependency_change, R.diff_docs_only, R.diff_no_secrets,
                         R.diff_no_pii, R.diff_tests_added, R.diff_tests_not_weakened, R.policy_change_reviewed):
            r = resolver(b, self.cfg)
            self.assertEqual(r["outcome"], "unverified", resolver.__name__)
            self.assertIn("3000 changed file(s) are not listed", r["reason"], resolver.__name__)
        found = bundle([f("db/migrations/1.sql")], changed_files=3001)
        self.assertEqual(R.diff_no_schema_change(found, self.cfg)["outcome"], "fail")  # a match is still a match
        self.assertEqual(R.diff_no_schema_change(bundle([listed], changed_files=1), self.cfg)["outcome"], "pass")

    def test_a_check_scoped_to_paths_cannot_be_ruled_out_when_files_are_missing(self):
        cfg = load_config(overrides={"expected_checks": [
            {"name": "test", "app": "github-actions", "kind": "test"},
            {"name": "db", "app": "github-actions", "paths": ["db/**"]}]})
        run = {"name": "test", "app": "github-actions", "status": "completed", "conclusion": "success"}
        b = bundle([f("src/a.py")], check_runs=[run])
        self.assertEqual(R.ci_checks_green(b, cfg)["outcome"], "pass")
        b["changed_files"] = 5
        self.assertEqual(R.ci_checks_green(b, cfg)["outcome"], "unverified")


class Paths(unittest.TestCase):
    def test_globs(self):
        self.assertTrue(path_matches("a/b/migrations/0001.py", ["**/migrations/**"]))
        self.assertTrue(path_matches("README.md", ["**/*.md"]))
        self.assertTrue(path_matches("pkg/requirements-dev.txt", ["requirements*.txt"]))
        self.assertFalse(path_matches("src/app.py", ["**/*.md", "docs/**"]))
        self.assertFalse(path_matches("requirements.txt", ["**/*.md", "docs/**", "**/LICENSE*"]))


class CiChecksGreen(unittest.TestCase):
    cfg = load_config(overrides={"expected_checks": [{"name": "test", "app": "github-actions"}]})

    def run_(self, runs):
        return R.ci_checks_green(bundle(check_runs=runs), self.cfg)["outcome"]

    def test_pass(self):
        self.assertEqual(self.run_([{"name": "test", "app": "github-actions", "status": "completed",
                                     "conclusion": "success"}]), "pass")

    def test_fail(self):
        self.assertEqual(self.run_([{"name": "test", "app": "github-actions", "status": "completed",
                                     "conclusion": "failure"}]), "fail")

    def test_same_name_from_another_app_does_not_count(self):
        self.assertEqual(self.run_([{"name": "test", "app": "someone-else", "status": "completed",
                                     "conclusion": "success"}]), "unverified")

    def test_skipped_is_not_a_pass(self):
        self.assertEqual(self.run_([{"name": "test", "app": "github-actions", "status": "completed",
                                     "conclusion": "skipped"}]), "unverified")

    def test_running_is_not_a_pass(self):
        self.assertEqual(self.run_([{"name": "test", "app": "github-actions", "status": "in_progress",
                                     "conclusion": None}]), "unverified")

    def test_nothing_configured(self):
        self.assertEqual(R.ci_checks_green(bundle(), load_config())["outcome"], "unverified")

    def test_path_scoped_check_applies_only_to_its_paths(self):
        cfg = load_config(overrides={"expected_checks": [
            {"name": "backend", "app": "github-actions", "paths": ["backend/**"], "kind": "test"},
            {"name": "gitleaks", "app": "github-actions"}]})
        runs = [{"name": "gitleaks", "app": "github-actions", "status": "completed", "conclusion": "success"}]
        landing = bundle([f("landing/index.html")], check_runs=runs)
        self.assertEqual(R.ci_checks_green(landing, cfg)["outcome"], "pass")
        # but a claim that tests pass has no test check to stand on
        r = R.ci_checks_green(landing, cfg, {"tests_claim": True})
        self.assertEqual((r["outcome"], r["reason"]), ("unverified", "no test check applies to the changed paths"))
        backend = bundle([f("backend/app/x.py")], check_runs=runs)
        self.assertEqual(R.ci_checks_green(backend, cfg)["outcome"], "unverified")  # backend check missing

    def test_status_only_counts_when_pinned(self):
        b = bundle(statuses=[{"context": "test", "state": "success", "creator": "anyone"}])
        self.assertEqual(R.ci_checks_green(b, self.cfg)["outcome"], "unverified")


class Diff(unittest.TestCase):
    cfg = load_config()

    def test_schema(self):
        self.assertEqual(R.diff_no_schema_change(bundle([f("db/migrations/0002.sql")]), self.cfg)["outcome"],
                         "fail")
        self.assertEqual(R.diff_no_schema_change(bundle([f("src/a.py")]), self.cfg)["outcome"], "pass")

    def test_dependencies(self):
        self.assertEqual(R.diff_no_dependency_change(bundle([f("package.json")]), self.cfg)["outcome"], "fail")
        self.assertEqual(R.diff_no_dependency_change(bundle([f("src/a.py")]), self.cfg)["outcome"], "pass")

    def test_docs_only(self):
        self.assertEqual(R.diff_docs_only(bundle([f("README.md"), f("docs/x.rst")]), self.cfg)["outcome"], "pass")
        self.assertEqual(R.diff_docs_only(bundle([f("README.md"), f("src/a.py")]), self.cfg)["outcome"], "fail")

    def test_tests_added(self):
        good = f("tests/test_a.py", "@@ -0,0 +1,2 @@\n+def test_x():\n+    assert 1\n")
        only_skip = f("tests/test_a.py", "@@ -1,1 +1,2 @@\n+@pytest.mark.skip\n def test_x():\n")
        self.assertEqual(R.diff_tests_added(bundle([good]), self.cfg)["outcome"], "pass")
        self.assertEqual(R.diff_tests_added(bundle([only_skip]), self.cfg)["outcome"], "fail")
        self.assertEqual(R.diff_tests_added(bundle([f("src/a.py", "@@ -0,0 +1 @@\n+x\n")]), self.cfg)["outcome"],
                         "fail")


class TestsNotWeakened(unittest.TestCase):
    cfg = load_config(overrides={"reviewers": ["alice"]})

    def test_clean(self):
        b = bundle([f("tests/test_a.py", "@@ -1,1 +1,2 @@\n def test_x():\n+    assert 2\n")])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "pass")

    def test_deleted_test_file(self):
        b = bundle([f("tests/test_a.py", "", status="removed")])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "unverified")

    def test_skip_added(self):
        b = bundle([f("tests/test_a.py", "@@ -1,1 +1,2 @@\n+@pytest.mark.skip\n def test_x():\n")])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "unverified")

    def test_test_case_removed(self):
        b = bundle([f("tests/test_a.py", "@@ -1,2 +1,0 @@\n-def test_x():\n-    assert 1\n")])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "unverified")

    def test_approval_on_head_clears_it(self):
        b = bundle([f("tests/test_a.py", "", status="removed")],
                   reviews=[{"user": "alice", "state": "APPROVED", "commit_id": HEAD}])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "pass")

    def test_approval_on_an_older_commit_does_not(self):
        b = bundle([f("tests/test_a.py", "", status="removed")],
                   reviews=[{"user": "alice", "state": "APPROVED", "commit_id": "o" * 40}])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "unverified")

    def test_approval_by_someone_not_named_does_not(self):
        b = bundle([f("tests/test_a.py", "", status="removed")],
                   reviews=[{"user": "mallory", "state": "APPROVED", "commit_id": HEAD}])
        self.assertEqual(R.diff_tests_not_weakened(b, self.cfg)["outcome"], "unverified")

    def test_a_test_file_renamed_out_of_the_test_paths_is_removed(self):
        # as cheap as deleting it, and the check exists to stop that
        for old, new, kind in (("tests/test_login.py", "attic/login_checks.txt", "test-file-removed"),
                               ("pytest.ini", "pytest.ini.off", "test-config-changed"),
                               # still under tests/, so still a test path, but no longer the suite's configuration
                               ("tests/conftest.py", "tests/conftest_old.txt", "test-config-changed"),
                               ("vitest.config.ts", "vitest.old.ts", "test-config-changed")):
            renamed = dict(f(new, None, status="renamed"), previous_path=old, additions=0)
            r = R.diff_tests_not_weakened(bundle([renamed]), self.cfg)
            self.assertEqual((r["outcome"], [(e["kind"], e["ref"]) for e in r["evidence"]]),
                             ("unverified", [(kind, old)]), old)

    def test_a_test_file_copied_out_of_the_test_paths_is_still_there(self):
        for old, new in (("tests/test_login.py", "docs/login_example.py"), ("pytest.ini", "docs/pytest.ini.txt")):
            copied = dict(f(new, "@@ -0,0 +1 @@\n+x = 1\n", status="copied"), previous_path=old, additions=1)
            self.assertEqual(R.diff_tests_not_weakened(bundle([copied]), self.cfg)["outcome"], "pass", old)

    def test_a_test_file_renamed_within_the_test_paths_is_read_as_before(self):
        moved = dict(f("tests/test_b.py", "@@ -1,1 +1,2 @@\n def test_x():\n+    assert 2\n", status="renamed"),
                     previous_path="tests/test_a.py", additions=1)
        self.assertEqual(R.diff_tests_not_weakened(bundle([moved]), self.cfg)["outcome"], "pass")
        into = dict(f("tests/test_b.py", None, status="renamed"), previous_path="attic/b.py", additions=0)
        self.assertEqual(R.diff_tests_not_weakened(bundle([into]), self.cfg)["outcome"], "pass")


class SecretsAndPii(unittest.TestCase):
    cfg = load_config()
    key = "AKIA" + "ABCDEFGHIJKLMNOP"

    def test_secret_found_and_not_repeated(self):
        r = R.diff_no_secrets(bundle([f("a.py", f"@@ -0,0 +1 @@\n+k = '{self.key}'\n")]), self.cfg)
        self.assertEqual(r["outcome"], "fail")
        self.assertNotIn(self.key, str(r))

    def test_secret_suppressed_by_fingerprint(self):
        fp = dlp.fingerprint("aws-access-key", self.key)
        cfg = load_config(overrides={"suppress_secret_fingerprints": [fp]})
        r = R.diff_no_secrets(bundle([f("a.py", f"@@ -0,0 +1 @@\n+k = '{self.key}'\n")]), cfg)
        self.assertEqual(r["outcome"], "pass")

    def test_pii(self):
        r = R.diff_no_pii(bundle([f("a.py", "@@ -0,0 +1 @@\n+owner = 'jane.doe@corp.io'\n")]), self.cfg)
        self.assertEqual(r["outcome"], "fail")
        self.assertNotIn("jane.doe", str(r))

    def test_placeholder_email_ignored(self):
        r = R.diff_no_pii(bundle([f("a.py", "@@ -0,0 +1 @@\n+owner = 'someone@example.com'\n")]), self.cfg)
        self.assertEqual(r["outcome"], "pass")

    def test_pii_suppressed_by_rule_and_path(self):
        cfg = load_config(overrides={"suppress_pii": [{"rule": "email", "path": "tests/**"}]})
        r = R.diff_no_pii(bundle([f("tests/fixture.py", "@@ -0,0 +1 @@\n+e = 'jane@corp.io'\n")]), cfg)
        self.assertEqual(r["outcome"], "pass")


class ReviewAndIssues(unittest.TestCase):
    cfg = load_config(overrides={"reviewers": ["alice"]})

    def test_workflow_change_needs_approval(self):
        b = bundle([f(".github/workflows/ci.yml")])
        self.assertEqual(R.policy_change_reviewed(b, self.cfg)["outcome"], "unverified")
        b["reviews"] = [{"user": "alice", "state": "APPROVED", "commit_id": HEAD}]
        self.assertEqual(R.policy_change_reviewed(b, self.cfg)["outcome"], "pass")

    def test_local_action_elsewhere_is_watched(self):
        cfg = load_config(overrides={"local_actions": ["tools/ci-action"]})
        b = bundle([f("tools/ci-action/action.yml")])
        self.assertEqual(R.policy_change_reviewed(b, cfg)["outcome"], "unverified")

    def test_issue(self):
        self.assertEqual(R.issue_linked(bundle(issues={"5": {"exists": True}}), self.cfg,
                                        {"issue": 5, "repo": None})["outcome"], "pass")
        self.assertEqual(R.issue_linked(bundle(issues={"5": {"exists": False}}), self.cfg,
                                        {"issue": 5, "repo": None})["outcome"], "fail")
        self.assertEqual(R.issue_linked(bundle(), self.cfg, {"issue": 5, "repo": None})["outcome"], "unverified")

    def test_a_lookup_that_failed_decides_nothing_and_a_pull_request_is_not_an_issue(self):
        # the shapes an earlier fetcher wrote: an error inside the record, and a pull request with exists: true
        failed = R.issue_linked(bundle(issues={"5": {"exists": None, "error": "HTTP 403"}}), self.cfg,
                                {"issue": 5, "repo": None})
        self.assertEqual((failed["outcome"], failed["reason"]), ("unverified", "issue 5 could not be read (HTTP 403)"))
        pr = R.issue_linked(bundle(issues={"o/x#5": {"exists": True, "is_pull_request": True}}), self.cfg,
                            {"issue": 5, "repo": "o/x"})
        self.assertEqual((pr["outcome"], pr["evidence"][0]["note"]), ("fail", "a pull request, not an issue"))

    def test_the_fetchers_records_are_read(self):
        args = {"issue": 5, "repo": "o/x"}
        pr = R.issue_linked(bundle(issues={"o/x#5": {"exists": False, "is_pull_request": True}}), self.cfg, args)
        self.assertEqual((pr["outcome"], pr["evidence"][0]["note"]), ("fail", "a pull request, not an issue"))
        failed = R.issue_linked(bundle(issue_errors={"o/x#5": "HTTP 404, private or missing"}), self.cfg, args)
        self.assertEqual((failed["outcome"], failed["reason"]),
                         ("unverified", "issue o/x#5 could not be read (HTTP 404, private or missing)"))
        self.assertEqual(R.issue_linked(bundle(issue_errors={"o/x#6": "HTTP 403"}), self.cfg, args)["reason"],
                         "issue o/x#5 was not fetched")


if __name__ == "__main__":
    unittest.main()
