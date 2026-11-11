"""Post-quantum keys for end-to-end encryption, the server's side:
version-3 statements of a post-quantum key; an account's era, classical or post-quantum, and the kem every
write to a post-quantum account's keys carries; the forms such an account takes (1,216-byte keys, 60-byte wraps, form-2
grants of exactly 1,197 bytes, a credential's anchor) and the classical ones it refuses; a server without ML-KEM
refusing what needs it; the hybrid recovery proof; and the upgrade of a classical account, a revocation with upgrade.
The page's side stands in as the tests' own (e2e_pq_page_side.py, written from the design's words): keys from 32-byte
seeds, the hybrid proof's answer from a decapsulation the server never makes."""

import hashlib
import hmac
import json
import os
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import e2e_pq_page_side as page
from proofpane_open import passkeys, store
from proofpane_open.ade import e2e, e2e_keys, holds, link
from proofpane_core.canonical import canonicalize
from test_ade_e2e import DROP, Keys, cancel, compacted, enrol, new_code_body, passkey_of, revocation, rows, signed_by, \
    staying, taken, view
from test_ade_e2e import device, keys, notices, owner, second, three  # noqa: F401  (fixtures)
from test_ade_e2e_envelope import b64u, raw_public, unb64u
from test_ade_link import HOST, paired
from test_ade_link_commands import assertion, nonce
from test_passkeys import Authenticator, confirmed

KEM = "x-wing"
UPGRADE_TOUCH = "e2e-upgrade-touch"


def grant_to(public_key):
    """A grant of a master key as the page seals one to a post-quantum key: a form-2 envelope of its 32 bytes, exactly
    1,197 bytes (the server reads its form alone, so its slot here is any text)."""
    return e2e._seal_hybrid(public_key, "proofpane-e2e/1|grant", os.urandom(32))


def hybrid_key():
    """A fresh post-quantum key pair as the page makes one: (its 32-byte seed, its 1,216-byte public key)."""
    seed = os.urandom(32)
    return seed, page.public_of(seed)


def hybrid_proof(client, owner, seed, purpose, public_key=None, digest=None):
    """The hybrid recovery proof as the page makes it: the server's challenge for the
    account's post-quantum recovery key (or `public_key`, the upgrade's new one), its ciphertext decapsulated with the
    seed the code gives, and HMAC-SHA256 under HKDF-SHA256(ss, info "proofpane-e2e/2 recovery proof") over the nonce
    and the purpose."""
    asked = {"purpose": purpose}
    if public_key is not None:
        asked["public_key"] = b64u(public_key)
    if digest is not None:
        asked["digest"] = digest
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json=asked)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["kem"] == KEM and "server_key" not in c
    ss = page.decapsulate(seed, unb64u(c["server_ct"]))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"proofpane-e2e/2 recovery proof").derive(ss)
    return {"challenge_id": c["challenge_id"],
            "proof": b64u(hmac.new(k, unb64u(c["nonce"]) + purpose.encode(), hashlib.sha256).digest())}


def touch_subject(mk_id, payload, credential_id, salt, public_key):
    """What a passkey's touch in the upgrade is made for, written here from the agreed words (the server's own
    function is not used, so a fault in it does not hide behind itself): "e2e-upgrade:" and the first 16 bytes of the
    sha256 of the RFC 8785 bytes of {credential_id, mk_id, public_key, salt, statement: the hex sha256 of the
    statement's canonical bytes}."""
    obj = {"credential_id": credential_id, "mk_id": mk_id, "public_key": public_key, "salt": salt,
           "statement": hashlib.sha256(canonicalize(payload)).hexdigest()}
    keys = sorted(obj)
    text = "{" + ",".join(json.dumps(k) + ":" + json.dumps(obj[k]) for k in keys) + "}"
    return "e2e-upgrade:" + hashlib.sha256(text.encode()).digest()[:16].hex()


class PQKeys(Keys):
    """The owner's side of a post-quantum account as the page keeps it: as Keys, with the content keys' and the
    credential's seeds in place of X25519 private keys, and the recovery key from a seed the code gives."""

    def __init__(self, client, owner, device):
        super().__init__(client, owner, device)
        self.recovery_seed = os.urandom(32)
        self.credential_seed = os.urandom(32)

    def statement(self, purpose="e2e-key", retires="grace", private=None, device=None, seq=None, prev=None,
                  hybrid=True, **over):
        """A statement the page signs: version 3 for a post-quantum key (`private`, here its seed, made when not
        given), or version 2 for a classical one (hybrid False). (the signed body, the seed or the private key)."""
        if not hybrid or purpose != "e2e-key":
            return super().statement(purpose, retires, private, device, seq, prev, **over)
        now = int(time.time())
        private = private or os.urandom(32)
        pk = page.public_of(private)
        p = {"v": 3, "deployment": HOST, "purpose": purpose, "account": self.account,
             "nonce": nonce(self.client, self.owner, purpose), "id": uuid.uuid4().hex,
             "seq": len(self.statements) + 1 if seq is None else seq, "prev": self.prev if prev is None else prev,
             "created_at": now, "expires_at": now + 600, "key": b64u(pk), "key_id": e2e.key_id(pk),
             "retires": retires}
        for name, value in over.items():
            if value is DROP:
                p.pop(name, None)
            else:
                p[name] = value
        return {"payload": p, "credential": assertion(device or self.device, p)}, private

    def took(self, signed, private=None):
        self.statements.append(canonicalize(signed["payload"]))
        if private is not None:
            self.content[signed["payload"]["key_id"]] = private

    def content_key(self, private, mk_id=None):
        if isinstance(private, (bytes, bytearray)):
            pk = page.public_of(private)
            return {"key_id": e2e.key_id(pk), "public_key": b64u(pk), "mk_id": mk_id or self.mk_id,
                    "wrapped": b64u(os.urandom(60))}
        return super().content_key(private, mk_id)

    def grant(self, to):
        return {"to": to, "sealed": grant_to(page.public_of(os.urandom(32)))}

    def credential(self, mk_id, passkey=None, seed=None):
        passkey = passkey or self.passkey
        return {"passkey_id": passkey["id"], "salt": b64u(os.urandom(32)),
                "public_key": b64u(page.public_of(seed or self.credential_seed)), "wrapped": b64u(os.urandom(60)),
                "anchor": mk_id}

    def turn_on_body(self, seal=None, **over):
        signed, private = self.statement(retires="all")
        self.mk_id = os.urandom(16).hex()
        body = {"statement": signed, "content_key": self.content_key(private), "mk_id": self.mk_id,
                "credential": self.credential(self.mk_id),
                "recovery": {"public_key": b64u(page.public_of(self.recovery_seed)), "anchor": b64u(os.urandom(60))},
                "grants": [self.grant(self.passkey["credential_id"]), self.grant("recovery")],
                "roster": b64u(os.urandom(300)), "kem": KEM}
        if seal is not None:
            body["seal"] = seal
        body.update(over)
        return body, signed, private

    def proof(self, purpose, private=None):
        return hybrid_proof(self.client, self.owner, private or self.recovery_seed, purpose)


@pytest.fixture
def pq(client, owner, device):
    return PQKeys(client, owner, device)


def pq_on(pq):
    """A post-quantum account with encryption on (its first turn-on, held for no time): the content key's seed."""
    got, seed = pq.turn_on()
    assert view(pq.client, pq.owner)["pq"] is True
    return seed


def rotation(keys, **over):
    signed, private = keys.statement()
    return dict({"statement": signed, "content_key": keys.content_key(private)}, **over), signed, private


