"""Connected apps (oauth.py): each of the account's connections with where it stands and when it was last used;
Revoke, with the sign-in alone, ending its tokens at their next use and its hold while it waits; and its connector
events, a page at a time. Another account's are not found."""

import time

import pytest

from conftest import signed_up
from connector_fixtures import allowed, connected, exchange
from proofpane_open import oauth, store
from proofpane_open.ade import holds


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.delenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", raising=False)
    oauth._failed.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


def listed(client, owner):
    r = client.get("/api/connector/grants", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["grants"]


def test_an_app_is_listed_with_where_it_stands(client, owner):
    got = allowed(client, owner)
    [app] = listed(client, owner)
    assert (app["id"], app["client_name"], app["returns_to"], app["described_at"], app["state"]) == \
        (got["grant"], "Claude", "claude.ai", None, "connecting")
    assert app["scopes"] == ["ade.read", "ade.propose", "offline_access"] and app["proposals_waiting"] == 0
    exchange(client, got)
    [app] = listed(client, owner)
    assert app["state"] == "waiting" and app["hold_id"] == got["hold"]["id"]
    later = oauth.grants_for(app_user(client, owner), now=app["takes_effect_at"])["grants"][0]
    assert later["state"] == "active"                       # its hold settled by the list itself


def app_user(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


def test_an_app_that_never_collected_its_code_is_not_listed_once_the_code_expired(client, owner):
    allowed(client, owner)
    user = app_user(client, owner)
    # timed from when the app was allowed, as its code is: a second read of the clock can fall a second later
    with store.connect() as db:
        at = db.execute("SELECT allowed_at FROM ade_connector_grants WHERE user_id = ?", (user,)).fetchone()[0]
    assert len(oauth.grants_for(user, now=at + oauth.CODE_TTL - 1)["grants"]) == 1
    assert oauth.grants_for(user, now=at + oauth.CODE_TTL)["grants"] == []


def test_revoke_takes_the_sign_in_alone_and_ends_the_tokens_at_their_next_use(client, owner):
    t = connected(client, owner)
    assert oauth.authenticate("Bearer " + t["access_token"])["id"] == t["grant"]
    r = client.delete(f"/api/connector/grants/{t['grant']}", headers=owner)
    assert r.status_code == 200, r.text
    assert (r.json()["state"], r.json()["revoked_why"]) == ("revoked", "the owner revoked it")
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + t["access_token"])
    with store.connect() as db:
        assert db.execute("SELECT cancelled_at FROM ade_holds").fetchone()[0] is not None
    assert client.delete(f"/api/connector/grants/{t['grant']}", headers=owner).json()["revoked_at"] == \
        r.json()["revoked_at"]
    assert listed(client, owner)[0]["state"] == "revoked"


def test_a_hold_that_ends_on_a_revoked_connection_leaves_it_revoked(client, owner):
    got = allowed(client, owner)
    with store.connect() as db:          # revoked without its hold cancelled, which no path of the app does
        db.execute("UPDATE ade_connector_grants SET revoked_at = ?, revoked_why = 'gone' WHERE id = ?",
                   (int(time.time()), got["grant"]))
    assert holds.settle(now=got["hold"]["takes_effect_at"]) == [got["hold"]["id"]]
    with store.connect() as db:
        assert db.execute("SELECT failed FROM ade_holds WHERE id = ?", (got["hold"]["id"],)).fetchone()[0] == \
            "The connection was revoked before it took effect."
        assert db.execute("SELECT active_at FROM ade_connector_grants WHERE id = ?", (got["grant"],)).fetchone()[0] \
            is None


def _sign_out_everywhere(client, owner):
    from test_passkeys import sign_out_everywhere
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _remove_a_passkey(client, owner):
    from test_passkeys import Authenticator, add, remove
    assert add(client, owner, Authenticator()).status_code == 200
    [key] = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    r = remove(client, owner, key["id"])
    assert r.status_code == 200, r.text
    return owner


@pytest.mark.parametrize("step", [_sign_out_everywhere, _remove_a_passkey])
def test_a_lost_device_s_steps_cancel_a_connection_still_waiting_on_its_hold(client, owner, step):
    """Allowing an app is confirmed, not signed, so the confirmation may have come from the lost device: a connection
    still waiting on its hold is revoked by either step, as the account's other approvals held are cancelled."""
    t = connected(client, owner)
    headers = step(client, owner)
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + t["access_token"])
    [app] = listed(client, headers)
    assert (app["state"], app["revoked_why"]) == ("revoked", "it was cancelled before it took effect")


