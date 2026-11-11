"""Pairing a session waits: a session that presents a ticket, or a live link moved
to a new key by one, is linked when a hold ends, 180 seconds after it paired (OPEN_EDITION_ADE_CONNECT_HOLD), 900 while
the account's high security is on (OPEN_EDITION_ADE_CONNECT_HOLD_HIGH), as an approved request to be linked is. The
pairing uses its ticket up and is a request approved as it is made; its notice goes at once, in fixed words; the page
lists it with Cancel, which takes the sign-in alone, and a lost device's steps cancel it. Until the hold ends the new
key is no link's: every link route refuses it but the poll, and nothing of encryption is served to it. When the hold
ends it is checked again and made, or fails saying why. A client from an older release, which does not say it waits,
is refused and its ticket left unused."""

import pytest

from conftest import signed_up
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import store
from proofpane_open.ade import holds, link, link_asks, link_routes, push, summary
from test_ade_link import Session, at_once, confirmed, post, raw, revoke, row, ticket
from test_ade_link_asks import address, approve, ask
from test_ade_push import FCM, Device, PushService
from test_high_security import keys, on, word  # noqa: F401  (keys: a fixture)
from test_passkeys import Authenticator, add, remove, sign_out_everywhere

MARK = "Zq7mark"
FROM = "203.0.113.7"


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def account(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


@pytest.fixture
def phone(account, monkeypatch):
    """The account's one device with notifications on, and the stand-in push service its notices reach, at once."""
    device, service = Device(), PushService()
    push.subscribe(account, FCM, device.p256dh, push.b64u(device.auth))
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    return device, service


def made(client, owner, link_id=None, confirmation=None):
    """A ticket's whole answer, as the page reads it: for a new link, or for re-pairing `link_id`."""
    body = {"confirmation": confirmation or confirmed(client, owner)}
    if link_id:
        body["link_id"] = link_id
    r = client.post("/api/ade/link/tickets", headers=owner, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def view(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def poll(s):
    """Where the session's pairing stands, as its client reads it (past the poll interval)."""
    with store.connect() as db:
        db.execute("UPDATE ade_link_asks SET last_poll_at = NULL")
    r = s.call("GET", "/ask")
    assert r.status_code == 200, r.text
    return r.json()


def held(client, owner):
    """The pairing the page lists, the one request of the account."""
    (one,) = view(client, owner)["asks"]
    return one


# ---- it waits ---------------------------------------------------------------------------------------------------------

def test_a_pairing_waits_three_minutes_and_its_key_is_no_link_s_until_then(client, owner, account, monkeypatch):
    monkeypatch.setattr(link_routes, "_client", lambda request: FROM)
    t = made(client, owner)
    assert t["pair_hold"] == link_asks.HOLD_DEFAULT == 180
    s = Session(client)
    r = s.pair(t["ticket"], name="mac", kind="local", ref="local-1")
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["status"], got["takes_effect_at"] - got["server_time"]) == ("approved", 180)
    assert got["fingerprint"] == link_asks.fingerprint(raw(s.key)) and "link_id" not in got and "owner_keys" not in got
    assert view(client, owner)["links"] == []                                    # no link yet
    assert Session(client).pair(t["ticket"], name="other").status_code == 403   # its ticket is used up
    # the key is no link's: every link route refuses it but the poll, and encryption serves it nothing
    for method, path, body in (("POST", "/heartbeat", {}), ("GET", "/feed", None), ("GET", "/state", None),
                               ("GET", "/resume", None), ("GET", "/owner-keys", None), ("GET", "/e2e", None),
                               ("PUT", "/checkpoint", {"expected_version": 0, "document": {}})):
        assert s.call(method, path, body).status_code == 401, path
    assert post(s, "step", "hello").status_code == 401
    # its name, and its ticket by the first half of the sha256 the ADE keeps of it (a pair run again knows its own
    # pairing by it); the server's time left out, which moves on between two polls
    assert {k: v for k, v in poll(s).items() if k != "server_time"} == {
        "status": "approved", "takes_effect_at": got["takes_effect_at"], "fingerprint": got["fingerprint"],
        "name": "mac", "ticket": link._ticket_hash(t["ticket"])[:32]}
    # the page lists it with its ticket, its key and where it came from, and Cancel (its hold)
    shown = held(client, owner)
    assert (shown["via"], shown["ticket_id"], shown["state"], shown["name"], shown["kind"]) == (
        "ticket", t["ticket_id"], "held", "mac", "local")
    assert (shown["fingerprint"], shown["asked_from"], shown["session_ref"]) == (got["fingerprint"], FROM, "local-1")
    assert shown["takes_effect_at"] == got["takes_effect_at"] and shown["hold_id"] and shown["replaces"] is None
    (hold,) = client.get("/api/ade/holds", headers=owner).json()["holds"]
    assert (hold["kind"], hold["summary"]) == (link_asks.HOLD, "Linking “mac” (local) by a ticket")
    # nothing before its time; at its time, the link, and the key is the link's
    assert holds.settle(account, now=got["takes_effect_at"] - 1) == []
    assert view(client, owner)["links"] == [] and s.call("POST", "/heartbeat", {}).status_code == 401
    assert holds.settle(account, now=got["takes_effect_at"]) == [hold["id"]]
    (made_link,) = view(client, owner)["links"]
    linked = poll(s)
    assert (linked["status"], linked["link_id"], linked["name"]) == ("linked", made_link["id"], "mac")
    assert linked["owner_keys"] == link.owner_keys_of(account)
    stored = row(made_link["id"])
    assert (stored["name"], stored["kind"], stored["session_ref"], stored["key_id"]) == (
        "mac", "local", "local-1", link.key_id(raw(s.key)))
    assert made_link["fingerprint"] == got["fingerprint"]           # the page tells it is this pairing's link
    assert s.call("POST", "/heartbeat", {}).status_code == 200
    assert view(client, owner)["asks"] == []                        # made: no longer a request


def test_while_high_security_is_on_a_pairing_and_an_approval_wait_fifteen_minutes(client, owner, account, keys):
    assert made(client, owner)["pair_hold"] == 180 and view(client, owner)["connect_hold"] == 180
    on(client, owner, keys)
    t = made(client, owner, confirmation=word(client, owner, keys["a"], "link-ticket:new"))
    assert t["pair_hold"] == link_asks.HOLD_HIGH_DEFAULT == 900 and view(client, owner)["connect_hold"] == 900
    got = Session(client).pair(t["ticket"], name="mac").json()
    assert got["takes_effect_at"] - got["server_time"] == 900
    # an approved request waits the same, so linking has one rule however it starts
    s, r = ask(client, address(client, owner), name="cloud")
    assert r.status_code == 200, r.text
    asked = [a for a in view(client, owner)["asks"] if a["state"] == "pending"][0]
    r = approve(client, owner, asked["id"], word(client, owner, keys["a"], f"link-approve:{asked['id']}"))
    assert r.status_code == 200, r.text
    assert r.json()["hold"]["takes_effect_at"] - r.json()["hold"]["approved_at"] == 900
    kinds = {h["kind"] for h in client.get("/api/ade/holds", headers=owner).json()["holds"]}
    assert kinds == {link_asks.HOLD_HIGH}


def test_each_tier_s_setting_sets_its_length_and_none_links_as_it_pairs(client, owner, account, keys, monkeypatch):
    for value, length in (("45", 45), ("86400", 86400), ("86401", 180), ("x", 180), ("-1", 180), ("4.5", 180)):
        monkeypatch.setenv(link_asks.HOLD_SETTING, value)
        assert made(client, owner)["pair_hold"] == length, value
    monkeypatch.setenv(link_asks.HOLD_SETTING, "45")
    got = Session(client).pair(ticket(client, owner), name="mac").json()
    assert got["takes_effect_at"] - got["server_time"] == 45
    # none: linked as it pairs, the answer the poll gives a linked one
    monkeypatch.setenv(link_asks.HOLD_SETTING, "0")
    s = Session(client)
    now = s.pair(ticket(client, owner), name="cloud", kind="cloud").json()
    assert (now["status"], now["name"], now["owner_keys"]) == ("linked", "cloud", link.owner_keys_of(account))
    assert row(now["link_id"])["name"] == "cloud" and s.call("POST", "/heartbeat", {}).status_code == 200
    # the high tier reads its own setting, and the standard one's does not move it
    on(client, owner, keys)
    monkeypatch.setenv(link_asks.HOLD_HIGH_SETTING, "100")
    t = made(client, owner, confirmation=word(client, owner, keys["a"], "link-ticket:new"))
    assert t["pair_hold"] == 100
    got = Session(client).pair(t["ticket"], name="desk").json()
    assert got["takes_effect_at"] - got["server_time"] == 100


# ---- the notice -------------------------------------------------------------------------------------------------------

def test_the_notice_says_a_session_waits_and_when_and_names_nothing_of_it(client, owner, phone, monkeypatch):
    device, service = phone
    got = Session(client).pair(ticket(client, owner), name=f"mac {MARK}"[:20].strip(), kind="local").json()
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    assert (told["title"], told["body"], told["url"]) == (
        "A session waits to be linked",
        f"Linked at {holds.utc(got['takes_effect_at'])} unless you cancel it in Linked sessions.", "/app/#channel")
    assert MARK.lower() not in repr(told).lower()
    assert held(client, owner)["told"] == 1                          # the page says it was sent toward one device
    # a pairing that moves a link to a new key says that; one held for no time, that it was linked
    mac = paired_now(client, owner, "desk")
    service.posts.clear()
    again = Session(client).pair(ticket(client, owner, mac.link_id), name="desk").json()
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    assert (told["title"], told["body"]) == (
        "A linked machine waits to move to a new key",
        f"Moved at {holds.utc(again['takes_effect_at'])} unless you cancel it in Linked sessions.")
    service.posts.clear()
    monkeypatch.setenv(link_asks.HOLD_SETTING, "0")
    Session(client).pair(ticket(client, owner), name="cloud", kind="cloud")
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    assert told["title"] == "A session was approved to be linked" and told["body"].startswith("Approved at ")


def paired_now(client, owner, name="mac", kind="local"):
    s = Session(client)
    with at_once():
        r = s.pair(ticket(client, owner), name=name, kind=kind)
    assert r.json()["status"] == "linked", r.text
    s.link_id = r.json()["link_id"]
    return s


# ---- cancelled --------------------------------------------------------------------------------------------------------

def test_a_ticket_whose_pairing_waits_is_refused_naming_that_pairing_s_key(client, owner, account):
    """Someone who saw a ticket paired first: whoever presents it after is told a pairing of it waits, with that
    pairing's key's fingerprint, to compare with the one its session printed. Once that pairing has
    ended, the one refusal for a used ticket."""
    t = made(client, owner)
    first = Session(client).pair(t["ticket"], name="mac").json()
    r = Session(client).pair(t["ticket"], name="mac")
    assert r.status_code == 403
    assert r.json()["detail"] == ("The ticket was used already, by a session whose pairing waits, with the key whose "
                                  f"fingerprint is {first['fingerprint']}: if that is not this session's, cancel it in "
                                  "Linked sessions.")
    assert client.post(f"/api/ade/holds/{held(client, owner)['hold_id']}/cancel", headers=owner).status_code == 200
    r = Session(client).pair(t["ticket"], name="mac")
    assert (r.status_code, r.json()["detail"]) == (403, link.TICKET_USED)


def test_cancel_takes_the_sign_in_alone_and_nothing_is_linked(client, owner, account):
    t = ticket(client, owner)
    s = Session(client)
    got = s.pair(t, name="mac").json()
    r = client.post(f"/api/ade/holds/{held(client, owner)['hold_id']}/cancel", headers=owner)
    assert r.status_code == 200 and r.json()["cancelled_at"], r.text
    assert poll(s)["status"] == "cancelled"
    assert holds.settle(account, now=got["takes_effect_at"]) == [] and view(client, owner)["links"] == []
    assert Session(client).pair(t, name="mac").status_code == 403          # its ticket stays used
    assert view(client, owner)["asks"] == []                                # nor is it listed


@pytest.mark.parametrize("step", ["remove a passkey", "sign out everywhere"])
def test_a_lost_device_s_steps_cancel_a_pairing_that_waits(client, owner, account, step):
    laptop = Authenticator(-7)
    assert add(client, owner, laptop, name="Laptop").status_code == 200
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()
    if step == "remove a passkey":
        (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
        r = remove(client, owner, listed["id"])
    else:
        r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    assert poll(s)["status"] == "cancelled"
    assert holds.settle(account, now=got["takes_effect_at"]) == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_links").fetchone()[0] == 0


def test_turning_high_security_on_cancels_a_pairing_that_waits(client, owner, account, keys):
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()
    on(client, owner, keys)
    assert poll(s)["status"] == "cancelled"
    assert holds.settle(account, now=got["takes_effect_at"]) == []


def test_while_high_security_is_on_a_lost_device_s_steps_cancel_its_longer_pairing_too(client, owner, account, keys):
    on(client, owner, keys)
    s = Session(client)
    confirmation = word(client, owner, keys["a"], "link-ticket:new")
    got = s.pair(made(client, owner, confirmation=confirmation)["ticket"], name="mac").json()
    (hold,) = client.get("/api/ade/holds", headers=owner).json()["holds"]
    assert hold["kind"] == link_asks.HOLD_HIGH
    r = client.post("/api/account/sign-out-everywhere", headers=owner,
                    json={"confirmation": word(client, owner, keys["a"], "sign-out-everywhere")})
    assert r.status_code == 200, r.text
    assert poll(s)["status"] == "cancelled"
    assert holds.settle(account, now=got["takes_effect_at"]) == []


# ---- its end ----------------------------------------------------------------------------------------------------------

def failed(client, owner, s, why):
    shown = held(client, owner)
    answer = poll(s)
    assert (shown["state"], shown["failed"], answer["status"], answer["detail"]) == ("failed", why, "failed", why)


def test_its_end_finds_the_name_taken_meanwhile_and_fails_saying_so(client, owner, account):
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()
    paired_now(client, owner, "mac")                       # another session took the name while it waited
    holds.settle(account, now=got["takes_effect_at"])
    (one,) = [a for a in view(client, owner)["asks"] if a["state"] == "failed"]
    assert one["failed"] == "The account already has a live link named mac."
    answer = poll(s)
    assert (answer["status"], answer["detail"]) == ("failed", "The account already has a live link named mac.")


def test_once_its_link_is_revoked_its_key_reads_revoked_and_nothing_of_the_account(client, owner, account):
    s = Session(client)
    t = made(client, owner)
    got = s.pair(t["ticket"], name="mac").json()
    holds.settle(account, now=got["takes_effect_at"])
    linked = poll(s)
    assert linked["status"] == "linked" and linked["owner_keys"]["account"] == account
    assert revoke(client, owner, linked["link_id"]).status_code == 200
    assert {k: v for k, v in poll(s).items() if k != "server_time"} == {
        "status": "revoked", "fingerprint": got["fingerprint"], "name": "mac",
        "ticket": link._ticket_hash(t["ticket"])[:32]}


def test_once_its_link_has_moved_to_another_key_its_key_reads_revoked(client, owner, account):
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()
    holds.settle(account, now=got["takes_effect_at"])
    link_id = poll(s)["link_id"]
    moved = Session(client)                                 # Link again with a new key: the link moves to it
    again = moved.pair(ticket(client, owner, link_id), name="mac").json()
    holds.settle(account, now=again["takes_effect_at"])
    assert poll(moved)["status"] == "linked" and poll(moved)["link_id"] == link_id
    answer = poll(s)
    assert answer["status"] == "revoked" and "link_id" not in answer and "owner_keys" not in answer


def test_the_page_is_told_how_a_ticket_s_pairing_ended_for_ten_minutes_and_no_longer(client, owner, account):
    s, t = Session(client), made(client, owner)
    got = s.pair(t["ticket"], name="mac").json()
    assert view(client, owner)["ended_pairings"] == []            # it waits: listed among the requests instead
    holds.settle(account, now=got["takes_effect_at"])
    linked = {"ticket_id": t["ticket_id"], "state": "linked"}
    assert view(client, owner)["ended_pairings"] == [linked]
    at = got["takes_effect_at"]
    assert link_asks.ended_pairings(account, now=at + link_asks.ENDED_SHOWN - 1) == [linked]
    assert link_asks.ended_pairings(account, now=at + link_asks.ENDED_SHOWN) == []
    # cancelled: told so; failed: listed with why, as before, and not among them
    other, t2 = Session(client), made(client, owner)
    other.pair(t2["ticket"], name="studio")
    (waiting,) = [a for a in view(client, owner)["asks"] if a["ticket_id"] == t2["ticket_id"]]
    assert client.post(f"/api/ade/holds/{waiting['hold_id']}/cancel", headers=owner).status_code == 200
    assert {"ticket_id": t2["ticket_id"], "state": "cancelled"} in view(client, owner)["ended_pairings"]
    third, t3 = Session(client), made(client, owner)
    got3 = third.pair(t3["ticket"], name="taken").json()
    paired_now(client, owner, "taken")
    holds.settle(account, now=got3["takes_effect_at"])
    shown = view(client, owner)
    assert [a["state"] for a in shown["asks"] if a["ticket_id"] == t3["ticket_id"]] == ["failed"]
    assert all(e["ticket_id"] != t3["ticket_id"] for e in shown["ended_pairings"])


def test_an_approval_begun_as_its_request_expired_waits_for_the_ticket_s_pairing_of_the_same_key(client, owner, account,
                                                                                                monkeypatch):
    """One request at a time for a key, on the approval's side too: the key's request expires, the key pairs by a ticket
    that second, and an approval begun a second before it takes the write lock is refused, not held beside it."""
    import time
    s, r = ask(client, address(client, owner))
    assert r.status_code == 200, r.text
    (asked,) = view(client, owner)["asks"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: asked["expires_at"])
    assert s.pair(ticket(client, owner), name="mac").json()["status"] == "approved"
    monkeypatch.setattr(time, "time", lambda: asked["expires_at"] - 1)
    r = approve(client, owner, asked["id"])
    assert r.status_code == 409 and r.json()["detail"] == ("This request's key waits to be linked by another request "
                                                           "already: wait for that to end.")
    monkeypatch.setattr(time, "time", real)
    # held: the pairing alone; the request was never approved
    assert [(a["state"], a["via"]) for a in view(client, owner)["asks"]] == [("pending", None), ("held", "ticket")]


def test_a_pairing_and_an_approval_take_their_time_once_they_hold_the_write_lock(client, owner, account, monkeypatch):
    """A wait for the write lock moves neither what a pairing checks nor when its hold ends: its time is read again
    once the lock is held. So a ticket that expired while its pairing waited for the lock is refused."""
    import time
    t = made(client, owner)
    real, calls = time.time, []

    def late():                         # the second reading of the clock, after the lock, is past the ticket's end
        calls.append(1)
        return real() if len(calls) == 1 else t["expires_at"]

    monkeypatch.setattr(link, "_now", lambda now: now if now is not None else int(late()))
    r = Session(client).pair(t["ticket"], name="mac")
    assert r.status_code == 403 and len(calls) >= 2


def test_the_high_security_wait_is_never_shorter_than_the_other(client, owner, account, keys, monkeypatch):
    monkeypatch.setenv(link_asks.HOLD_SETTING, "600")
    monkeypatch.setenv(link_asks.HOLD_HIGH_SETTING, "0")           # set below the other: the other's is taken
    assert holds.delay(link_asks.HOLD_HIGH) == 600
    on(client, owner, keys)
    assert link_asks.pair_hold(account) == 600
    monkeypatch.setenv(link_asks.HOLD_HIGH_SETTING, "1200")
    assert holds.delay(link_asks.HOLD_HIGH) == 1200 and holds.delay(link_asks.HOLD) == 600


def test_two_pairings_racing_for_one_ticket_make_one_pairing(client, owner, account, monkeypatch):
    """Two keys present one ticket at once, each past the ticket's first read and its signature before either takes
    the write lock: the ticket is read again under the lock, so one pairs and the other is refused."""
    import hashlib
    import json
    import threading
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_ade_link import HOST, b64u
    t = ticket(client, owner)
    real, gate = link._verify, threading.Barrier(2, timeout=10)

    def both_past_the_first_read(*args, **kwargs):
        real(*args, **kwargs)
        gate.wait()

    monkeypatch.setattr(link, "_verify", both_past_the_first_read)
    answers = []

    def pairs(name):
        key, now = Ed25519PrivateKey.generate(), int(time.time())
        body = json.dumps({"ticket": t, "name": name, "kind": "local", "public_key": b64u(raw(key)),
                           "session_ref": None, "waits": True}).encode()
        message = "\n".join((HOST, "POST", "/api/ade/link/pair", "", str(now),
                             hashlib.sha256(body).hexdigest())).encode()
        header = (f"ADE-Link key_id={hashlib.sha256(raw(key)).hexdigest()[:16]}, ts={now}, "
                  f"sig={b64u(key.sign(message))}")
        try:
            answers.append(link.pair(header, "POST", "/api/ade/link/pair", "", body, now=now)["status"])
        except link.LinkError as e:
            answers.append((e.status, str(e)))

    threads = [threading.Thread(target=pairs, args=(name,)) for name in ("one", "two")]
    for x in threads:
        x.start()
    for x in threads:
        x.join(30)
    (refused,) = [a for a in answers if a != "approved"]
    assert answers.count("approved") == 1 and refused[0] == 403
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_asks WHERE via = ?", (link_asks.TICKET,)).fetchone()[0] == 1
        won = db.execute("SELECT public_key FROM ade_link_asks WHERE via = ?", (link_asks.TICKET,)).fetchone()
    # refused under the lock, it is told of the pairing that won, as at the first read
    assert link_asks.fingerprint(link.unb64u(won["public_key"])) in refused[1]


def test_the_title_bar_counts_a_ticket_s_pairing_that_waits_and_no_approval_that_waits(client, owner, account):
    def alerts():
        r = client.get("/api/ade/summary", headers=owner)
        assert r.status_code == 200, r.text
        return r.json()["links"]["alerts"]

    s, r = ask(client, address(client, owner))
    assert r.status_code == 200 and alerts() == 1                   # a request waiting for the owner's answer
    (asked,) = view(client, owner)["asks"]
    assert approve(client, owner, asked["id"]).status_code == 200
    assert alerts() == 0                                            # answered: its hold needs no one
    assert Session(client).pair(ticket(client, owner), name="mac").json()["status"] == "approved"
    assert alerts() == 1                                            # a ticket's pairing: the owner has not seen it


def test_its_end_finds_the_key_on_a_live_link_and_fails_saying_so(client, owner, account):
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()
    with store.connect() as db:                            # a key no route lets a second request take, put there
        link.make_link(db, account, "other", "local", link.b64u(raw(s.key)), None, got["server_time"])
    holds.settle(account, now=got["takes_effect_at"])
    failed(client, owner, s, "This key is already on another live link.")


def test_its_end_finds_the_ticket_s_passkey_removed_and_fails_saying_so(client, owner, account):
    """A removal cancels a pairing that waits (above); one whose hold came due as the passkey went, settled after it,
    is checked again: the passkey that confirmed its ticket is no longer the account's."""
    laptop = Authenticator(-7)
    assert add(client, owner, laptop, name="Laptop").status_code == 200
    s = Session(client)
    t = made(client, owner, confirmation=word(client, owner, laptop, "link-ticket:new"))
    got = s.pair(t["ticket"], name="mac").json()
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    with store.connect() as db:                            # the passkey gone without its cancel, as in that race
        db.execute("DELETE FROM passkeys WHERE id = ?", (listed["id"],))
    holds.settle(account, now=got["takes_effect_at"])
    failed(client, owner, s, "The passkey that confirmed its ticket was removed.")


def test_a_password_s_ticket_names_no_passkey_to_check_at_its_end(client, owner, account):
    laptop = Authenticator(-7)
    assert add(client, owner, laptop, name="Laptop").status_code == 200
    s = Session(client)
    got = s.pair(ticket(client, owner), name="mac").json()             # the password confirmed it
    with store.connect() as db:
        db.execute("DELETE FROM passkeys")
    holds.settle(account, now=got["takes_effect_at"])
    assert poll(s)["status"] == "linked"


# ---- a re-pair --------------------------------------------------------------------------------------------------------

def test_a_re_pair_moves_the_link_to_the_new_key_when_its_hold_ends(client, owner, account):
    mac = paired_now(client, owner)
    assert mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "x"}}).status_code == 200
    fresh = Session(client)
    got = fresh.pair(ticket(client, owner, mac.link_id), name="ignored", kind="cloud", ref="new-ref").json()
    assert got["status"] == "approved"
    shown = held(client, owner)
    assert (shown["name"], shown["kind"], shown["replaces"]["id"]) == ("mac", "local", mac.link_id)
    assert shown["replaces"]["fingerprint"] == link_asks.fingerprint(raw(mac.key))       # the key it moves from
    (hold,) = client.get("/api/ade/holds", headers=owner).json()["holds"]
    assert hold["summary"] == "Moving the link “mac” to a new key, by a ticket"
    # until its hold ends the old key works and the new one does not
    assert mac.call("GET", "/state").status_code == 200
    assert fresh.call("GET", "/state").status_code == 401
    holds.settle(account, now=got["takes_effect_at"])
    assert mac.call("GET", "/state").status_code == 401
    assert fresh.call("GET", "/state").status_code == 200
    after = row(mac.link_id)
    assert (after["name"], after["kind"], after["session_ref"], after["checkpoint_version"]) == (
        "mac", "local", "new-ref", 1)
    linked = poll(fresh)
    assert (linked["status"], linked["link_id"], linked["name"]) == ("linked", mac.link_id, "mac")
    (moved,) = view(client, owner)["links"]
    assert moved["fingerprint"] == got["fingerprint"]