def pq_revocation(keys, **over):
    """A revocation of a post-quantum account, as the page makes it: in post-quantum forms, kem said."""
    signed, private = keys.statement(retires="all")
    mk_id = os.urandom(16).hex()
    body = {"statement": signed, "content_key": keys.content_key(private, mk_id), "mk_id": mk_id,
            "chain": {"previous": keys.mk_id, "wrapped": b64u(os.urandom(60))}, "roster": b64u(os.urandom(300)),
            "content_keys": [{"key_id": k, "wrapped": b64u(os.urandom(60))} for k in keys.content],
            "remove": [], "high_security": False, "kem": KEM}
    body.update(over)
    if "grants" not in over:
        body["grants"] = [keys.grant(c) for c in staying(keys, body["remove"], body["high_security"])] + \
            [keys.grant("recovery")]
    return body, signed, private


# ---- version-3 statements -------------------------------------------------------------------------------------------

def test_a_post_quantum_turn_on_states_a_version_3_key_and_makes_the_account_post_quantum(client, owner, pq):
    body, signed, seed = pq.turn_on_body()
    assert len(signed["payload"]["key"]) == 1622 and signed["payload"]["v"] == 3
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert view(client, owner)["pq"] is False                       # nothing in force while it waits
    assert holds.settle(pq.account, now=hold["takes_effect_at"]) == [hold["id"]]
    pq.took(signed, seed)
    v = view(client, owner)
    assert v["pq"] is True and v["mlkem"] is True and v["on"] is True
    (cred,) = v["credentials"]
    assert cred["anchor"] == pq.mk_id and len(unb64u(cred["public_key"])) == 1216 and len(unb64u(cred["wrapped"])) == 60
    (statement,) = v["statements"]
    assert json.loads(statement["payload"])["v"] == 3 and len(unb64u(statement["public_key"])) == 1216
    assert v["classical_texts"] == {"events": 0, "rows": 0, "talks": 0, "total": 0}
    assert rows("ade_e2e_state")[0]["pq"] == 1


def test_a_statement_s_version_says_its_key_s_form(client, owner, pq):
    body, signed, seed = pq.turn_on_body()
    seed2, pk2 = hybrid_key()
    classical = b64u(raw_public(X25519PrivateKey.generate()))
    bad_key = bytearray(page.public_of(seed2))
    bad_key[0], bad_key[1] = 0x01, (bad_key[1] & 0xF0) | 0x0D        # its first coefficient 3,329: one too many
    cases = (
        ("version 3 with a 32-byte key", dict(key=classical, key_id=e2e.key_id(unb64u(classical))), 422,
         "version-3 statement's key is 1,216 bytes"),
        ("version 2 with a 1,216-byte key", dict(v=2), 422, "version-2 statement's key is 32 bytes"),
        ("version 4", dict(v=4), 422, "v is 2 for a classical key and 3"),
        ("a key_id not the key's", dict(key_id="0" * 32), 422, "sha256 of its key"),
        ("a key failing the coefficient check", dict(key=b64u(bytes(bad_key)), key_id=e2e.key_id(bytes(bad_key))), 422,
         "every coefficient is below 3,329"))
    for case, over, status, words in cases:
        s = pq.statement(retires="all", private=seed, **over)[0]
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, statement=s))
        assert r.status_code == status and words in r.json()["detail"], (case, r.text)
    # e2e-off stays version 2: a version-3 one is no statement
    off = pq.statement("e2e-off", v=3)[0]
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": off})
    assert r.status_code == 422 and "v is 2" in r.json()["detail"], r.text
    assert rows("ade_e2e_statements") == []


def test_the_first_turn_on_without_kem_is_classical_and_takes_no_post_quantum_part(client, owner, pq):
    """A first turn-on that does not say kem is a classical account's (a page or an app from an older release): its
    post-quantum parts are refused, and the classical body is taken as it always was."""
    body, signed, seed = pq.turn_on_body()
    del body["kem"]
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 422 and "32 bytes" in r.json()["detail"], r.text
    classical = Keys(client, owner, pq.device)
    body, signed, private = classical.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, kem="kyber"))
    assert r.status_code == 422 and "kem is x-wing" in r.json()["detail"], r.text
    classical.turn_on()
    assert view(client, owner)["pq"] is False


# ---- the era: kem on every write to a post-quantum account's keys --------------------------------------------------

def test_a_post_quantum_account_takes_no_write_without_kem_as_an_older_page_sends_it(client, owner, pq, second):
    pq_on(pq)
    body, _, _ = rotation(pq)
    assert "kem" not in body
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=body)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    rev = pq_revocation(pq)[0]
    del rev["kem"]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=rev)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    off = pq.statement("e2e-off")[0]
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": off})
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    r = client.post("/api/ade/e2e/anchors", headers=owner,
                    json={"anchors": [], "mk_id": pq.mk_id, "recovery_anchor": {"public_key": "x", "anchor": "y"}})
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    word = confirmed(client, owner, pq.device, purpose="e2e-enrol-touch")
    own = confirmed(client, owner, second, purpose="e2e-enrol-own")["confirmation"]
    enrolled = {"credential": dict(pq.credential(pq.mk_id, passkey_of(client, owner, second)), confirmation=own),
                "mk_id": pq.mk_id, "grant": pq.grant("x")["sealed"], "roster": b64u(os.urandom(300)), **word}
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=dict(enrolled))
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    r = client.post("/api/ade/e2e/credentials", headers=owner, json=dict(enrolled, kem=KEM))
    assert r.status_code == 200, r.text
    assert rows("ade_e2e_pending")[0]["kind"] == "credentials"


def test_a_classical_account_takes_kem_on_no_write_but_the_upgrade(client, owner, keys):
    keys.turn_on()
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": signed, "content_key": keys.content_key(private), "kem": KEM})
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.CLASSICAL_FORMS, r.text
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": signed, "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text


def test_a_post_quantum_account_refuses_classical_material(client, owner, pq, second):
    pq_on(pq)
    # a classical key in a version-2 statement, in place of a post-quantum one
    signed, private = pq.statement(hybrid=False)
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": signed, "content_key": Keys.content_key(pq, private), "kem": KEM})
    assert r.status_code == 422 and "1216 bytes" in r.json()["detail"], r.text
    # an older page's wraps: a content key's 76 bytes
    body, _, _ = rotation(pq, kem=KEM)
    body["content_key"] = dict(body["content_key"], wrapped=b64u(os.urandom(76)))
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=body)
    assert r.status_code == 422 and "60 bytes" in r.json()["detail"], r.text
    # a revocation granting in form 1, or naming a credential's 145-byte wrap
    rev, _, _ = pq_revocation(pq)
    form1 = [{"to": g["to"], "sealed": e2e.seal(raw_public(X25519PrivateKey.generate()), "grant", {"mk": "x"})}
             for g in rev["grants"]]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(rev, grants=form1))
    assert r.status_code == 422 and "1197 bytes" in r.json()["detail"], r.text
    short = [{"to": g["to"], "sealed": e2e._seal_hybrid(page.public_of(os.urandom(32)), "x", os.urandom(31))}
             for g in rev["grants"]]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(rev, grants=short))
    assert r.status_code == 422 and "1197 bytes" in r.json()["detail"], r.text
    word = confirmed(client, owner, pq.device, purpose="e2e-enrol-touch")
    own = confirmed(client, owner, second, purpose="e2e-enrol-own")["confirmation"]
    cred = dict(pq.credential(pq.mk_id, passkey_of(client, owner, second)), confirmation=own)
    base = {"mk_id": pq.mk_id, "grant": pq.grant("x")["sealed"], "roster": b64u(os.urandom(300)), "kem": KEM, **word}
    for case, bad, words in (
            ("a credential's 145-byte wrap", dict(cred, wrapped=b64u(os.urandom(145))), "60 bytes"),
            ("a credential's 32-byte key", dict(cred, public_key=b64u(raw_public(X25519PrivateKey.generate()))),
             "1216 bytes"),
            ("no anchor", {k: v for k, v in cred.items() if k != "anchor"}, "anchor"),
            ("an anchor of another master key", dict(cred, anchor="e" * 32), "master key the change names")):
        r = client.post("/api/ade/e2e/credentials", headers=owner, json=dict(base, credential=bad))
        assert r.status_code == 422 and words in r.json()["detail"], (case, r.text)
    assert rows("ade_e2e_pending") == []


