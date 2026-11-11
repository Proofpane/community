"""A session that asks to be linked (ade/link_asks.py), through the app: the account's ask address, a request signed by
the session's new key, the same answer whether or not an account has the address, the owner's approval with the
confirmation a ticket takes, the hold before the link is made (its notice, its Cancel), the refusals, the limits
counted from the table, and that a session can never approve, refuse or cancel. Requests are signed by the tests' own
signer (test_ade_link.Session), so a mismatch with the client would show."""

import hashlib
import re
import time

import pytest
from fastapi.testclient import TestClient

from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import holds, link, link_asks, push
from test_ade_link import HOST, Session, at_once, b64u, confirmed, paired, raw, revoke, ticket
from test_ade_push import FCM, Device, PushService

OTHER = "other@example.com"
NOWHERE = "ask-" + "a" * 20                     # an address of the right shape that no account has
REF = "https://claude.ai/code/session_01ABC"


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def at(monkeypatch):
    """The server's clock and the signers', which the test moves: [now]."""
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    return t


def address(client, owner):
    r = client.get("/api/ade/link/address", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["address"]


def ask(client, addr, name="cloud", kind="cloud", ref=REF, s=None, **over):
    s = s or Session(client)
    body = dict({"address": addr, "name": name, "kind": kind, "public_key": b64u(raw(s.key)), "session_ref": ref},
                **over)
    return s, s.call("POST", "/ask", body)


def poll(s, **kw):
    with store.connect() as db:                         # the polls a test makes are not held to their pace
        db.execute("UPDATE ade_link_asks SET last_poll_at = NULL")
    return s.call("GET", "/ask", **kw)


def asks(client, owner):
    r = client.get("/api/ade/link/sessions", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["asks"]


def approve(client, owner, ask_id, confirmation=True):
    body = {"confirmation": confirmed(client, owner, f"link-approve:{ask_id}")} if confirmation is True else (
        {"confirmation": confirmation} if confirmation else {})
    return client.post(f"/api/ade/link/asks/{ask_id}/approve", headers=owner, json=body)


def fingerprint(s):
    return hashlib.sha256(raw(s.key)).hexdigest()[:32]


def links():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_links WHERE revoked_at IS NULL ORDER BY created_at")]


def asked_rows():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ade_link_asks ORDER BY created_at, rowid")]


# ---- the address ---------------------------------------------------------------------------------------------------

def test_each_account_has_one_ask_address_made_when_first_read_and_shown_to_its_owner_alone(client, owner):
    first = client.get("/api/ade/link/address", headers=owner).json()
    assert re.fullmatch(r"ask-[a-z2-7]{20}", first["address"]) and first["url"] == "https://open.example.test"
    assert client.get("/api/ade/link/address", headers=owner).json() == first
    other = signed_up(client, OTHER)
    assert address(client, other) != first["address"]
    assert client.get("/api/ade/link/address").status_code == 401
    r = client.post("/api/ade/link/address", headers=owner)
    assert r.status_code == 200 and r.json()["address"] != first["address"] and r.json()["refused"] == 0
    assert address(client, owner) == r.json()["address"]


def test_the_answer_to_an_ask_is_the_same_whether_or_not_an_account_has_the_address(client, owner, at):
    real = address(client, owner)
    mine, got_mine = ask(client, real)
    theirs, got_theirs = ask(client, NOWHERE)
    assert got_mine.status_code == got_theirs.status_code == 200
    a, b = got_mine.json(), got_theirs.json()
    assert a.pop("fingerprint") == fingerprint(mine) and b.pop("fingerprint") == fingerprint(theirs)
    assert a == b == {"status": "waiting", "expires_at": at[0] + link_asks.ASK_TTL, "server_time": at[0],
                      "name": "cloud"}
    at[0] += 600
    a, b = poll(mine).json(), poll(theirs).json()
    a.pop("fingerprint"), b.pop("fingerprint")
    assert a == b and a["status"] == "waiting"
    assert [x["name"] for x in asks(client, owner)] == ["cloud"]          # only the account's own request shows
    at[0] += link_asks.ASK_TTL
    a, b = poll(mine).json(), poll(theirs).json()
    assert a["status"] == b["status"] == "expired"
    assert asks(client, owner) == []


def test_the_limit_on_waiting_requests_reads_the_same_for_an_address_no_account_has(client, owner, at, monkeypatch):
    monkeypatch.setattr(link_asks, "PENDING_PER_NETWORK", 10)       # what this holds is the address's count alone
    for addr in (address(client, owner), NOWHERE):
        for _ in range(link_asks.PENDING_PER_ADDRESS):
            assert ask(client, addr)[1].status_code == 200
        r = ask(client, addr)[1]
        assert r.status_code == 429 and f"has {link_asks.PENDING_PER_ADDRESS} requests waiting" in r.json()["detail"]


# ---- approving, the hold, the link --------------------------------------------------------------------------------

def test_an_approved_request_is_linked_when_its_hold_ends_and_not_before(client, owner, at):
    s, r = ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    assert {k: seen[k] for k in ("name", "kind", "fingerprint", "session_ref", "state", "created_at", "expires_at")} \
        == {"name": "cloud", "kind": "cloud", "fingerprint": fingerprint(s), "session_ref": REF, "state": "pending",
            "created_at": at[0], "expires_at": at[0] + link_asks.ASK_TTL}
    assert seen["asked_from"] is None                      # a test client's address is none the ADE can read
    got = approve(client, owner, seen["id"])
    assert got.status_code == 200, got.text
    hold = got.json()["hold"]
    assert hold["takes_effect_at"] == at[0] + link_asks.HOLD_DEFAULT and hold["kind"] == link_asks.HOLD
    assert got.json()["told"] == 0                         # no device turned notifications on
    (held,) = asks(client, owner)
    assert held["state"] == "held" and held["hold_id"] == hold["id"] and held["takes_effect_at"] == hold["takes_effect_at"]
    assert poll(s).json() == {"fingerprint": fingerprint(s), "server_time": at[0], "status": "approved",
                              "takes_effect_at": hold["takes_effect_at"], "name": "cloud"}
    at[0] = hold["takes_effect_at"] - 1
    assert poll(s).json()["status"] == "approved" and links() == []
    assert asks(client, owner)[0]["state"] == "held"
    at[0] += 1
    # the page's read alone settles the hold: the link is made and shown, before the session polls again
    assert [x["name"] for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]] == ["cloud"]
    done = poll(s).json()
    made = links()
    assert done["status"] == "linked" and [m["id"] for m in made] == [done["link_id"]] and done["name"] == "cloud"
    assert (made[0]["name"], made[0]["kind"], made[0]["public_key"], made[0]["session_ref"]) == (
        "cloud", "cloud", b64u(raw(s.key)), REF)
    assert done["owner_keys"] == link.owner_keys_of(made[0]["user_id"])
    assert asks(client, owner) == []                       # a link now, on the page as one
    assert [x["name"] for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]] == ["cloud"]
    at[0] += 61
    assert s.call("POST", "/heartbeat", {}).status_code == 200          # the session's own key is the link's
    assert poll(s).json()["status"] == "linked"                        # asked again, the same answer


