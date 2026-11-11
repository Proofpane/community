"""Turning a classical account on again with a new master key, upgraded to post-quantum keys in the same change: the
fold (where turning it on again must make a new master key, rekey_due, the page "offers
to make that key as the upgrade does"). One change and one new master key, where before it took two of
each: the turn-on's rekey shape with upgrade: true, kem and the upgrade's parts (e2e_keys._upgrade_plan), every passkey
that stays given a new key at its own touch (a turn-on removes none), the recovery key made post-quantum from the code
kept or a new one, its proofs made for purpose turn-on, held as the upgrade is held (a day), and the account
post-quantum as its hold ends.

The classical account stands in for one made by older releases, with a turn-on without kem (tests/conftest.py). A
waiting fold says upgrade and never begins, so a server put back to the release before the fold refuses it as its hold
ends: that is tested with that release's own apply path, its three functions as they were there (RELEASE_59)."""

import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import e2e_pq_page_side as page
from proofpane_open import passkeys, store
from proofpane_open.ade import e2e, e2e_keys, holds, link
from test_ade_e2e import cancel, compacted, enrol, passkey_of, rekey_body, rows, signed_by, turned_off, view
from test_ade_e2e import device, keys, notices, owner, second  # noqa: F401  (fixtures)
from test_ade_e2e_envelope import b64u, raw_public, unb64u
from test_ade_e2e_pq_server import KEM, UPGRADE_TOUCH, Upgrade, grant_to, hybrid_proof, pq_on, touch_subject
from test_ade_e2e_pq_server import pq  # noqa: F401  (fixture)
from test_passkeys import Authenticator, add, confirmed, remove


class Fold(Upgrade):
    """A classical account's turn-on again that upgrades it, as the page makes it: the upgrade's parts (Upgrade's) on
    the turn-on route, in its rekey shape with upgrade: true (mk_id, chain, grants, roster, content_keys), no remove
    and no high_security, every passkey that stays touched (`touched`: (its passkey row, its authenticator)), the
    recovery key made post-quantum from the code kept (`kept`, with both proofs made for turning on) or a new one."""

    def __init__(self, keys, touched, kept=False, signer=None):
        super().__init__(keys, touched, kept, signer)

    def body(self, touch=True, **over):
        k = self.keys
        signed = self.statement()
        p = signed["payload"]
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
                "kem": KEM, "upgrade": True, "credentials": credentials, "kept": self.kept,
                "recovery": {"public_key": b64u(rpk), "anchor": b64u(os.urandom(60))},
                "grants": [{"to": row["credential_id"], "sealed": grant_to(page.public_of(self.creds[row["id"]]))}
                           for row, _ in self.touched] + [{"to": "recovery", "sealed": grant_to(rpk)}]}
        if self.kept:
            body["recovery_proof"] = k.proof("turn-on")
            body["new_recovery_proof"] = hybrid_proof(k.client, k.owner, self.recovery_seed, "turn-on", public_key=rpk)
        body.update(over)
        return body

    def send(self, body):
        return self.keys.client.post("/api/ade/e2e/turn-on", headers=self.keys.owner, json=body)


@pytest.fixture
def due(client, owner, keys, device, second):
    """A classical account (made as older releases made one) whose encryption is off, and whose turning on again
    makes a new master key: three passkeys enrolled, the page's own (A), the second (B) and a third (C), then encryption
    turned off and C removed from the account, at once, as removing a passkey is while it is off. (B's passkey row)"""
    third = Authenticator(alg=-257)
    assert add(client, owner, third, name="Third", confirm_with=device).status_code == 200
    keys.turn_on()
    enrol(keys, second, device)
    enrol(keys, third, device)
    turned_off(keys)
    assert remove(client, owner, passkey_of(client, owner, third)["id"]).status_code == 200
    v = view(client, owner)
    assert v["rekey_due"] is True and v["pq"] is False and len(v["credentials"]) == 2
    return passkey_of(client, owner, second)


def failed(hold):
    with store.connect() as db:
        return holds.get(db, hold["id"])["failed"]


def as_settled(db, hold):
    """A hold's row as holds.settle hands it to its kind's apply: whole, with what runs after it."""
    return dict(db.execute("SELECT * FROM ade_holds WHERE id = ?", (hold["id"],)).fetchone(), after=[])


def classical_keys(v):
    return all(len(unb64u(c["public_key"])) == 32 for c in v["credentials"]) and \
        len(unb64u(v["recovery"]["public_key"])) == 32


