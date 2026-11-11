"""Passkeys' names, the server's half: a passkey given no name is named by what it is,
from what the server read as it was added (the FIDO metadata's name for its model where its attestation chained to that
model's root, else its backup flags, else the browser's word on the authenticator that made it); a passkey can be
renamed; and what the page lists, on Account and in Encryption, carries its kind."""

import json
import uuid

import pytest

from conftest import signed_up
from fido_fixtures import HardwareKey, Maker, add_hardware
from fido_fixtures import maker, metadata, no_metadata  # noqa: F401  (fixtures)
from proofpane_open import passkeys, store
from proofpane_open.ade import holds, push
from test_ade_e2e import Keys, passkey_of, rows, view
from test_hardware_keys import reported_entry
from test_passkeys import Authenticator, confirm, confirmed, options_for

BE, BS = 0x08, 0x10                         # the backup-eligible and backup-state flags of authenticator data


@pytest.fixture
def owner(client):
    return signed_up(client)


def add_from(client, headers, device, attachment=None, name="", confirm_with=None, say=True, **kw):
    """Add `device` as the page does: its answer carries the browser's word on the authenticator that made it
    (authenticatorAttachment, as a credential's JSON carries it), unless `say` is False."""
    options = options_for(client, headers, confirm_with)
    credential = device.create(options, **kw)
    if say:
        credential["authenticatorAttachment"] = attachment
    return client.post("/api/account/passkeys", headers=headers,
                       json={"challenge_id": options["challenge_id"], "name": name, "credential": credential})


def listed(client, headers):
    return client.get("/api/account/passkeys", headers=headers).json()["passkeys"]


def stored(passkey_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM passkeys WHERE id = ?", (passkey_id,)).fetchone())


# ---- named by what it is ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("flags,attachment,name,kind", [
    (0x05, "platform", "Device-bound passkey", "device"),
    (0x05, None, "Device-bound passkey", "device"),                 # the browser said nothing of it
    (0x05, "cross-platform", "Security key", "security-key"),
    (0x05 | BE, "platform", "Synced passkey", "synced"),
    (0x05 | BE | BS, "platform", "Synced passkey", "synced"),
    (0x05 | BE | BS, "cross-platform", "Synced passkey", "synced"),   # a phone's synced passkey, reached by its code
])
def test_a_passkey_given_no_name_is_named_by_its_backup_flags_and_the_browser_s_word(client, owner, flags,
                                                                                     attachment, name, kind):
    r = add_from(client, owner, Authenticator(), attachment=attachment, flags=flags)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == name and r.json().get("kind") == kind
    (k,) = listed(client, owner)
    assert (k["name"], k.get("kind")) == (name, kind)
    assert stored(k["id"]).get("attachment", "no column") == attachment


@pytest.mark.parametrize("said", ["usb", "", 7, True, ["cross-platform"], {"x": 1}, "Cross-Platform", "platform " * 4])
def test_an_attachment_that_is_not_one_of_webauthn_s_two_is_passed_over(client, owner, said):
    r = add_from(client, owner, Authenticator(), attachment=said)
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json().get("kind")) == ("Device-bound passkey", "device")
    assert stored(listed(client, owner)[0]["id"]).get("attachment", "no column") is None


def test_an_answer_with_no_attachment_member_at_all_is_a_device_bound_passkey(client, owner):
    r = add_from(client, owner, Authenticator(), say=False)
    assert r.status_code == 200 and (r.json()["name"], r.json().get("kind")) == ("Device-bound passkey", "device")


def test_a_hardware_key_given_no_name_is_named_by_its_model_as_the_fido_metadata_names_it(client, owner, metadata):
    r = add_hardware(client, owner, metadata.maker, name="")[1]
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json().get("kind"), r.json()["tier"]) == ("Test Key 5 NFC", "hardware", "hardware")
    (k,) = listed(client, owner)
    assert (k["name"], k.get("kind")) == ("Test Key 5 NFC", "hardware")


def test_a_model_s_name_longer_than_a_passkey_s_is_cut_to_it(client, owner, metadata):
    # the metadata keeps a maker's description to 120 characters (hardware_keys.MAX_MAKER), a name to 60 (MAX_NAME)
    metadata.publish([metadata.maker.entry(description="Test Key " + "x" * 100)])
    r = add_hardware(client, owner, metadata.maker, name="")[1]
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert len(name) == passkeys.MAX_NAME and name.endswith("…") and name.startswith("Test Key xxx")
    # and a name so cut is one a rename takes back as it is
    (k,) = listed(client, owner)
    assert rename(client, owner, k["id"], name).status_code == 200