def test_no_classical_key_is_stated_after_a_post_quantum_one(client, owner, pq):
    """Not on a post-quantum account, and not where the account's flag was put back (a database restored to before
    its upgrade) while a version-3 statement is still in its chain: links with state refuse one too."""
    pq_on(pq)
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET pq = 0 WHERE user_id = ?", (pq.account,))
    signed, private = pq.statement(hybrid=False)
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": signed, "content_key": Keys.content_key(pq, private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.CLASSICAL_AFTER, r.text


def test_a_change_placed_in_one_era_does_not_take_effect_in_another(client, owner, keys):
    """As a change's hold ends its era is still the account's: a classical rotation waiting while the account's keys
    became post-quantum (written into the database here) keeps nothing."""
    keys.turn_on()
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": signed, "content_key": keys.content_key(private)})
    hold = r.json()["hold"]
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET pq = 1 WHERE user_id = ?", (keys.account,))
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.ERA_CHANGED
    assert len(rows("ade_e2e_content_keys")) == 1


def test_the_roster_takes_up_to_131072_bytes(client, owner, pq):
    body, signed, seed = pq.turn_on_body(roster=b64u(os.urandom(131073)))
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 422 and "28 to 131072 bytes" in r.json()["detail"], r.text
    body, signed, seed = pq.turn_on_body(roster=b64u(os.urandom(131072)))
    assert client.post("/api/ade/e2e/turn-on", headers=owner, json=body).status_code == 200


# ---- a server without ML-KEM ---------------------------------------------------------------------------------------

def test_a_server_without_ml_kem_refuses_what_needs_it_and_says_why(client, owner, pq, monkeypatch):
    seed = pq_on(pq)
    monkeypatch.setattr(e2e, "_READY", False)
    v = view(client, owner)
    assert v["mlkem"] is False
    body, _, _ = rotation(pq, kem=KEM)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=body)
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "rotate"})
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    other = PQKeys(client, owner, pq.device)
    other.statements = list(pq.statements)
    body, _, _ = other.turn_on_body(replace=True)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    # a change placed while it could, whose hold ends once it cannot: nothing of it is kept, nothing sealed in form 1
    monkeypatch.setattr(e2e, "_READY", True)
    body, signed, private = rotation(pq, kem=KEM)
    hold = client.post("/api/ade/e2e/rotate", headers=owner, json=body).json()["hold"]
    monkeypatch.setattr(e2e, "_READY", False)
    assert holds.settle(pq.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e.MLKEM_REFUSED
    assert [k["key_id"] for k in rows("ade_e2e_content_keys")] == [e2e.key_id(page.public_of(seed))]


def test_a_server_without_ml_kem_takes_no_change_the_recovery_code_could_not_cancel(client, owner, pq, monkeypatch):
    """The recovery code cancels any change (rule (i)), and to a post-quantum key it proves itself by the hybrid proof
    alone, which needs ML-KEM: so a server without it places no change to a post-quantum account's keys, renewed
    anchors and turning encryption off too, though they make and check no key (503); and one placed before it lost
    ML-KEM, which the code could not cancel since, fails as its hold ends, while a passkey that may still cancels it."""
    pq_on(pq)
    wrapped = view(client, owner)["credentials"][0]["wrapped"]

    def anchors():
        body = {"anchors": [{"passkey_id": pq.passkey["id"], "salt": b64u(os.urandom(32)),
                             "wrapped": b64u(os.urandom(60)), "anchor": pq.mk_id}], "mk_id": pq.mk_id, "kem": KEM}
        return client.post("/api/ade/e2e/anchors", headers=owner,
                           json=dict(body, **confirmed(client, owner, pq.device, purpose=e2e_keys.TOUCH_ANCHOR)))

    def off():
        return client.post("/api/ade/e2e/off", headers=owner,
                           json={"statement": pq.statement("e2e-off", retires=None)[0], "kem": KEM})
    monkeypatch.setattr(e2e, "_READY", False)
    for place in (anchors, off):
        r = place()
        assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "cancel"})
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    for place in (anchors, off):
        monkeypatch.setattr(e2e, "_READY", True)
        r = place()
        assert r.status_code == 200, r.text
        placed = r.json()["hold"]
        monkeypatch.setattr(e2e, "_READY", False)
        assert view(client, owner)["pending"]["cancel_by_code"] is True
        assert holds.settle(pq.account, now=placed["takes_effect_at"]) == [placed["id"]]
        with store.connect() as db:
            assert holds.get(db, placed["id"])["failed"] == e2e.MLKEM_REFUSED
    v = view(client, owner)
    assert v["on"] is True and v["pending"] is None and v["credentials"][0]["wrapped"] == wrapped
    # a passkey's cancel needs no ML-KEM
    monkeypatch.setattr(e2e, "_READY", True)
    placed = off().json()["hold"]
    monkeypatch.setattr(e2e, "_READY", False)
    r = cancel(pq, placed["id"], pq.device)
    assert r.status_code == 200, r.text
    with store.connect() as db:
        assert holds.get(db, placed["id"])["cancelled_at"] is not None


def sent_in(s, keys, purpose="e2e-off", **over):
    """A statement the account's passkey signed, sent in by its link `s` (POST /e2e) as a link holding it would."""
    signed, _ = keys.statement(purpose, **over)
    return s.call("POST", "/e2e", {"statements": [{"payload": canonicalize(signed["payload"]).decode(),
                                                   "credential": signed["credential"]}]})


def passkey_added(client, owner, device, name):
    """A passkey added as the page adds one while encryption is on: its options confirmed by `device`, then its
    ceremony: (the options' answer, the addition's answer, or None where the options were refused)."""
    r = client.post("/api/account/passkeys/options", headers=owner,
                    json=confirmed(client, owner, device, change="passkey-add"))
    if r.status_code != 200:
        return r, None
    options = r.json()
    return r, client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": name, "credential": Authenticator(alg=-8).create(options)})


def test_without_ml_kem_a_link_s_statements_are_not_held_where_the_code_could_not_cancel_them(client, owner, pq,
                                                                                            monkeypatch):
    """Statements a link sends in are held as a change the recovery code cancels: a passkey of the account signs an
    e2e-off and a link sends it in. On a post-quantum account the code proves
    itself by the hybrid proof alone, so without ML-KEM they are refused (503); held before the server lost ML-KEM,
    they fail as their hold ends, and encryption stays on."""
    mac = paired(client, owner)
    pq_on(pq)
    monkeypatch.setattr(e2e, "_READY", False)
    r = sent_in(mac, pq, retires=None)
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    monkeypatch.setattr(e2e, "_READY", True)
    r = sent_in(mac, pq, retires=None)
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    placed = r.json()["hold"]
    monkeypatch.setattr(e2e, "_READY", False)
    assert view(client, owner)["pending"]["cancel_by_code"] is True
    assert holds.settle(pq.account, now=placed["takes_effect_at"]) == [placed["id"]]
    with store.connect() as db:
        assert holds.get(db, placed["id"])["failed"] == e2e.MLKEM_REFUSED
    assert view(client, owner)["on"] is True and len(rows("ade_e2e_statements")) == 1


