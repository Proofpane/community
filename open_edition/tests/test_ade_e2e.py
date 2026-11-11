"""End-to-end encryption of what linked sessions send, the server's side (ade/e2e.py, ade/e2e_keys.py), through the
app: the owner's statements of the account's keys, signed by a passkey of the tests' own software authenticator and
taken only as the account's next; the keys the page keeps here, in one transaction each; the changes held while
encryption is on, cancelled or checked again when their hold ends; the confirmations they take (a passkey's, never the
password's, or the recovery code's proof); and what links send: envelopes read by their form alone, texts in the clear
refused while encryption is on, and what was sent in the clear sealed in place when it is turned on. The tests open
envelopes with an opener of their own (the page's side), so a mismatch with the server's sealing shows."""

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from conftest import signed_up
from connector_fixtures import connected
from fido_fixtures import add_hardware
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import oauth, passkeys, store
from proofpane_open.ade import connector, e2e, e2e_keys, holds, link
from proofpane_core.canonical import canonicalize
from test_ade_e2e_envelope import _vectors, b64u, open_envelope, raw_public, unb64u
from test_ade_link import HOST, OTHER, paired, post
from test_ade_link_commands import assertion, nonce
from test_passkeys import Authenticator, add, confirmed

DROP = object()
# the ceremony each of encryption's touches is made for, as the page asks for it (ui/src/e2eKeys.ts): the server takes
# a confirmation made for it for that change alone
ENROL, ANCHOR, CANCEL = "e2e-enrol-touch", "e2e-anchor-touch", "e2e-cancel"


class Keys:
    """The owner's side as the page keeps it: the account, its passkey (`device`), the statements it signed, the master
    key's id, the content keys' private halves, the recovery key, and the credential enrolled."""

    def __init__(self, client, owner, device):
        self.client, self.owner, self.device = client, owner, device
        self.account = client.get("/api/auth/me", headers=owner).json()["id"]
        self.passkey = passkey_of(client, owner, device)
        self.statements = []            # canonical bytes, by seq
        self.mk_id = None
        self.content = {}               # key_id: private key
        self.recovery = X25519PrivateKey.generate()

    @property
    def prev(self):
        return hashlib.sha256(self.statements[-1]).hexdigest() if self.statements else ""

    def statement(self, purpose="e2e-key", retires="grace", private=None, device=None, seq=None, prev=None, **over):
        """A statement the page signs: the next one unless `seq` or `prev` says otherwise; its content key made here
        when `private` is not given; `over` replaces a field of the payload, or drops it (DROP). (the signed body, the
        content key's private half or None)."""
        now = int(time.time())
        p = {"v": 2, "deployment": HOST, "purpose": purpose, "account": self.account,
             "nonce": nonce(self.client, self.owner, purpose), "id": uuid.uuid4().hex,
             "seq": len(self.statements) + 1 if seq is None else seq, "prev": self.prev if prev is None else prev,
             "created_at": now, "expires_at": now + 600}
        if purpose == "e2e-key":
            private = private or X25519PrivateKey.generate()
            p.update(key=b64u(raw_public(private)), key_id=e2e.key_id(raw_public(private)), retires=retires)
        for name, value in over.items():
            if value is DROP:
                p.pop(name, None)
            else:
                p[name] = value
        return {"payload": p, "credential": assertion(device or self.device, p)}, private

    def took(self, signed, private=None):
        """What the page keeps once the server took a statement it signed."""
        self.statements.append(canonicalize(signed["payload"]))
        if private is not None:
            self.content[e2e.key_id(raw_public(private))] = private

    def content_key(self, private, mk_id=None):
        return {"key_id": e2e.key_id(raw_public(private)), "public_key": b64u(raw_public(private)),
                "mk_id": mk_id or self.mk_id, "wrapped": b64u(os.urandom(76))}

    def grant(self, to):
        return {"to": to, "sealed": e2e.seal(raw_public(X25519PrivateKey.generate()), "grant", {"mk": "x"})}

    def turn_on_body(self, seal=None, **over):
        signed, private = self.statement(retires="all")
        self.mk_id = os.urandom(16).hex()
        body = {"statement": signed, "content_key": self.content_key(private),
                "mk_id": self.mk_id,
                "credential": {"passkey_id": self.passkey["id"], "salt": b64u(os.urandom(32)),
                               "public_key": b64u(raw_public(X25519PrivateKey.generate())),
                               "wrapped": b64u(os.urandom(120))},
                "recovery": {"public_key": b64u(raw_public(self.recovery)), "anchor": b64u(os.urandom(60))},
                "grants": [self.grant(self.passkey["credential_id"]), self.grant("recovery")],
                "roster": b64u(os.urandom(300))}
        if seal is not None:
            body["seal"] = seal
        body.update(over)
        return body, signed, private

    def turn_on(self, seal=None, **over):
        """Turn encryption on as the page does, held for no time here (it is held as every change is, and its hold
        ends at the next settle), and the compaction after it waited for: ({seq, key_id, hold, sealed: what it sealed
        in place, or None}, the content key's private half)."""
        body, signed, private = self.turn_on_body(seal, **over)
        r = at_once(lambda: self.client.post("/api/ade/e2e/turn-on", headers=self.owner, json=body))
        assert r.status_code == 200, r.text
        hold = r.json()["hold"]
        assert holds.settle(self.account) == [hold["id"]]
        compacted()
        with store.connect() as db:
            assert holds.get(db, hold["id"])["failed"] is None
        self.took(signed, private)
        sealed = view(self.client, self.owner)["sealed"] if body.get("seal", True) else None
        return {"seq": signed["payload"]["seq"], "key_id": signed["payload"]["key_id"], "hold": hold,
                "sealed": {k: v for k, v in sealed.items() if k != "at"} if sealed else None}, private

    def key(self):
        """The newest content key's private half."""
        return self.content[json.loads(self.statements[-1])["key_id"]]

    def proof(self, purpose, private=None):
        """The recovery code's proof for one change of `purpose`, as the page makes it."""
        r = self.client.post("/api/ade/e2e/recovery-proof", headers=self.owner, json={"purpose": purpose})
        assert r.status_code == 200, r.text
        c = r.json()
        shared = (private or self.recovery).exchange(X25519PublicKey.from_public_bytes(unb64u(c["server_key"])))
        k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"proofpane-e2e/1 recovery proof").derive(shared)
        return {"challenge_id": c["challenge_id"],
                "proof": b64u(hmac.new(k, unb64u(c["nonce"]) + purpose.encode(), hashlib.sha256).digest())}


def compacted(timeout=30):
    """The compactions begun after a sealing in place (e2e_keys.compact_soon), each in a thread of its own, finished."""
    for worker in threading.enumerate():
        if worker.name == "ade-e2e-compact":
            worker.join(timeout)
            assert not worker.is_alive(), "a compaction did not finish"


def at_once(call):
    """`call` made with every hold of a change to encryption set to no time (the settings read as a change is held)."""
    held = {name: os.environ.get(name) for name in (e2e_keys.HOLD_SETTING, e2e_keys.HOLD_RECOVERY_SETTING)}
    os.environ.update({name: "0" for name in held})
    try:
        return call()
    finally:
        for name, value in held.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def passkey_of(client, owner, device):
    """The account's passkey row for `device`: its id and credential id."""
    with store.connect() as db:
        row = db.execute("SELECT id, credential_id FROM passkeys WHERE credential_id = ?",
                         (b64u(device.credential_id),)).fetchone()
    return dict(row)


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
    return Keys(client, owner, device)


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    """Every notice sent to an account's devices (push.notice), as words made it, each then sent as before."""
    from proofpane_open.ade import push
    sent, notice = [], push.notice

    def recorded(user_id, message, now=None, post=None):
        sent.append(message)
        return notice(user_id, message, now, post)
    monkeypatch.setattr(push, "notice", recorded)
    return sent


def told(notices, hold):
    """The notice a hold's placing sent (holds.tell), found by its tag, a digest of the hold's own key."""
    from proofpane_open.ade import push
    tag = push.words("held-other", key="hold:" + hold["id"], at=0).tag
    (message,) = [m for m in notices if m.tag == tag]
    return message


def told_as(hold, kind):
    """A hold's notice as its kind of push.words says it: what waits and when it takes effect, nothing of the change,
    which the page shows (tests/test_push_words.py)."""
    from proofpane_open.ade import push
    return push.words(kind, key="hold:" + hold["id"], at=hold["takes_effect_at"])


def view(client, owner):
    r = client.get("/api/ade/e2e", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def rows(table, **where):
    with store.connect() as db:
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(f"{k} = ?" for k in where) if where else "")
        return [dict(r) for r in db.execute(sql, tuple(where.values())).fetchall()]


def sealed_for(keys, private, slot, value):
    return e2e.seal(raw_public(private), slot, value)


def event_slot(keys, s, kind, client_id, mirror=None, ref=None):
    return e2e.event_slot(keys.account, s.link_id, kind, mirror, ref, client_id)


def send_sealed(keys, s, kind, words="sealed words", private=None, client_id=None, **kw):
    """A sealed event to the owner, as the link sends one: its client_id 32 hex."""
    client_id = client_id or uuid.uuid4().hex
    text = sealed_for(keys, private or keys.key(), event_slot(keys, s, kind, client_id, kw.get("mirror"),
                                                              kw.get("ref")),
                      {"text": words, "session": kw.get("session"), "said_at": kw.get("said_at")})
    return post(s, kind, text, client_id=client_id, **kw), client_id, text


# ---- turning it on, and the statements ----------------------------------------------------------------------------

def test_turning_on_keeps_the_page_s_keys_and_its_statement_and_serves_them_back(client, owner, keys):
    got, private = keys.turn_on()
    assert (got["seq"], got["key_id"]) == (1, e2e.key_id(raw_public(private)))
    v = view(client, owner)
    assert v["on"] is True and v["newest"] == {"seq": 1, "key_id": got["key_id"]} and v["mk_id"] == keys.mk_id
    assert [c["passkey_id"] for c in v["credentials"]] == [keys.passkey["id"]]
    assert v["credentials"][0]["credential_id"] == keys.passkey["credential_id"]
    assert sorted(g["to"] for g in v["grants"]) == sorted([keys.passkey["credential_id"], "recovery"])
    assert {g["mk_id"] for g in v["grants"]} == {keys.mk_id} and v["roster"]["mk_id"] == keys.mk_id
    assert v["recovery"]["public_key"] == b64u(raw_public(keys.recovery))
    assert [(k["key_id"], k["mk_id"]) for k in v["content_keys"]] == [(got["key_id"], keys.mk_id)]
    (statement,) = v["statements"]
    assert statement["payload"].encode() == keys.statements[0] and statement["passkey_id"] == keys.passkey["id"]
    assert statement["digest"] == hashlib.sha256(keys.statements[0]).hexdigest()
    assert "credential" not in statement                    # the page is served the statements, not their signatures
    (mine,) = v["passkeys"]
    assert mine["enrolled"] is True and mine["prf"] is True and mine["can_enrol"] is False
    assert v["holds"] == {"change": 180, "recovery": 86400, "long": 86400} and v["pending"] is None
    assert (v["rekey_due"], v["compaction_due"]) == (False, False)


def test_turning_on_takes_a_signed_statement_of_the_account_s_first_key(client, owner, keys):
    body, signed, private = keys.turn_on_body()
    same = {"private": private}
    for case, statement, status, words in (
            ("seq 2 first", keys.statement(seq=2, prev="a" * 64, **same)[0], 409, "seq 1"),
            ("prev given for the first", keys.statement(prev="a" * 64, **same)[0], 422, "prev"),
            ("key_id not the key's", keys.statement(key_id="0" * 32, **same)[0], 422, "sha256 of its key"),
            ("retires another way", keys.statement(retires="soon", **same)[0], 422, "retires"),
            ("a key of 31 bytes", keys.statement(key=b64u(bytes(31)), **same)[0], 422, "32 bytes"),
            ("a field more", keys.statement(to="x", **same)[0], 422, "not expected to"),
            ("the nonce of another purpose", keys.statement(nonce=nonce(client, owner, "instruction"), **same)[0],
             400, "nonce"),
            ("turned off as the first", keys.statement("e2e-off")[0], 422, "e2e-key"),
            ("a life past ten minutes", keys.statement(expires_at=int(time.time()) + 601, **same)[0], 422,
             "60 to 600")):
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, statement=statement))
        assert r.status_code == status and words in r.json()["detail"], (case, r.text)
    # the signature over other bytes
    forged = dict(signed, credential=assertion(keys.device, dict(signed["payload"], retires="grace")))
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, statement=forged))
    assert r.status_code == 400 and "canonical form" in r.json()["detail"]
    assert rows("ade_e2e_statements") == [] and rows("ade_e2e_content_keys") == []
    assert client.post("/api/ade/e2e/turn-on", headers=owner, json=body).status_code == 200
    # a statement is never a command
    r = client.post("/api/ade/link/commands", headers=owner, json=keys.statement()[0])
    assert r.status_code == 422 and "instruction, answer or move" in r.json()["detail"]


def test_turning_on_refuses_a_body_that_does_not_hold_together_and_keeps_nothing(client, owner, keys, device):
    other = Authenticator(alg=-8)
    assert add(client, owner, other, confirm_with=device).status_code == 200
    theirs = passkey_of(client, owner, other)
    body, signed, private = keys.turn_on_body()
    wrong = {
        "a content key not the stated one": dict(body, content_key=keys.content_key(X25519PrivateKey.generate())),
        "a content key under another master key": dict(body, content_key=dict(body["content_key"],
                                                                              mk_id="b" * 32)),
        # granted to it as well: the passkey that signed the statement is the one turning on enrols, and no other
        "a credential of another passkey": dict(body, credential=dict(body["credential"], passkey_id=theirs["id"]),
                                                grants=[keys.grant(theirs["credential_id"]), keys.grant("recovery")]),
        "a salt of 16 bytes": dict(body, credential=dict(body["credential"], salt=b64u(os.urandom(16)))),
        "a credential key of low order": dict(body, credential=dict(body["credential"], public_key=b64u(bytes(32)))),
        "no grant to the recovery key": dict(body, grants=[keys.grant(keys.passkey["credential_id"])]),
        "a grant to another credential": dict(body, grants=body["grants"] + [keys.grant("someone-else")]),
        "a grant not sealed": dict(body, grants=[{"to": "recovery", "sealed": "plain"},
                                                 keys.grant(keys.passkey["credential_id"])]),
        "a roster of 20 bytes": dict(body, roster=b64u(os.urandom(20))),
        "a master key id not 32 hex": dict(body, mk_id="X" * 32),
        "seal not true or false": dict(body, seal="yes"),
    }
    for case, bad in wrong.items():
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=bad)
        assert r.status_code in (409, 422), (case, r.text)
    for case, bad, status, words in (
            # with no keys from before, the whole body; starting again (replace) takes it too
            ("the content key alone", {"statement": signed, "content_key": body["content_key"]}, 409,
             e2e_keys.NO_KEYS),
            ("replace with the content key alone", {"statement": signed, "content_key": body["content_key"],
                                                    "replace": True}, 422, "master key's id"),
            ("replace retiring after a grace", dict(body, replace=True, statement=keys.statement(
                retires="grace", private=private)[0]), 422, e2e_keys.RETIRES_ALL)):
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=bad)
        assert r.status_code == status and words in r.json()["detail"], (case, r.text)
    for table in ("ade_e2e_statements", "ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_roster",
                  "ade_e2e_content_keys"):
        assert rows(table) == [], table
    assert client.post("/api/ade/e2e/turn-on", headers=owner, json=body).status_code == 200   # its nonce unused


def test_turning_on_waits_on_its_hold_and_is_one_transaction_when_it_ends(client, owner, keys, device, monkeypatch):
    """Nothing of a turn-on is kept before its hold ends; it is cancelled as any change is (a turn-on removes no
    passkey: any of the account's passkeys cancels it, the sign-in alone does not); and when its hold ends, it takes
    effect whole or not at all, a failure inside it keeping nothing, the reason on the hold."""
    mac = paired(client, owner)
    assert post(mac, "step", "kept in the clear", client_id=uuid.uuid4().hex).status_code == 200
    body, signed, private = keys.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert (hold["kind"], hold["takes_effect_at"] - hold["approved_at"]) == (e2e_keys.HOLD, 180)
    assert hold["summary"] == "Turning end-to-end encryption on was signed"
    v = view(client, owner)
    assert v["on"] is False and v["statements"] == [] and v["pending"]["change"] == "turn-on"
    # no recovery code yet: a passkey alone cancels it
    assert (v["pending"]["cancel_by"], v["pending"]["cancel_by_code"]) == ([keys.passkey["id"]], False)
    for table in ("ade_e2e_statements", "ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_roster",
                  "ade_e2e_content_keys"):
        assert rows(table) == [], table
    assert post(mac, "step", "still in the clear", client_id=uuid.uuid4().hex).status_code == 200
    r = cancel(keys, hold["id"], recovery_proof={"challenge_id": "x" * 32, "proof": "y" * 43})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_RECOVERY, r.text
    # cancelled, nothing of it is kept; its nonce was used as it was asked
    assert cancel(keys, hold["id"]).status_code == 403
    assert cancel(keys, hold["id"], device).status_code == 200
    holds.settle(keys.account, now=hold["takes_effect_at"] + 1)
    assert rows("ade_e2e_statements") == [] and rows("ade_e2e_pending") == []
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 400 and "nonce" in r.json()["detail"]
    # a fault as its hold ends keeps nothing of it, and says why on the hold
    body, signed, private = keys.turn_on_body()
    hold = client.post("/api/ade/e2e/turn-on", headers=owner, json=body).json()["hold"]

    def broken(*a, **kw):
        raise link.LinkError(503, "a fault inside the transaction")
    monkeypatch.setattr(e2e_keys, "seal_in_place", broken)
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == "a fault inside the transaction"
    for table in ("ade_e2e_statements", "ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_roster",
                  "ade_e2e_content_keys", "ade_e2e_pending", "ade_e2e_state"):
        assert rows(table) == [], table
    assert rows("ade_link_events", link_id=mac.link_id)[0]["text"] == "kept in the clear"


def test_a_turn_on_is_checked_again_when_its_hold_ends_and_keeps_nothing_when_it_fails(client, owner, keys):
    """As it is asked and again as its hold ends: encryption is off, and its statement is the account's next. Turned on
    meanwhile, or its place in the chain taken (a restore, written into the database here), it keeps nothing."""
    for purpose, words in (("e2e-key", e2e_keys.ON_ALREADY), ("e2e-off", "the account's next")):
        body, signed, private = keys.turn_on_body()
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
        assert r.status_code == 200, r.text
        hold = r.json()["hold"]
        with store.connect() as db:
            db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, key_id, payload, created_at) VALUES "
                       "(?, 1, ?, ?, ?, '{}', 1)", (keys.account, uuid.uuid4().hex, purpose,
                                                    "f" * 32 if purpose == "e2e-key" else None))
        assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
        with store.connect() as db:
            assert words in holds.get(db, hold["id"])["failed"], purpose
        for table in ("ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_roster",
                      "ade_e2e_content_keys", "ade_e2e_pending"):
            assert rows(table) == [], (purpose, table)
        assert [s["purpose"] for s in rows("ade_e2e_statements")] == [purpose]
        with store.connect() as db:
            db.execute("DELETE FROM ade_e2e_statements")
    # high security turned on meanwhile (written into the database here): the device passkey it would enrol unlocks
    # nothing then
    body, signed, private = keys.turn_on_body()
    hold = client.post("/api/ade/e2e/turn-on", headers=owner, json=body).json()["hold"]
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (keys.account,))
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert "only a hardware key unlocks" in holds.get(db, hold["id"])["failed"]
    assert rows("ade_e2e_credentials") == [] and rows("ade_e2e_statements") == []


def turned_off(keys):
    """Encryption turned off, its hold settled at its time."""
    signed, _ = keys.statement("e2e-off")
    r = keys.client.post("/api/ade/e2e/off", headers=keys.owner, json={"statement": signed})
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"]) == [r.json()["hold"]["id"]]
    keys.took(signed)


def test_turning_on_again_is_signed_by_a_passkey_that_unlocks_the_keys(client, owner, keys, second):
    """Turning on again with the keys from before: signed by a passkey that unlocks them, under the current master key,
    stating a key never stated before."""
    keys.turn_on()
    first = keys.key()
    turned_off(keys)
    by_second, private = keys.statement(retires="all", device=second)          # a passkey not enrolled
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": by_second,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_ENROLLED, r.text
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private, "c" * 32)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.STALE_MK, r.text
    signed, _ = keys.statement(retires="all", private=first)                    # the key it stated first, again
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(first)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.KEY_STATED, r.text
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD, r.text
    assert rows("ade_e2e_pending")[0]["made_by"] == keys.passkey["id"]


def test_a_grant_as_the_link_client_s_vectors_seal_one_has_the_form_the_server_keeps():
    vectors = _vectors("e2e_v1.json")
    for name in ("grant", "recovery_grant"):
        assert e2e_keys._sealed(vectors[name]["envelope"], "grant") == vectors[name]["envelope"]


def test_the_ticket_answer_names_the_newest_statement_for_the_pair_command(client, owner, keys, monkeypatch):
    def ticket_e2e():
        r = client.post("/api/ade/link/tickets", headers=owner, json=confirmed(client, owner, change="link-ticket:new"))
        assert r.status_code == 200, r.text
        return r.json()["e2e"]
    assert ticket_e2e() is None
    got, private = keys.turn_on()
    assert ticket_e2e() == {"seq": 1, "key_id": got["key_id"]}
    # a change whose hold has ended is in it, though nothing else settled it first (turning off waits the long hold)
    signed, _ = keys.statement("e2e-off")
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", "0")
    assert client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed}).status_code == 200
    assert ticket_e2e() == {"seq": 2, "key_id": None}


def test_the_keys_are_read_in_one_transaction_so_a_change_committed_meanwhile_is_not_half_seen(client, owner, keys,
                                                                                               monkeypatch):
    keys.turn_on()
    real = e2e_keys.current

    def current_and_a_change_meanwhile(db, user_id):
        held = real(db, user_id)
        with store.connect() as other:          # a revocation's grants, committed while the view is being read
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE ade_e2e_grants SET mk_id = ? WHERE user_id = ?", ("e" * 32, user_id))
        return held
    monkeypatch.setattr(e2e_keys, "current", current_and_a_change_meanwhile)
    v = view(client, owner)
    assert v["mk_id"] == keys.mk_id and {g["mk_id"] for g in v["grants"]} == {keys.mk_id}
    monkeypatch.setattr(e2e_keys, "current", real)
    assert {g["mk_id"] for g in view(client, owner)["grants"]} == {"e" * 32}         # the change, read whole after


def test_turning_on_twice_is_refused_and_the_page_routes_take_the_sign_in(client, owner, keys):
    keys.turn_on()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=keys.turn_on_body()[0])
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.ON_ALREADY
    for method, path in (("GET", "/api/ade/e2e"), ("POST", "/api/ade/e2e/turn-on"), ("POST", "/api/ade/e2e/rotate"),
                         ("POST", "/api/ade/e2e/revoke"), ("POST", "/api/ade/e2e/credentials"),
                         ("POST", "/api/ade/e2e/off"), ("POST", "/api/ade/e2e/anchors"),
                         ("POST", "/api/ade/e2e/recovery-proof")):
        assert client.request(method, path, json={}).status_code == 401, path
    assert client.get("/api/ade/e2e", headers=owner).headers["cache-control"] == "no-store"
    other = signed_up(client, OTHER)
    assert view(client, other)["on"] is False and view(client, other)["statements"] == []


