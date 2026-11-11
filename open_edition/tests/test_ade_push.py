"""Notifications that reach the owner when no page is open (Web Push). The encryption is checked
against RFC 8291's own example, the VAPID signature against its key, and the notifier's rules (each item once, again
after it went away, a summary past MAX_PER_TICK, a gone subscription dropped) with the push service played by a test."""

import base64
import json
import os
import stat
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from conftest import ADMIN, signed_up
from proofpane_open.ade import approvals, push

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
NOW = 1_790_000_000


def point(key):
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


class Device:
    """A browser: its key pair and auth secret, and a way to read what the ADE sends it."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.p256dh = push.b64u(point(self.key.public_key()))

    def decrypt(self, body):
        salt, idlen = body[:16], body[20]
        as_public, record = body[21:21 + idlen], body[21 + idlen:]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ikm = push._hkdf(self.auth, shared, b"WebPush: info\x00" + point(self.key.public_key()) + as_public, 32)
        cek = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, record, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


class PushService:
    """Records every post and answers with `status`."""

    def __init__(self, status=201):
        self.status, self.posts = status, []

    def __call__(self, url, headers, body):
        self.posts.append((url, headers, body))
        return self.status


# The keys, the salt, the message and the result below are from RFC 8291, Section 5 and Appendix A
# (https://www.rfc-editor.org/rfc/rfc8291.txt), with section 6.d's text of the IETF Trust's Legal Provisions Relating to
# IETF Documents, its year 2017:
#
# Copyright (c) 2017 IETF Trust and the persons identified as authors of the code. All rights reserved.
# Redistribution and use in source and binary forms, with or without modification, is permitted pursuant to, and
# subject to the license terms contained in, the Revised BSD License set forth in Section 4.c of the IETF Trust’s
# Legal Provisions Relating to IETF Documents (http://trustee.ietf.org/license-info).
def test_the_encryption_is_rfc_8291_s_own_example():
    as_private = ec.derive_private_key(
        int.from_bytes(push.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = push.encrypt(push.unb64u("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"),
                        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                        "BTBZMqHH6r4Tts7J_aSIgg", as_private=as_private, salt=push.unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
    assert push.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBr"
        "u3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_a_message_reads_back_on_the_device_and_never_twice_the_same():
    device = Device()
    one = push.encrypt(b'{"title":"x"}', device.p256dh, push.b64u(device.auth))
    two = push.encrypt(b'{"title":"x"}', device.p256dh, push.b64u(device.auth))
    assert device.decrypt(one) == {"title": "x"} and one != two          # a fresh key and salt every time
    with pytest.raises(push.PushError):
        push.encrypt(b"x" * 5000, device.p256dh, push.b64u(device.auth))


def test_the_vapid_header_is_signed_for_the_push_service_s_origin():
    header = push.vapid_authorization(FCM, NOW)
    t, k = header[len("vapid t="):].split(", k=")
    head, claims, sig = t.split(".")
    signed = f"{head}.{claims}".encode("ascii")
    raw = push.unb64u(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), signed,
                  ec.ECDSA(hashes.SHA256()))
    assert json.loads(push.unb64u(head)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(push.unb64u(claims))
    assert body["aud"] == "https://fcm.googleapis.com" and body["exp"] == NOW + push.TTL
    assert body["sub"] == "https://open.example.test" and k == push.vapid_public_b64()


def test_the_vapid_key_is_made_once_and_kept_private(env):
    first = push.vapid_public_b64()
    assert push.vapid_public_b64() == first
    assert stat.S_IMODE(os.stat(env / "vapid.pem").st_mode) == 0o600


@pytest.mark.parametrize("endpoint,ok", [
    (FCM, True), ("https://updates.push.services.mozilla.com/wpush/v2/x", True), ("https://web.push.apple.com/Qx", True),
    ("https://wns2-bl2p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/x", False), ("https://evil.example/fcm", False),
    ("https://fcm.googleapis.com.evil.example/x", False), ("https://user@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False), ("https://10.0.0.1/x", False), ("https://localhost/x", False),
    ("https://fcm.googleapis.com/" + "x" * 1000, False), (None, False),
])
def test_only_a_known_push_service_over_https(endpoint, ok):
    if ok:
        assert push.check_endpoint(endpoint) == endpoint
    else:
        with pytest.raises(push.PushError):
            push.check_endpoint(endpoint)


def test_a_subscription_s_keys_must_be_a_browser_s():
    device = Device()
    with pytest.raises(push.PushError):
        push.subscribe("u1", FCM, push.b64u(os.urandom(65)), push.b64u(device.auth))     # not a point on the curve
    with pytest.raises(push.PushError):
        push.subscribe("u1", FCM, device.p256dh, push.b64u(os.urandom(8)))
    with pytest.raises(push.PushError):
        push.subscribe("u1", "https://evil.example/x", device.p256dh, push.b64u(device.auth))
    assert push.subscriptions("u1") == []


def test_a_device_is_kept_once_and_moves_with_the_account_that_subscribes_it(monkeypatch):
    a, b = Device(), Device()
    push.subscribe("u1", FCM, a.p256dh, push.b64u(a.auth), now=NOW)
    push.subscribe("u1", FCM, b.p256dh, push.b64u(b.auth), now=NOW)            # the same browser, new keys
    (only,) = push.subscriptions("u1")
    assert only["p256dh"] == b.p256dh
    push.subscribe("u2", FCM, a.p256dh, push.b64u(a.auth), now=NOW)            # the browser signed in as another
    assert push.subscriptions("u1") == [] and len(push.subscriptions("u2")) == 1
    assert "p256dh" not in json.dumps(push.list_for("u2")) and push.list_for("u2")[0]["service"] == "fcm.googleapis.com"
    monkeypatch.setattr(push, "MAX_SUBSCRIPTIONS", 1)
    with pytest.raises(push.PushError):
        push.subscribe("u2", FCM + "2", a.p256dh, push.b64u(a.auth), now=NOW)
    assert push.unsubscribe("u1", FCM) is False and push.unsubscribe("u2", FCM) is True


def items(*keys):
    return [(k, push.words("board", key=k)) for k in keys]


def tag(key):
    """The tag of the notice whose key is `key` (push.words): a digest of it."""
    return push.words("board", key=key).tag


def test_each_item_is_sent_once_and_again_after_it_went_away():
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    assert push.notify("u1", items("a", "b"), NOW, service) == 2
    assert [device.decrypt(body)["tag"] for _, _, body in service.posts] == [tag("a"), tag("b")]
    assert device.decrypt(service.posts[0][2]) == {"title": "A board needs you", "body": "Open the ADE.",
                                                   "tag": tag("a"), "url": "/#ade"}
    url, headers, _ = service.posts[0]
    assert url == FCM and headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].startswith("vapid t=")
    assert push.notify("u1", items("a", "b"), NOW + 60, service) == 0          # still there: not sent again
    assert push.notify("u1", items("b"), NOW + 120, service) == 0              # "a" went away
    assert push.notify("u1", items("a", "b"), NOW + 180, service) == 1         # "a" came back: new again
    assert device.decrypt(service.posts[-1][2])["tag"] == tag("a")


def test_more_than_a_handful_become_one_summary():
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    sent = push.notify("u1", items(*[f"k{i}" for i in range(push.MAX_PER_TICK + 3)]), NOW, service)
    bodies = [device.decrypt(body)["body"] for _, _, body in service.posts]
    assert sent == push.MAX_PER_TICK and bodies[-1] == "4 more things need you in the ADE."


def test_a_subscription_the_push_service_says_is_gone_is_dropped():
    device = Device()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    assert push.notify("u1", items("a"), NOW, PushService(410)) == 0
    assert push.subscriptions("u1") == []
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    push.notify("u1", items("b"), NOW, PushService(500))
    assert push.subscriptions("u1")[0]["failures"] == 1                          # a failure keeps the device


def test_a_round_gathers_approvals_and_board_alerts_and_skips_a_board_it_cannot_read(monkeypatch):
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    identity = approvals.hook_identity(approvals.create_token("u1", "the Mac", now=NOW)[1], now=NOW)
    approvals.request(identity, "git push origin work", now=NOW)
    from proofpane_open.ade import ledger
    good = ledger.add({"id": "u1", "admin": False}, "github", "owner/a", "main", ["t"], False)
    ledger.add({"id": "u1", "admin": False}, "github", "owner/b", "main", ["t"], False)
    push._board_cache.clear()

    def build(conn):
        if conn["repo"] == "owner/b":
            raise RuntimeError("rate limit")
        return {"alerts": [{"level": "alert", "kind": "silence", "channel": "t", "file": "log.md", "text": "quiet"},
                           {"level": "info", "kind": "questions", "channel": "t", "file": None, "text": "later"}]}

    assert push.tick(now=NOW, post=service, build=build) == {"u1": 2}
    messages = [device.decrypt(body) for _, _, body in service.posts]
    # in fixed words: neither the command nor the token that asked, nor the board's words (tests/test_push_words.py)
    assert (messages[0]["title"], messages[0]["body"]) == ("A command waits for you", "Open Approvals.")
    assert (messages[1]["title"], messages[1]["body"]) == ("A board needs you", "Open the ADE.")
    assert messages[1]["tag"] == tag(f"alert:{good['id']}:silence:t:log.md")
    assert push.tick(now=NOW + 60, post=service, build=build) == {"u1": 0}


def test_one_account_s_round_failing_stops_no_other_account_s(monkeypatch):
    """One account's items raising (its workspace's answer, among them) stopped the round for every
    account after it, round after round."""
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, Device().p256dh, push.b64u(Device().auth), now=NOW)
    push.subscribe("u2", FCM + "-2", device.p256dh, push.b64u(device.auth), now=NOW)
    identity = approvals.hook_identity(approvals.create_token("u2", "the Mac", now=NOW)[1], now=NOW)
    approvals.request(identity, "git push origin work", now=NOW)
    real = push.workspace_asks

    def workspace_asks(user_id):
        if user_id == "u1":
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        return real(user_id)
    monkeypatch.setattr(push, "workspace_asks", workspace_asks)
    assert push.tick(now=NOW, post=service, build=lambda conn: {"alerts": []}) == {"u1": None, "u2": 1}
    assert [device.decrypt(body)["title"] for _, _, body in service.posts] == ["A command waits for you"]


def test_a_round_of_more_items_than_sqlite_takes_variables_still_forgets_those_gone(monkeypatch):
    """The items gone since the last round were forgotten with one statement holding a variable for each item still
    there, which failed once the items outnumbered SQLite's limit (250,000 here; set lower for the test)."""
    import contextlib
    import sqlite3
    from proofpane_open import store
    real = store.connect

    @contextlib.contextmanager
    def limited(path=None):
        with real(path) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
            yield db
    monkeypatch.setattr(store, "connect", limited)
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    many = [f"k{i}" for i in range(150)]
    assert push.notify("u1", items(*many), NOW, service) == push.MAX_PER_TICK
    assert push.notify("u1", items(*many[1:]), NOW + 60, service) == 0         # "k0" went away
    assert push.notify("u1", items(*many), NOW + 120, service) == 1            # and came back: new again
    assert device.decrypt(service.posts[-1][2])["tag"] == tag("k0")