def test_without_ml_kem_a_passkey_is_not_added_where_the_code_could_not_cancel_it(client, owner, pq, monkeypatch):
    """While encryption is on a passkey's addition is held as a change the recovery code cancels. Without ML-KEM on a
    post-quantum account the code could not cancel it, so its options are refused before the passkey's ceremony (503),
    and so is the addition whose options came before the server lost ML-KEM; one held before fails as its hold ends,
    and the passkey is not added."""
    pq_on(pq)
    monkeypatch.setattr(e2e, "_READY", False)
    r, _ = passkey_added(client, owner, pq.device, "Refused")
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    monkeypatch.setattr(e2e, "_READY", True)
    early = client.post("/api/account/passkeys/options", headers=owner,
                        json=confirmed(client, owner, pq.device, change="passkey-add")).json()
    monkeypatch.setattr(e2e, "_READY", False)
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": early["challenge_id"], "name": "Early", "credential": Authenticator(alg=-8).create(early)})
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    monkeypatch.setattr(e2e, "_READY", True)
    _, r = passkey_added(client, owner, pq.device, "Held")
    assert r.status_code == 200, r.text
    placed = r.json()["held"]
    monkeypatch.setattr(e2e, "_READY", False)
    assert view(client, owner)["pending"]["cancel_by_code"] is True
    assert holds.settle(pq.account, now=placed["takes_effect_at"]) == [placed["id"]]
    with store.connect() as db:
        assert holds.get(db, placed["id"])["failed"] == e2e.MLKEM_REFUSED
    names = {p["name"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    assert not {"Refused", "Early", "Held"} & names


def test_without_ml_kem_a_classical_account_still_adds_a_passkey_and_takes_a_link_s_statements(client, owner, keys,
                                                                                               monkeypatch):
    """Where the recovery code needs no ML-KEM to cancel, nothing changes: a classical account's code proves itself by
    the classical proof, so its passkey's addition and a link's statements are held and take effect without ML-KEM."""
    mac = paired(client, owner)
    keys.turn_on()
    monkeypatch.setattr(e2e, "_READY", False)
    _, r = passkey_added(client, owner, keys.device, "Spare")
    assert r.status_code == 200, r.text
    placed = r.json()["held"]
    assert holds.settle(keys.account, now=placed["takes_effect_at"]) == [placed["id"]]
    assert "Spare" in {p["name"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    r = sent_in(mac, keys)
    assert r.status_code == 200 and r.json()["held"] == 1, r.text
    placed = r.json()["hold"]
    assert holds.settle(keys.account, now=placed["takes_effect_at"]) == [placed["id"]]
    with store.connect() as db:
        assert holds.get(db, placed["id"])["failed"] is None
    assert view(client, owner)["on"] is False


def test_without_ml_kem_a_post_quantum_account_whose_encryption_is_off_adds_a_passkey_at_once(client, owner, pq,
                                                                                            monkeypatch):
    """A post-quantum account whose encryption is off holds no passkey's addition, which the code would have to cancel:
    the passkey is added at once, as before."""
    pq_on(pq)
    signed = pq.statement("e2e-off", retires=None)[0]
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed, "kem": KEM})
    assert r.status_code == 200, r.text
    assert holds.settle(pq.account, now=r.json()["hold"]["takes_effect_at"]) == [r.json()["hold"]["id"]]
    assert view(client, owner)["on"] is False
    monkeypatch.setattr(e2e, "_READY", False)
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, change="passkey-add"))
    assert options.status_code == 200, options.text
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options.json()["challenge_id"], "name": "Spare",
        "credential": Authenticator(alg=-8).create(options.json())})
    assert r.status_code == 200 and "held" not in r.json() and r.json()["name"] == "Spare", r.text


def test_the_probe_is_asked_once_and_the_app_asks_it_at_start(monkeypatch):
    calls = []
    monkeypatch.setattr(e2e, "_READY", None)
    monkeypatch.setattr(e2e, "mlkem_here", lambda: calls.append(1) or False)
    monkeypatch.setattr(e2e_keys.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    e2e_keys.start()
    assert calls == [1] and e2e.ready() is False and calls == [1]


# ---- the hybrid recovery proof ---------------------------------------------------------------------------------------

def test_the_hybrid_proof_confirms_a_change_of_a_post_quantum_account(client, owner, pq):
    pq_on(pq)
    body, signed, private = rotation(pq, kem=KEM)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=dict(body, recovery_proof=pq.proof("rotate")))
    assert r.status_code == 200, r.text
    assert r.json()["hold"]["kind"] == e2e_keys.HOLD_RECOVERY
    assert rows("ade_e2e_pending")[0]["by_code"] == 1


def test_the_hybrid_proof_is_refused_from_another_code_purpose_account_or_challenge(client, owner, pq, monkeypatch):
    pq_on(pq)

    def asked(**over):
        body = dict(rotation(pq, kem=KEM)[0], **over)
        return client.post("/api/ade/e2e/rotate", headers=owner, json=body)

    # another code's seed, another purpose, the proof's bytes changed
    r = asked(recovery_proof=_wrong_seed_proof(client, owner))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_WRONG, r.text
    r = asked(recovery_proof=pq.proof("off"))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, r.text
    p = pq.proof("rotate")
    raw = bytearray(unb64u(p["proof"]))
    raw[0] ^= 1
    r = asked(recovery_proof=dict(p, proof=b64u(bytes(raw))))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_WRONG, r.text
    # the classical proof for a post-quantum account: its id is no hybrid proof's
    classical_id, _ = passkeys._new_challenge(e2e_keys.PROOF_PREFIX + "rotate", pq.account, int(time.time()))
    r = asked(recovery_proof={"challenge_id": classical_id, "proof": b64u(bytes(32))})
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, r.text
    # an id's fields changed (another purpose, account or key), or its sealed secret moved to another id
    good = pq.proof("rotate")
    head, box = good["challenge_id"].split(".")
    fields = json.loads(unb64u(head))
    for name, value in (("p", e2e_keys.PROOF2_PREFIX + "off"), ("u", "someone-else"), ("r", "0" * 64),
                        ("e", fields["e"] + 1)):
        moved = b64u(canonicalize(dict(fields, **{name: value}))) + "." + box
        r = asked(recovery_proof=dict(good, challenge_id=moved))
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, (name, r.text)
    other = pq.proof("rotate")
    swapped = head + "." + other["challenge_id"].split(".")[1]
    r = asked(recovery_proof=dict(good, challenge_id=swapped))
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, r.text
    # a head RFC 8785 cannot write is no id this deployment made: stale, never a server error
    for extra in ("NaN", "1e400", "9007199254740993", '"\\ud800"'):
        crafted = ('{"c":"' + b64u(os.urandom(32)) + '","x":' + extra + '}').encode()
        r = asked(recovery_proof=dict(good, challenge_id=b64u(crafted) + "." + box))
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, (extra, r.text)
    # used once
    assert asked(recovery_proof=good).status_code == 200
    r = asked(recovery_proof=good)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_STALE, r.text


def _wrong_seed_proof(client, owner):
    """A proof answered with a seed that is not the code's: the challenge decapsulates (ML-KEM's implicit rejection
    gives a secret all the same), and its MAC is wrong."""
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "rotate"})
    c = r.json()
    ss = page.decapsulate(os.urandom(32), unb64u(c["server_ct"]))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"proofpane-e2e/2 recovery proof").derive(ss)
    return {"challenge_id": c["challenge_id"],
            "proof": b64u(hmac.new(k, unb64u(c["nonce"]) + b"rotate", hashlib.sha256).digest())}