# ---- the store ------------------------------------------------------------------------------------------------------

def test_the_store_overwrites_what_it_deletes_and_keeps_a_write_ahead_log():
    with store.connect() as db:
        assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        # a checkpoint that resets the log cuts its file back to nothing, so no old page lingers in its tail
        assert db.execute("PRAGMA journal_size_limit").fetchone()[0] == 0


# ---- what links send: events --------------------------------------------------------------------------------------

def test_an_event_to_the_owner_is_taken_sealed_and_served_as_stored(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    r, client_id, text = send_sealed(keys, mac, "step", "the plan for Tuesday")
    assert r.status_code == 200, r.text
    assert r.json()["e2e"] == {"seq": 1, "key_id": e2e.key_id(raw_public(keys.key()))}
    (row,) = rows("ade_link_events", client_id=client_id)
    assert row["text"] == text
    (card,) = client.get("/api/ade/link/sessions", headers=owner).json()["links"]
    assert card["last_event"]["text"] == text and card["last_event"]["client_id"] == client_id
    slot = event_slot(keys, mac, "step", card["last_event"]["client_id"])       # rebuilt from what is served
    assert open_envelope(keys.key(), card["last_event"]["text"], slot)["text"] == "the plan for Tuesday"
    (event,) = client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=owner).json()["events"]
    assert event["client_id"] == client_id and event["text"] == text
    assert rows("ade_link_e2e", link_id=mac.link_id)[0]["key_id"] == e2e.key_id(raw_public(keys.key()))
    # to its own name is to the owner; a question to the owner too, its words never held to the plain text's rules here
    assert send_sealed(keys, mac, "note", to=mac.name)[0].status_code == 200
    q, qid, qtext = send_sealed(keys, mac, "question", to="owner")
    assert q.status_code == 200
    (question,) = client.get("/api/ade/link/sessions", headers=owner).json()["questions"]
    assert question["text"] == qtext and question["client_id"] == qid
    # a session's conversation, its mirrored notes: the same
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"}, registered=True)]).status_code == 200
    r, nid, ntext = send_sealed(keys, mac, "note", "the owner typed this", mirror="owner", session="s1",
                                said_at=int(time.time()) * 1000)
    assert r.status_code == 200 and r.json()["session"] == "s1", r.text
    (note,) = client.get(f"/api/ade/link/timeline/{mac.link_id}?session=s1", headers=owner).json()["events"]
    assert (note["client_id"], note["text"], note["mirror"], note["session"]) == (nid, ntext, "owner", "s1")
    slot = event_slot(keys, mac, "note", nid, mirror="owner")
    assert open_envelope(keys.key(), note["text"], slot)["session"] == "s1"


def test_an_envelope_goes_to_the_owner_alone_with_a_client_id_of_32_hex_and_a_stated_key(client, owner, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    keys.turn_on()
    for kw, words in ((dict(to="all"), "owner alone"), (dict(to="cloud"), "owner alone"),
                      (dict(client_id="a" * 16), "32 lowercase hex"), (dict(client_id="A" * 32), "32 lowercase hex"),
                      (dict(private=X25519PrivateKey.generate()), "never stated")):
        r = send_sealed(keys, mac, "note", **kw)[0]
        assert r.status_code == 422 and words in r.json()["detail"], (kw, r.text)
    assert rows("ade_link_events", link_id=mac.link_id) == []
    assert cloud.call("GET", "/feed").json()["events"] == []
    # a text that begins as an envelope does and is not one is refused, never taken as words
    r = post(mac, "note", "e2e1.not an envelope", client_id=uuid.uuid4().hex)
    assert r.status_code == 422 and "e2e1." in r.json()["detail"]


def test_an_envelope_holds_at_most_16400_bytes_inside(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    padding = len(canonicalize({"text": "", "session": None, "said_at": None}))
    assert send_sealed(keys, mac, "note", "x" * (e2e.EVENT_PLAIN - padding))[0].status_code == 200
    r = send_sealed(keys, mac, "note", "x" * (e2e.EVENT_PLAIN - padding + 1))[0]
    assert r.status_code == 422 and "16400" in r.json()["detail"]


def test_an_envelope_is_never_held_to_the_plain_text_s_rules(client, owner, keys):
    """A secret's shape, a hidden character, a question's rules: the link applies them before sealing and the page
    after opening; on the envelope, whose base64url can spell a key's shape by chance, the server applies none."""
    mac = paired(client, owner)
    keys.turn_on()
    header = bytes([1]) + bytes.fromhex(e2e.key_id(raw_public(keys.key()))) + os.urandom(44)
    shaped = "-AIza" + "B" * 30 + "C"                           # 36 characters, a whole number of 4-character groups
    data = header + os.urandom(2) + unb64u(shaped)
    text = "e2e1." + b64u(data)
    assert shaped in text
    with pytest.raises(link.LinkError):
        link.check_secrets(text, "text")                        # as a plain text it would be refused
    r = post(mac, "question", text, client_id=uuid.uuid4().hex, to="owner")
    assert r.status_code == 200, r.text


def test_while_encryption_is_on_a_text_to_the_owner_in_the_clear_is_refused(client, owner, keys):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    assert post(mac, "step", "in the clear before").status_code == 200
    keys.turn_on()
    key_id = e2e.key_id(raw_public(keys.key()))
    for kw in ({}, {"to": "owner"}, {"to": "mac"}):
        r = post(mac, "note", "in the clear", **kw)
        assert r.status_code == 409, (kw, r.text)
        assert r.json() == {"detail": link.E2E_REQUIRED, "e2e": "required", "seq": 1, "key_id": key_id}
    assert rows("ade_link_e2e", link_id=mac.link_id)[0]["last_required_at"] is not None
    # what other sessions read stays in the clear
    assert post(mac, "note", "to all", to="all").status_code == 200
    assert post(mac, "note", "to cloud", to="cloud").status_code == 200
    assert [e["text"] for e in cloud.call("GET", "/feed").json()["events"]] == ["to all", "to cloud"]
    # a post the server took before is given back as it was, not refused
    old = rows("ade_link_events", text="in the clear before")[0]
    r = post(mac, "step", "in the clear before", client_id=old["client_id"])
    assert r.status_code == 200 and r.json()["seq"] == old["seq"]


def test_the_two_fixed_texts_stay_in_the_clear_exactly_as_written(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    ts = int(time.time()) - 60
    stopped = "stopped at " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    assert post(mac, "note", stopped, client_id=f"stopped:{ts}").status_code == 200
    assert post(mac, "waiting", "working on it", client_id=uuid.uuid4().hex).status_code == 200
    earlier = "stopped at " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts - 1))
    for kind, text, client_id, more in (
            ("note", earlier + " ", f"stopped:{ts - 1}", {}), ("note", stopped, f"stopped:{ts + 1}", {}),
            ("note", stopped, uuid.uuid4().hex, {}), ("waiting", "working on it!", uuid.uuid4().hex, {}),
            ("note", "working on it", uuid.uuid4().hex, {}), ("waiting", "Working on it", uuid.uuid4().hex, {}),
            # exactly as ade_link.py posts them: working's client_id 32 hex, the stop's note no mirror or session
            ("waiting", "working on it", "a" * 16, {}), ("waiting", "working on it", uuid.uuid4().hex.upper(), {}),
            ("note", stopped, f"stopped:{ts}x", {}), ("note", stopped, f"stopped:{ts - 2}", {"mirror": "owner"}),
            ("note", stopped, f"stopped:{ts - 3}", {"mirror": "info", "session": "s1"})):
        r = post(mac, kind, text, client_id=client_id, **more)
        assert r.status_code == 409 and r.json()["e2e"] == "required", (kind, text, client_id, more)
    assert link.fixed_text("note", "stopped:9999999999999", "stopped at x") is False       # past any time written
    assert link.fixed_text("note", f"stopped:{ts}", stopped) is True
    for given in ({"mirror": "owner"}, {"session": "s1"}, {"said_at": ts * 1000}):
        assert link.fixed_text("note", f"stopped:{ts}", stopped, **given) is False, given


def test_an_envelope_under_a_key_retired_for_sealing_is_refused_while_encryption_is_on(client, owner, keys,
                                                                                         monkeypatch):
    mac = paired(client, owner)
    keys.turn_on()
    first = keys.key()
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 422 and "grace" in r.json()["detail"]          # a rotation retires after a grace
    rotate(keys)
    assert send_sealed(keys, mac, "step", private=first)[0].status_code == 200     # within the grace
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_statements SET retired_at = ? WHERE seq = 1", (int(time.time()) - 1,))
    r = send_sealed(keys, mac, "step", private=first)[0]
    assert r.status_code == 409 and r.json()["e2e"] == "required" and r.json()["seq"] == 2
    assert send_sealed(keys, mac, "step")[0].status_code == 200


def rotate(keys, recovery_proof=None):
    """A routine rotation, signed and sent; the hold placed (its body waits): the answer."""
    signed, private = keys.statement()
    body = {"statement": signed, "content_key": keys.content_key(private)}
    if recovery_proof is not None:
        body["recovery_proof"] = recovery_proof
    r = keys.client.post("/api/ade/e2e/rotate", headers=keys.owner, json=body)
    assert r.status_code == 200, r.text
    keys.took(signed, private)
    holds.settle(keys.account)
    return r.json()


# ---- what links send: History rows and conversations ----------------------------------------------------------------

def history(s, sessions, **extra):
    return s.call("PUT", "/history", dict({"sessions": sessions}, **extra))


def sealed_row(keys, s, sid, value, withheld=False, private=None, **row):
    """A History row as a link seals it: its id, withheld or not, and its fields inside."""
    slot = e2e.history_slot(keys.account, s.link_id, sid, withheld)
    sealed = sealed_for(keys, private or keys.key(), slot, value)
    return dict({"id": sid, "withheld": withheld, "sealed": sealed}, **row)


def test_a_history_row_is_kept_sealed_its_four_fields_inside(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    now = int(time.time())
    value = {"title": "the plan", "project": "~/grove", "branch": "main", "repositories": ["~/grove"]}
    r = history(mac, [sealed_row(keys, mac, "s1", value, last_at=now - 60, client="vscode", live="open",
                                 registered=True),
                      sealed_row(keys, mac, "s2", {"project": "~/private"}, withheld=True)], pins=["a" * 32, "b" * 32])
    assert r.status_code == 200 and (r.json()["listed"], r.json()["left_out"]) == (2, 0), r.text
    assert r.json()["e2e"] == {"seq": 1, "key_id": e2e.key_id(raw_public(keys.key()))}
    stored = {row["session_id"]: row for row in rows("ade_link_history", link_id=mac.link_id)}
    assert (stored["s1"]["title"], stored["s1"]["project"], stored["s1"]["branch"], stored["s1"]["repositories"]) == \
        ("", None, None, None)
    got = {row["id"]: row for row in client.get("/api/ade/link/history", headers=owner).json()["sessions"]}
    assert (got["s1"]["title"], got["s1"]["project"], got["s1"]["branch"], got["s1"]["repositories"]) == \
        ("", None, None, [])
    assert (got["s2"]["title"], got["s2"]["project"], got["s2"]["withheld"]) == (None, None, True)
    assert (got["s1"]["live"], got["s1"]["client"], got["s1"]["registered"]) == ("open", "vscode", True)
    assert open_envelope(keys.key(), got["s1"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s1",
                                                                           False)) == value
    assert open_envelope(keys.key(), got["s2"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s2",
                                                                           True)) == {"project": "~/private"}
    (linked,) = view(client, owner)["links"]
    assert linked["pins"] == ["a" * 32, "b" * 32] and linked["key_id"] == e2e.key_id(raw_public(keys.key()))
    r = history(mac, [], pins=["not a fingerprint"])
    assert r.status_code == 422 and "pins" in r.json()["detail"]


def test_a_link_s_pins_are_null_until_it_reports_them_and_empty_when_it_pins_none(client, owner, keys):
    """GET tells a link that never reported its pins (null: not known) from one that pins none ([], which refuses every
    signed command); a History list sent without pins, as an older link sends one, leaves what was reported."""
    mac = paired(client, owner)
    keys.turn_on()

    def pins():
        (linked,) = view(client, owner)["links"]
        return linked["pins"]
    assert pins() is None
    assert history(mac, []).status_code == 200 and pins() is None
    assert history(mac, [], pins=[]).status_code == 200 and pins() == []
    assert history(mac, [], pins=["a" * 32]).status_code == 200 and pins() == ["a" * 32]
    assert history(mac, []).status_code == 200 and pins() == ["a" * 32]
    assert history(mac, [], pins=[]).status_code == 200 and pins() == []


def test_a_sealed_row_holds_its_fields_inside_and_none_beside_within_its_bytes(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()

    def sized(n, name="title"):
        return {name: "x" * (n - len(canonicalize({name: ""})))}
    stranger = X25519PrivateKey.generate()
    r = history(mac, [
        sealed_row(keys, mac, "fits", sized(e2e.HISTORY_PLAIN)),
        sealed_row(keys, mac, "withheld-fits", sized(e2e.HISTORY_WITHHELD_PLAIN, "project"), withheld=True),
        sealed_row(keys, mac, "over", sized(e2e.HISTORY_PLAIN + 1)),
        sealed_row(keys, mac, "withheld-over", sized(e2e.HISTORY_WITHHELD_PLAIN + 1, "project"), withheld=True),
        sealed_row(keys, mac, "and-title", sized(10), title="beside"),
        sealed_row(keys, mac, "and-project", sized(10), project="~/beside"),
        sealed_row(keys, mac, "and-branch", sized(10), branch="beside"),
        sealed_row(keys, mac, "and-repositories", sized(10), repositories=["~/beside"]),
        sealed_row(keys, mac, "another-key", sized(10), private=stranger),
        {"id": "not-one", "sealed": "e2e1.!"}])
    assert r.status_code == 200 and (r.json()["listed"], r.json()["left_out"]) == (2, 8), r.text
    assert sorted(row["session_id"] for row in rows("ade_link_history", link_id=mac.link_id)) == \
        ["fits", "withheld-fits"]


def test_while_encryption_is_on_a_row_in_the_clear_refuses_the_whole_list(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    assert history(mac, [{"id": "s0", "title": "listed before"}]).status_code == 200
    keys.turn_on(seal=False)
    for row in ({"id": "s1", "title": "in the clear"}, {"id": "s1", "withheld": True, "project": "~/in-the-clear"}):
        r = history(mac, [sealed_row(keys, mac, "ok", {"title": "t"}), row])
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    assert [row["session_id"] for row in rows("ade_link_history", link_id=mac.link_id)] == ["s0"]   # nothing replaced
    assert rows("ade_link_e2e", link_id=mac.link_id)[0]["last_required_at"] is not None
    # a withheld row with no project holds no words: it goes as it is
    assert history(mac, [{"id": "s2", "withheld": True}]).status_code == 200
    # a row sealed to a key retired for sealing refuses the list too
    first = keys.key()
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    rotate(keys)
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_statements SET retired_at = ? WHERE seq = 1", (int(time.time()) - 1,))
    r = history(mac, [sealed_row(keys, mac, "old", {"title": "t"}, private=first)])
    assert r.status_code == 409 and r.json()["seq"] == 2
    assert history(mac, [sealed_row(keys, mac, "new", {"title": "t"})]).status_code == 200


def talk_messages(keys, s, sid, texts, earlier=0, now=None, private=None):
    """A conversation's messages as a link seals them, each bound to its place, the list's length and its times."""
    now = now or int(time.time())
    out = []
    for i, (who, text) in enumerate(texts):
        at, ms = now - 100 + i, (now - 100 + i) * 1000 - 5
        slot = e2e.talk_slot(keys.account, s.link_id, sid, i, len(texts), earlier, who, at, ms)
        out.append({"who": who, "text": sealed_for(keys, private or keys.key(), slot, {"text": text}), "at": at,
                    "ms": ms})
    return out


def test_a_conversation_is_kept_sealed_message_by_message(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).status_code == 200
    said = [("owner", "what is the plan?"), ("session", "the plan is " + chr(0x1F600))]
    messages = talk_messages(keys, mac, "s1", said, earlier=3)
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": messages, "earlier": 3})
    assert r.status_code == 200 and r.json()["e2e"]["seq"] == 1, r.text
    got = client.get(f"/api/ade/link/history/{mac.link_id}/s1", headers=owner).json()
    assert got["earlier"] == 3
    for i, m in enumerate(got["messages"]):
        slot = e2e.talk_slot(keys.account, mac.link_id, "s1", i, len(got["messages"]), got["earlier"], m["who"],
                             m["at"], m.get("ms"))
        assert open_envelope(keys.key(), m["text"], slot) == {"text": said[i][1]}
    # in the clear, while encryption is on: refused, whole
    clear = [{"who": "owner", "text": "in the clear", "at": int(time.time())}]
    for mixed in (clear, messages + clear):
        r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": mixed})
        assert r.status_code == 409 and r.json()["e2e"] == "required", r.text
    stranger = talk_messages(keys, mac, "s1", said, private=X25519PrivateKey.generate())
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": stranger})
    assert r.status_code == 422 and "never stated" in r.json()["detail"]


def test_a_conversation_s_caps_are_counted_as_its_sealed_messages_decode(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).status_code == 200
    pad = len(canonicalize({"text": ""}))
    one = [("session", "y" * (e2e.TALK_PLAIN - pad))]
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": talk_messages(
        keys, mac, "s1", one)}).status_code == 200
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": talk_messages(
        keys, mac, "s1", [("session", "y" * (e2e.TALK_PLAIN - pad + 1))])})
    assert r.status_code == 422 and "16400" in r.json()["detail"]
    many = one * 19                     # 19 messages of 16,477 bytes as they decode: past 300,000
    r = mac.call("PUT", "/history/talk", {"session": "s1", "messages": talk_messages(keys, mac, "s1", many)})
    assert r.status_code == 422 and "300000 bytes" in r.json()["detail"], r.text
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": talk_messages(
        keys, mac, "s1", one * 18)}).status_code == 200


# ---- sealing in place ------------------------------------------------------------------------------------------------

