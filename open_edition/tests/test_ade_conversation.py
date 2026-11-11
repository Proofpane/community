"""The conversation view: the channel as two transcripts, the cloud session's and the local session's."""

import base64
import io
from contextlib import redirect_stdout

import pytest

from proofpane_open.ade import channel, commits, conversation
from test_ade_board import AGENT, CLOUD, CLOUD_WHO, Memory, at, build, c, demo, log_line

HANDOFF = """# Handoff

## What to do

Run step 1.

## Added by the cloud session at 23:50 UTC: before midnight

Late words.

## Added by the cloud session at 00:10 UTC: after midnight

Early words.

## Added by a second cloud session at 09:00 UTC: the newest

Newest words.
"""


def _channel(history, log="", questions="", answers=""):
    files = {"HANDOFF-TO-LOCAL-SESSION.md": HANDOFF}
    if log:
        files["local-run-log.md"] = log
    if questions:
        files["questions-for-owner.md"] = questions
    if answers:
        files["owner-answers.md"] = answers
    ch = channel.read_channel("t", files)
    ch["history"] = history
    return ch


def test_a_handoff_section_is_dated_by_the_commit_that_wrote_it():
    second = ("Claude", "noreply@anthropic.com")
    history = {"HANDOFF-TO-LOCAL-SESSION.md": [
        c(3, "2026-09-28T09:02:00Z", "Newest\n\nClaude-Session: https://claude.ai/code/session_01SECONDxyz", second),
        c(2, "2026-09-28T00:11:30Z", f"After midnight\n\n{CLOUD}", CLOUD_WHO),
        c(1, "2026-09-27T23:51:00Z", f"Before midnight\n\n{CLOUD}", CLOUD_WHO)]}
    items = conversation.build([_channel(history)])["cloud"]["items"]
    assert [(i["title"], i["time"], i["who"]) for i in items] == [
        ("What to do", None, None),
        ("Added by the cloud session at 23:50 UTC: before midnight", "2026-09-27T23:51Z", "cloud session 1BOARD"),
        ("Added by the cloud session at 00:10 UTC: after midnight", "2026-09-28T00:11Z", "cloud session 1BOARD"),
        ("Added by a second cloud session at 09:00 UTC: the newest", "2026-09-28T09:02Z", "cloud session ONDxyz")]
    assert items[3]["text"] == "Newest words." and items[0]["text"] == "Run step 1."


def test_a_section_older_than_the_commits_read_keeps_its_place_but_no_date():
    history = {"HANDOFF-TO-LOCAL-SESSION.md": [c(3, "2026-09-28T09:05:00Z", f"Newest\n\n{CLOUD}", CLOUD_WHO)]}
    items = conversation.build([_channel(history)])["cloud"]["items"]
    assert [i["time"] for i in items] == [None, None, None, "2026-09-28T09:05Z"]
    assert items[3]["title"].endswith("the newest") and items[1]["title"].endswith("before midnight")
    assert items[1]["at"] == "23:50"   # undated, the heading's own time is still shown


def test_a_commit_is_used_for_one_section_only_and_never_out_of_order():
    history = {"HANDOFF-TO-LOCAL-SESSION.md": [c(2, "2026-09-28T00:05:00Z", f"x\n\n{CLOUD}", CLOUD_WHO)]}
    items = conversation.build([_channel(history)])["cloud"]["items"]
    # 00:05 is near both 23:50 and 00:10: the newer section takes it, and the older one is left undated
    assert [i["time"] for i in items] == [None, None, "2026-09-28T00:05Z", None]