def test_the_page_is_told_how_long_an_approval_waits_and_the_setting_decides_it(client, owner, monkeypatch, at):
    assert client.get("/api/ade/link/sessions", headers=owner).json()["connect_hold"] == 180
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECT_HOLD", "45")
    assert client.get("/api/ade/link/sessions", headers=owner).json()["connect_hold"] == 45
    s, _ = ask(client, address(client, owner))
    assert approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]["takes_effect_at"] == at[0] + 45


def test_approving_takes_the_confirmation_a_ticket_takes_and_uses_it_once(client, owner):
    s, _ = ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    r = approve(client, owner, seen["id"], confirmation=None)
    assert r.status_code == 403 and r.json()["detail"] == link.LINK_NEEDS_CONFIRM
    assert asks(client, owner)[0]["state"] == "pending"
    c = confirmed(client, owner, f"link-approve:{seen['id']}")
    assert approve(client, owner, seen["id"], confirmation=c).status_code == 200
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    with store.connect() as db, pytest.raises(passkeys.PasskeyError, match="expired or was used"):
        passkeys._use_confirmation(db, user_id, c, int(time.time()), subject=f"link-approve:{seen['id']}")


def test_a_rule_on_which_confirmations_count_covers_approving_as_it_covers_a_ticket(client, owner, monkeypatch):
    """Approving goes through link._use_confirmation, as a ticket does: a later rule there (a hardware key only, say)
    holds for both."""
    s, _ = ask(client, address(client, owner))
    (seen,) = asks(client, owner)

    def stricter(db, user_id, confirmation, now, subject):
        raise link.LinkError(403, "Only a hardware key confirms that.")

    monkeypatch.setattr(link, "_use_confirmation", stricter)
    for r in (approve(client, owner, seen["id"]),
              client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": confirmed(client, owner)})):
        assert r.status_code == 403 and r.json()["detail"] == "Only a hardware key confirms that."
    assert asks(client, owner)[0]["state"] == "pending"


def test_an_approval_s_confirmation_is_made_for_that_request_and_approves_no_other(client, owner):
    """Approving takes a confirmation made for that request; one made for another request, for a ticket, or for
    nothing in particular approves nothing and is left unused, and the request waits on."""
    first, _ = ask(client, address(client, owner), name="one", kind="local")
    second, _ = ask(client, address(client, owner), name="two", kind="local")
    by_name = {a["name"]: a for a in asks(client, owner)}
    one, two = by_name["one"]["id"], by_name["two"]["id"]
    for_two = confirmed(client, owner, f"link-approve:{two}")
    for given in (for_two, confirmed(client, owner, "link-ticket:new")):
        r = approve(client, owner, one, confirmation=given)
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    plain = client.post("/api/account/confirm", headers=owner, json={"password": "correct horse battery"}).json()
    r = approve(client, owner, one, confirmation=plain["confirmation"])
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FOR_NOTHING
    assert {a["state"] for a in asks(client, owner)} == {"pending"}
    assert approve(client, owner, two, confirmation=for_two).status_code == 200      # left unused, it still serves


def test_a_refused_approval_leaves_its_confirmation_unused(client, owner):
    s, _ = ask(client, address(client, owner), name="mac", kind="local")
    paired(client, owner, "mac")                       # a live link has the name now
    (seen,) = asks(client, owner)
    c = confirmed(client, owner)
    r = approve(client, owner, seen["id"], confirmation=c)
    assert r.status_code == 409 and "already has a live link named mac" in r.json()["detail"]
    assert client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": c}).status_code == 200


def test_the_owner_cancels_before_the_hold_ends_and_no_link_is_ever_made(client, owner, at):
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    at[0] = hold["takes_effect_at"] - 1
    r = client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner)
    assert r.status_code == 200 and r.json()["cancelled_at"] == at[0]
    assert poll(s).json()["status"] == "cancelled" and asks(client, owner) == []
    at[0] += 3600
    assert poll(s).json()["status"] == "cancelled" and links() == []
    assert client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner).json() == r.json()   # as the first


