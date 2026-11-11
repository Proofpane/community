import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_gh  # noqa: E402


def item(repo, number, merged, login="Copilot", kind="Bot", body=""):
    return {"repository_url": f"https://api.github.com/repos/{repo}", "number": number,
            "user": {"login": login, "type": kind}, "pull_request": {"merged_at": merged}, "body": body}


class FakeSearch:
    """run_search(query, page) over a list of items, as GitHub's search behaves: `merged:A..B` filters, the total is
    exact, pages are PAGE long, and nothing past SEARCH_CAP can be read."""

    def __init__(self, items):
        self.items, self.calls = items, []

    def __call__(self, query, page):
        self.calls.append((query, page))
        span = query.rsplit("merged:", 1)[1]
        since, until = span.split("..")
        repo = next((t[5:] for t in query.split() if t.startswith("repo:")), None)
        hits = [i for i in self.items if since <= i["pull_request"]["merged_at"][:10] <= until
                and (repo is None or fetch_gh.repo_of(i) == repo)]
        if (page - 1) * fetch_gh.PAGE >= fetch_gh.SEARCH_CAP:
            raise fetch_gh.GhError("search", 422, "Cannot access beyond the first 1000 results")
        visible = hits[:fetch_gh.SEARCH_CAP]
        return {"total_count": len(hits), "incomplete_results": False,
                "items": visible[(page - 1) * fetch_gh.PAGE:page * fetch_gh.PAGE]}


class Small(unittest.TestCase):
    """The search's cap and page made small, so slicing can be tested on a handful of items."""

    def setUp(self):
        self.saved = fetch_gh.SEARCH_CAP, fetch_gh.PAGE
        fetch_gh.SEARCH_CAP, fetch_gh.PAGE = 10, 5

    def tearDown(self):
        fetch_gh.SEARCH_CAP, fetch_gh.PAGE = self.saved


class SearchWindow(Small):
    def test_a_window_under_the_cap_is_read_in_full_in_one_slice(self):
        items = [item("a/b", i, f"2026-06-{i + 1:02d}T00:00:00Z") for i in range(8)]
        got, slices = fetch_gh.search_window("q", "2026-06-01", "2026-06-30", FakeSearch(items))
        self.assertEqual(len(got), 8)
        self.assertEqual(slices, [{"since": "2026-06-01", "until": "2026-06-30", "total_count": 8, "read": 8,
                                   "complete": True}])

    def test_a_window_over_the_cap_is_split_by_date_until_every_slice_fits(self):
        items = [item("a/b", i, f"2026-06-{i % 30 + 1:02d}T00:00:00Z") for i in range(45)]
        got, slices = fetch_gh.search_window("q", "2026-06-01", "2026-06-30", FakeSearch(items))
        self.assertEqual(sorted(i["number"] for i in got), list(range(45)))
        self.assertTrue(all(s["complete"] and s["total_count"] <= 10 for s in slices))
        # the slices tile the window: consecutive, no gap, no overlap
        self.assertEqual(slices[0]["since"], "2026-06-01")
        self.assertEqual(slices[-1]["until"], "2026-06-30")
        for a, b in zip(slices, slices[1:]):
            self.assertEqual(date.fromisoformat(a["until"]) + timedelta(days=1), date.fromisoformat(b["since"]))

    def test_a_single_day_over_the_cap_is_read_one_page_deep_and_marked_partial(self):
        items = [item("a/b", i, "2026-06-10T00:00:00Z") for i in range(12)] + \
                [item("a/b", 100, "2026-06-11T00:00:00Z")]
        search = FakeSearch(items)
        got, slices = fetch_gh.search_window("q", "2026-06-10", "2026-06-11", search)
        day = [s for s in slices if s["since"] == "2026-06-10"][0]
        self.assertEqual((day["total_count"], day["read"], day["complete"]), (12, 5, False))
        self.assertEqual(len(got), 6)
        self.assertEqual(sum(1 for q, _ in search.calls if "2026-06-10..2026-06-10" in q), 1)

    def test_a_repeat_across_pages_is_counted_once_and_the_slice_is_not_complete(self):
        items = [item("a/b", i, "2026-06-10T00:00:00Z") for i in range(7)]

        def shifting(query, page):  # the second page repeats an item of the first, and the seventh is never shown
            res = FakeSearch(items)(query, page)
            if page == 2:
                res["items"] = [items[0], items[5]]
            return res
        got, slices = fetch_gh.search_window("q", "2026-06-10", "2026-06-10", shifting)
        self.assertEqual(sorted(i["number"] for i in got), [0, 1, 2, 3, 4, 5])
        self.assertEqual((slices[0]["read"], slices[0]["complete"]), (6, False))

    def test_incomplete_results_from_github_mark_the_slice_partial(self):
        def timed_out(query, page):
            res = FakeSearch([item("a/b", 1, "2026-06-10T00:00:00Z")])(query, page)
            res["incomplete_results"] = True
            return res
        _, slices = fetch_gh.search_window("q", "2026-06-10", "2026-06-10", timed_out)
        self.assertFalse(slices[0]["complete"])


