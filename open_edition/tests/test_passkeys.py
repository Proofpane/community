"""Passkeys and signing out everywhere (passkeys.py, auth.py), through the app, with a software authenticator that
answers navigator.credentials.create() and .get() as a platform one does: a key pair per credential, authenticator
data built byte by byte, and its own CBOR writer, so nothing here trusts the module's reader to check itself."""

import hashlib
import json
import struct
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import signed_up
from proofpane_open import auth, passkeys, store

ORIGIN = "https://open.example.test"      # conftest's OPEN_EDITION_PUBLIC_URL
RP = "open.example.test"
OTHER = "other@example.com"


def cbor(value):
    """A CBOR writer for what an authenticator sends: definite lengths, shortest heads."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, fmt in ((24, ">B"), (25, ">H"), (26, ">I"), (27, ">Q")):
            if n < 1 << (8 * struct.calcsize(fmt)):
                return bytes([major << 5 | info]) + struct.pack(fmt, n)
        raise ValueError(n)
    if value is False or value is True or value is None:
        return bytes([0xF4 if value is False else 0xF5 if value is True else 0xF6])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(value)


b64u = passkeys.b64u


def unb64u(text):
    import base64
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Authenticator:
    """One software passkey."""

    def __init__(self, alg=-7, counter=None, rp=RP, origin=ORIGIN):
        self.alg, self.counter, self.rp, self.origin = alg, counter, rp, origin
        self.key = {-7: lambda: ec.generate_private_key(ec.SECP256R1()), -8: Ed25519PrivateKey.generate,
                    -257: lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)}[alg]()
        self.credential_id = bytes(range(32)) if alg == -7 else hashlib.sha256(str(alg).encode()).digest()
        self.user_handle = None

    def cose(self):
        pub = self.key.public_key()
        if self.alg == -8:
            return {1: 1, 3: -8, -1: 6, -2: pub.public_bytes(serialization.Encoding.Raw,
                                                               serialization.PublicFormat.Raw)}
        if self.alg == -7:
            n = pub.public_numbers()
            return {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        n = pub.public_numbers()
        return {1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")}

    def _count(self):
        if self.counter is None:
            return 0                          # a synced passkey keeps no counter
        self.counter += 1
        return self.counter

    def auth_data(self, flags=0x05, attested=False, count=None, tail=b"", new_credential=False):
        data = hashlib.sha256(self.rp.encode()).digest() + bytes([flags | (0x40 if attested or new_credential else 0)])
        data += struct.pack(">I", self._count() if count is None else count)
        if attested or new_credential:
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cbor(self.cose())
        return data + tail

    def client_data(self, kind, drop=(), raw=None, **fields):
        if raw is not None:
            return raw
        data = dict({"type": kind, "origin": self.origin, "crossOrigin": False}, **fields)
        return json.dumps({k: v for k, v in data.items() if k not in drop}).encode()

    def create(self, options, flags=0x05, fmt="none", att_stmt=None, tail=b"", **over):
        pk = options["publicKey"]
        self.user_handle = unb64u(pk["user"]["id"])
        data = self.auth_data(flags=flags, attested=True, tail=tail)
        return {"id": b64u(self.credential_id), "type": "public-key", "response": {
            "clientDataJSON": b64u(self.client_data("webauthn.create", **dict({"challenge": pk["challenge"]},
                                                                              **over))),
            "attestationObject": b64u(cbor({"fmt": fmt, "attStmt": att_stmt or {}, "authData": data}))}}

    def sign(self, message):
        if self.alg == -8:
            return self.key.sign(message)
        if self.alg == -7:
            return self.key.sign(message, ec.ECDSA(hashes.SHA256()))
        return self.key.sign(message, padding.PKCS1v15(), hashes.SHA256())

    def get(self, options, flags=0x05, kind="webauthn.get", count=None, handle=True, tail=b"", new_credential=False,
            **over):
        pk = options["publicKey"]
        data = self.auth_data(flags=flags, count=count, tail=tail, new_credential=new_credential)
        client = self.client_data(kind, **dict({"challenge": pk["challenge"]}, **over))
        return {"id": b64u(self.credential_id), "type": "public-key", "response": {
            "clientDataJSON": b64u(client), "authenticatorData": b64u(data),
            "signature": b64u(self.sign(data + hashlib.sha256(client).digest())),
            "userHandle": b64u(self.user_handle) if handle and self.user_handle else None}}


@pytest.fixture
def owner(client):
    return signed_up(client)


PASSWORD = "correct horse battery"                  # conftest's signed_up
SIGN_IN_ASKED = {"purpose": "sign-in"}             # what the page names as it asks for a sign-in's options
# what the page puts before a challenge the server picked, as it hands the authenticator one (ui/src/passkey.ts,
# derivedChallenge), written here from its words: held to the page's own in tests/vectors/ceremony_v1.json
CEREMONY_TAG = b"proofpane-confirm/1"


def derived(purpose, challenge):
    """The challenge the page has a passkey sign for the server's `challenge` (bytes), in a ceremony of `purpose`."""
    return hashlib.sha256(CEREMONY_TAG + b"\0" + purpose.encode("ascii") + b"\0" + challenge).digest()


def stepped(raw):
    """The challenge the page has a passkey sign for a step whose canonical JSON is `raw` (bytes), written here from
    its words (ui/src/passkey.ts, stepChallenge): held to the page's own in tests/vectors/step_v1.json."""
    return hashlib.sha256(b"proofpane-step/1\0" + raw).digest()


def as_step(raw):
    """A step's options, as the page hands them to the authenticator, for a payload whose canonical JSON is `raw`."""
    return {"publicKey": {"challenge": b64u(stepped(raw))}}


