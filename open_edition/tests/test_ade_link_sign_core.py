"""The second screen's signing core: what `ade_link.py sign` does at the owner's
terminal on the Mac their security key is plugged into, tested with no hardware. The key is the tests' own
(tests/ctap_soft.py), behind python-fido2's real USB transport and its real Fido2Client, so every request reaches the
key as CTAPHID packets and every answer comes back through the library's own code, its silent retry of a busy key
included. Here: the soft key itself, as a key behaves; reading a step strictly, its words held to the
server's own rules on every code point; the screen, with every string the ADE gave escaped and the nonce
never shown; and the shared vectors (tests/vectors/second_screen_v1.json), which the page's tests read too, and
which write_vectors() writes anew from the builders here (vectors())."""

import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unicodedata

import pytest
from fido2.client import ClientDataCollector, Fido2Client, UserInteraction
from fido2.ctap import CtapError
from fido2.ctap2 import Ctap2
from fido2.ctap2.extensions import HmacSecretExtension
from fido2.ctap2.pin import ClientPin, PinProtocolV1, PinProtocolV2
from fido2.webauthn import (CollectedClientData, PublicKeyCredentialCreationOptions, PublicKeyCredentialDescriptor,
                            PublicKeyCredentialRequestOptions, UserVerificationRequirement)

import ade_link
import ctap_soft
from conftest import signed_up
from fido_fixtures import maker, metadata  # noqa: F401  (fixtures)
from proofpane_open import passkeys, store
from proofpane_open.ade import link, link_commands              # puts the open packages on sys.path first
from proofpane_core.canonical import canonicalize
from test_ade_link_client import link_row, pair, run, served  # noqa: F401  (served: a fixture)
from test_passkeys import Authenticator, derived, options_for

RP = "ade.example.com"
ORIGIN = "https://ade.example.com"
ERR = CtapError.ERR


class PageData(ClientDataCollector):
    """The client data a browser writes for `origin`: the request's challenge, as the request gives it."""

    def __init__(self, origin=ORIGIN, rp_id=RP):
        self.origin, self.rp_id = origin, rp_id

    def collect_client_data(self, options):
        kind = "webauthn.create" if isinstance(options, PublicKeyCredentialCreationOptions) else "webauthn.get"
        return CollectedClientData.create(kind, options.challenge, self.origin), self.rp_id


class Said(UserInteraction):
    """The person at the key, as a test plays them: the PIN they give, and what they were asked, in order."""

    def __init__(self, pin="1234"):
        self.pin, self.log = pin, []

    def prompt_up(self):
        self.log.append("touch")

    def request_pin(self, permissions, rp_id):
        self.log.append("pin")
        return self.pin

    def request_uv(self, permissions, rp_id):
        self.log.append("uv")
        return True


def make(key, said=None, rp_id=RP, origin=ORIGIN, user=b"user-1", rk=False):
    """A passkey the key makes through python-fido2, as a browser asks for one: the library's registration answer."""
    client = Fido2Client(ctap_soft.device(key), PageData(origin, rp_id), user_interaction=said or Said(),
                         extensions=[])
    return client.make_credential(PublicKeyCredentialCreationOptions.from_dict({
        "rp": {"id": rp_id, "name": "ADE"}, "user": {"id": user, "name": "owner", "displayName": "owner"},
        "challenge": b"r" * 32, "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        "authenticatorSelection": {"userVerification": "required",
                                   "residentKey": "required" if rk else "discouraged"}}))


def get(key, ids, said=None, challenge=b"c" * 32, rp_id=RP, origin=ORIGIN, event=None):
    """The key's assertion through python-fido2 for the passkeys `ids`, user verification required."""
    client = Fido2Client(ctap_soft.device(key), PageData(origin, rp_id), user_interaction=said or Said(),
                         extensions=[])
    return client.get_assertion(PublicKeyCredentialRequestOptions(
        challenge=challenge, rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[PublicKeyCredentialDescriptor(type="public-key", id=i) for i in ids]), event=event)


# ---- the soft key, as a key behaves ----------------------------------------------------------------------------------

def test_the_soft_key_makes_a_passkey_and_signs_with_it_through_python_fido2():
    key, said = ctap_soft.SoftKey(), Said()
    made = make(key, said)
    data = made.response.attestation_object.auth_data
    assert made.response.attestation_object.fmt == "none" and data.flags & 0x45 == 0x45      # UP, UV, AT
    assert data.rp_id_hash == hashlib.sha256(RP.encode()).digest() and said.log == ["pin", "touch"]
    public = data.credential_data.public_key
    said = Said()
    answer = get(key, [made.raw_id], said).get_response(0)
    auth = answer.response.authenticator_data
    # python-fido2 asks the key silently first (up false, which signs a zero hash with no touch), as a key counts it
    assert answer.raw_id == made.raw_id and auth.flags & 0x05 == 0x05 and auth.counter == 2
    public.verify(bytes(auth) + hashlib.sha256(bytes(answer.response.client_data)).digest(), answer.response.signature)
    assert said.log == ["pin", "touch"] and key.signatures == 1
    probe, real = key.asked("getAssertion")
    assert probe[5] == {"up": False} and probe[2] == bytes(32)          # no touch, and over no client data
    assert 5 not in real and 6 in real and real[2] == hashlib.sha256(bytes(answer.response.client_data)).digest()
    assert get(key, [made.raw_id]).get_response(0).response.authenticator_data.counter == 4


def test_the_soft_key_s_pin_token_is_python_fido2_s_own_protocol_2_and_1():
    for protocols in ((2,), (1,)):
        key = ctap_soft.SoftKey(protocols=protocols)
        made = make(key)
        assert get(key, [made.raw_id]).get_response(0).raw_id == made.raw_id
        asked = [p for p in key.asked("clientPin") if p.get(2) in (0x05, 0x09)]
        assert {p[1] for p in asked} == set(protocols)


def test_the_soft_key_counts_wrong_pins_three_in_a_row_block_until_replugged_and_eight_for_good():
    key = ctap_soft.SoftKey(pin="1234")
    pin = ClientPin(Ctap2(ctap_soft.device(key)))
    for left in (7, 6):
        with pytest.raises(CtapError) as e:
            pin.get_pin_token("0000", ClientPin.PERMISSION.GET_ASSERTION, RP)
        assert e.value.code == ERR.PIN_INVALID and pin.get_pin_retries()[0] == left
    with pytest.raises(CtapError) as e:
        pin.get_pin_token("0000", ClientPin.PERMISSION.GET_ASSERTION, RP)
    assert e.value.code == ERR.PIN_AUTH_BLOCKED
    with pytest.raises(CtapError) as e:
        pin.get_pin_token("1234", ClientPin.PERMISSION.GET_ASSERTION, RP)          # the right PIN, before a replug
    assert e.value.code == ERR.PIN_AUTH_BLOCKED and pin.get_pin_retries() == (5, True)
    key.replug()
    pin = ClientPin(Ctap2(ctap_soft.device(key)))
    assert pin.get_pin_token("1234", ClientPin.PERMISSION.GET_ASSERTION, RP)     # and a right PIN counts them anew
    assert pin.get_pin_retries() == (8, False)
    for n in range(8):
        if n and n % 3 == 0:
            key.replug()
            pin = ClientPin(Ctap2(ctap_soft.device(key)))
        with pytest.raises(CtapError) as e:
            pin.get_pin_token("0000", ClientPin.PERMISSION.GET_ASSERTION, RP)
    assert e.value.code == ERR.PIN_BLOCKED and pin.get_pin_retries()[0] == 0
    key.replug()
    with pytest.raises(CtapError) as e:
        ClientPin(Ctap2(ctap_soft.device(key))).get_pin_token("1234", ClientPin.PERMISSION.GET_ASSERTION, RP)
    assert e.value.code == ERR.PIN_BLOCKED


def within(seconds, call):
    """call(), failing the test if it has not ended after `seconds` (a key that never hears a cancel waits forever)."""
    out = {}

    def run():
        try:
            out["value"] = call()
        except BaseException as e:  # noqa: BLE001  handed back to the test
            out["error"] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"still waiting after {seconds} seconds"
    if "error" in out:
        raise out["error"]
    return out.get("value")


def test_a_touch_refused_makes_and_signs_nothing():
    key = ctap_soft.SoftKey(touch="refused")
    with pytest.raises(Exception) as e:
        make(key)
    assert getattr(getattr(e.value, "cause", None), "code", None) == ERR.OPERATION_DENIED and key.credentials == []
    key.touch = "given"
    made = make(key)
    key.touch = "refused"
    with pytest.raises(Exception) as e:
        get(key, [made.raw_id])
    assert getattr(getattr(e.value, "cause", None), "code", None) == ERR.OPERATION_DENIED
    assert key.signatures == 0 and key.touches == 3 and len(key.credentials) == 1


def test_a_key_never_touched_waits_until_the_client_cancels():
    key = ctap_soft.SoftKey()
    made = make(key)
    key.touch = "never"
    event = threading.Event()
    threading.Timer(0.3, event.set).start()
    started = time.monotonic()
    with pytest.raises(Exception) as e:
        within(10, lambda: get(key, [made.raw_id], event=event))
    assert 0.25 < time.monotonic() - started < 5
    assert getattr(getattr(e.value, "cause", None), "code", None) == ERR.KEEPALIVE_CANCEL
    assert key.signatures == 0 and key.cancelled == 1


def test_python_fido2_waits_out_a_busy_key_silently_on_its_own():
    # the hazard the core's OneTry stops: the library's own call asks again every 0.1 seconds while the key says busy
    key = ctap_soft.SoftKey()
    key.busy = 3
    started = time.monotonic()
    assert Ctap2(ctap_soft.device(key)).info.versions == ["FIDO_2_0", "FIDO_2_1"]
    assert key.busy == 0 and key.busy_answered == 3 and time.monotonic() - started >= 0.29
    made = make(key)
    key.busy_at = "getAssertion"                       # busy at the one command named, the first time it comes
    started = time.monotonic()
    assert within(10, lambda: get(key, [made.raw_id])).get_response(0).raw_id == made.raw_id
    assert key.busy_at is None and key.busy_answered == 4 and time.monotonic() - started >= 0.09
    assert len(key.asked("getAssertion")) == 2          # the busy answer was given before the key read the command


def test_a_finger_gives_a_token_that_carries_presence_for_one_assertion():
    # the key waits for the finger as for a touch (a keep-alive saying so), and then signs with no second one
    key, said = ctap_soft.SoftKey(pin=None, uv=True), Said()
    make(key, said)
    assert said.log == ["uv", "touch"] and key.touches == 1
    said = Said()
    made = make(key)
    answer = get(key, [made.raw_id], said).get_response(0)
    assert answer.response.authenticator_data.flags & 0x05 == 0x05 and said.log == ["uv", "touch"]
    assert key.touches == 3 and key.asked("clientPin")[-1][2] == 0x06 and key.uv_retries == ctap_soft.UV_RETRIES
    key.finger = "other"
    with pytest.raises(Exception) as e:
        get(key, [made.raw_id])
    assert getattr(getattr(e.value, "cause", None), "code", None) == ERR.UV_INVALID and key.uv_retries == 2


def test_of_two_accounts_passkeys_on_one_key_the_one_asked_for_answers():
    key = ctap_soft.SoftKey()
    mine, theirs = make(key, user=b"user-1", rk=True), make(key, user=b"user-2", rk=True)
    for made in (mine, theirs):
        assert get(key, [made.raw_id]).get_response(0).raw_id == made.raw_id
    assert get(key, [b"\x07" * 32, theirs.raw_id]).get_response(0).raw_id == theirs.raw_id
    with pytest.raises(Exception) as e:
        get(key, [b"\x07" * 32])
    assert getattr(getattr(e.value, "cause", None), "code", None) == ERR.NO_CREDENTIALS


def test_a_passkey_made_the_page_s_way_signs_through_the_key_once_imported():
    page = Authenticator()
    key = ctap_soft.SoftKey()
    key.import_credential(RP, page.credential_id, page.key, -7)
    answer = get(key, [page.credential_id]).get_response(0)
    signed = bytes(answer.response.authenticator_data) + hashlib.sha256(bytes(answer.response.client_data)).digest()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    page.key.public_key().verify(answer.response.signature, signed, ec.ECDSA(hashes.SHA256()))


def test_a_maker_s_key_is_of_its_model_and_attests_packed_with_its_batch_key(maker):
    key = ctap_soft.SoftKey(maker=maker)
    made = make(key)
    att = made.response.attestation_object
    assert att.fmt == "packed" and att.att_stmt["x5c"] == maker.x5c()
    assert att.auth_data.credential_data.aaguid == maker.model.bytes == key.info()[3]
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    maker.key.public_key().verify(att.att_stmt["sig"], bytes(att.auth_data)
                                  + hashlib.sha256(bytes(made.response.client_data)).digest(),
                                  ec.ECDSA(hashes.SHA256()))


def test_the_key_records_every_command_it_is_sent():
    key = ctap_soft.SoftKey()
    made = make(key)
    get(key, [made.raw_id])
    names = [name for name, _ in key.requests]
    assert names[0] == "getInfo" and "makeCredential" in names and "getAssertion" in names
    assert all(4 not in p for p in key.asked("getAssertion"))           # this client asked for no extension


def hmac_client(key, said=None):
    """python-fido2's client with its hmac-secret extension, let through as hmac-secret as well as WebAuthn's PRF."""
    return Fido2Client(ctap_soft.device(key), PageData(), user_interaction=said or Said(),
                       extensions=[HmacSecretExtension(allow_hmac_secret=True)])


def made_with_hmac_secret(key, user=b"user-1", rk=False):
    """A passkey the key makes with hmac-secret, asked for through python-fido2 as a browser asks for PRF."""
    return hmac_client(key).make_credential(PublicKeyCredentialCreationOptions.from_dict({
        "rp": {"id": RP, "name": "ADE"}, "user": {"id": user, "name": "owner", "displayName": "owner"},
        "challenge": b"r" * 32, "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        "authenticatorSelection": {"userVerification": "required",
                                   "residentKey": "required" if rk else "discouraged"},
        "extensions": {"hmacCreateSecret": True}}))


def hmac_get(key, credential_id, salts, uv=UserVerificationRequirement.REQUIRED):
    """(the hmac-secret outputs python-fido2 decrypts, the authenticator data) of an assertion asking for `salts`."""
    answer = hmac_client(key).get_assertion(PublicKeyCredentialRequestOptions(
        challenge=b"c" * 32, rp_id=RP, user_verification=uv,
        allow_credentials=[PublicKeyCredentialDescriptor(type="public-key", id=credential_id)],
        extensions={"hmacGetSecret": dict(zip(("salt1", "salt2"), salts))})).get_response(0)
    return answer.client_extension_results.hmac_get_secret, answer.response.authenticator_data


def hmac_of(secret, salt):
    return hmac.new(secret, salt, hashlib.sha256).digest()


def test_hmac_secret_answers_under_one_secret_with_the_user_verified_and_another_without():
    key = ctap_soft.SoftKey()
    made = made_with_hmac_secret(key)
    data = made.response.attestation_object.auth_data
    assert made.client_extension_results.hmac_create_secret is True
    assert data.flags & 0xC5 == 0xC5 and data.extensions == {"hmac-secret": True}         # UP, UV, AT, ED
    with_uv, without_uv = key.credentials[-1].secrets
    assert len(with_uv) == len(without_uv) == 32 and with_uv != without_uv
    one, two = b"1" * 32, b"2" * 32
    out, auth = hmac_get(key, made.raw_id, (one, two))
    assert auth.flags & 0x85 == 0x85                                    # UP, UV, ED
    assert (out.output1, out.output2) == (hmac_of(with_uv, one), hmac_of(with_uv, two))
    out, auth = hmac_get(key, made.raw_id, (one,), UserVerificationRequirement.DISCOURAGED)
    assert auth.flags & 0x85 == 0x81 and out.output1 == hmac_of(without_uv, one) and not out.output2
    # the user verified again: the first secret again, kept with the passkey
    assert hmac_get(key, made.raw_id, (two,))[0].output1 == hmac_of(with_uv, two)


def test_a_passkey_made_without_hmac_secret_or_imported_gives_no_output_when_asked():
    key = ctap_soft.SoftKey()
    made = make(key)                                                    # asked for no extension
    assert made.response.attestation_object.auth_data.extensions is None
    page = Authenticator()
    key.import_credential(RP, page.credential_id, page.key, -7)
    for credential_id in (made.raw_id, page.credential_id):
        out, auth = hmac_get(key, credential_id, (b"1" * 32,))
        assert out is None and not auth.flags & 0x80 and auth.extensions is None
    # each was asked (python-fido2's silent probe first, which asks nothing)
    assert [4 in p for p in key.asked("getAssertion")] == [False, True, False, True]