class Qualifies(unittest.TestCase):
    def test_a_bot_author_qualifies_on_a_bot_query_and_a_person_does_not(self):
        self.assertTrue(fetch_gh.qualifies(item("a/b", 1, "2026-06-01T00:00:00Z")))
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, "2026-06-01T00:00:00Z", "alice", "User")))

    def test_an_unmerged_pull_request_never_qualifies(self):
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, None)))
        footer = "Generated with [Claude Code]"
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, None, "alice", "User", footer), [footer]))

    def test_a_footer_counts_only_on_the_last_non_empty_line_and_only_for_a_person(self):
        f = "Generated with [Claude Code]"
        merged = "2026-06-01T00:00:00Z"
        self.assertTrue(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", f"text\n\n🤖 {f}(x)\n\n"), [f]))
        self.assertTrue(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", f"text\r\n{f}\r\n"), [f]))
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", f"{f}\rThanks!"), [f]))
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", f"{f}\nThanks!"), [f]))
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, merged, "bot", "Bot", f), [f]))
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", None), [f]))

    def test_a_character_markdown_does_not_end_a_line_with_is_part_of_the_last_line(self):
        f = "Generated with [Claude Code]"
        merged = "2026-06-01T00:00:00Z"
        for sep in ("\u2028", "\x0c", "\x85"):
            self.assertTrue(fetch_gh.qualifies(item("a/b", 1, merged, "u", "User", f"text\n{f}{sep}Thanks!"), [f]),
                            repr(sep))

    def test_a_footer_query_qualifies_only_its_own_footer(self):
        queries = fetch_gh.agent_queries({"bot_authors": [], "footers": ["Made by A", "Made by B"]})
        self.assertEqual([f for _, f in queries], [["Made by A"], ["Made by B"]])
        body = "text\nMade by B"
        self.assertFalse(fetch_gh.qualifies(item("a/b", 1, "2026-06-01T00:00:00Z", "u", "User", body), ["Made by A"]))


class Rank(unittest.TestCase):
    def test_the_rule(self):
        counts = {"a/big": 30, "b/small": 5, "examplecorp/mine": 40, "ExampleCorp/other": 50, "c/fork": 25, "d/next": 20}
        chosen, why = fetch_gh.rank(counts, {"examplecorp"}, is_fork=lambda r: r == "c/fork")
        self.assertEqual(chosen, ["a/big", "d/next"])
        self.assertEqual(why, {"ExampleCorp/other": "excluded owner", "examplecorp/mine": "excluded owner",
                               "c/fork": "fork", "b/small": "5 qualifying, under 20"})

    def test_ties_are_broken_by_name_and_the_count_stops_at_five(self):
        counts = {f"o/r{i}": 20 for i in range(7)}
        chosen, _ = fetch_gh.rank(counts, set(), is_fork=lambda r: False)
        self.assertEqual(chosen, [f"o/r{i}" for i in range(5)])

    def test_a_repository_that_cannot_be_read_is_passed_over_and_an_outage_stops_the_draw(self):
        def gone(repo):
            raise fetch_gh.GhError(f"repos/{repo}", 404, "Not Found")
        chosen, why = fetch_gh.rank({"a/b": 30}, set(), is_fork=gone)
        self.assertEqual((chosen, list(why)), ([], ["a/b"]))
        self.assertTrue(why["a/b"].startswith("could not be read"))

        def limited(repo):
            raise fetch_gh.GhError(f"repos/{repo}", 403, "API rate limit exceeded")
        with self.assertRaises(fetch_gh.GhError):
            fetch_gh.rank({"a/b": 30}, set(), is_fork=limited)

    def test_gone_blocked_or_refused_passes_a_repository_over(self):
        for status, message in ((410, "Gone"), (451, "Repository access blocked"),
                                (403, "Resource not accessible by integration")):
            def refused(repo):
                raise fetch_gh.GhError(f"repos/{repo}", status, message)
            chosen, why = fetch_gh.rank({"a/b": 30, "c/d": 25}, set(), is_fork=lambda r: refused(r) if r == "a/b"
                                        else False)
            self.assertEqual(chosen, ["c/d"], status)
            self.assertTrue(why["a/b"].startswith("could not be read"), status)
        for status in (500, 502, None):
            def failing(repo):
                raise fetch_gh.GhError(f"repos/{repo}", status, "x")
            with self.assertRaises(fetch_gh.GhError):
                fetch_gh.rank({"a/b": 30}, set(), is_fork=failing)


class MostRecent(unittest.TestCase):
    def test_by_merge_time_newest_first(self):
        prs = {i: {"number": i, "merged_at": f"2026-08-{i + 1:02d}T00:00:00Z"} for i in range(25)}
        got = fetch_gh.most_recent(prs)
        self.assertEqual(len(got), fetch_gh.PER_REPO)
        self.assertEqual([p["number"] for p in got[:3]], [24, 23, 22])

    def test_merge_time_not_number_decides(self):
        prs = {5: {"number": 5, "merged_at": "2026-06-20T00:00:00Z"},
               9: {"number": 9, "merged_at": "2026-06-01T00:00:00Z"}}
        self.assertEqual([p["number"] for p in fetch_gh.most_recent(prs, 1)], [5])


