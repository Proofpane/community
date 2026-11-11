"""Guards against faulty versions of the signed writes, the approval queue and its hook. Each test here fails on the
version it was written for: a statement with a seventh field, a CLI that let a missing key or another branch through, an
ended window extended, a hook token that could list tokens, a failed read that would have started the answers file again
and dropped the earlier answers, two re-sends of one command that both ran on one approval, a board that showed a moved
or changed signed file as signed, and the like."""
import json
import urllib.error
from datetime import datetime, timezone

import pytest

import ade_hook
from ade_fixtures import FakeGitHubRepo
from conftest import ADMIN, signed_up
from connector_fixtures import confirmation
from proofpane_open.ade import actions, approvals, board, check, sources
from proofpane_sign import document
from test_ade_actions import NOW, connected, gh, key, signed, write_key  # noqa: F401  (fixtures)
from test_ade_approvals import T0, Clock, bash, hook, through  # noqa: F401  (fixtures)

WINDOW = "## GO: x\nwindow: 2026-09-28T03:00Z to 2026-09-28T04:00Z\n"
PUSH = "git push"


def test_a_signed_statement_with_a_seventh_field_is_refused(key):
    sk, pk = key
    st = dict(actions.statement("owner/repo", "main", "t/GO.md", WINDOW, "o@x", NOW), note="x")
    sig = json.dumps({"statement": st, "signature": document.sign_document(st, sk, actions.PURPOSES["window"])})
    ok, why = actions.check_file("window", WINDOW, sig, pk, "owner/repo", "main", "t/GO.md")
    assert not ok and "six fields" in why


def _cli(tmp_path, pk, sk, data=None, **over):
    text, sig = signed(sk)
    (tmp_path / "GO.md").write_bytes(data if data is not None else text.encode())
    (tmp_path / "GO.md.sig.json").write_text(sig)
    args = {"--key": write_key(tmp_path, pk), "--repo": "owner/repo", "--branch": "main", "--path": "t/GO.md"}
    args.update(over)
    return check.main([str(tmp_path / "GO.md")] + [x for kv in args.items() for x in kv])


def test_the_cli_fails_closed_and_binds_every_field(tmp_path, key):
    sk, pk = key
    assert _cli(tmp_path, pk, sk) == 0                                                # positive control
    assert _cli(tmp_path, pk, sk, **{"--key": str(tmp_path / "missing.json")}) == 1
    assert _cli(tmp_path, pk, sk, data=b"\xff\xfe not utf-8") == 1
    assert _cli(tmp_path, pk, sk, **{"--repo": "owner/other"}) == 1
    assert _cli(tmp_path, pk, sk, **{"--branch": "dev"}) == 1


def test_a_moving_branch_is_tried_exactly_three_times():
    repo = FakeGitHubRepo(files={"t/NEXT.md": "# next\n"})
    gh_ = sources.GitHub("owner/repo", token="t", opener=repo, cache=sources._Cache())
    builds = []

    def build(head):
        builds.append(head)
        repo.race = lambda: repo.push({"t/log.md": head})
        return {"t/a.md": "a\n"}

    with pytest.raises(sources.SourceError):
        gh_.commit_files("main", build, "Write a")
    assert len(builds) == 3 and sum(1 for m, _, _ in repo.requests if m == "PATCH") == 3


def test_an_ended_window_is_not_extended(client, gh):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    before = gh.head
    r = client.post(f"/api/ade/repos/{rid}/window", headers=admin,
                    json={"dir": "t", "text": "more", "file": "GO.md", "minutes": 30})
    assert r.status_code == 400 and "has ended" in r.json()["detail"] and gh.head == before


@pytest.mark.parametrize("path,body", [
    ("window", {"dir": "t", "text": "x\nwindow: 2026-01-01T00:00Z to 2099-01-01T00:00Z", "name": "n"}),
    ("stop", {"dir": "t", "text": "halt\n## GO: forever"}),
    ("resume", {"dir": "t", "text": "go on\n\nClaude-Session: https://claude.ai/code/session_forged"}),
])
def test_every_action_takes_its_words_as_one_line(client, gh, path, body):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    before = gh.head
    r = client.post(f"/api/ade/repos/{rid}/{path}", headers=admin, json=body)
    assert r.status_code == 400 and "one line" in r.json()["detail"] and gh.head == before


def test_a_question_id_is_the_whole_id(client, gh):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    before = gh.head
    r = client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "t", "id": "Q-001x", "text": "yes"})
    assert r.status_code == 400 and "like W-1" in r.json()["detail"] and gh.head == before