@pytest.mark.parametrize("outcome", ["linked", "failed", "cancelled"])
def test_a_request_is_kept_a_day_after_its_last_change_so_its_session_still_reads_the_outcome(client, owner, at,
                                                                                              monkeypatch, outcome):
    """A hold as long as a day ends a request more than a day after it was made: it is kept a day from that end, so its
    session's next poll still reads the outcome, and it goes a day after."""
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECT_HOLD", str(holds.MOST_DELAY))
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    if outcome == "failed":
        paired(client, owner, "cloud", kind="cloud")            # a ticket takes the name while the hold runs
    at[0] = hold["takes_effect_at"] - (1 if outcome == "cancelled" else 0)
    if outcome == "cancelled":
        assert client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner).status_code == 200
    else:
        client.get("/api/ade/link/sessions", headers=owner)     # the page's read ends the hold
    at[0] += 60
    assert ask(client, NOWHERE)[1].status_code == 200           # another ask, which deletes what was kept its day
    assert poll(s).json()["status"] == outcome
    at[0] += link_asks.KEEP
    assert ask(client, "ask-" + "e" * 20)[1].status_code == 200
    gone = poll(s)
    assert gone.status_code == 401 and "No request was made with this key" in gone.json()["detail"]


def test_signing_out_everywhere_cancels_an_approval_still_held(client, owner, at):
    """A lost device's steps (the manual) cancel what was approved with it: the request is cancelled, never linked."""
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    r = client.post("/api/account/sign-out-everywhere", headers=owner,
                    json={"confirmation": confirmed(client, owner, "sign-out-everywhere")})
    assert r.status_code == 200, r.text
    assert poll(s).json()["status"] == "cancelled"
    at[0] = hold["takes_effect_at"] + 60
    assert poll(s).json()["status"] == "cancelled" and links() == []


