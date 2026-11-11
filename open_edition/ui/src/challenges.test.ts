import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as N from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonicalJson, CEREMONY_TAG, confirmWithPasskey, derivedChallenge, pageChallenge, signInWithPasskey, STEP_TAG, stepChallenge } from "./passkey";

// What a passkey signs (passkey.ts): a step's challenge, the sha256 of "proofpane-step/1", a zero byte and its
// canonical JSON, which a linked session verifies; a challenge derived from one the server picked, for the ceremony's
// purpose; or 32 random bytes of the page's own. The derivation is held to tests/vectors/ceremony_v1.json and the
// step's to tests/vectors/step_v1.json, which the server's and the link client's tests hold their own to.
const VECTORS = JSON.parse(readFileSync(join(__dirname, "../../tests/vectors/ceremony_v1.json"), "utf8")) as {
  tag: string; vectors: { purpose: (typeof PURPOSES)[number]; server_challenge: string; signed_bytes: string; challenge: string }[];
};
const STEPS = JSON.parse(readFileSync(join(__dirname, "../../tests/vectors/step_v1.json"), "utf8")) as {
  tag: string; vectors: { payload: Record<string, unknown>; canonical: string; signed_bytes: string; challenge: string; old_challenge: string }[];
};

const b64u = (bytes: Uint8Array) => Buffer.from(bytes).toString("base64url");
const sha256 = (bytes: Uint8Array) => new Uint8Array(N.createHash("sha256").update(bytes).digest());
const PURPOSES = ["sign-in", "confirm", "e2e-cancel", "e2e-enrol-touch", "e2e-enrol-own", "e2e-anchor-touch",
  "e2e-upgrade-touch"] as const;

describe("the challenge derived from one the server picked", () => {
  it("is the vectors' own, for every purpose and for a challenge of any length", async () => {
    expect(VECTORS.tag).toBe(CEREMONY_TAG);
    expect(new Set(VECTORS.vectors.map((v) => v.purpose))).toEqual(new Set(PURPOSES));
    for (const v of VECTORS.vectors) {
      expect(b64u(await derivedChallenge(v.purpose, v.server_challenge))).toBe(v.challenge);
      // and the bytes the file says are hashed are those its words describe
      const signed = Buffer.from(v.signed_bytes, "hex");
      expect(signed.equals(Buffer.concat([Buffer.from(`proofpane-confirm/1\0${v.purpose}\0`, "ascii"), Buffer.from(v.server_challenge, "base64url")]))).toBe(true);
      expect(b64u(sha256(signed))).toBe(v.challenge);
    }
  });

  it("differs by purpose for the same server bytes, and is never the server's bytes themselves", async () => {
    const server = b64u(N.randomBytes(32));
    const made = await Promise.all(PURPOSES.map((p) => derivedChallenge(p, server)));
    expect(new Set(made.map(b64u)).size).toBe(PURPOSES.length);
    for (const m of made) expect(b64u(m)).not.toBe(server);
  });

  it("cannot be a step's, though the server picked a step's challenge for its ceremony", async () => {
    // what a compromised server would hand the page: the step challenge of a statement it composed
    const step = { v: 2, deployment: "pp.example", purpose: "e2e-off", account: "acct-1", nonce: "n", id: "0".repeat(32), seq: 2,
      prev: "a".repeat(64), created_at: 1790000000, expires_at: 1790000600 };
    const wanted = await stepChallenge(step);
    expect(wanted).toEqual(sha256(Buffer.from(`proofpane-step/1\0${canonicalJson(step)}`, "utf8")));
    for (const p of PURPOSES) expect(b64u(await derivedChallenge(p, b64u(wanted)))).not.toBe(b64u(wanted));
  });
});

