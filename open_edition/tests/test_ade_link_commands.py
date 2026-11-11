"""The session channel's commands (stage 2a), through the app: the owner signs a payload with a passkey of the tests'
own software authenticator, the server checks it before storing, the addressed session takes it once, and the
owner's stop holds until a resume the owner signs. Payloads are canonicalised here with the open core's
canonicaliser, the challenge is their sha256, and the authenticator answers as a platform one does."""

import hashlib
import json
import sys
import time
import unicodedata
import uuid

import pytest

from conftest import signed_up
from proofpane_open import passkeys, store
from proofpane_open.ade import link, link_commands      # puts the open packages on sys.path first
from proofpane_core.canonical import canonicalize
from test_ade_link import HOST, OTHER, Session, b64u, confirmed, paired, post, revoke, ticket
from test_passkeys import Authenticator, add, as_step

DROP = object()


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def device(client, owner):
    d = Authenticator()
    r = add(client, owner, d)
    assert r.status_code == 200, r.text
    return d


def account(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def nonce(client, owner, purpose):
    r = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": purpose})
    assert r.status_code == 200, r.text
    return r.json()["nonce"]


def payload(client, owner, purpose, to, text="run the tests", **over):
    """A payload as the page composes it; `over` replaces a field, or drops it (DROP)."""
    now = int(time.time())
    given = over.pop("nonce", None)
    p = {"v": 2, "deployment": HOST, "purpose": purpose, "account": account(client, owner),
         "nonce": given if given not in (None, DROP) else nonce(client, owner, purpose), "id": uuid.uuid4().hex,
         "to": to, "text": "" if purpose == "resume" else text, "created_at": now,
         "expires_at": now + (600 if purpose == "resume" else 3600)}
    if given is DROP:
        del p["nonce"]
    for key, value in over.items():
        if value is DROP:
            p.pop(key, None)
        else:
            p[key] = value
    return p


def assertion(device, signed, **kw):
    """The device's answer to the step challenge of `signed`'s canonical bytes, as the page asks for it."""
    return device.get(as_step(canonicalize(signed)), **kw)


def send(client, owner, device, p, signed=None, path="/api/ade/link/commands", **kw):
    return client.post(path, headers=owner, json={"payload": p, "credential": assertion(device, signed or p, **kw)})


def command(client, owner, device, to, text="run the tests", **over):
    p = payload(client, owner, "instruction", to, text, **over)
    r = send(client, owner, device, p)
    assert r.status_code == 200, r.text
    return r.json()["seq"], p


def event_row(seq):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_link_events WHERE seq = ?", (seq,)).fetchone())


def link_row(link_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM ade_links WHERE id = ?", (link_id,)).fetchone())


def take(session, seq, instance="i1"):
    return session.call("POST", "/take", {"seq": seq, "instance": instance})


# ---- a signed command ---------------------------------------------------------------------------------------------

def test_a_signed_instruction_is_stored_as_signed_and_reaches_only_its_session(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    seq, p = command(client, owner, device, mac.link_id)
    row = event_row(seq)
    assert row["payload"].encode("utf-8") == canonicalize(p)
    assert json.loads(row["signature"])["id"] == b64u(device.credential_id)
    assert (row["kind"], row["link_id"], row["to_id"], row["to_name"], row["needs_turn"], row["client_id"]) == \
        ("instruction", "owner", mac.link_id, "mac", 1, p["id"])
    feed = mac.call("GET", "/feed", query={"turn": 1}).json()["events"]
    assert [(e["seq"], e["kind"], e["state"]) for e in feed] == [(seq, "instruction", "queued")]
    assert cloud.call("GET", "/feed").json()["events"] == []
    got = mac.call("GET", f"/events/{seq}").json()
    assert got["payload"] == row["payload"] and got["signature"]["id"] == b64u(device.credential_id)
    assert cloud.call("GET", f"/events/{seq}").status_code == 404


def test_the_nonce_names_what_the_payload_carries(client, owner):
    r = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "instruction"}).json()
    assert (r["rp_id"], r["deployment"], r["account"]) == (HOST, HOST, account(client, owner))
    assert client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "signin"}).status_code == 422
    assert client.post("/api/ade/link/nonce", json={"purpose": "instruction"}).status_code == 401


@pytest.mark.parametrize("kw, status, words", [
    ({"origin": "https://other.example.test"}, 400, "another site"),
    ({"crossOrigin": True}, 400, "another site"),
    ({"topOrigin": "https://open.example.test"}, 400, "another site"),
    ({"kind": "webauthn.create"}, 400, "another ceremony"),
    ({"flags": 0x01}, 400, "did not verify you"),
])
def test_an_assertion_that_does_not_hold_is_refused_naming_the_check(client, owner, device, kw, status, words):
    mac = paired(client, owner)
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id), **kw)
    assert r.status_code == status and words in r.json()["detail"], r.text
    assert event_rows(mac.link_id) == []


def event_rows(link_id):
    with store.connect() as db:
        return db.execute("SELECT seq FROM ade_link_events WHERE to_id = ?", (link_id,)).fetchall()


def test_a_challenge_over_other_bytes_is_the_canonical_form_refused(client, owner, device):
    mac = paired(client, owner)
    p = payload(client, owner, "instruction", mac.link_id, "run the tests")
    r = send(client, owner, device, p, signed=dict(p, text="push to main"))
    assert r.status_code == 400 and r.json()["detail"] == "the page and the server do not agree on the canonical form"


def test_another_site_s_relying_party_is_refused(client, owner, device):
    mac = paired(client, owner)
    device.rp = "other.example.test"
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 400 and "for another site" in r.json()["detail"]