def test_a_hold_whose_time_has_come_has_made_its_link_and_is_past_cancelling(client, owner, at):
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    at[0] = hold["takes_effect_at"]
    r = client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner)
    assert r.status_code == 409 and "it has taken effect" in r.json()["detail"]
    assert len(links()) == 1 and poll(s).json()["status"] == "linked"


def test_nothing_a_session_sends_moves_a_hold(client, owner, at):
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    at[0] = hold["takes_effect_at"] - 10
    # a poll dated as late as the ADE accepts is read at the ADE's own time
    early = poll(s, ts=at[0] + link.SKEW - 1)
    assert early.status_code == 200 and early.json()["status"] == "approved" and links() == []
    # asking again changes nothing: in other words it is refused, in the same words answered as it stands
    again = ask(client, "ask-" + "b" * 20, s=s)[1]
    assert again.status_code == 409 and links() == []
    assert ask(client, asked_rows()[0]["address"], s=s)[1].json()["status"] == "approved"
    with store.connect() as db:
        assert holds.get(db, hold["id"])["takes_effect_at"] == hold["takes_effect_at"]


def signed_for(s, method, path, body=b""):
    """A session's signature over this very request, to any route of the app, as it signs one to the channel: the
    deployment's host, the method, the raw path and query, the time and the body's sha256."""
    ts = int(time.time())
    message = "\n".join((HOST, method, path, "", str(ts), hashlib.sha256(body).hexdigest())).encode()
    return f"ADE-Link key_id={hashlib.sha256(raw(s.key)).hexdigest()[:16]}, ts={ts}, sig={b64u(s.key.sign(message))}"


def test_a_session_can_never_approve_refuse_or_cancel_a_request_its_own_or_another_s(client, owner, at):
    other_one, _ = ask(client, address(client, owner), name="first")
    s, _ = ask(client, address(client, owner), name="second")
    first, second = asks(client, owner)
    hold = approve(client, owner, first["id"]).json()["hold"]
    linked = paired(client, owner, "mac")
    for signer in (s, other_one, linked):
        for method, path in (("POST", f"/asks/{second['id']}/approve"), ("POST", f"/asks/{second['id']}/refuse"),
                             ("POST", f"/asks/{first['id']}/refuse"), ("POST", "/address")):
            r = signer.call(method, path, {})
            assert r.status_code == 401, (method, path)
        # the holds' routes, outside the channel: a session's signature, made for that very request, is no sign-in there
        for method, path in (("POST", f"/api/ade/holds/{hold['id']}/cancel"), ("GET", "/api/ade/holds")):
            r = client.request(method, path, headers={"Authorization": signed_for(signer, method, path)})
            assert r.status_code == 401, (method, path)
    assert [x["state"] for x in asks(client, owner)] == ["held", "pending"]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["cancelled_at"] is None


def test_the_owner_refuses_and_the_session_is_told_so(client, owner):
    s, _ = ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    assert client.post(f"/api/ade/link/asks/{seen['id']}/refuse", headers=owner).json() == {"refused": seen["id"]}
    assert poll(s).json()["status"] == "refused" and asks(client, owner) == []
    r = client.post(f"/api/ade/link/asks/{seen['id']}/refuse", headers=owner)
    assert r.status_code == 409 and r.json()["state"] == "refused"
    assert approve(client, owner, seen["id"]).status_code == 409


