"""End-to-end encryption's envelope as the server reads and makes it (ade/e2e.py): its form, all the server reads of
one; the slot it is bound to; its plaintext's canonical bytes; a key of low order refused; and, when the link client's
vectors are here (tests/vectors/e2e_v1.json, made by ade_link.py's tests), the server's sealing checked against them
byte for byte, its form rules against the envelopes they refuse, and the page's own vectors (e2e_v1_page.json) read.
The tests open envelopes with an opener of their own, the page's side, so a mismatch with the server's sealing shows."""

import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from proofpane_open.ade import e2e

VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors")


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public(private):
    return private.public_key().public_bytes_raw()


def open_envelope(private, envelope, slot):
    """The page's side: the plaintext object of an envelope sealed to `private`'s public key and bound to `slot`."""
    data = unb64u(envelope[len("e2e1."):])
    header, sealed_bytes = data[:61], data[61:]
    assert header[0] == 1 and header[1:17] == hashlib.sha256(raw_public(private)).digest()[:16]
    epk, nonce_bytes = header[17:49], header[49:61]
    shared = private.exchange(X25519PublicKey.from_public_bytes(epk))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + raw_public(private),
             info=b"proofpane-e2e/1 content").derive(shared)
    return json.loads(AESGCM(k).decrypt(nonce_bytes, sealed_bytes, header + slot.encode("utf-8")).decode("utf-8"))


def test_an_envelope_is_read_by_its_form_alone():
    ek = X25519PrivateKey.generate()
    good = e2e.seal(raw_public(ek), "slot", {"text": "hello"})
    assert e2e.form(good, 100) == (e2e.key_id(raw_public(ek)), len(unb64u(good[5:])))
    assert open_envelope(ek, good, "slot") == {"text": "hello"}
    body = good[5:]
    data = unb64u(body)
    wrong = {
        "a prefix of another version": "e2e2." + body,
        "a character base64url lacks": "e2e1." + body[:-2] + "+/",
        "padding": "e2e1." + body + "=",
        "a length of 1 modulo 4": "e2e1." + body + "A" * ((1 - len(body)) % 4),
        "too short": "e2e1." + b64u(bytes([1]) + bytes(75)),
        "another version byte": "e2e1." + b64u(bytes([2]) + data[1:]),
        "empty": "e2e1.",
        "not a text": 7,
    }
    # trailing bits not zero: another text for 79 bytes (not a whole number of 3-byte groups), which decodes the same
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    whole = b64u(bytes([1]) + bytes(78))
    wrong["trailing bits not zero"] = "e2e1." + whole[:-1] + alphabet[alphabet.index(whole[-1]) + 1]
    assert unb64u(wrong["trailing bits not zero"][5:]) == bytes([1]) + bytes(78)
    assert e2e.form("e2e1." + whole, 100)[1] == 79
    for why, value in wrong.items():
        with pytest.raises(e2e.NotAnEnvelope):
            e2e.form(value, 100)
            pytest.fail(why)
    with pytest.raises(e2e.NotAnEnvelope):
        e2e.form(good, len(data) - e2e.OVERHEAD - 1)                    # one byte over the path's most
    assert e2e.form(good, len(data) - e2e.OVERHEAD)[0]                  # exactly the most
    assert e2e.form("e2e1." + b64u(bytes([1]) + bytes(76)), 0)[1] == e2e.OVERHEAD      # nothing inside is the least


def test_the_slot_writes_empty_fields_empty_and_numbers_in_decimal():
    assert e2e.event_slot("acct", "lnk", "note", None, None, "c" * 32) == \
        "proofpane-e2e/1|acct|lnk|event|note|||" + "c" * 32
    assert e2e.event_slot("a", "l", "done", "owner", 7, "x") == "proofpane-e2e/1|a|l|event|done|owner|7|x"
    assert e2e.history_slot("a", "l", "s1", True) == "proofpane-e2e/1|a|l|history|s1|1"
    assert e2e.history_slot("a", "l", "s1", False) == "proofpane-e2e/1|a|l|history|s1|0"
    assert e2e.talk_slot("a", "l", "s1", 0, 3, 5, "owner", None, 1700000000123) == \
        "proofpane-e2e/1|a|l|talk|s1|0|3|5|owner||1700000000123"