@pytest.mark.parametrize("protocol", [PinProtocolV2, PinProtocolV1])
def test_hmac_secret_refuses_salts_whose_mac_does_not_hold_or_that_are_not_one_or_two_salts(protocol):
    key = ctap_soft.SoftKey()
    made = made_with_hmac_secret(key)
    ctap, proto = Ctap2(ctap_soft.device(key)), protocol()
    agreement, shared = ClientPin(ctap, proto)._get_shared_secret()

    def asking(given):
        return lambda: ctap.get_assertion(RP, b"h" * 32, [{"type": "public-key", "id": made.raw_id}],
                                          {"hmac-secret": given})

    def ask(salts, wrong=False, version=True):
        sealed = proto.encrypt(shared, salts)
        mac = proto.authenticate(shared, sealed)
        given = {1: agreement, 2: sealed, 3: bytes([mac[0] ^ 1]) + mac[1:] if wrong else mac}
        return asking({**given, 4: proto.VERSION} if version else given)
    answer = ask(b"s" * 32)()
    assert proto.decrypt(shared, answer.auth_data.extensions["hmac-secret"]) == \
        hmac_of(key.credentials[-1].secrets[1], b"s" * 32)            # no user verified: the second secret
    # with no protocol named, it is protocol 1, as CTAP says: one made by protocol 2 does not verify as it
    if proto.VERSION == 1:
        assert ask(b"s" * 32, version=False)().auth_data.extensions["hmac-secret"]
    # asked with no touch (up false), as a disk unlocked with no touch asks it, it answers, under the second secret
    sealed = proto.encrypt(shared, b"t" * 32)
    silent = ctap.get_assertion(RP, b"h" * 32, [{"type": "public-key", "id": made.raw_id}], {"hmac-secret": {
        1: agreement, 2: sealed, 3: proto.authenticate(shared, sealed), 4: proto.VERSION}}, {"up": False})
    assert not silent.auth_data.flags & 0x01 and proto.decrypt(shared, silent.auth_data.extensions["hmac-secret"]) \
        == hmac_of(key.credentials[-1].secrets[1], b"t" * 32)
    touches = key.touches
    torn = b"x" * 33                                                    # MACed, but no whole block of a cipher
    refused = [(ask(b"s" * 32, wrong=True), ERR.PIN_AUTH_INVALID),
               (ask(b"s" * 48), ERR.INVALID_LENGTH), (ask(b"s" * 96), ERR.INVALID_LENGTH),
               (asking({1: agreement, 2: torn, 3: proto.authenticate(shared, torn), 4: proto.VERSION}),
                ERR.INVALID_LENGTH),
               (asking({1: agreement, 2: b"s" * 32}), ERR.MISSING_PARAMETER),
               (asking({1: agreement, 2: "s" * 32, 3: "m" * 32, 4: proto.VERSION}), ERR.PIN_AUTH_INVALID)]
    if proto.VERSION == 2:
        refused.append((ask(b"s" * 32, version=False), ERR.PIN_AUTH_INVALID))
    for call, code in refused:
        with pytest.raises(CtapError) as e:
            call()
        assert e.value.code == code
    assert key.touches == touches                                       # refused before any touch


def test_hmac_secret_answers_each_passkey_a_key_gives_one_after_another_under_its_own_secret():
    key = ctap_soft.SoftKey()
    for user in (b"user-1", b"user-2"):
        made_with_hmac_secret(key, user, rk=True)
    salt = b"1" * 32
    answers = hmac_client(key).get_assertion(PublicKeyCredentialRequestOptions(
        challenge=b"c" * 32, rp_id=RP, user_verification=UserVerificationRequirement.REQUIRED,
        extensions={"hmacGetSecret": {"salt1": salt}}))                # no allow list: every passkey for the rpId
    assert [name for name, _ in key.requests].count("getNextAssertion") == 1
    got = [answers.get_response(i) for i in range(2)]
    assert [g.raw_id for g in got] == [c.id for c in key.credentials]
    assert [g.client_extension_results.hmac_get_secret.output1 for g in got] == \
        [hmac_of(c.secrets[0], salt) for c in key.credentials]


# ---- reading a step --------------------------------------------------------------------------------------------------

ADE = "https://ade.example.com"
HOST = "ade.example.com"
ACCOUNT = "5f0c2a9e7b1d4c3e8a6f9b2d1c0e4a7b"
TO = "9b1e2f4071c34d5e8a9b0c1d2e3f4a5b"
ID = "0123456789abcdef0123456789abcdef"
NOW = 1791511200                    # 2026-10-09 02:00:00 UTC, as in the worked example (DESIGN_EXAMPLE)
# a nonce of the form passkeys._new_challenge gives (the worked example's, made with a stand-in MAC key)
NONCE = ("eyJjIjoiQUFFQ0F3UUZCZ2NJQ1FvTERBME9EeEFSRWhNVUZSWVhHQmthR3h3ZEhoOCIsInAiOiJjbWQ6aW5zdHJ1Y3Rpb24iLCJ1IjoiNWYw"
         "YzJhOWU3YjFkNGMzZThhNmY5YjJkMWMwZTRhN2IiLCJlIjoxNzkxNTExNTAwfQ.s9eKKl7pYWudnL966HFPRxiURXc8eRI7IN2Cn17wZk8")
WORDS = "Run the tests in open_edition, then fix the failing one.\nDo not push."
QUESTION = "May I run the database migration now?"
SESSION = "3f1c9d2e-7a4b-4c8d-9e0f-1a2b3c4d5e6f"
KEY = bytes(range(32))
PREV = hashlib.sha256(b"the statement before").hexdigest()
DROP = object()
SETTING = ade_link.SignerSetting(ADE, ACCOUNT)
VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors", "second_screen_v1.json")
ESC, CSI, RLO, LRI, NBSP, ZWSP = chr(0x1B), chr(0x9B), chr(0x202E), chr(0x2066), chr(0xA0), chr(0x200B)
# a field the ADE fills to move the cursor and clear the screen, to reverse what follows, and to start a line
HOSTILE = "a" + ESC + "[2J" + ESC + "[H" + CSI + "2J" + RLO + "b\nc" + LRI + "d\\e"
# a name that ends its quotes early and lines up a kind of its own, as the screen writes kinds after three spaces
FORGED = 'pp-r57"   (this Mac: its own folder says so)'


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def payload(purpose="instruction", **over):
    """A payload of `purpose` as the page composes one, made at NOW; `over` replaces a field, or drops it (DROP)."""
    p = {"v": 2, "deployment": HOST, "purpose": purpose, "account": ACCOUNT, "nonce": NONCE, "id": ID,
         "created_at": NOW, "expires_at": NOW + 3600}
    if purpose in ("instruction", "answer", "resume", "move"):
        p["to"] = TO
    if purpose == "instruction":
        p["text"] = WORDS
    elif purpose == "answer":
        p.update(text="Yes. Run it, then stop and tell me what changed.", ref=12,
                 digest="sha256:" + hashlib.sha256(QUESTION.encode("utf-8")).hexdigest())
    elif purpose == "resume":
        p.update(text="", expires_at=NOW + 600)
    elif purpose == "move":
        p["session"] = SESSION
    elif purpose == "e2e-key":
        p.update(seq=2, prev=PREV, key=b64u(KEY), key_id=hashlib.sha256(KEY).hexdigest()[:32], retires="grace",
                 expires_at=NOW + 600)
    elif purpose == "e2e-off":
        p.update(seq=3, prev=PREV, expires_at=NOW + 600)
    for name, value in over.items():
        if value is DROP:
            p.pop(name, None)
        else:
            p[name] = value
    return p


def text_of(p):
    return canonicalize(p).decode("utf-8")


def read(p, now=NOW, setting=SETTING):
    return ade_link.sign_read(setting, p if isinstance(p, (str, bytes)) else text_of(p), now)


def refusal(p, now=NOW, setting=SETTING):
    with pytest.raises(ade_link.SignRefused) as e:
        read(p, now, setting)
    return str(e.value)


@pytest.mark.parametrize("url, origin, host", [
    ("https://ade.example.com", "https://ade.example.com", "ade.example.com"),
    ("https://ADE.Example.com/", "https://ade.example.com", "ade.example.com"),
    ("https://ade.example.com:443", "https://ade.example.com", "ade.example.com"),
    ("https://ade.example.com:8443", "https://ade.example.com:8443", "ade.example.com:8443"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000", "127.0.0.1:8000"),
    ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example", "xn--bcher-kva.example"),
])
def test_a_signer_signs_for_the_address_it_was_given(url, origin, host):
    s = ade_link.SignerSetting(url, ACCOUNT)
    assert (s.origin(), s.host(), s.rp_id(), s.account) == (origin, host, host.split(":")[0], ACCOUNT)
    # the client data names the origin as the server's origin() writes it, never the address as it was typed
    data = json.loads(ade_link.sign_client_data(s, canonicalize(payload(deployment=host))))
    assert data["origin"] == origin


@pytest.mark.parametrize("url, account", [
    ("ftp://ade.example.com", ACCOUNT), ("ade.example.com", ACCOUNT), ("https://", ACCOUNT), (None, ACCOUNT),
    ("https://b" + chr(0xFC) + "cher.example", ACCOUNT), ("https://ade.example.com:99999", ACCOUNT),
    ("https://ade.example.com:port", ACCOUNT), ("https://[::1]:8443", ACCOUNT), ("https://ade_x.example.com", ACCOUNT),
    ("https://ade.example.com", ACCOUNT.upper()), ("https://ade.example.com", ACCOUNT[:31]),
    ("https://ade.example.com", None),
])
def test_a_signer_refuses_an_address_or_an_account_it_cannot_sign_for(url, account):
    with pytest.raises(ValueError):
        ade_link.SignerSetting(url, account)


@pytest.mark.parametrize("purpose", ade_link.SIGN_PURPOSES)
def test_each_kind_is_read_from_its_canonical_bytes(purpose):
    p = payload(purpose)
    got, raw = read(p)
    assert got == p and raw == canonicalize(p) == ade_link.canonical(p) == text_of(p).encode("utf-8")


FIELDS = "A {} has exactly the fields {}."
LASTS = "A {} lasts 60 to {} seconds."


@pytest.mark.parametrize("what, given, words", [
    ("bytes, not text", lambda: text_of(payload()).encode("utf-8"), "It is not a payload of at most 20,000 bytes."),
    ("more than 20,000 bytes", lambda: payload(pad="x" * 20000), "It is not a payload of at most 20,000 bytes."),
    ("not JSON", lambda: "{", "It is not a payload in canonical form (RFC 8785, no key twice)."),
    ("a key twice", lambda: text_of(payload())[:-1] + ',"v":2}',
     "It is not a payload in canonical form (RFC 8785, no key twice)."),
    ("a number past 2**53", lambda: text_of(payload())[:-1] + ',"w":9007199254740993}',
     "It is not a payload in canonical form (RFC 8785, no key twice)."),
    ("spaces", lambda: json.dumps(payload(), sort_keys=True), "It is not in canonical form (RFC 8785) as it came."),
    ("keys out of order", lambda: json.dumps(payload(), separators=(",", ":")),
     "It is not in canonical form (RFC 8785) as it came."),
    ("an escape where the character is written", lambda: text_of(payload(text="caf" + chr(0xE9))).replace(
        chr(0xE9), "\\u00e9"), "It is not in canonical form (RFC 8785) as it came."),
    ("a list", lambda: "[1]", "It is not in canonical form (RFC 8785) as it came."),
    ("a kind it does not sign", lambda: payload(purpose="link-approve"),
     "It is not a kind this program signs: an instruction, an answer, a resume, a move, or a statement of the "
     "encryption keys."),
    ("a sign-in", lambda: payload(purpose="signin"),
     "It is not a kind this program signs: an instruction, an answer, a resume, a move, or a statement of the "
     "encryption keys."),
    ("a field missing", lambda: payload(id=DROP),
     FIELDS.format("instruction", "account, created_at, deployment, expires_at, id, nonce, purpose, text, to, v")),
    ("a challenge of its own", lambda: payload(challenge="AAAA"),
     FIELDS.format("instruction", "account, created_at, deployment, expires_at, id, nonce, purpose, text, to, v")),
    ("a move's words", lambda: payload("move", text="hi"),
     FIELDS.format("move", "account, created_at, deployment, expires_at, id, nonce, purpose, session, to, v")),
    ("v 1", lambda: payload(v=1), "Its v is not 2."),
    ("v as text", lambda: payload(v="2"), "Its v is not 2."),
    ("v true", lambda: payload(v=True), "Its v is not 2."),
    ("deployment a number", lambda: payload(deployment=7), "Its deployment is not text."),
    ("account a list", lambda: payload(account=[ACCOUNT]), "Its account is not text."),
    ("nonce null", lambda: payload(nonce=None), "Its nonce is not text."),
    ("id a number", lambda: payload(id=1), "Its id is not text."),
    ("to a number", lambda: payload(to=1), "Its to is not text."),
    ("another ADE", lambda: payload(deployment="other.example.com"),
     "It is for another ADE: this signer signs for ade.example.com."),
    ("another port", lambda: payload(deployment="ade.example.com:8443"),
     "It is for another ADE: this signer signs for ade.example.com."),
    ("another account", lambda: payload(account="0" * 32),
     "It names another account than the one this signer was paired with."),
    ("an id in capitals", lambda: payload(id=ID.upper()), "Its id is not 32 lowercase hex characters."),
    ("an id too short", lambda: payload(id=ID[:31]), "Its id is not 32 lowercase hex characters."),
    ("an id too long", lambda: payload(id=ID + "0"), "Its id is not 32 lowercase hex characters."),
    ("an id with an escape", lambda: payload(id=ID[:28] + ESC + "[2J"), "Its id is not 32 lowercase hex characters."),
    ("a nonce with an escape", lambda: payload(nonce=NONCE + ESC + "[2J"),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a nonce padded with controls", lambda: payload(nonce=NONCE[:20] + "\x01\x02\x03\x04" + NONCE[20:]),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a nonce of three parts", lambda: payload(nonce=NONCE + ".x"),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a nonce of one part", lambda: payload(nonce=NONCE.replace(".", "")),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a nonce with padding", lambda: payload(nonce=NONCE + "="),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a nonce of 601 characters", lambda: payload(nonce="a" * 300 + "." + "b" * 300),
     "Its nonce is not in its form: two parts of base64url joined by a dot, at most 600 characters."),
    ("a to in capitals", lambda: payload(to=TO.upper()), "Its to is not a linked session's id: 32 lowercase hex "
                                                         "characters."),
    ("a to of 33", lambda: payload(to=TO + "a"), "Its to is not a linked session's id: 32 lowercase hex characters."),
    ("a to with an escape", lambda: payload(to=ESC + "[2J" + TO[4:]),
     "Its to is not a linked session's id: 32 lowercase hex characters."),
    ("a to with a line feed after it", lambda: payload(to=TO + "\n"),
     "Its to is not a linked session's id: 32 lowercase hex characters."),
    ("times as text", lambda: payload(created_at=str(NOW)), "Its times are not whole unix seconds."),
    ("a time true", lambda: payload(expires_at=True), "Its times are not whole unix seconds."),
    ("made too early", lambda: payload(created_at=NOW - 301, expires_at=NOW + 3000),
     "It was made more than 5 minutes from this Mac's clock."),
    ("made too late", lambda: payload(created_at=NOW + 301, expires_at=NOW + 3000),
     "It was made more than 5 minutes from this Mac's clock."),
    ("expired", lambda: payload(created_at=NOW - 120, expires_at=NOW), "It has expired."),
    ("a life too short", lambda: payload(expires_at=NOW + 59), LASTS.format("instruction", "86,400")),
    ("an instruction too long", lambda: payload(expires_at=NOW + 86401), LASTS.format("instruction", "86,400")),
    ("an answer too long", lambda: payload("answer", expires_at=NOW + 86401), LASTS.format("answer", "86,400")),
    ("a resume too long", lambda: payload("resume", expires_at=NOW + 601), LASTS.format("resume", "600")),
    ("a move too long", lambda: payload("move", expires_at=NOW + 3601), LASTS.format("move", "3,600")),
    ("a new key too long", lambda: payload("e2e-key", expires_at=NOW + 601), LASTS.format("e2e-key", "600")),
    ("encryption off too long", lambda: payload("e2e-off", expires_at=NOW + 601), LASTS.format("e2e-off", "600")),
    ("words empty", lambda: payload(text=" \n "), "Its words cannot be signed: they are empty."),
    ("an answer's words with an escape", lambda: payload("answer", text="yes" + ESC + "[2J"),
     "Its words cannot be signed: line 1, character 4 holds [U+001B], a control character."),
    ("a resume with words", lambda: payload("resume", text="go"), "A resume carries no words."),
    ("an answer to seq 0", lambda: payload("answer", ref=0),
     "An answer names its question by seq and its words by sha256:, as 64 lowercase hex."),
    ("an answer's ref as text", lambda: payload("answer", ref="12"),
     "An answer names its question by seq and its words by sha256:, as 64 lowercase hex."),
    ("an answer's digest in capitals", lambda: payload("answer", digest="sha256:" + "AB" * 32),
     "An answer names its question by seq and its words by sha256:, as 64 lowercase hex."),
    ("an answer's digest unnamed", lambda: payload("answer", digest="ab" * 32),
     "An answer names its question by seq and its words by sha256:, as 64 lowercase hex."),
    ("an answer's digest a number", lambda: payload("answer", digest=5),
     "An answer names its question by seq and its words by sha256:, as 64 lowercase hex."),
    ("a move of a path", lambda: payload("move", session="../" + SESSION),
     "A move's session is a Claude Code session's id."),
    ("a move of nothing", lambda: payload("move", session=""), "A move's session is a Claude Code session's id."),
    ("a move of a number", lambda: payload("move", session=5), "A move's session is a Claude Code session's id."),
    ("a statement's prev not hex", lambda: payload("e2e-off", prev="x" * 64),
     "It is not a statement of the encryption keys in its form."),
    ("a first statement with a prev", lambda: payload("e2e-off", seq=1),
     "It is not a statement of the encryption keys in its form."),
    ("a key whose id is another's", lambda: payload("e2e-key", key_id="0" * 32),
     "It is not a statement of the encryption keys in its form."),
    ("a key retiring some", lambda: payload("e2e-key", retires="some"),
     "It is not a statement of the encryption keys in its form."),
    ("a statement of seq 0", lambda: payload("e2e-off", seq=0),
     "It is not a statement of the encryption keys in its form."),
    ("a key of seq -1", lambda: payload("e2e-key", seq=-1),
     "It is not a statement of the encryption keys in its form."),
    ("a move too near its end for its hold", lambda: payload("move", expires_at=NOW + 299),
     "A move must have 300 seconds of its life left past its hold when it is sent, and this one has less."),
    ("a move made a while ago, now too near its end", lambda: payload("move", created_at=NOW - 100,
                                                                      expires_at=NOW + 250),
     "A move must have 300 seconds of its life left past its hold when it is sent, and this one has less."),
])
def test_what_does_not_hold_is_refused_naming_the_first_rule_it_fails(what, given, words):
    assert refusal(given()) == words


def test_a_payload_nested_past_the_reader_is_refused_as_not_canonical():
    nested = "[" * 10000 + "]" * 10000                                  # within the 20,000 bytes the server takes
    assert refused_by(lambda: ade_link.sign_read(SETTING, nested, NOW)) == \
        "It is not a payload in canonical form (RFC 8785, no key twice)."


def test_the_limits_themselves_are_read():
    for p in (payload(nonce="a" * 300 + "." + "b" * 299), payload(created_at=NOW - 300, expires_at=NOW + 3000),
              payload(created_at=NOW + 300, expires_at=NOW + 3000), payload(created_at=NOW - 299, expires_at=NOW + 1),
              payload(expires_at=NOW + 60), payload(expires_at=NOW + 86400), payload("resume", expires_at=NOW + 600),
              payload("move", expires_at=NOW + 3600), payload("e2e-key", expires_at=NOW + 600),
              payload(text="w" * 4000), payload("e2e-off", seq=1, prev=""), payload("move", expires_at=NOW + 300),
              payload("e2e-off", seq=2 ** 53 - 1)):
        assert read(p)[0] == p


def test_what_is_said_beside_a_step_in_a_shape_this_program_does_not_read_is_read_as_not_said():
    # the ADE's word, and this Mac's own records, in shapes no ADE or link writes, and times no clock can write: the
    # screen says nothing of them, or "an unknown time", and the ceremony goes on to sign, never stopping on an error
    # that is not a refusal
    odd = [{"links": [1]}, {"links": {TO: [1]}}, {"links": {TO: {"name": "x", "stopped_at": 10 ** 18}}},
           {"question": [1]}, {"app": "x"}, {"allow": "x"}, {"allow": 5}, {"parked_from": "x", "parked_at": 10 ** 18},
           [1], "x"]
    for ade in odd:
        lines = screen("instruction", ade=ade)
        assert lines[7].startswith("  ADE "), ade
    unknown = "               stopped since an unknown time   (the ADE's word)"
    assert unknown in screen(ade=odd[2]) and unknown in screen(ade={"links": {TO: {"stopped_at": -1}}})
    assert '  Parked from  "x" at an unknown time   (the ADE\'s word)' in screen(ade=odd[7])

    def refusing(ts):                                                   # a Mac's clock that cannot write this time
        if ts == NOW - 7:
            raise OSError(75, "Value too large")
        return time.gmtime(ts)
    got, raw = read(payload(), NOW + 29)
    lines = ade_link.sign_screen(SETTING, got, raw, None, {"links": {TO: {"stopped_at": NOW - 7}}}, NOW + 29,
                                 NOW + 300, local_time=refusing)
    assert unknown in lines
    for local in ({"links": [1]}, {"posted": 5}, {"posted": {TO: [1]}}, {"sessions": "x"}, {"pins": 7},
                  {"pins": [1, "3a7f0c1e" + "0" * 24]}, {"statement": [1]}, [1]):
        for purpose in ("instruction", "answer", "move", "e2e-key"):
            assert screen(purpose, local=local)[7].startswith("  ADE "), (local, purpose)
    for ade in ({"links": {TO: [1]}, "question": "x"}, "x", [1]):
        s = Signer()
        s.sign(Term(["sign", "1234"], s.key), ade=ade, local={"links": 5})
        assert s.key.signatures == 1


def test_a_passkey_named_in_a_shape_this_program_does_not_read_is_passed_over():
    # the ADE's list of the passkeys that may sign, holding something that is not a credential id
    s = Signer()
    s.sign(Term(["sign", "1234"], s.key), allow=[{"id": s.id}, [s.id], s.id])
    assert s.key.signatures == 1
    for allow in ([{"id": s.id}], {"id": s.id}, s.id, 5):
        assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key), allow=allow)) == \
            "None of the passkeys the ADE says may sign this is one of this account's keys this Mac holds. Nothing " \
            "was signed."
    for keys in ({"keys": {"credential_id": s.id}}, [s.keys], {"keys": [s.id]}, {"keys": 5}, 5):
        assert refused_by(lambda: ade_link.sign_ceremony(s.setting, keys, s.text(), Term(["sign"]), [s.id],
                                                         now=s.now)).startswith("None of the passkeys")