def test_turning_a_classical_account_on_again_with_a_new_master_key_upgrades_it_in_the_same_change(
        client, owner, keys, device, second, due, monkeypatch):
    """The fold with a new recovery code: one change, one new master key, held a day as the upgrade is (a turn-on's own
    hold is 180 seconds); nothing in force while it waits; then the master key, every passkey's key, the recovery key
    and the content key post-quantum, and the account post-quantum, compacted now and a day later."""
    begun = []
    # its compaction begun, not run: run in a thread of its own, it would clear the flag this test reads
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: begun.append(1))
    old_mk = keys.mk_id
    before = view(client, owner)
    fold = Fold(keys, [(keys.passkey, device), (due, second)])
    body = fold.body()
    r = fold.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG and hold["takes_effect_at"] - hold["approved_at"] == 86400
    assert hold["summary"] == e2e_keys.FOLD_SUMMARY
    v = view(client, owner)
    p = v["pending"]
    assert (p["change"], p["body"]["shape"], p["body"]["upgrade"], p["body"]["kem"]) == ("turn-on", "rekey", True, True)
    assert "begins" not in p["body"] and "confirmation" not in json.dumps(p["body"]["credentials"])
    assert p["body"]["remove"] == [] and p["body"]["recovery"] == body["recovery"]
    # a new code: it replaces the code, and any of the account's passkeys and the code in force cancel it
    assert (p["by_code"], p["replaces_code"], p["removes"], p["cancel_by_code"]) == (False, True, [], True)
    assert sorted(p["cancel_by"]) == sorted([keys.passkey["id"], due["id"]])
    # nothing in force while it waits
    assert (v["pq"], v["on"], v["mk_id"], v["rekey_due"]) == (False, False, old_mk, True)
    assert (v["credentials"], v["recovery"], v["grants"]) == (before["credentials"], before["recovery"], before["grants"])
    # its time comes
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    fold.taken(body)
    v = view(client, owner)
    assert (v["pq"], v["on"], v["mk_id"], v["rekey_due"]) == (True, True, fold.mk_id, False)
    creds = {c["passkey_id"]: c for c in v["credentials"]}
    assert set(creds) == {keys.passkey["id"], due["id"]}
    for c in body["credentials"]:
        mine = creds[c["passkey_id"]]
        assert (mine["salt"], mine["public_key"], mine["wrapped"], mine["anchor"]) == (
            c["salt"], c["public_key"], c["wrapped"], fold.mk_id)
    assert (v["recovery"]["public_key"], v["recovery"]["anchor"]) == (body["recovery"]["public_key"],
                                                                      body["recovery"]["anchor"])
    assert {g["mk_id"] for g in v["grants"]} == {fold.mk_id} and len(v["grants"]) == 3
    assert all(len(unb64u(g["sealed"][5:])) == 1197 for g in v["grants"])
    assert {k["mk_id"] for k in v["content_keys"]} == {fold.mk_id} and len(v["content_keys"]) == 2
    assert all(len(unb64u(k["wrapped"])) == 60 for k in v["content_keys"])
    assert json.loads(v["statements"][-1]["payload"])["v"] == 3
    assert [(c["mk_id"], c["previous"]) for c in v["chain"]] == [(fold.mk_id, old_mk)]
    assert v["sealed"]["at"] == hold["takes_effect_at"]                 # sealed in place as turning it on does
    (state,) = rows("ade_e2e_state")
    assert begun and state["pq"] == 1 and state["compaction_due"] is not None
    assert state["compact_again_at"] == hold["takes_effect_at"] + e2e_keys.COMPACT_AGAIN
    compacted()
    # and its changes are post-quantum after it: a classical key stated again is refused
    signed, private = keys.statement()
    r = client.post("/api/ade/e2e/rotate", headers=owner, json={"statement": signed,
                                                                 "content_key": keys.content_key(private)})
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text


def test_a_fold_that_keeps_the_code_proves_it_twice_for_turning_on_and_only_the_code_cancels_it(
        client, owner, keys, device, second, due):
    """Keeping the code takes both of the upgrade's proofs, each made for purpose turn-on: one made for a revocation is
    not a turn-on's (each change takes proofs made for its own purpose). It is then held the code's day and only the
    code cancels it; it replaces no code."""
    fold = Fold(keys, [(keys.passkey, device), (due, second)], kept=True)
    body = fold.body()
    rpk = unb64u(body["recovery"]["public_key"])
    for case, bad, words in (
            ("the code's proof made for a revocation", dict(body, recovery_proof=keys.proof("revoke")),
             e2e_keys.PROOF_STALE),
            ("the new key's proof made for a revocation", dict(body, new_recovery_proof=hybrid_proof(
                client, owner, fold.recovery_seed, "revoke", public_key=rpk)), e2e_keys.PROOF_STALE),
            ("no proof of the new key", {k: v for k, v in body.items() if k != "new_recovery_proof"},
             e2e_keys.KEPT_PROOFS),
            ("no proof of the code in force", {k: v for k, v in body.items() if k != "recovery_proof"},
             e2e_keys.KEPT_PROOFS)):
        r = fold.send(bad)
        assert r.status_code == 403 and r.json()["detail"] == words, (case, r.text)
    assert rows("ade_e2e_pending") == []
    r = fold.send(body)                 # each refusal left its proofs and touches unused
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY and hold["takes_effect_at"] - hold["approved_at"] == 86400
    v = view(client, owner)
    p = v["pending"]
    assert (p["by_code"], p["cancel_by"], p["cancel_by_code"], p["replaces_code"]) == (True, [], True, False)
    assert p["body"]["kept_against"] == v["recovery"]["public_key"]
    for passkey in (device, second):
        r = cancel(keys, hold["id"], passkey)
        assert r.status_code == 403 and r.json()["detail"] == e2e_keys.CANCEL_REMOVED, r.text
    assert cancel(keys, hold["id"], recovery_proof=keys.proof("cancel")).status_code == 200
    assert view(client, owner)["pending"] is None and classical_keys(view(client, owner))


