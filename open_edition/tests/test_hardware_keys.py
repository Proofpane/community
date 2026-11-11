"""Hardware keys (hardware_keys.py): a passkey counts as one only when all four conditions hold, judged from its packed
attestation against the FIDO metadata (fido_mds.py) as it is added; anything else is a device passkey, saying why.
Everything here is made by tests/fido_fixtures.py: no blob is fetched."""

import base64
import hashlib
import json
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import signed_up
from fido_fixtures import AAGUID_OID, DAY, MODEL, HardwareKey, Maker, add_hardware, certificate, day, keep, key_usage
from fido_fixtures import DER, as_v4, bit_string_country, name, now, signer_for
from fido_fixtures import maker, metadata, no_metadata  # noqa: F401  (fixtures)
from proofpane_open import config, fido_mds, hardware_keys, passkeys, store
from proofpane_open.ade import push
from test_passkeys import Authenticator, add, as_step, confirm, options_for, sign_in


@pytest.fixture
def owner(client):
    return signed_up(client)


def listed(client, headers):
    return client.get("/api/account/passkeys", headers=headers).json()


def row(passkey_id):
    with store.connect() as db:
        return dict(db.execute("SELECT * FROM passkeys WHERE id = ?", (passkey_id,)).fetchone())


# ---- a certified security key ----------------------------------------------------------------------------------

@pytest.mark.parametrize("alg", [-7, -257])
def test_a_certified_security_key_with_a_pin_is_a_hardware_key(client, owner, metadata, alg):
    maker = Maker(alg=alg)
    metadata.publish([maker.entry()])
    key, r = add_hardware(client, owner, maker, name="YubiKey 5 NFC")
    assert r.status_code == 200, r.text
    assert (r.json()["tier"], r.json()["maker"], r.json()["why"], r.json()["judge_again"]) == \
        ("hardware", "Test Key 5 NFC", None, False)
    got = listed(client, owner)
    (k,) = got["passkeys"]
    assert (k["tier"], k["maker"], k["aaguid"], k["why"]) == ("hardware", "Test Key 5 NFC", str(MODEL), None)
    assert got["metadata"] == {"valid": True, "next_update": day(30)} and got["high_security"] is False
    assert row(k["id"])["attestation"] is None                  # judged: nothing kept of its attestation
    assert sign_in(client, key).status_code == 200              # and it signs in as any passkey does


def test_the_options_ask_for_direct_attestation(client, owner, metadata):
    options = client.post("/api/account/passkeys/options", headers=owner,
                          json={"confirmation": client.post("/api/account/confirm", headers=owner,
                                                            json={"password": "correct horse battery",
                                                                  "change": "passkey-add"}).json()
                                ["confirmation"]}).json()
    assert options["publicKey"]["attestation"] == "direct"


def test_a_chain_through_the_maker_s_intermediate_is_followed(client, owner, metadata):
    maker = Maker(intermediate=True)
    metadata.publish([maker.entry()])
    assert add_hardware(client, owner, maker)[1].json()["tier"] == "hardware"


