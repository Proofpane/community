"""A lost device's steps and the Claude app connector (oauth.py): a connection keeps the passkey whose confirmation
allowed it, and removing that passkey revokes it, in effect or waiting on its hold, at once while end-to-end encryption
is off, and while it is on as the revocation that removes the passkey is placed, not when its hold ends; signing out
everywhere ends none, and answers and tells of those allowed in the last seven days; each step says that connected
apps keep working until revoked in Connected apps; and letting apps connect tells the account's devices."""

import time

import pytest

from conftest import signed_up
from connector_fixtures import CLAUDE, account_id, authorize, exchange, pkce, query, register, request_id, turn_on
from proofpane_open import oauth, store
from proofpane_open.ade import holds, push
from test_ade_e2e import Keys, enrol, passkey_of, revocation
from test_passkeys import Authenticator, add, confirmed, remove, sign_out_everywhere


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", raising=False)
    oauth._failed.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    assert add(client, owner, d).status_code == 200
    return d


@pytest.fixture
def second(client, owner, device):
    d = Authenticator(alg=-8)
    assert add(client, owner, d, name="Second", confirm_with=device).status_code == 200
    return d


@pytest.fixture
def told(monkeypatch):
    """Every notice the account's devices are sent at once, stood in: its title, body and page."""
    sent = []
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None:
                        sent.append({"title": message.title, "body": message.body, "url": message.url}) or 1)
    return sent


def allow_with(client, owner, device=None, name="Claude"):
    """An app registered and allowed with a confirmation `device`'s passkey made (the password's without one), and its
    code exchanged: what the allow answered, with the app's tokens."""
    client_id = register(client, name=name)
    verifier, challenge = pkce()
    rid = request_id(authorize(client, client_id, challenge))
    body = {"propose": True, "account": account_id(client, owner),
            **confirmed(client, owner, device, change=f"connector-allow:{rid}:propose")}
    r = client.post(f"/api/connector/requests/{rid}/allow", headers=owner, json=body)
    assert r.status_code == 200, r.text
    got = dict(r.json(), client_id=client_id, verifier=verifier, code=query(r.json()["redirect"])["code"],
               redirect_uri=CLAUDE)
    t = exchange(client, got)
    assert t.status_code == 200, t.text
    return dict(got, **t.json())


def in_effect(got):
    holds.settle(now=got["hold"]["takes_effect_at"])
    assert grant(got)["active_at"] is not None


def grant(got):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_connector_grants WHERE id = ?", (got["grant"],)).fetchone())


def works(got, now=None):
    try:
        return oauth.authenticate("Bearer " + got["access_token"], now=now)["id"] == got["grant"]
    except oauth.Unauthorized:
        return False


def test_a_connection_keeps_the_passkey_that_allowed_it(client, owner, device):
    turn_on(client, owner)
    assert grant(allow_with(client, owner, device))["confirmed_by"] == passkey_of(client, owner, device)["id"]
    assert grant(allow_with(client, owner))["confirmed_by"] is None                 # the password names none


def test_removing_a_passkey_revokes_every_connection_it_allowed_in_effect_or_waiting(client, owner, device, second,
                                                                                    told):
    turn_on(client, owner)
    by_it, by_device, by_password = (allow_with(client, owner, d) for d in (second, device, None))
    for got in (by_it, by_device, by_password):
        in_effect(got)
    waiting = allow_with(client, owner, second)
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert sorted(r.json()["disconnected"]) == sorted([by_it["grant"], waiting["grant"]])
    assert r.json()["apps"] == ("2 app connections it allowed were revoked. Other connected apps keep working until "
                                "you revoke them in Connected apps.")
    for got in (by_it, waiting):
        assert grant(got)["revoked_why"] == oauth.REMOVED_WHY and not works(got)
    assert works(by_device) and works(by_password)
    with store.connect() as db:
        assert holds.get(db, waiting["hold"]["id"])["cancelled_at"] is not None
    holds.settle(now=waiting["hold"]["takes_effect_at"])
    assert grant(waiting)["active_at"] is None
    # the notice says some were revoked and opens Connected apps, by no name; the page's answer names them
    assert told[-1] == {"title": "A passkey was removed from your account", "url": "/app/#apps",
                        "body": "The app connections it allowed were revoked. Not you? Open Account and sign out "
                                "everywhere."}


def test_a_connection_that_ended_stays_ended_when_the_passkey_that_allowed_it_goes(client, owner, device, second):
    turn_on(client, owner)
    old = allow_with(client, owner, second)
    in_effect(old)
    with store.connect() as db:                     # its 90 days over
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (int(time.time()) - 1, old["grant"]))
    r = remove(client, owner, passkey_of(client, owner, second)["id"])
    assert r.json()["disconnected"] == [] and grant(old)["revoked_at"] is None


