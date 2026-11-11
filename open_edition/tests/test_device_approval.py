"""Approving another device's sign-in takes the person's own word as well as the sign-in (auth.decide_device_signin):
a confirmation (passkeys.confirm) made for that device's request, by the password or a passkey, or by a hardware key
while the account's high security is on, used up in the transaction that approves. A sign-in alone, which may have
been taken from a browser, signs no device in: the device would get a sign-in of its own for 7 days, outlasting it.
A confirmation made for one device approves no other and is spent on no other change, and none made for anything else
approves a device. Signing out everywhere refuses a device approved that has not collected its sign-in yet. And the
page's own approval, run through vitest against this server, with the password and with a passkey."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time

import pytest
from cryptography.hazmat.primitives import serialization

from conftest import ADMIN, signed_up
from fido_fixtures import add_hardware
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import auth, config, desktop, passkeys, store
from test_passkeys import Authenticator, add, as_page_asks, confirmed, sign_out_everywhere

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "ui")
NODE = shutil.which("node")
VITEST = os.path.join(UI, "node_modules", ".bin", "vitest")
PASSWORD = "correct horse battery"                  # conftest's signed_up
OTHER = "other@example.com"


@pytest.fixture
def owner(client):
    return signed_up(client)


def start(client, name="Kitchen iPad"):
    """A device asks to be signed in: {id, code, poll, ...}."""
    r = client.post("/api/auth/device/start", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def confirm_for(client, headers, code, device=None, password=PASSWORD):
    """The person's word, made for approving the device waiting with `code`: the password, or `device` signing the
    challenge of options asked for that code. The answer, as the server gives it."""
    if device is None:
        return client.post("/api/account/confirm", headers=headers, json={"password": password, "device": code})
    options = client.post("/api/account/confirm/options", headers=headers, json={"purpose": "confirm", "device": code})
    assert options.status_code == 200, options.text
    options = options.json()
    return client.post("/api/account/confirm", headers=headers,
                       json={"challenge_id": options["challenge_id"],
                             "credential": device.get(as_page_asks(options, "confirm"))})


def word_for(client, headers, code, device=None):
    """confirm_for's confirmation itself."""
    r = confirm_for(client, headers, code, device)
    assert r.status_code == 200, r.text
    return r.json()["confirmation"]


def approve(client, headers, code, confirmation=None):
    body = {"code": code} if confirmation is None else {"code": code, "confirmation": confirmation}
    return client.post("/api/auth/device/approve", headers=headers, json=body)


def poll(client, asked):
    """The asking device's question, past the poll interval."""
    with store.connect() as db:
        db.execute("UPDATE device_signins SET last_poll_at = NULL")
    return client.post("/api/auth/device/poll", json={"id": asked["id"], "poll": asked["poll"]}).json()


def me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})


def user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def spent(client, headers, confirmation):
    """Whether `confirmation` (made by the password or a device passkey) has been used up, read from the record of
    spent challenges: for one no change can spend, a refusal cannot tell unused from used."""
    held, _ = passkeys._open_challenge(confirmation, passkeys.CONFIRMED, user_id(client, headers), int(time.time()))
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM passkey_used_challenges WHERE challenge_hash = ?",
                          (hashlib.sha256(held).hexdigest(),)).fetchone()[0] == 1


# ---- the standard tier ----------------------------------------------------------------------------------------------

def test_a_sign_in_alone_approves_no_device_and_the_device_waits_on(client, owner):
    asked = start(client)
    for given in (None, ""):
        r = approve(client, owner, asked["code"], given)
        assert (r.status_code, r.json()["detail"]) == (403, auth.NEEDS_CONFIRM_TO_APPROVE), given
        # a page from an older release, which asks for nothing, is told to reload
        assert r.json()["detail"].endswith("A page from an older release cannot ask for that: reload it.")
    assert poll(client, asked) == {"status": "waiting"}
    # with the password's word, made for this device, it is approved, and the device collects its own sign-in, once
    r = approve(client, owner, asked["code"], word_for(client, owner, asked["code"]))
    assert r.status_code == 200 and r.json() == {"name": "Kitchen iPad", "approved": True}
    got = poll(client, asked)
    assert got["status"] == "approved" and me(client, got["token"]).json()["email"] == ADMIN
    assert poll(client, asked) == {"status": "used"}
    # refusing takes the sign-in alone, as before
    other = start(client, "Tablet")
    assert client.post("/api/auth/device/refuse", headers=owner, json={"code": other["code"]}).json() == {
        "name": "Tablet", "approved": False}


