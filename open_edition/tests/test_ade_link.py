"""The ADE session channel, stage 1, through the app: tickets and pairing, signed requests,
events and their limits, heartbeats, the feed and the cursor, checkpoints, the resume document, and the owner's
alerts. Requests are signed here by a signer of the tests' own, so a mismatch with the client would show."""

import base64
import contextlib
import hashlib
import json
import os
import time
import urllib.parse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import ADMIN, signed_up
from proofpane_open import store
from proofpane_open.ade import link, link_asks

HOST = "open.example.test"     # the host of the tests' OPEN_EDITION_PUBLIC_URL
OTHER = "other@example.com"


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def raw(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Session:
    """A session's side, signing as the design says: host, method, raw path, raw query, time, the body's sha256."""

    def __init__(self, client, key=None):
        self.client = client
        self.key = key or Ed25519PrivateKey.generate()

    def call(self, method, path, body=None, query=None, ts=None, host=HOST, key=None, headers=None, sign_body=None,
             raw_body=None):
        key = key or self.key
        data = raw_body if raw_body is not None else json.dumps(body).encode() if body is not None else b""
        raw_path = "/api/ade/link" + path
        raw_query = urllib.parse.urlencode(query or {})
        ts = int(time.time()) if ts is None else ts
        signed = data if sign_body is None else sign_body
        message = "\n".join((host, method, raw_path, raw_query, str(ts),
                             hashlib.sha256(signed).hexdigest())).encode()
        auth = f"ADE-Link key_id={hashlib.sha256(raw(key)).hexdigest()[:16]}, ts={ts}, sig={b64u(key.sign(message))}"
        return self.client.request(method, raw_path + ("?" + raw_query if raw_query else ""),
                                   content=data if method in ("POST", "PUT") else None,
                                   headers=dict({"Authorization": auth, "Content-Type": "application/json"},
                                                **(headers or {})))

    def pair(self, ticket, name="mac", kind="local", ref="local-1", waits=True, **kw):
        """Pair as a current client does (waits: true); waits=None leaves it out, as an older client did."""
        body = {"ticket": ticket, "name": name, "kind": kind, "public_key": b64u(raw(self.key)), "session_ref": ref}
        if waits is not None:
            body["waits"] = waits
        return self.call("POST", "/pair", body, **kw)


@pytest.fixture
def owner(client):
    return signed_up(client)


PASSWORD = "correct horse battery"                  # conftest's signed_up


def confirmed(client, owner, change="link-ticket:new"):
    """A confirmation from the account's password (passkeys.confirm), made for `change` (passkeys.CHANGE: a ticket for
    a new session unless named), which that link change uses up."""
    r = client.post("/api/account/confirm", headers=owner, json={"password": PASSWORD, "change": change})
    assert r.status_code == 200, r.text
    return r.json()["confirmation"]


def ticket(client, owner, link_id=None):
    body = {"confirmation": confirmed(client, owner, f"link-ticket:{link_id or 'new'}")}
    if link_id:
        body["link_id"] = link_id
    r = client.post("/api/ade/link/tickets", headers=owner, json=body)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def revoke(client, owner, link_id, confirm=True):
    return client.request("DELETE", f"/api/ade/link/links/{link_id}", headers=owner,
                          json={"confirmation": confirmed(client, owner, f"link-revoke:{link_id}")} if confirm else {})


@contextlib.contextmanager
def at_once():
    """Pairing's holds set to none while it runs, so a pairing is linked as its answer comes: for the tests of what a
    link does once it is linked (test_link_pair_hold.py tests the wait itself)."""
    saved = {k: os.environ.get(k) for k in (link_asks.HOLD_SETTING, link_asks.HOLD_HIGH_SETTING)}
    os.environ.update(dict.fromkeys(saved, "0"))
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def paired(client, owner, name="mac", kind="local"):
    s = Session(client)
    with at_once():
        r = s.pair(ticket(client, owner), name=name, kind=kind)
    assert r.status_code == 200 and r.json()["status"] == "linked", r.text
    s.link_id = r.json()["link_id"]
    s.name = name
    return s


def post(s, kind, text="t", **kw):
    body = dict({"client_id": kw.pop("client_id", None) or hashlib.sha256(f"{kind}{text}{time.time()}{kw}".encode())
                 .hexdigest()[:16], "kind": kind, "text": text}, **kw)
    return s.call("POST", "/events", body)


def row(link_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_links WHERE id = ?", (link_id,)).fetchone())


# ---- tickets and pairing ----------------------------------------------------------------------------------------

def test_a_ticket_pairs_once_and_in_either_case(client, owner):
    t = ticket(client, owner)
    assert t.startswith("PP-") and len(t) == 11
    s = Session(client)
    assert s.pair(t.lower()).status_code == 200
    again = Session(client).pair(t, name="another")
    assert again.status_code == 403 and "used" in again.json()["detail"]


def test_a_ticket_is_ascii_in_either_case_and_a_spelling_unicode_maps_onto_one_is_not(client, owner):
    """The ticket shape the ADE refuses in what it keeps is ASCII, as the page's is; pair takes only what that shape
    covers, so a spelling whose upper case is a ticket (a long s for an S, a dotless i for an I) is not one."""
    t = ticket(client, owner)
    with store.connect() as db:                         # this ticket, as one whose letters hold the S and the I below
        db.execute("UPDATE ade_link_tickets SET ticket_hash = ? WHERE ticket_hash = ?",
                   (link._ticket_hash("PP-SSIIAAAA"), link._ticket_hash(t)))
    long_s, dotless_i = chr(0x17F), chr(0x131)
    for spelling in ("PP-" + long_s * 2 + "IIAAAA", "PP-SS" + dotless_i * 2 + "AAAA", "pp-" + long_s * 2 + "iiaaaa"):
        assert spelling.upper() == "PP-SSIIAAAA"         # what pair compared, and took, before
        assert Session(client).pair(spelling).status_code == 403, spelling
    assert Session(client).pair("pp-ssiiaaaa").status_code == 200


def test_an_expired_ticket_is_refused(client, owner, monkeypatch):
    t = ticket(client, owner)
    later = time.time() + link.TICKET_TTL + 1
    monkeypatch.setattr(link.time, "time", lambda: later)
    assert Session(client).pair(t, ts=int(later)).status_code == 403


def test_a_refused_pair_leaves_the_ticket_usable(client, owner):
    t = ticket(client, owner)
    s = Session(client)
    bad = s.call("POST", "/pair", {"ticket": t, "name": "bad name!", "kind": "local",
                                   "public_key": b64u(raw(s.key)), "waits": True})
    assert bad.status_code == 422
    assert s.pair(t).status_code == 200


def test_a_link_revoked_as_its_ticket_is_made_makes_no_ticket_and_spends_nothing(client, owner, monkeypatch):
    """The link a ticket re-pairs is read in the transaction that spends the confirmation (create_ticket), so a link
    revoked after any earlier look at it, but before that transaction begins, makes no ticket, and the confirmation
    is left unused. Here another connection revokes it just as the transaction's BEGIN is sent."""
    s = paired(client, owner)
    word = confirmed(client, owner, f"link-ticket:{s.link_id}")
    real = store.connect

    class Revoking:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                with real() as other:
                    other.execute("UPDATE ade_links SET revoked_at = ? WHERE id = ?", (int(time.time()), s.link_id))
            return self.db.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.db, name)

    @contextlib.contextmanager
    def revoking(path=None):
        with real(path) as db:
            yield Revoking(db)

    monkeypatch.setattr(store, "connect", revoking)
    r = client.post("/api/ade/link/tickets", headers=owner, json={"link_id": s.link_id, "confirmation": word})
    monkeypatch.setattr(store, "connect", real)
    assert r.status_code == 404, r.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_tickets WHERE link_id = ?", (s.link_id,)).fetchone()[0] == 0
        db.execute("UPDATE ade_links SET revoked_at = NULL WHERE id = ?", (s.link_id,))
    # left unused, it makes the ticket once the link is live again
    r = client.post("/api/ade/link/tickets", headers=owner, json={"link_id": s.link_id, "confirmation": word})
    assert r.status_code == 200, r.text


def test_a_wrong_or_used_ticket_is_refused_before_the_signature_is_checked(client, owner):
    used = ticket(client, owner)
    assert Session(client).pair(used).status_code == 200
    for t in ("PP-AAAAAAAA", used):
        s = Session(client)
        r = s.call("POST", "/pair", {"ticket": t, "name": "late", "kind": "local",
                                     "public_key": b64u(raw(s.key)), "waits": True}, key=Ed25519PrivateKey.generate())
        assert r.status_code == 403        # not 401: the ticket was looked at first


def test_a_pair_signed_by_another_key_than_its_body_s_is_refused(client, owner):
    s = Session(client)
    r = s.call("POST", "/pair", {"ticket": ticket(client, owner), "name": "mac", "kind": "local",
                                 "public_key": b64u(raw(Ed25519PrivateKey.generate())), "waits": True})
    assert r.status_code == 401


def test_intervals_by_kind_and_names_are_not_reused(client, owner):
    mac = paired(client, owner, "mac", "local")
    cloud = paired(client, owner, "cloud", "cloud")
    assert row(mac.link_id)["interval_min"] == 15 and row(cloud.link_id)["interval_min"] == 30
    r = Session(client).pair(ticket(client, owner), name="mac")
    assert r.status_code == 409
    for reserved in ("owner", "all"):
        assert Session(client).pair(ticket(client, owner), name=reserved).status_code == 422
    for kind in (["local"], {"local": 1}):            # not a string: refused, as any kind that is not one, not an error
        r = Session(client).pair(ticket(client, owner), name="other", kind=kind)
        assert r.status_code == 422 and r.json()["detail"] == "A link's kind is cloud or local.", kind


def test_a_new_name_has_no_two_spaces_together_and_none_at_its_end_and_reads_apart_from_every_live_one(client, owner):
    """Names are told apart with their case folded and each run of spaces made one (link.name_key), so `My Mac` or
    `my  mac`, a name kept before the rules on spaces, cannot stand beside `my mac` and pass for it on the page."""
    for bad in ("my  mac", "my mac ", "my mac  "):
        r = Session(client).pair(ticket(client, owner), name=bad)
        assert r.status_code == 422 and "no two spaces together and no space at its end" in r.json()["detail"], bad
    mac = paired(client, owner, "My Mac")
    for alike in ("my mac", "MY MAC", "my Mac"):
        r = Session(client).pair(ticket(client, owner), name=alike)
        assert r.status_code == 409 and r.json()["detail"] == "The account already has a live link named My Mac.", alike
    with store.connect() as db:                         # a name kept before two spaces together were refused
        db.execute("UPDATE ade_links SET name = 'my  mac' WHERE id = ?", (mac.link_id,))
    r = Session(client).pair(ticket(client, owner), name="My mac")
    assert r.status_code == 409 and r.json()["detail"] == "The account already has a live link named my  mac."
    assert paired(client, owner, "my mac 2").name == "my mac 2"          # another name is another name
    assert revoke(client, owner, mac.link_id).status_code == 200
    assert paired(client, owner, "my mac").name == "my mac"              # free once the link that had it is revoked


def test_a_key_on_another_live_link_is_refused(client, owner):
    mac = paired(client, owner)
    twin = Session(client, key=mac.key)
    assert twin.pair(ticket(client, owner), name="twin").status_code == 409


def test_re_pairing_replaces_the_key_and_keeps_the_link_s_state(client, owner):
    mac = paired(client, owner)
    other = paired(client, owner, "cloud", "cloud")
    assert post(other, "note", "hello", to="mac").status_code == 200
    assert mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "x"}}).status_code == 200
    assert mac.call("POST", "/ack", {"seq": 1}).status_code == 200
    fresh = Session(client)
    with at_once():                         # its wait is test_link_pair_hold's: here the move once it is made
        r = fresh.pair(ticket(client, owner, mac.link_id), name="ignored", kind="cloud", ref="new-ref")
    assert (r.json()["status"], r.json()["link_id"], r.json()["name"]) == ("linked", mac.link_id, "mac")
    assert r.json()["owner_keys"] == link.owner_keys_of(row(mac.link_id)["user_id"])        # the keys pair pins
    after = row(mac.link_id)
    assert (after["name"], after["kind"], after["session_ref"]) == ("mac", "local", "new-ref")
    assert after["acked_seq"] == 1 and after["checkpoint_version"] == 1
    assert mac.call("POST", "/heartbeat", {}).status_code == 401          # the old key stops as it moves
    assert fresh.call("POST", "/heartbeat", {}).status_code == 200
    with at_once():
        same = fresh.pair(ticket(client, owner, mac.link_id))              # the same key may return
    assert same.status_code == 200 and same.json()["status"] == "linked"