def test_a_hybrid_proof_made_before_the_recovery_key_changed_no_longer_passes(client, owner, pq):
    pq_on(pq)
    p = pq.proof("rotate")
    new_seed = os.urandom(32)
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_recovery SET public_key = ? WHERE user_id = ?",
                   (b64u(page.public_of(new_seed)), pq.account))
    body = dict(rotation(pq, kem=KEM)[0], recovery_proof=p)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=body)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.PROOF_WRONG, r.text


def test_a_classical_account_keeps_the_classical_proof(client, owner, keys):
    keys.turn_on()
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "rotate"})
    assert r.status_code == 200 and "server_key" in r.json() and "server_ct" not in r.json()
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private),
                                                                 "recovery_proof": keys.proof("rotate")})
    assert r.status_code == 200, r.text


def test_a_proof_for_a_new_recovery_key_is_the_upgrade_s_alone(client, owner, keys):
    keys.turn_on()
    seed, pk = hybrid_key()
    for asked, words in (({"purpose": "rotate", "public_key": b64u(pk)}, "purpose revoke"),
                         ({"purpose": "revoke", "public_key": b64u(bytes(32))}, "1216 bytes"),
                         ({"purpose": "reseal"}, "digest"),
                         ({"purpose": "rotate", "digest": "a" * 64}, "digest")):
        r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json=asked)
        assert r.status_code == 422 and words in r.json()["detail"], (asked, r.text)
    p = hybrid_proof(client, owner, seed, "revoke", public_key=pk)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(link.LinkError) as refused:
            e2e_keys.use_proof(db, keys.account, p, "revoke", int(time.time()))     # checked against the stored key
        assert refused.value.status == 403
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        other_seed, other_pk = hybrid_key()
        with pytest.raises(link.LinkError) as refused:
            e2e_keys.use_proof(db, keys.account, p, "revoke", int(time.time()), public_key=b64u(other_pk))
        assert refused.value.status == 403 and str(refused.value) == e2e_keys.PROOF_WRONG
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        e2e_keys.use_proof(db, keys.account, p, "revoke", int(time.time()), public_key=b64u(pk))


# ---- the upgrade -----------------------------------------------------------------------------------------------------

class Upgrade:
    """An upgrade of a classical account (`keys`, encryption on) as the page makes it: a new master key,
    a version-3 statement retiring every earlier key, a post-quantum content key, each passkey kept touched once for its
    fresh key (`touched`: (its passkey row, its authenticator)), the recovery key made post-quantum from the code kept
    (with both proofs) or from a new one, every content key wrapped again in 60 bytes. `signer`: the authenticator that
    signs the statement (the page's own unless given), whose touch is its signature."""

    def __init__(self, keys, touched, kept=True, signer=None):
        self.keys, self.touched, self.kept = keys, touched, kept
        self.signer = signer or keys.device
        self.seed = os.urandom(32)
        self.recovery_seed = os.urandom(32)
        self.mk_id = os.urandom(16).hex()
        self.creds = {}

    def statement(self):
        k, now = self.keys, int(time.time())
        pk = page.public_of(self.seed)
        p = {"v": 3, "deployment": HOST, "purpose": "e2e-key", "account": k.account,
             "nonce": nonce(k.client, k.owner, "e2e-key"), "id": uuid.uuid4().hex, "seq": len(k.statements) + 1,
             "prev": k.prev, "created_at": now, "expires_at": now + 600, "key": b64u(pk), "key_id": e2e.key_id(pk),
             "retires": "all"}
        return {"payload": p, "credential": assertion(self.signer, p)}

    def body(self, touch=True, **over):
        k = self.keys
        signed = self.statement()
        p = signed["payload"]
        enrolled = {r["passkey_id"] for r in rows("ade_e2e_credentials")}
        kept_ids = {row["id"] for row, _ in self.touched}
        credentials = []
        for row, dev in self.touched:
            seed = os.urandom(32)
            c = {"passkey_id": row["id"], "salt": b64u(os.urandom(32)), "public_key": b64u(page.public_of(seed)),
                 "wrapped": b64u(os.urandom(60)), "anchor": self.mk_id}
            if touch and dev is not self.signer:
                c["confirmation"] = confirmed(k.client, k.owner, dev, purpose=UPGRADE_TOUCH, change=touch_subject(
                    self.mk_id, p, row["credential_id"], c["salt"], c["public_key"]))["confirmation"]
            self.creds[row["id"]] = seed
            credentials.append(c)
        rpk = page.public_of(self.recovery_seed)
        body = {"statement": signed,
                "content_key": {"key_id": p["key_id"], "public_key": p["key"], "mk_id": self.mk_id,
                                "wrapped": b64u(os.urandom(60))},
                "mk_id": self.mk_id, "chain": {"previous": k.mk_id, "wrapped": b64u(os.urandom(60))},
                "roster": b64u(os.urandom(300)),
                "content_keys": [{"key_id": key_id, "wrapped": b64u(os.urandom(60))} for key_id in k.content],
                "remove": sorted(enrolled - kept_ids), "high_security": False, "kem": KEM, "upgrade": True,
                "credentials": credentials, "kept": self.kept,
                "recovery": {"public_key": b64u(rpk), "anchor": b64u(os.urandom(60))},
                "grants": [{"to": row["credential_id"], "sealed": grant_to(page.public_of(self.creds[row["id"]]))}
                           for row, _ in self.touched] + [{"to": "recovery", "sealed": grant_to(rpk)}]}
        if self.kept:
            body["recovery_proof"] = k.proof("revoke")
            body["new_recovery_proof"] = hybrid_proof(k.client, k.owner, self.recovery_seed, "revoke",
                                                      public_key=rpk)
        body.update(over)
        return body

    def send(self, body):
        return self.keys.client.post("/api/ade/e2e/revoke", headers=self.keys.owner, json=body)

    def taken(self, body):
        """What the page keeps once the upgrade took effect."""
        k = self.keys
        k.took(body["statement"])
        k.content[body["statement"]["payload"]["key_id"]] = self.seed
        k.mk_id = self.mk_id


@pytest.fixture
def classical_two(client, owner, keys, device, second):
    """A classical account with encryption on and two passkeys that unlock it: the page's own and the second. (the
    second's passkey row)."""
    keys.turn_on()
    enrol(keys, second, device)
    return passkey_of(client, owner, second)