describe("a step's challenge", () => {
  it("is the vectors' own, and never the form a step had in older releases", async () => {
    expect(STEPS.tag).toBe(STEP_TAG);
    expect(STEPS.vectors.length).toBeGreaterThanOrEqual(5);
    for (const v of STEPS.vectors) {
      expect(canonicalJson(v.payload)).toBe(v.canonical);
      // the bytes the file says are hashed are those its words describe: the tag, a zero byte, the canonical JSON
      const signed = Buffer.from(v.signed_bytes, "hex");
      expect(signed.equals(Buffer.concat([Buffer.from("proofpane-step/1\0", "ascii"), Buffer.from(v.canonical, "utf8")]))).toBe(true);
      expect(b64u(sha256(signed))).toBe(v.challenge);
      const made = b64u(await stepChallenge(v.payload));
      expect(made).toBe(v.challenge);
      // the old form, the sha256 of the canonical JSON alone, which a page of an older release signed
      expect(v.old_challenge).toBe(b64u(sha256(Buffer.from(v.canonical, "utf8"))));
      expect(made).not.toBe(v.old_challenge);
    }
  });

  it("hashes bytes that differ from a ceremony's at their eleventh byte", async () => {
    // "proofpane-" is the first ten of both; the eleventh is "s" for a step and "c" for a ceremony, so a step's
    // challenge equals a derived one only for two different inputs with one SHA-256
    expect(Buffer.from(STEP_TAG).subarray(0, 10).equals(Buffer.from(CEREMONY_TAG).subarray(0, 10))).toBe(true);
    expect([STEP_TAG[10], CEREMONY_TAG[10]]).toEqual(["s", "c"]);
  });
});

describe("the page's own challenge", () => {
  it("is 32 random bytes, made at once", () => {
    const a = pageChallenge();
    const b = pageChallenge();
    expect(a).toBeInstanceOf(Uint8Array);
    expect(a.length).toBe(32);
    expect(b64u(a)).not.toBe(b64u(b));
  });
});

describe("signing in and confirming", () => {
  type Asked = { publicKey: { challenge: Uint8Array; rpId?: string; userVerification?: string; timeout?: number; allowCredentials?: unknown[] } };
  const asked: Asked[] = [];
  const calls: { path: string; body: unknown }[] = [];
  const server = b64u(N.randomBytes(32));
  // the server's options, saying user verification is discouraged: the page asks for it whatever they say
  const options = { challenge_id: "ch1", publicKey: { challenge: server, rpId: "pp.example", timeout: 1000, userVerification: "discouraged", allowCredentials: [{ type: "public-key", id: "AQ" }] } };
  const install = () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ path: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
      const answers: Record<string, unknown> = {
        "/api/auth/passkey/options": options, "/api/account/confirm/options": options,
        "/api/auth/passkey": { token: "t", user: { id: "u" } }, "/api/account/confirm": { confirmation: "c1" },
      };
      return new Response(JSON.stringify(answers[String(url)] ?? { detail: "no route" }), { status: url in answers ? 200 : 404 });
    }));
    Object.defineProperty(navigator, "credentials", { configurable: true, value: { get: vi.fn(async (o: Asked) => {
      asked.push(o);
      const one = (n: number) => new Uint8Array([n]).buffer;
      return { id: "AQ", type: "public-key", response: { clientDataJSON: one(1), authenticatorData: one(2), signature: one(3), userHandle: null } };
    }) } });
  };
  afterEach(() => {
    asked.length = 0;
    calls.length = 0;
    vi.unstubAllGlobals();
    delete (navigator as unknown as { credentials?: unknown }).credentials;
  });

  it("signs in over the challenge derived for sign-in, user verification required", async () => {
    install();
    await signInWithPasskey();
    expect(asked).toHaveLength(1);
    expect(b64u(new Uint8Array(asked[0].publicKey.challenge))).toBe(b64u(await derivedChallenge("sign-in", server)));
    expect(asked[0].publicKey.userVerification).toBe("required");
    expect(asked[0].publicKey.timeout).toBe(300000);
    // the options asked for naming the purpose, which the server needs before it gives any (a page of an older release
    // named none, and is refused before its prompt)
    expect(calls.find((c) => c.path === "/api/auth/passkey/options")?.body).toEqual({ purpose: "sign-in" });
    expect(calls.find((c) => c.path === "/api/auth/passkey")?.body).toMatchObject({ challenge_id: "ch1" });
  });

  it("confirms over the challenge derived for confirm, which it names as it asks for the options", async () => {
    install();
    expect(await confirmWithPasskey()).toBe("c1");
    expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm" });
    expect(b64u(new Uint8Array(asked[0].publicKey.challenge))).toBe(b64u(await derivedChallenge("confirm", server)));
    expect(asked[0].publicKey.userVerification).toBe("required");
    expect(asked[0].publicKey.allowCredentials).toHaveLength(1);
    await confirmWithPasskey(true);
    expect(calls.filter((c) => c.path === "/api/account/confirm/options").at(-1)?.body).toEqual({ purpose: "confirm", hardware: true });
  });
});
