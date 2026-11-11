"""Guards of Web Push: each fails on the faulty version of the code it
was written for. Two pin defects: the service worker let "/\\evil.example/x" through (it starts
with "/" and not "//", and parses to another host), and an endpoint with a malformed port was a 500, not a 400. The
service worker is run in Node with a stand-in for the browser, to tap its notifications."""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import types

import pytest
from cryptography.hazmat.primitives import serialization

from conftest import ADMIN, signed_up
from proofpane_open.ade import approvals, ledger, push
from test_ade_push import FCM, NOW, Device, PushService, items


def message():
    """The notice the cases send, made as a case runs: its tag is keyed by the deployment's token secret, which
    conftest's env fixture sets for each case, so it cannot be made while this file is collected."""
    return push.words("test", key="test")


HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NODE = shutil.which("node")


def test_the_4kb_bound_counts_the_86_byte_header():
    device = Device()
    auth = push.b64u(device.auth)
    assert len(push.encrypt(b"x" * 3993, device.p256dh, auth)) == 4096         # 86 + 3993 + 1 + 16
    with pytest.raises(push.PushError):
        push.encrypt(b"x" * 3994, device.p256dh, auth)


def test_every_message_has_its_own_salt_and_its_own_key():
    device = Device()
    bodies = [push.encrypt(b"{}", device.p256dh, push.b64u(device.auth)) for _ in range(3)]
    assert len({b[:16] for b in bodies}) == 3 and len({b[21:86] for b in bodies}) == 3
    assert all(device.decrypt(b) == {} for b in bodies)


def test_the_vapid_token_lives_twelve_hours_within_rfc_8292_s_day():
    token = push.vapid_authorization(FCM, NOW)[len("vapid t="):].split(", k=")[0]
    claims = json.loads(push.unb64u(token.split(".")[1]))
    assert claims["exp"] - NOW == 12 * 3600 <= 24 * 3600


@pytest.mark.parametrize("endpoint", ["https://:pw@fcm.googleapis.com/x",
                                      "https://xnotify.windows.com/w", "https://evilpush.apple.com/x"])
def test_more_addresses_that_are_not_a_push_service(endpoint):
    with pytest.raises(push.PushError):
        push.check_endpoint(endpoint)


def test_send_posts_nowhere_but_a_push_service():
    device, service = Device(), PushService()
    stored = {"id": "x", "endpoint": "https://evil.example/x", "p256dh": device.p256dh, "auth": push.b64u(device.auth)}
    with pytest.raises(push.PushError):
        push.send(stored, message(), NOW, service)
    assert service.posts == []


def test_a_point_off_the_curve_is_refused():
    device = Device()
    with pytest.raises(push.PushError):
        push.subscribe("u1", FCM, push.b64u(b"\x04" + bytes(64)), push.b64u(device.auth))


def test_a_compressed_key_is_refused():
    device = Device()
    compressed = device.key.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.CompressedPoint)
    with pytest.raises(push.PushError):
        push.subscribe("u1", FCM, push.b64u(compressed), push.b64u(device.auth))
    assert push.subscriptions("u1") == []


def test_a_device_that_changes_accounts_starts_afresh_and_counts_there(monkeypatch):
    a = Device()
    push.subscribe("u1", FCM, a.p256dh, push.b64u(a.auth), now=NOW)
    assert push.send(push.subscriptions("u1")[0], message(), NOW, PushService(201)) == 201
    assert push.list_for("u1")[0]["last_sent_at"] == NOW
    push.subscribe("u2", FCM, a.p256dh, push.b64u(a.auth), now=NOW + 60)
    (moved,) = push.subscriptions("u2")
    assert moved["last_sent_at"] is None and moved["created_at"] == NOW + 60
    push.subscribe("u1", FCM + "1", a.p256dh, push.b64u(a.auth), now=NOW)
    monkeypatch.setattr(push, "MAX_SUBSCRIPTIONS", 1)
    with pytest.raises(push.PushError):                                        # moving it in would be u1's second
        push.subscribe("u1", FCM, a.p256dh, push.b64u(a.auth), now=NOW + 120)
    assert [s["endpoint"] for s in push.subscriptions("u2")] == [FCM]