def as_page_asks(options, purpose):
    """The server's options for a ceremony whose challenge it picked, as the page hands them to the authenticator:
    the challenge derived for `purpose`, never the server's own."""
    pk = options["publicKey"]
    return dict(options, publicKey=dict(pk, challenge=b64u(derived(purpose, unb64u(pk["challenge"])))))


def confirm(client, headers, device=None, password=PASSWORD, purpose="confirm", change=None, **kw):
    """The person's own word for a change the sign-in alone does not make: one of the account's own passkeys
    (`device`), signing the challenge derived for `purpose`, as the page asks for it, or its password; made for
    `change` (passkeys.CHANGE), the one change it is spent on, where one is named."""
    named = {"change": change} if change is not None else {}
    if device is None:
        return client.post("/api/account/confirm", headers=headers, json={"password": password, **named})
    options = client.post("/api/account/confirm/options", headers=headers, json={"purpose": purpose, **named}).json()
    return client.post("/api/account/confirm", headers=headers,
                       json={"challenge_id": options["challenge_id"],
                             "credential": device.get(as_page_asks(options, purpose), **kw)})


def confirmed(client, headers, device=None, purpose="confirm", change=None):
    """{"confirmation": ...}, as a change takes it: with the password, or with `device`; made for `change`."""
    r = confirm(client, headers, device, purpose=purpose, change=change)
    assert r.status_code == 200, r.text
    return {"confirmation": r.json()["confirmation"]}


def options_for(client, headers, device=None):
    return client.post("/api/account/passkeys/options", headers=headers,
                       json=confirmed(client, headers, device, change="passkey-add")).json()


def add(client, headers, device, name="Ana's iPhone", confirm_with=None, **kw):
    options = options_for(client, headers, confirm_with)
    return client.post("/api/account/passkeys", headers=headers,
                       json={"challenge_id": options["challenge_id"], "name": name,
                             "credential": device.create(options, **kw)})


def remove(client, headers, passkey_id, body=None):
    return client.request("DELETE", f"/api/account/passkeys/{passkey_id}", headers=headers,
                          json=confirmed(client, headers, change=f"passkey-remove:{passkey_id}") if body is None
                          else body)


def sign_out_everywhere(client, headers, body=None):
    return client.post("/api/account/sign-out-everywhere", headers=headers,
                       json=confirmed(client, headers, change="sign-out-everywhere") if body is None else body)


def sign_in(client, device, **kw):
    options = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    return client.post("/api/auth/passkey", json={"challenge_id": options["challenge_id"],
                                                  "credential": device.get(as_page_asks(options, "sign-in"), **kw)})


def me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})


# ---- adding one, then signing in with it ----------------------------------------------------------------------------

@pytest.mark.parametrize("alg", [-7, -8, -257])
def test_a_passkey_added_while_signed_in_signs_in_the_same_account(client, owner, alg):
    device = Authenticator(alg)
    r = add(client, owner, device)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Ana's iPhone"
    got = sign_in(client, device)
    assert got.status_code == 200, got.text
    assert me(client, got.json()["token"]).json()["email"] == "admin@example.com"
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert listed["last_used_at"] is not None and "public_key" not in listed and "credential_id" not in listed


def test_the_options_ask_for_a_discoverable_verified_passkey_and_its_attestation(client, owner):
    first = Authenticator()
    add(client, owner, first)
    pk = options_for(client, owner, first)["publicKey"]
    # direct: the authenticator's own attestation, which hardware_keys.py judges (tests/test_hardware_keys.py)
    assert pk["rp"] == {"id": RP, "name": "Proofpane Community"} and pk["attestation"] == "direct"
    assert pk["authenticatorSelection"] == {"residentKey": "required", "requireResidentKey": True,
                                            "userVerification": "required"}
    assert [p["alg"] for p in pk["pubKeyCredParams"]] == [-8, -7, -257]
    assert pk["excludeCredentials"] == [{"type": "public-key", "id": b64u(first.credential_id)}]
    signin = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()["publicKey"]
    assert signin["allowCredentials"] == [] and signin["userVerification"] == "required" and signin["rpId"] == RP


def test_adding_needs_the_sign_in_and_one_account_cannot_touch_another_s(client, owner):
    assert client.post("/api/account/passkeys/options").status_code == 401
    assert client.get("/api/account/passkeys").status_code == 401
    options = options_for(client, owner)
    add(client, owner, Authenticator())
    mine = client.get("/api/account/passkeys", headers=owner).json()["passkeys"][0]["id"]
    other = signed_up(client, OTHER)
    assert client.get("/api/account/passkeys", headers=other).json()["passkeys"] == []
    assert remove(client, other, mine).status_code == 404
    # a challenge made for one account does not add a key to another
    r = client.post("/api/account/passkeys", headers=other, json={
        "challenge_id": options["challenge_id"], "credential": Authenticator(-8).create(options)})
    assert r.status_code == 400


def test_a_removed_passkey_signs_in_no_more(client, owner):
    device = Authenticator()
    add(client, owner, device)
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    assert remove(client, owner, listed["id"]).json() == {"removed": listed["id"], "withdrawn": 0, "stopped": [],
                                                          "cancelled": [], "disconnected": [],
                                                          "apps": ""}    # the password confirms it
    r = sign_in(client, device)
    assert r.status_code == 401 and r.json()["detail"] == passkeys.SIGN_IN_FAILED


