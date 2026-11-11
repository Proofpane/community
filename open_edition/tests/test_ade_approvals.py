"""The ADE's approval queue and the hook that waits on it. One approval runs a command once; a request
stays open until it is answered or its time ends; a hook token can only ask, and only its own account's owner can
answer; a gated command is never let through because the check could not be made."""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import ade_hook
import hook_templates
from conftest import ADMIN, signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import approvals

T0 = 1_790_000_000


@pytest.fixture
def hook():
    token_id, token = approvals.create_token("u1", "the Mac session", now=T0)
    return approvals.hook_identity(token, now=T0), token


def test_a_hook_token_is_shown_once_stored_hashed_and_revocable(hook):
    identity, token = hook
    assert token.startswith("adeh_") and identity["user_id"] == "u1" and identity["name"] == "the Mac session"
    from proofpane_open import store
    with store.connect() as db:
        stored = [r[0] for r in db.execute("SELECT token_hash FROM ade_hook_tokens")]
    assert token not in stored and len(stored[0]) == 64
    assert approvals.hook_identity("adeh_not-a-real-token") is None and approvals.hook_identity("x") is None
    assert approvals.revoke_token("u2", identity["id"]) is False       # another account cannot
    assert approvals.revoke_token("u1", identity["id"]) is True
    assert approvals.hook_identity(token) is None


def test_one_approval_runs_the_command_once(hook):
    identity, _ = hook
    r = approvals.request(identity, "git push origin work", now=T0)
    assert r["state"] == "pending" and r["until"] == T0 + approvals.DEFAULT_TTL
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 1)["state"] == "pending"
    approvals.decide("u1", r["id"], True, "go", now=T0 + 2)
    first = approvals.read_for_hook(identity, r["id"], now=T0 + 3)
    assert first["state"] == "approved" and first["words"] == "go"
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 4)["state"] == "used"
    again = approvals.request(identity, "git push origin work", now=T0 + 5)
    assert again["id"] != r["id"] and again["state"] == "pending"   # a used approval does not carry over


def test_the_same_command_again_finds_the_open_request(hook):
    identity, _ = hook
    r = approvals.request(identity, "fly deploy # go 2026-09-28T03:00Z", now=T0)
    approvals.decide("u1", r["id"], True, now=T0 + 100)             # answered while no hook was waiting
    again = approvals.request(identity, "fly deploy # go 2026-09-28T03:00Z", now=T0 + 200)
    assert again["id"] == r["id"] and again["state"] == "approved"
    # the send that learnt of the approval used it up: a read, or a third send, does not find it usable again
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 201)["state"] == "used"
    third = approvals.request(identity, "fly deploy # go 2026-09-28T03:00Z", now=T0 + 202)
    assert third["id"] != r["id"] and third["state"] == "pending"
    other = approvals.request(identity, "fly deploy # go 2026-09-28T04:00Z", now=T0 + 202)
    assert other["id"] != r["id"]


@pytest.mark.parametrize("field, here, there", [
    ("cwd", "/home/ws/workspace/sessions/a/work", "/home/ws/workspace/sessions/b/work"),
    ("session", "5e55104f-aaaa", "5e55104f-bbbb"),
    ("tool", "Bash", "Other")])
def test_an_approval_is_for_the_request_as_the_owner_saw_it(hook, field, here, there):
    # the owner approved the command as shown with its folder and session; the
    # same command sent from another (a shared hook token, a session that changed directory) must not use it up and
    # run there, but asks again
    identity, _ = hook
    base = {"session": "5e55104f-aaaa", "cwd": "/home/ws/workspace/sessions/a/work", "tool": "Bash"}
    shown = approvals.request(identity, "rm -rf build", **dict(base, **{field: here}), now=T0)
    approvals.decide("u1", shown["id"], True, "go", now=T0 + 1)
    elsewhere = approvals.request(identity, "rm -rf build", **dict(base, **{field: there}), now=T0 + 2)
    assert elsewhere["id"] != shown["id"] and elsewhere["state"] == "pending" and elsewhere[field] == there
    # the approval is still there for the request it was given for, once
    same = approvals.request(identity, "rm -rf build", **dict(base, **{field: here}), now=T0 + 3)
    assert same["id"] == shown["id"] and same["state"] == "approved"
    assert approvals.request(identity, "rm -rf build", **dict(base, **{field: here}), now=T0 + 4)["state"] == "pending"


def test_a_resend_from_the_same_session_and_folder_finds_its_request_and_one_without_them_too(hook):
    identity, _ = hook
    where = {"session": "5e55104f-aaaa", "cwd": "/home/ws/workspace/sessions/a/work"}
    first = approvals.request(identity, "git push", **where, now=T0)
    resends = {approvals.request(identity, "git push", **where, now=T0 + i)["id"] for i in range(1, 6)}
    assert resends == {first["id"]}                  # one open request, however often the hook asks
    # a hook that sends neither a session nor a folder, as before: its resends find its request, and a request with
    # them does not stand for one without
    bare = approvals.request(identity, "git push", now=T0 + 10)
    assert bare["id"] != first["id"] and (bare["session"], bare["cwd"]) == (None, None)
    assert approvals.request(identity, "git push", session="", cwd="", now=T0 + 11)["id"] == bare["id"]
    approvals.decide("u1", bare["id"], True, now=T0 + 12)
    assert approvals.request(identity, "git push", now=T0 + 13)["state"] == "approved"
    assert approvals.request(identity, "git push", **where, now=T0 + 14)["id"] == first["id"]     # still pending