def test_another_account_finds_no_request_of_this_one(client, owner):
    ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    other = signed_up(client, OTHER)
    assert asks(client, other) == []
    assert client.post(f"/api/ade/link/asks/{seen['id']}/approve", headers=other,
                       json={"confirmation": confirmed(client, other)}).status_code == 404
    assert client.post(f"/api/ade/link/asks/{seen['id']}/refuse", headers=other).status_code == 404
    assert asks(client, owner)[0]["state"] == "pending"


def test_a_request_expires_after_thirty_minutes_and_cannot_be_approved_then(client, owner, at):
    s, _ = ask(client, address(client, owner))
    (seen,) = asks(client, owner)
    at[0] += link_asks.ASK_TTL - 1
    assert poll(s).json()["status"] == "waiting" and len(asks(client, owner)) == 1
    at[0] += 1
    assert poll(s).json()["status"] == "expired" and asks(client, owner) == []
    r = approve(client, owner, seen["id"])
    assert r.status_code == 409 and r.json()["state"] == "expired"


def test_a_name_a_live_link_takes_during_the_hold_leaves_the_request_failed_with_why(client, owner, at):
    s, _ = ask(client, address(client, owner), name="mac", kind="local")
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    mac = paired(client, owner, "mac")                 # through a ticket, while the hold runs
    at[0] = hold["takes_effect_at"]
    got = poll(s).json()
    assert got["status"] == "failed" and "already has a live link named mac" in got["detail"]
    assert [x["id"] for x in links()] == [mac.link_id]
    (shown,) = asks(client, owner)
    assert shown["state"] == "failed" and "named mac" in shown["failed"]
    at[0] += link_asks.FAILED_SHOWN + 1
    assert asks(client, owner) == []


# ---- what an ask must be ---------------------------------------------------------------------------------------------

def test_an_ask_is_held_to_what_a_pair_is(client, owner):
    real = address(client, owner)
    for over, status, words in (
            ({"address": "ask-" + "a" * 19}, 422, "not an ask address"),
            ({"address": "ask-" + "a" * 19 + chr(0x212A)}, 422, "not an ask address"),       # a Kelvin sign for a k
            ({"address": None}, 422, "not an ask address"),
            ({"name": "owner"}, 422, "not names a link can take"),
            ({"name": "bad/name"}, 422, "A link's name is"),
            ({"name": "my  mac"}, 422, "no two spaces together"),
            ({"name": "my mac "}, 422, "no space at its end"),
            ({"kind": "server"}, 422, "cloud or local"),
            ({"kind": ["cloud"]}, 422, "cloud or local"),                  # not a string: refused, not an error
            ({"kind": {"cloud": 1}}, 422, "cloud or local"),
            ({"session_ref": "sk-ant-api03-xyz"}, 422, "shaped like an Anthropic key"),
            ({"public_key": "short"}, 422, "32 bytes")):
        r = ask(client, real, **over)[1]
        assert r.status_code == status and words in r.json()["detail"], (over, r.text)
    s = Session(client)
    stranger = Session(client)
    r = s.call("POST", "/ask", {"address": real, "name": "x", "kind": "cloud", "public_key": b64u(raw(stranger.key))})
    assert r.status_code == 401 and "not signed with the key it asks with" in r.json()["detail"]
    r = s.call("POST", "/ask", {"address": real, "name": "x", "kind": "cloud", "public_key": b64u(raw(s.key))},
               sign_body=b"{}")
    assert r.status_code == 401 and "does not check" in r.json()["detail"]
    assert ask(client, real.upper())[1].status_code == 200            # as typed, in either case
    assert asked_rows()[0]["address"] == real
    assert len(asked_rows()) == 1


def test_the_same_ask_again_is_answered_as_it_stands_and_a_key_on_a_live_link_asks_nothing(client, owner, at):
    real = address(client, owner)                       # the clock held: each answer carries the server's time
    s, first = ask(client, real)
    assert ask(client, real, s=s)[1].json() == first.json() and len(asked_rows()) == 1
    r = ask(client, real, s=s, name="another")[1]
    assert r.status_code == 409 and "has asked already" in r.json()["detail"]
    mac = paired(client, owner, "mac")
    r = ask(client, real, s=mac, name="mac2")[1]
    assert r.status_code == 409 and "already on a live link" in r.json()["detail"]