def test_a_passkey_s_word_made_for_the_device_approves_it(client, owner):
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    asked = start(client)
    r = approve(client, owner, asked["code"], word_for(client, owner, asked["code"], phone))
    assert r.status_code == 200 and r.json()["approved"] is True
    assert poll(client, asked)["status"] == "approved"


def test_a_confirmation_made_for_one_device_approves_no_other_and_is_left_unused(client, owner):
    laptop = Authenticator(-7)
    add(client, owner, laptop, name="Laptop")
    for device in (None, laptop):
        first, second = start(client, "First"), start(client, "Second")
        for_first = word_for(client, owner, first["code"], device)
        r = approve(client, owner, second["code"], for_first)
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), device
        assert poll(client, second) == {"status": "waiting"}
        # left unused, it approves the device it was made for, its code written as the device shows it or as typed
        assert approve(client, owner, first["code"].lower().replace("-", " "), for_first).status_code == 200
        assert poll(client, first)["status"] == "approved" and poll(client, second) == {"status": "waiting"}


def test_a_confirmation_made_for_nothing_in_particular_approves_no_device(client, owner):
    asked = start(client)
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    for unbound in (confirmed(client, owner), confirmed(client, owner, phone)):
        r = approve(client, owner, asked["code"], unbound["confirmation"])
        # what a page from an older release makes: it is told to reload
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_FOR_NOTHING)
        # and it was left unused: no change spends one made for none now, so the record of spent ones is what shows it
        assert not spent(client, owner, unbound["confirmation"])
    # one made for another purpose is made for something else, whatever change it names
    r = approve(client, owner, asked["code"], confirmed(client, owner, phone, purpose="e2e-cancel")["confirmation"])
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)
    assert poll(client, asked) == {"status": "waiting"}
    # the record does tell a spent one: one made for this device, used by its approval
    given = word_for(client, owner, asked["code"], phone)
    assert approve(client, owner, asked["code"], given).status_code == 200 and spent(client, owner, given)


def test_a_confirmation_made_for_a_device_is_spent_on_no_other_change_and_left_unused(client, owner):
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    by_password, by_phone = start(client, "By password"), start(client, "By phone")
    given = [(by_password, word_for(client, owner, by_password["code"])),
             (by_phone, word_for(client, owner, by_phone["code"], phone))]
    for asked, word in given:
        for r in (client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": word}),
                  client.request("DELETE", f"/api/account/passkeys/{listed['id']}", headers=owner,
                                 json={"confirmation": word}),
                  client.post("/api/account/sign-out-everywhere", headers=owner, json={"confirmation": word}),
                  client.post("/api/account/passkeys/options", headers=owner, json={"confirmation": word})):
            assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), (asked["code"], r.request.url)
    assert me(client, owner["Authorization"][7:]).status_code == 200       # not signed out
    assert [k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]] == [listed["id"]]
    for asked, word in given:                           # each left unused: the password's and the passkey's alike
        assert approve(client, owner, asked["code"], word).status_code == 200


