"""End-to-end encryption of what a linked session sends the owner, the link client's side
(open_edition/ade_link.py): the envelope and the vectors the page opens, the statements of the owner's encryption keys
and their chain, and posts to the owner, sealed, held or refused. Against a stand-in of the server's side
(e2e_link_stand_in), whose opening side is written from the design without the client's code."""

import hashlib
import json
import os
import shlex
import shutil
import stat
import string
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import ade_link
from e2e_link_stand_in import (ACCOUNT, ENVELOPE, LINK_ID, LinkStandIn, NotOpened, a_link, b64u, body_of, key_id,
                               open_bytes, open_envelope, owner_key, raw_public, slot, unb64u)
from proofpane_core.canonical import canonicalize
from test_ade_link_client import run

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(HERE, "vectors", "e2e_v1.json")
PAGE_VECTORS = os.path.join(HERE, "vectors", "e2e_v1_page.json")


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    monkeypatch.delenv("ADE_OWNER_KEYS", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))           # no pin outside the server, no Claude Code record
    monkeypatch.setattr(ade_link, "sleep", lambda seconds: None)    # a retry waits for nothing


@pytest.fixture
def ade():
    s = LinkStandIn()
    yield s
    s.close()


def held(d):
    with open(os.path.join(d, "e2e.json"), encoding="utf-8") as fh:
        return json.load(fh)


def anchor(d):
    with open(os.path.join(d, "e2e_anchor.json"), encoding="utf-8") as fh:
        return json.load(fh)


def seqs(d):
    return [json.loads(s["payload"])["seq"] for s in held(d)["statements"]]


def event_slot(e):
    return slot(ACCOUNT, LINK_ID, "event", e["kind"], e.get("mirror"), e.get("ref"), e["client_id"])


def opened(e, private):
    return open_envelope(e["text"], event_slot(e), private)


def gets(ade):
    return [p for m, p, _ in ade.requests if m == "GET" and p == "/api/ade/link/e2e"]


def log(d):
    path = os.path.join(d, "mirror.log")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


# ---- the envelope ---------------------------------------------------------------------------------------------------

def fixed(name):
    """An X25519 private key made from fixed bytes, so the vectors are the same at every run."""
    return X25519PrivateKey.from_private_bytes(hashlib.sha256(b"proofpane-e2e/1 test " + name.encode()).digest())


def nonce_of(name):
    return hashlib.sha256(b"proofpane-e2e/1 nonce " + name.encode()).digest()[:12]


def seal_here(public, aad_slot, plain, ephemeral, nonce):
    """An envelope made here from the design's words, not by the client: to check the client's bytes."""
    epk = raw_public(ephemeral)
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(public))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + public, info=b"proofpane-e2e/1 content").derive(shared)
    header = bytes([1]) + hashlib.sha256(public).digest()[:16] + epk + nonce
    return "e2e1." + b64u(header + AESGCM(k).encrypt(nonce, plain, header + aad_slot.encode("utf-8")))


def hkdf(ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"", info=info.encode("utf-8")).derive(ikm)


CID = ["%032x" % (0xabc0 + n) for n in range(8)]

CASES = [
    ("event: a step to the owner (post)", ("event", "step", "", "", CID[0]),
     {"text": "Built the History list.", "session": None, "said_at": None}),
    ("event: a question to the owner", ("event", "question", "", "", CID[1]),
     {"text": "Q-1: per user or per org?", "session": None, "said_at": None}),
    ("event: a reply to a command it took (done, with the command's seq)", ("event", "done", "", 41, CID[2]),
     {"text": "done: 12 passed", "session": None, "said_at": None}),
    ("event: a mirrored note of the session's words, naming its session", ("event", "note", "session", "", CID[3]),
     {"text": "(1 of 2) Checking.", "session": "s-0001", "said_at": 1790000000123}),
    ("event: a mirrored note of the owner's words, no session named", ("event", "note", "owner", "", CID[4]),
     {"text": "how is the build?", "session": None, "said_at": 1790000000000}),
    ("event: text in other scripts, and what JSON escapes", ("event", "note", "", "", CID[5]),
     {"text": "café µ \U0001f600 \"q\" \\ tab\there\nnext\u0001\u007f end", "session": None,
      "said_at": None}),
    ("history: a listed row", ("history", "s-0001", 0),
     {"title": "The History list", "project": "~/work/app", "branch": "main",
      "repositories": ["~/work/app", "~/work/lib"]}),
    ("history: a listed row with no branch and no repository", ("history", "s-0003", 0),
     {"title": "(untitled)", "project": "~", "branch": None, "repositories": []}),
    ("history: a withheld row, its project alone", ("history", "s-0002", 1), {"project": "~/work/app"}),
    ("talk: the owner's message, first of two, three before them", ("talk", "s-0001", 0, 2, 3, "owner", 1790000000,
                                                                    1790000000000), {"text": "how is the build?"}),
    ("talk: the session's message, with no times", ("talk", "s-0001", 1, 2, 3, "session", "", ""),
     {"text": "Green: 12 passed."}),
]