def test_a_revoked_link_is_refused_and_another_account_s_is_not_found(client, owner):
    mac = paired(client, owner)
    other_owner = signed_up(client, OTHER)
    assert revoke(client, other_owner, mac.link_id).status_code == 404
    assert client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=other_owner).status_code == 404
    r = client.post("/api/ade/link/tickets", headers=other_owner,
                    json={"link_id": mac.link_id, "confirmation": confirmed(client, other_owner)})
    assert r.status_code == 404
    assert revoke(client, owner, mac.link_id).status_code == 200
    assert mac.call("POST", "/heartbeat", {}).status_code == 401
    assert Session(client).pair(ticket(client, owner, None), name="mac").status_code == 200   # the name is free again


# ---- signed requests --------------------------------------------------------------------------------------------

def test_a_request_is_refused_unless_its_signature_covers_exactly_it(client, owner):
    mac = paired(client, owner)
    assert mac.call("GET", "/feed").status_code == 200
    assert mac.call("GET", "/feed", key=Ed25519PrivateKey.generate()).status_code == 401
    assert mac.call("POST", "/events", {"client_id": "a", "kind": "step", "text": "t"},
                    sign_body=b'{"client_id": "a", "kind": "step", "text": "other"}').status_code == 401
    r = mac.call("GET", "/feed", host="evil.example", headers={"Host": "evil.example"})
    assert r.status_code == 401
    r = mac.call("GET", "/feed", host=HOST, headers={"Host": "evil.example"})
    assert r.status_code == 200                # the server signs its own host, whatever Host header arrived


