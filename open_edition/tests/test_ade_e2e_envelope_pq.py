"""Post-quantum envelopes as the server reads and makes them (ade/e2e.py):
X-Wing's encapsulation held to draft-11's vectors byte for byte, ML-KEM-768 held to Wycheproof's, the key check at
the coefficient boundary, form 2 sealed to a post-quantum key, a conversation's key and its form-3 messages, the forms
each path reads, and the connector giving out no envelope of any form. Envelopes are opened by the page's side written
for the tests (e2e_pq_page_side.py), never by the server's own code. Across languages: the page's own vectors
(e2e_v2_page.json) open here and are sealed again here by the server's code byte for byte, given the page's
randomness; the server's own (e2e_v2.json, sealed at random, PP_E2E_VECTORS_WRITE=1 writes them) the page opens."""

import base64
import collections
import hashlib
import hmac
import json
import os
import sys

import pytest
from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import mlkem
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import e2e_pq_page_side as page
from proofpane_open.ade import connector, e2e

VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors")
DRAFT11_SHA256 = "1353f61f0d706199d86b6802eab83cabbb5195b2d99fb35bca20f5e4f83db786"   # the draft's text
DRAFT11_FILE_SHA256 = "70c0c7cb747e76ab0da47f0aaa852fd1ea7f6eb7b1437f123b67f662aad0e036"   # xwing_draft11.json
# C2SP/wycheproof, testvectors_v1, at 12fd3aaf33eb5fa1f52e026912ee00c054f9d984
WYCHEPROOF = {
    "mlkem_768_keygen_seed_test.json": "fde5abe284396f4cb3c4610b90d680f0b57782e94c3365c97aee59e24881ebe4",
    "mlkem_768_test.json": "c59c067ae794c343df575dd90f6f7458f51881b11a22d6e9d8677c8d9ee21e90",
    "mlkem_768_encaps_test.json": "9d4381f94c40853bba430245b94968b7390d9175aacd9f1ae4e250a71c78b713",
}


def _path(*parts):
    path = os.path.join(VECTORS, *parts)
    assert os.path.exists(path), f"tests/vectors/{'/'.join(parts)} is missing: the vectors the code is held to"
    return path


def _json(*parts):
    with open(_path(*parts), encoding="utf-8") as fh:
        return json.load(fh)


def _wycheproof(name):
    return [t for g in _json("wycheproof", name)["testGroups"] for t in g["tests"]]


def _draft11():
    v = _json("xwing_draft11.json")
    assert v["source_sha256"] == DRAFT11_SHA256 and v["label"] == e2e.XWING_LABEL.hex()
    return [{k: bytes.fromhex(t[k]) for k in t} for t in v["vectors"]]


def _hybrid():
    seed = os.urandom(32)
    return seed, page.public_of(seed)


# ---- the vectors are the ones the design names ----------------------------------------------------------------------

