"""Guards of the ADE's channel reader, connected repositories, board and Verify against faulty versions of the code.
Each test here fails on the version it was written for, for the reason its name gives: a member who could learn about an
admin's clone from the errors of a connection they may not make, a refusal that passed only because git failed rather
than because the ADE's own check ran, a silence alerted a second early, and the like. The other faulty versions are
killed by the tests beside this file."""

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from ade_fixtures import BRANCH, commit, make_repo, write
from conftest import ADMIN, signed_up
from proofpane_open import config
from proofpane_open.ade import board, channel, commits, ledger, sources, verifier
from test_ade_board import AGENT, CLOUD, CLOUD_WHO, Memory, build, c, log_line
from test_ade_board import demo as board_demo
from test_ade_sources import API, ITEM, PARENT, SHA, FakeGitHub, _ok

HERE = os.path.dirname(os.path.abspath(__file__))

KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    path, shas = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    return path, shas


def connect(cl, headers, **body):
    payload = {"source": "local", "repo": "demo", "branch": BRANCH, "dirs": ["tasks/demo"], "verify": True}
    payload.update(body)
    return cl.post("/api/ade/repos", json=payload, headers=headers)


# ---- ledger ---------------------------------------------------------------------------------------------------
def test_the_ledger_refuses_a_clone_on_its_own(demo, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    for user, repo, token, words in (({"id": "m", "admin": False}, "demo", None, "Only an admin"),
                                     ({"id": "a", "admin": True}, "elsewhere", None, "names no such clone"),
                                     ({"id": "a", "admin": True}, "demo", "github_pat_x", "takes no token")):
        with pytest.raises(ledger.LedgerError, match=words):
            ledger.add(user, "local", repo, BRANCH, ["tasks/demo"], True, token=token)
    assert ledger.list_for("m") == [] and ledger.list_for("a") == []


@pytest.mark.parametrize("n", [16, 24, 33])
def test_a_key_that_aes_would_take_but_is_not_32_bytes_is_refused(monkeypatch, n):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", base64.b64encode(bytes(n)).decode())
    with pytest.raises(ledger.LedgerError, match="not 32 bytes"):
        ledger.keystore_key()


def test_what_is_not_a_token_is_not_stored(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    for token in ("github pat", "x" * (ledger.MAX_TOKEN + 1)):
        with pytest.raises(ledger.LedgerError, match="does not look like a GitHub token"):
            ledger.add({"id": "u1", "admin": False}, "github", "owner/private", "main", ["x"], False, token=token)
    assert ledger.list_for("u1") == []


def test_an_account_connects_at_most_twenty_repositories():
    user = {"id": "u1", "admin": False}
    for n in range(ledger.MAX_CONNECTIONS):
        ledger.add(user, "github", f"owner/r{n}", "main", ["x"], False)
    with pytest.raises(ledger.LedgerError, match="at most 20"):
        ledger.add(user, "github", "owner/one-more", "main", ["x"], False)
    assert ledger.add({"id": "u2", "admin": False}, "github", "owner/r0", "main", ["x"], False)["repo"] == "owner/r0"


def test_another_account_gets_no_source_even_without_a_token():
    row = ledger.add({"id": "u1", "admin": False}, "github", "owner/public", "main", ["x"], False)
    assert ledger.source_for("u1", row["id"]) is not None
    assert ledger.source_for("u2", row["id"]) is None


def test_the_directory_list_is_bounded():
    with pytest.raises(ledger.LedgerError, match="at most 12"):
        ledger.add({"id": "u1", "admin": False}, "github", "owner/r", "main", [f"d{n}" for n in range(13)], False)


# ---- routes ---------------------------------------------------------------------------------------------------
def test_a_member_learns_nothing_about_a_clone(client, demo):
    member = signed_up(client, "member@example.org")
    for change in ({}, {"branch": "missing"}, {"dirs": ["tasks/none"]}):
        r = connect(client, member, **change)
        assert (r.status_code, r.json()["detail"]) == (400, "Only an admin can connect a clone on this machine.")


@pytest.mark.parametrize("key", [None, "short"])
def test_a_refused_connection_asks_github_nothing(client, monkeypatch, key):
    asked = []
    monkeypatch.setattr(sources, "_default_opener", lambda url, headers: asked.append(url) or (404, {}, b"{}"))
    if key is None:
        monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY", raising=False)
    else:
        monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", key)
    admin = signed_up(client, ADMIN)
    assert client.get("/api/ade/repos", headers=admin).json()["can_store_tokens"] is False
    for body in ({"repo": "o/r", "branch": "main", "dirs": ["../etc"]},
                 {"repo": "o/r", "branch": "main", "dirs": ["x"], "token": "github_pat_secret"}):
        assert client.post("/api/ade/repos", headers=admin, json=body).status_code == 400
    assert asked == []


def test_a_private_repository_is_connected_with_a_token(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    sent = []

    def opener(url, headers):
        sent.append(headers.get("Authorization"))
        if "/git/ref/heads/main" in url:
            return 200, {}, json.dumps({"object": {"sha": SHA}}).encode()
        return 200, {}, b"[]"
    monkeypatch.setattr(sources, "_default_opener", opener)
    admin = signed_up(client, ADMIN)
    assert client.get("/api/ade/repos", headers=admin).json()["can_store_tokens"] is True
    r = client.post("/api/ade/repos", headers=admin, json={"repo": "owner/private", "branch": "main", "dirs": ["x"],
                                                            "token": "github_pat_secret"})
    assert r.status_code == 200 and r.json()["has_token"] is True and "github_pat_secret" not in r.text
    assert sent and set(sent) == {"Bearer github_pat_secret"}


# ---- sources --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("call", [
    lambda s: s.list_dir("HEAD", ""), lambda s: s.read_file("HEAD", "README.md"),
    lambda s: s.file_commits("HEAD", "README.md", 5), lambda s: s.branch_commits("HEAD", 5),
    lambda s: s.commit_diff("HEAD", "a" * 40), lambda s: s.commit_diff("a" * 40, "HEAD~1")])
def test_every_read_takes_only_a_full_commit_id(demo, call):
    fake = FakeGitHub([])
    for src in (sources.LocalGit("demo", demo[0]), sources.GitHub("owner/repo", opener=fake, cache=sources._Cache())):
        with pytest.raises(sources.SourceError, match="named by its full id"):
            call(src)
    assert fake.asked == []


@pytest.mark.parametrize("call", [
    lambda s: s.list_dir("a" * 40, "../x"), lambda s: s.read_file("a" * 40, "tasks/../README.md"),
    lambda s: s.file_commits("a" * 40, "a//b", 5)])
def test_every_read_takes_only_a_path_inside_the_repository(demo, call):
    fake = FakeGitHub([])
    for src in (sources.LocalGit("demo", demo[0]), sources.GitHub("owner/repo", opener=fake, cache=sources._Cache())):
        with pytest.raises(sources.SourceError, match="inside the repository"):
            call(src)
    assert fake.asked == []


def test_an_answer_larger_than_the_ade_reads_is_refused(demo):
    big = b"x" * (sources.MAX_READ + 1)
    gh = sources.GitHub("owner/repo", opener=lambda u, h: (200, {}, big), cache=sources._Cache())
    with pytest.raises(sources.SourceError, match="larger than the ADE reads"):
        gh.read_file(SHA, "a.md")
    path, _ = demo
    write(path, "big.md", "x" * (sources.MAX_READ + 1))
    sha = commit(path, "big", "2026-09-27T11:00:00+00:00")
    with pytest.raises(sources.SourceError, match="larger than 2 MB"):
        sources.LocalGit("demo", path).read_file(sha, "big.md")


# ---- board ----------------------------------------------------------------------------------------------------
def _alerts(b):
    return [a for a in b["alerts"] if a["level"] == "alert"]


def test_silence_of_exactly_two_intervals_is_not_yet_an_alert():
    last = datetime(2026, 9, 27, 10, 20, 30, tzinfo=timezone.utc)   # the log's commit: its latest sign of life
    conn = {"branch": "work", "dirs": ["t"], "verify": False}
    assert _alerts(board.build_board(conn, board_demo(), last + timedelta(minutes=40))) == []
    assert len(_alerts(board.build_board(conn, board_demo(), last + timedelta(minutes=40, seconds=1)))) == 1


def test_an_unread_update_waits_one_whole_interval():
    newer = c(5, "2026-09-27T10:10:00Z", f"Handoff: step 2\n\n{CLOUD}", CLOUD_WHO)
    source = board_demo(extra_history={"t/HANDOFF-TO-LOCAL-SESSION.md": [newer]})
    assert _alerts(build(source, "2026-09-27T10:30Z")) == []       # 20 minutes: not past the 20-minute interval
    assert len(_alerts(build(source, "2026-09-27T10:31Z"))) == 1


def test_a_read_in_the_minute_of_the_change_counts_as_read():
    newer = c(5, "2026-09-27T10:10:30Z", f"Handoff: step 2\n\n{CLOUD}", CLOUD_WHO)
    log = (log_line("2026-09-27T10:10Z", "read HANDOFF-TO-LOCAL-SESSION.md")
           + log_line("2026-09-27T10:12Z", "heartbeat", "going", "no unread update"))
    b = build(board_demo(log=log, extra_history={"t/HANDOFF-TO-LOCAL-SESSION.md": [newer]}), "2026-09-27T10:20Z")
    assert b["channels"][0]["instructions"][0]["unread"] is False


def test_a_read_that_names_the_change_counts_even_when_logged_before_it():
    newer = c(5, "2026-09-27T10:10:00Z", f"Handoff: step 2\n\n{CLOUD}", CLOUD_WHO)
    log = log_line("2026-09-27T10:05Z", f"read HANDOFF-TO-LOCAL-SESSION.md @ {newer['short']}")
    b = build(board_demo(log=log, extra_history={"t/HANDOFF-TO-LOCAL-SESSION.md": [newer]}), "2026-09-27T10:20Z")
    assert b["channels"][0]["instructions"][0]["unread"] is False


def test_a_stop_ends_when_a_later_line_moves_on():
    log = (log_line("2026-09-27T10:05Z", "step 1", "WAITING FOR OWNER (Q-001): may it deploy?")
           + log_line("2026-09-27T10:10Z", "heartbeat", "going", "no unread update")
           + log_line("2026-09-27T10:15Z", "step 2", "deployed"))
    b = build(board_demo(log=log), "2026-09-27T10:20Z")
    assert b["channels"][0]["logs"][0]["waiting"] is None
    assert not [a for a in b["alerts"] if "waiting for" in a["text"]]


def test_a_trailing_heartbeat_does_not_unfinish_a_log():
    log = (log_line("2026-09-27T10:00Z", "heartbeat", "going", "no unread update")
           + log_line("2026-09-27T10:10Z", "deploy", "DONE: deployed")
           + log_line("2026-09-27T10:15Z", "heartbeat", "idle", "no unread update"))
    b = build(board_demo(log=log), "2026-09-27T20:00Z")
    assert b["channels"][0]["logs"][0]["finished"] is True
    assert not [a for a in b["alerts"] if "No new line" in a["text"]]


def test_a_log_above_reads_the_instructions_below_it():
    files = {"t/v/NEXT.md": "# Next\n", "other/NEXT.md": "# Other\n",
             "t/local-run-log.md": log_line("2026-09-27T10:00Z", "read NEXT.md from the top")}
    history = {"t/v/NEXT.md": [c(7, "2026-09-27T09:00:00Z", f"Next\n\n{CLOUD}", CLOUD_WHO)],
               "other/NEXT.md": [c(8, "2026-09-27T09:00:00Z", f"Other\n\n{CLOUD}", CLOUD_WHO)],
               "t/local-run-log.md": [c(9, "2026-09-27T10:00:00Z", f"Log\n\n{AGENT}")]}
    by_dir = {ch["name"]: ch for ch in build(Memory(files, history), "2026-09-27T12:00Z", dirs=("t", "t/v", "other"))["channels"]}
    assert by_dir["t/v"]["instructions"][0]["unread"] is False
    assert by_dir["other"]["instructions"][0]["unread"] is True


def test_the_interval_is_the_latest_declared_else_the_median_gap():
    median = "".join(log_line(t, "heartbeat", "going", "no unread update")
                     for t in ("2026-09-27T09:00Z", "2026-09-27T09:10Z", "2026-09-27T09:20Z"))
    assert build(board_demo(log=median), "2026-09-27T09:25Z")["channels"][0]["logs"][0]["interval_minutes"] == 10
    changed = (log_line("2026-09-27T09:00Z", "heartbeat", "heartbeat every 20 minutes", "no unread update")
               + log_line("2026-09-27T09:10Z", "heartbeat", "heartbeat every 45 minutes", "no unread update"))
    assert build(board_demo(log=changed), "2026-09-27T09:25Z")["channels"][0]["logs"][0]["interval_minutes"] == 45


def test_with_no_log_on_the_board_nothing_is_called_unread():
    files = {"t/NEXT.md": "# Next\n"}
    history = {"t/NEXT.md": [c(7, "2026-09-27T09:00:00Z", f"Next\n\n{CLOUD}", CLOUD_WHO)]}
    b = build(Memory(files, history), "2026-09-27T12:00Z")
    assert b["channels"][0]["instructions"][0]["unread"] is False and _alerts(b) == []


# ---- verifier -------------------------------------------------------------------------------------------------
class OneDiff:
    label = "owner/repo"

    def __init__(self, text):
        self.text = text

    def commit_diff(self, sha, parent):
        return self.text


def test_an_empty_or_oversized_change_is_skipped_not_an_error():
    one = commits.make_commit("a" * 40, ["b" * 40], "O", "o@example.org", "2026-09-27T10:00:00Z", "Subject")
    empty = verifier.verify_commit(OneDiff(""), one)
    assert empty["status"] == "skipped" and "no change" in empty["reason"]
    big = verifier.verify_commit(OneDiff("+" * (config.MAX_DIFF + 1)), one)
    assert big["status"] == "skipped" and "larger than" in big["reason"]


# ---- channel and commits --------------------------------------------------------------------------------------
def test_an_example_in_the_answers_header_is_not_an_answer():
    text = "# Answers\n\nOne line each, for example:\nW-1: here, retry\n\n---\nW-2: done\n"
    assert [(a["id"], a["text"]) for a in channel.read_answers("owner-answers.md", text)] == [("W-2", "done")]


def test_a_question_under_an_answered_section_is_processed():
    text = "## Answered\n\nWAITING FOR OWNER (W-1): which window?\n"
    assert channel.read_questions("questions.md", text)[0]["processed"] is True


def test_a_later_mention_supplies_the_commit():
    line = "- 2026-09-26T04:52Z · read NEXT.md from the top; NEXT.md @ 30bb9da again"
    assert channel.read_log_line(line)["reads"] == [{"path": "NEXT.md", "commit": "30bb9da"}]


def test_a_session_url_is_only_ever_https():
    one = commits.make_commit("a" * 40, [], "O", "o@example.org", "2026-09-27T10:00:00Z",
                              "x\n\nAgent-Session: javascript:alert(1)")
    assert one["session"]["kind"] == "session" and one["session"]["url"] is None


# ---- web ------------------------------------------------------------------------------------------------------
def test_the_hidden_rule_is_live_css_at_the_top_level():
    with open(os.path.join(HERE, "..", "web", "style.css"), encoding="utf-8") as fh:
        css = re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.S)
    depth, top = 0, []
    for ch in css:
        depth += ch == "{"
        depth -= ch == "}"
        top.append(ch if depth == 0 or ch == "{" and depth == 1 else "")
    assert "[hidden] {" in "".join(top) and "[hidden] { display: none !important; }" in css