def test_the_upgrade_keeps_the_code_and_takes_effect_whole_after_a_day(client, owner, keys, device, second,
                                                                       classical_two, notices, monkeypatch):
    from test_ade_link import paired
    from test_ade_e2e import send_sealed
    begun = []
    # its compaction begun, not run: run in a thread of its own, it would clear the flag this test reads
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: begun.append(1))
    mac = paired(client, owner)
    old_mk, old_key = keys.mk_id, keys.key()
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)])
    body = up.body()
    r = up.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    # made with the code's proof: a day, and only the code cancels it
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY and hold["takes_effect_at"] - hold["approved_at"] == 86400
    assert hold["summary"] == "The keys of end-to-end encryption are being upgraded to post-quantum keys"
    v = view(client, owner)
    assert v["pq"] is False and v["pending"]["by_code"] is True and v["pending"]["cancel_by"] == []
    assert v["pending"]["replaces_code"] is False and v["pending"]["body"]["upgrade"] is True
    assert "confirmation" not in json.dumps(v["pending"]["body"]["credentials"])
    assert v["pending"]["body"]["kept_against"] == v["recovery"]["public_key"]
    # nothing in force while it waits: links seal to the classical key, the other keys stand
    assert send_sealed(keys, mac, "step", private=old_key)[0].status_code == 200
    assert {g["mk_id"] for g in v["grants"]} == {old_mk}
    # nothing changes beside it while it waits: made with the code's proof, only the code cancels it, so a change a
    # passkey alone signed takes no place of it (rule (v), as for any revocation)
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                "content_key": keys.content_key(private)})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    # its time comes
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] is None
    up.taken(body)
    v = view(client, owner)
    assert v["pq"] is True and v["mk_id"] == up.mk_id
    creds = {c["passkey_id"]: c for c in v["credentials"]}
    assert set(creds) == {keys.passkey["id"], classical_two["id"]}
    for c in body["credentials"]:
        mine = creds[c["passkey_id"]]
        assert (mine["salt"], mine["public_key"], mine["wrapped"], mine["anchor"]) == (
            c["salt"], c["public_key"], c["wrapped"], up.mk_id)
    assert v["recovery"]["public_key"] == body["recovery"]["public_key"]
    assert {g["mk_id"] for g in v["grants"]} == {up.mk_id} and len(v["grants"]) == 3
    assert all(len(unb64u(g["sealed"][5:])) == 1197 for g in v["grants"])
    assert {k["mk_id"] for k in v["content_keys"]} == {up.mk_id}
    assert all(len(unb64u(k["wrapped"])) == 60 for k in v["content_keys"])
    assert json.loads(v["statements"][-1]["payload"])["v"] == 3
    assert [(c["mk_id"], c["previous"]) for c in v["chain"]] == [(up.mk_id, old_mk)]
    # the classical keys are retired at once (as of the hold's end, by the settle's clock here), and the database is due
    # for compaction now and again a day later
    assert [s["retired_at"] for s in v["statements"][:-1]] == [hold["takes_effect_at"]]
    with store.connect() as db:
        assert e2e_keys.key_state(db, keys.account, e2e.key_id(raw_public(old_key)), hold["takes_effect_at"]) == \
            "retired"
    (state,) = rows("ade_e2e_state")
    assert begun == [1] and state["pq"] == 1 and state["compaction_due"] is not None
    assert state["compact_again_at"] == hold["takes_effect_at"] + e2e_keys.COMPACT_AGAIN
    compacted()


def test_the_upgrade_s_second_compaction_is_due_a_day_after_it_took_effect(client, owner, keys, device,
                                                                         classical_two, monkeypatch):
    up = Upgrade(keys, [(keys.passkey, device)], kept=False)
    body = up.body(remove=[classical_two["id"]])
    hold = up.send(body).json()["hold"]
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    compacted()
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET compaction_due = NULL")
    again = rows("ade_e2e_state")[0]["compact_again_at"]
    assert e2e_keys.compact_if_due(now=again - 1) is None and rows("ade_e2e_state")[0]["compact_again_at"] == again
    ran = []
    monkeypatch.setattr(e2e_keys, "compact", lambda *a, **kw: ran.append(1) or True)
    assert e2e_keys.compact_if_due(now=again) is True and ran == [1]
    (state,) = rows("ade_e2e_state")
    assert state["compact_again_at"] is None and state["compaction_due"] is None


def test_an_upgrade_with_a_new_code_waits_a_day_and_a_passkey_it_keeps_cancels_it(client, owner, keys, device,
                                                                                   second, classical_two):
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)], kept=False)
    body = up.body()
    assert "new_recovery_proof" not in body
    r = up.send(dict(body, new_recovery_proof={"challenge_id": "x", "proof": "y"}))
    assert r.status_code == 422 and "carries none" in r.json()["detail"], r.text
    r = up.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG
    v = view(client, owner)
    assert v["pending"]["by_code"] is False and v["pending"]["replaces_code"] is True
    assert sorted(v["pending"]["cancel_by"]) == sorted([keys.passkey["id"], classical_two["id"]])
    assert cancel(keys, hold["id"], second).status_code == 200
    holds.settle(keys.account, now=hold["takes_effect_at"] + 1)
    v = view(client, owner)
    assert v["pq"] is False and v["pending"] is None
    assert all(c["anchor"] is None for c in v["credentials"])


def test_a_stolen_passkey_s_upgrades_are_taken_over_by_a_revocation_of_it(client, owner, keys, device, second, three):
    """The thief's case, with the upgrade: one stolen passkey (B) places an upgrade that keeps itself alone, removes
    every other passkey (A, C) and makes a new recovery code, and places another the moment one is cancelled. A waiting
    upgrade is a revocation for rule (v): B's own revocation of A and C takes its place but waits a day, as it always
    has, and a passkey it removes (A) puts a revocation of B (C and the code kept) in the place of either at once;
    B then neither cancels that one nor puts an upgrade in its place, and it takes effect."""
    (a, b, c), _ = three
    b_row = passkey_of(client, owner, second)

    def thiefs_upgrade():
        up = Upgrade(keys, [(b_row, second)], kept=False, signer=second)
        return up.send(up.body())
    r = thiefs_upgrade()
    assert r.status_code == 200, r.text
    first = r.json()["hold"]
    pending = view(client, owner)["pending"]
    assert pending["body"]["upgrade"] is True and sorted(pending["removes"]) == sorted([a, c])
    assert (sorted(pending["cancel_by"]), pending["cancel_by_code"]) == (sorted([a, b, c]), True)
    # B's revocation of A and C, in place of its own upgrade: no shorter for it, a day, and A may still cancel it
    body, signed, private = revocation(keys, remove=[a, c], recovery=new_code_body())
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=signed_by(keys, body, private, second))
    assert r.status_code == 200, r.text
    second_hold = r.json()["hold"]
    assert second_hold["kind"] == e2e_keys.HOLD_LONG
    assert second_hold["takes_effect_at"] - second_hold["approved_at"] == 86400
    with store.connect() as db:
        assert holds.get(db, first["id"])["failed"] == e2e_keys.REPLACED
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([a, b, c])
    # and another upgrade in its place, which A's revocation of B takes the place of in turn
    r = thiefs_upgrade()
    assert r.status_code == 200, r.text
    upgrade = r.json()["hold"]
    with store.connect() as db:
        assert holds.get(db, second_hold["id"])["failed"] == e2e_keys.REPLACED
    mine, signed, private = revocation(keys, remove=[b])                            # A's, in its place
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=mine)
    assert r.status_code == 200, r.text
    ours = r.json()["hold"]
    with store.connect() as db:
        assert holds.get(db, upgrade["id"])["failed"] == e2e_keys.REPLACED
    assert sorted(view(client, owner)["pending"]["cancel_by"]) == sorted([a, c])
    r = cancel(keys, ours["id"], second)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    r = thiefs_upgrade()
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    assert [p["hold_id"] for p in rows("ade_e2e_pending")] == [ours["id"]]
    assert holds.settle(keys.account, now=ours["takes_effect_at"]) == [ours["id"]]
    keys.took(signed, private)
    keys.mk_id = mine["mk_id"]
    v = view(client, owner)
    assert v["pq"] is False and sorted(cr["passkey_id"] for cr in v["credentials"]) == sorted([a, c])