def test_signing_out_everywhere_leaves_a_connection_in_effect_to_its_revoke(client, owner, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    t = connected(client, owner)
    headers = _sign_out_everywhere(client, owner)
    grant = oauth.authenticate("Bearer " + t["access_token"])
    assert grant["id"] == t["grant"] and grant["active"] is True
    assert listed(client, headers)[0]["state"] == "active"


def test_another_account_s_app_is_not_found(client, owner):
    t = connected(client, owner)
    other = signed_up(client, "other@example.com")
    assert client.delete(f"/api/connector/grants/{t['grant']}", headers=other).status_code == 404
    assert client.get(f"/api/connector/grants/{t['grant']}/events", headers=other).status_code == 404
    assert listed(client, other) == []
    assert oauth.authenticate("Bearer " + t["access_token"])["id"] == t["grant"]


def test_an_app_that_ended_is_listed_for_thirty_days(client, owner):
    t = connected(client, owner)
    client.delete(f"/api/connector/grants/{t['grant']}", headers=owner)
    user = app_user(client, owner)
    assert len(oauth.grants_for(user, now=int(time.time()) + 29 * 86400)["grants"]) == 1
    assert oauth.grants_for(user, now=int(time.time()) + 31 * 86400)["grants"] == []


def test_every_live_app_is_listed_however_many_ended_after_it(client, owner, monkeypatch):
    """Revoke is on a live app's line in the list: connections that ended later never push it off, and only those that
    ended are kept to the list's length."""
    monkeypatch.setattr(oauth, "LISTED", 1)
    lives = [connected(client, owner) for _ in range(2)]                # more live ones than the list keeps of ended
    with store.connect() as db:                     # allowed a while ago, before the ones that ended, the first later
        for n, t in enumerate(lives):
            db.execute("UPDATE ade_connector_grants SET allowed_at = ? WHERE id = ?",
                       (int(time.time()) - 1000 * (n + 1), t["grant"]))
    for _ in range(3):
        t = connected(client, owner)
        assert client.delete(f"/api/connector/grants/{t['grant']}", headers=owner).status_code == 200
    got = oauth.grants_for(app_user(client, owner))["grants"]
    assert [a["id"] for a in got if a["state"] != "revoked"] == [t["grant"] for t in lives]
    assert [a["state"] for a in got].count("revoked") == 1
    assert [a["allowed_at"] for a in got] == sorted((a["allowed_at"] for a in got), reverse=True)        # newest first


def test_an_app_s_events_newest_first_a_page_at_a_time(client, owner, monkeypatch):
    t = connected(client, owner)
    for n in range(3):
        with pytest.raises(oauth.Unauthorized):                         # expired: noted, once a minute
            oauth.authenticate("Bearer " + t["access_token"], now=int(time.time()) + 3601 + n * oauth.REFUSED_EVERY)
    monkeypatch.setattr(oauth, "EVENTS_PAGE", 2)
    first = client.get(f"/api/connector/grants/{t['grant']}/events", headers=owner).json()
    assert [e["kind"] for e in first["events"]] == ["refused", "refused"] and first["before"]
    second = client.get(f"/api/connector/grants/{t['grant']}/events?before={first['before']}", headers=owner).json()
    assert [e["kind"] for e in second["events"]] == ["refused", "connected"]
    rest = client.get(f"/api/connector/grants/{t['grant']}/events?before={second['before']}", headers=owner).json()
    assert ([e["kind"] for e in rest["events"]], rest["before"]) == (["allowed"], None)
    huge = client.get(f"/api/connector/grants/{t['grant']}/events?before={10 ** 30}", headers=owner)
    assert huge.status_code == 200 and huge.json()["events"]