def test_a_signer_paired_with_a_port_signs_for_that_host_and_port():
    s = ade_link.SignerSetting("https://ade.example.com:8443", ACCOUNT)
    assert read(payload(deployment="ade.example.com:8443"), setting=s)[0]["deployment"] == "ade.example.com:8443"
    assert refusal(payload(), setting=s) == "It is for another ADE: this signer signs for ade.example.com:8443."


# ---- the words: the server's rules, every code point -----------------------------------------------------------------

def server_takes(text):
    try:
        link_commands.check_text(text, "words")
        return True
    except link.LinkError:
        return False


def test_words_are_held_to_the_server_s_rules_on_every_code_point():
    # so the Mac never spends a touch on words the server refuses, and never refuses words the server takes
    differ = [hex(c) for c in range(0x110000) if (ade_link.sign_words_refusal("a" + chr(c) + "b") is None)
              != server_takes("a" + chr(c) + "b")]
    assert differ == []


def word_cases():
    """(what, words) for the vectors: each class the server refuses, with marks drawn over or around a letter among
    them, and words it takes that the screen marks or lists; built from code points, never written as characters."""
    c = chr
    return [
        ("a newline and a tab", "one\ttwo\nthree"),
        ("a bell", "ring" + c(0x07)), ("an escape", "a" + ESC + "[2J"), ("delete", "a" + c(0x7F)),
        ("a C1 control (next line)", "a" + c(0x85) + "b"), ("a C1 control (CSI)", "a" + CSI + "2J"),
        ("a carriage return", "a\rb"),
        ("a zero-width space", "pu" + ZWSP + "sh"), ("a right-to-left override", "a" + RLO + "b"),
        ("a left-to-right isolate", "a" + LRI + "b"), ("a soft hyphen", "a" + c(0xAD) + "b"),
        ("a byte order mark", "a" + c(0xFEFF) + "b"), ("a line separator", "a" + c(0x2028) + "b"),
        ("a paragraph separator", "a" + c(0x2029) + "b"), ("private use", "a" + c(0xE000) + "b"),
        ("a tag character", "a" + c(0xE0041) + "b"), ("a lone surrogate", "a" + c(0xD800) + "b"),
        ("a Hangul filler", "a" + c(0x3164) + "b"), ("a blank Braille pattern", "a" + c(0x2800) + "b"),
        ("the combining grapheme joiner", "a" + c(0x034F) + "b"),
        ("a variation selector", "a" + c(0xFE00) + "b"),
        ("the emoji selector after a character", "go" + c(0x2764) + c(0xFE0F)),
        ("the emoji selector at the start", c(0xFE0F) + "go"),
        ("the text selector after another", "go" + c(0x2764) + c(0xFE0E) + c(0xFE0F)),
        ("a long stroke overlay", "do n" + c(0x0336) + "ot push"),
        ("a long solidus overlay", "a =" + c(0x0338) + " b"),
        ("a long solidus overlay after a letter", "push" + c(0x0338)),
        ("an enclosing keycap after a letter", "a" + c(0x20E3) + "b"),
        ("an unassigned code point", "a" + c(0x0378) + "b"),
        ("an em space", "a" + c(0x2003) + "b"),
        ("an ideographic space", "a" + c(0x3000) + "b"),
        ("a narrow no-break space", "a" + c(0x202F) + "b"),
        ("a combining long vertical line overlay", "a" + c(0x20D2) + "b"),
        ("an enclosing circle", "a" + c(0x20DD) + "b"),
        ("an enclosing circle backslash", "push" + c(0x20E0)),
        ("a keycap", "1" + c(0xFE0F) + c(0x20E3)),
        ("not in normal form C", "cafe" + c(0x0301)),
        ("normal form C", "caf" + c(0xE9)),
        ("a secret's shape", "use sk-ant-api03-abc"),
        ("a sealed text's start", "e2e1.AAAA"),
        ("empty", " \n\t "),
        ("4,001 characters", "w" * 4001),
        ("a no-break space and spaces at the end", "a" + NBSP + "b  "),
        ("three empty lines", "a\n\n\n\nb"),
        ("two empty lines", "a\n\n\nb"),
        ("a run of spaces lining words up as the screen's column of kinds",
         "Run the tests." + " " * 61 + "then git push --force origin main   (the ADE's word)"),
        ("spaces at the start of a line", "Run this:\n    git status"),
        ("Cyrillic in a Latin word", "op" + c(0x0435) + "n the door"),
        ("Greek in a Latin word", "g" + c(0x03BF) + " home"),
        ("fullwidth letters", c(0xFF41) + c(0xFF44) + c(0xFF45)),
        ("a mathematical letter", "x = " + c(0x1D41A)),
        ("right-to-left text", "say " + c(0x05E9) + c(0x05DC) + c(0x05D5) + c(0x05DD) + " now"),
        ("a stack of marks", "x" + c(0x0323) + c(0x0302) + c(0x0303) + "b"),
        ("two marks", "x" + c(0x0323) + c(0x0302) + "b"),
    ]


def test_a_refused_character_is_named_with_its_place_and_never_drawn():
    why = ade_link.sign_words_refusal("first line\nsecond " + RLO + "line")
    assert why == "line 2, character 8 holds [U+202E RIGHT-TO-LEFT OVERRIDE], which is not shown as itself"
    assert ade_link.sign_words_refusal("ab" + ESC) == "line 1, character 3 holds [U+001B], a control character"


@pytest.mark.parametrize("mark", [0x0334, 0x0336, 0x0338, 0x20D2, 0x20D3, 0x20D8, 0x20DD, 0x20E0, 0x20E3, 0x20E5])
def test_one_mark_drawn_over_or_around_a_letter_is_refused(mark):
    why = ade_link.sign_words_refusal("do n" + chr(mark) + "ot push")
    assert why == f"line 1, character 5 holds {ade_link._sign_name(chr(mark))}, a mark drawn over or around the " \
                  "character before it"


def test_a_mark_drawn_over_is_known_by_its_own_properties_too(monkeypatch):
    # a newer Unicode's overlay or enclosing mark is refused here whatever list the server holds
    monkeypatch.setattr(ade_link, "_DRAWN_OVER", frozenset())
    for mark in (0x0336, 0x20E0):
        assert ade_link.sign_words_refusal("n" + chr(mark) + "ot").endswith("a mark drawn over or around the "
                                                                           "character before it")
    assert ade_link.sign_words_refusal("caf" + chr(0xE9)) is None