def test_a_re_pair_whose_link_was_revoked_meanwhile_fails_saying_so(client, owner, account):
    mac = paired_now(client, owner)
    fresh = Session(client)
    got = fresh.pair(ticket(client, owner, mac.link_id), name="mac").json()
    assert revoke(client, owner, mac.link_id).status_code == 200
    holds.settle(account, now=got["takes_effect_at"])
    failed(client, owner, fresh, "The link its ticket re-pairs was revoked.")
    assert fresh.call("POST", "/heartbeat", {}).status_code == 401


def test_a_re_pair_finds_its_new_key_on_another_link_at_its_end_and_fails(client, owner, account):
    mac = paired_now(client, owner)
    fresh = Session(client)
    got = fresh.pair(ticket(client, owner, mac.link_id), name="mac").json()
    with store.connect() as db:
        link.make_link(db, account, "other", "local", link.b64u(raw(fresh.key)), None, got["server_time"])
    holds.settle(account, now=got["takes_effect_at"])
    failed(client, owner, fresh, "This key is already on another live link.")
    assert mac.call("GET", "/state").status_code == 200                 # the link keeps its key


# ---- what it is refused ------------------------------------------------------------------------------------------------

def test_a_client_from_before_is_refused_and_its_ticket_left_unused(client, owner):
    t = ticket(client, owner)
    for waits in (None, False, "true", 1):
        r = Session(client).pair(t, name="mac", waits=waits)
        assert (r.status_code, r.json()["detail"]) == (409, link.PAIR_WAITS), waits
    assert Session(client).pair(t, name="mac").json()["status"] == "approved"