def test_a_session_that_lost_the_answer_that_it_was_linked_asks_again_and_is_answered_linked(client, owner, at):
    """Its client stopped between the hold's end and its next poll (a command timeout shorter than the hold): asking
    again with the same key, address, name, kind and reference is answered as the request stands. Anything else asked
    with a key on a live link is refused, as before, and so is the old request once its link is not the key's."""
    real = address(client, owner)
    s, _ = ask(client, real)
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    at[0] = hold["takes_effect_at"]
    (made,) = client.get("/api/ade/link/sessions", headers=owner).json()["links"]     # the page's read makes it
    again = ask(client, real, s=s)[1]
    assert again.status_code == 200, again.text
    got = again.json()
    assert (got["status"], got["link_id"], got["name"]) == ("linked", made["id"], "cloud")
    assert got["owner_keys"] == link.owner_keys_of(links()[0]["user_id"]) and len(asked_rows()) == 1
    for other in ({"name": "other"}, {"kind": "local"}, {"ref": REF + "2"}, {"addr": "ask-" + "b" * 20}):
        r = ask(client, other.pop("addr", real), s=s, **other)[1]
        assert r.status_code == 409 and "already on a live link" in r.json()["detail"], other
    assert revoke(client, owner, made["id"]).status_code == 200
    with at_once():                                     # linked as it pairs (test_link_pair_hold.py: the wait)
        paired_again = Session(client, key=s.key).pair(ticket(client, owner), name="cloud", kind="cloud")
    assert paired_again.status_code == 200 and paired_again.json()["status"] == "linked"
    r = ask(client, real, s=s)[1]                       # the key's link now is one a ticket made
    assert r.status_code == 409 and "already on a live link" in r.json()["detail"]


def test_a_poll_is_signed_by_the_asking_key_and_comes_at_most_every_two_seconds(client, owner):
    s, _ = ask(client, address(client, owner))
    assert Session(client).call("GET", "/ask").status_code == 401                # a key that never asked
    assert client.get("/api/ade/link/ask").status_code == 401                   # no signature
    # the asking key's id, with a signature that does not check: over another host, or made too long ago
    forged = poll(s, host="alias.example")
    assert forged.status_code == 401 and "does not check" in forged.json()["detail"]
    stale = poll(s, ts=int(time.time()) - link.SKEW - 5)
    assert stale.status_code == 401 and "server_time" in stale.json()
    assert poll(s).status_code == 200
    r = s.call("GET", "/ask")
    assert r.status_code == 429 and "every 2 seconds" in r.json()["detail"]


# ---- limits ---------------------------------------------------------------------------------------------------------

def test_one_address_has_a_few_requests_waiting_and_an_answer_or_the_end_of_one_makes_room(client, owner, at):
    real = address(client, owner)
    for _ in range(link_asks.PENDING_PER_ADDRESS):
        assert ask(client, real)[1].status_code == 200
    assert ask(client, real)[1].status_code == 429
    first = asks(client, owner)[0]
    client.post(f"/api/ade/link/asks/{first['id']}/refuse", headers=owner)
    assert ask(client, real)[1].status_code == 200
    assert ask(client, real)[1].status_code == 429
    at[0] += link_asks.ASK_TTL
    assert ask(client, real)[1].status_code == 200                         # the others expired


def test_one_network_asks_so_many_times_an_hour_and_another_is_not_held_up(owner, at):
    from proofpane_open.main import app
    here = TestClient(app, client=("203.0.113.7", 50000))
    same_net = TestClient(app, client=("203.0.113.7", 50001))
    elsewhere = TestClient(app, client=("198.51.100.2", 50000))
    for i in range(link_asks.ASKS_PER_NETWORK):
        if i == link_asks.PENDING_PER_NETWORK:
            at[0] += link_asks.ASK_TTL          # those waiting expire: the hour's count stays
        r = ask(here, "ask-" + "b" * 19 + "abcdefghijklmnop"[i])[1]
        assert r.status_code == 200, r.text
    r = ask(same_net, "ask-" + "c" * 20)[1]
    assert r.status_code == 429 and "This network has asked" in r.json()["detail"]
    assert ask(elsewhere, "ask-" + "c" * 20)[1].status_code == 200
    assert {r["asked_from"] for r in asked_rows()} == {"203.0.113.7", "198.51.100.2"}
    at[0] += 3601
    assert ask(here, "ask-" + "d" * 20)[1].status_code == 200


