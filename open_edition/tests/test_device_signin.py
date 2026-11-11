"""Signing a device in from another one (auth.py): a device with no sign-in shows a short code, the person approves it
on a device already signed in, and the asking device receives a sign-in of that account, once. The code and the
device's own secret are kept as hashes; the code lasts ten minutes; nothing is given to a request refused, expired, or
already collected; and only a signed-in account can see what is asking, approve it or refuse it. Approving takes the
person's own word as well (tests/test_device_approval.py)."""

import re

import pytest

from conftest import ADMIN, signed_up
from proofpane_open import auth, config, passkeys, store
from test_device_approval import word_for

OTHER = "other@example.com"


def test_a_device_is_signed_in_once_with_the_account_that_approved_it(client):
    owner = signed_up(client)
    started = client.post("/api/auth/device/start", json={"name": "Proofpane Community on iPhone"}).json()
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", started["code"])
    assert started["interval"] == config.DEVICE_SIGNIN_POLL_SECONDS and len(started["poll"]) >= 40
    with store.connect() as db:                      # neither the code nor the device's secret is kept as given
        row = db.execute("SELECT * FROM device_signins WHERE id = ?", (started["id"],)).fetchone()
    assert started["code"] not in dict(row).values() and started["poll"] not in dict(row).values()

    asked = {"id": started["id"], "poll": started["poll"]}
    assert client.post("/api/auth/device/poll", json=asked).json() == {"status": "waiting"}
    # what is asking, for the person about to approve; written as the device showed it or as typed, the same code
    typed = started["code"].lower().replace("-", " ")
    seen = client.post("/api/auth/device/lookup", headers=owner, json={"code": typed})
    assert seen.status_code == 200 and seen.json()["name"] == "Proofpane Community on iPhone"
    given = word_for(client, owner, typed)
    assert client.post("/api/auth/device/approve", headers=owner, json={"code": typed, "confirmation": given}).json() == {
        "name": "Proofpane Community on iPhone", "approved": True}
    with store.connect() as db:
        db.execute("UPDATE device_signins SET last_poll_at = NULL")        # past the poll interval
    got = client.post("/api/auth/device/poll", json=asked).json()
    assert got["status"] == "approved" and got["user"]["email"] == ADMIN
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + got["token"]})
    assert me.status_code == 200 and me.json()["email"] == ADMIN
    with store.connect() as db:
        db.execute("UPDATE device_signins SET last_poll_at = NULL")
    assert client.post("/api/auth/device/poll", json=asked).json() == {"status": "used"}        # once
    again = client.post("/api/auth/device/approve", headers=owner, json={"code": started["code"]})
    assert again.status_code == 400 and "No device is waiting" in again.json()["detail"]


def test_only_a_signed_in_account_sees_approves_or_refuses_and_a_refused_device_gets_nothing(client):
    started = client.post("/api/auth/device/start", json={"name": "A browser"}).json()
    for verb in ("lookup", "approve", "refuse"):
        assert client.post(f"/api/auth/device/{verb}", json={"code": started["code"]}).status_code == 401
    other = signed_up(client, OTHER)
    assert client.post("/api/auth/device/refuse", headers=other, json={"code": started["code"]}).json() == {
        "name": "A browser", "approved": False}
    assert client.post("/api/auth/device/approve", headers=other, json={"code": started["code"]}).status_code == 400
    assert client.post("/api/auth/device/poll", json={"id": started["id"], "poll": started["poll"]}).json() == {
        "status": "refused"}


def test_the_device_alone_can_ask_and_not_too_often():
    started = auth.start_device_signin("Phone", now=1000)
    with pytest.raises(auth.AuthError):
        auth.poll_device_signin(started["id"], "not-its-secret", now=1001)
    with pytest.raises(auth.AuthError):
        auth.poll_device_signin("no-such-id", started["poll"], now=1001)
    assert auth.poll_device_signin(started["id"], started["poll"], now=1002) == {"status": "waiting"}
    assert auth.poll_device_signin(started["id"], started["poll"], now=1003) == {"status": "slow down"}
    assert auth.poll_device_signin(started["id"], started["poll"], now=1004) == {"status": "waiting"}


def test_a_request_lasts_ten_minutes_approved_or_not():
    user_id = auth.register(auth.create_invitation("late@example.com", "test"), "correct horse battery")[0]
    started = auth.start_device_signin("Phone", now=1000)
    end = 1000 + config.DEVICE_SIGNIN_TTL_SECONDS
    with pytest.raises(auth.AuthError):
        auth.decide_device_signin(user_id, started["code"], True, now=end)
    assert auth.poll_device_signin(started["id"], started["poll"], now=end) == {"status": "expired"}
    # approved in time, it is still collected only within the time
    late = auth.start_device_signin("Phone", now=2000)
    given = passkeys.confirm(auth.user(user_id), password="correct horse battery", now=2010,
                             subject=auth.approval_subject(late["code"], now=2010))["confirmation"]
    auth.decide_device_signin(user_id, late["code"], True, now=2010, confirmation=given)
    assert auth.poll_device_signin(late["id"], late["poll"], now=2000 + config.DEVICE_SIGNIN_TTL_SECONDS) == {
        "status": "expired"}