def test_a_changed_query_is_refused(client, owner):
    mac = paired(client, owner)
    ts = int(time.time())
    good = mac.call("GET", "/feed", query={"after": 0}, ts=ts)
    assert good.status_code == 200
    auth = good.request.headers["authorization"]
    r = client.get("/api/ade/link/feed?after=5", headers={"Authorization": auth})
    assert r.status_code == 401


def test_a_stale_time_is_refused_with_the_server_s_time(client, owner):
    mac = paired(client, owner)
    r = mac.call("GET", "/feed", ts=int(time.time()) - link.SKEW - 1)
    assert r.status_code == 401 and abs(r.json()["server_time"] - time.time()) < 5
    assert r.headers["cache-control"] == "no-store"


def test_last_seen_moves_at_most_once_a_minute(client, owner, monkeypatch):
    mac = paired(client, owner)
    assert mac.call("GET", "/feed").status_code == 200          # its first request as a link
    first = row(mac.link_id)["last_seen_at"]
    for later, moved in ((first + 30, False), (first + 61, True)):
        monkeypatch.setattr(link.time, "time", lambda: later)
        assert mac.call("GET", "/feed", ts=later).status_code == 200
        assert (row(mac.link_id)["last_seen_at"] == later) is moved


def test_a_signature_that_does_not_decode_is_refused_not_an_error(client, owner):
    mac = paired(client, owner)
    auth = mac.call("GET", "/feed").request.headers["authorization"]
    for sig in ("A" * 41, "A" * 44):                  # not base64 at all; base64 of 33 bytes, not a signature
        bad = auth[:auth.index("sig=") + len("sig=")] + sig
        assert client.get("/api/ade/link/feed", headers={"Authorization": bad}).status_code == 401


def test_each_side_uses_only_its_own_credential(client, owner):
    mac = paired(client, owner)
    auth = mac.call("GET", "/feed").request.headers["authorization"]
    assert client.get("/api/ade/link/sessions", headers={"Authorization": auth}).status_code == 401
    assert client.get("/api/ade/link/feed", headers=owner).status_code == 401


# ---- events ------------------------------------------------------------------------------------------------------

def test_a_repeated_client_id_returns_the_first_row(client, owner):
    mac = paired(client, owner)
    one = post(mac, "step", "first", client_id="same").json()["seq"]
    two = post(mac, "step", "changed", client_id="same").json()["seq"]
    assert one == two
    with store.connect() as db:
        assert db.execute("SELECT text FROM ade_link_events WHERE seq = ?", (one,)).fetchone()[0] == "first"


def test_the_text_limit_and_addressing(client, owner):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    assert post(mac, "step", "x" * 4001).status_code == 422
    assert post(mac, "question", "who?").status_code == 422          # a question names its addressee
    assert post(mac, "done", "all done").status_code == 422
    assert post(mac, "step", "t", to="nobody").status_code == 422
    with store.connect() as db:
        seqs = [r[0] for r in db.execute("SELECT to_name FROM ade_link_events")]
    assert seqs == []
    assert post(mac, "step", "t").status_code == 200
    with store.connect() as db:
        assert db.execute("SELECT to_name FROM ade_link_events").fetchone()[0] == "owner"


def test_a_mirrored_note_is_kept_as_such_and_only_as_a_note_to_the_owner(client, owner):
    """The mirror's notes (ade_link.py mirror) carry whose words they report, so the page can show them as such; only a
    note to the owner with no ref is one."""
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    q = post(mac, "question", "which tag?", to="owner").json()["seq"]
    for kind, kw in (("note", {"mirror": "someone"}), ("note", {"mirror": "owner", "to": "cloud"}),
                     ("note", {"mirror": "owner", "to": "all"}), ("step", {"mirror": "session"}),
                     ("note", {"mirror": "owner", "ref": q}), ("note", {"mirror": True})):
        assert post(mac, kind, "t", **kw).status_code == 422, (kind, kw)
    for who in ("owner", "session", "info"):
        assert post(mac, "note", f"words of {who}", mirror=who).status_code == 200
    assert post(mac, "note", "plain").status_code == 200
    events = client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=owner).json()["events"]
    got = {e["text"]: e.get("mirror") for e in events}
    assert got == {"words of owner": "owner", "words of session": "session", "words of info": "info", "plain": None,
                   "which tag?": None}
    assert "mirror" not in next(e for e in events if e["text"] == "plain")