def test_a_fold_that_keeps_the_code_takes_effect_with_the_code_s_key_made_post_quantum(
        client, owner, keys, device, second, due, monkeypatch):
    """A kept fold that is let take effect (its siblings above are cancelled, replaced, refused or failed, so none
    reaches _apply_upgrade with kept true): the recovery key the kept code was proved against is written post-quantum
    (_apply_turn_on calls _recovery_key for a fold), not left as the classical row. Without this the account would be
    post-quantum with a classical recovery key, and the kept code could never prove against it again (a
    version that writes the key only when the code is new fails it)."""
    monkeypatch.setattr(e2e_keys, "compact_soon", lambda: None)
    fold = Fold(keys, [(keys.passkey, device), (due, second)], kept=True)
    body = fold.body()
    r = fold.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_RECOVERY
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    v = view(client, owner)
    assert (v["pq"], v["on"], v["mk_id"], v["rekey_due"]) == (True, True, fold.mk_id, False)
    # the recovery key is the new post-quantum one the body carried, not the old classical row: a version that
    # writes it only for a new code leaves the classical public_key and anchor here, so this fails under it
    assert (v["recovery"]["public_key"], v["recovery"]["anchor"]) == (body["recovery"]["public_key"],
                                                                      body["recovery"]["anchor"])
    assert json.loads(v["statements"][-1]["payload"])["v"] == 3


