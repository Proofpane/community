"""Signers (proofpane_open/ade/signers.py), through the app: pairing with a ticket the page made, the three principals
kept apart (the page's sign-in, a link's key, a signer's key, each on its own routes alone), one key never standing for
a signer and a link, revoking and signing out everywhere, what a signer may not do, and the steps a signer composes,
posted to the functions the page's routes call. Requests are signed here by a signer of the tests' own, so a mismatch
with the client would show."""

import hashlib
import json
import time
import urllib.parse

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import link, signers
from proofpane_core.canonical import canonicalize
from test_ade_link import HOST, OTHER, Session, b64u, confirmed, paired, raw, ticket
from test_ade_link_commands import assertion, device, event_row, link_row, owner, payload  # noqa: F401  fixtures

MARK = {"proofpane": signers.MARKER}       # what a signer writes in its client data


class Signer:
    """A signer's side: its own Ed25519 key, signing what a link's signs (host, method, raw path, raw query, time, the
    body's sha256) under its own scheme name."""

    def __init__(self, client, key=None):
        self.client = client
        self.key = key or Ed25519PrivateKey.generate()

    def call(self, method, path, body=None, query=None, ts=None, scheme="ADE-Signer", key=None, data=None,
             signed_path=None):
        key = key or self.key
        data = data if data is not None else json.dumps(body).encode() if body is not None else b""
        raw_path = path if path.startswith("/api/") else "/api/ade/signer" + path
        raw_query = urllib.parse.urlencode(query or {})
        ts = int(time.time()) if ts is None else ts
        message = "\n".join((HOST, method, signed_path or raw_path, raw_query, str(ts),
                             hashlib.sha256(data).hexdigest())).encode()
        auth = f"{scheme} key_id={hashlib.sha256(raw(key)).hexdigest()[:16]}, ts={ts}, sig={b64u(key.sign(message))}"
        return self.client.request(method, raw_path + ("?" + raw_query if raw_query else ""),
                                   content=data if method in ("POST", "PUT", "DELETE") else None,
                                   headers={"Authorization": auth, "Content-Type": "application/json"})

    def pair(self, ticket, name="MacBook", **kw):
        return self.call("POST", "/pair", {"ticket": ticket, "name": name, "public_key": b64u(raw(self.key))}, **kw)


@pytest.fixture(autouse=True)
def unhurried(monkeypatch):
    # a signer reads its requests every two seconds at most; the tests read as fast as they need, but the one that
    # tests the bound
    monkeypatch.setattr(signers, "POLL_EVERY", 0)


def signer_ticket(client, owner, confirm=True):
    r = client.post("/api/ade/signers/tickets", headers=owner,
                    json={"confirmation": confirmed(client, owner, "signer-ticket")} if confirm else {})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"             # a ticket is a secret, used once
    return r.json()


def paired_signer(client, owner, name="MacBook"):
    s = Signer(client)
    r = s.pair(signer_ticket(client, owner)["ticket"], name=name)
    assert r.status_code == 200, r.text
    s.id = r.json()["signer_id"]
    return s


def signer_row(signer_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_signers WHERE id = ?", (signer_id,)).fetchone())


def account(client, owner):
    return client.get("/api/auth/me", headers=owner).json()["id"]


# ---- pairing ------------------------------------------------------------------------------------------------------------

def test_a_signer_pairs_with_a_ticket_the_page_made_and_signs_its_requests_with_its_own_key(client, owner):
    made = signer_ticket(client, owner)
    assert set(made) == {"ticket", "expires_at", "url", "account", "command"}
    assert made["url"] == "https://open.example.test" and made["account"] == account(client, owner)
    assert made["command"] == f"ade_link.py signer pair --url {made['url']} --ticket {made['ticket']}"
    assert made["expires_at"] - int(time.time()) in range(signers.TICKET_TTL - 5, signers.TICKET_TTL + 1)
    s = Signer(client)
    r = s.pair(made["ticket"].lower(), name="Lenas MacBook")          # in either case, as pair takes a ticket
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"signer_id", "account", "url"}
    assert r.json()["account"] == made["account"] and r.json()["url"] == made["url"]
    assert r.headers["cache-control"] == "no-store"
    row = signer_row(r.json()["signer_id"])
    assert row["public_key"] == b64u(raw(s.key)) and row["confirmed"] == link.TICKET_BY_OTHER
    keys = s.call("GET", "/owner-keys")
    assert keys.status_code == 200 and keys.json()["account"] == made["account"]
    page = client.get("/api/ade/signers", headers=owner)
    assert page.headers["cache-control"] == "no-store"
    listed = page.json()["signers"]
    assert [(x["id"], x["name"]) for x in listed] == [(row["id"], "Lenas MacBook")]
    assert set(listed[0]) == {"id", "name", "created_at", "last_seen_at"}