def test_the_secret_shapes_are_the_server_s():
    for words in ("key sk-ant-api03-x", "sk-" + "a" * 16, "ghp_abc", "github_pat_x", "FlyV1 x", "AKIAABC",
                  "-----BEGIN RSA PRIVATE KEY", "adeh_x", "PP-ABCDEFGH", "sk_live_x", "xoxb-1", "AIza" + "a" * 30,
                  "glpat-x", "npm_" + "a" * 36, "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + ".c"):
        assert ade_link.sign_words_refusal(words) == "they hold something shaped like a secret, which the ADE " \
                                                     "keeps none of"
        assert not server_takes(words)
    for words in ("ask the sk-ant team", "sk-" + "a" * 15, "AKI", "PP-ABCDEFG", "npm_" + "a" * 35, "task-ant-x"):
        assert ade_link.sign_words_refusal(words) is None and server_takes(words), words


# ---- the screen ------------------------------------------------------------------------------------------------------

def screen(purpose="instruction", local=None, ade=None, now=NOW + 29, deadline=NOW + 300, **over):
    p = payload(purpose, **over)
    got, raw = read(p, now)
    return ade_link.sign_screen(SETTING, got, raw, local, ade, now, deadline, local_time=time.gmtime)


def scenes():
    """The golden screens' inputs: each kind, with this Mac's own records and the ADE's word, as the vectors keep
    them (local time taken as UTC)."""
    allow = [{"name": "YubiKey 5C", "fingerprint": "3a7f0c1e" + "0" * 24}, {"name": "Spare key",
                                                                            "fingerprint": "91c04d2b" + "1" * 24}]
    held = {"seq": 1, "sha256": PREV}
    return [
        ("instruction", {"links": {TO: "pp-r57"}, "pins": ["3a7f0c1e" + "0" * 24]},
         {"links": {TO: {"name": "pp-r57"}}, "allow": allow, "high_security": True, "parked_from": "203.0.113.7",
          "parked_at": NOW + 9}),
        ("answer", {}, {"links": {TO: {"name": "cloud-runner"}}, "question": {"text": QUESTION}, "allow": allow[:1],
                        "parked_from": "203.0.113.7", "parked_at": NOW + 9}),
        ("resume", {"links": {TO: "pp-r57"}}, {"links": {TO: {"name": "pp-r57", "stopped_at": NOW - 600}},
                                               "allow": allow[:1]}),
        ("move", {"links": {TO: "pp-r57"}, "sessions": {SESSION: {"folder": "/Users/owner/proofpane",
                                                                  "last": NOW - 120, "running": False}}},
         {"title": "Fix the failing test", "allow": allow[:1]}),
        ("e2e-key", {"statement": held}, {"allow": allow[:1]}),
        ("e2e-off", {"statement": {"seq": 2, "sha256": "f" * 64}}, {"allow": allow[:1]}),
    ]


def test_the_head_says_what_each_kind_of_line_is_and_what_a_row_that_goes_on_begins_with():
    lines = screen()
    assert lines[:7] == [
        "PROOFPANE SECOND SCREEN",
        "Your security key is about to sign what follows. This program decoded it",
        "from the exact bytes it signs. Lines marked (signed) are decoded from those",
        "bytes, the only lines that bind; (this Mac) is this Mac's own record;",
        "(the ADE's word) is what the ADE says, which binds nothing. A row that begins",
        'with "  > " is the rest of the row above it, cut where the terminal wraps it.', ""]
    assert ade_link.sign_rows(lines[:7], 80) == lines[:7]               # none of it is cut at 80 columns
    assert lines[7] == "  ADE          https://ade.example.com   (this Mac: this signer's own setting)"
    assert lines[8] == f"  Account      {ACCOUNT}   (this Mac: the account this signer was paired with)"


def test_the_nonce_is_never_shown_and_every_line_says_what_it_is():
    for purpose, local, ade in scenes():
        lines = screen(purpose, local, ade)
        assert not any(NONCE in line or NONCE.split(".")[1] in line for line in lines), purpose
        rows = [line for line in lines if line.startswith("  ") and not line.startswith(("  |", "  END", "  The "))]
        assert all(line.endswith(")") for line in rows if line.strip()), [r for r in rows if not r.endswith(")")]


def test_a_target_this_mac_holds_is_named_by_its_own_folder_and_any_other_by_the_ade():
    mine = screen(local={"links": {TO: "pp-r57"}}, ade={"links": {TO: {"name": "spoofed"}}})
    assert f"  To           link {TO}   (signed)" in mine
    assert '               this Mac\'s link "pp-r57"   (this Mac: its own folder says so)' in mine
    assert not any("spoofed" in line for line in mine)
    theirs = screen(ade={"links": {TO: {"name": "cloud-runner", "stopped_at": NOW - 600}}})
    assert '               not a link of this Mac; the ADE calls it "cloud-runner"   (the ADE\'s word)' in theirs
    assert "               stopped since 2026-10-09 01:50:00 local (01:50:00 UTC)   (the ADE's word)" in theirs
    assert '               not a link of this Mac; the ADE calls it nothing   (the ADE\'s word)' in screen()


def test_an_answer_shows_its_question_only_when_its_digest_is_the_one_signed():
    rows = screen("answer", ade={"question": {"text": QUESTION}})
    assert "  Question     seq 12 on that session's timeline   (signed)" in rows
    i = rows.index("  The question, as the ADE served it (the ADE's word: matches the digest you sign):")
    assert rows[i + 1:i + 3] == ["  | " + QUESTION, "  END OF THE QUESTION: 1 line, 37 characters, all ASCII."]
    assert any("this Mac's link posted that question with that digest" in r
               for r in screen("answer", local={"posted": {TO: {12: payload("answer")["digest"]}}}))
    # as a link's posted.json keeps it: its seq as text, its digest as text or in an object
    digest = payload("answer")["digest"]
    for posted in ({"12": digest}, {"12": {"digest": digest}}, {12: {"digest": digest}}):
        assert any("this Mac's link posted that question with that digest" in r
                   for r in screen("answer", local={"posted": {TO: posted}})), posted
    assert any("this Mac's link posted that question with ANOTHER digest" in r
               for r in screen("answer", local={"posted": {TO: {"12": {"digest": "sha256:" + "0" * 64}}}}))
    assert any("with ANOTHER digest" in r for r in screen("answer", local={"posted": {TO: {12: "sha256:" + "0" * 64}}}))
    assert any("sealed in place" in r for r in screen("answer", ade={"question": {"sealed": "in place"}}))
    assert any("the digest is of its envelope" in r for r in screen("answer", ade={"question": {"sealed": True}}))
    assert any("the ADE gave no words for the question" in r for r in screen("answer"))
    other = screen("answer", ade={"question": {"text": QUESTION + " Now?"}})
    shown = "               the ADE served a question whose digest is not this one: not shown   (the ADE's word)"
    assert shown in other
    assert not any("Now?" in r for r in other)
    with pytest.raises(ade_link.SignRefused):
        ade_link.sign_check_context(payload("answer"), {"question": {"text": QUESTION + "?"}})
    ade_link.sign_check_context(payload("answer"), {"question": {"text": "other", "sealed": True}})
    ade_link.sign_check_context(payload("answer"), {"question": {"text": QUESTION}})


def test_a_move_says_what_this_mac_knows_of_the_session():
    found = screen("move", {"sessions": {SESSION: {"folder": "/Users/owner/x", "last": NOW - 120, "running": True}}})
    assert any('Claude Code\'s own transcript on this Mac, in "/Users/owner/x", last active 2026-10-09 01:58:00 local '
               "(01:58:00 UTC); running now   (this Mac)" in r for r in found)
    assert any("not found among this Mac's sessions   (this Mac)" in r for r in screen("move"))
    assert '  Title        "Fix it", sent beside the payload, not signed   (the ADE\'s word)' in \
        screen("move", ade={"title": "Fix it"})


def test_a_statement_says_whether_it_follows_the_one_this_mac_holds():
    assert any("this one follows it   (this Mac)" in r for r in screen("e2e-key", {"statement": {"seq": 1,
                                                                                             "sha256": PREV}}))
    assert any("this one DOES NOT follow it   (this Mac)" in r
               for r in screen("e2e-key", {"statement": {"seq": 1, "sha256": "0" * 64}}))
    assert any("this one DOES NOT follow it" in r for r in screen("e2e-key", {"statement": {"seq": 2,
                                                                                           "sha256": PREV}}))
    assert any("hold no statement to compare" in r for r in screen("e2e-off"))
    assert "  Kind         TURNS END-TO-END ENCRYPTION OFF for this account   (signed)" in screen("e2e-off")
    rows = screen("e2e-key")
    assert "  Retires      the earlier keys after a grace (a rotation)   (signed)" in rows
    assert "  Retires      every earlier key at once (a revocation)   (signed)" in screen("e2e-key", retires="all")


def test_an_app_s_proposal_and_the_signers_are_the_ade_s_word():
    rows = screen(ade={"app": {"name": "n8n", "words": "other words"}, "high_security": True,
                       "allow": [{"name": "YubiKey 5C", "fingerprint": "3a7f" + "0" * 28}]},
                  local={"pins": ["3a7f" + "0" * 28]})
    assert '  Proposed by  the app "n8n"   (the ADE\'s word)' in rows
    assert "               the words it proposed differ from the words you sign   (the ADE's word)" in rows
    assert "  Signs with   a passkey of this account on the key you touch   (this Mac)" in rows
    assert '               which the ADE names "YubiKey 5C" (3a7f0000...) (in this Mac\'s pin held outside the ' \
           'server)   (the ADE\'s word)' in rows
    assert "               the ADE takes only hardware keys now   (the ADE's word: high security is on)" in rows
    same = screen(ade={"app": {"name": "n8n", "words": WORDS}})
    assert not any("differ" in r for r in same)


def hostile_scene():
    """Every string the ADE gives, and each of this Mac's own a folder holds, carrying an escape sequence, a C1
    control, a bidi override and a line feed."""
    ade = {"links": {TO: {"name": HOSTILE}}, "app": {"name": HOSTILE, "words": HOSTILE}, "title": HOSTILE,
           "question": {"text": HOSTILE}, "parked_from": HOSTILE, "parked_at": NOW,
           "allow": [{"name": HOSTILE, "fingerprint": HOSTILE}]}
    local = {"sessions": {SESSION: {"folder": HOSTILE, "last": NOW}}}
    return local, ade


def test_nothing_the_ade_says_reaches_the_screen_raw():
    local, ade = hostile_scene()
    digest = "sha256:" + hashlib.sha256(HOSTILE.encode("utf-8")).hexdigest()
    for purpose, over in (("instruction", {}), ("answer", {"digest": digest}), ("move", {})):
        lines = screen(purpose, local, ade, **over)
        joined = "\n".join(lines)
        assert ESC not in joined and CSI not in joined and RLO not in joined and LRI not in joined, purpose
        assert not any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs") for line in lines for ch in line), \
            purpose
        assert NONCE not in joined


def test_this_mac_s_own_records_are_escaped_too():
    # a folder's name is a file anything on this Mac can write
    rows = screen(local={"links": {TO: HOSTILE}})
    assert '               this Mac\'s link "' + ade_link._flat(HOSTILE, 100) + '"   (this Mac: its own folder says ' \
           'so)' in rows


def field_cases():
    """(what, the screen line it is shown on) for each string the ADE gives, carrying HOSTILE."""
    local, ade = hostile_scene()
    digest = "sha256:" + hashlib.sha256(HOSTILE.encode("utf-8")).hexdigest()
    shown = {}
    for purpose, over in (("instruction", {}), ("answer", {"digest": digest}), ("move", {})):
        shown[purpose] = screen(purpose, local, ade, **over)
    flat = ade_link._flat(HOSTILE, 100)

    def line(purpose, start):
        return next(r for r in shown[purpose] if r.startswith(start))
    forged = screen("instruction", {}, {"links": {TO: {"name": FORGED}}})
    return [
        ("a link name the ADE gives", line("instruction", "               not a link of this Mac")),
        ("a link name that would forge the column of kinds",
         next(r for r in forged if r.startswith("               not a link of this Mac"))),
        ("an app's name", line("instruction", "  Proposed by")),
        ("a move's title", line("move", "  Title")),
        ("where the request was parked from", line("instruction", "  Parked from")),
        ("a passkey's name", line("instruction", "               which the ADE names")),
        ("a session's folder", line("move", "               Claude Code's own transcript")),
        ("a served question's words", "\n".join(shown["answer"][shown["answer"].index(
            "  The question, as the ADE served it (the ADE's word: matches the digest you sign):"):][:4])),
        ("the escaped value itself", flat),
    ]


def test_each_string_the_ade_gives_is_escaped_as_the_vectors_say():
    data = vectors_file()
    assert [(f["what"], f["shown"]) for f in data["fields"]] == field_cases()
    assert data["hostile"] == HOSTILE
    flat = dict(field_cases())["the escaped value itself"]
    assert flat == "a\\x1b[2J\\x1b[H\\x9b2J\\u202eb\\x0ac\\u2066d\\\\e"


def test_the_words_are_shown_with_what_does_not_show_itself_marked():
    lines = ade_link.sign_text_lines("go" + "\t" + "now" + NBSP + "x  \n\n\n\n\nend", "The words (signed):")
    assert lines == ["  The words (signed):", "  | go[U+0009]now[U+00A0]x[2 spaces]", "  | [4 empty lines]",
                     "  | end", '  line 1: [U+00A0 NO-BREAK SPACE] looks like " "',
                     "  END OF THE WORDS: 6 lines, 18 characters, 1 of them outside ASCII. Read what is marked above."]
    two = ade_link.sign_text_lines("a\n\n\nb", "W:")
    assert two == ["  W:", "  | a", "  |", "  |", "  | b", "  END OF THE WORDS: 4 lines, 5 characters, all ASCII."]
    # a run of spaces after a line's first other character is counted, so words cannot line up as the screen's own
    # column of kinds; spaces that begin a line, which only move it right of its bar, are drawn
    padded = ade_link.sign_text_lines("Run the tests." + " " * 61 + "then git push   (the ADE's word)\n    indented",
                                      "W:")
    assert padded[1:3] == ["  | Run the tests.[61 spaces]then git push[3 spaces](the ADE's word)", "  |     indented"]
    assert padded[-1].endswith("Read what is marked above.")


def test_what_reads_as_something_it_is_not_is_listed_below_the_words():
    c = chr
    notes = ade_link.sign_text_lines("op" + c(0x0435) + "n it", "W:")
    assert notes[2:4] == ["  line 1: [U+0435 CYRILLIC SMALL LETTER IE] in a word of LATIN letters",
                          "  line 1, that word: op[U+0435]n"]
    full = ade_link.sign_text_lines(c(0xFF41) + "b", "W:")
    assert full[2] == '  line 1: [U+FF41 FULLWIDTH LATIN SMALL LETTER A] looks like "a"'
    rtl = ade_link.sign_text_lines("say " + c(0x05E9) + c(0x05DC) + " now", "W:")
    assert rtl[2] == "  line 1 holds right-to-left text; in the order of its bytes: say [U+05E9 U+05DC] now"
    stack = ade_link.sign_text_lines("x" + c(0x0323) + c(0x0302) + c(0x0303) + "b", "W:")
    assert stack[2] == ("  line 1: [U+0078 LATIN SMALL LETTER X] carries 3 marks: [U+0323 COMBINING DOT BELOW] "
                        "[U+0302 COMBINING CIRCUMFLEX ACCENT] [U+0303 COMBINING TILDE]")
    assert stack[-1].endswith("Read what is marked above.")
    assert ade_link.sign_text_lines("x" + c(0x0323) + c(0x0302) + "b", "W:")[-1] == \
        "  END OF THE WORDS: 1 line, 4 characters, 2 of them outside ASCII."


def test_a_letter_or_digit_that_may_pass_for_ascii_is_listed_and_the_end_claims_only_a_count():
    c = chr
    # in a word that holds ASCII: a script g, a dotless i, a click letter, a Bengali zero; never a letter with an
    # accent drawn on it, which NFKD begins with its ASCII letter
    for word, ch in (("https://" + c(0x0261) + "ithub.com/x", c(0x0261)), ("ma" + c(0x0131) + "n", c(0x0131)),
                     ("stag" + c(0x01C0) + "ng", c(0x01C0)), ("1" + c(0x09E6), c(0x09E6))):
        lines = ade_link.sign_text_lines("fetch " + word, "W:")
        name = unicodedata.name(ch)
        assert lines[2:4] == [f"  line 1: [U+{ord(ch):04X} {name}] in a word of ASCII letters and digits",
                              "  line 1, that word: " + word.replace(ch, f"[U+{ord(ch):04X}]")], word
        assert lines[-1].endswith("1 of them outside ASCII. Read what is marked above.")
    assert ade_link.sign_text_lines("caf" + c(0xE9) + " au lait", "W:")[1:] == [
        "  | caf" + c(0xE9) + " au lait", "  END OF THE WORDS: 1 line, 12 characters, 1 of them outside ASCII."]
    # a whole word in a script that looks like Latin is not listed: the end says only how many are outside ASCII
    cyrillic = c(0x0440) + c(0x0435) + c(0x0440) + c(0x043E)
    assert ade_link.sign_text_lines("open " + cyrillic, "W:")[-1] == \
        "  END OF THE WORDS: 1 line, 9 characters, 4 of them outside ASCII."
    assert ade_link.sign_text_lines("all plain", "W:")[-1] == "  END OF THE WORDS: 1 line, 9 characters, all ASCII."
    # where ASCII ends: delete is in it, U+0080 is not (a question the ADE serves is shown, each marked)
    assert ade_link.sign_text_lines("a" + c(0x7F), "W:")[-1].startswith("  END OF THE WORDS: 1 line, 2 characters, "
                                                                        "all ASCII.")
    assert ade_link.sign_text_lines("a" + c(0x80), "W:")[-1].startswith("  END OF THE WORDS: 1 line, 2 characters, "
                                                                        "1 of them outside ASCII.")


def test_a_hangul_vowel_left_apart_from_a_syllable_is_marked_where_it_stands():
    # terminals draw a lone conjoining vowel as nothing: "1" U+1161 "0" would read as 10
    lines = ade_link.sign_text_lines("deploy to 1" + chr(0x1161) + "0", "W:")
    assert lines[1] == "  | deploy to 1[U+1161]0" and lines[-1].endswith("Read what is marked above.")
    assert ade_link._sign_mark(chr(0xD7B0) + chr(0xD7FB) + chr(0x11FF) + "x") == "[U+D7B0][U+D7FB][U+11FF]x"
    assert ade_link._sign_mark(chr(0xAC00) + chr(0x1200)) == chr(0xAC00) + chr(0x1200)   # a syllable, another script


# ---- the shared vectors ----------------------------------------------------------------------------------------------

DESIGN_EXAMPLE = payload(created_at=NOW, expires_at=NOW + 3600)


def vectors():
    """What tests/vectors/second_screen_v1.json holds, made here: each kind's payload, canonical text, challenge,
    request code and client data; a worked example (DESIGN_EXAMPLE); the words' cases with what the Mac does with each;
    the strings the ADE gives, escaped; refused payloads; and the golden screens."""
    def kind(p):
        raw = canonicalize(p)
        return {"payload": p, "canonical": raw.decode("utf-8"), "challenge": b64u(ade_link.step_challenge(raw)),
                "code": ade_link.sign_code(raw), "client_data": ade_link.sign_client_data(SETTING, raw).decode("ascii")}
    example = canonicalize(DESIGN_EXAMPLE)
    words = []
    for what, text in word_cases():
        why = ade_link.sign_words_refusal(text)
        words.append({"what": what, "words": text, "refused": why,
                      "shown": None if why else ade_link.sign_text_lines(text, "The words (signed):")})
    refused = [{"what": what, "payload": text_of(p), "refusal": refusal(p)} for what, p in (
        ("a to that is not 32 lowercase hex", payload(to=ESC + "[2J" + TO[4:])),
        ("a to in capitals", payload(to=TO.upper())),
        ("a nonce holding an escape", payload(nonce=NONCE + ESC + "[2J")),
        ("a nonce padded with controls", payload(nonce=NONCE[:20] + "\x01\x02\x03\x04" + NONCE[20:])),
        ("a nonce of 601 characters", payload(nonce="a" * 300 + "." + "b" * 300)))]
    screens = [{"kind": purpose, "payload": payload(purpose), "local": local, "ade": ade, "now": NOW + 29,
                "deadline": NOW + 300, "lines": lines, "rows_80": ade_link.sign_rows(lines, 80)}
               for purpose, local, ade in scenes() for lines in (screen(purpose, local, ade),)]
    return {
        "about": "The second screen's shared vectors, made by "
                 "tests/test_ade_link_sign_core.py (vectors(), and write_vectors() to write them anew) and "
                 "read by the page's tests. kinds: for each kind the Mac signs, the payload, its canonical JSON (RFC "
                 "8785), the challenge a passkey signs (base64url of sha256 of the ASCII proofpane-step/1, a zero "
                 "byte and the canonical bytes, as tests/vectors/step_v1.json has it), the request code (the first 5 "
                 "bytes of that challenge in RFC 4648 base32, as two groups of four) and the exact client data the "
                 "Mac writes for the origin here. design_example: a worked example of a step; its challenge and code "
                 "of an older form (sha256 of the canonical bytes "
                 "alone) are kept here as old_challenge and old_code, and a step is signed over the challenge and "
                 "shows the code given here. words: each case, refused with the Mac's words for why, or the lines "
                 "it is shown as. fields: each string the ADE gives, carrying an escape sequence, a C1 control, a "
                 "bidi override and a line feed (hostile), as the screen shows it, and a name holding a double quote "
                 "and a run of spaces that would pass for the screen's own column of kinds (forged). refused: "
                 "payloads refused before anything is shown. screens: the lines shown for each kind, local time "
                 "taken as UTC, and the rows a terminal 80 columns wide shows them as (rows_80: each row after a "
                 "line's first begins with \"  > \"); no screen holds the nonce. Strings that are not ASCII are "
                 "written as JSON escapes.",
        "tag": "proofpane-step/1", "marker": ade_link.SIGN_MARKER, "origin": ADE,
        "kinds": [kind(payload(purpose)) for purpose in ade_link.SIGN_PURPOSES],
        "design_example": {"canonical": example.decode("utf-8"), "sha256": hashlib.sha256(example).hexdigest(),
                           "old_challenge": b64u(hashlib.sha256(example).digest()),
                           "old_code": base64.b32encode(hashlib.sha256(example).digest()[:5]).decode("ascii"),
                           "challenge": b64u(ade_link.step_challenge(example)), "code": ade_link.sign_code(example)},
        "words": words, "hostile": HOSTILE, "forged": FORGED,
        "fields": [{"what": what, "shown": shown} for what, shown in field_cases()],
        "refused": refused, "screens": screens}


def vectors_file():
    with open(VECTORS, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_vectors_file_holds_what_the_core_makes_now():
    assert vectors_file() == json.loads(json.dumps(vectors()))


def test_each_kind_s_challenge_code_and_client_data_are_made_as_the_vectors_say():
    data = vectors_file()
    assert {v["payload"]["purpose"] for v in data["kinds"]} == set(ade_link.SIGN_PURPOSES)
    for v in data["kinds"]:
        raw = v["canonical"].encode("utf-8")
        assert canonicalize(v["payload"]) == ade_link.canonical(v["payload"]) == raw
        challenge = hashlib.sha256(b"proofpane-step/1\x00" + raw).digest()          # from the words, by hand
        assert v["challenge"] == b64u(challenge) == b64u(passkeys.step_challenge(raw))
        code = base64.b32encode(challenge[:5]).decode("ascii")
        assert v["code"] == code[:4] + "-" + code[4:]
        assert v["client_data"] == ('{"type":"webauthn.get","challenge":"' + v["challenge"] + '","origin":"'
                                    + data["origin"] + '","crossOrigin":false,"proofpane":"second-screen/1"}')
    example = data["design_example"]
    assert example["sha256"] == "bb6264a693096254266fdeb3ab76d098693adec60a9d3f8b421e44be6d7e6e6d"   # the example's
    assert (example["old_challenge"], example["old_code"]) == ("u2JkppMJYlQmb96zq3bQmGk63sYKnT-LQh5Evm1-bm0",
                                                               "XNRGJJUT")
    assert example["code"] != "XNRG-JJUT" and len(example["canonical"].encode("utf-8")) == 540


def test_the_server_takes_the_client_data_the_mac_writes(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", ADE)
    for v in vectors_file()["kinds"]:
        raw = v["canonical"].encode("utf-8")
        data = passkeys.check_client_data(v["client_data"].encode("ascii"), "webauthn.get",
                                          passkeys.step_challenge(raw))
        assert list(data) == ["type", "challenge", "origin", "crossOrigin", "proofpane"]
        assert data["proofpane"] == "second-screen/1" and data["crossOrigin"] is False


def test_the_words_vectors_are_what_the_mac_and_the_server_do():
    for case in vectors_file()["words"]:
        assert ade_link.sign_words_refusal(case["words"]) == case["refused"], case["what"]
        assert (case["refused"] is None) == server_takes(case["words"]), case["what"]
        if case["refused"] is None:
            assert ade_link.sign_text_lines(case["words"], "The words (signed):") == case["shown"]


def test_the_refused_payloads_are_refused_before_anything_is_shown():
    for case in vectors_file()["refused"]:
        assert refusal(case["payload"]) == case["refusal"]


def test_the_golden_screens():
    for case in vectors_file()["screens"]:
        got, raw = read(case["payload"], case["now"])
        assert ade_link.sign_screen(SETTING, got, raw, case["local"], case["ade"], case["now"], case["deadline"],
                                    local_time=time.gmtime) == case["lines"], case["kind"]
        assert ade_link.sign_rows(case["lines"], 80) == case["rows_80"], case["kind"]
        assert not any(NONCE in line for line in case["lines"])
        # at 80 columns each row is at most 79 wide, and each that goes on from the one above says so
        assert all(len(row) <= 79 for row in case["rows_80"]) and len(case["rows_80"]) > len(case["lines"])
        assert sum(row.startswith("  > ") for row in case["rows_80"]) == len(case["rows_80"]) - len(case["lines"])


def test_a_name_cannot_end_its_quotes_or_line_up_a_kind_of_its_own():
    shown = dict(field_cases())["a link name that would forge the column of kinds"]
    assert shown == ('               not a link of this Mac; the ADE calls it "pp-r57\\"[3 spaces](this Mac: its own '
                     'folder says so)"   (the ADE\'s word)')
    assert shown.count('"') - shown.count('\\"') == 2 and "   (this Mac" not in shown


def write_vectors():
    """Write the vectors anew from vectors(), every string that is not ASCII as a JSON escape."""
    with open(VECTORS, "w", encoding="utf-8") as fh:
        json.dump(vectors(), fh, indent=2, ensure_ascii=True)
        fh.write("\n")


# ---- the terminal -------------------------------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_client_parses_on_python_3_9_and_imports_python_fido2_only_to_sign():
    # the system Python of macOS is 3.9, where every other part of the client still runs
    import ast
    import subprocess
    with open(ade_link.__file__, encoding="utf-8") as fh:
        ast.parse(fh.read(), feature_version=(3, 9))
    got = subprocess.run([sys.executable, "-c", f"import sys; sys.path[:0] = [{ROOT!r}]; import ade_link; "
                                                "print(sorted(m for m in sys.modules if m.startswith('fido2')))"],
                         capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "[]", got.stderr


def test_a_line_holding_what_a_terminal_acts_on_is_never_printed():
    read_end, write_end = os.pipe()
    term = ade_link.SignTerminal(write_end)
    for bad in ("a" + ESC + "[2J", "a" + CSI + "b", "a" + RLO + "b", "a\rb", "a\tb", "a" + chr(0x2028),
                "a" + chr(0xD800), "a" + chr(0x07)):
        with pytest.raises(ade_link.SignRefused) as e:
            term.say(bad)
        assert str(e.value) == "This program built a line it will not print; it stopped. Nothing was signed."
    line = "plain [U+001B] words, " + chr(0xE9) + " and " + chr(0x05E9)
    term.say(line)
    os.close(write_end)
    assert os.read(read_end, 1000) == (line + "\n").encode("utf-8")       # nothing before it was written
    os.close(read_end)


class Pty:
    """A terminal of the test's own: the program's side (fd) and the owner's (master), what the program writes read
    back on the owner's side as it comes."""

    def __init__(self, rows=24, columns=80):
        import termios
        self.master, self.fd = os.openpty()
        termios.tcsetwinsize(self.fd, (rows, columns))
        self.seen = b""

    def wait_for(self, text, seconds=5):
        import select
        deadline = time.monotonic() + seconds
        while text.encode("utf-8") not in self.seen:
            assert time.monotonic() < deadline, (text, self.seen[-300:])
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                self.seen += os.read(self.master, 4096)
        return self.seen

    def type(self, text):
        os.write(self.master, text.encode("utf-8"))

    def close(self):
        os.close(self.master)
        os.close(self.fd)


def in_thread(call):
    out = {}

    def run():
        try:
            out["value"] = call()
        except BaseException as e:  # noqa: BLE001  handed back to the test
            out["error"] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def test_an_answer_is_one_typed_after_the_question_and_what_was_typed_before_is_discarded():
    pty = Pty()
    try:
        term = ade_link.SignTerminal(pty.fd)
        pty.type("sign\n")                                  # typed ahead, before anything was shown
        time.sleep(0.1)
        thread, out = in_thread(lambda: term.ask("Type sign: "))
        pty.wait_for("Type sign: ")
        time.sleep(0.2)
        pty.type("later\n")
        thread.join(5)
        assert out == {"value": "later"}
    finally:
        pty.close()


def test_the_pin_is_read_from_the_terminal_with_echo_off():
    import termios
    pty = Pty()
    try:
        term = ade_link.SignTerminal(pty.fd)
        thread, out = in_thread(lambda: term.secret("Your key's PIN: "))
        pty.wait_for("Your key's PIN: ")
        deadline = time.monotonic() + 5
        while termios.tcgetattr(pty.fd)[3] & termios.ECHO:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        pty.type("caf" + chr(0xE9) + "42\n")
        thread.join(5)
        assert out == {"value": "caf" + chr(0xE9) + "42"}
        seen = pty.wait_for("PIN: \r\n")
        assert b"caf" not in seen and b"42" not in seen                    # never echoed
        assert termios.tcgetattr(pty.fd)[3] & termios.ECHO                  # and echo is back after it
    finally:
        pty.close()


def test_a_pin_typed_before_it_was_asked_is_discarded_and_an_answer_is_kept_as_typed():
    import termios
    pty = Pty()
    try:
        term = ade_link.SignTerminal(pty.fd)
        pty.type("1234\n")                                  # typed ahead, while echo was still on
        time.sleep(0.1)
        thread, out = in_thread(lambda: term.secret("Your key's PIN: "))
        pty.wait_for("Your key's PIN: ")
        deadline = time.monotonic() + 5
        while termios.tcgetattr(pty.fd)[3] & termios.ECHO:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        pty.type("5678\n")
        thread.join(5)
        assert out == {"value": "5678"}
        # an answer is the line as typed: spaces around the word sign are kept, so " sign" is not sign
        thread, out = in_thread(lambda: term.ask("Type sign: "))
        pty.wait_for("Type sign: ")
        time.sleep(0.2)
        pty.type(" sign \n")
        thread.join(5)
        assert out == {"value": " sign "}
    finally:
        pty.close()


def test_a_long_screen_is_paged_by_the_rows_it_takes_and_nothing_follows_a_page_unasked():
    pty = Pty(rows=8, columns=40)
    try:
        term = ade_link.SignTerminal(pty.fd)
        lines = [f"line {n}" for n in range(1, 5)] + ["x" * 100] + [f"line {n}" for n in range(6, 9)]
        # 10 rows: the long line is cut at 39 columns, its rest marked; the sign question after the last screen
        # takes three rows at 40 columns, the cursor's included, so a screen holds five
        thread, out = in_thread(lambda: term.page(lines, then=ade_link.SIGN_ASK))
        seen = pty.wait_for("-- 5 of 10 rows: press Return for more --")
        assert b"line 4\r\n" + b"x" * 39 + b"\r\n-- 5" in seen and b"  > " not in seen
        time.sleep(0.2)
        pty.type("\n")
        pty.wait_for("line 8")
        thread.join(5)
        rest = pty.seen.split(b"-- 5 of 10 rows: press Return for more --")[1]
        assert "error" not in out and b"press Return" not in rest
        assert b"  > " + b"x" * 35 + b"\r\n  > " + b"x" * 26 + b"\r\nline 6" in rest
    finally:
        pty.close()


@pytest.mark.parametrize("then", ["", "Type sign and press Return to sign this. Anything else stops, and nothing is "
                                         "sent. "])
@pytest.mark.parametrize("rows, columns", [(24, 80), (24, 85), (24, 44), (24, 41), (24, 40), (24, 35), (24, 20),
                                           (8, 40), (6, 20)])
def test_each_screen_leaves_the_rows_its_question_takes_so_no_row_of_it_scrolls_past(rows, columns, then):
    base = screen() + [f"step {n} " + "x" * (n * 7) for n in range(1, 30)]
    for extra in range(rows + 1):                   # the last screen full too, where the question after it is `then`
        lines = base + [f"more {n}" for n in range(extra)]
        pages = ade_link.sign_pages(lines, rows, columns, then)
        if len(pages) > 1 and len(pages[-1][0]) == len(pages[0][0]):
            break
    else:
        pytest.fail("no last screen came out full")
    assert [row for page, _ in pages for row in page] == ade_link.sign_rows(lines, columns)   # each once, in order
    assert [more is None for _, more in pages] == [False] * (len(pages) - 1) + [True]
    for page, more in pages:
        question = then if more is None else more
        assert all(len(row) < columns for row in page)                  # ASCII here: each row takes one
        # the question from the start of a row, then the word sign typed after it: the cursor's row is the last
        assert len(page) + (len(question) + len("sign")) // columns + 1 <= rows, (len(page), question)
    assert ade_link.SIGN_ASK == "Type sign and press Return to sign this. Anything else stops, and nothing is sent. "


def test_a_line_is_cut_before_the_last_column_by_a_count_no_terminal_draws_wider():
    c, wide, mark = chr, chr(0x3042), chr(0x0301)
    assert ade_link._sign_wrap("abcdef", 4) == ["abcd", "ef"]
    assert ade_link._sign_wrap("abcdefgh", 4, 2) == ["abcd", "ef", "gh"]          # the rows after the first narrower
    assert ade_link._sign_wrap("ab" + wide + "c", 3) == ["ab", wide + "c"]        # a wide character takes two
    assert ade_link._sign_wrap("abc" + mark, 3) == ["ab", "c" + mark]             # a mark is kept with its letter
    assert ade_link._sign_wrap("a" + mark * 5, 3) == ["a" + mark * 2, mark * 3]   # unless they are wider than a row
    assert ade_link._sign_wrap("", 4) == [""]
    # any character but printable ASCII may be drawn two wide (an ambiguous width, an emoji, a Unicode newer or older
    # than the count's, a spacing mark), and a mark one wide where a terminal does not know it: none counts less
    for ch in (c(0xE9), c(0x2764), c(0x4DC0), c(0x3248), c(0xFF41), c(0x1D16D), c(0x1715), c(0x302E), c(0x1161)):
        assert ade_link._sign_columns(ch) == 2, hex(ord(ch))
    assert [ade_link._sign_columns(ch) for ch in (mark, c(0xFE0F), c(0x20DD), "a", " ", "~")] == [1] * 6
    assert ade_link.sign_rows(["abcdefgh", "ab"], 6) == ["abcde", "  > f", "  > g", "  > h", "ab"]


# the process tree the terminal checks look at, under a terminal of the test's own: the script stands in for
# `ade_link.py sign`, its forebear check stubbed (a test run by a coding agent has one), and writes what it found
TERMINAL_SCRIPT = """
import os, sys
sys.path[:0] = [{root!r}]
import ade_link
how = sys.argv[1]
ade_link.sign_agent_forebear = lambda folder=None: "process 7 (claude)" if how == "agent" else None
if how == "stdin":
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
elif how == "stdout":
    os.dup2(os.pipe()[1], 1)
elif how in ("group", "leader"):
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        os._exit(0)
    if how == "group":
        os.setpgid(0, 0)
try:
    ade_link.sign_terminal()
    said = "ok"
except ade_link.SignRefused as e:
    said = str(e)
with open({out!r}, "w") as fh:
    fh.write(said)
"""


def under_a_terminal(tmp_path, how):
    import pty
    import signal
    import subprocess
    out = tmp_path / f"said-{how}"
    script = tmp_path / "terminal_script.py"
    script.write_text(TERMINAL_SCRIPT.format(root=ROOT, out=str(out)))
    if how == "none":                                       # no terminal at all: a new session, pipes for its streams
        subprocess.run([sys.executable, str(script), how], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True, timeout=60)
        return out.read_text()
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execv(sys.executable, [sys.executable, str(script), how])
        finally:
            os._exit(127)
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            try:
                if not os.read(master, 4096):
                    break
            except OSError:
                break
    finally:
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(master)
    return out.read_text()


@pytest.mark.parametrize("how, said", [
    ("ok", "ok"),
    ("stdin", "Run this at a terminal of your own, reading from it and writing to it: its input or output is not "
              "that terminal. Nothing was signed."),
    ("stdout", "Run this at a terminal of your own, reading from it and writing to it: its input or output is not "
               "that terminal. Nothing was signed."),
    ("group", "Run this in the foreground of a terminal of your own: it is not. Nothing was signed."),
    ("leader", "Run this from your shell, not from inside another program: it does not lead its process group. "
               "Nothing was signed."),
    ("none", "Run this at a terminal of your own: it has none. Nothing was signed."),
    ("agent", "Run this at a terminal of your own: it was started by a coding agent (process 7 (claude)). Nothing "
              "was signed."),
])
def test_signing_runs_only_in_the_foreground_of_a_terminal_of_the_owner_s_own(tmp_path, how, said):
    assert under_a_terminal(tmp_path, how) == said


def forebears(monkeypatch, chain, folder):
    """sign_agent_forebear, with this process's forebears as `chain` says, [(pid, program)], nearest first; records
    looked for in `folder`, or where Claude Code may keep them when it is None."""
    parents = {pid: (chain[i + 1][0] if i + 1 < len(chain) else 1) for i, (pid, _) in enumerate(chain)}
    monkeypatch.setattr(ade_link.os, "getppid", lambda: chain[0][0])
    monkeypatch.setattr(ade_link, "_sign_parent", lambda pid: parents.get(pid))
    monkeypatch.setattr(ade_link, "_sign_program", lambda pid: dict(chain).get(pid, ""))
    return ade_link.sign_agent_forebear(None if folder is None else str(folder))


def test_a_coding_agent_among_the_forebears_is_found_by_its_program(monkeypatch, tmp_path):
    assert forebears(monkeypatch, [(900, "zsh"), (800, "login")], tmp_path) is None
    assert forebears(monkeypatch, [(900, "bash"), (800, "/usr/local/bin/claude"), (700, "zsh")], tmp_path) == \
        "process 800 (claude)"
    assert forebears(monkeypatch, [(900, "sh"), (800, "Codex")], tmp_path) == "process 800 (Codex)"
    far = [(1000 + n, "sh") for n in range(32)] + [(50, "claude")]      # 32 forebears are looked through
    assert forebears(monkeypatch, far, tmp_path) is None                # past them
    assert forebears(monkeypatch, far[1:], tmp_path) == "process 50 (claude)"


def test_a_coding_agent_is_found_by_claude_code_s_own_record_of_its_processes(monkeypatch, tmp_path):
    chain = [(900, "zsh"), (800, "node"), (700, "zsh")]
    record = tmp_path / "800.json"
    record.write_text(json.dumps({"pid": 800, "sessionId": "s"}))
    assert forebears(monkeypatch, chain, tmp_path) == "process 800, a Claude Code session"
    # a record whose start is not the process's own was left by another process given the same number
    record.write_text(json.dumps({"pid": 800, "sessionId": "s", "procStart": "12345"}))
    monkeypatch.setattr(ade_link, "_sign_same_start", lambda pid, started: False)
    assert forebears(monkeypatch, chain, tmp_path) is None
    monkeypatch.setattr(ade_link, "_sign_same_start", lambda pid, started: started == "12345")
    assert forebears(monkeypatch, chain, tmp_path) == "process 800, a Claude Code session"
    record.unlink()
    record.mkdir()                                                      # a record that cannot be read counts
    assert forebears(monkeypatch, chain, tmp_path) == "process 800, a Claude Code session"
    record.rmdir()
    record.symlink_to(tmp_path / "gone.json")                           # so does one whose name leads nowhere
    assert forebears(monkeypatch, chain, tmp_path) == "process 800, a Claude Code session"


@pytest.mark.parametrize("where", ["home", "config", "variable home"])
def test_a_claude_code_record_is_found_in_every_folder_claude_code_may_keep_it_in(monkeypatch, tmp_path, where):
    # Claude Code runs as node, so only its record names it: in the password database's home, where
    # CLAUDE_CONFIG_DIR says (a documented setting), or under $HOME, as an older release's guard read them
    folders = {"home": tmp_path / "pw" / ".claude", "config": tmp_path / "config",
               "variable home": tmp_path / "home" / ".claude"}
    monkeypatch.setattr(ade_link, "_take_sessions_dir", lambda: str(folders["home"] / "sessions"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folders["config"]))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    chain = [(900, "zsh"), (800, "node"), (700, "login")]
    assert forebears(monkeypatch, chain, None) is None
    (folders[where] / "sessions").mkdir(parents=True)
    (folders[where] / "sessions" / "800.json").write_text(json.dumps({"pid": 800, "sessionId": "s"}))
    assert forebears(monkeypatch, chain, None) == "process 800, a Claude Code session"


def test_the_walk_asks_the_system_s_own_ps_never_one_planted_on_path(monkeypatch, tmp_path, own_home):
    # on a Mac there is no /proc: each forebear's parent, program and start come from ps, and a coding agent that
    # starts this program gives it its own PATH. Where there is a /proc (Linux), it says what the walk's ps must agree
    # with; on a Mac, the system's own ps does, asked by its absolute path
    planted = tmp_path / "bin" / "ps"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\necho 1\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("TZ", "NZST-12")                                 # a start is read in UTC whatever this says
    import subprocess
    pid = str(os.getpid())
    assert subprocess.run(["ps", "-o", "ppid=", "-p", pid], capture_output=True, text=True).stdout.strip() == "1"
    if not os.path.exists("/bin/ps"):
        pytest.skip("this system has no /bin/ps")
    if os.path.isdir(f"/proc/{pid}"):
        with open(f"/proc/{pid}/comm") as fh:
            comm = fh.read().strip()
        with open(f"/proc/{pid}/stat") as fh:
            ticks = fh.read().rsplit(")", 1)[1].split()[19]             # Linux: the start in clock ticks after boot
        assert ade_link._sign_same_start(os.getpid(), ticks)
        assert not ade_link._sign_same_start(os.getpid(), ticks + "0")
    else:
        comm = subprocess.run(["/bin/ps", "-o", "comm=", "-p", pid], capture_output=True, text=True,
                              env={"PATH": "/bin:/usr/bin", "LC_ALL": "C", "HOME": own_home}).stdout.strip()
        assert comm and comm != "1"
    started =" ".join(subprocess.run(["/bin/ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True,
                                      env={"PATH": "/bin:/usr/bin", "TZ": "UTC0", "LC_ALL": "C",
                                           "HOME": own_home}).stdout.split())
    monkeypatch.setattr(ade_link, "PROC", str(tmp_path / "no-proc"))
    assert ade_link._sign_parent(os.getpid()) == os.getppid()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:                                                                # in a group of its own: its parent is not
        assert ade_link._sign_parent(child.pid) == os.getpid()          # its group, however this test was started
    finally:
        child.kill()
        child.wait()
    assert ade_link._sign_program(os.getpid()) == comm
    assert ade_link._sign_same_start(os.getpid(), started) and started
    assert not ade_link._sign_same_start(os.getpid(), "Mon Jan  1 00:00:00 2001")


def test_ps_is_asked_in_utc_and_the_c_locale_and_a_start_it_cannot_give_counts(monkeypatch, tmp_path):
    # the system's time zone, which this machine's may not show, must not move a start Claude Code wrote in UTC; and
    # where ps says nothing of a forebear, its record is taken as its own, so the check errs toward refusing
    import subprocess
    asked = []

    def run(argv, **kwargs):
        asked.append((argv, kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ade_link, "PROC", str(tmp_path / "no-proc"))
    assert ade_link._sign_same_start(4242, "Mon Jan  1 00:00:00 2001")
    assert asked == [(["/bin/ps", "-o", "lstart=", "-p", "4242"], {"PATH": "/bin:/usr/bin", "LC_ALL": "C",
                                                                     "TZ": "UTC0"})]


def test_a_coding_agent_that_is_a_real_forebear_is_found(tmp_path):
    # a stand-in named claude (this Python, run by that name) starts the signer; no real agent is reached. A Mac's
    # framework Python starts through a launcher that runs the interpreter again inside Python.app, by that name, so
    # there the stand-in is that interpreter itself, which keeps the name it is run by
    import subprocess
    stand_in = tmp_path / "claude"
    app = os.path.join(sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
    stand_in.symlink_to(app if sys.platform == "darwin" and os.path.isfile(app) else sys.executable)
    child = (f"import sys; sys.path[:0] = [{ROOT!r}]; import ade_link; "
             f"print(ade_link.sign_agent_forebear({str(tmp_path / 'none')!r}))")
    parent = (f"import os, subprocess, sys; print(os.getpid(), flush=True); "
              f"sys.exit(subprocess.run([{sys.executable!r}, '-c', {child!r}]).returncode)")
    got = subprocess.run([str(stand_in), "-c", parent], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    # the stand-in itself, by its own process id: not some other agent this test may happen to run under
    pid, found = got.stdout.split("\n", 1)
    assert found.strip() == f"process {pid} (claude)", got.stdout


# ---- the key --------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_keys_on_usb(monkeypatch):
    """The keys a ceremony may ask are the tests' own: python-fido2's own listing and opening of the keys on this
    machine's USB fail the test, so no test ever reaches a real key, while this program's listing (_sign_devices) is
    the one a Mac runs, given the tests' keys (keys_on_usb)."""
    import fido2.hid
    monkeypatch.setattr(fido2.hid, "list_descriptors", lambda: pytest.fail("a test looked for keys on USB"))
    monkeypatch.setattr(fido2.hid, "open_connection", lambda d: pytest.fail("a test opened a key on USB"))


def keys_on_usb(monkeypatch, *keys):
    """python-fido2's listing gives `keys` as a Mac with them plugged in does, each opened over its soft transport;
    the transports opened, in order."""
    import fido2.hid
    by_path, opened = {ctap_soft.descriptor(k).path: k for k in keys}, []

    def open_connection(descriptor):
        opened.append(ctap_soft.SoftHid(by_path[descriptor.path]))
        return opened[-1]
    monkeypatch.setattr(fido2.hid, "list_descriptors", lambda: [ctap_soft.descriptor(k) for k in keys])
    monkeypatch.setattr(fido2.hid, "open_connection", open_connection)
    return opened


def test_the_tests_never_look_for_keys_on_usb():
    s = Signer()
    with pytest.raises(pytest.fail.Exception):
        ade_link.sign_ceremony(s.setting, s.keys, s.text(), Term(["sign"]), [s.id], now=s.now)


class Term:
    """The owner's terminal as a test plays it: what is printed, in order, each line held to what a terminal may be
    given; the answers typed, in order; and at each question, how many commands the key had been sent by then."""

    def __init__(self, answers, key=None):
        self.answers, self.key, self.out, self.paged = list(answers), key, [], []

    def say(self, text):
        assert ade_link._sign_printable(text + "\n"), text
        self.out.append(text)

    def page(self, lines, then=""):
        """Each line said, as SignTerminal.page shows them a screen at a time; the call kept (paged), with the
        question asked after the last screen."""
        self.paged.append((list(lines), then))
        for line in lines:
            self.say(line)

    def ask(self, prompt):
        self.out.append(("ask", prompt, len(self.key.requests) if self.key else None))
        return self.answers.pop(0)

    def secret(self, prompt):
        self.out.append(("secret", prompt, len(self.key.requests) if self.key else None))
        return self.answers.pop(0)

    def said(self):
        return [x for x in self.out if isinstance(x, str)]


class Signer:
    """A key, this account's passkey on it, and the owner's keys as this Mac holds them; a step made now."""

    def __init__(self, key=None, alg=-7, setting=SETTING):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.key, self.setting = key or ctap_soft.SoftKey(), setting
        private = ec.generate_private_key(ec.SECP256R1()) if alg == -7 else Ed25519PrivateKey.generate()
        self.credential = self.key.import_credential(setting.rp_id(), os.urandom(32), private, alg)
        spki = private.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)
        self.id = b64u(self.credential.id)
        self.keys = {"account": ACCOUNT, "keys": [{"credential_id": self.id, "alg": alg, "public_key": b64u(spki),
                                                   "fingerprint": "3a7f0c1e" + "0" * 24, "name": "YubiKey 5C"}]}
        self.now = int(time.time())

    def text(self, purpose="instruction", **over):
        self.now = int(time.time())
        life = 600 if purpose in ("resume", "e2e-key", "e2e-off") else 3600
        return text_of(payload(purpose, **dict({"created_at": self.now, "expires_at": self.now + life}, **over)))

    def sign(self, term, text=None, allow=None, deadline=None, ade=None, local=None, devices=None):
        """The ceremony over `text`, by this key (or `devices`, or USB: the keys python-fido2 lists)."""
        text = self.text() if text is None else text
        devices = None if devices is USB else [ctap_soft.device(self.key)] if devices is None else devices
        return ade_link.sign_ceremony(self.setting, self.keys, text, term, [self.id] if allow is None else allow,
                                      deadline, local, ade, devices, self.now)


USB = object()                      # Signer.sign's devices: the keys on USB, as this program lists them


def refused_by(call):
    with pytest.raises(ade_link.SignRefused) as e:
        call()
    return str(e.value)


@pytest.mark.parametrize("alg", [-7, -8])
def test_a_step_is_signed_over_the_bytes_shown_after_sign_then_the_pin_then_the_touch(alg):
    s = Signer(alg=alg)
    term = Term(["sign", "1234"], s.key)
    text = s.text()
    _, raw, credential, key = s.sign(term, text)
    assert raw == text.encode("utf-8") and key["credential_id"] == s.id
    kinds = [x[0] if isinstance(x, tuple) else "line" for x in term.out]
    asked = kinds.index("ask")
    assert set(kinds[:asked]) == {"line"} and term.out[asked] == ("ask", ade_link.SIGN_ASK, 0)  # the key not yet asked
    # the screen went through page, a screen at a time, leaving room for the question asked after it
    assert term.paged == [(term.out[:asked], ade_link.SIGN_ASK)]
    # this Mac's own records reach the screen through the ceremony
    other = Signer(alg=alg)
    mine = Term(["sign", "1234"], other.key)
    other.sign(mine, local={"links": {TO: "pp-r57"}})
    assert '               this Mac\'s link "pp-r57"   (this Mac: its own folder says so)' in mine.said()
    assert kinds[asked + 1:] == ["line", "secret", "line"]
    assert term.out[asked + 1].startswith("Your key takes 8 more wrong PINs before its PIN is blocked")
    assert term.out[-1] == f"Touch your key now. It signs request {ade_link.sign_code(raw)}, shown above, and " \
                           "nothing else."
    client = ade_link.unb64u(credential["response"]["clientDataJSON"])
    assert client == ade_link.sign_client_data(s.setting, raw)
    probe, real = s.key.asked("getAssertion")
    assert real[1] == HOST and real[2] == hashlib.sha256(client).digest() and probe[1] == HOST
    assert all(4 not in p for p in (probe, real))                       # no extension asked of either
    assert s.key.signatures == 1
    # the passkeys that may sign are named as the owner's keys name them
    assert '               which the ADE names "YubiKey 5C" (3a7f0c1e...)   (the ADE\'s word)' in term.said()


def test_a_signing_touch_never_asks_for_hmac_secret_though_the_passkey_holds_it():
    s = Signer()
    s.credential.secrets = (os.urandom(32), os.urandom(32))             # a passkey made with hmac-secret
    _, raw, credential, _ = s.sign(Term(["sign", "1234"], s.key))
    data = ade_link.unb64u(credential["response"]["authenticatorData"])
    assert all(4 not in p for p in s.key.asked("getAssertion"))
    assert not data[32] & 0x80 and len(data) == 37                      # no extension output: rpIdHash, flags, count
    # the key gives it to a client that asks, so its absence above is this program's doing, not the key's
    out, auth = hmac_get(s.key, s.credential.id, (b"1" * 32,))
    assert auth.flags & 0x80 and out.output1 == hmac_of(s.credential.secrets[0], b"1" * 32)


def test_an_answer_to_a_question_served_with_another_digest_is_refused_before_it_is_shown():
    s = Signer()
    term = Term(["sign", "1234"], s.key)
    assert refused_by(lambda: s.sign(term, s.text("answer"), ade={"question": {"text": QUESTION + " Now?"}})) == \
        "The question the ADE served is not the one whose digest the answer signs. Nothing was signed."
    assert term.out == [] and s.key.requests == []


@pytest.mark.parametrize("answer", ["Sign", "sign ", " sign", "yes", "", "sign" + chr(0x00)])
def test_anything_but_sign_stops_before_the_key_is_asked(answer):
    s = Signer()
    assert refused_by(lambda: s.sign(Term([answer], s.key))) == "Not signed. Nothing was sent."
    assert s.key.requests == []


def test_what_cannot_be_signed_is_refused_before_anything_is_shown():
    s = Signer()
    term = Term(["sign", "1234"], s.key)
    assert refused_by(lambda: s.sign(term, s.text(to=ESC + "[2J" + TO[4:]))).startswith("Its to is not")
    assert term.out == [] and s.key.requests == []


def test_the_challenge_rpid_and_origin_are_this_program_s_own_never_the_ade_s():
    s = Signer()
    ade = {"challenge": b64u(bytes(32)), "rp_id": "evil.example", "origin": "https://evil.example",
           "client_data": "{}"}
    _, raw, credential, _ = s.sign(Term(["sign", "1234"], s.key), ade=ade)
    data = json.loads(ade_link.unb64u(credential["response"]["clientDataJSON"]))
    assert data["challenge"] == b64u(passkeys.step_challenge(raw)) and data["origin"] == ADE
    assert all(p[1] == HOST for p in s.key.asked("getAssertion"))


def test_the_tries_left_are_shown_and_a_wrong_pin_signs_nothing():
    s = Signer(ctap_soft.SoftKey(pin="2468"))
    s.key.pin_retries = 3
    term = Term(["sign", "1111"], s.key)
    assert refused_by(lambda: s.sign(term)) == "That PIN is wrong. Nothing was signed."
    said = term.said()
    assert "Your key takes 3 more wrong PINs before its PIN is blocked; three wrong in a row lock it until it is " \
           "plugged in again." in said
    assert "A key whose PIN is blocked is reset with its maker's tool, which removes every passkey on it." in said
    assert s.key.signatures == 0 and s.key.pin_retries == 2
    s.key.pin_retries = 4
    term = Term(["sign", "2468"], s.key)
    s.sign(term)
    assert not any("reset with its maker's tool" in line for line in term.said()) and s.key.signatures == 1


def test_no_pin_is_asked_where_none_can_work():
    # a PIN blocked, or three wrong in a row until the key is plugged in again: said before any PIN is asked
    for setup, words in ((lambda k: setattr(k, "pin_retries", 0), ade_link.SIGN_CTAP_WORDS["PIN_BLOCKED"]),
                         (lambda k: setattr(k, "auth_blocked", True), ade_link.SIGN_CTAP_WORDS["PIN_AUTH_BLOCKED"])):
        s = Signer()
        setup(s.key)
        term = Term(["sign", "1234"], s.key)
        assert refused_by(lambda: s.sign(term)) == words + " Nothing was signed."
        assert not any(x[0] == "secret" for x in term.out if isinstance(x, tuple)) and term.answers == ["1234"]
        assert [p.get(2) for p in s.key.asked("clientPin")] == [0x01] and s.key.touches == 0


@pytest.mark.parametrize("pin, least, sent", [("123", None, False), ("1234", None, True), ("12345", 6, False),
                                              ("123456", 6, True), ("9" * 63, None, True), ("9" * 64, None, False),
                                              ("9" * 60 + chr(0xE9) * 2, None, False)])
def test_a_pin_no_key_takes_is_never_given_to_it_so_no_try_is_spent(pin, least, sent):
    s = Signer(ctap_soft.SoftKey(pin=pin))
    s.key.min_pin = least
    if sent:
        s.sign(Term(["sign", pin], s.key))
        assert s.key.signatures == 1
        return
    assert refused_by(lambda: s.sign(Term(["sign", pin], s.key))) == \
        f"Your key's PIN is {least or 4} characters to 63 bytes long: that one was not given to the key, so no try " \
        "was spent. Nothing was signed."
    assert s.key.pin_retries == 8 and not [p for p in s.key.asked("clientPin") if p.get(2) in (0x05, 0x09)]


def test_the_pin_is_put_in_normal_form_c_before_the_key_is_given_it():
    s = Signer(ctap_soft.SoftKey(pin="caf" + chr(0xE9)))
    s.sign(Term(["sign", "cafe" + chr(0x0301)], s.key))                  # typed decomposed, as some keyboards give it
    assert s.key.signatures == 1


def test_an_empty_pin_signs_nothing():
    s = Signer()
    assert refused_by(lambda: s.sign(Term(["sign", ""], s.key))) == "No PIN was given. Nothing was signed."
    assert s.key.signatures == 0


def test_a_key_with_a_finger_is_asked_for_it_and_never_for_a_pin():
    s = Signer(ctap_soft.SoftKey(pin=None, uv=True))
    term = Term(["sign"], s.key)
    s.sign(term)
    assert any(line.startswith("Put an enrolled finger on your key. It signs request ") for line in term.said())
    assert not any(isinstance(x, tuple) and x[0] == "secret" for x in term.out) and s.key.signatures == 1


def test_a_key_that_cannot_check_it_is_you_is_refused_before_any_touch():
    s = Signer(ctap_soft.SoftKey(pin=None))
    assert refused_by(lambda: s.sign(Term(["sign"], s.key))) == (
        "This key cannot check that it is you: it has no PIN and no fingerprint. Set a PIN with its maker's tool, then "
        "run this again. Nothing was signed.")
    assert s.key.touches == 0 and s.key.asked("getAssertion") == []


def test_no_touch_no_signature():
    s = Signer(ctap_soft.SoftKey(touch="refused"))
    assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key))) == \
        "The key was not touched to sign. Nothing was signed."
    s = Signer(ctap_soft.SoftKey(touch="never"))
    text = s.text()
    started = time.monotonic()
    # the request's own end bounds the key's time: three seconds left from the second the step was made, so a test
    # that stalls a second before the key is asked still asks it
    assert refused_by(lambda: within(30, lambda: s.sign(Term(["sign", "1234"], s.key), text,
                                                         deadline=s.now + 3))) == \
        "Your key was not touched in time. Nothing was signed."
    assert time.monotonic() - started < 10 and s.key.signatures == 0 and s.key.cancelled == 1


def test_a_touch_a_second_after_the_pin_still_signs():
    # python-fido2 takes the key's time in milliseconds: given in seconds, a real touch would never come in time
    s = Signer(ctap_soft.SoftKey(touch="never"))

    class Late(Term):
        def secret(self, prompt):
            threading.Timer(1.0, setattr, (s.key, "touch", "given")).start()
            return super().secret(prompt)
    started = time.monotonic()
    within(30, lambda: s.sign(Late(["sign", "1234"], s.key)))
    assert s.key.signatures == 1 and time.monotonic() - started >= 1


def test_the_key_is_given_two_minutes_at_most_whatever_time_the_request_has(monkeypatch):
    monkeypatch.setattr(ade_link, "SIGN_TOUCH_MOST", 1)                 # two minutes, made one second here
    s = Signer(ctap_soft.SoftKey(touch="never"))
    started = time.monotonic()
    assert refused_by(lambda: within(30, lambda: s.sign(Term(["sign", "1234"], s.key)))) == \
        "Your key was not touched in time. Nothing was signed."
    assert time.monotonic() - started < 10


def test_a_device_python_fido2_cannot_ask_once_is_refused():
    s = Signer()

    class NoOnce:                                               # no single try to make: only a call that retries
        capabilities = 0x05

        def call(self, *a, **kw):
            raise AssertionError("asked")
    assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key), devices=[NoOnce()])) == \
        "This key is not one python-fido2 2.2.1 reaches over USB. Nothing was signed."


def test_a_busy_key_stops_the_ceremony_at_its_first_answer():
    s = Signer()
    s.key.busy = 1
    assert refused_by(lambda: within(10, lambda: s.sign(Term(["sign", "1234"], s.key)))) == ade_link.SIGN_BUSY
    assert s.key.busy_answered == 1 and s.key.requests == []
    s = Signer()
    s.key.busy_at = "getAssertion"                              # another program took the key after the PIN
    assert refused_by(lambda: within(10, lambda: s.sign(Term(["sign", "1234"], s.key)))) == ade_link.SIGN_BUSY
    assert s.key.busy_answered == 1 and s.key.signatures == 0


def test_a_key_on_usb_is_listed_greeted_once_and_signs(monkeypatch):
    s = Signer()
    opened = keys_on_usb(monkeypatch, s.key)
    s.sign(Term(["sign", "1234"], s.key), devices=USB)
    assert s.key.signatures == 1 and len(opened) == 1
    assert [n for n, _ in s.key.requests][:1] == ["getInfo"]           # INIT once, then CTAP2 on its channel


@pytest.mark.parametrize("other", [("INIT", "keep-alive"), ("INIT", "greeting"), ("getInfo", "keep-alive"),
                                   ("getAssertion", "busy"), ("touch", "busy")])
def test_another_program_s_traffic_on_the_key_says_it_is_in_use_and_cancels_this_request(monkeypatch, other):
    # a key sends every program's frames to every program that has it open: another program's request waiting for
    # a touch, its INIT answered, or the key telling it that it is busy with this one; the owner is told not to touch
    s = Signer()
    keys_on_usb(monkeypatch, s.key)
    s.key.other = other
    term = Term(["sign", "1234"], s.key)
    assert refused_by(lambda: within(10, lambda: s.sign(term, devices=USB))) == ade_link.SIGN_BUSY
    assert s.key.other is None and s.key.signatures == 0
    # this program's own request, where one waited for the touch, was cancelled: the key does not wait on for it
    assert s.key.cancels == (1 if other[0] == "touch" else 0)


def test_a_key_busy_when_it_is_first_greeted_says_it_is_in_use_at_once(monkeypatch):
    s = Signer()
    keys_on_usb(monkeypatch, s.key)
    s.key.busy_at = "INIT"                      # python-fido2's own INIT would ask again every 0.1 seconds, silently
    assert refused_by(lambda: within(10, lambda: s.sign(Term(["sign", "1234"], s.key), devices=USB))) == \
        ade_link.SIGN_BUSY
    assert s.key.busy_answered == 1 and s.key.requests == []


def test_a_cancel_ends_only_the_request_waiting_on_its_own_channel():
    import struct
    from fido2 import cbor
    from fido2.hid import CTAPHID
    s = Signer(ctap_soft.SoftKey(touch="never"))
    usb = ctap_soft.SoftHid(s.key)
    ours = usb.channel                                                  # the channel its INIT gives this program

    def send(channel, command, data=b""):
        usb.write_packet((struct.pack(">IBH", channel, 0x80 | command, len(data)) + data[:57]).ljust(64, b"\0"))
        for seq, at in enumerate(range(57, len(data), 59)):
            usb.write_packet((struct.pack(">IB", channel, seq) + data[at:at + 59]).ljust(64, b"\0"))
    send(ours, CTAPHID.CBOR, b"\x02" + cbor.encode({1: HOST, 2: b"h" * 32,
                                                   3: [{"type": "public-key", "id": s.credential.id}]}))
    assert usb.read_packet()[4] == 0x80 | CTAPHID.KEEPALIVE            # it waits for the touch
    send(ctap_soft.OTHER, CTAPHID.CANCEL)                               # another program's CANCEL, on its channel
    assert s.key.cancels == 0 and usb.read_packet()[4] == 0x80 | CTAPHID.KEEPALIVE
    send(ours, CTAPHID.CANCEL)
    answer = usb.read_packet()
    assert s.key.cancels == 1 and answer[:5] == struct.pack(">IB", ours, 0x80 | CTAPHID.CBOR)
    assert answer[7] == ERR.KEEPALIVE_CANCEL and s.key.signatures == 0


def test_a_key_that_says_it_is_busy_in_its_ctap2_answer_says_it_is_in_use():
    # said in the CTAP2 answer, python-fido2 does not ask again on its own, as it does for the CTAPHID error
    key = ctap_soft.SoftKey()
    key.busy, key.busy_as = 1, "status"
    with pytest.raises(CtapError) as e:
        Ctap2(ctap_soft.device(key))
    assert e.value.code == ERR.CHANNEL_BUSY and key.busy_answered == 1
    for at in (None, "getAssertion"):
        s = Signer()
        s.key.busy, s.key.busy_at, s.key.busy_as = (0, at, "status") if at else (1, None, "status")
        assert refused_by(lambda: within(10, lambda: s.sign(Term(["sign", "1234"], s.key)))) == ade_link.SIGN_BUSY
        assert s.key.busy_answered == 1 and s.key.signatures == 0


def test_a_key_that_cannot_be_opened_or_listed_is_said_so_and_what_was_opened_is_closed(monkeypatch):
    import fido2.hid
    one, two = Signer(), ctap_soft.SoftKey(serial="0002")
    opened = keys_on_usb(monkeypatch, one.key, two)
    first = fido2.hid.open_connection

    def held(descriptor):
        if descriptor.path == ctap_soft.descriptor(two).path:
            raise OSError("held by another program")
        return first(descriptor)
    monkeypatch.setattr(fido2.hid, "open_connection", held)
    assert refused_by(lambda: one.sign(Term(["sign", "1234"], one.key), devices=USB)) == \
        "A security key on USB could not be opened: another program may be holding it. Do not touch it. Nothing " \
        "was signed."
    assert len(opened) == 1 and opened[0].closed and one.key.signatures == 0

    def broken():
        raise RuntimeError("no USB here")
    monkeypatch.setattr(fido2.hid, "list_descriptors", broken)
    assert refused_by(lambda: one.sign(Term(["sign", "1234"], one.key), devices=USB)) == \
        "python-fido2 could not list the keys on USB (RuntimeError). Nothing was signed."


def test_a_mac_without_python_fido2_is_told_how_to_install_it_before_anything_is_shown_or_fetched(monkeypatch):
    monkeypatch.setattr(ade_link, "_FIDO", None)
    for name in [n for n in sys.modules if n == "fido2" or n.startswith("fido2.")]:
        monkeypatch.setitem(sys.modules, name, None)                    # an import of it fails
    s, term = Signer(), Term(["sign", "1234"])
    assert refused_by(lambda: s.sign(term)) == ade_link.SIGN_INSTALL and term.out == []
    text = s.text()
    relay = Relay(s, [parked(s, text, 1)])
    assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(text))) == \
        ade_link.SIGN_INSTALL and relay.fetches == 0 and term.out == []


