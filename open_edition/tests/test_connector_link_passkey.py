"""Linking turned on for a connected app, and the passkey whose confirmation turned it on (ade/connector_link.py):
removing that passkey turns linking off for the app and ends the sessions linked through it, as it revokes a
connection that passkey allowed (tests/test_connector_lost_device.py). A lost device's passkey, removed, leaves no
linking it turned on."""

import time

import pytest

from conftest import signed_up
from connector_fixtures import confirmation, connected, turn_on
from proofpane_open import oauth, store
from proofpane_open.ade import connector, connector_link
from test_ade_e2e import passkey_of
from test_connector_lost_device import allow_with, in_effect
from test_passkeys import Authenticator, add, confirmed, remove


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
def device(client, owner):
    d = Authenticator()
    assert add(client, owner, d).status_code == 200
    return d


@pytest.fixture
def second(client, owner, device):
    d = Authenticator(alg=-8)
    assert add(client, owner, d, name="Second", confirm_with=device).status_code == 200
    return d


def call(app, name, **args):
    return connector.call(app, {"name": name, "arguments": args}, int(time.time()))


def grants():
    with store.connect() as db:
        return {r["id"]: dict(r) for r in db.execute("SELECT * FROM ade_connector_grants")}


def sessions():
    with store.connect() as db:
        return {r["name"]: dict(r) for r in db.execute("SELECT * FROM ade_connector_links")}


def let_link(client, owner, grant_id, with_device=None):
    body = confirmed(client, owner, with_device, change=f"connector-links:{grant_id}") if with_device else {
        "confirmation": confirmation(client, owner, f"connector-links:{grant_id}")}
    r = client.put(f"/api/connector/grants/{grant_id}/links", headers=owner, json=dict(body, on=True))
    assert r.status_code == 200, r.text
    return oauth.authenticate


def test_linking_goes_off_with_the_passkey_that_turned_it_on_and_with_no_other(client, owner, device, second):
    by_second, by_device, by_password = (connected(client, owner) for _ in range(3))
    let_link(client, owner, by_second["grant"], second)
    let_link(client, owner, by_device["grant"], device)
    let_link(client, owner, by_password["grant"])
    kept = grants()
    assert kept[by_second["grant"]]["links_by"] == passkey_of(client, owner, second)["id"]
    assert kept[by_device["grant"]]["links_by"] == passkey_of(client, owner, device)["id"]
    assert kept[by_password["grant"]]["links_by"] is None               # the password names no passkey
    for t, name in ((by_second, "of the second"), (by_device, "of the device"), (by_password, "of the password")):
        app = oauth.authenticate("Bearer " + t["access_token"])
        assert call(app, "link_session", session=name)["isError"] is False
        assert call(app, "link_post", session=name, kind="step", text=f"{name}: a step")["isError"] is False
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    # the app the removed passkey let link is still connected (the password allowed it): it links no more
    after = grants()
    assert after[by_second["grant"]]["revoked_at"] is None
    assert (after[by_second["grant"]]["links_at"], after[by_second["grant"]]["links_by"]) == (None, None)
    assert after[by_device["grant"]]["links_at"] is not None and after[by_password["grant"]]["links_at"] is not None
    state = sessions()
    assert state["of the second"]["ended_why"] == connector_link.BY_PASSKEY and state["of the second"]["ended_at"]
    assert state["of the device"]["ended_at"] is None and state["of the password"]["ended_at"] is None
    with store.connect() as db:
        texts = [r["text"] for r in db.execute("SELECT text FROM ade_connector_link_events ORDER BY seq")]
        events = [(r["kind"], r["subject"]) for r in db.execute(
            "SELECT kind, subject FROM ade_connector_events WHERE grant_id = ? AND kind LIKE 'links-%' ORDER BY rowid",
            (by_second["grant"],))]
    assert texts == ["of the device: a step", "of the password: a step"]
    assert events == [("links-on", None), ("links-off", connector_link.BY_PASSKEY)]
    app = oauth.authenticate("Bearer " + by_second["access_token"])
    said = call(app, "link_post", session="of the second", kind="step", text="t")["structuredContent"]["said"]
    assert said == connector_link.LINKS_OFF
    # turned on again, with the password this time, its session is told why its link ended
    let_link(client, owner, by_second["grant"])
    assert grants()[by_second["grant"]]["links_by"] is None
    said = call(app, "link_post", session="of the second", kind="step", text="t")["structuredContent"]["said"]
    assert said == connector_link.ENDED.format(why=connector_link.BY_PASSKEY)