def test_the_fold_gives_every_passkey_that_stays_a_new_key_and_removes_none(client, owner, keys, device, second,
                                                                            due):
    """A turn-on removes no passkey: one that stays and is not touched refuses the fold, a remove in its body changes
    nothing, and one not at hand is removed from the account first, which takes effect at once while encryption is
    off; the fold then keeps the rest."""
    fold = Fold(keys, [(keys.passkey, device)])
    body = fold.body()
    granted = dict(body, grants=body["grants"] + [{"to": due["credential_id"],
                                                   "sealed": grant_to(page.public_of(os.urandom(32)))}])
    r = fold.send(granted)                              # a grant to B, and no new key for it
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.FOLD_EVERY, r.text
    r = fold.send(dict(granted, remove=[due["id"]]))
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.FOLD_EVERY, r.text
    r = fold.send(body)                                 # no grant to B either: the revocation's own rule
    assert r.status_code == 422 and "grants the new master key to every credential" in r.json()["detail"], r.text
    assert rows("ade_e2e_pending") == []
    assert remove(client, owner, due["id"]).status_code == 200
    fold = Fold(keys, [(keys.passkey, device)])
    body = fold.body()
    r = fold.send(body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert view(client, owner)["pending"]["removes"] == []
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    v = view(client, owner)
    assert v["pq"] is True and [c["passkey_id"] for c in v["credentials"]] == [keys.passkey["id"]]


def test_each_passkey_the_fold_keeps_is_touched_for_that_very_change(client, owner, keys, device, second, due):
    """Each passkey that stays, but the statement's signer, comes with a confirmation its own passkey made at its touch,
    for the upgrade's ceremony and for what existed before it: the new master key, the statement, that credential, its
    fresh salt and its new key. One missing, made by another passkey, for another ceremony, or for other values, is
    refused in the fold's words, and left unused."""
    fold = Fold(keys, [(keys.passkey, device), (due, second)])
    body = fold.body()
    entry = next(c for c in body["credentials"] if c["passkey_id"] == due["id"])
    good = entry["confirmation"]
    p = body["statement"]["payload"]

    def with_entry(**changes):
        return dict(body, credentials=[dict(c, **changes) if c is entry else c for c in body["credentials"]])

    def touch(dev, purpose=UPGRADE_TOUCH, **values):
        v = dict(salt=entry["salt"], public_key=entry["public_key"], mk_id=fold.mk_id, payload=p)
        v.update(values)
        return confirmed(client, owner, dev, purpose=purpose, change=touch_subject(
            v["mk_id"], v["payload"], due["credential_id"], v["salt"], v["public_key"]))["confirmation"]

    for case, bad, words in (
            ("none", with_entry(confirmation=None), e2e_keys.FOLD_OWN),
            ("made by another passkey", with_entry(confirmation=touch(device)), e2e_keys.FOLD_OWN),
            ("for another ceremony", with_entry(confirmation=confirmed(client, owner, second,
                                                                       purpose="e2e-anchor-touch")["confirmation"]),
             passkeys.CONFIRM_OTHER),
            ("for another salt", with_entry(confirmation=touch(second, salt=b64u(os.urandom(32)))),
             passkeys.CONFIRM_OTHER),
            ("for another statement", with_entry(confirmation=touch(second, payload=dict(p, seq=p["seq"] + 1))),
             passkeys.CONFIRM_OTHER)):
        r = fold.send(bad)
        assert r.status_code == 403 and r.json()["detail"] == words, (case, r.text)
    assert entry["confirmation"] == good
    assert fold.send(body).status_code == 200


def test_the_fold_refuses_what_does_not_make_a_post_quantum_account_and_keeps_nothing(client, owner, keys, device,
                                                                                     second, due):
    fold = Fold(keys, [(keys.passkey, device), (due, second)])
    body = fold.body()
    enrolled = {r["passkey_id"]: r for r in rows("ade_e2e_credentials")}
    same_salt = [dict(c, salt=enrolled[c["passkey_id"]]["salt"]) if c["passkey_id"] == keys.passkey["id"] else c
                 for c in body["credentials"]]
    form1 = [dict(g, sealed=e2e.seal(raw_public(X25519PrivateKey.generate()), "grant", {"mk": "x"}))
             for g in body["grants"]]
    for case, bad, status, words in (
            ("no kem", {k: v for k, v in body.items() if k != "kem"}, 422, e2e_keys.UPGRADE_KEM),
            ("upgrade not true or false", dict(body, upgrade="yes"), 422, "upgrade is true or false"),
            ("no recovery key", dict(body, recovery=None), 422, "carries recovery"),
            ("a classical recovery key", dict(body, recovery={"public_key": b64u(raw_public(
                X25519PrivateKey.generate())), "anchor": b64u(os.urandom(60))}), 422, "1216 bytes"),
            ("a credential of its own besides", dict(body, credential={"passkey_id": keys.passkey["id"]}), 422,
             "names no credential"),
            ("starting again", dict(body, replace=True), 422, "does not start again"),
            ("a salt it has", dict(body, credentials=same_salt), 422, e2e_keys.SALT_SAME),
            ("an anchor of the old master key", dict(body, credentials=[dict(c, anchor=keys.mk_id)
                                                                         for c in body["credentials"]]), 422,
             "master key the change names"),
            ("grants in form 1", dict(body, grants=form1), 422, "1197 bytes"),
            ("a content key wrapped in 76 bytes", dict(body, content_keys=[dict(k, wrapped=b64u(os.urandom(76)))
                                                                           for k in body["content_keys"]]), 422,
             "60 bytes"),
            ("no credentials", dict(body, credentials=[]), 422, "credentials are a list"),
            ("a new key that is classical", dict(body, credentials=[dict(body["credentials"][0], public_key=b64u(
                raw_public(X25519PrivateKey.generate())))] + body["credentials"][1:]), 422,
             "credential's public key")):
        r = fold.send(bad)
        assert r.status_code == status and words in r.json()["detail"], (case, r.text)
    # a new master key in post-quantum forms without upgrade is no fold: a classical account changes in its own forms
    plain, _, _ = rekey_body(keys, kem=KEM)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=plain)
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.CLASSICAL_FORMS, r.text
    # signed by a passkey that does not unlock encryption, every passkey that stays touched: a turn-on's own rule
    other = Authenticator(alg=-257)                             # C's algorithm: C was removed from the account
    assert add(client, owner, other, name="Other", confirm_with=device).status_code == 200
    by_other = Fold(keys, [(keys.passkey, device), (due, second)], signer=other)
    r = by_other.send(by_other.body())
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_ENROLLED, r.text
    # turning it on again with a new master key and no upgrade names no recovery key of its own, as before
    plain, _, _ = rekey_body(keys, recovery={"public_key": b64u(raw_public(X25519PrivateKey.generate())),
                                             "anchor": b64u(os.urandom(60))})
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=plain)
    assert r.status_code == 422 and "names no credential or recovery key" in r.json()["detail"], r.text
    # an upgrade as encryption is turned on again makes a new master key: the content key alone is no fold
    signed = fold.statement()
    r = fold.send({"statement": signed, "content_key": body["content_key"] | {"mk_id": keys.mk_id}, "kem": KEM,
                   "upgrade": True})
    assert r.status_code == 422 and r.json()["detail"] == e2e_keys.FOLD_REKEY, r.text
    assert rows("ade_e2e_pending") == [] and rows("ade_e2e_state")[0]["pq"] == 0
    assert fold.send(body).status_code == 200