def test_one_network_has_so_many_requests_waiting_whatever_their_addresses(client, owner, at):
    """Counted by the network, to any address: the refusal reads the same for an address no account has as for one an
    account has, so it shows nothing of which addresses exist."""
    from proofpane_open.main import app
    here = TestClient(app, client=("203.0.113.7", 50000))
    real = address(client, owner)
    for addr in [real] + ["ask-" + "b" * 19 + "abcd"[i] for i in range(link_asks.PENDING_PER_NETWORK - 1)]:
        assert ask(here, addr)[1].status_code == 200
    said = {ask(here, addr)[1].json()["detail"] for addr in (real, NOWHERE)}
    assert said == {f"This network has {link_asks.PENDING_PER_NETWORK} requests waiting, as many as it may: one must "
                    "be approved, refused or expire first."}
    assert ask(TestClient(app, client=("198.51.100.2", 50000)), NOWHERE)[1].status_code == 200
    client.post(f"/api/ade/link/asks/{asks(client, owner)[0]['id']}/refuse", headers=owner)    # an answer makes room
    assert ask(here, NOWHERE)[1].status_code == 200


def test_a_wider_network_has_so_many_requests_waiting_and_asks_so_many_times_an_hour(owner, at, monkeypatch):
    """An IPv4 /24 or an IPv6 /48, which one holder of many addresses usually has, is held as one wider network, apart
    from the networks in it."""
    from proofpane_open.main import app
    monkeypatch.setattr(link_asks, "PENDING_PER_WIDE_NETWORK", 3)
    monkeypatch.setattr(link_asks, "ASKS_PER_WIDE_NETWORK", 5)

    def from_(host, addr=NOWHERE):
        return ask(TestClient(app, client=(host, 50000)), addr)[1]

    for i, host in enumerate(("203.0.113.1", "203.0.113.2", "203.0.113.250")):         # three networks in one /24
        assert from_(host, "ask-" + "b" * 19 + "abc"[i]).status_code == 200
    r = from_("203.0.113.9")
    assert r.status_code == 429 and r.json()["detail"] == (
        "The wider network around this address (its /24, or its /48) has 3 requests waiting, as many as it may: one "
        "must be approved, refused or expire first.")
    assert from_("203.0.114.9").status_code == 200                                        # the next /24 is another
    at[0] += link_asks.ASK_TTL                  # those expire: the waiting count frees, the hour's does not
    for i, host in enumerate(("203.0.113.3", "203.0.113.4")):
        assert from_(host, "ask-" + "c" * 19 + "ab"[i]).status_code == 200
    r = from_("203.0.113.5")
    assert r.status_code == 429 and r.json()["detail"] == (
        "The wider network around this address (its /24, or its /48) has asked 5 times in the last hour, as many times "
        "as it may.")
    for i, host in enumerate(("2001:db8:1:1::1", "2001:db8:1:2::1", "2001:db8:1:3::1")):   # three /64s in one /48
        assert from_(host, "ask-" + "d" * 19 + "abc"[i]).status_code == 200
    assert from_("2001:db8:1:4::1").status_code == 429 and from_("2001:db8:2:1::1").status_code == 200


def test_the_deployment_has_so_many_requests_waiting_at_once(client, owner, monkeypatch):
    monkeypatch.setattr(link_asks, "ASKS_PENDING", 2)
    assert ask(client, "ask-" + "e" * 20)[1].status_code == 200
    assert ask(client, "ask-" + "f" * 20)[1].status_code == 200
    r = ask(client, "ask-" + "g" * 20)[1]
    assert r.status_code == 429 and "Too many sessions" in r.json()["detail"]