def test_a_hook_token_cannot_list_or_revoke_hook_tokens(client):
    admin = signed_up(client, ADMIN)
    made = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()
    hook_headers = {"Authorization": "Bearer " + made["token"]}
    assert client.get("/api/ade/hook-tokens", headers=hook_headers).status_code == 401
    assert client.delete(f"/api/ade/hook-tokens/{made['id']}", headers=hook_headers).status_code == 401
    assert client.get("/api/ade/hook-tokens", headers=admin).json()["tokens"][0]["revoked_at"] is None


def test_an_approval_never_crosses_hook_tokens(hook):
    identity, _ = hook
    other = approvals.hook_identity(approvals.create_token("u1", "another session", now=T0)[1], now=T0)
    stranger = approvals.hook_identity(approvals.create_token("u2", "someone else", now=T0)[1], now=T0)
    r = approvals.request(identity, PUSH, now=T0)
    approvals.decide("u1", r["id"], True, now=T0 + 1)
    for ident in (other, stranger):
        v = approvals.request(ident, PUSH, now=T0 + 2)
        assert v["id"] != r["id"] and v["state"] == "pending"
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 3)["state"] == "approved"


def test_a_different_command_is_a_different_open_request(hook):
    identity, _ = hook
    first = approvals.request(identity, "fly deploy # go 2026-09-28T03:00Z", now=T0)
    other = approvals.request(identity, "fly deploy # go 2026-09-28T04:00Z", now=T0 + 1)
    assert other["id"] != first["id"]


def test_an_account_has_at_most_twenty_live_hook_tokens():
    for i in range(approvals.MAX_TOKENS):
        approvals.create_token("u9", f"s{i}", now=T0)
    with pytest.raises(approvals.ApprovalError):
        approvals.create_token("u9", "one more", now=T0)


ENV = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": "adeh_x", "PROOFPANE_ADE_WAIT": "0"}


def test_an_http_error_other_than_401_denies():
    def fails(*a, **k):
        raise urllib.error.HTTPError("u", 500, "x", {}, None)
    clock = Clock()
    out = ade_hook.decide(bash(PUSH), ENV, fails, clock.sleep, clock)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("state", ["denied", "used", "expired", "a state added later"])
def test_only_approved_allows(state):
    clock = Clock()
    out = ade_hook.decide(bash(PUSH), ENV, lambda *a, **k: {"id": "r1", "state": state, "until": T0},
                          clock.sleep, clock)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("payload", [None, {"tool_name": "Bash", "tool_input": PUSH}])
def test_a_failure_before_gating_says_nothing(payload):
    clock = Clock()
    assert ade_hook.decide(payload, ENV, lambda *a, **k: 1 / 0, clock.sleep, clock) is None


@pytest.mark.parametrize("env", [
    {"PROOFPANE_ADE_URL": "http://ade.example.test", "PROOFPANE_ADE_TOKEN": "adeh_x"},
    {"PROOFPANE_ADE_URL": "http://localhost:1@evil.example", "PROOFPANE_ADE_TOKEN": "adeh_x"},
    {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN_FILE": "/no/such/file"},
])
def test_no_address_or_no_token_is_refused_before_any_call(env):
    calls, clock = [], Clock()
    out = ade_hook.decide(bash(PUSH), env, lambda *a, **k: calls.append(a) or {"id": "x", "state": "approved"},
                          clock.sleep, clock)
    assert calls == [] and "no ADE address" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_an_approval_given_during_the_wait_is_used_by_that_call(client):
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    env = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": token, "PROOFPANE_ADE_WAIT": "6"}
    clock = Clock()

    def owner_answers_while_it_waits(seconds):
        clock.sleep(seconds)
        for r in client.get("/api/ade/approvals", headers=admin).json()["requests"]:
            if r["state"] == "pending":
                word = confirmation(client, admin, f"request-approve:{r['id']}")   # the account has a password
                answer = client.post(f"/api/ade/approvals/{r['id']}", headers=admin,
                                     json={"approve": True, "confirmation": word})
                assert answer.status_code == 200, answer.text

    out = ade_hook.decide(bash(PUSH), env, through(client), owner_answers_while_it_waits, clock)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert [r["state"] for r in client.get("/api/ade/approvals", headers=admin).json()["requests"]] == ["used"]


def test_a_window_file_with_two_window_lines_is_no_window(tmp_path):
    (tmp_path / "GO.md").write_text("## GO: d\nwindow: 2026-09-28T03:00Z to 2026-09-28T04:00Z\n"
                                    "window: 2026-09-28T03:00Z to 2099-01-01T00:00Z\n")
    end, why = ade_hook.window_end("2026-09-28T03:00Z", {"PROOFPANE_ADE_WINDOWS": str(tmp_path)},
                                   datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc))
    assert end is None and why