def _workspace(monkeypatch, sessions, asks=()):
    """An account whose workspace runs, listing `sessions` and the asks `asks`."""
    from proofpane_open import auth
    from proofpane_open.ade import workspace_client, workspaces

    class Provider:
        def endpoint(self, user):
            return "the-endpoint"

    monkeypatch.setattr(auth, "user", lambda uid: {"id": uid, "admin": True})
    monkeypatch.setattr(workspaces, "for_user", lambda user: Provider())
    monkeypatch.setattr(workspace_client, "call", lambda endpoint, method, path, timeout=30:
                        {"on": True, "asks": list(asks)} if path == "/asks" else {"sessions": sessions})


def test_a_chat_question_whose_id_is_not_plain_ascii_is_left_out(monkeypatch):
    """A question's id with a lone surrogate made a key words could not encode, which raised out of
    the round. A question's id is Claude Code's tool_use_id or ask-N."""
    ask = {"id": "toolu_01ABCdef-9", "name": "Bash"}
    odd = ["\ud800", "x" * 81, "has space", "café", ""]
    _workspace(monkeypatch, [{"id": "a" * 12, "chat": {"asks": [ask, *[dict(ask, id=i) for i in odd],
                                                                dict(ask, id="ask-3")]}}])
    assert [k for k, _ in push.workspace_asks("u1")] == ["chat-ask:" + "a" * 12 + ":toolu_01ABCdef-9",
                                                         "chat-ask:" + "a" * 12 + ":ask-3"]