def test_a_key_whose_model_was_proven_but_is_no_hardware_key_is_named_by_its_model(client, owner, metadata):
    # its attestation chains to the model's root, but the metadata lists no hmac-secret: a device passkey, of that model
    metadata.publish([metadata.maker.entry(extensions=("credProtect",))])
    r = add_hardware(client, owner, metadata.maker, name="")[1]
    assert r.status_code == 200, r.text
    assert (r.json()["tier"], r.json()["name"]) == ("device", "Test Key 5 NFC")


def test_a_key_whose_attestation_does_not_chain_to_its_model_s_root_is_not_named_by_that_model(client, owner,
                                                                                               metadata):
    # it says it is the model, and the metadata names the model, but its attestation was made under another root: the
    # name it claims is unproven, so it is named as any security key the browser reported
    metadata.publish([metadata.maker.entry(roots=[Maker().root])])
    key, r = add_hardware(client, owner, metadata.maker, name="")
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "device" and r.json()["name"] == "Device-bound passkey"
    assert stored(listed(client, owner)[0]["id"])["maker"] == "Test Key 5 NFC"     # kept as before, never shown


def test_a_key_added_while_no_metadata_is_here_is_named_without_a_model(client, owner, no_metadata):
    r = add_hardware(client, owner, no_metadata.maker, name="")[1]
    assert r.status_code == 200, r.text
    assert (r.json()["tier"], r.json()["judge_again"], r.json()["name"]) == ("device", True, "Device-bound passkey")


@pytest.mark.parametrize("flags,attachment", [(0x05, "cross-platform"), (0x05 | BE, "platform")])
def test_a_name_the_person_gave_is_kept_whatever_the_passkey_is(client, owner, flags, attachment):
    r = add_from(client, owner, Authenticator(), attachment=attachment, name="  Ana's key  ", flags=flags)
    assert r.status_code == 200 and r.json()["name"] == "Ana's key"


def test_a_hardware_key_s_given_name_is_kept_over_its_model(client, owner, metadata):
    r = add_hardware(client, owner, metadata.maker, name="Blue key")[1]
    assert (r.json()["name"], r.json().get("kind")) == ("Blue key", "hardware")


# ---- what the page lists ---------------------------------------------------------------------------------------

def test_the_list_carries_each_passkey_s_kind_and_a_row_from_before_the_attachment_was_kept(client, owner, metadata):
    add_from(client, owner, Authenticator(), attachment="cross-platform", name="Key A")
    add_from(client, owner, Authenticator(alg=-8), attachment="platform", name="Phone", flags=0x05 | BE | BS)
    add_hardware(client, owner, metadata.maker, name="Yubi")
    kinds = {k["name"]: k.get("kind") for k in listed(client, owner)}
    assert kinds == {"Key A": "security-key", "Phone": "synced", "Yubi": "hardware"}
    # a row an older release kept has no attachment and maybe no backup-eligible flag: the browser's word was never
    # kept, so a key not shown to be synced is a device-bound passkey, never called a security key on no word
    with store.connect() as db:
        db.execute("UPDATE passkeys SET attachment = NULL, backup_eligible = NULL WHERE name = 'Key A'")
        db.execute("UPDATE passkeys SET backup_eligible = NULL WHERE name = 'Phone'")
    kinds = {k["name"]: k.get("kind") for k in listed(client, owner)}
    assert kinds == {"Key A": "device", "Phone": "synced", "Yubi": "hardware"}       # backed up, so synced


def test_encryption_s_view_carries_each_passkey_s_kind_and_its_model(client, owner, metadata):
    add_from(client, owner, Authenticator(), attachment="cross-platform", name="Key A")
    add_hardware(client, owner, metadata.maker, name="Yubi")
    got = {p["name"]: p for p in view(client, owner)["passkeys"]}
    assert (got["Key A"].get("kind"), got["Key A"].get("maker", "unserved")) == ("security-key", None)
    assert (got["Yubi"].get("kind"), got["Yubi"].get("maker")) == ("hardware", "Test Key 5 NFC")