def test_an_owner_left_with_only_the_recovery_code_adds_a_passkey_in_place_of_a_stolen_one_s_upgrade(
        client, owner, keys, device, second, classical_two):
    """The case table's first row, the thief placing upgrades: a stolen passkey (B) had the owner's (A) removed,
    unnoticed, and the owner holds the recovery code alone, which cancels any change. A passkey the owner adds by the
    code's proof takes the place of B's waiting upgrade, code-backed, so B neither cancels it nor puts another upgrade
    in its place."""
    a, b = keys.passkey["id"], classical_two["id"]
    body, signed, private = revocation(keys, remove=[a])
    sent = signed_by(keys, body, private, second)
    hold = client.post("/api/ade/e2e/revoke", headers=owner, json=sent).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    taken(keys, sent, private)
    assert [p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]] == [b]

    def thiefs_upgrade():
        up = Upgrade(keys, [(classical_two, second)], kept=False, signer=second)
        return up.send(up.body())
    r = thiefs_upgrade()
    assert r.status_code == 200, r.text
    waiting = r.json()["hold"]
    new = Authenticator(alg=-257)
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"recovery_proof": keys.proof("passkey")}).json()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "New", "credential": new.create(options), "prf": True})
    assert r.status_code == 200, r.text
    held = r.json()["held"]
    with store.connect() as db:
        assert holds.get(db, waiting["id"])["failed"] == e2e_keys.REPLACED
    pending = view(client, owner)["pending"]
    assert (pending["id"], pending["by_code"], pending["cancel_by"]) == (held["id"], True, [])
    r = cancel(keys, held["id"], second)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    r = thiefs_upgrade()
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text


def test_a_passkey_not_touched_is_removed_by_the_upgrade_or_it_is_refused(client, owner, keys, device, second,
                                                                          classical_two):
    up = Upgrade(keys, [(keys.passkey, device)], kept=False)
    r = up.send(up.body(remove=[]))                             # the second passkey neither touched nor removed
    assert r.status_code == 422 and "grants the new master key to every credential" in r.json()["detail"], r.text
    body = up.body(remove=[classical_two["id"]])
    r = up.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert view(client, owner)["pending"]["removes"] == [classical_two["id"]]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    v = view(client, owner)
    assert v["pq"] is True and [c["passkey_id"] for c in v["credentials"]] == [keys.passkey["id"]]
    assert classical_two["id"] not in [p["id"] for p in v["passkeys"]]


def test_an_upgrade_gives_a_new_key_to_each_passkey_that_stays_not_a_grant_alone(client, owner, keys, device, second,
                                                                                 classical_two):
    """A grant to a passkey that stays, with no post-quantum key for it, passes the revocation's own rule (a grant
    for every credential that stays) but not the upgrade's: that passkey would keep its classical key."""
    up = Upgrade(keys, [(keys.passkey, device)], kept=False)
    body = up.body(remove=[])
    body["grants"].append({"to": classical_two["credential_id"], "sealed": grant_to(page.public_of(os.urandom(32)))})
    r = up.send(body)
    assert r.status_code == 422 and "a post-quantum key made at its own touch" in r.json()["detail"], r.text


def test_statements_sent_in_never_state_a_classical_key_after_a_post_quantum_one(client, owner, keys, device, second,
                                                                                classical_two):
    """A server restored from a backup made before the upgrade holds no post-quantum statement: a link that sends in
    the upgrade's, and a classical key's after it, is refused by the chain it sends alone."""
    mac = paired(client, owner)
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)])
    body = up.body()
    hold = up.send(body).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    up.taken(body)
    sent = [{"payload": s["payload"], "credential": s["credential"]}
            for s in mac.call("GET", "/e2e").json()["statements"]]
    after, _ = keys.statement(retires="all")              # version 2, chained after the upgrade's, signed as any
    sent.append({"payload": canonicalize(after["payload"]).decode(), "credential": after["credential"]})
    with store.connect() as db:                            # restored from a backup made before the upgrade
        db.execute("DELETE FROM ade_e2e_statements WHERE seq = 2")
        db.execute("UPDATE ade_e2e_state SET pq = 0")
    r = mac.call("POST", "/e2e", {"statements": sent})
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.CLASSICAL_AFTER, r.text
    r = mac.call("POST", "/e2e", {"statements": sent[:2]})   # without it, the upgrade's statement goes back in
    assert r.status_code == 200 and r.json()["held"] == 1, r.text


def test_each_passkey_kept_in_the_upgrade_is_touched_for_that_very_change(client, owner, keys, device, second,
                                                                          classical_two):
    """Each passkey that stays, but the statement's signer, comes with a confirmation its own passkey made at its touch
    for the upgrade's ceremony and for what existed before it: the new master key, the statement, that credential,
    its fresh salt and its new key. One missing, made by another passkey, for another ceremony, or for other values,
    is refused, and left unused."""
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)], kept=False)
    body = up.body()
    entry = next(c for c in body["credentials"] if c["passkey_id"] == classical_two["id"])
    good = entry["confirmation"]
    p = body["statement"]["payload"]

    def with_entry(**changes):
        return dict(body, credentials=[dict(c, **changes) if c is entry else c for c in body["credentials"]])

    def touch(dev, purpose=UPGRADE_TOUCH, **values):
        v = dict(salt=entry["salt"], public_key=entry["public_key"], mk_id=up.mk_id, payload=p)
        v.update(values)
        return confirmed(client, owner, dev, purpose=purpose, change=touch_subject(
            v["mk_id"], v["payload"], classical_two["credential_id"], v["salt"], v["public_key"]))["confirmation"]

    for case, bad, words in (
            ("none", with_entry(confirmation=None), e2e_keys.UPGRADE_OWN),
            ("made by another passkey", with_entry(confirmation=touch(device)), e2e_keys.UPGRADE_OWN),
            ("for another ceremony", with_entry(confirmation=confirmed(client, owner, second,
                                                                       purpose="e2e-anchor-touch")["confirmation"]),
             passkeys.CONFIRM_OTHER),
            ("for another salt", with_entry(confirmation=touch(second, salt=b64u(os.urandom(32)))),
             passkeys.CONFIRM_OTHER),
            ("for another key", with_entry(confirmation=touch(second, public_key=b64u(page.public_of(os.urandom(32))))),
             passkeys.CONFIRM_OTHER),
            ("for another master key", with_entry(confirmation=touch(second, mk_id="e" * 32)), passkeys.CONFIRM_OTHER),
            ("for another statement", with_entry(confirmation=touch(second, payload=dict(p, id=uuid.uuid4().hex))),
             passkeys.CONFIRM_OTHER)):
        r = up.send(bad)
        assert r.status_code == 403 and r.json()["detail"] == words, (case, r.text)
    assert entry["confirmation"] == good
    r = up.send(body)                                           # the right one, unused by every refusal
    assert r.status_code == 200, r.text


def test_an_upgrade_that_keeps_the_code_proves_it_twice(client, owner, keys, device, second, classical_two):
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)])
    body = up.body()
    for case, bad, status, words in (
            ("no proof of the new key", {k: v for k, v in body.items() if k != "new_recovery_proof"}, 403,
             e2e_keys.KEPT_PROOFS),
            ("no proof of the code in force", {k: v for k, v in body.items() if k != "recovery_proof"}, 403,
             e2e_keys.KEPT_PROOFS),
            ("the new key's proof made for another key", dict(body, new_recovery_proof=hybrid_proof(
                client, owner, up.recovery_seed, "revoke", public_key=page.public_of(os.urandom(32)))), 403,
             e2e_keys.PROOF_WRONG),
            ("the new key's proof of another seed", dict(body, new_recovery_proof=_wrong_new_proof(
                client, owner, body["recovery"]["public_key"])), 403, e2e_keys.PROOF_WRONG)):
        r = up.send(bad)
        assert r.status_code == status and r.json()["detail"] == words, (case, r.text)
    assert rows("ade_e2e_pending") == []
    r = up.send(body)                   # each refusal left its proofs and touches unused
    assert r.status_code == 200, r.text