def test_a_ticket_takes_the_account_s_confirmation_and_pairs_once(client, owner):
    refused = client.post("/api/ade/signers/tickets", headers=owner, json={})
    assert refused.status_code == 403 and refused.json()["detail"] == signers.SIGNER_NEEDS_CONFIRM
    made = signer_ticket(client, owner)["ticket"]
    assert Signer(client).pair(made).status_code == 200
    again = Signer(client).pair(made)
    assert again.status_code == 403 and "used" in again.json()["detail"]
    late = signer_ticket(client, owner)["ticket"]
    with store.connect() as db:                                     # at its end
        db.execute("UPDATE ade_signer_tickets SET expires_at = ? WHERE used_at IS NULL", (int(time.time()),))
    assert Signer(client).pair(late).status_code == 403
    for wrong in ("PP-AAAAAAAA", "pp-0000000", "", 7, None, "PP-AAAA" + chr(0x017F) + "AAA"):
        assert Signer(client).pair(wrong).status_code == 403, wrong
    # a link's ticket is not a signer's, nor the other way
    s = Session(client)
    assert s.pair(signer_ticket(client, owner)["ticket"]).status_code == 403
    assert Signer(client).pair(ticket(client, owner)).status_code == 403


def test_a_signer_ticket_spends_only_a_confirmation_made_for_a_signer_ticket(client, owner):
    # each confirmation is bound to its change (passkeys.CHANGE); a signer's ticket is one: a word given for a
    # link's ticket, or for another change, makes no signer ticket and is left unused, and one given for a signer
    # ticket makes no link ticket and is left unused too
    for other in ("link-ticket:new", "sign-out-everywhere", "passkey-add"):
        given = confirmed(client, owner, other)
        r = client.post("/api/ade/signers/tickets", headers=owner, json={"confirmation": given})
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), other
        if other == "link-ticket:new":                              # left unused: its own change still takes it
            assert client.post("/api/ade/link/tickets", headers=owner,
                               json={"confirmation": given}).status_code == 200
    given = confirmed(client, owner, signers.TICKET_CHANGE)
    r = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": given})
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)
    r = client.post("/api/ade/signers/tickets", headers=owner, json={"confirmation": given})
    assert r.status_code == 200, r.text
    assert passkeys.CHANGE.fullmatch(signers.TICKET_CHANGE)


def test_a_signer_s_pairing_takes_its_time_once_it_holds_the_write_lock(client, owner, monkeypatch):
    """A wait for the write lock moves none of a pairing's checks: the ticket is read again under the lock, with the
    time taken again, as a session's pairing is (link.pair). So a ticket that expired while its pairing waited for the
    lock is refused, and left unused."""
    made = signer_ticket(client, owner)
    real, calls = time.time, []

    def late():                         # the second reading of the clock, under the lock, is at the ticket's end
        calls.append(1)
        return real() if len(calls) == 1 else made["expires_at"]

    monkeypatch.setattr(signers, "_now", lambda now: now if now is not None else int(late()))
    r = Signer(client).pair(made["ticket"])
    assert r.status_code == 403 and "has expired" in r.json()["detail"] and len(calls) == 2
    with store.connect() as db:
        assert db.execute("SELECT used_at FROM ade_signer_tickets").fetchone()["used_at"] is None