class DrawSample(Small):
    AGENTS = {"bot_authors": ["app/copilot-swe-agent"], "footers": ["Generated with [Claude Code]"]}

    def search_for(self, bots, people):
        """A run_search that answers author: queries from `bots` and footer queries from `people`."""
        def run(query, page):
            pool = bots if "author:" in query else people
            return FakeSearch(pool)(query, page)
        return run

    def test_a_complete_search_is_ranked_on_its_own_counts(self):
        bots = [item("a/one", i, f"2026-06-{i % 28 + 1:02d}T00:00:00Z") for i in range(25)]
        f = self.AGENTS["footers"][0]
        people = [item("b/two", i, f"2026-06-{i % 28 + 1:02d}T00:00:00Z", "u", "User", f) for i in range(3)]
        fetch_gh.SEARCH_CAP = 1000  # nothing partial
        rec, rows = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", set(),
                                         self.search_for(bots, people), lambda r: False)
        self.assertFalse(rec["partial"])
        self.assertNotIn("exact", rec["counts"])
        self.assertEqual(rec["counts"]["sampled"], {"a/one": 25, "b/two": 3})
        self.assertEqual([c["repo"] for c in rec["chosen"]], ["a/one"])
        self.assertEqual(rec["shortfall"], 4)
        self.assertEqual(len(rows), 28)
        self.assertEqual({r["stage"] for r in rows}, {"search"})

    def test_a_partial_search_is_settled_by_exact_counts_per_repository(self):
        # on 06-10 two repositories together pass the cap: the day is read one page deep, so the sampled counts are
        # short, and the choice waits for a search limited to each repository
        bots = [item("a/one", i, "2026-06-10T00:00:00Z") for i in range(6)] + \
               [item("a/one", 100 + i, f"2026-06-{i + 11:02d}T00:00:00Z") for i in range(15)] + \
               [item("c/three", i, "2026-06-10T00:00:00Z") for i in range(6)] + \
               [item("c/three", 100 + i, f"2026-06-{i + 11:02d}T00:00:00Z") for i in range(16)]
        rec, rows = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", set(),
                                         self.search_for(bots, []), lambda r: False)
        self.assertTrue(rec["partial"])
        self.assertLess(sum(rec["counts"]["sampled"].values()), 43)
        self.assertEqual(rec["counts"]["exact"], {"a/one": 21, "c/three": 22})
        self.assertTrue(rec["counts"]["exact_complete"])
        self.assertEqual([(c["repo"], c["qualifying"]) for c in rec["chosen"]], [("c/three", 22), ("a/one", 21)])
        self.assertEqual(len(rec["chosen"][0]["prs"]), fetch_gh.PER_REPO)
        self.assertEqual({r["stage"] for r in rows}, {"search", "count"})
        self.assertEqual({r["scope"] for r in rows if r["stage"] == "count"}, {"a/one", "c/three"})

    def test_an_excluded_owner_is_never_counted_exactly(self):
        bots = [item("examplecorp/x", i, "2026-06-10T00:00:00Z") for i in range(12)]
        rec, rows = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", {"examplecorp"},
                                         self.search_for(bots, []), lambda r: False)
        self.assertEqual(rec["candidates"], [])
        self.assertEqual(rec["chosen"], [])

    def test_a_count_search_github_rejects_leaves_the_counts_incomplete(self):
        bots = [item("a/one", i, "2026-06-10T00:00:00Z") for i in range(12)]

        def run(query, page):
            if "repo:" in query:
                raise fetch_gh.GhError("search/issues", 422, "The listed repositories cannot be searched")
            return FakeSearch(bots)(query, page)
        rec, _ = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", set(), run, lambda r: False)
        self.assertTrue(rec["partial"])
        self.assertFalse(rec["counts"]["exact_complete"])
        query = "is:pr is:merged is:public author:app/copilot-swe-agent"
        self.assertIn("422", rec["count_search"]["a/one"][query]["rejected"])
        # GitHub rejects a repository-limited search for a repository it no longer knows: not "0 qualifying"
        self.assertEqual(rec["passed_over"]["a/one"], "could not be counted: every search limited to it was rejected")

    def test_the_thirty_repositories_with_the_most_results_read_are_counted_exactly(self):
        day = lambda d: f"2026-06-{d:02d}T00:00:00Z"
        bots = [item("crowd/x", i, day(10)) for i in range(11)]  # one day over the cap: partial
        bots += [item("zz/top", i, day(i + 1)) for i in range(3)]  # the most after crowd/x, and last by name
        days = [d for d in range(1, 31) if d != 10]
        bots += [item(f"a/r{i:02d}", 1, day(days[i % len(days)])) for i in range(33)]
        rec, _ = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", set(), self.search_for(bots, []),
                                      lambda r: False)
        self.assertTrue(rec["partial"])
        self.assertEqual(len(rec["counts"]["sampled"]), 35)
        self.assertEqual(len(rec["candidates"]), fetch_gh.CANDIDATES)
        self.assertEqual(rec["candidates"][:2], ["crowd/x", "zz/top"])
        self.assertEqual(rec["candidates"][2:], [f"a/r{i:02d}" for i in range(28)])
        self.assertEqual(sorted(rec["counts"]["exact"]), sorted(rec["candidates"]))

    def test_a_rejected_qualifier_is_recorded_and_the_draw_goes_on(self):
        agents = {"bot_authors": ["app/no-such-agent", "app/copilot-swe-agent"], "footers": []}
        bots = [item("a/one", i, f"2026-06-{i % 28 + 1:02d}T00:00:00Z") for i in range(20)]

        def run(query, page):
            if "no-such-agent" in query:
                raise fetch_gh.GhError("search/issues", 422, "The listed users cannot be searched")
            return FakeSearch(bots)(query, page)
        fetch_gh.SEARCH_CAP = 1000
        rec, _ = fetch_gh.draw_sample(agents, "2026-06-01", "2026-06-30", set(), run, lambda r: False)
        rejected = {q: r["rejected"] for q, r in rec["search"].items()}
        self.assertIn("422", str(rejected["is:pr is:merged is:public author:app/no-such-agent"]))
        self.assertIsNone(rejected["is:pr is:merged is:public author:app/copilot-swe-agent"])
        self.assertEqual([c["repo"] for c in rec["chosen"]], ["a/one"])

    def test_any_other_failure_stops_the_draw_with_what_it_had(self):
        def broken(query, page):
            raise fetch_gh.GhError("search/issues", 502, "Bad Gateway")
        rec, rows = fetch_gh.draw_sample(self.AGENTS, "2026-06-01", "2026-06-30", set(), broken, lambda r: False)
        self.assertIn("502", rec["error"])
        self.assertNotIn("chosen", rec)


