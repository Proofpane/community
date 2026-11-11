"""The relay (proofpane_open/ade/signers.py), through the app: the page parks a step it composed, checked as the route
that will take it checks one before a signature; a signer reads the waiting ones with what the ADE says beside each,
hands back an assertion checked over the stored bytes and kept with nothing committed; the page collects it and sends
it to the route it would have used, which stores it and keeps who signed it. A stolen sign-in parks at most 3 from one
network, 10 in all; a request lives as long as its nonce; Cancel deletes it with its assertion."""

import json
import time

import pytest
from fastapi.testclient import TestClient

import ade_link
from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import link, link_commands, signers
from proofpane_core.canonical import canonicalize
from test_ade_link import OTHER, b64u, paired
from test_ade_link_commands import assertion, device, event_row, owner, payload  # noqa: F401  fixtures
from test_ade_signers import MARK, paired_signer, signer_row
from test_passkeys import Authenticator, add


@pytest.fixture(autouse=True)
def unhurried(monkeypatch):
    monkeypatch.setattr(signers, "POLL_EVERY", 0)


def park(client, owner, p, **extra):
    return client.post("/api/ade/sign-requests", headers=owner, json=dict({"payload": p}, **extra))


def parked(client, owner, p, **extra):
    r = park(client, owner, p, **extra)
    assert r.status_code == 200, r.text
    return r.json()


def request_row(request_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM ade_sign_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row is not None else None


def counter(credential_id):
    with store.connect() as db:
        return db.execute("SELECT sign_count FROM passkeys WHERE credential_id = ?",
                          (credential_id,)).fetchone()["sign_count"]


# ---- the whole way ------------------------------------------------------------------------------------------------

def test_a_parked_step_is_signed_by_a_signer_and_sent_on_by_the_page_to_its_own_route(client, owner):
    device = Authenticator(counter=0)
    assert add(client, owner, device).status_code == 200
    s = paired(client, owner, "mac")
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id, text="Run the tests in open_edition.")
    got = parked(client, owner, p)
    raw = canonicalize(p)
    assert set(got) == {"id", "code", "expires_at"}
    assert got["code"] == signers.code_of(raw) == ade_link.sign_code(raw)          # the code the signer computes
    waiting = signer.call("GET", "/requests")
    assert waiting.status_code == 200 and waiting.headers["cache-control"] == "no-store"
    [r] = waiting.json()["requests"]
    assert r["payload"] == raw.decode() and r["code"] == got["code"] and r["purpose"] == "instruction"
    assert r["allow"] == [b64u(device.credential_id)]
    assert r["context"]["links"] == {s.link_id: {"name": "mac", "stopped_at": None}}
    assert r["context"]["high_security"] is False and r["parked_from"] == "testclient"
    credential = assertion(device, p, **MARK)
    handed = signer.call("POST", f"/requests/{got['id']}", {"credential": credential})
    assert handed.status_code == 200 and handed.json() == {"state": "signed"}
    collected = client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()
    assert collected == {"id": got["id"], "state": "signed", "expires_at": got["expires_at"],
                         "payload": raw.decode(), "credential": credential, "signer": "MacBook"}
    # the page checks the payload is the one it parked, then sends it on to the route it would have used
    assert json.loads(collected["payload"]) == p
    sent = client.post("/api/ade/link/commands", headers=owner, json={"payload": p,
                                                                      "credential": collected["credential"]})
    assert sent.status_code == 200, sent.text
    row = event_row(sent.json()["seq"])
    with store.connect() as db:
        assert signers.through(db, row["user_id"], p["id"], row["signature"]) == "MacBook"
        # another assertion over the same payload (a browser's) came through no signer, nor one over another payload
        assert signers.through(db, row["user_id"], p["id"], json.dumps(assertion(device, p))) is None
        assert signers.through(db, row["user_id"], "0" * 32, row["signature"]) is None
        assert signers.through(db, row["user_id"], p["id"], "not json") is None
    assert signers.marked(row["signature"]) is True
    taken = s.call("POST", "/take", {"seq": sent.json()["seq"], "instance": "i1"})
    assert taken.status_code == 200, taken.text


