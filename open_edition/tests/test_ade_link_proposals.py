"""The owner's inbox, the server's side of a linked session's proposals: while the owner's switch for it is on, a
linked session may propose a message for another linked
session, a sealed link event of kind proposal to the owner, within limits counted on the link's events; it reaches that
session only as the owner's own instruction, which he signs with a passkey, its first line naming who proposed it; and
his close, or its day's deadline, ends it unsigned. Encryption is turned on as the page does it (tests/test_ade_e2e.py's
Keys), texts sealed as a link seals them, and the owner's instructions signed with a passkey of the tests' own software
authenticator."""

import json
import time
import uuid

import pytest

from conftest import signed_up
from proofpane_open import store
from proofpane_open.ade import link, push
from test_ade_e2e import Keys, send_sealed
from test_ade_link import OTHER, paired, post, revoke
from test_ade_link_commands import account, event_row, payload, send
from test_ade_push import FCM, Device, PushService
from test_passkeys import Authenticator, add

MARK = "Zq7mark"
OFF = "The owner has not let this session propose."
SEALED_ONLY = "A proposal goes sealed: its words are for the owner alone."
ANOTHER_SESSION = "This proposal is for another session than the one this instruction names."


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    r = add(client, owner, d)
    assert r.status_code == 200, r.text
    return d


@pytest.fixture
def keys(client, owner, device):
    k = Keys(client, owner, device)
    k.turn_on()
    return k


def switch(client, owner, s, allowed=True, headers=None):
    return client.put(f"/api/ade/link/links/{s.link_id}/proposes", headers=owner if headers is None else headers,
                      json={"allowed": allowed})


def allowed(client, owner, s):
    r = switch(client, owner, s)
    assert r.status_code == 200, r.text


def propose(keys, s, for_name, words="Please rebase onto main, then run the tests."):
    """A proposal as ade_link.py propose posts one: sealed to the owner, its client_id 32 hex; the response, its
    client_id and its text as stored."""
    return send_sealed(keys, s, "proposal", words, **{"for": for_name})


def proposed(keys, s, for_name, words="Please rebase onto main, then run the tests."):
    r, client_id, text = propose(keys, s, for_name, words)
    assert r.status_code == 200, r.text
    return r.json()["seq"], client_id, text


def sign(client, owner, device, to, text, client_id):
    """The owner's instruction for a proposal, as the page builds it: its id the proposal's client_id."""
    return send(client, owner, device, payload(client, owner, "instruction", to, text, id=client_id))


def inbox(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def open_proposals(client, owner):
    return [p["seq"] for p in inbox(client, owner).get("proposals", [])]


def insert_proposal(uid, s, for_id, closed=False, received_at=None):
    """A proposal of `s` stored as if posted: for the limits, which count the link's events."""
    now = int(time.time()) if received_at is None else received_at
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                   "received_at, deadline_at, for_id, closed_reason, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (uid, s.link_id, uuid.uuid4().hex, "proposal", "owner", "e2e1.x", 0, now, now + 86400, for_id,
                    "owner" if closed else None, now if closed else None))


# ---- the owner's switch ----------------------------------------------------------------------------------------------

def test_the_switch_is_the_owner_s_sign_in_alone_and_is_off_for_a_new_link(client, owner):
    mac = paired(client, owner)
    assert [entry.get("proposes") for entry in inbox(client, owner)["links"]] == [False]
    r = switch(client, owner, mac)
    assert r.status_code == 200 and r.json() == {"proposes": True}, r.text
    assert [entry.get("proposes") for entry in inbox(client, owner)["links"]] == [True]
    assert client.put(f"/api/ade/link/links/{mac.link_id}/proposes", json={"allowed": False}).status_code == 401
    other = signed_up(client, OTHER)
    assert switch(client, owner, mac, allowed=False, headers=other).status_code == 404
    assert switch(client, owner, mac, allowed="no").status_code == 422
    # a session's own key turns nothing on: the route takes the owner's sign-in alone
    assert mac.call("PUT", f"/links/{mac.link_id}/proposes", {"allowed": False}).status_code == 401
    assert [entry.get("proposes") for entry in inbox(client, owner)["links"]] == [True]
    r = switch(client, owner, mac, allowed=False)
    assert r.status_code == 200 and r.json() == {"proposes": False}
    gone = paired(client, owner, "gone")
    assert revoke(client, owner, gone.link_id).status_code == 200
    assert switch(client, owner, gone).status_code == 404


# ---- a proposal ------------------------------------------------------------------------------------------------------

