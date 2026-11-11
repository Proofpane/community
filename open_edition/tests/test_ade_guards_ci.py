"""Guards of Verify with CI: each fails on the faulty version of the code
it was written for. The last one pins a defect: verify_new recorded its results at the wall clock,
not at the time it was given, which made the recheck test pass or fail on the fraction of a second it started in."""

import os
import re
import time

import pytest

from ade_fixtures import BRANCH, make_repo
from conftest import ADMIN, signed_up
from proofpane_open.ade import ledger, sources, verifier
from test_ade_ci import EXPECT, WithCI, a_commit, run
from test_ade_sources import SHA, FakeGitHub

KEPT = {**EXPECT, "expected_statuses": []}
USER = {"id": "u1", "admin": False}
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def branch_of(commits):
    class Branch(WithCI):
        def head(self, branch):
            return SHA

        def branch_commits(self, sha, limit):
            return list(commits)
    return Branch


@pytest.mark.parametrize("paths", [[""], ["  "], ["src/**", 5], [None]])
def test_each_path_pattern_is_non_blank_text(paths):
    with pytest.raises(ledger.LedgerError, match="paths are a list of path patterns"):
        ledger.clean_verify_config({"expected_checks": [{"name": "t", "app": "a", "paths": paths}]})


def test_the_bound_counts_checks_and_statuses_together():
    statuses = [{"context": f"s{i}", "creator": "b"} for i in range(ledger.MAX_EXPECTED)]
    with pytest.raises(ledger.LedgerError, match="at most"):
        ledger.clean_verify_config({"expected_statuses": statuses + [{"context": "one more", "creator": "b"}]})
    with pytest.raises(ledger.LedgerError, match="at most"):
        ledger.clean_verify_config({"expected_checks": [{"name": "c", "app": "a"}], "expected_statuses": statuses})
    assert ledger.clean_verify_config({"expected_statuses": statuses}) is not None   # the bound itself is allowed


@pytest.mark.parametrize("cfg", [
    {"expected_checks": [{"name": "  ", "app": "a"}]},
    {"expected_checks": [{"name": "t", "app": ""}]},
    {"expected_statuses": [{"context": "ci", "creator": " "}]},
    {"expected_checks": [{"name": "t" * 201, "app": "a"}]},
])
def test_a_named_field_is_text_of_1_to_200_characters(cfg):
    with pytest.raises(ledger.LedgerError, match="names its"):
        ledger.clean_verify_config(cfg)
    assert ledger.clean_verify_config({"expected_checks": [{"name": "t" * 200, "app": "a"}]}) is not None


@pytest.mark.parametrize("entry", [5, None, [], ["name", "app"]])
def test_an_entry_that_is_not_an_object_is_refused_as_a_setting(entry):
    with pytest.raises(ledger.LedgerError, match="and no others"):
        ledger.clean_verify_config({"expected_checks": [entry]})


def test_another_account_can_neither_change_nor_clear_the_settings():
    conn = ledger.add(USER, "github", "owner/a", "main", [], True, verify_config=EXPECT)
    assert ledger.set_verify_config("u2", conn["id"], None) is None
    assert ledger.set_verify_config("u2", conn["id"], {"expected_statuses": [{"context": "x", "creator": "y"}]}) is None
    assert ledger.get("u1", conn["id"])["verify_config"] == KEPT


def test_the_settings_route_leaves_another_account_s_connection_as_it_was(client, tmp_path, monkeypatch):
    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    admin, member = signed_up(client, ADMIN), signed_up(client, "member@example.org")
    rid = client.post("/api/ade/repos", headers=admin, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                             "dirs": [], "verify": True, "verify_config": EXPECT}).json()["id"]
    assert client.patch(f"/api/ade/repos/{rid}", headers=member, json={"verify_config": None}).status_code == 404
    (conn,) = client.get("/api/ade/repos", headers=admin).json()["repos"]
    assert conn["verify_config"] == KEPT


def test_add_refuses_or_normalises_the_settings_itself():
    with pytest.raises(ledger.LedgerError):
        ledger.add(USER, "github", "owner/a", "main", [], True, verify_config={"nope": 1})
    assert ledger.list_for("u1") == []
    assert ledger.add(USER, "github", "owner/a", "main", [], True, verify_config=EXPECT)["verify_config"] == KEPT
    assert ledger.add(USER, "github", "owner/b", "main", [], True, verify_config={})["verify_config"] is None