def test_the_same_passkey_is_added_once_and_an_account_keeps_a_bounded_number(client, owner, monkeypatch):
    device = Authenticator()
    assert add(client, owner, device).status_code == 200
    assert add(client, owner, device, confirm_with=device).status_code == 409
    monkeypatch.setattr(passkeys, "MAX_PASSKEYS", 1)
    r = client.post("/api/account/passkeys/options", headers=owner, json=confirmed(client, owner))
    assert r.status_code == 409


@pytest.mark.parametrize("name,ok", [("", True), ("a" * 60, True), ("a" * 61, False), ("x\u202ey", False),
                                     ("tab\tname", False)])
def test_a_name_is_short_text(client, owner, name, ok):
    r = add(client, owner, Authenticator(), name=name)
    assert (r.status_code == 200) is ok
    if ok and not name:
        # a passkey given no name is named by what it is (tests/test_passkey_words.py): this software one is bound to
        # one device, and the browser said nothing of the authenticator that made it
        assert r.json()["name"] == "Device-bound passkey"


def test_a_self_attestation_adds_a_device_passkey(client, owner):
    # packed with no certificate: the key vouches for itself, which no maker certified
    r = add(client, owner, Authenticator(), fmt="packed", att_stmt={"alg": -7, "sig": b"not a maker's"})
    assert r.status_code == 200
    assert (r.json()["tier"], r.json()["why"]) == ("device", "It attested only with its own key, which no maker "
                                                             "certified.")


# ---- what adding refuses ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change,why", [
    ({"flags": 0x01}, "did not verify"),                     # present, not verified
    ({"flags": 0x04}, "did not verify"),                     # verified, not present
    ({"origin": "https://evil.example"}, "another site"),
    ({"crossOrigin": True}, "another site"),
    ({"crossOrigin": 0}, "another site"),                    # equal to False in Python, and no boolean
    ({"crossOrigin": 0.0}, "another site"),
    ({"topOrigin": ORIGIN}, "another site"),
])
def test_adding_refuses_an_answer_that_does_not_hold(client, owner, change, why):
    device = Authenticator()
    flags = change.pop("flags", 0x05)
    if "origin" in change:
        device.origin = change.pop("origin")
    r = add(client, owner, device, flags=flags, **change)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert client.get("/api/account/passkeys", headers=owner).json()["passkeys"] == []


def test_adding_refuses_another_site_s_key_and_a_mismatched_id(client, owner):
    assert "another site" in add(client, owner, Authenticator(rp="evil.example")).json()["detail"]
    options = options_for(client, owner)
    cred = Authenticator().create(options)
    cred["id"] = b64u(b"x" * 32)
    r = client.post("/api/account/passkeys", headers=owner, json={"challenge_id": options["challenge_id"],
                                                                  "credential": cred})
    assert r.status_code == 400 and "does not match" in r.json()["detail"]


def test_a_challenge_serves_its_own_purpose_and_account(client, owner):
    options = options_for(client, owner)
    body = {"challenge_id": options["challenge_id"], "credential": Authenticator().create(options)}
    body_bad = dict(body, credential=dict(body["credential"], type="password"))
    assert client.post("/api/account/passkeys", headers=owner, json=body_bad).status_code == 400
    assert client.post("/api/account/passkeys", headers=owner, json=body).status_code == 200   # a failure uses nothing
    # a sign-in challenge does not add a key, and an adding one does not sign in
    signin = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    r = client.post("/api/account/passkeys", headers=owner, json={
        "challenge_id": signin["challenge_id"], "credential": Authenticator().create(
            {"publicKey": dict(signin["publicKey"], user={"id": b64u(b"x")})})})
    assert r.status_code == 400


def test_a_challenge_expires_after_five_minutes(client, owner, monkeypatch):
    device = Authenticator()
    options = options_for(client, owner)
    later = time.time() + passkeys.CHALLENGE_TTL + 1
    monkeypatch.setattr(passkeys.time, "time", lambda: later)
    r = client.post("/api/account/passkeys", headers=owner, json={"challenge_id": options["challenge_id"],
                                                                  "credential": device.create(options)})
    assert r.status_code in (400, 401) and "expired" in r.json()["detail"]


# ---- what signing in refuses, always with one wording ----------------------------------------------------------------

@pytest.fixture
def added(client, owner):
    device = Authenticator(counter=0)
    assert add(client, owner, device).status_code == 200
    return device


@pytest.mark.parametrize("kw", [
    {"flags": 0x01}, {"flags": 0x04}, {"kind": "webauthn.create"}, {"origin": "https://evil.example"},
    {"crossOrigin": True}, {"crossOrigin": 0}, {"crossOrigin": 0.0}, {"challenge": "AAAA"},
])
def test_signing_in_refuses_an_answer_that_does_not_hold(client, added, kw):
    r = sign_in(client, added, **kw)
    assert r.status_code == 401 and r.json()["detail"] == passkeys.SIGN_IN_FAILED
    assert sign_in(client, added).status_code == 200            # the passkey itself still works


# what a passkey's client data may not give as its crossOrigin, as written in the JSON: the link client's own check of a
# step is run on the same list (test_ade_link_client_commands.py)
CROSS_ORIGIN_REFUSED = ["0", "0.0", "-0", "-0.0", "0e0", '"false"', '""', "null", "true", "[]"]