def test_a_proposal_waits_for_the_switch_goes_sealed_and_reaches_the_owner_s_list(client, owner, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    r, _, _ = propose(keys, mac, "cloud")
    assert r.status_code == 403 and r.json()["detail"] == OFF, r.text
    allowed(client, owner, mac)
    clear = post(mac, "proposal", "Please rebase onto main.", **{"for": "cloud"})
    assert clear.status_code == 422 and clear.json()["detail"] == SEALED_ONLY
    seq, client_id, text = proposed(keys, mac, "cloud")
    row = event_row(seq)
    assert (row["kind"], row["link_id"], row["to_name"], row["for_id"], row["needs_turn"], row["client_id"],
            row["closed_reason"]) == ("proposal", mac.link_id, "owner", cloud.link_id, 0, client_id, None)
    assert row["deadline_at"] == row["received_at"] + 86400
    (shown,) = inbox(client, owner).get("proposals", [])
    assert (shown["seq"], shown["text"], shown["link_id"], shown["for_id"], shown["for"], shown["from"],
            shown["deadline_at"], shown["client_id"]) == (seq, text, mac.link_id, cloud.link_id, "cloud", "mac",
                                                          row["deadline_at"], client_id)
    # it wakes nobody: the session it names reads nothing of it
    assert cloud.call("GET", "/feed").json()["events"] == []


def test_a_proposal_names_another_live_session_by_its_name(client, owner, keys):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    gone = paired(client, owner, "gone")
    assert revoke(client, owner, gone.link_id).status_code == 200
    allowed(client, owner, mac)
    for over in ({"for": "mac"}, {"for": "nobody"}, {"for": "gone"}, {"for": "Cloud"}, {"for": 7}, {},
                 {"for": "cloud", "to": "cloud"}, {"for": "cloud", "ref": 1}, {"for": "cloud", "deadline": 99}):
        r, _, _ = send_sealed(keys, mac, "proposal", "Please rebase.", **over)
        assert r.status_code == 422, (over, r.text)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'proposal'").fetchone()[0] == 0
    # naming a session it is for is a proposal's alone
    r, _, _ = send_sealed(keys, mac, "note", "Please rebase.", **{"for": "cloud"})
    assert r.status_code == 422, r.text


def test_a_link_s_proposals_are_bounded_open_by_the_hour_and_for_one_session(client, owner, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    third = paired(client, owner, "third")
    uid = account(client, owner)
    for s in (mac, third):
        allowed(client, owner, s)
    seqs = [proposed(keys, mac, "cloud")[0] for _ in range(5)]
    r, _, _ = propose(keys, mac, "cloud")
    assert r.status_code == 429 and "at most 5 proposals open" in r.json()["detail"], r.text
    for seq in seqs:                                      # closed, they still count in the hour
        assert client.post(f"/api/ade/link/questions/{seq}/close", headers=owner).status_code == 200
    for _ in range(14):
        insert_proposal(uid, mac, cloud.link_id, closed=True)
    proposed(keys, mac, "third")                           # the 20th in the hour
    r, _, _ = propose(keys, mac, "third")
    assert r.status_code == 429 and "20 proposals an hour" in r.json()["detail"], r.text
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET received_at = received_at - 3601 WHERE kind = 'proposal'")
    # ten open for one session, from whichever links: the eleventh waits
    for _ in range(9):
        insert_proposal(uid, third, cloud.link_id)
    proposed(keys, mac, "cloud")                           # the tenth for cloud
    r, _, _ = propose(keys, mac, "cloud")
    assert r.status_code == 429 and "10 proposals for it open" in r.json()["detail"], r.text


# ---- the owner signs it ----------------------------------------------------------------------------------------------

def test_the_owner_signs_a_proposal_as_his_own_instruction_to_the_session_it_names(client, owner, device, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    allowed(client, owner, mac)
    seq, client_id, _ = proposed(keys, mac, "cloud")
    words = "Proposed by mac:\nPlease rebase onto main, then run the tests."
    r = sign(client, owner, device, mac.link_id, words, client_id)
    assert r.status_code == 409 and r.json()["detail"] == ANOTHER_SESSION, r.text
    for text in ("Please rebase onto main.", "Proposed by cloud:\nPlease rebase.", "Proposed by mac: Please rebase.",
                 "Proposed by mac:\n", "Proposed by mac:\n  \n\t", "proposed by mac:\nPlease rebase.",
                 "Proposed by mac :\nPlease rebase.", "\nProposed by mac:\nPlease rebase."):
        r = sign(client, owner, device, cloud.link_id, text, client_id)
        assert r.status_code == 409, (text, r.text)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'instruction'").fetchone()[0] == 0
    assert event_row(seq)["closed_reason"] is None
    r = sign(client, owner, device, cloud.link_id, words, client_id)
    assert r.status_code == 200, r.text
    instruction = event_row(r.json()["seq"])
    assert (instruction["kind"], instruction["link_id"], instruction["to_id"], instruction["needs_turn"],
            instruction["client_id"], instruction["prompt_answer"]) == (
        "instruction", "owner", cloud.link_id, 1, client_id, 0)
    assert json.loads(instruction["payload"])["text"] == words
    closed = event_row(seq)
    assert (closed["closed_reason"], closed["closed_by"]) == ("signed", r.json()["seq"])
    assert seq not in open_proposals(client, owner)
    # the session it names takes it as the owner's own, woken for it
    turn = cloud.call("GET", "/feed", query={"turn": 1}).json()["events"]
    assert [(e["seq"], e["kind"]) for e in turn] == [(r.json()["seq"], "instruction")]
    again = sign(client, owner, device, cloud.link_id, words, client_id)
    assert again.status_code == 409


def test_a_proposal_is_signed_as_an_instruction_only(client, owner, device, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    allowed(client, owner, mac)
    seq, client_id, _ = proposed(keys, mac, "cloud")
    q = post(cloud, "question", "Which branch?", to="owner").json()["seq"]
    from proofpane_open.ade import link_commands
    r = send(client, owner, device, payload(client, owner, "answer", cloud.link_id, "Proposed by mac:\nmain",
                                            id=client_id, ref=q,
                                            digest=link_commands.text_digest("Which branch?")))
    assert r.status_code == 409, r.text
    assert event_row(seq)["closed_reason"] is None and event_row(q)["closed_by"] is None


@pytest.mark.parametrize("how", ["owner", "deadline"])
def test_a_closed_or_expired_proposal_cannot_be_signed(client, owner, device, keys, how):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    allowed(client, owner, mac)
    seq, client_id, _ = proposed(keys, mac, "cloud")
    if how == "owner":
        assert client.post(f"/api/ade/link/questions/{seq}/close", headers=owner).status_code == 200
    else:
        with store.connect() as db:
            db.execute("UPDATE ade_link_events SET deadline_at = ? WHERE seq = ?", (int(time.time()) - 1, seq))
        assert seq not in open_proposals(client, owner)
        assert event_row(seq)["closed_reason"] == "deadline"
    r = sign(client, owner, device, cloud.link_id, "Proposed by mac:\nPlease rebase.", client_id)
    assert r.status_code == 409 and "proposal" in r.json()["detail"], r.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'instruction'").fetchone()[0] == 0


def test_the_owner_s_close_of_a_proposal_tells_its_session_and_wakes_nobody(client, owner, keys):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    allowed(client, owner, mac)
    seq, _, _ = proposed(keys, mac, "cloud")
    r = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert r.status_code == 200, r.text
    note = event_row(r.json()["seq"])
    assert (note["link_id"], note["kind"], note["to_name"], note["ref"], note["text"], note["needs_turn"]) == (
        "owner", "note", "mac", seq, link.CLOSED_ON_PAGE, 0)
    row = event_row(seq)
    assert (row["closed_reason"], row["closed_by"]) == ("owner", r.json()["seq"])
    assert seq not in open_proposals(client, owner)
    again = client.post(f"/api/ade/link/questions/{seq}/close", headers=owner)
    assert again.status_code == 200 and again.json()["seq"] == r.json()["seq"]


# ---- what the owner is told ------------------------------------------------------------------------------------------

def test_a_proposal_is_told_in_words_that_name_nothing(client, owner, keys, monkeypatch):
    mac = paired(client, owner, name=f"mac {MARK}")
    paired(client, owner, f"cloud {MARK}", "cloud")
    allowed(client, owner, mac)
    uid = account(client, owner)
    device, service = Device(), PushService()
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    push.subscribe(uid, FCM, device.p256dh, push.b64u(device.auth))
    push._board_cache.clear()
    seq, _, _ = proposed(keys, mac, f"cloud {MARK}", f"Tell {MARK} to rebase.")
    assert dict(link.alerts(uid)).get(f"link-proposal:{seq}") == "link-proposal"
    push.tick(post=service, build=lambda conn: {"alerts": []})
    told = [device.decrypt(b) for _, _, b in service.posts]
    assert [(m["title"], m["body"], m["url"]) for m in told] == [
        ("A proposed message waits for you", "Open Approvals to read it.", "/app/#approvals")]
    assert MARK.lower() not in json.dumps(told).lower()
    assert client.post(f"/api/ade/link/questions/{seq}/close", headers=owner).status_code == 200
    assert f"link-proposal:{seq}" not in dict(link.alerts(uid))