@pytest.mark.parametrize("secret,shape", [
    ("sk-ant-api03-abc", "sk-ant-"), ("sk-" + "a" * 20, "16 or more"), ("ghp_x", "ghp_"), ("gho_x", "gho_"),
    ("ghu_x", "ghu_"), ("ghs_x", "ghs_"), ("ghr_x", "ghr_"),
    ("github_pat_x", "github_pat_"), ("FlyV1 fm2_x", "FlyV1"), ("AKIAxyz", "AKIA"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "PRIVATE KEY"), ("adeh_x", "adeh_"), ("PP-ABCDEFGH", "ticket")])
def test_anything_shaped_like_a_secret_is_refused_by_its_shape(client, owner, secret, shape):
    mac = paired(client, owner)
    for r in (post(mac, "step", f"here: {secret}"),
              mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"notes": secret}})):
        assert r.status_code == 422 and shape in r.json()["detail"] and secret not in r.json()["detail"]
    r = Session(client).pair(ticket(client, owner), name="n2", ref=secret)
    assert r.status_code == 422 and secret not in r.json()["detail"]
    r = Session(client).pair(ticket(client, owner), name=secret)
    assert r.status_code == 422 and secret not in r.json()["detail"]
    if not secret.startswith("-"):                   # a name starting with a dash is refused by the name's own rule
        assert shape in r.json()["detail"]


def test_task_is_not_a_secret(client, owner):
    mac = paired(client, owner)
    assert post(mac, "step", "task-1234567890abcdefghij done").status_code == 200


def test_which_events_need_a_turn(client, owner):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    q = post(mac, "question", "Q-1 cap?", to="cloud").json()["seq"]
    cases = [("question", {"to": "cloud"}, 1), ("question", {"to": "owner"}, 0), ("question", {"to": "all"}, 1),
             ("question", {"to": "mac"}, 0), ("done", {"to": "cloud"}, 1), ("step", {"to": "cloud"}, 0),
             ("waiting", {"to": "all"}, 0), ("note", {"to": "cloud"}, 0), ("note", {"to": "cloud", "ref": q}, 1)]
    for kind, extra, expected in cases:
        seq = post(mac, kind, f"{kind} {extra}", **extra).json()["seq"]
        with store.connect() as db:
            got = db.execute("SELECT needs_turn FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()[0]
        assert got == expected, (kind, extra)


def test_a_ref_must_be_a_question_of_this_account(client, owner):
    mac = paired(client, owner)
    step = post(mac, "step", "s").json()["seq"]
    other = paired(client, signed_up(client, OTHER), "x")
    theirs = post(other, "question", "q", to="owner").json()["seq"]
    for ref in (step, theirs, 99999):
        assert post(mac, "note", "a", to="owner", ref=ref).status_code == 422
    assert post(mac, "step", "a", ref=step).status_code == 422
    mine = post(mac, "question", "open?", to="owner").json()["seq"]
    for kind in ("step", "waiting", "question"):              # only a done or a note answers, or closes, one
        assert post(mac, kind, "a", to="owner", ref=mine).status_code == 422
    with store.connect() as db:
        assert db.execute("SELECT closed_by FROM ade_link_events WHERE seq = ?", (mine,)).fetchone()[0] is None
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE ref IS NOT NULL").fetchone()[0] == 0


def test_any_of_the_owner_s_links_closes_a_question(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    q = post(mac, "question", "Q-2?", to="all").json()["seq"]
    (open_q,) = client.get("/api/ade/link/sessions", headers=owner).json()["questions"]
    assert (open_q["seq"], open_q["from"], open_q["link_id"]) == (q, "mac", mac.link_id)   # the page's card
    answer = post(cloud, "note", "yes", to="mac", ref=q).json()["seq"]
    with store.connect() as db:
        assert db.execute("SELECT closed_by FROM ade_link_events WHERE seq = ?", (q,)).fetchone()[0] == answer
    assert client.get("/api/ade/link/sessions", headers=owner).json()["questions"] == []


def test_each_card_has_its_link_s_count_and_newest_event_and_no_one_else_s(client, owner):
    # a session's activity is seen on the page without a tap
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    theirs = paired(client, signed_up(client, OTHER), "mac")
    for text in ("first", "second", "third"):
        assert post(mac, "step", text).status_code == 200
    post(cloud, "note", "to mac", to="mac")
    post(theirs, "step", "not yours")
    client.post(f"/api/ade/link/questions/{post(mac, 'question', 'Q?', to='owner').json()['seq']}/close",
                headers=owner)                                              # the owner's own event is on no card
    cards = {x["name"]: x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert (cards["mac"]["events"], cards["mac"]["last_event"]["text"]) == (4, "Q?")
    assert cards["mac"]["last_event"]["kind"] == "question" and cards["mac"]["last_event"]["from"] == "mac"
    assert (cards["cloud"]["events"], cards["cloud"]["last_event"]["text"]) == (1, "to mac")
    third = paired(client, owner, "idle", "cloud")
    idle = next(x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"] if x["name"] == "idle")
    assert (idle["events"], idle["last_event"]) == (0, None) and third.link_id == idle["id"]
    # the account is the boundary, not the link id alone: a row of another account that carried mac's id (a state
    # the routes never make) is on no card of this one
    with store.connect() as db:
        db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, received_at) "
                   "VALUES ('someone-else', ?, 'x1', 'step', 'all', 'not yours', 1)", (mac.link_id,))
    cards = {x["name"]: x for x in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert (cards["mac"]["events"], cards["mac"]["last_event"]["text"]) == (4, "Q?")


def test_the_owner_closes_a_question_once_with_the_fixed_note(client, owner):
    mac = paired(client, owner)
    q = post(mac, "question", "Q-3?", to="owner").json()["seq"]
    first = client.post(f"/api/ade/link/questions/{q}/close", headers=owner).json()["seq"]
    again = client.post(f"/api/ade/link/questions/{q}/close", headers=owner).json()["seq"]
    assert first == again
    with store.connect() as db:
        note = dict(db.execute("SELECT * FROM ade_link_events WHERE seq = ?", (first,)).fetchone())
    assert (note["link_id"], note["kind"], note["to_name"], note["ref"], note["text"]) == (
        "owner", "note", "mac", q, "closed on the page")
    step = post(mac, "step", "s").json()["seq"]
    assert client.post(f"/api/ade/link/questions/{step}/close", headers=owner).status_code == 409


def test_the_hourly_limit_and_the_wake_cap_are_counted_from_the_table(client, owner, monkeypatch):
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    for i in range(link.WAKES_PER_HOUR):
        assert post(mac, "question", f"q{i}", to="cloud").status_code == 200
    r = post(mac, "question", "one more", to="cloud")
    assert r.status_code == 429 and "wakes others" in r.json()["detail"]
    assert post(mac, "step", "a step still goes").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET received_at = received_at - 3601")
    assert post(mac, "question", "an hour later", to="cloud").status_code == 200
    monkeypatch.setattr(link, "EVENTS_PER_HOUR", 5)
    for i in range(4):
        assert post(mac, "step", f"s{i}").status_code == 200
    r = post(mac, "step", "sixth")
    assert r.status_code == 429 and "events an hour" in r.json()["detail"]


# ---- heartbeat, feed, ack ----------------------------------------------------------------------------------------

def test_a_heartbeat_moves_presence_only_and_once_a_minute(client, owner):
    mac = paired(client, owner)
    assert mac.call("POST", "/heartbeat", {}).status_code == 200
    beat = row(mac.link_id)["last_heartbeat_at"]
    assert beat is not None
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events").fetchone()[0] == 0
    assert mac.call("POST", "/heartbeat", {}).status_code == 429
    old = mac.call("POST", "/heartbeat", {}, ts=beat - 120)          # an older one, replayed
    assert old.status_code == 429 and row(mac.link_id)["last_heartbeat_at"] == beat


def test_the_feed_gives_a_link_what_is_addressed_to_it_after_its_cursor(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    empty = cloud.call("GET", "/feed").json()
    assert empty == {"events": [], "through": 0, "head": 0}
    to_owner = post(mac, "step", "to the owner").json()["seq"]
    assert cloud.call("GET", "/feed").json() == {"events": [], "through": to_owner, "head": to_owner}
    s_all = post(mac, "step", "to all", to="all").json()["seq"]
    post(mac, "step", "to itself", to="mac")
    q = post(mac, "question", "to cloud", to="cloud").json()["seq"]
    w = post(mac, "waiting", "for the tray", to="all").json()["seq"]
    n = post(mac, "note", "fyi", to="cloud").json()["seq"]
    post(cloud, "step", "cloud's own", to="all")
    got = cloud.call("GET", "/feed").json()
    assert [e["seq"] for e in got["events"]] == [s_all, q, w, n] and got["through"] == got["head"]
    assert [e["seq"] for e in cloud.call("GET", "/feed", query={"turn": 1}).json()["events"]] == [q]
    assert cloud.call("POST", "/ack", {"seq": s_all}).json() == {"acked_seq": s_all}
    assert [e["seq"] for e in cloud.call("GET", "/feed").json()["events"]] == [q, w, n]  # after is the cursor
    assert cloud.call("POST", "/ack", {"seq": 1}).json() == {"acked_seq": s_all}      # never back
    assert cloud.call("POST", "/ack", {"seq": 10 ** 6}).status_code == 422             # never past the head
    cut = cloud.call("GET", "/feed", query={"limit": 1, "after": 0}).json()     # signed in the order it is sent
    assert [e["seq"] for e in cut["events"]] == [s_all] and cut["through"] == s_all
    for path in ("/feed", "/resume"):
        assert cloud.call("GET", path).headers["cache-control"] == "no-store"


# ---- checkpoint and resume ----------------------------------------------------------------------------------------

def test_a_checkpoint_is_written_against_the_version_held(client, owner):
    mac = paired(client, owner)
    assert mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "one"}}).json() == {
        "version": 1}
    stale = mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "two"}})
    assert stale.status_code == 409 and stale.json()["version"] == 1 and stale.json()["document"] == {"doing": "one"}
    assert json.loads(row(mac.link_id)["checkpoint"]) == {"doing": "one"}
    big = {"notes": "x" * link.MAX_CHECKPOINT}
    assert mac.call("PUT", "/checkpoint", {"expected_version": 1, "document": big}).status_code == 422