def test_pairing_proves_the_key_it_pairs_and_takes_a_proper_name(client, owner):
    s = Signer(client)
    other = Ed25519PrivateKey.generate()
    made = signer_ticket(client, owner)["ticket"]
    signed_by_other = s.call("POST", "/pair", {"ticket": made, "name": "mac", "public_key": b64u(raw(s.key))},
                             key=other)
    assert signed_by_other.status_code == 401 and "not signed with the key it pairs" in signed_by_other.text
    for key in ("", "AAAA", b64u(b"x" * 31), b64u(b"x" * 33), 7):
        r = s.call("POST", "/pair", {"ticket": made, "name": "mac", "public_key": key})
        assert r.status_code == 422, (key, r.text)
    for name in ("", " mac", "mac ", "two  spaces", "x" * 61, "a/b", "PP-ABCDEFGH", 7, None):
        r = s.pair(made, name=name)
        assert r.status_code == 422, (name, r.text)
    assert s.call("POST", "/pair", data=b"{not json").status_code == 422
    assert s.call("POST", "/pair", data=b"[]").status_code == 422
    # what was refused used nothing: the ticket still pairs
    assert s.pair(made, name="mac").status_code == 200


def test_a_ticket_no_hardware_key_confirmed_pairs_nothing_once_high_security_is_on(client, owner):
    made = signer_ticket(client, owner)["ticket"]
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1")
    r = Signer(client).pair(made)
    assert r.status_code == 403 and r.json()["detail"] == link.TICKET_NOT_HARDWARE


def test_an_account_has_at_most_five_signers(client, owner):
    for n in range(signers.MAX_SIGNERS):
        paired_signer(client, owner, name=f"mac {n}")
    r = client.post("/api/ade/signers/tickets", headers=owner, json={"confirmation": confirmed(client, owner, "signer-ticket")})
    assert r.status_code == 409 and "at most 5 signers" in r.json()["detail"]
    one = client.get("/api/ade/signers", headers=owner).json()["signers"][0]["id"]
    assert client.delete(f"/api/ade/signers/{one}", headers=owner).status_code == 200
    made = signer_ticket(client, owner)["ticket"]
    paired_signer(client, owner, name="sixth")                      # the room the revoked one left
    assert Signer(client).pair(made).status_code == 409             # a ticket made in time pairs no sixth


# ---- one key, one principal ----------------------------------------------------------------------------------------

def test_one_key_never_stands_for_a_signer_and_a_link(client, owner):
    s = paired(client, owner, name="mac")
    as_signer = Signer(client, key=s.key).pair(signer_ticket(client, owner)["ticket"])
    assert as_signer.status_code == 409 and "linked session's" in as_signer.json()["detail"]
    signer = paired_signer(client, owner)
    as_link = Session(client, key=signer.key).pair(ticket(client, owner), name="other")
    assert as_link.status_code == 409 and "a signer's" in as_link.json()["detail"]
    repaired = Session(client, key=signer.key).pair(ticket(client, owner, s.link_id), name="mac")
    assert repaired.status_code == 409 and "a signer's" in repaired.json()["detail"]
    assert link_row(s.link_id)["public_key"] == b64u(raw(s.key))      # the link keeps its own key
    twice = Signer(client, key=signer.key).pair(signer_ticket(client, owner)["ticket"])
    assert twice.status_code == 409 and "already a live signer's" in twice.json()["detail"]
    # once revoked, the key stands for nothing, and may be a link's
    assert client.delete(f"/api/ade/signers/{signer.id}", headers=owner).status_code == 200
    assert Session(client, key=signer.key).pair(ticket(client, owner), name="later").status_code == 200