def _wrong_new_proof(client, owner, public_key):
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "revoke", "public_key": public_key})
    c = r.json()
    ss = page.decapsulate(os.urandom(32), unb64u(c["server_ct"]))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"proofpane-e2e/2 recovery proof").derive(ss)
    return {"challenge_id": c["challenge_id"],
            "proof": b64u(hmac.new(k, unb64u(c["nonce"]) + b"revoke", hashlib.sha256).digest())}


def test_the_upgrade_refuses_what_does_not_make_a_post_quantum_account(client, owner, keys, device, second,
                                                                       classical_two):
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)], kept=False)
    body = up.body()
    enrolled = {r["passkey_id"]: r for r in rows("ade_e2e_credentials")}
    same_salt = [dict(c, salt=enrolled[c["passkey_id"]]["salt"]) if c["passkey_id"] == keys.passkey["id"] else c
                 for c in body["credentials"]]
    form1 = [dict(g, sealed=e2e.seal(raw_public(X25519PrivateKey.generate()), "grant", {"mk": "x"}))
             for g in body["grants"]]
    for case, bad, status, words in (
            ("no kem", {k: v for k, v in body.items() if k != "kem"}, 422, "kem x-wing"),
            ("no recovery key", dict(body, recovery=None), 422, "carries recovery"),
            ("a classical recovery key", dict(body, recovery={"public_key": b64u(raw_public(
                X25519PrivateKey.generate())), "anchor": b64u(os.urandom(60))}), 422, "1216 bytes"),
            ("a salt it has", dict(body, credentials=same_salt), 422, e2e_keys.SALT_SAME),
            ("an anchor of the old master key", dict(body, credentials=[dict(c, anchor=keys.mk_id)
                                                                         for c in body["credentials"]]), 422,
             "master key the change names"),
            ("grants in form 1", dict(body, grants=form1), 422, "1197 bytes"),
            ("a content key wrapped in 76 bytes", dict(body, content_keys=[dict(k, wrapped=b64u(os.urandom(76)))
                                                                           for k in body["content_keys"]]), 422,
             "60 bytes"),
            # a passkey that stays, given a classical key or a classical wrap in place of its post-quantum ones
            ("a staying credential with a classical key",
             dict(body, credentials=[dict(body["credentials"][0], public_key=b64u(raw_public(
                 X25519PrivateKey.generate())))] + body["credentials"][1:]), 422, "credential's public key"),
            ("a staying credential wrapped in 145 bytes",
             dict(body, credentials=[dict(body["credentials"][0], wrapped=b64u(os.urandom(145)))] +
                  body["credentials"][1:]), 422, "credential's wrap"),
            ("no credentials", dict(body, credentials=[]), 422, "credentials are a list")):
        r = up.send(bad)
        assert r.status_code == status and words in r.json()["detail"], (case, r.text)
    assert up.send(body).status_code == 200
    assert rows("ade_e2e_state") == [] or rows("ade_e2e_state")[0]["pq"] == 0


def test_an_upgrade_whose_account_became_post_quantum_while_it_waited_takes_no_effect(client, owner, keys, device,
                                                                                      second, classical_two):
    """Checked again as its hold ends (_upgrade_checks): an account made post-quantum while it waited takes no second
    upgrade. No change of the app's makes one meanwhile (each takes its place, ending it, or is refused), so a database
    written under it stands in."""
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)], kept=False)
    hold = up.send(up.body()).json()["hold"]
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_state SET pq = 1 WHERE user_id = ?", (keys.account,))
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    with store.connect() as db:
        assert holds.get(db, hold["id"])["failed"] == e2e_keys.NOT_CLASSICAL
    assert all(c["anchor"] is None for c in view(client, owner)["credentials"])


def test_an_upgrade_is_refused_once_the_account_is_post_quantum(client, owner, pq):
    pq_on(pq)
    body = pq_revocation(pq)[0]
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=dict(body, upgrade=True))
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_CLASSICAL, r.text


def test_an_upgrade_is_checked_again_as_its_hold_ends_and_keeps_nothing_when_stale(client, owner, keys, device, second,
                                                                                  classical_two):
    """When its hold ends: the recovery key the code was proved against is still the account's, and the master key the
    upgrade was made against still current. A database writer's change meanwhile ends it, nothing kept."""
    for change, words in (("UPDATE ade_e2e_recovery SET public_key = ? WHERE user_id = ?", e2e_keys.CODE_CHANGED),
                          ("UPDATE ade_e2e_roster SET mk_id = ? WHERE user_id = ?", e2e_keys.STALE_MK)):
        up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)])
        body = up.body()
        hold = up.send(body).json()["hold"]
        with store.connect() as db:
            before = db.execute("SELECT * FROM ade_e2e_recovery").fetchone()["public_key"]
            before_mk = db.execute("SELECT mk_id FROM ade_e2e_roster").fetchone()["mk_id"]
            value = b64u(raw_public(X25519PrivateKey.generate())) if "recovery" in change else "f" * 32
            db.execute(change, (value, keys.account))
        assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
        with store.connect() as db:
            assert holds.get(db, hold["id"])["failed"] == words, change
            db.execute("UPDATE ade_e2e_recovery SET public_key = ?", (before,))
            db.execute("UPDATE ade_e2e_roster SET mk_id = ?", (before_mk,))
        v = view(client, owner)
        assert v["pq"] is False and v["mk_id"] == keys.mk_id and v["pending"] is None
        assert all(len(unb64u(c["public_key"])) == 32 for c in v["credentials"])


def test_after_the_upgrade_the_account_changes_in_post_quantum_forms_with_its_retired_classical_keys(
        client, owner, keys, device, second, classical_two, monkeypatch):
    up = Upgrade(keys, [(keys.passkey, device), (classical_two, second)])
    body = up.body()
    hold = up.send(body).json()["hold"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    up.taken(body)
    pqk = PQKeys(client, owner, device)
    pqk.statements, pqk.content, pqk.mk_id = keys.statements, keys.content, keys.mk_id
    pqk.recovery_seed = up.recovery_seed
    # a rotation, with the recovery code's hybrid proof
    monkeypatch.setenv(e2e_keys.ROTATE_SETTING, "0")
    rot, signed, seed = rotation(pqk, kem=KEM)
    r = client.post("/api/ade/e2e/rotate", headers=owner, json=dict(rot, recovery_proof=pqk.proof("rotate")))
    assert r.status_code == 200, r.text
    holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"])
    pqk.took(signed, seed)
    # a revocation that wraps every content key again in 60 bytes, the retired classical ones too
    assert any(len(unb64u(k["public_key"])) == 32 for k in view(client, owner)["content_keys"])
    rev, signed, seed = pq_revocation(pqk)
    r = client.post("/api/ade/e2e/revoke", headers=owner, json=rev)
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account, now=r.json()["hold"]["takes_effect_at"]) == [r.json()["hold"]["id"]]
    v = view(client, owner)
    assert v["mk_id"] == rev["mk_id"] and all(len(unb64u(k["wrapped"])) == 60 for k in v["content_keys"])
    pqk.took(signed, seed)
    pqk.mk_id = rev["mk_id"]
    # a classical key stated again is refused
    s, private = pqk.statement(hybrid=False)
    r = client.post("/api/ade/e2e/rotate", headers=owner,
                    json={"statement": s, "content_key": Keys.content_key(pqk, private), "kem": KEM})
    assert r.status_code == 422, r.text