def test_every_change_that_uses_a_confirmation_names_the_change_it_is_made_for():
    """Every change that uses up a confirmation names the change it is for (passkeys._use_confirmation's `subject`,
    passkeys.CHANGE's forms, or a device's request), so one made for a device's request is refused there and left
    unused, and one made for another change approves no device: the tickets, a passkey's removal, signing out
    everywhere and adding a passkey above, and, read here in the source, revoking a link, approving a session's
    request to be linked, high security, the connector and a held request. End-to-end encryption's touches name a
    `purpose` instead, a ceremony of their own that no change's confirmation is made for."""
    import ast
    import inspect
    from proofpane_open.ade import link
    # what each name called resolves to, so a subject given by position counts: link._use_confirmation takes it fifth,
    # passkeys.use_confirmation third, while passkeys._use_confirmation's fifth is `hardware`
    modules = {"link": link, "passkeys": passkeys}
    source = os.path.join(HERE, "..", "proofpane_open")
    found = []
    for top, _, files in os.walk(source):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(top, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if called not in ("_use_confirmation", "use_confirmation", "use_passkey_confirmation"):
                        continue
                    assert not any(k.arg is None for k in node.keywords), (name, fn.name)   # a **mapping: unreadable
                    owner = f.value.id if isinstance(f, ast.Attribute) else name[:-3]
                    signature = inspect.signature(getattr(modules[owner], called))
                    given = signature.bind(*node.args, **{k.arg: k.value for k in node.keywords}).arguments
                    found.append((os.path.relpath(os.path.join(top, name), source), fn.name,
                                  "subject" in given, "purpose" in given))
    assert len(found) >= 15
    # the device's approval is among those naming a subject; every other one names a purpose, and is a touch's
    assert ("auth.py", "decide_device_signin", True, False) in found
    unnamed = [(path, fn, purpose) for path, fn, subject, purpose in found if not subject]
    assert all(purpose for _, _, purpose in unnamed), unnamed
    touches = os.path.join("ade", "e2e_keys.py")
    assert all(path == touches or (path, fn) == ("passkeys.py", "use_passkey_confirmation")
               for path, fn, _ in unnamed), unnamed
    assert any(path == os.path.join("ade", "e2e_keys.py") for path, _, _ in unnamed)


def test_a_used_or_expired_confirmation_approves_nothing_and_the_device_waits_on(client, owner):
    asked = start(client)
    user = auth.user(user_id(client, owner))
    late = int(time.time()) - passkeys.CHALLENGE_TTL - 1
    expired = passkeys.confirm(user, password=PASSWORD, now=late, subject=auth.approval_subject(asked["code"]))
    r = approve(client, owner, asked["code"], expired["confirmation"])
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_STALE)
    # one already spent, as by a request that raced this one, is refused too
    given = word_for(client, owner, asked["code"])
    now = int(time.time())
    held, expires_at = passkeys._open_challenge(given, passkeys.CONFIRMED, user["id"], now)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        passkeys._use_challenge(db, held, expires_at, now)
    r = approve(client, owner, asked["code"], given)
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_STALE)
    assert poll(client, asked) == {"status": "waiting"}
    # one fresh is used up by the approval, in its transaction
    given = word_for(client, owner, asked["code"])
    assert approve(client, owner, asked["code"], given).status_code == 200
    held, expires_at = passkeys._open_challenge(given, passkeys.CONFIRMED, user["id"], int(time.time()))
    with store.connect() as db:
        spent = db.execute("SELECT COUNT(*) FROM passkey_used_challenges WHERE challenge_hash = ?",
                           (hashlib.sha256(held).hexdigest(),)).fetchone()[0]
    assert spent == 1


def test_an_approval_refused_before_it_is_made_leaves_its_confirmation_unused(client, owner):
    asked = start(client)
    given = word_for(client, owner, asked["code"])
    r = approve(client, owner, "WRNG-CODE", given)
    assert r.status_code == 400 and "No device is waiting" in r.json()["detail"]
    assert approve(client, owner, asked["code"], given).status_code == 200


def test_a_confirmation_holds_only_while_the_passkey_that_gave_it_is_the_account_s(client, owner):
    asked, other = start(client), start(client, "Another")
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    given = word_for(client, owner, asked["code"], phone)
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert client.request("DELETE", f"/api/account/passkeys/{listed['id']}", headers=owner,
                          json=confirmed(client, owner, change=f"passkey-remove:{listed['id']}")).status_code == 200
    # offered for another device, it is refused as made for something else, whatever became of its passkey: what
    # it was made for is checked first, as it is for a purpose (passkeys._use_confirmation)
    r = approve(client, owner, other["code"], given)
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)
    r = approve(client, owner, asked["code"], given)
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_GONE)
    assert poll(client, asked) == {"status": "waiting"} and poll(client, other) == {"status": "waiting"}


def test_another_account_s_confirmation_approves_nothing(client, owner):
    other = signed_up(client, OTHER)
    asked = start(client)
    theirs = word_for(client, other, asked["code"])
    r = approve(client, owner, asked["code"], theirs)
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_STALE)
    assert poll(client, asked) == {"status": "waiting"}


# ---- asking for a confirmation for a device -------------------------------------------------------------------------

def test_a_confirmation_is_asked_for_a_device_only_while_one_waits_with_that_code(client, owner):
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    for path, body in (("/api/account/confirm", {"password": PASSWORD, "device": "ABCD-EFGH"}),
                       ("/api/account/confirm/options", {"purpose": "confirm", "device": "ABCD-EFGH"})):
        r = client.post(path, headers=owner, json=body)
        assert r.status_code == 400 and "No device is waiting" in r.json()["detail"], path
    asked = start(client)
    refused = client.post("/api/auth/device/refuse", headers=owner, json={"code": asked["code"]})
    assert refused.status_code == 200
    assert confirm_for(client, owner, asked["code"]).status_code == 400          # refused: no longer waiting


