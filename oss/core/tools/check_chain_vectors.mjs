// A second implementation of chain v2 verification, used to check the published vectors. It shares no code with
// proofpane_core: JSON is parsed by Node, the canonical form is JSON.stringify with keys in the default sort order
// (UTF-16 code units, RFC 8785's order), and hashing is Node's crypto. It checks that every valid vector verifies
// with the recorded line count, head and next seq, and that every invalid vector is refused. It does not check which
// rule refused an invalid vector: rule ids are the Python verifier's report, not part of the format.
//
// What the format requires beyond JSON.parse, and a JavaScript reader does not do by default, is done here: a
// byte-order mark is kept so that it is refused, strings must be well formed (no lone surrogates), nesting is at most
// 64 levels, and a year is read as written (Date.UTC maps 0 to 99 onto 1900 to 1999). Text is decoded strictly and
// compared with its canonical re-spelling, which for well-formed UTF-8 is the same as comparing bytes.
//
// Run from oss/core:  node tools/check_chain_vectors.mjs      (exit 0 when every vector behaves as recorded)
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const GENESIS = "sha256:" + "0".repeat(64);
const KEYS = ["event", "hash", "prev", "seq", "ts", "v"];
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const TS = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,9})?Z$/;
const MAX_SAFE = 2 ** 53 - 1;
const MAX_DEPTH = 64;

function canon(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  const keys = Object.keys(v).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function validTs(ts) {
  const m = typeof ts === "string" && TS.exec(ts);
  if (!m) return false;
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  if (y < 1) return false; // year 0 does not exist in the calendar the Python verifier uses
  const t = new Date(0);
  t.setUTCFullYear(y, mo - 1, d); // not Date.UTC, which reads 0 to 99 as 1900 to 1999
  t.setUTCHours(h, mi, s, 0);
  return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d &&
    t.getUTCHours() === h && t.getUTCMinutes() === mi && t.getUTCSeconds() === s;
}

// every string and key well formed, and nesting within the limit
function sound(v, depth = 0) {
  if (typeof v === "string") return v.isWellFormed();
  if (v === null || typeof v !== "object") return true;
  if (depth + 1 > MAX_DEPTH) return false;
  const items = Array.isArray(v) ? v : Object.entries(v).flat();
  return items.every((x) => sound(x, depth + 1));
}

function sha(text) {
  return "sha256:" + createHash("sha256").update(text, "utf8").digest("hex");
}

// Returns {ok, lines, head, nextSeq}; on failure, why (free text, for the log only).
function verify(buf, startSeq, startPrev, maxLine) {
  let seq = startSeq, prev = startPrev, n = 0, pos = 0;
  const fail = (why) => ({ ok: false, lines: n, head: prev, nextSeq: seq, why: `line ${n + 1}: ${why}` });
  while (pos < buf.length) {
    const nl = buf.indexOf(0x0a, pos);
    if (nl < 0) return fail("no newline");
    const raw = buf.subarray(pos, nl);
    if (raw.length > maxLine) return fail("too long");
    let text, line;
    try {
      text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw); // keep a BOM, to refuse it
    } catch {
      return fail("not UTF-8");
    }
    try {
      line = JSON.parse(text);
    } catch {
      return fail("not JSON");
    }
    if (!sound(line)) return fail("a lone surrogate, or nesting deeper than 64");
    // A duplicate key, a number spelled other than canonically, or any other respelling fails here.
    if (canon(line) !== text) return fail("not canonical");
    if (!isObject(line)) return fail("not an object");
    if (canon(Object.keys(line).sort()) !== canon(KEYS)) return fail("keys");
    if (line.v !== 2) return fail("version");
    if (!Number.isInteger(line.seq) || line.seq > MAX_SAFE || line.seq !== seq) return fail("seq");
    if (!validTs(line.ts)) return fail("ts");
    if (!isObject(line.event)) return fail("event");
    if (typeof line.prev !== "string" || !DIGEST.test(line.prev)) return fail("prev format");
    if (line.prev !== prev) return fail("prev");
    if (typeof line.hash !== "string" || !DIGEST.test(line.hash)) return fail("hash format");
    const { hash, ...rest } = line;
    if (sha(canon(rest)) !== hash) return fail("hash");
    seq += 1; prev = hash; n += 1; pos = nl + 1;
  }
  return { ok: true, lines: n, head: prev, nextSeq: seq };
}

const index = JSON.parse(readFileSync("vectors/chain/index.json", "utf8"));
let bad = 0;
for (const e of index) {
  const buf = readFileSync("vectors/chain/" + e.file);
  const r = verify(buf, e.start_seq, e.start_prev, e.max_line_bytes ?? 1 << 20);
  const good = e.valid
    ? r.ok && r.lines === e.lines && r.head === e.head && r.nextSeq === e.next_seq
    : !r.ok && r.lines === e.line - 1;
  if (!good) {
    bad += 1;
    console.log(`MISMATCH ${e.file}: expected ${e.valid ? "valid" : "refused at line " + e.line}, got ${JSON.stringify(r)}`);
  }
}
console.log(`${index.length} chain vectors checked, ${bad} mismatched`);
process.exit(bad ? 1 : 0);