def test_a_request_ends_with_its_time(hook):
    identity, _ = hook
    r = approvals.request(identity, "git push", until=T0 + 600, now=T0)
    assert r["until"] == T0 + 600
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 600)["state"] == "expired"
    with pytest.raises(approvals.ApprovalError):
        approvals.decide("u1", r["id"], True, now=T0 + 601)
    capped = approvals.request(identity, "git push --tags", until=T0 + 10 * 24 * 3600, now=T0)
    assert capped["until"] == T0 + approvals.MAX_TTL
    with pytest.raises(approvals.ApprovalError):
        approvals.request(identity, "git push -f", until=T0 - 1, now=T0)
    fresh = approvals.request(identity, "git push", now=T0 + 700)
    assert fresh["id"] != r["id"]                                    # an expired request is not reused


def test_an_approved_request_not_read_in_time_expires_unused(hook):
    identity, _ = hook
    r = approvals.request(identity, "git push", until=T0 + 60, now=T0)
    approvals.decide("u1", r["id"], True, now=T0 + 10)
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 61)["state"] == "expired"


def test_only_a_pending_request_is_answered_and_only_by_its_owner(hook):
    identity, _ = hook
    r = approvals.request(identity, "git push", now=T0)
    assert approvals.decide("u2", r["id"], True, now=T0 + 1) is None
    denied = approvals.decide("u1", r["id"], False, "not now", now=T0 + 2)
    assert denied["state"] == "denied" and denied["words"] == "not now"
    with pytest.raises(approvals.ApprovalError):
        approvals.decide("u1", r["id"], True, now=T0 + 3)
    assert approvals.read_for_hook(identity, r["id"], now=T0 + 4)["state"] == "denied"
    _, other_token = approvals.create_token("u1", "another session", now=T0)
    other = approvals.hook_identity(other_token, now=T0)
    assert approvals.read_for_hook(other, r["id"], now=T0 + 5) is None


def test_the_owner_sees_open_requests_first(hook):
    identity, _ = hook
    a = approvals.request(identity, "git push a", now=T0)
    b = approvals.request(identity, "git push b", now=T0 + 1)
    approvals.decide("u1", b["id"], False, now=T0 + 2)
    c = approvals.request(identity, "git push c", now=T0 + 3)
    listed = approvals.list_for_owner("u1", now=T0 + 4)
    assert [x["id"] for x in listed] == [c["id"], a["id"], b["id"]]
    assert listed[0]["token_name"] == "the Mac session" and approvals.list_for_owner("u2", now=T0) == []


# ---- the routes: a hook token and an account's token are each refused on the other's routes --------------------

def confirmation(client, headers, change, password="correct horse battery"):
    r = client.post("/api/account/confirm", headers=headers, json={"password": password, "change": change})
    assert r.status_code == 200, r.text
    return r.json()["confirmation"]


def approve(client, headers, request_id, text=None):
    """Approve as the page does: with the account's confirmation made for that request."""
    body = {"approve": True, "confirmation": confirmation(client, headers, f"request-approve:{request_id}")}
    if text is not None:
        body["text"] = text
    return client.post(f"/api/ade/approvals/{request_id}", headers=headers, json=body)


def test_approving_a_held_command_takes_a_confirmation_made_for_it_and_denying_the_sign_in_alone(client):
    """The command's own Approve takes the account's word made for that request; a sign-in alone, or a confirmation
    made for another request, for another change or for nothing in particular approves nothing, is left unused, and the
    request waits on. Deny takes the sign-in alone."""
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    hook_headers = {"Authorization": "Bearer " + token}
    first = client.post("/api/ade/hook/requests", headers=hook_headers, json={"command": "git push a"}).json()
    second = client.post("/api/ade/hook/requests", headers=hook_headers, json={"command": "git push b"}).json()
    r = client.post(f"/api/ade/approvals/{first['id']}", headers=admin, json={"approve": True})
    assert r.status_code == 403 and r.json()["detail"] == approvals.NEEDS_CONFIRM_TO_APPROVE
    for_second = confirmation(client, admin, f"request-approve:{second['id']}")
    plain = client.post("/api/account/confirm", headers=admin, json={"password": "correct horse battery"}).json()
    for given, words in ((for_second, passkeys.CONFIRM_OTHER),
                         (confirmation(client, admin, "sign-out-everywhere"), passkeys.CONFIRM_OTHER),
                         (plain["confirmation"], passkeys.CONFIRM_FOR_NOTHING)):
        r = client.post(f"/api/ade/approvals/{first['id']}", headers=admin,
                        json={"approve": True, "confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == words
    states = {q["id"]: q["state"] for q in client.get("/api/ade/approvals", headers=admin).json()["requests"]}
    assert states == {first["id"]: "pending", second["id"]: "pending"}
    r = client.post(f"/api/ade/approvals/{second['id']}", headers=admin,
                    json={"approve": True, "confirmation": for_second})
    assert r.status_code == 200 and r.json()["state"] == "approved"                   # left unused, it still serves
    r = client.post(f"/api/ade/approvals/{first['id']}", headers=admin, json={"approve": False})
    assert r.status_code == 200 and r.json()["state"] == "denied"


def test_while_high_security_is_on_only_a_hardware_key_s_confirmation_approves_a_held_command(client):
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    r = client.post("/api/ade/hook/requests", headers={"Authorization": "Bearer " + token},
                    json={"command": "git push"}).json()
    word = confirmation(client, admin, f"request-approve:{r['id']}")        # the password's, made before it was on
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1")
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=admin, json={"approve": True, "confirmation": word})
    assert got.status_code == 403 and got.json()["detail"] == passkeys.NEEDS_HARDWARE
    # none at all, as a page from an older release sends: told in a held command's words, and to reload
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=admin, json={"approve": True})
    assert got.status_code == 403 and got.json()["detail"] == approvals.NEEDS_HARDWARE_TO_APPROVE
    assert client.get("/api/ade/approvals", headers=admin).json()["requests"][0]["state"] == "pending"