def test_a_device_passkey_s_model_is_not_served_to_encryption_unless_proven(client, owner, metadata):
    # the same unproven claim as above: a model is served where its attestation proved it, whatever the tier
    metadata.publish([metadata.maker.entry(roots=[Maker().root])])
    add_hardware(client, owner, metadata.maker, name="Claims a model")
    (p,) = view(client, owner)["passkeys"]
    assert (p.get("kind"), p.get("maker", "unserved")) == ("device", None)


# ---- renaming ----------------------------------------------------------------------------------------------------

def word_for_rename(client, headers, passkey_id, device=None):
    """The confirmation the page gets to rename `passkey_id`: the password's, or `device`'s; None where the server
    makes none for a rename."""
    r = confirm(client, headers, device, change=f"passkey-rename:{passkey_id}")
    return r.json().get("confirmation") if r.status_code == 200 else None


def rename(client, headers, passkey_id, name, confirm_with=None):
    """Rename as the page does: with the person's word, made for this passkey's rename."""
    return client.patch(f"/api/account/passkeys/{passkey_id}", headers=headers,
                        json={"name": name, "confirmation": word_for_rename(client, headers, passkey_id,
                                                                            confirm_with)})


def test_a_passkey_is_renamed_with_the_person_s_word_and_keeps_what_it_is(client, owner):
    add_from(client, owner, Authenticator(), attachment="cross-platform")
    (k,) = listed(client, owner)
    before = stored(k["id"])
    r = rename(client, owner, k["id"], "  Blue YubiKey ")
    assert r.status_code == 200, r.text
    assert (r.json().get("id"), r.json().get("name"), r.json().get("kind")) == (k["id"], "Blue YubiKey", "security-key")
    (again,) = listed(client, owner)
    assert again["name"] == "Blue YubiKey"
    after = stored(k["id"])
    named = ("name", "name_by_server")                 # the server named it; the person has named it now
    assert {c: v for c, v in after.items() if c not in named} == {c: v for c, v in before.items() if c not in named}
    assert (before.get("name_by_server"), after.get("name_by_server")) == (1, 0)


def test_renaming_with_no_name_names_it_by_what_it_is_again(client, owner, metadata):
    add_hardware(client, owner, metadata.maker, name="Mine")
    add_from(client, owner, Authenticator(), attachment="platform", name="Old", flags=0x05 | BE)
    ids = {k["name"]: k["id"] for k in listed(client, owner)}
    assert rename(client, owner, ids["Mine"], "").json().get("name") == "Test Key 5 NFC"
    assert rename(client, owner, ids["Old"], "   ").json().get("name") == "Synced passkey"


@pytest.mark.parametrize("name", ["a" * 61, "x" + chr(0x202E) + "y", "tab\tname", "line\nbreak"])
def test_a_new_name_is_short_text(client, owner, name):
    add_from(client, owner, Authenticator(), name="Kept")
    (k,) = listed(client, owner)
    r = rename(client, owner, k["id"], name)
    assert r.status_code == 400
    assert listed(client, owner)[0]["name"] == "Kept"


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": 7}, {"name": ["x"]}])
def test_a_rename_without_a_name_in_text_is_refused(client, owner, body):
    add_from(client, owner, Authenticator(), name="Kept")
    (k,) = listed(client, owner)
    r = client.patch(f"/api/account/passkeys/{k['id']}", headers=owner, json=body)
    assert r.status_code in (400, 422)
    assert listed(client, owner)[0]["name"] == "Kept"


def test_one_account_cannot_rename_another_s_passkey_and_renaming_needs_the_sign_in(client, owner):
    add_from(client, owner, Authenticator(), name="Kept")
    (k,) = listed(client, owner)
    other = signed_up(client, "other@example.com")
    assert rename(client, other, k["id"], "Mine now").status_code == 404
    assert client.patch(f"/api/account/passkeys/{k['id']}", json={"name": "x"}).status_code == 401
    assert rename(client, owner, uuid.uuid4().hex, "x").status_code == 404
    assert listed(client, owner)[0]["name"] == "Kept"


# ---- while end-to-end encryption is on ---------------------------------------------------------------------------