def test_replacing_the_address_refuses_what_waits_on_the_old_one_and_the_old_one_is_no_account_s(client, owner, at):
    old = address(client, owner)
    s, _ = ask(client, old)
    r = client.post("/api/ade/link/address", headers=owner).json()
    assert r["refused"] == 1 and poll(s).json()["status"] == "refused"
    late, got = ask(client, old)
    assert got.status_code == 200 and got.json()["status"] == "waiting" and asks(client, owner) == []
    at[0] += link_asks.ASK_TTL
    assert poll(late).json()["status"] == "expired"
    assert ask(client, r["address"])[1].status_code == 200 and len(asks(client, owner)) == 1


# ---- what the owner's devices are told -------------------------------------------------------------------------------

@pytest.fixture
def phone(client, owner, monkeypatch):
    device, service = Device(), PushService()
    push.subscribe(client.get("/api/auth/me", headers=owner).json()["id"], FCM, device.p256dh, push.b64u(device.auth))
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    return device, service


def test_the_approval_is_told_at_once_to_the_owner_s_devices_with_when_the_link_is_made(client, owner, phone):
    device, service = phone
    real = address(client, owner)
    from proofpane_open.main import app
    s, _ = ask(TestClient(app, client=("203.0.113.9", 4000)), real, name="mac", kind="local")
    (seen,) = asks(client, owner)
    assert approve(client, owner, seen["id"], confirmation=None).status_code == 403
    assert service.posts == []                                         # a refused approval tells nothing
    got = approve(client, owner, seen["id"]).json()
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    assert got["told"] == 1 and asks(client, owner)[0]["told"] == 1
    # in fixed words, when it takes effect: never its name, key or address, which the page shows
    assert told == {"title": "A link waits to be made", "url": "/app/#channel",
                    "body": f"Takes effect at {holds.utc(got['hold']['takes_effect_at'])} unless you cancel it.",
                    "tag": push.words("held-link", key="hold:" + got["hold"]["id"], at=0).tag}
    assert not any(w in repr(told) for w in ("mac", "203.0.113.9", fingerprint(s)))


def test_with_no_hold_the_approval_s_notice_says_it_is_in_effect(client, owner, phone, monkeypatch):
    device, service = phone
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECT_HOLD", "0")
    s, _ = ask(client, address(client, owner))
    got = approve(client, owner, asks(client, owner)[0]["id"]).json()
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    assert (told["title"], told["body"]) == (
        "A link was approved", f"Approved at {holds.utc(got['hold']['takes_effect_at'])}, to take effect at once: this "
                               "deployment holds no approval.")


def test_a_waiting_request_reaches_the_owner_s_devices_once_through_the_notifier(client, owner, phone, at):
    device, service = phone
    push._board_cache.clear()
    s, _ = ask(client, address(client, owner))
    push.tick(now=at[0], build=lambda conn: {"alerts": []})
    push.tick(now=at[0] + 60, build=lambda conn: {"alerts": []})
    (told,) = [device.decrypt(b) for _, _, b in service.posts]
    # in fixed words: never what it calls itself, where it asked from or its key, which the page shows
    assert (told["title"], told["body"], told["url"]) == ("A session asks to be linked",
                                                          "Open Linked sessions to see it.", "/app/#channel")
    assert "cloud" not in repr(told) and fingerprint(s) not in repr(told)


def test_the_notifier_s_round_makes_the_link_of_a_hold_that_has_ended_with_nothing_reading(client, owner, phone, at):
    s, _ = ask(client, address(client, owner))
    hold = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    push.tick(now=hold["takes_effect_at"] - 1, build=lambda conn: {"alerts": []})
    assert links() == []
    push.tick(now=hold["takes_effect_at"], build=lambda conn: {"alerts": []})
    assert [x["name"] for x in links()] == ["cloud"]


# ---- tickets, as they were ------------------------------------------------------------------------------------------

def test_a_ticket_still_pairs_beside_asking(client, owner):
    ask(client, address(client, owner))
    s = Session(client)
    with at_once():                                     # linked as it pairs (test_link_pair_hold.py: the wait)
        r = s.pair(ticket(client, owner), name="mac")
    assert r.status_code == 200 and r.json()["owner_keys"] == link.owner_keys_of(links()[0]["user_id"])
    assert [x["name"] for x in links()] == ["mac"] and asks(client, owner)[0]["state"] == "pending"
