"""Write the published chain v2 vectors. Run from oss/core:  python3 tools/gen_chain_vectors.py

Each vector is a raw file under vectors/chain/ (bytes as a chain would store them, torn writes and bad encodings
included) and an entry in vectors/chain/index.json giving how to verify it and what the verifier must report. Valid
chains carry the head and line count; invalid ones carry the rule that must reject them and the line it fails on.
There is at least one invalid vector per rule in `chain.CHAIN_RULES`.

The expected values are computed by `proofpane_core.chain` and reviewed by hand; tools/check_chain_vectors.mjs checks
the valid ones with a second implementation, and that every invalid one is refused.
"""

import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)
sys.path.insert(0, CORE)

from proofpane_core import chain  # noqa: E402
from proofpane_core.canonical import canonicalize, digest  # noqa: E402

OUT = os.path.join(CORE, "vectors", "chain")
TS = "2026-09-24T00:00:00.000000Z"


def build(events, seq=0, prev=chain.GENESIS):
    lines = []
    for i, event in enumerate(events):
        line = chain.make_line(event, seq + i, prev, f"2026-09-24T00:00:0{i % 10}.{i:06d}Z")
        lines.append(line)
        prev = line["hash"]
    return lines


def raw(lines):
    return b"".join(chain.encode(line) for line in lines)


def rehash(line):
    line = dict(line)
    line["hash"] = chain.line_hash(line)
    return line


def spelled(line, **changes):
    """The canonical bytes of a line with fields changed and its hash recomputed, bypassing make_line's checks."""
    line = rehash({**line, **changes})
    return canonicalize(line) + b"\n"


def hand_line(event_text, seq=0, prev=chain.GENESIS, ts="2026-09-24T00:00:00Z"):
    """A line whose event is given as JSON text (so it can hold what this package refuses to write), with the hash
    computed over the text as JSON.stringify would write it."""
    body = f'{{"event":{event_text},"prev":"{prev}","seq":{seq},"ts":"{ts}","v":2}}'
    h = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f'{{"event":{event_text},"hash":"{h}","prev":"{prev}","seq":{seq},"ts":"{ts}","v":2}}\n'.encode("utf-8")


EVENTS = [
    {"kind": "tool.denied", "tool": "Read", "path": "~/.ssh/id_ed25519", "rule": "deny.paths"},
    {"kind": "tool.allowed", "tool": "Bash", "command": "pytest -q", "n": 3, "ratio": 0.5, "flag": True, "none": None},
    {"kind": "note", "text": "escapes \" \\ \n \t \u0000 and UTF-16 order: € 😀 דּ", "list": [1, [2, {}]]},
]


def valid():
    out = {}
    three = build(EVENTS)
    out["empty"] = (b"", {})
    out["one"] = (raw(three[:1]), {})
    out["three"] = (raw(three), {})
    five = build(EVENTS + [{"kind": "a"}, {"kind": "b"}])
    out["window"] = (raw(five[2:]), {"start_seq": 2, "start_prev": five[1]["hash"]})
    recovered = chain.make_line({"kind": "chain.recovered", "removed_bytes": 17,
                                 "removed_digest": "sha256:" + "ab" * 32,
                                 "moved_to": f"spool.torn-1-{three[0]['hash'][7:19]}-abababababab"},
                                1, three[0]["hash"], TS)
    out["recovered"] = (raw(three[:1]) + chain.encode(recovered), {})
    # a year below 100 is a real year: a JavaScript reader that uses Date.UTC would move it to the 1900s
    out["early-year"] = (chain.encode(chain.make_line({"kind": "note"}, 0, chain.GENESIS, "0050-01-01T00:00:00Z")), {})
    # nesting at the limit: the line, its event and 62 arrays make 64 levels, which a reader must accept
    out["at-depth-limit"] = (hand_line('{"x":' + "[" * 62 + "]" * 62 + "}"), {})
    return out


