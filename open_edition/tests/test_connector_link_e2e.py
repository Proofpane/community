"""End-to-end encryption and a session linked through the Claude app connector (ade/connector_link.py): such a session
holds no key and seals nothing, so turning encryption on ends every one of the account's and deletes their posts and
checkpoints, in the transaction in which it takes effect, and while it is on none links or sends."""

import json
import time

import pytest

from conftest import signed_up
from connector_fixtures import confirmation, connected
from proofpane_open import oauth, store
from proofpane_open.ade import connector, connector_link, e2e_keys, holds
from test_ade_e2e import Keys, at_once, view
from test_passkeys import Authenticator, add


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def keys(client, owner):
    device = Authenticator()
    assert add(client, owner, device).status_code == 200
    return Keys(client, owner, device)


def call(app, name, **args):
    return connector.call(app, {"name": name, "arguments": args}, int(time.time()))


def rows(table):
    with store.connect() as db:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def test_turning_encryption_on_ends_the_sessions_linked_so_and_deletes_what_they_sent(client, owner, keys,
                                                                                      monkeypatch):
    t = connected(client, owner)
    r = client.put(f"/api/connector/grants/{t['grant']}/links", headers=owner,
                   json={"on": True, "confirmation": confirmation(client, owner, f"connector-links:{t['grant']}")})
    assert r.status_code == 200, r.text
    app = oauth.authenticate("Bearer " + t["access_token"])
    for name in ("one", "two"):
        assert call(app, "link_session", session=name)["isError"] is False
    assert call(app, "link_post", session="one", kind="step", text="A-STEP-IN-THE-CLEAR")["isError"] is False
    assert call(app, "link_post", session="one", kind="question", text="A-QUESTION-IN-THE-CLEAR")["isError"] is False
    assert call(app, "link_checkpoint", session="two", document={"doing": "DOING-IN-THE-CLEAR"},
                expected_version=0)["isError"] is False
    # one of the account's that had ended already, and another account's session: neither is this turning on's
    assert call(app, "link_session", session="removed before")["isError"] is False
    removed = [s for s in rows("ade_connector_links") if s["name"] == "removed before"][0]
    assert client.delete(f"/api/connector/links/{removed['id']}", headers=owner).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_connector_links SET ended_at = ended_at - 500 WHERE id = ?", (removed["id"],))
    was = [s for s in rows("ade_connector_links") if s["name"] == "removed before"][0]
    other = signed_up(client, "other@example.test")
    theirs = connected(client, other)
    turned = client.put(f"/api/connector/grants/{theirs['grant']}/links", headers=other, json={
        "on": True, "confirmation": confirmation(client, other, f"connector-links:{theirs['grant']}")})
    assert turned.status_code == 200, turned.text
    their_app = oauth.authenticate("Bearer " + theirs["access_token"])
    assert call(their_app, "link_session", session="one")["isError"] is False
    assert call(their_app, "link_post", session="one", kind="step", text="ANOTHER-ACCOUNT-S-STEP")["isError"] is False
    began = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: began.append(1))
    body, signed, private = keys.turn_on_body(seal=False)
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body))
    assert r.status_code == 200, r.text
    # held: nothing is ended or deleted before its hold ends
    assert len(rows("ade_connector_link_events")) == 3
    assert [s["name"] for s in rows("ade_connector_links") if s["ended_at"] is not None] == ["removed before"]
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    with store.connect() as db:
        assert holds.get(db, r.json()["hold"]["id"])["failed"] is None
    every = rows("ade_connector_links")
    kept = [s for s in every if s["user_id"] == keys.account and s["name"] in ("one", "two")]
    assert [(s["ended_why"], s["checkpoint"], s["checkpoint_version"]) for s in kept] == [
        ("end-to-end encryption was turned on", None, 0)] * 2
    assert all(s["ended_at"] is not None for s in kept)
    assert "IN-THE-CLEAR" not in json.dumps(every) + json.dumps(rows("ade_connector_link_events"))
    # the one that had ended is as it was, and the other account's session and its post are untouched
    assert [s for s in every if s["name"] == "removed before"] == [was]
    [their_s] = [s for s in every if s["user_id"] != keys.account]
    assert their_s["ended_at"] is None
    assert [e["text"] for e in rows("ade_connector_link_events")] == ["ANOTHER-ACCOUNT-S-STEP"]
    # having deleted them, the database is due for compaction, though nothing was sealed
    v = view(client, owner)
    assert v["on"] is True and v["compaction_due"] is True and began == [1]
    # and while it is on, none links or sends, and the page lists none
    for tool, args in (("link_session", {}), ("link_post", {"kind": "step", "text": "t"}),
                       ("link_checkpoint", {"document": {}, "expected_version": 0})):
        result = call(app, tool, session="one", **args)
        assert result["isError"] is True and result["structuredContent"]["said"] == connector_link.E2E_ON
    assert client.get("/api/connector/links", headers=owner).json()["links"] == []
    assert all(s["ended_at"] is not None for s in rows("ade_connector_links") if s["user_id"] == keys.account)
    assert [s["name"] for s in client.get("/api/connector/links", headers=other).json()["links"]] == ["one"]


def test_turning_encryption_on_with_no_session_linked_so_asks_no_compaction_for_them(client, owner, keys,
                                                                                    monkeypatch):
    began = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: began.append(1))
    body, signed, private = keys.turn_on_body(seal=False)
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body))
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    v = view(client, owner)
    assert v["on"] is True and v["compaction_due"] is False and began == []
    with store.connect() as db:                     # asked again, it has nothing to end: it says so
        assert connector_link.encryption_on(db, keys.account, int(time.time())) == 0