@pytest.mark.parametrize("cross", CROSS_ORIGIN_REFUSED)
def test_cross_origin_is_the_json_false_or_left_out_and_nothing_else(cross):
    """crossOrigin is a boolean, which a browser sends as false or, an older one, leaves out. 0 and 0.0 (however
    written) are equal to False in Python, "false" and the empty string are no booleans, and null is not left out:
    each is refused, the check being on the JSON value itself, as the link client's (ade_link.py) is too."""
    challenge = b"c" * 32
    head = '{"type": "webauthn.get", "challenge": "%s", "origin": "%s"' % (b64u(challenge), ORIGIN)
    with pytest.raises(passkeys.PasskeyError, match="another site"):
        passkeys.check_client_data(f'{head}, "crossOrigin": {cross}}}'.encode(), "webauthn.get", challenge)
    for kept in (f'{head}, "crossOrigin": false}}', head + "}"):
        assert passkeys.check_client_data(kept.encode(), "webauthn.get", challenge) == json.loads(kept)


def test_an_answer_that_leaves_cross_origin_out_signs_in(client, added):
    assert sign_in(client, added, drop=("crossOrigin",)).status_code == 200


def test_signing_in_refuses_another_key_s_signature_and_tampered_data(client, added):
    impostor = Authenticator(counter=100)
    impostor.credential_id, impostor.user_handle = added.credential_id, added.user_handle
    assert sign_in(client, impostor).status_code == 401
    options = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    cred = added.get(as_page_asks(options, "sign-in"))
    data = bytearray(unb64u(cred["response"]["authenticatorData"]))
    data[36] ^= 1                                               # the counter, after signing
    cred["response"]["authenticatorData"] = b64u(bytes(data))
    assert client.post("/api/auth/passkey", json={"challenge_id": options["challenge_id"],
                                                  "credential": cred}).status_code == 401


def test_authenticator_data_must_end_where_it_says_and_a_sign_in_carries_no_new_key(client, owner, added):
    assert sign_in(client, added, tail=b"\x00").status_code == 401
    assert sign_in(client, added, new_credential=True).status_code == 401
    assert sign_in(client, added, flags=0x45).status_code == 401   # the new-credential flag alone, no credential
    r = add(client, owner, Authenticator(-8), confirm_with=added, tail=b"\x00")
    assert r.status_code == 400 and "after its end" in r.json()["detail"]
    assert sign_in(client, added).status_code == 200


def test_the_limit_is_checked_again_when_the_key_arrives(client, owner, monkeypatch):
    monkeypatch.setattr(passkeys, "MAX_PASSKEYS", 1)
    first, second = (options_for(client, owner) for _ in range(2))
    for options, device, status in ((first, Authenticator(), 200), (second, Authenticator(-8), 409)):
        r = client.post("/api/account/passkeys", headers=owner, json={"challenge_id": options["challenge_id"],
                                                                      "credential": device.create(options)})
        assert r.status_code == status


def test_the_user_handle_must_be_there_and_be_the_passkey_s_own_account(client, added):
    # no account is named before a passkey sign-in, so WebAuthn L3 (7.2) requires the handle
    assert sign_in(client, added, handle=False).status_code == 401
    own = added.user_handle
    added.user_handle = b"f" * 32
    assert sign_in(client, added).status_code == 401
    added.user_handle = own
    assert sign_in(client, added).status_code == 200


def test_a_counter_that_goes_back_is_refused_and_a_synced_zero_is_not(client, owner):
    counting = Authenticator(counter=0)
    add(client, owner, counting)                               # counter 1 at adding
    assert sign_in(client, counting).status_code == 200        # 2
    assert sign_in(client, counting, count=2).status_code == 401
    assert sign_in(client, counting, count=1).status_code == 401
    assert sign_in(client, counting).status_code == 200        # 3
    synced = Authenticator(-8)
    add(client, owner, synced, name="synced", confirm_with=counting)
    for _ in range(3):
        assert sign_in(client, synced).status_code == 200      # 0 every time


def test_an_unknown_passkey_says_no_more_than_a_wrong_one(client, owner):
    stranger = Authenticator()
    stranger.user_handle = b"u" * 32
    r = sign_in(client, stranger)
    assert r.status_code == 401 and r.json()["detail"] == passkeys.SIGN_IN_FAILED


def test_a_sign_in_that_worked_cannot_be_sent_again(client, owner, monkeypatch):
    synced = Authenticator(-8)            # reports 0 every time, so its counter cannot refuse a replay: the challenge must
    add(client, owner, synced)
    options = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    body = {"challenge_id": options["challenge_id"], "credential": synced.get(as_page_asks(options, "sign-in"))}
    assert client.post("/api/auth/passkey", json=body).status_code == 200
    replay = client.post("/api/auth/passkey", json=body)
    assert replay.status_code == 401 and replay.json()["detail"] == passkeys.SIGN_IN_FAILED
    # a used challenge is remembered only until it expires: the next ceremony after that clears it
    with store.connect() as db:
        # the confirmation, adding, signing in
        assert db.execute("SELECT COUNT(*) FROM passkey_used_challenges").fetchone()[0] == 3
    later = time.time() + passkeys.CHALLENGE_TTL + 1
    monkeypatch.setattr(passkeys.time, "time", lambda: later)
    assert sign_in(client, synced).status_code == 200
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM passkey_used_challenges").fetchone()[0] == 1


def test_asking_for_sign_in_challenges_writes_nothing_and_blocks_nobody(client, added):
    # a cap on live challenges once let anyone switch passkey sign-in off for everyone
    for _ in range(1200):
        assert client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).status_code == 200
    with store.connect() as db:
        # the one adding and its confirmation
        assert db.execute("SELECT COUNT(*) FROM passkey_used_challenges").fetchone()[0] == 2
    assert sign_in(client, added).status_code == 200


