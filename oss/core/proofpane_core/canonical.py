"""RFC 8785 (JSON Canonicalization Scheme) for the values Proofpane hashes.

Every value that is hashed is JSON-representable and canonicalised with RFC 8785. Anything else is
refused rather than coerced: there is no `default=str`, because two different values that stringify alike would
then hash alike.

Refused: non-finite floats, Python integers outside the IEEE 754 exact range (|n| > 2**53 - 1), strings with lone
surrogates, non-string object keys, and any type other than dict, list, str, int, float, bool and None. Tuples are
refused too: they are not JSON, and accepting them would make the input type ambiguous.

Numbers are IEEE 754 doubles, as RFC 8785 requires. A Python int is taken to mean an exact integer, so one beyond
2**53 - 1 is refused rather than rounded. JSON text is read the same way (`loads_strict`): an integer literal within
the range is an int; beyond it, the literal is read as a double only when it is that double's RFC 8785 spelling,
as `100000000000000000000` is for 1e20. Any other literal is refused: `9007199254740993` would be rounded, and
`1152921504606846976`, though exactly 2**60, is spelled `1152921504606847000` in RFC 8785. So text `canonicalize`
wrote always reads back, and nothing is rounded silently. A value written with a
fraction or exponent (`2.9514790517935283e+20`, `9007199254740993.0`) is a double already and is canonicalised as
that double, rounding included.

Nesting is limited to MAX_DEPTH levels of brackets, counted the same way when writing and when reading. The work is
linear in the size of the value: the path to a value is built only when an error has to name it.

`loads_strict` parses JSON text, given as `str` or as UTF-8 bytes, with the same rules plus duplicate-key rejection
and a nesting limit, so text read from a file can be checked and canonicalised in one step.
"""

import hashlib
import json
import math

MAX_SAFE_INTEGER = 2 ** 53 - 1
MAX_DEPTH = 64  # manifests are shallow; this refuses hostile nesting before the recursion limit

_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class CanonicalizationError(ValueError):
    """The value cannot be represented in RFC 8785 without loss or ambiguity."""


def _repr(value):
    try:
        return repr(value)
    except (ValueError, RecursionError):  # an integer past Python's digit limit, alone or inside a container
        return f"<{type(value).__name__} too large to show>"


def printable(value, limit=80):
    """`value` as a message may quote it: its repr, which escapes control characters and so cannot rewrite a terminal
    line, cut to `limit` characters. What a message quotes comes from the file being checked and can be any size."""
    r = _repr(value)
    return r if len(r) <= limit else r[:limit - 3] + "..."


def _key_text(key, limit=60):
    r = repr(key)[1:-1]
    return r if len(r) <= limit else r[:limit - 3] + "..."


def canonicalize(value):
    """Return the RFC 8785 canonical UTF-8 bytes of `value`."""
    out = []
    _encode(value, out, None)
    return "".join(out).encode("utf-8")


def digest(value):
    """`sha256:<hex>` over the RFC 8785 bytes of `value`."""
    return "sha256:" + hashlib.sha256(canonicalize(value)).hexdigest()


def digest_bytes(data):
    """`sha256:<hex>` over raw bytes, for artifacts that are not JSON values (a patch, a file)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


_INDEX, _MEMBER = 0, 1


def _render(path):
    """The JSONPath-like name of a position, built from the parent links only when an error needs it."""
    parts = []
    while path is not None:
        path, kind, what = path
        parts.append(f"[{what}]" if kind == _INDEX else "." + _key_text(what))
    return "$" + "".join(reversed(parts))


def _encode(value, out, path, depth=0):
    # a container at depth d is at bracket level d + 1, which is what _nesting_depth counts when reading
    if depth >= MAX_DEPTH and isinstance(value, (list, dict)):
        raise CanonicalizationError(f"{_render(path)}: nested deeper than {MAX_DEPTH} levels")
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            # the value is not formatted: a very long integer cannot even be printed (Python's digit limit)
            raise CanonicalizationError(f"{_render(path)}: integer is outside the exact IEEE 754 range")
        out.append(int.__repr__(int(value)))  # a subclass's own __str__ or __repr__ is not trusted
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{_render(path)}: {float(value)!r} is not a finite number")
        out.append(format_number(float(value)))
    elif isinstance(value, str):
        out.append(_string(value, path))
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out, (path, _INDEX, i), depth + 1)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError(f"{_render(path)}: object key {printable(key)} is not a string")
        out.append("{")
        # RFC 8785 section 3.2.3: sort by the UTF-16 code units of the keys, not by code points
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key, (path, _MEMBER, key), is_key=True))
            out.append(":")
            _encode(value[key], out, (path, _MEMBER, key), depth + 1)
        out.append("}")
    else:
        raise CanonicalizationError(f"{_render(path)}: {type(value).__name__} is not a JSON value")


def utf16_key(key):
    """Sort key giving RFC 8785's order: by UTF-16 code units, not by code points."""
    return key.encode("utf-16-be", "surrogatepass")