def test_the_default_opener_maps_an_http_error_and_an_unreachable_host(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {"X-Test": "1"}, io.BytesIO(b"gone"))
    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    assert sources._default_opener("https://api.github.com/x", {}) == (404, {"X-Test": "1"}, b"gone")

    def unreachable(request, timeout):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(sources.SourceError, match="could not be reached"):
        sources._default_opener("https://api.github.com/x", {})


def test_an_unread_update_alerts_after_the_shortest_interval_on_the_board():
    newer = c(5, "2026-09-27T10:10:00Z", f"Handoff: step 2\n\n{CLOUD}", CLOUD_WHO)
    slow = {"u/local-run-log.md": log_line("2026-09-27T10:00Z", "heartbeat", "heartbeat every 60 minutes", "x")}
    source = board_demo(extra_files=slow, extra_history={"t/HANDOFF-TO-LOCAL-SESSION.md": [newer], "u/local-run-log.md": []})
    b = build(source, "2026-09-27T10:31Z", dirs=("t", "u"))
    assert [a for a in _alerts(b) if "changed 21 minutes ago" in a["text"]]


def test_the_board_links_only_to_claude_sessions():
    with open(os.path.join(HERE, "..", "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    # every link the page makes: a download's blob, claudeLink's, which refuses anything but claude.ai, a workspace
    # session's terminal page on this site, whose session id is only ever in the fragment, GitHub's page for a
    # sign-in's one-time code, a fixed address, never one read from what gh prints, and the
    # page Claude Code's sign-in prints, which claudeSignInLink refuses unless it is Claude's own sign-in address, and
    # the app's own pages of linked sessions and of approvals (where a held command is approved), fixed
    # addresses on this site
    assert sorted(re.findall(r"\.href = ([^;]+);", script)) == ['"/app/#approvals"', '"/app/#channel"',
                                                                  '"https://github.com/login/device"',
                                                                  '(x.chat ? "console.html#" : "terminal.html#") + x.id',
                                                                  "URL.createObjectURL(blob)", "link", "url"]
    assert re.search(r'function claudeSignInLink\(url\) \{\n\s*return typeof url === "string" && '
                     r'\(url\.startsWith\("https://claude\.com/cai/oauth/authorize\?"\) \|\|\n\s*'
                     r'url\.startsWith\("https://claude\.ai/oauth/authorize\?"\)\) \? url : null;\n\}', script)
    assert "const link = claudeSignInLink(" in script and script.count('.href = link;') == 1
    assert re.search(r'function claudeLink\(url, text\) \{\s*//[^\n]*\n\s*if \(!url \|\| '
                     r'!url\.startsWith\("https://claude\.ai/"\)\) return null;\s*const a = el\("a", text\);'
                     r'\s*a\.href = url;', script)
    # the function and its three uses: the sessions table, the pane header, and a linked session's card
    assert script.count("claudeLink(") == 4
    assert "claudeLink(l.session_ref, " in script


def test_github_lists_a_branch_s_commits():
    fake = FakeGitHub([(f"{API}/commits?", _ok([ITEM]))])
    (one,) = sources.GitHub("owner/repo", opener=fake, cache=sources._Cache()).branch_commits(SHA, 30)
    assert (one["sha"], one["parents"], one["session"]["kind"]) == (SHA, [PARENT], "cloud")
    assert [u for u, _ in fake.asked] == [f"{API}/commits?sha={SHA}&per_page=30"]


def test_the_board_says_when_it_was_read():
    assert build(board_demo(), "2026-09-27T10:30Z")["now"] == "2026-09-27T10:30:00Z"