def test_a_key_whose_answer_is_not_strict_cbor_is_said_so():
    s = Signer()
    s.key.garble = "getInfo"
    assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key))) == \
        "Your key's answer could not be read. Nothing was signed."


def test_of_two_accounts_on_one_key_only_this_account_s_passkey_is_asked():
    s = Signer()
    other = Signer(s.key)                                       # another account's passkey for this ADE, same key
    _, _, credential, _ = s.sign(Term(["sign", "1234"], s.key), allow=[other.id, s.id])
    assert credential["id"] == s.id
    assert all([d["id"] for d in p[3]] == [s.credential.id] for p in s.key.asked("getAssertion"))
    before = len(s.key.requests)
    assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key), allow=[other.id])) == \
        "None of the passkeys the ADE says may sign this is one of this account's keys this Mac holds. Nothing was " \
        "signed."
    assert len(s.key.requests) == before


def test_an_answer_that_does_not_verify_here_is_never_handed_over():
    from cryptography.hazmat.primitives.asymmetric import ec
    s = Signer()
    s.credential.private_key = ec.generate_private_key(ec.SECP256R1())     # the key signs with another key pair
    assert refused_by(lambda: s.sign(Term(["sign", "1234"], s.key))) == \
        "The key's answer did not verify here (the signature does not verify). Nothing is sent."