def test_a_ca_whose_key_usage_does_not_let_it_sign_certificates_breaks_the_chain(client, owner, metadata):
    maker = Maker(intermediate=True)
    # the maker's intermediate again, same name and key, its key usage without certificate signing (RFC 5280, 4.2.1.3)
    maker.chain_rest = [certificate(maker.inter.subject, maker.inter_key, maker.root.subject, maker.root_key,
                                    extensions=((x509.BasicConstraints(ca=True, path_length=None), True),
                                                (key_usage(False), True)))]
    metadata.publish([maker.entry()])
    r = add_hardware(client, owner, maker)[1].json()
    assert r["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."


def test_a_certificate_that_says_it_is_no_ca_issues_no_attestation_certificate(client, owner, metadata):
    # the maker's intermediate again, same name and key, saying it is no CA and with no key usage to refuse it by: a
    # key taken out of one batch must not issue a leaf for another model whose root the metadata lists
    maker = Maker(intermediate=True)
    maker.chain_rest = [certificate(maker.inter.subject, maker.inter_key, maker.root.subject, maker.root_key,
                                    extensions=((x509.BasicConstraints(ca=False, path_length=None), True),))]
    metadata.publish([maker.entry()])
    r = add_hardware(client, owner, maker)[1].json()
    assert r["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."


def test_a_ca_s_path_length_holds_in_an_attestation_chain(client, owner, metadata):
    # the root, a batch CA whose path length is 0, and under it another CA that issues the leaf: one CA too many
    maker = Maker()
    batch_key, sub_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    batch = certificate(name("Batch CA", "NZ", "Test Maker"), batch_key, maker.root.subject, maker.root_key,
                        extensions=((x509.BasicConstraints(ca=True, path_length=0), True), (key_usage(True), True)))
    sub = certificate(name("Sub CA", "NZ", "Test Maker"), sub_key, batch.subject, batch_key, ca=True)
    maker.leaf = certificate(maker.leaf.subject, maker.key, sub.subject, sub_key, ca=False, extensions=(
        (x509.UnrecognizedExtension(AAGUID_OID, b"\x04\x10" + maker.model.bytes), False),))
    maker.chain_rest = [sub, batch]
    metadata.publish([maker.entry()])
    r = add_hardware(client, owner, maker)[1].json()
    assert r["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."
    assert hardware_keys.chain_constraints_hold([maker.leaf, sub, batch, maker.root]) is False
    assert hardware_keys.chain_constraints_hold([maker.leaf, batch, maker.root]) is True


def test_an_issuer_must_say_it_is_a_ca_whatever_the_verifier_checks():
    # cryptography's verifier refuses such a chain itself; these checks keep that from resting on it alone
    maker = Maker(intermediate=True)
    no_ca = certificate(maker.inter.subject, maker.inter_key, maker.root.subject, maker.root_key,
                        extensions=((x509.BasicConstraints(ca=False, path_length=None), True),))
    for constraints in (None, x509.BasicConstraints(ca=False, path_length=None)):
        with pytest.raises(ValueError):
            hardware_keys._is_ca(None, no_ca, constraints)
    assert hardware_keys._is_ca(None, maker.inter, x509.BasicConstraints(ca=True, path_length=None)) is None
    assert hardware_keys.chain_constraints_hold([maker.leaf, no_ca, maker.root]) is False
    no_constraints = certificate(maker.inter.subject, maker.inter_key, maker.root.subject, maker.root_key)
    assert hardware_keys.chain_constraints_hold([maker.leaf, no_constraints, maker.root]) is False
    assert hardware_keys.chain_constraints_hold([maker.leaf, maker.inter, maker.root]) is True
    assert hardware_keys.chain_constraints_hold([maker.leaf]) is True            # the leaf as its own anchor


def test_the_chain_s_constraints_are_checked_again_after_the_verifier(client, owner, metadata, maker, monkeypatch):
    seen = []
    monkeypatch.setattr(hardware_keys, "chain_constraints_hold", lambda chain: seen.append(len(chain)) or False)
    r = add_hardware(client, owner, maker)[1].json()
    assert r["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."
    assert seen == [2]                                              # the leaf and its root, as the verifier built it


def test_the_attestation_certificate_says_it_is_no_ca_and_may_sign(client, owner, metadata):
    no_constraints = Maker(leaf_ca=None)
    metadata.publish([no_constraints.entry()])
    r = add_hardware(client, owner, no_constraints)[1].json()
    assert r["why"] == "Its attestation certificate does not say it is no CA (basic constraints), as WebAuthn asks."
    maker = Maker()
    no_signing = x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                               data_encipherment=False, key_agreement=True, key_cert_sign=False, crl_sign=False,
                               encipher_only=False, decipher_only=False)
    maker.leaf = certificate(maker.leaf.subject, maker.key, maker.root.subject, maker.root_key, extensions=(
        (x509.BasicConstraints(ca=False, path_length=None), True), (no_signing, True),
        (x509.UnrecognizedExtension(AAGUID_OID, b"\x04\x10" + maker.model.bytes), False)))
    metadata.publish([maker.entry()])
    r = add_hardware(client, owner, maker, name="b")[1].json()
    assert r["why"] == "Its attestation certificate's key usage does not let it sign."


def test_an_intermediate_the_metadata_lists_anchors_the_chain(client, owner, metadata):
    # MDS lets a model's trust anchor be an intermediate or the attestation certificate itself, not only a root
    maker = Maker(intermediate=True)
    metadata.publish([maker.entry(roots=[maker.inter])])
    assert add_hardware(client, owner, maker)[1].json()["tier"] == "hardware"
    leaf_anchored = Maker()
    metadata.publish([leaf_anchored.entry(roots=[leaf_anchored.leaf])])
    assert add_hardware(client, owner, leaf_anchored, name="second")[1].json()["tier"] == "hardware"


def test_the_metadata_s_transports_decide_and_only_without_them_the_browser_s_word_counts(client, owner, metadata,
                                                                                       maker):
    metadata.publish([maker.entry(transports=None)])               # the metadata lists none: the browser's word
    r = add_hardware(client, owner, maker, transports=["usb"])[1].json()
    assert (r["tier"], r["phone_by_browser"]) == ("hardware", True)
    r = add_hardware(client, owner, maker, name="b", transports=["ble", "internal"])[1].json()
    assert (r["tier"], r["why"]) == ("device", "Neither the FIDO metadata nor the browser lists NFC or USB for it, "
                                               "which a phone needs.")
    metadata.publish([maker.entry(transports=["internal"])])        # it lists some: its word alone decides
    r = add_hardware(client, owner, maker, name="c", transports=["nfc", "hybrid"])[1].json()
    assert (r["tier"], r["why"]) == ("device", "The FIDO metadata lists neither NFC nor USB for its model, which a "
                                               "phone needs.")
    metadata.publish([maker.entry(transports=["usb"])])
    r = add_hardware(client, owner, maker, name="d", transports=["ble"])[1].json()
    assert (r["tier"], r["phone_by_browser"]) == ("hardware", False)
    assert {k["name"]: k["phone_by_browser"] for k in listed(client, owner)["passkeys"]} == \
        {"YubiKey": True, "b": False, "c": False, "d": False}
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = 'device' WHERE name = 'YubiKey'")    # no longer a hardware key
    assert {k["name"]: k["phone_by_browser"] for k in listed(client, owner)["passkeys"]}["YubiKey"] is False


def test_a_key_judged_again_says_too_whether_its_phone_rests_on_the_browser_s_word(client, owner, no_metadata, maker):
    add_hardware(client, owner, maker, transports=["nfc"])
    no_metadata.publish([maker.entry(transports=None)])
    (k,) = listed(client, owner)["passkeys"]
    assert (k["tier"], k["phone_by_browser"]) == ("hardware", True)


# ---- anything else is a device passkey, saying why ---------------------------------------------------------------

def _other_bytes(maker):
    def statement(signed):
        return maker.statement(signed + b"!")
    return statement


@pytest.mark.parametrize("make, why", [
    (lambda m: Maker(aaguid_ext=False), "Its attestation certificate does not carry its model's id (AAGUID)."),
    (lambda m: Maker(aaguid_critical=True),
     "Its attestation certificate marks its model's id critical, which WebAuthn does not allow."),
    (lambda m: Maker(cert_model=uuid.uuid4()), "Its attestation certificate names another model than the key does."),
    (lambda m: Maker(raw_ext=b"\x04\x0f" + bytes(15)), "Its attestation certificate's model id cannot be read."),
    (lambda m: Maker(raw_ext=b"\x04\x10" + bytes(17)), "Its attestation certificate's model id cannot be read."),
    (lambda m: Maker(unit="Security Keys"), "Its attestation certificate does not name its maker as WebAuthn asks."),
    (lambda m: Maker(leaf_ca=True), "Its attestation certificate is a CA's, not a key's."),
])
def test_an_attestation_certificate_that_does_not_meet_webauthn_s_rules_makes_a_device_passkey(
        client, owner, metadata, make, why):
    m = make(None)
    metadata.publish([m.entry()])
    r = add_hardware(client, owner, m)[1]
    assert r.status_code == 200, r.text
    assert (r.json()["tier"], r.json()["why"], r.json()["judge_again"]) == ("device", why, False)


def test_a_platform_passkey_with_no_attestation_is_a_device_passkey(client, owner, metadata):
    r = add(client, owner, Authenticator()).json()
    assert r["tier"] == "device" and r["why"].startswith("It gave no attestation") and not r["judge_again"]
    (k,) = listed(client, owner)["passkeys"]
    assert (k["tier"], k["maker"], k["aaguid"]) == ("device", None, None)          # an AAGUID of zeros names none


def test_self_attestation_another_format_and_a_bad_signature_make_device_passkeys(client, owner, metadata, maker):
    key = HardwareKey(maker)
    r = add(client, owner, key, att_stmt={"alg": -7, "sig": b"\x30\x00"}).json()
    assert r["why"] == "It attested only with its own key, which no maker certified."
    r = add(client, owner, HardwareKey(maker), name="tpm", fmt="tpm", att_stmt={"ver": "2.0"}).json()
    assert r["why"] == "Its attestation is of the tpm kind, not the packed kind a certified security key gives."
    r = add(client, owner, HardwareKey(maker), name="odd", fmt="Odd Kind!", att_stmt={}).json()
    assert r["why"] == "Its attestation is of the another kind, not the packed kind a certified security key gives."
    r = add(client, owner, HardwareKey(maker), name="bad", statement=_other_bytes(maker)).json()
    assert r["why"] == "Its attestation's signature does not verify."
    # the statement's algorithm must be the certificate key's: RS256 named over a P-256 key is no signature
    r = add(client, owner, HardwareKey(maker), name="alg",
            statement=lambda s: dict(maker.statement(s), alg=-257)).json()
    assert r["why"] == "Its attestation's signature does not verify."
    r = add(client, owner, HardwareKey(maker), name="x5c", statement=lambda s: dict(maker.statement(s), x5c=[])).json()
    assert r["why"] == "Its attestation cannot be read."
    r = add(client, owner, HardwareKey(maker), name="der",
            statement=lambda s: dict(maker.statement(s), x5c=[b"not a certificate"])).json()
    assert r["why"] == "Its attestation's certificates cannot be read."
    assert {k["tier"] for k in listed(client, owner)["passkeys"]} == {"device"}


@pytest.mark.parametrize("entries, why", [
    (lambda m: [], "The FIDO metadata does not list its model."),
    (lambda m: [m.entry(), m.entry()], "The FIDO metadata lists its model twice."),
    (lambda m: [m.entry(roots=[Maker().root])],
     "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."),
    (lambda m: [m.entry(roots=[])],
     "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."),
    (lambda m: [m.entry(extensions=("credProtect",))],
     "It cannot derive a key: the FIDO metadata lists no hmac-secret for its model."),
    (lambda m: [m.entry(extensions=None)],
     "It cannot derive a key: the FIDO metadata lists no hmac-secret for its model."),
    (lambda m: [m.entry(transports=("ble", "internal"))],
     "The FIDO metadata lists neither NFC nor USB for its model, which a phone needs."),
])
def test_what_the_metadata_says_of_the_model_decides(client, owner, metadata, maker, entries, why):
    metadata.publish(entries(maker))
    r = add_hardware(client, owner, maker)[1].json()
    assert (r["tier"], r["why"], r["judge_again"]) == ("device", why, False)


def test_an_attestation_certificate_out_of_its_dates_does_not_chain(client, owner, metadata):
    maker = Maker(not_after=now() - DAY / 24)
    metadata.publish([maker.entry()])
    r = add_hardware(client, owner, maker)[1].json()
    assert r["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."


@pytest.mark.parametrize("statuses, why", [
    ([("FIDO_CERTIFIED_L1", "2024-01-01"), ("NOT_FIDO_CERTIFIED", "2025-06-01")], "not certified"),
    ([("FIDO_CERTIFIED_L2", "2024-01-01"), ("UPDATE_AVAILABLE", "2025-06-01")], "not certified"),
    ([("FIDO_CERTIFIED_L1", "2025-06-01"), ("UPDATE_AVAILABLE", "2025-06-01")], "not certified"),
    ([("FIDO_CERTIFIED_L1", "2025-06-01"), ("SELF_ASSERTION_SUBMITTED", None)], "not certified"),
    ([("NOT_FIDO_CERTIFIED", "2024-01-01")], "not certified"),
    ([("FIDO_CERTIFIED_L1", "2024-01-01"), ("REVOKED", "2025-06-01")], "revoked"),
    ([("REVOKED", "2023-01-01"), ("FIDO_CERTIFIED_L3plus", "2025-06-01")], "revoked"),
    ([("FIDO_CERTIFIED", "2025-06-01"), ("ATTESTATION_KEY_COMPROMISE", "2024-01-01")],
     "with its attestation key compromised"),
    ([("USER_VERIFICATION_BYPASS", None), ("FIDO_CERTIFIED", "2025-06-01")], "with a way around its PIN"),
    ([("FIDO_CERTIFIED", "2025-06-01"), ("USER_KEY_REMOTE_COMPROMISE", "2025-06-01")],
     "with its keys open to remote attack"),
    ([("FIDO_CERTIFIED", "2025-06-01"), ("USER_KEY_PHYSICAL_COMPROMISE", "2020-06-01")],
     "with its keys open to physical attack"),
    ([("FIDO_CERTIFIED", "2025-6-1")], "cannot be read"),
    ([("FIDO_CERTIFIED", "2025-02-30")], "cannot be read"),
    ([], "gives no status"),
])
def test_the_model_s_status_reports_decide(client, owner, metadata, maker, statuses, why):
    metadata.publish([maker.entry(statuses=statuses)])
    r = add_hardware(client, owner, maker)[1].json()
    assert r["tier"] == "device", statuses
    if why == "not certified":
        assert r["why"] == "The FIDO metadata does not list its model as FIDO certified now."
    elif why == "cannot be read":
        assert r["why"] == "The FIDO metadata's status reports for its model cannot be read."
    elif why == "gives no status":
        assert r["why"] == "The FIDO metadata gives no status for its model."
    else:
        assert r["why"] == f"The FIDO metadata reports its model {why}."


@pytest.mark.parametrize("statuses", [
    [("FIDO_CERTIFIED_L1", "2025-03-01")],
    [("FIDO_CERTIFIED", None)],
    [("NOT_FIDO_CERTIFIED", "2019-01-01"), ("FIDO_CERTIFIED_L1plus", "2021-01-01"), ("FIDO_CERTIFIED_L2plus",
                                                                                     "2021-01-01")],
    [("UPDATE_AVAILABLE", "2020-01-01"), ("FIDO_CERTIFIED_L3", "2024-01-01")],
])
def test_a_latest_report_that_is_a_certification_with_none_against_it_holds(client, owner, metadata, maker, statuses):
    metadata.publish([maker.entry(statuses=statuses)])
    assert add_hardware(client, owner, maker)[1].json()["tier"] == "hardware"


def test_a_maker_s_description_is_kept_to_what_shows_itself(client, owner, metadata, maker):
    metadata.publish([maker.entry(description="Key\u202e 5\x00 " + "x" * 200)])
    got = add_hardware(client, owner, maker)[1].json()["maker"]
    assert got.startswith("Key 5 xxx") and len(got) == hardware_keys.MAX_MAKER


def test_the_judgment_reads_the_uv_flag_itself():
    # registration refuses a new key that did not verify its user (test_passkeys); the judgment holds to it too
    auth = bytearray(64)
    auth[32] = 0x41                                     # present, attested, not verified
    j = hardware_keys.judge({"authData": bytes(auth), "fmt": "packed", "attStmt": {}}, b"", [], lambda: None, 0)
    assert j.tier == "device" and "did not verify you" in j.why


def test_an_attestation_certificate_must_be_of_version_3():
    class V1:
        version = hardware_keys.x509.Version.v1
    assert hardware_keys._leaf_problem(V1(), bytes(16)) == "Its attestation certificate is not of version 3, as " \
                                                          "WebAuthn asks."


# ---- without a valid blob ------------------------------------------------------------------------------------------

def test_without_a_valid_blob_no_key_is_hardware_and_one_waits_to_be_judged_again(client, owner, no_metadata, maker):
    r = add_hardware(client, owner, maker)[1].json()
    assert (r["tier"], r["why"], r["judge_again"]) == ("device", hardware_keys.NO_BLOB, True)
    got = listed(client, owner)
    assert got["metadata"] == {"valid": False, "next_update": None}
    (k,) = got["passkeys"]
    held = json.loads(row(k["id"])["attestation"])                  # kept, to be judged again
    assert set(held) == {"object", "client_data_hash", "transports"}
    assert passkeys.awaiting_judgment() == 1
    no_metadata.publish()                                           # a valid blob arrives
    (k,) = listed(client, owner)["passkeys"]                        # the list judges it again first
    assert (k["tier"], k["maker"], k["why"], k["judge_again"]) == ("hardware", "Test Key 5 NFC", None, False)
    assert row(k["id"])["attestation"] is None and passkeys.awaiting_judgment() == 0


def test_a_key_judged_again_is_judged_by_all_four_conditions(client, owner, no_metadata, maker):
    add_hardware(client, owner, maker)
    no_metadata.publish([maker.entry(extensions=("credProtect",))])
    assert passkeys.judge_again() == 1
    (k,) = listed(client, owner)["passkeys"]
    assert (k["tier"], k["why"]) == ("device", "It cannot derive a key: the FIDO metadata lists no hmac-secret for its "
                                               "model.")
    assert passkeys.judge_again() == 0                              # judged once: no longer waiting


def test_a_kept_attestation_that_cannot_be_read_is_judged_a_device_passkey(client, owner, no_metadata, maker):
    add_hardware(client, owner, maker)
    with store.connect() as db:
        db.execute("UPDATE passkeys SET attestation = '{\"object\": \"!\"}'")
    no_metadata.publish()
    (k,) = listed(client, owner)["passkeys"]
    assert (k["tier"], k["why"], k["judge_again"]) == ("device", passkeys.UNREAD_ATTESTATION, False)


@pytest.mark.parametrize("bad", ["expired", "another root", "tampered", "another host"])
def test_a_blob_that_is_not_valid_is_never_used(client, owner, no_metadata, maker, bad):
    signer = no_metadata.signer
    if bad == "expired":
        data = signer.blob([maker.entry()], next_update=day(-2))
    elif bad == "another root":
        data = signer_for("ES256").blob([maker.entry()])           # the ES256 signer's own root, not trusted here
    elif bad == "tampered":
        head, body, sig = signer.blob([maker.entry()]).split(b".")
        data = b".".join((head, passkeys.b64u(json.dumps({"no": 9, "nextUpdate": day(30), "entries": [
            maker.entry()]}).encode()).encode(), sig))
    else:                       # a TLS certificate the same CA gave another site: chained right, named wrong
        key = ec.generate_private_key(ec.SECP256R1())
        data = signer.blob([maker.entry()], chain=[signer.signing_cert("evil.example", key), signer.inter], key=key,
                           alg="ES256")
    keep(data)
    fido_mds.forget()
    r = add_hardware(client, owner, maker)[1].json()
    assert (r["tier"], r["why"], r["judge_again"]) == ("device", hardware_keys.NO_BLOB, True)


def test_a_passkey_added_before_tiers_were_judged_says_so(client, owner):
    add(client, owner, Authenticator())
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier_why = NULL")          # as the column's default leaves an older row
    (k,) = listed(client, owner)["passkeys"]
    assert (k["tier"], k["why"]) == ("device", passkeys.OLD_PASSKEY)


def test_the_browser_s_transports_are_kept_as_short_words():
    assert passkeys._transports(["usb", "nfc", 3, "x" * 33, "café", ""]) == ["usb", "nfc"]
    assert passkeys._transports("usb") == [] and len(passkeys._transports(["usb"] * 20)) == 8


def test_judging_needs_no_blob_for_a_key_with_no_attestation(client, owner, monkeypatch):
    # a platform passkey is judged from what it gave alone: nothing is asked of the metadata, so no fetch is woken
    asked = []
    monkeypatch.setattr(fido_mds, "kept", lambda *a, **k: asked.append("kept"))
    monkeypatch.setattr(fido_mds, "nudge", lambda: asked.append("nudge"))
    assert add(client, owner, Authenticator()).json()["tier"] == "device"
    assert asked == []
    assert config.mds_url() == ""                                   # and the tests fetch nothing (conftest)


def test_a_replay_or_a_key_added_already_is_refused_before_any_attestation_is_judged(client, owner, metadata, maker,
                                                                                    monkeypatch):
    # one answer to a challenge is judged or refused, once: a refusal uses the challenge up too, so no options fetched
    # once make the server read certificates again and again for their five minutes
    judged, judge = [], hardware_keys.judge
    monkeypatch.setattr(hardware_keys, "judge", lambda *a, **k: judged.append(1) or judge(*a, **k))

    def answer(options, key):
        return client.post("/api/account/passkeys", headers=owner,
                           json={"challenge_id": options["challenge_id"], "credential": key.create(options)})
    first, key = options_for(client, owner), HardwareKey(maker)
    assert answer(first, key).status_code == 200 and judged == [1]
    r = answer(first, HardwareKey(maker))                           # the same options again, with another key
    assert r.status_code == 400 and "expired or was used" in r.json()["detail"] and judged == [1]
    second = options_for(client, owner)
    r = answer(second, key)                                         # new options, the key added already
    assert r.status_code == 409 and r.json()["detail"] == "That passkey is already added." and judged == [1]
    r = answer(second, HardwareKey(maker))                          # its challenge went with the refusal
    assert r.status_code == 400 and "expired or was used" in r.json()["detail"] and judged == [1]
    assert len(listed(client, owner)["passkeys"]) == 1


def test_what_changes_while_the_attestation_is_judged_is_checked_again(client, owner, metadata, maker, monkeypatch):
    judge = hardware_keys.judge

    def meanwhile(*a, **k):
        monkeypatch.setattr(passkeys, "MAX_PASSKEYS", 0)            # as if other keys were added while it was judged
        return judge(*a, **k)
    monkeypatch.setattr(hardware_keys, "judge", meanwhile)
    r = add_hardware(client, owner, maker)[1]
    assert r.status_code == 409 and listed(client, owner)["passkeys"] == []


def test_a_key_stored_to_be_judged_again_asks_the_refresher_for_a_round(client, owner, no_metadata, maker,
                                                                         monkeypatch):
    nudged = []
    monkeypatch.setattr(fido_mds, "nudge", lambda: nudged.append(1))
    add(client, owner, Authenticator())                             # judged already: nothing to ask
    assert nudged == []
    add_hardware(client, owner, maker)
    assert nudged == [1]


# ---- what the client sends cannot make an error of a judgment -------------------------------------------------------

def test_a_certificate_this_server_cannot_read_makes_a_device_passkey_never_an_error(client, owner, metadata, maker):
    maker.leaf_der = as_v4(maker.leaf)                              # cryptography refuses to load a version 4
    r = add_hardware(client, owner, maker)[1]
    assert r.status_code == 200 and r.json()["why"] == "Its attestation's certificates cannot be read."
    maker.leaf_der = bit_string_country(maker)                      # reading its subject raises a TypeError
    r = add_hardware(client, owner, maker, name="b")[1]
    assert r.status_code == 200 and r.json()["why"] == "Its attestation certificate cannot be read."


def test_a_root_the_metadata_lists_that_cannot_be_read_anchors_nothing(client, owner, metadata, maker):
    entry = maker.entry()
    entry["metadataStatement"]["attestationRootCertificates"] = [base64.b64encode(as_v4(maker.root)).decode()]
    metadata.publish([entry])
    r = add_hardware(client, owner, maker)[1]
    assert r.status_code == 200
    assert r.json()["why"] == "Its attestation certificate does not chain to a root the FIDO metadata lists for its model."


def test_a_credential_that_may_be_synced_is_a_device_passkey(client, owner, metadata, maker):
    # the backup-eligible flag, in the authenticator data the attestation signs: a synced passkey sets it
    why = ("It may be copied to other devices (its backup-eligible flag is set), as a synced passkey can: it is not "
           "bound to one key.")
    assert add_hardware(client, owner, maker, flags=0x05 | 0x08)[1].json()["why"] == why
    assert add(client, owner, Authenticator(-8), name="synced", flags=0x05 | 0x08 | 0x10).json()["why"] == why


# ---- the backup flags ----------------------------------------------------------------------------------------------

UP_UV, BE, BS = 0x05, 0x08, 0x10


def _judged(key, flags, now_ts):
    created = key.create({"publicKey": {"user": {"id": passkeys.b64u(b"u")}, "challenge": passkeys.b64u(bytes(32))}},
                         flags=flags)
    attestation = passkeys.cbor_whole(passkeys.unb64u(created["response"]["attestationObject"], "attestation"))
    client_hash = hashlib.sha256(passkeys.unb64u(created["response"]["clientDataJSON"], "client data")).digest()
    return hardware_keys.judge(attestation, client_hash, ["usb"], lambda: fido_mds.kept(now_ts), now_ts)


def test_a_key_that_says_it_is_backed_up_is_a_device_passkey_whatever_else_holds(metadata, maker):
    key, at = HardwareKey(maker), int(now().timestamp())
    assert _judged(key, UP_UV, at).tier == "hardware"                # the same key, the flag clear
    got = _judged(key, UP_UV | BS, at)                              # set without BE, which registration refuses
    assert (got.tier, got.why) == ("device", "It says it is backed up (its backup-state flag is set): it is not bound "
                                             "to one key.")


def test_a_key_that_says_it_is_backed_up_but_may_not_be_is_refused(client, owner, metadata, maker):
    r = add_hardware(client, owner, maker, flags=UP_UV | BS)[1]
    assert r.status_code == 400 and r.json()["detail"] == ("The passkey's new key says it is backed up, yet not that "
                                                           "it may be.")
    assert listed(client, owner)["passkeys"] == []
    device = Authenticator()
    assert add(client, owner, device).status_code == 200
    assert sign_in(client, device, flags=UP_UV | BS).status_code == 401
    assert sign_in(client, device).status_code == 200


@pytest.mark.parametrize("added, other", [(UP_UV, UP_UV | BE), (UP_UV | BE, UP_UV), (UP_UV | BE | BS, UP_UV)])
def test_a_use_must_carry_the_backup_eligible_flag_the_key_was_added_with(client, owner, added, other):
    device = Authenticator(-8)
    r = add(client, owner, device, flags=added)
    assert r.status_code == 200
    assert row(r.json()["id"])["backup_eligible"] == int(bool(added & BE))
    assert sign_in(client, device, flags=other).status_code == 401
    assert sign_in(client, device, flags=added).status_code == 200
    assert confirm(client, owner, device, flags=other).status_code == 403
    assert confirm(client, owner, device, flags=added).status_code == 200


def test_a_key_kept_before_the_flag_was_learns_it_at_its_next_use_and_a_hardware_key_s_is_clear(
        client, owner, metadata, maker):
    device = Authenticator(-8)
    add(client, owner, device, flags=UP_UV | BE)
    hardware = add_hardware(client, owner, maker)[0]
    with store.connect() as db:
        db.execute("UPDATE passkeys SET backup_eligible = NULL")    # as the column leaves a row from before
    assert sign_in(client, device, flags=UP_UV | BE).status_code == 200
    assert sign_in(client, device, flags=UP_UV).status_code == 401  # learned at that use
    assert sign_in(client, hardware, flags=UP_UV | BE).status_code == 401   # judged with it clear
    assert sign_in(client, hardware).status_code == 200


def test_a_command_must_carry_the_flag_too_checked_again_where_it_is_stored(client, owner):
    device = Authenticator()
    add(client, owner, device)
    canonical = b'{"say":"hello"}'
    options = as_step(canonical)
    with store.connect() as db:
        user_id = db.execute("SELECT user_id FROM passkeys").fetchone()[0]
    with pytest.raises(passkeys.PasskeyError, match="backup-eligible"):
        passkeys.check_command(user_id, canonical, device.get(options, flags=UP_UV | BE))
    held, auth = passkeys.check_command(user_id, canonical, device.get(options))
    with store.connect() as db:
        db.execute("UPDATE passkeys SET backup_eligible = 1")       # as another use recorded meanwhile
    with store.connect() as db, pytest.raises(passkeys.PasskeyError, match="backup-eligible"):
        passkeys.commit_counter(db, held, auth, int(now().timestamp()))
    with store.connect() as db:
        db.execute("UPDATE passkeys SET backup_eligible = NULL")    # a row from before: learned where it is stored
    held, auth = passkeys.check_command(user_id, canonical, device.get(options))
    with store.connect() as db:
        passkeys.commit_counter(db, held, auth, int(now().timestamp()))
    with store.connect() as db:
        assert db.execute("SELECT backup_eligible FROM passkeys").fetchone()[0] == 0


# ---- a report published later --------------------------------------------------------------------------------------

COMPROMISED = "The FIDO metadata has since reported its model with its attestation key compromised."


def reported_entry(maker, certificate=None, status="ATTESTATION_KEY_COMPROMISE"):
    """The maker's entry, certified, with a later report against the model, about `certificate` (DER) when given."""
    entry = maker.entry()
    report = {"status": status, "effectiveDate": "2026-09-01"}
    if certificate is not None:
        report["certificate"] = base64.b64encode(certificate).decode()
    entry["statusReports"].append(report)
    return entry


@pytest.fixture
def told(monkeypatch):
    """What the account's devices are told of their hardware keys: (user id, title, body); the notices of a passkey
    added and the like are left out. Each in fixed words (push.words): never the key's name or the report's words,
    which Account shows."""
    out = []

    def notice(user_id, message, now=None, post=None):
        if message.title == "A hardware key was reported":
            assert (message.body, message.url) == ("Open Account.", "/app/#account")
            out.append((user_id, message.title, message.body))
        return 1
    monkeypatch.setattr(push, "notice", notice)
    return out


def test_a_later_report_against_its_model_makes_a_hardware_key_a_device_passkey_once_and_never_back(
        client, owner, metadata, maker, told):
    add_hardware(client, owner, maker, name="YubiKey")
    add(client, owner, Authenticator(-8), name="iPhone")
    assert passkeys.reconsider() == 0                               # the blob it was judged with: nothing new
    metadata.publish([reported_entry(maker)], no=2)
    assert passkeys.reconsider() == 1
    key = next(k for k in listed(client, owner)["passkeys"] if k["name"] == "YubiKey")
    assert (key["tier"], key["why"], key["reported"]) == ("device", COMPROMISED, None)
    assert [(t, b) for _, t, b in told] == [("A hardware key was reported", "Open Account.")]
    assert passkeys.reconsider() == 0 and len(told) == 1            # that blob is done with
    metadata.publish([maker.entry()], no=3)                         # a blob with no report: never made one again
    assert passkeys.reconsider() == 0
    assert next(k for k in listed(client, owner)["passkeys"] if k["name"] == "YubiKey")["tier"] == "device"


def test_an_account_whose_keys_are_reported_together_is_told_once(client, owner, metadata, maker, told):
    """Two keys of a reported model once made two identical notices, which never replaced each other and
    showed on a lock screen how many keys were reported."""
    add_hardware(client, owner, maker, name="YubiKey")
    add_hardware(client, owner, maker, name="Spare YubiKey")
    metadata.publish([reported_entry(maker)], no=2)
    assert passkeys.reconsider() == 2
    assert [(t, b) for _, t, b in told] == [("A hardware key was reported", "Open Account.")]


@pytest.mark.parametrize("about, gone", [("another batch", False), ("its leaf", True), ("its intermediate", True),
                                         ("its root", True), ("an unreadable certificate", True)])
def test_a_report_about_one_certificate_reaches_only_the_keys_whose_attestation_has_it(client, owner, metadata, about,
                                                                                       gone, told):
    maker = Maker(intermediate=True)
    metadata.publish([maker.entry()])
    add_hardware(client, owner, maker)
    certificate = {"another batch": Maker().x5c()[0], "its leaf": maker.x5c()[0], "its intermediate": maker.x5c()[1],
                   "its root": maker.root.public_bytes(DER), "an unreadable certificate": None}[about]
    entry = reported_entry(maker, certificate)
    if certificate is None:
        entry["statusReports"][-1]["certificate"] = "not base64!"
    metadata.publish([entry], no=2)
    (k,) = listed(client, owner)["passkeys"]                       # listing reconsiders against a new blob too
    assert k["tier"] == ("device" if gone else "hardware"), about


def test_a_key_judged_before_its_certificates_were_kept_is_taken_to_be_reported_by_any_report(client, owner, metadata,
                                                                                            maker, told):
    add_hardware(client, owner, maker)
    with store.connect() as db:
        db.execute("UPDATE passkeys SET attestation_certs = NULL")   # as a row from before leaves it
    metadata.publish([reported_entry(maker, Maker().x5c()[0])], no=2)
    assert passkeys.reconsider() == 1


def test_a_blob_another_request_reconsidered_first_is_not_reconsidered_again(client, owner, metadata, maker, told,
                                                                              monkeypatch):
    add_hardware(client, owner, maker)
    metadata.publish([reported_entry(maker)], no=2)
    assert passkeys.reconsider() == 1 and len(told) == 1
    with store.connect() as db:
        db.execute("UPDATE passkeys SET tier = 'hardware'")         # so a second pass would show
    real = passkeys._reconsidered
    # the first read made before the other request recorded the blob; the transaction's own read sees it
    monkeypatch.setattr(passkeys, "_reconsidered", lambda store_db: -1 if store_db is None else real(store_db))
    assert passkeys.reconsider() == 0 and len(told) == 1


def test_a_report_of_another_kind_than_revoked_or_compromised_changes_nothing(client, owner, metadata, maker, told):
    add_hardware(client, owner, maker)
    metadata.publish([reported_entry(maker, status="UPDATE_AVAILABLE")], no=2)
    assert passkeys.reconsider() == 0 and told == []


def test_the_hardware_key_s_certificates_are_kept_as_it_is_added_and_when_it_is_judged_again(client, owner,
                                                                                             no_metadata, maker):
    add_hardware(client, owner, maker)
    with store.connect() as db:
        assert db.execute("SELECT attestation_certs FROM passkeys").fetchone()[0] is None   # not yet a hardware key
    no_metadata.publish()
    listed(client, owner)
    want = [hashlib.sha256(c).hexdigest() for c in maker.x5c() + [maker.root.public_bytes(DER)]]   # up to its root
    with store.connect() as db:
        assert json.loads(db.execute("SELECT attestation_certs FROM passkeys").fetchone()[0]) == want