def test_turning_linking_off_forgets_the_passkey_that_turned_it_on(client, owner, device):
    t = connected(client, owner)
    let_link(client, owner, t["grant"], device)
    assert grants()[t["grant"]]["links_by"] == passkey_of(client, owner, device)["id"]
    assert client.put(f"/api/connector/grants/{t['grant']}/links", headers=owner, json={"on": False}).status_code == 200
    assert (grants()[t["grant"]]["links_at"], grants()[t["grant"]]["links_by"]) == (None, None)


def test_a_passkey_s_going_leaves_an_app_already_disconnected_as_it_was(client, owner, device, second):
    """Revoking an app ends its sessions and leaves its switch as it was. The passkey that had turned its linking on,
    removed later, turns nothing off there and adds no event to it: only an app still connected is told."""
    t = connected(client, owner)
    let_link(client, owner, t["grant"], second)
    assert client.delete(f"/api/connector/grants/{t['grant']}", headers=owner).status_code == 200
    was = grants()[t["grant"]]
    assert was["revoked_at"] is not None and was["links_at"] is not None

    def events():
        with store.connect() as db:
            return [r["kind"] for r in db.execute("SELECT kind FROM ade_connector_events WHERE grant_id = ? ORDER BY "
                                                  "rowid", (t["grant"],))]
    before = events()
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert grants()[t["grant"]] == was and events() == before


@pytest.mark.parametrize("cleared", (True, False), ids=("its sessions ended by the timer", "the timer not yet run"))
def test_a_passkey_s_going_leaves_a_connection_past_its_end_as_it_was(client, owner, device, second, cleared):
    """A connection that has run its 90 days is a connected app no more, as a revoked one is none. The passkey that
    had turned its linking on, removed later, turns nothing off there, ends no session of it (that is the timer's),
    adds no event to it, and neither counts it nor speaks of it: the answer names the app still connected alone, as
    the page's list did before the removal."""
    ended, live = connected(client, owner), connected(client, owner)
    for t, name in ((ended, "of the ended"), (live, "of the live")):
        let_link(client, owner, t["grant"], second)
        app = oauth.authenticate("Bearer " + t["access_token"])
        assert call(app, "link_session", session=name)["isError"] is False
    with store.connect() as db:
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (int(time.time()) - 10, ended["grant"]))
    if cleared:
        connector_link.prune()
        assert sessions()["of the ended"]["ended_why"] == connector_link.BY_APP_END

    def events():
        with store.connect() as db:
            return [r["kind"] for r in db.execute("SELECT kind FROM ade_connector_events WHERE grant_id = ? ORDER BY "
                                                  "rowid", (ended["grant"],))]
    was, session, before = grants()[ended["grant"]], sessions()["of the ended"], events()
    assert was["links_at"] is not None and was["revoked_at"] is None
    gone = passkey_of(client, owner, second)["id"]
    assert linking(client, owner)[gone] == [grants()[live["grant"]]["client_name"]]     # the page's list, before
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert (r.json()["disconnected"], r.json()["unlinked"]) == ([], [live["grant"]])
    assert r.json()["apps"] == ("Linking was turned off for 1 connected app it had turned it on for: the sessions "
                                "linked through it ended, and their posts and checkpoints were deleted. "
                                + oauth.KEEP_WORKING)
    assert grants()[ended["grant"]] == was and sessions()["of the ended"] == session and events() == before
    after = grants()[live["grant"]]
    assert (after["links_at"], after["links_by"]) == (None, None)
    assert sessions()["of the live"]["ended_why"] == connector_link.BY_PASSKEY



def test_a_connection_is_past_its_end_from_the_second_it_ends(client, owner, device, second):
    """A connection is live while its end is ahead, and ended from the second of its end on, as the check of its token
    holds it (oauth.authenticate). The linking side of a passkey's going reads the same second."""
    t = connected(client, owner)
    let_link(client, owner, t["grant"], second)
    row, its = grants()[t["grant"]], passkey_of(client, owner, second)["id"]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert connector_link.passkeys_going(db, row["user_id"], [its], row["ends_at"]) == []
    assert grants()[t["grant"]] == row
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert connector_link.passkeys_going(db, row["user_id"], [its], row["ends_at"] - 1) == [t["grant"]]
    assert grants()[t["grant"]]["links_at"] is None


# ---- what the removal says, before it and after ----------------------------------------------------------------------

def linking(client, owner):
    """What the account's list of passkeys says of linking: {a passkey's id: the names of the apps}."""
    r = client.get("/api/account/passkeys", headers=owner)
    assert r.status_code == 200, r.text
    return {k["id"]: k["linking"] for k in r.json()["passkeys"]}