def test_the_device_list_shows_no_key_no_secret_and_no_endpoint():
    device = Device()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    (shown,) = push.list_for("u1")
    assert set(shown) == {"service", "created_at", "last_sent_at", "endpoint_hash"}
    text = json.dumps(shown)
    assert device.p256dh not in text and push.b64u(device.auth) not in text and FCM not in text


def test_the_device_list_is_the_account_s_own(client):
    a, b = Device(), Device()
    push.subscribe("u1", FCM, a.p256dh, push.b64u(a.auth), now=NOW)
    push.subscribe("u2", FCM + "2", b.p256dh, push.b64u(b.auth), now=NOW)
    assert len(push.list_for("u1")) == 1 and len(push.list_for("u2")) == 1
    assert client.get("/api/ade/push", headers=signed_up(client, ADMIN)).json()["devices"] == []


@pytest.mark.parametrize("status", [404, 410])
def test_either_gone_status_drops_the_subscription(status):
    device = Device()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    push.notify("u1", items("a"), NOW, PushService(status))
    assert push.subscriptions("u1") == []


def test_a_delivery_records_when_and_clears_failures_and_no_answer_is_a_failure():
    device = Device()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)

    def sub():
        return push.subscriptions("u1")[0]
    assert push.send(sub(), message(), NOW, PushService(None)) is None
    assert (sub()["failures"], sub()["last_sent_at"]) == (1, None)
    push.send(sub(), message(), NOW + 1, PushService(503))
    assert sub()["failures"] == 2
    push.send(sub(), message(), NOW + 2, PushService(201))
    assert (sub()["failures"], sub()["last_sent_at"]) == (0, NOW + 2)


def test_an_item_that_was_the_only_one_is_new_again_when_it_comes_back():
    device, service = Device(), PushService()
    push.subscribe("u1", FCM, device.p256dh, push.b64u(device.auth), now=NOW)
    assert push.notify("u1", items("a"), NOW, service) == 1
    assert push.notify("u1", [], NOW + 60, service) == 0
    assert push.notify("u1", items("a"), NOW + 120, service) == 1


def test_every_device_of_the_account_gets_each_item():
    a, b, service = Device(), Device(), PushService()
    push.subscribe("u1", FCM, a.p256dh, push.b64u(a.auth), now=NOW)
    push.subscribe("u1", FCM + "2", b.p256dh, push.b64u(b.auth), now=NOW)
    assert push.notify("u1", items("x"), NOW, service) == 2
    got = {url: body for url, _, body in service.posts}
    tag = push.words("board", key="x").tag
    assert a.decrypt(got[FCM])["tag"] == tag and b.decrypt(got[FCM + "2"])["tag"] == tag


def _approval(user, command):
    identity = approvals.hook_identity(approvals.create_token(user, "the Mac", now=NOW)[1], now=NOW)
    return approvals.request(identity, command, now=NOW)


def test_only_the_account_s_own_waiting_approvals_are_its_items():
    mine, answered = _approval("u1", "git push"), _approval("u1", "make build")
    _approval("u2", "git status")
    approvals.decide("u1", answered["id"], True, now=NOW)
    assert [k for k, _ in push.items_for("u1", [], NOW)] == ["approval:" + mine["id"]]


def test_a_board_is_read_at_most_every_board_every_seconds():
    push._board_cache.clear()
    ledger.add({"id": "u1", "admin": False}, "github", "owner/a", "main", ["t"], False)
    reads = []

    def build(conn):
        reads.append(conn["id"])
        return {"alerts": []}
    push.board_alerts("u1", NOW, build)
    push.board_alerts("u1", NOW + push.BOARD_EVERY - 1, build)
    assert len(reads) == 1
    push.board_alerts("u1", NOW + push.BOARD_EVERY, build)
    assert len(reads) == 2


def test_the_notifier_runs_rounds_in_a_daemon_thread_and_outlives_a_failing_round(monkeypatch):
    assert push.start(every=0) is None
    rounds, second, park = [], threading.Event(), threading.Event()

    def tick():
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("a round fails")
        second.set()
        park.wait()                                  # hold the thread here: it must not run on after the test
    monkeypatch.setattr(push, "tick", tick)
    monkeypatch.setattr(push, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time))
    thread = push.start(every=1)
    assert thread is not None and thread.daemon and thread.name == "ade-push"
    assert second.wait(5) and len(rounds) == 2