def test_removing_a_passkey_that_allowed_none_says_the_others_keep_working(client, owner, device, second, told):
    turn_on(client, owner)
    in_effect(allow_with(client, owner, device))
    r = remove(client, owner, passkey_of(client, owner, second)["id"])
    assert (r.json()["disconnected"], r.json()["apps"]) == ([], oauth.KEEP_WORKING)
    assert told[-1] == {"title": "A passkey was removed from your account", "url": "/app/#account",
                        "body": "Not you? Open Account and sign out everywhere."}


def test_with_the_deployment_s_connector_off_nothing_is_said_of_apps_that_cannot_work(client, owner, device, second,
                                                                                     monkeypatch):
    turn_on(client, owner)
    by_it = allow_with(client, owner, second)
    monkeypatch.delenv("OPEN_EDITION_CONNECTOR")
    r = remove(client, owner, passkey_of(client, owner, second)["id"])
    assert r.json()["disconnected"] == [by_it["grant"]] and r.json()["apps"] == "1 app connection it allowed was revoked."
    assert oauth.lost_device_words() == "" and oauth.signed_out_words([{"client_name": "Claude"}]) == ""


def test_with_encryption_on_placing_a_revocation_ends_what_the_passkey_it_removes_allowed_at_once(
        client, owner, device, second, told):
    """While end-to-end encryption is on, a passkey is removed only when a revocation's hold ends, a day later when it
    removes the account's other credentials. The connections the passkey allowed end as the revocation is placed: a
    connection waiting on its hold is cancelled, and one in effect revoked, at once."""
    keys = Keys(client, owner, device)
    keys.turn_on()
    enrol(keys, second, device)
    turn_on(client, owner)
    gone = passkey_of(client, owner, second)["id"]
    by_it, kept = allow_with(client, owner, second), allow_with(client, owner, device)
    in_effect(by_it)
    in_effect(kept)
    waiting = allow_with(client, owner, second)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[gone])[0])
    assert r.status_code == 200, r.text
    placed = r.json()["hold"]
    assert placed["takes_effect_at"] > int(time.time()) + 3600
    assert gone in {p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    for got in (by_it, waiting):
        assert grant(got)["revoked_why"] == oauth.REVOCATION_WHY and not works(got)
    with store.connect() as db:
        assert holds.get(db, waiting["hold"]["id"])["cancelled_at"] is not None
    holds.settle(now=waiting["hold"]["takes_effect_at"])
    assert grant(waiting)["active_at"] is None
    assert works(kept)
    # one it allows while the revocation waits ends as the revocation takes effect and removes it
    meanwhile = allow_with(client, owner, second)
    in_effect(meanwhile)
    holds.settle(keys.account, now=placed["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, placed["id"])["failed"] is None
    assert gone not in {p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    assert grant(meanwhile)["revoked_why"] == oauth.REMOVED_WHY and grant(kept)["revoked_at"] is None


def test_signing_out_everywhere_answers_and_tells_of_the_connections_allowed_in_the_last_seven_days(client, owner,
                                                                                                   told):
    turn_on(client, owner)
    recent, old, gone = (allow_with(client, owner, name=n) for n in ("Claude", "Old app", "Gone"))
    for got in (recent, old, gone):
        in_effect(got)
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET allowed_at = allowed_at - 8 * 86400 WHERE id = ?", (old["grant"],))
    client.delete(f"/api/connector/grants/{gone['grant']}", headers=owner)
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    [shown] = r.json()["connected_apps"]
    assert (shown["id"], shown["client_name"], shown["state"]) == (recent["grant"], "Claude", "active")
    assert works(recent) and works(old)                      # it ends neither
    words = ("Connected apps keep working until you revoke them in Connected apps; allowed in the last 7 days: "
             "“Claude”.")
    assert r.json()["apps"] == words
    # the notice says connected apps keep working and opens Connected apps, by no app's name
    assert told[-1] == {"title": "Every device was signed out", "url": "/app/#apps",
                        "body": "The device that asked was signed in again; connected apps keep working until you "
                                "revoke them. Not you? Sign in and sign out everywhere again."}


def test_signing_out_everywhere_with_none_allowed_lately_says_they_keep_working(client, owner, told):
    r = sign_out_everywhere(client, owner)
    assert (r.json()["connected_apps"], r.json()["apps"]) == ([], oauth.KEEP_WORKING)
    assert (told[-1]["title"], told[-1]["url"]) == ("Every device was signed out", "/app/#account")


def test_letting_apps_connect_tells_the_account_s_devices_once(client, owner, told):
    turn_on(client, owner)
    assert told == [{"title": "Letting apps connect was turned on", "url": "/app/#apps",
                     "body": "Each app still needs your Allow. Not you? Turn it off in Connected apps."}]
    turn_on(client, owner)                                   # on already: nothing changed, nothing told
    client.put("/api/connector/account", headers=owner, json={"on": False})
    assert len(told) == 1
    r = client.put("/api/connector/account", headers=owner, json={"on": True})      # refused: nothing told
    assert r.status_code == 403 and len(told) == 1