def test_turning_on_seals_in_place_what_links_sent_in_the_clear_with_the_head_kept(client, owner, keys, env):
    mac = paired(client, owner)
    cloud = paired(client, owner, "cloud", "cloud")
    now = int(time.time())
    many = [f"~/{i:02d}" + "r" * 296 for i in range(20)]           # 20 of 300 characters: past what a row holds
    assert history(mac, [{"id": "s1", "title": "SEALME title", "project": "~/SEALME", "branch": "main",
                          "repositories": ["~/SEALME-repo"], "registered": True},
                         {"id": "s2", "withheld": True, "project": "~/SEALME-withheld"},
                         {"id": "s3", "withheld": True},
                         {"id": "s4", "title": "SEALME many", "repositories": many},
                         {"id": "s5", "title": "SEALME none"}], conversations=True).status_code == 200
    said = [{"who": "owner", "text": "SEALME asked", "at": now - 5, "ms": (now - 6) * 1000},
            {"who": "session", "text": "SEALME said", "at": now}]
    longer = [{"who": "session", "text": "p" * 4000, "at": now} for _ in range(15)]
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": longer}).status_code == 200
    assert mac.call("PUT", "/history/talk", {"session": "s1", "messages": said, "earlier": 2}).status_code == 200
    with store.connect() as db:          # the longer conversation's pages are free now, until the file is rebuilt
        assert db.execute("PRAGMA freelist_count").fetchone()[0] > 0
    ids = {}
    for name, kind, text, kw in (("step", "step", "SEALME step", {}),
                                 ("question", "question", "SEALME question?", {"to": "owner"}),
                                 ("mirrored", "note", "SEALME mirrored", {"mirror": "session", "session": "s1",
                                                                         "said_at": now * 1000}),
                                 ("itself", "note", "SEALME to itself", {"to": "mac"}),
                                 ("all", "note", "LEFTCLEAR to all", {"to": "all"}),
                                 ("cloud", "note", "LEFTCLEAR to cloud", {"to": "cloud"})):
        ids[name] = uuid.uuid4().hex
        assert post(mac, kind, text, client_id=ids[name], **kw).status_code == 200, name
    assert post(mac, "note", "LEFTCLEAR a short client id", client_id="c" * 16).status_code == 200
    stopped_at = now - 30
    stopped = "stopped at " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stopped_at))
    assert post(mac, "note", stopped, client_id=f"stopped:{stopped_at}").status_code == 200
    assert post(mac, "waiting", "working on it", client_id=uuid.uuid4().hex).status_code == 200
    head = max(r["seq"] for r in rows("ade_link_events"))
    assert view(client, owner)["to_seal"] == {"events": 4, "left": 1, "rows": 4, "talks": 1}
    # another connection open meanwhile, as the app's other requests keep one: the log is not removed as the last
    # connection closes, so only the checkpoint empties it
    path = os.path.join(str(env), "open_edition.db")
    watcher = sqlite3.connect(path)
    watcher.execute("SELECT COUNT(*) FROM ade_links").fetchall()
    got, private = keys.turn_on()
    assert got["sealed"] == {"events": 4, "left": 1, "rows": 4, "talks": 1}
    events = {r["client_id"]: r for r in rows("ade_link_events")}
    for name, words, session, said_at, mirror in (("step", "SEALME step", None, None, None),
                                                  ("question", "SEALME question?", None, None, None),
                                                  ("mirrored", "SEALME mirrored", "s1", now * 1000, "session"),
                                                  ("itself", "SEALME to itself", None, None, None)):
        r = events[ids[name]]
        slot = e2e.event_slot(keys.account, mac.link_id, r["kind"], mirror, None, r["client_id"])
        assert open_envelope(private, r["text"], slot) == {"text": words, "session": session, "said_at": said_at}
    assert events[ids["all"]]["text"] == "LEFTCLEAR to all" and events[ids["cloud"]]["text"] == "LEFTCLEAR to cloud"
    assert events["c" * 16]["text"] == "LEFTCLEAR a short client id"
    assert events[f"stopped:{stopped_at}"]["text"] == stopped
    assert rows("ade_link_heads", user_id=keys.account)[0]["seq"] == head
    listed = {r["session_id"]: r for r in rows("ade_link_history", link_id=mac.link_id)}
    assert open_envelope(private, listed["s1"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s1", False)) \
        == {"title": "SEALME title", "project": "~/SEALME", "branch": "main", "repositories": ["~/SEALME-repo"]}
    assert (listed["s1"]["title"], listed["s1"]["project"], listed["s1"]["branch"], listed["s1"]["repositories"]) == \
        ("", None, None, None)
    assert open_envelope(private, listed["s2"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s2", True)) \
        == {"project": "~/SEALME-withheld"}
    assert listed["s3"]["sealed"] is None
    # as a link seals a row: its repositories a list, dropped from the end until the row fits in 6,000 bytes
    opened = open_envelope(private, listed["s4"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s4", False))
    assert opened["repositories"] == many[:19] and len(canonicalize(opened)) <= e2e.HISTORY_PLAIN
    assert open_envelope(private, listed["s5"]["sealed"], e2e.history_slot(keys.account, mac.link_id, "s5", False)) \
        == {"title": "SEALME none", "project": None, "branch": None, "repositories": []}
    (talk,) = rows("ade_link_history_talks", link_id=mac.link_id)
    for i, m in enumerate(json.loads(talk["messages"])):
        slot = e2e.talk_slot(keys.account, mac.link_id, "s1", i, 2, 2, m["who"], m["at"], m.get("ms"))
        assert open_envelope(private, m["text"], slot) == {"text": said[i]["text"]}
    # the database file holds no page of what was sealed, it was rebuilt with no free page, and its write-ahead log
    # was emptied
    assert os.path.getsize(path + "-wal") == 0
    watcher.close()
    with open(path, "rb") as fh:
        whole = fh.read()
    assert b"SEALME" not in whole and b"LEFTCLEAR" in whole
    with store.connect() as db:
        assert db.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert view(client, owner)["to_seal"] is None                     # nothing is offered while encryption is on


def test_turning_on_without_sealing_in_place_leaves_the_clear_texts(client, owner, keys):
    mac = paired(client, owner)
    assert post(mac, "step", "kept in the clear", client_id=uuid.uuid4().hex).status_code == 200
    got, _ = keys.turn_on(seal=False)
    assert got["sealed"] is None
    assert rows("ade_link_events", link_id=mac.link_id)[0]["text"] == "kept in the clear"
    assert view(client, owner)["compaction_due"] is False


def test_a_compaction_a_reader_keeps_from_completing_stays_due_until_it_completes(client, owner, keys, env,
                                                                                  monkeypatch):
    """A reader holding an older state of the database (another request's read transaction) keeps the checkpoint from
    emptying the log, and the old pages from leaving the file: the compaction says it did not complete, and the
    account's database stays due for one (GET says so) until one does, at the next sweep."""
    mac = paired(client, owner)
    assert post(mac, "step", "SEALME step", client_id=uuid.uuid4().hex).status_code == 200
    monkeypatch.setattr(e2e_keys, "COMPACT_TRIES", 2)
    monkeypatch.setattr(e2e_keys, "COMPACT_WAIT", 0)
    path = os.path.join(str(env), "open_edition.db")
    watcher = sqlite3.connect(path)          # open throughout, so the log is emptied by a checkpoint, not removed
    watcher.execute("SELECT COUNT(*) FROM ade_links").fetchall()
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM ade_link_events").fetchall()       # a read that holds its state
    got, private = keys.turn_on()
    assert got["sealed"]["events"] == 1
    v = view(client, owner)
    assert v["compaction_due"] is True and v["sealed"]["at"] is not None
    assert os.path.getsize(path + "-wal") > 0
    started = time.monotonic()
    assert e2e_keys.compact_if_due() is False                                 # still kept from completing
    assert time.monotonic() - started < 5          # each checkpoint waits a tenth of a second for the reader, not ten
    assert view(client, owner)["compaction_due"] is True
    reader.rollback()
    reader.close()
    assert e2e_keys.compact_if_due() is True
    assert view(client, owner)["compaction_due"] is False
    assert os.path.getsize(path + "-wal") == 0
    watcher.close()
    with open(path, "rb") as fh:
        assert b"SEALME" not in fh.read()
    assert e2e_keys.compact_if_due() is None                                  # none due now


def test_a_compaction_tries_the_checkpoint_again_once_a_reader_lets_it(client, owner, keys, env, monkeypatch):
    import types
    keys.turn_on()
    path = os.path.join(str(env), "open_edition.db")
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM ade_links").fetchall()            # holds the state from before the rebuild
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        reader.rollback()                                                  # it ends while the compaction waits
    monkeypatch.setattr(e2e_keys, "time", types.SimpleNamespace(sleep=sleep, time=time.time))
    assert e2e_keys.compact(tries=3, wait=0.25) is True
    assert waits == [0.25]
    reader.close()


def test_a_compaction_completes_only_when_its_checkpoint_says_it_did_and_the_log_is_empty(monkeypatch):
    import contextlib
    import types
    said = {"busy": 1}

    @contextlib.contextmanager
    def connect(path=None):
        with store.connect(path) as db:
            class Saying:                            # the database's own answers, but the checkpoint's busy
                def execute(self, sql, *args):
                    got = db.execute(sql, *args)
                    if "wal_checkpoint" not in sql:
                        return got
                    row = got.fetchone()
                    return types.SimpleNamespace(fetchone=lambda: (said["busy"], row[1], row[2]))
            yield Saying()
    monkeypatch.setattr(e2e_keys, "store", types.SimpleNamespace(connect=connect))
    assert e2e_keys.compact(tries=2, wait=0) is False           # it says a reader kept it busy: the log is empty
    said["busy"] = 0
    assert e2e_keys.compact(tries=2, wait=0) is True
    monkeypatch.setattr(e2e_keys, "_wal_size", lambda path: 4152)  # done, and written to since: the log is not empty
    assert e2e_keys.compact(tries=2, wait=0) is False


def test_a_compaction_clears_only_the_flags_that_were_there_before_it_began(client, owner, keys, monkeypatch):
    """A sealing in place committed while a compaction runs may have written after its rebuild: its flag stays, for the
    next."""
    keys.turn_on()
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET compaction_due = 'before' WHERE user_id = ?", (keys.account,))
    real = e2e_keys.compact

    def compact_and_a_sealing_meanwhile(tries=None, wait=None, path=None):
        done = real(tries, wait, path)
        with store.connect() as db:
            db.execute("UPDATE ade_e2e_state SET compaction_due = 'meanwhile' WHERE user_id = ?", (keys.account,))
        return done
    monkeypatch.setattr(e2e_keys, "compact", compact_and_a_sealing_meanwhile)
    assert e2e_keys.compact_if_due() is True
    assert rows("ade_e2e_state")[0]["compaction_due"] == "meanwhile"
    monkeypatch.setattr(e2e_keys, "compact", real)
    assert e2e_keys.compact_if_due() is True and rows("ade_e2e_state")[0]["compaction_due"] is None


def test_the_request_that_ends_a_turn_on_s_hold_does_not_wait_for_the_compaction(client, owner, keys, monkeypatch):
    """The request that settles a turn-on's hold commits the sealing alone; the compaction after it runs in a thread of
    its own, which that request does not wait for, one compaction at a time: a second waits for the first, then finds
    nothing left to do."""
    began, release, lock, running = threading.Event(), threading.Event(), threading.Lock(), {"now": 0, "most": 0}

    def slow(tries=None, wait=None, path=None):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        began.set()
        release.wait(10)
        with lock:
            running["now"] -= 1
        return True
    monkeypatch.setattr(e2e_keys, "compact", slow)
    mac = paired(client, owner)
    assert post(mac, "step", "SEALME", client_id=uuid.uuid4().hex).status_code == 200
    body, signed, private = keys.turn_on_body()
    assert at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body)).status_code == 200
    started = time.monotonic()
    v = view(client, owner)                                          # this read settles the hold, and seals
    assert time.monotonic() - started < 3 and v["on"] is True and v["compaction_due"] is True
    assert began.wait(5)
    other = e2e_keys.compact_soon()
    time.sleep(0.3)
    assert running["most"] == 1 and other.is_alive()                 # it waits for the first
    release.set()
    compacted()
    assert running["most"] == 1 and view(client, owner)["compaction_due"] is False


def test_a_compaction_begun_after_a_sealing_compacts_the_database_that_sealing_wrote(tmp_path, monkeypatch):
    """compact_soon takes the database the request that sealed wrote, and the compaction connects to that one alone:
    the app's database may be another by the time its thread runs (as for a test that has moved on), and is left as it
    is."""
    sealed, since = str(tmp_path / "sealed.db"), str(tmp_path / "since.db")
    with store.connect(sealed) as db:
        db.execute("INSERT INTO ade_e2e_state (user_id, compaction_due) VALUES (?, ?)", ("someone", "token"))
    asked = iter([sealed])                          # the request's database, then another to whoever asks after
    monkeypatch.setattr(e2e_keys.config, "db_path", lambda: next(asked, since))
    compacted_at, real = [], e2e_keys.compact
    monkeypatch.setattr(e2e_keys, "compact",
                        lambda tries=None, wait=None, path=None: compacted_at.append(path) or real(tries, wait, path))
    e2e_keys.compact_soon().join(30)
    assert compacted_at == [sealed] and not os.path.exists(since)
    with store.connect(sealed) as db:
        assert db.execute("SELECT compaction_due FROM ade_e2e_state WHERE user_id = 'someone'").fetchone()[0] is None


def test_the_sweep_compacts_at_once_and_then_again_and_a_failure_never_stops_it(monkeypatch):
    import types
    rounds, second, park = [], threading.Event(), threading.Event()

    def due():
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("a round fails")
        second.set()
        park.wait()                                  # hold the thread here: it must not run on after the test
    monkeypatch.setattr(e2e_keys, "compact_if_due", due)
    monkeypatch.setattr(e2e_keys, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time))
    thread = e2e_keys.start(every=1)
    assert thread.daemon and thread.name == "ade-e2e"
    assert second.wait(5) and len(rounds) == 2


def test_serving_the_app_starts_the_sweep(monkeypatch):
    from fastapi.testclient import TestClient
    from proofpane_open import main
    started = []
    monkeypatch.setattr(main.ade_e2e_keys, "start", lambda every=None: started.append(every))
    monkeypatch.setattr(main.ade_push, "start", lambda every=None: None)
    monkeypatch.setattr(main.ade_moves, "start", lambda every=None: None)
    with TestClient(main.app):
        pass
    assert started == [None]


# ---- the changes held while encryption is on -------------------------------------------------------------------------

def test_a_change_waits_on_its_hold_whole_and_takes_effect_when_it_ends(client, owner, keys, notices):
    mac = paired(client, owner)
    keys.turn_on()
    first = e2e.key_id(raw_public(keys.key()))
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD and hold["takes_effect_at"] - hold["approved_at"] == 180
    assert hold["summary"] == "A new content key for end-to-end encryption was signed"
    # the notice's words: a change waits, and when; nothing of the recovery code, which did not confirm it
    assert told(notices, hold) == told_as(hold, "held-e2e")
    v = view(client, owner)
    assert (v["pending"]["change"], v["pending"]["id"]) == ("rotate", hold["id"])
    assert [s["seq"] for s in v["statements"]] == [1] and [k["key_id"] for k in v["content_keys"]] == [first]
    assert mac.call("GET", "/state").json()["e2e"] == {"seq": 1, "key_id": first}
    assert holds.settle(keys.account, now=hold["takes_effect_at"] - 1) == []
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    keys.took(signed, private)
    v = view(client, owner)
    assert [s["seq"] for s in v["statements"]] == [1, 2] and v["pending"] is None
    assert {k["key_id"] for k in v["content_keys"]} == {first, signed["payload"]["key_id"]}
    assert v["statements"][0]["retired_at"] == hold["takes_effect_at"] + e2e_keys.GRACE     # a grace of an hour
    assert mac.call("GET", "/state").json()["e2e"] == {"seq": 2, "key_id": signed["payload"]["key_id"]}


def test_a_statement_is_the_next_by_its_prev_as_well_as_its_seq(client, owner, keys):
    keys.turn_on()
    for seq, prev in ((2, "f" * 64), (3, keys.prev), (1, "")):
        signed, private = keys.statement(seq=seq, prev=prev)
        r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                     "content_key": keys.content_key(private)})
        assert r.status_code == 409 and r.json()["seq"] == 2 and r.json()["prev"] == keys.prev, (seq, r.text)
    assert rows("ade_e2e_pending") == []


def test_a_key_is_stated_once_and_its_newest_statement_says_whether_it_is_retired(client, owner, keys):
    """A key retired is never stated again, so it cannot be made one links seal to once more: refused as it is asked,
    and as its hold ends if one stated it meanwhile. Where a key was stated twice all the same (a database from before
    that rule), the newest statement of it decides whether it is retired."""
    keys.turn_on()
    first = keys.key()
    signed, _ = keys.statement(private=first)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(first)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.KEY_STATED, r.text
    signed, private = keys.statement()
    hold = client.post("/api/ade/e2e/rotate", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private)}).json()["hold"]
    with store.connect() as db:          # stated meanwhile (written into the database here)
        db.execute("UPDATE ade_e2e_statements SET key_id = ? WHERE seq = 1", (signed["payload"]["key_id"],))
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.KEY_STATED
    assert len(rows("ade_e2e_content_keys")) == 1
    kid, now = "a" * 32, int(time.time())
    with store.connect() as db:
        for seq in (7, 8):
            db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, key_id, payload, created_at) VALUES "
                       "(?, ?, ?, 'e2e-key', ?, '{}', 1)", (keys.account, seq, uuid.uuid4().hex, kid))
        for retired, said in (((7, now - 1), None), ((8, now - 1), "retired")):
            db.execute("UPDATE ade_e2e_statements SET retired_at = NULL WHERE key_id = ?", (kid,))
            db.execute("UPDATE ade_e2e_statements SET retired_at = ? WHERE seq = ?", (retired[1], retired[0]))
            assert e2e_keys.key_state(db, keys.account, kid, now) == said, retired


def test_a_change_cancelled_is_discarded_and_never_takes_effect(client, owner, keys):
    keys.turn_on()
    signed, private = keys.statement()
    body = {"statement": signed, "content_key": keys.content_key(private)}
    hold = client.post("/api/ade/e2e/rotate", headers=owner, json=body).json()["hold"]
    r = cancel(keys, hold["id"], keys.device)
    assert r.status_code == 200 and r.json()["cancelled_at"] is not None
    assert rows("ade_e2e_pending") == []
    assert holds.settle(keys.account, now=hold["takes_effect_at"] + 10) == []
    assert [s["seq"] for s in view(client, owner)["statements"]] == [1]
    assert len(rows("ade_e2e_content_keys")) == 1
    # the body cannot be placed again: its nonce was used as it was asked
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=body)
    assert r.status_code == 400 and "nonce" in r.json()["detail"]
    assert rotate(keys)["hold"]["kind"] == e2e_keys.HOLD


def test_a_change_is_checked_again_when_its_hold_ends_and_nothing_of_it_is_kept_when_it_fails(client, owner, keys):
    keys.turn_on()
    signed, private = keys.statement()
    hold = client.post("/api/ade/e2e/rotate", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private)}).json()["hold"]
    # meanwhile the account's newest statement changed (a restore, here written into the database)
    with store.connect() as db:
        db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, payload, created_at) VALUES "
                   "(?, 2, ?, 'e2e-off', '{}', 1)", (keys.account, uuid.uuid4().hex))
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        ended = holds.get(db, hold["id"])
    assert ended["done_at"] == hold["takes_effect_at"] and "the account's next" in ended["failed"]
    assert [k["key_id"] for k in rows("ade_e2e_content_keys")] == [e2e.key_id(raw_public(keys.key()))]
    assert rows("ade_e2e_pending") == []


def test_a_change_held_for_no_time_is_told_in_effect_whoever_made_it(client, owner, keys, notices, monkeypatch):
    """With no hold (both settings 0) a change takes effect as it is made: its notice says it was made and is in
    effect, one made with the recovery code too, since nothing is left to cancel."""
    keys.turn_on()
    monkeypatch.setenv(e2e_keys.ROTATE_SETTING, "0")
    for proof in (None, "rotate"):
        hold = at_once(lambda: rotate(keys, recovery_proof=keys.proof(proof) if proof else None))["hold"]
        assert hold["takes_effect_at"] == hold["approved_at"], proof
        assert told(notices, hold) == told_as(hold, "held-e2e-now"), proof


def test_the_recovery_code_s_proof_confirms_a_change_held_a_day(client, owner, keys, notices):
    keys.turn_on()
    out = rotate(keys, recovery_proof=keys.proof("rotate"))
    hold = out["hold"]
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY and hold["takes_effect_at"] - hold["approved_at"] == 86400
    # its notice says only the recovery code cancels it
    assert told(notices, hold) == told_as(hold, "held-e2e-code")
    assert cancel(keys, hold["id"]).status_code == 403                  # the sign-in alone cancels this kind neither
    assert view(client, owner)["pending"]["by_code"] is True                # code-backed: only the code cancels it
    r = cancel(keys, hold["id"], keys.device)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    keys.statements.pop()
    for proof, words in ((keys.proof("rotate", X25519PrivateKey.generate()), e2e_keys.PROOF_WRONG),
                         (keys.proof("off"), e2e_keys.PROOF_STALE),
                         ({"challenge_id": keys.proof("rotate")["challenge_id"], "proof": "short"},
                          e2e_keys.PROOF_WRONG)):
        signed, private = keys.statement()
        r = client.post("/api/ade/e2e/rotate", headers=owner, json={
            "statement": signed, "content_key": keys.content_key(private), "recovery_proof": proof})
        assert r.status_code == 403 and r.json()["detail"] == words, r.text
    used = keys.proof("rotate")
    rotate(keys, recovery_proof=used)
    r = cancel(keys, view(client, owner)["pending"]["id"], recovery_proof=keys.proof("cancel"))
    assert r.status_code == 200
    keys.statements.pop()
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private), "recovery_proof": used})
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE                 # once
    # no proof is made for starting again (turn-on is a purpose: the fold that keeps the code)
    assert client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "replace"}).status_code == 422
    other = signed_up(client, OTHER)
    r = client.post("/api/ade/e2e/recovery-proof", headers=other, json={"purpose": "rotate"})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_RECOVERY


# ---- the confirmations, and what only a revocation does --------------------------------------------------------------

def enrol_body(keys, device, own=True, **over):
    """A body that enrols `device`, its credential with a confirmation `device` made itself (its own touch, as the
    page's second touch makes it; `own` a confirmation given instead, or None for none), with what `over` gives."""
    credential = {"passkey_id": passkey_of(keys.client, keys.owner, device)["id"], "salt": b64u(os.urandom(32)),
                  "public_key": b64u(raw_public(X25519PrivateKey.generate())), "wrapped": b64u(os.urandom(120))}
    if own is True:
        own = confirmed(keys.client, keys.owner, device, purpose="e2e-enrol-own")["confirmation"]
    if own is not None:
        credential["confirmation"] = own
    return dict({"credential": credential, "mk_id": keys.mk_id, "grant": keys.grant("x")["sealed"],
                 "roster": b64u(os.urandom(300))}, **over)


@pytest.fixture
def second(client, owner, device):
    """A second passkey of the account, added before encryption is on."""
    d = Authenticator(alg=-8)
    assert add(client, owner, d, name="Second", confirm_with=device).status_code == 200
    return d


def test_enrolling_a_passkey_takes_a_passkey_s_confirmation_never_the_password(client, owner, keys, device, second):
    keys.turn_on()
    for given, status, words in ((confirmed(client, owner), 403, passkeys.NEEDS_PASSKEY),
                                 ({}, 403, passkeys.NEEDS_PASSKEY)):
        r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, **given))
        assert r.status_code == status and r.json()["detail"] == words, r.text
    body = enrol_body(keys, second, **confirmed(client, owner, device, purpose=ENROL))
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD and "“Second”" in hold["summary"]
    assert rows("ade_e2e_pending")[0]["made_by"] == keys.passkey["id"]             # the passkey that confirmed it
    assert [c["passkey_id"] for c in view(client, owner)["credentials"]] == [keys.passkey["id"]]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    v = view(client, owner)
    mine = passkey_of(client, owner, second)
    assert sorted(c["passkey_id"] for c in v["credentials"]) == sorted([keys.passkey["id"], mine["id"]])
    assert sorted(g["to"] for g in v["grants"]) == sorted([keys.passkey["credential_id"], mine["credential_id"],
                                                          "recovery"])
    assert v["roster"]["sealed"] == body["roster"]
    assert {p["id"]: p["enrolled"] for p in v["passkeys"]} == {keys.passkey["id"]: True, mine["id"]: True}
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, **confirmed(client, owner, device, purpose=ENROL)))
    assert r.status_code == 409 and "already" in r.json()["detail"]


def test_enrolling_by_the_recovery_code_waits_a_day_and_a_stale_master_key_is_refused(client, owner, keys, second):
    keys.turn_on()
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, mk_id="c" * 32, recovery_proof=keys.proof("credentials")))
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.STALE_MK
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, recovery_proof=keys.proof("credentials")))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    assert rows("ade_e2e_pending")[0]["made_by"] == e2e_keys.RECOVERY


def test_enrolling_takes_the_added_passkey_s_own_touch_so_a_stolen_passkey_plants_no_key_on_another(client, owner,
                                                                                                    keys, device,
                                                                                                    second):
    """A thief holding an enrolled passkey (`device`) and the sign-in enrols the account's spare
    passkey (`second`) under a key of the thief's own. Without the spare's own confirmation, made at its own touch,
    it is refused (none, the stolen passkey's, the password's), with the code's proof too; each refusal leaves the
    change's confirmation unused. With it, the change waits, its body keeping no confirmation, and the spare's own is
    used up with it."""
    keys.turn_on()
    word = confirmed(client, owner, device, purpose=ENROL)["confirmation"]
    for own in (None, confirmed(client, owner, device, purpose="e2e-enrol-own")["confirmation"],
                confirmed(client, owner)["confirmation"]):
        r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, own=own,
                                                                                  confirmation=word))
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.ENROL_OWN, r.text
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, own=None, recovery_proof=keys.proof("credentials")))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.ENROL_OWN, r.text
    assert rows("ade_e2e_pending") == []
    own = confirmed(client, owner, second, purpose="e2e-enrol-own")["confirmation"]
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, own=own,
                                                                              confirmation=word))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    (pending,) = rows("ade_e2e_pending")
    kept = json.loads(pending["body"])
    assert kept["own_touch"] is True and "confirmation" not in kept["credential"], kept
    assert cancel(keys, hold["id"], device).status_code == 200
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, own=own, **confirmed(client, owner, device, purpose=ENROL)))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_STALE, r.text     # used up


def test_an_enrolment_waiting_without_its_own_touch_is_refused_as_its_hold_ends(client, owner, keys, device, second):
    """An enrolment placed under the earlier rule, with no touch of the passkey added (its body says so: no
    `own_touch`), is refused as its hold ends, and nothing of it is kept."""
    keys.turn_on()
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, **confirmed(client, owner, device, purpose=ENROL)))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    with store.connect() as db:          # as the earlier rule placed it
        body = json.loads(db.execute("SELECT body FROM ade_e2e_pending WHERE hold_id = ?", (hold["id"],)).fetchone()[0])
        del body["own_touch"]
        db.execute("UPDATE ade_e2e_pending SET body = ? WHERE hold_id = ?", (json.dumps(body), hold["id"]))
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.ENROL_OWN
    assert [c["passkey_id"] for c in view(client, owner)["credentials"]] == [keys.passkey["id"]]


def test_renewed_anchors_wait_on_a_hold_and_rewrite_the_credentials_own_wraps_with_their_salts(client, owner, keys,
                                                                                               device):
    """A renewed anchor is the credential's own wrap made again under the key its passkey's PRF output gives for a
    fresh salt: the salt is kept with it, and served with it, or the page would derive the old key and open nothing."""
    keys.turn_on()
    before = view(client, owner)["credentials"][0]
    wrapped, salt = b64u(os.urandom(130)), b64u(os.urandom(32))
    body = {"anchors": [{"passkey_id": keys.passkey["id"], "salt": salt, "wrapped": wrapped}], "mk_id": keys.mk_id}
    for case, anchor in (("no salt", {"passkey_id": keys.passkey["id"], "wrapped": wrapped}),
                         ("a salt of 16 bytes", {"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(16)),
                                                 "wrapped": wrapped}),
                         ("a passkey not enrolled", {"passkey_id": "nobody", "salt": salt, "wrapped": wrapped})):
        r = client.post("/api/ade/e2e/anchors", headers=owner,
                        json=dict(body, anchors=[anchor], **confirmed(client, owner, device, purpose=ANCHOR)))
        assert r.status_code == 422, (case, r.text)
    r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(body, **confirmed(client, owner)))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_PASSKEY
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json=dict(body, **confirmed(client, owner, device, purpose=ANCHOR)))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert {k: view(client, owner)["credentials"][0][k] for k in ("salt", "wrapped")} == \
        {"salt": before["salt"], "wrapped": before["wrapped"]}
    holds.settle(keys.account, now=hold["takes_effect_at"])
    (renewed,) = view(client, owner)["credentials"]
    assert (renewed["salt"], renewed["wrapped"]) == (salt, wrapped) and renewed["public_key"] == before["public_key"]
    assert rows("ade_e2e_credentials")[0]["salt"] == salt