def test_a_post_quantum_account_has_nothing_to_fold(client, owner, pq, device):
    """A post-quantum account turned off and on again with a new master key stays post-quantum, in its own forms: a
    fold is refused (409), as an upgrade is, and no proof is made for a new recovery key of its."""
    pq_on(pq)
    signed, _ = pq.statement("e2e-off")
    r = client.post("/api/ade/e2e/off", headers=owner, json={"statement": signed, "kem": KEM})
    assert holds.settle(pq.account, now=r.json()["hold"]["takes_effect_at"]) == [r.json()["hold"]["id"]]
    pq.took(signed)
    body = Fold(pq, [(pq.passkey, device)]).body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_CLASSICAL, r.text
    stale = dict(body, chain=dict(body["chain"], previous="d" * 32))     # said as such before its keys are read
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=stale)
    assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NOT_CLASSICAL, r.text
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner,
                    json={"purpose": "turn-on", "public_key": b64u(page.public_of(os.urandom(32)))})
    assert r.status_code == 422 and "on a classical account" in r.json()["detail"], r.text


def test_the_new_recovery_key_s_proof_is_made_for_turning_on_on_a_classical_account(client, owner, keys):
    """The fold's proofs are made for purpose turn-on: the hybrid one for the body's new recovery key, and the classical
    one for the code in force. A proof for a new recovery key is made for no other purpose but the upgrade's."""
    keys.turn_on()
    public_key = page.public_of(os.urandom(32))
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner,
                    json={"purpose": "turn-on", "public_key": b64u(public_key)})
    assert r.status_code == 200 and r.json()["kem"] == KEM and r.json()["purpose"] == "turn-on", r.text
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json={"purpose": "turn-on"})
    assert r.status_code == 200 and "server_key" in r.json(), r.text                   # classical, the code in force
    for asked in ({"purpose": "rotate", "public_key": b64u(public_key)},
                  {"purpose": "cancel", "public_key": b64u(public_key)}):
        r = client.post("/api/ade/e2e/recovery-proof", headers=owner, json=asked)
        assert r.status_code == 422 and "purpose revoke (or turn-on" in r.json()["detail"], (asked, r.text)


def test_a_fold_is_checked_again_as_its_hold_ends_and_keeps_nothing_when_its_keys_changed(client, owner, keys,
                                                                                         device, second, due,
                                                                                         monkeypatch):
    """As its hold ends: the recovery key the kept code was proved against still the account's, the master key it was
    made from still current, the account still classical, and this server still with ML-KEM. A change under it (here
    a database writer's) ends it, nothing kept; so does a passkey it keeps removed from the account meanwhile."""
    def placed(kept=True):
        hold = Fold(keys, [(keys.passkey, device), (due, second)], kept=kept).send(
            Fold(keys, [(keys.passkey, device), (due, second)], kept=kept).body())
        assert hold.status_code == 200, hold.text
        return hold.json()["hold"]

    for change, value, words in (
            ("UPDATE ade_e2e_recovery SET public_key = ? WHERE user_id = ?",
             b64u(raw_public(X25519PrivateKey.generate())), e2e_keys.CODE_CHANGED),
            ("UPDATE ade_e2e_roster SET mk_id = ? WHERE user_id = ?", "f" * 32, e2e_keys.STALE_MK),
            ("UPDATE ade_e2e_state SET pq = ? WHERE user_id = ?", 1, e2e_keys.NOT_CLASSICAL)):
        hold = placed()
        with store.connect() as db:
            recovery = db.execute("SELECT public_key FROM ade_e2e_recovery").fetchone()["public_key"]
            mk = db.execute("SELECT mk_id FROM ade_e2e_roster").fetchone()["mk_id"]
            db.execute(change, (value, keys.account))
        assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
        assert failed(hold) == words, change
        with store.connect() as db:
            db.execute("UPDATE ade_e2e_recovery SET public_key = ?", (recovery,))
            db.execute("UPDATE ade_e2e_roster SET mk_id = ?", (mk,))
            db.execute("UPDATE ade_e2e_state SET pq = 0")
        v = view(client, owner)
        assert (v["pq"], v["on"], v["mk_id"], v["pending"]) == (False, False, keys.mk_id, None) and classical_keys(v)
    hold = placed(kept=False)
    monkeypatch.setattr(e2e, "_READY", False)
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) == e2e.MLKEM_REFUSED
    monkeypatch.setattr(e2e, "_READY", True)
    hold = placed(kept=False)
    assert remove(client, owner, due["id"]).status_code == 200           # a passkey it keeps, removed meanwhile
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert "grants the new master key to every credential" in failed(hold)
    v = view(client, owner)
    assert (v["pq"], v["on"], v["mk_id"]) == (False, False, keys.mk_id) and classical_keys(v)