def test_the_passkey_must_be_this_account_s_and_must_have_signed(client, owner, device):
    mac = paired(client, owner)
    other = signed_up(client, OTHER)
    stranger = Authenticator(alg=-257)
    assert add(client, other, stranger).status_code == 200
    r = send(client, owner, stranger, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 401 and r.json()["detail"] == "the credential is not one of this account's passkeys"
    # the account's own credential named, the signature another passkey's of the same account
    second = Authenticator(alg=-8)
    assert add(client, owner, second, confirm_with=device).status_code == 200
    p = payload(client, owner, "instruction", mac.link_id)
    cred = assertion(second, p)
    cred["id"] = b64u(device.credential_id)
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": cred})
    assert r.status_code == 401 and r.json()["detail"] == "the signature does not verify with that passkey"


def test_a_counter_that_goes_back_is_refused(client, owner):
    counted = Authenticator(alg=-8, counter=5)
    assert add(client, owner, counted).status_code == 200
    mac = paired(client, owner)
    command(client, owner, counted, mac.link_id)
    r = send(client, owner, counted, payload(client, owner, "instruction", mac.link_id), count=3)
    assert r.status_code == 401 and "counter went back" in r.json()["detail"]


def test_a_counter_value_is_used_once(client, owner):
    counted = Authenticator(alg=-8, counter=5)
    assert add(client, owner, counted).status_code == 200
    mac = paired(client, owner)
    assert send(client, owner, counted, payload(client, owner, "instruction", mac.link_id), count=20).status_code == 200
    r = send(client, owner, counted, payload(client, owner, "instruction", mac.link_id), count=20)
    assert r.status_code == 401 and "counter went back" in r.json()["detail"]


def test_a_nonce_is_this_server_s_for_this_account_and_purpose_and_used_once(client, owner, device, monkeypatch):
    mac = paired(client, owner)
    other = signed_up(client, OTHER)
    p = payload(client, owner, "instruction", mac.link_id)
    assert send(client, owner, device, p).status_code == 200
    again = send(client, owner, device, dict(p, id=uuid.uuid4().hex))
    assert again.status_code == 400 and again.json()["detail"] == "the nonce was used already"
    theirs = payload(client, owner, "instruction", mac.link_id, nonce=nonce(client, other, "instruction"))
    assert "is not one this server gave" in send(client, owner, device, theirs).json()["detail"]
    wrong_purpose = payload(client, owner, "instruction", mac.link_id, nonce=nonce(client, owner, "answer"))
    assert send(client, owner, device, wrong_purpose).status_code == 400
    forged = payload(client, owner, "instruction", mac.link_id, nonce="eyJjIjoiIn0.AAAA")
    assert send(client, owner, device, forged).status_code == 400
    old = payload(client, owner, "instruction", mac.link_id)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 301)
    old.update(created_at=int(real() + 301), expires_at=int(real() + 3901))
    assert "or it expired" in send(client, owner, device, old).json()["detail"]


@pytest.mark.parametrize("over, status, words", [
    ({"nonce": DROP}, 422, "missing nonce"),
    ({"extra": "x"}, 422, "not expected extra"),
    ({"v": 3}, 422, "v is 2"),
    ({"v": True}, 422, "v is 2"),
    ({"deployment": "other.example.test"}, 422, "another deployment"),
    ({"account": "someone"}, 403, "another account"),
    ({"id": "ABC"}, 422, "32 lowercase hex"),
    ({"to": "0" * 32}, 422, "not one of this account's live links"),
    # each in its own form, before anything reads it: no control sequence a terminal or a log would act on
    ({"to": "no-such-link"}, 422, "to is a linked session's id"),
    ({"to": "T" + chr(0x1B) + "[1;31m EVIL"}, 422, "to is a linked session's id"),
    ({"to": "0" * 32 + "\n"}, 422, "to is a linked session's id"),
    ({"nonce": "N" + chr(0x1B) + "[2J" + chr(0x1B) + "[H N"}, 422, "nonce is the one POST /nonce gave"),
    ({"nonce": "abc"}, 422, "nonce is the one POST /nonce gave"),
    ({"nonce": "a.b.c"}, 422, "nonce is the one POST /nonce gave"),
    ({"nonce": "eyJ.AAAA\n"}, 422, "nonce is the one POST /nonce gave"),
    ({"nonce": "A" * 600 + ".AAAA"}, 422, "nonce is the one POST /nonce gave"),
])
def test_a_payload_field_that_does_not_hold_is_refused(client, owner, device, over, status, words):
    mac = paired(client, owner)
    over = dict(over)
    to = over.pop("to", mac.link_id)
    r = send(client, owner, device, payload(client, owner, "instruction", to, **over))
    assert r.status_code == status and words in r.json()["detail"], r.text


@pytest.mark.parametrize("offset, life, ok", [
    (301, 3600, False), (-301, 3600, False), (299, 3600, False), (40, 3600, False), (-3, 3600, False),
    (25, 3600, True), (0, 59, False), (0, 60, True), (0, 86400, True), (0, 86401, False),
])
def test_the_page_s_clock_and_the_command_s_life_are_bounded(client, owner, device, monkeypatch, offset, life, ok):
    mac = paired(client, owner)
    t0 = int(time.time())
    # the server's clock held at t0: a request that takes a second would otherwise move 301 to 300 and the bound with it
    monkeypatch.setattr(link_commands, "_now", lambda now: int(now if now is not None else t0))
    created = t0 + offset
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, created_at=created,
                                            expires_at=created + life))
    assert (r.status_code == 200) == ok, r.text
    if not ok:
        assert r.status_code == 422


@pytest.mark.parametrize("text, words", [
    ("a\rb", "control character (U+000D)"),
    ("a\x7fb", "control character (U+007F)"),
    ("a\u202eb", "U+202E"),
    ("a\u200bb", "U+200B"),
    ("a\ufeffb", "U+FEFF"),
    ("a\ud800b", "surrogate"),
    ("e\u0301", "normal form C"),
    ("x" * 4001, "at most 4000"),
    ("  ", "not empty"),
    ("key sk-ant-xyz", "an Anthropic key"),
    ("e2e1.AAAA", "begins e2e1."),                      # read everywhere as sealed, which a command never is
    ("do no" + chr(0x0336) + "t merge", "U+0336"),       # a struck "not" reads as nothing struck
    ("keep it" + chr(0x20E0), "U+20E0"),                 # a prohibition sign drawn around it
])
def test_a_command_s_words_are_held_to_the_text_rules(client, owner, device, text, words):
    mac = paired(client, owner)
    p = payload(client, owner, "instruction", mac.link_id, text)
    if words == "surrogate":
        # a lone surrogate has no canonical form to sign, nor a UTF-8 one to send as is: the device signs other words
        # and the body escapes it, as JSON may; the server refuses it before anything else
        body = json.dumps({"payload": p, "credential": assertion(device, dict(p, text="x"))}).encode("ascii")
        r = client.post("/api/ade/link/commands", headers=dict(owner, **{"Content-Type": "application/json"}),
                        content=body)
        assert r.status_code == 400 and "surrogate" in r.json()["detail"], r.text      # the app's own guard
        return
    r = send(client, owner, device, p)
    assert r.status_code == 422 and words in r.json()["detail"], r.text