def test_renewed_anchors_renew_the_recovery_code_s_anchor_of_the_code_in_force(client, owner, keys, device):
    """The recovery code unlocks as a passkey does, so its anchor is renewed too (recovery_anchor), naming the recovery
    key it is of: kept only while that is the account's, as it is asked and as its hold ends, so an anchor sealed under
    a code since replaced never takes the new code's place. Only the recovery code's proof writes it, held a day: the
    server cannot tell an anchor that opens from one that does not, so a passkey's confirmation (which a thief with a
    stolen passkey can make) would let it leave the code unable to unlock."""
    keys.turn_on()
    code_key = b64u(raw_public(keys.recovery))
    anchor = b64u(os.urandom(60))
    body = {"anchors": [], "recovery_anchor": {"public_key": code_key, "anchor": anchor}, "mk_id": keys.mk_id}
    for case, bad, status in (
            ("neither a passkey's nor the code's", dict(body, recovery_anchor=None), 422),
            ("anchors that are not a list", dict(body, anchors=None), 422),
            ("an anchor of 27 bytes", dict(body, recovery_anchor={"public_key": code_key, "anchor": b64u(bytes(27))}),
             422),
            ("no recovery key", dict(body, recovery_anchor={"anchor": anchor}), 422),
            ("another recovery key", dict(body, recovery_anchor={"public_key": b64u(raw_public(
                X25519PrivateKey.generate())), "anchor": anchor}), 409)):
        r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(bad, recovery_proof=keys.proof("anchors")))
        assert r.status_code == status, (case, r.text)
    assert r.json()["detail"] == e2e_keys.CODE_CHANGED
    # a passkey's confirmation does not write the code's anchor, whatever the anchor: nothing waits after it, and the
    # refusal leaves that confirmation unused, so it still confirms a renewal of the passkey's own record
    word = confirmed(client, owner, device, purpose=ANCHOR)
    r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(body, **word))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.ANCHOR_CODE, r.text
    assert view(client, owner)["pending"] is None and rows("ade_e2e_pending") == []
    own = {"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))}
    r = client.post("/api/ade/e2e/anchors", headers=owner, json={"anchors": [own], "mk_id": keys.mk_id, **word})
    assert r.status_code == 200, r.text
    assert cancel(keys, r.json()["hold"]["id"], device).status_code == 200
    r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(body, recovery_proof=keys.proof("anchors")))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY
    assert view(client, owner)["recovery"]["anchor"] != anchor                      # nothing before the hold ends
    holds.settle(keys.account, now=hold["takes_effect_at"])
    assert view(client, owner)["recovery"]["anchor"] == anchor
    # the recovery code replaced while one waits: the anchor of the old code is not kept as the new code's
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json=dict(body, recovery_anchor={"public_key": code_key, "anchor": b64u(os.urandom(60))},
                              recovery_proof=keys.proof("anchors")))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    replaced = b64u(raw_public(X25519PrivateKey.generate()))
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_recovery SET public_key = ? WHERE user_id = ?", (replaced, keys.account))
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.CODE_CHANGED
    recovery = view(client, owner)["recovery"]
    assert (recovery["public_key"], recovery["anchor"]) == (replaced, anchor)


def test_a_code_s_anchor_waiting_from_a_passkey_s_confirmation_is_refused_as_its_hold_ends(client, owner, keys, device):
    """A renewal that carries the code's anchor and waits with only a passkey's confirmation (made under the earlier
    rule, before an upgrade) is refused as its hold ends, and the anchor in force stays: the rule holds at both ends."""
    keys.turn_on()
    before = view(client, owner)["recovery"]
    own = {"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))}
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json={"anchors": [own], "mk_id": keys.mk_id, **confirmed(client, owner, device, purpose=ANCHOR)})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    with store.connect() as db:      # as the earlier rule placed it: the code's anchor beside a passkey's confirmation
        body = json.loads(db.execute("SELECT body FROM ade_e2e_pending WHERE hold_id = ?", (hold["id"],)).fetchone()[0])
        body["recovery"] = {"public_key": before["public_key"], "anchor": b64u(os.urandom(60))}
        db.execute("UPDATE ade_e2e_pending SET body = ? WHERE hold_id = ?", (json.dumps(body), hold["id"]))
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.ANCHOR_CODE
    assert view(client, owner)["recovery"] == before
    assert view(client, owner)["credentials"][0]["salt"] != own["salt"]             # nothing of it was kept


def test_the_code_s_renewed_anchor_beside_a_passkey_s_takes_that_passkey_s_own_touch_and_is_named(client, owner, keys,
                                                                                                 device, second,
                                                                                                 notices):
    """The recovery code's renewed anchor and the second passkey's in one change: only the code's proof confirms it (a
    passkey's is refused, with the second's own touch beside it or not), so the second's record still takes its own
    touch; its summary names it, its notice says only the code cancels the change, and only the code does; the
    code's anchor alone names no passkey."""
    keys.turn_on()
    enrol(keys, second, device)
    b = passkey_of(client, owner, second)["id"]
    code = {"public_key": b64u(raw_public(keys.recovery)), "anchor": b64u(os.urandom(60))}

    def anchor(**more):
        return dict({"passkey_id": b, "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))}, **more)
    body = {"anchors": [anchor()], "recovery_anchor": code, "mk_id": keys.mk_id}
    for anchors in ([anchor()], [anchor(**confirmed(client, owner, second, purpose=ANCHOR))]):
        r = client.post("/api/ade/e2e/anchors", headers=owner,
                        json=dict(body, anchors=anchors, **confirmed(client, owner, device, purpose=ANCHOR)))
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.ANCHOR_CODE, r.text
    r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(body, recovery_proof=keys.proof("anchors")))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.ANCHOR_OWN, r.text
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json=dict(body, anchors=[anchor(**confirmed(client, owner, second, purpose=ANCHOR))],
                              recovery_proof=keys.proof("anchors")))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY
    assert hold["summary"] == "Renewed anchors for end-to-end encryption: “Second”"
    assert told(notices, hold) == told_as(hold, "held-e2e-code")
    r = cancel(keys, hold["id"], device)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict(body, anchors=[],
                                                                     recovery_proof=keys.proof("anchors")))
    assert r.status_code == 200 and r.json()["hold"]["summary"] == "Renewed anchors for end-to-end encryption", r.text


def test_while_encryption_is_on_a_passkey_is_removed_and_high_security_turned_on_only_by_a_revocation(
        client, owner, keys, device, second):
    keys.turn_on()
    word = confirmed(client, owner, device, purpose=ENROL)["confirmation"]
    gone = passkey_of(client, owner, second)["id"]
    r = client.request("DELETE", f"/api/account/passkeys/{gone}", headers=owner, json={"confirmation": word})
    assert r.status_code == 409 and r.json()["detail"] == passkeys.REVOKES_ONLY
    r = client.put("/api/account/high-security", headers=owner, json={"on": True, "confirmation": word})
    assert r.status_code == 409 and r.json()["detail"] == passkeys.REVOKES_ONLY
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 2
    # the confirmation was left unused: it still confirms what encryption takes it for
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, confirmation=word))
    assert r.status_code == 200, r.text


def test_while_encryption_is_on_adding_a_passkey_takes_a_passkey_s_word_and_waits_on_a_hold(client, owner, keys,
                                                                                            device):
    # options the password gave before encryption was turned on add no passkey after
    early = client.post("/api/account/passkeys/options", headers=owner,
                        json=confirmed(client, owner, change="passkey-add")).json()
    keys.turn_on()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": early["challenge_id"], "name": "Early", "credential": Authenticator(alg=-8).create(early)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_PASSKEY
    r = client.post("/api/account/passkeys/options", headers=owner, json=confirmed(client, owner, change="passkey-add"))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_PASSKEY
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, device, change="passkey-add")).json()
    new = Authenticator(alg=-8)
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "YubiKey", "credential": new.create(options), "prf": True})
    assert r.status_code == 200 and set(r.json()) == {"held", "told", "kind"}, r.text
    hold = r.json()["held"]
    assert hold["kind"] == e2e_keys.HOLD and "“YubiKey”" in hold["summary"]
    assert rows("ade_e2e_pending")[0]["made_by"] == keys.passkey["id"]             # the passkey that confirmed it
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 1
    holds.settle(keys.account, now=hold["takes_effect_at"])
    names = {p["name"]: p for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    assert set(names) == {"Ana's iPhone", "YubiKey"}
    assert rows("passkeys", id=names["YubiKey"]["id"])[0]["prf"] == 1
    # by the recovery code, for an owner who lost every passkey: held a day
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"recovery_proof": keys.proof("passkey")}).json()
    third = Authenticator(alg=-257)
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "Spare", "credential": third.create(options)})
    assert r.status_code == 200 and r.json()["held"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    assert rows("ade_e2e_pending")[0]["made_by"] == e2e_keys.RECOVERY
    # code-backed: no passkey cancels it, the code does; cancelled, it is never added
    assert cancel(keys, r.json()["held"]["id"], device).status_code == 403
    assert cancel(keys, r.json()["held"]["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    holds.settle(keys.account, now=int(time.time()) + 90000)
    assert "Spare" not in {p["name"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}


def test_while_encryption_is_on_a_change_takes_a_passkey_s_word_where_the_account_has_nothing_else(client, owner,
                                                                                                   keys):
    """A change made while encryption is on takes a passkey's confirmation or the recovery code's proof, never nothing:
    not even where the account has neither a password nor a passkey left to confirm with."""
    keys.turn_on()
    with store.connect() as db:          # written into the database here
        db.execute("UPDATE users SET password_hash = '' WHERE id = ?", (keys.account,))
        db.execute("DELETE FROM passkeys WHERE user_id = ?", (keys.account,))
    r = client.post("/api/ade/e2e/anchors", headers=owner, json={
        "anchors": [{"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(130))}],
        "mk_id": keys.mk_id})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_PASSKEY, r.text


def test_the_recovery_code_adds_a_passkey_only_while_encryption_is_on(client, owner, keys):
    keys.turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"recovery_proof": keys.proof("passkey")}).json()
    with store.connect() as db:          # turned off meanwhile (written into the database here)
        db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, payload, created_at) VALUES "
                   "(?, 2, ?, 'e2e-off', '{}', 1)", (keys.account, uuid.uuid4().hex))
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "Spare", "credential": Authenticator(alg=-8).create(options)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_CONFIRM, r.text
    assert "Spare" not in {p["name"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}


def cancel(keys, hold_id, device=None, **given):
    """POST /api/ade/holds/{id}/cancel as the page sends it: with a confirmation `device` made for it, or with
    `given`."""
    body = dict(confirmed(keys.client, keys.owner, device, purpose=CANCEL), **given) if device is not None else given
    return keys.client.post(f"/api/ade/holds/{hold_id}/cancel", headers=keys.owner, json=body or None)


def held_rotation(keys):
    """A rotation signed and waiting on its hold, which the caller cancels: the hold."""
    signed, private = keys.statement()
    r = keys.client.post("/api/ade/e2e/rotate", headers=keys.owner,
                         json={"statement": signed, "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text
    return r.json()["hold"]


def test_cancelling_a_change_takes_a_passkey_it_does_not_remove_or_the_recovery_code(client, owner, keys, device,
                                                                                    second):
    """Whoever holds a stolen device has its sign-in, and can sign out everywhere: neither cancels a change to
    encryption. Cancelling takes a confirmation made with a passkey the change does not remove, or the recovery code's
    proof, so a thief holding a passkey cannot keep its revocation from taking effect, while the owner's other passkey
    or recovery code cancels anything, which frees the one change that waits. (A third passkey of the account, which
    the revocation keeps, leaves its signer more than itself: (iii) does not apply.)"""
    from test_passkeys import sign_out_everywhere
    spare = Authenticator(alg=-257)
    assert add(client, owner, spare, name="Spare", confirm_with=device).status_code == 200
    keys.turn_on()
    enrol(keys, second, device)
    gone = passkey_of(client, owner, second)["id"]
    kept = passkey_of(client, owner, spare)["id"]
    body = revocation(keys, remove=[gone])[0]
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=body).json()["hold"]
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([keys.passkey["id"], kept])
    thief = confirmed(client, owner, second, purpose=CANCEL)    # the passkey the revocation removes
    for given, words in (({}, e2e_keys.CANCEL_TAKES), (confirmed(client, owner), e2e_keys.CANCEL_TAKES),
                         (thief, e2e_keys.CANCEL_REMOVED),
                         ({"recovery_proof": keys.proof("rotate")}, e2e_keys.PROOF_STALE)):
        r = cancel(keys, hold["id"], **given)
        assert r.status_code == 403 and r.json()["detail"] == words, (given, r.text)
    r = sign_out_everywhere(client, owner)                      # a lost device's step, which the thief can take too
    assert r.status_code == 200, r.text
    keys.owner = owner = {"Authorization": "Bearer " + r.json()["token"]}
    with store.connect() as db:
        assert holds.get(db, hold["id"])["cancelled_at"] is None
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [hold["id"]]
    # another passkey's confirmation cancels it, and frees the one change that waits
    r = cancel(keys, hold["id"], device)
    assert r.status_code == 200 and r.json()["cancelled_at"] is not None, r.text
    assert rows("ade_e2e_pending") == []
    # and so does the recovery code's proof, given for a cancel
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[gone])[0]).json()["hold"]
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    # a rotation removes no passkey: any of the account's cancels it, the refused confirmation above too (left unused),
    # though the sign-in alone does not
    hold = held_rotation(keys)
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([keys.passkey["id"], gone, kept])
    r = cancel(keys, hold["id"])
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_TAKES, r.text
    assert cancel(keys, hold["id"], **thief).status_code == 200
    assert cancel(keys, held_rotation(keys)["id"], device).status_code == 200
    holds.settle(keys.account, now=int(time.time()) + 1000)
    assert [s["seq"] for s in view(client, owner)["statements"]] == [1]                 # none took effect


def test_a_change_takes_the_place_of_the_one_that_waits_only_where_its_maker_could_cancel_that_one(
        client, owner, keys, device, second, three):
    """(v) Whoever holds a stolen passkey could keep a change waiting, one after another, so the owner's would never be
    let in: a change of any kind takes the place of the one that waits (its hold ended, failed, saying so), in the
    transaction that places it, when the passkey that made it could cancel that one, or the recovery code's proof comes
    with it; otherwise it is refused, and the one that waits stays."""
    (a, b, c), third = three
    rotation = held_rotation(keys)
    assert rows("ade_e2e_pending")[0]["made_by"] == a
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[b])[0])
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    with store.connect() as db:
        ended = holds.get(db, rotation["id"])
    assert ended["done_at"] is not None and ended["failed"] == e2e_keys.REPLACED
    assert [(p["hold_id"], p["kind"], p["made_by"]) for p in rows("ade_e2e_pending")] == [(hold["id"], "revoke", a)]
    assert cancel(keys, rotation["id"], device).status_code == 409                          # past cancelling

    def rotation_by(signer, **more):
        signed, private = keys.statement(device=signer)
        return client.post("/api/ade/e2e/rotate", headers=owner,
                           json=dict({"statement": signed, "content_key": keys.content_key(private)}, **more))
    # B, which the revocation removes (C stays, so (iii) does not apply), takes its place with no change of any kind
    body, signed, private = revocation(keys)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=signed_by(keys, body, private, second))
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    r = rotation_by(second)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [hold["id"]]
    # C, which could cancel it, takes its place with a change of another kind
    r = rotation_by(third)
    assert r.status_code == 200, r.text
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.REPLACED
    # a code-backed change waits: no passkey takes its place, not even the one that made it; the code's proof does
    r = rotation_by(device, recovery_proof=keys.proof("rotate"))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    coded = r.json()["hold"]
    for signer in (device, second, third):
        r = rotation_by(signer)
        assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    body = revocation(keys, remove=[b])[0]
    assert client.post("/api/ade/e2e/revoke", headers=owner, json=body).status_code == 409
    r = client.post("/api/ade/e2e/revoke", headers=owner,
                    json=dict(revocation(keys, remove=[b])[0], recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200, r.text
    with store.connect() as db:
        assert holds.get(db, coded["id"])["failed"] == e2e_keys.REPLACED
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [r.json()["hold"]["id"]]
    holds.settle(keys.account, now=rotation["takes_effect_at"] - 1)         # before its time: nothing of any
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1] and len(rows("ade_e2e_content_keys")) == 1


def test_a_revocation_that_makes_a_new_recovery_code_waits_a_day_and_the_code_in_force_cancels_it(client, owner,
                                                                                                  keys, device,
                                                                                                  notices):
    """(i) The recovery code in force when a change was made cancels it, a change that replaces that code included: an
    owner who kept the code has the last word over what a stolen passkey signed. A new code waits a day."""
    keys.turn_on()
    new_code = X25519PrivateKey.generate()
    body = revocation(keys, recovery={"public_key": b64u(raw_public(new_code)), "anchor": b64u(os.urandom(60))})[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert (hold["kind"], hold["takes_effect_at"] - hold["approved_at"]) == (e2e_keys.HOLD_LONG, 86400)
    # its notice says a change waits, and its time, a day off; the page says it makes a new code
    assert told(notices, hold) == told_as(hold, "held-e2e")
    assert view(client, owner)["pending"]["replaces_code"] is True
    assert view(client, owner)["pending"]["cancel_by_code"] is True
    r = cancel(keys, hold["id"], recovery_proof=keys.proof("cancel"))
    assert r.status_code == 200 and r.json()["cancelled_at"] is not None, r.text
    # one that keeps the code and removes no passkey waits a day too, and the code cancels it too
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0]).json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG and view(client, owner)["pending"]["cancel_by_code"] is True
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    # confirmed by the recovery code itself, its notice says only the code cancels it, it waits the day it would
    # anyway, and that code cancels it
    again = revocation(keys, recovery={"public_key": b64u(raw_public(new_code)), "anchor": b64u(os.urandom(60))})[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(again, recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    assert told(notices, r.json()["hold"]) == told_as(r.json()["hold"], "held-e2e-code")
    assert cancel(keys, r.json()["hold"]["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    # a proof from another key than the code in force does not
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0]).json()["hold"]
    r = cancel(keys, hold["id"], recovery_proof=keys.proof("cancel", new_code))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_WRONG, r.text


def new_code_body():
    """A new recovery code's key and anchor, as a revocation that makes one carries them."""
    return {"public_key": b64u(raw_public(X25519PrivateKey.generate())), "anchor": b64u(os.urandom(60))}


def signed_by(keys, body, private, device, **more):
    """A revocation's body signed with another passkey (`device`) than the page's own: its statement made again."""
    return dict(body, statement=keys.statement(retires="all", private=private, device=device)[0], **more)


@pytest.fixture
def three(client, owner, keys, device, second):
    """Encryption on, with three passkeys that unlock it: the page's own (A), the second (B) and a third (C). (their
    passkey ids, the third's authenticator)."""
    third = Authenticator(alg=-257)
    assert add(client, owner, third, name="Third", confirm_with=device).status_code == 200
    keys.turn_on()
    enrol(keys, second, device)
    enrol(keys, third, device)
    return (keys.passkey["id"], passkey_of(client, owner, second)["id"], passkey_of(client, owner, third)["id"]), third


def test_a_passkey_a_revocation_removes_cancels_it_only_when_it_leaves_no_credential_besides_its_signer(
        client, owner, keys, device, second, three):
    """(iii): a passkey a revocation removes cancels it when the revocation, not code-backed and signed with a passkey,
    leaves no passkey besides its signer, whatever it does to the recovery code. Not when another passkey stays, and
    no passkey at all when the recovery code's proof came with it (code-backed: the code alone cancels it)."""
    (a, b, c), _ = three
    for remove, recovery, proof, words, cancel_by in (
            ([b, c], None, False, "keeps the code, leaves only its signer", [a, b, c]),
            ([b], new_code_body(), False, "leaves the third", [a, c]),
            ([b, c], new_code_body(), False, "replaces the code, leaves only its signer", [a, b, c]),
            ([b, c], None, True, "code-backed", [])):
        body = revocation(keys, remove=remove, recovery=recovery)[0]
        if proof:
            body["recovery_proof"] = keys.proof("revoke")
        hold = client.post("/api/ade/e2e/revoke", headers=owner, json=body).json()["hold"]
        pending = view(client, owner)["pending"]
        assert (sorted(pending["cancel_by"]), pending["cancel_by_code"], pending["by_code"]) == \
            (sorted(cancel_by), True, proof), words
        if b in cancel_by:                                  # the passkey it removes cancels it, as the view says
            assert cancel(keys, hold["id"], second).status_code == 200, words
            continue
        r = cancel(keys, hold["id"], second)
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, words
        if a in cancel_by:
            assert cancel(keys, hold["id"], device).status_code == 200, words
        else:
            assert cancel(keys, hold["id"], device).status_code == 403, words
            assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200, words


def test_one_stolen_passkey_s_revocation_is_cancelled_or_taken_over_by_a_passkey_it_removes(client, owner, keys, device,
                                                                                         second, three):
    """The thief's case: one stolen passkey (B) signs a revocation that removes every other passkey (A, C) and
    replaces the recovery code, which, before, nothing the owner holds could cancel. A passkey it removes (A) cancels
    it, or puts its own revocation (B removed, C and the code kept) in its place at once; the thief can then neither
    cancel that one nor replace it, and it takes effect."""
    (a, b, c), _ = three

    def thiefs():
        body, signed, private = revocation(keys, remove=[a, c], recovery=new_code_body())
        return signed_by(keys, body, private, second)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=thiefs())
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG
    pending = view(client, owner)["pending"]
    assert (sorted(pending["cancel_by"]), pending["cancel_by_code"]) == (sorted([a, b, c]), True)
    assert cancel(keys, hold["id"], device).status_code == 200                  # A cancels it
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=thiefs()).json()["hold"]
    mine, signed, private = revocation(keys, remove=[b])                        # A's, in its place
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=mine)
    assert r.status_code == 200, r.text
    ours = r.json()["hold"]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.REPLACED
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([a, c])
    r = cancel(keys, ours["id"], second)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=thiefs())
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [ours["id"]]
    assert holds.settle(keys.account, now=ours["takes_effect_at"]) == [ours["id"]]
    keys.took(signed, private)
    keys.mk_id = mine["mk_id"]
    assert sorted(p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == \
        sorted([a, c])
    assert sorted(cr["passkey_id"] for cr in view(client, owner)["credentials"]) == sorted([a, c])


def test_the_recovery_code_s_proof_with_a_revocation_lets_it_take_a_waiting_revocation_s_place(client, owner, keys,
                                                                                               device, second, three):
    """(v) under (i): a revocation whose signer could not cancel the waiting one takes its place when the recovery code's
    proof comes with it, as that code could cancel it."""
    (a, b, c), _ = three
    first = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[b])[0]).json()["hold"]
    body, signed, private = revocation(keys)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=signed_by(keys, body, private, second))
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text     # C stays: B may not cancel it
    body, signed, private = revocation(keys)
    r = client.post("/api/ade/e2e/revoke", headers=owner,
                    json=signed_by(keys, body, private, second, recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    with store.connect() as db:
        assert holds.get(db, first["id"])["failed"] == e2e_keys.REPLACED


def test_a_lost_passkey_cannot_cancel_or_take_over_the_revocation_that_removes_it(client, owner, keys, device,
                                                                                  second):
    """The lost phone's case: the owner, with another passkey and the recovery code, revokes the lost one (A), signed
    with the other and confirmed with the code. Code-backed, only the code cancels it: whoever found A neither cancels
    it nor puts a change of its own in its place (nor does the owner's other passkey cancel it: the code does)."""
    keys.turn_on()
    enrol(keys, second, device)
    a = keys.passkey["id"]
    body, signed, private = revocation(keys, remove=[a])
    r = client.post("/api/ade/e2e/revoke", headers=owner,
                    json=signed_by(keys, body, private, second, recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    hold = r.json()["hold"]
    pending = view(client, owner)["pending"]
    assert (pending["cancel_by"], pending["cancel_by_code"], pending["by_code"]) == ([], True, True)
    for finder in (device, second):
        r = cancel(keys, hold["id"], finder)
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0])        # signed with A
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    with store.connect() as db:
        assert holds.get(db, hold["id"])["cancelled_at"] is None and holds.get(db, hold["id"])["done_at"] is None


def test_a_revocation_turning_high_security_on_is_not_cancelled_by_a_passkey_it_leaves_out(client, owner, metadata,
                                                                                           maker):
    """Turning high security on leaves every passkey but the hardware keys out of the new master key: R. Their
    confirmations do not cancel it while another passkey besides its signer stays: here a second hardware key on the
    account's list, never enrolled, which counts all the same. (Where the one left out is the only other, (iii) lets it
    cancel: test_with_high_security_who_may_cancel_counts_only_the_passkeys_that_could.)"""
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    spare, r = add_hardware(client, owner, maker, name="Spare", confirm_with=yubikey)
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    hold = client.post("/api/ade/e2e/revoke", headers=owner,
                       json=revocation(keys, high_security=True)[0]).json()["hold"]
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([keys.passkey["id"],
                                                                         passkey_of(client, owner, spare)["id"]])
    r = cancel(keys, hold["id"], phone)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    assert cancel(keys, hold["id"], yubikey).status_code == 200


def test_while_high_security_is_on_only_a_hardware_key_s_confirmation_cancels_a_change(client, owner, metadata,
                                                                                       maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    early = confirmed(client, owner, phone, purpose=CANCEL)     # made just before high security was turned on
    r = client.put("/api/account/high-security", headers=owner, json={"on": True, **confirmed(client, owner, yubikey, change="high-security-on")})
    assert r.status_code == 200, r.text
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    hold = held_rotation(keys)
    assert view(client, owner)["pending"]["cancel_by"] == [keys.passkey["id"]]
    r = cancel(keys, hold["id"], **early)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE, r.text
    assert cancel(keys, hold["id"], yubikey).status_code == 200


def test_a_confirmation_cancels_only_while_the_passkey_that_made_it_is_the_account_s(client, owner, keys, device,
                                                                                    second):
    keys.turn_on()
    hold = held_rotation(keys)
    word = confirmed(client, owner, second, purpose=CANCEL)
    with store.connect() as db:          # removed since it confirmed (written into the database here)
        db.execute("DELETE FROM passkeys WHERE id = ?", (passkey_of(client, owner, second)["id"],))
    r = cancel(keys, hold["id"], **word)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_GONE, r.text
    once = confirmed(client, owner, device, purpose=CANCEL)
    assert cancel(keys, hold["id"], **once).status_code == 200
    r = cancel(keys, held_rotation(keys)["id"], **once)                 # a confirmation cancels once
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_STALE, r.text


def test_a_confirmation_is_spent_on_the_change_it_was_made_for_alone(client, owner, keys, device, second):
    """A touch given for one change (enrolling a passkey, renewing anchors, cancelling the change that waits, or a
    change to the account) once confirmed any other. Each takes only a confirmation made for it: one made
    for another is refused, and left unused, so each then confirms its own."""
    keys.turn_on()
    # the change's confirmation is made for the ticket it is spent on; the touches are bound by their purpose
    word = {made_for: confirmed(client, owner, device, purpose=made_for,
                                change="link-ticket:new" if made_for == passkeys.CONFIRM else None)
            for made_for in (ENROL, ANCHOR, CANCEL, passkeys.CONFIRM)}
    hold = held_rotation(keys)
    own = {"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))}
    changes = {ENROL: lambda w: client.post("/api/ade/e2e/credentials", headers=owner,
                                            json=enrol_body(keys, second, **w)),
               ANCHOR: lambda w: client.post("/api/ade/e2e/anchors", headers=owner,
                                             json={"anchors": [own], "mk_id": keys.mk_id, **w}),
               CANCEL: lambda w: cancel(keys, hold["id"], **w),
               passkeys.CONFIRM: lambda w: client.post("/api/ade/link/tickets", headers=owner, json=w)}
    for made_for, given in word.items():
        for change, ask in changes.items():
            if change != made_for:
                r = ask(given)
                assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), (made_for, change)
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [hold["id"]]           # nothing placed or cancelled
    for change in (CANCEL, ENROL, ANCHOR, passkeys.CONFIRM):
        r = changes[change](word[change])
        assert r.status_code == 200, (change, r.text)


def test_a_passkey_s_own_touch_counts_only_when_made_for_its_own_part(client, owner, keys, device, second):
    """The touch of the passkey an enrolment adds, and of each renewed passkey but the confirming
    one, is taken only when made for that part (e2e-enrol-own, e2e-anchor-touch): the same passkey's touch made for
    another part is refused, and the change's own confirmation is left unused."""
    keys.turn_on()
    word = confirmed(client, owner, device, purpose=ENROL)
    for made_for in (ENROL, ANCHOR, CANCEL, passkeys.CONFIRM):
        own = confirmed(client, owner, second, purpose=made_for)["confirmation"]
        r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, own=own, **word))
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), (made_for, r.text)
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=enrol_body(keys, second, **word))
    assert r.status_code == 200, r.text
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    b = passkey_of(client, owner, second)["id"]
    word = confirmed(client, owner, device, purpose=ANCHOR)
    for made_for in (ENROL, "e2e-enrol-own", CANCEL, passkeys.CONFIRM):
        anchor = {"passkey_id": b, "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120)),
                  **confirmed(client, owner, second, purpose=made_for)}
        r = client.post("/api/ade/e2e/anchors", headers=owner, json={"anchors": [anchor], "mk_id": keys.mk_id, **word})
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), (made_for, r.text)
    anchor = dict(anchor, **confirmed(client, owner, second, purpose=ANCHOR))
    r = client.post("/api/ade/e2e/anchors", headers=owner, json={"anchors": [anchor], "mk_id": keys.mk_id, **word})
    assert r.status_code == 200, r.text


