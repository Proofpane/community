import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The second screen on the page (secondScreen.ts): the request code computed here, as the Mac computes it, against
// the vectors the Python tests make (tests/vectors/second_screen_v1.json); the relay, with the server stood in; and the
// words the page may say of a stored step.
const calls: [string, string, unknown][] = [];
let answers: Record<string, unknown> = {};
vi.mock("./api", () => ({
  api: vi.fn(async (path: string, opts?: { method?: string; body?: unknown }) => {
    calls.push([opts?.method ?? "GET", path, opts?.body]);
    const key = `${opts?.method ?? "GET"} ${path}`;
    if (!(key in answers)) throw new Error("unexpected " + key);
    return answers[key];
  }),
}));

const { base32, collectFromMac, codeParkedHere, MARKED_MEANS, MARKED_WORDS, NOT_PARKED, parkOnMac, requestCode, SecondScreen,
  signedWords } = await import("./secondScreen");
const { canonicalJson, stepChallenge } = await import("./passkey");

const VECTORS = JSON.parse(readFileSync(join(__dirname, "../../tests/vectors/second_screen_v1.json"), "utf8")) as {
  kinds: { payload: Record<string, unknown>; canonical: string; challenge: string; code: string }[];
  design_example?: { code?: string };
};

beforeEach(() => { calls.length = 0; answers = {}; });

describe("the request code", () => {
  it("is RFC 4648 base32, unpadded", () => {
    const of = (s: string) => base32(new TextEncoder().encode(s));
    expect(["", "f", "fo", "foo", "foob", "fooba", "foobar"].map(of)).toEqual(["", "MY", "MZXQ", "MZXW6", "MZXW6YQ", "MZXW6YTB", "MZXW6YTBOI"]);
  });

  it("is the vectors' own for every kind, from the payload the page composed, as the Mac computes it from the bytes", async () => {
    expect(VECTORS.kinds.length).toBeGreaterThan(3);
    for (const v of VECTORS.kinds) {
      expect(canonicalJson(v.payload)).toBe(v.canonical);
      expect(Buffer.from(await stepChallenge(v.payload)).toString("base64url")).toBe(v.challenge);
      expect(await requestCode(v.payload)).toBe(v.code);
    }
  });
});

describe("the relay", () => {
  const v = VECTORS.kinds[0];
  const prepared = v.payload;          // what a reviewed step parks: its payload alone, never its challenge

  it("parks the payload the page composed and shows the code this page computed, never the server's", async () => {
    answers = { "POST /api/ade/sign-requests": { id: "r1", code: "AAAA-AAAA", expires_at: 1790000300 } };
    const parked = await parkOnMac(prepared);
    expect(parked).toEqual({ id: "r1", code: v.code, expires_at: 1790000300 });
    expect(calls).toEqual([["POST", "/api/ade/sign-requests", { payload: v.payload }]]);
    expect(codeParkedHere("r1")).toBe(v.code);
    expect(codeParkedHere("r2")).toBeUndefined();
    answers = { "POST /api/ade/sign-requests": { id: "r2", code: "x", expires_at: 1 } };
    calls.length = 0;
    await parkOnMac(prepared, "A move's title");
    expect(calls[0][2]).toEqual({ payload: v.payload, title: "A move's title" });
  });

  it("hands on the assertion only with the payload the page parked, byte for byte", async () => {
    const parked = { id: "r1", code: v.code, expires_at: 1790000300 };
    answers = { "GET /api/ade/sign-requests/r1": { id: "r1", state: "waiting", expires_at: 1790000300 } };
    expect(await collectFromMac(prepared, parked)).toBeNull();
    const credential = { id: "AQ", response: {} };
    answers = { "GET /api/ade/sign-requests/r1": { id: "r1", state: "signed", expires_at: 1, payload: v.canonical, credential, signer: "MacBook" } };
    const signed = await collectFromMac(prepared, parked);
    expect(signed).toEqual({ payload: v.payload, credential });
    expect(signed?.payload).toBe(prepared);                 // the page's own payload, never one read from the server
    // a payload the server holds that is not the one parked, by one character, or not text at all: nothing is sent
    for (const payload of [v.canonical.replace("Do not push.", "Do push."), v.canonical + " ", JSON.stringify(v.payload, null, 1), undefined, 7]) {
      answers = { "GET /api/ade/sign-requests/r1": { id: "r1", state: "signed", expires_at: 1, payload, credential } };
      await expect(collectFromMac(prepared, parked)).rejects.toThrow(NOT_PARKED);
    }
  });

  it("asks for a request, cancels it and revokes a signer by its id, written so it cannot be another path", async () => {
    answers = { "GET /api/ade/sign-requests/a%2F..%2Fb": { id: "x", state: "waiting", expires_at: 1 },
                "DELETE /api/ade/sign-requests/a%2F..%2Fb": { cancelled: 1 }, "DELETE /api/ade/signers/a%2F..%2Fb": { revoked_at: 1 } };
    await collectFromMac(prepared, { id: "a/../b", code: "", expires_at: 1 });
    await SecondScreen.cancel("a/../b");
    await SecondScreen.revoke("a/../b");
    expect(calls.map((c) => c[1])).toEqual(["/api/ade/sign-requests/a%2F..%2Fb", "/api/ade/sign-requests/a%2F..%2Fb", "/api/ade/signers/a%2F..%2Fb"]);
  });
});

describe("what the page says of a stored step", () => {
  const keys = [{ id: "pk1", name: "YubiKey 5C", tier: "hardware" }, { id: "pk2", name: "Phone", tier: "device" }];

  it("names the passkey as the server records it, and the signer it was relayed by", () => {
    expect(signedWords({ passkey_id: "pk1", marked: false, relayed_by: null }, keys)).toEqual({ line: "Signed with YubiKey 5C (hardware key)", marked: false });
    expect(signedWords({ passkey_id: "pk2", marked: false, relayed_by: "MacBook" }, keys)).toEqual({ line: "Signed with Phone, relayed by your signer MacBook", marked: false });
    expect(signedWords({ passkey_id: "gone", marked: false, relayed_by: null }, keys)).toBeNull();
    expect(signedWords({}, keys)).toBeNull();
    // a signer's name is the server's text: nothing else is written as one
    expect(signedWords({ passkey_id: "pk2", relayed_by: 7 as never }, keys)).toEqual({ line: "Signed with Phone", marked: false });
    expect(signedWords({ passkey_id: "pk2", relayed_by: "" }, keys)).toEqual({ line: "Signed with Phone", marked: false });
  });

  it("says a signature says it was signed outside a browser only for a hardware key's that carries the marker", () => {
    expect(signedWords({ passkey_id: "pk1", marked: true }, keys)?.marked).toBe(true);
    expect(signedWords({ passkey_id: "pk2", marked: true }, keys)?.marked).toBe(false);     // a passkey held in software
    expect(signedWords({ passkey_id: "gone", marked: true }, keys)).toBeNull();
    expect(MARKED_WORDS).toBe("says it was signed outside a browser");
    expect(MARKED_MEANS).toContain("An honest browser never says so; a compromised one, or any other program, can.");
  });

  it("writes what the server names with every hidden character shown", () => {
    const [rlo, zwsp] = [String.fromCodePoint(0x202e), String.fromCodePoint(0x200b)];
    const named = signedWords({ passkey_id: "pk1", relayed_by: `Mac${rlo}Book` }, [{ id: "pk1", name: `Key${zwsp}`, tier: "device" }]);
    expect(named?.line).toBe("Signed with Key[U+200B], relayed by your signer Mac[U+202E]Book");
  });
});