def test_a_passkey_s_answer_confirms_only_what_its_options_were_asked_for(client, owner):
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    first, second = start(client, "First"), start(client, "Second")
    options = client.post("/api/account/confirm/options", headers=owner,
                          json={"purpose": "confirm", "device": first["code"]}).json()
    answer = phone.get(as_page_asks(options, "confirm"))
    r = client.post("/api/account/confirm", headers=owner,
                    json={"challenge_id": options["challenge_id"], "credential": answer, "device": second["code"]})
    # the passkey did confirm, for another device than the one named: not "That did not confirm it"
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)
    # the answer was not used up by that: given with the device its options were for, it confirms that one
    r = client.post("/api/account/confirm", headers=owner,
                    json={"challenge_id": options["challenge_id"], "credential": answer, "device": first["code"]})
    assert r.status_code == 200, r.text
    given = r.json()["confirmation"]
    assert approve(client, owner, second["code"], given).json()["detail"] == passkeys.CONFIRM_OTHER
    assert approve(client, owner, first["code"], given).status_code == 200
    # options asked for no device give a confirmation for none, whatever device the answer names
    options = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm"}).json()
    r = client.post("/api/account/confirm", headers=owner,
                    json={"challenge_id": options["challenge_id"], "credential": phone.get(as_page_asks(options, "confirm")),
                          "device": second["code"]})
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)


# ---- high security ----------------------------------------------------------------------------------------------------

def test_while_high_security_is_on_only_a_hardware_key_s_word_approves_a_device(client, owner, metadata, maker):
    key, r = add_hardware(client, owner, maker)
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    asked = start(client)
    # made for this device while high security was off: the password's and a device passkey's
    early = [word_for(client, owner, asked["code"]), word_for(client, owner, asked["code"], phone)]
    by_key_for_another = word_for(client, owner, start(client, "Another")["code"], key)
    on = client.put("/api/account/high-security", headers=owner,
                    json={"on": True,
                          "confirmation": confirmed(client, owner, key, change="high-security-on")["confirmation"]})
    assert on.status_code == 200 and on.json()["high_security"] is True, on.text
    for given in early + [None]:
        r = approve(client, owner, asked["code"], given)
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.NEEDS_HARDWARE), given
    for device in (None, phone):                    # nor are they given now
        r = confirm_for(client, owner, asked["code"], device)
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.NEEDS_HARDWARE)
    r = approve(client, owner, asked["code"], by_key_for_another)
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER)
    assert poll(client, asked) == {"status": "waiting"}
    r = approve(client, owner, asked["code"], word_for(client, owner, asked["code"], key))
    assert r.status_code == 200 and r.json()["approved"] is True
    assert poll(client, asked)["status"] == "approved"


# ---- an account with nothing to confirm with ------------------------------------------------------------------------

def test_the_desktop_server_s_own_account_approves_with_its_sign_in_alone(client):
    # it has neither a password nor a passkey (desktop.ensure_account): its browsers sign in by another device
    mine = {"Authorization": "Bearer " + auth.issue_token(desktop.ensure_account("you@this.desktop"))}
    assert client.get("/api/account/confirm", headers=mine).json()["needed"] is False
    asked = start(client, "A browser on Mac")
    r = approve(client, mine, asked["code"])
    assert r.status_code == 200 and r.json()["approved"] is True
    got = poll(client, asked)
    assert got["status"] == "approved" and me(client, got["token"]).json()["email"] == "you@this.desktop"


# ---- signing out everywhere ------------------------------------------------------------------------------------------

def test_signing_out_everywhere_refuses_a_device_approved_and_not_yet_signed_in(client, owner):
    other = signed_up(client, OTHER)
    held_back, theirs, waiting = start(client, "Held back"), start(client, "Theirs"), start(client, "Waiting")
    assert approve(client, owner, held_back["code"], word_for(client, owner, held_back["code"])).status_code == 200
    assert approve(client, other, theirs["code"], word_for(client, other, theirs["code"])).status_code == 200
    # the device holds back its poll while its owner signs out everywhere: it would get a sign-in of the new
    # generation, which no sign-out before it ended
    r = sign_out_everywhere(client, owner)
    assert r.status_code == 200
    owner = {"Authorization": "Bearer " + r.json()["token"]}
    # refused, and said to be an approval withdrawn, not a refusal on the other device
    assert poll(client, held_back) == {"status": "refused", "withdrawn": True}
    # another account's device, and one still waiting, are not this account's to refuse
    assert poll(client, theirs)["status"] == "approved"
    assert approve(client, owner, waiting["code"], word_for(client, owner, waiting["code"])).status_code == 200
    assert poll(client, waiting)["status"] == "approved"