class Window(unittest.TestCase):
    def test_the_default_is_the_ninety_complete_days_before_today(self):
        import argparse
        since, until = fetch_gh._window(argparse.Namespace(since=None, until=None))
        span = date.fromisoformat(until) - date.fromisoformat(since)
        self.assertEqual(span.days + 1, fetch_gh.WINDOW_DAYS)
        self.assertLess(date.fromisoformat(until), fetch_gh.datetime.now(fetch_gh.timezone.utc).date())


class Issues(unittest.TestCase):
    def fetch(self, status, retryable=False):
        def f(path):
            raise fetch_gh.GhError(path, status, "x", retryable=retryable)
        return f

    def test_a_404_decides_only_in_the_pull_requests_own_repository(self):
        # GitHub answers 404 for a repository it does not show, so elsewhere a 404 may be a private repository
        self.assertEqual(fetch_gh.issue_record("o/r", "o/r", 3, self.fetch(404)), {"exists": False})
        self.assertEqual(fetch_gh.issue_record("o/r", "O/R", 3, self.fetch(404)), {"exists": False})
        other = fetch_gh.issue_record("o/r", "acme/internal", 3, self.fetch(404))
        self.assertEqual(list(other), ["error"])
        self.assertIn("404", other["error"])

    def test_a_410_decides_anywhere(self):
        for target in ("o/r", "acme/other"):
            self.assertEqual(fetch_gh.issue_record("o/r", target, 3, self.fetch(410)), {"exists": False})

    def test_any_other_failure_is_an_error_not_an_answer(self):
        for status in (403, 451, 500, None):
            rec = fetch_gh.issue_record("o/r", "o/r", 3, self.fetch(status))
            self.assertEqual(list(rec), ["error"], status)

    def test_a_rate_limit_or_outage_that_outlasted_the_retries_is_raised(self):
        for status in (403, 429, 502):
            with self.assertRaises(fetch_gh.GhError):
                fetch_gh.issue_record("o/r", "o/r", 3, self.fetch(status, retryable=True))

    def test_retryable_marks_only_what_the_retries_were_for(self):
        from unittest import mock
        answers = [(403, "API rate limit exceeded"), (403, "Resource not accessible by integration"),
                   (502, "Bad Gateway"), (404, "Not Found")]
        for status, message in answers:
            done = mock.Mock(returncode=1, stdout="", stderr=f"gh: {message} (HTTP {status})")
            with mock.patch.object(fetch_gh.subprocess, "run", return_value=done), \
                    mock.patch.object(fetch_gh, "_sleep", lambda s: None):
                with self.assertRaises(fetch_gh.GhError) as cm:
                    fetch_gh.gh_json("repos/o/r/issues/3")
            self.assertEqual(cm.exception.retryable, message in ("API rate limit exceeded", "Bad Gateway"), message)

    def test_a_pull_request_is_not_recorded_as_an_issue_that_exists(self):
        # `exists` is True only for an issue: a verifier that reads nothing else cannot pass a pull request
        self.assertEqual(fetch_gh.issue_record("o/r", "o/r", 3, lambda p: {"number": 3, "pull_request": {}}),
                         {"exists": False, "is_pull_request": True})
        self.assertEqual(fetch_gh.issue_record("o/r", "o/r", 3, lambda p: {"number": 3}), {"exists": True})

    def test_a_name_github_does_not_allow_is_not_looked_up(self):
        asked = []
        for target in ("../user", "o/..", "o/.", "-o/r", "o/r/x", "o\u00e9/r"):
            rec = fetch_gh.issue_record("o/r", target, 3, lambda p: asked.append(p) or {"number": 3})
            self.assertEqual(list(rec), ["error"], target)
        self.assertEqual(asked, [])
        self.assertEqual(fetch_gh.issue_record("o/r", "a-b/c.d_e", 3, lambda p: {"number": 3}), {"exists": True})

    def test_a_response_that_is_not_json_is_a_failed_call(self):
        from unittest import mock
        done = mock.Mock(returncode=0, stdout="<html>busy</html>", stderr="")
        with mock.patch.object(fetch_gh.subprocess, "run", return_value=done):
            with self.assertRaises(fetch_gh.GhError):
                fetch_gh.gh_json("search/issues?q=x")
            with self.assertRaises(fetch_gh.GhError):
                fetch_gh.gh_json("repos/o/r/pulls/1/files", paginate=True, jq=".[]")

    def test_one_long_word_costs_linear_time(self):
        import time
        costs = []
        for n in (16384, 65536):
            t = time.perf_counter()
            fetch_gh.issue_refs("o/r", "", "a" * n)
            costs.append(time.perf_counter() - t)
        self.assertLess(costs[1], 1.0)
        self.assertLess(costs[1], 10 * max(costs[0], 1e-3))  # four times the text: quadratic would be sixteen

    def test_references_are_keyed_as_a_claim_looks_them_up(self):
        refs = fetch_gh.issue_refs("o/r", "Fixes #0012", "Closes other/repo#4. See #9 and #12345678901.")
        self.assertEqual(refs, {"12": ("o/r", 12), "other/repo#4": ("other/repo", 4), "9": ("o/r", 9)})

    def test_references_beyond_the_claims_are_capped(self):
        body = "Fixes #1. " + " ".join(f"see #{n}" for n in range(2, 200))
        refs = fetch_gh.issue_refs("o/r", "", body)
        self.assertIn("1", refs)
        self.assertEqual(len(refs), 1 + fetch_gh.MAX_ISSUE_REFS)