def enrol(keys, device, confirm_with):
    """`device` enrolled to unlock the keys, its hold settled at its time."""
    word = confirmed(keys.client, keys.owner, confirm_with, purpose=ENROL)
    r = keys.client.post("/api/ade/e2e/credentials", headers=keys.owner, json=enrol_body(keys, device, **word))
    assert r.status_code == 200, r.text
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])


def staying(keys, remove=(), high_security=False):
    """The credential ids of the account's enrolled credentials a revocation keeps, as the server counts them: all but
    those `remove` names, and, turning high security on or with it on, all but the hardware keys'."""
    with store.connect() as db:
        high = high_security or passkeys._high_security(db, keys.account)
        return [r["credential_id"] for r in db.execute(
            "SELECT c.credential_id, c.passkey_id, p.tier FROM ade_e2e_credentials c JOIN passkeys p ON p.id = "
            "c.passkey_id WHERE c.user_id = ?", (keys.account,)).fetchall()
            if r["passkey_id"] not in remove and (not high or r["tier"] == passkeys.HARDWARE)]


def revocation(keys, retires="all", **over):
    """A revocation's body as the page makes it: a new master key chained from the current one, granted to every
    credential that stays enrolled (staying) and to the recovery key, every content key wrapped again, a new content
    key. (the body, the signed statement, the new content key's private half)."""
    signed, private = keys.statement(retires=retires)
    mk_id = os.urandom(16).hex()
    body = {"statement": signed, "content_key": keys.content_key(private, mk_id), "mk_id": mk_id,
            "chain": {"previous": keys.mk_id, "wrapped": b64u(os.urandom(60))},
            "roster": b64u(os.urandom(300)),
            "content_keys": [{"key_id": k, "wrapped": b64u(os.urandom(76))} for k in keys.content],
            "remove": [], "high_security": False}
    body.update(over)
    if "grants" not in over:
        body["grants"] = [keys.grant(c) for c in staying(keys, body["remove"], body["high_security"])] + \
            [keys.grant("recovery")]
    return body, signed, private


def test_a_revocation_replaces_the_master_key_removes_a_passkey_and_retires_every_earlier_key(client, owner, keys,
                                                                                              device, second,
                                                                                              monkeypatch, notices):
    mac = paired(client, owner)
    keys.turn_on()
    enrol(keys, second, device)
    # it takes effect now, by the clock links post by: it removes the account's other credential, so it waits as
    # long as one the recovery code confirmed (e2e_keys.HOLD_LONG), which is no time here
    monkeypatch.setenv(e2e_keys.HOLD_RECOVERY_SETTING, "0")
    old_mk, gone = keys.mk_id, passkey_of(client, owner, second)
    body, signed, private = revocation(keys, remove=[gone["id"]])
    for case, bad, status in (
            ("a grant to the passkey removed", dict(body, grants=body["grants"] + [keys.grant(gone["credential_id"])]),
             422),
            ("a content key not wrapped again", dict(body, content_keys=[]), 422),
            ("a chain from another master key", dict(body, chain={"previous": "d" * 32, "wrapped": b64u(bytes(60))}),
             409),
            ("the same master key", dict(body, mk_id=old_mk, content_key=dict(body["content_key"], mk_id=old_mk),
                                         chain=dict(body["chain"], previous=old_mk)), 422),
            ("a passkey not the account's", dict(body, remove=["nobody"]), 404),
            ("a grant to the recovery key missing", dict(body, grants=[keys.grant(keys.passkey["credential_id"])]),
             422),
            # a credential that stays without a grant would be left out without being removed
            ("a grant to the credential that stays missing", dict(body, grants=[keys.grant("recovery")]), 422)):
        r = client.post("/api/ade/e2e/revoke", headers=owner, json=bad)
        assert r.status_code == status, (case, r.text)
    graced = revocation(keys, retires="grace")[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=graced)
    assert r.status_code == 422 and "retires: all" in r.json()["detail"]
    r = client.post("/api/ade/e2e/revoke", headers=owner,
                    json=dict(body, grants=[keys.grant(keys.passkey["credential_id"])]))
    assert r.status_code == 422 and "to the recovery key as well" in r.json()["detail"], r.text
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["summary"] == "The keys of end-to-end encryption are being replaced"
    assert hold["kind"] == e2e_keys.HOLD_LONG
    # the page reads the passkey it removes (read a moment before the hold ends: the page's own read settles a hold
    # whose time has come, and this one's has); the notice, only that a change was made, held for no time here
    before = e2e_keys.account_view(keys.account, now=hold["takes_effect_at"] - 1)
    assert before["pending"]["removes"] == [passkey_of(client, owner, second)["id"]]
    assert told(notices, hold) == told_as(hold, "held-e2e-now") and "Second" not in repr(notices)
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 2      # nothing yet
    assert len(rows("ade_e2e_grants")) == 3 and rows("ade_e2e_chain") == []
    holds.settle(keys.account)
    keys.took(signed, private)
    keys.mk_id = body["mk_id"]
    v = view(client, owner)
    assert [p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]] == \
        [keys.passkey["id"]]
    assert [c["passkey_id"] for c in v["credentials"]] == [keys.passkey["id"]]
    assert {g["mk_id"] for g in v["grants"]} == {keys.mk_id}
    assert sorted(g["to"] for g in v["grants"]) == sorted([keys.passkey["credential_id"], "recovery"])
    assert v["mk_id"] == keys.mk_id and [(c["mk_id"], c["previous"]) for c in v["chain"]] == [(keys.mk_id, old_mk)]
    assert {k["mk_id"] for k in v["content_keys"]} == {keys.mk_id} and len(v["content_keys"]) == 2
    assert hold["takes_effect_at"] <= v["statements"][0]["retired_at"] <= int(time.time())     # retired at once
    first = [k for k in keys.content.values() if k is not private][0]
    r = send_sealed(keys, mac, "step", private=first)[0]
    assert r.status_code == 409 and r.json()["seq"] == 2
    assert send_sealed(keys, mac, "step")[0].status_code == 200


def test_a_revocation_that_cannot_take_effect_when_its_hold_ends_keeps_nothing(client, owner, keys, device, second):
    keys.turn_on()
    enrol(keys, second, device)
    body, signed, private = revocation(keys, remove=[passkey_of(client, owner, second)["id"]])
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=body).json()["hold"]
    with store.connect() as db:          # the passkey is gone by the time the hold ends (written in here)
        db.execute("DELETE FROM passkeys WHERE id = ?", (passkey_of(client, owner, second)["id"],))
    before = {t: rows(t) for t in ("ade_e2e_grants", "ade_e2e_chain", "ade_e2e_roster", "ade_e2e_content_keys",
                                    "ade_e2e_statements", "ade_e2e_credentials")}
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == "No such passkey."
    assert {t: rows(t) for t in before} == before


def test_a_revocation_leaves_as_they_are_the_content_keys_it_abandons_each_once(client, owner, keys, notices):
    """A content key listed under a master key the page's keys do not lead back to (a row one who can write the
    database made) is one the page can neither open nor wrap again: a revocation names it in abandon, and it is left
    as it is, so one such row never blocks every revocation. Every content key is wrapped again or abandoned, never
    both, each once; the waiting change the page reads names it, and the notice nothing of it."""
    keys.turn_on()
    junk = X25519PrivateKey.generate()
    junk_id = e2e.key_id(raw_public(junk))
    stranger = os.urandom(16).hex()
    with store.connect() as db:
        db.execute("INSERT INTO ade_e2e_content_keys (user_id, key_id, public_key, mk_id, wrapped, created_at) VALUES "
                   "(?,?,?,?,?,?)", (keys.account, junk_id, b64u(raw_public(junk)), stranger, b64u(os.urandom(76)),
                                     int(time.time())))
    body, signed, private = revocation(keys)
    (held,) = [k for k in keys.content]
    form = "A revocation's abandon is a list of the content keys it leaves as they are, by key_id, each once."
    for case, bad, words in (("the junk key neither wrapped again nor abandoned", body, e2e_keys.EVERY_CONTENT_KEY),
                             ("nothing wrapped again", dict(body, content_keys=[], abandon=[junk_id]),
                              e2e_keys.EVERY_CONTENT_KEY),
                             ("a key wrapped again and abandoned", dict(body, abandon=[junk_id, held]),
                              e2e_keys.EVERY_CONTENT_KEY),
                             ("the junk key abandoned twice", dict(body, abandon=[junk_id, junk_id]), form),
                             ("an abandon that is not key ids", dict(body, abandon=["not a key"]), form),
                             ("an abandon that is not a list", dict(body, abandon=junk_id), form)):
        r = client.post("/api/ade/e2e/revoke", headers=owner, json=bad)
        assert r.status_code == 422 and r.json()["detail"] == words, (case, r.text)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(body, abandon=[junk_id]))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert view(client, owner)["pending"]["body"]["abandon"] == [junk_id]
    assert told(notices, hold) == told_as(hold, "held-e2e")
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] is None
    keys.took(signed, private)
    by_id = {k["key_id"]: k for k in view(client, owner)["content_keys"]}
    assert by_id[junk_id]["mk_id"] == stranger                              # left as it was
    assert by_id[held]["mk_id"] == body["mk_id"] and by_id[e2e.key_id(raw_public(private))]["mk_id"] == body["mk_id"]


def test_the_view_names_what_made_the_change_that_waits_and_what_it_removes(client, owner, keys, device, second):
    keys.turn_on()
    enrol(keys, second, device)
    gone = passkey_of(client, owner, second)
    body, signed, private = revocation(keys, remove=[gone["id"]],
                                       recovery={"public_key": b64u(raw_public(X25519PrivateKey.generate())),
                                                 "anchor": b64u(os.urandom(60))})
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
    assert r.status_code == 200, r.text
    pending = view(client, owner)["pending"]
    assert (pending["made_by"], pending["removes"], pending["replaces_code"]) == (keys.passkey["id"], [gone["id"]], True)
    cancel(keys, r.json()["hold"]["id"], device)
    rotation = rotate(keys)
    pending = view(client, owner)["pending"]
    assert (pending["made_by"], pending["removes"], pending["replaces_code"]) == (keys.passkey["id"], [], False), rotation


def test_a_revocation_turns_high_security_on_and_leaves_the_other_passkeys_out(client, owner, metadata, maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    enrol(keys, phone, yubikey)
    phone_row = passkey_of(client, owner, phone)
    body, signed, private = revocation(keys, high_security=True)
    bad = dict(body, grants=body["grants"] + [keys.grant(phone_row["credential_id"])])
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=bad)
    assert r.status_code == 422 and "stays enrolled" in r.json()["detail"]
    # turning high security on takes a hardware key's word, here the statement's signature, as the switch itself does
    by_phone, _ = keys.statement(retires="all", private=private, device=phone)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(body, statement=by_phone))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE_TO_TURN
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=body).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    v = view(client, owner)
    assert v["high_security"] is True
    assert [c["passkey_id"] for c in v["credentials"]] == [keys.passkey["id"]]          # the phone is not enrolled
    assert {p["name"]: p["can_enrol"] for p in v["passkeys"]} == {"YubiKey": False, "Phone": False}
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 2      # still a passkey
    # and a statement the phone signs is refused, as a command it signs is
    keys.took(signed, private)
    keys.mk_id = body["mk_id"]
    signed, private = keys.statement(device=phone)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    # nor does the phone enrol again while high security is on (refused before its own touch would be asked for, which
    # high security would not let a phone give)
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, phone, own=None, **confirmed(client, owner, yubikey, purpose=ENROL)))
    assert r.status_code == 409 and "only a hardware key" in r.json()["detail"]


def test_a_revocation_keeps_a_hardware_key_while_high_security_is_or_will_be_on(client, owner, metadata, maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    spare, r = add_hardware(client, owner, maker, name="Spare", confirm_with=yubikey)
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    hardware = [passkey_of(client, owner, k)["id"] for k in (yubikey, spare)]
    body = revocation(keys, high_security=True, remove=hardware)[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
    assert r.status_code == 409 and r.json()["detail"] == passkeys.NO_HARDWARE_KEY
    with store.connect() as db:          # a passkey that waits to be judged could become a hardware key later
        db.execute("UPDATE passkeys SET judge_again = 1 WHERE id = ?", (passkey_of(client, owner, phone)["id"],))
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, high_security=True)[0])
    assert r.status_code == 409 and r.json()["detail"] == passkeys.KEY_WAITING
    # removed with it, the waiting one does not stand in the way
    body = revocation(keys, high_security=True, remove=[passkey_of(client, owner, phone)["id"]])[0]
    assert client.post("/api/ade/e2e/revoke", headers=owner, json=body).status_code == 200
    assert rows("ade_e2e_pending")[0]["kind"] == "revoke"
    assert cancel(keys, view(client, owner)["pending"]["id"], yubikey).status_code == 200
    with store.connect() as db:          # high security on before: the last hardware key is not removed
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (keys.account,))
        db.execute("UPDATE passkeys SET judge_again = 0")
    body = revocation(keys, remove=hardware)[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
    assert r.status_code == 409 and r.json()["detail"] == passkeys.LAST_HARDWARE_KEY


def test_a_revocation_that_removes_a_passkey_cancels_each_approval_held_that_names_none(client, owner, keys, device,
                                                                                       second):
    """As removing a passkey from Account does: an approval still held whose confirmation does not say which passkey
    made it (a request to link a session) may have been made with the passkey removed, so it never takes effect
    (holds.cancel_all). A revocation that removes none leaves it."""
    from proofpane_open.ade import link_asks

    def approval(at):
        with store.connect() as db:
            return holds.place(db, keys.account, link_asks.HOLD, uuid.uuid4().hex, "A session was approved", at)

    keys.turn_on()
    enrol(keys, second, device)
    for remove, cancelled in (([], False), ([passkey_of(client, owner, second)["id"]], True)):
        body, signed, private = revocation(keys, remove=remove)
        r = client.post("/api/ade/e2e/revoke", headers=owner, json=body)
        assert r.status_code == 200, r.text
        hold = r.json()["hold"]
        meanwhile = approval(hold["takes_effect_at"] - 100)     # its time comes after the revocation's
        assert hold["id"] in holds.settle(keys.account, now=hold["takes_effect_at"])
        keys.took(signed, private)
        keys.mk_id = body["mk_id"]
        with store.connect() as db:
            assert holds.get(db, hold["id"])["failed"] is None
            assert (holds.get(db, meanwhile["id"])["cancelled_at"] is not None) is cancelled, remove
    assert [p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]] == \
        [keys.passkey["id"]]


def test_a_revocation_does_what_account_s_remove_and_the_high_security_switch_do(client, owner, metadata, maker):
    """A revocation that removes a passkey voids the unused tickets it confirmed, as Account's Remove does; one that
    turns high security on voids those no hardware key confirmed and cancels each approval still held that names no
    passkey, as the switch does (passkeys.remove_in, high_security_in)."""
    from proofpane_open.ade import link_asks
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    phone, tablet = Authenticator(alg=-8), Authenticator(alg=-257)
    for d, name in ((phone, "Phone"), (tablet, "Tablet")):
        assert add(client, owner, d, name=name, confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    ids = {name: passkey_of(client, owner, d)["id"] for name, d in (("yubikey", yubikey), ("phone", phone),
                                                                   ("tablet", tablet))}
    for d in (yubikey, phone, tablet):
        assert client.post("/api/ade/link/tickets", headers=owner, json=confirmed(client, owner, d, change="link-ticket:new")).status_code == 200

    def revoked(body, signed, private, meanwhile=None):
        hold = client.post("/api/ade/e2e/revoke", headers=owner, json=body).json()["hold"]
        approval = meanwhile(hold) if meanwhile else None
        assert holds.settle(keys.account, now=hold["takes_effect_at"])[0] == hold["id"]
        keys.took(signed, private)
        keys.mk_id = body["mk_id"]
        return approval

    def approval(hold):
        with store.connect() as db:          # its time comes after the revocation's
            return holds.place(db, keys.account, link_asks.HOLD, uuid.uuid4().hex, "A session was approved",
                               hold["takes_effect_at"] - 100)

    revoked(*revocation(keys, remove=[ids["tablet"]]))
    assert sorted(t["confirmed_by"] for t in rows("ade_link_tickets")) == sorted([ids["yubikey"], ids["phone"]])
    held = revoked(*revocation(keys, high_security=True), meanwhile=approval)
    assert [t["confirmed_by"] for t in rows("ade_link_tickets")] == [ids["yubikey"]]
    with store.connect() as db:
        assert holds.get(db, held["id"])["cancelled_at"] is not None
    assert passkeys.high_security(keys.account)


def test_turning_off_waits_on_its_hold_and_then_links_send_in_the_clear_again(client, owner, keys):
    mac = paired(client, owner)
    keys.turn_on()
    first = keys.key()
    signed, _ = keys.statement("e2e-off")
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["summary"] == "Turning end-to-end encryption off was signed"
    assert post(mac, "step", "still sealed until it ends").status_code == 409
    holds.settle(keys.account, now=hold["takes_effect_at"])
    keys.took(signed)
    v = view(client, owner)
    assert v["on"] is False and v["newest"] == {"seq": 2, "key_id": None}
    assert mac.call("GET", "/state").json()["e2e"] == {"seq": 2, "key_id": None}
    r = post(mac, "step", "in the clear again")
    assert r.status_code == 200 and r.json()["e2e"] == {"seq": 2, "key_id": None}
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_statements SET retired_at = 1 WHERE seq = 1")
    assert send_sealed(keys, mac, "step", private=first)[0].status_code == 200   # retired or not, both are taken
    # turning it on again: the keys it has, with a content key under its master key; or replace, which forgets them
    body, signed, private = keys.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.FROM_BEFORE
    old_mk = rows("ade_e2e_roster")[0]["mk_id"]
    signed, private = keys.statement()                  # retires grace: every earlier key goes at once instead
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private, old_mk)})
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.RETIRES_ALL
    signed, private = keys.statement(retires="all")
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private, old_mk)}))
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    keys.took(signed, private)
    v = view(client, owner)
    assert v["on"] is True and v["mk_id"] == old_mk and len(v["content_keys"]) == 2
    assert all(s["retired_at"] is not None for s in v["statements"][:-1] if s["purpose"] == "e2e-key")