def test_the_time_left_bounds_the_screen_and_the_key():
    s = Signer()
    text = s.text()
    term = Term(["sign"], s.key)
    assert refused_by(lambda: s.sign(term, text, deadline=s.now)) == \
        "The request's time ran out before it was shown. Nothing was signed."
    assert term.out == []

    class Slow(Term):                                           # the owner reads until the request's time is up
        def ask(self, prompt):
            while int(time.time()) <= s.now:
                time.sleep(0.05)
            return super().ask(prompt)
    assert refused_by(lambda: s.sign(Slow(["sign"], s.key), text, deadline=s.now + 1)) == \
        "The request's time ran out before the key was asked. Nothing was signed."
    assert s.key.requests == []


def test_a_key_with_no_ctap2_or_one_taken_out_ends_the_ceremony_cleanly():
    s = Signer()
    s.key.cbor = False
    assert refused_by(lambda: s.sign(Term(["sign"], s.key))) == "This key does not speak FIDO2. Nothing was signed."
    s = Signer()
    s.key.versions = ("U2F_V2",)                                # CTAP2 framing, but no FIDO2 version named
    assert refused_by(lambda: s.sign(Term(["sign"], s.key))) == "This key does not speak FIDO2. Nothing was signed."
    s = Signer()
    # a pin that names a passkey by no id at all: the key is never asked for it
    blank = {"account": ACCOUNT, "keys": [dict(s.keys["keys"][0], credential_id="")]}
    assert refused_by(lambda: ade_link.sign_ceremony(s.setting, blank, s.text(), Term(["sign"], s.key), [""],
                                                     devices=[ctap_soft.device(s.key)], now=s.now)) == \
        "The ADE named no passkey that may sign this. Nothing was signed."
    assert s.key.requests == []
    s = Signer()

    class TakenOut(Term):                                       # the key is taken out while the PIN is typed
        def secret(self, prompt):
            s.key.unplugged = True
            return super().secret(prompt)
    assert refused_by(lambda: s.sign(TakenOut(["sign", "1234"], s.key))) == \
        "The key was taken out, or stopped answering. Nothing was signed."
    assert s.key.signatures == 0