class Workflows(unittest.TestCase):
    def test_job_names(self):
        text = "name: CI\non: [pull_request]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n  test:\n    name: Unit tests\n" \
               "    runs-on: ubuntu-latest\n    steps:\n      - name: checkout\n"
        self.assertEqual(fetch_gh.workflow_job_names(text), ["lint", "Unit tests"])


class Bundle(unittest.TestCase):
    def test_build(self):
        pr = {"number": 3, "title": "t", "body": "b", "user": {"login": "bot", "type": "Bot"},
              "merged_at": "2026-09-01T00:00:00Z", "base": {"sha": "a"}, "head": {"sha": "b"}, "changed_files": 1}
        b = fetch_gh.build_bundle("o/r", pr, [{"filename": "x.py", "status": "added", "patch": "+1"}],
                                  [{"name": "test", "app": {"slug": "github-actions"}, "status": "completed",
                                    "conclusion": "success"}],
                                  {"statuses": [{"context": "c", "state": "success", "creator": {"login": "ci"}}]},
                                  [{"user": {"login": "alice"}, "state": "APPROVED", "commit_id": "b"}], {})
        self.assertEqual(b["check_runs"][0]["app"], "github-actions")
        self.assertEqual(b["statuses"][0]["creator"], "ci")
        self.assertEqual(b["reviews"][0]["commit_id"], "b")
        self.assertEqual(b["changed_files"], 1)
        self.assertTrue(b["merged"])

    def test_a_patch_or_count_github_left_out_is_recorded_as_missing(self):
        pr = {"number": 3, "title": "t", "body": "b", "user": {"login": "u", "type": "User"}, "merged_at": None,
              "base": {"sha": "a"}, "head": {"sha": "b"}}
        files = [{"filename": "big.sql", "status": "modified", "additions": 5000},
                 {"filename": "x.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+1", "additions": 1},
                 {"filename": "y.py", "status": "modified"}]
        b = fetch_gh.build_bundle("o/r", pr, files, [], {"statuses": []}, [], {})
        self.assertEqual([(f["patch"], f["additions"]) for f in b["files"]],
                         [(None, 5000), ("@@ -0,0 +1 @@\n+1", 1), (None, None)])
        self.assertIsNone(b["changed_files"])


class Bundles(unittest.TestCase):
    def test_a_response_not_shaped_as_expected_costs_one_bundle_not_the_run(self):
        import contextlib
        import io
        from unittest import mock
        calls = []

        def fake(repo, number, qualified_by=None):
            calls.append(number)
            if number == 2:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return {"repo": repo, "number": number}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fetch_gh, "fetch_bundle", fake):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = fetch_gh.main(["bundles", "--repo", "o/r", "--prs", "1,2,3", "--out", tmp])
            self.assertEqual((code, calls, sorted(os.listdir(tmp))), (1, [1, 2, 3], ["o__r__1.json", "o__r__3.json"]))
            self.assertIn("not fetched: o/r#2: ValueError", err.getvalue())


# ---------------------------------------------------------------- end to end, against a fake gh


