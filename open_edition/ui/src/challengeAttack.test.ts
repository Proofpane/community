import { readFileSync, writeFileSync } from "node:fs";
import * as N from "node:crypto";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { canonicalJson, confirmWithPasskey, signInWithPasskey } from "./passkey";

// The attack the derived challenges close, made by a compromised server against the page's own code: it composes the
// next statement of the owner's encryption keys, an e2e-off (its own nonce, seq one past the newest, prev the newest's
// sha256), and hands the page its step challenge (the sha256 of "proofpane-step/1", a zero byte and its canonical
// JSON) as the challenge of a ceremony of its own: signing in,
// a confirmation, cancelling the change that waits, the second touch of the owner's passkey as it is added to
// encryption (its own confirmation), the touch that confirms adding a passkey, a touch of renewing anchors. The owner
// touches; the server keeps the statement and the owner's answer (an answer of another passkey is none a link takes
// as the owner's). A link takes a statement as
// its owner's when the client data's challenge is the statement's step challenge (ade_link.step_challenge), so the
// page must never have the authenticator sign that challenge as it is. Here an authenticator signs as a platform one
// does, ES256 over the authenticator data and the client data's hash. tests/test_signing_domains.py runs this file
// with PP_CHALLENGE_ATTACK naming the owner's key and the link's newest statement, and hands what the server kept here
// to the link client itself.
const given = process.env.PP_CHALLENGE_ATTACK
  ? JSON.parse(readFileSync(process.env.PP_CHALLENGE_ATTACK, "utf8")) as {
    key: string; credential_id: string; rp_id: string; origin: string; account: string; deployment: string; newest: string; out: string;
  }
  : null;

const b64u = (bytes: Uint8Array) => Buffer.from(bytes).toString("base64url");
const sha256 = (bytes: Uint8Array) => new Uint8Array(N.createHash("sha256").update(bytes).digest());
const bytesOf = (v: BufferSource) => (v instanceof ArrayBuffer ? new Uint8Array(v) : new Uint8Array(v.buffer, v.byteOffset, v.byteLength)).slice();
const ascii = (s: string) => new Uint8Array(Buffer.from(s, "utf8"));
const concat = (...parts: ArrayLike<number>[]) => { const out: number[] = []; for (const p of parts) out.push(...Array.from(p)); return new Uint8Array(out); };

/** An ECDSA signature as WebAuthn sends it, DER, from WebCrypto's r || s. */
function der(raw: Uint8Array): Uint8Array {
  const int = (b: Uint8Array) => {
    let i = 0;
    while (i < b.length - 1 && b[i] === 0) i++;
    const v = b[i] & 0x80 ? concat([0], b.subarray(i)) : b.subarray(i);
    return concat([0x02, v.length], v);
  };
  const body = concat(int(raw.subarray(0, 32)), int(raw.subarray(32)));
  return concat([0x30, body.length], body);
}

let owner: { key: CryptoKey; credentialId: string; rpId: string; origin: string; account: string; deployment: string; newest: string };
const prfSecret = N.randomBytes(32);
const prf = (salt: BufferSource) => new Uint8Array(N.createHmac("sha256", prfSecret).update(bytesOf(salt)).digest()).buffer;
let statement: { payload: Record<string, unknown>; text: string; challenge: string };
/** What the server kept: each ceremony's purpose as the page named it, and the owner's answer. */
const kept: { ceremony: string; credential: { response: { clientDataJSON: string } } }[] = [];

