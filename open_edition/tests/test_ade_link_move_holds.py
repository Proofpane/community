"""A move waits on a hold (ade/moves.py, ade/holds.py), through the app: posted, it is held, and nothing of it reaches
its link (no event, no row of ade_link_moves, no seq) until the hold ends; then it is checked again and stored, at a seq
past any its link has read. The owner cancels it until then, and a passkey removed or a link revoked meanwhile ends
it. The moves held count in the owner's commands an hour and in a link's commands not taken; the owner's list, Linked
sessions and the notice say where each stands. The link is the tests' own signer, and the server's clock the test's."""

import json
import threading
import time
import uuid

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import holds, link, link_commands, moves, push, workspaces
from proofpane_core.canonical import canonicalize
from test_ade_link import OTHER, b64u, paired, revoke
from test_ade_link_commands import account, device, owner, send, take  # noqa: F401  fixtures
from test_ade_link_moves import SESSION, _instruction, lists, move_payload, workspace  # noqa: F401  fixtures
from test_ade_push import FCM, Device, PushService
from test_passkeys import Authenticator, add, remove

WAIT = moves.HOLD_DEFAULT


@pytest.fixture
def at(monkeypatch):
    """The server's clock and the signers', which the test moves: [now]."""
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    return t


def held_move(client, owner, device, s, session=SESSION, **over):
    """A move signed and posted, so held: (the answer, its payload)."""
    p = move_payload(client, owner, s.link_id, session, **over)
    r = send(client, owner, device, p)
    assert r.status_code == 200, r.text
    return r.json(), p


def events(kind):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_events WHERE kind = ? ORDER BY seq", (kind,))]


def stored_moves():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_moves ORDER BY seq")]


def held_row(hold_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_held_moves WHERE hold_id = ?", (hold_id,)).fetchone())


def hold_of(hold_id):
    with store.connect() as db:
        return holds.get(db, hold_id)


def owners(client, owner, **query):
    r = client.get("/api/ade/link/moves", headers=owner, params=query)
    assert r.status_code == 200, r.text
    return r.json()["moves"]


def sessions(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def untaken(uid, s, n, client_id=None):
    """`n` instructions to the link `s` that it has not taken, stored as the server stores them."""
    now = int(time.time())
    with store.connect() as db:
        for i in range(n):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at, to_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", client_id or uuid.uuid4().hex, "instruction", s.name, "t", 1, now, s.link_id,
                        now + 3600))


# ---- held: nothing reaches the link ---------------------------------------------------------------------------------