def test_the_local_pane():
    log = (log_line("2026-09-27T10:00Z", "heartbeat", "waiting", "no unread update")
           + log_line("2026-09-27T10:20Z", "heartbeat", "still waiting", "handoff has an unread update")
           + log_line("2026-09-27T10:21Z", "read HANDOFF-TO-LOCAL-SESSION.md @ abc1234")
           + log_line("2026-09-27T10:22Z", "step 1", "set up the venv")
           + log_line("2026-09-27T10:23Z", "step 2", "WAITING FOR OWNER (Q-001): may it deploy?")
           + log_line("2026-09-27T10:40Z", "heartbeat", "waiting for Q-001", "no unread update")
           + log_line("2026-09-27T10:50Z", "deploy", "DONE: deployed"))
    questions = "WAITING FOR OWNER (Q-001): may it deploy?\n\nWAITING FOR OWNER (Q-002): and then?\n"
    history = {"local-run-log.md": [c(5, "2026-09-27T10:50:10Z", f"Log\n\n{AGENT}")]}
    items = conversation.build([_channel(history, log, questions, "---\nQ-001: yes, go ahead\n")])["local"]["items"]
    # undated first (Q-002), then by time; a question the log's stop names is dated by that stop
    assert [(i["kind"], i["time"]) for i in items] == [
        ("question", None), ("heartbeats", "2026-09-27T10:20Z"), ("tool", "2026-09-27T10:21Z"),
        ("message", "2026-09-27T10:22Z"), ("waiting", "2026-09-27T10:23Z"), ("question", "2026-09-27T10:23Z"),
        ("heartbeats", "2026-09-27T10:40Z"), ("done", "2026-09-27T10:50Z")]
    assert items[0]["id"] == "Q-002"
    beats = [i for i in items if i["kind"] == "heartbeats"]
    assert [(b["count"], b["first"], b["time"], b["unread"]) for b in beats] == [
        (2, "2026-09-27T10:00Z", "2026-09-27T10:20Z", True), (1, "2026-09-27T10:40Z", "2026-09-27T10:40Z", False)]
    read = next(i for i in items if i["kind"] == "tool")
    assert read["name"] == "Read" and read["arg"] == "HANDOFF-TO-LOCAL-SESSION.md @ abc1234"
    step = next(i for i in items if i["kind"] == "message")
    assert (step["title"], step["text"], step["who"]) == ("step 1", "set up the venv", "agent session as Owner")
    q1 = next(i for i in items if i.get("id") == "Q-001")
    assert (q1["time"], q1["answer"], q1["state"]) == ("2026-09-27T10:23Z", "yes, go ahead", "answered")
    assert next(i for i in items if i.get("id") == "Q-002")["state"] == "open"
    assert items[-1]["kind"] == "done" and items[-1]["text"] == "DONE: deployed"


def test_a_pane_keeps_its_last_items_and_counts_the_rest(monkeypatch):
    monkeypatch.setattr(conversation, "LIMIT", 3)
    log = "".join(log_line(f"2026-09-27T10:{m:02d}Z", f"step {m}", "did it") for m in range(10, 16))
    pane = conversation.build([_channel({}, log)])["local"]
    assert pane["earlier"] == 3 and [i["title"] for i in pane["items"]] == ["step 13", "step 14", "step 15"]


def test_the_board_carries_the_conversation():
    b = build(demo(), "2026-09-27T10:30Z")
    conv = b["conversation"]
    assert [i["kind"] for i in conv["cloud"]["items"]][-1] == "tool"      # the window the cloud session wrote
    assert conv["cloud"]["items"][-1]["name"] == "Window"
    local = conv["local"]["items"]
    assert [i["kind"] for i in local] == ["tool", "heartbeats", "waiting", "question", "heartbeats"]


def test_a_local_session_that_names_itself():
    s = commits.make_commit("a" * 40, ["b" * 40], "Owner", "o@example.org", "2026-09-27T10:00:00Z",
                            "Log\n\nCo-Authored-By: Claude <noreply@anthropic.com>\nLocal-Session: 5e5510a1-b2c3")["session"]
    assert (s["kind"], s["id"], s["name"]) == ("local", "local:5e5510a1-b2c3", "local session 5e5510a1")


def test_ade_status_summarises_without_any_secret(client, monkeypatch, tmp_path):
    from ade_fixtures import BRANCH, make_repo
    from conftest import ADMIN, signed_up
    from proofpane_open import __main__ as cli
    from proofpane_open.ade import ledger

    path, _ = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", base64.b64encode(bytes(range(32))).decode())
    headers = signed_up(client, ADMIN)
    me = client.get("/api/auth/me", headers=headers).json()
    ledger.add(dict(me, admin=True), "local", "demo", BRANCH, ["tasks/demo"], False)
    ledger.add(dict(me, admin=True), "github", "owner/private", "main", ["x"], False, token="github_pat_SECRET")
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["ade-status", ADMIN]) == 0
    text = out.getvalue()
    assert "2 connection(s)" in text and '"repo": "demo"' in text and '"has_token": true' in text
    assert "board: head" in text and "local pane" in text and "board: failed" in text   # the fake repo cannot be read
    assert "SECRET" not in text and "github_pat" not in text
    assert cli.main(["ade-status", "nobody@example.org"]) == 1