def test_a_key_that_asks_to_be_linked_is_no_signer_s_and_a_signer_s_key_asks_nothing(client, owner):
    from test_ade_link_asks import address, ask
    where = address(client, owner)
    session, r = ask(client, where)
    assert r.status_code == 200, r.text
    refused = Signer(client, key=session.key).pair(signer_ticket(client, owner)["ticket"])
    assert refused.status_code == 409 and "asks to be one" in refused.json()["detail"]
    signer = paired_signer(client, owner)
    _, r = ask(client, where, name="other", s=Session(client, key=signer.key))
    assert r.status_code == 409 and "a signer's" in r.json()["detail"]


# ---- three principals, each on its own routes ------------------------------------------------------------------------

SIGNER_ROUTES = [("GET", "/owner-keys"), ("GET", "/requests"), ("POST", "/requests/" + "0" * 32),
                 ("GET", "/sessions"), ("POST", "/nonce"), ("POST", "/commands"), ("POST", "/links/x/resume")]


def test_every_answer_under_the_second_screen_s_routes_carries_no_store(client, owner):
    signer = paired_signer(client, owner)
    answers = [(client.get("/api/ade/sign-requests"), 401),            # signed out: the sign-in check's own answer
               (client.get("/api/ade/signers"), 401),
               (client.post("/api/ade/signers/tickets", headers=owner, json={"confirmation": 5}), 422),   # FastAPI's
               (signer.call("GET", "/requests/" + "0" * 32), 404),     # no signer route answers these: the app's own
               (signer.call("POST", "/nothing"), 405)]
    for r, status in answers:
        assert r.status_code == status and r.headers["cache-control"] == "no-store", r.text
    assert client.get("/api/health").headers.get("cache-control") != "no-store"      # only these routes


def test_a_link_s_key_and_the_page_s_sign_in_open_no_signer_route(client, owner):
    s = paired(client, owner)
    for method, path in SIGNER_ROUTES:
        for r in (Signer(client, key=s.key).call(method, path, {}),                     # the link's key
                  Signer(client, key=s.key).call(method, path, {}, scheme="ADE-Link"),  # and its own scheme
                  client.request(method, "/api/ade/signer" + path, headers=owner, json={}),
                  client.request(method, "/api/ade/signer" + path, json={})):
            assert r.status_code == 401, (method, path, r.text)
            assert r.headers["cache-control"] == "no-store"


def test_a_signer_s_key_opens_no_link_route_and_no_page_route(client, owner):
    paired(client, owner)
    signer = paired_signer(client, owner)
    for method, path in (("POST", "/heartbeat"), ("GET", "/feed"), ("POST", "/events"), ("GET", "/owner-keys"),
                         ("POST", "/take"), ("GET", "/state"), ("GET", "/e2e"), ("PUT", "/checkpoint")):
        for scheme in ("ADE-Link", "ADE-Signer"):
            r = signer.call(method, "/api/ade/link" + path, {}, scheme=scheme)
            assert r.status_code == 401, (method, path, scheme, r.text)
    for method, path in (("POST", "/api/ade/sign-requests"), ("GET", "/api/ade/sign-requests"),
                         ("GET", "/api/ade/signers"), ("POST", "/api/ade/signers/tickets"),
                         ("POST", "/api/ade/link/nonce"), ("POST", "/api/ade/link/commands"),
                         ("GET", "/api/ade/link/sessions"), ("POST", "/api/account/confirm/options"),
                         ("POST", "/api/ade/holds/x/cancel")):
        r = signer.call(method, path, {})
        assert r.status_code == 401, (method, path, r.text)


def test_a_signer_has_no_route_to_read_a_session_s_words_or_to_act(client, owner):
    signer = paired_signer(client, owner)
    for method, path in (("GET", "/feed"), ("GET", "/timeline/x"), ("GET", "/history"), ("POST", "/events"),
                         ("POST", "/take"), ("GET", "/questions"), ("GET", "/checkpoint"), ("GET", "/e2e"),
                         ("POST", "/approve"), ("POST", "/park"), ("GET", "/assertions"), ("POST", "/confirm")):
        r = signer.call(method, path, {})
        assert r.status_code in (404, 405), (method, path, r.status_code)