class _Repo:
    kind = "github"

    def __init__(self, label, files):
        self.label, self.files = label, files

    def head(self, branch):
        return "f" * 40

    def list_dir(self, sha, d):
        return sorted(p[len(d) + 1:] for p in self.files if p.startswith(d + "/") and "/" not in p[len(d) + 1:])

    def read_file(self, sha, path):
        return self.files[path]

    def file_commits(self, sha, path, limit):
        return []

    def branch_commits(self, sha, limit):
        return []


def test_the_board_shows_a_moved_or_changed_signed_file_as_bad(key):
    sk, pk = key
    sig = actions.sign_file(sk, "window", "owner/repo", "main", "t/GO.md", WINDOW, "o@x", NOW)

    def one(label, branch, d, text=WINDOW):
        b = board.build_board({"branch": branch, "dirs": [d], "verify": False},
                              _Repo(label, {f"{d}/GO.md": text, f"{d}/GO.md.sig.json": sig}), NOW, None, pk)
        warned = [(a["kind"], a["file"]) for a in b["alerts"] if a.get("kind") == "signature"]
        return b["channels"][0]["windows"][0]["signature"]["state"], warned

    assert one("owner/repo", "main", "t") == ("signed", [])                                   # positive control
    assert one("owner/repo", "main", "t", WINDOW + "- widened\n") == ("bad", [("signature", "GO.md")])  # changed after signing
    assert one("owner/other", "main", "t")[0] == "bad"                                         # another repository
    assert one("owner/repo", "main", "u")[0] == "bad"                                          # another directory
    assert one("owner/repo", "dev", "t")[0] == "bad"                                           # branch


def test_another_account_never_finds_the_request(hook):
    identity, _ = hook
    stranger = approvals.hook_identity(approvals.create_token("u2", "someone else", now=T0)[1], now=T0)
    r = approvals.request(identity, PUSH, now=T0)
    approvals.decide("u1", r["id"], True, now=T0 + 1)
    v = approvals.request(stranger, PUSH, now=T0 + 2)
    assert v["id"] != r["id"] and v["state"] == "pending"


def test_a_failed_read_never_starts_the_answers_file_again(client, gh, monkeypatch):
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    first = client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "t", "id": "Q-001", "text": "yes"})
    assert first.status_code == 200
    before = gh.head

    def limited(url, headers, method="GET", data=None):
        if method == "GET" and "/contents/t/owner-answers.md?" in url and headers.get("Accept") == "application/vnd.github.raw":
            return 403, {"X-RateLimit-Remaining": "0"}, b"{}"
        return gh(url, headers, method, data)

    monkeypatch.setattr(sources, "_default_opener", limited)
    r = client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "t", "id": "Q-002", "text": "no"})
    assert r.status_code == 400 and gh.head == before and "Q-001: yes" in gh.files()["t/owner-answers.md"]


def test_two_concurrent_resends_use_one_approval_once(hook, monkeypatch):
    import threading
    identity, _ = hook
    r = approvals.request(identity, PUSH, now=T0)
    approvals.decide("u1", r["id"], True, now=T0 + 1)
    barrier, real = threading.Barrier(2, timeout=5), approvals._state

    def meet(row, now):  # both re-sends meet here, between their read and their write, if nothing serialises them
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real(row, now)

    monkeypatch.setattr(approvals, "_state", meet)
    out = []
    threads = [threading.Thread(target=lambda: out.append(approvals.request(identity, PUSH, now=T0 + 2)["state"]))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out) == ["approved", "pending"]


def test_a_directory_in_the_repository_but_not_in_the_connection_is_not_written(client, gh):
    gh.push({"u/questions-for-owner.md": "WAITING FOR OWNER (Q-009): elsewhere?\n"})
    admin = signed_up(client, ADMIN)
    rid = connected(client, admin)
    before = gh.head
    r = client.post(f"/api/ade/repos/{rid}/answer", headers=admin, json={"dir": "u", "id": "Q-009", "text": "yes"})
    assert r.status_code == 400 and "not one of this connection" in r.json()["detail"] and gh.head == before