FAKE_GH = r'''
import hashlib, json, os, re, sys, urllib.parse


def sha(kind, p):
    return hashlib.sha1(f"{kind}{p['repo']}{p['number']}".encode()).hexdigest()


def fail(status, message):
    sys.stderr.write(f"gh: {message} (HTTP {status})\n")
    sys.exit(1)


def search(data, params):
    q, per_page, page = params["q"][0], int(params["per_page"][0]), int(params["page"][0])
    cap = int(os.environ["FAKE_GH_CAP"])
    if (page - 1) * per_page >= cap:
        fail(422, "Cannot access beyond the first 1000 results")
    items, phrase = list(data["prs"]), None
    norm = lambda s: " ".join(re.sub(r"\W+", " ", (s or "").lower()).split())
    for t in re.findall(r'"[^"]*"|\S+', q):
        key, _, value = t.partition(":")
        if t.startswith('"'):
            phrase = t.strip('"')
        elif t == "is:pr":
            pass
        elif t == "is:merged":
            items = [p for p in items if p["merged_at"]]
        elif t == "is:public":
            items = [p for p in items if not data["repos"].get(p["repo"], {}).get("private")]
        elif key == "author":
            login = data["apps"].get(value[4:]) if value.startswith("app/") else value
            if login is None:
                fail(422, "Validation Failed: the listed users cannot be searched")
            items = [p for p in items if p["author"] == login]
        elif key == "repo":
            items = [p for p in items if p["repo"] == value]
        elif key == "merged":
            a, b = value.split("..")
            items = [p for p in items if p["merged_at"] and a <= p["merged_at"][:10] <= b]
        elif t == "in:body":
            items = [p for p in items if norm(phrase) in norm(p["body"])]
        else:
            fail(422, "unknown qualifier " + t)
    items.sort(key=lambda p: (p["created_at"], p["repo"], p["number"]), reverse=True)
    chunk = items[:cap][(page - 1) * per_page:page * per_page]
    return {"total_count": len(items), "incomplete_results": False,
            "items": [{"repository_url": "https://api.github.com/repos/" + p["repo"], "number": p["number"],
                       "user": {"login": p["author"], "type": p["author_type"]}, "body": p["body"],
                       "pull_request": {"merged_at": p["merged_at"]}} for p in chunk]}


def route(data, path):
    parts = urllib.parse.urlsplit(path)
    if parts.path == "search/issues":
        return search(data, urllib.parse.parse_qs(parts.query))
    seg = parts.path.split("/")
    if seg[0] != "repos":
        fail(404, "Not Found")
    repo = seg[1] + "/" + seg[2]
    rest = seg[3:]
    pr = lambda n: next((p for p in data["prs"] if p["repo"] == repo and p["number"] == int(n)), None)
    if not rest:
        if repo not in data["repos"]:
            fail(404, "Not Found")
        return {"full_name": repo, "fork": data["repos"][repo].get("fork", False)}
    if rest[0] == "contents":
        files = data["workflows"].get(repo, {})
        if len(rest) == 1:
            return [{"name": ".github", "type": "dir"}, {"name": "README.md", "type": "file"}]
        if rest[1:] == [".github", "workflows"]:
            if not files:
                fail(404, "Not Found")
            return [{"name": n, "path": ".github/workflows/" + n, "type": "file"} for n in sorted(files)]
        return files["/".join(rest[3:])]
    if rest[0] == "pulls":
        p = pr(rest[1])
        if p is None:
            fail(404, "Not Found")
        if len(rest) == 2:
            return {"number": p["number"], "title": p["title"], "body": p["body"],
                    "user": {"login": p["author"], "type": p["author_type"]}, "merged_at": p["merged_at"],
                    "base": {"sha": sha("base", p)}, "head": {"sha": sha("head", p)},
                    "changed_files": len(p["files"])}
        if rest[2] == "files":
            return p["files"]
        if rest[2] == "reviews":
            return []
    if rest[0] == "commits":
        if rest[2] == "check-runs":
            return {"check_runs": [{"name": "test", "app": {"slug": "github-actions"}, "status": "completed",
                                    "conclusion": "success"}]}
        if rest[2] == "status":
            return {"statuses": []}
    if rest[0] == "issues":
        found = data["issues"].get(repo + "#" + rest[1])
        if found is None:
            fail(404, "Not Found")
        if found == "forbidden":
            fail(403, "Resource not accessible by integration")
        return found
    fail(404, "Not Found")


def main():
    args, jq, path = sys.argv[1:], None, None
    assert args[0] == "api", args
    i = 1
    while i < len(args):
        if args[i] == "-H":
            i += 2
        elif args[i] == "--paginate":
            i += 1
        elif args[i] == "--jq":
            jq, i = args[i + 1], i + 2
        else:
            path, i = args[i], i + 1
    with open(os.environ["FAKE_GH_DATA"], encoding="utf-8") as f:
        data = json.load(f)
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
        f.write(path + "\n")
    fails_path = os.environ["FAKE_GH_FAILS"]
    with open(fails_path, encoding="utf-8") as f:
        pending = json.load(f)
    for sub, (status, message, times) in list(pending.items()):
        if sub in path and times:
            pending[sub][2] = times - 1
            with open(fails_path, "w", encoding="utf-8") as f:
                json.dump(pending, f)
            fail(status, message)
    out = route(data, path)
    if jq == ".[]":
        for x in out:
            print(json.dumps(x, ensure_ascii=False))
    elif jq in (".check_runs[]", ".statuses[]"):
        for x in out[jq[1:-2]]:
            print(json.dumps(x, ensure_ascii=False))
    elif jq is not None:
        sys.exit("fake gh: unsupported jq " + jq)
    elif isinstance(out, str):
        sys.stdout.write(out)
    else:
        print(json.dumps(out, ensure_ascii=False))


main()
'''

FOOTER = "Generated with [Claude Code]"


