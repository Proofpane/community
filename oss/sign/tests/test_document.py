"""Signed documents: canonical bytes, a purpose bound into the signature, a closed signature format, key files."""

import copy
import json
import os
import stat
import tempfile
import unittest

import _paths  # noqa: F401  (puts the packages on sys.path)

from proofpane_sign import composite
from proofpane_sign.document import (key_id, read_private_key, read_public_key, sign_document, verify_document,
                                     write_private_key, write_public_key)

DOC = {"format": "proofpane.receipt/0", "run": "r-1", "decision": "pass", "obligations": [{"id": "o1", "ok": True}]}


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.sk = composite.generate()
        self.pk = composite.public_key(self.sk)
        self.sig = sign_document(DOC, self.sk, "receipt")

    def test_a_signature_verifies_for_its_document_purpose_and_key(self):
        self.assertTrue(verify_document(DOC, self.sig, self.pk, "receipt"))
        self.assertEqual(set(self.sig), {"alg", "purpose", "key", "sig"})
        self.assertEqual(self.sig["key"], key_id(self.pk))

    def test_the_document_is_signed_as_its_canonical_form(self):
        # the same value written another way is the same document
        reordered = {k: DOC[k] for k in reversed(list(DOC))}
        self.assertNotEqual(list(reordered), list(DOC))
        self.assertTrue(verify_document(reordered, self.sig, self.pk, "receipt"))

    def test_any_change_to_the_document_is_invalid(self):
        for change in (lambda d: d.update(decision="fail"), lambda d: d["obligations"][0].update(ok=False),
                       lambda d: d.update(extra=None), lambda d: d.pop("run")):
            doc = copy.deepcopy(DOC)
            change(doc)
            self.assertFalse(verify_document(doc, self.sig, self.pk, "receipt"))

    def test_another_purpose_is_invalid_even_if_the_label_says_so(self):
        self.assertFalse(verify_document(DOC, self.sig, self.pk, "manifest"))
        relabelled = dict(self.sig, purpose="manifest")
        self.assertFalse(verify_document(DOC, relabelled, self.pk, "manifest"))

    def test_another_key_is_invalid(self):
        other = composite.public_key(composite.generate())
        self.assertFalse(verify_document(DOC, self.sig, other, "receipt"))
        self.assertFalse(verify_document(DOC, dict(self.sig, key=key_id(other)), other, "receipt"))

    def test_the_signature_format_is_closed(self):
        broken = [dict(self.sig, alg="ed25519"), dict(self.sig, extra=1), {k: v for k, v in self.sig.items() if k != "key"},
                  dict(self.sig, sig=self.sig["sig"] + "="), dict(self.sig, sig="not base64!"), dict(self.sig, sig=5),
                  dict(self.sig, sig=self.sig["sig"][:-8]), [self.sig], None]
        for bad in broken:
            self.assertFalse(verify_document(DOC, bad, self.pk, "receipt"), repr(bad)[:60])

    def test_a_value_with_no_canonical_form_is_invalid_to_verify_and_refused_to_sign(self):
        self.assertFalse(verify_document({"x": float("nan")}, self.sig, self.pk, "receipt"))
        with self.assertRaises(ValueError):
            sign_document({"x": float("nan")}, self.sk, "receipt")

    def test_a_malformed_purpose_is_refused(self):
        for bad in ("", "Receipt", "a/b", "x" * 64, "-a", 5):
            with self.assertRaises(ValueError):
                sign_document(DOC, self.sk, bad)
            with self.assertRaises(ValueError):
                verify_document(DOC, self.sig, self.pk, bad)

    def test_the_purpose_is_the_composite_context(self):
        import base64
        raw = base64.b64decode(self.sig["sig"])
        from proofpane_core.canonical import canonicalize
        self.assertTrue(composite.verify(self.pk, canonicalize(DOC), raw, b"proofpane/receipt"))
        self.assertFalse(composite.verify(self.pk, canonicalize(DOC), raw, b""))


class TheSignedBytes(unittest.TestCase):
    def test_they_are_the_rfc8785_form_not_a_lookalike(self):
        # 1.0 and a non-ASCII string are where RFC 8785 and json.dumps(sort_keys=True) part ways
        value = {"n": 1.0, "s": "caf\u00e9", "z": [2.50, {"b": 1, "a": 0}]}
        sk = composite.generate()
        pk = composite.public_key(sk)
        sig = sign_document(value, sk, "receipt")
        import base64
        from proofpane_core.canonical import canonicalize
        raw = base64.b64decode(sig["sig"])
        self.assertEqual(canonicalize(value), '{"n":1,"s":"caf\u00e9","z":[2.5,{"a":0,"b":1}]}'.encode("utf-8"))
        self.assertTrue(composite.verify(pk, canonicalize(value), raw, b"proofpane/receipt"))
        lookalike = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.assertNotEqual(lookalike, canonicalize(value))
        self.assertFalse(composite.verify(pk, lookalike, raw, b"proofpane/receipt"))


class KeyFiles(unittest.TestCase):
    def test_keys_round_trip_and_the_private_file_is_owner_only(self):
        sk = composite.generate()
        pk = composite.public_key(sk)
        with tempfile.TemporaryDirectory() as tmp:
            priv, pub = os.path.join(tmp, "k.key"), os.path.join(tmp, "k.pub")
            write_private_key(priv, sk)
            write_public_key(pub, pk)
            self.assertEqual(read_private_key(priv), sk)
            self.assertEqual(read_public_key(pub), pk)
            self.assertEqual(stat.S_IMODE(os.stat(priv).st_mode), 0o600)

    def test_an_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            priv = os.path.join(tmp, "k.key")
            write_private_key(priv, composite.generate())
            with open(priv, encoding="utf-8") as fh:
                before = fh.read()
            with self.assertRaises(FileExistsError):
                write_private_key(priv, composite.generate())
            with open(priv, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)

    def test_a_public_key_file_whose_id_does_not_match_is_refused(self):
        pk = composite.public_key(composite.generate())
        with tempfile.TemporaryDirectory() as tmp:
            pub = os.path.join(tmp, "k.pub")
            write_public_key(pub, pk)
            with open(pub, encoding="utf-8") as fh:
                data = json.load(fh)
            data["key"] = key_id(composite.public_key(composite.generate()))
            os.chmod(pub, 0o600)
            with open(pub, "w") as fh:
                json.dump(data, fh)
            with self.assertRaises(ValueError):
                read_public_key(pub)

    def test_a_file_of_the_other_kind_or_shape_is_refused(self):
        sk = composite.generate()
        with tempfile.TemporaryDirectory() as tmp:
            priv = os.path.join(tmp, "k.key")
            write_private_key(priv, sk)
            with self.assertRaises(ValueError):
                read_public_key(priv)
            odd = os.path.join(tmp, "odd.key")
            with open(odd, "w") as fh:
                json.dump({"alg": composite.ALGORITHM, "private_key": "AAAA"}, fh)
            with self.assertRaises(ValueError):
                read_private_key(odd)


if __name__ == "__main__":
    unittest.main()