def test_a_passkey_added_while_encryption_is_on_is_named_when_it_waits_and_when_its_hold_ends(client, owner, monkeypatch):
    first = Authenticator()
    add_from(client, owner, first, attachment="platform", name="First")
    keys = Keys(client, owner, first)
    keys.turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, first, change="passkey-add")).json()
    new = Authenticator(alg=-8)
    credential = dict(new.create(options), authenticatorAttachment="cross-platform")
    r = client.post("/api/account/passkeys", headers=owner,
                    json={"challenge_id": options["challenge_id"], "name": "", "credential": credential})
    assert r.status_code == 200, r.text
    hold = r.json()["held"]
    assert "“Security key”" in hold["summary"]
    holds.settle(keys.account, now=hold["takes_effect_at"])
    got = {k["name"]: k for k in listed(client, owner)}
    assert got.get("Security key", {}).get("kind") == "security-key"
    assert stored(passkey_of(client, owner, new)["id"]).get("attachment", "no column") == "cross-platform"


def test_a_passkey_held_by_a_server_from_before_the_attachment_was_kept_is_added_when_its_hold_ends(client, owner):
    # a hold an older release placed keeps the passkey's values without the attachment: its end adds it all the same
    first = Authenticator()
    add_from(client, owner, first, attachment="platform", name="First")
    keys = Keys(client, owner, first)
    keys.turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, first, change="passkey-add")).json()
    new = Authenticator(alg=-257)
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": "Held", "credential": new.create(options)})
    hold = r.json()["held"]
    (pending,) = rows("ade_e2e_pending")
    body = json.loads(pending["body"])
    body["passkey"].pop("attachment", None)
    with store.connect() as db:
        db.execute("UPDATE ade_e2e_pending SET body = ? WHERE hold_id = ?", (json.dumps(body), hold["id"]))
    assert holds.settle(keys.account, now=hold["takes_effect_at"]) == [hold["id"]]
    got = {k["name"]: k for k in listed(client, owner)}
    assert got["Held"].get("kind") == "device" and stored(got["Held"]["id"]).get("attachment", "no column") is None


def fresh(alg=-7):
    """A software passkey with a credential id of its own (an Authenticator's is the same for each of its kind)."""
    device = Authenticator(alg)
    device.credential_id = uuid.uuid4().bytes + uuid.uuid4().bytes
    return device


# ---- a rename takes the person's word ----------------------------------------------------------------------------

def patch_name(client, headers, passkey_id, name, confirmation=None):
    return client.patch(f"/api/account/passkeys/{passkey_id}", headers=headers,
                        json={"name": name, "confirmation": confirmation})


def test_a_rename_without_the_person_s_word_is_refused_and_the_name_stays(client, owner):
    # a stolen sign-in alone must not swap the names the owner removes and revokes by
    add_from(client, owner, fresh(), name="MacBook")
    (k,) = listed(client, owner)
    r = patch_name(client, owner, k["id"], "iPhone")
    assert r.status_code == 403
    assert r.json()["detail"] == passkeys.NEEDS_CONFIRM
    assert listed(client, owner)[0]["name"] == "MacBook"


def test_a_rename_spends_only_a_confirmation_made_for_that_passkey_s_rename_and_once(client, owner):
    add_from(client, owner, fresh(), name="MacBook")
    add_from(client, owner, fresh(), name="iPhone")
    ids = {k["name"]: k["id"] for k in listed(client, owner)}
    for other in (f"passkey-remove:{ids['MacBook']}", "passkey-add"):
        made = confirmed(client, owner, change=other)["confirmation"]
        assert patch_name(client, owner, ids["MacBook"], "Phone", made).status_code == 403, other
    made = word_for_rename(client, owner, ids["iPhone"])
    assert patch_name(client, owner, ids["MacBook"], "Phone", made).status_code == 403
    word = word_for_rename(client, owner, ids["MacBook"])
    assert patch_name(client, owner, ids["MacBook"], "Old laptop", word).status_code == 200
    assert patch_name(client, owner, ids["MacBook"], "Again", word).status_code == 403          # used once
    assert {k["name"] for k in listed(client, owner)} == {"Old laptop", "iPhone"}


def test_a_refused_rename_leaves_its_confirmation_unused(client, owner):
    add_from(client, owner, fresh(), name="Kept")
    (k,) = listed(client, owner)
    word = word_for_rename(client, owner, k["id"])
    assert patch_name(client, owner, k["id"], "a" * 61, word).status_code == 400
    assert patch_name(client, owner, k["id"], "Fine", word).status_code == 200
    assert patch_name(client, owner, k["id"], "Finer", word).status_code == 403