def vectors():
    """The vectors, made from fixed keys and nonces: what tests/vectors/e2e_v1.json holds."""
    ek = fixed("content key")
    out = {"about": "End-to-end encryption: envelopes made by the link client (ade_link.py) from fixed keys "
                    "and nonces, for the page to open and refuse as the Python tests do. "
                    "Keys are base64url without padding; private keys raw (32 bytes) and, for the content key, as "
                    "PKCS#8 DER too; plaintext_text is the plaintext's RFC 8785 bytes as text. A talk's i counts from "
                    "0. The page's own sealed vectors go in e2e_v1_page.json, in this file's shape (content_key, "
                    "envelopes with slot, plaintext and envelope), which the Python tests open when it is there. The "
                    "grant seals the master key's 32 bytes as they are (the envelope's construction, its AAD the "
                    "grant's); the content-key wrap is AES-256-GCM under the content-wrap subkey, given as nonce, "
                    "wrapped (ciphertext and tag) and the two together.",
           "version": 1, "account": ACCOUNT, "link_id": LINK_ID,
           "content_key": {"private": b64u(ek.private_bytes(serialization.Encoding.Raw,
                                                             serialization.PrivateFormat.Raw,
                                                             serialization.NoEncryption())),
                           "pkcs8": b64u(ek.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption())),
                           "public": b64u(raw_public(ek)), "key_id": key_id(raw_public(ek))},
           "envelopes": [], "refused": []}
    for n, (name, parts, value) in enumerate(CASES):
        eph, nonce = fixed(f"ephemeral {n}"), nonce_of(f"envelope {n}")
        s = slot(ACCOUNT, LINK_ID, *parts)
        out["envelopes"].append({
            "name": name, "slot": s, "plaintext": value, "plaintext_text": canonicalize(value).decode("utf-8"),
            "ephemeral_private": b64u(eph.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                        serialization.NoEncryption())),
            "nonce": b64u(nonce), "envelope": ade_link.e2e_seal(raw_public(ek), s, value, eph, nonce)})
    first = out["envelopes"][0]
    raw = body_of(first["envelope"])
    flipped = bytearray(raw)
    flipped[-1] ^= 1
    versioned = bytearray(raw)
    versioned[0] = 2
    other = fixed("another content key")
    body = first["envelope"][len("e2e1."):]
    # a body cut to a length whose last character carries four bits no byte uses, one of them set: it decodes, but
    # does not encode again to itself
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_"
    cut = b64u(raw[:len(raw) - (len(raw) - 1) % 3])
    loose = "e2e1." + cut[:-1] + alphabet[alphabet.index(cut[-1]) | 1]
    bad_plain = [
        ("the plaintext is not in its canonical form", b'{"text": "x", "session": null, "said_at": null}'),
        ("the plaintext is not UTF-8", b'{"text":"\xff","session":null,"said_at":null}'),
        ("the plaintext is not a JSON object", b'["x"]')]
    out["refused"] = [
        {"why": "another slot: another client_id", "slot": slot(ACCOUNT, LINK_ID, "event", "step", "", "", CID[7]),
         "envelope": first["envelope"]},
        {"why": "another account in the slot", "slot": slot("acct-other", LINK_ID, "event", "step", "", "", CID[0]),
         "envelope": first["envelope"]},
        {"why": "a byte of the tag changed", "slot": first["slot"], "envelope": "e2e1." + b64u(bytes(flipped))},
        {"why": "a header of another version", "slot": first["slot"], "envelope": "e2e1." + b64u(bytes(versioned))},
        {"why": "sealed to another key", "slot": first["slot"],
         "envelope": ade_link.e2e_seal(raw_public(other), first["slot"], CASES[0][2], fixed("ephemeral x"),
                                       nonce_of("x"))},
        {"why": "a body with padding", "slot": first["slot"], "envelope": first["envelope"] + "=="},
        {"why": "a body whose length is 1 modulo 4", "slot": first["slot"],
         "envelope": first["envelope"] + "A" * ((1 - len(body)) % 4)},
        {"why": "a body that does not encode again to itself", "slot": first["slot"], "envelope": loose},
        {"why": "not the envelope's mark", "slot": first["slot"], "envelope": "e2e2." + first["envelope"][5:]},
    ] + [{"why": why, "slot": first["slot"],
          "envelope": ade_link._e2e_envelope(raw_public(ek), first["slot"], plain, fixed(f"bad {n}"),
                                             nonce_of(f"bad {n}"))}
         for n, (why, plain) in enumerate(bad_plain)]
    mk = hashlib.sha256(b"proofpane-e2e/1 test master key").digest()
    mk_id = hkdf(mk, "proofpane-e2e/1 mk_id|" + ACCOUNT, 16).hex()
    for name, to, ck in (("grant", "cred-0001", fixed("credential key")), ("recovery_grant", "recovery",
                                                                            fixed("recovery key"))):
        gslot = f"proofpane-e2e/1|{ACCOUNT}|grant|{to}|{mk_id}"
        eph, nonce = fixed(f"{name} ephemeral"), nonce_of(name)
        out[name] = {"to": to, "mk": b64u(mk), "mk_id": mk_id,
                     "private": b64u(ck.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                      serialization.NoEncryption())),
                     "public": b64u(raw_public(ck)), "slot": gslot,
                     "ephemeral_private": b64u(eph.private_bytes(serialization.Encoding.Raw,
                                                                 serialization.PrivateFormat.Raw,
                                                                 serialization.NoEncryption())),
                     "nonce": b64u(nonce), "envelope": ade_link._e2e_envelope(raw_public(ck), gslot, mk, eph, nonce)}
    subkey = hkdf(mk, "proofpane-e2e/1 content-wrap|" + ACCOUNT, 32)
    aad = f"proofpane-e2e/1 content key|{ACCOUNT}|{out['content_key']['key_id']}|{mk_id}"
    pkcs8 = unb64u(out["content_key"]["pkcs8"])
    wnonce = nonce_of("content-key wrap")
    wrapped = AESGCM(subkey).encrypt(wnonce, pkcs8, aad.encode("utf-8"))
    out["content_key_wrap"] = {"mk": b64u(mk), "mk_id": mk_id, "subkey": b64u(subkey), "aad": aad,
                               "plaintext_pkcs8": out["content_key"]["pkcs8"], "nonce": b64u(wnonce),
                               "wrapped": b64u(wrapped), "nonce_and_wrapped": b64u(wnonce + wrapped)}
    return out


def test_the_vectors_file_holds_what_the_client_makes_from_its_fixed_keys():
    made = vectors()
    if os.environ.get("PP_E2E_VECTORS_WRITE"):
        os.makedirs(os.path.dirname(VECTORS), exist_ok=True)
        with open(VECTORS, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(made, indent=1, ensure_ascii=True) + "\n")
    with open(VECTORS, encoding="utf-8") as fh:
        kept = json.load(fh)
    assert kept == made, "the client no longer makes the vectors: if the format changed on purpose, write them again"


def test_each_vector_opens_by_the_design_s_own_words_and_is_the_bytes_made_here_too():
    v = vectors()
    ek = X25519PrivateKey.from_private_bytes(unb64u(v["content_key"]["private"]))
    assert key_id(raw_public(ek)) == v["content_key"]["key_id"]
    for case in v["envelopes"]:
        assert ENVELOPE.fullmatch(case["envelope"])
        assert open_envelope(case["envelope"], case["slot"], ek) == case["plaintext"], case["name"]
        eph = X25519PrivateKey.from_private_bytes(unb64u(case["ephemeral_private"]))
        assert seal_here(raw_public(ek), case["slot"], case["plaintext_text"].encode("utf-8"), eph,
                         unb64u(case["nonce"])) == case["envelope"], case["name"]
        raw = body_of(case["envelope"])
        assert raw[0] == 1 and raw[1:17] == hashlib.sha256(raw_public(ek)).digest()[:16]
        assert raw[17:49] == raw_public(eph) and raw[49:61] == unb64u(case["nonce"])
        assert len(raw) == 77 + len(case["plaintext_text"].encode("utf-8"))
    for case in v["refused"]:
        with pytest.raises(NotOpened):
            open_envelope(case["envelope"], case["slot"], ek)
    for name in ("grant", "recovery_grant"):
        g = v[name]
        ck = X25519PrivateKey.from_private_bytes(unb64u(g["private"]))
        assert open_bytes(g["envelope"], g["slot"], ck) == unb64u(g["mk"])
    w = v["content_key_wrap"]
    assert AESGCM(unb64u(w["subkey"])).decrypt(unb64u(w["nonce"]), unb64u(w["wrapped"]),
                                               w["aad"].encode("utf-8")) == unb64u(w["plaintext_pkcs8"])
    assert serialization.load_der_private_key(unb64u(w["plaintext_pkcs8"]), None).private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()) == unb64u(
        v["content_key"]["private"])