def test_an_envelope_is_bound_to_its_slot_and_its_plaintext_is_canonical():
    ek = X25519PrivateKey.generate()
    value = {"z": 1, "a": "caf" + chr(0xE9), "said_at": None}
    envelope = e2e.seal(raw_public(ek), "one slot", value)
    data = unb64u(envelope[5:])
    header = data[:61]
    assert header[1:17] == bytes.fromhex(e2e.key_id(raw_public(ek)))
    shared = ek.exchange(X25519PublicKey.from_public_bytes(header[17:49]))
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=header[17:49] + raw_public(ek),
             info=b"proofpane-e2e/1 content").derive(shared)
    plain = AESGCM(k).decrypt(header[49:61], data[61:], header + b"one slot")
    assert plain == ('{"a":"caf' + chr(0xE9) + '","said_at":null,"z":1}').encode("utf-8")       # RFC 8785, UTF-8
    with pytest.raises(Exception):
        open_envelope(ek, envelope, "another slot")
    assert e2e.seal(raw_public(ek), "one slot", value) != envelope              # a fresh key and nonce each time


def test_a_key_of_low_order_is_no_key_to_seal_to():
    assert not e2e.usable_key(bytes(32))
    assert not e2e.usable_key(bytes([1]) + bytes(31))                   # the point of order 1
    assert not e2e.usable_key(bytes(31))
    assert e2e.usable_key(raw_public(X25519PrivateKey.generate()))
    with pytest.raises(ValueError):
        e2e.seal(bytes(32), "slot", {"text": "x"})


def _vectors(name):
    """The vectors the link client and the page wrote, which the server's sealing must agree with: a test that reads
    them fails, never passes over them, when the file is not here."""
    path = os.path.join(VECTORS, name)
    assert os.path.exists(path), f"tests/vectors/{name} is missing: the vectors the server is held to are not here"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# what the server's form check refuses among the envelopes the link client's vectors refuse; the rest (another slot, a
# tag or a key, a plaintext that is not canonical, UTF-8 or an object) only an opener can tell
FORM = ("header of another version", "padding", "1 modulo 4", "encode again", "mark")


def test_the_server_seals_as_the_link_client_s_vectors_say_and_reads_their_forms():
    vectors = _vectors("e2e_v1.json")
    public = unb64u(vectors["content_key"]["public"])
    assert e2e.key_id(public) == vectors["content_key"]["key_id"]
    for case in vectors["envelopes"]:
        ephemeral = X25519PrivateKey.from_private_bytes(unb64u(case["ephemeral_private"]))
        got = e2e.seal(public, case["slot"], case["plaintext"], ephemeral=ephemeral, nonce=unb64u(case["nonce"]))
        assert got == case["envelope"], case["name"]
        assert e2e.plaintext(case["plaintext"]).decode("utf-8") == case["plaintext_text"], case["name"]
        assert e2e.form(got, e2e.EVENT_PLAIN)[0] == vectors["content_key"]["key_id"]
    for case in vectors["refused"]:
        if any(words in case["why"] for words in FORM):
            with pytest.raises(e2e.NotAnEnvelope):
                e2e.form(case["envelope"], e2e.EVENT_PLAIN)
        else:
            e2e.form(case["envelope"], e2e.EVENT_PLAIN)                 # its form holds: the page refuses it


def test_the_page_s_own_vectors_have_the_envelope_s_form():
    vectors = _vectors("e2e_v1_page.json")
    for case in vectors["envelopes"]:
        assert e2e.form(case["envelope"], e2e.EVENT_PLAIN)[0] == vectors["content_key"]["key_id"]


def test_a_vector_file_that_is_not_here_fails_the_tests_that_read_it(tmp_path, monkeypatch):
    """A test passed over (skipped) when its vectors are missing would leave the server held to nothing, and say so
    only in a count of skips."""
    monkeypatch.setitem(globals(), "VECTORS", str(tmp_path))
    with pytest.raises(AssertionError, match="is missing"):
        try:
            _vectors("e2e_v1.json")
        except pytest.skip.Exception:
            pytest.fail("a missing vector file was passed over")