def test_newlines_tabs_and_any_language_are_words(client, owner, device):
    mac = paired(client, owner)
    text = "line one\n\tline two " + "".join(chr(c) for c in (0x8BD5, 0x4E00, 0x4E0B)) + " \xe9"
    seq, p = command(client, owner, device, mac.link_id, text)
    assert json.loads(event_row(seq)["payload"])["text"] == text


def test_the_owner_s_budget_is_counted_by_account_and_the_queue_by_link(client, owner, device):
    mac = paired(client, owner)
    uid = account(client, owner)
    now = int(time.time())
    with store.connect() as db:
        for i in range(link_commands.QUEUED_PER_LINK):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at, to_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", f"q{i}", "instruction", "mac", "t", 1, now - 7200, mac.link_id, now + 3600))
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 429 and "at most 50 commands" in r.json()["detail"]
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET withdrawn_at = ? WHERE to_id = ?", (now, mac.link_id))
        for i in range(link_commands.OWNER_PER_HOUR - 1):
            db.execute("INSERT INTO ade_link_events (user_id, link_id, client_id, kind, to_name, text, needs_turn, "
                       "received_at, to_id, expires_at, withdrawn_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (uid, "owner", f"h{i}", "instruction", "mac", "t", 1, now, mac.link_id, now + 3600, now))
    # the 60th in the hour is taken (the links' wake cap of 12 is not the owner's), the 61st is not
    assert send(client, owner, device, payload(client, owner, "instruction", mac.link_id)).status_code == 200
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id))
    assert r.status_code == 429 and "60 commands an hour" in r.json()["detail"]


def test_a_payload_or_an_answer_too_large_is_refused(client, owner, device):
    mac = paired(client, owner)
    p = payload(client, owner, "instruction", mac.link_id)
    cred = assertion(device, p)
    cred["response"]["padding"] = "x" * 9000
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": cred})
    assert r.status_code == 422 and "at most 8192 bytes" in r.json()["detail"]


# ---- taking it ------------------------------------------------------------------------------------------------------

def test_the_first_take_wins_and_a_retry_by_the_same_instance_is_the_same(client, owner, device):
    mac = paired(client, owner)
    seq, p = command(client, owner, device, mac.link_id)
    first = take(mac, seq)
    assert first.status_code == 200 and first.json()["seq"] == seq
    again = take(mac, seq)
    assert again.status_code == 200 and again.json()["taken_at"] == first.json()["taken_at"]
    row = event_row(seq)
    assert (row["taken_by"], row["taken_instance"]) == (mac.link_id, "i1")
    with store.connect() as db:
        took = db.execute("SELECT * FROM ade_link_events WHERE kind = 'take' AND ref = ?", (seq,)).fetchall()
    assert len(took) == 1 and took[0]["text"] == "sha256:" + hashlib.sha256(canonicalize(p)).hexdigest()
    assert took[0]["link_id"] == mac.link_id and took[0]["to_name"] == "owner"
    assert mac.call("GET", "/feed", query={"turn": 1}).json()["events"][0]["state"] == "taken"


def test_a_second_instance_of_the_same_link_is_refused_and_raises_the_conflict(client, owner, device):
    mac = paired(client, owner)
    seq, _ = command(client, owner, device, mac.link_id)
    assert take(mac, seq, "i1").status_code == 200
    r = take(mac, seq, "i2")
    assert r.status_code == 409 and r.json()["why"] == "taken" and r.json()["taken_by"] == mac.link_id
    row = event_row(seq)
    assert row["conflict_at"] is not None and row["conflict_instance"] == "i2"
    told = dict(link.alerts(account(client, owner)))
    assert told[f"link-take-conflict:{seq}"] == "link-look"           # in fixed words, never the link's name