def test_another_account_s_rename_of_a_passkey_not_its_own_is_not_found_and_spends_nothing(client, owner):
    add_from(client, owner, fresh(), name="Kept")
    (k,) = listed(client, owner)
    other = signed_up(client, "other@example.com")
    add_from(client, other, fresh(), name="Theirs")
    (theirs,) = listed(client, other)
    word = word_for_rename(client, other, k["id"])
    assert patch_name(client, other, k["id"], "Mine now", word).status_code == 404
    assert listed(client, owner)[0]["name"] == "Kept"
    # made for that id, it renames nothing of the other account's either
    assert patch_name(client, other, theirs["id"], "x", word).status_code == 403


def test_while_encryption_is_on_a_rename_takes_a_passkey_s_word_never_the_password_s(client, owner):
    first = fresh()
    add_from(client, owner, first, attachment="platform", name="First")
    Keys(client, owner, first).turn_on()
    (k,) = listed(client, owner)
    r = patch_name(client, owner, k["id"], "Renamed", word_for_rename(client, owner, k["id"]))
    assert r.status_code == 403
    assert r.json()["detail"] == passkeys.NEEDS_PASSKEY_ALONE
    assert rename(client, owner, k["id"], "Renamed", confirm_with=first).status_code == 200
    assert listed(client, owner)[0]["name"] == "Renamed"


def test_under_high_security_a_rename_takes_a_hardware_key_s_word(client, owner, metadata):
    key = add_hardware(client, owner, metadata.maker, name="YubiKey")[0]
    phone = Authenticator(-8)
    add_from(client, owner, phone, name="Phone")
    on = client.put("/api/account/high-security", headers=owner,
                    json={"on": True, **confirmed(client, owner, key, change="high-security-on")})
    assert on.status_code == 200, on.text
    ids = {k["name"]: k["id"] for k in listed(client, owner)}
    r = patch_name(client, owner, ids["Phone"], "Old phone")
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_HARDWARE
    assert rename(client, owner, ids["Phone"], "Old phone", confirm_with=key).status_code == 200


def test_a_rename_is_told_to_the_account_s_devices_in_words_that_name_nothing_of_it(client, owner, monkeypatch):
    told = []
    monkeypatch.setattr(push, "notice", lambda user_id, message, now=None, post=None: told.append(message) or 1)
    add_from(client, owner, fresh(), name="Zq7 laptop")
    (k,) = listed(client, owner)
    told.clear()
    assert rename(client, owner, k["id"], "Zq7 phone").status_code == 200
    assert [(m.title, m.body, m.url) for m in told] == [
        ("A passkey of your account was renamed", "Not you? Open Account and sign out everywhere.", "/app/#account")]
    # a refused rename tells nothing
    told.clear()
    assert patch_name(client, owner, k["id"], "Zq7 other").status_code == 403
    assert told == []


# ---- the held answer says what the passkey is --------------------------------------------------------------------

def test_a_passkey_held_while_encryption_is_on_says_its_kind_as_it_waits(client, owner):
    first = fresh()
    add_from(client, owner, first, attachment="platform", name="First")
    Keys(client, owner, first).turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, first, change="passkey-add")).json()
    credential = dict(fresh(alg=-8).create(options, flags=0x05 | BE | BS), authenticatorAttachment="platform")
    r = client.post("/api/account/passkeys", headers=owner,
                    json={"challenge_id": options["challenge_id"], "name": "", "credential": credential})
    assert r.status_code == 200 and "held" in r.json(), r.text
    assert r.json().get("kind") == "synced"


# ---- a name given by kind follows what the key is found to be ----------------------------------------------------

def test_a_name_given_by_kind_follows_the_key_once_the_metadata_comes(client, owner, no_metadata):
    add_from(client, owner, HardwareKey(no_metadata.maker), attachment="cross-platform", name="")
    add_from(client, owner, HardwareKey(no_metadata.maker), attachment="cross-platform", name="Mine")
    assert sorted(k["name"] for k in listed(client, owner)) == ["Mine", "Security key"]
    no_metadata.publish()
    got = {k["name"]: k for k in listed(client, owner)}
    assert set(got) == {"Test Key 5 NFC", "Mine"}
    assert got["Test Key 5 NFC"]["tier"] == got["Mine"]["tier"] == "hardware"