def test_a_device_names_itself_in_what_the_approver_can_read():
    for name in ("", " ", "x" * 61, "line\nbreak", "bell\x07", "tab\there",
                 "Ana\u202es iPhone",        # turns the text after it around
                 "An\u200ba",                # an invisible space
                 "Ana\u2066s\u2069",        # an isolate, which hides what is in it from the order around it
                 "no\u00a0break", "line\u2028sep", "private\ue000use", None, 7):
        with pytest.raises(auth.AuthError):
            auth.start_device_signin(name)
    # letters of any script, marks, digits, punctuation and symbols are fine, kept as written, trimmed and composed
    accented = "Ame\u0301lie's iPad"        # e and a combining acute accent: composed into one letter
    assert auth.device_name("  " + accented + " ") == "Am\u00e9lie's iPad"
    for name in ("Proofpane Community on Mac", "Phone \U0001f4f1 #2", "\u0645\u0627\u0643"):
        assert auth.device_name(name) == name


def test_only_so_many_wait_at_once_and_what_is_refused_makes_room(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_SIGNINS_PENDING", 2)
    owner = auth.register(auth.create_invitation("room@example.com", "test"), "correct horse battery")[0]
    auth.start_device_signin("one", now=5000)
    two = auth.start_device_signin("two", now=5000)
    with pytest.raises(auth.AuthError) as refused:
        auth.start_device_signin("three", now=5000)
    assert "Too many" in str(refused.value)
    # a refused request is no longer open
    auth.decide_device_signin(owner, two["code"], False, now=5001)
    assert auth.start_device_signin("four", now=5002)["code"]
    # nor is an expired one
    assert auth.start_device_signin("five", now=5000 + config.DEVICE_SIGNIN_TTL_SECONDS)["code"]


def test_one_network_has_only_so_many_open_and_another_is_not_held_up(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_SIGNINS_PER_NETWORK", 2)
    for _ in range(2):
        assert auth.start_device_signin("Phone", "203.0.113.7", now=7000)["from"] == "203.0.113.7"
    with pytest.raises(auth.AuthError) as refused:
        auth.start_device_signin("Phone", "203.0.113.7", now=7000)
    assert "This network" in str(refused.value)
    # the same address written as IPv4 inside IPv6 is the same network
    with pytest.raises(auth.AuthError):
        auth.start_device_signin("Phone", "::ffff:203.0.113.7", now=7000)
    assert auth.start_device_signin("Phone", "198.51.100.2", now=7000)["code"]
    # an IPv6 client has a /64, all of it one network
    auth.start_device_signin("Phone", "2001:db8:1:2::1", now=7000)
    auth.start_device_signin("Phone", "2001:db8:1:2:ffff::9", now=7000)
    with pytest.raises(auth.AuthError):
        auth.start_device_signin("Phone", "2001:db8:1:2::77", now=7000)
    assert auth.start_device_signin("Phone", "2001:db8:1:3::1", now=7000)["code"]
    # an address the server cannot read is not counted as a network, and not shown
    assert auth.start_device_signin("Phone", "testclient", now=7000)["from"] is None


def test_one_holder_of_many_addresses_has_only_so_many_open_too(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_SIGNINS_PER_WIDE_NETWORK", 3)
    # three addresses of one IPv4 /24, one each
    for last in (1, 2, 3):
        auth.start_device_signin("Phone", f"203.0.113.{last}", now=9000)
    with pytest.raises(auth.AuthError) as refused:
        auth.start_device_signin("Phone", "203.0.113.4", now=9000)
    assert "This network" in str(refused.value)
    assert auth.start_device_signin("Phone", "203.0.114.1", now=9000)["code"]      # the next /24 is another
    # three /64s of one IPv6 /48
    for sub in (1, 2, 3):
        auth.start_device_signin("Phone", f"2001:db8:7:{sub}::1", now=9000)
    with pytest.raises(auth.AuthError):
        auth.start_device_signin("Phone", "2001:db8:7:ff::1", now=9000)
    assert auth.start_device_signin("Phone", "2001:db8:8::1", now=9000)["code"]
    assert auth.network_of("203.0.113.9", wide=True) == "203.0.113.0/24"
    assert auth.network_of("2001:db8:7:ff::1", wide=True) == "2001:db8:7::/48"
    assert auth.network_of("::ffff:203.0.113.9", wide=True) == "203.0.113.0/24"


def test_the_approver_sees_where_the_request_came_from_against_their_own_network():
    asked = auth.start_device_signin("Proofpane Community on Mac", "203.0.113.7", now=8000)
    seen = auth.device_signin_view(asked["code"], "203.0.113.7", now=8001)
    assert seen["asked_from"] == "203.0.113.7" and seen["viewer"] == "203.0.113.7" and seen["same_network"] is True
    assert auth.device_signin_view(asked["code"], "198.51.100.2", now=8001)["same_network"] is False
    assert auth.device_signin_view(asked["code"], "testclient", now=8001)["same_network"] is None
    unknown = auth.start_device_signin("Phone", "", now=8000)
    seen = auth.device_signin_view(unknown["code"], "203.0.113.7", now=8001)
    assert seen["asked_from"] is None and seen["same_network"] is None


def test_the_routes_see_the_client_s_address():
    from fastapi.testclient import TestClient
    from proofpane_open.main import app
    asker = TestClient(app, client=("203.0.113.7", 50000))
    started = asker.post("/api/auth/device/start", json={"name": "Phone"}).json()
    assert started["from"] == "203.0.113.7"
    approver = TestClient(app, client=("203.0.113.7", 50001))
    owner = signed_up(approver)
    seen = approver.post("/api/auth/device/lookup", headers=owner, json={"code": started["code"]}).json()
    assert seen["asked_from"] == "203.0.113.7" and seen["same_network"] is True