def test_a_request_signed_too_far_from_the_ade_s_clock_is_refused_with_its_time(client, owner):
    signer = paired_signer(client, owner)
    r = signer.call("GET", "/owner-keys", ts=int(time.time()) - link.SKEW - 5)
    assert r.status_code == 401 and isinstance(r.json()["server_time"], int)
    # the signature covers the path: one made for another route opens none
    moved = signer.call("GET", "/sessions", signed_path="/api/ade/signer/owner-keys")
    assert moved.status_code == 401 and "does not check" in moved.json()["detail"]
    r = client.get("/api/ade/signer/owner-keys", headers={"Authorization": "ADE-Signer key_id=00"})
    assert r.status_code == 401


# ---- ending a signer ------------------------------------------------------------------------------------------------

def test_revoking_a_signer_ends_its_key_everywhere_and_only_the_owner_revokes_it(client, owner):
    signer = paired_signer(client, owner)
    other = signed_up(client, OTHER)
    assert client.delete(f"/api/ade/signers/{signer.id}", headers=other).status_code == 404
    assert signer.call("GET", "/owner-keys").status_code == 200
    r = client.delete(f"/api/ade/signers/{signer.id}", headers=owner)
    assert r.status_code == 200 and isinstance(r.json()["revoked_at"], int)
    refused = signer.call("GET", "/owner-keys")
    assert refused.status_code == 401 and refused.json()["detail"] == signers.REVOKED
    assert client.delete(f"/api/ade/signers/{signer.id}", headers=owner).status_code == 404
    assert client.get("/api/ade/signers", headers=owner).json()["signers"] == []


def test_signing_out_everywhere_revokes_every_signer_its_tickets_and_what_was_parked(client, owner, device):
    from test_ade_sign_relay import park
    a, b = paired_signer(client, owner, "one"), paired_signer(client, owner, "two")
    unused = signer_ticket(client, owner)["ticket"]
    s = paired(client, owner)
    assert park(client, owner, payload(client, owner, "instruction", s.link_id)).status_code == 200
    r = client.post("/api/account/sign-out-everywhere", headers=owner,
                    json={"confirmation": confirmed(client, owner, "sign-out-everywhere")})
    assert r.status_code == 200, r.text
    fresh = {"Authorization": "Bearer " + r.json()["token"]}
    for x in (a, b):
        assert x.call("GET", "/owner-keys").status_code == 401
    assert Signer(client).pair(unused).status_code == 403
    assert client.get("/api/ade/sign-requests", headers=fresh).json()["requests"] == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_sign_requests").fetchone()[0] == 0


def test_another_account_s_signers_are_not_this_one_s(client, owner):
    paired_signer(client, owner)
    other = signed_up(client, OTHER)
    assert client.get("/api/ade/signers", headers=other).json()["signers"] == []
    theirs = paired_signer(client, other)
    assert theirs.call("GET", "/owner-keys").json()["account"] == account(client, other)


# ---- what a signer reads ----------------------------------------------------------------------------------------------

def test_a_signer_reads_the_live_links_by_id_name_and_kind_and_nothing_else(client, owner):
    s = paired(client, owner, "mac")
    gone = paired(client, owner, "old", kind="cloud")
    from test_ade_link import revoke
    assert revoke(client, owner, gone.link_id).status_code == 200
    signer = paired_signer(client, owner)
    assert client.post(f"/api/ade/link/links/{s.link_id}/stop", headers=owner).status_code == 200
    links = signer.call("GET", "/sessions").json()["links"]
    assert [(x["id"], x["name"], x["kind"]) for x in links] == [(s.link_id, "mac", "local")]
    assert set(links[0]) == {"id", "name", "kind", "stopped_at"} and isinstance(links[0]["stopped_at"], int)


def test_a_signer_reads_the_owner_s_keys_and_the_ones_that_may_sign_now(client, owner, device):
    signer = paired_signer(client, owner)
    got = signer.call("GET", "/owner-keys").json()
    assert set(got) == {"account", "passkeys", "credentials", "high_security"}
    assert got["high_security"] is False
    assert [k["credential_id"] for k in got["passkeys"]] == [b64u(device.credential_id)]
    assert got["credentials"] == [b64u(device.credential_id)]          # every passkey, while high security is off