def test_the_resume_document_is_bounded_ordered_and_acks_nothing(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    q = post(cloud, "question", "Q-9: which way?", to="mac").json()["seq"]
    for i in range(40):
        post(cloud, "note", f"line {i}\nwith a newline " + "y" * 150, to="mac")
    cloud.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "deploying", "notes": "z" * 3000}})
    mac.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "building", "notes": "w" * 3900}})
    mac.call("POST", "/ack", {"seq": q})            # the question is acked, and still comes back while open
    got = mac.call("GET", "/resume").json()
    doc = got["document"]
    assert len(doc) <= link.RESUME_MAX and got["checkpoint_version"] == 1
    lines = doc.split("\n")
    assert lines[0].startswith("ADE resume for mac:") and lines[1] == link.DATA_RULE and lines[-1] == link.DATA_RULE
    assert doc.index("Your checkpoint") < doc.index("Other sessions:") < doc.index("Open questions") \
        < doc.index("Unacked, newest last:")
    assert f"[{q}]" in doc[doc.index("Open questions"):doc.index("Unacked")]
    assert "with a newline" in doc and "\\n" in doc and "line 39" in doc and "older unacked" in doc
    assert row(mac.link_id)["acked_seq"] == q


# ---- reading the other sessions -----------------------------------------------------------------------------------