def test_an_account_with_nothing_to_confirm_with_approves_with_its_sign_in(client):
    """The desktop server's own account, which has neither a password nor a passkey, approves with the sign-in alone."""
    mine = signed_up(client, "mac@example.org")
    with store.connect() as db:
        db.execute("UPDATE users SET password_hash = '' WHERE email = ?", ("mac@example.org",))
    token = client.post("/api/ade/hook-tokens", headers=mine, json={"name": "Mac"}).json()["token"]
    r = client.post("/api/ade/hook/requests", headers={"Authorization": "Bearer " + token},
                    json={"command": "git push"}).json()
    got = client.post(f"/api/ade/approvals/{r['id']}", headers=mine, json={"approve": True})
    assert got.status_code == 200 and got.json()["state"] == "approved"


def test_hook_routes_take_only_a_hook_token_and_account_routes_only_an_account(client):
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    hook_headers = {"Authorization": "Bearer " + token}
    assert client.post("/api/ade/hook/requests", headers=admin, json={"command": "git push"}).status_code == 401
    assert client.get("/api/ade/approvals", headers=hook_headers).status_code == 401
    assert client.post("/api/ade/hook-tokens", headers=hook_headers, json={"name": "x"}).status_code == 401
    r = client.post("/api/ade/hook/requests", headers=hook_headers, json={"command": "git push"}).json()
    assert client.post(f"/api/ade/approvals/{r['id']}", headers=hook_headers, json={"approve": True}).status_code == 401
    listed = client.get("/api/ade/hook-tokens", headers=admin).json()["tokens"]
    assert [t["name"] for t in listed] == ["Mac"] and token not in json.dumps(listed)
    member = signed_up(client, "member@example.org")
    assert client.post(f"/api/ade/approvals/{r['id']}", headers=member, json={"approve": True}).status_code == 404
    assert client.get("/api/ade/approvals", headers=member).json()["requests"] == []
    assert approve(client, admin, r["id"]).json()["state"] == "approved"
    bad = client.post("/api/ade/hook/requests", headers=hook_headers, json={"command": "x", "until": "tomorrow"})
    assert bad.status_code == 400


def test_the_token_list_says_when_each_hook_last_called_in(client, monkeypatch):
    """The Approvals panel says "never called in" for a token no hook has reached the ADE with (web/app.js), since a
    hook that is not set up, or cannot start, asks nothing: a token's time is set by a request that authenticates with
    it, an ask or a poll, never by its creation or by a request refused, and on that token's row only."""
    at = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: at[0])
    admin, member = signed_up(client, ADMIN), signed_up(client, "member@example.org")
    made = {name: client.post("/api/ade/hook-tokens", headers=headers, json={"name": name}).json()
            for headers, name in ((admin, "Mac"), (admin, "Linux"), (member, "theirs"))}
    hook = lambda name: {"Authorization": "Bearer " + made[name]["token"]}  # noqa: E731

    def last_used(headers):
        tokens = client.get("/api/ade/hook-tokens", headers=headers).json()["tokens"]
        return {t["name"]: t["last_used_at"] for t in tokens}

    assert last_used(admin) == {"Mac": None, "Linux": None} and last_used(member) == {"theirs": None}
    at[0] += 60
    # a request that does not authenticate counts for no token: one the ADE never made, the account's own sign-in
    for bearer in ("adeh_" + "x" * 43, admin["Authorization"][len("Bearer "):]):
        r = client.post("/api/ade/hook/requests", headers={"Authorization": "Bearer " + bearer},
                        json={"command": "git push"})
        assert r.status_code == 401
    assert last_used(admin) == {"Mac": None, "Linux": None}
    # an ask sets its own token's time, and no other token's, of this account or another
    asked = client.post("/api/ade/hook/requests", headers=hook("Mac"), json={"command": "git push"})
    assert asked.status_code == 200
    assert last_used(admin) == {"Mac": at[0], "Linux": None} and last_used(member) == {"theirs": None}
    # a poll is a request too, and the time is the last one's, not the first's
    at[0] += 60
    polled = at[0]
    assert client.get(f"/api/ade/hook/requests/{asked.json()['id']}", headers=hook("Mac")).status_code == 200
    assert last_used(admin) == {"Mac": polled, "Linux": None} and last_used(member) == {"theirs": None}
    # a revoked token's requests are refused, and leave its time as it was: its last, or never
    for name in ("Mac", "Linux"):
        assert client.delete(f"/api/ade/hook-tokens/{made[name]['id']}", headers=admin).status_code == 200
    at[0] += 60
    for name in ("Mac", "Linux"):
        r = client.post("/api/ade/hook/requests", headers=hook(name), json={"command": "git push"})
        assert r.status_code == 401
        assert client.get(f"/api/ade/hook/requests/{asked.json()['id']}", headers=hook(name)).status_code == 401
    assert last_used(admin) == {"Mac": polled, "Linux": None} and last_used(member) == {"theirs": None}


# ---- the hook, against the app through the test client ------------------------------------------------------------

def through(client):
    """The hook's HTTP call, sent to the app through the test client instead of the network (the time the hook gives
    it, `timeout`, is not needed there)."""
    def call(method, url, token, body=None, timeout=None):
        path = url.split("://", 1)[1].split("/", 1)[1]
        r = client.request(method, "/" + path, headers={"Authorization": "Bearer " + token}, json=body)
        if r.status_code >= 400:
            import urllib.error
            raise urllib.error.HTTPError(url, r.status_code, r.text, {}, None)
        return r.json()
    return call


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s1", "cwd": "/w"}


