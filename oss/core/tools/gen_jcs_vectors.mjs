// Independent reference for the RFC 8785 vectors. Node's JSON.stringify formats numbers with ECMAScript
// Number::toString and escapes strings as RFC 8785 requires; the default Array.prototype.sort compares UTF-16 code
// units, which is RFC 8785's key order. The Python canonicaliser is checked against what this file writes, so the
// expected values come from a second implementation rather than from the code under test.
//
// Run from oss/core:  node tools/gen_jcs_vectors.mjs
// Deterministic: a fixed-seed generator, so re-running reproduces the files byte for byte.
import { writeFileSync } from "node:fs";

let seed = 0x2f6b1d3a;
function rand() { // xorshift32
  seed ^= seed << 13; seed >>>= 0; seed ^= seed >>> 17; seed ^= seed << 5; seed >>>= 0;
  return seed / 0x100000000;
}
const pick = (xs) => xs[Math.floor(rand() * xs.length)];

function canon(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  const keys = Object.keys(v).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
}

// ---- numbers: random bit patterns cover the whole double range; the named ones cover the format's branches
const view = new DataView(new ArrayBuffer(8));
const numbers = [];
function addNumber(x) {
  view.setFloat64(0, x);
  let hex = "";
  for (let i = 0; i < 8; i++) hex += view.getUint8(i).toString(16).padStart(2, "0");
  numbers.push({ bits: hex, text: JSON.stringify(x) });
}
const named = [0, -0, 1, -1, 0.1, 0.5, 1.5, 100, 1e20, 1e21, 1e22, 1e-6, 1e-7, 123e-20, 5e-324, -5e-324,
  1.7976931348623157e308, -1.7976931348623157e308, 2 ** 53 - 1, 2 ** 53, 333333333.33333329, 4.5, 0.002, 1e-27,
  1e30, 9007199254740993, 0.000001, 0.0000001, 123456789012345680000, 1234567890123456789012, 2.2250738585072014e-308];
for (const x of named) addNumber(x);
for (let i = 0; i < 2000; i++) {
  let x;
  do {
    for (let b = 0; b < 8; b++) view.setUint8(b, Math.floor(rand() * 256));
    x = view.getFloat64(0);
  } while (!Number.isFinite(x));
  addNumber(x);
}
for (let i = 0; i < 500; i++) { // short decimals, the common case in real manifests
  const x = Math.round((rand() - 0.5) * 10 ** Math.floor(rand() * 12)) / 10 ** Math.floor(rand() * 8);
  addNumber(x);
}
writeFileSync("vectors/jcs/numbers.json", JSON.stringify(numbers, null, 1) + "\n");

// ---- objects: random keys and strings drawn from characters that exercise escaping and UTF-16 ordering
const chars = ["a", "b", "z", "A", "0", "9", "_", " ", '"', "\\", "/", "\n", "\r", "\t", "\b", "\f", "\u0000",
  "\u001f", "\u007f", "\u0080", "ö", "€", " ", " ", "דּ", "￿", "😀",
  "𐀀", "􏿿", "é", "中"];
function randString(max) {
  let s = "";
  const n = Math.floor(rand() * max);
  for (let i = 0; i < n; i++) s += pick(chars);
  return s;
}
function randValue(depth) {
  const r = rand();
  if (depth > 3 || r < 0.35) {
    const t = rand();
    if (t < 0.2) return null;
    if (t < 0.3) return rand() < 0.5;
    if (t < 0.6) return randString(6);
    return pick([0, 1, -1, 0.5, 1e21, 1e-7, 42, 3.14159, 2 ** 53 - 1, -1e-300]);
  }
  if (r < 0.6) {
    const arr = [];
    const n = Math.floor(rand() * 4);
    for (let i = 0; i < n; i++) arr.push(randValue(depth + 1));
    return arr;
  }
  const obj = {};
  const n = Math.floor(rand() * 5);
  for (let i = 0; i < n; i++) obj[randString(4)] = randValue(depth + 1);
  return obj;
}
const objects = [];
// RFC 8785 section 3.2.3 (key ordering) and section 3.2.2 (a mixed example)
const fixed = [
  '{"\\u20ac":"Euro Sign","\\r":"Carriage Return","\\ufb33":"Hebrew Letter Dalet With Dagesh","1":"One",' +
    '"\\ud83d\\ude00":"Emoji: Grinning Face","\\u0080":"Control","\\u00f6":"Latin Small Letter O With Diaeresis"}',
  '{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],' +
    '"string":"\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/","literals":[null,true,false]}',
];
for (const text of fixed) objects.push({ input: text, expected: canon(JSON.parse(text)) });
for (let i = 0; i < 300; i++) {
  const v = randValue(0);
  objects.push({ input: JSON.stringify(v), expected: canon(v) });
}
writeFileSync("vectors/jcs/objects.json", JSON.stringify(objects, null, 1) + "\n");
console.log(`numbers: ${numbers.length}, objects: ${objects.length}`);