# ---- what confirmed an approval, and its end ------------------------------------------------------------------------

def passkey_id(client, headers, name):
    (key,) = [k for k in client.get("/api/account/passkeys", headers=headers).json()["passkeys"] if k["name"] == name]
    return key["id"]


def test_removing_the_passkey_that_approved_a_device_ends_its_sign_in_and_refuses_one_not_yet_collected(client, owner):
    """Whoever holds a stolen unlocked phone signs a laptop in with one touch of its passkey; removing that passkey now
    ends the laptop's sign-in (it had lasted 7 days) and refuses one approved and not collected yet. A device approved
    with the password, or with another passkey, is not touched."""
    phone, laptop = Authenticator(-8), Authenticator(-7)
    add(client, owner, phone, name="iPhone")
    add(client, owner, laptop, name="Laptop")
    collected, waiting, by_password, by_other = (start(client, n) for n in ("Thief", "Thief 2", "Mine", "Mine 2"))
    for asked, device in ((collected, phone), (waiting, phone), (by_password, None), (by_other, laptop)):
        assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"], device)).status_code == 200
    signed = {n: poll(client, a)["token"] for n, a in (("thief", collected), ("password", by_password),
                                                       ("other", by_other))}
    assert all(me(client, t).status_code == 200 for t in signed.values())
    gone = passkey_id(client, owner, "iPhone")
    r = client.request("DELETE", f"/api/account/passkeys/{gone}", headers=owner,
                       json=confirmed(client, owner, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert me(client, signed["thief"]).status_code == 401                 # ended
    assert poll(client, waiting) == {"status": "refused", "withdrawn": True}
    assert me(client, signed["password"]).status_code == 200 and me(client, signed["other"]).status_code == 200
    assert me(client, owner["Authorization"][7:]).status_code == 200      # the owner's own sign-in stays


def test_a_device_s_sign_in_outlives_its_request_and_its_record_goes_with_that_sign_in(client, owner):
    """A request lasts 10 minutes; the sign-in it gave, 7 days, and token_user reads the request's row to know it has
    not been ended: so the row stays while the sign-in lasts, and goes once it has expired."""
    asked = start(client)
    assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"])).status_code == 200
    token = poll(client, asked)["token"]

    def count():
        with store.connect() as db:
            return db.execute("SELECT COUNT(*) FROM device_signins WHERE id = ?", (asked["id"],)).fetchone()[0]
    auth.start_device_signin("Another", now=int(time.time()) + config.DEVICE_SIGNIN_TTL_SECONDS + 60)   # sweeps
    assert me(client, token).status_code == 200 and count() == 1
    auth.start_device_signin("Yet another", now=int(time.time()) + config.TOKEN_TTL_SECONDS + 60)
    assert count() == 0


def test_a_device_that_signs_out_everywhere_stays_the_sign_in_its_approver_s_passkey_ends(client, owner):
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    asked = start(client, "Thief")
    assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"], phone)).status_code == 200
    thief = {"Authorization": "Bearer " + poll(client, asked)["token"]}
    # it signs every other device out, with the phone's passkey, and gets a new sign-in: still that device's
    r = sign_out_everywhere(client, thief, body=confirmed(client, thief, phone, change="sign-out-everywhere"))
    assert r.status_code == 200, r.text
    thief = r.json()["token"]
    assert me(client, thief).status_code == 200
    mine = auth.issue_token(user_id(client, {"Authorization": "Bearer " + thief}))    # the owner signs in again
    gone = passkey_id(client, {"Authorization": "Bearer " + mine}, "iPhone")
    r = client.request("DELETE", f"/api/account/passkeys/{gone}", headers={"Authorization": "Bearer " + mine},
                       json=confirmed(client, {"Authorization": "Bearer " + mine}, change=f"passkey-remove:{gone}"))
    assert r.status_code == 200, r.text
    assert me(client, thief).status_code == 401


def test_a_sign_out_everywhere_as_a_device_collects_its_sign_in_leaves_it_none(client, owner, monkeypatch):
    """The sign-in was made after the transaction that marked it collected, of the generation read then: a sign out
    everywhere between the two left the device a sign-in of the new generation. It is made in that
    transaction now, so one that lands after it ends it."""
    asked = start(client)
    assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"])).status_code == 200
    account, landed, real = user_id(client, owner), [], auth._generation

    def generation(user):
        if not landed:                                   # as it lands just after the collection's transaction
            landed.append(True)
            auth.sign_out_everywhere(account)
        return real(user)
    monkeypatch.setattr(auth, "_generation", generation)
    got = poll(client, asked)
    if not landed:                                       # read with the collection: the sign-out comes just after
        landed.append(True)
        auth.sign_out_everywhere(account)
    assert got["status"] == "approved" and me(client, got["token"]).status_code == 401


def test_an_approval_from_a_sign_in_signed_out_meanwhile_approves_nothing(client, owner, monkeypatch):
    """The route checked the sign-in, and then the account signed out everywhere before the approval's transaction:
    the approval checks it again inside, and answers as for a sign-in ended before."""
    asked = start(client)
    given = word_for(client, owner, asked["code"])
    account, real = user_id(client, owner), auth.decide_device_signin

    def decide(*args, **kwargs):
        auth.sign_out_everywhere(account)                 # between the route's check and the change
        return real(*args, **kwargs)
    monkeypatch.setattr(auth, "decide_device_signin", decide)
    r = approve(client, owner, asked["code"], given)
    assert (r.status_code, r.json()["detail"]) == (401, "Sign in first.")
    assert poll(client, asked) == {"status": "waiting"}


def test_turning_high_security_on_refuses_a_device_no_hardware_key_approved_that_has_not_signed_in(client, owner,
                                                                                                 metadata, maker):
    key, r = add_hardware(client, owner, maker)
    assert r.json()["tier"] == "hardware", r.text
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    by_password, by_phone, by_key, signed_in = (start(client, n) for n in ("Password", "Phone", "Key", "Signed in"))
    for asked, device in ((by_password, None), (by_phone, phone), (by_key, key), (signed_in, None)):
        assert approve(client, owner, asked["code"], word_for(client, owner, asked["code"], device)).status_code == 200
    token = poll(client, signed_in)["token"]
    on = client.put("/api/account/high-security", headers=owner,
                    json={"on": True,
                          "confirmation": confirmed(client, owner, key, change="high-security-on")["confirmation"]})
    assert on.status_code == 200, on.text
    assert poll(client, by_password) == poll(client, by_phone) == {"status": "refused", "withdrawn": True}
    assert poll(client, by_key)["status"] == "approved"
    assert me(client, token).status_code == 200           # turning it on ends no sign-in


# ---- the page's own approval, against this server ---------------------------------------------------------------------

def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@pytest.fixture
def served(client, monkeypatch):
    """This app, served on a local port, for the page's own code to call."""
    import uvicorn
    from proofpane_open.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", url)      # the origin and relying party every passkey answer names
    yield url
    server.should_exit = True
    thread.join(10)


@pytest.mark.skipif(NODE is None or not os.path.exists(VITEST), reason="needs node and the page's packages in ui/")
def test_the_page_approves_devices_with_the_password_and_with_a_passkey_through_this_server(client, served,
                                                                                             tmp_path):
    owner = signed_up(client)
    device = Authenticator(-7, rp="127.0.0.1", origin=served)
    assert add(client, owner, device, name="This Mac").status_code == 200
    first, second = start(client, "Kitchen iPad"), start(client, "Proofpane Community on iPhone")
    key = device.key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption())
    given = {"server": served, "token": owner["Authorization"][7:], "user": client.get("/api/auth/me",
                                                                                         headers=owner).json(),
             "password": PASSWORD, "origin": served, "rp_id": "127.0.0.1", "key": b64u(key),
             "credential_id": b64u(device.credential_id),
             "first": {"code": first["code"], "name": "Kitchen iPad", "id": first["id"], "poll": first["poll"]},
             "second": {"code": second["code"], "name": "Proofpane Community on iPhone"}}
    (tmp_path / "given.json").write_text(json.dumps(given))
    page = subprocess.run([VITEST, "run", "src/deviceApproval.test.tsx"], cwd=UI, capture_output=True, text=True,
                          timeout=600, env=dict(os.environ, PP_DEVICE_APPROVAL=str(tmp_path / "given.json")))
    assert page.returncode == 0, page.stdout + page.stderr
    # each device collects a sign-in of the owner's account, approved by the page with a confirmation made for it
    for asked in (first, second):
        got = poll(client, asked)
        assert got["status"] == "approved", (asked, page.stdout)
        assert me(client, got["token"]).json()["email"] == ADMIN