def test_a_workspace_that_lists_thousands_adds_at_most_its_share_to_a_round(monkeypatch):
    sessions = [{"id": f"{i:012x}", "chat": {"asks": [{"id": f"toolu_{i}"}]}} for i in range(push.MAX_WORKSPACE_ITEMS * 3)]
    _workspace(monkeypatch, sessions, asks=[{"id": f"{i:012x}"} for i in range(10)])
    got = push.workspace_asks("u1")
    assert len(got) == push.MAX_WORKSPACE_ITEMS == 100
    assert [k for k, _ in got[:2]] == ["chat-ask:000000000000:toolu_0", "chat-ask:000000000001:toolu_1"]


def test_the_routes_serve_only_the_account_s_devices(client, monkeypatch):
    service = PushService()
    monkeypatch.setattr(push, "_default_post", service)
    assert client.get("/api/ade/push").status_code == 401
    admin = signed_up(client, ADMIN)
    member = signed_up(client, "member@example.org")
    device = Device()
    assert client.get("/api/ade/push", headers=admin).json()["key"] == push.vapid_public_b64()
    body = {"endpoint": FCM, "keys": {"p256dh": device.p256dh, "auth": push.b64u(device.auth)}}
    assert client.post("/api/ade/push/subscribe", headers=admin, json=body).status_code == 200
    bad = client.post("/api/ade/push/subscribe", headers=admin, json=dict(body, endpoint="https://evil.example/x"))
    assert bad.status_code == 400
    assert client.post("/api/ade/push/test", headers=member).json() == {"sent": 0, "devices": 0}
    assert client.post("/api/ade/push/test", headers=admin).json() == {"sent": 1, "devices": 1}
    assert device.decrypt(service.posts[-1][2])["body"] == "Notifications reach this device."
    assert client.post("/api/ade/push/unsubscribe", headers=member, json={"endpoint": FCM}).json() == {"ok": False}
    assert len(push.subscriptions(client.get("/api/auth/me", headers=admin).json()["id"])) == 1