def test_a_fold_waiting_when_the_server_is_put_back_to_the_release_before_ends_failed_there_and_keeps_nothing(
        client, owner, keys, device, second, due):
    """The release before the fold has none. Its apply path (RELEASE_59: _apply_turn_on, _turn_on_checks and
    _era_still as they are there; every other function they call is as it is here) takes a turn-on's rekey shape as a
    revocation's new master key, and lets a turn-on change the account's era only with begins. A waiting fold says
    upgrade and never begins, so that release refuses it as its hold ends (409), keeping nothing. Had it said begins, as
    the upgrade's body does, that release would take it: the new master key's grants written to keys it does not
    store, the passkeys' records and the recovery key left classical, so neither a passkey nor the code would open that
    master key again."""
    assert hashlib.sha256(RELEASE_59.encode("utf-8")).hexdigest() == RELEASE_59_SHA256
    old = dict(vars(e2e_keys))
    exec(RELEASE_59, old)                                               # noqa: S102  (that release's own text)
    fold = Fold(keys, [(keys.passkey, device), (due, second)])
    body = fold.body()
    r = fold.send(body)
    assert r.status_code == 200, r.text
    placed = r.json()["hold"]
    before = view(client, owner)
    stored = json.loads(rows("ade_e2e_pending")[0]["body"])
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(link.LinkError) as refused:
            old["_apply_turn_on"](db, as_settled(db, placed), stored, placed["takes_effect_at"])
        db.rollback()
    assert (refused.value.status, str(refused.value)) == (409, e2e_keys.ERA_CHANGED)
    assert view(client, owner) == before
    # the control: the same body saying begins is taken there, and would leave the account no key that opens it
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old["_apply_turn_on"](db, as_settled(db, placed), dict(stored, begins=True), placed["takes_effect_at"])
        grants = db.execute("SELECT sealed FROM ade_e2e_grants WHERE user_id = ?", (keys.account,)).fetchall()
        creds = db.execute("SELECT public_key FROM ade_e2e_credentials WHERE user_id = ?", (keys.account,)).fetchall()
        recovery = db.execute("SELECT public_key FROM ade_e2e_recovery WHERE user_id = ?", (keys.account,)).fetchone()
        state = db.execute("SELECT pq FROM ade_e2e_state WHERE user_id = ?", (keys.account,)).fetchone()
        assert {len(unb64u(g["sealed"][5:])) for g in grants} == {1197}                # form-2 grants
        assert {len(unb64u(c["public_key"])) for c in creds} == {32}                    # to keys no record holds
        assert len(unb64u(recovery["public_key"])) == 32 and state["pq"] == 0
        db.rollback()
    # on this server the same waiting fold takes effect whole
    assert holds.settle(keys.account, now=placed["takes_effect_at"]) == [placed["id"]]
    assert failed(placed) is None and view(client, owner)["pq"] is True


def test_a_first_turn_on_waiting_replaces_no_code(client, owner, keys):
    """replaces_code says the fold's new code, and a start again: a first turn-on, whose body holds the account's first
    recovery key, replaces none."""
    body, _, _ = keys.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    assert view(client, owner)["pending"]["replaces_code"] is False


def test_an_upgrade_placed_here_still_says_begins_as_the_release_before_applies_it(client, owner, keys, device):
    """The upgrade's waiting body says begins as before, beside upgrade: a server put back to the release before the
    fold applies a waiting upgrade as its hold ends (its _era_still, RELEASE_59's, takes begins alone)."""
    old = dict(vars(e2e_keys))
    exec(RELEASE_59, old)                                               # noqa: S102  (that release's own text)
    keys.turn_on()
    up = Upgrade(keys, [(keys.passkey, device)], kept=False)
    r = up.send(up.body())
    assert r.status_code == 200, r.text
    stored = json.loads(rows("ade_e2e_pending")[0]["body"])
    assert (stored["upgrade"], stored["begins"]) == (True, True)
    with store.connect() as db:
        old["_era_still"](db, keys.account, stored)
        with pytest.raises(link.LinkError) as refused:
            old["_era_still"](db, keys.account, dict(stored, begins=None))
    assert (refused.value.status, str(refused.value)) == (409, e2e_keys.ERA_CHANGED)


def test_a_fold_is_a_change_like_any_other_for_rule_v(client, owner, keys, device, second, due):
    """(v) as for every change: a new code's fold, cancelled by any of the account's passkeys, gives its place to a
    turn-on again a passkey of them signs (here the classical one, still taken where the server has ML-KEM: a page
    from before the fold sends it); a fold that keeps the code, which the code alone cancels, gives its place to
    nothing but a change with the code's proof, which another such fold has."""
    b = due["id"]
    fold = Fold(keys, [(keys.passkey, device), (due, second)], signer=device)
    first = fold.send(fold.body()).json()["hold"]
    body, signed, private = rekey_body(keys)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=signed_by(keys, body, private, second))
    assert r.status_code == 200, r.text
    classical = r.json()["hold"]
    assert failed(first) == e2e_keys.REPLACED
    pending = view(client, owner)["pending"]
    assert (pending["id"], pending["made_by"], "upgrade" in pending["body"]) == (classical["id"], b, False)
    assert (pending["replaces_code"], pending["by_code"]) == (False, False)
    # a fold keeping the code takes that one's place by the code's proof, and only the code's proof takes its place
    kept = Fold(keys, [(keys.passkey, device), (due, second)], kept=True)
    r = kept.send(kept.body())
    assert r.status_code == 200, r.text
    coded = r.json()["hold"]
    assert failed(classical) == e2e_keys.REPLACED
    for signer in (device, second):
        body, signed, private = rekey_body(keys)
        r = client.post("/api/ade/e2e/turn-on", headers=owner, json=signed_by(keys, body, private, signer))
        assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
        bare = Fold(keys, [(keys.passkey, device), (due, second)], signer=signer)
        r = bare.send(bare.body())
        assert r.status_code == 409 and r.json()["detail"] == e2e_keys.NO_PLACE, r.text
    again = Fold(keys, [(keys.passkey, device), (due, second)], kept=True, signer=second)
    r = again.send(again.body())
    assert r.status_code == 200, r.text
    assert failed(coded) == e2e_keys.REPLACED
    assert [(p["made_by"], p["by_code"]) for p in rows("ade_e2e_pending")] == [(b, 1)]