def test_serving_the_app_starts_the_notifier(monkeypatch):
    from fastapi.testclient import TestClient
    from proofpane_open import main
    started = []
    monkeypatch.setattr(main.ade_push, "start", lambda every=None: started.append(every))
    # the compaction sweep (ade/e2e_keys.py) is tested apart: no thread of it runs on after this test
    monkeypatch.setattr(main.ade_e2e_keys, "start", lambda every=None: None)
    with TestClient(main.app):
        pass
    assert started == [None]


PROBE = r'''"use strict";
const fs = require("fs");
const ORIGIN = "https://open.example.test";
const src = fs.readFileSync(process.argv[2], "utf8");
const listeners = {}, shown = [], opened = [];
const self = {
  location: { origin: ORIGIN },
  addEventListener: (type, fn) => { listeners[type] = fn; },
  registration: { showNotification: (title, opts) => { shown.push({ title, opts }); return Promise.resolve(); } },
  clients: { matchAll: () => Promise.resolve([]), openWindow: (u) => { opened.push(u); return Promise.resolve(); } },
};
new Function("self", src)(self);
(async () => {
  const out = { events: Object.keys(listeners).sort(), taps: [] };
  for (const url of JSON.parse(process.argv[3])) {
    shown.length = 0; opened.length = 0;
    const waits = [];
    listeners.push({ data: { json: () => ({ title: "t", body: "b", url }), text: () => "" }, waitUntil: (p) => waits.push(p) });
    await Promise.all(waits);
    const tap = [];
    listeners.notificationclick({ notification: { data: shown[0].opts.data, close: () => {} }, waitUntil: (p) => tap.push(p) });
    await Promise.all(tap);
    out.taps.push(new URL(opened[0], ORIGIN).href);
  }
  console.log(JSON.stringify(out));
})();
'''


def _tap(tmp_path, urls):
    script = tmp_path / "probe.js"
    script.write_text(PROBE, encoding="utf-8")
    run = subprocess.run([NODE, str(script), os.path.join(HERE, "web", "sw.js"), json.dumps(urls)],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node to run the service worker")
def test_the_worker_hears_push_and_taps_only_and_opens_only_this_site(tmp_path):
    out = _tap(tmp_path, ["/#ade", "/#ade?x=1", "https://evil.example/x", "//evil.example/x", "javascript:alert(1)"])
    assert out["events"] == ["notificationclick", "push"]
    assert out["taps"] == ["https://open.example.test/#ade", "https://open.example.test/#ade?x=1"] + \
        ["https://open.example.test/#ade"] * 3


def test_the_csp_names_exactly_what_the_page_needs():
    with open(os.path.join(HERE, "Caddyfile"), encoding="utf-8") as fh:
        value = [line for line in fh if "Content-Security-Policy" in line][0].split('"')[1]
    csp = {d.split()[0]: d.split()[1:] for d in (p.strip() for p in value.split(";")) if d}
    assert csp == {"default-src": ["'none'"], "script-src": ["'self'"], "style-src": ["'self'"], "img-src": ["'self'"],
                   "connect-src": ["'self'"], "manifest-src": ["'self'"], "worker-src": ["'self'"],
                   "form-action": ["'self'"], "frame-ancestors": ["'none'"], "base-uri": ["'none'"]}


def test_the_page_subscribes_with_the_browser_s_keys():
    with open(os.path.join(HERE, "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    assert re.search(r'api\("POST", "/api/ade/push/subscribe", \{ endpoint: json\.endpoint, keys: json\.keys \}\)', script)


@pytest.mark.skipif(NODE is None, reason="needs node to run the service worker")
def test_a_backslash_does_not_take_a_tap_off_this_site(tmp_path):
    # "/\evil.example/x" starts with "/" and not "//", yet a URL parser reads it as //evil.example/x
    assert _tap(tmp_path, ["/\\evil.example/x"])["taps"][0].startswith("https://open.example.test/")


@pytest.mark.parametrize("endpoint", ["https://fcm.googleapis.com:99999/x", "https://fcm.googleapis.com:abc/x"])
def test_a_malformed_port_is_refused_as_a_push_error(endpoint):
    # urlsplit().port raises a bare ValueError, which no handler maps to 400
    with pytest.raises(push.PushError):
        push.check_endpoint(endpoint)
