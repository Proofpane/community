"""The ADE through its HTTP surface: who may connect what, that one account never reaches another's connection, that
a token is only ever stored sealed, and Verify: each commit verified and signed, a merge or a first commit skipped
with its reason and never passed."""

import base64
import os

import pytest

from ade_fixtures import BRANCH, make_repo
from conftest import ADMIN, signed_up
from proofpane_open import tools
from proofpane_open.ade import commits, ledger, verifier

KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    path, shas = make_repo(tmp_path)
    monkeypatch.setenv("OPEN_EDITION_ADE_LOCAL_REPOS", f"demo={path}")
    return path, shas


def connect(client, headers, **body):
    payload = {"source": "local", "repo": "demo", "branch": BRANCH, "dirs": ["tasks/demo"], "verify": True}
    payload.update(body)
    return client.post("/api/ade/repos", json=payload, headers=headers)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/ade/repos"), ("post", "/api/ade/repos"), ("delete", "/api/ade/repos/x"),
    ("get", "/api/ade/repos/x/board"), ("post", "/api/ade/repos/x/verify"),
    ("get", "/api/ade/repos/x/verifications/" + "a" * 40)])
def test_every_ade_route_needs_an_account(client, method, path):
    kwargs = {"json": {"repo": "o/r", "branch": "main"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_a_clone_is_connected_only_by_an_admin_and_only_by_its_name(client, demo):
    member = signed_up(client, "member@example.org")
    admin = signed_up(client, ADMIN)
    r = connect(client, member)
    assert r.status_code == 400 and "Only an admin" in r.json()["detail"]
    assert client.get("/api/ade/repos", headers=member).json()["local"] == []
    assert client.get("/api/ade/repos", headers=admin).json()["local"] == ["demo"]
    r = connect(client, admin, repo="elsewhere")
    assert r.status_code == 400 and "names no such clone" in r.json()["detail"]
    r = connect(client, admin)
    assert r.status_code == 200, r.text
    listed = client.get("/api/ade/repos", headers=admin).json()["repos"]
    assert [(x["repo"], x["dirs"], x["verify"], x["has_token"]) for x in listed] == [
        ("demo", ["tasks/demo"], True, False)]


@pytest.mark.parametrize("change,words", [
    ({"dirs": ["tasks/none"]}, "is not a directory"),
    ({"dirs": ["../etc"]}, "inside the repository"),
    ({"branch": "missing"}, "has no branch missing"),
    ({"branch": "a..b"}, "not a branch name"),
    ({"dirs": [], "verify": False}, "at least one channel directory"),
    ({"source": "ftp"}, "on GitHub, or a clone"),
])
def test_a_connection_that_cannot_be_read_is_not_stored(client, demo, change, words):
    admin = signed_up(client, ADMIN)
    r = connect(client, admin, **change)
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert client.get("/api/ade/repos", headers=admin).json()["repos"] == []


def test_the_board_and_verify_through_the_api(client, demo):
    path, shas = demo
    admin = signed_up(client, ADMIN)
    repo_id = connect(client, admin).json()["id"]
    b = client.get(f"/api/ade/repos/{repo_id}/board", headers=admin).json()
    assert b["head"] == shas["merge"] and b["connection"]["id"] == repo_id
    (ch,) = b["channels"]
    assert ch["windows"][0]["name"] == "demo deploy" and ch["logs"][0]["waiting"]["ids"] == ["Q-001"]
    assert [q["id"] for q in ch["questions"] if q["open"]] == ["Q-001"]
    assert ch["instructions"][0]["unread"] is False  # the log read the handoff at the commit that wrote it
    assert [x["verification"] for x in b["commits"]] == [None] * 6

    first = client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin).json()
    assert [d["sha"] for d in first["done"]] == [shas[k] for k in ("first", "handoff", "log", "code", "side")]
    assert first["remaining"] == 1
    assert first["done"][0]["status"] == "skipped" and "first commit" in first["done"][0]["reason"]
    second = client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin).json()
    assert [(d["sha"], d["status"]) for d in second["done"]] == [(shas["merge"], "skipped")]
    assert "merge commit" in second["done"][0]["reason"] and second["remaining"] == 0
    assert client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin).json() == {"done": [], "remaining": 0}

    record = client.get(f"/api/ade/repos/{repo_id}/verifications/{shas['code']}", headers=admin).json()
    assert record["status"] == "verified" and record["digest"].startswith("sha256:")
    # a commit carries no CI result, so the gate asks for a person and names the check it could not see
    assert record["rule"] == "needs-person" and record["verify"] == "failure"
    assert [(b["resolver"], b["outcome"]) for b in record["because"]] == [("ci.checks_green", "unverified")]
    assert [b["id"] for b in record["because"]] == record["manifest"]["decision"]["because"]
    subject = record["manifest"]["subject"]
    assert (subject["repo"], subject["base_sha"], subject["head_sha"]) == ("local/demo", shas["log"], shas["code"])
    assert tools.check_signature(record["manifest"], record["signature"])["ok"] is True
    record["manifest"]["subject"]["head_sha"] = shas["side"]
    assert tools.check_signature(record["manifest"], record["signature"])["ok"] is False

    b = client.get(f"/api/ade/repos/{repo_id}/board", headers=admin).json()
    by_sha = {x["sha"]: x["verification"] for x in b["commits"]}
    assert by_sha[shas["code"]]["status"] == "verified" and by_sha[shas["merge"]]["status"] == "skipped"
    assert "manifest" not in by_sha[shas["code"]]   # the board carries summaries; a manifest is fetched on its own