@pytest.mark.parametrize("open_by", ["a pairing", "a request waiting for its answer", "an approved request"])
def test_a_key_that_waits_already_pairs_no_more_and_the_second_ticket_stays_unused(client, owner, open_by):
    """One open request for a key at a time: two held for one key would race at their ends, and its poll, which reads
    the key's newest, could say the one that lost while the other linked it."""
    s = Session(client)
    if open_by == "a pairing":
        assert s.pair(ticket(client, owner), name="mac").json()["status"] == "approved"
    else:
        assert ask(client, address(client, owner), name="mac", kind="local", s=s)[1].status_code == 200
        if open_by == "an approved request":
            (one,) = view(client, owner)["asks"]
            assert approve(client, owner, one["id"]).status_code == 200
    second = ticket(client, owner)
    r = s.pair(second, name="mac 2")
    assert (r.status_code, r.json()["detail"]) == (409, "This key waits to be linked already: wait for that to end.")
    assert Session(client).pair(second, name="mac 2").json()["status"] == "approved"


def test_the_name_must_be_free_as_it_pairs_and_the_ticket_stays_unused_when_it_is_not(client, owner):
    paired_now(client, owner, "mac")
    t = ticket(client, owner)
    r = Session(client).pair(t, name="MAC")
    assert (r.status_code, r.json()["detail"]) == (409, "The account already has a live link named mac.")
    assert Session(client).pair(t, name="cloud", kind="cloud").json()["status"] == "approved"