def test_the_vector_files_are_the_pinned_ones():
    for name, digest in WYCHEPROOF.items():
        with open(_path("wycheproof", name), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == digest, name
    with open(_path("xwing_draft11.json"), "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == DRAFT11_FILE_SHA256      # the file itself, not its own word
    vectors = _draft11()
    assert len(vectors) == 3 and all(t["seed"] == t["sk"] for t in vectors)
    assert {(len(t["pk"]), len(t["eseed"]), len(t["ct"]), len(t["ss"])) for t in vectors} == {(1216, 64, 1120, 32)}


# ---- X-Wing, against draft-11 ---------------------------------------------------------------------------------------

def test_draft11_keys_and_decapsulation_on_the_page_s_side_here():
    for t in _draft11():
        assert page.public_of(t["sk"]) == t["pk"]
        assert page.decapsulate(t["sk"], t["ct"]) == t["ss"]


def test_the_server_s_encapsulation_is_draft11_s_with_the_same_randomness():
    """pyca draws ML-KEM's randomness itself, so the vector's ML-KEM half (its ciphertext, and the secret the vector's
    key decapsulates from it) is handed in; the rest (the X25519 half from eseed[32:64], the combiner and its order,
    pk_X, the label) is the server's own, and gives the vector's ciphertext and secret exactly."""
    for t in _draft11():
        sk_m = page.expand(t["sk"])[0]
        ct_m = t["ct"][:1088]
        seen = []

        def mlkem_half(key, ss_m=sk_m.decapsulate(ct_m), ct_m=ct_m):
            seen.append(key.public_bytes_raw())
            return ss_m, ct_m

        ephemeral = X25519PrivateKey.from_private_bytes(t["eseed"][32:])
        assert e2e.encapsulate(t["pk"], ephemeral, mlkem_half) == (t["ct"], t["ss"])
        assert seen == [t["pk"][:1184]]                                 # the key's ML-KEM half, as it was given


def test_an_encapsulation_decapsulates_to_its_secret():
    seed, pk = _hybrid()
    ct, ss = e2e.encapsulate(pk)
    assert len(ct) == e2e.HYBRID_CT == 1120 and len(ss) == 32
    assert page.decapsulate(seed, ct) == ss
    assert e2e.encapsulate(pk)[0][:1088] != ct[:1088] and e2e.encapsulate(pk)[0][1088:] != ct[1088:]   # fresh, both
    for ss_m, ct_m in ((bytes(31), bytes(1088)), (bytes(32), bytes(1087)), (bytes(33), bytes(1089))):
        with pytest.raises(ValueError, match="of another length"):
            e2e.encapsulate(pk, _mlkem=lambda _key, ss_m=ss_m, ct_m=ct_m: (ss_m, ct_m))


# ---- ML-KEM-768, against Wycheproof ---------------------------------------------------------------------------------

def test_wycheproof_keys_from_seeds():
    tests = _wycheproof("mlkem_768_keygen_seed_test.json")
    assert len(tests) == 100
    for t in tests:
        assert t["result"] == "valid"
        key = mlkem.MLKEM768PrivateKey.from_seed_bytes(bytes.fromhex(t["seed"]))
        assert key.public_key().public_bytes_raw().hex() == t["ek"], t["tcId"]


def test_wycheproof_decapsulation():
    counts = collections.Counter()
    for t in _wycheproof("mlkem_768_test.json"):
        try:
            key = mlkem.MLKEM768PrivateKey.from_seed_bytes(bytes.fromhex(t["seed"]))
            got = (key.public_key().public_bytes_raw().hex(), key.decapsulate(bytes.fromhex(t["c"])).hex())
        except ValueError:
            got = None
        if t["result"] == "valid":
            assert got == (t["ek"], t["K"]), t["tcId"]
        else:
            assert got is None, t["tcId"]                               # malformed: refused, never a secret
        counts[t["result"]] += 1
    assert counts == {"valid": 161, "invalid": 40}


def test_wycheproof_encapsulation_keys_through_the_server_s_key_check():
    """pyca has no derandomized encapsulation, so here Wycheproof's keys alone: each valid one taken, each invalid one
    refused (a coefficient of 3,329 or more, or another length), in the ADE's words."""
    x = X25519PrivateKey.generate().public_key().public_bytes_raw()
    counts = collections.Counter()
    for t in _wycheproof("mlkem_768_encaps_test.json"):
        key = bytes.fromhex(t["ek"]) + x
        assert e2e.usable_hybrid_key(key) is (t["result"] == "valid"), t["tcId"]
        if t["result"] == "invalid":
            with pytest.raises(ValueError) as refused:
                e2e.encapsulate(key)
            assert str(refused.value) == e2e.HYBRID_KEY_WORDS, t["tcId"]
        counts[(t["result"], len(t["ek"]) // 2 == 1184)] += 1
    assert counts == {("valid", True): 133, ("invalid", True): 112, ("invalid", False): 20}


# ---- the key check --------------------------------------------------------------------------------------------------

def _coefficient(pk_m, index):
    at = (index // 2) * 3
    return pk_m[at] | (pk_m[at + 1] & 0x0F) << 8 if index % 2 == 0 else pk_m[at + 1] >> 4 | pk_m[at + 2] << 4


def _with_coefficient(pk_m, index, value):
    """pk_M with coefficient `index` of the 768 its first 1,152 bytes hold (two in each three bytes) set to `value`."""
    b = bytearray(pk_m)
    at = (index // 2) * 3
    if index % 2 == 0:
        b[at], b[at + 1] = value & 0xFF, (b[at + 1] & 0xF0) | value >> 8
    else:
        b[at + 1], b[at + 2] = (b[at + 1] & 0x0F) | (value & 0x0F) << 4, value >> 4
    return bytes(b)


@pytest.mark.parametrize("index", [0, 767])
def test_the_key_check_at_the_coefficient_boundary(index):
    _, pk = _hybrid()
    for value, usable in ((3328, True), (3329, False), (4095, False)):
        key = _with_coefficient(pk[:1184], index, value) + pk[1184:]
        assert _coefficient(key, index) == value and key[1152:] == pk[1152:]
        assert e2e.usable_hybrid_key(key) is usable, value
        if usable:
            e2e.seal(key, "slot", {"text": "x"})
        else:
            for refuse in (e2e.encapsulate, lambda k: e2e.seal(k, "slot", {"text": "x"})):
                with pytest.raises(ValueError) as refused:
                    refuse(key)
                assert str(refused.value) == e2e.HYBRID_KEY_WORDS        # not pyca's words, which name a length


def test_a_key_of_another_length_or_with_a_low_order_x25519_half_is_refused():
    _, pk = _hybrid()
    for key in (pk[:-1], pk + b"\0", pk[:1184] + bytes(32), pk[:1184] + bytes([1]) + bytes(31), b"", "text",
                "x" * 1216, list(pk)):
        assert not e2e.usable_hybrid_key(key)
        with pytest.raises(ValueError):
            e2e.encapsulate(key)
    for key in (pk[:1184] + bytes(32), pk[:1184] + bytes([1]) + bytes(31)):
        with pytest.raises(ValueError):
            e2e.seal(key, "slot", {"text": "x"})                     # the X25519 half of low order
    with pytest.raises(ValueError):
        e2e.seal(pk[:-1], "slot", {"text": "x"})                     # neither a classical nor a post-quantum key


def test_the_server_knows_whether_its_cryptography_does_ml_kem(monkeypatch):
    assert e2e.mlkem_here()

    def gone(*_args, **_kwargs):
        raise UnsupportedAlgorithm("this OpenSSL has no ML-KEM")

    with monkeypatch.context() as m:
        m.setattr(mlkem.MLKEM768PrivateKey, "generate", gone)
        assert not e2e.mlkem_here()
    for ss, ct in ((bytes(31), bytes(1088)), (bytes(32), bytes(1087))):  # an encapsulation of the wrong lengths
        class Key:
            def public_key(self, ss=ss, ct=ct):
                return type("Public", (), {"encapsulate": lambda _self: (ss, ct)})()

        with monkeypatch.context() as m:
            m.setattr(mlkem.MLKEM768PrivateKey, "generate", lambda: Key())
            assert not e2e.mlkem_here()


def test_without_ml_kem_a_post_quantum_key_is_refused_as_that_not_as_a_bad_key(monkeypatch):
    """pyca without the module (older than 47), or with it but an OpenSSL without the algorithm: one error of the
    ADE's own, which no caller takes for a bad key (so a route says why it refuses, and never gives a 500)."""
    _, pk = _hybrid()

    def gone(*_args, **_kwargs):
        raise UnsupportedAlgorithm("this OpenSSL has no ML-KEM")

    for setup in ("algorithm", "module"):
        with monkeypatch.context() as m:
            if setup == "algorithm":
                m.setattr(mlkem.MLKEM768PublicKey, "from_public_bytes", gone)
            else:
                m.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.mlkem", None)
            for call in (e2e.usable_hybrid_key, e2e.encapsulate, lambda k: e2e.seal(k, "slot", {"text": "x"}),
                         lambda k: e2e.seal_talk_key(k, "slot", bytes(32))):
                with pytest.raises(e2e.MLKEMMissing, match="cannot use ML-KEM"):
                    call(pk)
            assert not issubclass(e2e.MLKEMMissing, ValueError)
            assert e2e.usable_hybrid_key(pk[:-1]) is False                  # not a post-quantum key whatever is here
    with pytest.raises(e2e.MLKEMMissing):
        e2e.encapsulate(pk, _mlkem=gone)                                # the encapsulation itself unsupported


# ---- form 2 ---------------------------------------------------------------------------------------------------------

def test_a_form_2_envelope_opens_with_the_seed_and_is_bound_to_its_slot_and_header():
    seed, pk = _hybrid()
    value = {"text": "hello"}
    envelope = e2e.seal(pk, "one slot", value)
    data = page.body_of(envelope)
    plain = e2e.plaintext(value)
    assert len(data) == e2e.OVERHEADS[2] + len(plain) == 1165 + len(plain)
    assert (data[0], data[1:17]) == (2, hashlib.sha256(pk).digest()[:16])
    assert e2e.read(envelope, len(plain), (2,)) == e2e.Shape(2, e2e.key_id(pk), len(data))
    assert page.open_envelope2(seed, envelope, "one slot") == value
    with pytest.raises(page.NotOpened):
        page.open_envelope2(seed, envelope, "another slot")
    for at in (0, 1, 17, 17 + 1087, 1105, 1136, 1137, 1148, 1149, len(data) - 1):
        # the form, the key_id, ct_M (first and last byte), ct_X (first and last), the nonce, the ciphertext, the tag
        changed = bytearray(data)
        changed[at] ^= 1
        with pytest.raises((page.NotOpened, ValueError)):
            page.open_bytes2(seed, "e2e1." + e2e.b64u(bytes(changed)), "one slot")
    other_seed, _ = _hybrid()
    with pytest.raises(page.NotOpened):
        page.open_envelope2(other_seed, envelope, "one slot")
    assert e2e.seal(pk, "one slot", value) != envelope                  # fresh randomness and nonce each time


def test_the_form_is_the_key_s():
    _, pk = _hybrid()
    classical = X25519PrivateKey.generate().public_key().public_bytes_raw()
    assert e2e.read(e2e.seal(pk, "s", {"a": 1}), 100, e2e.FORMS).form == 2
    assert e2e.read(e2e.seal(classical, "s", {"a": 1}), 100, e2e.FORMS).form == 1


def test_a_fixed_ephemeral_and_nonce_land_where_the_header_says():
    seed, pk = _hybrid()
    ephemeral = X25519PrivateKey.generate()
    nonce = os.urandom(12)
    data = page.body_of(e2e.seal(pk, "s", {"a": 1}, ephemeral=ephemeral, nonce=nonce))
    assert data[1105:1137] == ephemeral.public_key().public_bytes_raw() and data[1137:1149] == nonce
    with pytest.raises(ValueError):
        e2e.seal(pk, "s", {"a": 1}, nonce=os.urandom(11))
    assert page.open_envelope2(seed, "e2e1." + e2e.b64u(data), "s") == {"a": 1}


# ---- a conversation's key and its messages (form 3) ------------------------------------------------------------------

def test_a_conversation_key_is_sealed_to_the_post_quantum_key_bound_to_its_list():
    seed, pk = _hybrid()
    tk = e2e.new_talk_key()
    assert len(tk) == 32 and tk != e2e.new_talk_key()
    key_slot = e2e.talk_key_slot("acct", "lnk", "s1", 2, 5)
    assert key_slot == "proofpane-e2e/1|acct|lnk|talk-key|s1|2|5"
    sealed = e2e.seal_talk_key(pk, key_slot, tk)
    assert len(page.body_of(sealed)) == e2e.TALK_KEY_SEALED == 1197
    assert e2e.read(sealed, e2e.TALK_KEY, (2,)) == e2e.Shape(2, e2e.key_id(pk), 1197)
    assert page.open_talk_key(seed, sealed, key_slot) == tk
    for other in (e2e.talk_key_slot("acct", "lnk", "s1", 3, 5), e2e.talk_key_slot("acct", "lnk", "s1", 2, 4),
                  e2e.talk_key_slot("acct", "lnk", "s2", 2, 5), e2e.talk_slot("acct", "lnk", "s1", 0, 2, 5, "", "", "")):
        with pytest.raises(page.NotOpened):
            page.open_talk_key(seed, sealed, other)
    classical = X25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ValueError, match="to a post-quantum key alone"):
        e2e.seal_talk_key(classical, key_slot, tk)                      # a classical account's messages stay form 1
    for bad in (tk[:31], tk + b"\0", "x" * 32):
        with pytest.raises(ValueError):
            e2e.seal_talk_key(pk, key_slot, bad)


def test_a_conversation_key_s_id_names_and_commits_to_it():
    tk = os.urandom(32)
    expected = HKDF(algorithm=hashes.SHA256(), length=16, salt=None, info=b"proofpane-e2e/3 talk key id").derive(tk)
    assert e2e.talk_key_id(tk) == expected.hex() == page.talk_key_id(tk)
    assert e2e.talk_key_id(tk) != hashlib.sha256(tk).digest()[:16].hex()
    with pytest.raises(ValueError):
        e2e.talk_key_id(tk[:16])


def test_a_conversation_s_messages_are_form_3_each_under_a_key_of_its_own():
    tk = e2e.new_talk_key()
    first = e2e.talk_slot("acct", "lnk", "s1", 0, 2, 5, "owner", 1790000000, 1790000000000)
    second = e2e.talk_slot("acct", "lnk", "s1", 1, 2, 5, "session", None, None)
    value = {"text": "how is the build?"}
    m0 = e2e.seal_talk_message(tk, first, value)
    data = page.body_of(m0)
    assert (data[0], data[1:17].hex()) == (3, e2e.talk_key_id(tk))
    assert len(data) == e2e.OVERHEADS[3] + len(e2e.plaintext(value)) == 45 + len(e2e.plaintext(value))
    assert e2e.read(m0, e2e.TALK_PLAIN, (3,)) == e2e.Shape(3, e2e.talk_key_id(tk), len(data))
    assert page.open_talk_message(tk, m0, first) == value
    with pytest.raises(page.NotOpened):
        page.open_talk_message(tk, m0, second)                          # moved to another place in the list
    with pytest.raises(page.NotOpened):
        page.open_talk_message(e2e.new_talk_key(), m0, first)          # another conversation's key
    with pytest.raises(InvalidTag):
        AESGCM(tk).decrypt(data[17:29], data[29:], data[:29] + first.encode("utf-8"))   # the key is not tk itself
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
             info=b"proofpane-e2e/3 talk message|" + first.encode("utf-8")).derive(tk)
    assert json.loads(AESGCM(k).decrypt(data[17:29], data[29:], data[:29] + first.encode("utf-8"))) == value
    assert e2e.seal_talk_message(tk, first, value) != m0                # a fresh nonce
    with pytest.raises(ValueError):
        e2e.seal_talk_message(tk[:31], first, value)
    with pytest.raises(ValueError):
        e2e.seal_talk_message(tk, first, value, nonce=os.urandom(13))


def test_a_post_quantum_conversation_is_never_sealed_one_message_at_a_time():
    """seal() would make form-2 messages with no key beside them, which the page refuses; so it
    refuses a talk or talk-key slot under a post-quantum key, and seal_talk seals the conversation whole."""
    _, pk = _hybrid()
    for slot in (e2e.talk_slot("acct", "lnk", "s1", 0, 1, 0, "owner", None, None),
                 e2e.talk_key_slot("acct", "lnk", "s1", 1, 0)):
        with pytest.raises(ValueError, match="sealed whole"):
            e2e.seal(pk, slot, {"text": "x"})
    classical = X25519PrivateKey.generate().public_key().public_bytes_raw()
    one = e2e.seal(classical, e2e.talk_slot("acct", "lnk", "s1", 0, 1, 0, "owner", None, None), {"text": "x"})
    assert e2e.read(one, e2e.TALK_PLAIN, (1,)).form == 1                # a classical account's conversation, as in 57
    for slot in (e2e.event_slot("acct", "lnk", "talk", None, None, "c" * 32), e2e.history_slot("acct", "lnk", "talk", 0)):
        assert e2e.read(e2e.seal(pk, slot, {"text": "x"}), 100, (2,)).form == 2   # "talk" elsewhere in a slot is no kind


def test_seal_talk_seals_a_conversation_whole_as_the_server_would_in_place():
    seed, pk = _hybrid()
    messages = [{"who": "owner", "text": "how is the build?", "at": 1790000000, "ms": 1790000000000},
                {"who": "session", "text": "Green: 12 passed.", "at": None}]
    key, key_id, sealed = e2e.seal_talk(pk, "acct", "lnk", "s1", 3, messages)
    assert e2e.read_talk_key(key) == e2e.Shape(2, e2e.key_id(pk), 1197)
    tk = page.open_talk_key(seed, key, e2e.talk_key_slot("acct", "lnk", "s1", 2, 3))
    assert page.talk_key_id(tk) == key_id
    for i, (m, s) in enumerate(zip(messages, sealed)):
        assert {k: v for k, v in s.items() if k != "text"} == {k: v for k, v in m.items() if k != "text"}
        slot = e2e.talk_slot("acct", "lnk", "s1", i, 2, 3, m["who"], m.get("at"), m.get("ms"))
        assert page.open_talk_message(tk, s["text"], slot) == {"text": m["text"]}
    assert messages[0]["text"] == "how is the build?"                   # the caller's list is not changed
    assert e2e.seal_talk(pk, "acct", "lnk", "s1", 3, messages)[1] != key_id   # a fresh key for each list
    with pytest.raises(ValueError, match="never a mix"):
        e2e.seal_talk(pk, "acct", "lnk", "s1", 3, [messages[0], dict(messages[1], text=sealed[1]["text"])])
    classical = X25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ValueError, match="sealed whole to a post-quantum key alone"):
        e2e.seal_talk(classical, "acct", "lnk", "s1", 3, messages)
    empty_key, _, none = e2e.seal_talk(pk, "acct", "lnk", "s1", 0, [])
    assert none == [] and e2e.read_talk_key(empty_key).length == 1197


def test_a_conversation_key_s_envelope_is_read_at_exactly_its_length():
    seed, pk = _hybrid()
    slot = e2e.talk_key_slot("acct", "lnk", "s1", 1, 0)
    assert e2e.read_talk_key(e2e.seal_talk_key(pk, slot, bytes(32))).length == 1197
    for n in (0, 31, 33):
        other = e2e._seal_hybrid(pk, slot, bytes(n))                    # form 2, but not 32 bytes inside
        with pytest.raises(e2e.NotAnEnvelope, match="exactly 1197 bytes|holds at most 32 bytes"):
            e2e.read_talk_key(other)
    with pytest.raises(e2e.NotAnEnvelope):
        e2e.read_talk_key(e2e.seal_talk_message(bytes(32), "m", {"text": "x"}))     # form 3


# ---- what the paths read --------------------------------------------------------------------------------------------

def test_read_takes_the_forms_a_path_names_each_with_its_overhead():
    for f, overhead in ((1, 77), (2, 1165), (3, 45)):
        least = "e2e1." + e2e.b64u(bytes([f]) + bytes(overhead - 1))
        assert e2e.read(least, 0, e2e.FORMS) == e2e.Shape(f, "00" * 16, overhead)   # nothing inside is the least
        ten = "e2e1." + e2e.b64u(bytes([f]) + bytes(overhead - 1 + 10))
        assert e2e.read(ten, 10, (f,)).length == overhead + 10
        with pytest.raises(e2e.NotAnEnvelope, match="holds at most 9 bytes"):
            e2e.read(ten, 9, e2e.FORMS)
        with pytest.raises(e2e.NotAnEnvelope, match="holds at most"):
            e2e.read("e2e1." + e2e.b64u(bytes([f]) + bytes(overhead - 2)), 100, e2e.FORMS)   # short of a header and tag
        with pytest.raises(e2e.NotAnEnvelope, match=r"is of form (\d, )*\d or \d\.$|is of form \d\.$"):
            e2e.read(ten, 10, tuple(g for g in e2e.FORMS if g != f))
    for other in (0, 4, 255):
        with pytest.raises(e2e.NotAnEnvelope, match=r"^An encrypted text here is of form 1, 2 or 3\.$"):
            e2e.read("e2e1." + e2e.b64u(bytes([other]) + bytes(1200)), 100, e2e.FORMS)
    _, pk = _hybrid()
    two = e2e.seal(pk, "s", {"a": 1})
    with pytest.raises(e2e.NotAnEnvelope, match=r"^An encrypted text here is of form 1\.$"):
        e2e.read(two, 100, (1,))
    with pytest.raises(e2e.NotAnEnvelope, match=r"^An encrypted text here is of form 1 or 3\.$"):
        e2e.read(two, 100, (1, 3))
    one_mod_four = two + "A" * ((1 - len(two[len(e2e.PREFIX):])) % 4 or 4)
    assert len(one_mod_four[len(e2e.PREFIX):]) % 4 == 1
    for written in (two + "=", two[:-1] + "!", one_mod_four):          # padding, a character outside, 1 modulo 4
        with pytest.raises(e2e.NotAnEnvelope):
            e2e.read(written, 100, e2e.FORMS)
    with pytest.raises(TypeError):
        e2e.read(two, 100)                                              # a path names the forms it takes


def test_the_older_reading_still_takes_form_1_alone():
    _, pk = _hybrid()
    tk = e2e.new_talk_key()
    for envelope in (e2e.seal(pk, "s", {"a": 1}), e2e.seal_talk_message(tk, "s", {"a": 1})):
        with pytest.raises(e2e.NotAnEnvelope):
            e2e.form(envelope, e2e.EVENT_PLAIN)
    classical = X25519PrivateKey.generate().public_key().public_bytes_raw()
    one = e2e.seal(classical, "s", {"a": 1})
    assert e2e.form(one, 100) == tuple(e2e.read(one, 100, (1,))[1:])


def test_the_connector_gives_out_no_envelope_of_any_form():
    _, pk = _hybrid()
    tk = e2e.new_talk_key()
    for envelope in (e2e.seal(pk, "s", {"text": "the plan"}), e2e.seal_talk_key(pk, "k", tk),
                     e2e.seal_talk_message(tk, "m", {"text": "the plan"})):
        assert connector.plain(envelope) is None and connector.readable(envelope) == (None, True)
    four = "e2e1." + e2e.b64u(bytes([4]) + bytes(1200))                # no form: text, as an older release gave it
    assert connector.plain(four) == four


# ---- across languages -----------------------------------------------------------------------------------------------

def _b(text):
    return page.unb64u(text)


def _again(public_raw, recipient_seed, envelope, slot, plain, ephemeral_private, nonce):
    """A form-2 envelope sealed again by the server's own code with the page's randomness: the X25519 ephemeral key and
    the nonce as given, and ML-KEM's half (pyca has no derandomized encapsulation) handed in: the envelope's ct_M and the
    secret the recipient's key decapsulates from it."""
    ct_m = page.body_of(envelope)[17:1105]
    ss_m = page.expand(recipient_seed)[0].decapsulate(ct_m)
    return e2e._seal_hybrid(public_raw, slot, plain, X25519PrivateKey.from_private_bytes(ephemeral_private), nonce,
                            lambda _key: (ss_m, ct_m))


def _code_text(code):
    text = base64.b32encode(code).decode("ascii")
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def _proof(ss, nonce, purpose):
    return e2e.b64u(hmac.new(page.hkdf(ss, "proofpane-e2e/2 recovery proof"), nonce + purpose.encode("utf-8"),
                             hashlib.sha256).digest())


def test_the_page_s_post_quantum_vectors_open_here_and_the_server_seals_them_again():
    v = _json("e2e_v2_page.json")
    account, link_id = v["account"], v["link_id"]
    seed, pk = _b(v["content_key"]["seed"]), _b(v["content_key"]["public"])
    assert page.public_of(seed) == pk and e2e.key_id(pk) == v["content_key"]["key_id"]
    for case in v["envelopes"]:
        plain = e2e.plaintext(case["plaintext"])
        assert plain.decode("utf-8") == case["plaintext_text"], case["name"]
        assert page.open_envelope2(seed, case["envelope"], case["slot"]) == case["plaintext"], case["name"]
        assert e2e.read(case["envelope"], e2e.EVENT_PLAIN, (2,)) == e2e.Shape(
            2, v["content_key"]["key_id"], e2e.OVERHEADS[2] + len(plain)), case["name"]
        assert _again(pk, seed, case["envelope"], case["slot"], plain, _b(case["eseed"])[32:],
                      _b(case["nonce"])) == case["envelope"], case["name"]

    t = v["talk"]
    tk = _b(t["tk"])
    assert t["key_slot"] == e2e.talk_key_slot(account, link_id, t["session"], t["count"], t["earlier"])
    assert page.open_talk_key(seed, t["key_envelope"], t["key_slot"]) == tk and e2e.talk_key_id(tk) == t["key_id"]
    ct_m = page.body_of(t["key_envelope"])[17:1105]
    ss_m = page.expand(seed)[0].decapsulate(ct_m)
    assert e2e.seal_talk_key(pk, t["key_slot"], tk, X25519PrivateKey.from_private_bytes(_b(t["key_eseed"])[32:]),
                             _b(t["key_nonce"]), lambda _key: (ss_m, ct_m)) == t["key_envelope"]
    assert len(t["messages"]) == t["count"]
    for m in t["messages"]:
        assert page.open_talk_message(tk, m["envelope"], m["slot"]) == m["plaintext"]
        assert e2e.seal_talk_message(tk, m["slot"], m["plaintext"], _b(m["nonce"])) == m["envelope"]   # nothing random
        assert e2e.read(m["envelope"], e2e.TALK_PLAIN, (3,)).form == 3

    for g in (v["grant"], v["recovery_grant"]):
        recipient_seed, recipient, mk = _b(g["seed"]), _b(g["public"]), _b(g["mk"])
        assert page.public_of(recipient_seed) == recipient
        assert g["aad"] == f"proofpane-e2e/1|{account}|grant|{g['to']}|{g['mk_id']}"
        assert page.hkdf(mk, f"proofpane-e2e/1 mk_id|{account}", 16).hex() == g["mk_id"]
        assert page.open_bytes2(recipient_seed, g["envelope"], g["aad"]) == mk
        eseed, nonce = page.grant_eseed(mk, recipient, g["aad"])            # written here from the design's words
        data = page.body_of(g["envelope"])
        assert data[1105:1137] == page.x25519_public(eseed[32:]) and data[1137:1149] == nonce
        assert _again(recipient, recipient_seed, g["envelope"], g["aad"], mk, eseed[32:], nonce) == g["envelope"]

    w = v["content_key_wrap"]
    subkey = page.hkdf(_b(w["mk"]), f"proofpane-e2e/1 content-wrap|{account}")
    assert e2e.b64u(subkey) == w["subkey"]
    assert w["aad"] == f"proofpane-e2e/2 content key|{account}|{w['key_id']}|{w['mk_id']}"
    blob = _b(w["nonce_and_wrapped"])
    assert len(blob) == 60 and AESGCM(subkey).decrypt(blob[:12], blob[12:], w["aad"].encode("utf-8")) == _b(w["seed"])
    assert e2e.key_id(page.public_of(_b(w["seed"]))) == w["key_id"]

    c = v["classical_key_wrap"]
    assert c["subkey"] == w["subkey"]
    assert c["aad"] == f"proofpane-e2e/2 content key x25519|{account}|{c['key_id']}|{c['mk_id']}"
    blob = _b(c["nonce_and_wrapped"])
    assert len(blob) == 60 and AESGCM(subkey).decrypt(blob[:12], blob[12:], c["aad"].encode("utf-8")) == _b(c["scalar"])
    assert page.x25519_public(_b(c["scalar"])) == _b(c["public"]) and e2e.key_id(_b(c["public"])) == c["key_id"]

    cw = v["credential_wrap"]
    assert cw["credential_info"] == f"proofpane-e2e/1 credential|{account}|{cw['credential_id']}"
    assert cw["aad"] == f"proofpane-e2e/2 credential|{account}|{cw['credential_id']}|{cw['anchor']}"
    blob = _b(cw["nonce_and_wrapped"])
    kek = page.hkdf(_b(cw["prf"]), cw["credential_info"])
    assert len(blob) == 60 and AESGCM(kek).decrypt(blob[:12], blob[12:], cw["aad"].encode("utf-8")) == _b(cw["seed"])
    assert page.public_of(_b(cw["seed"])) == _b(cw["public"])
    assert cw["public"] == v["grant"]["public"]

    r = v["recovery"]
    code = _b(r["code_bytes"])
    assert len(code) == 20 and _code_text(code) == r["code"]
    assert page.hkdf(code, f"proofpane-e2e/2 recovery|{account}") == _b(r["seed"])
    assert page.public_of(_b(r["seed"])) == _b(r["public"]) == _b(v["recovery_grant"]["public"])
    p = r["proof"]
    assert _b(p["server_ct"])[1088:] == page.x25519_public(_b(p["server_eseed"])[32:])
    assert _proof(page.decapsulate(_b(r["seed"]), _b(p["server_ct"])), _b(p["nonce"]), p["purpose"]) == p["proof"]


def server_vectors():
    """The server's own vectors: sealed by the server's code at random (pyca has no derandomized encapsulation), to a
    content key and a recovery key made from fixed seeds, for the page to open."""
    account, link_id = "acct-e2e-server", "0123456789abcdef0123456789abcdef"
    seed = hashlib.sha256(b"proofpane-e2e/2 server vectors: content key").digest()
    pk = page.public_of(seed)
    cases = [
        ("event: a step to the owner", e2e.event_slot(account, link_id, "step", None, None, "0" * 28 + "e101"),
         {"said_at": None, "session": None, "text": "Sealed by the server."}),
        ("event: a mirrored note, in other scripts and with what JSON escapes",
         e2e.event_slot(account, link_id, "note", "owner", None, "0" * 28 + "e102"),
         {"said_at": 1790000000789, "session": "s-0007",
          "text": "caf" + chr(0xE9) + " " + chr(0x3BB) + " " + chr(0x1F600) + ' "q" \\ tab\there\nnext' + chr(1) + " end"}),
        ("history: a listed row", e2e.history_slot(account, link_id, "s-0007", False),
         {"title": "Sealing in place", "project": "~/work/app", "branch": "main", "repositories": ["~/work/app"]}),
        ("history: a withheld row, its project alone", e2e.history_slot(account, link_id, "s-0008", True),
         {"project": "~/private"}),
    ]
    envelopes = [{"name": name, "slot": slot, "plaintext": value, "plaintext_text": e2e.plaintext(value).decode("utf-8"),
                  "envelope": e2e.seal(pk, slot, value)} for name, slot, value in cases]
    tk = e2e.new_talk_key()
    session, count, earlier = "s-0007", 2, 3
    key_slot = e2e.talk_key_slot(account, link_id, session, count, earlier)
    said = [(e2e.talk_slot(account, link_id, session, 0, count, earlier, "owner", 1790000000, 1790000000000),
             {"text": "how is the build?"}),
            (e2e.talk_slot(account, link_id, session, 1, count, earlier, "session", None, None),
             {"text": "Green: 12 passed."})]
    talk = {"session": session, "count": count, "earlier": earlier, "tk": e2e.b64u(tk), "key_id": e2e.talk_key_id(tk),
            "key_slot": key_slot, "key_envelope": e2e.seal_talk_key(pk, key_slot, tk),
            "messages": [{"slot": slot, "plaintext": value, "plaintext_text": e2e.plaintext(value).decode("utf-8"),
                          "envelope": e2e.seal_talk_message(tk, slot, value)} for slot, value in said]}
    code = hashlib.sha256(b"proofpane-e2e/2 server vectors: recovery code").digest()[:20]
    rk_seed = page.hkdf(code, f"proofpane-e2e/2 recovery|{account}")
    rk_public = page.public_of(rk_seed)
    server_ct, ss = e2e.encapsulate(rk_public)                         # the server's part of the proof
    nonce = os.urandom(32)
    return {
        "about": "End-to-end encryption, post-quantum: what the server's code (ade/e2e.py, pyca's "
                 "ML-KEM-768 and X25519, hashlib's SHA3-256) seals at random, for the page to open (ui/src/"
                 "e2eVectors.test.ts); made by tests/test_ade_e2e_envelope_pq.py with PP_E2E_VECTORS_WRITE=1. Bytes are "
                 "base64url without padding; the content key and the recovery key are made from their seeds as X-Wing "
                 "makes keys. Envelopes are form 2, the conversation's key a form-2 envelope of tk bound to key_slot, "
                 "its messages form 3. The recovery proof answers the server's encapsulation to the recovery key "
                 "(server_ct): HMAC-SHA256 under HKDF-SHA256(ss, salt empty, info \"proofpane-e2e/2 recovery proof\") "
                 "over nonce || purpose.",
        "version": 2, "account": account, "link_id": link_id,
        "content_key": {"seed": e2e.b64u(seed), "public": e2e.b64u(pk), "key_id": e2e.key_id(pk)},
        "envelopes": envelopes, "talk": talk,
        "recovery": {"code_bytes": e2e.b64u(code), "seed": e2e.b64u(rk_seed), "public": e2e.b64u(rk_public),
                     "proof": {"server_ct": e2e.b64u(server_ct), "nonce": e2e.b64u(nonce), "purpose": "e2e-reseal",
                               "proof": _proof(ss, nonce, "e2e-reseal")}},
    }


def _check_server_vectors(v):
    seed, pk = _b(v["content_key"]["seed"]), _b(v["content_key"]["public"])
    assert page.public_of(seed) == pk and e2e.key_id(pk) == v["content_key"]["key_id"]
    assert len(v["envelopes"]) == 4
    for case in v["envelopes"]:
        assert e2e.read(case["envelope"], e2e.EVENT_PLAIN, (2,)).form == 2, case["name"]
        assert page.open_envelope2(seed, case["envelope"], case["slot"]) == case["plaintext"], case["name"]
        assert e2e.plaintext(case["plaintext"]).decode("utf-8") == case["plaintext_text"], case["name"]
    t = v["talk"]
    assert t["key_slot"] == e2e.talk_key_slot(v["account"], v["link_id"], t["session"], t["count"], t["earlier"])
    tk = page.open_talk_key(seed, t["key_envelope"], t["key_slot"])
    assert e2e.b64u(tk) == t["tk"] and page.talk_key_id(tk) == t["key_id"]
    assert len(t["messages"]) == t["count"]
    for m in t["messages"]:
        assert page.open_talk_message(tk, m["envelope"], m["slot"]) == m["plaintext"]
    r = v["recovery"]
    assert page.hkdf(_b(r["code_bytes"]), f"proofpane-e2e/2 recovery|{v['account']}") == _b(r["seed"])
    assert page.public_of(_b(r["seed"])) == _b(r["public"])
    p = r["proof"]
    assert _proof(page.decapsulate(_b(r["seed"]), _b(p["server_ct"])), _b(p["nonce"]), p["purpose"]) == p["proof"]


def test_the_server_s_own_post_quantum_vectors_open_on_the_page_s_side():
    """The committed file, which the page opens (e2eVectors.test.ts), opens here too; and a set the server's code makes
    now opens the same way, so a change to its sealing that the page could not open fails here."""
    path = os.path.join(VECTORS, "e2e_v2.json")
    if os.environ.get("PP_E2E_VECTORS_WRITE"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(server_vectors(), fh, indent=1, ensure_ascii=True)
            fh.write("\n")
    _check_server_vectors(_json("e2e_v2.json"))
    _check_server_vectors(server_vectors())