def test_turning_on_again_with_replace_forgets_the_keys_from_before(client, owner, keys, device, notices):
    """Starting again forgets the keys and the recovery code from before when its hold ends, a day after (whoever made
    it may have taken a passkey): every earlier key retired at once. Until then the recovery code it forgets is the
    code in force, and cancels it, as a passkey does."""
    keys.turn_on()
    turned_off(keys)
    old_mk = keys.mk_id
    body, signed, private = keys.turn_on_body(replace=True)
    graced = dict(body, statement=keys.statement(retires="grace", private=private)[0])
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=graced)
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.RETIRES_ALL, r.text
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert (hold["kind"], hold["takes_effect_at"] - hold["approved_at"]) == (e2e_keys.HOLD_LONG, 86400)
    assert told(notices, hold) == told_as(hold, "held-e2e")              # a change waits, a day off
    v = view(client, owner)
    assert v["pending"]["body"]["replace"] is True                       # the page reads that it forgets them
    assert v["mk_id"] == old_mk and v["pending"]["cancel_by_code"] is True                # nothing forgotten yet
    assert v["pending"]["cancel_by"] == [keys.passkey["id"]]
    assert (v["pending"]["replaces_code"], v["pending"]["removes"]) == (True, [])     # said, deciding nothing
    r = cancel(keys, hold["id"], recovery_proof=keys.proof("cancel"))
    assert r.status_code == 200, r.text
    hold = client.post("/api/ade/e2e/turn-on", headers=owner, json=keys.turn_on_body(replace=True)[0]).json()["hold"]
    assert cancel(keys, hold["id"], device).status_code == 200
    assert view(client, owner)["mk_id"] == old_mk
    got, private = keys.turn_on(replace=True)
    assert got["hold"]["kind"] == e2e_keys.HOLD_LONG
    v = view(client, owner)
    assert v["mk_id"] == keys.mk_id and [k["key_id"] for k in v["content_keys"]] == [got["key_id"]]
    assert len(v["grants"]) == 2 and [s["seq"] for s in v["statements"]] == [1, 2, 3]


def rekey_body(keys, **over):
    """Turning encryption on again with a new master key made from the current one, as a revocation makes it (mk_id,
    chain, grants, roster, content_keys): (the body, the signed statement, the new content key's private half)."""
    body, signed, private = revocation(keys, **over)
    for name in ("remove", "high_security"):
        body.pop(name)
    return body, signed, private


def test_a_passkey_removed_while_encryption_is_off_takes_its_grant_with_it_and_a_new_master_key_to_turn_on_again(
        client, owner, keys, device, second):
    """While encryption is off a passkey can be removed from Account: its credential and its grant go with it at once,
    and since its authenticator still makes the PRF output that opened the master key, turning encryption on again
    makes a new master key, as a revocation does, granted to the credentials that stay; the content key alone under the
    old one is refused."""
    from test_passkeys import remove
    keys.turn_on()
    enrol(keys, second, device)
    turned_off(keys)
    gone = passkey_of(client, owner, second)
    assert view(client, owner)["rekey_due"] is False
    assert remove(client, owner, gone["id"]).status_code == 200
    assert [c["passkey_id"] for c in rows("ade_e2e_credentials")] == [keys.passkey["id"]]
    assert sorted(g["recipient"] for g in rows("ade_e2e_grants")) == sorted([keys.passkey["credential_id"],
                                                                             "recovery"])
    assert view(client, owner)["rekey_due"] is True
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.REKEY_DUE, r.text
    old_mk = keys.mk_id
    body, signed, private = rekey_body(keys)
    for case, bad, status in (
            ("a grant to the passkey removed", dict(body, grants=body["grants"] + [keys.grant(gone["credential_id"])]),
             422),
            ("a grant to the credential that stays missing", dict(body, grants=[keys.grant("recovery")]), 422),
            ("a chain from another master key", dict(body, chain=dict(body["chain"], previous="d" * 32)), 409),
            ("a content key not wrapped again", dict(body, content_keys=[]), 422),
            ("a credential of its own besides", dict(body, credential={"passkey_id": keys.passkey["id"]}), 422)):
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=bad)
        assert r.status_code == status, (case, r.text)
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body))
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    keys.took(signed, private)
    keys.mk_id = body["mk_id"]
    v = view(client, owner)
    assert v["on"] is True and v["rekey_due"] is False and v["mk_id"] == keys.mk_id != old_mk
    assert [(c["mk_id"], c["previous"]) for c in v["chain"]] == [(keys.mk_id, old_mk)]
    assert sorted((g["mk_id"], g["to"]) for g in v["grants"]) == sorted([(keys.mk_id, keys.passkey["credential_id"]),
                                                                          (keys.mk_id, "recovery")])
    assert {k["mk_id"] for k in v["content_keys"]} == {keys.mk_id} and len(v["content_keys"]) == 2
    assert all(s["retired_at"] is not None for s in v["statements"][:-1] if s["purpose"] == "e2e-key")
    # and the next time it is turned off and on, the content key alone does again
    turned_off(keys)
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text


def test_a_new_master_key_made_as_encryption_is_turned_on_again_is_signed_by_a_credential_that_stays(client, owner,
                                                                                                    keys, device,
                                                                                                    second):
    from test_passkeys import remove
    third = Authenticator(alg=-257)
    assert add(client, owner, third, name="Third", confirm_with=device).status_code == 200
    keys.turn_on()
    enrol(keys, second, device)
    turned_off(keys)
    assert remove(client, owner, passkey_of(client, owner, third)["id"]).status_code == 200      # never enrolled
    assert view(client, owner)["rekey_due"] is False                     # it opened nothing: no new master key due
    assert remove(client, owner, passkey_of(client, owner, second)["id"]).status_code == 200
    body, signed, private = rekey_body(keys)
    other = Authenticator(alg=-8)
    assert add(client, owner, other, name="Other", confirm_with=device).status_code == 200
    by_other = keys.statement(retires="all", private=private, device=other)[0]          # not enrolled
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, statement=by_other))
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_ENROLLED, r.text
    assert client.post("/api/ade/e2e/turn-on", headers=owner, json=body).status_code == 200


def test_high_security_turned_on_while_encryption_is_off_leaves_the_other_passkeys_out_of_the_keys(client, owner,
                                                                                                  metadata, maker):
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    enrol(keys, phone, yubikey)
    turned_off(keys)
    r = client.put("/api/account/high-security", headers=owner, json={"on": True, **confirmed(client, owner, yubikey, change="high-security-on")})
    assert r.status_code == 200, r.text
    assert [c["passkey_id"] for c in rows("ade_e2e_credentials")] == [keys.passkey["id"]]
    assert sorted(g["recipient"] for g in rows("ade_e2e_grants")) == sorted([keys.passkey["credential_id"],
                                                                             "recovery"])
    assert view(client, owner)["rekey_due"] is True
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.REKEY_DUE, r.text
    body = rekey_body(keys)[0]
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    assert cancel(keys, r.json()["hold"]["id"], yubikey).status_code == 200
    # starting again makes a new master key as well: none is due after it
    keys.turn_on(replace=True)
    assert view(client, owner)["rekey_due"] is False


def test_a_credential_whose_passkey_is_gone_or_left_out_takes_a_new_master_key_to_turn_on_again(client, owner, keys,
                                                                                               device, second):
    """However it came to be (here, a database restored from before a removal, written in): a credential whose passkey
    the account no longer has, or that is not a hardware key while high security is on, still opens the master key.
    Turning on again makes a new one, and drops it."""
    keys.turn_on()
    enrol(keys, second, device)
    turned_off(keys)
    gone = passkey_of(client, owner, second)["id"]
    with store.connect() as db:
        assert e2e_keys._stale(db, keys.account) == []
        db.execute("DELETE FROM passkeys WHERE id = ?", (gone,))
        assert e2e_keys._stale(db, keys.account) == [gone]
    assert view(client, owner)["rekey_due"] is True
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.REKEY_DUE, r.text
    body, signed, private = rekey_body(keys)
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body))
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    assert [c["passkey_id"] for c in rows("ade_e2e_credentials")] == [keys.passkey["id"]]
    assert view(client, owner)["rekey_due"] is False
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (keys.account,))
        assert e2e_keys._stale(db, keys.account) == [keys.passkey["id"]]          # not a hardware key
    # while encryption is on, a revocation makes a new master key: none is said to be due until it is off
    assert view(client, owner)["rekey_due"] is False
    with store.connect() as db:          # turned off (written into the database here)
        db.execute("INSERT INTO ade_e2e_statements (user_id, seq, id, purpose, payload, created_at) VALUES "
                   "(?, 4, ?, 'e2e-off', '{}', 1)", (keys.account, uuid.uuid4().hex))
    assert view(client, owner)["rekey_due"] is True


# ---- a link's side: the statements, and sending them in after a restore ---------------------------------------------

def test_a_change_whose_hold_has_ended_takes_effect_before_a_link_s_request_meets_it(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    keys.turn_on()
    assert history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).status_code == 200
    for setting in ("OPEN_EDITION_ADE_E2E_HOLD", "OPEN_EDITION_ADE_E2E_RECOVERY_HOLD", e2e_keys.ROTATE_SETTING):
        monkeypatch.setenv(setting, "0")        # held no time, turning off too, and rotations not bounded here
    calls = {
        "events": lambda: send_sealed(keys, mac, "step")[0].json(),
        "history": lambda: history(mac, [sealed_row(keys, mac, "s1", {"title": "t"})], conversations=True).json(),
        "talk": lambda: mac.call("PUT", "/history/talk", {"session": "s1", "messages": talk_messages(
            keys, mac, "s1", [("owner", "hi")])}).json(),
        "state": lambda: mac.call("GET", "/state").json(),
        "statements": lambda: mac.call("GET", "/e2e").json(),
    }
    for name, call in calls.items():
        signed, private = keys.statement()
        r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                     "content_key": keys.content_key(private)})
        assert r.status_code == 200, r.text
        keys.took(signed, private)          # its hold has ended (no seconds); only the link's request settles it
        assert call()["e2e"]["seq"] == len(keys.statements), name
    signed, _ = keys.statement("e2e-off")
    assert client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed}).status_code == 200
    r = post(mac, "step", "in the clear at once")
    assert r.status_code == 200 and r.json()["e2e"]["key_id"] is None


def test_a_statement_sent_in_after_a_restore_is_taken_though_its_passkey_s_counter_moved_on(client, owner,
                                                                                             monkeypatch):
    from test_ade_link_commands import command
    counted = Authenticator(alg=-8, counter=5)
    assert add(client, owner, counted).status_code == 200
    keys = Keys(client, owner, counted)
    mac = paired(client, owner)
    keys.turn_on()
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    rotate(keys)
    held = mac.call("GET", "/e2e").json()["statements"]
    with store.connect() as db:          # restored from a backup made before the second statement
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
    command(client, owner, counted, mac.link_id)       # the passkey signed since: its count is past the statement's
    r = mac.call("POST", "/e2e", {"statements": [{"payload": s["payload"], "credential": s["credential"]}
                                                 for s in held]})
    assert r.status_code == 200 and r.json()["held"] == 1, r.text


def test_with_high_security_on_the_recovery_code_still_adds_a_passkey_held_a_day(client, owner, metadata, maker):
    yubikey, r = add_hardware(client, owner, maker)
    assert r.json()["tier"] == "hardware", r.text
    r = client.put("/api/account/high-security", headers=owner,
                   json={"on": True, **confirmed(client, owner, yubikey, change="high-security-on")})
    assert r.status_code == 200, r.text
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"recovery_proof": keys.proof("passkey")}).json()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "Spare", "credential": Authenticator(alg=-8).create(options)})
    assert r.status_code == 200 and r.json()["held"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text


def test_a_link_reads_the_statements_by_seq_with_their_signatures(client, owner, keys, monkeypatch):
    mac = paired(client, owner)
    assert mac.call("GET", "/e2e").json() == {"statements": [], "more": False, "e2e": None}
    assert mac.call("GET", "/state").json()["e2e"] is None
    keys.turn_on()
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    rotate(keys)
    got = mac.call("GET", "/e2e")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    got = got.json()
    assert [s["seq"] for s in got["statements"]] == [1, 2] and got["more"] is False
    newest = {"seq": 2, "key_id": e2e.key_id(raw_public(keys.key()))}
    assert got["e2e"] == newest
    for s, raw in zip(got["statements"], keys.statements):
        assert s["payload"].encode() == raw and s["passkey_id"] == keys.passkey["id"]
        assert s["credential"]["id"] == b64u(keys.device.credential_id)
        passkeys.check_command(keys.account, raw, s["credential"], counted=False)     # it verifies as signed
    assert client.get("/api/ade/link/e2e", headers=owner).status_code == 401           # a link's route alone
    # a page at a time: those after a seq, at most limit, more while others follow; the newest said whole each time
    for query, seqs, more in (({"limit": 1}, [1], True), ({"after": 1, "limit": 1}, [2], False),
                              ({"after": 1}, [2], False), ({"after": 2}, [], False), ({"after": 9}, [], False)):
        page = mac.call("GET", "/e2e", query=query).json()
        assert ([s["seq"] for s in page["statements"]], page["more"], page["e2e"]) == (seqs, more, newest), query
    for query in ({"limit": 0}, {"limit": e2e_keys.FOR_LINK_PAGE + 1}, {"after": -1}):
        r = mac.call("GET", "/e2e", query=query)
        assert r.status_code == 422 and "limit 1 to 500" in r.json()["detail"], (query, r.text)
    assert e2e_keys.FOR_LINK_PAGE == 500


def test_after_a_restore_a_link_sends_in_the_statements_the_server_lacks_and_they_wait_on_a_hold(client, owner, keys,
                                                                                                monkeypatch):
    mac = paired(client, owner)
    keys.turn_on()
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    rotate(keys)
    monkeypatch.delenv("OPEN_EDITION_ADE_E2E_HOLD")
    held = mac.call("GET", "/e2e").json()["statements"]
    sent = [{"payload": s["payload"], "credential": s["credential"]} for s in held]
    with store.connect() as db:          # restored from a backup made before the second statement
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
        db.execute("UPDATE ade_e2e_statements SET retired_at = NULL")
    for case, statements, words in (
            ("a signature over other bytes", [{"payload": keys.statement(seq=1, prev="")[0]["payload"],
                                               "credential": held[0]["credential"]}], "signature"),
            ("a payload text not canonical", [{"payload": json.dumps(json.loads(sent[1]["payload"]), indent=1),
                                               "credential": sent[1]["credential"]}], "canonical"),
            ("no statement", [], "at most"),
            ("a payload of another kind", [{"payload": {"purpose": "instruction"}, "credential": {}}],
             "statements of the account's keys")):
        r = mac.call("POST", "/e2e", {"statements": statements})
        assert r.status_code == 422 and words in r.json()["detail"], (case, r.text)
    signed_fork, _ = keys.statement(seq=1, prev="")                                  # a statement 1 of its own
    r = mac.call("POST", "/e2e", {"statements": [{"payload": json.dumps(signed_fork["payload"]),
                                                  "credential": signed_fork["credential"]}]})
    assert r.status_code == 422                                                       # not canonical text
    r = mac.call("POST", "/e2e", {"statements": [{"payload": canonicalize(signed_fork["payload"]).decode(),
                                                  "credential": signed_fork["credential"]}]})
    assert r.status_code == 409 and "fork" in r.json()["detail"]
    skipped, _ = keys.statement(seq=3, prev="e" * 64)
    r = mac.call("POST", "/e2e", {"statements": [{"payload": canonicalize(skipped["payload"]).decode(),
                                                  "credential": skipped["credential"]}]})
    assert r.status_code == 409 and "do not chain" in r.json()["detail"]
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 200 and r.json()["held"] == 1 and r.json()["e2e"]["seq"] == 1, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD and "statement(s) of your encryption keys" in hold["summary"]
    assert [s["seq"] for s in mac.call("GET", "/e2e").json()["statements"]] == [1]       # not before its hold ends
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.WAITING
    holds.settle(keys.account, now=hold["takes_effect_at"])
    got = mac.call("GET", "/e2e").json()
    assert [s["seq"] for s in got["statements"]] == [1, 2] and got["statements"][1]["payload"] == sent[1]["payload"]
    assert mac.call("POST", "/e2e", {"statements": sent}).json() == {"held": 0, "hold": None, "e2e": got["e2e"]}


def test_removing_a_passkey_ends_the_statements_sent_in_that_it_signed_while_they_wait(client, owner, keys, second):
    """A change a passkey signed ends when that passkey is removed (holds.end), as a move it signed ends: here a link's
    statements sent in after a restore from before encryption was turned on, while it is off, when a passkey may be
    removed. Removing another passkey leaves them waiting: a lost device's steps cancel only what names no passkey."""
    from test_passkeys import remove
    mac = paired(client, owner)
    keys.turn_on()
    sent = [{"payload": s["payload"], "credential": s["credential"]}
            for s in mac.call("GET", "/e2e").json()["statements"]]
    with store.connect() as db:          # restored from a backup made before encryption was turned on
        db.execute("DELETE FROM ade_e2e_statements")
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    hold = r.json()["hold"]
    assert remove(client, owner, passkey_of(client, owner, second)["id"]).status_code == 200
    with store.connect() as db:
        assert (holds.get(db, hold["id"])["done_at"], holds.get(db, hold["id"])["cancelled_at"]) == (None, None)
    r = remove(client, owner, keys.passkey["id"])
    assert r.status_code == 200 and r.json()["cancelled"] == [], r.text
    with store.connect() as db:
        ended = holds.get(db, hold["id"])
    assert ended["done_at"] is not None and ended["failed"] == e2e_keys.SIGNER_REMOVED
    assert rows("ade_e2e_pending") == []
    assert client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=owner).status_code == 409    # past cancelling
    holds.settle(keys.account, now=hold["takes_effect_at"])
    assert mac.call("GET", "/e2e").json()["statements"] == []


def test_with_high_security_on_a_statement_sent_in_counts_only_if_a_hardware_key_signed_it(client, owner, metadata,
                                                                                            maker, monkeypatch):
    """As when the owner signs one: while high security is on, a statement a device passkey signed is refused when a
    link sends it in, and when its hold ends, should high security have been turned on meanwhile."""
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    mac = paired(client, owner)
    keys.turn_on()
    enrol(keys, phone, yubikey)                                    # a passkey that unlocks the keys signs one
    monkeypatch.setenv(e2e_keys.HOLD_SETTING, "0")
    signed, private = keys.statement(device=phone)                 # signed with the phone, before high security
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text
    monkeypatch.delenv(e2e_keys.HOLD_SETTING)
    sent = [{"payload": s["payload"], "credential": s["credential"]}
            for s in mac.call("GET", "/e2e").json()["statements"]]
    assert len(sent) == 2

    def high(on):
        with store.connect() as db:          # written into the database here
            db.execute("UPDATE users SET high_security = ? WHERE id = ?", (1 if on else 0, keys.account))
    with store.connect() as db:              # restored from a backup made before the phone's statement
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
    high(True)
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS, r.text
    assert rows("ade_e2e_pending") == []
    high(False)
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    hold = r.json()["hold"]
    high(True)
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == passkeys.ONLY_HARDWARE_SIGNS
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1]


def test_statements_sent_in_that_the_owner_cancelled_are_refused_when_sent_in_again(client, owner, keys, monkeypatch):
    """The statements a link sends in after a restore are signed by the owner's passkey, as one the owner cancelled
    before is: once the owner cancels them as they wait, the same are refused if a link sends them in again."""
    mac = paired(client, owner)
    keys.turn_on()
    monkeypatch.setenv(e2e_keys.HOLD_SETTING, "0")
    rotate(keys)
    monkeypatch.delenv(e2e_keys.HOLD_SETTING)
    sent = [{"payload": s["payload"], "credential": s["credential"]}
            for s in mac.call("GET", "/e2e").json()["statements"]]
    with store.connect() as db:          # restored from a backup made before the second statement
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
    hold = mac.call("POST", "/e2e", {"statements": sent}).json()["hold"]
    assert cancel(keys, hold["id"], keys.device).status_code == 200
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.REFUSED_AGAIN, r.text
    assert [r["digest"] for r in rows("ade_e2e_refused")] == [hashlib.sha256(sent[1]["payload"].encode()).hexdigest()]
    assert rows("ade_e2e_pending") == []
    # the one the server holds is passed over, as before: refused is what the owner cancelled
    r = mac.call("POST", "/e2e", {"statements": sent[:1]})
    assert r.status_code == 200 and r.json()["held"] == 0, r.text


def test_removing_the_passkey_that_made_a_change_that_waits_ends_it(client, owner, device, second):
    """While encryption is off a passkey can be removed from Account: a change that waits that it signed (turning
    encryption on, here) ends then, failed, saying why, as a move it signed does; removing another leaves it."""
    from test_passkeys import remove
    third = Authenticator(alg=-257)
    assert add(client, owner, third, name="Third", confirm_with=device).status_code == 200
    keys = Keys(client, owner, second)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=keys.turn_on_body()[0])
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert rows("ade_e2e_pending")[0]["made_by"] == keys.passkey["id"]
    assert remove(client, owner, passkey_of(client, owner, third)["id"]).status_code == 200
    with store.connect() as db:
        assert holds.get(db, hold["id"])["done_at"] is None
    assert remove(client, owner, keys.passkey["id"]).status_code == 200
    with store.connect() as db:
        ended = holds.get(db, hold["id"])
    assert ended["done_at"] is not None and ended["failed"] == e2e_keys.MADE_BY_REMOVED
    assert rows("ade_e2e_pending") == []
    holds.settle(keys.account, now=hold["takes_effect_at"])
    assert view(client, owner)["on"] is False and rows("ade_e2e_statements") == []


# ---- what is told and shown without the words: the notifier, the desktop app, the old page --------------------------