def test_the_hook_asks_waits_and_uses_an_approval_once(client):
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    env = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": token, "PROOFPANE_ADE_WAIT": "6"}
    call, clock = through(client), Clock()
    assert ade_hook.decide(bash("ls -la"), env, call, clock.sleep, clock) is None          # not gated
    assert ade_hook.decide({"tool_name": "Read", "tool_input": {}}, env, call, clock.sleep, clock) is None
    first = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no answer yet" in first["hookSpecificOutput"]["permissionDecisionReason"]
    (req,) = client.get("/api/ade/approvals", headers=admin).json()["requests"]
    assert req["command"] == "git push origin work" and req["session"] == "s1" and req["token_name"] == "Mac"
    assert approve(client, admin, req["id"], text="go ahead").status_code == 200
    second = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert second["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert second["hookSpecificOutput"]["permissionDecisionReason"].endswith("go ahead")
    third = ade_hook.decide(bash("git push origin work"), env, call, clock.sleep, clock)
    assert third["hookSpecificOutput"]["permissionDecision"] == "deny"          # a new request: the approval is used


def test_a_working_hook_calls_in_only_at_a_gated_command(client):
    # what the Approvals panel says of a token never called in (web/app.js, HOOK_NOT_CALLED_IN: "A hook asks here only
    # at a gated command"): a working hook whose session has run no gated command has not called in either
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    env = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": token, "PROOFPANE_ADE_WAIT": "0"}
    call, clock = through(client), Clock()

    def last_used():
        return client.get("/api/ade/hook-tokens", headers=admin).json()["tokens"][0]["last_used_at"]

    for payload in (bash("ls -la"), bash("git status"), {"tool_name": "Read", "tool_input": {"file_path": "/w/a"}}):
        assert ade_hook.decide(payload, env, call, clock.sleep, clock) is None
    assert last_used() is None
    out = ade_hook.decide(bash("git push"), env, call, clock.sleep, clock)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and last_used() is not None


def test_the_hook_denies_a_gated_command_it_cannot_check(client):
    denied = lambda out: out["hookSpecificOutput"]["permissionDecision"] == "deny"  # noqa: E731
    clock = Clock()
    for env in ({}, {"PROOFPANE_ADE_URL": "http://ade.example.test", "PROOFPANE_ADE_TOKEN": "adeh_x"},
                {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN_FILE": "/no/such/file"}):
        assert denied(ade_hook.decide(bash("git push"), env, through(client), clock.sleep, clock))

    def unreachable(*a, **k):
        raise OSError("no route to host")

    env = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": "adeh_x"}
    out = ade_hook.decide(bash("git push"), env, unreachable, clock.sleep, clock)
    assert denied(out) and "could not be asked" in out["hookSpecificOutput"]["permissionDecisionReason"]
    out = ade_hook.decide(bash("git push"), env, through(client), clock.sleep, clock)   # a token the ADE refuses
    assert denied(out) and "refused the hook token" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert ade_hook.decide(bash("echo hi"), {}, unreachable, clock.sleep, clock) is None  # not gated: nothing


def test_a_command_sent_in_a_window_needs_that_window_open(tmp_path, client):
    # the window is placed around the real time, since the server refuses a request whose time has already ended;
    # a window written for one fixed day made this test fail from the hour that window closed
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start, end = now - timedelta(minutes=30), now + timedelta(minutes=30)
    stamp = lambda t: t.strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    admin = signed_up(client, ADMIN)
    token = client.post("/api/ade/hook-tokens", headers=admin, json={"name": "Mac"}).json()["token"]
    (tmp_path / "GO.md").write_text(f"## GO: deploy\nwindow: {stamp(start)} to {stamp(end)}\n")
    env = {"PROOFPANE_ADE_URL": "https://ade.example.test", "PROOFPANE_ADE_TOKEN": token, "PROOFPANE_ADE_WAIT": "0",
           "PROOFPANE_ADE_WINDOWS": str(tmp_path)}
    clock = Clock()
    ade_hook.decide(bash(f"anything at all # go {stamp(start)}"), env, through(client), clock.sleep, clock, now=now)
    (req,) = client.get("/api/ade/approvals", headers=admin).json()["requests"]
    assert req["until"] == int(end.timestamp())
    out = ade_hook.decide(bash(f"x # go {stamp(start)}"), env, through(client), clock.sleep, clock, now=end)
    assert f"ended at {stamp(end)}" in out["hookSpecificOutput"]["permissionDecisionReason"]
    later = stamp(end + timedelta(hours=2))
    out = ade_hook.decide(bash(f"x # go {later}"), env, through(client), clock.sleep, clock, now=now)
    assert "no window file" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(client.get("/api/ade/approvals", headers=admin).json()["requests"]) == 1   # nothing more was sent


@pytest.mark.parametrize("command,held", [
    ("git push", True), ("git  push --force origin x", True), ("cd x && git push", True), ("gh pr merge 3", True),
    ("fly deploy .", True), ("flyctl secrets set A=1", True), ("rm -rf build", True), ("rm -fr /", True),
    ("terraform apply", True), ("x # go 2026-09-28T03:00Z", True),
    ("git status", False), ("git pushd", False), ("ls", False), ("rm file.txt", False), ("pytest -q", False),
])
def test_the_default_gate(command, held):
    assert ade_hook.gated(command, {})[0] is held


def test_the_settings_block_wires_the_hook_under_its_timeout():
    (block,) = hook_templates.blocks("ade_hook.settings.json", "PreToolUse")
    # once a command is gated, the hook has until DEADLINE from its start, at which it denies, with time to spare for
    # the interpreter's start and its exit under the block's timeout, and the default wait and a last poll fit inside
    # the run when the ADE answers at once
    assert block["matcher"] == "Bash" and ade_hook.DEADLINE + 10 <= block["hooks"][0]["timeout"] == 90
    assert ade_hook.WAIT + ade_hook.POLL + ade_hook.HTTP_TIMEOUT < ade_hook.DEADLINE
    # a program and its arguments, which no CLAUDE_CODE_SHELL_PREFIX wraps, its interpreter
    # named by its own path, not found on a PATH a settings file can set, and started in UTF-8 mode, which a locale a
    # settings file names whose character set Python has no codec for does not keep from starting (test_hooks_locale.py,
    # on Linux); beside it, the command line that checks the two paths it names
    hook, check = block["hooks"]
    assert hook_templates.program(hook) == ["/absolute/path/to/python3", "-I", "-X", "utf8",
                                            "/absolute/path/to/open_edition/ade_hook.py"]
    assert hook_templates.start_check(check) == ("/absolute/path/to/python3",
                                                 "/absolute/path/to/open_edition/ade_hook.py")
    assert check["timeout"] <= hook["timeout"]


def _start_check_run(tmp_path, python, hook_file):
    """The approval block's start check, its two paths filled, run as Claude Code runs a command line (`sh -c`), with
    an empty folder as the whole PATH, so that any program it started would not be found."""
    import subprocess
    (block,) = hook_templates.blocks("ade_hook.settings.json", "PreToolUse")
    line = block["hooks"][1]["command"]
    line = line.replace("/absolute/path/to/python3", str(python))
    line = line.replace("/absolute/path/to/open_edition/ade_hook.py", str(hook_file))
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    return subprocess.run(["/bin/sh", "-c", line], capture_output=True, text=True, timeout=30,
                          env={"PATH": str(empty), "HOME": str(tmp_path)})


def test_the_start_check_holds_every_bash_command_when_the_hook_cannot_start_and_runs_nothing(tmp_path):
    # the program form's failure to start is no decision, so the command would go on: this line exits 2 (a denial)
    # when the interpreter is not an executable file or the hook's file is not there, and otherwise says nothing
    ran = tmp_path / "ran"
    python = tmp_path / "python3"
    python.write_text(f"#!/bin/sh\necho interpreter >> {ran}\n")
    python.chmod(0o755)
    hook_file = tmp_path / "ade_hook.py"
    hook_file.write_text(f"open({str(ran)!r}, 'a').write('hook')\n")
    both = _start_check_run(tmp_path, python, hook_file)
    assert (both.returncode, both.stdout, both.stderr) == (0, "", "")
    for missing_python, missing_hook in ((tmp_path / "no-python", hook_file), (python, tmp_path / "no-hook.py")):
        r = _start_check_run(tmp_path, missing_python, missing_hook)
        assert r.returncode == 2 and r.stdout == "", r
        assert r.stderr.startswith("ADE: the approval hook cannot start: ") and r.stderr.count("\n") == 1, r.stderr
        assert str(missing_python) in r.stderr and str(missing_hook) in r.stderr
    python.chmod(0o644)                                         # there, but not executable
    assert _start_check_run(tmp_path, python, hook_file).returncode == 2
    folder = tmp_path / "a-folder"
    folder.mkdir()                                              # executable, but not a file
    assert _start_check_run(tmp_path, folder, hook_file).returncode == 2
    python.chmod(0o755)
    assert _start_check_run(tmp_path, python, tmp_path).returncode == 2       # the hook's path names a folder
    unfilled = _start_check_run(tmp_path, "/absolute/path/to/python3", "/absolute/path/to/open_edition/ade_hook.py")
    assert unfilled.returncode == 2                             # the template pasted with its placeholders
    assert not ran.exists()                                     # neither the interpreter nor the hook ever ran


# One command for each alternative GATE holds, its inner ones and the optional `ctl` and `s` included; and the form of
# each after another command, which a gate anchored at the start would miss. An earlier hook took a gate of
# the owner's in GATE's place once it found each of these, so a gate that found them and less than GATE narrowed the
# hold; now a command either finds is held.
SAMPLES = ("git push origin main", "gh pr merge 12", "gh release create v1.0", "fly deploy", "flyctl deploy",
           "fly secrets set KEY=value", "fly ssh console", "fly apps destroy demo", "fly volumes destroy vol_1",
           "fly machine destroy m1", "fly machines destroy m1", "npm publish", "twine upload dist/x.whl",
           "docker push image:tag", "terraform apply", "terraform destroy", "kubectl apply -f app.yaml",
           "kubectl delete pod web", "rm -rf build", "rm -fr build")
AFTER = "cd work && "


def test_the_samples_are_the_default_list_s():
    assert all(ade_hook.gated(s, {})[0] and ade_hook.gated(AFTER + s, {})[0] for s in SAMPLES)


def test_a_gate_of_the_samples_holds_what_the_default_list_holds():
    """The samples' gate: the twenty samples, each escaped, joined. It finds each of them, alone and after another
    command, and finds none of five commands the default list holds (the control), which the hook holds all the same:
    a gate of the owner's only adds."""
    own = "|".join(re.escape(s) for s in SAMPLES)
    assert all(re.search(own, s) and re.search(own, AFTER + s) for s in SAMPLES)
    five = ("git push origin feature", "git push --force", "rm -rf /", "kubectl delete deployment web", "gh pr merge 7")
    assert not any(re.search(own, command) for command in five)
    for command in five:
        assert ade_hook.gated(command, {"PROOFPANE_ADE_GATE": own}) == (True, None), command
        out = ade_hook.decide(bash(command), {"PROOFPANE_ADE_GATE": own}, lambda *a, **k: pytest.fail("not asked"))
        assert "no ADE address" in _reason(out), command        # held: denied for want of an ADE address, asked nothing


@pytest.mark.parametrize("narrowed", [
    ade_hook.GATE.replace(r"\bgit\s+push\b|", ""),
    ade_hook.GATE.replace(r"pr\s+merge|release", r"pr\s+merge"),
    ade_hook.GATE.replace(r"pr\s+merge|release", "release"),
    ade_hook.GATE.replace("fly(?:ctl)?", "fly"),
    ade_hook.GATE.replace("fly(?:ctl)?", "flyctl"),
    ade_hook.GATE.replace("deploy|secrets", "secrets"),
    ade_hook.GATE.replace("|secrets|", "|"),
    ade_hook.GATE.replace("|ssh|", "|"),
    ade_hook.GATE.replace(r"|apps\s+destroy|", "|"),
    ade_hook.GATE.replace(r"|volumes\s+destroy|", "|"),
    ade_hook.GATE.replace(r"|machines?\s+destroy", ""),
    ade_hook.GATE.replace("machines?", "machines"),
    ade_hook.GATE.replace("machines?", "machine"),
    ade_hook.GATE.replace(r"npm\s+publish|", ""),
    ade_hook.GATE.replace(r"|twine\s+upload|", "|"),
    ade_hook.GATE.replace(r"|docker\s+push", ""),
    ade_hook.GATE.replace("(?:apply|destroy)", "apply"),
    ade_hook.GATE.replace("(?:apply|destroy)", "destroy"),
    ade_hook.GATE.replace("(?:apply|delete)", "apply"),
    ade_hook.GATE.replace("(?:apply|delete)", "delete"),
    ade_hook.GATE.replace("(?:rf|fr)", "rf"),
    ade_hook.GATE.replace("(?:rf|fr)", "fr"),
    ade_hook.GATE.replace(r"|\brm\s+-\w*(?:rf|fr)\w*\b", ""),
    "^(?:" + ade_hook.GATE + ")",                               # anchored: misses a gated command after another one
    "(?!)",                                                     # finds nothing
])
def test_a_gate_that_finds_less_than_the_default_list_narrows_nothing(narrowed):
    """Each of the default list's alternatives taken out in turn, or the list anchored at the start, or a gate that finds
    nothing: every sample is still held, alone and after another command, and a command neither finds is not, with no
    denial of every Bash command (which such a gate once brought)."""
    assert narrowed != ade_hook.GATE                            # each replacement found what it takes out
    env = {"PROOFPANE_ADE_GATE": narrowed}
    assert all(ade_hook.gated(s, env) == ade_hook.gated(AFTER + s, env) == (True, None) for s in SAMPLES)
    for command in ("ls", "git status"):
        assert ade_hook.gated(command, env) == (False, None), command
        assert ade_hook.decide(bash(command), env, lambda *a, **k: pytest.fail("nothing is sent")) is None


def test_a_gate_that_adds_to_the_default_list_is_taken():
    for own in (r"\bmake\s+release\b", ade_hook.GATE + r"|\bmake\s+release\b"):     # alone, or with the list written in
        for command, held in (("make release", True), ("git push origin main", True), ("cd w && rm -rf x", True),
                              ("ls", False), ("make test", False)):
            assert ade_hook.gated(command, {"PROOFPANE_ADE_GATE": own}) == (held, None), (own, command)


def _pending(seen):
    """A stand-in ADE that records each call and keeps the request pending."""
    def call(method, url, token, body=None, timeout=None):
        seen.append((method, url, token, body))
        return {"id": "r1", "state": "pending", "until": T0 + 3600}
    return call


LATE_5 = ("Held by the ADE: the hook had no answer within 5 seconds of its start; nothing was let through. Send the "
          "same command again to ask again.")


def test_each_request_is_given_the_time_left_before_the_run_s_deadline(tmp_path, monkeypatch):
    """Once the command is gated, what the hook does from then on runs in a thread of its own
    given the time left before DEADLINE seconds from its start (`_within`, whose bound this test records), at which the
    hook denies, and each request is given that time, at most HTTP_TIMEOUT, as its timeout. A poll starts only while
    its pause and one request's timeout fit before the deadline, so a run the ADE slowed down still says the request
    stays open, unless one of its requests takes longer, which ends in the late denial. With no time left at the start,
    nothing is started: no thread, no read of the token file, nothing asked. Played on the test's own clock, with a
    stand-in ADE whose every answer takes as long as the test says, in each case here less than the time the request is
    given: a run on this clock cannot be cut by the deadline itself, which waits on real time (the network's own cases,
    a name lookup or a file that does not answer, are in test_ade_hook_network.py)."""
    clock, given, bounds = Clock(), [], []
    real_within = ade_hook._within

    def within(seconds, fn):
        bounds.append(seconds)
        return real_within(seconds, fn)
    monkeypatch.setattr(ade_hook, "_within", within)

    def slow(seconds):
        def call(method, url, token, body=None, timeout=None):
            given.append((method, clock(), timeout))
            clock.t += seconds
            return {"id": "r1", "state": "pending", "until": T0 + 3600}
        return call

    monkeypatch.setattr(ade_hook, "DEADLINE", 5.0)
    out = ade_hook.decide(bash("git push"), _owner_env(tmp_path), slow(0), clock.sleep, clock)
    assert "no answer yet" in _reason(out) and given == [("POST", 0.0, 5.0)]   # five seconds left of the run: no poll fits
    assert bounds == [5.0]                              # the gated part given the whole deadline, from decide's call
    clock.t, given[:], bounds[:] = 10.0, [], []
    out = ade_hook.decide(bash("git push"), _owner_env(tmp_path), slow(0), clock.sleep, clock, started=7.0)
    assert "no answer yet" in _reason(out) and given == [("POST", 10.0, 2.0)]   # counted from the start it is given
    assert bounds == [2.0]                              # the gated part too: the 2 seconds left, not a deadline anew
    given[:], bounds[:] = [], []
    asked, read = threading.Event(), threading.Event()
    real_token = ade_hook._token

    def never(*args, **kwargs):
        asked.set()
        return {"id": "r1", "state": "approved"}

    def reading_token(env):
        read.set()
        return real_token(env)
    monkeypatch.setattr(ade_hook, "_token", reading_token)
    out = ade_hook.decide(bash("git push"), _owner_env(tmp_path), never, clock.sleep, clock, started=4.0)
    assert _reason(out) == LATE_5
    assert bounds == [] and not read.is_set() and not asked.is_set()   # no time left at the start: nothing is started

    # a read of the token file that takes ten seconds of a deadline of five: on this clock the run ends at ten, through
    # ask's guard; the real hook ends such a run at five, through _within, the read still going (the pipe tests in
    # test_ade_hook_network.py), and what this case checks is the guard, which keeps the thread left behind from asking
    def slow_token(env):
        clock.t += 10.0
        return real_token(env)
    monkeypatch.setattr(ade_hook, "_token", slow_token)
    clock.t = 0.0
    out = ade_hook.decide(bash("git push"), _owner_env(tmp_path), never, clock.sleep, clock)
    assert _reason(out) == LATE_5 and not asked.wait(0.5)   # no time left at the first request: it is not made
    monkeypatch.setattr(ade_hook, "_token", real_token)
    # the deadline as it is: each answer takes 6 seconds, a poll then fits once (6 + 2 + 8 <= 20), and the run answers
    # at 14, past which no poll fits, that the request stays open
    monkeypatch.setattr(ade_hook, "DEADLINE", 20.0)
    clock.t, given[:], bounds[:] = 0.0, [], []
    out = ade_hook.decide(bash("git push"), _owner_env(tmp_path), slow(6), clock.sleep, clock)
    assert "no answer yet" in _reason(out), out
    assert given == [("POST", 0.0, 8.0), ("GET", 8.0, 8.0)] and clock.t == 14.0 and bounds == [20.0]


def _owner_env(tmp_path, **values):
    """The approval block's `env` as its template gives it, the owner's address and token file filled (a file of the
    test's own), with `values` laid over it."""
    env = dict(hook_templates.template("ade_hook.settings.json")["env"])
    token = tmp_path / "owner-token"
    token.write_text("adeh_owner\n")
    env.update(PROOFPANE_ADE_URL="https://ade.example.test", PROOFPANE_ADE_TOKEN_FILE=str(token))
    env.update(values)
    return env


def _reason(out):
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny", out
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_hook_s_settings_the_block_leaves_empty_are_their_defaults(tmp_path, monkeypatch):
    # the block sets each of the hook's settings it has no owner's value for to empty, which must read as its default:
    # never as a gate of everything or of nothing, no token, no wait or no window
    env, seen, clock = _owner_env(tmp_path), [], Clock()
    left = ("PROOFPANE_ADE_TOKEN", "PROOFPANE_ADE_GATE", "PROOFPANE_ADE_WINDOWS", "PROOFPANE_ADE_WAIT",
            "PROOFPANE_ADE_PROXY", "PROOFPANE_ADE_CA_FILE")
    assert {n: env[n] for n in left} == dict.fromkeys(left, "")
    assert ade_hook.decide(bash("ls"), env, _pending(seen), clock.sleep, clock) is None          # the default gate
    assert seen == []
    out = ade_hook.decide(bash("x # go 2026-09-28T03:00Z"), env, _pending(seen), clock.sleep, clock)
    assert "no answer yet" in _reason(out)
    method, url, token, body = seen[0]
    assert (method, url, token) == ("POST", "https://ade.example.test/api/ade/hook/requests", "adeh_owner")
    assert body["until"] is None                                    # no window file read: none is named
    assert ade_hook.WAIT <= clock.t < ade_hook.WAIT + ade_hook.POLL  # the default wait, then the answer above
    assert ade_hook._wait({"PROOFPANE_ADE_WAIT": ""}) == ade_hook.WAIT == 50
    # the token file left empty: the one the page's lines write, in the home folder
    home = tmp_path / "home"
    (home / ".proofpane-ade").mkdir(parents=True)
    (home / ".proofpane-ade" / "hook-token").write_text("adeh_default\n")
    monkeypatch.setenv("HOME", str(home))
    seen.clear()
    ade_hook.decide(bash("git push"), _owner_env(tmp_path, PROOFPANE_ADE_TOKEN_FILE="", PROOFPANE_ADE_WAIT="0"),
                    _pending(seen), clock.sleep, clock)
    assert seen[0][2] == "adeh_default"


def test_what_the_hook_itself_refuses_where_the_block_s_pins_do_not_reach(tmp_path):
    # each of the hook's settings as a repository's own settings would set it were the block's `env` not above them:
    # the hook itself refuses a gate that finds less than the default list (above), a wait past WAIT, which would
    # outlast the hook's timeout (a hook the client kills says nothing, which it reads as allow), and a proxy that is
    # not http://host:port; where it asks, with which token, through which proxy, trusting which certificates, and
    # which window file sets how long a request lasts, it takes as given, so for those the pins are the guard
    clock, seen = Clock(), []
    for wait in ("1000", "inf"):
        clock.t = 0.0
        out = ade_hook.decide(bash("git push"), _owner_env(tmp_path, PROOFPANE_ADE_WAIT=wait), _pending(seen),
                              clock.sleep, clock)
        assert "no answer yet" in _reason(out) and ade_hook.WAIT <= clock.t < ade_hook.WAIT + ade_hook.POLL, wait
    clock.t = 0.0
    ade_hook.decide(bash("git push"), _owner_env(tmp_path, PROOFPANE_ADE_WAIT="-5"), _pending(seen), clock.sleep, clock)
    assert clock.t == 0.0                                       # less than none is none
    seen.clear()
    for name, value in (("PROOFPANE_ADE_WAIT", "nan"), ("PROOFPANE_ADE_WAIT", "a minute"),
                        ("PROOFPANE_ADE_PROXY", "socks5://127.0.0.1:1080"), ("PROOFPANE_ADE_PROXY", "127.0.0.1:3128"),
                        ("PROOFPANE_ADE_PROXY", "http://127.0.0.1:3128/a-path")):
        out = ade_hook.decide(bash("git push"), _owner_env(tmp_path, **{name: value}), _pending(seen), clock.sleep,
                              clock)
        why = "is not a number of seconds" if name == "PROOFPANE_ADE_WAIT" else "is not http://host:port"
        assert _reason(out) == f"Held by the ADE: {name} {why}; nothing was sent.", value
    assert seen == []
    # taken as given
    theirs = tmp_path / "their-token"
    theirs.write_text("adeh_theirs\n")
    for values, at, token in (({"PROOFPANE_ADE_URL": "https://ade.attacker.example"}, "https://ade.attacker.example",
                               "adeh_owner"),
                              ({"PROOFPANE_ADE_TOKEN": "adeh_theirs"}, "https://ade.example.test", "adeh_theirs"),
                              ({"PROOFPANE_ADE_TOKEN_FILE": str(theirs)}, "https://ade.example.test", "adeh_theirs")):
        seen.clear()
        ade_hook.decide(bash("git push"), _owner_env(tmp_path, PROOFPANE_ADE_WAIT="0", **values), _pending(seen),
                        clock.sleep, clock)
        assert seen[0][1].startswith(at + "/") and seen[0][2] == token, values
    windows = tmp_path / "their-windows"
    windows.mkdir()
    (windows / "GO.md").write_text("## GO: x\nwindow: 2026-09-28T03:00Z to 2099-01-01T00:00Z\n")
    seen.clear()
    ade_hook.decide(bash("x # go 2026-09-28T03:00Z"),
                    _owner_env(tmp_path, PROOFPANE_ADE_WAIT="0", PROOFPANE_ADE_WINDOWS=str(windows)), _pending(seen),
                    clock.sleep, clock, now=datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc))
    assert seen[0][3]["until"] == "2099-01-01T00:00Z"           # the server keeps a request a day at most


def test_the_block_s_env_left_unfilled_denies_a_gated_command_and_sends_nothing(tmp_path):
    # pasted as the template gives it: the address placeholder is no ADE address, and the token file's is no file
    template = hook_templates.template("ade_hook.settings.json")["env"]
    token = tmp_path / "hook-token"
    token.write_text("adeh_x\n")
    for filled in ({}, {"PROOFPANE_ADE_URL": "https://ade.example.test"}, {"PROOFPANE_ADE_TOKEN_FILE": str(token)}):
        out = ade_hook.decide(bash("git push"), {**template, **filled}, lambda *a, **k: pytest.fail("nothing is sent"))
        assert "no ADE address" in _reason(out), filled


def test_a_hook_cannot_flood_the_queue(hook, monkeypatch):
    identity, _ = hook
    monkeypatch.setattr(approvals, "MAX_OPEN", 3)
    for n in range(3):
        approvals.request(identity, f"git push {n}", now=T0)
    with pytest.raises(approvals.ApprovalError):
        approvals.request(identity, "git push 3", now=T0)
    assert approvals.request(identity, "git push 0", now=T0)["command"] == "git push 0"   # an open one is found
    first = approvals.list_for_owner("u1", now=T0)[-1]
    approvals.decide("u1", first["id"], False, now=T0 + 1)
    assert approvals.request(identity, "git push 3", now=T0 + 2)["state"] == "pending"


def test_every_open_request_is_listed_however_many_were_answered(hook):
    identity, _ = hook
    old = approvals.request(identity, "git push the old one", now=T0)
    for n in range(5):
        r = approvals.request(identity, f"git push {n}", now=T0 + 10 + n)
        approvals.decide("u1", r["id"], False, now=T0 + 20 + n)
    listed = approvals.list_for_owner("u1", now=T0 + 30, limit=2)
    assert listed[0]["id"] == old["id"] and listed[0]["state"] == "pending" and len(listed) == 3


def test_a_gate_pattern_that_does_not_compile_denies_rather_than_passes():
    out = ade_hook.decide(bash("ls"), {"PROOFPANE_ADE_GATE": "(unclosed"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not a regular expression" in out["hookSpecificOutput"]["permissionDecisionReason"]


def _main_with(monkeypatch, tmp_path, data, encoding):
    """ade_hook.main() given `data` (bytes) on stdin through a text layer of `encoding`, as Python opens stdin in a
    locale of that character set, with no ADE address and a token file of the test's own: what it printed, parsed."""
    import io
    import sys
    for name in ("PROOFPANE_ADE_URL", "PROOFPANE_ADE_TOKEN", "PROOFPANE_ADE_GATE", "PROOFPANE_ADE_WINDOWS",
                 "PROOFPANE_ADE_WAIT", "PROOFPANE_ADE_PROXY", "PROOFPANE_ADE_CA_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROOFPANE_ADE_TOKEN_FILE", str(tmp_path / "no-token-here"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding=encoding))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert ade_hook.main() == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def test_the_hook_reads_claude_code_s_input_as_utf8_whatever_the_locale_s_character_set(monkeypatch, tmp_path):
    # Claude Code writes its input in UTF-8; a settings file's `env` can name the locale, and Python reads stdin in the
    # locale's character set. Read so, a gated command with one character outside ASCII could not be read, and the
    # hook said nothing, which the client takes as allow. Read as UTF-8 bytes, the command
    # is judged: here denied for want of an ADE address, which is the hook deciding, not failing.
    data = json.dumps(bash("git push origin main # café"), ensure_ascii=False).encode("utf-8")
    for encoding in ("ascii", "utf-8", "latin-1"):
        reason = _reason(_main_with(monkeypatch, tmp_path, data, encoding))
        assert "no ADE address" in reason, (encoding, reason)
    # a command the gate does not hold is still let through, read in the same way
    data = json.dumps(bash("ls # café"), ensure_ascii=False).encode("utf-8")
    assert _main_with(monkeypatch, tmp_path, data, "ascii") is None


@pytest.mark.parametrize("data", [b"not json", b"[]", b"\"git push\"", b"{\"tool_name\": \"Bash\"", b"\xff\xfe\x00{",
                                  b""])
def test_input_the_hook_cannot_read_as_one_json_object_is_denied(monkeypatch, tmp_path, data):
    # a command that cannot be read cannot be judged, so it is denied: before, such input was let through. Empty input
    # among it: it was read as {}
    reason = _reason(_main_with(monkeypatch, tmp_path, data, "utf-8"))
    assert reason == ("Held by the ADE: the hook could not read what Claude Code sent, so the command cannot be judged; "
                      "nothing was sent.")