beforeAll(async () => {
  if (given) {
    const key = await crypto.subtle.importKey("pkcs8", Buffer.from(given.key, "base64url"), { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
    owner = { key, credentialId: given.credential_id, rpId: given.rp_id, origin: given.origin, account: given.account, deployment: given.deployment, newest: given.newest };
  } else {
    const pair = (await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"])) as CryptoKeyPair;
    const first = { v: 2, deployment: "pp.example", purpose: "e2e-key", account: "acct-attack", nonce: "n1", id: "1".repeat(32), seq: 1, prev: "",
      key: b64u(N.randomBytes(32)), key_id: "2".repeat(32), retires: "all", created_at: 1790000000, expires_at: 1790000600 };
    owner = { key: pair.privateKey, credentialId: b64u(N.randomBytes(32)), rpId: "pp.example", origin: "https://pp.example", account: "acct-attack",
      deployment: "pp.example", newest: canonicalJson(first) };
  }
  // the server's statement, the next of the chain: the owner never saw it
  const newest = JSON.parse(owner.newest) as { seq: number; created_at: number };
  const payload = { v: 2, deployment: owner.deployment, purpose: "e2e-off", account: owner.account, nonce: "the server's own",
    id: N.randomBytes(16).toString("hex"), seq: newest.seq + 1, prev: N.createHash("sha256").update(owner.newest, "utf8").digest("hex"),
    created_at: newest.created_at + 60, expires_at: newest.created_at + 660 };
  const text = canonicalJson(payload);
  // the step challenge, written here from its words, as a link checks it
  statement = { payload, text, challenge: b64u(sha256(concat(ascii("proofpane-step/1"), [0], ascii(text)))) };

  const options = (allow: boolean) => ({ challenge_id: "planted", publicKey: { challenge: statement.challenge, rpId: owner.rpId, timeout: 300000,
    userVerification: "required", allowCredentials: allow ? [{ type: "public-key", id: owner.credentialId }] : [] } });
  const named: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : {};
    const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    const route = `${init.method ?? "GET"} ${url}`;
    if (route === "POST /api/auth/passkey/options") return reply(options(false));
    if (route === "POST /api/auth/passkey") { kept.push({ ceremony: "sign-in", credential: body.credential }); return reply({ token: "t", user: { id: "u" } }); }
    if (route === "POST /api/account/confirm/options") { named.push(String(body.purpose)); return reply(options(true)); }
    if (route === "POST /api/account/confirm") {
      if (body.credential?.id === owner.credentialId) kept.push({ ceremony: named.at(-1)!, credential: body.credential });
      return reply({ confirmation: "c" });
    }
    if (/^POST \/api\/ade\/holds\/[^/]+\/cancel$/.test(route)) return reply({ cancelled_at: 1 });
    return new Response(JSON.stringify({ detail: `no route for ${route}` }), { status: 404 });
  });
  // the owner's passkey, as a platform authenticator answers: present and verified, its counter kept at 0 (synced); a
  // touch the page asks of another passkey alone (the one being added) answers as that one, signed all the same
  Object.defineProperty(navigator, "credentials", { configurable: true, value: { get: async (o: CredentialRequestOptions) => {
    const pk = o.publicKey!;
    const asked = (pk.allowCredentials ?? []).map((c) => b64u(bytesOf(c.id)));
    const id = !asked.length || asked.includes(owner.credentialId) ? owner.credentialId : asked[0];
    const authData = concat(sha256(ascii(pk.rpId ?? owner.rpId)), [0x05], [0, 0, 0, 0]);
    const clientData = ascii(JSON.stringify({ type: "webauthn.get", challenge: b64u(bytesOf(pk.challenge)), origin: owner.origin, crossOrigin: false }));
    const signature = der(new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, owner.key, concat(authData, sha256(clientData)))));
    const salts = (pk.extensions?.prf?.evalByCredential as Record<string, { first: BufferSource; second?: BufferSource }> | undefined)?.[id];
    const results = salts ? { prf: { results: { first: prf(salts.first), ...(salts.second ? { second: prf(salts.second) } : {}) } } } : {};
    return { id, type: "public-key", getClientExtensionResults: () => results,
      response: { clientDataJSON: clientData.buffer, authenticatorData: authData.buffer, signature: signature.buffer, userHandle: null } };
  } } });
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  if (given) writeFileSync(given.out, JSON.stringify({ statement: statement.text, kept }));
});

/** The keys as the server serves them to the page, with the owner's passkey listed: `over`, the rest. */
const viewWith = (over: Record<string, unknown>) => K.readView({
  passkeys: [{ id: "pk-1", credential_id: owner.credentialId, name: "Laptop", created_at: 1, tier: "device", prf: true }], ...over,
});

/** Keys unlocked in this tab, for a change made from them: a master key, its roster naming the passkey that unlocked
 *  them (`by`: the owner's, by default). */
async function ringWith(credential?: { public_key: string }, by = { credentialId: owner.credentialId, passkeyId: "pk-1" }): Promise<K.Ring> {
  const master = await C.masterOf(C.random(32), owner.account);
  const roster = { credentials: credential ? [{ credential_id: by.credentialId, passkey_id: by.passkeyId, public_key: credential.public_key }] : [], recovery: "" };
  return { account: owner.account, master, masters: new Map([[master.id, master]]), roster, rosterWhy: "", pq: false, seq: 0, rosterSealed: "sealed", keys: new Map(), unreached: [], unopened: [],
    via: { kind: "credential", credentialId: by.credentialId, passkeyId: by.passkeyId, ck: { pq: false, key: master.key }, CK: new Uint8Array(32) as C.Bytes, anchor: master.id } };
}