def test_a_name_given_by_its_model_follows_the_key_when_the_model_is_reported_later(client, owner, metadata, maker):
    add_from(client, owner, HardwareKey(maker), attachment="cross-platform", name="")
    add_from(client, owner, HardwareKey(maker), attachment="cross-platform", name="Mine")
    assert sorted(k["name"] for k in listed(client, owner)) == ["Mine", "Test Key 5 NFC"]
    metadata.publish([reported_entry(maker)], no=2)
    got = {k["name"]: k for k in listed(client, owner)}
    # a compromised attestation key proves nothing of the model: no longer named by it
    assert set(got) == {"Security key", "Mine"}
    assert got["Security key"]["tier"] == got["Mine"]["tier"] == "device"


def test_a_model_reported_since_with_a_weakness_and_as_proving_nothing_names_nothing(client, owner, metadata, maker):
    add_from(client, owner, HardwareKey(maker), attachment="cross-platform", name="")
    entry = maker.entry()
    entry["statusReports"] += [{"status": "USER_VERIFICATION_BYPASS", "effectiveDate": "2026-08-01"},
                               {"status": "ATTESTATION_KEY_COMPROMISE", "effectiveDate": "2026-09-01"}]
    metadata.publish([entry], no=2)
    (k,) = listed(client, owner)
    assert (k["tier"], k["name"]) == ("device", "Security key")
    assert k["why"] == "The FIDO metadata has since reported its model with its attestation key compromised."


def test_a_rename_to_no_name_lets_the_name_follow_the_key_and_a_given_name_stops_it(client, owner, no_metadata):
    add_from(client, owner, HardwareKey(no_metadata.maker), attachment="cross-platform", name="Given first")
    add_from(client, owner, HardwareKey(no_metadata.maker), attachment="cross-platform", name="")
    ids = {k["name"]: k["id"] for k in listed(client, owner)}
    assert rename(client, owner, ids["Given first"], "").json().get("name") == "Security key 2"
    assert rename(client, owner, ids["Security key"], "Given later").status_code == 200
    no_metadata.publish()
    assert sorted(k["name"] for k in listed(client, owner)) == ["Given later", "Test Key 5 NFC"]


# ---- the ways to confirm say whether encryption is on ------------------------------------------------------------

def test_the_ways_to_confirm_say_whether_end_to_end_encryption_is_on(client, owner):
    first = fresh()
    add_from(client, owner, first, attachment="platform", name="First")
    assert client.get("/api/account/confirm", headers=owner).json().get("e2e") is False
    Keys(client, owner, first).turn_on()
    assert client.get("/api/account/confirm", headers=owner).json().get("e2e") is True


# ---- two passkeys named by one kind are told apart ---------------------------------------------------------------

def test_passkeys_named_by_one_kind_are_told_apart_by_a_number(client, owner):
    for _ in range(3):
        add_from(client, owner, fresh(), attachment="platform", flags=0x05 | BE)
    assert [k["name"] for k in listed(client, owner)] == ["Synced passkey", "Synced passkey 2", "Synced passkey 3"]


def test_the_add_s_answer_names_the_passkey_as_it_is_kept(client, owner):
    add_from(client, owner, fresh(), attachment="platform", flags=0x05 | BE)
    r = add_from(client, owner, fresh(), attachment="platform", flags=0x05 | BE)
    assert r.status_code == 200, r.text
    assert r.json().get("name") == "Synced passkey 2"


def test_a_passkey_held_while_its_name_is_taken_is_numbered_when_its_hold_ends(client, owner):
    first = fresh()
    add_from(client, owner, first, attachment="platform", name="First")
    keys = Keys(client, owner, first)
    keys.turn_on()
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json=confirmed(client, owner, first, change="passkey-add")).json()
    new = fresh(alg=-8)
    credential = dict(new.create(options), authenticatorAttachment="cross-platform")
    r = client.post("/api/account/passkeys", headers=owner,
                    json={"challenge_id": options["challenge_id"], "name": "", "credential": credential})
    hold = r.json()["held"]
    (k,) = listed(client, owner)
    assert rename(client, owner, k["id"], "Security key", confirm_with=first).status_code == 200
    holds.settle(keys.account, now=hold["takes_effect_at"])
    assert sorted(p["name"] for p in listed(client, owner)) == ["Security key", "Security key 2"]


def test_a_name_the_person_gave_is_not_taken_by_one_named_by_kind(client, owner):
    add_from(client, owner, fresh(), attachment="cross-platform", name="Security key")
    add_from(client, owner, fresh(), attachment="cross-platform")
    assert [k["name"] for k in listed(client, owner)] == ["Security key", "Security key 2"]