def test_the_page_s_views_say_what_signed_each_step_and_a_session_s_feed_does_not(client, owner, device):
    s = paired(client, owner, "mac")
    signer = paired_signer(client, owner)
    with store.connect() as db:
        passkey_id = db.execute("SELECT id FROM passkeys WHERE credential_id = ?",
                                (b64u(device.credential_id),)).fetchone()["id"]
    # one step signed through the signer, marked, and one in a browser, neither
    p = payload(client, owner, "instruction", s.link_id, text="Through the Mac.")
    got = parked(client, owner, p)
    assert signer.call("POST", f"/requests/{got['id']}",
                       {"credential": assertion(device, p, **MARK)}).status_code == 200
    credential = client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()["credential"]
    sent = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": credential})
    q = payload(client, owner, "instruction", s.link_id, text="In the browser.")
    plain = client.post("/api/ade/link/commands", headers=owner, json={"payload": q, "credential": assertion(device, q)})
    by_mac = {"passkey_id": passkey_id, "marked": True, "relayed_by": "MacBook"}
    by_browser = {"passkey_id": passkey_id, "marked": False, "relayed_by": None}
    expected = {sent.json()["seq"]: by_mac, plain.json()["seq"]: by_browser}

    def facts(view):
        return {k: view[k] for k in ("passkey_id", "marked", "relayed_by")}

    def card():
        [one] = client.get("/api/ade/link/sessions", headers=owner).json()["links"]
        return one

    assert {c["seq"]: facts(c) for c in card()["commands"]} == expected
    assert card()["resume_signed"] is None
    events = client.get(f"/api/ade/link/timeline/{s.link_id}", headers=owner).json()["events"]
    assert {e["seq"]: facts(e) for e in events if e["from"] == link.OWNER} == expected
    # the session reads its commands without them: what signed a step is for the page to say, not the server's word
    # in front of a session, which checks the signature itself
    fed = s.call("GET", "/feed").json()["events"]
    assert {e["seq"] for e in fed} >= set(expected)
    assert not any({"passkey_id", "marked", "relayed_by"} & set(e) for e in fed)
    # a resume the signer composed, marked; and once the signer is revoked, it is still named for what it relayed
    assert client.post(f"/api/ade/link/links/{s.link_id}/stop", headers=owner).status_code == 200
    with store.connect() as db:
        stopped = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (s.link_id,)).fetchone()[0]
    while int(time.time()) <= stopped:
        time.sleep(0.05)
    r = signer.call("POST", "/nonce", {"purpose": "resume"})
    resume = payload(client, owner, "resume", s.link_id, nonce=r.json()["nonce"])
    assert signer.call("POST", f"/links/{s.link_id}/resume",
                       {"payload": resume, "credential": assertion(device, resume, **MARK)}).status_code == 200
    assert card()["resume_signed"] == by_mac
    assert client.delete(f"/api/ade/signers/{signer.id}", headers=owner).status_code == 200
    assert card()["resume_signed"] == by_mac
    assert {c["seq"]: facts(c) for c in card()["commands"]} == expected


def test_a_step_is_named_relayed_only_by_the_assertion_stored_and_only_the_account_s_own_record(client, owner, device):
    s = paired(client, owner, "mac")
    signer = paired_signer(client, owner)
    # the page parks a step and a signer hands back its assertion; the page sends the browser's own instead: the step
    # stored was not signed through the signer, whatever the relay holds for the same payload
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    assert signer.call("POST", f"/requests/{got['id']}",
                       {"credential": assertion(device, p, **MARK)}).status_code == 200
    browser = assertion(device, p)
    seq = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": browser}).json()["seq"]

    def stored():
        [card] = client.get("/api/ade/link/sessions", headers=owner).json()["links"]
        return next(c for c in card["commands"] if c["seq"] == seq)

    assert (stored()["relayed_by"], stored()["marked"]) == (None, False)
    # another account's record of that very payload and signature, as no route could make it: never this one's word
    other = signed_up(client, OTHER)
    theirs = paired_signer(client, other, name="Theirs")
    with store.connect() as db:
        db.execute("INSERT INTO ade_signed_through (user_id, payload_id, signature, signer_id, created_at) VALUES "
                   "(?,?,?,?,?)", (signer_row(theirs.id)["user_id"], p["id"], browser["response"]["signature"],
                                   theirs.id, int(time.time())))
    assert stored()["relayed_by"] is None