def test_only_the_addressed_session_takes_and_never_a_stale_or_withdrawn_command(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    seq, _ = command(client, owner, device, mac.link_id)
    assert take(cloud, seq).status_code == 404
    late, _ = command(client, owner, device, mac.link_id)
    with store.connect() as db:
        db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?", (int(time.time()) - 1, late))
    r = take(mac, late)
    assert r.status_code == 409 and r.json()["why"] == "expired"
    gone, _ = command(client, owner, device, mac.link_id)
    assert client.post(f"/api/ade/link/commands/{gone}/withdraw", headers=owner).status_code == 200
    r = take(mac, gone)
    assert r.status_code == 409 and r.json()["why"] == "withdrawn"
    q = post(mac, "question", "may I?", to="owner").json()["seq"]
    assert take(mac, q).status_code == 404
    assert take(mac, seq).status_code == 200
    w = client.post(f"/api/ade/link/commands/{seq}/withdraw", headers=owner)
    assert w.status_code == 409 and w.json()["state"] == "taken"


def test_a_reply_closes_a_taken_command_and_working_renews_it(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    seq, _ = command(client, owner, device, mac.link_id)
    assert post(mac, "done", "done: x", to="owner", ref=seq).status_code == 422      # not taken yet
    assert take(mac, seq).status_code == 200
    assert post(cloud, "done", "done: x", to="owner", ref=seq).status_code == 422    # taken by another
    assert post(mac, "waiting", "still on it", to="owner", ref=seq).status_code == 200
    row = event_row(seq)
    assert row["renewed_at"] is not None and row["closed_by"] is None
    reply = post(mac, "done", "done: the tests pass", to="owner", ref=seq).json()["seq"]
    assert event_row(seq)["closed_by"] == reply
    cards = {c["id"]: c for c in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert cards[mac.link_id]["commands"][0]["state"] == "answered" and cards[cloud.link_id]["commands"] == []


def test_the_same_copy_is_given_a_take_again_only_before_it_said_anything_of_the_command(client, owner, device):
    """A retry from the copy that took a command is its own lost answer only while that copy has said nothing of the
    command; once it renewed the take or answered it, it had the take, and a copy asking again has lost its record of
    it."""
    mac = paired(client, owner)
    renewed, _ = command(client, owner, device, mac.link_id)
    answered, _ = command(client, owner, device, mac.link_id)
    for seq in (renewed, answered):
        assert take(mac, seq).status_code == 200
        assert take(mac, seq).json().get("retry") is True
    assert post(mac, "waiting", "still on it", to="owner", ref=renewed).status_code == 200
    assert post(mac, "done", "done: x", to="owner", ref=answered).status_code == 200
    for seq, why in ((renewed, "taken"), (answered, "answered")):
        r = take(mac, seq)
        assert r.status_code == 409 and r.json()["why"] == why and r.json()["taken_by"] == mac.link_id
        assert event_row(seq)["conflict_at"] is None                 # the same copy: no second copy to tell of


def test_the_same_copy_is_given_a_lost_take_again_once(client, owner, device):
    """A copy restored in place after its answer was lost asks again as the same copy: given once, refused after."""
    mac = paired(client, owner)
    seq, _ = command(client, owner, device, mac.link_id)
    first = take(mac, seq)
    again = take(mac, seq)
    assert first.status_code == 200 and again.json() == {"seq": seq, "taken_at": first.json()["taken_at"], "retry": True}
    third = take(mac, seq)
    assert third.status_code == 409 and third.json()["why"] == "taken" and "once already" in third.json()["detail"]
    assert event_row(seq)["retried_at"] is not None and event_row(seq)["conflict_at"] is None


# ---- an answer ------------------------------------------------------------------------------------------------------

def test_an_answer_names_an_open_question_by_the_digest_of_its_words(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    q = post(mac, "question", "May I delete the old branch?", to="owner").json()["seq"]
    digest = link_commands.text_digest("May I delete the old branch?")
    wrong = payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=link_commands.text_digest("other"))
    r = send(client, owner, device, wrong)
    assert r.status_code == 409 and "digest" in r.json()["detail"]
    elsewhere = payload(client, owner, "answer", cloud.link_id, "yes", ref=q, digest=digest)
    assert send(client, owner, device, elsewhere).status_code == 409
    p = payload(client, owner, "answer", mac.link_id, "yes, delete it", ref=q, digest=digest)
    r = send(client, owner, device, p)
    assert r.status_code == 200, r.text
    assert event_row(q)["closed_by"] == r.json()["seq"]
    again = payload(client, owner, "answer", mac.link_id, "no", ref=q, digest=digest)
    assert send(client, owner, device, again).status_code == 409
    assert take(mac, r.json()["seq"]).status_code == 200


def test_a_question_s_words_are_held_to_the_text_rules(client, owner):
    mac = paired(client, owner)
    r = post(mac, "question", "a\u202eb", to="owner")
    assert r.status_code == 422 and "U+202E" in r.json()["detail"]
    assert post(mac, "step", "a\u202eb").status_code == 200        # a step is shown as it came, as before


# ---- stop and resume ------------------------------------------------------------------------------------------------

def test_a_stop_holds_takes_until_a_resume_signed_after_it(client, owner, device):
    mac = paired(client, owner)
    seq, _ = command(client, owner, device, mac.link_id)
    stopped = client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner)
    assert stopped.status_code == 200
    t = stopped.json()["stopped_at"]
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).json()["stopped_at"] == t
    r = take(mac, seq)
    assert r.status_code == 409 and r.json()["why"] == "stopped"
    state = mac.call("GET", "/state").json()
    assert state["stopped"] and state["stopped_at"] == t and state["resume"] is None
    early = payload(client, owner, "resume", mac.link_id, created_at=t, expires_at=t + 600)
    r = send(client, owner, device, early, path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert r.status_code == 409 and "before the stop" in r.json()["detail"]
    p = payload(client, owner, "resume", mac.link_id, created_at=t + 1, expires_at=t + 601)
    r = send(client, owner, device, p, path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert r.status_code == 200, r.text
    state = mac.call("GET", "/state").json()
    assert not state["stopped"] and state["resume"]["payload"].encode("utf-8") == canonicalize(p)
    assert state["resume"]["signature"]["id"] == b64u(device.credential_id)
    assert take(mac, seq).status_code == 200
    r = send(client, owner, device, p, path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert r.status_code == 409                                      # not stopped now
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).status_code == 200
    reused = send(client, owner, device, dict(p, id=uuid.uuid4().hex),
                  path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert reused.status_code in (400, 409)
    assert mac.call("GET", "/state").json()["resume"] is None        # a new stop drops the old resume


def test_a_resume_is_a_resume_of_this_link_and_a_stop_needs_only_the_sign_in(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    t = client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).json()["stopped_at"]
    other = payload(client, owner, "resume", cloud.link_id, created_at=t + 1, expires_at=t + 601)
    assert send(client, owner, device, other, path=f"/api/ade/link/links/{mac.link_id}/resume").status_code == 422
    long = payload(client, owner, "resume", mac.link_id, created_at=t + 1, expires_at=t + 602)
    assert send(client, owner, device, long, path=f"/api/ade/link/links/{mac.link_id}/resume").status_code == 422
    texted = dict(payload(client, owner, "resume", mac.link_id, created_at=t + 1, expires_at=t + 601), text="go")
    assert send(client, owner, device, texted, path=f"/api/ade/link/links/{mac.link_id}/resume").status_code == 422
    unsigned = client.post(f"/api/ade/link/links/{mac.link_id}/resume", headers=owner, json={})
    assert unsigned.status_code == 422
    other_owner = signed_up(client, OTHER)
    assert client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=other_owner).status_code == 404


def test_the_session_acknowledges_the_stop_it_saw_once(client, owner):
    mac = paired(client, owner)
    t = client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).json()["stopped_at"]
    assert post(mac, "note", "stopped", client_id="stopped:1").status_code == 200
    assert link_row(mac.link_id)["stop_acked_at"] is None
    first = post(mac, "note", f"stopped at {t}", client_id=f"stopped:{t}").json()["seq"]
    acked = link_row(mac.link_id)["stop_acked_at"]
    assert acked is not None
    assert post(mac, "note", f"stopped at {t}", client_id=f"stopped:{t}").json()["seq"] == first


# ---- the session's own reads -----------------------------------------------------------------------------------------

def test_the_owner_keys_are_the_account_s_passkeys_with_their_fingerprints(client, owner, device):
    mac = paired(client, owner)
    keys = mac.call("GET", "/owner-keys").json()
    assert keys["account"] == account(client, owner)
    (k,) = keys["passkeys"]
    assert k["credential_id"] == b64u(device.credential_id) and k["alg"] == -7
    spki = base64_der(k["public_key"])
    assert k["fingerprint"] == hashlib.sha256(spki).hexdigest()[:32]
    listed = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert listed[0]["fingerprint"] == k["fingerprint"] and "public_key" not in listed[0]
    assert client.get("/api/ade/link/owner-keys", headers=owner).status_code == 401


def base64_der(text):
    import base64
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_a_command_is_delivered_by_the_link_s_id_never_to_a_name_taken_again(client, owner, device):
    mac = paired(client, owner)
    seq, _ = command(client, owner, device, mac.link_id)
    assert revoke(client, owner, mac.link_id).status_code == 200
    again = paired(client, owner, name="mac")
    assert again.call("GET", "/feed").json()["events"] == []
    assert again.call("GET", "/feed", query={"after": 0}).json()["events"] == []     # not only past its cursor
    assert again.call("GET", f"/events/{seq}").status_code == 404
    assert take(again, seq).status_code == 404


def test_resume_lists_the_commands_a_session_holds_by_seq_only(client, owner, device):
    mac = paired(client, owner)
    held, _ = command(client, owner, device, mac.link_id, "taken words alpha")
    queued, _ = command(client, owner, device, mac.link_id, "second, secret words")
    assert take(mac, held).status_code == 200
    doc = mac.call("GET", "/resume").json()["document"]
    assert f"[{held}] instruction, taken by you" in doc and f"[{queued}] instruction, queued" in doc
    assert "secret words" not in doc and "words alpha" not in doc    # in no part of it, the unacked events included
    assert f"(a command from the owner: take {queued} verifies it and prints the words to act on; show {queued} " \
        "previews)" in doc
    assert len(doc) <= link.RESUME_MAX


# ---- what the owner is told ---------------------------------------------------------------------------------------

def test_the_owner_is_told_of_commands_not_taken_taken_and_silent_and_stops_not_acknowledged(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    uid = account(client, owner)
    local_seq, _ = command(client, owner, device, mac.link_id)
    cloud_seq, _ = command(client, owner, device, cloud.link_id)
    t_local, t_cloud = event_row(local_seq)["received_at"], event_row(cloud_seq)["received_at"]
    keys = lambda at: [k for k, _ in link.alerts(uid, at)]  # noqa: E731
    assert f"link-untaken:{local_seq}" not in keys(t_local + 299)
    assert f"link-untaken:{local_seq}" in keys(t_local + 300) and f"link-untaken:{cloud_seq}" not in keys(t_local + 300)
    # each told as a linked session that needs a look: never the link's name or the command's seq (push.words)
    assert dict(link.alerts(uid, t_local + 300))[f"link-untaken:{local_seq}"] == "link-look"
    assert f"link-untaken:{cloud_seq}" not in keys(t_cloud + 1799) and f"link-untaken:{cloud_seq}" in keys(t_cloud + 1800)
    assert take(mac, local_seq).status_code == 200
    taken = event_row(local_seq)["taken_at"]
    assert f"link-untaken:{local_seq}" not in keys(t_local + 300)
    assert f"link-stale:{local_seq}" not in keys(taken + 1799) and f"link-stale:{local_seq}" in keys(taken + 1800)
    assert dict(link.alerts(uid, taken + 1800))[f"link-stale:{local_seq}"] == "link-look"
    with store.connect() as db:          # taken 1000 s ago, so a renewal now is not the take's own second
        db.execute("UPDATE ade_link_events SET taken_at = taken_at - 1000 WHERE seq = ?", (local_seq,))
    post(mac, "waiting", "on it", to="owner", ref=local_seq)
    renewed = event_row(local_seq)["renewed_at"]
    assert renewed - event_row(local_seq)["taken_at"] >= 1000
    assert f"link-stale:{local_seq}" not in keys(renewed + 1799) and f"link-stale:{local_seq}" in keys(renewed + 1800)
    # told of until a day after its life ended, then no more (the alert does not run for ever)
    lapsed = event_row(local_seq)["expires_at"]
    assert f"link-stale:{local_seq}" in keys(lapsed + link_commands.TAKEN_SHOWN - 1)
    assert f"link-stale:{local_seq}" not in keys(lapsed + link_commands.TAKEN_SHOWN)
    t = client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).json()["stopped_at"]
    assert f"link-stop:{mac.link_id}" in keys(t + 121) and f"link-stop:{mac.link_id}" not in keys(t + 119)
    assert dict(link.alerts(uid, t + 121))[f"link-stop:{mac.link_id}"] == "link-look"
    post(mac, "note", "stopped", client_id=f"stopped:{t}")
    assert f"link-stop:{mac.link_id}" not in keys(t + 121)
    client.post(f"/api/ade/link/links/{cloud.link_id}/stop", headers=owner)
    assert f"link-stop:{cloud.link_id}" not in keys(t + 3600)          # a cloud stop is a request, not paged


# ---- link changes take the person's own word (2a-0) ---------------------------------------------------------------

def test_a_ticket_a_re_pair_and_a_revoke_each_use_up_a_confirmation(client, owner, device):
    mac = paired(client, owner)
    bare = client.post("/api/ade/link/tickets", headers=owner, json={})
    assert bare.status_code == 403 and bare.json()["detail"] == link.LINK_NEEDS_CONFIRM
    used = confirmed(client, owner)
    assert client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": used}).status_code == 200
    again = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": used})
    assert again.status_code == 403 and "expired or was used" in again.json()["detail"]
    re_pair = client.post("/api/ade/link/tickets", headers=owner, json={"link_id": mac.link_id})
    assert re_pair.status_code == 403
    assert revoke(client, owner, mac.link_id, confirm=False).status_code == 403
    assert link_row(mac.link_id)["revoked_at"] is None
    # a revoke of a link that is not there leaves the confirmation unused: refused for it again as missing, never used
    missing = "e" * 32
    kept = confirmed(client, owner, f"link-revoke:{missing}")
    for _ in range(2):
        r = client.request("DELETE", f"/api/ade/link/links/{missing}", headers=owner, json={"confirmation": kept})
        assert r.status_code == 404
    # made for revoking that link, it revokes no other
    r = client.request("DELETE", f"/api/ade/link/links/{mac.link_id}", headers=owner, json={"confirmation": kept})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    assert link_row(mac.link_id)["revoked_at"] is None
    r = client.request("DELETE", f"/api/ade/link/links/{mac.link_id}", headers=owner,
                       json={"confirmation": confirmed(client, owner, f"link-revoke:{mac.link_id}")})
    assert r.status_code == 200


def test_a_ticket_s_confirmation_is_made_for_that_ticket_and_no_other_link_change_s_makes_one(client, owner):
    """A ticket takes a confirmation made for a ticket, for a new session or to re-pair
    that one link; one made for revoking a link, for approving a request, for another link's re-pair or for a new
    session's ticket where a re-pair is asked (or the other way) makes no ticket and is left unused, and what is left
    unused still makes the ticket it was made for."""
    mac = paired(client, owner)
    other = "d" * 32
    made_for = {change: confirmed(client, owner, change) for change in (
        f"link-revoke:{mac.link_id}", f"link-approve:{other}", f"link-ticket:{other}", "link-ticket:new",
        f"link-ticket:{mac.link_id}", "sign-out-everywhere")}
    wrong = {None: (f"link-revoke:{mac.link_id}", f"link-approve:{other}", f"link-ticket:{other}",
                    f"link-ticket:{mac.link_id}", "sign-out-everywhere"),
             mac.link_id: (f"link-revoke:{mac.link_id}", f"link-ticket:{other}", "link-ticket:new")}
    for link_id, changes in wrong.items():
        for change in changes:
            body = {"confirmation": made_for[change], **({"link_id": link_id} if link_id else {})}
            r = client.post("/api/ade/link/tickets", headers=owner, json=body)
            assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER, (link_id, change)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_tickets WHERE used_at IS NULL").fetchone()[0] == 0
    assert client.post("/api/ade/link/tickets", headers=owner,
                       json={"confirmation": made_for["link-ticket:new"]}).status_code == 200
    assert client.post("/api/ade/link/tickets", headers=owner,
                       json={"confirmation": made_for[f"link-ticket:{mac.link_id}"],
                             "link_id": mac.link_id}).status_code == 200
    # one made for nothing in particular, as a page from an older release makes it, is told to reload
    plain = client.post("/api/account/confirm", headers=owner, json={"password": "correct horse battery"}).json()
    r = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": plain["confirmation"]})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FOR_NOTHING


def test_an_account_with_neither_a_password_nor_a_passkey_is_only_asked_to_go_ahead(client):
    owner = signed_up(client, OTHER)
    with store.connect() as db:
        db.execute("UPDATE users SET password_hash = '' WHERE email = ?", (OTHER,))
    assert client.post("/api/ade/link/tickets", headers=owner, json={}).status_code == 200


# ---- characters not shown as themselves ------------------------------------------------

@pytest.mark.parametrize("code", [0xE0041, 0xE0001, 0x00AD, 0x200E, 0x200F, 0x061C, 0x2062, 0xFE00, 0xFE0D, 0xE0100,
                                  0xE000, 0xF0000, 0x202E, 0x2066, 0x200B, 0xFEFF])
def test_a_command_refuses_a_character_not_shown_as_itself(code):
    with pytest.raises(link.LinkError) as e:
        link_commands.check_text("run the tests" + chr(code) + " now", "command")
    assert f"U+{code:04X}" in str(e.value)


# ---- marks drawn over or around a character --------------------------------------------------------------------------

@pytest.mark.parametrize("code", [0x0336, 0x0337, 0x0338, 0x20DD, 0x20DE, 0x20DF, 0x20E0, 0x20E2, 0x20E3, 0x20E4,
                                  0x0334, 0x0335, 0x20D2, 0x20E5, 0x20EA, 0x0488, 0x1ABE, 0xA670, 0x1D167])
def test_a_command_refuses_a_mark_drawn_over_or_around_a_character(code):
    """An overlay (a stroke or a solidus through the character, so "not" reads struck and "x" crossed) or an enclosing
    mark (a circle, a prohibition sign, a keycap) changes how a line reads, yet passes normal form C."""
    text = "run the tests x" + chr(code) + " now"
    assert unicodedata.normalize("NFC", text) == text
    with pytest.raises(link.LinkError) as e:
        link_commands.check_text(text, "command")
    assert f"U+{code:04X}" in str(e.value) and "drawn over or around" in str(e.value)


def test_the_marks_drawn_over_are_unicode_s_overlays_and_enclosing_marks():
    """The list the server and the page hold is Unicode's own (combining class 1, category Me), so neither misses one
    the other refuses; an ordinary mark, above or below, is no such mark."""
    wanted = {c for c in range(sys.maxunicode + 1) if unicodedata.combining(chr(c)) == 1
              or unicodedata.category(chr(c)) == "Me"}
    assert link_commands.DRAWN_OVER == wanted
    for ordinary in (0x0301, 0x0323, 0x20E1, 0x0E48):       # acute, dot below, an arrow above, a Thai tone mark
        assert link_commands.check_text("x" + chr(ordinary) + " now", "command")


def test_marks_are_stacked_only_on_one_character():
    """stacked counts the non-spacing marks on each character apart: a run ends at the next character."""
    acute, dot = chr(0x0301), chr(0x0323)
    assert link_commands.stacked("a" + acute + "b" + acute + dot) is None
    assert link_commands.stacked("a" + dot + acute + acute + " b") == ord("a")
    assert link_commands.stacked(acute * 3 + "a") == 0x0301
    assert link_commands.stacked("a" + dot + acute, most=1) == ord("a")


def test_a_question_holding_a_mark_drawn_over_is_refused(client, owner):
    mac = paired(client, owner)
    r = post(mac, "question", "may I merge" + chr(0x0338) + "?", to="owner")
    assert r.status_code == 422 and "U+0338" in r.json()["detail"]


def test_the_text_and_emoji_selectors_pass_once_after_a_character_and_never_alone_or_in_a_run():
    heart = chr(0x2764)
    assert link_commands.check_text("ship it " + heart + chr(0xFE0F), "command")
    assert link_commands.check_text("x" + chr(0xFE0E) + " y" + chr(0xFE0F), "command")
    for text in (chr(0xFE0F) + "ship it", heart + chr(0xFE0F) + chr(0xFE0F), heart + chr(0xFE0E) + chr(0xFE0F)):
        with pytest.raises(link.LinkError):
            link_commands.check_text(text, "command")


# ---- characters that hide, line feeds, a second copy's take and a card's commands ---------------------------------

@pytest.mark.parametrize("code", [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180E, 0x180F,
                                  0x2800, 0x3164, 0xFFA0, 0x2028, 0x2029])
def test_a_command_refuses_a_character_drawn_as_nothing_or_ending_a_line(code):
    with pytest.raises(link.LinkError) as e:
        link_commands.check_text("run the tests" + chr(code) + " now", "command")
    assert f"U+{code:04X}" in str(e.value)


def test_an_id_a_digest_or_a_name_with_a_line_feed_after_it_is_refused(client, owner, device):
    mac = paired(client, owner)
    r = send(client, owner, device, payload(client, owner, "instruction", mac.link_id, id=uuid.uuid4().hex + "\n"))
    assert r.status_code == 422 and "32 lowercase hex" in r.json()["detail"]
    q = post(mac, "question", "May I?", to="owner").json()["seq"]
    digest = link_commands.text_digest("May I?") + "\n"
    r = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=digest))
    assert r.status_code == 422
    s = Session(client)
    assert s.pair(ticket(client, owner), name="cloud\n", kind="cloud").status_code == 422