def test_renaming_to_no_name_numbers_it_against_the_others_and_never_against_itself(client, owner):
    add_from(client, owner, fresh(), attachment="cross-platform")
    add_from(client, owner, fresh(), attachment="cross-platform", name="Spare")
    ids = {k["name"]: k["id"] for k in listed(client, owner)}
    assert rename(client, owner, ids["Security key"], "").json().get("name") == "Security key"
    assert rename(client, owner, ids["Spare"], "").json().get("name") == "Security key 2"


def test_a_model_s_name_cut_to_length_keeps_its_number_within_a_name_s_length(client, owner, metadata, maker):
    metadata.publish([maker.entry(description="Test Key " + "x" * 100)])
    for _ in range(2):
        add_hardware(client, owner, maker, name="")
    names = [k["name"] for k in listed(client, owner)]
    assert len(set(names)) == 2 and all(len(n) <= passkeys.MAX_NAME for n in names), names
    assert names[1].endswith("… 2") and names[1].startswith("Test Key xxx")


# ---- a model whose attestation proves nothing names nothing ------------------------------------------------------

@pytest.mark.parametrize("statuses,named", [
    ([("FIDO_CERTIFIED", "2025-06-01"), ("ATTESTATION_KEY_COMPROMISE", "2025-07-01")], False),
    ([("FIDO_CERTIFIED", "2025-06-01"), ("REVOKED", "2025-07-01")], False),
    # a weakness reported of a model its attestation did prove: still that model
    ([("FIDO_CERTIFIED", "2025-06-01"), ("USER_VERIFICATION_BYPASS", "2025-07-01")], True),
    ([("FIDO_CERTIFIED", "2025-06-01"), ("USER_KEY_PHYSICAL_COMPROMISE", "2025-07-01")], True),
    # both: the report that the attestation proves nothing decides, whichever the metadata lists first
    ([("FIDO_CERTIFIED", "2025-06-01"), ("USER_VERIFICATION_BYPASS", "2025-07-01"),
      ("ATTESTATION_KEY_COMPROMISE", "2025-08-01")], False),
])
def test_a_key_whose_model_is_reported_to_prove_nothing_is_not_named_by_that_model(client, owner, metadata, maker,
                                                                                   statuses, named):
    metadata.publish([maker.entry(statuses=statuses)])
    add_from(client, owner, HardwareKey(maker), attachment="cross-platform")
    (k,) = listed(client, owner)
    assert k["tier"] == "device"
    assert k["name"] == ("Test Key 5 NFC" if named else "Security key")
    (p,) = view(client, owner)["passkeys"]
    assert p.get("maker") == ("Test Key 5 NFC" if named else None)


def test_with_both_reports_the_words_say_the_attestation_proves_nothing(client, owner, metadata, maker):
    metadata.publish([maker.entry(statuses=[("FIDO_CERTIFIED", "2025-06-01"), ("USER_VERIFICATION_BYPASS", "2025-07-01"),
                                            ("REVOKED", "2025-08-01")])])
    add_from(client, owner, HardwareKey(maker), attachment="cross-platform", name="K")
    assert listed(client, owner)[0]["why"] == "The FIDO metadata reports its model revoked."


# ---- the test gaps ------------------------------------------------------------------------------------------------

def test_renaming_to_no_name_a_key_whose_attestation_did_not_chain_does_not_name_it_by_its_model(client, owner,
                                                                                               metadata):
    metadata.publish([metadata.maker.entry(roots=[Maker().root])])
    add_hardware(client, owner, metadata.maker, name="Claims a model")
    (k,) = listed(client, owner)
    assert rename(client, owner, k["id"], "").json().get("name") == "Device-bound passkey"


def test_encryption_s_view_reads_a_row_from_before_the_attachment_was_kept(client, owner):
    add_from(client, owner, fresh(), attachment="cross-platform", name="Key A")
    add_from(client, owner, fresh(alg=-8), attachment="platform", name="Phone", flags=0x05 | BE | BS)
    with store.connect() as db:
        db.execute("UPDATE passkeys SET attachment = NULL, backup_eligible = NULL")
    kinds = {p["name"]: p.get("kind") for p in view(client, owner)["passkeys"]}
    assert kinds == {"Key A": "device", "Phone": "synced"}