def test_several_keys_are_chosen_by_number_their_names_escaped():
    s = Signer(ctap_soft.SoftKey(name="Yubi" + ESC + "[2JKey", serial="77"))
    spare = ctap_soft.SoftKey(name="Spare", serial="12")
    term = Term(["sign", "2", "1234"], s.key)
    s.sign(term, devices=[ctap_soft.device(spare), ctap_soft.device(s.key)])
    assert '  1. "Spare", serial "12"' in term.said() and '  2. "Yubi\\x1b[2JKey", serial "77"' in term.said()
    assert spare.requests == [] and s.key.signatures == 1
    nameless = ctap_soft.device(ctap_soft.SoftKey(name="", serial=""))   # a key that says neither, in this program's
    term = Term(["sign", "2", "1234"], s.key)                          # words, never in quotes
    s = Signer()
    s.sign(term, devices=[nameless, ctap_soft.device(s.key)])
    assert "  1. a security key, serial unknown" in term.said()
    for choice in ("3", "0", "", "one"):
        s = Signer()
        assert refused_by(lambda: s.sign(Term(["sign", choice], s.key), devices=[ctap_soft.device(spare),
                                                                              ctap_soft.device(s.key)])) == \
            "No key was chosen. Nothing was signed."
    assert refused_by(lambda: s.sign(Term(["sign"], s.key), devices=[])) == \
        "No security key was found on USB. Plug yours in, then run this again. Nothing was signed."


def test_the_key_s_errors_are_said_in_this_program_s_words():
    words = ade_link._sign_ctap_words
    assert words(CtapError(ERR.PIN_BLOCKED)) == "Your key's PIN is blocked: it is reset with its maker's tool, which " \
                                                "removes every passkey on it."
    assert words(CtapError(ERR.PIN_AUTH_BLOCKED)).startswith("Three wrong PINs in a row")
    assert words(CtapError(ERR.UV_INVALID)) == "Your key did not recognise that finger."
    assert words(CtapError(ERR.USER_ACTION_TIMEOUT)) == "Your key was not touched in time."
    assert words(CtapError(ERR.INVALID_CBOR)) == "The key or python-fido2 stopped (INVALID_CBOR)."
    assert words(ValueError("x" + ESC)) == "The key or python-fido2 stopped (ValueError)."


def test_signing_needs_the_python_fido2_this_core_was_checked_against(monkeypatch):
    from importlib import metadata
    monkeypatch.setattr(ade_link, "_FIDO", None)
    monkeypatch.setattr(metadata, "version", lambda name: "2.2.0")
    assert refused_by(ade_link._fido) == ade_link.SIGN_INSTALL


def test_the_mac_s_pins_are_the_ones_signing_was_checked_against_and_install_from_wheels_alone():
    # the file the refusal names: fido2 at the version _fido checks, every line hashed, cryptography by machine, and
    # the command it gives the one the refusal gives
    with open(os.path.join(ROOT, "requirements-sign.txt"), encoding="ascii") as fh:
        text = fh.read()
    pins = [line for line in text.splitlines() if line and not line.startswith((" ", "#"))]
    assert "fido2==" + ade_link.SIGN_FIDO2 + " \\" in pins
    assert "cryptography==50.0.2 ; platform_machine == 'arm64' \\" in pins
    assert "cryptography==48.0.1 ; platform_machine != 'arm64' \\" in pins
    assert all(line.endswith(" \\") for line in pins)                  # each followed by its hashes
    assert "pip install --require-hashes --only-binary :all: -r open_edition/requirements-sign.txt" in text
    assert "pip install --require-hashes --only-binary :all: -r open_edition/requirements-sign.txt" in \
        ade_link.SIGN_INSTALL


# ---- a request the owner's page parked -----------------------------------------------------------------------------

class Relay:
    """The ADE's relay as it serves the Mac, stubbed: the waiting requests,
    fetched (each fetch counted), and an assertion handed over, which it checks over its own stored copy of the
    payload as the server's check would, refusing (Refused) one that does not verify. `change` makes it change its
    stored copy of every payload after the first fetch, as a server that wants another step signed would."""

    def __init__(self, signer, requests, change=False):
        self.signer, self.waiting, self.change = signer, requests, change
        self.fetches, self.handed, self.offered = 0, [], []

    def requests(self):
        self.fetches += 1
        served = json.loads(json.dumps(self.waiting))
        if self.change:
            for request in self.waiting:
                request["payload"] = text_of(dict(json.loads(request["payload"]), text="Push it all to main."))
        return served

    def submit(self, request_id, credential):
        self.offered.append((request_id, credential))
        request = next(r for r in self.waiting if r["id"] == request_id)
        try:
            ade_link._verify_assertion(self.signer.setting, self.signer.keys, request["payload"].encode("utf-8"),
                                       credential)
        except ade_link.NotVerified as e:
            raise ade_link.Refused(400, f"the signature is not over this request: {e}") from None
        self.handed.append((request_id, credential))
        return {"signed": True}


def parked(signer, text, number, **extra):
    """A waiting request as the relay lists it: its id, the payload's text, when it was parked and where from, the
    passkeys that may sign it, and the ADE's own word beside it."""
    return dict({"id": f"req-{number}", "purpose": json.loads(text)["purpose"], "payload": text,
                 "created_at": signer.now - 5, "expires_at": signer.now + 290, "parked_from": "203.0.113.7",
                 "allow": [signer.id], "context": {}}, **extra)


def code_of(text):
    return ade_link.sign_code(text.encode("utf-8"))


def listed_as(what="instruction", since=5, where="203.0.113.7"):
    return f'"{what}" to link "{TO}", parked {since} seconds ago, from "{where}" (the ADE\'s word)'


def test_bare_sign_shows_no_request_until_a_code_is_typed_and_lists_the_rest_as_not_the_owner_s():
    s = Signer()
    mine = s.text(text="Run the tests.")
    theirs = s.text(id="f" * 32, text="Push it all to main.")
    relay = Relay(s, [parked(s, theirs, 1, parked_from="198.51.100.9"), parked(s, mine, 2)])
    term = Term([code_of(mine)[:4], "sign", "1234"], s.key)
    answer = ade_link.sign_parked(s.setting, s.keys, relay, term, local={}, devices=[ctap_soft.device(s.key)],
                                  now=s.now)
    asked = next(i for i, x in enumerate(term.out) if isinstance(x, tuple))
    listed = term.out[:asked]
    assert listed == ["Requests waiting for this Mac's signature (2):", "  1. " + listed_as(where="198.51.100.9"),
                      "  2. " + listed_as()]
    assert not any(code_of(mine)[:4] in line or code_of(theirs)[:4] in line for line in listed)
    assert term.out[asked][1] == "Type the first four characters of the code your page shows beside your request: "
    assert term.out[asked + 1] == "Not the request you came to sign: " + listed_as(where="198.51.100.9")
    assert "  | Run the tests." in term.said() and not any("Push it" in line for line in term.said())
    assert answer == {"signed": True} and [h[0] for h in relay.handed] == ["req-2"] and relay.fetches == 1
    assert term.said()[-2:] == [f"Signed request {code_of(mine)} and handed it over: your page sends it on.",
                                ade_link.SIGN_AFTER]