def test_a_sealed_question_is_answered_with_the_digest_of_its_envelope_as_stored(client, owner, keys):
    from proofpane_open.ade import link_commands
    from test_ade_link_commands import payload
    mac = paired(client, owner)
    keys.turn_on()
    r, qid, text = send_sealed(keys, mac, "question", "ship it?", to="owner")
    seq = r.json()["seq"]

    def answer(digest):
        p = payload(client, owner, "answer", mac.link_id, "yes", ref=seq, digest=digest)
        return client.post("/api/ade/link/commands", headers=owner, json={"payload": p,
                                                                          "credential": assertion(keys.device, p)})
    r = answer(link_commands.text_digest("ship it?"))              # the words inside are not what is stored
    assert r.status_code == 409 and "digest" in r.json()["detail"]
    assert answer(link_commands.text_digest(text)).status_code == 200
    (q,) = [e for e in client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=owner).json()["events"]
            if e["seq"] == seq]
    assert "sealed_in_place" not in q                               # its link sealed it


def test_a_question_open_as_encryption_is_turned_on_is_answered_with_the_digest_of_the_words_asked(client, owner,
                                                                                                   keys):
    """A question asked in the clear and still open when encryption is turned on is sealed in place, and its view says
    so (sealed_in_place). Its session keeps the digest of the words it asked (ade_link.py, posted.json), never of a
    text the server sealed later: the answer carries that digest, and one of the sealed text, which could never
    verify, is refused saying why."""
    from proofpane_open.ade import link_commands
    from test_ade_link_commands import payload
    mac = paired(client, owner)
    r = post(mac, "question", "ship it?", to="owner", client_id=uuid.uuid4().hex)
    assert r.status_code == 200
    seq = r.json()["seq"]
    on, _ = keys.turn_on()
    assert on["sealed"]["events"] == 1
    (q,) = [e for e in client.get(f"/api/ade/link/timeline/{mac.link_id}", headers=owner).json()["events"]
            if e["seq"] == seq]
    assert q["sealed_in_place"] is True
    assert open_envelope(keys.key(), q["text"], event_slot(keys, mac, "question", q["client_id"])) == {
        "text": "ship it?", "session": None, "said_at": None}

    def answer(digest):
        p = payload(client, owner, "answer", mac.link_id, "yes", ref=seq, digest=digest)
        return client.post("/api/ade/link/commands", headers=owner, json={"payload": p,
                                                                          "credential": assertion(keys.device, p)})
    r = answer(link_commands.text_digest(q["text"]))
    assert r.status_code == 409 and r.json()["detail"] == link_commands.SEALED_IN_PLACE
    assert answer(link_commands.text_digest("ship it?")).status_code == 200


def test_a_question_sealed_or_in_the_clear_is_told_without_its_words(client, owner, keys):
    from proofpane_open.ade import push
    mac = paired(client, owner)
    keys.turn_on()
    r, qid, text = send_sealed(keys, mac, "question", "when do we launch?", to="owner")
    assert r.status_code == 200
    asked = [item for item in link.alerts(keys.account) if item[0].startswith("link-question:")]
    assert asked == [(f"link-question:{r.json()['seq']}", "link-question")]
    pushed = [m for k, m in push.items_for(keys.account, [], int(time.time())) if k.startswith("link-question:")]
    assert [(m.title, m.body) for m in pushed] == [("A question waits for you", "Open Linked sessions to read it.")]
    assert text[5:30] not in repr(pushed)
    # a question in the clear is told the same way: never its words, nor the asker's name
    them = signed_up(client, OTHER)
    other = paired(client, them, "cloud", "cloud")
    assert post(other, "question", "is the build green?", to="owner").status_code == 200
    theirs = client.get("/api/auth/me", headers=them).json()["id"]
    assert [i[1] for i in link.alerts(theirs) if i[0].startswith("link-question:")] == ["link-question"]
    pushed = [m for k, m in push.items_for(theirs, [], int(time.time())) if k.startswith("link-question:")]
    assert len(pushed) == 1 and "green" not in repr(pushed) and "cloud" not in repr(pushed)


# ---- a move of a sealed row: its title beside what is signed ---------------------------------------------------------

def test_a_move_of_a_sealed_row_is_named_by_the_title_the_page_opened(client, owner, keys, monkeypatch):
    from proofpane_open.ade import moves
    from test_ade_link_moves import move_payload
    monkeypatch.setenv(moves.HOLD_SETTING, "0")
    monkeypatch.setattr(moves, "check_workspace", lambda user_id: None)
    # an account with a workspace that keeps its work there (ade/storage.py): one that has not chosen and has no
    # workspace this deployment runs keeps it on its own machine, and its moves are refused (test_storage_choice.py)
    with store.connect() as db:
        db.execute("INSERT INTO ade_storage_choice (user_id, choice, chosen_at) VALUES (?, 'hosted', 0)",
                   (keys.account,))
    mac = paired(client, owner)

    def move(session, **beside):
        p = move_payload(client, owner, mac.link_id, session)
        return client.post("/api/ade/link/commands", headers=owner,
                           json=dict({"payload": p, "credential": assertion(keys.device, p)}, **beside))
    # a row in the clear: the title its link listed names the move, whatever is sent beside it
    assert history(mac, [{"id": "s1", "title": "Listed title"}]).status_code == 200
    r = move("s1", title="Not this one")
    assert r.status_code == 200 and "“Listed title”" in r.json()["held"]["summary"], r.text
    holds.settle()
    keys.turn_on()
    assert history(mac, [sealed_row(keys, mac, "s2", {"title": "Opened title"}),
                         sealed_row(keys, mac, "s3", {"title": "Never opened"})]).status_code == 200
    for title, words in (("two\nlines", "not shown as itself"), ("x" * 121, "at most 120"), ("", "at most 120")):
        r = move("s2", title=title)
        assert r.status_code == 422 and words in r.json()["detail"], (title, r.text)
    monkeypatch.delenv(moves.HOLD_SETTING)                      # these wait on their holds, as a move does
    r = move("s2", title="Opened title")
    assert r.status_code == 200 and "“Opened title”" in r.json()["held"]["summary"], r.text
    r = move("s3")
    assert r.status_code == 200 and "“(untitled)”" in r.json()["held"]["summary"], r.text
    # the card of a move that waits names it by that title, never by the "" its sealed row keeps
    held = client.get("/api/ade/link/sessions", headers=owner).json()["held_moves"]
    assert {m["session"]: m["title"] for m in held} == {"s2": "Opened title", "s3": None}
    holds.settle(now=int(time.time()) + moves.HOLD_DEFAULT + 1)
    stored = {r["session_id"]: r for r in rows("ade_link_moves")}
    assert {sid: r["title"] for sid, r in stored.items()} == {"s1": "Listed title", "s2": "Opened title", "s3": None}
    assert moves._task(dict(stored["s2"], link_name="mac")).startswith("Opened title\n")       # the workspace's name
    assert moves._task(dict(stored["s3"], link_name="mac")).startswith("(untitled)\n")


# ---- who may cancel, the second version: the recovery code the root ---------------------------------------------------

def theirs(keys, device, **more):
    """A revocation signed with `device`, another passkey than the page's own (as whoever holds it makes one), sent with
    `more` beside it: the server's answer."""
    body, signed, private = revocation(keys, **{k: v for k, v in more.items() if k != "recovery_proof"})
    extra = {"recovery_proof": more["recovery_proof"]} if "recovery_proof" in more else {}
    return keys.client.post("/api/ade/e2e/revoke", headers=keys.owner,
                            json=signed_by(keys, body, private, device, **extra))


def taken(keys, sent, private):
    """What the page keeps once a revocation took effect (`sent`, its body as it was sent): its statement and its new
    master key."""
    keys.took(sent["statement"], private)
    keys.mk_id = sent["mk_id"]


def passkey_ids(client, owner):
    return [p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]]


def test_an_owner_left_with_only_the_recovery_code_adds_enrols_and_revokes_whatever_a_stolen_passkey_does(
        client, owner, keys, device, second):
    """The case table's first row. A stolen passkey (B) had the owner's (A) removed, unnoticed, and the owner holds the
    recovery code alone. By the code's proof the owner adds a passkey, enrols it, and revokes B with a statement the
    new one signs: each change code-backed, which only the code cancels, so B neither cancels it nor takes its place;
    and each takes the place of the change B keeps waiting."""
    keys.turn_on()
    enrol(keys, second, device)
    a, b = keys.passkey["id"], passkey_of(client, owner, second)["id"]
    body, signed, private = revocation(keys, remove=[a])
    sent = signed_by(keys, body, private, second)
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=sent).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    taken(keys, sent, private)
    assert passkey_ids(client, owner) == [b]

    def thiefs_waiting():
        r = theirs(keys, second)                       # B keeps a change of its own waiting, one after another
        assert r.status_code == 200, r.text
        return r.json()["hold"]

    def stands(hold):
        """The owner's change waits, code-backed: B neither cancels it nor takes its place, and it takes effect."""
        pending = view(client, owner)["pending"]
        assert (pending["id"], pending["by_code"], pending["cancel_by"]) == (hold["id"], True, [])
        r = cancel(keys, hold["id"], second)
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
        r = theirs(keys, second)
        assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
        holds.settle(keys.account, now=hold["takes_effect_at"])
        with store.connect() as db:
            assert holds.get(db, hold["id"])["failed"] is None and holds.get(db, hold["id"])["done_at"] is not None

    waiting = thiefs_waiting()
    new = Authenticator(alg=-257)
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"recovery_proof": keys.proof("passkey")}).json()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "New", "credential": new.create(options), "prf": True})
    assert r.status_code == 200, r.text
    with store.connect() as db:
        assert holds.get(db, waiting["id"])["failed"] == e2e_keys.REPLACED
    stands(r.json()["held"])
    n = passkey_of(client, owner, new)["id"]
    thiefs_waiting()
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, new, recovery_proof=keys.proof("credentials")))
    assert r.status_code == 200, r.text
    stands(r.json()["hold"])
    assert sorted(c["passkey_id"] for c in view(client, owner)["credentials"]) == sorted([b, n])
    thiefs_waiting()
    body, signed, private = revocation(keys, remove=[b])
    sent = signed_by(keys, body, private, new, recovery_proof=keys.proof("revoke"))
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=sent)
    assert r.status_code == 200, r.text
    stands(r.json()["hold"])
    taken(keys, sent, private)
    assert passkey_ids(client, owner) == [n]
    assert [c["passkey_id"] for c in view(client, owner)["credentials"]] == [n]


def test_against_one_stolen_passkey_the_owner_holding_the_recovery_code_wins(client, owner, keys, device, second):
    """The second row: the thief holds one passkey (B), the owner another (A) and the recovery code. Each one's plain
    revocation of the other's passkey is cancelled, or taken the place of, by the other ((iii): neither leaves more than
    its signer); the owner's sent with the code's proof is code-backed, which the thief's passkey neither cancels nor
    takes the place of, and it takes effect."""
    keys.turn_on()
    enrol(keys, second, device)
    a, b = keys.passkey["id"], passkey_of(client, owner, second)["id"]
    assert theirs(keys, second, remove=[a]).status_code == 200
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([a, b])
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[b])[0])
    assert r.status_code == 200, r.text                                             # A's, in its place
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([a, b])
    assert theirs(keys, second, remove=[a]).status_code == 200                      # and B's in the place of that
    body, signed, private = revocation(keys, remove=[b])
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(body, recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200, r.text
    ours = r.json()["hold"]
    assert view(client, owner)["pending"]["cancel_by"] == []
    r = cancel(keys, ours["id"], second)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    r = theirs(keys, second, remove=[a])
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    assert holds.settle(keys.account, now=ours["takes_effect_at"]) == [ours["id"]]
    taken(keys, body, private)
    assert passkey_ids(client, owner) == [a]


def test_two_who_each_hold_one_passkey_and_no_one_the_code_stand_off(client, owner, keys, device, second):
    """The third row: A and B each hold one passkey, and no one the recovery code. Each cancels the other's revocation
    of it, or puts its own in that one's place, so nothing takes effect while the other answers: a standoff."""
    keys.turn_on()
    enrol(keys, second, device)
    a, b = keys.passkey["id"], passkey_of(client, owner, second)["id"]
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[b])[0]).json()["hold"]
    assert cancel(keys, hold["id"], second).status_code == 200                      # B cancels A's
    hold = theirs(keys, second, remove=[a]).json()["hold"]
    assert cancel(keys, hold["id"], device).status_code == 200                      # A cancels B's
    for _ in range(2):                                                              # each in the place of the other's
        assert client.post("/api/ade/e2e/revoke", headers=owner,
                           json=revocation(keys, remove=[b])[0]).status_code == 200
        assert theirs(keys, second, remove=[a]).status_code == 200
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1] and sorted(passkey_ids(client, owner)) == sorted([a, b])


def test_a_thief_holding_a_passkey_and_the_recovery_code_stands_off_with_the_owner(client, owner, keys, device, second):
    """The fourth row: the thief holds a passkey (B) and the recovery code too. The code cancels any change (i), so each
    cancels the other's code-backed revocation, or puts its own, code-backed, in that one's place: a standoff, not a
    takeover."""
    keys.turn_on()
    enrol(keys, second, device)
    a, b = keys.passkey["id"], passkey_of(client, owner, second)["id"]
    hold = client.post("/api/ade/e2e/revoke", headers=owner,
                       json=dict(revocation(keys, remove=[b])[0], recovery_proof=keys.proof("revoke"))).json()["hold"]
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200      # the thief's code
    hold = theirs(keys, second, remove=[a], recovery_proof=keys.proof("revoke")).json()["hold"]
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200      # the owner's
    mine = client.post("/api/ade/e2e/revoke", headers=owner,
                       json=dict(revocation(keys, remove=[b])[0], recovery_proof=keys.proof("revoke")))
    assert mine.status_code == 200, mine.text
    r = theirs(keys, second, remove=[a], recovery_proof=keys.proof("revoke"))
    assert r.status_code == 200, r.text
    with store.connect() as db:
        assert holds.get(db, mine.json()["hold"]["id"])["failed"] == e2e_keys.REPLACED
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1] and sorted(passkey_ids(client, owner)) == sorted([a, b])


def test_an_attacker_with_only_the_recovery_code_and_a_sign_in_stands_off_until_its_sign_ins_end(client, owner, keys,
                                                                                                 device):
    """The fifth row: an attacker holding the recovery code and a sign-in of the account, but no passkey, cancels with
    the code what the owner places, and places code-backed changes of its own (here a passkey of its own), which no
    passkey cancels and the owner cancels with the code: a standoff, until the owner signs out everywhere, which ends
    the attacker's sign-in and with it every use it had of the code."""
    from conftest import ADMIN
    from test_passkeys import sign_out_everywhere
    keys.turn_on()
    r = client.post("/api/auth/login", json={"email": ADMIN, "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    attacker = Keys(client, {"Authorization": "Bearer " + r.json()["token"]}, device)
    attacker.recovery = keys.recovery                                   # the code, leaked
    hold = held_rotation(keys)
    assert cancel(attacker, hold["id"], recovery_proof=attacker.proof("cancel")).status_code == 200
    options = client.post("/api/account/passkeys/options", headers=attacker.owner,
                          json={"recovery_proof": attacker.proof("passkey")}).json()
    r = client.post("/api/account/passkeys", headers=attacker.owner, json={
        "challenge_id": options["challenge_id"], "name": "Theirs", "credential": Authenticator(alg=-257).create(options)})
    assert r.status_code == 200, r.text
    held = r.json()["held"]
    assert cancel(keys, held["id"], device).status_code == 403                      # code-backed: no passkey cancels it
    assert cancel(keys, held["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200, r.text
    keys.owner = {"Authorization": "Bearer " + r.json()["token"]}
    hold = held_rotation(keys)
    assert client.post("/api/ade/e2e/recovery-proof", headers=attacker.owner,
                       json={"purpose": "cancel"}).status_code == 401
    assert client.post(f"/api/ade/holds/{hold['id']}/cancel", headers=attacker.owner, json={}).status_code == 401
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1, 2]


def test_with_high_security_who_may_cancel_counts_only_the_passkeys_that_could(client, owner, metadata, maker):
    """While high security is on, only the hardware keys count, in R, in "no passkey besides its signer" and among
    those who may cancel, a hardware key on the account's list never enrolled among them; a device passkey, which could
    not cancel, counts nowhere; and high_security repeated while it is on removes no passkey."""
    from test_passkeys import confirm
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    spare, r = add_hardware(client, owner, maker, name="Spare", confirm_with=yubikey)       # never enrolled
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    r = client.put("/api/account/high-security", headers=owner, json={"on": True, **confirmed(client, owner, yubikey, change="high-security-on")})
    assert r.status_code == 200, r.text
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    y, s, p = (passkey_of(client, owner, d)["id"] for d in (yubikey, spare, phone))
    for remove, flag, words, kind in (
            ([s], False, "the only other that could, removed: it may cancel ((iii): the phone does not count)",
             e2e_keys.HOLD),
            ([p], False, "the phone, which could not cancel, removed: R holds none that counts", e2e_keys.HOLD),
            ([], True, "high security repeated: removes no passkey, so it waits a day", e2e_keys.HOLD_LONG)):
        r = client.post("/api/ade/e2e/revoke", headers=owner,
                        json=revocation(keys, remove=remove, high_security=flag)[0])
        assert r.status_code == 200, (words, r.text)
        hold = r.json()["hold"]
        assert (sorted(view(client, owner)["pending"]["cancel_by"]), hold["kind"]) == (sorted([y, s]), kind), words
        assert confirm(client, owner, phone).status_code == 403, words      # the phone's word is not even made
        assert cancel(keys, hold["id"], spare).status_code == 200, words


# ---- what signs a change, how long it waits, what it says ---------------------------------------------------------

def test_a_change_is_signed_or_confirmed_by_a_passkey_that_unlocks_the_keys_or_comes_with_the_code(client, owner,
                                                                                                  keys, device, second):
    """A rotation, a revocation and turning off are signed, and adding a passkey to encryption and renewed anchors
    confirmed, by a passkey whose credential unlocks the keys and still does after the change: not one of the account's
    never enrolled, not one the revocation removes, not the passkey being added. The recovery code's proof given with
    it stands for that. As a confirmed change's hold ends, its confirmer is asked again."""
    keys.turn_on()                                                  # A enrolled; B the account's, never enrolled
    a = keys.passkey["id"]

    def by(signer, path, **more):
        if path == "rotate":
            signed, private = keys.statement(device=signer)
            body = {"statement": signed, "content_key": keys.content_key(private)}
        elif path == "off":
            body = {"statement": keys.statement("e2e-off", device=signer)[0]}
        else:
            made, signed, private = revocation(keys)
            body = signed_by(keys, made, private, signer)
        return client.post(f"/api/ade/e2e/{path}", headers=owner, json=dict(body, **more))
    for path in ("rotate", "off", "revoke"):
        r = by(second, path)
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.NOT_STAYING, (path, r.text)
        r = by(second, path, recovery_proof=keys.proof(path))
        assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, (path, r.text)
        assert cancel(keys, r.json()["hold"]["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=[a])[0])    # signed by A, gone
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.NOT_STAYING, r.text
    r = client.post("/api/ade/e2e/credentials", headers=owner,             # B confirming itself
                    json=enrol_body(keys, second, **confirmed(client, owner, second, purpose=ENROL)))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CONFIRM_STAYING, r.text
    anchor = {"passkey_id": a, "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120)),
              **confirmed(client, owner, device, purpose=ANCHOR)}
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json={"anchors": [anchor], "mk_id": keys.mk_id, **confirmed(client, owner, second, purpose=ANCHOR)})
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CONFIRM_STAYING, r.text
    assert rows("ade_e2e_pending") == []
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, **confirmed(client, owner, device, purpose=ENROL)))
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    with store.connect() as db:          # A no longer unlocks the keys by its end (written into the database here)
        db.execute("DELETE FROM ade_e2e_credentials WHERE passkey_id = ?", (a,))
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.CONFIRM_STAYING
    assert rows("ade_e2e_credentials") == []


def test_as_a_signed_change_s_hold_ends_its_signer_is_asked_again(client, owner, keys, device, second):
    """As a hold ends: a rotation, turning off or a revocation whose signer no longer unlocks the keys by then keeps
    nothing; code-backed, it does not ask that."""
    keys.turn_on()
    enrol(keys, second, device)
    b = passkey_of(client, owner, second)["id"]
    signed, private = keys.statement(device=second)
    rotation = client.post("/api/ade/e2e/rotate", headers=owner,
                           json={"statement": signed, "content_key": keys.content_key(private)}).json()["hold"]
    gone = rows("ade_e2e_credentials", passkey_id=b)[0]
    with store.connect() as db:          # B no longer unlocks the keys (written into the database here)
        db.execute("DELETE FROM ade_e2e_credentials WHERE passkey_id = ?", (b,))
    holds.settle(keys.account, now=rotation["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, rotation["id"])["failed"] == e2e_keys.NOT_STAYING
        db.execute("INSERT INTO ade_e2e_credentials (user_id, passkey_id, credential_id, salt, public_key, wrapped, "
                   "created_at) VALUES (:user_id, :passkey_id, :credential_id, :salt, :public_key, :wrapped, "
                   ":created_at)", gone)
    signed, private = keys.statement(device=second)
    coded = client.post("/api/ade/e2e/rotate", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private),
        "recovery_proof": keys.proof("rotate")}).json()["hold"]
    with store.connect() as db:
        db.execute("DELETE FROM ade_e2e_credentials WHERE passkey_id = ?", (b,))
    holds.settle(keys.account, now=coded["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, coded["id"])["failed"] is None
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1, 2]


def test_as_its_hold_ends_a_statement_counts_only_if_a_hardware_key_signed_it_while_high_security_is_on(
        client, owner, metadata, maker):
    """High security turned on while a rotation, turning off or a revocation that a device passkey signed waits
    (written into the database here: while encryption is on, only a revocation turns it on, and that would take the
    change's place): as the hold ends, nothing of the change is kept."""
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    enrol(keys, phone, yubikey)

    def high(on):
        with store.connect() as db:
            db.execute("UPDATE users SET high_security = ? WHERE id = ?", (1 if on else 0, keys.account))
    for path in ("rotate", "off", "revoke"):
        if path == "rotate":
            signed, private = keys.statement(device=phone)
            body = {"statement": signed, "content_key": keys.content_key(private)}
        elif path == "off":
            body = {"statement": keys.statement("e2e-off", device=phone)[0]}
        else:
            made, signed, private = revocation(keys)
            body = signed_by(keys, made, private, phone)
        r = client.post(f"/api/ade/e2e/{path}", headers=owner, json=body)
        assert r.status_code == 200, (path, r.text)
        hold = r.json()["hold"]
        high(True)
        holds.settle(keys.account, now=hold["takes_effect_at"])
        high(False)
        with store.connect() as db:
            assert holds.get(db, hold["id"])["failed"] == passkeys.ONLY_HARDWARE_SIGNS, path
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1]


def test_turning_off_and_a_revocation_that_removes_no_passkey_wait_a_day(client, owner, keys, device, second, three,
                                                                         notices):
    """Turning encryption off waits the long hold, as does a revocation that removes no passkey; one that removes a
    passkey and leaves another credential besides its signer's waits as a change does. Each notice says a change
    waits, and when; one made with the recovery code, that only the code cancels it."""
    (a, b, c), _ = three
    hold = client.post("/api/ade/e2e/off", headers=owner, json={"statement": keys.statement("e2e-off")[0]}).json()["hold"]
    assert (hold["kind"], hold["takes_effect_at"] - hold["approved_at"]) == (e2e_keys.HOLD_LONG, 86400)
    assert (told(notices, hold), view(client, owner)["pending"]["change"]) == (told_as(hold, "held-e2e"), "off")
    assert cancel(keys, hold["id"], device).status_code == 200
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": keys.statement("e2e-off")[0],
                                                             "recovery_proof": keys.proof("off")})
    assert r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text
    assert told(notices, r.json()["hold"]) == told_as(r.json()["hold"], "held-e2e-code")
    assert cancel(keys, r.json()["hold"]["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    for remove, kind in (([], e2e_keys.HOLD_LONG), ([b], e2e_keys.HOLD)):
        hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys, remove=remove)[0]).json()["hold"]
        assert hold["kind"] == kind, remove
        assert cancel(keys, hold["id"], device).status_code == 200