def test_a_held_move_makes_no_event_and_no_row_and_its_link_reads_nothing_of_it(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    before = mac.call("GET", "/feed").json()
    got, p = held_move(client, owner, device, mac)
    assert set(got) == {"held", "expires_at"} and got["expires_at"] == p["expires_at"]          # no seq
    hold = got["held"]
    assert (hold["kind"], hold["subject"], hold["approved_at"], hold["takes_effect_at"], hold["told"]) == \
        (moves.HOLD, p["id"], at[0], at[0] + WAIT, 0)
    assert events("move") == [] and stored_moves() == []
    assert mac.call("GET", "/feed").json() == before                     # no event, and the head where it was
    assert mac.call("GET", "/feed", query={"turn": 1}).json()["events"] == []
    for seq in range(1, before["head"] + 5):
        assert take(mac, seq).status_code in (404, 422)                  # nothing to take
    kept = held_row(hold["id"])
    assert kept["payload"].encode("utf-8") == canonicalize(p) and kept["seq"] is None
    assert json.loads(kept["signature"])["id"] == b64u(device.credential_id)
    assert (kept["user_id"], kept["link_id"], kept["session_id"], kept["title"], kept["client_id"],
            kept["expires_at"], kept["created_at"]) == (account(client, owner), mac.link_id, SESSION, "Work on s1",
                                                        p["id"], p["expires_at"], at[0])
    with store.connect() as db:
        assert db.execute("SELECT passkey_id FROM ade_link_held_moves").fetchone()[0] == \
            db.execute("SELECT id FROM passkeys").fetchone()[0]


def test_at_its_time_a_settle_stores_it_past_what_its_link_read_and_the_link_takes_it(client, owner, device,
                                                                                    workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    got, p = held_move(client, owner, device, mac)
    hold = got["held"]
    at[0] = hold["takes_effect_at"] - 1
    assert holds.settle() == [] and events("move") == []
    read = mac.call("GET", "/feed").json()                               # the link's cursor, as it would keep it
    at[0] += 1
    assert holds.settle() == [hold["id"]]
    (row,) = events("move")
    assert row["seq"] > read["head"]                                     # past anything its link has read
    assert row["payload"].encode("utf-8") == canonicalize(p)
    assert json.loads(row["signature"])["id"] == b64u(device.credential_id)
    assert (row["link_id"], row["kind"], row["to_id"], row["to_name"], row["client_id"], row["text"],
            row["needs_turn"], row["received_at"], row["expires_at"]) == (
        "owner", "move", mac.link_id, "mac", p["id"], "move session s1 into the workspace", 1, at[0], p["expires_at"])
    (m,) = stored_moves()
    assert (m["seq"], m["state"], m["link_id"], m["session_id"], m["title"], m["created_at"], m["expires_at"]) == \
        (row["seq"], "asked", mac.link_id, SESSION, "Work on s1", at[0], p["expires_at"])
    assert held_row(hold["id"])["seq"] == row["seq"]
    assert hold_of(hold["id"])["done_at"] == at[0] and hold_of(hold["id"])["failed"] is None
    after = mac.call("GET", "/feed", query={"after": read["through"]}).json()["events"]
    assert [(e["seq"], e["kind"], e["state"]) for e in after] == [(row["seq"], "move", "queued")]
    assert mac.call("GET", f"/events/{row['seq']}").json()["payload"] == row["payload"]
    assert take(mac, row["seq"]).status_code == 200 and stored_moves()[0]["state"] == "taken"


@pytest.mark.parametrize("stored", ["before the head is read", "after the head is read"])
def test_a_move_stored_while_its_link_reads_its_feed_is_never_passed_over(client, owner, device, workspace, at,
                                                                         monkeypatch, stored):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    at[0] = hold["takes_effect_at"]
    head = link._head

    def settled_meanwhile(db, user_id):              # the notifier's round stores the move as the feed is read
        if stored.startswith("before"):
            holds.settle()
        got = head(db, user_id)
        if stored.startswith("after"):
            holds.settle()
        return got

    monkeypatch.setattr(link, "_head", settled_meanwhile)
    got = mac.call("GET", "/feed").json()
    (row,) = events("move")
    assert all(e["seq"] <= got["head"] for e in got["events"]), got     # nothing past the head it gives
    assert row["seq"] in [e["seq"] for e in got["events"]] or got["through"] < row["seq"], got
    monkeypatch.setattr(link, "_head", head)
    after = mac.call("GET", "/feed", query={"after": got["through"]}).json()["events"]
    assert [e["seq"] for e in got["events"] + after].count(row["seq"]) == 1     # given once, by one read or the next


def test_a_cancelled_move_is_never_stored(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    at[0] = hold["takes_effect_at"] - 1
    r = client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner)
    assert r.status_code == 200 and r.json()["cancelled_at"] == at[0]
    at[0] += 7200
    assert holds.settle() == [] and owners(client, owner)[0]["state"] == "cancelled"
    assert events("move") == [] and stored_moves() == [] and held_row(hold["id"])["seq"] is None
    assert client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner).json() == r.json()    # as the first


def test_a_hold_whose_time_has_come_has_stored_its_move_and_is_past_cancelling(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    at[0] = hold["takes_effect_at"]
    r = client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner)
    assert r.status_code == 409 and "it has taken effect" in r.json()["detail"]
    assert len(events("move")) == 1 and stored_moves()[0]["state"] == "asked"


def test_nothing_a_link_sends_shortens_applies_or_cancels_a_hold(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    signed = mac.call("POST", "/x", {}).request.headers["authorization"]
    for method, path in (("POST", f"/api/ade/holds/{hold['id']}/cancel"), ("GET", "/api/ade/holds"),
                         ("GET", "/api/ade/link/moves")):
        assert client.request(method, path, headers={"Authorization": signed}).status_code == 401
    at[0] = hold["takes_effect_at"] - 10
    # a request dated as late as the ADE accepts is read at the ADE's own time
    assert mac.call("GET", "/feed", ts=at[0] + link.SKEW - 1).json()["events"] == []
    at[0] += 60                                       # past its time, within its life
    for r in (mac.call("GET", "/feed"), mac.call("POST", "/heartbeat", {}), mac.call("GET", "/resume")):
        assert r.status_code == 200
    assert events("move") == []                       # its time came, and no read of the link's took it out of its hold
    assert hold_of(hold["id"])["takes_effect_at"] == hold["takes_effect_at"]
    assert hold_of(hold["id"])["cancelled_at"] is None and hold_of(hold["id"])["done_at"] is None
    holds.settle()                                    # as the notifier's next round, or the page's next read
    assert len(events("move")) == 1


# ---- what is checked again as the hold ends -------------------------------------------------------------------------

@pytest.mark.parametrize("meanwhile, why", [
    ("unlisted", moves.UNLISTED),
    ("withheld", moves.WITHHELD),
    ("revoked", moves.REVOKED),
    ("passkey", moves.WITHDRAWN),
    ("queue", f"Its link holds {link_commands.QUEUED_PER_LINK} commands it has not taken, as many as it may."),
    ("id", "A command with this id is stored already."),
    ("workspace", "A move goes into your hosted workspace: The hosted workspace is off on this deployment."),
])
def test_each_check_made_again_as_the_hold_ends_keeps_the_move_from_being_stored(client, owner, device, workspace, at,
                                                                                monkeypatch, meanwhile, why):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    got, p = held_move(client, owner, device, mac)
    hold, uid = got["held"], account(client, owner)
    with store.connect() as db:
        if meanwhile == "revoked":       # a revoke that left the hold, as none can now: the check still holds
            db.execute("UPDATE ade_links SET revoked_at = ? WHERE id = ?", (at[0], mac.link_id))
        if meanwhile == "passkey":       # likewise a passkey gone without its removal ending the hold
            db.execute("DELETE FROM passkeys")
    if meanwhile == "unlisted":
        lists(mac, ("s2", False))
    if meanwhile == "withheld":
        lists(mac, (SESSION, True))
    if meanwhile == "queue":
        untaken(uid, mac, link_commands.QUEUED_PER_LINK)
    if meanwhile == "id":                # an instruction stored meanwhile with the move's id
        untaken(uid, mac, 1, client_id=p["id"])
    if meanwhile == "workspace":         # the deployment's workspace turned off meanwhile

        def off(user):
            raise workspaces.WorkspaceAccess(404, "The hosted workspace is off on this deployment.")

        monkeypatch.setattr(workspaces, "for_user", off)
    at[0] = hold["takes_effect_at"]
    assert holds.settle() == [hold["id"]]
    ended = hold_of(hold["id"])
    assert (ended["done_at"], ended["failed"]) == (at[0], why)
    assert events("move") == [] and stored_moves() == [] and held_row(hold["id"])["seq"] is None
    (v,) = owners(client, owner)
    assert (v["seq"], v["state"], v["reason"]) == (None, "failed", why)


def test_a_move_is_stored_with_a_minute_of_its_life_left_and_not_with_less(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    life = WAIT + moves.HOLD_MARGIN
    one = held_move(client, owner, device, mac, expires_at=at[0] + life)[0]["held"]
    two = held_move(client, owner, device, mac, "s2", expires_at=at[0] + life + 1)[0]["held"]
    at[0] += life - moves.LIFE_LEFT + 1               # the notifier and the page late: the first has 59 seconds left
    assert sorted(holds.settle()) == sorted([one["id"], two["id"]])
    assert hold_of(one["id"])["failed"] == (f"Under 60 seconds of its life were left when its hold ended: it ends at "
                                            f"{holds.utc(at[0] + 59)}.")
    assert hold_of(two["id"])["failed"] is None
    assert [m["session_id"] for m in stored_moves()] == ["s2"]


def test_a_move_is_stored_while_its_link_has_room_for_it(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    untaken(account(client, owner), mac, link_commands.QUEUED_PER_LINK - 1)
    at[0] = hold["takes_effect_at"]
    holds.settle()
    assert hold_of(hold["id"])["failed"] is None and len(events("move")) == 1


# ---- the life it needs, and the limits ------------------------------------------------------------------------------

def test_a_move_must_live_its_hold_and_five_minutes_more_as_it_is_posted(client, owner, device, workspace, at,
                                                                       monkeypatch):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    short = move_payload(client, owner, mac.link_id, expires_at=at[0] + WAIT + moves.HOLD_MARGIN - 1)
    r = send(client, owner, device, short)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == ("This move has 479 seconds of its life left. A move waits 180 seconds before its "
                                  "link may take it, then needs 300 seconds more: 480 in all.")
    with store.connect() as db:          # the pairing's own hold aside (test_link_pair_hold.py), no hold of a move
        assert db.execute("SELECT COUNT(*) FROM ade_link_held_moves").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ade_holds WHERE kind = ?", (moves.HOLD,)).fetchone()[0] == 0
    monkeypatch.setenv(moves.HOLD_SETTING, "100")      # the setting read as each move is posted
    assert send(client, owner, device, short).status_code == 200         # and the refused payload's nonce was not used
    monkeypatch.setenv(moves.HOLD_SETTING, "3301")     # a hold that leaves no hour-long move room
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, expires_at=at[0] + 3600))
    assert r.status_code == 422 and "3601 in all" in r.json()["detail"]
    monkeypatch.delenv(moves.HOLD_SETTING)
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s2", expires_at=at[0] + WAIT + 300))
    assert r.status_code == 200 and r.json()["held"]["takes_effect_at"] == at[0] + WAIT


def test_the_moves_held_count_in_a_link_s_commands_not_taken(client, owner, device, workspace, at, monkeypatch):
    monkeypatch.setattr(link_commands, "QUEUED_PER_LINK", 3)
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, *((f"s{i}", False) for i in range(1, 5)))
    lists(mini, ("s5", False))
    first = held_move(client, owner, device, mac)[0]["held"]
    for session in ("s2", "s3"):
        held_move(client, owner, device, mac, session)
    for p in (move_payload(client, owner, mac.link_id, "s4"), _instruction(client, owner, mac.link_id)):
        r = send(client, owner, device, p)
        assert r.status_code == 429 and "at most 3 commands it has not taken" in r.json()["detail"], r.text
    assert held_move(client, owner, device, mini, "s5")[0]["held"]      # another link's are its own
    assert client.post(f"/api/ade/holds/{first['id']}/cancel", headers=owner).status_code == 200
    assert held_move(client, owner, device, mac, "s4")[0]["held"]       # a cancelled one counts no more


def test_a_move_whose_hold_failed_frees_its_place_among_its_link_s_commands(client, owner, device, workspace, at,
                                                                           monkeypatch):
    monkeypatch.setattr(link_commands, "QUEUED_PER_LINK", 2)
    other = Authenticator(alg=-8)
    assert add(client, owner, other, name="the other", confirm_with=device).status_code == 200
    mac = paired(client, owner)
    lists(mac, *((f"s{i}", False) for i in range(1, 4)))
    lost = held_move(client, owner, other, mac, "s1")[0]["held"]
    held_move(client, owner, device, mac, "s2")
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s3"))
    assert r.status_code == 429 and "at most 2 commands it has not taken" in r.json()["detail"], r.text
    assert remove(client, owner, next(k["id"] for k in client.get("/api/account/passkeys", headers=owner)
                                      .json()["passkeys"] if k["name"] == "the other")).status_code == 200
    assert hold_of(lost["id"])["failed"] == moves.WITHDRAWN               # ended, failed, before its time
    assert held_move(client, owner, device, mac, "s3")[0]["held"]         # its place is free


def test_every_move_signed_in_the_hour_counts_in_the_owner_s_commands_an_hour_whatever_became_of_it(
        client, owner, device, workspace, at, monkeypatch):
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 3)
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, *((f"s{i}", False) for i in range(1, 4)))
    lists(mini, ("t1", False), ("t2", False))
    t0 = at[0]
    cancelled = held_move(client, owner, device, mac, "s1")[0]["held"]
    failing = held_move(client, owner, device, mini, "t1")[0]["held"]
    held_move(client, owner, device, mac, "s2")                          # three, by two links: the account's
    for p in (move_payload(client, owner, mini.link_id, "t2"), _instruction(client, owner, mini.link_id)):
        r = send(client, owner, device, p)
        assert r.status_code == 429 and "at most 3 commands an hour" in r.json()["detail"], r.text
    assert client.post(f"/api/ade/holds/{cancelled['id']}/cancel", headers=owner).status_code == 200
    lists(mini, ("t1", True), ("t2", False))         # t1's hold fails as it ends
    at[0] = t0 + WAIT
    holds.settle()
    assert hold_of(failing["id"])["failed"] == moves.WITHHELD and len(events("move")) == 1
    for p in (move_payload(client, owner, mac.link_id, "s3"), _instruction(client, owner, mac.link_id)):
        r = send(client, owner, device, p)           # cancelled, failed or stored, each still counts: nothing given back
        assert r.status_code == 429 and "at most 3 commands an hour" in r.json()["detail"], r.text
    at[0] = t0 + 3600                                # an hour since the cancelled and the failed were signed
    assert held_move(client, owner, device, mac, "s3")[0]["held"]