def test_the_page_s_sealed_vectors_open_here():
    if not os.path.exists(PAGE_VECTORS):
        pytest.skip("the page's tests have not written e2e_v1_page.json yet")
    with open(PAGE_VECTORS, encoding="utf-8") as fh:
        v = json.load(fh)
    key = v["content_key"]
    ek = X25519PrivateKey.from_private_bytes(unb64u(key["private"])) if "private" in key else \
        serialization.load_der_private_key(unb64u(key["pkcs8"]), None)
    assert key_id(raw_public(ek)) == key["key_id"]
    assert v["envelopes"]
    for case in v["envelopes"]:
        assert open_envelope(case["envelope"], case["slot"], ek) == case["plaintext"], case.get("name")
        if "ephemeral_private" in case:
            # the same bytes the client seals from the same keys: RFC 8785 alike, the envelope alike
            assert canonicalize(case["plaintext"]) == case["plaintext_text"].encode("utf-8"), case["name"]
            eph = X25519PrivateKey.from_private_bytes(unb64u(case["ephemeral_private"]))
            assert ade_link.e2e_seal(raw_public(ek), case["slot"], case["plaintext"], eph,
                                     unb64u(case["nonce"])) == case["envelope"], case["name"]
    for case in v.get("refused", []):
        with pytest.raises(NotOpened):
            open_envelope(case["envelope"], case["slot"], ek)
    account = v["account"]
    for name in ("grant", "recovery_grant"):
        if name not in v:
            continue
        g = v[name]                                     # the master key's 32 bytes, sealed as an envelope is
        ck = X25519PrivateKey.from_private_bytes(unb64u(g["private"]))
        assert raw_public(ck) == unb64u(g["public"])
        assert g["slot"] == f"proofpane-e2e/1|{account}|grant|{g['to']}|{g['mk_id']}"
        assert hkdf(unb64u(g["mk"]), "proofpane-e2e/1 mk_id|" + account, 16).hex() == g["mk_id"]
        assert open_bytes(g["envelope"], g["slot"], ck) == unb64u(g["mk"]), name
    # each AES-256-GCM blob under its master key's subkey for its purpose and account
    for name, purpose in (("chain", "chain"), ("roster", "roster"), ("content_key_wrap", "content-wrap")):
        if name not in v:
            continue
        b = v[name]
        subkey = hkdf(unb64u(b["mk"]), f"proofpane-e2e/1 {purpose}|{account}", 32)
        assert subkey == unb64u(b["subkey"]), name
        joined = unb64u(b["nonce_and_wrapped"])
        assert joined == unb64u(b["nonce"]) + unb64u(b["wrapped"]), name
        plain = AESGCM(subkey).decrypt(joined[:12], joined[12:], b["aad"].encode("utf-8"))
        if name == "chain":
            assert plain == unb64u(b["previous_mk"])
            assert hkdf(plain, "proofpane-e2e/1 mk_id|" + account, 16).hex() == b["previous"]
        elif name == "roster":
            assert plain == b["plaintext_text"].encode("utf-8") == canonicalize(b["plaintext"])
        else:
            assert b["aad"] == f"proofpane-e2e/1 content key|{account}|{key['key_id']}|{b['mk_id']}"
            assert plain == unb64u(b["plaintext_pkcs8"])
            assert serialization.load_der_private_key(plain, None).private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                serialization.NoEncryption()) == ek.private_bytes(serialization.Encoding.Raw,
                                                                  serialization.PrivateFormat.Raw,
                                                                  serialization.NoEncryption())


def test_a_fresh_key_and_nonce_for_each_envelope_and_the_plaintext_is_rfc_8785():
    ek = X25519PrivateKey.generate()
    value = {"text": "z", "session": "s1", "said_at": 5, "b": [1, "é"]}
    s = slot(ACCOUNT, LINK_ID, "event", "note", "", "", CID[0])
    one, two = (ade_link.e2e_seal(raw_public(ek), s, value) for _ in range(2))
    assert one != two and body_of(one)[17:61] != body_of(two)[17:61]
    assert open_bytes(one, s, ek) == canonicalize(value) == \
        b'{"b":[1,"\xc3\xa9"],"said_at":5,"session":"s1","text":"z"}'
    # a lone surrogate (an undecodable byte of a command line) has no UTF-8 form: it goes as U+FFFD
    assert open_envelope(ade_link.e2e_seal(raw_public(ek), s, {"text": "a\udcffb"}), s, ek) == {"text": "a�b"}


def test_an_all_zero_secret_is_refused():
    class Zero:
        def public_key(self):
            return X25519PrivateKey.generate().public_key()

        def exchange(self, peer):
            return bytes(32)

    with pytest.raises(ValueError, match="all zero"):
        ade_link._e2e_envelope(raw_public(X25519PrivateKey.generate()), "s", b"{}", Zero(), bytes(12))
    with pytest.raises(ValueError):                          # a key of small order, as the library refuses it too
        ade_link.e2e_seal(bytes(32), "s", {"text": "x"})
    with pytest.raises(ValueError, match="12 bytes"):
        ade_link.e2e_seal(raw_public(X25519PrivateKey.generate()), "s", {"text": "x"}, nonce=bytes(11))


def test_a_slot_writes_an_empty_field_as_empty_text_and_a_number_in_decimal():
    assert ade_link.e2e_slot("a", "l", "event", "step", None, None, "c") == "proofpane-e2e/1|a|l|event|step|||c"
    assert ade_link.e2e_slot("a", "l", "talk", "s", 0, 2, 3, "owner", 17, None) == \
        "proofpane-e2e/1|a|l|talk|s|0|2|3|owner|17|"