def unsigned(statement):
    """A statement as a waiting change's body serves it: as kept, but its assertion."""
    return {k: v for k, v in statement.items() if k != "credential"}


def test_the_change_that_waits_is_served_whole_with_what_made_it(client, owner, keys, device, second):
    """GET serves the change that waits with its body as kept (public keys, wraps and statements: nothing secret), so a
    tab can check before its hold ends that what it holds still opens, but its statement's signature, which the
    statements served go without too; and who made it, and whether it is code-backed."""
    keys.turn_on()
    enrol(keys, second, device)
    held_rotation(keys)
    view_now = view(client, owner)
    pending = view_now["pending"]
    (kept,) = rows("ade_e2e_pending")
    kept = json.loads(kept["body"])
    assert set(kept["statement"]) == {"payload", "credential", "passkey_id"}         # kept with its signature
    assert pending["body"] == dict(kept, statement=unsigned(kept["statement"]))
    assert set(pending["body"]) == {"statement", "content_key"}
    assert set(pending["body"]["statement"]) == {"payload", "passkey_id"}
    assert all("credential" not in s for s in view_now["statements"])               # as the statements served
    assert (pending["made_by"], pending["by_code"]) == (keys.passkey["id"], False)
    assert pending["body"]["statement"]["passkey_id"] == keys.passkey["id"]
    body = revocation(keys, remove=[passkey_of(client, owner, second)["id"]])[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(body, recovery_proof=keys.proof("revoke")))
    assert r.status_code == 200, r.text
    pending = view(client, owner)["pending"]
    assert pending["body"]["mk_id"] == body["mk_id"] and pending["body"]["roster"] == body["roster"]
    assert set(pending["body"]["grants"]) == {g["to"] for g in body["grants"]}
    assert pending["body"]["content_keys"] == {k["key_id"]: k["wrapped"] for k in body["content_keys"]}
    assert (pending["made_by"], pending["by_code"]) == (keys.passkey["id"], True)
    (kept,) = rows("ade_e2e_pending")
    kept = json.loads(kept["body"])
    assert "credential" in kept["statement"] and pending["body"] == dict(kept, statement=unsigned(kept["statement"]))


def test_a_waiting_enrolment_keeps_its_record_and_statements_sent_in_go_without_their_signatures(client, owner, keys,
                                                                                               device, second,
                                                                                               monkeypatch):
    """The two other bodies: an enrolment's `credential` is the passkey's record (its salt, public key
    and wrap), no assertion, and is served whole; the statements a link sent in after a restore are served each without
    its signature, which their kept body keeps until the hold ends."""
    keys.turn_on()
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, **confirmed(client, owner, device, purpose=ENROL)))
    assert r.status_code == 200, r.text
    pending = view(client, owner)["pending"]
    (kept,) = rows("ade_e2e_pending")
    kept = json.loads(kept["body"])
    assert pending["change"] == "credentials" and pending["body"] == kept
    assert {"passkey_id", "salt", "public_key", "wrapped"} <= set(pending["body"]["credential"])
    assert cancel(keys, r.json()["hold"]["id"], device).status_code == 200
    mac = paired(client, owner)
    monkeypatch.setenv("OPEN_EDITION_ADE_E2E_HOLD", "0")
    rotate(keys)
    monkeypatch.delenv("OPEN_EDITION_ADE_E2E_HOLD")
    held = mac.call("GET", "/e2e").json()["statements"]
    with store.connect() as db:          # restored from a backup made before the second statement
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
        db.execute("UPDATE ade_e2e_statements SET retired_at = NULL")
    r = mac.call("POST", "/e2e", {"statements": [{"payload": s["payload"], "credential": s["credential"]}
                                                 for s in held]})
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    pending = view(client, owner)["pending"]
    (kept,) = rows("ade_e2e_pending")
    kept = json.loads(kept["body"])
    assert pending["change"] == "upload" and len(kept["statements"]) == 1
    assert set(kept["statements"][0]) == {"payload", "credential", "passkey_id"}    # kept with its signature
    assert pending["body"] == {"statements": [unsigned(s) for s in kept["statements"]]}
    assert pending["body"]["statements"][0]["payload"] == held[1]["payload"]


def test_a_change_s_notice_names_nothing_it_touches_and_the_page_reads_what_it_does(client, owner, metadata, maker,
                                                                                    notices):
    """A change's notice says that a change to encryption waits and when, and nothing of what it touches:
    not the passkeys a revocation removes, nor those turning high security on leaves out,
    nor the passkeys whose anchors are renewed. The page reads them from the change that waits."""
    yubikey, r = add_hardware(client, owner, maker, name="YubiKey")
    spare, r = add_hardware(client, owner, maker, name="Spare", confirm_with=yubikey)
    phone = Authenticator(alg=-8)
    assert add(client, owner, phone, name="Phone", confirm_with=yubikey).status_code == 200
    keys = Keys(client, owner, yubikey)
    keys.turn_on()
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(
        keys, remove=[passkey_of(client, owner, spare)["id"]], high_security=True)[0]).json()["hold"]
    assert told(notices, hold) == told_as(hold, "held-e2e")
    pending = view(client, owner)["pending"]                # GET's removes is the same R (in the account's order:
    assert (sorted(pending["removes"]), pending["replaces_code"]) == (       # by when each was added, then by id)
        sorted([passkey_of(client, owner, spare)["id"], passkey_of(client, owner, phone)["id"]]), False)
    assert cancel(keys, hold["id"], yubikey).status_code == 200
    salt = b64u(os.urandom(32))
    r = client.post("/api/ade/e2e/anchors", headers=owner, json={
        "anchors": [{"passkey_id": keys.passkey["id"], "salt": salt, "wrapped": b64u(os.urandom(120))}],
        "mk_id": keys.mk_id, **confirmed(client, owner, yubikey, purpose=ANCHOR)})
    assert r.status_code == 200, r.text
    assert r.json()["hold"]["summary"] == "Renewed anchors for end-to-end encryption: “YubiKey”"
    assert told(notices, r.json()["hold"]) == told_as(r.json()["hold"], "held-e2e")
    names = ("YubiKey", "Spare", "Phone", "high security", "removes", "anchor")
    assert notices and not [m for m in notices for n in names if n.lower() in repr(m).lower()]


def test_renewed_anchors_take_each_passkey_s_own_touch_and_a_fresh_salt(client, owner, keys, device, second):
    """A change renews the confirming passkey's own anchor, and another's only with a confirmation that passkey made
    (each in its entry); with the recovery code's proof, every renewed anchor takes its own passkey's. A salt equal to
    the one the record has is refused."""
    keys.turn_on()
    enrol(keys, second, device)
    a, b = keys.passkey["id"], passkey_of(client, owner, second)["id"]
    salts = {c["passkey_id"]: c["salt"] for c in view(client, owner)["credentials"]}

    def anchor(passkey_id, salt=None, **more):
        return dict({"passkey_id": passkey_id, "salt": salt or b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))},
                    **more)

    def renew(anchors, **word):
        return client.post("/api/ade/e2e/anchors", headers=owner,
                           json=dict({"anchors": anchors, "mk_id": keys.mk_id}, **word))
    def touch(device):
        return confirmed(client, owner, device, purpose=ANCHOR)
    for case, anchors, word, status, words in (
            ("B's with no touch of B", [anchor(a), anchor(b)], touch(device), 403, e2e_keys.ANCHOR_OWN),
            ("B's with A's touch", [anchor(a), anchor(b, **touch(device))], touch(device), 403, e2e_keys.ANCHOR_OWN),
            ("B's alone, A confirming", [anchor(b)], touch(device), 403, e2e_keys.ANCHOR_OWN),
            ("the salt it has", [anchor(a, salts[a])], touch(device), 422, e2e_keys.SALT_SAME),
            ("the code's proof, A's with no touch of A", [anchor(a), anchor(b, **touch(second))],
             {"recovery_proof": keys.proof("anchors")}, 403, e2e_keys.ANCHOR_OWN)):
        r = renew(anchors, **word)
        assert r.status_code == status and r.json()["detail"] == words, (case, r.text)
    assert rows("ade_e2e_pending") == []
    r = renew([anchor(a), anchor(b, **touch(second))], **touch(device))
    assert r.status_code == 200, r.text
    lead, names = r.json()["hold"]["summary"].split(": ", 1)       # in the account's order: by when, then by id
    assert lead == "Renewed anchors for end-to-end encryption" and sorted(names.split(", ")) == ["“Ana's iPhone”",
                                                                                               "“Second”"]
    assert cancel(keys, r.json()["hold"]["id"], device).status_code == 200
    r = renew([anchor(a, **touch(device))], recovery_proof=keys.proof("anchors"))
    assert r.status_code == 200 and r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY, r.text


def test_a_change_whose_time_has_come_takes_effect_before_another_could_take_its_place(client, owner, keys,
                                                                                       monkeypatch):
    """A change whose hold has ended, which nothing has settled yet, takes effect as the next change is asked,
    before that one could take its place: here a revocation made after the rotation, its statement the one after."""
    keys.turn_on()
    monkeypatch.setenv(e2e_keys.HOLD_SETTING, "0")
    signed, private = keys.statement()
    rotation = client.post("/api/ade/e2e/rotate", headers=owner, json={
        "statement": signed, "content_key": keys.content_key(private)}).json()["hold"]
    keys.took(signed, private)                              # its hold has ended (no seconds); nothing read since
    monkeypatch.delenv(e2e_keys.HOLD_SETTING)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0])
    assert r.status_code == 200, r.text
    with store.connect() as db:
        ended = holds.get(db, rotation["id"])
    assert ended["done_at"] is not None and ended["failed"] is None
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1, 2]


@pytest.mark.parametrize("route", ["rotate", "off", "credentials", "anchors", "passkey"])
def test_a_code_backed_change_whose_time_has_come_takes_effect_before_any_other_is_placed(client, owner, keys, device,
                                                                                         second, monkeypatch, route):
    """For each route that places a change: a code-backed change whose hold has ended (here the second passkey
    enrolled by the recovery code, held no time), which nothing has settled yet, takes effect as the next change is
    asked, which is then placed after it; were it not settled first, no passkey's change could take its place (409)."""
    third = Authenticator(alg=-257)
    assert add(client, owner, third, name="Third", confirm_with=device).status_code == 200
    keys.turn_on()
    monkeypatch.setenv(e2e_keys.HOLD_RECOVERY_SETTING, "0")
    r = client.post("/api/ade/e2e/credentials", headers=owner,
                    json=enrol_body(keys, second, recovery_proof=keys.proof("credentials")))
    assert r.status_code == 200, r.text
    due = r.json()["hold"]["id"]
    monkeypatch.delenv(e2e_keys.HOLD_RECOVERY_SETTING)
    if route == "rotate":                                   # nothing read since: the change asked settles it first
        signed, private = keys.statement()
        r = client.post("/api/ade/e2e/rotate", headers=owner,
                        json={"statement": signed, "content_key": keys.content_key(private)})
    elif route == "off":
        r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": keys.statement("e2e-off")[0]})
    elif route == "credentials":
        r = client.post("/api/ade/e2e/credentials", headers=owner,
                        json=dict(enrol_body(keys, third), **confirmed(client, owner, device, purpose=ENROL)))
    elif route == "anchors":
        own = {"passkey_id": keys.passkey["id"], "salt": b64u(os.urandom(32)), "wrapped": b64u(os.urandom(120))}
        word = confirmed(client, owner, device, purpose=ANCHOR)
        r = client.post("/api/ade/e2e/anchors", headers=owner, json=dict({"anchors": [own], "mk_id": keys.mk_id}, **word))
    else:
        fourth = Authenticator(alg=-8)
        fourth.credential_id = os.urandom(32)               # not the second's, whose alg it shares
        options = client.post("/api/account/passkeys/options", headers=owner,
                              json=confirmed(client, owner, device, change="passkey-add")).json()
        r = client.post("/api/account/passkeys", headers=owner, json={
            "challenge_id": options["challenge_id"], "name": "Fourth", "credential": fourth.create(options)})
    assert r.status_code == 200, r.text
    with store.connect() as db:
        ended = holds.get(db, due)
    assert ended["done_at"] is not None and ended["failed"] is None
    assert passkey_of(client, owner, second)["id"] in {c["passkey_id"] for c in rows("ade_e2e_credentials")}


def test_a_turn_on_whose_time_has_come_takes_effect_before_another_could_take_its_place(client, owner, keys,
                                                                                       monkeypatch):
    """For turning on: one whose hold has ended, which nothing has settled yet, takes effect as another turn-on is
    asked, which is then refused (the statement it signed is no longer the account's next); never ended in its
    place."""
    monkeypatch.setenv(e2e_keys.HOLD_SETTING, "0")
    body, _, _ = keys.turn_on_body()
    first = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert first.status_code == 200, first.text
    monkeypatch.delenv(e2e_keys.HOLD_SETTING)
    again, _, _ = keys.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=again)
    assert r.status_code == 409, r.text
    compacted()
    with store.connect() as db:
        ended = holds.get(db, first.json()["hold"]["id"])
    assert ended["done_at"] is not None and ended["failed"] is None
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1] and view(client, owner)["on"] is True


def test_a_statement_sent_in_names_a_usable_key_and_is_signed_by_a_passkey_that_unlocks_the_keys(client, owner, keys,
                                                                                                 device, second):
    """Statements a link sends in: one whose key is not an X25519 key anything can be sealed to is
    refused, however well signed; and so is one signed by a passkey of the account that never unlocked the keys, as it
    is sent in and again as its hold ends."""
    mac = paired(client, owner)
    keys.turn_on()

    def send(signed):
        return mac.call("POST", "/e2e", {"statements": [{"payload": canonicalize(signed["payload"]).decode(),
                                                         "credential": signed["credential"]}]})
    zero = bytes(32)                                        # a low-order point: what is sealed to it, anyone opens
    r = send(keys.statement(key=b64u(zero), key_id=e2e.key_id(zero))[0])
    assert r.status_code == 422 and "not an X25519 key" in r.json()["detail"], r.text
    r = send(keys.statement(device=second)[0])
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.UPLOAD_STAYING, r.text
    assert rows("ade_e2e_pending") == []
    r = send(keys.statement()[0])
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    with store.connect() as db:          # its passkey no longer unlocks the keys by then (written into the database here)
        db.execute("DELETE FROM ade_e2e_credentials WHERE passkey_id = ?", (keys.passkey["id"],))
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, r.json()["hold"]["id"])["failed"] == e2e_keys.UPLOAD_STAYING
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1]


def test_a_key_statement_sent_in_that_turns_encryption_on_again_is_held_to_turning_on_and_seals_in_place(
        client, owner, keys, monkeypatch, notices):
    """Statements a link sends in after a restore that turn encryption on again are held to what turning it on again
    is as their hold ends (no new master key due, here refused), and once taken seal in place what links sent in the
    clear while it was off, as turning it on does; their notice says a change to encryption waits, and the page reads
    the statements they hold."""
    mac = paired(client, owner)
    keys.turn_on()
    for setting in (e2e_keys.HOLD_SETTING, e2e_keys.HOLD_RECOVERY_SETTING):
        monkeypatch.setenv(setting, "0")
    signed, _ = keys.statement("e2e-off")
    assert client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed}).status_code == 200
    keys.took(signed)
    assert view(client, owner)["on"] is False
    for setting in (e2e_keys.HOLD_SETTING, e2e_keys.HOLD_RECOVERY_SETTING):
        monkeypatch.delenv(setting)
    client_id = uuid.uuid4().hex
    assert post(mac, "step", "SEALME while off", client_id=client_id).status_code == 200
    signed, private = keys.statement(retires="all")         # turning on again, as a link that holds it sends it in
    sent = {"statements": [{"payload": canonicalize(signed["payload"]).decode(), "credential": signed["credential"]}]}
    _set_due = "INSERT INTO ade_e2e_state (user_id, rekey_due) VALUES (?, 1) ON CONFLICT (user_id) DO UPDATE SET rekey_due = 1"
    with store.connect() as db:          # a new master key due (written into the database here): refused as it ends
        db.execute(_set_due, (keys.account,))
    r = mac.call("POST", "/e2e", sent)
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    hold = r.json()["hold"]
    assert told(notices, hold) == told_as(hold, "held-e2e")
    assert view(client, owner)["pending"]["change"] == "upload"
    holds.settle(keys.account, now=hold["takes_effect_at"])
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.REKEY_DUE
        db.execute("UPDATE ade_e2e_state SET rekey_due = 0 WHERE user_id = ?", (keys.account,))
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1, 2]
    r = mac.call("POST", "/e2e", sent)
    assert r.status_code == 200, r.text
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    compacted()
    v = view(client, owner)
    assert v["on"] is True and v["newest"]["seq"] == 3 and v["sealed"]["events"] == 1
    (event,) = [e for e in rows("ade_link_events") if e["client_id"] == client_id]
    assert event["sealed_in_place"] == 1
    got = open_envelope(private, event["text"], e2e.event_slot(keys.account, mac.link_id, "step", None, None,
                                                                client_id))
    assert got["text"] == "SEALME while off"


def test_turning_on_compacts_the_database_only_when_it_sealed_something_in_place(client, owner, keys, monkeypatch):
    """Turning on with seal, where links sent nothing in the clear, writes nothing over: the database is not due
    for compaction, and none begins."""
    began = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: began.append(1))
    keys.turn_on(seal=True)
    v = view(client, owner)
    assert {k: v["sealed"][k] for k in ("events", "left", "rows", "talks")} == {"events": 0, "left": 0, "rows": 0,
                                                                              "talks": 0}
    assert v["compaction_due"] is False and began == []


def test_routine_rotations_are_at_most_one_a_day_and_revocations_are_not_bounded(client, owner, keys, monkeypatch):
    """A routine rotation is refused (429) less than a day after the last took effect, so a stolen passkey cannot
    grow the account's statements and content keys without end; a revocation, for a key that must go now, is not
    bounded; the bound is a setting (0: none)."""
    keys.turn_on()
    monkeypatch.setenv(e2e_keys.HOLD_SETTING, "0")
    rotate(keys)
    signed, private = keys.statement()
    soon = {"statement": signed, "content_key": keys.content_key(private)}
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=soon)
    assert r.status_code == 429 and "at most once in 24 hours" in r.json()["detail"], r.text
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=revocation(keys)[0]).json()["hold"]
    assert hold is not None and cancel(keys, hold["id"], device=keys.device).status_code == 200
    with store.connect() as db:          # a day after the last took effect (written into the database here)
        db.execute("UPDATE ade_e2e_statements SET created_at = created_at - 86400 WHERE seq = 2")
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text
    keys.took(signed, private)
    view(client, owner)                                     # its hold (no seconds) ends at this read
    monkeypatch.setenv(e2e_keys.ROTATE_SETTING, "0")
    assert rotate(keys)["hold"] is not None
    assert [s["seq"] for s in rows("ade_e2e_statements")] == [1, 2, 3, 4]


# ---- what connected apps propose, as encryption is turned on ------------------------------------------------------

def proposed(client, owner, monkeypatch, mac=None):
    """A connected app's two proposals for a linked session (`mac`, or one paired here): one the owner refused, with
    its reason and the owner's words in refusing it, and one that waits, with its words and its reason. (the refused
    one's id, the waiting one's id)"""
    monkeypatch.setenv("OPEN_EDITION_CONNECTOR", "on")
    monkeypatch.setenv("OPEN_EDITION_ADE_CONNECTOR_HOLD", "0")
    oauth._failed.clear()
    connector._ws_cache.clear()
    app = oauth.authenticate("Bearer " + connected(client, owner)["access_token"])
    mac = mac or paired(client, owner, name="mac")

    def propose(name, **args):
        result = connector.call(app, {"name": name, "arguments": args}, int(time.time()))
        assert result["isError"] is False, result
        return result["structuredContent"]["proposal"]["id"]

    refused = propose("propose_stop", session="link:" + mac.link_id, why="it loops")
    r = client.post(f"/api/connector/proposals/{refused}/refuse", headers=owner, json={"words": "no"})
    assert r.status_code == 200, r.text
    waits = propose("propose_instruction", session="link:" + mac.link_id, text="keep me", why="later")
    return refused, waits


def words(proposal_id):
    """A proposal's words, its reason and the words on it, as its row keeps them."""
    (row,) = rows("ade_proposals", id=proposal_id)
    return row["text"], row["why"], row["said"]


@pytest.mark.parametrize("sent", ["sealing off", "nothing", "a step"])
def test_turning_on_clears_the_words_of_every_decided_proposal_whether_or_not_it_seals(client, owner, keys,
                                                                                      monkeypatch, sent):
    """Turning encryption on clears, in the transaction in which it takes effect and whether or not it seals in place,
    the words of every connector proposal already decided, which nothing seals (proposals.forget_decided): the refused
    one loses its reason and the owner's words at once, not a day later; the one that waits keeps its words for the
    owner to read. Having written them over, the database is due for compaction, though nothing was sealed; and where
    a step sent in the clear was sealed too, the compaction is asked once."""
    mac = paired(client, owner, name="mac")
    refused, waits = proposed(client, owner, monkeypatch, mac=mac)
    if sent == "a step":
        assert post(mac, "step", "words in the clear", client_id=uuid.uuid4().hex).status_code == 200
    seal = sent != "sealing off"
    began = []
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: began.append(1))
    body, signed, private = keys.turn_on_body(seal=seal)
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner, json=body))
    assert r.status_code == 200, r.text
    assert words(refused) == (None, "it loops", "no")         # held: nothing cleared before its hold ends
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    with store.connect() as db:
        assert holds.get(db, r.json()["hold"]["id"])["failed"] is None
    assert words(refused) == (None, None, None)
    assert words(waits) == ("keep me", "later", None)
    v = view(client, owner)
    assert v["on"] is True and v["compaction_due"] is True and began == [1]
    if seal:
        assert {k: v["sealed"][k] for k in ("events", "rows", "talks")} == {"events": int(sent == "a step"), "rows": 0,
                                                                          "talks": 0}


def test_a_statement_sent_in_that_turns_encryption_on_again_clears_the_decided_proposals_words(client, owner, keys,
                                                                                              monkeypatch):
    """A key statement a link sends in after a restore, turning encryption on again, clears the decided proposals'
    words as its hold ends, as turning it on does; the waiting one keeps its words."""
    mac = paired(client, owner)
    keys.turn_on()
    for setting in (e2e_keys.HOLD_SETTING, e2e_keys.HOLD_RECOVERY_SETTING):
        monkeypatch.setenv(setting, "0")
    signed, _ = keys.statement("e2e-off")
    assert client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed}).status_code == 200
    keys.took(signed)
    assert view(client, owner)["on"] is False
    refused, waits = proposed(client, owner, monkeypatch, mac=mac)
    signed, _ = keys.statement(retires="all")               # turning on again, as a link that holds it sends it in
    sent = {"statements": [{"payload": canonicalize(signed["payload"]).decode(), "credential": signed["credential"]}]}
    r = mac.call("POST", "/e2e", sent)
    assert r.status_code == 200, r.text
    assert words(refused) == (None, "it loops", "no")
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    compacted()
    assert view(client, owner)["on"] is True
    assert words(refused) == (None, None, None)
    assert words(waits) == ("keep me", "later", None)