def invalid():
    three = build(EVENTS)
    first, second = three[0], three[1]
    ok1 = chain.encode(first)
    out = {}

    def case(name, data, rule, line, **opts):
        out[name] = (data, {"rejected_by": rule, "line": line, **opts})

    case("line-size", ok1 + chain.encode(second), "line-size", 2, max_line_bytes=len(chain.encode(first)))
    case("line-newline", ok1 + chain.encode(second)[:-9], "line-newline", 2)
    case("line-utf8", ok1 + chain.encode(second).replace(b"pytest", b"py\xfftest"), "line-utf8", 2)
    case("line-json", ok1 + b"not json\n", "line-json", 2)
    case("line-json-duplicate-key", ok1 + b'{"v":2,"v":2}\n', "line-json", 2)
    case("line-json-nan", b'{"event":{"x":NaN}}\n', "line-json", 1)
    case("line-canonical", canonicalize(first).replace(b'"v":2', b'"v": 2') + b"\n", "line-canonical", 1)
    case("line-canonical-number", spelled(first).replace(b'"seq":0', b'"seq":0.0'), "line-canonical", 1)
    case("line-object", b"[]\n", "line-object", 1)
    # what JSON.parse accepts and the format does not: a byte-order mark, a lone surrogate, nesting past 64 levels
    case("line-json-bom", b"\xef\xbb\xbf" + ok1, "line-json", 1)
    # written by hand, with a hash that is right for the text, so that only the format's own rule can refuse them:
    # a reader that let the value through would find the rest of the line sound
    case("line-json-lone-surrogate", hand_line('{"x":"\\ud800"}'), "line-json", 1)
    case("line-json-lone-surrogate-in-key", hand_line('{"\\ud800":1}'), "line-json", 1)
    case("line-json-too-deep", hand_line('{"x":' + "[" * 63 + "]" * 63 + "}"), "line-json", 1)
    case("line-keys", spelled(first, extra=1), "line-keys", 1)
    missing = {k: v for k, v in first.items() if k != "ts"}
    case("line-keys-missing", canonicalize(missing) + b"\n", "line-keys", 1)
    case("line-version", spelled(first, v=1), "line-version", 1)
    case("line-seq", ok1 + spelled(second, seq=5), "line-seq", 2)
    case("line-seq-start", spelled(first, seq=1), "line-seq", 1)
    case("line-ts", spelled(first, ts="2026-02-30T00:00:00Z"), "line-ts", 1)
    case("line-ts-offset", spelled(first, ts="2026-09-24T00:00:00+00:00"), "line-ts", 1)
    case("line-ts-trailing", spelled(first, ts="2026-09-24T00:00:00Zx"), "line-ts", 1)
    case("line-ts-year-zero", spelled(first, ts="0000-01-01T00:00:00Z"), "line-ts", 1)
    case("line-ts-leap-second", spelled(first, ts="2026-06-30T23:59:60Z"), "line-ts", 1)
    case("line-event", spelled(first, event="denied"), "line-event", 1)
    case("line-prev-format", spelled(first, prev="sha256:" + "0" * 63), "line-prev-format", 1)
    case("line-prev-format-trailing", spelled(first, prev=chain.GENESIS + "0"), "line-prev-format", 1)
    case("line-prev", ok1 + spelled(second, prev=digest({"another": "line"})), "line-prev", 2)
    case("line-prev-genesis", spelled(first, prev=digest({"not": "genesis"})), "line-prev", 1)
    bad_hash = canonicalize({**first, "hash": "sha256:" + "g" * 64}) + b"\n"
    case("line-hash-format", bad_hash, "line-hash-format", 1)
    trailing = canonicalize({**first, "hash": first["hash"] + "0"}) + b"\n"
    case("line-hash-format-trailing", trailing, "line-hash-format", 1)
    wrong = canonicalize({**first, "hash": digest({"some": "other line"})}) + b"\n"
    case("line-hash", wrong, "line-hash", 1)
    # an edit to an earlier line, with its own hash recomputed, breaks the next line's prev
    edited = spelled(first, event={**first["event"], "tool": "Write"})
    case("edited-earlier-line", edited + chain.encode(second), "line-prev", 2)
    return out


def write(name, data, meta, entries):
    path = os.path.join(OUT, name + ".jsonl")
    with open(path, "wb") as fh:
        fh.write(data)
    entries.append({"file": name + ".jsonl", **meta})


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith(".jsonl"):
            os.remove(os.path.join(OUT, f))
    entries = []
    for name, (data, opts) in valid().items():
        start_seq, start_prev = opts.get("start_seq", 0), opts.get("start_prev", chain.GENESIS)
        result = chain.verify(io.BytesIO(data), start_seq, start_prev)
        assert result["ok"], (name, result)
        write(name, data, {"valid": True, "start_seq": start_seq, "start_prev": start_prev, "lines": result["lines"],
                           "head": result["head"], "next_seq": result["next_seq"]}, entries)
    for name, (data, meta) in invalid().items():
        limit = meta.get("max_line_bytes", chain.MAX_LINE_BYTES)
        result = chain.verify(io.BytesIO(data), max_line_bytes=limit)
        assert not result["ok"] and result["problem"].startswith(f"[{meta['rejected_by']}] line {meta['line']}:"), \
            (name, result)
        write(name, data, {"valid": False, "start_seq": 0, "start_prev": chain.GENESIS, **meta}, entries)
    entries.sort(key=lambda e: e["file"])
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"chain vectors: {sum(e['valid'] for e in entries)} valid, {sum(not e['valid'] for e in entries)} invalid")


if __name__ == "__main__":
    main()