@pytest.mark.parametrize("bend", [
    lambda cid: cid[:-2] + ("AA" if cid[-2:] != "AA" else "BB"),                    # another MAC
    lambda cid: b64u(json.dumps({"c": b64u(b"x" * 32), "p": "signin", "u": None, "e": 9999999999}).encode())
    + "." + cid.split(".")[1],                                                      # another payload
    lambda cid: "x" * 700,
    lambda cid: 12345,
])
def test_a_challenge_id_this_deployment_did_not_make_is_refused(client, added, bend):
    options = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    r = client.post("/api/auth/passkey", json={"challenge_id": bend(options["challenge_id"]),
                                               "credential": added.get(as_page_asks(options, "sign-in"))})
    assert r.status_code in (400, 422) and r.status_code != 200


def spare_bits_set(segment):
    """`segment`, base64url, with its last character's lowest bit flipped: the same bytes, where that bit is one past
    them (as in a MAC's 43 characters, which carry 258 bits for its 256), to a decoder that does not check the form."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    return segment[:-1] + alphabet[alphabet.index(segment[-1]) ^ 1]


def bent_ids(cid):
    """A challenge id bent in each way a lenient base64 decoder reads as the id itself, so its MAC holds: four
    characters it skips (four, so the padding it counts comes out the same) in what the id carries or in its MAC; the
    other alphabet's + and / for - and _; the MAC's padding written out, or followed by more; and the last character
    of what the id carries, or of its MAC, with its spare bits set."""
    payload, mac = cid.split(".")
    skipped = ["\x1b" * 4, "\r\x0b\x08\x0c", " " * 4, "\n" * 4, "\t" * 4, "!!!!", "~~~~", "\x00" * 4]
    bent = [payload[:8] + junk + payload[8:] + "." + mac for junk in skipped]
    bent += [payload + "." + mac[:8] + junk + mac[8:] for junk in skipped]
    bent += [cid.replace("-", "+").replace("_", "/"), cid + "=", cid + "=AAAA", payload + "." + spare_bits_set(mac),
             spare_bits_set(payload) + "." + mac]
    return [b for b in bent if b != cid]


def test_a_challenge_id_or_a_nonce_opens_only_as_it_was_written():
    """Each bent id opened, under a decoder that skipped what it could not read, as the one it was bent from: every
    challenge id, a command's nonce among them, now opens only as _new_challenge wrote it."""
    now = int(time.time())
    for _ in range(100):                        # an id with a - or a _ in it, for the other alphabet's + and /
        cid, challenge = passkeys._new_challenge("signin", None, now)
        if "-" in cid or "_" in cid:
            break
    assert len(bent_ids(cid)) == 21
    for bent in bent_ids(cid):
        with pytest.raises(passkeys.PasskeyError, match="expired or was used"):
            passkeys._open_challenge(bent, "signin", None, now)
    assert passkeys._open_challenge(cid, "signin", None, now) == (challenge, now + passkeys.CHALLENGE_TTL)
    nonce = passkeys.new_nonce("u1", "instruction", now)
    for bent in bent_ids(nonce):
        with pytest.raises(passkeys.PasskeyError, match="the nonce is not one this server gave"):
            passkeys.open_nonce(bent, "u1", "instruction", now)
    assert passkeys.open_nonce(nonce, "u1", "instruction", now)[1] == now + passkeys.CHALLENGE_TTL


def test_an_id_of_three_parts_is_refused_as_one_this_deployment_did_not_make():
    """An id that is one _new_challenge wrote with a third part after it (its characters all base64url's) is refused as
    any id it did not write is, never split into an error the routes do not answer."""
    now = int(time.time())
    cid, _ = passkeys._new_challenge("signin", None, now)
    payload, mac = cid.split(".")
    for bent in (cid + ".AAAA", cid + "." + mac, payload + "." + cid, cid + "."):
        with pytest.raises(passkeys.PasskeyError, match="expired or was used"):
            passkeys._open_challenge(bent, "signin", None, now)
    nonce = passkeys.new_nonce("u1", "instruction", now)
    with pytest.raises(passkeys.PasskeyError, match="the nonce is not one this server gave"):
        passkeys.open_nonce(nonce + "." + nonce.split(".")[1], "u1", "instruction", now)


def test_a_sign_in_whose_challenge_id_is_bent_is_refused_and_the_id_as_written_signs_in(client, added):
    options = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    for bent in bent_ids(options["challenge_id"]):
        r = client.post("/api/auth/passkey", json={"challenge_id": bent,
                                                   "credential": added.get(as_page_asks(options, "sign-in"))})
        assert r.status_code == 400, (repr(bent), r.status_code)
    r = client.post("/api/auth/passkey", json={"challenge_id": options["challenge_id"],
                                               "credential": added.get(as_page_asks(options, "sign-in"))})
    assert r.status_code == 200


# ---- crafted client data, and four inputs close to a valid one -------------------------------------------------------

def test_client_data_too_long_or_nested_too_deep_is_refused_not_an_error(client, owner, added, monkeypatch):
    # b64u("[" * 3000) as clientDataJSON once gave a 500 (RecursionError on Linux)
    assert sign_in(client, added, raw=b"[" * 3000).status_code == 401
    assert add(client, owner, Authenticator(-8), confirm_with=added, raw=b"[" * 3000).status_code == 400
    # an answer otherwise good, signed as it is, but over the cap
    assert sign_in(client, added, pad="x" * 3000).status_code == 401
    assert add(client, owner, Authenticator(-8), confirm_with=added, pad="x" * 3000).status_code == 400
    assert sign_in(client, added).status_code == 200
    # the reader recursing out (as it does on Python 3.11 at a depth this one still reads) is a refusal too

    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(passkeys.json, "loads", too_deep)
    with pytest.raises(passkeys.PasskeyError, match="not JSON"):
        passkeys.check_client_data(b"[[[]]]", "webauthn.get", b"x")


