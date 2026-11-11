"""Verify with a commit's CI: a connection names the checks a commit's CI must pass (Verify's own
`expected_checks` and `expected_statuses`), the ADE reads the commit's check runs and statuses from GitHub, and a
commit decided while its CI was still running is verified again later. A clone has no CI."""

import json
import sqlite3
import time

import pytest

from ade_fixtures import BRANCH, make_repo
from conftest import ADMIN, DIFF, signed_up
from proofpane_open import config, store
from proofpane_open.ade import commits, ledger, sources, verifier
from test_ade_sources import API, SHA, FakeGitHub, _ok

EXPECT = {"expected_checks": [{"name": "test", "app": "github-actions"}]}
PARENT = "b" * 40


def a_commit(sha=SHA, when="2026-09-28T01:00:00Z"):
    return commits.make_commit(sha, [PARENT], "Owner", "o@example.org", when, "Change f\n\nThe change adds tests.")


class WithCI:
    """A source with one diff and the CI a test gives it; it counts how often its CI is read."""
    label = "owner/repo"
    kind = "github"

    def __init__(self, runs=(), statuses=(), fails=False):
        self.runs, self.statuses, self.fails, self.reads = list(runs), list(statuses), fails, 0

    def commit_diff(self, sha, parent):
        return DIFF

    def commit_checks(self, sha):
        self.reads += 1
        if self.fails:
            raise sources.SourceError("GitHub refused the token: it may have expired or been revoked.")
        return self.runs, self.statuses


def ci_outcome(record):
    (o,) = [o for o in record["manifest"]["obligations"] if o.get("resolver") == "ci.checks_green"]
    return o["outcome"], o.get("reason")


def run(status="completed", conclusion="success"):
    return {"name": "test", "app": "github-actions", "status": status, "conclusion": conclusion}


def test_the_commit_s_ci_decides_the_expected_check():
    passed = verifier.verify_commit(WithCI([run()]), a_commit(), EXPECT)
    assert ci_outcome(passed)[0] == "pass" and passed["reason"] is None
    failed = verifier.verify_commit(WithCI([run(conclusion="failure")]), a_commit(), EXPECT)
    assert ci_outcome(failed)[0] == "fail" and failed["rule"] == "any-fail"
    running = verifier.verify_commit(WithCI([run(status="in_progress", conclusion=None)]), a_commit(), EXPECT)
    assert ci_outcome(running) == ("unverified", "pending: still running: test")
    other_app = verifier.verify_commit(WithCI([dict(run(), app="someone-else")]), a_commit(), EXPECT)
    assert ci_outcome(other_app) == ("unverified", "pending: missing: test (github-actions)")


def test_a_status_counts_only_from_its_creator():
    cfg = {"expected_statuses": [{"context": "ci/build", "creator": "ci-bot"}]}
    ok = verifier.verify_commit(WithCI(statuses=[{"context": "ci/build", "state": "success", "creator": "ci-bot"}]),
                                a_commit(), cfg)
    assert ci_outcome(ok)[0] == "pass"
    forged = verifier.verify_commit(WithCI(statuses=[{"context": "ci/build", "state": "success", "creator": "me"}]),
                                    a_commit(), cfg)
    assert ci_outcome(forged)[0] == "unverified"


def test_no_settings_no_ci_read_and_ci_that_cannot_be_read_is_said():
    source = WithCI([run()])
    none = verifier.verify_commit(source, a_commit())
    assert source.reads == 0 and ci_outcome(none) == ("unverified", "no expected check applies to this change")
    broken = WithCI(fails=True)
    record = verifier.verify_commit(broken, a_commit(), EXPECT)
    assert broken.reads == 1 and record["status"] == "verified"
    assert record["reason"].startswith("CI could not be read: GitHub refused the token")
    assert ci_outcome(record)[0] == "unverified"


def test_a_pending_ci_is_verified_again_later_and_only_for_a_day(monkeypatch):
    now = int(time.time())
    recent = a_commit(when=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600)))
    old = a_commit(sha="c" * 40, when=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 2 * 86400)))

    class Branch(WithCI):
        def head(self, branch):
            return SHA

        def branch_commits(self, sha, limit):
            return [recent, old]

    source = Branch([run(status="queued", conclusion=None)])
    first = verifier.verify_new("r1", source, "main", verify_config=EXPECT, now=now)
    assert [d["sha"] for d in first["done"]] == [old["sha"], recent["sha"]]
    assert verifier._ci_pending(ledger.verification_summaries("r1")[recent["sha"]])
    assert verifier.verify_new("r1", source, "main", verify_config=EXPECT, now=now)["done"] == []  # too soon
    source.runs = [run()]
    later = now + verifier.RECHECK_AFTER
    again = verifier.verify_new("r1", source, "main", verify_config=EXPECT, now=later)
    assert [d["sha"] for d in again["done"]] == [recent["sha"]]        # the two-day-old commit is not read again
    assert not verifier._ci_pending(ledger.verification_summaries("r1")[recent["sha"]])