def test_the_page_may_register_the_worker_and_the_worker_opens_only_this_site():
    here = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(here, "Caddyfile"), encoding="utf-8") as fh:
        csp = [line for line in fh if "Content-Security-Policy" in line][0]
    assert "manifest-src 'self'" in csp and "worker-src 'self'" in csp and "default-src 'none'" in csp
    with open(os.path.join(here, "web", "sw.js"), encoding="utf-8") as fh:
        worker = fh.read()
    assert "u.origin === self.location.origin" in worker
    assert 'addEventListener("fetch"' not in worker and "fetch(" not in worker


def test_only_a_chat_s_well_formed_questions_become_notifications(monkeypatch):
    from proofpane_open import auth
    from proofpane_open.ade import workspace_client, workspaces

    class Provider:
        started = False

        def endpoint(self, user):
            return "the-endpoint"

        def ensure(self, user):
            Provider.started = True

    monkeypatch.setattr(auth, "user", lambda uid: {"id": uid, "admin": True})
    monkeypatch.setattr(workspaces, "for_user", lambda user: Provider())
    ask = {"id": "toolu_1", "name": "Bash", "summary": "rm -rf build", "for_session": True, "time": 1}
    waiting = {"state": "waiting", "at": 1790000000, "message": "Claude needs your permission to use Bash",
               "kind": "permission_prompt"}
    codex = {"state": "waiting", "at": 1790000001, "event": "PermissionRequest", "tool": "Bash",
             "message": "Wants to run: curl -H 'Authorization: Bearer sk-secret' https://api.example.com"}
    sessions = [{"id": "a" * 12, "repo": "api", "chat": {"asks": [ask, {"name": "no id"}, "not a dict"]}},
                {"id": "../evil", "repo": "x", "chat": {"asks": [ask]}},
                {"id": "b" * 12, "repo": None, "chat": {"asks": [dict(ask, id="toolu_2")]}},
                {"id": "c" * 12, "repo": "shell", "chat": None}, "not a session",
                # a terminal agent that says, through its hooks, it waits; and ones that do not count
                {"id": "d" * 12, "repo": "api", "program": "gemini", "task": "Fix the totals\n\nmore", "running": True,
                 "chat": None, "activity": waiting},
                {"id": "e" * 12, "repo": "api", "program": "claude", "task": "x", "running": False, "activity": waiting},
                {"id": "f" * 12, "repo": "api", "program": "claude", "task": "x", "running": True,
                 "activity": dict(waiting, state="working")},
                {"id": "0" * 12, "repo": "api", "program": "claude", "task": "x", "running": True,
                 "activity": dict(waiting, at="soon")},
                # what the program said, and a command, stay off the notification; a name that is not a name too
                {"id": "1" * 12, "repo": "api", "program": "codex", "task": "Call the API", "running": True,
                 "activity": codex},
                {"id": "2" * 12, "repo": "api", "program": "grok", "task": "", "running": True,
                 "activity": {"state": "waiting", "at": 1790000002, "tool": "cat ~/.grok/auth.json", "kind": "x y"}}]
    monkeypatch.setattr(workspace_client, "call", lambda endpoint, method, path, timeout=30: {"sessions": sessions})
    got = push.workspace_asks("u1")
    # each in the fixed words of a session that waits, opening its session: never its program, repository, task, tool
    # or what it said (tests/test_push_words.py)
    waits = ("A session in your workspace waits for you", "Open the ADE.")
    assert [(k, m.title, m.body, m.url) for k, m in got] == [
        ("chat-ask:" + "a" * 12 + ":toolu_1", *waits, "/app/#session=" + "a" * 12),
        ("chat-ask:" + "b" * 12 + ":toolu_2", *waits, "/app/#session=" + "b" * 12),
        ("waiting:" + "d" * 12 + ":1790000000", *waits, "/app/#session=" + "d" * 12),
        ("waiting:" + "1" * 12 + ":1790000001", *waits, "/app/#session=" + "1" * 12),
        ("waiting:" + "2" * 12 + ":1790000002", *waits, "/app/#session=" + "2" * 12)]
    assert all(m.tag == push.words("workspace", key=k, session=m.url[-12:]).tag for k, m in got)
    assert Provider.started is False

    def fails(*args, **kwargs):
        raise workspace_client.WorkspaceCallError(502, "The workspace is not answering.")
    monkeypatch.setattr(workspace_client, "call", fails)
    assert push.workspace_asks("u1") == []          # not answering: nothing this round, and the round goes on