def test_github_refuses_a_commit_that_is_not_a_full_id_without_asking():
    fake = FakeGitHub([])
    for bad in ("HEAD", "a" * 39, "A" * 40, "../../../x"):
        with pytest.raises(sources.SourceError, match="full id"):
            sources.GitHub("owner/repo", opener=fake, cache=sources._Cache()).commit_checks(bad)
    assert fake.asked == []


def test_a_clone_refuses_a_commit_that_is_not_a_full_id(tmp_path):
    path, _ = make_repo(tmp_path)
    with pytest.raises(sources.SourceError, match="full id"):
        sources.LocalGit("demo", path).commit_checks("HEAD")


def test_a_commit_whose_ci_failed_or_that_names_no_check_is_not_verified_again():
    now = int(time.time())
    commit = a_commit(when=iso(now - 3600))
    failed = branch_of([commit])([run(conclusion="failure")])
    assert verifier.verify_new("r1", failed, "main", verify_config=EXPECT, now=now)["done"][0]["rule"] == "any-fail"
    assert verifier.verify_new("r1", failed, "main", verify_config=EXPECT, now=now + 3 * verifier.RECHECK_AFTER)["done"] == []
    unconfigured = branch_of([commit])([run()])
    verifier.verify_new("r2", unconfigured, "main", now=now)
    assert verifier.verify_new("r2", unconfigured, "main", now=now + 3 * verifier.RECHECK_AFTER)["done"] == []


def test_commits_with_no_result_come_before_rechecks():
    now = int(time.time())
    older, newer = a_commit(sha="c" * 40, when=iso(now - 3600)), a_commit(sha="d" * 40, when=iso(now - 600))
    shown = [older]
    source = branch_of(shown)([run(status="queued", conclusion=None)])
    verifier.verify_new("r1", source, "main", verify_config=EXPECT, now=now)
    shown[:] = [newer, older]                                   # newest first, as a branch lists them
    later = verifier.verify_new("r1", source, "main", limit=1, verify_config=EXPECT, now=now + 2 * verifier.RECHECK_AFTER)
    assert [d["sha"] for d in later["done"]] == [newer["sha"]] and later["remaining"] == 1


def test_a_bad_setting_is_refused_before_the_repository_is_read(client, tmp_path, monkeypatch):
    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    reads, real_head = [], sources.LocalGit.head
    monkeypatch.setattr(sources.LocalGit, "head", lambda self, branch: reads.append(branch) or real_head(self, branch))
    admin = signed_up(client, ADMIN)
    r = client.post("/api/ade/repos", headers=admin, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                           "dirs": [], "verify": True, "verify_config": {"nope": 1}})
    assert r.status_code == 400 and reads == []


def test_the_verify_route_gives_verify_the_connection_s_settings(client, tmp_path, monkeypatch):
    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    admin = signed_up(client, ADMIN)
    rid = client.post("/api/ade/repos", headers=admin, json={"source": "local", "repo": "demo", "branch": BRANCH,
                                                             "dirs": [], "verify": True, "verify_config": EXPECT}).json()["id"]
    done = client.post(f"/api/ade/repos/{rid}/verify", headers=admin).json()["done"]
    reasons = [o.get("reason") for d in done if d["status"] == "verified"
               for o in ledger.verification(rid, d["sha"])["manifest"]["obligations"]
               if o.get("resolver") == "ci.checks_green"]
    assert reasons and set(reasons) == {"pending: missing: test (github-actions)"}   # a clone has no CI: missing


def test_the_page_saves_the_checks_with_a_patch():
    with open(os.path.join(HERE, "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    assert re.search(r'api\("PATCH", "/api/ade/repos/" \+ encodeURIComponent\(r\.id\), \{ verify_config: cfg \}\)', script)


def test_verify_new_records_its_results_at_the_now_it_was_given():
    now = 1_700_000_000
    commit = a_commit(when=iso(now - 3600))
    verifier.verify_new("r1", branch_of([commit])([run()]), "main", verify_config=EXPECT, now=now)
    assert ledger.verification_summaries("r1")[commit["sha"]]["created_at"] == now