def test_a_move_stored_counts_once_in_the_limits_and_no_more_as_held(client, owner, device, workspace, at,
                                                                     monkeypatch):
    monkeypatch.setattr(link_commands, "OWNER_PER_HOUR", 3)
    monkeypatch.setattr(link_commands, "QUEUED_PER_LINK", 3)
    mac = paired(client, owner)
    lists(mac, *((f"s{i}", False) for i in range(1, 5)))
    first = [held_move(client, owner, device, mac, session)[0]["held"] for session in ("s1", "s2")]
    at[0] = first[0]["takes_effect_at"]
    assert sorted(holds.settle()) == sorted(h["id"] for h in first) and len(events("move")) == 2
    with store.connect() as db:
        assert moves.held_since(db, account(client, owner), at[0] - 3600) == 0
        assert moves.held_open(db, account(client, owner), mac.link_id, at[0]) == 0
    assert held_move(client, owner, device, mac, "s3")[0]["held"]      # two stored and one held: three
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s4"))
    assert r.status_code == 429, r.text


def test_one_move_of_a_session_at_a_time_by_whichever_link(client, owner, device, workspace, at):
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, (SESSION, False))
    lists(mini, (SESSION, False))                    # the same session, listed by another link of its machine
    hold = held_move(client, owner, device, mac)[0]["held"]
    for s in (mac, mini):
        r = send(client, owner, device, move_payload(client, owner, s.link_id))
        assert r.status_code == 409 and r.json()["detail"] == moves.BUSY, r.text
    at[0] = hold["takes_effect_at"]
    holds.settle()                                   # stored and asked: still under way
    r = send(client, owner, device, move_payload(client, owner, mini.link_id))
    assert r.status_code == 409 and r.json()["detail"] == moves.BUSY, r.text
    (row,) = events("move")
    assert client.post(f"/api/ade/link/commands/{row['seq']}/withdraw", headers=owner).status_code == 200
    assert held_move(client, owner, device, mini)[0]["held"]           # it ended: another may follow
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_held_moves").fetchone()[0] == 2