def test_two_tabs_folding_at_once_leave_one_fold_and_the_other_s_parts_are_spent_on_no_other_change(
        client, owner, keys, device, second, due):
    """Two tabs prepare a fold from the same keys: the second placed takes the first's place (its signer may cancel
    it), and only it takes effect. The first's touches are bound to its own statement, so none is spent on the
    second, whose own are needed."""
    one = Fold(keys, [(keys.passkey, device), (due, second)])
    two = Fold(keys, [(keys.passkey, device), (due, second)])
    first_body, second_body = one.body(), two.body()
    first = one.send(first_body).json()["hold"]
    stolen = next(c for c in first_body["credentials"] if c["passkey_id"] == due["id"])["confirmation"]
    mixed = dict(second_body, credentials=[dict(c, confirmation=stolen) if c["passkey_id"] == due["id"] else c
                                           for c in second_body["credentials"]])
    r = two.send(mixed)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER, r.text
    r = two.send(second_body)
    assert r.status_code == 200, r.text
    second_hold = r.json()["hold"]
    assert failed(first) == e2e_keys.REPLACED
    assert holds.settle(keys.account, now=second_hold["takes_effect_at"]) == [second_hold["id"]]
    assert failed(second_hold) is None
    v = view(client, owner)
    assert v["pq"] is True and v["mk_id"] == two.mk_id
    assert {c["public_key"] for c in v["credentials"]} == {c["public_key"] for c in second_body["credentials"]}


def test_a_stolen_passkey_s_fold_waits_a_day_and_the_recovery_code_cancels_it(client, owner, keys, device, second,
                                                                              due):
    """One stolen passkey (B) cannot fold the owner's passkey (A) into new keys without A's own touch, nor claim to keep
    the code without the code's proof. With A removed from the account first (as removing a passkey is, at once, while
    encryption is off) it folds alone with a new code: that waits a day, not a turn-on's 180 seconds, and the recovery
    code in force cancels it."""
    thiefs = Fold(keys, [(keys.passkey, device), (due, second)], signer=second)
    body = thiefs.body(touch=False)
    r = thiefs.send(body)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.FOLD_OWN, r.text
    claimed = Fold(keys, [(keys.passkey, device), (due, second)], kept=True, signer=second)
    body = {k: v for k, v in claimed.body().items() if k not in ("recovery_proof", "new_recovery_proof")}
    r = claimed.send(body)
    assert r.status_code == 403 and r.json()["detail"] == e2e_keys.KEPT_PROOFS, r.text
    assert remove(client, owner, keys.passkey["id"]).status_code == 200
    alone = Fold(keys, [(due, second)], signer=second)
    r = alone.send(alone.body())
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG and hold["takes_effect_at"] - hold["approved_at"] == 86400
    pending = view(client, owner)["pending"]
    assert (pending["cancel_by"], pending["cancel_by_code"], pending["replaces_code"]) == ([due["id"]], True, True)
    r = cancel(keys, hold["id"], recovery_proof=keys.proof("cancel"))
    assert r.status_code == 200, r.text
    v = view(client, owner)
    assert v["pending"] is None and v["pq"] is False and classical_keys(v)


def test_a_server_without_ml_kem_refuses_the_fold_and_still_turns_a_classical_account_on_again(
        client, owner, keys, device, second, due, monkeypatch):
    """Fail-closed: without ML-KEM the fold is refused (503), and so is the new recovery key's proof, while
    the classical turn-on again with a new master key, which keeps the account classical, is taken as before."""
    monkeypatch.setattr(e2e, "_READY", False)
    assert view(client, owner)["mlkem"] is False
    fold = Fold(keys, [(keys.passkey, device), (due, second)])
    r = fold.send(fold.body())
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    r = client.post("/api/ade/e2e/recovery-proof", headers=owner,
                    json={"purpose": "turn-on", "public_key": b64u(page.public_of(os.urandom(32)))})
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    body, signed, private = rekey_body(keys)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is False and v["mk_id"] == body["mk_id"] and classical_keys(v)


