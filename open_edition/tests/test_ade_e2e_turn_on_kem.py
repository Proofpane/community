"""A turn-on that makes an account's keys anew says kem (e2e_keys._whole_era): a first
turn-on, or a start again with replace, makes post-quantum keys, and one without kem, which only a page older than the
server or a direct call sends, is refused in the words for an older page (400, passkeys.OLD_PAGE), as it is asked and
again as its hold ends. Turning a classical account on again under its keys keeps it classical, as before.

Every other test makes its classical accounts the way older releases made them, with a turn-on without kem, since
e2e_keys.CLASSICAL_WHOLE_TURN_ON is on for each test (tests/conftest.py). These turn it off, as the server ships it,
and on again only to place a turn-on as a server before the refusal took it."""

import ast
import os

from conftest import ROOT
from proofpane_open import passkeys, store
from proofpane_open.ade import e2e, e2e_keys, holds
from test_ade_e2e import at_once, enrol, passkey_of, rekey_body, rows, turned_off, view
from test_ade_e2e import device, keys, notices, owner, second  # noqa: F401  (fixtures)
from test_ade_e2e_pq_server import KEM, PQKeys
from test_ade_e2e_pq_server import pq  # noqa: F401  (fixture)

# what a turn-on keeps once it takes effect: none of it, where it is refused
KEPT = ("ade_e2e_credentials", "ade_e2e_recovery", "ade_e2e_grants", "ade_e2e_roster", "ade_e2e_content_keys",
        "ade_e2e_statements", "ade_e2e_pending")


def refusing(monkeypatch, refuses=True):
    """The server as it ships (refuses: a turn-on with the whole body says kem), or, with `refuses` false, as a server
    before the refusal took one without it. (raising=False: on a server from before the refusal, which has no such
    attribute, these tests run and fail on what it does, rather than on setting it.)"""
    monkeypatch.setattr(e2e_keys, "CLASSICAL_WHOLE_TURN_ON", not refuses, raising=False)


def failed(hold):
    with store.connect() as db:
        return holds.get(db, hold["id"])["failed"]


def test_a_first_turn_on_without_kem_is_refused_in_the_words_for_an_older_page(client, owner, keys, pq, monkeypatch):
    """An older release's page sends a first turn-on without kem: refused (400, passkeys.OLD_PAGE), whatever its
    forms, before they are read, and nothing of it is kept. A kem other than x-wing is refused as before (422). The
    same turn-on in post-quantum keys, with kem, is taken and makes the account post-quantum."""
    refusing(monkeypatch)
    body, _, _ = keys.turn_on_body()
    assert "kem" not in body
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    # post-quantum forms without kem: the same words, where a server taking it reads them as classical forms (422)
    without, _, _ = pq.turn_on_body()
    del without["kem"]
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=without)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=dict(body, kem="kyber"))
    assert r.status_code == 422 and "kem is x-wing" in r.json()["detail"], r.text
    for table in KEPT:
        assert rows(table) == [], table
    v = view(client, owner)
    assert v["on"] is False and v["pq"] is False and v["pending"] is None
    pq.turn_on()
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is True


def test_starting_again_without_kem_is_refused_and_with_kem_makes_the_account_post_quantum(client, owner, keys,
                                                                                         device, monkeypatch):
    """A classical account (made as older releases made one) whose encryption is off starts again (replace) only in
    post-quantum keys: without kem it is refused, its keys from before kept as they were; with kem it waits a day and
    makes the account post-quantum."""
    keys.turn_on()
    turned_off(keys)
    before = view(client, owner)
    refusing(monkeypatch)
    body, _, _ = keys.turn_on_body(replace=True)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    after = view(client, owner)
    assert after["pending"] is None and after["pq"] is False
    assert (after["mk_id"], after["statements"], after["credentials"]) == \
        (before["mk_id"], before["statements"], before["credentials"])
    again = PQKeys(client, owner, device)
    again.statements = list(keys.statements)
    got, _ = again.turn_on(replace=True)
    assert got["hold"]["kind"] == e2e_keys.HOLD_LONG
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is True and v["mk_id"] == again.mk_id != before["mk_id"]


def test_a_first_turn_on_without_kem_placed_before_the_refusal_ends_failed_as_its_hold_ends(client, owner, keys,
                                                                                           monkeypatch):
    """One placed by a server before the refusal, still waiting when a server with it starts, takes no effect: its hold
    ends failed, in the words for an older page, and nothing of it is kept."""
    refusing(monkeypatch, False)
    body, _, _ = keys.turn_on_body()
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert "kem" not in rows("ade_e2e_pending")[0]["body"]
    refusing(monkeypatch)
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) == passkeys.OLD_PAGE
    for table in KEPT:
        assert rows(table) == [], table
    v = view(client, owner)
    assert v["on"] is False and v["pq"] is False