def test_a_move_is_under_way_held_asked_in_its_life_taken_uploaded_or_starting(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    uid = account(client, owner)
    hold = held_move(client, owner, device, mac)[0]["held"]
    with store.connect() as db:
        assert moves._busy(db, uid, SESSION, at[0]) is True
        assert not moves._busy(db, uid, SESSION, at[0], held=False)     # as its own hold ends: the stored ones alone
        assert not moves._busy(db, uid, "s2", at[0]) and not moves._busy(db, "someone else", SESSION, at[0])
    failing = held_move(client, owner, device, mac, "s2")[0]["held"]
    lists(mac, (SESSION, False), ("s2", True))       # s2's hold fails as it ends
    at[0] = hold["takes_effect_at"]
    holds.settle()
    assert hold_of(failing["id"])["failed"] == moves.WITHHELD
    with store.connect() as db:
        assert moves._busy(db, uid, "s2", at[0]) is False               # a hold ended, failed, holds nothing
    (row,) = stored_moves()
    with store.connect() as db:
        for state, busy in (("taken", True), ("uploaded", True), ("starting", True), ("started", False),
                            ("failed", False), ("expired", False), ("asked", True)):
            db.execute("UPDATE ade_link_moves SET state = ? WHERE seq = ?", (state, row["seq"]))
            assert moves._busy(db, uid, SESSION, at[0]) is busy, state
        assert moves._busy(db, uid, SESSION, row["expires_at"] - 1) is True
        assert moves._busy(db, uid, SESSION, row["expires_at"]) is False  # its life over: not taken now, swept or not
        assert not moves._busy(db, "someone else", SESSION, at[0])


def test_a_move_whose_session_has_another_under_way_when_its_hold_ends_is_not_stored(client, owner, device, workspace,
                                                                                     at, monkeypatch):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    first = held_move(client, owner, device, mac)[0]["held"]
    check = moves.check_free
    monkeypatch.setattr(moves, "check_free", lambda *args: None)        # a second, as one signed before the check
    at[0] += 1
    second = held_move(client, owner, device, mac)[0]["held"]
    monkeypatch.setattr(moves, "check_free", check)
    at[0] = second["takes_effect_at"]
    assert holds.settle() == [first["id"], second["id"]]
    assert hold_of(first["id"])["failed"] is None and hold_of(second["id"])["failed"] == moves.BUSY_AT_END
    assert len(events("move")) == 1 and held_row(second["id"])["seq"] is None


def test_the_owner_s_list_reads_its_stored_and_held_moves_as_one(client, owner, device, workspace, at, monkeypatch):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    at[0] = hold["takes_effect_at"] - 1              # the list's own settle finds nothing due
    stored_rows, workers = moves._stored_rows, []

    def stored_then_its_hold_ends(db, where, args):
        rows = stored_rows(db, where, args)
        worker = threading.Thread(target=holds.settle, kwargs={"now": hold["takes_effect_at"]})
        worker.start()                               # the notifier's round, between the list's two reads
        worker.join(1.0)                             # it ends here, unless the list's read holds it back
        workers.append(worker)
        return rows

    monkeypatch.setattr(moves, "_stored_rows", stored_then_its_hold_ends)
    listed = owners(client, owner)
    workers[0].join(15)
    assert [(v["seq"], v["state"]) for v in listed] == [(None, "held")]    # once, as it was when the read began
    assert len(events("move")) == 1                  # the round stored it once the list was read


def test_a_move_s_id_is_one_no_move_held_and_no_command_stored_has(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    _, p = held_move(client, owner, device, mac)
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s2", id=p["id"]))
    assert r.status_code == 409 and r.json()["detail"] == "A move with this id is held already."
    seq = send(client, owner, device, _instruction(client, owner, mac.link_id)).json()["seq"]
    with store.connect() as db:
        stored = db.execute("SELECT client_id FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0]
    r = send(client, owner, device, move_payload(client, owner, mac.link_id, "s2", id=stored))
    assert r.status_code == 409 and r.json()["detail"] == "A command with this id is stored already."
    with store.connect() as db:          # the pairing's own hold aside, the one move's
        assert db.execute("SELECT COUNT(*) FROM ade_link_held_moves").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM ade_holds WHERE kind = ?", (moves.HOLD,)).fetchone()[0] == 1


# ---- a passkey removed, a link revoked ------------------------------------------------------------------------------

def test_removing_the_passkey_that_signed_a_held_move_ends_its_hold_saying_why(client, owner, device, workspace, at):
    other = Authenticator(alg=-8)
    assert add(client, owner, other, name="the other", confirm_with=device).status_code == 200
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    lost = held_move(client, owner, device, mac)[0]["held"]
    kept = held_move(client, owner, other, mac, "s2")[0]["held"]
    gone = next(k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                if k["name"] != "the other")
    at[0] += 10
    assert remove(client, owner, gone).status_code == 200
    ended = hold_of(lost["id"])
    assert (ended["done_at"], ended["failed"]) == (at[0], moves.WITHDRAWN) and hold_of(kept["id"])["done_at"] is None
    r = client.post(f"/api/ade/holds/{lost['id']}/cancel", headers=owner)
    assert r.status_code == 409 and r.json()["detail"] == (
        f"It ended at {holds.utc(at[0])}, before its time: it could not take effect (The passkey that signed it was "
        "removed before its link took it).")
    at[0] = lost["takes_effect_at"]
    holds.settle()
    assert [m["session_id"] for m in stored_moves()] == ["s2"]
    assert {v["session"]: (v["state"], v["reason"]) for v in owners(client, owner)} == {
        SESSION: ("failed", moves.WITHDRAWN), "s2": ("asked", None)}


def test_revoking_a_link_ends_the_holds_of_its_moves_saying_why(client, owner, device, workspace, at):
    mac = paired(client, owner)
    mini = paired(client, owner, name="mini")
    lists(mac, (SESSION, False))
    lists(mini, ("s2", False))
    gone = held_move(client, owner, device, mac)[0]["held"]
    kept = held_move(client, owner, device, mini, "s2")[0]["held"]
    at[0] += 10
    assert revoke(client, owner, mac.link_id).status_code == 200
    ended = hold_of(gone["id"])
    assert (ended["done_at"], ended["failed"]) == (at[0], moves.REVOKED) and hold_of(kept["id"])["done_at"] is None
    at[0] = gone["takes_effect_at"]
    holds.settle()
    assert [m["link_id"] for m in stored_moves()] == [mini.link_id]
    assert {v["link_id"]: (v["state"], v["reason"]) for v in owners(client, owner)} == {
        mac.link_id: ("failed", moves.REVOKED), mini.link_id: ("asked", None)}


# ---- what the owner reads -------------------------------------------------------------------------------------------

def test_the_owner_s_list_gives_each_move_held_cancelled_failed_or_stored_newest_first(client, owner, device,
                                                                                       workspace, at):
    other = Authenticator(alg=-8)
    assert add(client, owner, other, name="the other", confirm_with=device).status_code == 200
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False))
    t0 = at[0]
    cancelled = held_move(client, owner, device, mac)[0]["held"]
    client.post(f"/api/ade/holds/{cancelled['id']}/cancel", headers=owner)
    at[0] += 1
    failed, failed_p = held_move(client, owner, other, mac, "s2")
    remove(client, owner, next(k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                                if k["name"] == "the other"))
    at[0] += 1
    held, held_p = held_move(client, owner, device, mac)
    listed = owners(client, owner)
    assert [v["state"] for v in listed] == ["held", "failed", "cancelled"]
    assert listed[0] == {"seq": None, "hold_id": held["held"]["id"], "link_id": mac.link_id, "session": SESSION,
                         "state": "held", "takes_effect_at": t0 + 2 + WAIT, "told": 0, "created_at": t0 + 2,
                         "expires_at": held_p["expires_at"], "reason": None}
    assert (listed[1]["hold_id"], listed[1]["session"], listed[1]["reason"]) == \
        (failed["held"]["id"], "s2", moves.WITHDRAWN)
    assert (listed[2]["hold_id"], listed[2]["reason"], listed[2]["created_at"]) == (cancelled["id"], None, t0)
    assert [v["state"] for v in owners(client, owner, session=SESSION)] == ["held", "cancelled"]
    assert owners(client, owner, link_id="nope") == []
    at[0] = held["held"]["takes_effect_at"]
    # the list's own read settles first: stored, the move's own row stands for its held row
    first = owners(client, owner)
    (row,) = events("move")
    assert [(v["seq"], v["state"]) for v in first] == [(row["seq"], "asked"), (None, "failed"), (None, "cancelled")]
    assert "hold_id" not in first[0]
    stranger = signed_up(client, OTHER)
    assert client.get("/api/ade/link/moves", headers=stranger).json() == {"moves": []}


def test_linked_sessions_says_how_long_a_move_waits_and_shows_each_that_waits(client, owner, device, workspace, at,
                                                                              monkeypatch):
    mac = paired(client, owner)
    lists(mac, (SESSION, False), ("s2", False), ("s3", False))
    got = sessions(client, owner)
    assert (got["move_hold"], got["connect_hold"], got["held_moves"]) == (WAIT, 180, [])
    monkeypatch.setenv(moves.HOLD_SETTING, "45")
    assert sessions(client, owner)["move_hold"] == 45
    monkeypatch.delenv(moves.HOLD_SETTING)
    waiting, p = held_move(client, owner, device, mac)
    ends = held_move(client, owner, device, mac, "s2")[0]["held"]
    cancelled = held_move(client, owner, device, mac, "s3")[0]["held"]
    client.post(f"/api/ade/holds/{cancelled['id']}/cancel", headers=owner)
    lists(mac, (SESSION, False), ("s2", True), ("s3", False))   # s2's move cannot be stored when its hold ends
    (shown, other) = sessions(client, owner)["held_moves"]
    assert shown == {"seq": None, "hold_id": waiting["held"]["id"], "link_id": mac.link_id, "session": SESSION,
                     "state": "held", "takes_effect_at": at[0] + WAIT, "told": 0, "created_at": at[0],
                     "expires_at": p["expires_at"], "reason": None, "title": "Work on s1", "link": "mac"}
    assert other["hold_id"] == ends["id"] and other["state"] == "held"
    at[0] = ends["takes_effect_at"]
    (failed,) = sessions(client, owner)["held_moves"]                   # the stored one shows as a move now
    assert (failed["hold_id"], failed["state"], failed["reason"], failed["title"]) == \
        (ends["id"], "failed", moves.WITHHELD, "Work on s2")            # its title as it was when it was signed
    at[0] += moves.HELD_SHOWN
    assert sessions(client, owner)["held_moves"] == []


def test_the_holds_list_names_the_move_and_where_from(client, owner, device, workspace, at):
    mac = paired(client, owner)
    lists(mac, (SESSION, False))
    hold = held_move(client, owner, device, mac)[0]["held"]
    (listed,) = client.get("/api/ade/holds", headers=owner).json()["holds"]
    assert (listed["id"], listed["kind"], listed["summary"]) == (
        hold["id"], "move", "Moving “Work on s1” from mac into the workspace was approved")


@pytest.fixture
def phone(client, owner, monkeypatch):
    device_, service = Device(), PushService()
    push.subscribe(account(client, owner), FCM, device_.p256dh, push.b64u(device_.auth))
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    return device_, service


def test_the_notice_says_a_move_waits_and_when_and_the_page_what_moves_from_where(client, owner, device, workspace, at,
                                                                                  phone):
    handset, service = phone
    mac = paired(client, owner)
    # the pairing's own notice first (its hold is none here: test_link_pair_hold.py), then only the move's
    assert [handset.decrypt(b)["title"] for _, _, b in service.posts] == ["A session was approved to be linked"]
    service.posts.clear()
    lists(mac, (SESSION, False))
    refused = move_payload(client, owner, mac.link_id, expires_at=at[0] + 400)
    assert send(client, owner, device, refused).status_code == 422 and service.posts == []     # a refusal tells nothing
    got, _ = held_move(client, owner, device, mac)
    (told,) = [handset.decrypt(b) for _, _, b in service.posts]
    assert got["held"]["told"] == 1 and owners(client, owner)[0]["told"] == 1
    # in fixed words: a move waits, and when; the session's title and its machine are the page's (its hold's summary)
    assert (told["title"], told["body"], told["url"]) == (
        "A move waits", f"Takes effect at {holds.utc(at[0] + WAIT)} unless you cancel it.", "/app/#channel")
    assert "Work on s1" not in repr(told) and "mac" not in repr(told)