def test_a_take_from_a_second_copy_is_told_whatever_became_of_the_command(client, owner, device):
    mac = paired(client, owner)
    seq, _ = command(client, owner, device, mac.link_id)
    assert take(mac, seq, "i1").status_code == 200
    reply = post(mac, "done", "done: x", to="owner", ref=seq).json()["seq"]
    assert event_row(seq)["closed_by"] == reply
    assert take(mac, seq, "i2").status_code == 409                    # a copy, after the command was answered
    keys = [k for k, _ in link_commands.alerts(account(client, owner))]
    assert keys.count(f"link-take-conflict:{seq}") == 1
    later = int(time.time()) + link_commands.CONFLICT_SHOWN + 1
    assert f"link-take-conflict:{seq}" not in [k for k, _ in link_commands.alerts(account(client, owner), later)]


def test_what_a_session_reads_has_every_character_that_hides_written_as_an_escape(client, owner):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    hidden = "a" + chr(0x2028) + "b" + chr(0x85) + "c" + chr(0x0B) + "d" + chr(0x200B) + "e\\f\ng\rh\ti"
    assert post(cloud, "note", hidden, to="mac").status_code == 200
    cloud.call("PUT", "/checkpoint", {"expected_version": 0, "document": {"doing": "x" + chr(0x2029) + "y\nz"}})
    doc = mac.call("GET", "/resume").json()["document"]
    assert "a\\u2028b\\x85c\\x0bd\\u200be\\\\f\\ng\\rh\ti" in doc
    assert "x\\u2029y\\nz" in doc
    for line_ in doc.split("\n"):
        assert not any(ch in line_ for ch in (chr(0x2028), chr(0x2029), chr(0x85), chr(0x0B), "\r"))