def test_a_start_again_without_kem_placed_before_the_refusal_ends_failed_and_keeps_the_keys_from_before(
        client, owner, keys, monkeypatch):
    """A start again (replace) placed by a server before the refusal, still waiting a day when a server with it starts,
    takes no effect as its hold ends: the keys from before are not forgotten, and the account stays classical."""
    keys.turn_on()
    turned_off(keys)
    before = view(client, owner)
    body, _, _ = keys.turn_on_body(replace=True)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert hold["kind"] == e2e_keys.HOLD_LONG
    refusing(monkeypatch)
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) == passkeys.OLD_PAGE
    after = view(client, owner)
    assert after["on"] is False and after["pq"] is False and after["pending"] is None
    for name in ("mk_id", "statements", "credentials", "recovery", "grants", "content_keys"):
        assert after[name] == before[name], name


def test_turning_a_classical_account_on_again_keeps_it_classical(client, owner, keys, device, second, monkeypatch):
    """The refusal is the whole body's alone: turning a classical account on again, under its master key (the content
    key alone) or with a new one made from it (rekey_due), takes no kem and keeps it classical, as it is asked and as
    its hold ends."""
    from test_passkeys import remove
    keys.turn_on()
    enrol(keys, second, device)
    turned_off(keys)
    refusing(monkeypatch)
    signed, private = keys.statement(retires="all")
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json={"statement": signed,
                                                                  "content_key": keys.content_key(private)})
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    keys.took(signed, private)
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is False
    turned_off(keys)
    assert remove(client, owner, passkey_of(client, owner, second)["id"]).status_code == 200
    assert view(client, owner)["rekey_due"] is True
    old_mk = keys.mk_id
    body, signed, private = rekey_body(keys)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 200, r.text
    hold = r.json()["hold"]
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    assert failed(hold) is None
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is False and v["mk_id"] == body["mk_id"] != old_mk


def test_a_server_without_ml_kem_turns_encryption_on_for_no_account_with_no_keys_and_starts_none_again(
        client, owner, keys, pq, device, monkeypatch):
    """Without ML-KEM a turn-on that makes keys anew is refused either way: without kem as an older page's (400), with
    it for want of ML-KEM (503). A classical account turns on again under its keys as before."""
    keys.turn_on()                      # a classical account from before the refusal, made while the server had ML-KEM
    turned_off(keys)
    mk_id = keys.mk_id
    refusing(monkeypatch)
    monkeypatch.setattr(e2e, "_READY", False)
    assert view(client, owner)["mlkem"] is False
    body, _, _ = keys.turn_on_body(replace=True)                        # (which names a new master key in `keys`)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    again = PQKeys(client, owner, device)
    again.statements = list(keys.statements)
    body, _, _ = again.turn_on_body(replace=True)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    assert rows("ade_e2e_pending") == []
    signed, private = keys.statement(retires="all")
    r = at_once(lambda: client.post("/api/ade/e2e/turn-on", headers=owner,
                                    json={"statement": signed, "content_key": keys.content_key(private, mk_id)}))
    assert r.status_code == 200, r.text
    assert holds.settle(keys.account) == [r.json()["hold"]["id"]]
    v = view(client, owner)
    assert v["on"] is True and v["pq"] is False and v["mk_id"] == mk_id


def test_a_server_without_ml_kem_turns_encryption_on_for_no_account_that_has_no_keys(client, owner, keys, pq,
                                                                                      monkeypatch):
    """An account with no keys yet, on a server without ML-KEM: no first turn-on is taken, with kem or without."""
    refusing(monkeypatch)
    monkeypatch.setattr(e2e, "_READY", False)
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=keys.turn_on_body()[0])
    assert r.status_code == 400 and r.json()["detail"] == passkeys.OLD_PAGE, r.text
    body = pq.turn_on_body()[0]
    assert body["kem"] == KEM
    r = client.post("/api/ade/e2e/turn-on", headers=owner, json=body)
    assert r.status_code == 503 and r.json()["detail"] == e2e.MLKEM_REFUSED, r.text
    for table in KEPT:
        assert rows(table) == [], table


def test_the_tests_stand_in_is_off_as_the_server_ships_and_nothing_in_the_server_sets_it():
    """e2e_keys.CLASSICAL_WHOLE_TURN_ON is False where it is written, its one assignment; and no other file of the
    server names it, so no setting, route or request turns it on. The tests turn it on (tests/conftest.py)."""
    path = os.path.join(ROOT, "proofpane_open", "ade", "e2e_keys.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    assigned = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and any(isinstance(t, ast.Name) and t.id == "CLASSICAL_WHOLE_TURN_ON"
                        for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert len(assigned) == 1 and assigned[0] in tree.body
    assert isinstance(assigned[0].value, ast.Constant) and assigned[0].value.value is False
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Global) and "CLASSICAL_WHOLE_TURN_ON" in n.names]
    named = []
    for folder, _, files in os.walk(os.path.join(ROOT, "proofpane_open")):
        for name in files:
            if name.endswith(".py") and os.path.join(folder, name) != path:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    if "CLASSICAL_WHOLE_TURN_ON" in fh.read():
                        named.append(name)
    assert named == []
    assert e2e_keys.CLASSICAL_WHOLE_TURN_ON is True                     # the tests' own stand-in, on
