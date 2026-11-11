import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as Kem from "./mlkem768";

// ML-KEM-768 as the page does it (./mlkem768, the one module that names a library), held to Wycheproof's three
// ML-KEM-768 files, kept under tests/vectors/wycheproof/ with their sha256 checked here:
// a library update, or a swap, that changes one byte fails these.
const DIR = join(__dirname, "../../tests/vectors/wycheproof");
const PINNED: Record<string, string> = {
  "mlkem_768_keygen_seed_test.json": "fde5abe284396f4cb3c4610b90d680f0b57782e94c3365c97aee59e24881ebe4",
  "mlkem_768_test.json": "c59c067ae794c343df575dd90f6f7458f51881b11a22d6e9d8677c8d9ee21e90",
  "mlkem_768_encaps_test.json": "9d4381f94c40853bba430245b94968b7390d9175aacd9f1ae4e250a71c78b713",
};

interface Case { tcId: number; result: "valid" | "invalid"; flags?: string[]; [field: string]: unknown }

function cases(name: string): Case[] {
  const raw = readFileSync(join(DIR, name));
  expect(createHash("sha256").update(raw).digest("hex"), name).toBe(PINNED[name]);
  return (JSON.parse(raw.toString("utf8")).testGroups as { tests: Case[] }[]).flatMap((g) => g.tests);
}

const bytes = (h: unknown) => new Uint8Array(Buffer.from(String(h), "hex"));
const hex = (b: Uint8Array) => Buffer.from(b).toString("hex");

describe("ML-KEM-768, against Wycheproof", () => {
  it("makes each key pair from its seed", async () => {
    const all = cases("mlkem_768_keygen_seed_test.json");
    expect(all).toHaveLength(100);
    for (const c of all) {
      expect(c.result).toBe("valid");
      const [ek, dk] = await Kem.keysFromSeed(bytes(c.seed));
      expect([hex(ek), hex(dk)], String(c.tcId)).toEqual([c.ek, c.dk]);
    }
  });

  it("decapsulates each valid ciphertext to its secret, and refuses each malformed input", async () => {
    const counts = { valid: 0, invalid: 0 };
    for (const c of cases("mlkem_768_test.json")) {
      let got: string[] | null;
      try {
        const [ek, dk] = await Kem.keysFromSeed(bytes(c.seed));
        got = [hex(ek), hex(await Kem.decapsulate(bytes(c.c), dk))];
      } catch {
        got = null;
      }
      expect(got, String(c.tcId)).toEqual(c.result === "valid" ? [c.ek, c.K] : null);
      counts[c.result]++;
    }
    expect(counts).toEqual({ valid: 161, invalid: 40 });
  });

  it("encapsulates with the given message to each valid key, and refuses each invalid one, as validPublic judges", async () => {
    const counts = { valid: 0, invalid: 0, overflow: 0 };
    for (const c of cases("mlkem_768_encaps_test.json")) {
      const ek = bytes(c.ek);
      expect(Kem.validPublic(ek), String(c.tcId)).toBe(c.result === "valid");
      if (c.result === "valid") {
        const [ct, ss] = await Kem.encapsulate(ek, bytes(c.m));
        expect([hex(ct), hex(ss)], String(c.tcId)).toEqual([c.c, c.K]);
      } else {
        await expect(Kem.encapsulate(ek, bytes(c.m)), String(c.tcId)).rejects.toThrow();
        if (c.flags?.includes("ModulusOverflow")) counts.overflow++;
      }
      counts[c.result]++;
    }
    expect(counts).toEqual({ valid: 133, invalid: 132, overflow: 12 });
  });
});

/** An encapsulation key with coefficient `index` of the 768 (two in each three bytes of the first 1,152) set. */
function withCoefficient(pk: Uint8Array, index: number, value: number): Uint8Array {
  const b = pk.slice();
  const at = Math.floor(index / 2) * 3;
  if (index % 2 === 0) {
    b[at] = value & 0xff;
    b[at + 1] = (b[at + 1] & 0xf0) | (value >> 8);
  } else {
    b[at + 1] = (b[at + 1] & 0x0f) | ((value & 0x0f) << 4);
    b[at + 2] = value >> 4;
  }
  return b;
}

describe("the key check", () => {
  it("takes a coefficient of 3,328 and refuses 3,329 and above, at the first and the last coefficient", async () => {
    const [pk] = await Kem.keysFromSeed(new Uint8Array(64).fill(7));
    expect(Kem.validPublic(pk)).toBe(true);
    for (const index of [0, 767]) {
      expect(Kem.validPublic(withCoefficient(pk, index, 3328))).toBe(true);
      for (const value of [3329, 4095]) {
        const bad = withCoefficient(pk, index, value);
        expect(Kem.validPublic(bad), `${index} ${value}`).toBe(false);
        await expect(Kem.encapsulate(bad)).rejects.toThrow();
      }
    }
    expect(Kem.validPublic(pk.subarray(1))).toBe(false);
    expect(Kem.validPublic(new Uint8Array(1185))).toBe(false);
  });

  it("refuses a message or a seed of another length", async () => {
    const [pk] = await Kem.keysFromSeed(new Uint8Array(64).fill(7));
    await expect(Kem.encapsulate(pk, new Uint8Array(31))).rejects.toThrow();
    await expect(Kem.keysFromSeed(new Uint8Array(63))).rejects.toThrow();
    await expect(Kem.decapsulate(new Uint8Array(1087), new Uint8Array(2400))).rejects.toThrow();
  });

  it("gives arrays a later call does not change, each the whole of a buffer of its own", async () => {
    const [pk] = await Kem.keysFromSeed(new Uint8Array(64).fill(7));
    const [ct1, ss1] = await Kem.encapsulate(pk, new Uint8Array(32).fill(1));
    const kept = [ct1.slice(), ss1.slice()];
    await Kem.encapsulate(pk, new Uint8Array(32).fill(2));
    expect([ct1, ss1]).toEqual(kept);
    for (const [a, n] of [[ct1, Kem.CIPHERTEXT_LEN], [ss1, Kem.SECRET_LEN], [Kem.sha3(pk), 32], [Kem.shake(pk, 96), 96]] as const) {
      expect([a.byteOffset, a.byteLength, a.buffer.byteLength]).toEqual([0, n, n]);
    }
  });
});