def _prs():
    """One month of merged pull requests across repositories chosen to exercise every rule `draw_sample` applies."""
    prs = []

    def add(repo, number, day, author="Copilot", kind="Bot", body="", merged=True, files=None):
        stamp = f"2026-06-{day:02d}T12:00:00Z"
        prs.append({"repo": repo, "number": number, "author": author, "author_type": kind, "body": body,
                    "title": f"change {number}", "created_at": stamp, "merged_at": stamp if merged else None,
                    "files": files or [{"filename": "app.py", "status": "modified",
                                        "patch": "@@ -1 +1 @@\n-a\n+b", "additions": 1}]})
    for i in range(24):  # a/one: 24 by Copilot, six of them on the crowded 06-10
        add("a/one", i + 1, 10 if i < 6 else i - 5)
    add("a/one", 90, 3, merged=False)  # unmerged: never counted
    for i in range(25):  # c/fork: 25, six on 06-10, and a fork
        add("c/fork", i + 1, 10 if i < 6 else i - 5)
    for i in range(21):  # g/gone: 21, one on 06-10, answers 404 when asked whether it is a fork
        add("g/gone", i + 1, i + 1)
    for i in range(22):  # b/two: 22 by people with the footer on the last line
        add("b/two", i + 1, i + 1, "alice", "User", f"Does a thing.\n\n\U0001f916 {FOOTER}(https://x)\n")
    for i in range(3):  # footer not last: found by the search, does not qualify
        add("b/two", 100 + i, i + 1, "bob", "User", f"{FOOTER}\n\nThanks!")
    for i in range(30):  # examplecorp/own: excluded owner
        add("examplecorp/own", i + 1, i % 28 + 1, "devin-ai-integration[bot]")
    for i in range(5):  # s/small: under 20
        add("s/small", i + 1, i + 1, "devin-ai-integration[bot]")
    for i in range(20):  # p/private: not public
        add("p/private", i + 1, i % 28 + 1)
    # references for the bundles of a/one's newest pull requests
    for p in prs:
        if (p["repo"], p["number"]) == ("a/one", 24):
            p["body"] = "Fixes #3. Closes other/repo#4. Resolves #999. See third/repo#5."
    return prs