def test_another_account_never_reaches_a_connection(client, demo):
    admin = signed_up(client, ADMIN)
    member = signed_up(client, "member@example.org")
    repo_id = connect(client, admin).json()["id"]
    client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin)
    sha = demo[1]["code"]
    for call in (lambda: client.get(f"/api/ade/repos/{repo_id}/board", headers=member),
                 lambda: client.post(f"/api/ade/repos/{repo_id}/verify", headers=member),
                 lambda: client.get(f"/api/ade/repos/{repo_id}/verifications/{sha}", headers=member),
                 lambda: client.delete(f"/api/ade/repos/{repo_id}", headers=member)):
        assert call().status_code == 404
    assert client.get("/api/ade/repos", headers=member).json()["repos"] == []
    assert client.get(f"/api/ade/repos/{repo_id}/board", headers=admin).status_code == 200


def test_disconnecting_forgets_the_results(client, demo):
    admin = signed_up(client, ADMIN)
    repo_id = connect(client, admin).json()["id"]
    client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin)
    assert ledger.verification_summaries(repo_id)
    assert client.delete(f"/api/ade/repos/{repo_id}", headers=admin).json() == {"ok": True}
    assert ledger.verification_summaries(repo_id) == {}
    assert client.delete(f"/api/ade/repos/{repo_id}", headers=admin).status_code == 404


def test_verify_can_be_turned_off(client, demo):
    admin = signed_up(client, ADMIN)
    repo_id = connect(client, admin, verify=False).json()["id"]
    assert client.get(f"/api/ade/repos/{repo_id}/board", headers=admin).json()["commits"] == []
    assert client.post(f"/api/ade/repos/{repo_id}/verify", headers=admin).status_code == 400


def test_without_a_key_store_key_no_token_is_taken(client, monkeypatch):
    monkeypatch.delenv("OPEN_EDITION_KEYSTORE_KEY", raising=False)
    admin = signed_up(client, ADMIN)
    r = client.post("/api/ade/repos", headers=admin, json={"repo": "owner/private", "branch": "main",
                                                           "dirs": ["x"], "token": "github_pat_secret"})
    assert r.status_code == 400 and "cannot store a token" in r.json()["detail"]
    assert client.get("/api/ade/repos", headers=admin).json()["can_store_tokens"] is False


def test_a_token_is_stored_sealed_and_opens_only_in_its_own_row(client, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", KEY)
    user = {"id": "u1", "admin": False}
    row = ledger.add(user, "github", "owner/private", "main", ["x"], False, token="github_pat_secret")
    assert row["has_token"] is True and "token" not in str(row).lower().replace("has_token", "")
    from proofpane_open import store
    with store.connect() as db:
        box = db.execute("SELECT token_box FROM ade_repos WHERE id = ?", (row["id"],)).fetchone()[0]
    assert "github_pat_secret" not in box and box.startswith("v1.")
    assert ledger.unseal(box, "u1", row["id"]) == "github_pat_secret"
    assert ledger.source_for("u1", row["id"]).token == "github_pat_secret"
    for user_id, repo_id in (("u2", row["id"]), ("u1", "another-row")):
        with pytest.raises(ledger.LedgerError):
            ledger.unseal(box, user_id, repo_id)
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", base64.b64encode(bytes(32)).decode())
    with pytest.raises(ledger.LedgerError):
        ledger.unseal(box, "u1", row["id"])
    assert ledger.source_for("u2", row["id"]) is None


@pytest.mark.parametrize("value", ["short", base64.b64encode(bytes(31)).decode(), "!!!!"])
def test_a_key_store_key_that_is_set_wrong_is_an_error_not_off(monkeypatch, value):
    monkeypatch.setenv("OPEN_EDITION_KEYSTORE_KEY", value)
    with pytest.raises(ledger.LedgerError):
        ledger.keystore_key()


def test_the_claim_is_the_message_without_its_trailers():
    c = commits.make_commit("a" * 40, ["b" * 40], "Owner", "o@example.org", "2026-09-27T10:00:00Z",
                            "Add a test\n\nThe change adds tests.\n\nMore words.\n\n"
                            "Co-Authored-By: Claude <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/x")
    assert verifier._description(c) == "The change adds tests.\n\nMore words."
    kept = commits.make_commit("a" * 40, ["b" * 40], "O", "o@example.org", "2026-09-27T10:00:00Z",
                               "Subject\n\nNote: this line is prose\nand this one is not a trailer")
    assert verifier._description(kept) == "Note: this line is prose\nand this one is not a trailer"


def test_the_hidden_attribute_wins_over_every_display_rule():
    """`nav` is flex and `label` is block in the stylesheet, which beat the browser's own `[hidden]` rule: the
    navigation showed before sign-in, the Check page's signature field never hid, and neither did the ADE's clone
    and token fields. One rule at the top of the stylesheet fixes all of them."""
    with open(os.path.join(os.path.dirname(__file__), "..", "web", "style.css"), encoding="utf-8") as fh:
        css = fh.read()
    assert "[hidden] { display: none !important; }" in css