def test_a_code_given_with_the_command_takes_its_request_alone():
    s = Signer()
    mine, other = s.text(), s.text(id="e" * 32)
    for typed in (code_of(mine), code_of(mine).lower(), code_of(mine).replace("-", " "), code_of(mine)[:4]):
        relay = Relay(s, [parked(s, other, 1), parked(s, mine, 2)])
        term = Term(["sign", "1234"], s.key)
        ade_link.sign_parked(s.setting, s.keys, relay, term, typed=typed, devices=[ctap_soft.device(s.key)],
                             now=s.now)
        assert [h[0] for h in relay.handed] == ["req-2"], typed
        assert term.said()[0] == "Not the request you came to sign: " + listed_as()


@pytest.mark.parametrize("typed", ["KS5", "KS55-RUD", "KS55-RUD1", "", "KS55-RUDC-X",
                                   "KS55" + chr(0x2010) + "RUDC"])
def test_a_code_that_is_not_one_is_refused(typed):
    s = Signer()
    relay = Relay(s, [parked(s, s.text(), 1)])
    assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, Term([], s.key), typed=typed,
                                                   now=s.now)) == \
        "A request's code is four characters, or all eight, of A to Z and 2 to 7. Nothing was signed."


def test_the_code_is_the_one_the_bytes_give_never_the_one_the_ade_says():
    s = Signer()
    mine, other = s.text(), s.text(id="e" * 32)
    # the ADE says the other request carries the code of mine
    relay = Relay(s, [parked(s, other, 1, code=code_of(mine)), parked(s, mine, 2, code=code_of(other))])
    ade_link.sign_parked(s.setting, s.keys, relay, Term(["sign", "1234"], s.key), typed=code_of(mine),
                         devices=[ctap_soft.device(s.key)], now=s.now)
    assert [h[0] for h in relay.handed] == ["req-2"]


def shared_prefix(s):
    """Two payloads whose codes begin with the same four characters, found by changing the id, as anyone who can
    park a request could (about a thousand tries)."""
    seen = {}
    for n in range(100000):
        text = s.text(id=f"{n:032x}")
        prefix = code_of(text)[:4]
        if prefix in seen:
            return seen[prefix], text
        seen[prefix] = text
    raise AssertionError("no two codes share four characters")


def test_two_requests_whose_codes_begin_alike_ask_for_the_whole_code():
    s = Signer()
    first, second = shared_prefix(s)
    relay = Relay(s, [parked(s, first, 1), parked(s, second, 2)])
    term = Term([code_of(second), "sign", "1234"], s.key)
    ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(second)[:4],
                         devices=[ctap_soft.device(s.key)], now=s.now)
    assert ("ask", "More than one request begins so. Type the whole code your page shows: ", 0) in term.out
    assert [h[0] for h in relay.handed] == ["req-2"]
    for whole, why in ((code_of(second)[:4], "The whole code is eight characters of A to Z and 2 to 7. Nothing was "
                                             "signed."),
                       (code_of(second)[:4] + "AAAA", "No waiting request carries that code. Nothing was signed.")):
        relay = Relay(s, [parked(s, first, 1), parked(s, second, 2)])
        assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, Term([whole], s.key),
                                                       typed=code_of(second)[:4], now=s.now)) == why


def test_two_requests_that_carry_one_whole_code_are_both_refused():
    s = Signer()
    text = s.text()
    relay = Relay(s, [parked(s, text, 1), parked(s, text, 2)])
    for typed in (code_of(text), code_of(text)[:4]):
        answers = [code_of(text)] if len(typed) == 4 else []
        assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, Term(answers, s.key), typed=typed,
                                                       now=s.now)) == \
            "Two waiting requests carry that code, which only someone searching for it could make. Neither is " \
            "signed: cancel them on the page."
    assert relay.handed == [] and s.key.requests == []


def test_the_bytes_signed_are_the_bytes_fetched_once_and_shown():
    s = Signer()
    text = s.text(text="Run the tests.")
    relay = Relay(s, [parked(s, text, 1)], change=True)
    term = Term(["sign", "1234"], s.key)
    with pytest.raises(ade_link.Refused) as e:
        ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(text), devices=[ctap_soft.device(s.key)],
                             now=s.now)
    assert "not over this request" in str(e.value) and relay.fetches == 1 and relay.handed == []
    assert "  | Run the tests." in term.said() and not any("Signed request" in line for line in term.said())
    # what the key signed is the step shown, whose challenge the changed copy does not have
    (_, credential), = relay.offered
    client = json.loads(ade_link.unb64u(credential["response"]["clientDataJSON"]))
    assert client["challenge"] == b64u(passkeys.step_challenge(text.encode("utf-8")))
    assert ade_link._verify_assertion(s.setting, s.keys, text.encode("utf-8"), credential)["credential_id"] == s.id


def test_what_the_relay_says_beside_a_request_is_escaped_and_binds_nothing():
    s = Signer()
    text = s.text()
    request = parked(s, text, 1, parked_from=HOSTILE, purpose=HOSTILE, challenge="AAAA", rp_id="evil.example",
                     origin="https://evil.example")
    junk = text_of({"purpose": HOSTILE, "to": HOSTILE})        # listed before it is read: its kind and target escaped
    relay = Relay(s, [request, parked(s, s.text(id="e" * 32), 2, parked_from=HOSTILE), parked(s, junk, 3)])
    term = Term([code_of(text)[:4], "sign", "1234"], s.key)
    ade_link.sign_parked(s.setting, s.keys, relay, term, devices=[ctap_soft.device(s.key)], now=s.now)
    joined = "\n".join(term.said())
    assert ESC not in joined and CSI not in joined and RLO not in joined and LRI not in joined
    assert '"' + ade_link._flat(HOSTILE, 60) + '"' in joined
    assert '"' + ade_link._flat(HOSTILE, 40) + '" to link "' + ade_link._flat(HOSTILE, 40) + '"' in joined
    assert all(p[1] == HOST for p in s.key.asked("getAssertion")) and [h[0] for h in relay.handed] == ["req-1"]


def test_a_parked_answer_shows_the_question_carried_beside_it_when_its_digest_is_the_one_signed():
    s = Signer()
    text = s.text("answer")
    relay = Relay(s, [parked(s, text, 1, context={"question": {"text": QUESTION}})])
    term = Term(["sign", "1234"], s.key)
    ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(text), devices=[ctap_soft.device(s.key)],
                         now=s.now)
    assert "  | " + QUESTION in term.said() and [h[0] for h in relay.handed] == ["req-1"]
    relay = Relay(s, [parked(s, text, 1, context={"question": {"text": QUESTION + " Now?"}})])
    term = Term(["sign", "1234"], s.key)
    assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(text), now=s.now)) \
        == "The question the ADE served is not the one whose digest the answer signs. Nothing was signed."
    assert term.said() == [] and relay.handed == []


@pytest.mark.parametrize("relay_end, step_end, left", [(290, 3600, 290), (290, 100, 100)])
def test_a_parked_step_shows_the_nearer_end_and_this_mac_s_own_records(relay_end, step_end, left):
    # the relay's end (its nonce's life) where it comes first, the step's own where that does; and what this Mac's
    # records say of the link, through the parked flow as through the ceremony
    s = Signer()
    text = s.text(expires_at=s.now + step_end)
    relay = Relay(s, [parked(s, text, 1, expires_at=s.now + relay_end)])
    term = Term(["sign", "1234"], s.key)
    ade_link.sign_parked(s.setting, s.keys, relay, term, typed=code_of(text), local={"links": {TO: "pp-r57"}},
                         devices=[ctap_soft.device(s.key)], now=s.now)
    said = term.said()
    assert f"  Time left    {left} seconds   (this Mac: its clock)" in said
    assert '               this Mac\'s link "pp-r57"   (this Mac: its own folder says so)' in said


def test_a_relay_that_lists_nothing_or_nothing_readable_signs_nothing():
    s = Signer()
    for listed, why in (([], "No request waits for this Mac's signature."),
                        ({"requests": []}, "The ADE's list of waiting requests is not one this program reads. Nothing "
                                           "was signed."),
                        ([1, 2], "The ADE's list of waiting requests is not one this program reads. Nothing was "
                                 "signed.")):
        relay = Relay(s, [])
        relay.requests = lambda listed=listed: listed
        assert refused_by(lambda: ade_link.sign_parked(s.setting, s.keys, relay, Term([], s.key), now=s.now)) == why


# ---- the proof: the server's own routes and a session's own take, neither changed ---------------------------------

@pytest.fixture
def owner(client):
    return signed_up(client)


@pytest.fixture
def at_a_session(monkeypatch, tmp_path):
    """A session's environment for ade_link.py's own commands, the test's own: no pin held outside the server, no
    inbox, no Claude Code session a variable names, and Claude Code's records read from the test's own home, never the
    machine's (a run inside a Claude Code session would otherwise bind the take to that session)."""
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "ADE_OWNER_KEYS", "CLAUDE_PROJECT_DIR",
                 "CLAUDE_CODE_SESSION_ID", "ADE_LINK_DIR", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "session-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ade_link, "_take_sessions_dir", lambda: str(home / ".claude" / "sessions"))


def page_adds(client, owner, key, origin, name="YubiKey 5C"):
    """The page adds the key's passkey through the server's own registration (passkeys.register): python-fido2's
    Fido2Client asks the key for a passkey over its USB transport, with the client data a browser writes."""
    options = options_for(client, owner)
    made = Fido2Client(ctap_soft.device(key), PageData(origin, options["publicKey"]["rp"]["id"]),
                       user_interaction=Said(), extensions=[]).make_credential(
        PublicKeyCredentialCreationOptions.from_dict(options["publicKey"]))
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": options["challenge_id"], "name": name, "credential": {
            "id": b64u(made.raw_id), "type": "public-key", "response": {
                "clientDataJSON": b64u(bytes(made.response.client_data)),
                "attestationObject": b64u(bytes(made.response.attestation_object))}}})
    assert r.status_code == 200, r.text
    return b64u(made.raw_id), r.json()


def page_confirms(client, owner, key, origin, change, purpose="confirm"):
    """The page's confirmation with the key's passkey, made for `change` (passkeys.CHANGE: each
    change spends only a confirmation made for it), which the page names on the options and on the confirmation:
    python-fido2 asks the key over the challenge the page derives for `purpose` from the server's
    (test_passkeys.derived), with the client data a browser writes."""
    options = client.post("/api/account/confirm/options", headers=owner,
                          json={"purpose": purpose, "change": change}).json()
    pk = options["publicKey"]
    answer = get(key, [ade_link.unb64u(c["id"]) for c in pk["allowCredentials"]],
                 challenge=derived(purpose, ade_link.unb64u(pk["challenge"])), rp_id=pk["rpId"],
                 origin=origin).get_response(0)
    response = answer.response
    r = client.post("/api/account/confirm", headers=owner, json={
        "challenge_id": options["challenge_id"], "change": change, "credential": {
            "id": b64u(answer.raw_id), "type": "public-key", "response": {
                "clientDataJSON": b64u(bytes(response.client_data)),
                "authenticatorData": b64u(bytes(response.authenticator_data)), "signature": b64u(response.signature),
                "userHandle": b64u(response.user_handle) if response.user_handle else None}}})
    assert r.status_code == 200, r.text
    return r.json()["confirmation"]


def a_pinned_link(client, owner, served, capsys, tmp_path):
    """A session's link on this Mac, paired and its owner's keys pinned (trusting the server once): its folder and
    the keys it pinned."""
    d = pair(client, owner, served, capsys, tmp_path / "mac", "mac")
    assert run(capsys, "--dir", d, "pin")[0] == 0
    return d, json.loads((d / "owner_keys.json").read_text())


def composed(client, owner, served, purpose, to, **over):
    """A step as the page composes it, with a nonce of the server's for its purpose, made now."""
    r = client.post("/api/ade/link/nonce", headers=owner, json={"purpose": purpose})
    assert r.status_code == 200, r.text
    t = int(time.time())
    return payload(purpose, **dict({"deployment": served.split("://", 1)[1], "account": r.json()["account"],
                                    "nonce": r.json()["nonce"], "id": os.urandom(16).hex(), "to": to,
                                    "created_at": t, "expires_at": t + (600 if purpose == "resume" else 3600)},
                                   **over))


def on_the_mac(served, keys, key, p, allow):
    """The Mac signs the step: the core reads it, shows it, signs its bytes on the owner's word, PIN and touch, and
    checks the answer before it gives it back."""
    term = Term(["sign", "1234"], key)
    got, raw, credential, _ = ade_link.sign_ceremony(ade_link.SignerSetting(served, p["account"]), keys, text_of(p),
                                                     term, allow, local={}, devices=[ctap_soft.device(key)],
                                                     now=p["created_at"])
    assert got == p and raw == canonicalize(p)
    return credential, term


def test_an_instruction_the_mac_signs_is_stored_by_the_server_s_route_and_taken_by_a_session_s_take(
        client, owner, served, capsys, tmp_path, at_a_session):
    key = ctap_soft.SoftKey()
    credential_id, added = page_adds(client, owner, key, served)
    assert added["tier"] == "device"
    d, keys = a_pinned_link(client, owner, served, capsys, tmp_path)
    p = composed(client, owner, served, "instruction", link_row("mac")["id"], text="Run the tests, then stop.")
    credential, term = on_the_mac(served, keys, key, p, [credential_id])
    assert "  | Run the tests, then stop." in term.said()
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    with store.connect() as db:
        row = db.execute("SELECT payload, signature FROM ade_link_events WHERE seq = ?", (seq,)).fetchone()
    assert row["payload"].encode("utf-8") == canonicalize(p)
    client_data = json.loads(ade_link.unb64u(json.loads(row["signature"])["response"]["clientDataJSON"]))
    assert client_data["proofpane"] == "second-screen/1"
    code, out, _ = run(capsys, "--dir", d, "take", seq)
    assert code == 0 and "TAKEN BY YOU AND VERIFIED FROM THE OWNER: " in out and "Run the tests, then stop." in out


def test_a_resume_the_mac_signs_lifts_a_stop_through_the_server_s_route(client, owner, served, capsys, tmp_path,
                                                                       at_a_session):
    key = ctap_soft.SoftKey()
    credential_id, _ = page_adds(client, owner, key, served)
    d, keys = a_pinned_link(client, owner, served, capsys, tmp_path)
    link_id = link_row("mac")["id"]
    assert client.post(f"/api/ade/link/links/{link_id}/stop", headers=owner).status_code == 200
    stopped = link_row("mac")["stopped_at"]
    while int(time.time()) <= stopped:                                  # a resume is made after the stop it lifts
        time.sleep(0.05)
    p = composed(client, owner, served, "resume", link_id)
    credential, _ = on_the_mac(served, keys, key, p, [credential_id])
    r = client.post(f"/api/ade/link/links/{link_id}/resume", headers=owner,
                    json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    assert link_row("mac")["stopped_at"] is None
    # and the session's own hook, which lifts the stop it saw, takes the Mac's resume as it takes the page's
    held = {"payload": text_of(p), "signature": credential}
    assert ade_link.verify_resume(ade_link.Link(str(d)), held, after=stopped) == p["id"]


def test_while_high_security_is_on_the_server_takes_the_mac_s_signature_by_a_hardware_key_alone(
        client, owner, served, capsys, tmp_path, at_a_session, metadata, maker):
    phone = ctap_soft.SoftKey(serial="0002")                    # no attestation: a device passkey
    phone_id, added = page_adds(client, owner, phone, served, name="Phone")
    assert added["tier"] == "device"
    yubikey = ctap_soft.SoftKey(maker=maker)                    # the maker's model, attested: a hardware key
    yubikey_id, added = page_adds(client, owner, yubikey, served)
    assert added["tier"] == "hardware", added
    d, keys = a_pinned_link(client, owner, served, capsys, tmp_path)
    assert {k["credential_id"] for k in keys["keys"]} == {phone_id, yubikey_id}
    r = client.put("/api/account/high-security", headers=owner,
                   json={"on": True,
                         "confirmation": page_confirms(client, owner, yubikey, served, "high-security-on")})
    assert r.status_code == 200 and r.json()["high_security"] is True, r.text
    link_id = link_row("mac")["id"]
    p = composed(client, owner, served, "instruction", link_id)
    credential, _ = on_the_mac(served, keys, yubikey, p, [yubikey_id, phone_id])
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": credential})
    assert r.status_code == 200, r.text
    code, out, _ = run(capsys, "--dir", d, "take", r.json()["seq"])
    assert code == 0 and "TAKEN BY YOU AND VERIFIED FROM THE OWNER: " in out
    q = composed(client, owner, served, "instruction", link_id)
    credential, _ = on_the_mac(served, keys, phone, q, [phone_id])
    r = client.post("/api/ade/link/commands", headers=owner, json={"payload": q, "credential": credential})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.ONLY_HARDWARE_SIGNS, r.text


def test_what_the_server_would_refuse_is_never_signed(client, owner, served, capsys, tmp_path, at_a_session):
    key = ctap_soft.SoftKey()
    credential_id, _ = page_adds(client, owner, key, served)
    _, keys = a_pinned_link(client, owner, served, capsys, tmp_path)
    p = composed(client, owner, served, "instruction", link_row("mac")["id"], text="do n" + chr(0x0336) + "ot push")
    before = len(key.requests)
    with pytest.raises(ade_link.SignRefused) as e:
        on_the_mac(served, keys, key, p, [credential_id])
    assert str(e.value).endswith("a mark drawn over or around the character before it.")
    assert len(key.requests) == before                                  # no touch spent
    unsigned = client.post("/api/ade/link/commands", headers=owner, json={"payload": p, "credential": {}})
    assert unsigned.status_code == 422 and "U+0336" in unsigned.text    # nor would the server take the words