def test_removing_the_passkey_that_turned_linking_on_says_what_it_ended_and_deleted(client, owner, device, second):
    """An app the password allowed stays connected when the passkey that turned its linking on is removed, and its
    sessions end with their posts and checkpoints deleted. The removal's answer names the app (`unlinked`) and says
    so; it does not answer as if nothing of the connected apps had changed."""
    t = connected(client, owner)                                    # allowed with the password
    let_link(client, owner, t["grant"], second)
    app = oauth.authenticate("Bearer " + t["access_token"])
    assert call(app, "link_session", session="one")["isError"] is False
    assert call(app, "link_post", session="one", kind="step", text="work")["isError"] is False
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert (r.json()["disconnected"], r.json()["unlinked"]) == ([], [t["grant"]])
    assert r.json()["apps"] == ("Linking was turned off for 1 connected app it had turned it on for: the sessions "
                                "linked through it ended, and their posts and checkpoints were deleted. "
                                + oauth.KEEP_WORKING)
    assert sessions()["one"]["ended_why"] == connector_link.BY_PASSKEY
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_connector_link_events").fetchone()[0] == 0
    # a passkey that turned no linking on: nothing unlinked, and the words as they were
    again = Authenticator(alg=-8)
    assert add(client, owner, again, name="Third", confirm_with=device).status_code == 200
    third = passkey_of(client, owner, again)["id"]
    r = remove(client, owner, third, body=confirmed(client, owner, device, change=f"passkey-remove:{third}"))
    assert (r.json()["unlinked"], r.json()["apps"]) == ([], oauth.KEEP_WORKING)
    # the words for several, and beside connections revoked
    assert oauth.lost_device_words(2, 3) == (
        "2 app connections it allowed were revoked. Linking was turned off for 3 connected apps it had turned it on "
        "for: the sessions linked through them ended, and their posts and checkpoints were deleted. Other connected "
        "apps keep working until you revoke them in Connected apps.")


def test_the_page_s_list_of_passkeys_says_which_apps_linking_leans_on_each(client, owner, device, second):
    """Before a passkey is removed the page can say what its removal ends: each passkey is listed with the names of
    the apps still connected whose linking it turned on (`linking`), and with none once that linking is off, the app
    is revoked, or it was the password that turned it on."""
    by_second, by_password, revoked, ended = (connected(client, owner) for _ in range(4))
    let_link(client, owner, by_second["grant"], second)
    let_link(client, owner, by_password["grant"])
    let_link(client, owner, revoked["grant"], second)
    let_link(client, owner, ended["grant"], second)
    assert client.delete(f"/api/connector/grants/{revoked['grant']}", headers=owner).status_code == 200
    with store.connect() as db:                         # a connection whose 90 days are over is connected no more
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (int(time.time()) - 10, ended["grant"]))
    name = grants()[by_second["grant"]]["client_name"]
    its, other = passkey_of(client, owner, second)["id"], passkey_of(client, owner, device)["id"]
    assert linking(client, owner) == {its: [name], other: []}
    assert client.put(f"/api/connector/grants/{by_second['grant']}/links", headers=owner,
                      json={"on": False}).status_code == 200
    assert linking(client, owner) == {its: [], other: []}


# ---- a passkey that ALLOWED an app: its removal revokes the app, and a revoke ends what is linked through it ------

def listed(client, owner):
    """The account's passkeys as the page lists them, by name."""
    r = client.get("/api/account/passkeys", headers=owner)
    assert r.status_code == 200, r.text
    return {k["name"]: k for k in r.json()["passkeys"]}


def linked_through(app):
    """A session linked through `app`, with a post and a checkpoint."""
    assert call(app, "link_session", session="one")["isError"] is False
    assert call(app, "link_post", session="one", kind="step", text="work")["isError"] is False
    assert call(app, "link_checkpoint", session="one", document={"doing": "d"}, expected_version=0)["isError"] is False