def test_a_session_reads_the_others_checkpoints_only_while_the_owner_lets_it(client, owner):
    mac, cloud = paired(client, owner), paired(client, owner, "cloud", "cloud")
    document = {"doing": "reviewing c0ffee42", "next": ["deploy round"], "waiting_on": "the owner's tray"}
    assert cloud.call("PUT", "/checkpoint", {"expected_version": 0, "document": document}).status_code == 200
    refused = mac.call("GET", "/others")
    assert refused.status_code == 403 and "has not let" in refused.text           # off for a new link
    view = client.get("/api/ade/link/sessions", headers=owner).json()
    assert {x["name"]: x["reads_others"] for x in view["links"]} == {"mac": False, "cloud": False}

    grant = lambda allowed, who=owner: client.put(f"/api/ade/link/links/{mac.link_id}/reads-others",  # noqa: E731
                                                  headers=who, json={"allowed": allowed})
    assert grant(True).json() == {"reads_others": True}
    got = mac.call("GET", "/others").json()
    assert [x["name"] for x in got["sessions"]] == ["cloud"]                       # the others, never its own
    assert got["sessions"][0]["checkpoint"] == document and got["rule"] == link.DATA_RULE
    assert cloud.call("GET", "/others").status_code == 403                          # the grant is mac's alone
    assert grant(True).status_code == 200                                           # the same again changes nothing
    assert grant(False).json() == {"reads_others": False}
    assert mac.call("GET", "/others").status_code == 403
    # the session is told by the owner, once for each change
    told = [e["text"] for e in mac.call("GET", "/feed").json()["events"] if e["from"] == link.OWNER]
    assert told == [link.READS_GRANTED, link.READS_WITHDRAWN]
    # only the owner's own sign-in grants it: another account's is not found, and a session's signature is no sign-in
    assert grant(True, signed_up(client, OTHER)).status_code == 404
    signed = mac.call("PUT", f"/links/{mac.link_id}/reads-others", {"allowed": True})
    assert signed.status_code == 401 and mac.call("GET", "/others").status_code == 403
    # a revoked link has nothing to grant
    assert revoke(client, owner, cloud.link_id).status_code == 200
    assert client.put(f"/api/ade/link/links/{cloud.link_id}/reads-others", headers=owner,
                      json={"allowed": True}).status_code == 404