def test_the_relay_commits_nothing_the_route_commits(client, owner):
    device = Authenticator(counter=0)
    assert add(client, owner, device).status_code == 200
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    before = counter(b64u(device.credential_id))
    assert signer.call("POST", f"/requests/{got['id']}", {"credential": assertion(device, p)}).status_code == 200
    challenge, _ = passkeys.open_nonce(p["nonce"], p["account"], "instruction", int(time.time()))
    with store.connect() as db:
        assert passkeys.nonce_used(db, challenge, int(time.time())) is False        # no nonce used
    assert counter(b64u(device.credential_id)) == before                             # no counter moved
    credential = client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()["credential"]
    assert client.post("/api/ade/link/commands", headers=owner,
                       json={"payload": p, "credential": credential}).status_code == 200
    with store.connect() as db:
        assert passkeys.nonce_used(db, challenge, int(time.time())) is True
    assert counter(b64u(device.credential_id)) > before


def test_a_signed_resume_parked_and_collected_lifts_a_stop(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    assert client.post(f"/api/ade/link/links/{s.link_id}/stop", headers=owner).status_code == 200
    with store.connect() as db:
        stopped = db.execute("SELECT stopped_at FROM ade_links WHERE id = ?", (s.link_id,)).fetchone()[0]
    while int(time.time()) <= stopped:
        time.sleep(0.05)
    p = payload(client, owner, "resume", s.link_id)
    got = parked(client, owner, p)
    assert signer.call("POST", f"/requests/{got['id']}",
                       {"credential": assertion(device, p, **MARK)}).status_code == 200
    credential = client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()["credential"]
    r = client.post(f"/api/ade/link/links/{s.link_id}/resume", headers=owner,
                    json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text


# ---- what is parked -------------------------------------------------------------------------------------------------

def test_a_step_the_route_would_refuse_is_never_parked(client, owner, device):
    s = paired(client, owner)

    def p(**over):
        return dict(payload(client, owner, "instruction", s.link_id), **over)

    for over, status, words in (
            ({"deployment": "other.example.test"}, 422, "another deployment"),
            ({"account": "0" * 32}, 403, "another account"),
            ({"to": "mac"}, 422, "32 lowercase hex"),
            ({"text": "do n" + chr(0x0336) + "ot push"}, 422, "U+0336"),
            ({"text": "a key sk-ant-123"}, 422, "shaped like"),
            ({"created_at": int(time.time()) - 400}, 422, "5 minutes"),
            ({"expires_at": int(time.time()) + 10}, 422, "expires"),
            ({"v": 1}, 422, "v is 2"),
            ({"extra": 1}, 422, "exactly the fields")):
        r = park(client, owner, p(**over))
        assert r.status_code == status and words in r.json()["detail"], (over, r.text)
    for bad in ({"purpose": "confirm"}, {"purpose": None}):
        r = park(client, owner, dict(p(), **bad))
        assert r.status_code == 422, (bad, r.text)
    assert park(client, owner, "x").status_code == 422
    assert client.post("/api/ade/sign-requests", headers=owner, json=[]).status_code == 422
    # its nonce: this server's, for this account and purpose, not used
    other = signed_up(client, OTHER)
    theirs = payload(client, other, "instruction", s.link_id)
    r = park(client, owner, dict(p(), nonce=theirs["nonce"]))
    assert r.status_code == 400
    wrong_purpose = payload(client, owner, "resume", s.link_id)
    r = park(client, owner, dict(p(), nonce=wrong_purpose["nonce"]))
    assert r.status_code == 400
    used = p()
    assert client.post("/api/ade/link/commands", headers=owner,
                       json={"payload": used, "credential": assertion(device, used)}).status_code == 200
    again = dict(used, id="f" * 32)
    r = park(client, owner, again)
    assert r.status_code == 409 and "used already" in r.json()["detail"]
    late = p()
    late["created_at"] -= 60
    r = park(client, owner, late)
    assert r.status_code == 422 and "the time its nonce was given" in r.json()["detail"]
    r = park(client, owner, p(text="x" * (link_commands.PAYLOAD_MAX + 1)))
    assert r.status_code == 422 and f"at most {link_commands.PAYLOAD_MAX} bytes" in r.json()["detail"]
    r = park(client, owner, p(), title="a title")
    assert r.status_code == 422 and "Only a move" in r.json()["detail"]
    assert client.get("/api/ade/sign-requests", headers=owner).json()["requests"] == []     # none was parked


def test_the_same_step_parked_again_is_the_same_request(client, owner):
    s = paired(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    first = parked(client, owner, p)
    assert parked(client, owner, p) == first
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_sign_requests").fetchone()[0] == 1


def test_at_most_three_wait_from_one_network_and_ten_in_all(client, owner):
    s = paired(client, owner)

    def one(address):
        return park(TestClient(client.app, client=(address, 50000)), owner,
                    payload(client, owner, "instruction", s.link_id))

    for _ in range(signers.WAITING_PER_NETWORK):
        assert one("203.0.113.7").status_code == 200
    r = one("203.0.113.7")
    assert r.status_code == 429 and "one network" in r.json()["detail"]
    # an IPv6 address counts by its /64: another address in it is the same network
    for i in range(signers.WAITING_PER_NETWORK):
        assert one(f"2001:db8:0:1::{i + 1}").status_code == 200
    assert one("2001:db8:0:1::ff").status_code == 429
    assert one("2001:db8:0:2::1").status_code == 200                 # another /64, another network
    for address in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        assert one(address).status_code == 200
    r = one("192.0.2.9")
    assert r.status_code == 429 and f"At most {signers.WAITING_PER_ACCOUNT}" in r.json()["detail"]
    # cancelling makes room; the address is what the page lists each was parked from
    listed = client.get("/api/ade/sign-requests", headers=owner).json()["requests"]
    assert len(listed) == signers.WAITING_PER_ACCOUNT
    assert listed[0]["parked_from"] == "203.0.113.7"
    assert client.delete(f"/api/ade/sign-requests/{listed[0]['id']}", headers=owner).status_code == 200
    assert one("192.0.2.9").status_code == 200


def test_the_page_lists_what_waits_never_a_code_and_cancels_it_all(client, owner):
    s = paired(client, owner)
    a = parked(client, owner, payload(client, owner, "instruction", s.link_id))
    parked(client, owner, payload(client, owner, "resume", s.link_id))
    listed = client.get("/api/ade/sign-requests", headers=owner)
    assert listed.headers["cache-control"] == "no-store"
    rows = listed.json()["requests"]
    assert [r["purpose"] for r in rows] == ["instruction", "resume"]
    assert all(set(r) == {"id", "purpose", "to", "created_at", "expires_at", "parked_from", "state"} for r in rows)
    assert rows[0]["to"] == s.link_id and rows[0]["state"] == "waiting" and a["code"] not in listed.text
    other = signed_up(client, OTHER)
    assert client.get("/api/ade/sign-requests", headers=other).json()["requests"] == []
    assert client.get(f"/api/ade/sign-requests/{a['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/ade/sign-requests/{a['id']}", headers=other).status_code == 404
    assert client.delete("/api/ade/sign-requests", headers=other).json() == {"cancelled": 0}
    r = client.delete("/api/ade/sign-requests", headers=owner)
    assert r.status_code == 200 and r.json() == {"cancelled": 2}
    assert client.get("/api/ade/sign-requests", headers=owner).json()["requests"] == []


# ---- what a signer hands back ----------------------------------------------------------------------------------------

def test_an_assertion_that_does_not_hold_over_the_stored_bytes_is_refused_and_nothing_is_kept(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    other_bytes = dict(p, text="push to main")
    for credential, status in ((assertion(device, other_bytes), 400),          # another step's bytes
                               ({"id": "x", "type": "public-key", "response": {}}, 400),
                               (Authenticator(alg=-8).get({"publicKey": {"challenge": "AA"}}), 401)):
        r = signer.call("POST", f"/requests/{got['id']}", {"credential": credential})
        assert r.status_code == status, (status, r.text)
    for body in ({}, {"credential": "x"}, []):
        assert signer.call("POST", f"/requests/{got['id']}", body).status_code == 422
    big = assertion(device, p)
    big["padding"] = "x" * link_commands.ASSERTION_MAX
    assert signer.call("POST", f"/requests/{got['id']}", {"credential": big}).status_code == 422
    assert request_row(got["id"])["state"] == "waiting" and request_row(got["id"])["credential"] is None
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_signed_through").fetchone()[0] == 0
    assert client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).json()["state"] == "waiting"


def test_a_request_is_signed_once_and_another_account_s_is_not_found(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    other = signed_up(client, OTHER)
    theirs = paired_signer(client, other)
    assert theirs.call("GET", "/requests").json()["requests"] == []
    assert theirs.call("POST", f"/requests/{got['id']}",
                       {"credential": assertion(device, p)}).status_code == 404
    assert signer.call("POST", f"/requests/{got['id']}", {"credential": assertion(device, p)}).status_code == 200
    kept = request_row(got["id"])["credential"]
    again = signer.call("POST", f"/requests/{got['id']}", {"credential": assertion(device, p)})
    assert again.status_code == 409 and again.json()["detail"] == "That request is signed already."
    # one that is signed is refused before any assertion is read: not checked, not kept
    other = signer.call("POST", f"/requests/{got['id']}", {"credential": {"id": "x", "response": {}}})
    assert other.status_code == 409 and other.json()["detail"] == "That request is signed already."
    assert request_row(got["id"])["credential"] == kept
    # a signed request is no longer offered to a signer, and no signer route hands an assertion back
    listed = signer.call("GET", "/requests")
    assert listed.json()["requests"] == [] and "signature" not in listed.text
    assert signer.call("POST", f"/requests/{'0' * 32}", {"credential": assertion(device, p)}).status_code == 404


def test_while_high_security_is_on_the_relay_keeps_a_hardware_key_s_assertion_alone(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    with store.connect() as db:                                     # on, as a hardware key would turn it on
        db.execute("UPDATE users SET high_security = 1")
        db.execute("UPDATE passkeys SET tier = 'device'")
    r = signer.call("POST", f"/requests/{got['id']}", {"credential": assertion(device, p)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    assert request_row(got["id"])["state"] == "waiting"


def test_cancel_ends_the_request_with_its_assertion(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    assert signer.call("POST", f"/requests/{got['id']}", {"credential": assertion(device, p)}).status_code == 200
    assert client.delete(f"/api/ade/sign-requests/{got['id']}", headers=owner).json() == {"cancelled": 1}
    assert request_row(got["id"]) is None
    assert client.get(f"/api/ade/sign-requests/{got['id']}", headers=owner).status_code == 404
    assert client.delete(f"/api/ade/sign-requests/{got['id']}", headers=owner).status_code == 404


def test_a_request_ends_with_its_nonce_and_is_deleted_ten_minutes_later(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id)
    got = parked(client, owner, p)
    user = p["account"]
    _, nonce_end = passkeys.open_nonce(p["nonce"], user, "instruction", int(time.time()))
    assert got["expires_at"] == nonce_end < p["expires_at"]         # its nonce's end, before its payload's
    end = got["expires_at"]
    assert signers.requests_for_page(user, now=end - 1)["requests"] != []
    assert signers.requests_for_page(user, now=end)["requests"] == []
    with pytest.raises(link.LinkError) as e:
        signers.collect(user, got["id"], now=end)
    assert e.value.status == 404
    row = signer_row(signer.id)
    assert signers.requests_for_signer(row, now=end)["requests"] == []
    with pytest.raises(link.LinkError) as e:
        signers.submit(row, got["id"], {"credential": assertion(device, p)}, now=end)
    assert e.value.status == 404
    assert request_row(got["id"]) is not None                       # kept a while after its end
    signers.requests_for_signer(row, now=end + signers.KEPT_AFTER)
    assert request_row(got["id"]) is None


def test_a_request_lives_no_longer_than_the_payload_it_holds(client, owner):
    s = paired(client, owner)
    now = int(time.time())
    p = payload(client, owner, "instruction", s.link_id, created_at=now, expires_at=now + 60)
    assert parked(client, owner, p)["expires_at"] == now + 60


# ---- what the ADE says beside a request ------------------------------------------------------------------------------

def test_an_answer_carries_the_one_question_it_names_in_the_clear_or_says_it_is_sealed(client, owner):
    from test_ade_link import post
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    asked = post(s, "question", "Which branch?", to="owner").json()["seq"]
    post(s, "question", "Another question", to="owner")
    p = payload(client, owner, "answer", s.link_id, text="main", ref=asked,
                digest=link_commands.text_digest("Which branch?"))
    parked(client, owner, p)
    [r] = signer.call("GET", "/requests").json()["requests"]
    assert r["context"]["question"] == {"text": "Which branch?"}
    assert "Another question" not in json.dumps(r)
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET sealed_in_place = 1 WHERE seq = ?", (asked,))
    assert signer.call("GET", "/requests").json()["requests"][0]["context"]["question"] == {"sealed": "in place"}
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET sealed_in_place = 0, text = ? WHERE seq = ?", ("e2e1.sealed", asked))
    assert signer.call("GET", "/requests").json()["requests"][0]["context"]["question"] == {"sealed": True}


def test_a_question_of_another_link_or_none_at_all_is_not_carried(client, owner):
    from test_ade_link import post
    s, t = paired(client, owner, "mac"), paired(client, owner, "pc")
    signer = paired_signer(client, owner)
    theirs = post(t, "question", "Their question", to="owner").json()["seq"]
    p = payload(client, owner, "answer", s.link_id, text="x", ref=theirs,
                digest=link_commands.text_digest("Their question"))
    parked(client, owner, p)
    [r] = signer.call("GET", "/requests").json()["requests"]
    assert "question" not in r["context"] and "Their question" not in json.dumps(r)


def test_a_move_carries_the_title_parked_beside_it_or_its_list_s(client, owner):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "move", s.link_id, text=None, session="abc123")
    p.pop("text")
    parked(client, owner, p, title="Fix the login page")
    [r] = signer.call("GET", "/requests").json()["requests"]
    assert r["context"]["title"] == "Fix the login page"
    for title in ("a\nb", "x" * 121, 7):
        q = payload(client, owner, "move", s.link_id, session="abc123")
        q.pop("text")
        assert park(client, owner, q, title=title).status_code == 422, title


def test_an_app_s_proposal_carries_its_name_and_words(client, owner):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = payload(client, owner, "instruction", s.link_id, text="Run the tests.")
    with store.connect() as db:
        db.execute("INSERT INTO ade_connector_grants (id, user_id, client_id, client_name, redirect_uri, scopes, "
                   "resource, allowed_at, ends_at) VALUES ('g1', ?, 'c1', 'Claude', 'x', 'x', 'x', 0, 0)",
                   (p["account"],))
        db.execute("INSERT INTO ade_proposals (id, user_id, grant_id, kind, link_id, text, state, created_at, "
                   "expires_at) VALUES (?, ?, 'g1', 'instruction', ?, 'Run the tests now.', 'proposed', 0, ?)",
                   (p["id"], p["account"], s.link_id, int(time.time()) + 600))
    parked(client, owner, p)
    [r] = signer.call("GET", "/requests").json()["requests"]
    assert r["context"]["app"] == {"name": "Claude", "words": "Run the tests now."}


def test_while_high_security_is_on_the_keys_that_may_sign_are_its_hardware_keys(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    parked(client, owner, payload(client, owner, "instruction", s.link_id))
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1")
        db.execute("UPDATE passkeys SET tier = 'hardware'")
    [r] = signer.call("GET", "/requests").json()["requests"]
    assert r["allow"] == [b64u(device.credential_id)] and r["context"]["high_security"] is True
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = 'device'")
    assert signer.call("GET", "/requests").json()["requests"][0]["allow"] == []


def test_marked_reads_the_client_data_and_nothing_else(device):
    p = {"a": 1}
    assert signers.marked(assertion(device, p, **MARK)) is True
    assert signers.marked(json.dumps(assertion(device, p, **MARK))) is True
    assert signers.marked(assertion(device, p)) is False
    assert signers.marked(assertion(device, p, proofpane="second-screen/2")) is False
    for nothing in (None, "", "{", {}, {"response": {"clientDataJSON": "!!"}}, [], 7):
        assert signers.marked(nothing) is False
    # client data that is not an object, not UTF-8, not base64url text, or nested past the reader: never marked, and
    # never an error that would turn a page's view of a stored step into a 500
    for client_data in (b"[1]", b'"second-screen/1"', b"\xff\xfe", b"[" * 100000 + b"]" * 100000):
        assert signers.marked({"response": {"clientDataJSON": link.b64u(client_data)}}) is False
    for response in ([], {"clientDataJSON": 7}, {"clientDataJSON": None}, "x"):
        assert signers.marked({"response": response}) is False
