"""Who made a commit, from its author and trailers."""

import pytest

from proofpane_open.ade import commits

CLOUD = ("State: a fourth stop recorded\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
         "Claude-Session: https://claude.ai/code/session_01ABCdefGHIjkl")
LOCAL = "local heartbeat at 14:12Z (c801a09)\n\nCo-Authored-By: Claude (1M context) <noreply@anthropic.com>"


def test_trailers_are_the_last_paragraph():
    assert commits.trailers(CLOUD) == [("Co-Authored-By", "Claude <noreply@anthropic.com>"),
                                       ("Claude-Session", "https://claude.ai/code/session_01ABCdefGHIjkl")]
    assert commits.trailers("Subject: with a colon") == []
    assert commits.trailers("subject\n\nbody: in the middle\n\nnot a trailer line\nKey: value") == [("Key", "value")]
    assert commits.trailers("subject\r\n\r\nSigned-off-by: A <a@example.org>\r\n") == [
        ("Signed-off-by", "A <a@example.org>")]


@pytest.mark.parametrize("name,email,message,kind,sid", [
    ("Claude", "noreply@anthropic.com", CLOUD, "cloud", "https://claude.ai/code/session_01ABCdefGHIjkl"),
    ("Owner", "Owner@Example.org", LOCAL, "agent", "agent:owner@example.org"),
    ("Owner", "owner@example.org", "fix a typo", "person", "person:owner@example.org"),
    ("Owner", "owner@example.org", "x\n\nCo-Authored-By: Alice <alice@example.org>", "person", "person:owner@example.org"),
    ("Owner", "owner@example.org", "x\n\nCo-authored-by: Codex <codex@openai.com>", "agent", "agent:owner@example.org"),
    ("Owner", "owner@example.org", "x\n\nAgent-Session: mac-1", "session", "mac-1"),
    ("Owner", "owner@example.org", "answer W-2\n\nProofpane-ADE: sha256:abc", "ade", "ade:sha256:abc"),
])
def test_the_kind_of_session_a_commit_names(name, email, message, kind, sid):
    c = commits.make_commit("a" * 40, ["b" * 40], name, email, "2026-09-28T03:12:08+13:00", message)
    assert (c["session"]["kind"], c["session"]["id"]) == (kind, sid)
    assert c["time"] == "2026-09-27T14:12:08Z" and c["short"] == "aaaaaaa"


def test_a_cloud_session_links_to_itself():
    s = commits.make_commit("c" * 40, [], "Claude", "noreply@anthropic.com", "2026-09-27T14:19:07Z", CLOUD)["session"]
    assert s["url"] == "https://claude.ai/code/session_01ABCdefGHIjkl" and s["name"] == "cloud session GHIjkl"


@pytest.mark.parametrize("value,want", [
    ("2026-09-27T14:19:07Z", "2026-09-27T14:19:07Z"), ("2026-09-28T03:12:08+13:00", "2026-09-27T14:12:08Z"),
    ("2026-09-27T14:19:07", None), ("yesterday", None), ("", None), (None, None)])
def test_times_are_utc(value, want):
    assert commits.utc(value) == want


def test_a_github_item():
    item = {"sha": "d" * 40, "parents": [{"sha": "e" * 40}],
            "commit": {"author": {"name": "Claude", "email": "noreply@anthropic.com", "date": "2026-09-27T14:19:07Z"},
                       "message": CLOUD}}
    c = commits.from_github(item)
    assert c["parents"] == ["e" * 40] and c["subject"] == "State: a fourth stop recorded"
    assert c["session"]["kind"] == "cloud"