def test_a_sign_in_signed_for_another_site_is_refused(client, added):
    added.rp = "evil.example"             # the real key signs authenticator data hashed for another relying party
    assert sign_in(client, added).status_code == 401


def test_client_data_without_its_challenge_is_refused(client, owner, added):
    assert sign_in(client, added, drop=("challenge",)).status_code == 401
    assert add(client, owner, Authenticator(-8), confirm_with=added, drop=("challenge",)).status_code == 400


def test_an_origin_that_only_starts_like_this_site_s_is_refused(client, added):
    added.origin = ORIGIN + ":8443"
    assert sign_in(client, added).status_code == 401


def test_a_counter_back_at_zero_is_refused(client, owner):
    counting = Authenticator(counter=0)
    add(client, owner, counting)                               # 1
    assert sign_in(client, counting).status_code == 200        # 2
    assert sign_in(client, counting, count=0).status_code == 401


def test_the_app_itself_refuses_to_start_for_such_a_host(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, OPEN_EDITION_PUBLIC_URL="https://b\u00fccher.example", OPEN_EDITION_DATA=str(tmp_path),
               OPEN_EDITION_TOKEN_SECRET="t" * 40)
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    r = subprocess.run([sys.executable, "-c", "import proofpane_open.main"], cwd=here, env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode != 0 and "xn--" in r.stderr


def test_the_app_will_not_start_for_a_host_a_passkey_cannot_be_made_for(monkeypatch):
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://b\u00fccher.example")
    with pytest.raises(RuntimeError, match="xn--"):
        passkeys.check_config()
    monkeypatch.setenv("OPEN_EDITION_PUBLIC_URL", "https://xn--bcher-kva.example")
    passkeys.check_config()


# ---- a change the sign-in alone does not make ------------------------------------------------------------------------

def test_adding_a_passkey_needs_a_confirmation_besides_the_sign_in(client, owner):
    # a stolen sign-in token alone could otherwise add its holder's passkey: a way in that signing out everywhere does
    # not end
    for body in ({}, {"confirmation": None}, {"confirmation": ""}):
        r = client.post("/api/account/passkeys/options", headers=owner, json=body)
        assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_CONFIRM
    assert client.post("/api/account/passkeys/options", headers=owner).status_code == 403    # no body at all, too
    first = Authenticator()
    assert add(client, owner, first).status_code == 200                                 # the password confirms it
    assert add(client, owner, Authenticator(-8), confirm_with=first).status_code == 200  # and so does a passkey
    assert add(client, owner, Authenticator(-257)).status_code == 200                   # the password still does
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 3
    # a confirmation serves one change
    once = confirmed(client, owner, change="passkey-add")
    assert client.post("/api/account/passkeys/options", headers=owner, json=once).status_code == 200
    r = client.post("/api/account/passkeys/options", headers=owner, json=once)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_STALE


def test_removing_a_passkey_and_signing_out_everywhere_need_one_too(client, owner):
    # without it, a stolen sign-in could remove the owner's passkeys (and then add its own, as to an account that
    # has none), or sign every other device out and stay signed in itself
    device = Authenticator()
    add(client, owner, device)
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    for body, why in ((None, passkeys.NEEDS_CONFIRM), ({}, passkeys.NEEDS_CONFIRM),
                      ({"confirmation": "x.y"}, passkeys.CONFIRM_STALE)):
        kw = {} if body is None else {"json": body}
        r = client.request("DELETE", f"/api/account/passkeys/{listed['id']}", headers=owner, **kw)
        assert r.status_code == 403 and r.json()["detail"] == why
        r = client.post("/api/account/sign-out-everywhere", headers=owner, **kw)
        assert r.status_code == 403 and r.json()["detail"] == why
    assert client.get("/api/auth/me", headers=owner).status_code == 200                # nothing ended
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 1
    assert sign_in(client, device).status_code == 200


def test_a_confirmation_is_the_account_s_own_passkey_or_its_password(client, owner):
    mine, theirs = Authenticator(), Authenticator(-8)
    add(client, owner, mine)
    other = signed_up(client, OTHER)
    add(client, other, theirs)
    for device, kw in ((theirs, {}), (mine, {"kind": "webauthn.create"}), (mine, {"origin": "https://elsewhere.example"})):
        r = confirm(client, owner, device, **kw)
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FAILED, (device.alg, kw)
    for password in ("wrong horse battery", "", PASSWORD + " ", "x" * 10000):
        r = confirm(client, owner, password=password)
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FAILED, password[:20]
    # a password or a passkey's answer, not neither and not both
    options = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm"}).json()
    assert client.post("/api/account/confirm", headers=owner, json={}).status_code == 400
    both = {"password": PASSWORD, "challenge_id": options["challenge_id"],
            "credential": mine.get(as_page_asks(options, "confirm"))}
    assert client.post("/api/account/confirm", headers=owner, json=both).status_code == 400
    # a confirm challenge is the account's own, offering its own passkeys
    assert {c["id"] for c in options["publicKey"]["allowCredentials"]} == {b64u(mine.credential_id)}
    answer = theirs.get(as_page_asks(options, "confirm"))
    r = client.post("/api/account/confirm", headers=other,
                    json={"challenge_id": options["challenge_id"], "credential": answer})
    assert r.status_code == 400
    # and so is a confirmation; a challenge of another kind, or one this deployment did not make, confirms nothing
    signin = client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()["challenge_id"]
    for given in (confirmed(client, other)["confirmation"], signin, options["challenge_id"], "x.y", "x" * 700):
        r = client.post("/api/account/passkeys/options", headers=owner, json={"confirmation": given})
        assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_STALE
    for route in ("/api/account/confirm/options", "/api/account/confirm"):
        assert client.post(route, json={"password": PASSWORD}).status_code == 401
    assert client.get("/api/account/confirm").status_code == 401


def test_a_password_confirms_nothing_for_an_account_that_is_not_there(client):
    with pytest.raises(passkeys.PasskeyError) as refused:
        passkeys.confirm({"id": "no-such-account"}, password=PASSWORD)
    assert refused.value.status == 403


def test_a_confirmation_lasts_five_minutes(client, owner, monkeypatch):
    once = confirmed(client, owner)
    later = time.time() + passkeys.CHALLENGE_TTL + 1
    monkeypatch.setattr(passkeys.time, "time", lambda: later)
    r = sign_out_everywhere(client, owner, body=once)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_STALE


def test_the_page_learns_what_the_account_can_confirm_with(client, owner):
    assert client.get("/api/account/confirm", headers=owner).json() == {"needed": True, "password": True,
                                                                         "passkeys": 0, "hardware": 0,
                                                                         "high_security": False, "e2e": False}
    r = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm"})
    assert r.status_code == 409 and r.json()["detail"] == passkeys.NO_PASSKEY
    add(client, owner, Authenticator())
    assert client.get("/api/account/confirm", headers=owner).json() == {"needed": True, "password": True,
                                                                         "passkeys": 1, "hardware": 0,
                                                                         "high_security": False, "e2e": False}


@pytest.mark.parametrize("change", [
    "passkey-add-x", "passkey-addx", " passkey-add", "passkey-add\n", "PASSKEY-ADD", f"passkey-remove:{'a' * 32}x",
    f"passkey-remove:{'A' * 32}", f"passkey-remove:{'a' * 31}", "passkey-remove:", "link-ticket:Garbage",
    "link-ticket:", f"link-approve:{'a' * 32}:", f"link-approve:{'a' * 32}:{'b' * 32}:{'c' * 32}",
    f"device-signin:{'a' * 32}", "high-security-maybe", "request-approve:", "connector-on:x", "", 7])
def test_a_change_is_named_only_in_one_of_its_forms(client, owner, change):
    """The change a confirmation is asked for is one of passkeys.CHANGE's forms, whole (a form with more after it,
    another case, an id of another length, or a device's request, which its code names, is none), else 422 before
    anything is asked or counted, on the options and the confirmation alike."""
    body = {"change": change}
    for path, more in (("/api/account/confirm/options", {"purpose": "confirm"}), ("/api/account/confirm",
                                                                                {"password": PASSWORD})):
        r = client.post(path, headers=owner, json=dict(more, **body))
        assert r.status_code == 422, (path, change, r.text)
        if isinstance(change, str):
            assert r.json()["detail"] == passkeys.BAD_CHANGE
    # a wrong password with it is not read, so not counted against the account (auth.counted_check); with a change of
    # its forms the same wrong password is (the control: this test can see a count)
    r = client.post("/api/account/confirm", headers=owner, json={"password": "not the password at all", **body})
    assert r.status_code == 422 and auth._failures == {}, r.text
    r = client.post("/api/account/confirm", headers=owner, json={"password": "not the password at all",
                                                                  "change": "passkey-add"})
    assert r.status_code == 403 and auth._failures != {}, r.text


def test_a_confirmation_names_a_device_or_a_change_never_both(client, owner):
    asked = client.post("/api/auth/device/start", json={"name": "Laptop"}).json()
    for path, more in (("/api/account/confirm/options", {"purpose": "confirm"}), ("/api/account/confirm",
                                                                                {"password": PASSWORD})):
        r = client.post(path, headers=owner, json=dict(more, device=asked["code"], change="passkey-add"))
        assert r.status_code == 422 and "Name one change" in r.json()["detail"], (path, r.text)


def test_a_missing_passkey_leaves_the_confirmation_unused(client, owner):
    add(client, owner, Authenticator())
    (listed,) = client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
    missing = "f" * 32
    once = confirmed(client, owner, change=f"passkey-remove:{missing}")
    # refused for the passkey that is not there, it is left unused: the same confirmation is refused for that again,
    # never as one used up
    assert remove(client, owner, missing, body=once).status_code == 404
    assert remove(client, owner, missing, body=once).status_code == 404
    # made for that passkey, it removes no other
    r = remove(client, owner, listed["id"], body=once)
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_OTHER
    assert remove(client, owner, listed["id"]).json() == {"removed": listed["id"], "withdrawn": 0,
                                                         "stopped": [], "cancelled": [],
                                                         "disconnected": [], "apps": ""}


@pytest.fixture
def local(client):
    """An account with no password and no passkey: the desktop server's own, which signs in only through the link
    that server prints on its own machine."""
    headers = signed_up(client, OTHER)
    with store.connect() as db:
        db.execute("UPDATE users SET password_hash = '' WHERE email = ?", (OTHER,))
    return headers


def test_an_account_with_nothing_to_confirm_with_needs_no_confirmation(client, local):
    assert client.get("/api/account/confirm", headers=local).json() == {"needed": False, "password": False,
                                                                         "passkeys": 0, "hardware": 0,
                                                                         "high_security": False, "e2e": False}
    r = client.post("/api/account/sign-out-everywhere", headers=local)
    assert r.status_code == 200
    local = {"Authorization": "Bearer " + r.json()["token"]}
    first = Authenticator()
    options = client.post("/api/account/passkeys/options", headers=local).json()
    r = client.post("/api/account/passkeys", headers=local,
                    json={"challenge_id": options["challenge_id"], "credential": first.create(options)})
    assert r.status_code == 200
    # once it has a passkey, that passkey confirms, and there is no password to give
    assert client.get("/api/account/confirm", headers=local).json() == {"needed": True, "password": False,
                                                                         "passkeys": 1, "hardware": 0,
                                                                         "high_security": False, "e2e": False}
    assert client.post("/api/account/passkeys/options", headers=local).status_code == 403
    r = confirm(client, local, password="")
    assert r.status_code == 403 and r.json()["detail"] == passkeys.CONFIRM_FAILED
    assert add(client, local, Authenticator(-8), confirm_with=first).status_code == 200


def test_options_given_with_nothing_to_confirm_with_add_no_second_one_unconfirmed(client, local):
    early, later = (client.post("/api/account/passkeys/options", headers=local).json() for _ in range(2))
    for options, device, status in ((early, Authenticator(), 200), (later, Authenticator(-8), 403)):
        r = client.post("/api/account/passkeys", headers=local, json={"challenge_id": options["challenge_id"],
                                                                      "credential": device.create(options)})
        assert r.status_code == status


def test_an_unconfirmed_registration_is_refused_for_an_account_with_a_password(client, owner):
    # the page cannot get such a challenge for this account (the options need a confirmation); adding checks again
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    challenge_id, challenge = passkeys._new_challenge("register", user_id, int(time.time()))
    options = {"publicKey": {"challenge": b64u(challenge), "user": {"id": b64u(user_id.encode())}}}
    r = client.post("/api/account/passkeys", headers=owner,
                    json={"challenge_id": challenge_id, "credential": Authenticator().create(options)})
    assert r.status_code == 403 and r.json()["detail"] == passkeys.NEEDS_CONFIRM


# ---- a lost device ---------------------------------------------------------------------------------------------------

def test_signing_out_everywhere_ends_every_token_but_the_new_one(client, owner):
    phone = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                 "password": "correct horse battery"}).json()["token"]
    r = sign_out_everywhere(client, owner)
    fresh = r.json()["token"]
    assert me(client, phone).status_code == 401
    assert client.get("/api/auth/me", headers=owner).status_code == 401
    assert me(client, fresh).status_code == 200
    again = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                 "password": "correct horse battery"}).json()["token"]
    assert me(client, again).status_code == 200                              # signing in again works