@pytest.mark.parametrize("links_by", ["the same passkey", "the password", "another passkey"])
def test_removing_a_passkey_that_allowed_an_app_says_what_that_ends_and_deletes(client, owner, device, second,
                                                                               links_by):
    """Removing a passkey revokes each app it allowed, and a revoke ends the sessions linked through the app and
    deletes their posts and checkpoints, whoever turned that app's linking on. The list says it before, under the
    passkey that allowed the app (`disconnects`, the apps it allowed whose linking is on), and names that app under no
    passkey as one that stays connected with its linking off (`linking`); the removal's answer says it after."""
    turn_on(client, owner)
    t = allow_with(client, owner, second)
    in_effect(t)
    let_link(client, owner, t["grant"],
             {"the same passkey": second, "the password": None, "another passkey": device}[links_by])
    linked_through(oauth.authenticate("Bearer " + t["access_token"]))
    before = listed(client, owner)
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    # what it did: the app revoked, its session ended, the post and the checkpoint gone
    assert (len(r.json()["disconnected"]), r.json()["unlinked"]) == (1, [])
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_connector_link_events").fetchone()[0] == 0
    assert [(s["ended_why"], s["checkpoint"]) for s in sessions().values()] == [(connector_link.BY_REVOKE, None)]
    # what it says after
    assert r.json()["apps"] == ("1 app connection it allowed was revoked: the sessions linked through it ended, and "
                                "their posts and checkpoints were deleted. Other connected apps keep working until you "
                                "revoke them in Connected apps.")
    # and what the list said before
    [first] = [name for name in before if name != "Second"]
    assert before["Second"].get("disconnects") == ["Claude"]
    assert before["Second"]["linking"] == []
    assert before[first].get("disconnects") == []
    assert before[first]["linking"] == (["Claude"] if links_by == "another passkey" else [])
    assert all(k.get("disconnects") == [] and k["linking"] == [] for k in listed(client, owner).values())


def test_removing_a_passkey_says_of_how_many_of_the_apps_it_allowed_the_sessions_ended(client, owner, device, second):
    turn_on(client, owner)
    one, two, three = (allow_with(client, owner, second, name=name) for name in ("Claude", "Other", "Third"))
    for t in (one, two, three):
        in_effect(t)
    let_link(client, owner, one["grant"])
    let_link(client, owner, three["grant"], device)
    before = listed(client, owner)
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert r.json()["apps"] == ("3 app connections it allowed were revoked: the sessions linked through 2 of them "
                                "ended, and their posts and checkpoints were deleted. Other connected apps keep "
                                "working until you revoke them in Connected apps.")
    # in the order they were allowed, and within a second by their ids: as sets here
    assert sorted(before["Second"].get("disconnects") or []) == ["Claude", "Third"]


def test_removing_a_passkey_that_allowed_an_app_with_linking_off_says_as_it_did(client, owner, device, second):
    turn_on(client, owner)
    in_effect(allow_with(client, owner, second))
    before = listed(client, owner)
    gone = passkey_of(client, owner, second)["id"]
    r = remove(client, owner, gone, body=confirmed(client, owner, device, change=f"passkey-remove:{gone}"))
    assert r.json()["apps"] == ("1 app connection it allowed was revoked. Other connected apps keep working until you "
                                "revoke them in Connected apps.")
    assert before["Second"].get("disconnects") == []


def test_the_list_names_as_disconnected_by_a_removal_only_an_app_still_connected_with_linking_on(client, owner, device,
                                                                                               second):
    """`disconnects` is what removing the passkey would end now: an app it allowed that is revoked already, one past
    its end, and one whose linking was turned off again are none of them named."""
    turn_on(client, owner)
    live, revoked, ended, off = (allow_with(client, owner, second, name=name)
                                 for name in ("Live", "Revoked", "Ended", "Off"))
    for t in (live, revoked, ended, off):
        in_effect(t)
        let_link(client, owner, t["grant"])
    assert sorted(listed(client, owner)["Second"]["disconnects"]) == ["Ended", "Live", "Off", "Revoked"]
    assert client.delete(f"/api/connector/grants/{revoked['grant']}", headers=owner).status_code == 200
    with store.connect() as db:                         # a connection whose 90 days are over is connected no more
        db.execute("UPDATE ade_connector_grants SET ends_at = ? WHERE id = ?", (int(time.time()) - 10, ended["grant"]))
    assert client.put(f"/api/connector/grants/{off['grant']}/links", headers=owner,
                      json={"on": False}).status_code == 200
    assert listed(client, owner)["Second"]["disconnects"] == ["Live"]


def test_the_words_for_the_apps_a_removed_passkey_allowed():
    words = oauth.lost_device_words
    ended = ": the sessions linked through {} ended, and their posts and checkpoints were deleted."
    keep = " Other connected apps keep working until you revoke them in Connected apps."
    assert words(1, 0, 1) == "1 app connection it allowed was revoked" + ended.format("it") + keep
    assert words(2, 0, 2) == "2 app connections it allowed were revoked" + ended.format("them") + keep
    assert words(3, 0, 1) == "3 app connections it allowed were revoked" + ended.format("1 of them") + keep
    assert words(2, 0, 0) == words(2) == "2 app connections it allowed were revoked." + keep
    assert words(0, 0, 0) == words() == oauth.KEEP_WORKING
    assert words(1, 1, 1) == (
        "1 app connection it allowed was revoked" + ended.format("it") + " Linking was turned off for 1 connected app "
        "it had turned it on for: the sessions linked through it ended, and their posts and checkpoints were deleted."
        + keep)