# ---- the statements and their chain ---------------------------------------------------------------------------------

def test_a_post_to_the_owner_goes_sealed_to_the_newest_key_of_a_chain_the_link_verified(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    _, second = ade.owner.state(retires="all")
    code, out, err = run(capsys, "--dir", d, "post", "step", "built the list")
    assert code == 0, err
    (e,) = ade.events
    assert ENVELOPE.fullmatch(e["text"]) and opened(e, second) == {"text": "built the list", "session": None,
                                                                   "said_at": None}
    with pytest.raises(NotOpened):
        opened(e, first)
    assert seqs(d) == [1, 2] and [s["signer"] for s in held(d)["statements"]] == [owner_key(ade.owner.device)[
        "fingerprint"]] * 2
    tip = held(d)["statements"][-1]["payload"]
    assert anchor(d)["anchor"] == {"seq": 2, "hash": hashlib.sha256(tip.encode()).hexdigest()}
    for name in ("e2e.json", "e2e_anchor.json"):
        assert stat.S_IMODE(os.stat(os.path.join(d, name)).st_mode) == 0o600


@pytest.mark.parametrize("to, sealed", [(None, True), ("owner", True), ("mac", True), ("all", False),
                                        ("cloud", False)])
def test_only_what_goes_to_the_owner_is_sealed_the_link_s_own_name_counting_as_the_owner(ade, capsys, tmp_path, to,
                                                                                          sealed):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    code, _, err = run(capsys, "--dir", d, "post", "note", "the plan", *(["--to", to] if to else []))
    assert code == 0, err
    (e,) = ade.events
    if sealed:
        assert opened(e, key)["text"] == "the plan"
    else:
        assert e["text"] == "the plan"


def test_a_sealed_question_s_digest_is_over_the_envelope_the_ade_stores(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    code, out, _ = run(capsys, "--dir", d, "post", "question", "Q-1: per user or per org?", "--to", "owner")
    (e,) = ade.events
    posted = json.loads(open(os.path.join(d, "posted.json")).read())[str(e["seq"])]
    assert posted["digest"] == "sha256:" + hashlib.sha256(e["text"].encode()).hexdigest()
    assert posted["text"] == "Q-1: per user or per org?"                          # what the session reads back
    assert opened(e, key)["text"] == "Q-1: per user or per org?"


def test_a_reply_is_sealed_with_its_ref_and_working_s_fixed_text_stays_in_the_clear(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    ade.require = True
    words = tmp_path / "reply.txt"
    words.write_text("12 passed")
    assert run(capsys, "--dir", d, "reply", "41", "--status", "done", "--file", words)[0] == 0
    assert run(capsys, "--dir", d, "working", "41")[0] == 0
    done, working = ade.events
    assert done["ref"] == 41 and opened(done, key) == {"text": "done: 12 passed", "session": None, "said_at": None}
    assert (working["kind"], working["text"], working["ref"]) == ("waiting", "working on it", 41)


def test_a_retry_sends_the_same_envelope_sealed_once_for_its_client_id(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    ade.refuse_next_post = [503, 503]
    code, _, err = run(capsys, "--dir", d, "post", "question", "Q-2: ship?", "--to", "owner")
    assert code == 0, err
    sent = ade.posted()
    assert len(sent) == 3 and len({json.dumps(b, sort_keys=True) for b in sent}) == 1      # the same body each time
    (e,) = ade.events
    posted = json.loads(open(os.path.join(d, "posted.json")).read())[str(e["seq"])]
    assert posted["digest"] == "sha256:" + hashlib.sha256(sent[0]["text"].encode()).hexdigest()


def test_the_ade_s_409_has_the_statements_read_again_and_the_text_sealed_once_more(ade, capsys, tmp_path,
                                                                                    monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "post", "step", "before")[0] == 0         # read: no statement, in the clear
    _, key = ade.owner.state()                                                # the owner turns encryption on
    ade.require = True
    code, _, err = run(capsys, "--dir", d, "post", "step", "after")          # within the minute the read stands
    assert code == 0, err
    clear, sealed = ade.posted()[1:]
    assert clear["text"] == "after" and clear["client_id"] == sealed["client_id"]
    assert [e["text"] for e in ade.events][0] == "before" and opened(ade.events[1], key)["text"] == "after"
    assert len(gets(ade)) == 2                                                # read again after the 409


def test_with_no_key_it_can_verify_after_a_409_nothing_more_goes_and_the_hold_is_noted_once(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    assert run(capsys, "--dir", d, "post", "step", "before")[0] == 0
    ade.owner.state(device=ade.owner.other)                  # signed by a passkey this link has not pinned
    ade.require = True
    for n in range(2):
        code, _, err = run(capsys, "--dir", d, "post", "step", f"words {n}")
        assert code == 2 and "not posted: encryption may be on" in err and "has not pinned" in err
        assert "The words stay on this machine" in err
    assert [b["text"] for b in ade.posted()] == ["before", "words 0"]       # the second never left: it knew then
    assert log(d).count("held: encryption") == 1
    ade.owner.state("e2e-off")                              # a pinned passkey turns it off, chained through the first
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert run(capsys, "--dir", d, "post", "step", "clear again")[0] == 0
    assert ade.events[-1]["text"] == "clear again" and log(d).count("no longer held: encryption") == 1


def test_a_link_that_knows_encryption_may_be_on_sends_nothing_in_the_clear(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(device=ade.owner.other)
    code, _, err = run(capsys, "--dir", d, "post", "note", "secret plan")
    assert code == 2 and ade.posted() == [] and "statement 1 is signed by a passkey this link has not pinned" in err


def test_a_text_is_held_to_the_ade_s_rules_before_it_is_sealed(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    for argv, words in ((["question", "a\u202eb", "--to", "owner"], "U+202E"),
                        (["note", "key sk-ant-api03-abc"], "shaped like a secret"),
                        (["note", "x" * 4001], "at most 4000 characters"),
                        (["question", "é", "--to", "owner"], "normal form C"),
                        (["note", "\x01" * 4000], "longer than an envelope holds")):
        code, _, err = run(capsys, "--dir", d, "post", *argv)
        assert code == 2 and words in err, (argv, err)
    assert ade.posted() == []


def test_statements_are_ordered_by_seq_not_as_served(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    _, newest = ade.owner.state()
    real = ade.route

    def backwards(method, path, body):
        status, content = real(method, path, body)
        if path.endswith("/e2e") and method == "GET":
            content = dict(content, statements=list(reversed(content["statements"])))
        return status, content
    monkeypatch.setattr(ade, "route", backwards)
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert seqs(d) == [1, 2] and opened(ade.events[0], newest)["text"] == "x"


def test_a_statement_whose_signature_does_not_verify_is_not_taken(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    forged, _ = ade.owner.state()
    forged["credential"] = ade.owner.signed(ade.owner.payload("e2e-off", seq=9, prev="0" * 64))["credential"]
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert seqs(d) == [1] and "statement 2 does not verify" in out
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert opened(ade.events[0], first)["text"] == "x"


def test_a_statement_signed_by_an_unpinned_passkey_is_taken_once_a_pinned_one_chains_back_through_it(ade, capsys,
                                                                                                  tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    _, unpinned = ade.owner.state(device=ade.owner.other)
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    assert seqs(d) == [1] and opened(ade.events[0], first)["text"] == "one"      # still the key it verified
    assert "signed by a passkey this link has not pinned" in held(d)["wait"]
    _, third = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    assert seqs(d) == [1, 2, 3] and [s["signer"] is None for s in held(d)["statements"]] == [False, True, False]
    assert run(capsys, "--dir", d, "post", "step", "two")[0] == 0
    assert opened(ade.events[1], third)["text"] == "two" and held(d)["wait"] is None


def test_a_statement_that_does_not_match_what_the_link_took_is_a_fork_reported_not_taken(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    ade.owner.statements[0], _ = ade.owner.state(keep=False, seq=1)       # the server's own statement 1, another key
    _, later = ade.owner.state()
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "statement 1 is not the one this link took" in out and seqs(d) == [1]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert opened(ade.events[0], first)["text"] == "x"                         # sealed to the key it holds


def test_a_statement_that_does_not_chain_to_the_one_the_link_holds_is_a_fork(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    ade.owner.state(prev="0" * 64)                                            # seq 2, its prev not statement 1's
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "statement 2 does not chain to the one before it" in out and seqs(d) == [1]


@pytest.mark.parametrize("over", [{"key_id": "0" * 32}, {"seq": 0}, {"prev": "x" * 64}, {"retires": "soon"},
                                  {"key": b64u(b"\x01" * 31)}, {"key": b64u(b"\x02" * 32) + "="},
                                  {"account": "acct-other"}, {"deployment": "elsewhere"}, {"extra": 1}])
def test_a_statement_that_is_not_one_is_not_taken(ade, capsys, tmp_path, over):
    d = a_link(ade, tmp_path / "mac")
    if "key" in over:
        over = dict(over, key_id=hashlib.sha256(unb64u(over["key"])).hexdigest()[:32])
    ade.owner.state(**over)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert held(d)["statements"] == [] and "does not read as one" in out
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and ade.posted() == []   # it may be on: held


def test_a_statement_numbered_out_of_its_place_is_not_taken_and_leaves_nothing_damaged(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    first = hashlib.sha256(ade.owner.statements[0]["payload"].encode()).hexdigest()
    ade.owner.state(seq=3, prev=first)                      # signed, and chained to the first, but numbered 3
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert code == 0 and "not numbered from 1 up (statement 3 after 1)" in out and seqs(d) == [1]


def test_a_server_behind_its_links_on_another_history_is_a_fork_and_is_sent_nothing(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    for _ in range(3):
        ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    ade.owner.statements[:] = [ade.owner.state(keep=False, seq=1)[0]]      # restored, then turned on afresh
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "the ADE's statement 1 is not the one this link took" in out and "took last" not in out
    assert ade.uploads == [] and seqs(d) == [1, 2, 3]


def test_a_read_begun_earlier_never_writes_over_what_a_later_read_kept(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    now = [2_000_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    real, nested = ade_link.Link.request, []

    def overtaken(self, method, path, *a, **kw):
        answer = real(self, method, path, *a, **kw)
        if path == "/e2e" and method == "GET" and not nested:
            nested.append(1)
            ade.owner.state()                               # a second statement, which a read begun later takes first
            now[0] += 5
            ade_link._e2e_refresh(ade_link.Link(d), force=True)
        return answer
    monkeypatch.setattr(ade_link.Link, "request", overtaken)
    ade_link._e2e_refresh(ade_link.Link(d), force=True)
    assert seqs(d) == [1, 2] and held(d)["fetched_at"] == now[0] and ade.uploads == []


def test_a_statement_taken_is_never_dropped_because_its_signer_was_unpinned_since(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    pin = json.loads(open(os.path.join(d, "owner_keys.json")).read())
    pin["keys"] = [owner_key(ade.owner.other)]                  # the owner keeps another passkey pinned alone
    with open(os.path.join(d, "owner_keys.json"), "w") as fh:
        json.dump(pin, fh)
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_reset_says_nothing_of_a_hold_ending_which_it_cannot_know(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state(device=ade.owner.other)
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and "held: encryption" in log(d)
    assert run(capsys, "--dir", d, "e2e", "--reset")[0] == 0
    assert "no longer held" not in log(d)
    assert run(capsys, "--dir", d, "post", "step", "y")[0] == 2 and log(d).count("held: encryption") == 1


def test_an_e2e_off_the_link_verifies_sends_in_the_clear_again(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "sealed")[0] == 0
    ade.owner.state("e2e-off")
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "encryption is off: statement 2 turned it off" in out
    assert run(capsys, "--dir", d, "post", "step", "clear again")[0] == 0
    assert opened(ade.events[0], key)["text"] == "sealed" and ade.events[1]["text"] == "clear again"


def test_after_a_restore_the_link_sends_the_ade_the_statements_it_lacks(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state()
    third, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0 and seqs(d) == [1, 2, 3]
    del ade.owner.statements[1:]                                          # a backup from before the second
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    (upload,) = ade.uploads
    assert [json.loads(s["payload"])["seq"] for s in upload["statements"]] == [2, 3]
    assert upload["statements"][1] == {"payload": third["payload"], "credential": third["credential"]}
    assert len(ade.owner.statements) == 3 and seqs(d) == [1, 2, 3]
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and opened(ade.events[0], key)["text"] == "x"


def test_an_e2e_json_deleted_is_taken_again_only_back_to_the_anchor_never_on_the_server_s_word(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    second, key = ade.owner.state(retires="all")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    os.unlink(os.path.join(d, "e2e.json"))
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and seqs(d) == [1, 2]
    assert opened(ade.events[0], key)["text"] == "x"
    # a server that gives an older chain (one that ends before what the link took) is not believed
    os.unlink(os.path.join(d, "e2e.json"))
    newest = ade.owner.statements.pop()
    code, _, err = run(capsys, "--dir", d, "post", "step", "y")
    assert code == 2 and "this link took statement 2" in err and len(ade.events) == 1
    ade.owner.statements.append(newest)
    assert run(capsys, "--dir", d, "e2e")[0] == 0                       # read again now, not at the minute's end
    assert run(capsys, "--dir", d, "post", "step", "z")[0] == 0 and opened(ade.events[1], key)["text"] == "z"


def test_an_e2e_json_or_anchor_that_cannot_be_read_holds_every_sealed_path(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    for name, text in (("e2e.json", "{cut"), ("e2e.json", ""), ("e2e_anchor.json", '{"anchor": 5}')):
        kept = open(os.path.join(d, name)).read()
        with open(os.path.join(d, name), "w") as fh:
            fh.write(text)
        code, _, err = run(capsys, "--dir", d, "post", "step", "x")
        assert code == 2 and ("cannot be read" in err or "not a record this client writes" in err), (name, err)
        code, out, _ = run(capsys, "--dir", d, "e2e")
        assert code == 2 and "mended in person" in out
        with open(os.path.join(d, name), "w") as fh:
            fh.write(kept)
    # a statement altered in e2e.json no longer chains to the anchor's
    data = held(d)
    payload = json.loads(data["statements"][0]["payload"])
    payload["key"] = b64u(raw_public(X25519PrivateKey.generate()))
    payload["key_id"] = hashlib.sha256(unb64u(payload["key"])).hexdigest()[:32]
    data["statements"][0]["payload"] = canonicalize(payload).decode()
    with open(os.path.join(d, "e2e.json"), "w") as fh:
        json.dump(data, fh)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "does not hold the statement its anchor names" in err and ade.posted() == []


def test_the_statements_are_read_at_most_once_a_minute_on_a_sealed_path(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    now = [2_000_000_000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    run(capsys, "--dir", d, "post", "step", "a")
    run(capsys, "--dir", d, "post", "step", "b")
    run(capsys, "--dir", d, "post", "step", "c", "--to", "all")                 # in the clear: nothing read
    assert len(gets(ade)) == 1
    now[0] += 60
    run(capsys, "--dir", d, "post", "step", "d")
    assert len(gets(ade)) == 2


def test_an_ade_without_the_statements_route_states_none(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.e2e_route = False
    assert run(capsys, "--dir", d, "post", "step", "plain")[0] == 0
    assert ade.events[0]["text"] == "plain" and held(d)["statements"] == [] and held(d)["server"] == {"seq": 0,
                                                                                                      "on": False}


def test_an_answer_that_names_a_newer_statement_has_them_read_at_the_next_send(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.say_e2e = True
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    _, key = ade.owner.state()                                 # turned on, not yet required
    assert run(capsys, "--dir", d, "post", "step", "two")[0] == 0          # within the minute: in the clear
    assert held(d)["fetched_at"] == 0 and held(d)["server"] == {"seq": 1, "on": True}
    assert run(capsys, "--dir", d, "post", "step", "three")[0] == 0
    assert [e["text"] for e in ade.events[:2]] == ["one", "two"] and opened(ade.events[2], key)["text"] == "three"


def test_a_link_with_no_pin_holds_where_the_ade_says_encryption_is_on(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac", pinned=False)
    ade.owner.state()
    ade.require = True
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "no owner keys pinned" in err
    # it reads the list, and takes nothing of it: the ADE's word alone, which holds it before its first text
    assert ade.posted() == [] and len(gets(ade)) == 1 and not held(d).get("statements")
    assert run(capsys, "--dir", d, "post", "step", "y")[0] == 2 and ade.posted() == []


def test_a_link_whose_link_id_is_gone_holds_rather_than_seal_to_none(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0 and seqs(d) == [1]      # the statement taken
    where = os.path.join(d, "config.json")
    with open(where, encoding="utf-8") as fh:
        config = json.load(fh)
    del config["link_id"]
    with open(where, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    code, _, err = run(capsys, "--dir", d, "post", "step", "y")          # within the minute: what it took stands
    assert code == 2 and "this link has no link id to seal with: run pin" in err
    assert len(ade.posted()) == 1


def test_a_statement_s_expiry_bounds_only_the_server_s_acceptance(ade, capsys, tmp_path):
    """A statement whose life ended long ago is taken, and so is one made after it. A time ahead of when the link first
    saw a statement is another matter (test_ade_link_client_e2e_guards.py: one dated ahead is held)."""
    d = a_link(ade, tmp_path / "mac")
    week = int(time.time()) - 7 * 86400
    ade.owner.state(created_at=week, expires_at=week + 600)                    # expired long ago
    _, key = ade.owner.state(created_at=week + 3 * 86400, expires_at=week + 3 * 86400 + 600)   # made later
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 0
    assert seqs(d) == [1, 2] and opened(ade.events[0], key)["text"] == "x"


def test_a_409_that_is_not_encryption_s_is_said_and_nothing_is_read_or_sealed_again(ade, capsys, tmp_path,
                                                                                     monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    real = ade.route

    def withheld(method, path, body):
        if path.endswith("/events"):
            return 409, {"detail": "That session's turns stay on its machine: its link lists it withheld."}
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", withheld)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "refused (409)" in err and "turns stay on its machine" in err
    assert len(ade.posted()) == 1 and len(gets(ade)) == 1


def test_a_post_to_all_is_never_sealed_whatever_the_ade_answers(ade, capsys, tmp_path, monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    real = ade.route

    def required_for_all(method, path, body):
        return ade.required() if path.endswith("/events") else real(method, path, body)
    monkeypatch.setattr(ade, "route", required_for_all)
    code, _, err = run(capsys, "--dir", d, "post", "note", "for all", "--to", "all")
    assert code == 2 and "refused (409)" in err
    assert [b["text"] for b in ade.posted()] == ["for all"]


def test_a_key_the_ade_no_longer_takes_is_no_key_to_seal_to(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, first = ade.owner.state()
    assert run(capsys, "--dir", d, "post", "step", "one")[0] == 0
    ade.owner.state(device=ade.owner.other, retires="all")       # a revocation signed by a passkey it has not pinned
    ade.require = True
    ade.retired.add(key_id(raw_public(first)))
    code, _, err = run(capsys, "--dir", d, "post", "step", "two")
    assert code == 2 and f"the ADE no longer takes key {key_id(raw_public(first))}" in err
    sent = len(ade.posted())
    assert run(capsys, "--dir", d, "post", "step", "three")[0] == 2 and len(ade.posted()) == sent   # it knows now
    assert len(ade.events) == 1


def test_a_key_a_restored_ade_refused_goes_again_once_the_ade_has_its_statement_back(ade, capsys, tmp_path,
                                                                                     monkeypatch):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    del ade.owner.statements[1:]                    # restored from before statement 2: it does not know key 2
    ade.require = True
    ade.retired.add(key_id(raw_public(key)))
    real = ade.route

    def taken_back(method, path, body):
        if path.endswith("/e2e") and method == "POST":
            ade.retired.discard(key_id(raw_public(key)))
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", taken_back)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 0, err
    assert len(ade.uploads) == 1 and opened(ade.events[0], key)["text"] == "x"


def test_after_a_409_nothing_goes_in_the_clear_though_the_link_holds_an_e2e_off(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state("e2e-off")
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    del ade.owner.statements[1:]                    # restored from before the e2e-off: it requires encryption
    ade.require = True
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "the ADE requires encryption, and this link has no key it can seal to" in err
    assert len(ade.posted()) == 1 and len(ade.uploads) == 1 and ade.events == []
    assert run(capsys, "--dir", d, "post", "step", "y")[0] == 0 and ade.events[0]["text"] == "y"   # off again


def test_a_text_refused_again_after_the_statements_are_read_is_held(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    _, key = ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    ade.require = True
    ade.retired.add(key_id(raw_public(key)))        # an ADE that refuses the key, its newest statement still that one
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and f"the ADE refused the text sealed to key {key_id(raw_public(key))} too" in err
    assert len(ade.posted()) == 2 and ade.events == []


def test_a_pair_command_naming_a_key_holds_where_the_ade_states_none(ade, capsys, tmp_path):
    code, _, err = pair(capsys, ade, tmp_path / "mac", "--e2e", "1:" + "a" * 32)
    assert code == 0 and "the pair command named statement 1, and the ADE gives only up to statement 0" in err
    code, _, err = run(capsys, "--dir", tmp_path / "mac", "post", "step", "x")
    assert code == 2 and ade.posted() == []


def test_a_link_that_lost_its_copy_of_what_it_took_holds_where_the_ade_states_none(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    os.unlink(os.path.join(d, "e2e.json"))
    ade.e2e_route = False                                   # an ADE that states nothing now
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "this link took statement 1" in err and ade.posted() == []


def test_another_statement_where_the_link_s_anchor_is_is_not_believed_once_its_copy_is_lost(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    os.unlink(os.path.join(d, "e2e.json"))
    ade.owner.statements[0], _ = ade.owner.state(keep=False, seq=1)      # signed by the pinned passkey too
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "statement 1 is not the one this link took last" in out
    assert run(capsys, "--dir", d, "post", "step", "x")[0] == 2 and ade.posted() == []


def test_a_chain_broken_before_the_anchor_is_not_taken_and_leaves_nothing_damaged(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    for _ in range(3):
        ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    os.unlink(os.path.join(d, "e2e.json"))
    ade.owner.statements[1], _ = ade.owner.state(keep=False, seq=2)      # another statement 2: 3 no longer chains
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert code == 0 and "this link took statement 3, and the ADE gives a chain broken before it" in out
    assert held(d)["statements"] == []


def test_an_e2e_json_older_than_its_anchor_never_seals_to_the_older_key(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    older = held(d)
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    older["fetched_at"] = int(time.time())                  # put back from before statement 2, its read fresh
    with open(os.path.join(d, "e2e.json"), "w") as fh:
        json.dump(older, fh)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "this link took statement 2" in err and ade.posted() == []


def test_an_e2e_json_whose_statements_do_not_chain_holds_though_its_anchor_matches(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    ade.owner.state()
    assert run(capsys, "--dir", d, "e2e")[0] == 0
    data = held(d)
    first = json.loads(data["statements"][0]["payload"])
    first["nonce"] = "another"
    data["statements"][0]["payload"] = canonicalize(first).decode()
    with open(os.path.join(d, "e2e.json"), "w") as fh:
        json.dump(data, fh)
    code, _, err = run(capsys, "--dir", d, "post", "step", "x")
    assert code == 2 and "do not chain as this client took them" in err and ade.posted() == []


def test_the_watcher_reads_the_statements_as_it_starts(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    assert run(capsys, "--dir", d, "watch", "--max", "0")[0] == 0
    assert len(gets(ade)) == 1 and seqs(d) == [1]


def test_a_post_waits_for_the_statements_lock(ade, capsys, tmp_path):
    import fcntl
    import threading
    d = a_link(ade, tmp_path / "mac")
    ade.owner.state()
    fd = os.open(os.path.join(d, "e2e.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = []
    t = threading.Thread(target=lambda: done.append(ade_link.main(["--dir", d, "post", "step", "x"])))
    t.start()
    t.join(1.0)
    assert t.is_alive() and not os.path.exists(os.path.join(d, "e2e.json"))
    os.close(fd)
    t.join(10)
    assert done == [0] and seqs(d) == [1]


# ---- pairing, and what the link says it holds ------------------------------------------------------------------------

def pair(capsys, ade, d, *more):
    return run(capsys, "pair", "--url", ade.url, "--ticket", "PP-AAAAAAAA", "--name", "mac", "--kind", "local",
               "--dir", d, "--keys", owner_key(ade.owner.device)["fingerprint"], *more)


def test_pairing_with_the_newest_statement_takes_it_and_says_so(ade, capsys, tmp_path):
    ade.owner.state()
    s, key = ade.owner.state()
    kid = json.loads(s["payload"])["key_id"]
    code, out, err = pair(capsys, ade, tmp_path / "mac", "--e2e", f"2:{kid}")
    assert code == 0, err
    assert f"encryption is on for this account: what this link sends the owner goes sealed to key {kid} (statement 2" \
        in out
    assert anchor(tmp_path / "mac") == {"anchor": {"seq": 2, "hash": hashlib.sha256(s["payload"].encode()).hexdigest()},
                                        "floor": {"seq": 2, "key_id": kid}, "deployment": ade.owner.host,
                                        "account": ACCOUNT}


def test_a_new_link_refuses_anything_older_than_the_pair_command_names(ade, capsys, tmp_path):
    ade.owner.state()
    s, _ = ade.owner.state()
    kid = json.loads(s["payload"])["key_id"]
    ade.owner.statements.pop()                                 # the server gives only what came before
    code, out, err = pair(capsys, ade, tmp_path / "mac", "--e2e", f"2:{kid}")
    assert code == 0 and "the pair command named statement 2, and the ADE gives only up to statement 1" in err
    assert held(tmp_path / "mac")["statements"] == []
    code, _, err = run(capsys, "--dir", tmp_path / "mac", "post", "step", "x")
    assert code == 2 and ade.posted() == []


def test_a_pair_command_naming_another_key_than_the_ade_s_is_a_fork(ade, capsys, tmp_path):
    ade.owner.state()
    code, _, err = pair(capsys, ade, tmp_path / "mac", "--e2e", "1:" + "f" * 32)
    assert code == 0 and "statement 1 is not the one the pair command named" in err


@pytest.mark.parametrize("given, said", [("0:none", "encryption is off for this account: this link sends in the "
                                                     "clear"),
                                         ("1:off", "encryption is off for this account (statement 1)")])
def test_pairing_where_encryption_is_off_or_never_was_says_so(ade, capsys, tmp_path, given, said):
    if given == "1:off":
        ade.owner.state("e2e-off")
    code, out, _ = pair(capsys, ade, tmp_path / "mac", "--e2e", given)
    assert code == 0 and said in out


@pytest.mark.parametrize("given", ["2", "1:none", "0:off", "x:" + "a" * 32, "1:" + "g" * 32, "1:" + "a" * 31,
                                   "01:off"])
def test_a_pair_command_s_e2e_that_is_not_one_is_refused_before_the_ticket_is_spent(ade, capsys, tmp_path, given):
    with pytest.raises(SystemExit) as e:
        pair(capsys, ade, tmp_path / "mac", "--e2e", given)
    assert e.value.code == 2 and "is not what --e2e takes" in capsys.readouterr().err
    assert ade.requests == [] and not os.path.exists(tmp_path / "mac")       # nothing asked, nothing written


# the line the page writes for a session on this Mac (ui/src/components/pairE2e.test.ts), each form of its --e2e:
# setup, which pairs as pair does and then puts the hooks in the session's settings; the client where
# SETUP_A.md installs it, run isolated, the name the owner gave and nothing to fill in
PAGE_PAIR = 'python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --url {url} --ticket PP-ABCDEFGH --keys {keys} ' \
            "--e2e {e2e} --name 'mac' --kind local --mirror"


@pytest.mark.parametrize("e2e, floor", [("4:" + "0f" * 16, {"seq": 4, "key_id": "0f" * 16}),
                                        ("5:off", {"seq": 5, "key_id": "off"}),
                                        ("0:none", {"seq": 0, "key_id": "none"})])
def test_setup_takes_the_e2e_exactly_as_the_page_writes_it(ade, capsys, tmp_path, e2e, floor):
    installed = tmp_path / "home" / ".proofpane-ade" / "ade_link.py"        # where SETUP_A.md installs the client
    installed.parent.mkdir(parents=True)
    shutil.copyfile(ade_link.__file__, installed)
    line = PAGE_PAIR.format(url=ade.url, keys=owner_key(ade.owner.device)["fingerprint"], e2e=e2e)
    argv = shlex.split(line)[3:]                         # what follows python3 -I .../ade_link.py
    # the hooks go in a folder of the test's own, never the checkout the test runs in: one that is there, as the
    # folder a session runs in is
    (tmp_path / "project").mkdir()
    code, out, err = run(capsys, *argv, "--dir", tmp_path / "mac", "--folder", tmp_path / "project")
    assert code == 0 and out.startswith("ADE: paired as mac"), err
    assert "is not what --e2e takes" not in err and anchor(tmp_path / "mac")["floor"] == floor
    assert os.path.isfile(tmp_path / "project" / ".claude" / "settings.local.json")      # and the hooks went in


def test_linking_a_directory_again_keeps_the_higher_floor(ade, capsys, tmp_path):
    s, _ = ade.owner.state()
    kid = json.loads(s["payload"])["key_id"]
    pair(capsys, ade, tmp_path / "mac", "--e2e", f"1:{kid}")
    pair(capsys, ade, tmp_path / "mac", "--e2e", "0:none")
    assert anchor(tmp_path / "mac")["floor"] == {"seq": 1, "key_id": kid}


def test_an_asking_session_takes_the_pair_command_s_e2e_too(ade, capsys, tmp_path, monkeypatch):
    real = ade.route

    def asks(method, path, body):
        if path.endswith("/ask"):
            return 200, {"status": "linked", "link_id": LINK_ID, "owner_keys": ade.owner_keys()}
        return real(method, path, body)
    monkeypatch.setattr(ade, "route", asks)
    s, _ = ade.owner.state()
    kid = json.loads(s["payload"])["key_id"]
    code, out, err = run(capsys, "pair", "--url", ade.url, "--ask", "ask-" + "a" * 20, "--name", "mac", "--kind",
                         "local", "--dir", tmp_path / "mac", "--e2e", f"1:{kid}")
    assert code == 0, err
    assert f"goes sealed to key {kid} (statement 1" in out and anchor(tmp_path / "mac")["floor"]["seq"] == 1


def test_e2e_says_what_the_link_holds_and_reset_drops_it_on_the_owner_s_word(ade, capsys, tmp_path):
    d = a_link(ade, tmp_path / "mac")
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert code == 0 and "holds no statement, and nothing it holds says encryption is on" in out
    s, _ = ade.owner.state(retires="all")
    kid = json.loads(s["payload"])["key_id"]
    code, out, _ = run(capsys, "--dir", d, "e2e")
    fp = owner_key(ade.owner.device)["fingerprint"]
    assert f"goes sealed to key {kid}, from statement 1 (a revocation, which retired every earlier key at once), " \
           f"signed by passkey {fp}" in out
    # a fork it will not take, until the owner's word drops what it holds
    ade.owner.statements[0], _ = ade.owner.state(keep=False, seq=1)
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert "statement 1 is not the one this link took" in out
    code, out, _ = run(capsys, "--dir", d, "e2e", "--reset")
    assert code == 0 and "dropped what this link held" in out
    assert not os.path.exists(os.path.join(d, "e2e.json")) and not os.path.exists(os.path.join(d, "e2e_anchor.json"))
    new_kid = json.loads(ade.owner.statements[0]["payload"])["key_id"]
    code, out, _ = run(capsys, "--dir", d, "e2e")
    assert f"goes sealed to key {new_kid}" in out