def test_the_terminal_takes_no_token_signing_out_everywhere_ended(client, owner):
    from proofpane_open.ade import workspace_routes
    token = owner["Authorization"][len("Bearer "):]
    assert workspace_routes._hello_user(token)["email"] == "admin@example.com"
    sign_out_everywhere(client, owner)
    assert workspace_routes._hello_user(token) is None


def test_a_token_from_before_generations_is_of_the_first(client, owner):
    from proofpane_open import auth
    import base64
    import hmac
    from proofpane_open import config
    user_id = client.get("/api/auth/me", headers=owner).json()["id"]
    payload = base64.urlsafe_b64encode(json.dumps({"sub": user_id, "exp": int(time.time()) + 60}).encode()
                                       ).rstrip(b"=").decode()
    mac = base64.urlsafe_b64encode(hmac.new(config.token_secret(), payload.encode(), hashlib.sha256).digest()
                                   ).rstrip(b"=").decode()
    old = f"{payload}.{mac}"
    assert auth.token_user(old)["id"] == user_id
    sign_out_everywhere(client, owner)
    assert auth.token_user(old) is None


# ---- the CBOR reader ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data", [
    b"\x5f\x41\x00\xff",                          # an indefinite-length byte string
    b"\xa2\x01\x02\x01\x03",                      # a repeated map key
    b"\xa1\xf5\x01",                              # a boolean key
    b"\x42\x00",                                  # a byte string cut short
    b"\x9b" + b"\xff" * 8,                        # a list claiming 2**64 - 1 items
    b"\x81" * 12 + b"\x00",                       # nesting past the bound
    b"\xf9\x3c\x00",                              # a half-precision float
    b"\x9f" + b"\x00" * 31,                       # an indefinite list, which a reader taking 31 as a count accepts
    b"\x1c",                                      # a reserved additional-information value
    b"\x63\xff\xfe\xfd",                          # text that is not UTF-8
    b"\x01\x02",                                  # bytes after the end
])
def test_the_cbor_reader_refuses_what_webauthn_does_not_send(data):
    with pytest.raises(passkeys.PasskeyError):
        passkeys.cbor_whole(data)


def test_the_cbor_reader_reads_what_it_is_sent():
    value = {1: 2, 3: -7, -1: b"\x00" * 40, "fmt": "none", "list": [True, False, None, 1000000, -70000]}
    assert passkeys.cbor_whole(cbor(value)) == value


@pytest.mark.parametrize("cose,why", [
    ({1: 2, 3: -7, -1: 1, -2: b"\x00" * 32, -3: b"\x00" * 32}, "not a valid key"),      # not on the curve
    ({1: 3, 3: -257, -1: b"\xff" + b"\x01" * 191, -2: b"\x01\x00\x01"}, "too weak"),    # 1536-bit RSA
    ({1: 2, 3: -35, -1: 2, -2: b"\x00" * 48, -3: b"\x00" * 48}, "does not take"),       # ES384
    ({1: 1, 3: -8, -1: 6, -2: b"\x00" * 31}, "does not take"),
])
def test_a_key_this_site_does_not_take(cose, why):
    with pytest.raises(passkeys.PasskeyError, match=why):
        passkeys.cose_key(cose)