describe("a compromised server hands the page a statement's challenge as its own ceremony's", () => {
  it("signing in", async () => {
    await signInWithPasskey();
  });

  it("confirming a change", async () => {
    await confirmWithPasskey();
  });

  it("cancelling the change that waits", async () => {
    const view = viewWith({ pending: { id: "hold-1", change: "rotate", takes_effect_at: 2, cancel_by: ["pk-1"], cancel_by_code: false } });
    const c = new K.CancelChange(owner.account, view, view.pending!);
    await c.prepare();
    await c.withPasskey();
  });

  it("the second touch of the owner's passkey as it is added to encryption, its own confirmation", async () => {
    // another passkey of the account unlocks the keys; the owner's, which links pin, is being added: its first touch
    // over the page's own challenge, its second over the server's, derived for e2e-enrol-own
    const pair = await C.newPair(true);
    const laptop = { credentialId: b64u(N.randomBytes(32)), passkeyId: "pk-0" };
    const ring = await ringWith({ public_key: C.b64u(pair.pub) }, laptop);
    const view = viewWith({ mk_id: ring.master.id, roster: [{ mk_id: ring.master.id, sealed: "sealed" }], content_keys: [],
      credentials: [{ passkey_id: "pk-0", credential_id: laptop.credentialId, salt: C.b64u(C.random(32)), public_key: C.b64u(pair.pub), wrapped: "w" }],
      passkeys: [{ id: "pk-0", credential_id: laptop.credentialId, name: "Laptop", created_at: 1, tier: "device", prf: true },
        { id: "pk-1", credential_id: owner.credentialId, name: "Phone", created_at: 2, tier: "device", prf: true }] });
    const before = kept.length;
    const e = new K.Enrol(ring, view, view.passkeys[1]);
    await e.prepare();
    await e.firstTouch();
    await e.secondTouch();
    expect(kept.slice(before).map((k) => k.ceremony)).toEqual(["e2e-enrol-own"]);
  });

  it("the touch that confirms adding a passkey to encryption", async () => {
    // the owner's passkey unlocks the keys already; another of the account's is being added, and the owner's touch
    // confirms it (the added passkey's own answer, of its second touch, is none a link takes as the owner's)
    const pair = await C.newPair(true);
    const ring = await ringWith({ public_key: C.b64u(pair.pub) });
    const view = viewWith({ mk_id: ring.master.id, roster: [{ mk_id: ring.master.id, sealed: "sealed" }], content_keys: [],
      credentials: [{ passkey_id: "pk-1", credential_id: owner.credentialId, salt: C.b64u(C.random(32)), public_key: C.b64u(pair.pub), wrapped: "w" }],
      passkeys: [{ id: "pk-1", credential_id: owner.credentialId, name: "Laptop", created_at: 1, tier: "device", prf: true },
        { id: "pk-2", credential_id: b64u(N.randomBytes(32)), name: "Phone", created_at: 2, tier: "device", prf: true }] });
    const before = kept.length;
    const e = new K.Enrol(ring, view, view.passkeys[1]);
    await e.prepare();
    await e.firstTouch();
    await e.secondTouch();
    // the confirmation made and kept; what the page sends after it needs a grant this made-up view does not list
    await expect(e.confirmAndSend()).rejects.toThrow(K.NO_GRANT);
    expect(kept.slice(before).map((k) => k.ceremony)).toEqual(["e2e-enrol-touch"]);
  });

  it("a touch of renewing anchors", async () => {
    // the passkey's own record, wrapped under its PRF output for its salt, anchored at the current master key
    const pair = await C.newPair(true);
    const salt = C.random(32);
    const wrappedFor = async (ring: K.Ring) => C.sealCredential(await C.credentialKek(new Uint8Array(prf(salt)), owner.account, owner.credentialId),
      pair.priv, ring.master.id, owner.account, owner.credentialId);
    const ring = await ringWith({ public_key: C.b64u(pair.pub) });
    // the keys as this tab opened them (RenewAnchors.prepare holds them to the ring: checkCurrent)
    const view = viewWith({ mk_id: ring.master.id, roster: [{ mk_id: ring.master.id, sealed: "sealed" }], content_keys: [],
      credentials: [{ passkey_id: "pk-1", credential_id: owner.credentialId, salt: C.b64u(salt),
        public_key: C.b64u(pair.pub), wrapped: await wrappedFor(ring) }] });
    const renew = new K.RenewAnchors(ring, view);
    await renew.prepare();
    await renew.touch(owner.credentialId);
    expect(renew.confirmations.get("pk-1")).toBe("c");
  });

  it("each time the passkey signed a challenge derived for the ceremony, never the statement's", () => {
    expect(kept.map((k) => k.ceremony)).toEqual(["sign-in", "confirm", "e2e-cancel", "e2e-enrol-own", "e2e-enrol-touch", "e2e-anchor-touch"]);
    for (const k of kept) {
      const signed = JSON.parse(Buffer.from(k.credential.response.clientDataJSON, "base64url").toString("utf8")).challenge;
      expect(signed).not.toBe(statement.challenge);
      // the derivation as the server checks it, written here from its words
      expect(signed).toBe(b64u(sha256(concat(ascii(`proofpane-confirm/1\0${k.ceremony}\0`), Buffer.from(statement.challenge, "base64url")))));
    }
  });
});