def test_a_card_reads_each_link_s_newest_commands_and_no_more(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    mine = [command(client, owner, device, mac.link_id, f"m{i}")[0] for i in range(link.CARD_COMMANDS + 2)]
    theirs = [command(client, owner, device, cloud.link_id, f"c{i}")[0] for i in range(3)]
    cards = {c["id"]: c for c in client.get("/api/ade/link/sessions", headers=owner).json()["links"]}
    assert [c["seq"] for c in cards[mac.link_id]["commands"]] == sorted(mine, reverse=True)[:link.CARD_COMMANDS]
    assert [c["seq"] for c in cards[cloud.link_id]["commands"]] == sorted(theirs, reverse=True)


# ---- a seq past what sqlite stores -------------------------------------------------------------------------------

def test_a_seq_past_what_sqlite_stores_is_no_event_never_a_500(client, owner, device):
    mac = paired(client, owner)
    for seq in (2 ** 63, 2 ** 64):
        r = client.post(f"/api/ade/link/commands/{seq}/withdraw", headers=owner)
        assert r.status_code == 404 and r.headers["cache-control"] == "no-store"
        r = mac.call("GET", f"/events/{seq}")
        assert r.status_code == 404 and r.headers["cache-control"] == "no-store"
        r = take(mac, seq)
        assert r.status_code == 422 and r.headers["cache-control"] == "no-store"
    seq, _ = command(client, owner, device, mac.link_id)                  # the largest one stored still answers
    assert link_commands.SEQ_MAX == 2 ** 63 - 1 and mac.call("GET", f"/events/{seq}").status_code == 200


# ---- questions open again, a removed passkey's commands and a resume dated ahead ---------------------------------

def _open_questions(client, owner):
    """The questions the page shows open, each with its Answer and Close."""
    return {q["seq"] for q in client.get("/api/ade/link/sessions", headers=owner).json()["questions"]}


@pytest.mark.parametrize("gone", ["withdrawn", "expired"])
def test_a_question_whose_answer_is_withdrawn_or_expires_untaken_is_open_again(client, owner, device, monkeypatch,
                                                                              gone):
    mac = paired(client, owner)
    q = post(mac, "question", "May I delete the old branch?", to="owner").json()["seq"]
    digest = link_commands.text_digest("May I delete the old branch?")
    first = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=digest))
    assert first.status_code == 200 and q not in _open_questions(client, owner)
    if gone == "withdrawn":
        assert client.post(f"/api/ade/link/commands/{first.json()['seq']}/withdraw", headers=owner).status_code == 200
    else:                                              # an hour on, past the answer's life: no session took it
        later = int(time.time()) + 3700
        monkeypatch.setattr(link_commands, "_now", lambda now=None: later if now is None else now)
        monkeypatch.setattr(link, "_now", lambda now=None: later if now is None else now)
    assert q in _open_questions(client, owner)
    if gone == "withdrawn":                            # the owner answers again
        again = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "no", ref=q, digest=digest))
        assert again.status_code == 200 and event_row(q)["closed_by"] == again.json()["seq"]
        assert q not in _open_questions(client, owner)


