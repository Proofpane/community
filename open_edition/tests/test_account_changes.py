"""A change to how an account signs in is told at once to every device the account subscribed for notifications
(ade/push.py notice): a passkey added or removed, another device approved, every device signed out. Each is told in
fixed words (push.words), never a passkey's or a device's name, the account's address or where a device asked from.
A change refused tells nothing, and one device that cannot be reached does not keep the others
from hearing."""

import threading
import time

import pytest

from conftest import signed_up
from proofpane_open.ade import push
from test_ade_push import FCM, Device, PushService
from test_device_approval import word_for
from test_passkeys import Authenticator, add, remove, sign_out_everywhere


@pytest.fixture
def service(monkeypatch):
    """The push service, played by a test; a notice is sent at once here instead of from a thread of its own."""
    played = PushService()
    monkeypatch.setattr(push, "_default_post", played)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    return played


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def phone(client, owner):
    """A browser of the owner's that turned notifications on."""
    device = Device()
    push.subscribe(client.get("/api/auth/me", headers=owner).json()["id"], FCM, device.p256dh, push.b64u(device.auth))
    return device


def heard(device, service):
    return [device.decrypt(body) for _, _, body in service.posts]


def test_each_change_to_how_the_account_signs_in_reaches_its_devices_in_fixed_words(client, owner, phone, service):
    add(client, owner, Authenticator(), name="Ana's iPhone")
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    remove(client, owner, listed["id"])
    sign_out_everywhere(client, owner)
    got = heard(phone, service)
    assert [(m["title"], m["body"], m["url"]) for m in got] == [
        ("A passkey was added to your account", "Not you? Open Account, remove it, then sign out everywhere.",
         "/app/#account"),
        ("A passkey was removed from your account", "Not you? Open Account and sign out everywhere.", "/app/#account"),
        ("Every device was signed out", "The device that asked was signed in again. Not you? Sign in and sign out "
         "everywhere again.", "/app/#account")]
    assert "Ana" not in repr(got) and "example.com" not in repr(got)
    assert len({m["tag"] for m in got}) == 3          # each its own, so one does not replace another on the device


def test_a_device_approved_is_told_in_fixed_words_never_its_name_or_where_it_asked_from(client, owner, phone, service,
                                                                                        monkeypatch):
    monkeypatch.setattr("proofpane_open.main._client", lambda request: "203.0.113.7")
    for approve in (False, True):
        code = client.post("/api/auth/device/start", json={"name": "Kitchen iPad"}).json()["code"]
        # approving takes a confirmation made for the device (tests/test_device_approval.py); refusing, nothing
        body = {"code": code, "confirmation": word_for(client, owner, code)} if approve else {"code": code}
        r = client.post("/api/auth/device/approve" if approve else "/api/auth/device/refuse", headers=owner,
                        json=body)
        assert r.json() == {"name": "Kitchen iPad", "approved": approve}
    (told,) = heard(phone, service)                   # the refusal told nothing
    assert (told["title"], told["body"], told["url"]) == (
        "A device was signed in", "Not you? Open Account and sign out everywhere.", "/app/#account")
    assert not any(w in repr(told) for w in ("Kitchen", "203.0.113.7", "example.com"))
    # a code that is not waiting approves nothing and tells nothing
    assert client.post("/api/auth/device/approve", headers=owner, json={"code": "ABCD-EFGH"}).status_code >= 400
    assert len(service.posts) == 1


def test_a_change_refused_tells_nothing(client, owner, phone, service):
    add(client, owner, Authenticator())
    service.posts.clear()
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert client.request("DELETE", f"/api/account/passkeys/{listed['id']}", headers=owner).status_code == 403
    assert client.post("/api/account/sign-out-everywhere", headers=owner).status_code == 403
    # a well-formed id that is no passkey of the account's, so a confirmation can name it and the 404 is the answer
    assert remove(client, owner, "0" * 32).status_code == 404
    assert service.posts == []


def test_every_device_hears_though_one_cannot_be_reached(client, owner, phone, service, monkeypatch):
    other = Device()
    push.subscribe(client.get("/api/auth/me", headers=owner).json()["id"], FCM + "-second", other.p256dh,
                   push.b64u(other.auth))

    def flaky(url, headers, body):
        if url == FCM:
            raise OSError("the push service did not answer")
        return service(url, headers, body)

    monkeypatch.setattr(push, "_default_post", flaky)
    sign_out_everywhere(client, owner)
    assert [other.decrypt(body)["title"] for _, _, body in service.posts] == ["Every device was signed out"]


def test_the_change_does_not_wait_for_the_push_services(client, owner, phone, monkeypatch):
    # the real _spawn: the request returns while a push service is still answering
    answer = threading.Event()
    posted = []

    def slow(url, headers, body):
        answer.wait(10)
        posted.append(url)
        return 201

    monkeypatch.setattr(push, "_default_post", slow)
    started = time.monotonic()
    assert sign_out_everywhere(client, owner).status_code == 200
    assert time.monotonic() - started < 5 and posted == []
    answer.set()
    for _ in range(100):
        if posted:
            break
        time.sleep(0.05)
    assert posted == [FCM]


def test_an_account_with_no_device_sends_nothing_and_starts_no_thread(client, owner, service, monkeypatch):
    spawned = []
    monkeypatch.setattr(push, "_spawn", spawned.append)
    assert push.notice(client.get("/api/auth/me", headers=owner).json()["id"], push.words("signed-out")) == 0
    sign_out_everywhere(client, owner)
    assert service.posts == [] and spawned == []


def test_a_change_made_is_not_failed_by_its_notice(client, owner, phone, service, monkeypatch):
    def broken(user_id):
        raise OSError("the database could not be read")

    monkeypatch.setattr(push, "subscriptions", broken)
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 200