def test_a_ticket_s_pairing_counts_toward_no_limit_of_an_ask_address(client, owner, monkeypatch):
    monkeypatch.setattr(link_routes, "_client", lambda request: FROM)
    monkeypatch.setattr(link_asks, "ASKS_PER_NETWORK", 1)
    assert Session(client).pair(ticket(client, owner), name="mac").json()["status"] == "approved"
    _, r = ask(client, address(client, owner), name="cloud")
    assert r.status_code == 200, r.text                      # its network's first ask, whatever it paired by ticket
    _, r = ask(client, address(client, owner), name="cloud 2")
    assert r.status_code == 429                              # and the limit still counts the asks


# ---- the page's reads ----------------------------------------------------------------------------------------------

def test_the_title_bar_counts_a_pairing_that_waits_and_the_server_s_time_moves_no_stamp(client, owner, monkeypatch):
    def read():
        r = client.get("/api/ade/summary", headers=owner)
        assert r.status_code == 200, r.text
        return r.json()
    before = read()["links"]["alerts"]
    Session(client).pair(ticket(client, owner), name="mac")
    first = read()
    assert first["links"]["alerts"] == before + 1
    later = int(summary.time.time()) + 7                    # the same list, read seven seconds on
    monkeypatch.setattr(summary.time, "time", lambda: later)
    assert view(client, owner)["server_time"] == later
    assert read()["stamps"]["links"] == first["stamps"]["links"]