_utf16_key = utf16_key


def _string(s, path, is_key=False):
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        where = _render(path[0] if is_key else path) + (" key" if is_key else "")
        raise CanonicalizationError(f"{where}: string contains a lone surrogate") from None
    parts = ['"']
    for ch in s:
        if ch in _SHORT_ESCAPES:
            parts.append(_SHORT_ESCAPES[ch])
        elif ch < " ":
            parts.append(f"\\u{ord(ch):04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def format_number(x, path="$"):
    """ECMAScript Number::toString for a finite double, which RFC 8785 section 3.2.2.3 prescribes."""
    if not math.isfinite(x):
        raise CanonicalizationError(f"{path}: {x!r} is not a finite number")
    if x == 0:
        return "0"  # covers -0.0
    if x < 0:
        return "-" + format_number(-x, path)
    # repr gives the shortest decimal string that round-trips, which is the digit string ECMAScript uses
    r = float.__repr__(x)
    mantissa, _, exp = r.partition("e")
    exponent = int(exp) if exp else 0
    whole, _, frac = mantissa.partition(".")
    digits = (whole + frac).lstrip("0")
    exponent -= len(frac)
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    s, k = stripped, len(stripped)
    n = exponent + k  # x = s * 10**(n - k)
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    sign = "+" if e > 0 else "-"
    if k == 1:
        return f"{s}e{sign}{abs(e)}"
    return f"{s[0]}.{s[1:]}e{sign}{abs(e)}"


def _reject_constant(name):
    raise CanonicalizationError(f"{name} is not a JSON value")


def _parse_int(literal):
    """An integer literal as `loads_strict` reads it: exact within the safe range; beyond it, a double only when the
    literal is that double's RFC 8785 spelling. The length checks keep int() and float() off hostile literals."""
    digits = literal.lstrip("-")
    if len(digits) <= 16:
        value = int(literal)
        if abs(value) <= MAX_SAFE_INTEGER:
            return value
    if len(digits) <= 21:  # from 1e21 up, the canonical spelling has an exponent
        value = float(literal)
        if format_number(value) == literal:
            return value
    shown = literal if len(literal) <= 40 else literal[:40] + "..."
    raise CanonicalizationError(f"integer {shown} is beyond 2**53 - 1 and is not the RFC 8785 spelling of a double")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise CanonicalizationError(f"duplicate object key {printable(key)}")
        obj[key] = value
    return obj


def _nesting_depth(text):
    """Deepest bracket nesting outside strings, found without recursion so hostile input cannot exhaust the stack."""
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def loads_strict(text):
    """Parse JSON text, given as `str` or UTF-8 bytes, refusing NaN and Infinity, duplicate keys, nesting deeper than
    MAX_DEPTH, integers that would be rounded, and anything `canonicalize` would refuse."""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as e:
            raise CanonicalizationError(f"not UTF-8: {e.reason} at byte {e.start}") from None
    if _nesting_depth(text) > MAX_DEPTH:
        raise CanonicalizationError(f"nested deeper than {MAX_DEPTH} levels")
    value = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates, parse_int=_parse_int)
    canonicalize(value)
    return value