def test_github_reports_a_commit_s_ci_in_verify_s_shape():
    fake = FakeGitHub([
        (f"{API}/commits/{SHA}/check-runs?", _ok({"check_runs": [
            {"name": "test", "app": {"slug": "github-actions"}, "status": "completed", "conclusion": "success"}]})),
        (f"{API}/commits/{SHA}/status?", _ok({"statuses": [
            {"context": "ci/build", "state": "failure", "creator": {"login": "ci-bot"}}]})),
    ])
    runs, statuses = sources.GitHub("owner/repo", opener=fake, cache=sources._Cache()).commit_checks(SHA)
    assert runs == [{"name": "test", "app": "github-actions", "status": "completed", "conclusion": "success"}]
    assert statuses == [{"context": "ci/build", "state": "failure", "creator": "ci-bot"}]
    assert [u for u, _ in fake.asked] == [f"{API}/commits/{SHA}/check-runs?per_page=100",
                                           f"{API}/commits/{SHA}/status?per_page=100"]
    with pytest.raises(sources.SourceError):
        sources.GitHub("owner/repo", opener=fake, cache=sources._Cache()).commit_checks("HEAD")


def test_a_clone_has_no_ci(tmp_path):
    path, shas = make_repo(tmp_path)
    assert sources.LocalGit("demo", path).commit_checks(shas["code"]) == ([], [])


@pytest.mark.parametrize("cfg", [
    {"expected_checks": [{"name": "test"}]},
    {"expected_checks": [{"name": "test", "app": "gha", "extra": 1}]},
    {"expected_checks": [{"name": "test", "app": "gha", "kind": "lint"}]},
    {"expected_checks": [{"name": "test", "app": "gha", "paths": "src/**"}]},
    {"expected_checks": [{"name": "test", "app": "gha", "paths": []}]},
    {"expected_checks": ["test"]},
    {"expected_statuses": [{"context": "ci"}]},
    {"expected_checks": [{"name": "t", "app": "a"}] * (ledger.MAX_EXPECTED + 1)},
    {"required_checks": []},
    ["test"],
])
def test_a_setting_verify_does_not_know_is_refused(cfg):
    with pytest.raises(ledger.LedgerError):
        ledger.clean_verify_config(cfg)


def test_settings_are_kept_in_verify_s_form():
    cfg = {"expected_checks": [{"name": "test", "app": "gha", "paths": ["src/**"], "kind": "test"}],
           "expected_statuses": [{"context": "ci/build", "creator": "ci-bot"}]}
    assert ledger.clean_verify_config(cfg) == cfg
    assert ledger.clean_verify_config({}) is None and ledger.clean_verify_config(None) is None
    assert ledger.clean_verify_config({"expected_checks": [], "expected_statuses": []}) is None


def test_a_database_made_before_the_column_gets_it_and_keeps_its_rows(env):
    path = config.db_path()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE ade_repos (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source TEXT NOT NULL, "
               "repo TEXT NOT NULL, branch TEXT NOT NULL, dirs TEXT NOT NULL, verify INTEGER NOT NULL, "
               "token_box TEXT, created_at INTEGER NOT NULL)")
    db.execute("INSERT INTO ade_repos VALUES ('r1', 'u1', 'github', 'owner/repo', 'main', '[\"x\"]', 1, NULL, 1)")
    db.commit()
    db.close()
    assert ledger.get("u1", "r1")["verify_config"] is None
    assert ledger.set_verify_config("u1", "r1", EXPECT)["verify_config"] == {**EXPECT, "expected_statuses": []}
    with store.connect() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(ade_repos)")]
    assert columns.count("verify_config") == 1 and ledger.get("u1", "r1")["repo"] == "owner/repo"


def test_the_settings_route_changes_only_one_s_own_connection(client, tmp_path, monkeypatch):
    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    admin = signed_up(client, ADMIN)
    member = signed_up(client, "member@example.org")
    r = client.post("/api/ade/repos", headers=admin, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                           "dirs": [], "verify": True, "verify_config": EXPECT})
    assert r.status_code == 200 and r.json()["verify_config"]["expected_checks"] == EXPECT["expected_checks"]
    rid = r.json()["id"]
    assert client.patch(f"/api/ade/repos/{rid}", headers=member, json={"verify_config": None}).status_code == 404
    bad = client.patch(f"/api/ade/repos/{rid}", headers=admin, json={"verify_config": {"expected_checks": ["x"]}})
    assert bad.status_code == 400
    cleared = client.patch(f"/api/ade/repos/{rid}", headers=admin, json={"verify_config": None})
    assert cleared.status_code == 200 and cleared.json()["verify_config"] is None
    refused = client.post("/api/ade/repos", headers=admin, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                                 "dirs": [], "verify": True,
                                                                 "verify_config": {"nope": 1}})
    assert refused.status_code == 400 and len(client.get("/api/ade/repos", headers=admin).json()["repos"]) == 1
