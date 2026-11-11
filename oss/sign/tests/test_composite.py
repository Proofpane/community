"""Composite ML-DSA-65 + Ed25519, checked first against the draft's own test vector, then against the ways a
composite signature is known to go wrong: a component accepted alone, a component lifted out and used as a plain
signature, a context that does not bind."""

import base64
import hashlib
import json
import os
import unittest

import _paths  # noqa: F401  (puts the packages on sys.path)
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

from proofpane_sign import composite

VECTOR = os.path.join(_paths.VECTORS, "composite", "id-MLDSA65-Ed25519-SHA512.json")


def _vector():
    with open(VECTOR, encoding="utf-8") as fh:
        v = json.load(fh)
    return {k: (base64.b64decode(x) if k in ("m", "ctx", "pk", "sk", "s", "sWithContext") else x) for k, x in v.items()}


class TheDraftVector(unittest.TestCase):
    """Made by the draft's reference implementation, not by this package."""

    def setUp(self):
        self.v = _vector()

    def test_the_vector_is_the_one_its_provenance_names(self):
        self.assertEqual(self.v["tcId"], composite.ALGORITHM)
        self.assertEqual(self.v["m"], b"The quick brown fox jumps over the lazy dog.")
        self.assertEqual(len(self.v["pk"]), composite.PUBLIC_KEY_BYTES)
        self.assertEqual(len(self.v["sk"]), composite.PRIVATE_KEY_BYTES)
        self.assertEqual(len(self.v["s"]), composite.SIGNATURE_BYTES)

    def test_its_signatures_verify(self):
        v = self.v
        self.assertTrue(composite.verify(v["pk"], v["m"], v["s"]))
        self.assertTrue(composite.verify(v["pk"], v["m"], v["sWithContext"], v["ctx"]))

    def test_each_verifies_only_under_its_own_context(self):
        v = self.v
        self.assertFalse(composite.verify(v["pk"], v["m"], v["s"], v["ctx"]))
        self.assertFalse(composite.verify(v["pk"], v["m"], v["sWithContext"]))
        self.assertFalse(composite.verify(v["pk"], v["m"], v["sWithContext"], v["ctx"][:-1]))

    def test_its_private_key_gives_its_public_key_and_signs_for_it(self):
        v = self.v
        self.assertEqual(composite.public_key(v["sk"]), v["pk"])
        s = composite.sign(v["sk"], v["m"], v["ctx"])
        self.assertTrue(composite.verify(v["pk"], v["m"], s, v["ctx"]))

    def test_the_constants_are_the_drafts(self):
        self.assertEqual(composite.PREFIX.hex().upper(),
                         "436F6D706F73697465416C676F726974686D5369676E61747572657332303235")
        self.assertEqual(composite.LABEL, b"COMPSIG-MLDSA65-Ed25519-SHA512")
        self.assertEqual((composite.PUBLIC_KEY_BYTES, composite.PRIVATE_KEY_BYTES, composite.SIGNATURE_BYTES),
                         (1984, 64, 3373))


class BothComponentsAreRequired(unittest.TestCase):
    def setUp(self):
        self.sk = composite.generate()
        self.pk = composite.public_key(self.sk)
        self.msg = b"a manifest's canonical bytes"
        self.sig = composite.sign(self.sk, self.msg, b"proofpane/test")

    def test_a_fresh_signature_verifies(self):
        self.assertTrue(composite.verify(self.pk, self.msg, self.sig, b"proofpane/test"))

    def test_a_flipped_byte_in_either_component_is_invalid(self):
        for at in (0, composite.MLDSA_SIGNATURE - 1, composite.MLDSA_SIGNATURE, composite.SIGNATURE_BYTES - 1):
            bad = bytearray(self.sig)
            bad[at] ^= 0x01
            self.assertFalse(composite.verify(self.pk, self.msg, bytes(bad), b"proofpane/test"), at)

    def test_one_valid_component_with_the_other_from_another_message_is_invalid(self):
        other = composite.sign(self.sk, b"another message", b"proofpane/test")
        cut = composite.MLDSA_SIGNATURE
        for mixed in (self.sig[:cut] + other[cut:], other[:cut] + self.sig[cut:]):
            self.assertFalse(composite.verify(self.pk, self.msg, mixed, b"proofpane/test"))

    def test_a_key_with_one_foreign_component_is_invalid(self):
        stranger = composite.public_key(composite.generate())
        cut = composite.MLDSA_PUBLIC
        for mixed in (self.pk[:cut] + stranger[cut:], stranger[:cut] + self.pk[cut:]):
            self.assertFalse(composite.verify(mixed, self.msg, self.sig, b"proofpane/test"))

    def test_the_components_in_the_other_order_are_invalid(self):
        cut = composite.MLDSA_SIGNATURE
        swapped = self.sig[cut:] + self.sig[:cut]
        self.assertFalse(composite.verify(self.pk, self.msg, swapped, b"proofpane/test"))


