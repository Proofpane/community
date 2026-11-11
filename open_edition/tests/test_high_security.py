"""High security, chosen per account (passkeys.py): while it is on, only a hardware key's confirmation makes a risky
change (a session linked, a link revoked, a passkey added or removed, every other device signed out), only a hardware
key signs a command to a linked session, and a session pins the hardware keys alone. Turning it on or off takes a
hardware key; the last hardware key stays while it is on; a password confirms nothing while it is on. Hardware keys
are the tests' own (tests/fido_fixtures.py), judged against a blob of the tests' own."""

import os
import time
import uuid

import pytest

from conftest import signed_up
from fido_fixtures import HardwareKey, Maker, add_hardware
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import auth, fido_mds, passkeys, store
from proofpane_open.ade import holds, link, link_commands, moves, push
from test_ade_link import Session, at_once, paired, post
from test_ade_link_asks import address, approve, ask, asks, at, links, poll  # noqa: F401  (at: a fixture)
from test_ade_link_commands import payload, send, take
from test_ade_link_moves import lists, move_payload, workspace  # noqa: F401  (workspace: a fixture)
from test_ade_push import FCM, Device, PushService
from test_device_approval import word_for
from test_hardware_keys import COMPROMISED, reported_entry, told  # noqa: F401  (told: a fixture)
from test_passkeys import Authenticator, add, confirm, confirmed, sign_in

PASSWORD = "correct horse battery"                  # conftest's signed_up


@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def keys(client, owner, metadata, maker):
    """The account's passkeys: two hardware keys of the maker's model, and a phone's passkey, a device passkey."""
    out = {}
    for label in ("a", "b"):
        out[label], r = add_hardware(client, owner, maker, name=f"YubiKey {label.upper()}")
        assert r.json()["tier"] == "hardware", r.text
    out["phone"] = Authenticator(-8)
    assert add(client, owner, out["phone"], name="iPhone").json()["tier"] == "device"
    return out