# _era_still, _turn_on_checks and _apply_turn_on as the release before the fold had them in
# open_edition/proofpane_open/ade/e2e_keys.py, each function's text whole, in that order, joined by two blank lines and
# ended by a newline: what a server put back to that release runs as a turn-on's hold ends
RELEASE_59_SHA256 = "86d822d0b47cc690b18328288d243651e5ff0947adf5f1991f9314f96b669127"
RELEASE_59 = '''def _era_still(db, user_id, plan):
    """As a change's hold ends: the era it was made in is still the account's (its plan's kem: a body placed before this
    release has none, a classical one), or the change is one that makes it post-quantum (a turn-on with the whole body
    or the upgrade, which asks for a classical account itself); 409 otherwise, and 503 for a post-quantum change where
    this server has no ML-KEM now: one placed before it lost ML-KEM, which the recovery code could not cancel since
    (_era), fails as its hold ends."""
    kem = bool(plan.get("kem"))
    if kem and not e2e.ready():
        raise link.LinkError(503, e2e.MLKEM_REFUSED)
    if kem != is_pq(db, user_id) and not (kem and plan.get("begins")):
        raise link.LinkError(409, ERA_CHANGED)


def _turn_on_checks(db, user_id, plan, payload, signer_id):
    """The checks of a turn-on, made as it is asked and again as its hold ends: encryption is off; its statement is
    the account's next and names a new key, retiring every earlier key at once after encryption was on before or when
    it starts again; and by its shape: the whole body only for an account with no keys, or with replace, its credential
    the signer's own and one it may enrol; the content key alone only under the current master key, signed by a passkey
    that unlocks the keys, and only while no new master key is due (rekey_due); a new master key only as a revocation
    may make one, signed by a passkey whose credential stays."""
    if on(db, user_id):
        raise link.LinkError(409, ON_ALREADY)
    _check_next(db, user_id, payload)
    _check_key_new(db, user_id, payload)
    if (newest(db, user_id) is not None or plan.get("replace")) and payload["retires"] != "all":
        raise link.LinkError(422, RETIRES_ALL)
    mk = current(db, user_id)
    if plan["shape"] == "whole":
        if mk is not None and not plan["replace"]:
            raise link.LinkError(409, FROM_BEFORE)
        row = _passkey(db, user_id, plan["credential"]["passkey_id"])
        _may_enrol(db, user_id, row)
        if row["id"] != signer_id:
            raise link.LinkError(422, "The credential enrolled as encryption is turned on is the passkey that signed "
                                      "its statement.")
        return
    if mk is None:
        raise link.LinkError(409, NO_KEYS)
    if plan["shape"] == "again":
        if rekey_due(db, user_id):
            raise link.LinkError(409, REKEY_DUE)
        if "content_key" in plan and plan["content_key"]["mk_id"] != mk:      # a link's statement sent in has none
            raise link.LinkError(409, STALE_MK)
        if _enrolled(db, user_id, signer_id) is None:
            raise link.LinkError(409, NOT_ENROLLED)
        return
    stay = _revocation(db, user_id, dict(plan, remove=[], high_security=False))
    if signer_id not in stay.values():
        raise link.LinkError(409, NOT_ENROLLED)


def _apply_turn_on(db, hold, body, now):
    """A turn-on's hold has ended: checked again (_turn_on_checks), its keys stored as its shape says, its statement
    kept, and, with seal, what links sent in the clear sealed in place (_seal_after: the database due for compaction,
    when something was, which a thread of its own begins once this has committed, the request that settled it not
    waiting); then, whether or not it seals, the words of every connector proposal already decided cleared
    (_forget_decided)."""
    user_id = hold["user_id"]
    stored = body["statement"]
    payload = json.loads(stored["payload"])
    _turn_on_checks(db, user_id, body, payload, stored["passkey_id"])
    _era_still(db, user_id, body)
    if body["shape"] == "whole":
        if body["replace"]:
            _forget(db, user_id)
        _enrol(db, user_id, body["credential"], now)
        _recovery_key(db, user_id, body["recovery"], now)
        for to, grant in body["grants"].items():
            _grant(db, user_id, body["mk_id"], to, grant, now)
        _roster(db, user_id, body["mk_id"], body["roster"], now)
        _set_state(db, user_id, rekey_due=0)
        if body.get("begins"):
            _set_state(db, user_id, pq=1)       # never back: a post-quantum account's every write carries kem (_era)
    elif body["shape"] == "rekey":
        _new_master_key(db, user_id, body, now)
    _new_content_key(db, user_id, body["content_key"], now)
    _store_statement(db, user_id, stored, now)
    if body["seal"]:
        _seal_after(db, hold, user_id, payload["key"], now)
    _forget_decided(db, hold, user_id, now)
'''
