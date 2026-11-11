"""What a passkey signs (passkeys.py, derived_challenge and step_challenge): in a ceremony whose challenge this server
picks (signing in, a confirmation, end-to-end encryption's touches) the page has the authenticator sign a challenge
derived from it for the ceremony's purpose, never the server's challenge itself, and the server checks the derived
one; a step a linked session takes (a command, a move, a statement of the encryption keys) is signed over the sha256
of "proofpane-step/1", a zero byte and its canonical JSON. So a compromised server cannot hand the page a step's
challenge as its own ceremony's and keep the owner's touch as a signature over a step the owner never saw; and since a
step's challenge in older releases was the sha256 of its canonical JSON alone, and a page then signed the server's
challenge as it was, nothing a passkey signed in an older release is a step. Here: both derivations held to the vectors
the page holds its own to, each purpose's answer refused for every other, the page's answer before this (over the
server's challenge as it is) refused, a step signed in the old form refused by the server and the link client, a page
that names no purpose refused before any challenge is made, the attack made against the page's own code
(ui/src/challengeAttack.test.ts, run here through vitest) and judged by the link client itself, and the old page's
sign-in."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import pytest
from cryptography.hazmat.primitives import serialization

import ade_link
from conftest import signed_up
from e2e_link_stand_in import ACCOUNT, a_link, owner_key
from proofpane_core.canonical import canonicalize
from proofpane_open import passkeys, store
from test_ade_link_client import run
from test_ade_link_client_e2e import ade, opened, quiet, seqs  # noqa: F401  (ade, quiet: fixtures)
from test_passkeys import SIGN_IN_ASKED, Authenticator, add, as_page_asks, as_step, confirmed, derived, stepped

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "..", "ui")
VECTORS = os.path.join(HERE, "vectors", "ceremony_v1.json")
STEP_VECTORS = os.path.join(HERE, "vectors", "step_v1.json")
NODE = shutil.which("node")
VITEST = os.path.join(UI, "node_modules", ".bin", "vitest")
PURPOSES = (passkeys.SIGN_IN,) + passkeys.CONFIRM_PURPOSES


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@pytest.fixture
def owner(client):
    return signed_up(client)


# ---- the derivation --------------------------------------------------------------------------------------------------

def test_each_purpose_s_challenge_is_derived_as_the_vectors_say():
    with open(VECTORS, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["tag"].encode("ascii") == passkeys.CEREMONY_TAG == b"proofpane-confirm/1"
    assert {v["purpose"] for v in data["vectors"]} == set(PURPOSES)
    for v in data["vectors"]:
        challenge = unb64u(v["server_challenge"])
        signed = bytes.fromhex(v["signed_bytes"])
        # the bytes hashed, as the words say: the tag, a zero byte, the purpose, a zero byte, the server's bytes
        assert signed == b"proofpane-confirm/1\x00" + v["purpose"].encode("ascii") + b"\x00" + challenge
        assert b64u(hashlib.sha256(signed).digest()) == v["challenge"]
        assert b64u(passkeys.derived_challenge(v["purpose"], challenge)) == v["challenge"]
        assert b64u(derived(v["purpose"], challenge)) == v["challenge"]        # the tests' stand-in for the page


@pytest.mark.parametrize("purpose", ["register", "signin", "", "confirm\x00", "e2e-key", "e2e-off", "CONFIRM"])
def test_a_challenge_is_derived_for_a_ceremony_s_purpose_alone(purpose):
    with pytest.raises(ValueError):
        passkeys.derived_challenge(purpose, bytes(32))


def test_no_purpose_and_challenge_give_the_bytes_of_another():
    # the zero after the purpose ends it: a purpose and the start of a challenge never read as another purpose
    seen = {}
    for purpose in PURPOSES:
        for challenge in (b"", b"\x00", b"-touch", bytes(32), b"\x00" + b"x" * 31, b"e2e-cancel\x00" + bytes(21)):
            got = passkeys.derived_challenge(purpose, challenge)
            assert got not in seen, (purpose, challenge, seen.get(got))
            seen[got] = (purpose, challenge)


# ---- a step's challenge ------------------------------------------------------------------------------------------------

def test_a_step_s_challenge_is_made_as_the_vectors_say_by_the_server_and_the_link():
    with open(STEP_VECTORS, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["tag"].encode("ascii") == passkeys.STEP_TAG == ade_link.STEP_TAG == b"proofpane-step/1"
    assert {v["payload"]["purpose"] for v in data["vectors"]} == {"instruction", "resume", "move", "e2e-off"}
    for v in data["vectors"]:
        raw = canonicalize(v["payload"])
        assert raw == v["canonical"].encode("utf-8")
        signed = bytes.fromhex(v["signed_bytes"])
        # the bytes hashed, as the words say: the tag, a zero byte, the canonical JSON
        assert signed == b"proofpane-step/1\x00" + raw
        assert b64u(hashlib.sha256(signed).digest()) == v["challenge"]
        assert b64u(passkeys.step_challenge(raw)) == v["challenge"]
        assert b64u(ade_link.step_challenge(raw)) == v["challenge"]
        assert b64u(stepped(raw)) == v["challenge"]                          # the tests' stand-in for the page
        # the older releases' form, the sha256 of the canonical JSON alone, is none of these
        assert v["old_challenge"] == b64u(hashlib.sha256(raw).digest()) != v["challenge"]


def test_a_step_signed_in_the_older_form_is_refused_by_the_server(client, owner):
    # what an older page signed: a command over the sha256 of its canonical JSON alone, or (asked by a server that set
    # its challenge to that) a sign-in or a confirmation over the server's challenge as it was
    device = Authenticator(-8)
    add(client, owner, device)
    user = client.get("/api/auth/me", headers=owner).json()["id"]
    with open(STEP_VECTORS, encoding="utf-8") as fh:
        vectors = json.load(fh)["vectors"]
    for v in vectors:
        raw = canonicalize(v["payload"])
        old = device.get({"publicKey": {"challenge": v["old_challenge"]}})
        with pytest.raises(passkeys.PasskeyError) as refused:
            passkeys.check_command(user, raw, old)
        assert refused.value.status == 400 and str(refused.value) == passkeys.CANONICAL_DRIFT
        # the same passkey's signature over the step's challenge is one the server takes
        held, _ = passkeys.check_command(user, raw, device.get(as_step(raw)))
        assert held["user_id"] == user


# ---- each ceremony checks its own purpose's -------------------------------------------------------------------------

def ask(client, headers, purpose):
    """The options of a ceremony of `purpose`, as the page asks for them."""
    if purpose == passkeys.SIGN_IN:
        return client.post("/api/auth/passkey/options", json=SIGN_IN_ASKED).json()
    return client.post("/api/account/confirm/options", headers=headers, json={"purpose": purpose}).json()


def answer(client, headers, purpose, options, credential):
    if purpose == passkeys.SIGN_IN:
        return client.post("/api/auth/passkey", json={"challenge_id": options["challenge_id"], "credential": credential})
    return client.post("/api/account/confirm", headers=headers,
                       json={"challenge_id": options["challenge_id"], "credential": credential})


@pytest.mark.parametrize("purpose", PURPOSES)
def test_an_answer_made_for_one_purpose_is_refused_for_every_other(client, owner, purpose):
    device = Authenticator(-8)                  # synced: no counter, so only the challenge can refuse a second use
    assert add(client, owner, device).status_code == 200
    options = ask(client, owner, purpose)
    refused = {passkeys.SIGN_IN: (401, passkeys.SIGN_IN_FAILED)}.get(purpose, (403, passkeys.CONFIRM_FAILED))
    for other in PURPOSES:
        if other != purpose:
            r = answer(client, owner, purpose, options, device.get(as_page_asks(options, other)))
            assert (r.status_code, r.json()["detail"]) == refused, other
    # nor the server's challenge as it is, which the page before this signed
    r = answer(client, owner, purpose, options, device.get(options))
    assert (r.status_code, r.json()["detail"]) == refused
    # and each refusal left it unused: the purpose's own answer still goes through
    assert answer(client, owner, purpose, options, device.get(as_page_asks(options, purpose))).status_code == 200


@pytest.mark.parametrize("purpose", PURPOSES)
def test_the_check_refuses_another_purpose_s_answer_as_not_for_this_request(client, owner, purpose):
    device = Authenticator(-8)
    add(client, owner, device)
    options = ask(client, owner, purpose)
    challenge = unb64u(options["publicKey"]["challenge"])
    user = None if purpose == passkeys.SIGN_IN else client.get("/api/auth/me", headers=owner).json()["id"]
    for other in [p for p in PURPOSES if p != purpose]:
        credential = device.get(as_page_asks(options, other))
        with pytest.raises(passkeys.PasskeyError, match="not for this request"):
            passkeys._assertion(credential, challenge, 2 ** 40, 0, purpose, owner=user)


def test_a_confirmation_s_options_name_one_of_its_purposes_and_keep_it(client, owner):
    device = Authenticator(-8)
    add(client, owner, device)
    for wrong in ("sign-in", "register", "", "e2e-key", "CONFIRM", 7):
        r = client.post("/api/account/confirm/options", headers=owner, json={"purpose": wrong})
        assert r.status_code == 422, wrong
    options = client.post("/api/account/confirm/options", headers=owner, json={"purpose": "confirm"}).json()
    assert answer(client, owner, "confirm", options, device.get(as_page_asks(options, "confirm"))).status_code == 200
    # the purpose travels in the challenge id, under its MAC: the page cannot answer it as another
    options = ask(client, owner, "e2e-cancel")
    payload, mac = options["challenge_id"].split(".")
    fields = json.loads(unb64u(payload))
    assert fields["s"] == "e2e-cancel"
    bent = b64u(json.dumps(dict(fields, s="confirm"), separators=(",", ":")).encode()) + "." + mac
    r = client.post("/api/account/confirm", headers=owner,
                    json={"challenge_id": bent, "credential": device.get(as_page_asks(options, "confirm"))})
    assert r.status_code == 400 and "expired or was used" in r.json()["detail"]


def test_a_page_that_names_no_purpose_is_refused_before_its_prompt(client, owner):
    # a page from an older release asks for a sign-in's options with no body, and a confirmation's with none or with
    # {"hardware": true}, and would have its passkey sign the server's challenge as it is: it gets no options, so it
    # never prompts, and is told to reload
    device = Authenticator(-8)
    add(client, owner, device)
    for body in ({}, {"json": {}}, {"json": {"purpose": None}}):
        r = client.post("/api/auth/passkey/options", **body)
        assert (r.status_code, r.json()) == (400, {"detail": passkeys.OLD_PAGE}), body
    for body in ({}, {"json": {}}, {"json": {"hardware": True}}, {"json": {"purpose": None}}):
        r = client.post("/api/account/confirm/options", headers=owner, **body)
        assert (r.status_code, r.json()) == (400, {"detail": passkeys.OLD_PAGE}), body
    for wrong in ("confirm", "register", "", "SIGN-IN", 7):
        r = client.post("/api/auth/passkey/options", json={"purpose": wrong})
        assert r.status_code == 422 and "challenge_id" not in r.json(), wrong
    options = client.post("/api/auth/passkey/options", json={"purpose": "sign-in"}).json()
    r = answer(client, owner, passkeys.SIGN_IN, options, device.get(as_page_asks(options, passkeys.SIGN_IN)))
    assert r.status_code == 200


def test_options_given_before_purposes_were_named_are_a_change_s_confirmation(client, owner):
    # a challenge id this server made just before it named purposes carries none: it is a change's confirmation
    device = Authenticator(-8)
    add(client, owner, device)
    user = client.get("/api/auth/me", headers=owner).json()["id"]
    challenge_id, challenge = passkeys._new_challenge("confirm", user, int(time.time()))
    options = {"challenge_id": challenge_id, "publicKey": {"challenge": b64u(challenge)}}
    r = answer(client, owner, "e2e-cancel", options, device.get(as_page_asks(options, "e2e-cancel")))
    assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_FAILED)
    assert answer(client, owner, "confirm", options, device.get(as_page_asks(options, "confirm"))).status_code == 200


# ---- a confirmation is spent on its own purpose's change -------------------------------------------------------------

@pytest.mark.parametrize("purpose", [p for p in passkeys.CONFIRM_PURPOSES if p != passkeys.CONFIRM])
def test_a_touch_given_for_encryption_confirms_no_change_to_the_account(client, owner, purpose):
    """A confirmation made by a touch for one of encryption's purposes was once taken by any change,
    removing another passkey, adding one, a link's ticket or signing out everywhere among them. Each of those takes only
    a confirmation made for a change ("confirm"): one made for another purpose is refused, and nothing changes."""
    phone, laptop = Authenticator(-8), Authenticator(-7)
    assert add(client, owner, phone).status_code == 200
    assert add(client, owner, laptop, name="Laptop", confirm_with=phone).status_code == 200
    laptop_id = next(p["id"] for p in client.get("/api/account/passkeys", headers=owner).json()["passkeys"]
                     if p["name"] == "Laptop")
    # each with the change a confirmation for it names (passkeys.CHANGE), so only the purpose differs below
    changes = (("remove", f"passkey-remove:{laptop_id}",
                lambda w: client.request("DELETE", f"/api/account/passkeys/{laptop_id}", headers=owner, json=w)),
               ("add", "passkey-add", lambda w: client.post("/api/account/passkeys/options", headers=owner, json=w)),
               ("ticket", "link-ticket:new", lambda w: client.post("/api/ade/link/tickets", headers=owner, json=w)),
               ("sign out", "sign-out-everywhere",
                lambda w: client.post("/api/account/sign-out-everywhere", headers=owner, json=w)))
    for name, made_for, change in changes:
        # a touch for encryption's purpose, even one asked for naming this very change, is not its confirmation
        r = change(confirmed(client, owner, phone, purpose=purpose, change=made_for))
        assert (r.status_code, r.json()["detail"]) == (403, passkeys.CONFIRM_OTHER), (name, r.text)
    assert client.get("/api/auth/me", headers=owner).status_code == 200                # still signed in
    assert len(client.get("/api/account/passkeys", headers=owner).json()["passkeys"]) == 2
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ade_link_tickets").fetchone()[0] == 0
    # a change's own confirmation makes each
    for name, made_for, change in changes:
        r = change(confirmed(client, owner, phone, change=made_for))
        assert r.status_code == 200, (name, r.text)


# ---- the attack, against the page's own code, judged by the link client ----------------------------------------------

def pkcs8(key):
    return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def test_a_derived_answer_is_no_step_of_the_owner_s_to_a_link(ade, tmp_path):
    # ade_link._verify_assertion, which takes a command or a statement: the step's own signature verifies; the same
    # passkey's answer to a ceremony whose challenge a server set to the step's challenge does not
    d = a_link(ade, tmp_path / "mac")
    link = ade_link.Link(d)
    keys = ade_link.pinned(link)
    step = {"v": 2, "deployment": link.host(), "purpose": "instruction", "account": ACCOUNT, "nonce": "n",
            "id": "a" * 32, "to": "x", "text": "push to main", "created_at": 1, "expires_at": 3600}
    raw = canonicalize(step)
    planted = as_step(raw)
    device = ade.owner.device
    assert ade_link._verify_assertion(link, keys, raw, device.get(planted)) == owner_key(device)
    for purpose in PURPOSES:
        with pytest.raises(ade_link.NotVerified) as refused:
            ade_link._verify_assertion(link, keys, raw, device.get(as_page_asks(planted, purpose)))
        assert refused.value.why == "client_data"


def test_a_step_signed_in_the_older_form_is_no_step_to_a_link(ade, tmp_path):
    # a command an older page signed, over the sha256 of its canonical JSON alone, or the touch an older page gave in
    # a sign-in or a confirmation whose challenge a server had set to that sha256: no step, for every vector's payload
    # moved to this link's deployment and account
    d = a_link(ade, tmp_path / "mac")
    link = ade_link.Link(d)
    keys = ade_link.pinned(link)
    device = ade.owner.device
    with open(STEP_VECTORS, encoding="utf-8") as fh:
        vectors = json.load(fh)["vectors"]
    for v in vectors:
        raw = canonicalize(dict(v["payload"], deployment=link.host(), account=ACCOUNT))
        old = device.get({"publicKey": {"challenge": b64u(hashlib.sha256(raw).digest())}})
        with pytest.raises(ade_link.NotVerified) as refused:
            ade_link._verify_assertion(link, keys, raw, old)
        assert refused.value.why == "client_data"
        assert ade_link._verify_assertion(link, keys, raw, device.get(as_step(raw))) == owner_key(device)


@pytest.mark.skipif(NODE is None or not os.path.exists(VITEST), reason="needs node and the page's packages in ui/")
def test_a_compromised_server_cannot_have_the_owner_s_touch_turn_encryption_off(ade, capsys, tmp_path):
    first_dir = a_link(ade, tmp_path / "links" / "first")
    _, first = ade.owner.state()                                    # the owner's statement 1: encryption on
    s1 = ade.owner.statements[0]
    assert run(capsys, "--dir", first_dir, "e2e")[0] == 0 and seqs(first_dir) == [1]
    # the page runs, its touches answered with the owner's own passkey, against a server that plants statement 2
    device = ade.owner.device
    given = {"key": b64u(pkcs8(device.key)), "credential_id": b64u(device.credential_id), "rp_id": "127.0.0.1",
             "origin": ade.url, "account": ACCOUNT, "deployment": ade.owner.host, "newest": s1["payload"],
             "out": str(tmp_path / "kept.json")}
    (tmp_path / "given.json").write_text(json.dumps(given))
    page = subprocess.run([VITEST, "run", "src/challengeAttack.test.ts"], cwd=UI, capture_output=True, text=True,
                          timeout=600, env=dict(os.environ, PP_CHALLENGE_ATTACK=str(tmp_path / "given.json")))
    kept = json.loads((tmp_path / "kept.json").read_text())
    planted = json.loads(kept["statement"])
    assert planted["purpose"] == "e2e-off" and planted["seq"] == 2
    assert planted["prev"] == hashlib.sha256(s1["payload"].encode()).hexdigest()
    assert [k["ceremony"] for k in kept["kept"]] == ["sign-in", "confirm", "e2e-cancel", "e2e-enrol-own", "e2e-enrol-touch",
                                                     "e2e-anchor-touch"], page.stdout + page.stderr
    for n, k in enumerate(kept["kept"]):
        # the server keeps the touch as statement 2 and serves it to a link that holds statement 1
        d = a_link(ade, tmp_path / "links" / f"attacked-{n}")
        ade.owner.statements = [s1]
        assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
        ade.owner.statements = [s1, {"payload": kept["statement"], "credential": k["credential"]}]
        code, out, _ = run(capsys, "--dir", d, "e2e")
        assert seqs(d) == [1], (k["ceremony"], out)
        assert "statement 2 does not verify (the passkey's answer is not for this payload" in out, (k["ceremony"], out)
        assert run(capsys, "--dir", d, "post", "step", f"still sealed {n}")[0] == 0
        assert opened(ade.events[-1], first)["text"] == f"still sealed {n}"            # never in the clear
    # signed over the sha256 of its canonical JSON alone, as an older page signed in or confirmed when a
    # server had set its challenge to that, it is no statement either
    d = a_link(ade, tmp_path / "links" / "old-form")
    ade.owner.statements = [s1]
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    old = device.get({"publicKey": {"challenge": b64u(hashlib.sha256(kept["statement"].encode("utf-8")).digest())}})
    ade.owner.statements = [s1, {"payload": kept["statement"], "credential": old}]
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1] and "statement 2 does not verify (the passkey's answer is not for this payload" in out, out
    # the same statement, signed as a step is (over its step challenge), is one the link takes
    d = a_link(ade, tmp_path / "links" / "signed")
    ade.owner.statements = [s1, ade.owner.signed(planted)]
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1, 2] and "statement 2 turned it off" in out
    assert page.returncode == 0, page.stdout + page.stderr          # and the page's own checks of what it signed hold


# ---- the old page ----------------------------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="needs node to run the page's function")
def test_the_old_page_signs_in_over_the_derived_challenge_too(tmp_path):
    with open(os.path.join(HERE, "..", "web", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    source = (re.search(r"function keyBytes\(b64\) \{\n.*?\n\}\n", script, re.S).group(0)
              + re.search(r"async function signInChallenge\(server\) \{\n.*?\n\}\n", script, re.S).group(0))
    probe = tmp_path / "probe.js"
    probe.write_text(source + "signInChallenge(process.argv[2]).then((c) => "
                              "process.stdout.write(Buffer.from(c).toString('base64url')));\n")
    with open(VECTORS, encoding="utf-8") as fh:
        vector = next(v for v in json.load(fh)["vectors"] if v["purpose"] == passkeys.SIGN_IN)
    got = subprocess.run([NODE, str(probe), vector["server_challenge"]], capture_output=True, text=True, check=True)
    assert got.stdout == vector["challenge"]
    # and the sign-in hands the browser that one alone, asking for user verification whatever the server said
    click = script[script.index('$("passkey-signin").addEventListener'):script.index("async function loadAccount()")]
    # its options asked for naming the purpose, as the server needs (test_a_page_that_names_no_purpose_...)
    assert 'await api("POST", "/api/auth/passkey/options", { purpose: "sign-in" });' in click
    assert click.count("/api/auth/passkey/options") == 1
    assert "const challenge = await signInChallenge(pk.challenge);" in click
    assert 'challenge, rpId: pk.rpId, timeout: 300000, userVerification: "required"' in click
    assert "keyBytes(pk.challenge)" not in click and "pk.userVerification" not in click