@pytest.mark.parametrize("then", ["answer", "close"])
def test_a_question_whose_answer_expired_untaken_takes_a_new_answer_or_a_close(client, owner, device, then):
    mac = paired(client, owner)
    q = post(mac, "question", "May I delete the old branch?", to="owner").json()["seq"]
    digest = link_commands.text_digest("May I delete the old branch?")
    first = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=digest))
    assert first.status_code == 200 and q not in _open_questions(client, owner)
    with store.connect() as db:                         # its life over, and no session took it
        db.execute("UPDATE ade_link_events SET expires_at = ? WHERE seq = ?",
                   (int(time.time()) - 1, first.json()["seq"]))
    assert q in _open_questions(client, owner)
    if then == "answer":
        again = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "no", ref=q, digest=digest))
        assert again.status_code == 200 and event_row(q)["closed_by"] == again.json()["seq"]
    else:
        closed = client.post(f"/api/ade/link/questions/{q}/close", headers=owner)
        assert closed.status_code == 200 and event_row(q)["closed_by"] == closed.json()["seq"]
    assert q not in _open_questions(client, owner)


def _resume_open(session):
    """The seqs in the open-questions part of a session's resume document."""
    doc = session.call("GET", "/resume").json()["document"]
    part = doc.split("Open questions to you or to all:")[1].split("Commands to you")[0].split("Unacked, newest last:")[0]
    return {int(word[1:-1]) for word in part.split() if word.startswith("[") and word.endswith("]")
            and word[1:-1].isdigit()}