def test_a_session_an_agent_asks_for_is_a_notification_in_fixed_words(monkeypatch):
    from proofpane_open import auth
    from proofpane_open.ade import workspace_client, workspaces

    class Provider:
        def endpoint(self, user):
            return "the-endpoint"

    monkeypatch.setattr(auth, "user", lambda uid: {"id": uid, "admin": True})
    monkeypatch.setattr(workspaces, "for_user", lambda user: Provider())
    asks = [{"id": "a1b2c3d4e5f6", "repo": "api", "program": "codex", "base": "main",
             "task": "Run curl -H 'Authorization: Bearer sk-secret' and fix it",
             "by": {"id": "0123456789ab", "title": "Fix the totals\nmore", "program": "claude"}},
            {"id": "../evil", "repo": "api", "program": "codex", "task": "x", "by": {}},
            {"id": "f6e5d4c3b2a1", "repo": "bad name!", "program": "nope", "task": "x", "by": "not a dict"},
            "junk"]

    def call(endpoint, method, path, timeout=30):
        return {"on": True, "asks": asks} if path == "/asks" else {"sessions": []}
    monkeypatch.setattr(workspace_client, "call", call)
    got = push.workspace_asks("u1")
    # in fixed words, opening Approvals: never the asking session's name, the program or what the agent wrote
    asked = ("A session in your workspace waits for you", "Open Approvals.", "/app/#approvals")
    assert [(k, m.title, m.body, m.url) for k, m in got] == [("session-ask:a1b2c3d4e5f6", *asked),
                                                             ("session-ask:f6e5d4c3b2a1", *asked)]

    def older(endpoint, method, path, timeout=30):
        if path == "/asks":
            raise workspace_client.WorkspaceCallError(404, "Not Found")
        return {"sessions": []}
    monkeypatch.setattr(workspace_client, "call", older)
    assert push.workspace_asks("u1") == []          # an agent from before asks: nothing, and no error
