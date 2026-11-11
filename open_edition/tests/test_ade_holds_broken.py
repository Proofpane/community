"""A hold whose kind's apply breaks (raises, where a refusal returns why), for each kind that holds something while it
waits (ade/holds.py: released): it ends failed, never tried again, and what it held is freed as a refusal frees it. A
change to end-to-end encryption frees the one place a change may wait in, so the next change, a revocation among them,
is taken; a request to link a session is left failed; a connection is revoked. And a change past its time that settling
could not reach is cancelled with its guard's proof."""

import time

import pytest

from conftest import signed_up
from connector_fixtures import allowed, exchange
from proofpane_open import oauth, store
from proofpane_open.ade import e2e_keys, holds, link_asks, moves
from test_ade_e2e import Keys, cancel, held_rotation, revocation, rows
from test_passkeys import Authenticator, add


def breaks(db, hold, now):
    raise RuntimeError("a bug")


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    assert add(client, owner, d).status_code == 200
    return d


@pytest.fixture
def keys(client, owner, device):
    k = Keys(client, owner, device)
    k.turn_on()
    return k


def broken_apply(monkeypatch, kind):
    """The registered apply of `kind` made to raise, as a bug would; a call that puts the kinds back as they were."""
    real = holds._KINDS
    monkeypatch.setattr(holds, "_KINDS", {k: dict(v) for k, v in real.items()})
    holds._KINDS[kind]["apply"] = breaks
    return lambda: monkeypatch.setattr(holds, "_KINDS", real)


def test_a_change_to_encryption_whose_apply_breaks_frees_the_place_for_the_next(client, owner, keys, monkeypatch):
    held = held_rotation(keys)
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [held["id"]]
    mended = broken_apply(monkeypatch, held["kind"])
    assert holds.settle(keys.account, now=held["takes_effect_at"]) == [held["id"]]
    with store.connect() as db:
        assert holds.get(db, held["id"])["failed"] == holds.BROKE
    assert rows("ade_e2e_pending") == []                                 # the one place a change waits in: free
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1]         # and nothing of it took effect
    mended()
    nxt = held_rotation(keys)                                            # the next change is taken
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [nxt["id"]]


def test_a_revocation_whose_apply_breaks_leaves_room_for_another(client, owner, keys, monkeypatch):
    """Encryption keeps one change waiting, and a revocation waits for none but another revocation: one stuck for
    good would keep every later revocation out, the owner's of a stolen passkey among them."""
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0])
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    mended = broken_apply(monkeypatch, hold["kind"])
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert rows("ade_e2e_pending") == []
    mended()
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0])
    assert r.status_code == 200, r.text


def test_a_change_past_its_time_that_settling_could_not_reach_is_cancelled_with_its_guard_s_proof(
        client, owner, keys, device, monkeypatch):
    held = held_rotation(keys)
    with store.connect() as db:                                          # its time has come
        db.execute("UPDATE ade_holds SET takes_effect_at = ? WHERE id = ?", (int(time.time()) - 60, held["id"]))
    real = holds.settle
    monkeypatch.setattr(holds, "settle", lambda user_id=None, now=None: [])           # as when the store was busy
    r = cancel(keys, held["id"])                                          # the sign-in alone: its guard refuses
    assert r.status_code == 403, r.text
    r = cancel(keys, held["id"], device)
    assert r.status_code == 200 and r.json()["cancelled_at"], r.text
    assert rows("ade_e2e_pending") == []
    monkeypatch.setattr(holds, "settle", real)
    assert holds.settle(keys.account) == [] and [s["seq"] for s in rows("ade_e2e_statements")] == [1]


def test_a_request_to_link_whose_linking_breaks_is_left_failed(client, owner, monkeypatch):
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    now = int(time.time())
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO ade_link_asks (id, address, user_id, name, kind, public_key, key_id, asked_net, "
                   "created_at, expires_at, state) VALUES ('ask1', 'ask-a', ?, 'mac', 'local', 'k', 'kid', 'n', ?, ?, "
                   "'held')", (user_id, now, now + 1800))
        hold = holds.place(db, user_id, link_asks.HOLD, "ask1", "Linking mac was approved", now)
    broken_apply(monkeypatch, link_asks.HOLD)
    assert holds.settle(user_id, now=hold["takes_effect_at"]) == [hold["id"]]
    assert rows("ade_link_asks", id="ask1")[0]["state"] == "failed"
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == holds.BROKE


def test_a_connection_whose_taking_effect_breaks_is_revoked(client, owner, monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    got = allowed(client, owner)
    t = exchange(client, got).json()
    broken_apply(monkeypatch, oauth.HOLD)
    assert holds.settle(now=got["hold"]["takes_effect_at"]) == [got["hold"]["id"]]
    [grant] = rows("ade_connector_grants")
    assert (grant["revoked_why"], grant["active_at"]) == ("it could not take effect", None)
    with pytest.raises(oauth.Unauthorized):
        oauth.authenticate("Bearer " + t["access_token"], now=got["hold"]["takes_effect_at"])


def test_each_kind_that_holds_something_while_it_waits_releases_it():
    """A change to encryption, a request to link and a connection each name what frees what they hold; a move holds
    nothing a hold that has ended does not free (a move is under way only while its hold has not ended)."""
    assert all(holds._KINDS[k]["released"] is e2e_keys._discard for k in (e2e_keys.HOLD, e2e_keys.HOLD_RECOVERY,
                                                                         e2e_keys.HOLD_LONG))
    assert holds._KINDS[link_asks.HOLD]["released"] is link_asks._broke
    assert holds._KINDS[oauth.HOLD]["released"] is oauth._hold_broke
    assert holds._KINDS[moves.HOLD]["released"] is None