def test_a_question_open_again_is_offered_everywhere_and_closes_again_by_any_way_it_can(client, owner, device):
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    digest = link_commands.text_digest("May I merge it?")

    def asked_and_withdrawn():
        q = post(mac, "question", "May I merge it?", to="all").json()["seq"]
        a = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=digest))
        assert a.status_code == 200 and q not in _resume_open(cloud)
        assert client.post(f"/api/ade/link/commands/{a.json()['seq']}/withdraw", headers=owner).status_code == 200
        assert q in _resume_open(cloud)                   # the other session is told it is open again
        return q
    q = asked_and_withdrawn()
    assert client.post(f"/api/ade/link/questions/{q}/close", headers=owner).status_code == 200    # the page's Close
    assert q not in _open_questions(client, owner) and q not in _resume_open(cloud)
    q = asked_and_withdrawn()
    note = post(cloud, "note", "merged it already", ref=q, to="all")                               # a session's note
    assert note.status_code == 200 and event_row(q)["closed_by"] == note.json()["seq"]
    assert q not in _open_questions(client, owner) and q not in _resume_open(cloud)


def test_an_answer_taken_keeps_its_question_closed_after_its_life(client, owner, device, monkeypatch):
    mac = paired(client, owner)
    q = post(mac, "question", "Ship it?", to="owner").json()["seq"]
    digest = link_commands.text_digest("Ship it?")
    a = send(client, owner, device, payload(client, owner, "answer", mac.link_id, "yes", ref=q, digest=digest))
    assert take(mac, a.json()["seq"]).status_code == 200
    later = int(time.time()) + 3700
    monkeypatch.setattr(link_commands, "_now", lambda now=None: later if now is None else now)
    monkeypatch.setattr(link, "_now", lambda now=None: later if now is None else now)
    assert q not in _open_questions(client, owner)
    late = payload(client, owner, "answer", mac.link_id, "no", ref=q, digest=digest, created_at=later,
                   expires_at=later + 3600)                    # made at that later time: only the question refuses it
    r = send(client, owner, device, late)
    assert r.status_code == 409 and "not an open question" in r.text
    assert client.post(f"/api/ade/link/questions/{q}/close", headers=owner).status_code == 409


def test_removing_a_passkey_withdraws_what_it_signed_and_undoes_its_resumes(client, owner, device):
    """A lost device's passkey, removed, leaves nothing it signed for a session to act on: its queued commands are
    withdrawn and each resume it signed that a link holds is undone (that session is stopped again), one past its ten
    minutes too, since a resume never expires for the stop it was made for; a command already taken and another
    passkey's commands are left as they were."""
    from test_passkeys import add, remove
    from test_passkeys import Authenticator
    mac = paired(client, owner)
    cloud = paired(client, owner, name="cloud", kind="cloud")
    pi = paired(client, owner, name="pi")
    other = Authenticator(alg=-8)                            # its own credential id (the default one's is fixed)
    assert add(client, owner, other, name="the other", confirm_with=device).status_code == 200
    queued, _ = command(client, owner, device, mac.link_id, "queued by the lost device")
    taken, _ = command(client, owner, device, mac.link_id, "taken already")
    assert take(mac, taken).status_code == 200
    kept, _ = command(client, owner, other, mac.link_id, "signed by the other passkey")
    for s in (cloud, pi):                                   # each stopped, then resumed by the lost device
        t = client.post(f"/api/ade/link/links/{s.link_id}/stop", headers=owner).json()["stopped_at"]
        p = payload(client, owner, "resume", s.link_id, created_at=t + 1, expires_at=t + 601)
        assert send(client, owner, device, p, path=f"/api/ade/link/links/{s.link_id}/resume").status_code == 200
    with store.connect() as db:                             # pi's resume is past its ten minutes
        spent = json.loads(link_row(pi.link_id)["resume_payload"])
        spent["expires_at"] = int(time.time()) - 1
        db.execute("UPDATE ade_links SET resume_payload = ? WHERE id = ?", (json.dumps(spent), pi.link_id))
    lost = next(k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                if k["name"] != "the other")
    r = remove(client, owner, lost)
    assert r.status_code == 200, r.text
    assert (r.json()["withdrawn"], sorted(r.json()["stopped"])) == (1, sorted([cloud.link_id, pi.link_id]))
    assert event_row(queued)["withdrawn_at"] is not None and take(mac, queued).status_code == 409
    assert event_row(taken)["withdrawn_at"] is None and event_row(kept)["withdrawn_at"] is None
    for s in (cloud, pi):
        state = s.call("GET", "/state").json()
        assert state["stopped"] and state["resume"] is None  # the owner resumes it with another passkey
    assert take(mac, kept).status_code == 200



def test_a_resume_signed_before_a_stop_cannot_lift_it_however_it_is_dated(client, owner, device):
    """created_at is held to the nonce's issue: a page that dates its resume ahead, to after a stop
    the owner makes later, is refused, so a resume lifts only a stop made before its nonce was given."""
    mac = paired(client, owner)
    p = payload(client, owner, "resume", mac.link_id)                 # its nonce given now, before any stop
    stopped = client.post(f"/api/ade/link/links/{mac.link_id}/stop", headers=owner).json()["stopped_at"]
    ahead = dict(p, created_at=stopped + 60, expires_at=stopped + 660)
    r = send(client, owner, device, ahead, path=f"/api/ade/link/links/{mac.link_id}/resume")
    assert r.status_code == 422 and "the time its nonce was given" in r.json()["detail"], r.text
    assert mac.call("GET", "/state").json()["stopped"]