class NoComponentWorksAlone(unittest.TestCase):
    """The stripping attack: take one component out of a composite signature and present it as a plain signature
    on the message. Both components sign M', not the message, so neither verifies as a plain signature."""

    def test_neither_component_is_a_plain_signature_on_the_message(self):
        sk = composite.generate()
        pk = composite.public_key(sk)
        msg = b"pay 10 to Mallory"
        sig = composite.sign(sk, msg)
        ml = mldsa.MLDSA65PublicKey.from_public_bytes(pk[:composite.MLDSA_PUBLIC])
        ed = ed25519.Ed25519PublicKey.from_public_bytes(pk[composite.MLDSA_PUBLIC:])
        from cryptography.exceptions import InvalidSignature
        with self.assertRaises(InvalidSignature):
            ed.verify(sig[composite.MLDSA_SIGNATURE:], msg)
        with self.assertRaises(InvalidSignature):
            ml.verify(sig[:composite.MLDSA_SIGNATURE], msg)
        with self.assertRaises(InvalidSignature):
            ml.verify(sig[:composite.MLDSA_SIGNATURE], msg, composite.LABEL)
        # the positive control: they are valid over M', as the draft defines it
        m_prime = composite.message_representative(msg)
        self.assertEqual(m_prime, composite.PREFIX + composite.LABEL + b"\x00" + hashlib.sha512(msg).digest())
        ed.verify(sig[composite.MLDSA_SIGNATURE:], m_prime)
        ml.verify(sig[:composite.MLDSA_SIGNATURE], m_prime, composite.LABEL)


class Inputs(unittest.TestCase):
    def test_wrong_lengths_are_invalid_not_errors(self):
        sk = composite.generate()
        pk = composite.public_key(sk)
        sig = composite.sign(sk, b"m")
        self.assertFalse(composite.verify(pk[:-1], b"m", sig))
        self.assertFalse(composite.verify(pk + b"\x00", b"m", sig))
        self.assertFalse(composite.verify(pk, b"m", sig[:-1]))
        self.assertFalse(composite.verify(pk, b"m", sig + b"\x00"))
        self.assertFalse(composite.verify(b"", b"m", b""))

    def test_a_context_longer_than_255_bytes_raises(self):
        sk = composite.generate()
        composite.sign(sk, b"m", b"x" * 255)
        with self.assertRaises(ValueError):
            composite.sign(sk, b"m", b"x" * 256)
        with self.assertRaises(ValueError):
            composite.verify(composite.public_key(sk), b"m", b"\x00" * composite.SIGNATURE_BYTES, b"x" * 256)

    def test_text_and_integers_are_refused(self):
        sk = composite.generate()
        for bad in ("text", 5, None):
            with self.assertRaises(TypeError):
                composite.sign(sk, bad)
            with self.assertRaises(TypeError):
                composite.message_representative(b"m", bad)

    def test_a_private_key_of_the_wrong_length_raises(self):
        with self.assertRaises(ValueError):
            composite.sign(b"\x00" * 63, b"m")

    def test_generate_gives_different_keys(self):
        self.assertNotEqual(composite.generate(), composite.generate())


if __name__ == "__main__":
    unittest.main()