def ids(client, owner):
    return {k["name"]: k["id"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}


def turn(client, owner, on, confirmation):
    return client.put("/api/account/high-security", headers=owner, json={"on": on, "confirmation": confirmation})


def word(client, owner, device=None, change="high-security-on"):
    """A confirmation from `device`, or the password, made for `change` (turning high security on unless named): the
    string itself."""
    return confirmed(client, owner, device, change=change)["confirmation"]


def on(client, owner, keys):
    r = turn(client, owner, True, word(client, owner, keys["a"]))
    assert r.status_code == 200 and r.json()["high_security"] is True, r.text
    return r.json()


# ---- turning it on and off -------------------------------------------------------------------------------------------

def test_turning_it_on_takes_a_hardware_key_kept_and_its_confirmation(client, owner, metadata, maker):
    r = turn(client, owner, True, word(client, owner))
    assert r.status_code == 409 and r.json()["detail"] == passkeys.NO_HARDWARE_KEY
    phone = Authenticator(-8)
    add(client, owner, phone, name="iPhone")
    r = turn(client, owner, True, word(client, owner, phone))
    assert r.status_code == 409 and r.json()["detail"] == passkeys.NO_HARDWARE_KEY      # a device passkey is not one
    key, _ = add_hardware(client, owner, maker)
    for given in (word(client, owner), word(client, owner, phone), None, ""):
        r = turn(client, owner, True, given)
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE_TO_TURN, given
    assert client.get("/api/account/passkeys", headers=owner).json()["high_security"] is False
    given = word(client, owner, key)
    assert turn(client, owner, True, given).json() == {"high_security": True, "withdrawn": 0, "stopped": [],
                                                       "cancelled": []}
    assert turn(client, owner, True, given).json()["detail"] == passkeys.CONFIRM_STALE      # used once
    assert client.get("/api/account/passkeys", headers=owner).json()["high_security"] is True
    assert client.get("/api/account/confirm", headers=owner).json() == {
        "needed": True, "password": True, "passkeys": 2, "hardware": 1, "high_security": True, "e2e": False}


def test_a_refused_turn_leaves_its_confirmation_unused(client, owner, keys):
    phone = ids(client, owner)["iPhone"]
    given = word(client, owner, change=f"passkey-remove:{phone}")  # the password's: refused for the turn
    assert turn(client, owner, True, given).status_code == 403
    r = client.request("DELETE", f"/api/account/passkeys/{phone}", headers=owner, json={"confirmation": given})
    assert r.status_code == 200, r.text                             # and still good for what high security is off for


def test_turning_it_off_takes_a_hardware_key_s_confirmation_and_nothing_else(client, owner, keys):
    early = word(client, owner)                                     # made before high security was on
    on(client, owner, keys)
    for given, why in ((early, passkeys.NEEDS_HARDWARE_TO_TURN), (None, passkeys.NEEDS_HARDWARE_TO_TURN)):
        r = turn(client, owner, False, given)
        assert r.status_code == 403 and r.json()["detail"] == why
    assert confirm(client, owner).status_code == 403                # the password confirms nothing now
    assert confirm(client, owner, keys["phone"]).status_code == 403  # nor the phone's passkey
    # made for turning it on, a hardware key's confirmation does not turn it off
    r = turn(client, owner, False, word(client, owner, keys["b"]))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    assert turn(client, owner, False, word(client, owner, keys["b"], change="high-security-off")).json() == {
        "high_security": False, "withdrawn": 0, "stopped": [], "cancelled": []}
    assert confirm(client, owner).status_code == 200                # the password confirms again


def test_no_password_turns_it_off_or_confirms_anything_while_it_is_on(client, owner, keys):
    # a forgotten password must not turn it off: nothing the password does reaches the switch
    on(client, owner, keys)
    token = client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()["token"]
    signed_in = {"Authorization": "Bearer " + token}
    counted = {key: list(entries) for key, entries in auth._failures.items()}
    for password in (PASSWORD, "a wrong password", ""):
        r = client.post("/api/account/confirm", headers=signed_in, json={"password": password})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE, password
    assert {key: list(entries) for key, entries in auth._failures.items()} == counted     # not checked, not counted
    assert turn(client, signed_in, False, None).status_code == 403
    assert client.get("/api/account/passkeys", headers=signed_in).json()["high_security"] is True


def test_a_confirmation_says_whether_a_hardware_key_made_it(client, owner, keys):
    assert confirm(client, owner, keys["a"]).json()["hardware"] is True
    for device in (keys["phone"], None):
        assert confirm(client, owner, device).json()["hardware"] is False
    # and only a hardware key's is the kind high security takes
    on(client, owner, keys)
    r = client.post("/api/account/confirm/options", headers=owner,
                    json={"purpose": "confirm"}).json()["publicKey"]["allowCredentials"]
    assert {c["id"] for c in r} == {passkeys.b64u(keys["a"].credential_id), passkeys.b64u(keys["b"].credential_id)}


def test_the_page_may_ask_for_a_hardware_key_alone_to_turn_it_on(client, owner, keys):
    every = client.post("/api/account/confirm/options", headers=owner,
                        json={"purpose": "confirm"}).json()["publicKey"]["allowCredentials"]
    only = client.post("/api/account/confirm/options", headers=owner,
                       json={"purpose": "confirm", "hardware": True}).json()["publicKey"]["allowCredentials"]
    assert len(every) == 3 and len(only) == 2
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = 'device'")
    r = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm", "hardware": True})
    assert r.status_code == 409 and r.json()["detail"] == passkeys.NO_HARDWARE_KEY


def test_turning_it_on_or_off_is_told_to_the_account_s_devices(client, owner, keys, monkeypatch):
    service = PushService()
    monkeypatch.setattr(push, "_default_post", service)
    monkeypatch.setattr(push, "_spawn", lambda work: work())
    device = Device()
    push.subscribe(client.get("/api/auth/me", headers=owner).json()["id"], FCM, device.p256dh, push.b64u(device.auth))
    on(client, owner, keys)
    turn(client, owner, False, word(client, owner, keys["a"], change="high-security-off"))
    assert turn(client, owner, True, None).status_code == 403       # refused: told nothing
    told = [device.decrypt(body)["title"] for _, _, body in service.posts]
    assert told == ["High security was turned on", "High security was turned off"]


def test_while_on_approving_a_device_s_sign_in_takes_a_hardware_key_s_confirmation(client, owner, keys):
    early = word(client, owner)                                     # the password's, made while it was off
    on(client, owner, keys)
    started = client.post("/api/auth/device/start", json={"name": "Laptop"}).json()
    for given in (None, early):
        r = client.post("/api/auth/device/approve", headers=owner, json={"code": started["code"],
                                                                         "confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE, given
    asked = {"id": started["id"], "poll": started["poll"]}
    assert client.post("/api/auth/device/poll", json=asked).json() == {"status": "waiting"}
    # a hardware key's word made for nothing in particular approves no device (tests/test_device_approval.py), and says
    # to reload a page that makes such words
    r = client.post("/api/auth/device/approve", headers=owner, json={"code": started["code"],
                                                                     "confirmation": word(client, owner, keys["a"],
                                                                                          change=None)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FOR_NOTHING
    by_key = word_for(client, owner, started["code"], keys["a"])
    r = client.post("/api/auth/device/approve", headers=owner, json={"code": "WRONG-CODE", "confirmation": by_key})
    assert r.status_code == 400                                     # no such request: the confirmation stays unused
    r = client.post("/api/auth/device/approve", headers=owner, json={"code": started["code"], "confirmation": by_key})
    assert r.status_code == 200 and r.json() == {"name": "Laptop", "approved": True}
    with store.connect() as db:
        db.execute("UPDATE device_signins SET last_poll_at = NULL")    # past the poll interval
    assert client.post("/api/auth/device/poll", json=asked).json()["status"] == "approved"
    other = client.post("/api/auth/device/start", json={"name": "Tablet"}).json()
    assert client.post("/api/auth/device/refuse", headers=owner, json={"code": other["code"]}).json() == {
        "name": "Tablet", "approved": False}                        # refusing takes the sign-in alone


def test_the_switch_takes_true_or_false(client, owner, keys):
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    given = word(client, owner, keys["a"])                          # a confirmation that would turn it on
    for not_a_bool in (1, "true", None):
        with pytest.raises(passkeys.PasskeyError) as refused:
            passkeys.set_high_security(user_id, not_a_bool, given)
        assert refused.value.status == 422 and "true or false" in str(refused.value), not_a_bool
    assert passkeys.high_security(user_id) is False
    assert turn(client, owner, True, given).status_code == 200       # the confirmation was left unused
    assert client.put("/api/account/high-security", headers=owner, json={"confirmation": "x"}).status_code == 422
    assert client.put("/api/account/high-security", json={"on": True}).status_code == 401


# ---- the risky changes, while it is on ---------------------------------------------------------------------------

def _ticket(client, owner, given, context):
    return client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": given})


def _revoke(client, owner, given, context):
    return client.request("DELETE", f"/api/ade/link/links/{context['link']}", headers=owner,
                          json={"confirmation": given})


def _remove(client, owner, given, context):
    return client.request("DELETE", f"/api/account/passkeys/{ids(client, owner)['iPhone']}", headers=owner,
                          json={"confirmation": given})


def _sign_out_everywhere(client, owner, given, context):
    return client.post("/api/account/sign-out-everywhere", headers=owner, json={"confirmation": given})


def _add(client, owner, given, context):
    # a passkey added could be a hardware key of whoever holds the password: adding one is held to it too
    return client.post("/api/account/passkeys/options", headers=owner, json={"confirmation": given})


_MADE_FOR = {_ticket: lambda c, ids: "link-ticket:new", _revoke: lambda c, ids: f"link-revoke:{c['link']}",
             _remove: lambda c, ids: f"passkey-remove:{ids['iPhone']}",
             _sign_out_everywhere: lambda c, ids: "sign-out-everywhere", _add: lambda c, ids: "passkey-add"}


@pytest.mark.parametrize("change", [_ticket, _revoke, _remove, _sign_out_everywhere, _add])
def test_while_on_a_risky_change_takes_a_hardware_key_s_confirmation(client, owner, keys, change):
    context = {"link": paired(client, owner).link_id}               # linked while it was off
    made_for = _MADE_FOR[change](context, ids(client, owner))
    by_password, by_phone = word(client, owner, change=made_for), word(client, owner, keys["phone"], change=made_for)
    on(client, owner, keys)
    for given, why in ((by_password, passkeys.NEEDS_HARDWARE), (by_phone, passkeys.NEEDS_HARDWARE),
                       (None, passkeys.NEEDS_HARDWARE), ("x.y", passkeys.CONFIRM_STALE)):
        r = change(client, owner, given, context)
        assert r.status_code == 403 and r.json()["detail"] == why, (change.__name__, why, r.text)
    r = change(client, owner, word(client, owner, keys["b"], change=made_for), context)
    assert r.status_code == 200, (change.__name__, r.text)


def test_while_off_the_password_and_any_passkey_confirm_as_before(client, owner, keys):
    for device in (None, keys["phone"], keys["a"]):
        r = client.post("/api/ade/link/tickets", headers=owner,
                        json={"confirmation": word(client, owner, device, change="link-ticket:new")})
        assert r.status_code == 200 and r.json()["high_security"] is False


def test_the_last_hardware_key_stays_while_it_is_on(client, owner, keys):
    on(client, owner, keys)
    have = ids(client, owner)
    r = client.request("DELETE", f"/api/account/passkeys/{have['YubiKey B']}", headers=owner,
                       json={"confirmation": word(client, owner, keys["a"],
                                                  change=f"passkey-remove:{have['YubiKey B']}")})
    assert r.status_code == 200, r.text
    given = word(client, owner, keys["a"], change=f"passkey-remove:{have['YubiKey A']}")
    r = client.request("DELETE", f"/api/account/passkeys/{have['YubiKey A']}", headers=owner,
                       json={"confirmation": given})
    assert r.status_code == 409 and r.json()["detail"] == passkeys.LAST_HARDWARE_KEY
    # a device passkey goes as ever, with a hardware key's word
    r = client.request("DELETE", f"/api/account/passkeys/{have['iPhone']}", headers=owner,
                       json={"confirmation": word(client, owner, keys["a"], change=f"passkey-remove:{have['iPhone']}")})
    assert r.status_code == 200
    assert turn(client, owner, False, word(client, owner, keys["a"], change="high-security-off")).status_code == 200
    # the refused removal left `given` unused: off, it removes the key it was made for
    r = client.request("DELETE", f"/api/account/passkeys/{have['YubiKey A']}", headers=owner,
                       json={"confirmation": given})
    assert r.status_code == 200                                     # off: it goes, with the password's word
    assert sign_in(client, keys["a"]).status_code == 401


# ---- the commands to a linked session, and the keys a session pins ---------------------------------------------------

@pytest.fixture
def linked(client, owner, keys):
    """A session linked while high security was off, then high security turned on."""
    session = paired(client, owner)
    on(client, owner, keys)
    return session


def test_while_on_only_a_hardware_key_signs_an_instruction(client, owner, keys, linked):
    p = payload(client, owner, "instruction", linked.link_id)
    r = send(client, owner, keys["phone"], p)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    r = send(client, owner, keys["a"], payload(client, owner, "instruction", linked.link_id))
    assert r.status_code == 200, r.text
    turn(client, owner, False, word(client, owner, keys["a"], change="high-security-off"))
    assert send(client, owner, keys["phone"], payload(client, owner, "instruction", linked.link_id)).status_code == 200


def test_while_on_only_a_hardware_key_signs_an_answer(client, owner, keys, linked):
    asked = post(linked, "question", "Which branch?", to="owner")
    assert asked.status_code == 200, asked.text
    seq = asked.json()["seq"]

    def answer(device):
        return send(client, owner, device, payload(client, owner, "answer", linked.link_id, "main", ref=seq,
                                                   digest=link_commands.text_digest("Which branch?")))
    assert answer(keys["phone"]).status_code == 403
    assert answer(keys["b"]).status_code == 200


def test_while_on_only_a_hardware_key_signs_a_move(client, owner, keys, linked, workspace):
    lists(linked, ("s1", False))
    r = send(client, owner, keys["phone"], move_payload(client, owner, linked.link_id, "s1"))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_held_moves").fetchone()[0] == 0     # nothing held of it
    r = send(client, owner, keys["a"], move_payload(client, owner, linked.link_id, "s1"))
    assert r.status_code == 200 and r.json()["held"]["kind"] == moves.HOLD, r.text


def test_while_on_only_a_hardware_key_signs_a_resume_and_a_stop_takes_the_sign_in(client, owner, keys, linked):
    stop = client.post(f"/api/ade/link/links/{linked.link_id}/stop", headers=owner)
    assert stop.status_code == 200                                 # a stop can only halt: the sign-in alone, as ever
    t = stop.json()["stopped_at"]
    path = f"/api/ade/link/links/{linked.link_id}/resume"

    def resume(device):
        return send(client, owner, device, payload(client, owner, "resume", linked.link_id, created_at=t + 1,
                                                   expires_at=t + 601), path=path)
    r = resume(keys["phone"])
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    r = resume(keys["a"])
    assert r.status_code == 200, r.text


def test_a_signature_checked_before_it_was_turned_on_is_refused_as_it_is_stored(client, owner, keys, monkeypatch):
    session = paired(client, owner)
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    checked = passkeys.check_command

    def then_turned_on(*args, **kwargs):
        got = checked(*args, **kwargs)
        with store.connect() as db:                                 # turned on between the check and the store
            db.execute("UPDATE users SET high_security = 1 WHERE id = ?", (user_id,))
        return got
    monkeypatch.setattr(passkeys, "check_command", then_turned_on)
    r = send(client, owner, keys["phone"], payload(client, owner, "instruction", session.link_id))
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_events WHERE kind = 'instruction'").fetchone()[0] == 0


def test_while_on_a_session_pins_and_is_asked_to_verify_the_hardware_keys_alone(client, owner, keys):
    every = {k["fingerprint"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    hardware = {k["fingerprint"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                if k["tier"] == "hardware"}
    before = paired(client, owner, name="before")
    assert {k["fingerprint"] for k in before.call("GET", "/owner-keys").json()["passkeys"]} == every
    nonce = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "instruction"}).json()
    assert nonce["credentials"] == []                               # off: any of the account's passkeys
    on(client, owner, keys)
    # the ticket's pair command names the hardware keys alone, and pairing offers those alone to pin
    ticket = client.post("/api/ade/link/tickets", headers=owner,
                         json={"confirmation": word(client, owner, keys["a"], change="link-ticket:new")}).json()
    assert set(ticket["keys"]) == hardware and ticket["high_security"] is True
    s = Session(client)
    with at_once():                         # linked as it pairs: the wait is test_link_pair_hold.py's
        paired_now = s.pair(ticket["ticket"], name="after").json()
    assert {k["fingerprint"] for k in paired_now["owner_keys"]["passkeys"]} == hardware
    # a session linked before reads the hardware keys alone, so its next pin drops the others
    assert {k["fingerprint"] for k in before.call("GET", "/owner-keys").json()["passkeys"]} == hardware
    nonce = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": "resume"}).json()
    assert set(nonce["credentials"]) == {passkeys.b64u(keys[k].credential_id) for k in ("a", "b")}
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    assert link_commands.owner_keys({"user_id": user_id})["passkeys"] == passkeys.owner_keys(user_id)


# ---- a confirmation holds while the passkey that gave it does --------------------------------------------------------

def remove_key(client, owner, name, device=None):
    """Remove the passkey `name` with a confirmation from `device`, or the password, made for removing it."""
    passkey_id = ids(client, owner)[name]
    return client.request("DELETE", f"/api/account/passkeys/{passkey_id}", headers=owner,
                          json={"confirmation": word(client, owner, device, change=f"passkey-remove:{passkey_id}")})


def test_a_confirmation_holds_only_while_the_passkey_that_gave_it_is_the_account_s(client, owner, keys):
    by_key, by_phone, by_password = (word(client, owner, device, change="link-ticket:new")
                                     for device in (keys["a"], keys["phone"], None))
    assert remove_key(client, owner, "YubiKey A", keys["b"]).status_code == 200
    assert remove_key(client, owner, "iPhone").status_code == 200
    for given in (by_key, by_phone):
        r = client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_GONE
    assert client.post("/api/ade/link/tickets", headers=owner, json={"confirmation": by_password}).status_code == 200


def test_a_hardware_key_s_confirmation_holds_only_while_it_is_a_hardware_key(client, owner, keys):
    given = word(client, owner, keys["a"])
    with store.connect() as db:                         # judged otherwise since (its model reported compromised)
        db.execute("UPDATE passkeys SET tier = 'device' WHERE name = 'YubiKey A'")
    r = turn(client, owner, True, given)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_GONE


def test_registration_options_hold_only_while_the_passkey_that_confirmed_them_does(client, owner, keys, maker):
    begun = {device: client.post("/api/account/passkeys/options", headers=owner,
                                 json={"confirmation": word(client, owner, keys[device], change="passkey-add")}).json()
             for device in ("a", "phone")}
    assert remove_key(client, owner, "YubiKey A").status_code == 200
    assert remove_key(client, owner, "iPhone").status_code == 200
    for options in begun.values():
        r = client.post("/api/account/passkeys", headers=owner, json={
            "challenge_id": options["challenge_id"], "name": "late", "credential": HardwareKey(maker).create(options)})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_GONE
    assert "late" not in ids(client, owner)


# ---- a ticket to link a session, and what confirmed it ---------------------------------------------------------------

def made_ticket(client, owner, device=None):
    """A ticket for a new session, with a confirmation from `device`, or the password, made for it."""
    r = client.post("/api/ade/link/tickets", headers=owner,
                    json={"confirmation": word(client, owner, device, change="link-ticket:new")})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def pairs(client, ticket, name):
    return Session(client).pair(ticket, name=name)


def test_turning_it_on_voids_the_tickets_no_hardware_key_confirmed(client, owner, keys):
    tickets = {"password": made_ticket(client, owner), "phone": made_ticket(client, owner, keys["phone"]),
               "key": made_ticket(client, owner, keys["b"])}
    on(client, owner, keys)
    for made_by in ("password", "phone"):
        r = pairs(client, tickets[made_by], made_by)
        assert r.status_code == 403 and "is not the page's" in r.json()["detail"], made_by
    assert pairs(client, tickets["key"], "key").status_code == 200


def test_removing_a_passkey_voids_the_tickets_it_confirmed(client, owner, keys):
    by_phone, by_password = made_ticket(client, owner, keys["phone"]), made_ticket(client, owner)
    assert remove_key(client, owner, "iPhone").status_code == 200
    assert pairs(client, by_phone, "phone").status_code == 403
    assert pairs(client, by_password, "password").status_code == 200


def test_signing_out_everywhere_voids_every_unused_ticket(client, owner, keys):
    unused = made_ticket(client, owner, keys["a"])
    r = client.post("/api/account/sign-out-everywhere", headers=owner,
                    json={"confirmation": word(client, owner, change="sign-out-everywhere")})
    assert r.status_code == 200
    assert pairs(client, unused, "after").status_code == 403


def test_a_ticket_no_hardware_key_confirmed_pairs_nothing_while_high_security_is_on(client, owner, keys):
    # turning it on voids such a ticket; pairing refuses one whatever voided it or not
    unvouched = made_ticket(client, owner)
    with store.connect() as db:
        db.execute("UPDATE users SET high_security = 1")
    r = pairs(client, unvouched, "late")
    assert r.status_code == 403 and r.json()["detail"] == link.TICKET_NOT_HARDWARE


# ---- a ticket to pair a signer (the second screen), and what confirmed it: as a session's ticket --------------------

def made_signer_ticket(client, owner, device=None):
    """A ticket to pair a signer (ade/signers.py), with a confirmation from `device`, or the password, made for it."""
    r = client.post("/api/ade/signers/tickets", headers=owner,
                    json={"confirmation": word(client, owner, device, change="signer-ticket")})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def signer_pairs(client, ticket, name):
    from test_ade_signers import Signer
    return Signer(client).pair(ticket, name=name)


def unused_signer_tickets():
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM ade_signer_tickets WHERE used_at IS NULL").fetchone()[0]


def test_turning_it_on_voids_the_signer_tickets_no_hardware_key_confirmed(client, owner, keys):
    # a signer's ticket is voided by the rules that void a session's (link.void_tickets)
    tickets = {"password": made_signer_ticket(client, owner), "phone": made_signer_ticket(client, owner, keys["phone"]),
               "key": made_signer_ticket(client, owner, keys["b"])}
    on(client, owner, keys)
    assert unused_signer_tickets() == 1                         # voided: deleted as it is turned on, not only refused
    for made_by in ("password", "phone"):
        r = signer_pairs(client, tickets[made_by], made_by)
        assert r.status_code == 403 and "is not the page's" in r.json()["detail"], made_by
    assert signer_pairs(client, tickets["key"], "key").status_code == 200


def test_turning_it_on_and_off_again_brings_back_no_signer_ticket(client, owner, keys):
    unvouched = made_signer_ticket(client, owner, keys["phone"])
    on(client, owner, keys)
    assert turn(client, owner, False, word(client, owner, keys["b"], change="high-security-off")).status_code == 200
    r = signer_pairs(client, unvouched, "late")
    assert r.status_code == 403 and "is not the page's" in r.json()["detail"]


def test_removing_a_passkey_voids_the_signer_tickets_it_confirmed(client, owner, keys):
    by_phone, by_password = made_signer_ticket(client, owner, keys["phone"]), made_signer_ticket(client, owner)
    assert remove_key(client, owner, "iPhone").status_code == 200
    assert unused_signer_tickets() == 1
    assert signer_pairs(client, by_phone, "phone").status_code == 403
    assert signer_pairs(client, by_password, "password").status_code == 200


def test_a_signer_ticket_pairs_only_while_what_confirmed_it_still_vouches(client, owner, keys):
    # held at pairing too, whatever voided the ticket or not, as a session's pairing is (link_asks._paired): the passkey
    # that confirmed it still the account's, and while high security is on, still counted a hardware key
    gone = made_signer_ticket(client, owner, keys["phone"])
    with store.connect() as db:                                 # removed by a path that voids no ticket
        db.execute("DELETE FROM passkeys WHERE name = 'iPhone'")
    r = signer_pairs(client, gone, "gone")
    assert (r.status_code, r.json()["detail"]) == (403, "The passkey that confirmed this ticket was removed: make a new one.")
    vouched = made_signer_ticket(client, owner, keys["a"])
    on(client, owner, keys)
    with store.connect() as db:                                 # judged otherwise since (its model reported compromised)
        db.execute("UPDATE passkeys SET tier = 'device' WHERE name = 'YubiKey A'")
    r = signer_pairs(client, vouched, "a")
    assert (r.status_code, r.json()["detail"]) == (403, link.TICKET_NOT_HARDWARE)
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = 'hardware' WHERE name = 'YubiKey A'")
    assert signer_pairs(client, vouched, "a").status_code == 200    # refused, it was left unused


def test_a_signer_ticket_no_hardware_key_confirmed_pairs_nothing_once_its_key_is_counted_one(client, owner, keys):
    # high security's rule at pairing, as a session's (link_asks._paired): what confirmed the ticket was no hardware key
    by_phone = made_signer_ticket(client, owner, keys["phone"])
    with store.connect() as db:                                 # turned on by a path that voids no ticket, and the
        db.execute("UPDATE users SET high_security = 1")         # phone's passkey judged a hardware key since
        db.execute("UPDATE passkeys SET tier = 'hardware' WHERE name = 'iPhone'")
    r = signer_pairs(client, by_phone, "phone")
    assert (r.status_code, r.json()["detail"]) == (403, link.TICKET_NOT_HARDWARE)


def test_voiding_leaves_a_used_ticket_so_its_cancelled_pairing_still_names_it(client, owner, keys):
    # link.void_tickets voids unused tickets alone: a ticket a pairing used stays, so that pairing, which removing the
    # passkey that confirmed it cancels, still names its ticket to its session (link_asks._answer), by which a pair run
    # again knows the pairing is its own
    made = made_ticket(client, owner, keys["phone"])
    s = Session(client)
    assert s.pair(made, name="mac").json()["status"] == "approved"          # its pairing waits on its hold
    assert remove_key(client, owner, "iPhone").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE ade_link_asks SET last_poll_at = NULL")
    answer = s.call("GET", "/ask").json()
    assert answer["status"] == "cancelled" and answer["ticket"] == link._ticket_hash(made)[:32]


# ---- what was begun or signed before it was turned on -----------------------------------------------------------------

def test_a_registration_begun_with_another_word_before_it_was_turned_on_adds_nothing_after(client, owner, keys, maker):
    # options given while it was off, with the password or the phone's passkey: the key they would add could be a
    # hardware key of whoever holds the password, so once it is on they add nothing
    begun = [client.post("/api/account/passkeys/options", headers=owner,
                         json={"confirmation": word(client, owner, device, change="passkey-add")}).json()
             for device in (None, keys["phone"])]
    on(client, owner, keys)
    for options in begun:
        r = client.post("/api/account/passkeys", headers=owner, json={
            "challenge_id": options["challenge_id"], "name": "theirs", "credential": HardwareKey(maker).create(options)})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE
    assert "theirs" not in ids(client, owner)
    # options a hardware key's word gave add one, while it is on
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"confirmation": word(client, owner, keys["b"], change="passkey-add")}).json()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "spare", "credential": HardwareKey(maker).create(options)})
    assert r.status_code == 200 and r.json()["tier"] == "hardware"


def test_it_is_turned_on_only_once_no_passkey_waits_to_be_judged(client, owner, keys, maker):
    # a key added while no valid blob is here is judged later, and could become a hardware key then, without a
    # hardware key's word
    os.unlink(fido_mds.path())
    fido_mds.forget()
    assert add_hardware(client, owner, maker, name="late")[1].json()["judge_again"] is True
    given = word(client, owner, keys["a"])
    r = turn(client, owner, True, given)
    assert r.status_code == 409 and r.json()["detail"] == passkeys.KEY_WAITING
    assert remove_key(client, owner, "late").status_code == 200
    assert turn(client, owner, True, given).json()["high_security"] is True     # the refusal left it unused


def test_turning_it_on_withdraws_the_commands_other_passkeys_signed(client, owner, keys):
    session = paired(client, owner)
    by_phone = send(client, owner, keys["phone"], payload(client, owner, "instruction", session.link_id, "phone"))
    by_key = send(client, owner, keys["a"], payload(client, owner, "instruction", session.link_id, "key"))
    assert by_phone.status_code == 200 and by_key.status_code == 200
    done = on(client, owner, keys)
    assert (done["withdrawn"], done["stopped"]) == (1, [])
    r = take(session, by_phone.json()["seq"])
    assert r.status_code == 409 and r.json()["why"] == "withdrawn"
    assert take(session, by_key.json()["seq"]).status_code == 200


def test_turning_it_on_ends_the_holds_of_the_moves_other_passkeys_signed(client, owner, keys, workspace, monkeypatch):
    # a move waits on its hold before its link may take it: turned on meanwhile, a move a device passkey signed is
    # never stored, as a command it signed is withdrawn, and one a hardware key signed goes on
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    mac = paired(client, owner)
    lists(mac, ("by-phone", False), ("by-key", False))
    held = {}
    for session, device in (("by-phone", keys["phone"]), ("by-key", keys["a"])):
        r = send(client, owner, device, move_payload(client, owner, mac.link_id, session))
        assert r.status_code == 200, r.text
        held[session] = r.json()["held"]
    t[0] += 10
    assert on(client, owner, keys)["withdrawn"] == 1                 # the held move ended counts as withdrawn
    with store.connect() as db:
        ended, going = holds.get(db, held["by-phone"]["id"]), holds.get(db, held["by-key"]["id"])
    # its words say why: the passkey is still on the account, so "removed" would be false
    assert (ended["done_at"], ended["failed"]) == (t[0], moves.HIGH_SECURITY) and going["done_at"] is None
    t[0] = held["by-key"]["takes_effect_at"]
    holds.settle()
    with store.connect() as db:
        stored = [r["session_id"] for r in db.execute("SELECT session_id FROM ade_link_moves ORDER BY seq")]
    assert stored == ["by-key"]


def test_turning_it_on_fails_a_stored_move_another_passkey_signed_saying_why(client, owner, keys, workspace,
                                                                              monkeypatch):
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    mac = paired(client, owner)
    lists(mac, ("by-phone", False))
    r = send(client, owner, keys["phone"], move_payload(client, owner, mac.link_id, "by-phone"))
    assert r.status_code == 200, r.text
    t[0] = r.json()["held"]["takes_effect_at"]
    holds.settle()                                                  # stored: asked, not taken
    assert on(client, owner, keys)["withdrawn"] == 1
    with store.connect() as db:
        move = db.execute("SELECT state, reason FROM ade_link_moves WHERE session_id = 'by-phone'").fetchone()
    assert (move["state"], move["reason"]) == ("failed", moves.HIGH_SECURITY)


def test_removing_a_passkey_ends_its_held_moves_saying_so_and_counts_them(client, owner, keys, workspace, monkeypatch):
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    mac = paired(client, owner)
    lists(mac, ("by-phone", False))
    r = send(client, owner, keys["phone"], move_payload(client, owner, mac.link_id, "by-phone"))
    assert r.status_code == 200, r.text
    t[0] += 10
    removed = remove_key(client, owner, "iPhone")
    assert removed.status_code == 200 and removed.json()["withdrawn"] == 1, removed.text
    with store.connect() as db:
        assert holds.get(db, r.json()["held"]["id"])["failed"] == moves.WITHDRAWN


def _resumed_by(client, owner, device, name):
    """A session linked, stopped, and resumed with `device`'s signature: the link, its resume stored."""
    session = paired(client, owner, name=name)
    t = client.post(f"/api/ade/link/links/{session.link_id}/stop", headers=owner).json()["stopped_at"]
    r = send(client, owner, device, payload(client, owner, "resume", session.link_id, created_at=t + 1,
                                            expires_at=t + 601), path=f"/api/ade/link/links/{session.link_id}/resume")
    assert r.status_code == 200, r.text
    return session


def test_a_resume_however_old_is_undone_when_its_passkey_is_removed(client, owner, keys, monkeypatch):
    # a resume never expires for the stop it was made for (a session may see it at its next tool call, any time)
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    session = _resumed_by(client, owner, keys["phone"], "phone")
    t[0] += 700                                                     # past the resume's ten minutes
    removed = remove_key(client, owner, "iPhone")
    assert removed.status_code == 200 and removed.json()["stopped"] == [session.link_id], removed.text
    state = session.call("GET", "/state").json()
    assert state["stopped"] is True and state["resume"] is None


def test_a_resume_however_old_is_undone_when_high_security_is_turned_on(client, owner, keys, monkeypatch):
    t = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: t[0])
    phone, key = _resumed_by(client, owner, keys["phone"], "phone"), _resumed_by(client, owner, keys["b"], "key")
    t[0] += 700
    assert on(client, owner, keys)["stopped"] == [phone.link_id]
    assert phone.call("GET", "/state").json()["stopped"] is True
    assert key.call("GET", "/state").json()["stopped"] is False


def test_turning_it_on_stops_again_a_session_another_passkey_resumed(client, owner, keys):
    resumed = {}
    for name, device in (("phone", keys["phone"]), ("key", keys["b"])):
        session = paired(client, owner, name=name)
        t = client.post(f"/api/ade/link/links/{session.link_id}/stop", headers=owner).json()["stopped_at"]
        r = send(client, owner, device, payload(client, owner, "resume", session.link_id, created_at=t + 1,
                                                expires_at=t + 601), path=f"/api/ade/link/links/{session.link_id}/resume")
        assert r.status_code == 200, r.text
        resumed[name] = session
    assert on(client, owner, keys)["stopped"] == [resumed["phone"].link_id]
    assert resumed["phone"].call("GET", "/state").json()["stopped"] is True     # to be resumed with a hardware key
    assert resumed["key"].call("GET", "/state").json()["stopped"] is False


def test_turning_it_on_cancels_a_link_approval_still_held_and_leaves_one_that_took_effect(client, owner, keys, at):
    # approved with the password, as high security off allows: a hold records no passkey, so none may take effect after
    early, _ = ask(client, address(client, owner), name="early", kind="local")
    first = approve(client, owner, asks(client, owner)[0]["id"]).json()["hold"]
    at[0] += 100
    late, _ = ask(client, address(client, owner), name="late", kind="local")
    second = approve(client, owner, [a for a in asks(client, owner) if a["name"] == "late"][0]["id"]).json()["hold"]
    at[0] = first["takes_effect_at"]                                # due, though nothing has settled it yet
    assert on(client, owner, keys)["cancelled"] == [second["id"]]
    with store.connect() as db:                                     # settled first: it had taken effect
        assert holds.get(db, first["id"])["done_at"] == first["takes_effect_at"]
    assert poll(early).json()["status"] == "linked"
    assert poll(late).json()["status"] == "cancelled"
    at[0] = second["takes_effect_at"] + 60
    assert poll(late).json()["status"] == "cancelled" and [r["name"] for r in links()] == ["early"]


# ---- a report published later against the model of the account's hardware keys --------------------------------------

def test_a_report_against_every_hardware_key_of_a_high_security_account_is_told_and_locks_nothing(client, owner, keys,
                                                                                                 metadata, maker, told):
    on(client, owner, keys)
    metadata.publish([reported_entry(maker)], no=2)
    assert passkeys.reconsider() == 0                               # none made a device passkey: none would be left
    got = {k["name"]: (k["tier"], k["reported"]) for k in client.get("/api/account/passkeys", headers=owner).json()[
        "passkeys"]}
    assert got == {"YubiKey A": ("hardware", COMPROMISED), "YubiKey B": ("hardware", COMPROMISED),
                   "iPhone": ("device", None)}
    # told in fixed words, once for the account however many of its keys; Account says each is
    # kept, and to add another hardware key and remove it
    assert [t for _, t, _ in told] == ["A hardware key was reported"]
    r = turn(client, owner, False, word(client, owner, keys["a"], change="high-security-off"))  # not locked out
    assert r.status_code == 200, r.text
    metadata.publish([reported_entry(maker)], no=3)                 # the next blob, high security off: made so
    assert passkeys.reconsider() == 2
    assert {(k["tier"], k["reported"]) for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]} \
        == {("device", None)}


def test_while_another_hardware_key_is_free_of_reports_the_reported_ones_become_device_passkeys(client, owner, keys,
                                                                                                 metadata, maker, told):
    other = Maker(model=uuid.UUID("5ca1ab1e-0000-4000-8000-000000000001"))
    metadata.publish([maker.entry(), other.entry()])
    add_hardware(client, owner, other, name="Other key", confirm_with=keys["a"])
    on(client, owner, keys)
    metadata.publish([reported_entry(maker), other.entry()], no=2)
    assert passkeys.reconsider() == 2
    got = {k["name"]: k["tier"] for k in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]}
    assert got == {"YubiKey A": "device", "YubiKey B": "device", "iPhone": "device", "Other key": "hardware"}
    assert [t for _, t, _ in told] == ["A hardware key was reported"]          # once for the account