def test_the_client_prints_the_others_between_the_rule_and_says_when_it_may_not(client, owner, tmp_path, capsys,
                                                                                  monkeypatch):
    import ade_link
    mac, cloud = paired(client, owner), paired(client, owner, "cloud", "cloud")
    note = {"doing": "line one\nline two"}
    cloud.call("PUT", "/checkpoint", {"expected_version": 0, "document": note})
    answers = {"refused": True}

    def request(self, method, path, query=None, body=None):
        assert (method, path) == ("GET", "/others")
        r = mac.call("GET", "/others")
        if r.status_code != 200:
            raise ade_link.Refused(r.status_code, r.json().get("detail", ""))
        return r.json()

    monkeypatch.setattr(ade_link.Link, "request", request)
    monkeypatch.setattr(ade_link.Link, "__init__", lambda self, directory: setattr(self, "config", {"url": "x"}))
    assert ade_link.main(["--dir", str(tmp_path), "others"]) == 2                   # refused: the owner has not let it
    client.put(f"/api/ade/link/links/{mac.link_id}/reads-others", headers=owner, json={"allowed": True})
    capsys.readouterr()
    assert ade_link.main(["--dir", str(tmp_path), "others"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == out[-1] == ade_link.RULE and len(out) == 3
    heard = link._iso(row(cloud.link_id)["last_seen_at"])          # its checkpoint's request
    assert out[1].startswith(ade_link.OTHERS_MARK + f"cloud (cloud), last heard {heard}, checkpoint version 1: ")
    assert '{"doing":"line one\\\\nline two"}' in out[1]               # the JSON's own escape, its backslash doubled


# ---- the owner's alerts ------------------------------------------------------------------------------------------

def test_silence_and_open_questions_reach_the_owner_s_phone_once_through_the_notifier(client, owner, monkeypatch):
    from test_ade_push import FCM, Device, PushService
    from proofpane_open.ade import push
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    device, service = Device(), PushService()
    # each pairing's notice (its hold is none here) goes to the same stand-in push service, before the first round
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    t0 = int(time.time())
    push.subscribe(user_id, FCM, device.p256dh, push.b64u(device.auth), now=t0)
    push._board_cache.clear()
    mac = paired(client, owner)
    paired(client, owner, "cloud", "cloud")
    assert [device.decrypt(b)["title"] for _, _, b in service.posts] == ["A session was approved to be linked"] * 2

    opened = []

    def tick(at):
        before = len(service.posts)
        push.tick(now=at, post=service, build=lambda conn: {"alerts": []})
        messages = [device.decrypt(b) for _, _, b in service.posts[before:]]
        opened.extend(m["url"] for m in messages)
        return [(m["title"], m["body"]) for m in messages]

    assert tick(t0) == []
    q_owner = post(mac, "question", "Q-5: which cap?", to="owner").json()["seq"]
    post(mac, "question", "Q-6: all of you?", to="all")
    post(mac, "question", "Q-7: only cloud", to="cloud")                        # the other session's, not the owner's
    # in fixed words: never the question or who asks it (tests/test_push_words.py)
    asked = ("A question waits for you", "Open Linked sessions to read it.")
    silent = ("A linked session needs a look", "Open Linked sessions.")
    assert tick(t0) == [asked, asked]
    assert tick(t0 + 60) == []                                                  # each once
    assert opened == ["/app/#channel"] * 2                                      # a tap opens the linked sessions
    thirty_one = t0 + 31 * 60                                                   # two of the Mac's 15-minute intervals
    assert tick(thirty_one) == [silent]
    monkeypatch.setattr(link.time, "time", lambda: thirty_one)
    assert mac.call("POST", "/heartbeat", {}, ts=thirty_one).status_code == 200
    assert tick(thirty_one + 60) == []                                          # a heartbeat ends the silence
    client.post(f"/api/ade/link/questions/{q_owner}/close", headers=owner)
    tick(thirty_one + 60)
    with store.connect() as db:
        seen = {r[0] for r in db.execute("SELECT key FROM ade_push_seen")}
    assert f"link-question:{q_owner}" not in seen and f"link-silent:{mac.link_id}" not in seen
    later = tick(thirty_one + 31 * 60)                                         # the cloud link's 30 minutes, twice
    assert later == [silent, silent]                                          # a silence that comes back is new
    with store.connect() as db:
        seen = {r[0] for r in db.execute("SELECT key FROM ade_push_seen")}
    assert {f"link-silent:{mac.link_id}"} <= seen and len([k for k in seen if k.startswith("link-silent:")]) == 2


# ---- the link routes: long lines, resumes, a new link, ids, headers and bodies -----------------------------------

def test_one_long_open_question_and_the_newest_long_line_are_cut_not_dropped(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    q = post(cloud, "question", "Q-8: " + "q" * 3990, to="mac").json()["seq"]
    newest = post(cloud, "note", "n" * 3990, to="mac").json()["seq"]
    doc = mac.call("GET", "/resume").json()["document"]
    assert len(doc) <= link.RESUME_MAX
    opened = doc[doc.index("Open questions"):doc.index("Unacked")]
    unacked = doc[doc.index("Unacked"):]
    assert f"[{q}]" in opened and link.CUT_MARK in opened
    assert f"[{newest}]" in unacked and link.CUT_MARK in unacked


def test_the_resume_counts_every_unacked_event_and_shows_the_newest_however_many(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    uid, cursor = row(mac.link_id)["user_id"], row(mac.link_id)["acked_seq"]
    many, then = 10_005, int(time.time()) - 7200     # more than the 10,000 it once read, oldest first; hours ago
    with store.connect() as db:
        db.executemany("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at) VALUES (?,?,?,?,?,?,?,?)",
                       [(uid, cloud.link_id, f"c{i}", "note", "mac", f"note {i}", int(i % 3 == 0), then)
                        for i in range(many)])
    newest = post(cloud, "note", "the newest", to="mac").json()["seq"]
    lines = mac.call("GET", "/resume").json()["document"].split("\n")
    assert lines[0] == (f"ADE resume for mac: head {newest}, cursor {cursor}, {many + 1} unacked, "
                        f"{(many + 2) // 3} need your turn.")
    unacked = lines[lines.index("Unacked, newest last:") + 1:-1]
    shown = [ln for ln in unacked if ln.startswith("[")]
    assert shown[-1].startswith(f"[{newest}] ") and shown[-1].endswith(" note cloud>mac: the newest")
    assert shown[-2].endswith(f" note cloud>mac: note {many - 1}")          # newest last, the ones before it before
    assert unacked[0] == f"... {many + 1 - len(shown)} older unacked"
    assert sum(len(ln) + 1 for ln in shown) >= link.RESUME_BUDGETS["unacked"] - link.MIN_CUT - 1   # its budget filled
    # the lines given may be only the newest of more: those not read are counted with those cut
    assert link._cut(["[2] b", "[1] a"], 100, "older unacked", 5) == ["[2] b", "[1] a", "... 3 older unacked"]
    assert link._cut(["[2] b", "[1] a"], 100, "older unacked") == ["[2] b", "[1] a"]


def test_a_new_link_starts_at_the_head_and_still_hears_the_open_questions(client, owner):
    mac = paired(client, owner)
    for i in range(10):
        post(mac, "step", f"old step {i}", to="all")
    q = post(mac, "question", "Q-9: still open?", to="all").json()["seq"]
    late = paired(client, owner, "late", "cloud")
    assert row(late.link_id)["acked_seq"] == q
    assert late.call("GET", "/feed").json()["events"] == []
    assert late.call("GET", "/feed", query={"turn": 1}).json()["events"] == []
    doc = late.call("GET", "/resume").json()["document"]
    assert f"[{q}]" in doc[doc.index("Open questions"):doc.index("Unacked")] and "old step" not in doc


def test_a_client_id_is_scanned_and_a_ticket_is_a_secret_shape_in_either_case(client, owner):
    mac = paired(client, owner)
    r = post(mac, "step", "fine", client_id="ghp_notatoken")
    assert r.status_code == 422 and "client_id" in r.json()["detail"] and "ghp_notatoken" not in r.json()["detail"]
    for text in ("a ticket pp-abcdefgh here", "PP-ABCDEFGH"):
        assert post(mac, "step", text).status_code == 422


def test_every_answer_under_the_link_routes_carries_no_store(client, owner):
    mac = paired(client, owner)
    signed_out = client.get("/api/ade/link/sessions")
    unreadable = mac.call("GET", "/feed", query={"after": "x"})
    bad_body = client.post("/api/ade/link/tickets", headers=owner, json={"link_id": 5})
    for r, status in ((signed_out, 401), (unreadable, 422), (bad_body, 422)):
        assert r.status_code == status and r.headers["cache-control"] == "no-store"
    assert client.get("/api/health").headers.get("cache-control") != "no-store"      # only the link routes


def test_a_time_in_the_future_is_refused_too(client, owner):
    mac = paired(client, owner)
    r = mac.call("GET", "/feed", ts=int(time.time()) + link.SKEW + 5)
    assert r.status_code == 401 and "server_time" in r.json()


def test_a_signature_that_does_not_check_names_the_host_the_ade_signs_for(client, owner):
    mac = paired(client, owner)
    r = mac.call("GET", "/feed", host="alias.example", headers={"Host": "alias.example"})
    assert r.status_code == 401 and HOST in r.json()["detail"]


def test_the_owner_s_close_reaches_the_asker_as_its_turn(client, owner):
    mac = paired(client, owner)
    q = post(mac, "question", "Q-10: cap?", to="owner").json()["seq"]
    note = client.post(f"/api/ade/link/questions/{q}/close", headers=owner).json()["seq"]
    (got,) = mac.call("GET", "/feed", query={"turn": 1}).json()["events"]
    assert (got["seq"], got["from"], got["to"], got["ref"], got["needs_turn"], got["text"]) == (
        note, "owner", "mac", q, True, link.CLOSED_ON_PAGE)


def test_the_page_is_given_the_address_to_pair_with(client, owner):
    assert client.post("/api/ade/link/tickets", headers=owner,
                       json={"confirmation": confirmed(client, owner)}).json()["url"] == "https://open.example.test"
    assert client.get("/api/ade/link/sessions", headers=owner).json()["public_url"] == "https://open.example.test"
    assert client.post("/api/ade/hook-tokens", headers=owner, json={"name": "mac"}).json()["url"] == \
        "https://open.example.test"


def test_a_body_nested_past_the_json_reader_s_depth_is_refused_not_an_error(client, owner):
    # such a body made passkeys' routes recurse; the link routes read their own bodies too
    mac = paired(client, owner)
    deep = b"[" * 20000 + b"]" * 20000                           # 40 KB, under Caddy's 64 KB cap
    r = mac.call("POST", "/events", sign_body=deep, raw_body=deep)
    assert r.status_code == 400 and "nests too deep" in r.json()["detail"]


def test_a_signature_header_with_anything_after_it_is_refused(client, owner):
    s = paired(client, owner)
    assert s.call("POST", "/heartbeat", {}).status_code == 200
    real = s.client.request

    def with_tail(method, url, **kw):
        kw["headers"] = dict(kw["headers"], Authorization=kw["headers"]["Authorization"] + " and more")
        return real(method, url, **kw)
    s.client = type("Tailed", (), {"request": staticmethod(with_tail)})()
    r = s.call("POST", "/heartbeat", {})
    assert r.status_code == 401 and "Sign the request" in r.json()["detail"]


# ---- each linked machine's sessions: the History list ---------------------------------------------------------------

def put_history(s, sessions):
    return s.call("PUT", "/history", {"sessions": sessions})


def test_a_link_lists_its_machine_s_sessions_and_the_owner_reads_them_newest_first(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    now = int(time.time())
    assert put_history(mac, [
        {"id": "s-old", "title": "fix the sign-in", "project": "~/proofpane", "branch": "main", "started_at": now - 900,
         "last_at": now - 600},
        {"id": "s-new", "title": "the History list", "project": "~/proofpane", "branch": None, "started_at": None,
         "last_at": now - 60}]).json() == {"listed": 2, "left_out": 0, "talks": [], "noted": [], "e2e": None}
    assert put_history(cloud, [{"id": "cse_1", "title": "a cloud task", "last_at": now - 300}]).status_code == 200
    got = client.get("/api/ade/link/history", headers=owner).json()["sessions"]
    assert [(s["id"], s["link"], s["kind"]) for s in got] == [("s-new", "mac", "local"), ("cse_1", "cloud", "cloud"),
                                                              ("s-old", "mac", "local")]
    assert got[2] == dict(got[2], title="fix the sign-in", project="~/proofpane", branch="main", started_at=now - 900)
    # a list replaces the link's last; another account sees none of it; a revoked link's list is not shown
    assert put_history(mac, [{"id": "s-new", "title": "the History list", "last_at": now}]).status_code == 200
    assert sorted(s["id"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]) == \
        ["cse_1", "s-new"]
    other = signed_up(client, OTHER)
    assert client.get("/api/ade/link/history", headers=other).json() == {"sessions": []}
    assert revoke(client, owner, cloud.link_id).status_code == 200
    assert [s["id"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]] == ["s-new"]
    assert client.get("/api/ade/link/history").status_code == 401


@pytest.mark.parametrize("bad", [
    {"id": "kept", "title": "t"},                                       # an id twice: the first stands
    {"id": "../x", "title": "t"},
    {"id": "a", "title": ""},
    {"id": "a", "title": "x" * 121},
    {"id": "a", "title": "key sk-ant-api03-x"},
    {"id": "a", "title": "a\u202eb"},
    {"id": "a", "title": "two\nlines"},
    {"id": "a", "title": "t", "project": "~/" + "p" * 300},
    {"id": "a", "title": "t", "last_at": 2 ** 40},
    {"id": "a", "title": "t", "started_at": True},
])
def test_a_session_the_server_cannot_keep_is_left_out_of_its_list_and_counted(client, owner, bad):
    """One row the server refuses is left out, not the list (a machine's newer Unicode can give one), and counted."""
    mac = paired(client, owner)
    r = put_history(mac, [{"id": "kept", "title": "kept"}, bad])
    assert r.status_code == 200 and (r.json()["listed"], r.json()["left_out"]) == (1, 1), r.text
    got = client.get("/api/ade/link/history", headers=owner).json()["sessions"]
    assert [(s["id"], s["title"]) for s in got] == [("kept", "kept")]


def test_a_list_longer_than_a_link_lists_is_refused_whole(client, owner):
    mac = paired(client, owner)
    assert put_history(mac, [{"id": "kept", "title": "kept"}]).status_code == 200
    r = put_history(mac, [{"id": f"s{i}", "title": "t"} for i in range(101)])
    assert r.status_code == 422 and "at most 100" in r.json()["detail"], r.text
    assert [s["id"] for s in client.get("/api/ade/link/history", headers=owner).json()["sessions"]] == ["kept"]


def test_the_app_reads_a_history_list_or_a_conversation_to_2_mib_and_other_link_routes_to_1(client, owner):
    """Sealed (end-to-end encryption), a History list and a conversation grow by a third: the app reads a link's PUT of
    either whole up to 2 MiB (main.MAX_BODY_HISTORY, as Caddy lets them through), every other request to 1 MiB."""
    mac = paired(client, owner)
    big = b'{"sessions": [], "x": "' + b"a" * (1536 * 1024) + b'"}'
    for path in ("/history", "/history/talk"):
        r = mac.call("PUT", path, raw_body=big)
        assert r.status_code != 413, (path, r.text)          # read whole, then the route's own answer
    too_big = b'{"sessions": [], "x": "' + b"a" * (2 * 1024 * 1024) + b'"}'
    assert mac.call("PUT", "/history", raw_body=too_big).status_code == 413
    assert mac.call("POST", "/events", raw_body=big).status_code == 413
    assert mac.call("POST", "/history", raw_body=big).status_code == 413          # PUT alone takes the larger body


# ---- what the link's server side refuses: NaN, the ADE's own client ids, a number past any seq -----------------------

def test_nan_and_the_infinities_are_refused_as_not_json(client, owner):
    mac = paired(client, owner)
    for constant in (b"NaN", b"Infinity", b"-Infinity"):
        raw_body = b'{"client_id": "c1", "kind": "note", "text": "t", "extra": ' + constant + b"}"
        r = mac.call("POST", "/events", raw_body=raw_body)
        assert r.status_code == 422 and r.json()["detail"] == "The request is JSON.", constant
    assert client.get("/api/ade/link/sessions", headers=owner).status_code == 200


def test_a_session_cannot_post_under_a_client_id_the_ade_writes_itself(client, owner):
    mac = paired(client, owner)
    r = post(mac, "note", "t", client_id="take:5")
    assert r.status_code == 422 and "take: is the ADE's own" in r.json()["detail"]
    assert post(mac, "note", "t", client_id="taken:5").status_code == 200


def test_a_number_past_any_seq_is_refused_or_reads_nothing_never_a_500(client, owner):
    mac = paired(client, owner)
    big = 2 ** 64
    r = post(mac, "note", "t", ref=big)
    assert r.status_code == 422 and "refers to an event" in r.json()["detail"]
    assert client.post(f"/api/ade/link/questions/{big}/close", headers=owner).status_code == 409
    r = mac.call("GET", "/feed", query={"after": str(big)})
    assert r.status_code == 200 and r.json()["events"] == []
    r = client.get(f"/api/ade/link/timeline/{mac.link_id}?before={big}", headers=owner)
    assert r.status_code == 200