class EndToEnd(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("the fake gh is a script with a #! line")
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable} -S\n" + FAKE_GH)
        os.chmod(gh, 0o755)
        apps = {"copilot-swe-agent": "Copilot", "devin-ai-integration": "devin-ai-integration[bot]"}
        data = {"prs": _prs(), "apps": apps,
                "repos": {"a/one": {}, "c/fork": {"fork": True}, "b/two": {}, "examplecorp/own": {}, "s/small": {},
                          "p/private": {"private": True}},
                "workflows": {"a/one": {"ci.yml": "jobs:\n  test:\n    name: Unit tests\n    runs-on: x\n"}},
                # other/repo answers 404, as GitHub does for a private repository; third/repo refuses with 403
                "issues": {"a/one#3": {"number": 3}, "third/repo#5": "forbidden"}}
        self.log = os.path.join(self.dir, "calls.log")
        fails = os.path.join(self.dir, "fails.json")
        paths = {"FAKE_GH_DATA": os.path.join(self.dir, "data.json"), "FAKE_GH_LOG": self.log,
                 "FAKE_GH_FAILS": fails, "FAKE_GH_CAP": "10"}
        with open(paths["FAKE_GH_DATA"], "w", encoding="utf-8") as f:
            json.dump(data, f)
        # a rate limit once on one pull request's files, and a pull request that cannot be fetched on the first run
        self.fails = {"pulls/24/files": [403, "API rate limit exceeded for user", 1],
                      "b/two/pulls/22": [404, "Not Found", 1]}
        with open(fails, "w", encoding="utf-8") as f:
            json.dump(self.fails, f)
        saved_env = {k: os.environ.get(k) for k in (*paths, "PATH")}
        os.environ.update(paths)
        os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]

        def restore():
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        saved = (fetch_gh.SEARCH_CAP, fetch_gh.PAGE, fetch_gh.SEARCH_INTERVAL, fetch_gh.PER_REPO, fetch_gh._sleep)
        self.pauses = []
        fetch_gh.SEARCH_CAP, fetch_gh.PAGE, fetch_gh.SEARCH_INTERVAL, fetch_gh.PER_REPO = 10, 5, 0, 3
        fetch_gh._sleep = self.pauses.append

        def put_back():
            (fetch_gh.SEARCH_CAP, fetch_gh.PAGE, fetch_gh.SEARCH_INTERVAL, fetch_gh.PER_REPO,
             fetch_gh._sleep) = saved
        self.addCleanup(put_back)
        self.agents = os.path.join(self.dir, "agents.json")
        with open(self.agents, "w", encoding="utf-8") as f:
            json.dump({"bot_authors": ["app/copilot-swe-agent", "app/devin-ai-integration", "app/no-such-agent"],
                       "footers": [FOOTER]}, f)

    def main(self, argv):
        """fetch_gh.main with its progress kept here: printed, it would split unittest's verdict lines."""
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = fetch_gh.main(argv)
        self.printed = out.getvalue()
        return code

    def run_all(self):
        out = os.path.join(self.dir, "out")
        code = self.main(["all", "--exclude-owner", "ExampleCorp", "--agents", self.agents, "--since", "2026-06-01",
                          "--until", "2026-06-30", "--out", out])
        return code, out

    def test_the_whole_draw_and_every_bundle(self):
        code, out = self.run_all()
        self.assertEqual(code, 1)  # one pull request could not be fetched on the first run
        self.assertIn("rejected queries: 1, rejected count searches: 0;", self.printed)
        with open(os.path.join(out, "sample.json"), encoding="utf-8") as f:
            sample = json.load(f)
        self.assertTrue(sample["partial"])  # 06-10 holds 13 by Copilot, over the cap of 10
        self.assertEqual(sample["counts"]["exact"], {"a/one": 24, "b/two": 22, "c/fork": 25, "g/gone": 21,
                                                     "s/small": 5})
        self.assertTrue(sample["counts"]["exact_complete"])
        self.assertNotIn("p/private", sample["counts"]["sampled"])
        self.assertEqual([(c["repo"], c["qualifying"]) for c in sample["chosen"]], [("a/one", 24), ("b/two", 22)])
        self.assertEqual(sample["shortfall"], 3)
        self.assertEqual(sample["passed_over"]["c/fork"], "fork")
        self.assertTrue(sample["passed_over"]["g/gone"].startswith("could not be read"))
        self.assertEqual(sample["passed_over"]["s/small"], "5 qualifying, under 20")
        rejected = sample["search"]["is:pr is:merged is:public author:app/no-such-agent"]["rejected"]
        self.assertIn("422", rejected)
        self.assertEqual([p["number"] for p in sample["chosen"][0]["prs"]], [24, 23, 22])
        self.assertEqual([p["number"] for p in sample["chosen"][1]["prs"]], [22, 21, 20])
        with open(os.path.join(out, "search-results.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertTrue(any(r["repo"] == "b/two" and r["number"] == 100 and not r["qualifies"] for r in rows))
        self.assertFalse(any(r["repo"] == "examplecorp/own" and r["stage"] == "count" for r in rows))
        with open(os.path.join(out, "configs", "a__one.json"), encoding="utf-8") as f:
            config = json.load(f)
        self.assertEqual(config["candidate_jobs"], [{"workflow": "ci.yml", "job": "Unit tests"}])
        self.assertIn(".github/", config["top_level"])
        with open(os.path.join(out, "bundles", "a__one__24.json"), encoding="utf-8") as f:
            bundle = json.load(f)
        self.assertEqual(bundle["issues"], {"3": {"exists": True}, "999": {"exists": False}})
        self.assertEqual(bundle["issue_errors"],
                         {"other/repo#4": "HTTP 404, which another repository gives when it is private or missing",
                          "third/repo#5": "HTTP 403"})
        self.assertEqual(bundle["qualified_by"], "is:pr is:merged is:public author:app/copilot-swe-agent")
        with open(self.log, encoding="utf-8") as f:
            asked = f.read().split("\n")
        # the combined status lists 30 contexts a page unless asked for more
        self.assertTrue(any(re.fullmatch(r"repos/a/one/commits/[0-9a-f]{40}/status\?per_page=100", p) for p in asked))
        self.assertEqual(bundle["changed_files"], 1)
        # the rate limit was waited out once, then the files were read; the 403 that is not a rate limit was not
        # retried
        self.assertEqual(self.pauses, [30])
        self.assertEqual(sorted(os.listdir(os.path.join(out, "bundles"))),
                         ["a__one__22.json", "a__one__23.json", "a__one__24.json", "b__two__20.json",
                          "b__two__21.json"])
        # a second run of the bundles fetches only the one that failed
        with open(self.log, encoding="utf-8") as f:
            before = len(f.readlines())
        code = self.main(["bundles", "--sample", os.path.join(out, "sample.json"),
                          "--out", os.path.join(out, "bundles")])
        self.assertEqual(code, 0)
        with open(self.log, encoding="utf-8") as f:
            again = [line.strip() for line in f.readlines()[before:]]
        self.assertTrue(again and all("b/two" in p for p in again), again)
        self.assertIn("b__two__22.json", os.listdir(os.path.join(out, "bundles")))
        # every bundle it wrote can be verified
        here = os.path.dirname(__file__)
        core = os.path.join(here, "..", "..", "core")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(here, ".."), core]))
        proc = subprocess.run([sys.executable, "-m", "verify_pr", "summarize", os.path.join(out, "bundles")],
                              capture_output=True, text=True, env=env, cwd=os.path.join(here, ".."))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["total"]["prs"], 6, (summary["not_verified"], summary["skipped"]))

    def test_a_search_outage_stops_the_draw_and_writes_what_it_had(self):
        self.fails = {"search/issues": [503, "Service Unavailable", 99]}
        with open(os.environ["FAKE_GH_FAILS"], "w", encoding="utf-8") as f:
            json.dump(self.fails, f)
        code, out = self.run_all()
        self.assertEqual(code, 1)
        with open(os.path.join(out, "sample.json"), encoding="utf-8") as f:
            sample = json.load(f)
        self.assertIn("503", sample["error"])
        self.assertEqual(len(self.pauses), 5)  # retried with backoff before giving up
        self.assertFalse(os.path.exists(os.path.join(out, "bundles")))


if __name__ == "__main__":
    unittest.main()
