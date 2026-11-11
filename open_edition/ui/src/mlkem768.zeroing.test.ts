import { afterEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";

// Every array the library returns is recorded, so these tests see whether the page leaves a copy of a secret behind:
// after a key is made, sealed to, opened and forgotten, every secret the
// library gave (the seed's expansion, the decapsulation key, ML-KEM's secrets, the X-Wing secrets) is zero.
const given = vi.hoisted(() => [] as { what: string; bytes: Uint8Array }[]);
// views: the library's results given as views into larger buffers, as another version of it might give them, so the
// page copies each and must zero the original
const mode = vi.hoisted(() => ({ views: false }));

vi.mock("mlkem", async (importOriginal) => {
  const real = await importOriginal<typeof import("mlkem")>();
  const keep = <T extends Uint8Array>(what: string, bytes: T): T => {
    if (mode.views) {
      const larger = new Uint8Array(bytes.length + 8);
      larger.set(bytes, 4);
      bytes.fill(0);
      bytes = larger.subarray(4, 4 + bytes.length) as T;
    }
    given.push({ what, bytes });
    return bytes;
  };
  return {
    ...real,
    createMlKem768: async () => {
      const kem = await real.createMlKem768();
      return {
        generateKeyPair: () => {
          const [pk, dk] = kem.generateKeyPair();
          return [keep("public key", pk), keep("decapsulation key", dk)];
        },
        deriveKeyPair: (seed: Uint8Array) => {
          const [pk, dk] = kem.deriveKeyPair(seed);
          return [keep("public key", pk), keep("decapsulation key", dk)];
        },
        encap: (pk: Uint8Array, message?: Uint8Array) => {
          const [ct, ss] = kem.encap(pk, message);
          return [keep("ciphertext", ct), keep("ML-KEM secret", ss)];
        },
        decap: (ct: Uint8Array, dk: Uint8Array) => keep("ML-KEM secret", kem.decap(ct, dk)),
      };
    },
    sha3_256: (bytes: Uint8Array) => keep("X-Wing secret", real.sha3_256(bytes)),
    shake256: (bytes: Uint8Array, opts?: { dkLen?: number }) => keep("expanded seed", real.shake256(bytes, opts)),
  };
});

const SECRETS = new Set(["expanded seed", "decapsulation key", "ML-KEM secret", "X-Wing secret"]);

function leftBehind(): string[] {
  return given.filter((g) => SECRETS.has(g.what) && g.bytes.some((x) => x !== 0)).map((g) => `${g.what} (${g.bytes.length})`);
}

afterEach(() => {
  given.length = 0;
  mode.views = false;
  vi.restoreAllMocks();
});

describe("no copy of a secret is left behind", () => {
  it("when the library gives views into larger buffers, which the page copies and zeroes", async () => {
    mode.views = true;
    const key = await C.hybridFromSeed(C.random(32));
    const pub = C.hybridPublic(key);
    const text = await C.sealEnvelope2(pub, C.utf8("plain"), "s");
    expect(new TextDecoder().decode(await C.openEnvelope2(key, text, "s"))).toBe("plain");
    C.forgetHybrid(key);
    expect(given.every((g) => g.bytes.byteOffset === 4)).toBe(true);
    expect(leftBehind()).toEqual([]);
  });

  it("when a key is made, sealed to, opened, used for a conversation and the recovery proof, and forgotten", async () => {
    const key = await C.hybridFromSeed(C.random(32));
    const pub = C.hybridPublic(key);
    const text = await C.sealEnvelope2(pub, C.utf8("plain"), "s");
    expect(new TextDecoder().decode(await C.openEnvelope2(key, text, "s"))).toBe("plain");
    const tk = C.random(32);
    const tkId = (await C.talkKeyOf(tk.slice())).id;
    expect((await C.openTalkKey(key, await C.sealTalkKey(pub, tk, "k"), "k", tkId)).id).toBe(tkId);
    const { ct, ss } = await C.xwingEncapsulate(pub);              // the server's part of the proof
    C.zero(ss);                                                     // a caller zeroes what it is given
    await C.recoveryProofHybrid(key, ct, C.random(32), "e2e-reseal");
    expect(leftBehind().filter((w) => !w.startsWith("decapsulation key"))).toEqual([]);   // all but the key held
    C.forgetHybrid(key);
    expect(new Set(given.map((g) => g.what))).toEqual(new Set([...SECRETS, "public key", "ciphertext"]));
    expect(leftBehind()).toEqual([]);
  });

  it("when making a key fails after the library made its half", async () => {
    vi.spyOn(globalThis.crypto.subtle, "importKey").mockRejectedValueOnce(new Error("no X25519 here"));
    await expect(C.hybridFromSeed(C.random(32))).rejects.toThrow();
    expect(given.map((g) => g.what)).toEqual(["expanded seed", "public key", "decapsulation key"]);
    expect(leftBehind()).toEqual([]);
  });
});