def test_a_signer_reads_its_requests_every_two_seconds_at_most(client, owner, monkeypatch):
    monkeypatch.setattr(signers, "POLL_EVERY", 2)
    signer = paired_signer(client, owner)
    assert signer.call("GET", "/requests").status_code == 200
    r = signer.call("GET", "/requests")
    assert r.status_code == 429 and r.json()["retry_after"] in (1, 2)
    row = signer_row(signer.id)
    later = signers.requests_for_signer(row, now=row["polled_at"] + 2)
    assert later == {"requests": []}
    with pytest.raises(link.LinkError) as e:
        signers.requests_for_signer(row, now=row["polled_at"] + 3)
    assert e.value.status == 429


# ---- what a signer composes ------------------------------------------------------------------------------------------

def signer_payload(client, owner, signer, purpose, to, **over):
    """A payload as the signer composes it, with a nonce asked by the signer."""
    r = signer.call("POST", "/nonce", {"purpose": purpose})
    assert r.status_code == 200, r.text
    return payload(client, owner, purpose, to, nonce=r.json()["nonce"], **over)


def test_an_instruction_a_signer_composes_is_stored_by_the_page_s_own_function_and_names_its_signer(client, owner,
                                                                                                     device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    p = signer_payload(client, owner, signer, "instruction", s.link_id, text="Run the tests.")
    credential = assertion(device, p, **MARK)
    r = signer.call("POST", "/commands", {"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    row = event_row(r.json()["seq"])
    assert row["payload"].encode() == canonicalize(p) and json.loads(row["signature"]) == credential
    with store.connect() as db:
        assert signers.through(db, row["user_id"], p["id"], row["signature"]) == "MacBook"
    assert signers.marked(row["signature"]) is True
    taken = s.call("POST", "/take", {"seq": r.json()["seq"], "instance": "i1"})
    assert taken.status_code == 200, taken.text
    # the same function's checks: a signature over other bytes is refused, and nothing is recorded for it
    q = signer_payload(client, owner, signer, "instruction", s.link_id)
    wrong = signer.call("POST", "/commands", {"payload": q, "credential": assertion(device, p, **MARK)})
    assert wrong.status_code == 400 and "canonical form" in wrong.json()["detail"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_signed_through WHERE payload_id = ?", (q["id"],)).fetchone()[0] == 0


def test_a_resume_a_signer_composes_lifts_the_stop(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    assert client.post(f"/api/ade/link/links/{s.link_id}/stop", headers=owner).status_code == 200
    stopped = link_row(s.link_id)["stopped_at"]
    while int(time.time()) <= stopped:
        time.sleep(0.05)
    p = signer_payload(client, owner, signer, "resume", s.link_id)
    r = signer.call("POST", f"/links/{s.link_id}/resume", {"payload": p, "credential": assertion(device, p, **MARK)})
    assert r.status_code == 200, r.text
    assert link_row(s.link_id)["stopped_at"] is None
    with store.connect() as db:
        assert signers.through(db, link_row(s.link_id)["user_id"], p["id"],
                               link_row(s.link_id)["resume_signature"]) == "MacBook"


def test_a_signer_composes_no_answer_and_no_statement(client, owner, device):
    s = paired(client, owner)
    signer = paired_signer(client, owner)
    for purpose in ("answer", "e2e-key", "e2e-off", "confirm", ""):
        r = signer.call("POST", "/nonce", {"purpose": purpose})
        assert r.status_code == 422, (purpose, r.text)
    p = payload(client, owner, "answer", s.link_id, ref=1, digest="sha256:" + "0" * 64)
    r = signer.call("POST", "/commands", {"payload": p, "credential": assertion(device, p)})
    assert r.status_code == 422 and "instruction or a move" in r.json()["detail"]
    r = signer.call("POST", "/commands", {"payload": "x", "credential": {}})
    assert r.status_code == 422
