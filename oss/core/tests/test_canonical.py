import json
import os
import struct
import time
import unittest

from _paths import VECTORS  # noqa: F401  (puts the package on sys.path)
from proofpane_core.canonical import (MAX_DEPTH, CanonicalizationError, canonicalize, digest,  # noqa: E402
                                      format_number, loads_strict, printable)


def _load(name):
    with open(os.path.join(VECTORS, "jcs", name), encoding="utf-8") as fh:
        return json.load(fh)


class AgainstIndependentReference(unittest.TestCase):
    """The expected values were written by tools/gen_jcs_vectors.mjs, a second implementation (Node's
    JSON.stringify and default sort), not by the code under test."""

    def test_numbers(self):
        vectors = _load("numbers.json")
        self.assertGreater(len(vectors), 2500)
        for v in vectors:
            x = struct.unpack(">d", bytes.fromhex(v["bits"]))[0]
            self.assertEqual(format_number(x), v["text"], v["bits"])

    def test_objects(self):
        vectors = _load("objects.json")
        self.assertGreater(len(vectors), 300)
        for v in vectors:
            self.assertEqual(canonicalize(loads_strict(v["input"])).decode("utf-8"), v["expected"], v["input"])


class RfcExamples(unittest.TestCase):
    def test_key_order_is_utf16(self):
        # RFC 8785 section 3.2.3: the emoji (surrogate pair starting 0xD83D) sorts before U+FB33
        got = canonicalize({"€": 1, "\r": 2, "דּ": 3, "1": 4, "\U0001F600": 5, "\u0080": 6, "ö": 7})
        keys = [k for k in json.loads(got)]
        self.assertEqual(keys, ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"])

    def test_mixed_example(self):
        text = ('{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],'
                '"string":"\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/","literals":[null,true,false]}')
        expected = ('{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
                    '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}')
        self.assertEqual(canonicalize(loads_strict(text)).decode("utf-8"), expected)


class Refusals(unittest.TestCase):
    def assertRefused(self, value):
        with self.assertRaises(CanonicalizationError):
            canonicalize(value)

    def test_non_finite(self):
        for x in (float("nan"), float("inf"), float("-inf")):
            self.assertRefused(x)

    def test_unsafe_integers(self):
        self.assertEqual(canonicalize(2 ** 53 - 1), b"9007199254740991")
        self.assertRefused(2 ** 53)
        self.assertRefused(-(2 ** 53))

    def test_lone_surrogate(self):
        self.assertRefused("\ud800")
        self.assertRefused({"\udc00": 1})

    def test_non_json_types(self):
        for value in ((1, 2), {1, 2}, b"x", object(), {1: "a"}):
            self.assertRefused(value)

    def test_strict_parse(self):
        for text in ('{"a":1,"a":2}', "[NaN]", "[Infinity]", '"\\ud800"', "12345678901234567890"):
            with self.assertRaises(CanonicalizationError, msg=text):
                loads_strict(text)


class Hostile(unittest.TestCase):
    def test_deep_nesting_is_refused_before_the_stack_runs_out(self):
        for text in ("[" * 1000 + "]" * 1000, '{"a":' * 1000 + "1" + "}" * 1000):
            with self.assertRaises(CanonicalizationError):
                loads_strict(text)
        with self.assertRaises(CanonicalizationError):
            canonicalize(json.loads("[" * 100 + "]" * 100))
        # brackets inside strings are not nesting, including after an escaped quote inside the same string
        self.assertEqual(loads_strict('["' + "[" * 200 + '\\"x"]'), ["[" * 200 + '"x'])
        self.assertEqual(loads_strict('["he said \\"' + "[" * 100 + '"]'), ['he said "' + "[" * 100])

    def test_the_work_is_linear_in_the_size_of_the_value(self):
        # one long key over a long list: building each child's path eagerly made this quadratic (0.5 s at 0.48 MB)
        text = json.dumps({"k" * 1000000: list(range(100000))})  # quadratic: about 10 s; linear: about 0.3 s
        start = time.perf_counter()
        loads_strict(text)
        self.assertLess(time.perf_counter() - start, 2.0)

    def test_subclasses_cannot_choose_their_own_text(self):
        class F(float):
            def __repr__(self):
                return "2"

        class I(int):
            def __repr__(self):
                return "7"
            __str__ = __repr__

        self.assertEqual(canonicalize([F(1.5), I(3)]), b"[1.5,3]")
        self.assertEqual(format_number(F(1.5)), "1.5")  # the public formatter too, not only through canonicalize

    def test_a_repeated_long_key_is_cut_in_the_message(self):
        key = "k" * 100000
        with self.assertRaises(CanonicalizationError) as cm:
            loads_strict('{"' + key + '":1,"' + key + '":2}')
        self.assertLess(len(str(cm.exception)), 200)

    def test_a_quoted_key_is_escaped_as_well_as_cut(self):
        key = json.dumps("\rOK\x1b[8m" + "k" * 1000)[1:-1]  # as the file spells it
        with self.assertRaises(CanonicalizationError) as cm:
            loads_strict('{"' + key + '":1,"' + key + '":2}')
        self.assertNotIn("\r", str(cm.exception))
        self.assertNotIn("\x1b", str(cm.exception))
        self.assertLess(len(str(cm.exception)), 200)

    def test_a_non_string_key_is_cut_in_the_message(self):
        with self.assertRaises(CanonicalizationError) as cm:
            canonicalize({("k" * 100000,): 1})
        self.assertLess(len(str(cm.exception)), 200)

    def test_a_value_too_large_to_print_is_described_not_raised_on(self):
        # repr of an integer past Python's digit limit raises ValueError; a message must never be what fails
        big = 10 ** 5000
        for value in (big, [big], {"k": big}):
            self.assertLess(len(printable(value)), 80)
        with self.assertRaises(CanonicalizationError) as cm:
            canonicalize({big: 1})
        self.assertLess(len(str(cm.exception)), 200)
        from proofpane_core import manifest
        self.assertEqual(len(manifest.validate({big: 1})), 1)  # reported, not raised

    def test_a_key_in_a_path_is_escaped(self):
        with self.assertRaises(CanonicalizationError) as cm:
            canonicalize({"\rOK\x1b[8m": [float("nan")]})
        self.assertNotIn("\r", str(cm.exception))
        self.assertNotIn("\x1b", str(cm.exception))

    def test_a_long_key_is_cut_in_the_path_an_error_names(self):
        with self.assertRaises(CanonicalizationError) as cm:
            canonicalize({"k" * 100000: [1, float("nan")]})
        self.assertLess(len(str(cm.exception)), 200)

    def test_bytes_are_read_as_utf8(self):
        self.assertEqual(loads_strict(canonicalize({"a": [1, 2], "b": "\u20ac"})), {"a": [1, 2], "b": "\u20ac"})
        self.assertEqual(loads_strict(bytearray(b"[1]")), [1])
        with self.assertRaises(CanonicalizationError):
            loads_strict(b'["\xff"]')

    def test_huge_integer_is_a_clean_refusal(self):
        with self.assertRaises(CanonicalizationError):
            canonicalize(10 ** 5000)

    def test_a_value_too_deep_to_print_is_described(self):
        deep = []
        for _ in range(100000):
            deep = [deep]
        self.assertEqual(printable(deep), "<list too large to show>")
        self.assertEqual(printable({"n": 10 ** 5000}), "<dict too large to show>")


class RoundTrip(unittest.TestCase):
    """What canonicalize writes, loads_strict reads back to the same canonical bytes. Two gaps were found here: nesting
    was counted one level apart on the two sides, and a double from 2**53 up to 1e21, whose canonical spelling is an
    integer literal, was refused on reading as an integer out of range."""

    def test_every_number_vector(self):
        for v in _load("numbers.json"):
            x = struct.unpack(">d", bytes.fromhex(v["bits"]))[0]
            text = canonicalize(x).decode("utf-8")
            self.assertEqual(canonicalize(loads_strict(text)).decode("utf-8"), text, v["bits"])

    def test_every_object_vector(self):
        for v in _load("objects.json"):
            self.assertEqual(canonicalize(loads_strict(v["expected"])).decode("utf-8"), v["expected"])

    def test_integer_literals_beyond_the_exact_range(self):
        self.assertIsInstance(loads_strict("9007199254740991"), int)
        self.assertEqual(loads_strict("9007199254740992"), 2.0 ** 53)  # the canonical spelling of that double
        self.assertIsInstance(loads_strict("9007199254740992"), float)
        self.assertEqual(loads_strict("-100000000000000000000"), -1e20)
        self.assertEqual(loads_strict("1152921504606847000"), 2.0 ** 60)
        # would round; exact but not the canonical spelling; from 1e21 up spelled with an exponent; hostile length
        for text in ("9007199254740993", "1152921504606846976", "1000000000000000000000", "1" * 5000):
            with self.assertRaises(CanonicalizationError, msg=text[:30]):
                loads_strict(text)

    def test_the_depth_limit_is_the_same_both_ways(self):
        for levels in (MAX_DEPTH - 1, MAX_DEPTH, MAX_DEPTH + 1, MAX_DEPTH + 2):
            value = []
            for _ in range(levels - 1):
                value = [value]
            outcomes = []
            for step in (lambda: canonicalize(value), lambda: loads_strict("[" * levels + "]" * levels)):
                try:
                    step()
                    outcomes.append(True)
                except CanonicalizationError:
                    outcomes.append(False)
            self.assertEqual(outcomes, [levels <= MAX_DEPTH] * 2, levels)


class Basics(unittest.TestCase):
    def test_negative_zero(self):
        self.assertEqual(format_number(-0.0), "0")

    def test_whitespace_and_order(self):
        self.assertEqual(canonicalize({"b": [1, {"d": None, "c": True}], "a": "x"}),
                         b'{"a":"x","b":[1,{"c":true,"d":null}]}')

    def test_digest_is_over_canonical_bytes(self):
        self.assertEqual(digest({"b": 1, "a": 2}), digest(json.loads('{"a": 2, "b": 1}')))
        self.assertTrue(digest([]).startswith("sha256:") and len(digest([])) == 71)

    def test_float_and_int_of_same_value_agree(self):
        # 4.0 and 4 are the same JSON number, so they must canonicalise identically
        self.assertEqual(canonicalize(4.0), canonicalize(4))


if __name__ == "__main__":
    unittest.main()
