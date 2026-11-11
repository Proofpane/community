import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { assertionWith, canonicalJson, confirmCeremony, type ConfirmPurpose, confirmWithPasskey, derivedChallenge, pageChallenge, SERVER_AHEAD,
  SERVER_AHEAD_WORDS } from "./passkey";
import { CANCEL_REMOVED, CONFIRM_STAYING, FakeAuthenticator, FakeServer, WORDS } from "./e2eTestkit";
import { CLASSICAL } from "./e2eTestkit";

const ACCOUNT = "acct-1";
let server: FakeServer;
let auth: FakeAuthenticator;

beforeEach(() => {
  server = new FakeServer();
  auth = new FakeAuthenticator();
  vi.stubGlobal("fetch", server.fetch);
  auth.install();
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  localStorage.clear();
});

const view = () => K.E2EApi.view();
/** A statement's step challenge, written here from its words: sha256("proofpane-step/1" 0 canonical JSON). */
const stepped = (p: Record<string, unknown>) => C.sha256(C.concat(C.utf8("proofpane-step/1"), new Uint8Array([0]), C.canonicalBytes(p)));
const posted = (path: string) => server.calls.filter((c) => c.method === "POST" && c.path === path);
type Body = Record<string, any>;           // eslint-disable-line @typescript-eslint/no-explicit-any
const bodyOf = (path: string) => posted(path).at(-1)!.body as Body;

function passkey(id: string, name = id, tier: "device" | "hardware" = "device") {
  if (!server.passkeys.some((p) => p.credential_id === id)) server.addPasskey(id, name, tier);
  if (!auth.creds.has(id)) auth.add(id);
}

/** Encryption turned on with `id`'s passkey, its hold ended: the ring (unlocked with the same passkey once it took
 *  effect), and the recovery code as it was shown. */
async function turnedOn(id = "credAAAA", tier: "device" | "hardware" = "device") {
  passkey(id, id, tier);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
  await t.firstTouch();
  await t.secondTouch();
  const { answer, ring } = await t.send(true);
  expect(answer.hold).toBeTruthy();
  expect(ring).toBeNull();
  server.settle();
  return { ring: await unlockAs(id), code };
}

/** Every row the server keeps, as a copy: what an old copy of its database would hold. */
function snapshot() {
  const { statements, credentials, recovery, grants, chain, roster, contentKeys, passkeys, high, rekey } = server;
  return JSON.parse(JSON.stringify({ statements, credentials, recovery, grants, chain, roster, contentKeys, passkeys, high, rekey }));
}
function restore(rows: ReturnType<typeof snapshot>) {
  Object.assign(server, JSON.parse(JSON.stringify(rows)));
}

async function turnedOff() {
  const off = new K.TurnOff(await view(), ACCOUNT);
  await off.prepare();
  await off.signAndSend();
  server.settle();
}

/** The current master key's 32 bytes, as one who holds the recovery code opens them: what a test's attacker kept. */
async function masterBytes(code: string) {
  const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
  const mkId = server.roster!.mk_id;
  const g = server.grants.find((x) => x.to === "recovery" && x.mk_id === mkId)!;
  return C.openGrant(rk, RK, g.sealed, ACCOUNT, "recovery", mkId);
}

/** The credential of the passkey that unlocked `ring`, or the first passkey's for one the recovery code unlocked. */
const viaId = (ring: K.Ring) => (ring.via.kind === "credential" ? ring.via.credentialId : "credAAAA");

/** `id`'s passkey added to encryption: two touches of it, then the confirming touch of `by`'s, a passkey that unlocks
 *  the keys already (the ring's own, by default), as the server takes it (CONFIRM_STAYING). */
async function enrolled(ring: K.Ring, id: string, tier: "device" | "hardware" = "device", by = viaId(ring)) {
  passkey(id, id, tier);
  const v = await view();
  const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
  await e.prepare();
  auth.choose = prefer(id);
  await e.firstTouch();
  await e.secondTouch();
  auth.choose = prefer(by);
  await e.confirmAndSend();
  server.settle();
}

/** The person touches `id`'s passkey where it is offered, else the first offered. */
const prefer = (id: string) => (allowed: string[]) => (allowed.includes(id) ? id : allowed[0]);

async function revoked(ring: K.Ring, what: K.RevokeWhat) {
  const r = new K.Revoke(ring, await view(), what);
  await r.prepare();
  const answer = await r.signAndSend();
  server.settle();
  return { r, answer, body: bodyOf("/api/ade/e2e/revoke") };
}

async function unlockAs(id: string, floor: string[] = []) {
  auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
  return K.unlockWithTouch(await view(), ACCOUNT, floor);
}

/** Every extractable CryptoKey reachable from `root`: its fields, its objects' and Maps' keys and values, all the way
 *  down. */
function extractableKeys(root: unknown) {
  const seen = new Set<unknown>();
  const found: unknown[] = [];
  const walk = (v: unknown) => {
    if (!v || typeof v !== "object" || seen.has(v) || ArrayBuffer.isView(v)) return;
    seen.add(v);
    if ("extractable" in v && "algorithm" in v && "usages" in v) {
      if ((v as CryptoKey).extractable) found.push(v);
      return;
    }
    for (const x of v instanceof Map ? [...v.keys(), ...v.values()] : Object.values(v)) walk(x);
  };
  walk(root);
  return found;
}

/** A confirmation made with `id`'s passkey for `purpose`: as Account makes one for the account change `change` it
 *  confirms (passkeys.CHANGE: `passkey-remove:` and an id, `high-security-on`; the server spends it on that change
 *  alone), or one of encryption's touches, as the page's flows make theirs (passkeys.CONFIRM_PURPOSES; the server
 *  spends each on a change of its own purpose only). */
async function confirmation(id: string, purpose: ConfirmPurpose = "confirm", change?: string) {
  auth.choose = () => id;
  if (purpose === "confirm") return confirmWithPasskey(false, change ? { change } : undefined);
  const c = await confirmCeremony(purpose);
  const { credential } = await assertionWith(c.ask);
  return (await K.E2EApi.confirm(c.challenge_id, credential)).confirmation;
}

/** The passkey `id` removed from the account, as Account removes one: confirmed with `by`'s passkey for that change.
 *  The server must take it. */
async function removePasskey(id: string, by: string) {
  const r = await server.fetch(`/api/account/passkeys/${id}`, { method: "DELETE",
    body: JSON.stringify({ confirmation: await confirmation(by, "confirm", `passkey-remove:${id}`) }) });
  expect(r.status).toBe(200);
}

/** High security turned on or off, as Account turns it: confirmed with `by`'s passkey (a hardware key) for that
 *  change. The server must take it. */
async function turnHighSecurity(on: boolean, by: string) {
  const r = await server.fetch("/api/account/high-security", { method: "PUT",
    body: JSON.stringify({ on, confirmation: await confirmation(by, "confirm", on ? "high-security-on" : "high-security-off") }) });
  expect(r.status).toBe(200);
}

/** What WebCrypto opens and when each request goes, from now: `at` finds the first opening with an AAD that ends with
 *  `aad`, `sent` the request to `path`. */
function watchOpens() {
  const order: { what: string; aad?: Uint8Array }[] = [];
  const subtle = crypto.subtle;
  const decrypt = subtle.decrypt.bind(subtle);
  const unwrap = subtle.unwrapKey.bind(subtle);
  vi.spyOn(subtle, "decrypt").mockImplementation((alg, key, data) => {
    order.push({ what: "decrypt", aad: new Uint8Array((alg as AesGcmParams).additionalData as ArrayBuffer) });
    return decrypt(alg, key, data);
  });
  vi.spyOn(subtle, "unwrapKey").mockImplementation((...a: Parameters<SubtleCrypto["unwrapKey"]>) => {
    order.push({ what: "unwrap", aad: new Uint8Array((a[3] as AesGcmParams).additionalData as ArrayBuffer) });
    return unwrap(...a);
  });
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") order.push({ what: `POST ${url}` });
    return server.fetch(url, init);
  });
  const endsWith = (a: Uint8Array | undefined, b: Uint8Array) => !!a && a.length >= b.length && C.same(a.subarray(a.length - b.length), b);
  return {
    at: (what: string, aad: string) => order.findIndex((o) => o.what === what && endsWith(o.aad, C.utf8(aad))),
    count: (what: string, aad: string) => order.filter((o) => o.what === what && endsWith(o.aad, C.utf8(aad))).length,
    sent: (path: string) => order.findIndex((o) => o.what === `POST ${path}`),
  };
}

describe("turning encryption on", () => {
  it("makes the recovery code first, and asks four of its characters back", () => {
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    const groups = t.codeText().split(" ");
    expect(groups).toHaveLength(8);
    expect(t.codeMatches(groups[t.group])).toBe(true);
    expect(t.codeMatches(` ${groups[t.group].toLowerCase()} `)).toBe(true);
    expect(t.codeMatches(groups[t.group].slice(1))).toBe(false);
    expect(t.codeMatches("")).toBe(false);
  });

  it("signs the statement with the first touch, checks the PRF with the second, opens both grants and the content key's wrap before anything is sent, and sends everything in one request, which waits on its hold", async () => {
    passkey("credAAAA", "Laptop");
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    const code = C.recoveryBytes(t.codeText())!;
    await t.prepare(await view());
    expect(posted("/api/ade/link/nonce").map((c) => c.body)).toEqual([{ purpose: "e2e-key" }]);
    await t.firstTouch();
    await t.secondTouch();
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(0);

    // what WebCrypto opens, and when the request goes
    const order: { what: string; aad?: Uint8Array }[] = [];
    const subtle = crypto.subtle;
    const decrypt = subtle.decrypt.bind(subtle);
    const unwrap = subtle.unwrapKey.bind(subtle);
    vi.spyOn(subtle, "decrypt").mockImplementation((alg, key, data) => {
      order.push({ what: "decrypt", aad: new Uint8Array((alg as AesGcmParams).additionalData as ArrayBuffer) });
      return decrypt(alg, key, data);
    });
    vi.spyOn(subtle, "unwrapKey").mockImplementation((...a: Parameters<SubtleCrypto["unwrapKey"]>) => {
      order.push({ what: "unwrap", aad: new Uint8Array((a[3] as AesGcmParams).additionalData as ArrayBuffer) });
      return unwrap(...a);
    });
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      if (init?.method === "POST") order.push({ what: `POST ${url}` });
      return server.fetch(url, init);
    });
    const { answer, ring: none, via, view: after } = await t.send(true);
    expect(answer.hold?.takes_effect_at).toBe(server.now + 180);
    expect(none).toBeNull();
    expect(after.mk_id).toBeNull();                                      // nothing kept before the hold ends
    expect(after.pending?.change).toBe("turn-on");
    expect(via).toMatchObject({ kind: "credential", credentialId: "credAAAA", passkeyId: "pk-credAAAA" });

    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(1);
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(Object.keys(body).sort()).toEqual(["content_key", "credential", "grants", "mk_id", "recovery", "roster", "seal", "statement"]);
    const p = body.statement.payload;
    expect(Object.keys(p).sort()).toEqual(["account", "created_at", "deployment", "expires_at", "id", "key", "key_id", "nonce", "prev", "purpose", "retires", "seq", "v"]);
    expect(p).toMatchObject({ v: 2, purpose: "e2e-key", account: ACCOUNT, deployment: "pp.example", seq: 1, prev: "", retires: "all" });
    expect(p.expires_at - p.created_at).toBe(K.STATEMENT_LIFE);
    expect(body.seal).toBe(true);

    const endsWith = (a: Uint8Array | undefined, b: Uint8Array) => !!a && a.length >= b.length && C.same(a.subarray(a.length - b.length), b);
    const at = (what: string, aad: string) => order.findIndex((o) => o.what === what && endsWith(o.aad, C.utf8(aad)));
    const sent = order.findIndex((o) => o.what === "POST /api/ade/e2e/turn-on");
    for (const [what, aad] of [["decrypt", C.AAD.grant(ACCOUNT, "recovery", body.mk_id)], ["decrypt", C.AAD.grant(ACCOUNT, "credAAAA", body.mk_id)],
      ["unwrap", C.AAD.contentKey(ACCOUNT, p.key_id, body.mk_id)], ["decrypt", C.AAD.recoveryAnchor(ACCOUNT)]]) {
      expect(at(what, aad), aad).toBeGreaterThan(-1);
      expect(at(what, aad), aad).toBeLessThan(sent);
    }

    // what was sent opens as it should, with the code, the passkey's PRF and the master key
    const { rk, RK } = await C.recoveryKeys(code, ACCOUNT);
    expect(body.recovery.public_key).toBe(C.b64u(RK));
    expect(await C.openRecoveryAnchor(code, body.recovery.anchor, ACCOUNT)).toBe(body.mk_id);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    const mk = await C.openGrant(rk, RK, body.grants.find((g: Body) => g.to === "recovery").sealed, ACCOUNT, "recovery", body.mk_id);
    const master = await C.masterOf(mk, ACCOUNT);
    expect(master.id).toBe(body.mk_id);
    expect(await C.openRoster(master, body.roster, ACCOUNT)).toEqual({
      credentials: [{ credential_id: "credAAAA", passkey_id: "pk-credAAAA", public_key: body.credential.public_key }], recovery: C.b64u(RK) });
    expect(body.credential.passkey_id).toBe("pk-credAAAA");
    const prf = await auth.prf("credAAAA", C.unb64u(body.credential.salt)!);
    const opened = await C.openCredential(await C.credentialKek(prf, ACCOUNT, "credAAAA"), body.credential.wrapped, ACCOUNT, "credAAAA");
    expect(opened.anchor).toBe(body.mk_id);
    expect(C.b64u(await C.publicOf(opened.ck))).toBe(body.credential.public_key);
    const toCred = await C.openGrant(opened.ck, C.unb64u(body.credential.public_key)!, body.grants.find((g: Body) => g.to === "credAAAA").sealed, ACCOUNT, "credAAAA", body.mk_id);
    expect(C.same(toCred, mk)).toBe(true);
    expect(body.content_key).toMatchObject({ key_id: p.key_id, public_key: p.key, mk_id: body.mk_id });
    const ek = await C.unwrapContentKey(master, body.content_key.wrapped, p.key_id, ACCOUNT);
    expect(C.b64u(await C.publicOf(ek))).toBe(p.key);

    // the touches: the first over the statement, the second the same credential and salt; user verification each time
    expect(auth.ceremonies[0].challenge).toEqual(await stepped(p));
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);
    expect(auth.ceremonies[0].salts).toEqual({ credAAAA: body.credential.salt });
    expect(auth.ceremonies[1].allow).toEqual(["credAAAA"]);
    expect(auth.ceremonies[1].salts).toEqual({ credAAAA: body.credential.salt });
    expect(auth.ceremonies.map((c) => c.userVerification)).toEqual(["required", "required"]);
    expect(auth.outputs).toHaveLength(2);
    expect(auth.outputs.every((b) => new Uint8Array(b).every((x) => x === 0))).toBe(true);
    expect(t.code.every((b) => b === 0)).toBe(true);
    // nothing is on until the hold ends; then the passkey unlocks it, and this browser trusts the keys it made
    expect((await view()).mk_id).toBeNull();
    server.settle();
    expect([...(await unlockAs("credAAAA", K.floorOf(ACCOUNT))).keys.keys()]).toEqual([p.key_id]);
  });

  it("gives the ring at once when its hold takes no time, from the view read after it, and what unlocks it", async () => {
    passkey("credAAAA");
    server.delays.change = 0;
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await t.firstTouch();
    await t.secondTouch();
    const { answer, ring, via, view: after } = await t.send(true);
    expect(answer.hold).toBeTruthy();
    expect(server.pending).toBeNull();
    expect(after.mk_id).toBe(bodyOf("/api/ade/e2e/turn-on").mk_id);
    expect(ring?.master.id).toBe(after.mk_id);
    expect(via.anchor).toBe(after.mk_id);
    expect(after.sealed).toMatchObject({ events: 3, left: 1, rows: 2, talks: 1 });     // what this sealing sealed
  });

  it("offers each passkey that may be enrolled, each with a salt of its own, and enrols the one touched", async () => {
    passkey("credAAAA");
    passkey("credBBBB");
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    auth.choose = () => "credBBBB";
    await t.firstTouch();
    await t.secondTouch();
    await t.send(false);
    const c = auth.ceremonies[0];
    expect(c.allow).toEqual(["credAAAA", "credBBBB"]);
    expect(c.salts.credAAAA).not.toBe(c.salts.credBBBB);
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(body.credential).toMatchObject({ passkey_id: "pk-credBBBB", salt: c.salts.credBBBB });
    expect(body.seal).toBe(false);
    expect(auth.ceremonies[1].allow).toEqual(["credBBBB"]);
  });

  it("while high security is on, offers its hardware keys alone", async () => {
    passkey("credAAAA", "phone", "device");
    passkey("credBBBB", "key", "hardware");
    server.high = true;
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await t.firstTouch();
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);
  });

  it("keeps nothing when the second touch gives another PRF output, and says this browser and key cannot unlock", async () => {
    server.addPasskey("credAAAA", "Security key", "hardware");
    auth.add("credAAAA", { unstable: true });
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await t.firstTouch();
    await expect(t.secondTouch()).rejects.toThrow(K.UNSTABLE);
    await expect(t.send(true)).rejects.toThrow();
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(0);
  });

  it("refuses a passkey that gives no PRF in this browser", async () => {
    server.addPasskey("credAAAA", "Old key");
    auth.add("credAAAA", { noPrf: true });
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await expect(t.firstTouch()).rejects.toThrow(K.NO_PRF_ADD);      // turning it on adds the passkey to encryption
  });

  it("says a security key's words where a security key, by what the server keeps of it, gives no key as it turns encryption on", async () => {
    server.addPasskey("credAAAA", "Blue", "device", { kind: "security-key" });
    auth.add("credAAAA", { noPrf: true });
    const first = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await first.prepare(await view());
    await expect(first.firstTouch()).rejects.toThrow(K.NO_PRF_KEY_ADD);
    auth.creds.get("credAAAA")!.noPrf = false;
    const second = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await second.prepare(await view());
    await second.firstTouch();
    auth.creds.get("credAAAA")!.noPrf = true;
    await expect(second.secondTouch()).rejects.toThrow(K.NO_PRF_KEY_ADD);
  });

  it("says, where the passkey gives no key at its second touch as it turns encryption on, that it cannot be added here", async () => {
    server.addPasskey("credAAAA", "Old key");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await t.firstTouch();
    auth.creds.get("credAAAA")!.noPrf = true;
    await expect(t.secondTouch()).rejects.toThrow(K.NO_PRF_ADD);
  });

  it("for an account that keeps its keys from before, turns on again under the current master key, or starts again with replace, which waits a day", async () => {
    const { ring } = await turnedOn();
    await turnedOff();
    expect((await view()).on).toBe(false);
    const again = new K.TurnOnAgain(ring, await view());
    expect(again.rekey).toBe(false);
    await again.prepare();
    expect(again.rekey).toBe(false);
    auth.ceremonies.length = 0;
    expect((await again.signAndSend(true)).hold?.takes_effect_at).toBe(server.now + 180);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);                // a passkey that unlocks it signs it
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(Object.keys(body).sort()).toEqual(["content_key", "seal", "statement"]);
    expect(body.content_key.mk_id).toBe(ring.master.id);
    expect(body.statement.payload).toMatchObject({ seq: 3, retires: "all" });
    expect((await view()).on).toBe(false);                             // until its hold ends
    server.settle();
    expect((await view()).on).toBe(true);

    // or, locked, it starts again: the old keys are forgotten once its day has passed; until then the old code, still
    // the one in force, cancels it
    await turnedOff();
    const fresh = new K.TurnOn(ACCOUNT, true, CLASSICAL);
    await fresh.prepare(await view());
    await fresh.firstTouch();
    await fresh.secondTouch();
    expect((await fresh.send(true)).answer.hold?.takes_effect_at).toBe(server.now + 86400);
    expect(bodyOf("/api/ade/e2e/turn-on").replace).toBe(true);
    expect((await view()).pending?.cancel_by_code).toBe(true);
    expect(server.roster!.mk_id).toBe(ring.master.id);
    server.settle();
    expect(server.roster!.mk_id).not.toBe(ring.master.id);
  });

  it("turns on again with a new master key while one is due, sealed to the credentials that stay and signed by one", async () => {
    const { ring: first } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    const ring = await unlockAs("credAAAA");
    expect(ring.roster!.credentials.map((c) => c.credential_id).sort()).toEqual(["credAAAA", "credBBBB"]);
    // credBBBB removed while it was off: its credential and its grant gone, and a new master key due
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credBBBB");
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");
    server.grants = server.grants.filter((g) => g.to !== "credBBBB");
    server.rekey = true;
    const v = await view();
    expect(v.rekey_due).toBe(true);
    const again = new K.TurnOnAgain(ring, v);
    await again.prepare();
    expect(again.rekey).toBe(true);
    auth.ceremonies.length = 0;
    await again.signAndSend(true);
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(Object.keys(body).sort()).toEqual(["chain", "content_key", "content_keys", "grants", "mk_id", "roster", "seal", "statement"]);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);       // never to the one removed
    expect(body.chain.previous).toBe(ring.master.id);
    expect(body.statement.payload.retires).toBe("all");
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);
    server.settle();
    const now = await view();
    expect(now.on).toBe(true);
    expect(now.mk_id).toBe(body.mk_id);
    expect(now.rekey_due).toBe(false);
    const after = await unlockAs("credAAAA");
    expect(after.master.id).toBe(body.mk_id);
    expect(after.roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
    expect([...after.keys.keys()].sort()).toEqual(now.content_keys.map((k) => k.key_id).sort());
  });

  it("has the test server read a turn-on's new master key for its forms before anything is asked of the account's keys (_turn_on_plan)", async () => {
    const { ring: first } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    const ring = await unlockAs("credAAAA");
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credBBBB");
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");
    server.grants = server.grants.filter((g) => g.to !== "credBBBB");
    server.rekey = true;
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    expect(again.rekey).toBe(true);
    // what the page sends, changed on its way: the chain's wrap cut short and the master key it names another; the
    // server reads the forms first (_revocation_body), so the wrap is what it says
    vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
      if (url === "/api/ade/e2e/turn-on" && init.method === "POST") {
        const b = JSON.parse(String(init.body)) as Body;
        b.chain = { previous: "f".repeat(32), wrapped: C.b64u(C.random(27)) };
        init = { ...init, body: JSON.stringify(b) };
      }
      return server.fetch(url, init);
    });
    await expect(again.signAndSend(true)).rejects.toThrow("The chain's wrap is 28 to 1024 bytes, in base64url without padding.");
    expect(server.pending).toBeNull();
  });

  it("does not turn on again where no passkey that unlocks the keys is left to sign it", async () => {
    const { code } = await turnedOn("credAAAA");
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    server.credentials = [];                       // removed while it was off
    server.grants = server.grants.filter((g) => g.to === "recovery");
    server.rekey = true;
    const v = await view();
    const ring = await K.unlockWithCode(v, ACCOUNT, code);
    await expect(new K.TurnOnAgain(ring, v).prepare()).rejects.toThrow(K.NO_SIGNER);
  });
});

describe("unlocking", () => {
  it("takes one touch, every enrolled credential allowed with its own salt, and opens the master key and every content key, nothing extractable", async () => {
    const { ring: first } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    auth.ceremonies.length = 0;
    const ring = await unlockAs("credBBBB");
    const v = await view();
    expect(auth.ceremonies).toHaveLength(1);
    expect(auth.ceremonies[0].allow.sort()).toEqual(["credAAAA", "credBBBB"]);
    expect(auth.ceremonies[0].salts).toEqual(Object.fromEntries(v.credentials.map((c) => [c.credential_id, c.salt])));
    expect(auth.ceremonies[0].userVerification).toBe("required");
    expect(ring.master.id).toBe(v.mk_id);
    expect([...ring.keys.keys()]).toEqual(v.content_keys.map((k) => k.key_id));
    const keys = [ring.master.key, ...[...ring.masters.values()].map((m) => m.key), ...[...ring.keys.values()].map((k) => k.ek.key as CryptoKey),
      (ring.via.kind === "credential" ? ring.via.ck : ring.via.rk).key as CryptoKey];
    expect(keys.every((k) => k.extractable === false)).toBe(true);
    expect(auth.outputs.at(-1) && new Uint8Array(auth.outputs.at(-1)!).every((b) => b === 0)).toBe(true);
  });

  it("refuses a master key the server made up: a grant to the credential's public key of a key that does not lead back to its anchor", async () => {
    await turnedOn("credAAAA");
    const forged = C.random(32);
    const fake = await C.masterOf(forged, ACCOUNT);
    const CK = C.unb64u(server.credentials[0].public_key)!;
    server.grants = [{ mk_id: fake.id, to: "credAAAA", sealed: await C.sealGrant(CK, forged, ACCOUNT, "credAAAA", fake.id) }];
    server.roster = { mk_id: fake.id, sealed: await C.sealRoster(fake, { credentials: [{ credential_id: "credAAAA", passkey_id: "pk-credAAAA", public_key: C.b64u(CK) }], recovery: C.b64u(CK) }, ACCOUNT) };
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    // nor does a link of the chain it makes up to the real key help, without the real key
    const other = await C.masterOf(C.random(32), ACCOUNT);
    server.chain = [{ mk_id: fake.id, previous: other.id, wrapped: await C.sealChain(fake, C.random(32), other.id, ACCOUNT) }];
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
  });

  // each refusal names its own cause (compared whole: toThrow(undefined) would take any error)
  const refusal = (p: Promise<unknown>) => p.then(() => "(none)", (e: Error) => e.message);
  it("says a grant of the current master key that does not open is one, not keys that do not lead back", async () => {
    await turnedOn("credAAAA");
    const current = (await view()).mk_id!;
    const CK = C.unb64u(server.credentials[0].public_key)!;
    const all = server.grants.slice();
    const honest = all.find((g) => g.mk_id === current && g.to === "credAAAA")!;
    const sealed = C.unb64u(honest.sealed.slice(C.PREFIX.length))!;
    sealed[sealed.length - 1] ^= 1;
    server.grants = all.map((g) => (g === honest ? { ...g, sealed: C.PREFIX + C.b64u(sealed) } : g));
    expect(await refusal(unlockAs("credAAAA"))).toBe(K.GRANT_NOT_OPEN);        // it does not open with this key
    server.grants = all.map((g) => (g === honest ? { ...g, sealed: "" } : g));
    expect(await refusal(unlockAs("credAAAA"))).toBe(K.GRANT_NOT_OPEN);
    const other = await C.sealGrant(CK, C.random(32), ACCOUNT, "credAAAA", current);
    server.grants = all.map((g) => (g === honest ? { ...g, sealed: other } : g));
    expect(await refusal(unlockAs("credAAAA"))).toBe(K.GRANT_NOT_OPEN);        // it opens, to another key than it names
    expect(K.GRANT_NOT_OPEN).toBe("The master key's grant to this key does not open with it: the server holds a grant not sealed to this key, or a damaged one.");
    server.grants = all;
    expect((await unlockAs("credAAAA")).master.id).toBe(current);
  });

  it("says a record this passkey does not open may not be its own, and blames no browser alone", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const a = server.credentials.find((c) => c.credential_id === "credAAAA")!;
    const b = server.credentials.find((c) => c.credential_id === "credBBBB")!;
    const own = a.wrapped;
    a.wrapped = b.wrapped;                                                       // another passkey's record planted under it
    const refused = await unlockAs("credAAAA").then(() => null, (e: Error) => e.message);
    expect(refused).toBe(K.OWN_RECORD);
    expect(refused).toContain("that record may not be its own");
    a.wrapped = own;
    await expect(unlockAs("credAAAA")).resolves.toBeTruthy();
  });

  it("advises removing a passkey the roster does not name only to whoever does not know it", () => {
    const one = K.unnamedWords(["Phone"]);
    expect(one).toContain("“Phone” as unlocking encryption, which the roster of these keys does not name: no new master key is taken without it");
    expect(one).toContain("If you do not know that passkey, remove it first, or in the same change.");
    expect(one).toContain("If it is yours, do not remove it on this word alone: a roster that leaves out a passkey of yours is also what a change you did not make would leave.");
    expect(one).not.toMatch(/added without the master key|, so remove/);
    const two = K.unnamedWords(["Phone", "Key"]);
    expect(two).toContain("“Phone”, “Key” as unlocking encryption");
    expect(two).toContain("If you do not know those passkeys, remove them first, or in the same change. If they are yours, do not remove them on this word alone");
  });

  it("refuses a chain whose link does not open, or opens to another key than the one it names", async () => {
    const { ring } = await turnedOn("credAAAA");
    await revoked(ring, {});
    expect((await unlockAs("credAAAA")).master.id).toBe(server.roster!.mk_id);       // the anchor is the first key
    const link = server.chain[0];
    const wrapped = C.unb64u(link.wrapped)!;
    wrapped[wrapped.length - 1] ^= 1;
    server.chain = [{ ...link, wrapped: C.b64u(wrapped) }];
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    server.chain = [{ ...link, previous: "ab".repeat(16) }];
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    server.chain = [link, { ...link, previous: "cd".repeat(16) }];                 // two keys before the current one
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    server.chain = [];
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
  });

  it("refuses a fork: a second key replacing a key on the way to the anchor, or a second master key sealed to the key that reaches it", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const old = await masterBytes(code);                          // kept by one who once held it
    const oldMaster = await C.masterOf(old, ACCOUNT);
    await revoked(ring, {});
    const honest = { grants: server.grants.slice(), chain: server.chain.slice(), roster: server.roster };
    const CK = C.unb64u(server.credentials[0].public_key)!;
    const forked = C.random(32);
    const fork = await C.masterOf(forked, ACCOUNT);
    const forkLink = { mk_id: fork.id, previous: oldMaster.id, wrapped: await C.sealChain(fork, old, oldMaster.id, ACCOUNT) };
    const forkGrant = { mk_id: fork.id, to: "credAAAA", sealed: await C.sealGrant(CK, forked, ACCOUNT, "credAAAA", fork.id) };
    const forkRoster = { mk_id: fork.id, sealed: await C.sealRoster(fork, { credentials: [{ credential_id: "credAAAA", passkey_id: "pk-credAAAA", public_key: C.b64u(CK) }], recovery: C.b64u(CK) }, ACCOUNT) };
    // the fork made current, the honest link from the same old key still there
    server.grants = [forkGrant];
    server.chain = [...honest.chain, forkLink];
    server.roster = forkRoster;
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    // the honest key current, a forked one sealed to the same credential beside it
    server.grants = [...honest.grants, forkGrant];
    server.chain = [...honest.chain, forkLink];
    server.roster = honest.roster;
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    // what remains, as the manual says: with the honest key's rows deleted, a fork from the old key reaches an anchor
    // set before it was replaced, until anchors are renewed (RenewAnchors, below)
    server.grants = [forkGrant];
    server.chain = [forkLink];
    server.roster = forkRoster;
    expect((await unlockAs("credAAAA")).master.id).toBe(fork.id);
  });

  it("takes the links older than a credential's anchor as no fork: one added after a revocation unlocks after the next", async () => {
    const { ring } = await turnedOn("credAAAA");
    await revoked(ring, {});
    const second = await unlockAs("credAAAA");
    await enrolled(second, "credBBBB");                              // anchored at the second master key
    await revoked(await K.follow(second, await view()), {});
    expect(server.chain).toHaveLength(2);
    const b = await unlockAs("credBBBB");
    expect(b.via.anchor).toBe(second.master.id);
    expect(b.master.id).toBe(server.roster!.mk_id);
    expect((await unlockAs("credAAAA")).masters.size).toBe(3);
  });

  it("reads with a credential the roster does not name, and seals to that roster and rewrites it never", async () => {
    const { ring } = await turnedOn("credAAAA");
    const oldRoster = server.roster!;
    await enrolled(ring, "credBBBB");
    server.roster = oldRoster;                                    // the server kept the roster from before credBBBB
    const b = await unlockAs("credBBBB");
    expect(b.roster).toBeNull();
    expect(b.rosterWhy).toBe(K.ROSTER_LACKS_THIS);
    expect(b.keys.size).toBe(1);                                  // what is sealed opens: reading needs no roster
    const a = await unlockAs("credAAAA");
    expect(a.master.id).toBe(ring.master.id);
    expect(a.roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
    expect(a.rosterWhy).toBe("");
  });

  it("refuses a credential whose own record does not match the public key the server lists", async () => {
    await turnedOn("credAAAA");
    server.credentials[0] = { ...server.credentials[0], public_key: C.b64u((await C.newPair()).pub) };
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
  });

  it("with the recovery code: rk checked against the recovery key, its anchor opened; a wrong or malformed code is refused", async () => {
    const { code } = await turnedOn("credAAAA");
    const ring = await K.unlockWithCode(await view(), ACCOUNT, code.toLowerCase());
    expect(ring.via.kind).toBe("recovery");
    expect(ring.keys.size).toBe(1);
    await expect(K.unlockWithCode(await view(), ACCOUNT, C.recoveryText(C.random(20)))).rejects.toThrow(K.WRONG_CODE);
    await expect(K.unlockWithCode(await view(), ACCOUNT, "not a code")).rejects.toThrow(K.CODE_FORM);
    const anchor = C.unb64u(server.recovery!.anchor)!;
    anchor[anchor.length - 1] ^= 1;
    server.recovery = { ...server.recovery!, anchor: C.b64u(anchor) };
    // the right code, an anchor that does not open with it: said as that
    expect(await K.unlockWithCode(await view(), ACCOUNT, code).then(() => "(none)", (e: Error) => e.message)).toBe(
      "The recovery code is this account's, but the anchor the server holds for it does not open with it: the server holds an anchor not made with this code, or a damaged one.");
  });

  it("follows a rotation and a revocation made elsewhere with the key it holds, and no touch", async () => {
    const { ring } = await turnedOn("credAAAA");
    const elsewhere = await unlockAs("credAAAA");
    const r = new K.Rotate(elsewhere, await view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await revoked(await K.follow(elsewhere, await view()), {});
    const touches = auth.ceremonies.length;
    const followed = await K.follow(ring, await view());
    expect(auth.ceremonies.length).toBe(touches);
    expect(followed.master.id).toBe(server.roster!.mk_id);
    expect(followed.master.id).not.toBe(ring.master.id);
    expect(followed.keys.size).toBe(3);
  });
});

describe("adding a passkey to encryption", () => {
  it("says, where the passkey added gives no key at either of its touches, that it cannot be added here", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const first = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await first.prepare();
    auth.choose = prefer("credBBBB");
    auth.creds.get("credBBBB")!.noPrf = true;
    await expect(first.firstTouch()).rejects.toThrow(K.NO_PRF_ADD);
    auth.creds.get("credBBBB")!.noPrf = false;
    const second = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await second.prepare();
    await second.firstTouch();
    auth.creds.get("credBBBB")!.noPrf = true;
    await expect(second.secondTouch()).rejects.toThrow(K.NO_PRF_ADD);
  });

  /** The challenge an adding holds for its second touch: the server's for e2e-enrol-own, or null. */
  const ownOf = (e: K.Enrol) => (e as unknown as { s: { own?: unknown } }).s.own;

  it("takes two touches of it, the first wrapping under its PRF and the second, its own confirmation, opening that; a passkey that unlocks the keys already confirms it; then the master key is sealed to it and the roster rewritten", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    passkey("credBBBB", "Security key", "hardware");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    expect(e.byCode).toBe(false);
    expect(e.confirmers().map((p) => p.credential_id)).toEqual(["credAAAA"]);
    await e.prepare();
    const options = (purpose: string) => posted("/api/account/confirm/options").filter((c) => c.body?.purpose === purpose);
    const confirmedWith = () => posted("/api/account/confirm").map((c) => (c.body as Body).credential.id);
    expect(options("e2e-enrol-own")).toHaveLength(0);
    auth.ceremonies.length = 0;
    auth.choose = () => "credBBBB";
    await e.firstTouch();
    expect(options("e2e-enrol-own")).toHaveLength(1);                  // the second touch's challenge, as the first ends
    expect(options("e2e-enrol-touch")).toHaveLength(0);
    expect(confirmedWith()).toEqual([]);
    expect(ownOf(e)).toBeTruthy();
    await e.secondTouch();
    expect(confirmedWith()).toEqual(["credBBBB"]);                     // the passkey added, its own confirmation
    expect(ownOf(e)).toBeNull();                                       // its challenge used: never signed twice
    expect(options("e2e-enrol-touch")).toHaveLength(1);                // the confirmation's challenge, after the two
    auth.choose = () => "credAAAA";
    await e.confirmAndSend();
    expect(confirmedWith()).toEqual(["credBBBB", "credAAAA"]);
    expect(options("e2e-enrol-touch")).toHaveLength(2);                // a fresh one behind it, for a refusal
    expect(options("e2e-enrol-own")).toHaveLength(1);
    const body = bodyOf("/api/ade/e2e/credentials");
    expect(Object.keys(body).sort()).toEqual(["confirmation", "credential", "grant", "mk_id", "roster"]);
    expect(Object.keys(body.credential).sort()).toEqual(["confirmation", "passkey_id", "public_key", "salt", "wrapped"]);
    expect(body.confirmation).toMatch(/^confirmation-/);
    expect(body.credential.confirmation).toMatch(/^confirmation-/);
    expect(body.credential.confirmation).not.toBe(body.confirmation);
    expect(body.mk_id).toBe(ring.master.id);
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"], ["credBBBB"], ["credAAAA"]]);
    expect(auth.ceremonies[0].salts).toEqual({ credBBBB: body.credential.salt });
    expect(auth.ceremonies[1].salts).toEqual({ credBBBB: body.credential.salt });
    expect(auth.ceremonies[2].salts).toEqual({});                     // the confirming touch asks no PRF
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "pk-credAAAA", byCode: false });
    const prf = await auth.prf("credBBBB", C.unb64u(body.credential.salt)!);
    const opened = await C.openCredential(await C.credentialKek(prf, ACCOUNT, "credBBBB"), body.credential.wrapped, ACCOUNT, "credBBBB");
    expect(opened.anchor).toBe(ring.master.id);
    const mk = await C.openGrant(opened.ck, C.unb64u(body.credential.public_key)!, body.grant, ACCOUNT, "credBBBB", ring.master.id);
    expect(C.same(mk, await masterBytes(code))).toBe(true);
    const roster = await C.openRoster(await C.masterOf(mk, ACCOUNT), body.roster, ACCOUNT);
    expect(roster.credentials.map((c) => c.credential_id)).toEqual(["credAAAA", "credBBBB"]);
    expect(roster.recovery).toBe(ring.roster!.recovery);
    server.settle();
    expect((await unlockAs("credBBBB")).via).toMatchObject({ kind: "credential", credentialId: "credBBBB", anchor: ring.master.id });
  });

  it("keeps no extractable private key while the person touches: once the first touch seals the new key, only its public half is kept", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB", "Security key", "hardware");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    expect(extractableKeys(e)).toEqual([]);
    auth.choose = () => "credBBBB";
    await e.firstTouch();
    expect(extractableKeys(e)).toEqual([]);                          // the wait for the second touch
    await e.secondTouch();
    expect(extractableKeys(e)).toEqual([]);
    auth.choose = () => "credAAAA";
    await e.confirmAndSend();
    expect(extractableKeys(e)).toEqual([]);
    server.settle();
    expect((await unlockAs("credBBBB")).via).toMatchObject({ kind: "credential", credentialId: "credBBBB", anchor: ring.master.id });
  });

  it("keeps nothing, and asks the server for no confirmation, when the second touch gives another PRF output", async () => {
    const { ring } = await turnedOn("credAAAA");
    server.addPasskey("credBBBB", "Security key", "hardware");
    auth.add("credBBBB", { unstable: true });
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    await e.firstTouch();
    await expect(e.secondTouch()).rejects.toThrow(K.UNSTABLE);
    expect(posted("/api/account/confirm")).toHaveLength(0);           // its confirmation only once the wrap opened
    await expect(e.send()).rejects.toThrow();
    expect(posted("/api/ade/e2e/credentials")).toHaveLength(0);
  });

  it("says the second touch is being prepared when its challenge could not be fetched, prompts nothing, fetches it again, and takes the touch once it is here", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    let down = true;
    server.answer = (call) => (down && call.path === "/api/account/confirm/options" && call.body?.purpose === "e2e-enrol-own" ? [503, { detail: "Not now." }] : null);
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    expect(ownOf(e)).toBeNull();
    down = false;
    auth.ceremonies.length = 0;
    await expect(e.secondTouch()).rejects.toThrow(K.BEING_PREPARED);
    expect(auth.ceremonies).toHaveLength(0);
    await vi.waitFor(() => expect(ownOf(e)).toBeTruthy());
    await e.secondTouch();
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"]]);
    auth.choose = prefer("credAAAA");
    expect((await e.confirmAndSend()).hold).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "pk-credAAAA" });
  });

  it("takes the second touch again over a fresh challenge when its confirmation is refused, keeping nothing of the refused one", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    let refuse = true;
    server.answer = (call) => (refuse && call.method === "POST" && call.path === "/api/account/confirm" ? [403, { detail: "Not this time." }] : null);
    auth.ceremonies.length = 0;
    await expect(e.secondTouch()).rejects.toThrow("Not this time.");
    refuse = false;
    await expect(e.confirmAndSend()).rejects.toThrow("Touch the passkey twice first.");
    await vi.waitFor(() => expect(ownOf(e)).toBeTruthy());
    await e.secondTouch();
    // two prompts of the passkey added, each over a challenge of its own
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"], ["credBBBB"]]);
    expect(C.b64u(auth.ceremonies[1].challenge)).not.toBe(C.b64u(auth.ceremonies[0].challenge));
    auth.choose = prefer("credAAAA");
    expect((await e.confirmAndSend()).hold).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "pk-credAAAA" });
  });

  /** `id`'s passkey, of the account and not yet added to encryption, its two touches made: the adding, ready to be
   *  confirmed. */
  async function touchedTwice(ring: K.Ring, id: string) {
    passkey(id);
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
    await e.prepare();
    auth.choose = prefer(id);
    await e.firstTouch();
    await e.secondTouch();
    return e;
  }

  it("never takes the new passkey's own touch as its confirmation: the page offers only passkeys that unlock the keys already, and the server refuses one the new passkey made", async () => {
    const { ring } = await turnedOn("credAAAA");
    const e = await touchedTwice(ring, "credBBBB");
    const confirms = posted("/api/account/confirm").length;            // the new passkey's own, of its second touch
    auth.ceremonies.length = 0;
    auth.choose = () => "credBBBB";
    await expect(e.confirmAndSend()).rejects.toThrow(K.ASKED_ANOTHER);     // forced past what the page offers
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credAAAA"]]);
    expect(posted("/api/account/confirm")).toHaveLength(confirms);
    expect(posted("/api/ade/e2e/credentials")).toHaveLength(0);
    // a confirmation the new passkey made, sent all the same, the server refuses
    (e as unknown as { s: { confirmation: string } }).s.confirmation = await confirmation("credBBBB", "e2e-enrol-touch");
    await expect(e.send()).rejects.toThrow(CONFIRM_STAYING);
    expect(server.pending).toBeNull();
    // the one the page asks for goes, over the fresh challenge fetched behind the refused touch
    await vi.waitFor(() => expect((e as unknown as { s: { ceremony: unknown } }).s.ceremony).toBeTruthy());
    auth.choose = () => "credAAAA";
    expect((await e.confirmAndSend()).hold?.takes_effect_at).toBe(server.now + 180);
  });

  it("goes with the recovery code's proof instead, asked for after the touches: no confirming touch, a day's wait, and only the code cancels it", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const e = await touchedTwice(ring, "credBBBB");
    auth.ceremonies.length = 0;
    const answer = await e.sendWithCode(await K.codeKey(await view(), ACCOUNT, code));
    expect(answer.hold?.takes_effect_at).toBe(server.now + 86400);
    expect(auth.ceremonies).toHaveLength(0);
    const body = bodyOf("/api/ade/e2e/credentials");
    expect(Object.keys(body).sort()).toEqual(["credential", "grant", "mk_id", "recovery_proof", "roster"]);
    expect(body.credential.confirmation).toMatch(/^confirmation-/);
    expect(posted("/api/ade/e2e/recovery-proof").at(-1)!.body).toEqual({ purpose: "credentials" });
    // no passkey's confirmation but the new passkey's own, of its second touch
    expect(posted("/api/account/confirm").map((c) => (c.body as Body).credential.id)).toEqual(["credBBBB"]);
    expect((await view()).pending).toMatchObject({ change: "credentials", made_by: "recovery", by_code: true, cancel_by: [], cancel_by_code: true });
    server.settle();
    expect((await unlockAs("credBBBB")).via).toMatchObject({ kind: "credential", credentialId: "credBBBB" });
  });

  it("goes only with the recovery code while a change waits that only the code cancels: refused before any touch without it, then in that change's place in one step", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    await r.signAndSend(await K.recoveryProof(ACCOUNT, "rotate", { code }));
    const waiting = server.pending!.id;
    const v = await view();
    expect(v.pending).toMatchObject({ by_code: true, cancel_by: [], cancel_by_code: true });
    passkey("credBBBB");
    const w = await view();
    const e = new K.Enrol(ring, w, w.passkeys.find((p) => p.credential_id === "credBBBB")!);
    expect(e.byCode).toBe(true);
    expect(e.confirmers()).toEqual([]);
    await e.prepare();
    auth.choose = () => "credBBBB";
    await e.firstTouch();
    await e.secondTouch();
    expect(posted("/api/account/confirm/options").filter((c) => c.body?.purpose === "e2e-enrol-touch")).toHaveLength(0);
    auth.ceremonies.length = 0;
    await expect(e.confirmAndSend()).rejects.toThrow(K.CODE_NEEDED);
    await expect(e.send()).rejects.toThrow(K.CODE_NEEDED);
    expect(auth.ceremonies).toHaveLength(0);
    await e.sendWithCode(await K.codeKey(w, ACCOUNT, code));
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "recovery" });
    expect(server.pending!.id).not.toBe(waiting);
  });

  it("is refused before any touch where neither a passkey that unlocks the keys nor the code could take the waiting change's place", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true };
    server.answer = (call) => {                                        // a server that says the code does not cancel it
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: { ...v.pending, cancel_by_code: false } }];
    };
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    expect(e.byCode).toBe(false);
    await expect(e.prepare()).rejects.toThrow(K.WAITING_CHANGE);
  });

  it("says the confirmation is being prepared when its challenge could not be fetched, fetches it again, and goes once it is here", async () => {
    const { ring } = await turnedOn("credAAAA");
    let down = true;
    const touchOptions = (call: { path: string; body?: Record<string, unknown> }) =>
      call.path === "/api/account/confirm/options" && call.body?.purpose === "e2e-enrol-touch";
    server.answer = (call) => (down && touchOptions(call) ? [503, { detail: "Not now." }] : null);
    const e = await touchedTwice(ring, "credBBBB");
    down = false;
    auth.ceremonies.length = 0;
    await expect(e.confirmAndSend()).rejects.toThrow(K.BEING_PREPARED);
    expect(auth.ceremonies).toHaveLength(0);
    await vi.waitFor(() => expect((e as unknown as { s: { ceremony: unknown } }).s.ceremony).toBeTruthy());
    expect(server.calls.filter(touchOptions)).toHaveLength(2);
    auth.choose = () => "credAAAA";
    expect((await e.confirmAndSend()).hold).toBeTruthy();
  });
});

describe("a routine rotation", () => {
  it("states a new content key under the current master key as the next statement, retiring the earlier after a grace", async () => {
    const { ring } = await turnedOn("credAAAA");
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    auth.ceremonies.length = 0;
    const answer = await r.signAndSend();
    expect(answer.hold?.takes_effect_at).toBe(server.now + 180);
    expect(auth.ceremonies).toHaveLength(1);
    const body = bodyOf("/api/ade/e2e/rotate");
    expect(Object.keys(body).sort()).toEqual(["content_key", "statement"]);
    const p = body.statement.payload;
    expect(p).toMatchObject({ seq: 2, retires: "grace", prev: C.hex(await C.sha256(C.utf8(server.statements[0].payload))) });
    expect(body.content_key).toMatchObject({ key_id: p.key_id, public_key: p.key, mk_id: ring.master.id });
    expect(auth.ceremonies[0].challenge).toEqual(await stepped(p));
    expect(auth.ceremonies[0].userVerification).toBe("required");
    server.settle();
    expect((await K.follow(ring, await view())).keys.has(p.key_id)).toBe(true);
  });
});

describe("a revocation", () => {
  it("seals the new master key to the roster's keys alone, never to a key the server lists that the roster lacks, and wraps every content key again", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    // a credential the server lists that the roster lacks, with a key of whoever wrote it: never sealed to, and the
    // server takes no new master key without a grant to it, so it is removed in the same change
    const rogue = await C.newPair();
    passkey("credXXXX");
    server.credentials.push({ passkey_id: "pk-credXXXX", credential_id: "credXXXX", salt: C.b64u(C.random(32)), public_key: C.b64u(rogue.pub), wrapped: C.b64u(C.random(60)), created_at: 1 });
    await expect(new K.Revoke(unlocked, await view(), { remove: ["pk-credBBBB"] }).prepare()).rejects.toThrow(K.UNNAMED);
    const before = await masterBytes(code);
    const removedRow = server.credentials.find((c) => c.credential_id === "credBBBB")!;
    const { body } = await revoked(unlocked, { remove: ["pk-credBBBB", "pk-credXXXX"] });
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(body.remove).toEqual(["pk-credBBBB", "pk-credXXXX"]);
    expect(body.high_security).toBe(false);
    expect(body.statement.payload).toMatchObject({ retires: "all", seq: 2 });
    expect(body.content_keys.map((k: Body) => k.key_id)).toEqual([...unlocked.keys.keys()]);
    expect(body.content_key.mk_id).toBe(body.mk_id);
    expect(body.chain.previous).toBe(unlocked.master.id);
    // the new key's chain link holds the old key
    const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
    const next = await C.masterOf(await C.openGrant(rk, RK, body.grants.find((g: Body) => g.to === "recovery").sealed, ACCOUNT, "recovery", body.mk_id), ACCOUNT);
    expect(C.same(await C.openChain(next, body.chain.wrapped, unlocked.master.id, ACCOUNT), before)).toBe(true);
    for (const k of body.content_keys) {
      const ek = await C.unwrapContentKey(next, k.wrapped, k.key_id, ACCOUNT);
      expect(C.same(await C.publicOf(ek), unlocked.keys.get(k.key_id)!.EK)).toBe(true);
    }
    expect((await C.openRoster(next, body.roster, ACCOUNT)).credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
    // the removed passkey has nothing to open with, even were its old record kept: no grant of the new key is its;
    // the one that stays opens through the chain
    expect((await view()).credentials.map((c) => c.credential_id)).not.toContain("credBBBB");
    server.credentials.push(removedRow);
    const v = await view();
    const t = await K.prfTouch({ challenge: pageChallenge(), allow: [{ credentialId: "credBBBB", salt: C.unb64u(removedRow.salt)! }] });
    const via = await K.viaFromPrf(v, ACCOUNT, "credBBBB", t.prf);
    await expect(K.openRing(v, ACCOUNT, via)).rejects.toThrow(K.NO_GRANT);
    server.credentials.pop();
    const after = await unlockAs("credAAAA");
    expect(after.master.id).toBe(body.mk_id);
    expect(after.keys.size).toBe(2);
  });

  it("leaves out of the new master key a credential of the roster the server no longer lists, or whose passkey is gone", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    await enrolled(await unlockAs("credAAAA"), "credCCCC");
    const unlocked = await unlockAs("credAAAA");
    expect(unlocked.roster!.credentials.map((c) => c.credential_id).sort()).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");    // as the server dropped it
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credCCCC");          // listed, its passkey gone
    const { body, r } = await revoked(unlocked, {});
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(r.kept).toEqual(["pk-credAAAA"]);
    expect((await unlockAs("credAAAA")).roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
  });

  it("while high security is on, leaves a credential that is not a hardware key out of the new master key", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB", "hardware");
    const unlocked = await unlockAs("credAAAA");
    server.high = true;                            // turned on since, and the device's credential not left out yet
    auth.choose = (allowed) => allowed[0];
    const { body } = await revoked(unlocked, {});
    expect(body.high_security).toBe(false);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credBBBB", "recovery"]);
  });

  it("turning high security on keeps the hardware keys alone, and is signed by a hardware key", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB", "hardware");
    const unlocked = await K.follow(ring, await view());
    auth.choose = (allowed) => allowed[0];
    auth.ceremonies.length = 0;
    const { body, r } = await revoked(unlocked, { highSecurity: true });
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);
    expect(body.high_security).toBe(true);
    expect(body.remove).toEqual([]);
    expect(r.leaving).toEqual(["pk-credAAAA"]);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credBBBB", "recovery"]);
    expect(server.high).toBe(true);
  });

  it("a new recovery code replaces the recovery key and its anchor: the old code opens nothing after it", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const fresh = C.random(20);
    const freshText = C.recoveryText(fresh);
    const { body } = await revoked(ring, { newCode: fresh.slice() });
    const { RK } = await C.recoveryKeys(fresh, ACCOUNT);
    expect(body.recovery.public_key).toBe(C.b64u(RK));
    expect(await C.openRecoveryAnchor(fresh, body.recovery.anchor, ACCOUNT)).toBe(body.mk_id);
    await expect(K.unlockWithCode(await view(), ACCOUNT, code)).rejects.toThrow(K.WRONG_CODE);
    expect((await K.unlockWithCode(await view(), ACCOUNT, freshText)).master.id).toBe(body.mk_id);
  });
});

describe("renewing anchors", () => {
  it("says, where the passkey gives no key as its record is renewed, that it is renewed where it gives one", async () => {
    const { ring } = await turnedOn("credAAAA");
    await revoked(ring, {});
    const now = await unlockAs("credAAAA");
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    auth.creds.get("credAAAA")!.noPrf = true;
    await expect(renew.touch("credAAAA")).rejects.toThrow(/so its record cannot be renewed here/);
  });

  it("writes each touched credential's wrap again with the current master key as its anchor, and then a fork from an older key is refused", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const old = await masterBytes(code);
    const oldMaster = await C.masterOf(old, ACCOUNT);
    await revoked(ring, {});
    const now = await unlockAs("credAAAA");
    expect(now.via.anchor).toBe(oldMaster.id);
    const renew = new K.RenewAnchors(now, await view());
    expect(renew.targets().map((c) => c.credential_id)).toEqual(["credAAAA"]);
    await renew.prepare();
    await renew.touch("credAAAA");
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "confirmation", "mk_id"]);
    expect(body.mk_id).toBe(now.master.id);
    expect(body.anchors.map((a: Body) => a.passkey_id)).toEqual(["pk-credAAAA"]);
    server.settle();
    expect((await unlockAs("credAAAA")).via.anchor).toBe(now.master.id);
    // the fork the manual names: the honest rows deleted, a key chained from the old one, now refused
    const CK = C.unb64u(server.credentials[0].public_key)!;
    const forked = C.random(32);
    const fork = await C.masterOf(forked, ACCOUNT);
    server.grants = [{ mk_id: fork.id, to: "credAAAA", sealed: await C.sealGrant(CK, forked, ACCOUNT, "credAAAA", fork.id) }];
    server.chain = [{ mk_id: fork.id, previous: oldMaster.id, wrapped: await C.sealChain(fork, old, oldMaster.id, ACCOUNT) }];
    server.roster = { mk_id: fork.id, sealed: await C.sealRoster(fork, { credentials: [{ credential_id: "credAAAA", passkey_id: "pk-credAAAA", public_key: C.b64u(CK) }], recovery: C.b64u(CK) }, ACCOUNT) };
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
  });

  it("refuses a renewed record that does not open, under the fresh salt's key, to this key and the current master key", async () => {
    const { ring } = await turnedOn("credAAAA");
    await revoked(ring, {});
    const now = await unlockAs("credAAAA");                             // anchored at the first master key
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    // the record as written names another master key than the current one
    const encrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    const record = C.utf8(C.AAD.credential(ACCOUNT, "credAAAA"));
    vi.spyOn(crypto.subtle, "encrypt").mockImplementation((alg, key, data) => {
      const aad = new Uint8Array((alg as AesGcmParams).additionalData as ArrayBuffer);
      if (!C.same(aad, record)) return encrypt(alg, key, data);
      const text = new TextDecoder().decode(data as Uint8Array).replace(now.master.id, ring.master.id);
      return encrypt(alg, key, C.utf8(text));
    });
    await expect(renew.touch("credAAAA")).rejects.toThrow(K.OWN_RECORD);
    expect(renew.rows).toHaveLength(0);
  });

  it("while high security is on, renews only the hardware keys' records, whose own confirmation alone the server takes", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credHHHH", "hardware");
    const unlocked = await K.follow(ring, await view());
    server.high = true;                            // turned on since, and the device's credential not left out yet
    const v = await view();
    expect(v.credentials.map((c) => c.credential_id).sort()).toEqual(["credAAAA", "credHHHH"]);
    expect(new K.RenewAnchors(unlocked, v).targets().map((c) => c.credential_id)).toEqual(["credHHHH"]);
  });

  it("sends nothing when every anchor touched is current", async () => {
    const { ring } = await turnedOn("credAAAA");
    const renew = new K.RenewAnchors(ring, await view());
    await renew.prepare();
    await renew.touch("credAAAA");
    expect(await renew.send()).toBeNull();
    expect(posted("/api/ade/e2e/anchors")).toHaveLength(0);
  });

  /** A master key forged from `old` (the first one's bytes, which a removed passkey's holder kept): granted to the
   *  recovery key, chained to the first master key, its roster naming the recovery key alone. */
  async function forkForTheCode(old: C.Bytes, oldId: string) {
    const RK = C.unb64u(server.recovery!.public_key)!;
    const forked = C.random(32);
    const fork = await C.masterOf(forked, ACCOUNT);
    server.grants = [{ mk_id: fork.id, to: "recovery", sealed: await C.sealGrant(RK, forked, ACCOUNT, "recovery", fork.id) }];
    server.chain = [{ mk_id: fork.id, previous: oldId, wrapped: await C.sealChain(fork, old, oldId, ACCOUNT) }];
    server.roster = { mk_id: fork.id, sealed: await C.sealRoster(fork, { credentials: [], recovery: C.b64u(RK) }, ACCOUNT) };
    server.contentKeys = [];
    return fork.id;
  }

  it("renews the recovery code's anchor with the code this tab was unlocked with, nothing typed, and a fork from an older master key is then refused with the code", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const old = await masterBytes(code);                               // what a removed passkey's holder kept
    await enrolled(ring, "credPPPP");
    await revoked(await K.follow(ring, await view()), { remove: ["pk-credPPPP"] });
    const byCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    expect(byCode.via.anchor).toBe(ring.master.id);                   // the code's anchor: the first master key
    // before it is renewed, a browser that keeps no floor takes a fork from the first master key, with the code
    const before = snapshot();
    const forked = await forkForTheCode(old, ring.master.id);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).master.id).toBe(forked);
    restore(before);
    const renew = new K.RenewAnchors(byCode, await view());
    await renew.prepare();
    expect(renew.hasCode() && renew.codeHeld()).toBe(true);
    await renew.renewCode();
    expect(renew.codeDone).toBe(true);
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors).toEqual([]);
    expect(body.recovery_anchor.public_key).toBe(server.recovery!.public_key);
    expect(await C.openRecoveryAnchor(C.recoveryBytes(code)!, body.recovery_anchor.anchor, ACCOUNT)).toBe(byCode.master.id);
    expect(server.pending?.madeBy).toBe("recovery");                  // no passkey touched: the code's proof
    server.settle();
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).via.anchor).toBe(byCode.master.id);
    // the same fork now: it does not lead back to the code's anchor
    await forkForTheCode(old, ring.master.id);
    await expect(K.unlockWithCode(await view(), ACCOUNT, code)).rejects.toThrow(K.NOT_LEAD_BACK);
  });

  it("refuses a renewed recovery anchor that does not open, with the code's key, to the current master key", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await revoked(ring, {});
    const now = await unlockAs("credAAAA");
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    // the anchor as written names another master key than the current one
    const encrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    const aad = C.utf8(C.AAD.recoveryAnchor(ACCOUNT));
    vi.spyOn(crypto.subtle, "encrypt").mockImplementation((alg, key, data) => {
      if (!C.same(new Uint8Array((alg as AesGcmParams).additionalData as ArrayBuffer), aad)) return encrypt(alg, key, data);
      return encrypt(alg, key, C.utf8(new TextDecoder().decode(data as Uint8Array).replace(now.master.id, ring.master.id)));
    });
    await expect(renew.renewCode(code)).rejects.toThrow("The renewed recovery anchor does not name the master key.");
    expect(renew.recovery).toBeNull();
    expect(renew.codeDone).toBe(false);
  });

  it("renews it with the code typed in a tab unlocked with a passkey, checked to be the account's, beside the passkeys' touches: the code's proof confirms it, the passkey touched giving its own confirmation", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await revoked(ring, {});
    const now = await unlockAs("credAAAA");
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    expect(renew.codeHeld()).toBe(false);
    await expect(renew.renewCode()).rejects.toThrow(K.CODE_FORM);
    await expect(renew.renewCode("A".repeat(32))).rejects.toThrow(K.WRONG_CODE);
    await renew.renewCode(code);
    await renew.touch("credAAAA");
    expect(renew.maker()).toBe("pk-credAAAA");
    expect(renew.confirmedBy()).toBeNull();                           // the code's anchor goes with the code's proof alone
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors.map((a: Body) => [a.passkey_id, a.confirmation])).toEqual([["pk-credAAAA", renew.confirmations.get("pk-credAAAA")]]);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
    server.settle();
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).via.anchor).toBe(now.master.id);
    expect((await unlockAs("credAAAA")).via.anchor).toBe(now.master.id);
    // current already: nothing more to renew, and nothing sent for it
    const again = new K.RenewAnchors(await unlockAs("credAAAA"), await view());
    await again.prepare();
    await again.renewCode(code);
    expect(again.codeDone).toBe(true);
    expect(again.recovery).toBeNull();
    expect(await again.send()).toBeNull();
  });
});

describe("turning it off", () => {
  it("signs an e2e-off as the next statement, with no key", async () => {
    await turnedOn("credAAAA");
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    const p = bodyOf("/api/ade/e2e/off").statement.payload;
    expect(Object.keys(p).sort()).toEqual(["account", "created_at", "deployment", "expires_at", "id", "nonce", "prev", "purpose", "seq", "v"]);
    expect(p).toMatchObject({ purpose: "e2e-off", seq: 2 });
    expect(posted("/api/ade/link/nonce").at(-1)!.body).toEqual({ purpose: "e2e-off" });
  });
});

describe("the view", () => {
  /** A statement as the server serves one: its columns and its signed payload, the columns saying what the payload
   *  says unless `columns` says otherwise. */
  const signedSt = (seq: number, purpose: "e2e-key" | "e2e-off", columns: Record<string, unknown> = {}) => {
    const key = purpose === "e2e-key" ? { key: `K${seq}`, key_id: String(seq).repeat(32).slice(0, 32), retires: "all" } : null;
    return { seq, id: `id-${seq}`, purpose, key_id: key?.key_id ?? null, public_key: key?.key ?? null, retires: key?.retires ?? null,
      payload: { seq, id: `id-${seq}`, purpose, ...key }, created_at: 1, ...columns };
  };

  it("says encryption is on when its newest statement is e2e-key as signed, which is what links obey, whatever else the server says", () => {
    expect(K.readView({ on: true, statements: [signedSt(1, "e2e-key"), signedSt(2, "e2e-off")] }).on).toBe(false);
    expect(K.readView({ on: false, statements: [signedSt(2, "e2e-key"), signedSt(1, "e2e-off")] }).on).toBe(true);
    // the newest's purpose column says otherwise: as it was signed, and named as misstated
    expect(K.readView({ statements: [signedSt(1, "e2e-key"), signedSt(2, "e2e-off", { purpose: "e2e-key" })] })).toMatchObject({ on: false, misstated: [2] });
    expect(K.readView({ statements: [signedSt(1, "e2e-off", { purpose: "e2e-key" }), signedSt(2, "e2e-key", { purpose: "e2e-off" })] })).toMatchObject({ on: true, misstated: [1, 2] });
    expect(K.readView({ on: true, statements: [] }).on).toBe(false);
    expect(K.readView({ rekey_due: true }).rekey_due).toBe(true);
    expect(K.readView({ rekey_due: "yes" }).rekey_due).toBe(false);
  });

  it("reads each statement's purpose, key and retires from its signed payload, and names in misstated one served with a column that says otherwise, or a payload that is no statement's", () => {
    const read = (s: Record<string, unknown>) => K.readView({ statements: [signedSt(1, "e2e-key"), s] });
    expect(read(signedSt(2, "e2e-key"))).toMatchObject({ misstated: [] });
    expect(read(signedSt(2, "e2e-off"))).toMatchObject({ misstated: [] });
    // a column left out says nothing
    expect(read({ seq: 2, id: "id-2", payload: signedSt(2, "e2e-key").payload, created_at: 1 }))
      .toMatchObject({ misstated: [], statements: [{}, { purpose: "e2e-key", key_id: "2".repeat(32), public_key: "K2", retires: "all" }] });
    // each column that says otherwise
    for (const [column, value] of [["seq", 3], ["id", "id-9"], ["purpose", "e2e-off"], ["key_id", "9".repeat(32)], ["key_id", null],
      ["public_key", "K9"], ["retires", "grace"]] as const) {
      const v = read(signedSt(2, "e2e-key", { [column]: value }));
      expect(v.misstated, `${column} ${value}`).toEqual([column === "seq" ? 3 : 2]);
      expect(v.statements[1], `${column} ${value}`).toMatchObject({ purpose: "e2e-key", key_id: "2".repeat(32), public_key: "K2", retires: "all" });
    }
    expect(read(signedSt(2, "e2e-off", { key_id: "2".repeat(32) })).misstated).toEqual([2]);
    // a payload that is no statement's, its columns saying nothing but its seq and id
    const shaped = (payload: unknown) => read({ seq: 2, id: "id-2", payload, created_at: 1 }).misstated;
    const k = { seq: 2, id: "id-2", purpose: "e2e-key", key: "K2", key_id: "2".repeat(32), retires: "all" };
    const off = { seq: 2, id: "id-2", purpose: "e2e-off" };
    expect([shaped(k), shaped(JSON.stringify(k)), shaped(off)]).toEqual([[], [], []]);
    for (const payload of ["not json", "[]", { ...k, key_id: undefined }, { ...k, key_id: "A".repeat(32) }, { ...k, key_id: "2".repeat(31) },
      { ...k, key: undefined }, { ...k, retires: "later" }, { ...off, key: "K2" }, { ...off, key_id: "2".repeat(32) },
      { ...off, retires: "all" }, { ...off, purpose: "instruction" }]) {
      expect(shaped(payload), JSON.stringify(payload)).toEqual([2]);
    }
  });

  it("takes who may cancel the change that waits exactly as listed, and no new master key as due while encryption is on", () => {
    const st = signedSt;
    const p = (more: Record<string, unknown>) => K.readView({ pending: { id: "h", change: "rotate", takes_effect_at: 1, ...more } }).pending;
    expect(p({ cancel_by: ["pk-a", 7, "pk-b"], cancel_by_code: true })).toMatchObject({ cancel_by: ["pk-a", "pk-b"], cancel_by_code: true });
    expect(p({ cancel_by_code: "yes" })).toMatchObject({ cancel_by: [], cancel_by_code: false });     // nothing it does not name
    expect(K.readView({ pending: null }).pending).toBeNull();
    expect(K.readView({ rekey_due: true, statements: [st(1, "e2e-key")] }).rekey_due).toBe(false);
    expect(K.readView({ rekey_due: true, statements: [st(1, "e2e-key"), st(2, "e2e-off")] }).rekey_due).toBe(true);
  });

  it("takes what made the change that waits, and what it removes, as said, and nothing it does not say", () => {
    const p = (more: Record<string, unknown>) => K.readView({ pending: { id: "h", change: "revoke", takes_effect_at: 1, ...more } }).pending;
    expect(p({ made_by: "pk-a", removes: ["pk-b", 7], replaces_code: true })).toMatchObject({ made_by: "pk-a", removes: ["pk-b"], replaces_code: true });
    expect(p({ made_by: "recovery" })).toMatchObject({ made_by: "recovery", removes: [], replaces_code: false, by_code: true });   // made by the code
    expect(p({ made_by: 3, removes: "pk-b", replaces_code: "yes" })).toMatchObject({ made_by: null, removes: [], replaces_code: false });
    // whether it is code-backed, and what was asked, as said: a passkey's change sent with the code's proof is
    expect(p({ made_by: "pk-a", by_code: true, body: { remove: ["pk-b"] } })).toMatchObject({ made_by: "pk-a", by_code: true, body: { remove: ["pk-b"] } });
    expect(p({ made_by: "pk-a" })).toMatchObject({ by_code: false, body: null });
    expect(p({ made_by: "pk-a", by_code: "yes", body: ["x"] })).toMatchObject({ by_code: false, body: null });
  });
});

describe("the statements served", () => {
  it("are listed as they were signed before a new one is signed: one with a column that says otherwise is refused before any nonce is asked", async () => {
    await turnedOn("credAAAA");
    server.statements[0].retires = "grace";
    const v = await view();
    expect(v.misstated).toEqual([1]);
    const nonces = posted("/api/ade/link/nonce").length;
    await expect(K.nextLink(v)).rejects.toThrow(K.STATED_OTHERWISE);
    await expect(new K.TurnOff(v, ACCOUNT).prepare()).rejects.toThrow(K.STATED_OTHERWISE);
    expect(posted("/api/ade/link/nonce")).toHaveLength(nonces);
    server.statements[0].retires = "all";
    await expect(new K.TurnOff(await view(), ACCOUNT).prepare()).resolves.toBeUndefined();
  });

  it("are chained from the first before a new one is signed: a gap, a changed prev or a payload changed after it was stated is refused", async () => {
    await turnedOn("credAAAA");
    const r = new K.Rotate(await unlockAs("credAAAA"), await view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    expect(await K.nextLink(await view())).toMatchObject({ seq: 3 });
    const v = await view();
    await expect(K.nextLink({ ...v, statements: [v.statements[1]] })).rejects.toThrow(/do not chain/);
    const changed = { ...v.statements[0], payload: (v.statements[0].payload as string).replace('"retires":"all"', '"retires":"grace"') };
    await expect(K.nextLink({ ...v, statements: [changed, v.statements[1]] })).rejects.toThrow(/do not chain/);
    const spaced = { ...v.statements[0], payload: (v.statements[0].payload as string).replace(":", ": ") };
    await expect(K.nextLink({ ...v, statements: [spaced, v.statements[1]] })).rejects.toThrow(/canonical/);
  });
});

describe("a statement's time", () => {
  const own = 1_800_000_000;                                       // this device's clock, in seconds

  it("is the server's, refused before any touch where it is not a whole second or runs more than five minutes ahead of this device's clock", async () => {
    await turnedOn("credAAAA");
    vi.spyOn(Date, "now").mockReturnValue(own * 1000);
    const touches = auth.ceremonies.length;
    for (const [at, words] of [[own + SERVER_AHEAD + 1, SERVER_AHEAD_WORDS], [own + 0.5, "The server's time is not a time."]] as const) {
      server.now = at;
      await expect(new K.TurnOff(await view(), ACCOUNT).prepare(), String(at)).rejects.toThrow(words);
    }
    expect(auth.ceremonies).toHaveLength(touches);
    expect(posted("/api/ade/e2e/off")).toHaveLength(0);
    server.now = own + SERVER_AHEAD;                               // as far ahead as the server's own skew allows
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    expect(bodyOf("/api/ade/e2e/off").statement.payload).toMatchObject({ created_at: own + SERVER_AHEAD, expires_at: own + SERVER_AHEAD + K.STATEMENT_LIFE });
  });

  it("behind this device's clock is taken: it only shortens how long a link takes the statement as new", async () => {
    await turnedOn("credAAAA");
    vi.spyOn(Date, "now").mockReturnValue((server.now + 86400) * 1000);   // this device a day ahead of the server
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    expect(bodyOf("/api/ade/e2e/off").statement.payload).toMatchObject({ created_at: server.now });
  });
});

describe("the recovery code as a confirmation", () => {
  it("answers the server's challenge with the proof it checks, from the code typed or from the keys it unlocked, for one purpose", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const proof = await K.recoveryProof(ACCOUNT, "rotate", { code: code.toLowerCase() });
    expect(posted("/api/ade/e2e/recovery-proof").at(-1)!.body).toEqual({ purpose: "rotate" });
    expect(Object.keys(proof).sort()).toEqual(["challenge_id", "proof"]);
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    const answer = await r.signAndSend(proof);
    expect(bodyOf("/api/ade/e2e/rotate").recovery_proof).toEqual(proof);
    expect(answer.hold?.takes_effect_at).toBe(server.now + 86400);             // what the code confirms waits a day
    server.pending = null;
    // a proof made for another change is refused
    const other = await K.recoveryProof(ACCOUNT, "revoke", { code });
    const again = new K.Rotate(ring, await view());
    await again.prepare();
    await expect(again.signAndSend(other)).rejects.toThrow(/not the recovery code's proof|expired or was used/);
    // with the keys unlocked by the code, nothing is typed again
    const byCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    const fromRing = await K.recoveryProof(ACCOUNT, "rotate", { ring: byCode });
    const third = new K.Rotate(byCode, await view());
    await third.prepare();
    expect((await third.signAndSend(fromRing)).hold?.takes_effect_at).toBe(server.now + 86400);
    await expect(K.recoveryProof(ACCOUNT, "revoke", { code: "nonsense" })).rejects.toThrow(K.CODE_FORM);
  });
});

describe("who may cancel the change that waits, as the server lists it", () => {
  /** Encryption on with A, and B and C added to it: the recovery code. */
  async function three() {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    await enrolled(await K.follow(ring, await view()), "credCCCC");
    return code;
  }
  const waiting = async () => (await view()).pending!;
  /** A revocation `what`, signed with `id`'s passkey (the page offers it, or a thief forces it), with `proof` beside. */
  async function revokeAs(id: string, what: K.RevokeWhat, proof?: K.RecoveryProof) {
    const r = new K.Revoke(await unlockAs(id), await view(), what);
    await r.prepare();
    auth.choose = () => id;
    return r.signAndSend(proof);
  }
  /** A passkey's touch for cancelling (e2e-cancel, as CancelChange makes one), sent to cancel the change that waits past
   *  what the page offers: the server's answer. */
  async function cancelWith(id: string) {
    const r = await server.fetch(`/api/ade/holds/${server.pending!.id}/cancel`, { method: "POST",
      body: JSON.stringify({ confirmation: await confirmation(id, "e2e-cancel") }) });
    return { status: r.status, detail: (await r.json()).detail };
  }
  const MAY_NOT_CANCEL = { status: 403, detail: CANCEL_REMOVED };

  it("lets the recovery code in force cancel a change that replaces it, beside every passkey it does not remove", async () => {
    const code = await three();
    const fresh = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await revokeAs("credBBBB", { newCode: fresh.code });
    fresh.dispose();
    const p = await waiting();
    expect(p.cancel_by).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    expect(p.cancel_by_code).toBe(true);
    const c = new K.CancelChange(ACCOUNT, await view(), p);
    expect(c.byCode()).toBe(true);
    await c.withCode({ code });
    expect(server.pending).toBeNull();
  });

  it("a stolen passkey's revocation that leaves it alone: one it removes cancels it, or puts its own in its place, which the thief then neither cancels nor replaces", async () => {
    await three();
    const stolen = async () => {
      const fresh = new K.TurnOn(ACCOUNT, false, CLASSICAL);
      const what = { remove: ["pk-credAAAA", "pk-credCCCC"], newCode: fresh.code };
      expect(K.removedMayCancel(await view(), what)).toEqual(["pk-credAAAA", "pk-credCCCC"]);      // as the page warns
      await revokeAs("credBBBB", what);
      fresh.dispose();
    };
    await stolen();
    const p = await waiting();
    expect(p.cancel_by).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    expect(p.cancel_by_code).toBe(true);
    // A cancels it with a touch, the server's list all the browser is offered
    const c = new K.CancelChange(ACCOUNT, await view(), p);
    await c.prepare();
    auth.choose = () => "credAAAA";
    auth.ceremonies.length = 0;
    await c.withPasskey();
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    expect(server.pending).toBeNull();
    // or puts its own revocation, removing B, in its place
    await stolen();
    const replaced = server.pending!.id;
    await revokeAs("credAAAA", { remove: ["pk-credBBBB"] });
    expect(server.pending).toMatchObject({ change: "revoke", madeBy: "pk-credAAAA", removes: ["pk-credBBBB"] });
    expect(server.pending!.id).not.toBe(replaced);
    expect((await waiting()).cancel_by).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    // B neither cancels it nor, signing a revocation itself, puts one in its place; the page offers B neither
    expect(new K.CancelChange(ACCOUNT, await view(), await waiting()).passkeys().map((k) => k.credential_id)).toEqual(["credAAAA", "credCCCC"]);
    expect(await cancelWith("credBBBB")).toEqual(MAY_NOT_CANCEL);
    const thief = new K.Revoke(await unlockAs("credBBBB"), await view(), { remove: ["pk-credAAAA"] });
    await thief.prepare();
    auth.ceremonies.length = 0;
    auth.choose = () => "credBBBB";                                    // forced past what the page offers (credCCCC alone)
    await expect(thief.signAndSend()).rejects.toThrow(/A change waits already/);
    expect(auth.ceremonies[0].allow).toEqual(["credCCCC"]);
    expect(server.pending).toMatchObject({ madeBy: "pk-credAAAA" });
    server.settle();                                                   // A's revocation takes effect
    expect(server.passkeys.map((k) => k.id)).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    expect((await unlockAs("credAAAA")).roster!.credentials.map((k) => k.credential_id)).toEqual(["credAAAA", "credCCCC"]);
  });

  it("a revocation sent with the recovery code's proof that removes a lost passkey: only the code cancels it, and the lost one cannot put its own in its place without the code", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const proof = await K.recoveryProof(ACCOUNT, "revoke", { code });
    expect((await revokeAs("credBBBB", { remove: ["pk-credAAAA"] }, proof)).hold?.takes_effect_at).toBe(server.now + 86400);
    const p = await waiting();
    expect(p).toMatchObject({ made_by: "pk-credBBBB", by_code: true, cancel_by: [], cancel_by_code: true });
    expect(new K.CancelChange(ACCOUNT, await view(), p).passkeys()).toEqual([]);
    expect(await cancelWith("credAAAA")).toEqual(MAY_NOT_CANCEL);
    expect(await cancelWith("credBBBB")).toEqual(MAY_NOT_CANCEL);      // not even the passkey that signed it
    // the lost passkey's revocation would take its place only with the recovery code, which its finder lacks
    const lost = new K.Revoke(await unlockAs("credAAAA"), await view(), { remove: ["pk-credBBBB"] });
    expect(lost.byCode).toBe(true);
    await lost.prepare();
    auth.ceremonies.length = 0;
    await expect(lost.signAndSend()).rejects.toThrow(K.CODE_NEEDED);
    expect(auth.ceremonies).toHaveLength(0);                           // refused before any touch
    await expect(K.codeKey(await view(), ACCOUNT, "A".repeat(32))).rejects.toThrow(K.WRONG_CODE);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(1);
    expect(server.pending?.id).toBe(p.id);
    server.settle();
    expect(server.passkeys.map((k) => k.id)).toEqual(["pk-credBBBB"]);
  });

  it("puts a revocation in the place of a change only the code cancels (one sent with the code's proof) only with the code, in one step, the code's proof asked for after the touch", async () => {
    const code = await three();
    await revokeAs("credBBBB", { remove: ["pk-credAAAA", "pk-credCCCC"] }, await K.recoveryProof(ACCOUNT, "revoke", { code }));
    const thiefs = server.pending!.id;
    const v = await view();
    expect(v.pending).toMatchObject({ by_code: true, cancel_by: [], cancel_by_code: true });
    expect(K.onlyWithCode(v, { remove: ["pk-credBBBB"] })).toBe(true);
    expect(K.onlyWithCode(v, { remove: ["pk-credCCCC"] })).toBe(true);             // no passkey's touch takes its place
    expect(K.onlyWithCode({ ...v, pending: null }, { remove: ["pk-credBBBB"] })).toBe(false);      // with nothing waiting, a passkey's does
    const r = new K.Revoke(await unlockAs("credAAAA"), await view(), { remove: ["pk-credBBBB"] });
    expect(r.byCode).toBe(true);
    await r.prepare();
    await expect(r.signAndSend()).rejects.toThrow(K.CODE_NEEDED);                  // never without the code
    const rk = await K.codeKey(await view(), ACCOUNT, code.toLowerCase());
    const touchedAt: number[] = [];
    const get = navigator.credentials.get.bind(navigator.credentials);
    vi.spyOn(navigator.credentials, "get").mockImplementation(async (o) => { touchedAt.push(server.calls.length); return get(o); });
    auth.choose = () => "credAAAA";
    auth.ceremonies.length = 0;
    const asked = server.calls.length;
    expect((await r.signAndSendWithCode(rk)).hold?.takes_effect_at).toBe(server.now + 86400);    // what the code confirms
    // any passkey that may sign it with the code's proof beside it, as the server takes one: even one it removes
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    const proofAt = server.calls.findIndex((c, i) => i >= asked && c.path === "/api/ade/e2e/recovery-proof");
    expect(proofAt).toBeGreaterThanOrEqual(touchedAt[0]);                          // the touch first, then the proof
    expect(server.calls[proofAt].body).toEqual({ purpose: "revoke" });
    expect(bodyOf("/api/ade/e2e/revoke").recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "revoke", madeBy: "pk-credAAAA", removes: ["pk-credBBBB"], byCode: true });
    expect(server.pending!.id).not.toBe(thiefs);
    // only the code cancels it in turn: B's passkey neither cancels it nor, without the code, puts another in its place
    expect((await waiting()).cancel_by).toEqual([]);
    expect(await cancelWith("credBBBB")).toEqual(MAY_NOT_CANCEL);
    await expect(revokeAs("credBBBB", { remove: ["pk-credAAAA", "pk-credCCCC"] })).rejects.toThrow(K.CODE_NEEDED);
    server.settle();
    expect(server.passkeys.map((k) => k.id)).toEqual(["pk-credAAAA", "pk-credCCCC"]);
  });

  it("leaves what a refused change was asked with unused, as the server's transaction does: its statement's nonce, its proof, its confirmation", async () => {
    const code = await three();
    await revokeAs("credAAAA", { remove: ["pk-credBBBB"] });                    // the owner's revocation waits
    // B's revocation, refused under (v), leaves its statement's nonce: once nothing waits, the same statement is taken
    const thief = new K.Revoke(await unlockAs("credBBBB"), await view(), { remove: ["pk-credAAAA"] });
    await thief.prepare();
    auth.choose = () => "credBBBB";
    await expect(thief.signAndSend()).rejects.toThrow(/A change waits already/);
    server.pending = null;
    expect((await thief.signAndSend()).hold).toBeTruthy();
    server.pending = null;
    // a wrong proof leaves the challenge it answers, and the statement, unused: the right one is then taken
    const ring = await unlockAs("credAAAA");
    const rotate = new K.Rotate(ring, await view());
    await rotate.prepare();
    const proof = await K.recoveryProof(ACCOUNT, "rotate", { code });
    const wrong = { ...proof, proof: (proof.proof[0] === "A" ? "B" : "A") + proof.proof.slice(1) };
    await expect(rotate.signAndSend(wrong)).rejects.toThrow(/not the recovery code's proof/);
    expect((await rotate.signAndSend(proof)).hold?.takes_effect_at).toBe(server.now + 86400);
    server.pending = null;
    // once a change is placed, what it was asked with is used up: its statement's nonce, and its proof
    await expect(rotate.signAndSend(proof)).rejects.toThrow(/nonce is not one this server gave/);
    const again = new K.Rotate(ring, await view());
    await again.prepare();
    await expect(again.signAndSend(proof)).rejects.toThrow(/expired or was used/);
    // a confirmation given with a change refused while another waits stays good for it
    passkey("credDDDD");
    const v = await view();
    const enrol = new K.Enrol(await K.follow(ring, v), v, v.passkeys.find((p) => p.credential_id === "credDDDD")!);
    await enrol.prepare();
    auth.choose = () => "credDDDD";
    await enrol.firstTouch();
    await enrol.secondTouch();
    // a change the code made waits since: only the code cancels it, so A's confirmation cannot put this in its place
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true };
    auth.choose = () => "credAAAA";
    await expect(enrol.confirmAndSend()).rejects.toThrow(/waits already/);
    server.pending = null;
    expect((await enrol.send()).hold).toBeTruthy();
    server.pending = null;
    await expect(enrol.send()).rejects.toThrow(/That confirmation has expired or was used/);          // used up then
  });

  it("does not put a revocation in a waiting one's place where neither a passkey that may sign it nor the code could cancel that one", async () => {
    const code = await three();
    // one sent with the code's proof, which no passkey cancels, and a server that says the code does not either
    await revokeAs("credBBBB", { remove: ["pk-credAAAA", "pk-credCCCC"] }, await K.recoveryProof(ACCOUNT, "revoke", { code }));
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: { ...v.pending, cancel_by_code: false } }];
    };
    const v = await view();
    expect(v.pending).toMatchObject({ cancel_by: [], cancel_by_code: false });
    const r = new K.Revoke(await unlockAs("credAAAA"), v, { remove: ["pk-credBBBB"] });
    expect(r.byCode).toBe(false);
    const asked = server.calls.length;
    await expect(r.prepare()).rejects.toThrow(K.WAITING_CHANGE);
    expect(server.calls.slice(asked).map((c) => c.path)).toEqual([]);              // nothing asked of the server for it
  });

  it("names, before a revocation is signed, the passkeys it removes that could still cancel it: when it leaves no passkey but its signer, and it is not sent with the code's proof", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    passkey("credCCCC");                                               // the account's, not added to encryption
    const v = await view();
    const code = C.random(20);
    expect(K.removedMayCancel(v, { remove: ["pk-credAAAA", "pk-credCCCC"], newCode: code })).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    expect(K.removedMayCancel(v, { remove: ["pk-credAAAA", "pk-credCCCC"] })).toEqual(["pk-credAAAA", "pk-credCCCC"]);   // the code kept changes nothing
    expect(K.removedMayCancel(v, { remove: ["pk-credAAAA"], newCode: code })).toEqual([]);       // C stays, so B is not alone
    expect(K.removedMayCancel(v, { remove: ["pk-credAAAA"] })).toEqual([]);
    expect(K.removedMayCancel(v, { newCode: code })).toEqual([]);                                // it removes none
    expect(K.removedMayCancel(v, { remove: ["pk-credAAAA", "pk-credCCCC"] }, true)).toEqual([]);   // the code's proof with it: only the code
    passkey("credHHHH", "key", "hardware");
    const w = await view();
    expect(K.removedMayCancel(w, { highSecurity: true, newCode: code })).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    expect(K.removedMayCancel(w, { highSecurity: true })).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    expect(K.removedMayCancel(w, { highSecurity: true }, true)).toEqual([]);
    expect(K.removedMayCancel({ ...w, high_security: true }, { remove: ["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"], newCode: code })).toEqual([]);
  });

  it("never says a new master key is due while encryption is on: nothing is turned on again then", async () => {
    const { ring } = await turnedOn("credAAAA");
    server.rekey = true;
    expect(server.view().rekey_due).toBe(false);                      // as the server serves it, while on
    const v = await view();
    expect(v.rekey_due).toBe(false);
    const gone = { ...v, credentials: [] };                            // a roster that names a credential no longer listed
    expect(K.rekeyDue(ring, gone)).toBe(false);
    expect(K.rekeyDue(ring, { ...gone, on: false })).toBe(true);
    await turnedOff();
    expect((await view()).rekey_due).toBe(true);
  });
});

describe("who makes a change, as the server takes one", () => {
  /** A server whose view serves what `change` makes of the change that waits. */
  function served(change: (pending: Record<string, unknown>) => Record<string, unknown>) {
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: change({ ...(v.pending ?? {}) }) }];
    };
  }
  /** A change the recovery code made, waiting: only the code cancels it. */
  const codeBacked = () => ({ id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true });

  it("a rotation: signed by a passkey that unlocks the keys and stays; with the code's proof, asked for after the touch, by any passkey of the account, which then waits a day and only the code cancels", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    passkey("credCCCC");                                               // the account's, not added to encryption
    server.rotateEvery = 0;
    const unlocked = await K.follow(ring, await view());
    let r = new K.Rotate(unlocked, await view());
    expect(r.byCode).toBe(false);
    await r.prepare();
    auth.ceremonies.length = 0;
    auth.choose = () => "credBBBB";
    expect((await r.signAndSend()).hold?.takes_effect_at).toBe(server.now + 180);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB"]);           // never C, which unlocks nothing
    expect(server.pending).toMatchObject({ change: "rotate", madeBy: "pk-credBBBB", byCode: false });
    server.pending = null;
    r = new K.Rotate(unlocked, await view());
    await r.prepare();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    auth.ceremonies.length = 0;
    auth.choose = () => "credCCCC";
    const asked = server.calls.length;
    expect((await r.signAndSendWithCode(rk)).hold?.takes_effect_at).toBe(server.now + 86400);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    expect(server.calls.slice(asked).map((c) => c.path)).toEqual(["/api/ade/e2e/recovery-proof", "/api/ade/e2e/rotate"]);
    expect(bodyOf("/api/ade/e2e/recovery-proof")).toEqual({ purpose: "rotate" });
    expect(bodyOf("/api/ade/e2e/rotate").recovery_proof).toBeTruthy();
    expect((await view()).pending).toMatchObject({ change: "rotate", made_by: "pk-credCCCC", by_code: true, cancel_by: [], cancel_by_code: true });
  });

  it("a rotation goes only with the code's proof while a change the code made waits: refused before any touch without it, then in that change's place; refused before anything is asked where the code could not either", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    server.rotateEvery = 0;
    server.pending = codeBacked();
    const v = await view();
    const r = new K.Rotate(ring, v);
    expect(r.byCode).toBe(true);
    await r.prepare();
    auth.ceremonies.length = 0;
    await expect(r.signAndSend()).rejects.toThrow(K.CODE_NEEDED);
    expect(auth.ceremonies).toHaveLength(0);
    expect(posted("/api/ade/e2e/rotate")).toHaveLength(0);
    await r.signAndSendWithCode(await K.codeKey(v, ACCOUNT, code));
    expect(server.pending).toMatchObject({ change: "rotate", madeBy: "pk-credAAAA", byCode: true });
    expect(server.pending!.id).not.toBe("hold-w");
    served((p) => ({ ...p, cancel_by: [], cancel_by_code: false }));
    const w = await view();
    const asked = server.calls.length;
    await expect(new K.Rotate(ring, w).prepare()).rejects.toThrow(K.WAITING_CHANGE);
    expect(server.calls.slice(asked)).toEqual([]);
  });

  it("turning it off: signed by a passkey that unlocks the keys and stays, or with the code's proof by any passkey of the account; only with the code while a change the code made waits", async () => {
    const { code } = await turnedOn("credAAAA");
    passkey("credCCCC");
    let off = new K.TurnOff(await view(), ACCOUNT);
    expect(off.byCode).toBe(false);
    await off.prepare();
    auth.ceremonies.length = 0;
    expect((await off.signAndSend()).hold?.takes_effect_at).toBe(server.now + 86400);      // turning it off waits a day
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);
    server.pending = null;
    off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    auth.ceremonies.length = 0;
    auth.choose = () => "credCCCC";
    const asked = server.calls.length;
    await off.signAndSendWithCode(await K.codeKey(await view(), ACCOUNT, code));
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credCCCC"]);
    expect(server.calls.slice(asked).map((c) => c.path).filter((p) => p !== "/api/ade/e2e")).toEqual(["/api/ade/e2e/recovery-proof", "/api/ade/e2e/off"]);
    expect(bodyOf("/api/ade/e2e/recovery-proof")).toEqual({ purpose: "off" });
    expect(server.pending).toMatchObject({ change: "off", madeBy: "pk-credCCCC", byCode: true });
    const again = new K.TurnOff(await view(), ACCOUNT);
    expect(again.byCode).toBe(true);
    await again.prepare();
    auth.ceremonies.length = 0;
    await expect(again.signAndSend()).rejects.toThrow(K.CODE_NEEDED);
    expect(auth.ceremonies).toHaveLength(0);
  });

  it("turning it on, the first time or again, offers only the passkeys that may take the waiting change's place, as the server lists them, and takes no code's proof: refused before any touch where none may", async () => {
    passkey("credAAAA");
    passkey("credBBBB");
    const waits = (cancelBy: string[]) => served(() => ({ id: "hold-x", change: "turn-on", takes_effect_at: server.now + 180,
      cancel_by: cancelBy, cancel_by_code: true, made_by: "pk-credBBBB" }));
    waits(["pk-credBBBB"]);
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    auth.ceremonies.length = 0;
    await t.firstTouch();
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);
    waits([]);
    auth.ceremonies.length = 0;
    await expect(new K.TurnOn(ACCOUNT, false, CLASSICAL).prepare(await view())).rejects.toThrow(K.WAITING_CHANGE);     // the code is no way for this one
    expect(auth.ceremonies).toHaveLength(0);
    server.answer = null;
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    await turnedOff();
    const unlocked = await unlockAs("credAAAA");
    waits(["pk-credBBBB"]);
    const again = new K.TurnOnAgain(unlocked, await view());
    await again.prepare();
    auth.ceremonies.length = 0;
    auth.choose = prefer("credBBBB");
    await again.signAndSend(true);
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);
    waits([]);
    const v = await view();
    const asked = server.calls.length;
    await expect(new K.TurnOnAgain(unlocked, v).prepare()).rejects.toThrow(K.WAITING_CHANGE);
    expect(server.calls.slice(asked)).toEqual([]);
  });

  /** Encryption on with A, and B and C added to it, every anchor then older than the master key (a revocation that
   *  removes none): the keys as A unlocks them, and the recovery code. */
  async function renewable() {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    await enrolled(await K.follow(ring, await view()), "credCCCC");
    await revoked(await K.follow(ring, await view()), {});
    return { ring: await unlockAs("credAAAA"), code };
  }

  it("renewing anchors: each renewed record but the confirming passkey's carries its own passkey's confirmation, and the first touched that may take the waiting change's place confirms it", async () => {
    const { ring } = await renewable();
    const renew = new K.RenewAnchors(ring, await view());
    expect(renew.byCode).toBe(false);
    expect(renew.mayConfirm()).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    await renew.prepare();
    for (const id of ["credBBBB", "credAAAA", "credCCCC"]) {
      auth.choose = () => id;
      await renew.touch(id);
    }
    expect(renew.maker()).toBe("pk-credBBBB");
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(body.confirmation).toBe(renew.confirmations.get("pk-credBBBB"));
    expect(body.anchors.map((a: Body) => [a.passkey_id, a.confirmation ?? null])).toEqual([["pk-credBBBB", null],
      ["pk-credAAAA", renew.confirmations.get("pk-credAAAA")], ["pk-credCCCC", renew.confirmations.get("pk-credCCCC")]]);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "pk-credBBBB", byCode: false });
    server.settle();
    for (const id of ["credAAAA", "credBBBB", "credCCCC"]) expect((await unlockAs(id)).via.anchor).toBe(ring.master.id);
  });

  it("renewing anchors where no passkey touched may take the waiting change's place: one that may is asked for, or the recovery code's proof, given, confirms it", async () => {
    const { ring, code } = await renewable();
    const r = new K.Revoke(ring, await view(), { remove: ["pk-credBBBB"] });            // A's revocation of B waits
    await r.prepare();
    auth.choose = () => "credAAAA";
    await r.signAndSend();
    const v = await view();
    expect(v.pending).toMatchObject({ cancel_by: ["pk-credAAAA", "pk-credCCCC"] });
    const renew = new K.RenewAnchors(ring, v);
    expect(renew.mayConfirm()).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    await renew.prepare();
    auth.choose = () => "credBBBB";
    await renew.touch("credBBBB");
    expect(renew.maker()).toBeNull();
    await expect(renew.send()).rejects.toThrow(K.TOUCH_ONE_THAT_MAY);
    expect(posted("/api/ade/e2e/anchors")).toHaveLength(0);
    await renew.renewCode(code);                                       // the code given, it confirms it
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors.map((a: Body) => [a.passkey_id, a.confirmation])).toEqual([["pk-credBBBB", renew.confirmations.get("pk-credBBBB")]]);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
  });

  it("renewing anchors while a change the code made waits: the code's proof confirms it, every renewed record with its own passkey's confirmation", async () => {
    const { ring, code } = await renewable();
    server.pending = codeBacked();
    const renew = new K.RenewAnchors(ring, await view());
    expect(renew.byCode).toBe(true);
    expect(renew.mayConfirm()).toEqual([]);
    await renew.prepare();
    for (const id of ["credAAAA", "credBBBB"]) {
      auth.choose = () => id;
      await renew.touch(id);
    }
    expect(renew.maker()).toBeNull();
    await expect(renew.send()).rejects.toThrow(K.CODE_NEEDED);
    expect(posted("/api/ade/e2e/anchors")).toHaveLength(0);
    await renew.renewCode(code);
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors.map((a: Body) => [a.passkey_id, a.confirmation])).toEqual([
      ["pk-credAAAA", renew.confirmations.get("pk-credAAAA")], ["pk-credBBBB", renew.confirmations.get("pk-credBBBB")]]);
    expect(bodyOf("/api/ade/e2e/recovery-proof")).toEqual({ purpose: "anchors" });
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
    expect(server.pending!.id).not.toBe("hold-w");
  });

  it("renewing the recovery code's anchor goes only with the code's proof, though a passkey that may confirm the change is touched: it waits the code's hold, and only the code cancels it", async () => {
    const { ring, code } = await renewable();
    const renew = new K.RenewAnchors(ring, await view());
    await renew.prepare();
    auth.choose = () => "credAAAA";
    await renew.touch("credAAAA");
    expect(renew.confirmedBy()).toBe("pk-credAAAA");                 // the passkeys' anchors alone: A confirms them
    await renew.renewCode(code);
    expect(renew.recovery).not.toBeNull();
    expect(renew.maker()).toBe("pk-credAAAA");
    expect(renew.confirmedBy()).toBeNull();                           // with the code's anchor: the code's proof
    await renew.send();
    const body = bodyOf("/api/ade/e2e/anchors");
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors.map((a: Body) => [a.passkey_id, a.confirmation])).toEqual([["pk-credAAAA", renew.confirmations.get("pk-credAAAA")]]);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
    expect(server.pending!.takes_effect_at - server.now).toBe(server.delays.recovery);
    expect(server.view().pending.cancel_by).toEqual([]);
    server.settle();
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).via.anchor).toBe(ring.master.id);
  });

  it("renewing the recovery code's anchor where a passkey may take the waiting change's place but the code may not: refused, a passkey's touch or not, and nothing sent", async () => {
    const { ring, code } = await renewable();
    const r = new K.Revoke(ring, await view(), { remove: ["pk-credBBBB"] });            // A's revocation of B waits
    await r.prepare();
    auth.choose = () => "credAAAA";
    await r.signAndSend();
    served((p) => ({ ...p, cancel_by_code: false }));
    const renew = new K.RenewAnchors(ring, await view());
    expect(renew.mayConfirm()).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    await renew.prepare();
    await renew.renewCode(code);
    await expect(renew.send()).rejects.toThrow(K.WAITING_CHANGE);     // touching a passkey would not help
    auth.choose = () => "credAAAA";
    await renew.touch("credAAAA");
    await expect(renew.send()).rejects.toThrow(K.WAITING_CHANGE);     // a passkey's confirmation does not cover the code's anchor
    expect(posted("/api/ade/e2e/anchors")).toHaveLength(0);
  });

  it("renewing anchors is refused before any touch where neither a passkey nor the code may take the waiting change's place", async () => {
    const { ring } = await renewable();
    server.pending = codeBacked();
    served((p) => ({ ...p, cancel_by_code: false }));
    const renew = new K.RenewAnchors(ring, await view());
    await expect(renew.prepare()).rejects.toThrow(K.WAITING_CHANGE);
    expect(posted("/api/account/confirm/options").filter((c) => c.body?.purpose === "e2e-anchor-touch")).toHaveLength(0);
  });

  it("turning high security on with the code's proof is signed by a hardware key alone, as the server takes it", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    passkey("credHHHH", "key", "hardware");
    await enrolled(ring, "credHHHH", "hardware");
    const unlocked = await K.follow(ring, await view());
    server.pending = codeBacked();
    const v = await view();
    const r = new K.Revoke(unlocked, v, { highSecurity: true });
    expect(r.byCode).toBe(true);
    await r.prepare();
    auth.ceremonies.length = 0;
    auth.choose = (allowed) => allowed[0];
    await r.signAndSendWithCode(await K.codeKey(v, ACCOUNT, code));
    expect(auth.ceremonies[0].allow).toEqual(["credHHHH"]);
    expect(server.pending).toMatchObject({ change: "revoke", madeBy: "pk-credHHHH", byCode: true });
  });

  it("goes with the recovery code's proof only where the account has a code that may take the waiting change's place", async () => {
    await turnedOn("credAAAA");
    const v = await view();
    expect(K.codeMakes(v)).toBe(true);
    expect(K.codeMakes({ ...v, recovery: null })).toBe(false);             // a server that lists no recovery key
    server.pending = codeBacked();
    expect(K.codeMakes(await view())).toBe(true);
    served((p) => ({ ...p, cancel_by_code: false }));
    expect(K.codeMakes(await view())).toBe(false);
  });

  it("offers to sign only the credentials the server's nonce names, where it names any", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    server.rotateEvery = 0;
    const unlocked = await K.follow(ring, await view());
    let named: string[] = ["credBBBB"];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const answer = await server.fetch(url, init);
      if (url !== "/api/ade/link/nonce") return answer;
      return new Response(JSON.stringify({ ...(await answer.json()), credentials: named }), { status: answer.status });
    });
    let r = new K.Rotate(unlocked, await view());
    await r.prepare();
    auth.ceremonies.length = 0;
    auth.choose = (allowed) => allowed[0];
    await r.signAndSend();
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);              // not credAAAA, which the page may sign with too
    server.pending = null;
    named = ["credXXXX"];                                                 // none the page may sign with
    auth.ceremonies.length = 0;
    await expect(new K.Rotate(unlocked, await view()).prepare()).rejects.toThrow(K.NONE_TO_SIGN);
    expect(auth.ceremonies).toHaveLength(0);
  });

  it("turning high security on where more than one hardware key stays waits a change's hold: it removes the passkeys it leaves out", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credHHHH", "key", "hardware");
    await enrolled(ring, "credHHHH", "hardware");
    passkey("credJJJJ", "key two", "hardware");
    await enrolled(await K.follow(ring, await view()), "credJJJJ", "hardware");
    const unlocked = await K.follow(ring, await view());
    const v = await view();
    expect(K.revocationHold(v, { highSecurity: true }, false)).toBe("change");
    const r = new K.Revoke(unlocked, v, { highSecurity: true });
    await r.prepare();
    auth.choose = (allowed) => allowed[0];
    expect((await r.signAndSend()).hold?.takes_effect_at).toBe(server.now + 180);
  });

  it("says, before a revocation is signed, which hold it waits on, as the server then holds it", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    await enrolled(await K.follow(ring, await view()), "credCCCC");
    passkey("credHHHH", "key", "hardware");
    await enrolled(await K.follow(ring, await view()), "credHHHH", "hardware");
    const unlocked = await K.follow(ring, await view());
    const v = await view();
    expect(v.holds).toEqual({ change: 180, recovery: 86400, long: 86400 });
    const cases: [K.RevokeWhat, boolean, "change" | "long" | "recovery"][] = [
      [{ remove: ["pk-credBBBB"] }, false, "change"],
      [{}, false, "long"],                                                         // it removes no passkey
      [{ remove: ["pk-credBBBB"], newCode: C.random(20) }, false, "long"],          // a new recovery code
      [{ remove: ["pk-credBBBB", "pk-credCCCC", "pk-credHHHH"] }, false, "long"],   // only its signer stays
      [{ highSecurity: true }, false, "long"],                                     // the hardware key alone stays
      [{ remove: ["pk-credBBBB"] }, true, "recovery"],
    ];
    for (const [what, withCode, hold] of cases) {
      expect(K.revocationHold(v, what, withCode), JSON.stringify(Object.keys(what))).toBe(hold);
      const r = new K.Revoke(unlocked, v, what);
      await r.prepare();
      auth.choose = (allowed) => (allowed.includes("credHHHH") ? "credHHHH" : allowed.includes("credAAAA") ? "credAAAA" : allowed[0]);
      const answer = withCode ? await r.signAndSendWithCode(await K.codeKey(v, ACCOUNT, code)) : await r.signAndSend();
      expect(answer.hold?.takes_effect_at, JSON.stringify(what)).toBe(server.now + v.holds![hold]!);
      server.pending = null;
    }
    expect(K.revocationHold(v, { remove: ["pk-credBBBB"] }, false)).toBe("change");
    expect(K.revocationHold({ ...v, credentials: v.credentials.filter((c) => c.passkey_id === "pk-credAAAA") }, { remove: ["pk-credBBBB"] }, false)).toBe("change");   // the signer was alone before too
  });

  it("renewing anchors: a touch whose challenge could not be fetched says it is being prepared, fetches it again, and goes once it is here", async () => {
    const { ring } = await renewable();
    let down = true;
    server.answer = (call) => (down && call.path === "/api/account/confirm/options" ? [503, { detail: "Not now." }] : null);
    const renew = new K.RenewAnchors(ring, await view());
    await renew.prepare();
    down = false;
    auth.ceremonies.length = 0;
    await expect(renew.touch("credAAAA")).rejects.toThrow(K.BEING_PREPARED);
    expect(auth.ceremonies).toHaveLength(0);
    await vi.waitFor(() => expect((renew as unknown as { ceremony: unknown }).ceremony).toBeTruthy());
    auth.choose = () => "credAAAA";
    await renew.touch("credAAAA");
    expect(renew.rows.map((r) => r.passkey_id)).toEqual(["pk-credAAAA"]);
  });
});

describe("the change that waits, checked with this tab's keys", () => {
  /** The body of the change that waits as the test server keeps it, which GET serves. */
  const kept = () => server.pending!.body as Body;
  const copy = (b: Body): Body => JSON.parse(JSON.stringify(b));
  const check = async (ring: K.Ring) => K.checkWaiting(ring, await view());
  const OPENS = { state: "opens", why: K.WAITING_OPENS };
  const BROKEN = { state: "broken", why: K.WAITING_BROKEN };
  /** Each tampering of the body, one at a time, the body put back after each: every one is broken. */
  async function eachBroken(ring: K.Ring, cases: [string, (b: Body) => void | Promise<void>][]) {
    const body = copy(kept());
    for (const [what, tamper] of cases) {
      await tamper(kept());
      expect(await check(ring), what).toEqual(BROKEN);
      server.pending!.body = copy(body);
    }
    expect(await check(ring)).toEqual(OPENS);
  }
  /** A credential's public key as the server lists it. */
  const credentialKey = (id: string) => C.unb64u(server.credentials.find((c) => c.credential_id === id)!.public_key)!;
  /** An envelope with its header's key_id written as `key`'s, the rest as it was: a name a sealer may write falsely. */
  const headed = async (sealed: string, key: Uint8Array) => {
    const body = C.envelopeBytes(sealed)!;
    body.set(await C.keyIdBytes(key), 1);
    return C.PREFIX + C.b64u(body);
  };
  /** The new master key a waiting change puts in place, opened as one who holds the recovery code. */
  async function waitingMaster(code: string) {
    const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
    return C.masterOf(await C.openGrant(rk, RK, kept().grants.recovery, ACCOUNT, "recovery", kept().mk_id), ACCOUNT);
  }

  it("a rotation: its content key opens under the master key this tab holds, to the key it names, which its statement states; any part that does not is broken", async () => {
    const { ring } = await turnedOn("credAAAA");
    server.rotateEvery = 0;
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    await r.signAndSend();
    expect(server.pending?.change).toBe("rotate");
    expect(await check(ring)).toEqual(OPENS);
    const other = await C.newPair();
    const otherId = await C.keyIdOf(other.pub);
    const third = await C.newPair();
    const stating = (b: Body, key: string, keyId: string) => {
      b.statement = { ...b.statement, payload: JSON.stringify({ ...JSON.parse(b.statement.payload), key, key_id: keyId }) };
    };
    await eachBroken(ring, [
      ["a wrap that does not open", (b) => { b.content_key.wrapped = C.b64u(C.random(60)); }],
      ["under another master key", (b) => { b.content_key.mk_id = "0".repeat(32); }],
      ["a key_id that is not its key's", (b) => { b.content_key.key_id = otherId; }],
      ["another key named, the wrap of the first", (b) => {
        b.content_key = { ...b.content_key, public_key: C.b64u(other.pub), key_id: otherId };
        stating(b, C.b64u(other.pub), otherId);
      }],
      ["the wrap of another key, under the key_id named", async (b) => {
        b.content_key = { ...b.content_key, public_key: C.b64u(other.pub), key_id: otherId,
          wrapped: await C.wrapContentKey(ring.master, third.priv, otherId, ACCOUNT) };
        stating(b, C.b64u(other.pub), otherId);
      }],
      ["another key stated", (b) => { stating(b, C.b64u(other.pub), otherId); }],
      ["another key stated under the key_id named", (b) => { stating(b, C.b64u(other.pub), b.content_key.key_id); }],
      ["a key_id that is not its key's, all else made to agree with it", async (b) => {
        const fresh = await C.newPair();
        b.content_key = { ...b.content_key, public_key: C.b64u(fresh.pub), key_id: otherId,
          wrapped: await C.wrapContentKey(ring.master, fresh.priv, otherId, ACCOUNT) };
        stating(b, C.b64u(fresh.pub), otherId);
      }],
      ["a statement whose payload is not JSON", (b) => { b.statement.payload = "{"; }],
      ["no content key", (b) => { delete b.content_key; }],
    ]);
  });

  it("a revocation: a tab whose passkey stays opens the new master key by its grant, the chain to the key it holds, every content key it holds wrapped again, and the roster; a tab the recovery code unlocked opens it too; any part that does not is broken", async () => {
    const { ring: first, code } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    const a = await unlockAs("credAAAA");
    const withCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    const r = new K.Revoke(a, await view(), { remove: ["pk-credBBBB"] });
    await r.prepare();
    auth.choose = prefer("credAAAA");
    await r.signAndSend();
    expect(server.pending?.change).toBe("revoke");
    expect(await check(a)).toEqual(OPENS);
    expect(await check(withCode)).toEqual(OPENS);
    const next = await waitingMaster(code);
    const roster = await C.openRoster(next, kept().roster, ACCOUNT);
    const other = await C.newPair();
    const third = await C.newPair();
    const [keyId] = [...a.keys.keys()];
    await eachBroken(a, [
      ["another master key named", (b) => { b.mk_id = "0".repeat(32); }],
      ["its grant sealed to another key", async (b) => { b.grants.credAAAA = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credAAAA", b.mk_id); }],
      ["its grant of another master key", async (b) => { b.grants.credAAAA = await C.sealGrant(credentialKey("credAAAA"), C.random(32), ACCOUNT, "credAAAA", b.mk_id); }],
      ["the key it replaces not this tab's", (b) => { b.previous = "0".repeat(32); }],
      ["a chain that does not open", (b) => { b.chain = C.b64u(C.random(80)); }],
      ["a chain to another key", async (b) => { b.chain = await C.sealChain(next, C.random(32), a.master.id, ACCOUNT); }],
      ["a new content key that does not open", (b) => { b.content_key.wrapped = C.b64u(C.random(60)); }],
      ["a content key this tab holds left out", (b) => { delete b.content_keys[keyId]; }],
      ["a content key this tab holds wrapped again as another", async (b) => { b.content_keys[keyId] = await C.wrapContentKey(next, third.priv, keyId, ACCOUNT); }],
      ["a roster that does not open", (b) => { b.roster = C.b64u(C.random(80)); }],
      ["a roster naming another recovery key", async (b) => { b.roster = await C.sealRoster(next, { ...roster, recovery: C.b64u(other.pub) }, ACCOUNT); }],
      ["a roster without this tab's credential", async (b) => { b.roster = await C.sealRoster(next, { ...roster, credentials: [] }, ACCOUNT); }],
      ["no roster", (b) => { delete b.roster; }],
      ["no chain", (b) => { delete b.chain; }],
      // every part made under another master key, the grant sealed with the one named: only the grant's key tells
      ["a body made whole under another master key than it names", async (b) => {
        const otherBytes = C.random(32);
        const other2 = await C.masterOf(otherBytes, ACCOUNT);
        b.grants.credAAAA = await C.sealGrant(credentialKey("credAAAA"), otherBytes, ACCOUNT, "credAAAA", b.mk_id);
        b.chain = await C.sealChain(other2, await masterBytes(code), a.master.id, ACCOUNT);
        const pair = await C.newPair();
        const pairId = await C.keyIdOf(pair.pub);
        b.content_key = { key_id: pairId, public_key: C.b64u(pair.pub), mk_id: other2.id, wrapped: await C.wrapContentKey(other2, pair.priv, pairId, ACCOUNT) };
        b.statement = { ...b.statement, payload: JSON.stringify({ ...JSON.parse(b.statement.payload), key: C.b64u(pair.pub), key_id: pairId }) };
        for (const row of server.contentKeys.filter((k) => a.keys.has(k.key_id))) {
          const ek = await C.unwrapContentKey(a.master, row.wrapped, row.key_id, ACCOUNT, true);
          b.content_keys[row.key_id] = await C.wrapContentKey(other2, ek, row.key_id, ACCOUNT);
        }
        b.roster = await C.sealRoster(other2, roster, ACCOUNT);
      }],
    ]);
    // a content key it leaves as it is, as abandon names it, is not wrapped again: no part of the check
    kept().abandon = [keyId];
    delete kept().content_keys[keyId];
    expect(await check(a)).toEqual(OPENS);
  });

  it("a revocation another passkey made: its roster names every credential that stays with the key the server lists, this tab's with its own, and nothing else, and each grant names the key the roster names for its recipient; any part that does not is broken", async () => {
    const { ring: first, code } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    await enrolled(await K.follow(first, await view()), "credCCCC");
    const a = await unlockAs("credAAAA");
    const tablet = await unlockAs("credCCCC");
    // the tablet's revocation, which removes no passkey
    const r = new K.Revoke(tablet, await view(), {});
    await r.prepare();
    auth.choose = prefer("credCCCC");
    await r.signAndSend();
    expect(server.view().pending).toMatchObject({ change: "revoke", removes: [] });
    expect(await check(a)).toEqual(OPENS);
    const next = await waitingMaster(code);
    const roster = await C.openRoster(next, kept().roster, ACCOUNT);
    const resealed = (rr: C.Roster) => C.sealRoster(next, rr, ACCOUNT);
    const other = await C.newPair();
    const naming = (id: string, change: Partial<C.Roster["credentials"][number]>) =>
      resealed({ ...roster, credentials: roster.credentials.map((c) => (c.credential_id === id ? { ...c, ...change } : c)) });
    const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
    const nextBytes = await C.openGrant(rk, RK, kept().grants.recovery, ACCOUNT, "recovery", kept().mk_id);
    await eachBroken(a, [
      ["the recovery key's grant sealed to another key", async (b) => { b.grants.recovery = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "recovery", b.mk_id); }],
      ["the recovery key's grant sealed to another key, its envelope naming the recovery key", async (b) => {
        b.grants.recovery = await headed(await C.sealGrant(other.pub, nextBytes, ACCOUNT, "recovery", b.mk_id), RK);
      }],
      ["another passkey's grant sealed to another key, its envelope naming that passkey's key", async (b) => {
        b.grants.credBBBB = await headed(await C.sealGrant(other.pub, nextBytes, ACCOUNT, "credBBBB", b.mk_id), credentialKey("credBBBB"));
      }],
      ["the recovery key's grant of the same master key sealed with a random ephemeral key", async (b) => {
        b.grants.recovery = await C.sealEnvelope(RK, nextBytes, C.AAD.grant(ACCOUNT, "recovery", b.mk_id));
      }],
      ["another passkey's grant sealed to another key", async (b) => { b.grants.credBBBB = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credBBBB", b.mk_id); }],
      ["a grant that is no envelope", (b) => { b.grants.credBBBB = "sealed"; }],
      ["a grant left out", (b) => { delete b.grants.credBBBB; }],
      ["a grant to a credential the roster does not name", async (b) => { b.grants.credZZZZ = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credZZZZ", b.mk_id); }],
      ["this tab's credential named with another key", async (b) => { b.roster = await naming("credAAAA", { public_key: C.b64u(other.pub) }); }],
      ["this tab's credential named under another passkey", async (b) => { b.roster = await naming("credAAAA", { passkey_id: "pk-credBBBB" }); }],
      ["another credential named under another passkey", async (b) => { b.roster = await naming("credBBBB", { passkey_id: "pk-credCCCC" }); }],
      ["another credential named with another key, its grant sealed to that key", async (b) => {
        b.roster = await naming("credBBBB", { public_key: C.b64u(other.pub) });
        b.grants.credBBBB = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credBBBB", b.mk_id);
      }],
      ["a credential that stays left out of the roster", async (b) => { b.roster = await resealed({ ...roster, credentials: roster.credentials.filter((c) => c.credential_id !== "credBBBB") }); }],
      ["a credential the server does not list named too", async (b) => {
        b.roster = await resealed({ ...roster, credentials: [...roster.credentials, { credential_id: "credZZZZ", passkey_id: "pk-credZZZZ", public_key: C.b64u(other.pub) }] });
      }],
      ["a credential the server does not list named too, with a grant sealed to it", async (b) => {
        b.roster = await resealed({ ...roster, credentials: [...roster.credentials, { credential_id: "credZZZZ", passkey_id: "pk-credZZZZ", public_key: C.b64u(other.pub) }] });
        b.grants.credZZZZ = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credZZZZ", b.mk_id);
      }],
    ]);
    // a server that lists this tab's credential under another passkey, as the roster names it: broken all the same,
    // by the passkey this tab was unlocked with
    const listed = server.credentials.find((c) => c.credential_id === "credAAAA")!;
    kept().roster = await naming("credAAAA", { passkey_id: "pk-credBBBB" });
    listed.passkey_id = "pk-credBBBB";
    expect(await check(a)).toEqual(BROKEN);
    listed.passkey_id = "pk-credAAAA";
    // a credential the server lists under a passkey the account does not have does not stay: the change opens without
    // it, and a roster that names it, with a grant sealed to it, is broken
    kept().roster = await resealed(roster);
    server.credentials.push({ ...listed, passkey_id: "pk-credZZZZ", credential_id: "credZZZZ", public_key: C.b64u(other.pub) });
    expect(await check(a)).toEqual(OPENS);
    kept().roster = await resealed({ ...roster, credentials: [...roster.credentials, { credential_id: "credZZZZ", passkey_id: "pk-credZZZZ", public_key: C.b64u(other.pub) }] });
    kept().grants.credZZZZ = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credZZZZ", kept().mk_id);
    expect(await check(a)).toEqual(BROKEN);
    server.credentials.pop();
  });

  it("a revocation that removes this tab's passkey, or high security that leaves it out: the tab is told it is left out, and opens nothing more", async () => {
    const { ring: first } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB", "hardware");
    const a = await unlockAs("credAAAA");
    const b = await unlockAs("credBBBB");
    const r = new K.Revoke(b, await view(), { highSecurity: true });
    await r.prepare();
    auth.choose = prefer("credBBBB");
    await r.signAndSend();
    expect(await check(b)).toEqual(OPENS);
    expect(await check(a)).toEqual({ state: "leaves", why: K.WAITING_LEAVES });
    // a server that lists none of the passkeys it removes: the tab reads from the change itself that high security
    // leaves the other passkey out, and it still opens
    server.pending!.turnsHigh = false;
    expect(server.view().pending?.removes).toEqual([]);
    expect(await check(b)).toEqual(OPENS);
    server.pending!.turnsHigh = true;
    // a broken body changes nothing for the tab left out: it has no grant to open the rest with
    kept().chain = C.b64u(C.random(80));
    expect(await check(a)).toEqual({ state: "leaves", why: K.WAITING_LEAVES });
    expect(await check(b)).toEqual(BROKEN);
  });

  it("a new recovery code: a tab the code in force unlocked is told that code opens nothing after it; a tab a passkey unlocked opens it, its roster naming the new code's key", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const withCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    const r = new K.Revoke(ring, await view(), { newCode: C.random(20) });
    await r.prepare();
    await r.signAndSend();
    expect(kept().recovery.public_key).toBeTruthy();
    expect(await check(ring)).toEqual(OPENS);
    expect(await check(withCode)).toEqual({ state: "leaves", why: K.WAITING_NEW_CODE });
    const other = await C.newPair();
    await eachBroken(ring, [
      ["another recovery key said than the roster names", (b) => { b.recovery.public_key = C.b64u(other.pub); }],
      ["no new code said, the roster naming one", (b) => { b.recovery = null; }],
    ]);
  });

  it("adding a passkey: under the master key this tab holds, its grant exactly that master key's grant to the new credential's key, and its roster naming the recovery key, every credential this tab's roster names, and it; any part that does not is broken", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    await e.secondTouch();
    auth.choose = prefer("credAAAA");
    await e.confirmAndSend();
    expect(server.pending?.change).toBe("credentials");
    expect(await check(ring)).toEqual(OPENS);
    const roster = await C.openRoster(ring.master, kept().roster, ACCOUNT);
    const resealed = (r: C.Roster) => C.sealRoster(ring.master, r, ACCOUNT);
    const other = await C.newPair();
    const mkBytes = await masterBytes(code);
    const added = C.unb64u(kept().credential.public_key)!;
    await eachBroken(ring, [
      ["under another master key", (b) => { b.mk_id = "0".repeat(32); }],
      ["its grant sealed to another key", async (b) => { b.grant = await C.sealGrant(other.pub, C.random(32), ACCOUNT, "credBBBB", b.mk_id); }],
      ["its grant sealed to another key, its envelope naming the new credential's key", async (b) => {
        b.grant = await headed(await C.sealGrant(other.pub, mkBytes, ACCOUNT, "credBBBB", b.mk_id), added);
      }],
      ["its grant of this master key sealed with a random ephemeral key", async (b) => {
        b.grant = await C.sealEnvelope(added, mkBytes, C.AAD.grant(ACCOUNT, "credBBBB", b.mk_id));
      }],
      ["its grant of another master key to the new credential's key", async (b) => { b.grant = await C.sealGrant(added, C.random(32), ACCOUNT, "credBBBB", b.mk_id); }],
      ["no grant", (b) => { delete b.grant; }],
      ["a credential with another key than its grant is sealed to", (b) => { b.credential.public_key = C.b64u(other.pub); }],
      ["no credential", (b) => { delete b.credential; }],
      ["a roster that does not open", (b) => { b.roster = C.b64u(C.random(80)); }],
      ["a roster without the new credential", async (b) => { b.roster = await resealed({ ...roster, credentials: roster.credentials.filter((c) => c.credential_id !== "credBBBB") }); }],
      ["a roster without a credential this tab's names", async (b) => { b.roster = await resealed({ ...roster, credentials: roster.credentials.filter((c) => c.credential_id !== "credAAAA") }); }],
      ["a roster naming the new credential with another key", async (b) => {
        b.roster = await resealed({ ...roster, credentials: roster.credentials.map((c) => (c.credential_id === "credBBBB" ? { ...c, public_key: C.b64u(other.pub) } : c)) });
      }],
      ["a roster naming another recovery key", async (b) => { b.roster = await resealed({ ...roster, recovery: C.b64u(other.pub) }); }],
    ]);
  });

  it("turning it on again: a content key under the master key this tab holds opens; starting again leaves all this tab unlocked", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    await again.signAndSend(true);
    expect(kept().shape).toBe("again");
    expect(await check(ring)).toEqual(OPENS);
    await eachBroken(ring, [
      ["a wrap that does not open", (b) => { b.content_key.wrapped = C.b64u(C.random(60)); }],
      ["another key stated", async (b) => {
        const other = await C.newPair();
        b.statement = { ...b.statement, payload: JSON.stringify({ ...JSON.parse(b.statement.payload), key: C.b64u(other.pub), key_id: await C.keyIdOf(other.pub) }) };
      }],
    ]);
    server.pending = null;
    const fresh = new K.TurnOn(ACCOUNT, true, CLASSICAL);
    await fresh.prepare(await view());
    await fresh.firstTouch();
    await fresh.secondTouch();
    await fresh.send(true);
    expect(kept()).toMatchObject({ shape: "whole", replace: true });
    expect(await check(ring)).toEqual({ state: "leaves", why: K.WAITING_STARTS_AGAIN });
    kept().replace = false;                        // the first turn-on: no keys of this tab's for it to leave
    expect(await check(ring)).toBeNull();
  });

  it("turning it on again with a new master key: checked as a revocation's, by a tab whose passkey stays", async () => {
    const { ring: first, code } = await turnedOn("credAAAA");
    await enrolled(first, "credBBBB");
    await turnedOff();
    const ring = await unlockAs("credAAAA");
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credBBBB");
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");
    server.grants = server.grants.filter((g) => g.to !== "credBBBB");
    server.rekey = true;
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    expect(again.rekey).toBe(true);
    await again.signAndSend(true);
    expect(kept().shape).toBe("rekey");
    expect(await check(ring)).toEqual(OPENS);
    const next = await waitingMaster(code);
    await eachBroken(ring, [
      ["the key it replaces not this tab's", (b) => { b.previous = "0".repeat(32); }],
      ["a chain to another key", async (b) => { b.chain = await C.sealChain(next, C.random(32), ring.master.id, ACCOUNT); }],
      ["a new content key that does not open", (b) => { b.content_key.wrapped = C.b64u(C.random(60)); }],
    ]);
  });

  it("says which changes unlocking would check: a rotation, a revocation, a passkey added, turning it on again, and starting again", () => {
    const waiting = (change: string, body: Record<string, unknown> | null): K.E2EPending => ({ id: "hold-c", change, takes_effect_at: 1,
      cancel_by: [], cancel_by_code: false, made_by: null, by_code: false, removes: [], replaces_code: false, body });
    const cases: [string, Record<string, unknown> | null, boolean][] = [
      ["rotate", {}, true], ["revoke", {}, true], ["credentials", {}, true],
      ["turn-on", { shape: "again" }, true], ["turn-on", { shape: "rekey" }, true], ["turn-on", { shape: "whole", replace: true }, true],
      ["turn-on", { shape: "whole", replace: false }, false], ["turn-on", {}, false],
      ["off", {}, false], ["anchors", {}, false], ["upload", {}, false], ["passkey", {}, false], ["rotate", null, false],
    ];
    for (const [change, body, checked] of cases) expect(K.checkable(waiting(change, body)), `${change} ${JSON.stringify(body)}`).toBe(checked);
    expect(K.checkable(null)).toBe(false);
  });

  it("says nothing of a change these keys open nothing of, or with no body, or when nothing waits", async () => {
    const { ring } = await turnedOn("credAAAA");
    expect(await check(ring)).toBeNull();
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    expect(server.pending?.change).toBe("off");
    expect(await check(ring)).toBeNull();
    server.pending = { id: "hold-a", change: "anchors", takes_effect_at: server.now + 180, apply: () => {}, body: { mk_id: ring.master.id, anchors: {} } };
    expect(await check(ring)).toBeNull();
    server.pending = { id: "hold-r", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    server.answer = (call) => (call.method === "GET" && call.path === "/api/ade/e2e" ? [200, { ...server.view(), pending: { ...server.view().pending, body: null } }] : null);
    expect(await check(ring)).toBeNull();
    server.answer = null;
    server.pending.body = { shape: "upload", statements: [] };
    expect(await check(ring)).toEqual(BROKEN);                 // a rotation whose body says no content key
  });
});

describe("opening a text", () => {
  it("opens with the content key it names and its slot, and says why when it cannot", async () => {
    const { ring } = await turnedOn("credAAAA");
    const [keyId, { EK }] = [...ring.keys][0];
    const slot = `proofpane-e2e/1|${ACCOUNT}|link-1|event|step|||${"a".repeat(32)}`;
    const env = await C.sealEnvelope(EK, C.utf8('{"said_at":null,"session":null,"text":"Built it."}'), slot);
    expect(C.envelopeKeyId(env)).toBe(keyId);
    expect(await K.openWith(ring, env, slot)).toEqual({ ok: true, value: { said_at: null, session: null, text: "Built it." } });
    expect(await K.openWith(ring, env, slot.replace("step", "note"))).toEqual({ ok: false, why: K.UNREADABLE });
    expect(await K.openWith(ring, await C.sealEnvelope((await C.newPair()).pub, C.utf8("{}"), slot), slot)).toEqual({ ok: false, why: K.NOT_HELD });
    expect(await K.openWith(ring, "e2e1.!", slot)).toEqual({ ok: false, why: K.NOT_ITS_FORM });
    expect(await K.openWith(ring, await C.sealEnvelope(EK, C.utf8('{"text": "x"}'), slot), slot)).toEqual({ ok: false, why: K.NOT_AN_OBJECT });
  });
});

describe("key handling", () => {
  it("makes no key a string, on the way to turning encryption on, unlocking, adding a passkey and a revocation", async () => {
    const made: string[] = [];
    const keep = (r: unknown) => { if (typeof r === "string") made.push(r); return r; };
    const decode = TextDecoder.prototype.decode;
    vi.spyOn(TextDecoder.prototype, "decode").mockImplementation(function (this: TextDecoder, ...a) { return keep(decode.apply(this, a)) as string; });
    const fromCharCode = String.fromCharCode;
    vi.spyOn(String, "fromCharCode").mockImplementation((...a) => keep(fromCharCode(...a)) as string);
    const btoa0 = globalThis.btoa;
    vi.spyOn(globalThis, "btoa").mockImplementation((s) => keep(btoa0(s)) as string);
    const stringify = JSON.stringify;
    vi.spyOn(JSON, "stringify").mockImplementation((...a: Parameters<typeof JSON.stringify>) => keep(stringify(...a)) as string);
    const join = Array.prototype.join;
    vi.spyOn(Array.prototype, "join").mockImplementation(function (this: unknown[], ...a) { return keep(join.apply(this, a)) as string; });

    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await unlockAs("credBBBB");
    await revoked(unlocked, { remove: ["pk-credAAAA"] });
    const now = await unlockAs("credBBBB");
    vi.restoreAllMocks();

    // the secrets, opened here as only a holder of the code and the passkeys could: the master key now and the first
    // one (through the chain), the recovery key, each passkey's PRF output (the first touch's too, of the passkey since
    // removed), a credential's key, and every content key
    const secrets: Uint8Array[] = [];
    const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
    const mk = await C.openGrant(rk, RK, server.grants.find((g) => g.to === "recovery")!.sealed, ACCOUNT, "recovery", server.roster!.mk_id);
    secrets.push(mk, await C.hkdfBytes(await C.hkdfKey(C.recoveryBytes(code)!), C.INFO.recovery(ACCOUNT), 32));
    const master = await C.masterOf(mk, ACCOUNT);
    const first = await C.openChain(master, server.chain[0].wrapped, server.chain[0].previous, ACCOUNT);
    expect((await C.masterOf(first, ACCOUNT)).id).toBe(ring.master.id);
    secrets.push(first);
    secrets.push(await auth.prf("credAAAA", C.unb64u(auth.ceremonies[0].salts.credAAAA)!));
    const cred = server.credentials.find((c) => c.credential_id === "credBBBB")!;
    const prf = await auth.prf("credBBBB", C.unb64u(cred.salt)!);
    secrets.push(prf.slice());
    const { der } = await C.openCredentialDer(await C.credentialKek(prf, ACCOUNT, "credBBBB"), cred.wrapped, ACCOUNT, "credBBBB");
    secrets.push(der, der.subarray(16));
    for (const k of server.contentKeys) {
      const ek = await C.unwrapContentKey(master, k.wrapped, k.key_id, ACCOUNT, true);
      const pk8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", ek));
      secrets.push(pk8, pk8.subarray(16));
    }
    expect(now.keys.size).toBe(2);
    expect(made.length).toBeGreaterThan(100);
    const forms = secrets.flatMap((s) => [C.b64u(s), Buffer.from(s).toString("base64"), C.hex(s), Buffer.from(s).toString("latin1")]);
    const found = forms.filter((f) => made.some((m) => m.includes(f)));
    expect(found).toEqual([]);
  });

  it("zeroes the master key's bytes once turning it on has sealed them, before the first touch", async () => {
    passkey("credAAAA");
    passkey("credBBBB");
    const made: { live: Uint8Array; copy: Uint8Array }[] = [];
    const fill = crypto.getRandomValues.bind(crypto);
    vi.spyOn(crypto, "getRandomValues").mockImplementation(<T extends ArrayBufferView | null>(a: T): T => {
      fill(a as unknown as Uint8Array);
      if (a instanceof Uint8Array && a.length === 32) made.push({ live: a, copy: a.slice() });
      return a;
    });
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    const zeroed = made.map((m) => m.live.every((b) => b === 0));
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    const mkId = bodyOf("/api/ade/e2e/turn-on").mk_id;
    const ids = await Promise.all(made.map(async (m) => (await C.masterOf(m.copy, ACCOUNT)).id));
    const i = ids.indexOf(mkId);
    expect(i).toBeGreaterThan(-1);
    expect(zeroed[i]).toBe(true);
    // each passkey the first touch could pick got its grant then: the one touched is sent, and opens to the master key
    expect(bodyOf("/api/ade/e2e/turn-on").grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
  });

  it("turning it on keeps no extractable private key while the person touches: the content key wrapped at once, the credential's held only wrapped", async () => {
    passkey("credAAAA");
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    expect(extractableKeys(t)).toEqual([]);
    await t.firstTouch();
    expect(extractableKeys(t)).toEqual([]);
    await t.secondTouch();
    expect(extractableKeys(t)).toEqual([]);
    await t.send(true);
    server.settle();
    const ring = await unlockAs("credAAAA");                          // what it sent opens as it did
    expect(ring.keys.size).toBe(1);
    expect(ring.via.anchor).toBe(ring.master.id);
  });

  it("adding a passkey makes its key pair at the first touch, not while the page waits for it", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const generate = vi.spyOn(crypto.subtle, "generateKey");
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    expect(generate).not.toHaveBeenCalled();
    await e.firstTouch();
    expect(generate).toHaveBeenCalledTimes(1);
  });
});

describe("a server put back to older keys", () => {
  it("is refused by a tab that follows: the current master key must lead back to the one the tab holds", async () => {
    const { ring } = await turnedOn("credAAAA");
    const before = snapshot();
    await revoked(ring, {});
    const now = await K.follow(ring, await view());
    expect(now.master.id).not.toBe(ring.master.id);
    restore(before);                                     // the database as it was before the revocation
    // the anchor alone takes it, the credential's anchor being the first master key; the tab does not
    expect((await K.openRing(await view(), ACCOUNT, now.via)).master.id).toBe(ring.master.id);
    await expect(K.follow(now, await view())).rejects.toThrow(K.ROLLED_BACK);
  });

  it("is refused at an unlock by this browser's floor, even with a passkey's own record from before its anchor was renewed; a fresh browser relies on the anchor alone", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    K.raiseFloor(ACCOUNT, ring);
    const first = snapshot();                            // the first master key current, the credential anchored at it
    const old = await masterBytes(code);
    await revoked(ring, {});
    const second = await unlockAs("credAAAA", K.floorOf(ACCOUNT));
    K.raiseFloor(ACCOUNT, second);
    expect(K.floorOf(ACCOUNT)).toEqual([second.master.id]);

    // anchors renewed: the credential's own record names the second master key, written under a fresh salt, and opened
    // again under it before it is sent (its record opened twice: the old with the old salt's key, the new with the new)
    const before = { ...server.credentials[0] };
    const renew = new K.RenewAnchors(second, await view());
    await renew.prepare();
    auth.ceremonies.length = 0;
    const w = watchOpens();
    await renew.touch("credAAAA");
    const record = C.AAD.credential(ACCOUNT, "credAAAA");
    expect(w.count("decrypt", record)).toBe(2);
    await renew.send();
    expect(w.at("decrypt", record)).toBeLessThan(w.sent("/api/ade/e2e/anchors"));
    const sent = bodyOf("/api/ade/e2e/anchors").anchors[0];
    expect(Object.keys(sent).sort()).toEqual(["passkey_id", "salt", "wrapped"]);
    expect(auth.ceremonies[0].salts).toEqual({ credAAAA: before.salt });
    expect(auth.ceremonies[0].seconds).toEqual({ credAAAA: sent.salt });
    expect(sent.salt).not.toBe(before.salt);
    const oldPrf = await auth.prf("credAAAA", C.unb64u(before.salt)!);
    await expect(C.openCredential(await C.credentialKek(oldPrf, ACCOUNT, "credAAAA"), sent.wrapped, ACCOUNT, "credAAAA")).rejects.toThrow();
    server.settle();
    expect(server.credentials[0]).toMatchObject({ salt: sent.salt, wrapped: sent.wrapped });
    expect((await unlockAs("credAAAA", K.floorOf(ACCOUNT))).via.anchor).toBe(second.master.id);

    // the server put back to the first master key, with the credential's own record from before its renewal: the
    // anchor alone takes it (a browser that kept nothing of the account: this one's record set aside, since what it
    // keeps is asked whatever floor an unlock is given), this browser does not, with the passkey or the code
    const anchorAlone = async () => {
      const kept = localStorage.getItem(K.floorKey(ACCOUNT));
      localStorage.removeItem(K.floorKey(ACCOUNT));
      try {
        return await unlockAs("credAAAA");
      } finally {
        if (kept !== null) localStorage.setItem(K.floorKey(ACCOUNT), kept);
      }
    };
    restore(first);
    expect((await anchorAlone()).master.id).toBe(ring.master.id);
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.ROLLED_BACK);
    await expect(unlockAs("credAAAA", K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);
    await expect(K.unlockWithCode(await view(), ACCOUNT, code, K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);

    // nor a fork from the first master key, which a passkey removed since could make, with that record put back
    const CK = C.unb64u(server.credentials[0].public_key)!;
    const forked = C.random(32);
    const fork = await C.masterOf(forked, ACCOUNT);
    server.grants = [{ mk_id: fork.id, to: "credAAAA", sealed: await C.sealGrant(CK, forked, ACCOUNT, "credAAAA", fork.id) }];
    server.chain = [{ mk_id: fork.id, previous: ring.master.id, wrapped: await C.sealChain(fork, old, ring.master.id, ACCOUNT) }];
    server.roster = { mk_id: fork.id, sealed: await C.sealRoster(fork, { credentials: [{ credential_id: "credAAAA", passkey_id: "pk-credAAAA", public_key: C.b64u(CK) }], recovery: C.b64u(CK) }, ACCOUNT) };
    server.contentKeys = [];
    expect((await anchorAlone()).master.id).toBe(fork.id);
    await expect(unlockAs("credAAAA", K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);

    // a fresh browser keeps no floor: the anchor alone, until its first unlock, which sets one
    restore(first);
    localStorage.clear();
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    K.raiseFloor(ACCOUNT, await unlockAs("credAAAA", K.floorOf(ACCOUNT)));
    expect(K.floorOf(ACCOUNT)).toEqual([ring.master.id]);
  });

  it("the floor is raised, never lowered, and keys this browser made are trusted until they are seen to take effect", () => {
    const ring = (id: string, ...back: string[]) => ({ master: { id }, masters: new Map([id, ...back].map((m) => [m, {}])) }) as unknown as K.Ring;
    const [a, b, d, n] = ["a", "b", "d", "e"].map((c) => c.repeat(32));
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    K.raiseFloor(ACCOUNT, ring(a));
    expect(K.floorOf(ACCOUNT)).toEqual([a]);
    K.raiseFloor(ACCOUNT, ring(b, a));
    expect(K.floorOf(ACCOUNT)).toEqual([b]);
    K.raiseFloor(ACCOUNT, ring(a));                      // another tab's older ring does not lower it
    expect(K.floorOf(ACCOUNT)).toEqual([b]);
    K.expectKeys(ACCOUNT, n);                            // keys started again here, waiting on their hold
    expect(K.floorOf(ACCOUNT)).toEqual([b, n]);
    K.raiseFloor(ACCOUNT, ring(d, b));
    expect(K.floorOf(ACCOUNT)).toEqual([d, n]);
    K.raiseFloor(ACCOUNT, ring(n));                      // they took effect
    expect(K.floorOf(ACCOUNT)).toEqual([n]);
    expect(K.floorOf("acct-2")).toEqual([]);
    K.dropFloor(ACCOUNT);
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    K.expectKeys(ACCOUNT, n);                            // keys made here, in a browser that opened none: no floor yet
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    K.raiseFloor(ACCOUNT, ring(n));
    expect(K.floorOf(ACCOUNT)).toEqual([n]);
    localStorage.setItem(`pp-e2e-floor:${ACCOUNT}`, "{not json");
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    localStorage.setItem(`pp-e2e-floor:${ACCOUNT}`, JSON.stringify({ floor: "ABC", next: a }));
    expect(K.floorOf(ACCOUNT)).toEqual([]);
  });

  it("a browser that started encryption again trusts the new keys once they take effect; another browser asks first", async () => {
    const { ring } = await turnedOn("credAAAA");
    K.raiseFloor(ACCOUNT, ring);
    await turnedOff();
    const fresh = new K.TurnOn(ACCOUNT, true, CLASSICAL);
    await fresh.prepare(await view());
    await fresh.firstTouch();
    await fresh.secondTouch();
    await fresh.send(true);
    const mkId = bodyOf("/api/ade/e2e/turn-on").mk_id;
    expect(K.floorOf(ACCOUNT)).toEqual([ring.master.id, mkId]);
    // still the old keys while it waits
    expect((await unlockAs("credAAAA", K.floorOf(ACCOUNT))).master.id).toBe(ring.master.id);
    server.settle();
    const now = await unlockAs("credAAAA", K.floorOf(ACCOUNT));
    expect(now.master.id).toBe(mkId);
    K.raiseFloor(ACCOUNT, now);
    expect(K.floorOf(ACCOUNT)).toEqual([mkId]);
    // a browser that only ever opened the old keys
    localStorage.clear();
    K.raiseFloor(ACCOUNT, ring);
    await expect(unlockAs("credAAAA", K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);
  });

  it("a tab unlocked with a recovery code since replaced is told so, and a content key under a master key off the path is named, not opened", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const byCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    await revoked(ring, { newCode: C.random(20) });
    await expect(K.follow(byCode, await view())).rejects.toThrow(K.CODE_REPLACED);
    // the server lists a content key under a master key that does not lead back to the tab's
    const now = await unlockAs("credAAAA");
    const stranger = await C.masterOf(C.random(32), ACCOUNT);
    const pair = await C.newPair(true);
    const keyId = await C.keyIdOf(pair.pub);
    server.contentKeys.push({ key_id: keyId, public_key: C.b64u(pair.pub), mk_id: stranger.id, wrapped: await C.wrapContentKey(stranger, pair.priv, keyId, ACCOUNT) });
    const followed = await K.follow(now, await view());
    expect(followed.unreached).toEqual([keyId]);
    expect(followed.keys.has(keyId)).toBe(false);
    const slot = `proofpane-e2e/1|${ACCOUNT}|link-1|event|step|||${"a".repeat(32)}`;
    expect(await K.openWith(followed, await C.sealEnvelope(pair.pub, C.utf8('{"text":"x"}'), slot), slot)).toEqual({ ok: false, why: K.UNREACHED });
    // and a new master key leaves it as it is (abandon), wrapping the others again: one junk row blocks no revocation
    const { body } = await revoked(followed, {});
    expect(body.abandon).toEqual([keyId]);
    expect(body.content_keys.map((k: Body) => k.key_id)).toEqual(followed.keys.size ? [...followed.keys.keys()] : []);
    expect(server.contentKeys.find((k) => k.key_id === keyId)).toMatchObject({ mk_id: stranger.id });
    const after = await K.follow(followed, await view());
    expect(after.master.id).toBe(body.mk_id);
    expect(after.unreached).toEqual([keyId]);
  });
});

describe("a tab whose keys are older than this browser's floor", () => {
  /** Two tabs at the first master key, with a passkey P enrolled: tab B holds them (`b`), and the rows it opened, every
   *  one (`opened`); tab A then revokes P and follows, raising this browser's floor past B's keys. */
  async function behindTheFloor() {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credPPPP");
    passkey("credQQQQ");                                 // the account's, not added to encryption
    const b = await K.follow(ring, await view());
    await K.raiseFloor(ACCOUNT, b);
    const opened = snapshot();
    const a = await K.follow(ring, await view());
    await revoked(a, { remove: ["pk-credPPPP"] });
    await K.raiseFloor(ACCOUNT, await K.follow(a, await view()));
    expect(K.floorOf(ACCOUNT)).toEqual([server.roster!.mk_id]);
    return { b, opened, statements: JSON.parse(JSON.stringify(server.statements)) };
  }

  it("makes no change from them, though the server serves that tab exactly the rows it opened", async () => {
    const { b, opened } = await behindTheFloor();
    restore(opened);                                     // to tab B, the rows it opened: P's key current again
    const v = await view();
    const sent = server.calls.length;
    await expect(new K.Rotate(b, v).prepare()).rejects.toThrow(K.BEHIND);
    await expect(new K.Revoke(b, v, {}).prepare()).rejects.toThrow(K.BEHIND);
    await expect(new K.Enrol(b, v, v.passkeys.find((p) => p.credential_id === "credQQQQ")!).prepare()).rejects.toThrow(K.BEHIND);
    await expect(new K.RenewAnchors(b, v).prepare()).rejects.toThrow(K.BEHIND);
    expect(server.calls.slice(sent)).toEqual([]);         // no nonce, no confirmation's challenge
    // and following them with this browser's floor, as the tab does then, is refused: they do not lead back to it
    await expect(K.follow(b, v, K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);
  });

  it("signs no statement under keys a revocation replaced: the old key rows served with the account's chain as it is", async () => {
    const { b, opened, statements } = await behindTheFloor();
    restore(opened);
    server.statements = statements;                      // the revocation's statement the newest: the next would follow it
    await expect(new K.Rotate(b, await view()).prepare()).rejects.toThrow(K.BEHIND);
    // a browser whose floor never saw the newer keys: the newest key stated is not one these keys hold
    localStorage.clear();
    await expect(new K.Rotate(b, await view()).prepare()).rejects.toThrow(K.NOT_NEWEST);
    await expect(new K.Revoke(b, await view(), {}).prepare()).rejects.toThrow(K.NOT_NEWEST);
    // and with the newest statement's key_id column set to a key these keys hold: read as signed, and no change of any
    // kind is made while the server lists it otherwise
    const [held, newest] = [statements[0].key_id, statements[1].key_id];
    expect(b.keys.has(held!) && !b.keys.has(newest!)).toBe(true);
    server.statements[1].key_id = held;
    const lying = await view();
    expect(lying.misstated).toEqual([2]);
    expect(lying.statements[1].key_id).toBe(newest);
    await expect(new K.Rotate(b, lying).prepare()).rejects.toThrow(K.STATED_OTHERWISE);
    await expect(new K.Revoke(b, lying, {}).prepare()).rejects.toThrow(K.STATED_OTHERWISE);
    await expect(new K.Enrol(b, lying, lying.passkeys.find((p) => p.credential_id === "credQQQQ")!).prepare()).rejects.toThrow(K.STATED_OTHERWISE);
    await expect(new K.RenewAnchors(b, lying).prepare()).rejects.toThrow(K.STATED_OTHERWISE);
    // a view whose columns went unchecked: the newest key is read from the payload all the same
    const unchecked = { ...lying, misstated: [], statements: lying.statements.map((s, i) => (i === 1 ? { ...s, key_id: held } : s)) };
    await expect(new K.Rotate(b, unchecked).prepare()).rejects.toThrow(K.NOT_NEWEST);
    // and one whose newest e2e-key payload names no key: refused, not taken as naming none
    const { key_id: _gone, ...unnamed } = JSON.parse(lying.statements[1].payload as string) as Record<string, unknown>;
    const noKey = { ...unchecked, statements: unchecked.statements.map((s, i) => (i === 1 ? { ...s, payload: canonicalJson(unnamed) } : s)) };
    await expect(new K.Rotate(b, noKey).prepare()).rejects.toThrow(K.NOT_NEWEST);
    expect(posted("/api/ade/e2e/rotate")).toHaveLength(0);
  });

  it("states nothing either with a row for the newest key put back among the old rows, one that does not open", async () => {
    const { b, opened, statements } = await behindTheFloor();
    restore(opened);
    server.statements = statements;
    const newest = JSON.parse(statements[1].payload as string) as { key: string; key_id: string };
    expect(b.keys.has(newest.key_id)).toBe(false);
    server.contentKeys.push({ key_id: newest.key_id, public_key: newest.key, mk_id: b.master.id, wrapped: C.b64u(C.random(80)) });
    localStorage.clear();                                // a browser whose floor never saw the newer keys
    const now = await K.follow(b, await view());
    expect(now.unopened).toEqual([newest.key_id]);
    await expect(new K.Rotate(now, await view()).prepare()).rejects.toThrow(K.NEWEST_UNOPENED);
    await expect(new K.Revoke(now, await view(), {}).prepare()).rejects.toThrow(K.NEWEST_UNOPENED);
    expect(posted("/api/ade/e2e/rotate")).toHaveLength(0);
  });

  it("does not turn encryption on again from them", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    const b = await K.follow(ring, await view());
    await K.raiseFloor(ACCOUNT, b);
    const opened = snapshot();
    server.rekey = true;                                 // tab A turns it on again with a new master key, and follows it
    const a = await K.follow(ring, await view());
    const again = new K.TurnOnAgain(a, await view());
    await again.prepare();
    await again.signAndSend(true);
    server.settle();
    await K.raiseFloor(ACCOUNT, await K.follow(a, await view()));
    restore(opened);
    await expect(new K.TurnOnAgain(b, await view()).prepare()).rejects.toThrow(K.BEHIND);
  });

  it("refuses, before any touch, a change prepared from them before another tab raised the floor", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credPPPP");
    await revoked(await K.follow(ring, await view()), {});          // every anchor older than the master key now
    passkey("credQQQQ");
    passkey("credRRRR");
    const b = await K.follow(ring, await view());
    await K.raiseFloor(ACCOUNT, b);
    const v = await view();
    const rotate = new K.Rotate(b, v);
    await rotate.prepare();
    const revoke = new K.Revoke(b, v, { remove: ["pk-credPPPP"] });
    await revoke.prepare();
    const enrol = new K.Enrol(b, v, v.passkeys.find((p) => p.credential_id === "credQQQQ")!);
    await enrol.prepare();
    const touched = new K.Enrol(b, v, v.passkeys.find((p) => p.credential_id === "credRRRR")!);
    await touched.prepare();
    auth.choose = () => "credRRRR";
    await touched.firstTouch();
    await touched.secondTouch();
    const renew = new K.RenewAnchors(b, v);
    await renew.prepare();
    auth.choose = () => "credAAAA";
    await renew.touch("credAAAA");
    expect(renew.rows).toHaveLength(1);
    // another tab follows a revocation, raising the floor past B's keys
    const a = await K.follow(b, await view());
    await revoked(a, { remove: ["pk-credPPPP"] });
    await K.raiseFloor(ACCOUNT, await K.follow(a, await view()));
    const rk = await K.codeKey(v, ACCOUNT, code);
    auth.ceremonies.length = 0;
    const sent = server.calls.length;
    await expect(rotate.signAndSend()).rejects.toThrow(K.BEHIND);
    await expect(rotate.signAndSendWithCode(rk)).rejects.toThrow(K.BEHIND);
    await expect(revoke.signAndSend()).rejects.toThrow(K.BEHIND);
    await expect(revoke.signAndSendWithCode(rk)).rejects.toThrow(K.BEHIND);
    await expect(enrol.firstTouch()).rejects.toThrow(K.BEHIND);
    await expect(touched.confirmAndSend()).rejects.toThrow(K.BEHIND);
    await expect(touched.sendWithCode(rk)).rejects.toThrow(K.BEHIND);              // before the code's proof is asked for
    await expect(renew.touch("credPPPP")).rejects.toThrow(K.BEHIND);
    await expect(renew.renewCode(code)).rejects.toThrow(K.BEHIND);
    await expect(renew.send()).rejects.toThrow(K.BEHIND);
    expect(auth.ceremonies).toHaveLength(0);
    expect(server.calls.slice(sent)).toEqual([]);
  });

  it("refuses, before the touch, turning it on again prepared before another tab raised the floor", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    await K.raiseFloor(ACCOUNT, ring);
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    // another tab opened keys newer than these: a stand-in for its ring, its master key leading back to this one's
    const newer = "e".repeat(32);
    await K.raiseFloor(ACCOUNT, { master: { id: newer }, masters: new Map([[newer, {}], [ring.master.id, {}]]) } as unknown as K.Ring);
    expect(K.floorOf(ACCOUNT)).toEqual([newer]);
    auth.ceremonies.length = 0;
    await expect(again.signAndSend(true)).rejects.toThrow(K.BEHIND);
    expect(auth.ceremonies).toHaveLength(0);
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(1);           // the first turning on alone
  });

  it("does not turn it on again from old key rows served with the account's newer statements", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    const b = await K.follow(ring, await view());
    const opened = snapshot();
    // elsewhere: on again, under a new content key, and off again
    const again = new K.TurnOnAgain(b, await view());
    await again.prepare();
    await again.signAndSend(true);
    server.settle();
    await turnedOff();
    const statements = JSON.parse(JSON.stringify(server.statements));
    restore(opened);
    server.statements = statements;
    localStorage.clear();                                // a browser whose floor never saw them
    await expect(new K.TurnOnAgain(b, await view()).prepare()).rejects.toThrow(K.NOT_NEWEST);
  });

  it("never lowers the floor when another tab writes it between this one's read and its write", async () => {
    const ring = (id: string, ...back: string[]) => ({ master: { id }, masters: new Map([id, ...back].map((m) => [m, {}])) }) as unknown as K.Ring;
    const [m1, m2] = ["a", "b"].map((c) => c.repeat(32));
    await K.raiseFloor(ACCOUNT, ring(m1));
    // this tab, still at m1, reads the floor; another tab, at m2, writes it before this one writes
    const getItem = Storage.prototype.getItem;
    let reads = 0;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
      const v = getItem.call(this, key);
      if (key === K.floorKey(ACCOUNT) && reads++ === 0) localStorage.setItem(key, JSON.stringify({ floor: m2, next: null }));
      return v;
    });
    await K.raiseFloor(ACCOUNT, ring(m1));
    vi.restoreAllMocks();
    expect(K.floorOf(ACCOUNT)).toEqual([m2]);
  });

  it("changes the floor under the Web Locks API's lock for the account, one tab at a time", async () => {
    const ring = (id: string, ...back: string[]) => ({ master: { id }, masters: new Map([id, ...back].map((m) => [m, {}])) }) as unknown as K.Ring;
    const [m1, m2] = ["a", "b"].map((c) => c.repeat(32));
    const queue: { name: string; run: () => void }[] = [];
    const locks = { request: (name: string, cb: () => void) => new Promise<void>((done) => { queue.push({ name, run: () => { cb(); done(); } }); }) };
    Object.defineProperty(navigator, "locks", { value: locks, configurable: true });
    try {
      const first = K.raiseFloor(ACCOUNT, ring(m2, m1));
      const second = K.raiseFloor(ACCOUNT, ring(m1));
      expect(K.floorOf(ACCOUNT)).toEqual([]);                      // nothing changed before the lock is granted
      expect(queue.map((q) => q.name)).toEqual([K.floorKey(ACCOUNT), K.floorKey(ACCOUNT)]);
      while (queue.length) queue.shift()!.run();
      await Promise.all([first, second]);
      expect(K.floorOf(ACCOUNT)).toEqual([m2]);
    } finally {
      delete (navigator as unknown as { locks?: unknown }).locks;
    }
  });
});

describe("a new master key", () => {
  it("is refused from a tab the server's keys have moved past", async () => {
    const { ring } = await turnedOn("credAAAA");
    await revoked(await unlockAs("credAAAA"), {});
    await expect(new K.Revoke(ring, await view(), {}).prepare()).rejects.toThrow(K.KEYS_CHANGED);
    await expect(new K.TurnOnAgain(ring, await view()).prepare()).rejects.toThrow(K.KEYS_CHANGED);
  });

  it("nor is a passkey added from a tab whose roster has changed since: it would drop the one added meanwhile", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");                    // elsewhere: the roster now names it
    passkey("credCCCC");
    const v = await view();
    await expect(new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credCCCC")!).prepare()).rejects.toThrow(K.KEYS_CHANGED);
    await enrolled(await K.follow(ring, v), "credCCCC");
    expect((await unlockAs("credAAAA")).roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
  });

  it("wraps again exactly the content keys the tab verified, each checked against its public half", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const honest = server.contentKeys[0];
    // a content key the server listed after the tab unlocked: refused, not wrapped again unseen
    server.contentKeys.push({ ...honest, key_id: C.hex(C.random(16)) });
    await expect(new K.Revoke(ring, await view(), {}).prepare()).rejects.toThrow(K.KEYS_CHANGED);
    // the tab's own key, its wrap now holding another key (made by one who holds the master key)
    const m = await C.masterOf(await masterBytes(code), ACCOUNT);
    const other = await C.newPair(true);
    server.contentKeys = [{ ...honest, wrapped: await C.wrapContentKey(m, other.priv, honest.key_id, ACCOUNT) }];
    await expect(new K.Revoke(ring, await view(), {}).prepare()).rejects.toThrow(K.BAD_CONTENT_KEY);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
  });

  it("opens everything it made before the revocation is sent: the chain link, the new grants, the new recovery anchor, every content key wrapped again, the roster", async () => {
    const { ring } = await turnedOn("credAAAA");
    const fresh = C.random(20);
    const r = new K.Revoke(ring, await view(), { newCode: fresh.slice() });
    const w = watchOpens();
    await r.prepare();
    await r.signAndSend();
    const body = bodyOf("/api/ade/e2e/revoke");
    const sent = w.sent("/api/ade/e2e/revoke");
    const opened: [string, string][] = [
      ["decrypt", C.AAD.chain(ACCOUNT, body.mk_id, ring.master.id)],
      ["decrypt", C.AAD.grant(ACCOUNT, "credAAAA", body.mk_id)],
      ["decrypt", C.AAD.grant(ACCOUNT, "recovery", body.mk_id)],
      ["decrypt", C.AAD.recoveryAnchor(ACCOUNT)],
      ["decrypt", C.AAD.roster(ACCOUNT, body.mk_id)],
      ...body.content_keys.map((k: Body) => ["unwrap", C.AAD.contentKey(ACCOUNT, k.key_id, body.mk_id)] as [string, string]),
    ];
    for (const [what, aad] of opened) {
      expect(w.at(what, aad), aad).toBeGreaterThan(-1);
      expect(w.at(what, aad), aad).toBeLessThan(sent);
    }
  });

  it("adding a passkey opens its new grant with the key its second touch opened, and the roster it rewrote, before it is sent", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    await e.firstTouch();
    await e.secondTouch();
    const w = watchOpens();
    await e.confirmAndSend();                                          // A confirms it, the one that unlocks the keys
    for (const aad of [C.AAD.grant(ACCOUNT, "credBBBB", ring.master.id), C.AAD.roster(ACCOUNT, ring.master.id)]) {
      expect(w.at("decrypt", aad), aad).toBeGreaterThan(-1);
      expect(w.at("decrypt", aad), aad).toBeLessThan(w.sent("/api/ade/e2e/credentials"));
    }
  });

  it("turning it on opens the roster it sealed before it is sent", async () => {
    passkey("credAAAA");
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await view());
    await t.firstTouch();
    await t.secondTouch();
    const w = watchOpens();
    await t.send(true);
    const aad = C.AAD.roster(ACCOUNT, bodyOf("/api/ade/e2e/turn-on").mk_id);
    expect(w.at("decrypt", aad)).toBeGreaterThan(-1);
    expect(w.at("decrypt", aad)).toBeLessThan(w.sent("/api/ade/e2e/turn-on"));
  });

  it("checks each grant it cannot open, to another passkey's key, names that key before the revocation is sent", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    const B = C.unb64u(server.credentials.find((c) => c.credential_id === "credBBBB")!.public_key)!;
    // the grant to B's key sealed naming another key: the first digest of B's key, as it is sealed, comes out wrong
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    let first = true;
    vi.spyOn(crypto.subtle, "digest").mockImplementation((alg, data) => {
      const bytes = ArrayBuffer.isView(data) ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : new Uint8Array(data as ArrayBuffer);
      if (first && C.same(bytes, B)) {
        first = false;
        return digest(alg, C.random(32));
      }
      return digest(alg, data);
    });
    await expect(new K.Revoke(unlocked, await view(), {}).prepare()).rejects.toThrow("A grant just made is not sealed to the key it is for.");
    expect(first).toBe(false);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
  });

  it("leaves out every credential whose passkey is gone, which the server no longer lists with the key the roster names, or, while high security is on, which is not a hardware key's", async () => {
    const { ring } = await turnedOn("credAAAA", "hardware");
    for (const id of ["credBBBB", "credCCCC", "credDDDD", "credEEEE"]) await enrolled(await K.follow(ring, await view()), id);
    const unlocked = await K.follow(ring, await view());
    expect(unlocked.roster!.credentials).toHaveLength(5);
    server.passkeys = server.passkeys.filter((p) => p.id !== "pk-credBBBB");                     // gone from the account
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credCCCC");       // no longer enrolled
    const d = server.credentials.find((c) => c.credential_id === "credDDDD")!;
    d.public_key = C.b64u((await C.newPair()).pub);                                             // listed with another key
    // that one is never sealed to, and the server takes no new master key without it: it goes in the same change, and
    // the refusal names it
    await expect(new K.Revoke(unlocked, await view(), {}).prepare()).rejects.toThrow(K.unnamedWords(["credDDDD"]));
    expect(K.unnamed(unlocked, await view()).map((p) => p.passkey_id)).toEqual(["pk-credDDDD"]);
    let r = new K.Revoke(unlocked, await view(), { remove: ["pk-credDDDD"] });
    await r.prepare();
    expect(r.leaving.sort()).toEqual(["pk-credBBBB", "pk-credCCCC", "pk-credDDDD"]);
    expect(r.kept.sort()).toEqual(["pk-credAAAA", "pk-credEEEE"]);
    // and every one but the hardware keys while high security is on
    server.high = true;
    r = new K.Revoke(unlocked, await view(), {});
    await r.prepare();
    expect(r.kept).toEqual(["pk-credAAAA"]);
    auth.ceremonies.length = 0;
    await r.signAndSend();
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);
    const body = bodyOf("/api/ade/e2e/revoke");
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    server.settle();
    const next = await K.follow(unlocked, await view());
    expect(next.roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
  });
});

describe("what a revocation keeps", () => {
  it("refuses one that leaves no passkey to unlock encryption, unless the person says only the recovery code is wanted; that one goes only with the code's proof, signed by any passkey of the account", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    passkey("credBBBB");                                 // a passkey of the account that does not unlock encryption
    await expect(new K.Revoke(ring, await view(), { remove: ["pk-credAAAA"] }).prepare()).rejects.toThrow(K.NONE_KEPT);
    const r = new K.Revoke(ring, await view(), { remove: ["pk-credAAAA"], onlyCode: true });
    expect(r.byCode).toBe(true);                         // no passkey that stays after it unlocks the keys
    await r.prepare();
    expect(r.kept).toEqual([]);
    expect(r.leaving).toEqual(["pk-credAAAA"]);
    auth.ceremonies.length = 0;
    await expect(r.signAndSend()).rejects.toThrow(K.CODE_NEEDED);                  // never without the code, before any touch
    expect(auth.ceremonies).toHaveLength(0);
    auth.choose = () => "credBBBB";
    expect((await r.signAndSendWithCode(await K.codeKey(await view(), ACCOUNT, code))).hold?.takes_effect_at).toBe(server.now + 86400);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB"]);           // as the server takes one with the code
    expect(bodyOf("/api/ade/e2e/revoke").grants.map((g: Body) => g.to)).toEqual(["recovery"]);
    server.settle();
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).master.id).toBe(server.roster!.mk_id);
  });

  it("goes only with the code's proof when no passkey that stays could sign it; is refused, before anything is asked, when the code could not make it either, or no passkey could sign it at all", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const r = new K.Revoke(ring, await view(), { remove: ["pk-credAAAA"], onlyCode: true });
    expect(r.byCode).toBe(true);
    await r.prepare();
    await expect(r.signAndSend()).rejects.toThrow(K.CODE_NEEDED);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
    // the passkey it removes signs it, with the code's proof beside it
    await r.signAndSendWithCode(await K.codeKey(await view(), ACCOUNT, code));
    expect(server.pending).toMatchObject({ change: "revoke", madeBy: "pk-credAAAA", byCode: true });
    // a server that says the code does not cancel the change that waits, which no passkey cancels either
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: { ...v.pending, cancel_by: [], cancel_by_code: false } }];
    };
    const v = await view();
    const asked = server.calls.length;
    await expect(new K.Revoke(ring, v, { remove: ["pk-credAAAA"], onlyCode: true }).prepare()).rejects.toThrow(K.NONE_TO_SIGN);
    await expect(new K.Revoke(ring, v, {}).prepare()).rejects.toThrow(K.WAITING_CHANGE);           // A stays, but may not
    const noneAtAll = new K.Revoke(ring, { ...v, pending: null, high_security: true }, {});
    expect(noneAtAll.byCode).toBe(false);                                // the code would not help: no passkey may sign
    await expect(noneAtAll.prepare()).rejects.toThrow(K.NONE_AT_ALL);
    expect(server.calls.slice(asked)).toEqual([]);
  });

  it("turning high security on takes a hardware key that unlocks encryption, whatever the person says", async () => {
    const { ring } = await turnedOn("credAAAA");          // a device passkey
    passkey("credHHHH", "key", "hardware");              // a hardware key that does not unlock encryption yet
    for (const onlyCode of [false, true]) {
      await expect(new K.Revoke(ring, await view(), { highSecurity: true, onlyCode }).prepare()).rejects.toThrow(K.NO_HARDWARE_KEPT);
    }
    await enrolled(ring, "credHHHH", "hardware");
    const r = new K.Revoke(await K.follow(ring, await view()), await view(), { highSecurity: true });
    await r.prepare();
    expect(r.kept).toEqual(["pk-credHHHH"]);
  });
});

describe("a passkey removed while encryption is off", () => {
  async function offWithout(remove: string) {
    const { ring, code } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    await turnedOff();
    await removePasskey(remove, "credAAAA");
    return { unlocked, code };
  }

  it("takes its credential and grants with it, and turning encryption on again then makes a new master key without it", async () => {
    const { unlocked, code } = await offWithout("pk-credBBBB");
    const before = await masterBytes(code);
    passkey("credCCCC");                                 // the account's, not added to encryption: it does not sign it
    const v = await view();
    expect(v.rekey_due).toBe(true);
    expect(v.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
    const now = await K.follow(unlocked, v);
    const again = new K.TurnOnAgain(now, await view());
    await again.prepare();
    expect(again.rekey).toBe(true);
    expect(again.leaving).toEqual(["pk-credBBBB"]);
    expect(again.kept).toEqual(["pk-credAAAA"]);
    auth.ceremonies.length = 0;
    await again.signAndSend(true);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA"]);
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(Object.keys(body).sort()).toEqual(["chain", "content_key", "content_keys", "grants", "mk_id", "roster", "seal", "statement"]);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(body.chain.previous).toBe(now.master.id);
    expect(body.statement.payload.retires).toBe("all");
    server.settle();
    expect((await view()).on).toBe(true);
    const after = await unlockAs("credAAAA");
    expect(after.master.id).toBe(body.mk_id);
    expect(after.roster!.credentials.map((c) => c.credential_id)).toEqual(["credAAAA"]);
    expect(C.same(before, await masterBytes(code))).toBe(false);
  });

  it("leaves out of the next master key a passkey this browser removed, though the server lists it again, enrolled, and says none is due", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    await turnedOff();
    const listed = await view();
    const kept = JSON.parse(JSON.stringify({ passkey: server.passkeys.find((p) => p.id === "pk-credBBBB"),
      credential: server.credentials.find((c) => c.passkey_id === "pk-credBBBB"), grants: server.grants.filter((g) => g.to === "credBBBB") }));
    await removePasskey("pk-credBBBB", "credAAAA");
    K.rememberRemoved(ACCOUNT, listed, "pk-credBBBB");
    expect(K.removedHere(ACCOUNT)).toEqual([{ passkey_id: "pk-credBBBB", credential_id: "credBBBB", public_key: kept.credential.public_key }]);
    // the server lists it again, enrolled with the key it had and its grant, and says no new master key is due
    server.passkeys.push(kept.passkey);
    server.credentials.push(kept.credential);
    server.grants.push(...kept.grants);
    server.rekey = false;
    const v = await view();
    expect(v.rekey_due).toBe(false);
    const now = await K.follow(unlocked, v);
    expect(K.rosterAfter(now, v)).toMatchObject({ leaving: [{ passkey_id: "pk-credBBBB" }] });
    const again = new K.TurnOnAgain(now, v);
    await again.prepare();
    expect(again.rekey).toBe(true);
    expect(again.leaving).toEqual(["pk-credBBBB"]);
    auth.choose = () => "credAAAA";
    await again.signAndSend(true).catch(() => {});      // a server that keeps it takes no new master key without it
    expect(bodyOf("/api/ade/e2e/turn-on").grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    // another account's list is its own
    expect(K.removedHere("acct-2")).toEqual([]);
  });

  it("takes a passkey as one this browser removed by its id, its credential's or its key, each alone", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const now = await K.follow(ring, await view());
    const v = await view();
    const b = now.roster!.credentials.find((c) => c.credential_id === "credBBBB")!;
    for (const entry of [{ passkey_id: b.passkey_id }, { passkey_id: "pk-renamed", credential_id: b.credential_id },
      { passkey_id: "pk-renamed", public_key: b.public_key }]) {
      localStorage.setItem(`pp-e2e-removed:${ACCOUNT}`, JSON.stringify([entry]));
      expect(K.rosterAfter(now, v).leaving.map((c) => c.credential_id), JSON.stringify(entry)).toEqual(["credBBBB"]);
    }
    localStorage.setItem(`pp-e2e-removed:${ACCOUNT}`, JSON.stringify([{ passkey_id: "pk-other", credential_id: "credOTHER", public_key: "x" }]));
    expect(K.rosterAfter(now, v).leaving).toEqual([]);
    localStorage.setItem(`pp-e2e-removed:${ACCOUNT}`, "{not json");
    expect(K.removedHere(ACCOUNT)).toEqual([]);
    expect(K.rosterAfter(now, v).leaving).toEqual([]);
  });

  it("makes the new master key from the roster alone, where the server does not say one is due", async () => {
    const { unlocked } = await offWithout("pk-credBBBB");
    server.rekey = false;
    expect((await view()).rekey_due).toBe(false);
    const again = new K.TurnOnAgain(await K.follow(unlocked, await view()), await view());
    await again.prepare();
    expect(again.rekey).toBe(true);
    await again.signAndSend(false);
    expect(bodyOf("/api/ade/e2e/turn-on").grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
  });

  it("cannot turn encryption on again when no passkey that unlocked it is left: only starting again does", async () => {
    const { code } = await turnedOn("credAAAA");
    passkey("credBBBB");                                 // the account's, never added to encryption
    await turnedOff();
    await removePasskey("pk-credAAAA", "credBBBB");
    const byCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    await expect(new K.TurnOnAgain(byCode, await view()).prepare()).rejects.toThrow(K.NO_SIGNER);
    expect(posted("/api/ade/link/nonce")).toHaveLength(2);         // turning it on and off: none for this
  });

  it("makes one where the server says one is due, though the roster names no passkey that leaves", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    server.rekey = true;
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    expect(again.rekey).toBe(true);
    expect(again.leaving).toEqual([]);
    await again.signAndSend(true);
    expect(bodyOf("/api/ade/e2e/turn-on").chain.previous).toBe(ring.master.id);
  });

  it("turning high security on while it is off leaves the passkeys that are not hardware keys out of the next master key", async () => {
    passkey("credHHHH", "key", "hardware");
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credHHHH", "hardware");
    await turnedOff();
    await turnHighSecurity(true, "credHHHH");
    const v = await view();
    expect(v.rekey_due).toBe(true);
    expect(v.credentials.map((c) => c.credential_id)).toEqual(["credHHHH"]);      // the device passkey's went with its grant
    expect(K.unlockAllowed(v).map((a) => a.credentialId)).toEqual(["credHHHH"]);
    const again = new K.TurnOnAgain(await unlockAs("credHHHH"), await view());
    await again.prepare();
    expect(again.leaving).toEqual(["pk-credAAAA"]);
    auth.ceremonies.length = 0;
    await again.signAndSend(true);
    expect(auth.ceremonies[0].allow).toEqual(["credHHHH"]);
    server.settle();
    expect((await unlockAs("credHHHH")).roster!.credentials.map((c) => c.credential_id)).toEqual(["credHHHH"]);
  });
});

describe("the test server, as the server", () => {
  const status = async (path: string, method: string, body: unknown) =>
    (await server.fetch(path, { method, body: JSON.stringify(body) })).status;

  it("turns high security on or off only at a hardware key's word, and on only with one kept, whose last it keeps", async () => {
    const turn = async (on: boolean, confirmation: string) => {
      const r = await server.fetch("/api/account/high-security", { method: "PUT", body: JSON.stringify({ on, confirmation }) });
      return { status: r.status, detail: (await r.json()).detail };
    };
    passkey("credAAAA", "phone", "device");
    expect(await turn(true, await confirmation("credAAAA", "confirm", "high-security-on"))).toEqual({ status: 409, detail: WORDS.noHardware });
    passkey("credHHHH", "key", "hardware");
    expect(await turn(true, await confirmation("credAAAA", "confirm", "high-security-on"))).toEqual({ status: 403, detail: WORDS.needsHardwareToTurn });
    const early = await confirmation("credAAAA", "confirm", "high-security-off");     // a device passkey's, for turning it off
    await turnHighSecurity(true, "credHHHH");
    expect(await status("/api/account/passkeys/pk-credHHHH", "DELETE", { confirmation: await confirmation("credHHHH", "confirm", "passkey-remove:pk-credHHHH") })).toBe(409);
    expect(await turn(false, early)).toEqual({ status: 403, detail: WORDS.needsHardwareToTurn });
    await turnHighSecurity(false, "credHHHH");
  });

  it("while high security is on, takes a confirmation from a hardware key alone: a device passkey's touch, or one it gave before, confirms nothing", async () => {
    passkey("credAAAA", "phone", "device");
    passkey("credHHHH", "key", "hardware");
    passkey("credBBBB", "other", "device");
    const early = await confirmation("credAAAA", "confirm", "passkey-remove:pk-credBBBB");     // given while high security was off
    await turnHighSecurity(true, "credHHHH");
    await expect(confirmation("credAAAA", "confirm", "passkey-remove:pk-credBBBB")).rejects.toThrow(WORDS.needsHardware);
    const removing = await server.fetch("/api/account/passkeys/pk-credBBBB", { method: "DELETE", body: JSON.stringify({ confirmation: early }) });
    expect(removing.status).toBe(403);
    expect((await removing.json()).detail).toBe(WORDS.needsHardware);
    await removePasskey("pk-credBBBB", "credHHHH");
  });

  it("takes a revocation that turns high security on only signed by a hardware key, while it is off too", async () => {
    const { ring } = await turnedOn("credAAAA");                        // a device passkey
    await enrolled(ring, "credHHHH", "hardware");
    const r = new K.Revoke(await K.follow(ring, await view()), await view(), { highSecurity: true });
    await r.prepare();
    auth.choose = () => "credAAAA";                                    // a browser that answers with another passkey
    await expect(r.signAndSend()).rejects.toThrow(WORDS.needsHardwareToTurn);
    expect(server.pending).toBeNull();
  });

  it("checks a change again as its hold ends: turning it on again, placed before a passkey was removed, is refused then", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    await turnedOff();
    const again = new K.TurnOnAgain(unlocked, await view());
    await again.prepare();
    expect(again.rekey).toBe(false);
    auth.choose = () => "credAAAA";
    await again.signAndSend(true);                                     // the content key alone, waiting on its hold
    await removePasskey("pk-credBBBB", "credAAAA");
    expect((await view()).pending?.change).toBe("turn-on");            // not made by the passkey removed: it waits on
    server.settle();
    expect(server.refused).toEqual([WORDS.rekeyDue]);
    const v = await view();
    expect(v.on).toBe(false);
    expect(v.rekey_due).toBe(true);
  });

  it("ends a change that waits when the passkey that made it is removed", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const unlocked = await K.follow(ring, await view());
    await turnedOff();
    const again = new K.TurnOnAgain(unlocked, await view());
    await again.prepare();
    auth.choose = () => "credAAAA";
    await again.signAndSend(true);
    await removePasskey("pk-credAAAA", "credBBBB");
    expect(server.pending).toBeNull();
    expect((await view()).on).toBe(false);
  });

  it("takes a new master key only if it is new, keeps a hardware key while high security is or will be on, and turns it on with no passkey waiting to be judged", async () => {
    const { ring, code } = await turnedOn("credHHHH", "hardware");
    const first = await masterBytes(code);
    await revoked(ring, {});
    const now = await K.follow(ring, await view());
    // a "new" master key that is the first one again: the chain names it
    const fill = crypto.getRandomValues.bind(crypto);
    let once = true;
    vi.spyOn(crypto, "getRandomValues").mockImplementation(<T extends ArrayBufferView | null>(a: T): T => {
      fill(a as unknown as Uint8Array);
      if (once && a instanceof Uint8Array && a.length === 32) { once = false; a.set(first); }
      return a;
    });
    const looped = new K.Revoke(now, await view(), {});
    await looped.prepare();
    vi.restoreAllMocks();
    await expect(looped.signAndSend()).rejects.toThrow(WORDS.newMaster);
    // a passkey waiting to be judged, while high security is turned on
    passkey("credJJJJ", "new key", "hardware");
    server.passkeys.find((p) => p.id === "pk-credJJJJ")!.judge_again = true;
    const judged = new K.Revoke(now, await view(), { highSecurity: true });
    await judged.prepare();
    await expect(judged.signAndSend()).rejects.toThrow(WORDS.keyWaiting);
    server.passkeys = server.passkeys.filter((p) => p.id !== "pk-credJJJJ");
    // high security turned on, its hold ending after the hardware key it keeps is no longer one: refused then
    const high = new K.Revoke(now, await view(), { highSecurity: true });
    await high.prepare();
    await high.signAndSend();
    server.passkeys.find((p) => p.id === "pk-credHHHH")!.tier = "device";
    server.settle();
    expect(server.refused).toEqual([WORDS.noHardware]);
    expect(server.high).toBe(false);
    expect(server.roster!.mk_id).toBe(now.master.id);
  });

  it("takes renewed anchors of a salt of 32 bytes and a wrap of 28 to 1,024, each passkey once", async () => {
    const { ring } = await turnedOn("credAAAA");
    const anchor = (wrapped: Uint8Array) => ({ passkey_id: "pk-credAAAA", salt: C.b64u(C.random(32)), wrapped: C.b64u(wrapped) });
    for (const anchors of [[anchor(C.random(27))], [anchor(C.random(1025))], [anchor(C.random(60)), anchor(C.random(60))]]) {
      expect(await status("/api/ade/e2e/anchors", "POST", { anchors, mk_id: ring.master.id, confirmation: await confirmation("credAAAA", "e2e-anchor-touch") })).toBe(422);
    }
    expect(await status("/api/ade/e2e/anchors", "POST", { anchors: [anchor(C.random(60))], mk_id: ring.master.id,
      confirmation: await confirmation("credAAAA", "e2e-anchor-touch") })).toBe(200);
  });
});

describe("the challenge a touch that confirms signs", () => {
  /** The server's answers to the options for a confirmation, as they reach the page: `weaken` says user verification
   *  is discouraged and the prompt lasts a second, which the page does not take. */
  function optionsServed(weaken = false) {
    const served: { challenge: string }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const r = await server.fetch(url, init);
      if (url !== "/api/account/confirm/options" || !r.ok) return r;
      const o = await r.json();
      served.push(o.publicKey);
      if (weaken) Object.assign(o.publicKey, { userVerification: "discouraged", timeout: 1000 });
      return new Response(JSON.stringify(o), { status: 200 });
    });
    return served;
  }
  const signed = (i: number) => C.b64u(auth.ceremonies[i].challenge);
  const derived = async (purpose: Parameters<typeof derivedChallenge>[0], served: { challenge: string }) => C.b64u(await derivedChallenge(purpose, served.challenge));

  it("adding a passkey: its first touch signs the page's own challenge, its second the challenge derived for e2e-enrol-own, fetched as the first ends, and the touch that confirms it the challenge derived for e2e-enrol-touch, fetched after them", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB", "Security key", "hardware");
    const served = optionsServed(true);
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    expect(posted("/api/account/confirm/options")).toHaveLength(0);
    auth.ceremonies.length = 0;
    await e.firstTouch();
    expect(posted("/api/account/confirm/options").map((c) => c.body)).toEqual([{ purpose: "e2e-enrol-own" }]);
    await e.secondTouch();
    expect(bodyOf("/api/account/confirm/options")).toEqual({ purpose: "e2e-enrol-touch" });
    await e.confirmAndSend();
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"], ["credBBBB"], ["credAAAA"]]);
    expect(auth.ceremonies[0].challenge).toHaveLength(32);
    expect(signed(1)).toBe(await derived("e2e-enrol-own", served[0]));
    expect(signed(2)).toBe(await derived("e2e-enrol-touch", served[1]));
    expect([served[0].challenge, served[1].challenge, signed(1), signed(2)]).not.toContain(signed(0));
    expect([served[0].challenge, served[1].challenge, signed(0), signed(1)]).not.toContain(signed(2));
    expect(auth.ceremonies.map((c) => c.userVerification)).toEqual(["required", "required", "required"]);
  });

  it("renewing anchors: each touch signs a challenge of its own derived for e2e-anchor-touch, fetched before it", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const served = optionsServed(true);
    const renew = new K.RenewAnchors(await K.follow(ring, await view()), await view());
    await renew.prepare();
    expect(bodyOf("/api/account/confirm/options")).toEqual({ purpose: "e2e-anchor-touch" });
    auth.ceremonies.length = 0;
    auth.choose = () => "credAAAA";
    await renew.touch("credAAAA");
    auth.choose = () => "credBBBB";
    await renew.touch("credBBBB");
    expect(signed(0)).toBe(await derived("e2e-anchor-touch", served[0]));
    expect(signed(1)).toBe(await derived("e2e-anchor-touch", served[1]));
    expect(signed(1)).not.toBe(signed(0));
    expect(auth.ceremonies.map((c) => c.userVerification)).toEqual(["required", "required"]);
    // the test server took each as derived for that, and as its own passkey's
    expect([...renew.confirmations.keys()]).toEqual(["pk-credAAAA", "pk-credBBBB"]);
    for (const c of renew.confirmations.values()) expect(c).toMatch(/^confirmation-/);
  });

  it("cancelling the change that waits: one touch over the challenge derived for e2e-cancel, user verification required whatever the server said", async () => {
    await turnedOn("credAAAA");
    server.pending = { id: "hold-9", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    const served = optionsServed(true);
    const c = new K.CancelChange(ACCOUNT, await view(), (await view()).pending!);
    await c.prepare();
    expect(bodyOf("/api/account/confirm/options")).toEqual({ purpose: "e2e-cancel" });
    auth.ceremonies.length = 0;
    await c.withPasskey();
    expect(server.pending).toBeNull();
    expect(signed(0)).toBe(await derived("e2e-cancel", served[0]));
    expect(auth.ceremonies[0].userVerification).toBe("required");
  });
});

describe("a turning-on in the place of the one this tab made", () => {
  const MINE = "a".repeat(32);
  const THEIRS = "b".repeat(32);
  /** The change that waits, as the page reads it from the server's view. */
  const waits = (change: string, body?: unknown) =>
    K.readView({ pending: { id: "hold-1", change, takes_effect_at: 1, made_by: "pk-x", ...(body === undefined ? {} : { body }) } }).pending;

  it("is a turning-on whose body names another master key, or none", () => {
    expect(K.turnOnInPlace(waits("turn-on", { mk_id: THEIRS }), MINE)).toBe(true);
    expect(K.turnOnInPlace(waits("turn-on", { content_key: {} }), MINE)).toBe(true);     // names none: not this tab's
    expect(K.turnOnInPlace(waits("turn-on", { mk_id: MINE }), MINE)).toBe(false);
  });

  it("is not said of a body the server does not serve, of another change, or of nothing waiting", () => {
    expect(K.turnOnInPlace(waits("turn-on"), MINE)).toBe(false);
    expect(K.turnOnInPlace(waits("turn-on", "not an object"), MINE)).toBe(false);
    expect(K.turnOnInPlace(waits("rotate", { mk_id: THEIRS }), MINE)).toBe(false);
    expect(K.turnOnInPlace(null, MINE)).toBe(false);
  });
});

describe("a content key or a roster that does not open", () => {
  const slot = `proofpane-e2e/1|${ACCOUNT}|link-1|event|step|||${"a".repeat(32)}`;
  const said = C.utf8('{"said_at":null,"session":null,"text":"Built it."}');
  /** A wrap or a roster as one who stores what does not open would send it. */
  const junk = (n = 80) => C.b64u(C.random(n));
  /** A content key pair, extractable, with its key_id. */
  const pair = async () => {
    const p = await C.newPair(true);
    return { ...p, id: await C.keyIdOf(p.pub) };
  };
  /** A rotation, signed and taken into effect: the key it states. */
  async function rotated(ring: K.Ring): Promise<string> {
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    return bodyOf("/api/ade/e2e/rotate").content_key.key_id as string;
  }

  it("unlocks past a content key that does not open, whichever way it fails, and reads what the others open", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const [heldId, { EK }] = [...ring.keys][0];
    const m = await C.masterOf(await masterBytes(code), ACCOUNT);
    const [a, b, c, d] = [await pair(), await pair(), await pair(), await pair()];
    const other = await C.newPair(true);
    const wrongId = C.hex(C.random(16));
    server.contentKeys.push(
      // its key_id not that of the key it names
      { key_id: wrongId, public_key: C.b64u(a.pub), mk_id: m.id, wrapped: await C.wrapContentKey(m, a.priv, wrongId, ACCOUNT) },
      // the key it names not one
      { key_id: b.id, public_key: "!!", mk_id: m.id, wrapped: await C.wrapContentKey(m, b.priv, b.id, ACCOUNT) },
      // its wrap not one
      { key_id: c.id, public_key: C.b64u(c.pub), mk_id: m.id, wrapped: junk() },
      // its wrap holding another key: stored so by one who holds the master key
      { key_id: d.id, public_key: C.b64u(d.pub), mk_id: m.id, wrapped: await C.wrapContentKey(m, other.priv, d.id, ACCOUNT) },
    );
    const now = await unlockAs("credAAAA");
    expect(now.unopened).toEqual([wrongId, b.id, c.id, d.id]);
    expect([...now.keys.keys()]).toEqual([heldId]);
    expect(now.unreached).toEqual([]);
    expect(now.roster!.credentials.map((x) => x.credential_id)).toEqual(["credAAAA"]);
    expect(await K.openWith(now, await C.sealEnvelope(EK, said, slot), slot)).toEqual({ ok: true, value: { said_at: null, session: null, text: "Built it." } });
    expect(await K.openWith(now, await C.sealEnvelope(c.pub, said, slot), slot)).toEqual({ ok: false, why: K.KEY_UNOPENED });
    expect(await K.openWith(now, await C.sealEnvelope(d.pub, said, slot), slot)).toEqual({ ok: false, why: K.KEY_UNOPENED });
    expect(await K.openWith(now, await C.sealEnvelope(other.pub, said, slot), slot)).toEqual({ ok: false, why: K.NOT_HELD });
    // a tab open before them follows them and stays open
    expect((await K.follow(ring, await view())).unopened).toEqual([wrongId, b.id, c.id, d.id]);
  });

  it("once a rotation whose wrap does not open takes effect: the tab reads the rest, and states no key from these keys", async () => {
    const { ring } = await turnedOn("credAAAA");
    const first = [...ring.keys.keys()][0];
    const fresh = await rotated(ring);
    // the thief's rotation, as one who holds a passkey and the master key sends it: its wrap not one
    server.contentKeys.find((k) => k.key_id === fresh)!.wrapped = junk();
    const now = await K.follow(ring, await view());
    expect(now.unopened).toEqual([fresh]);
    expect([...now.keys.keys()]).toEqual([first]);
    const v = await view();
    expect(K.newestUnopened(now, v)).toBe(true);
    expect(K.newestUnopened(ring, v)).toBe(false);                     // a ring that lists it not as unopened
    await expect(new K.Rotate(now, v).prepare()).rejects.toThrow(K.NEWEST_UNOPENED);
    await expect(new K.Revoke(now, v, {}).prepare()).rejects.toThrow(K.NEWEST_UNOPENED);
    expect((await unlockAs("credAAAA")).unopened).toEqual([fresh]);
    expect(posted("/api/ade/e2e/rotate")).toHaveLength(1);
  });

  it("an older content key that does not open stops no revocation: it is left as it is, and every other wrapped again", async () => {
    const { ring } = await turnedOn("credAAAA");
    const first = [...ring.keys.keys()][0];
    const fresh = await rotated(ring);
    server.contentKeys.find((k) => k.key_id === first)!.wrapped = junk();
    const now = await K.follow(ring, await view());
    expect(now.unopened).toEqual([first]);
    expect(K.newestUnopened(now, await view())).toBe(false);
    const { body } = await revoked(now, {});
    expect(body.abandon).toEqual([first]);
    expect(body.content_keys.map((k: Body) => k.key_id)).toEqual([fresh]);
    const after = await K.follow(now, await view());
    expect(after.master.id).toBe(body.mk_id);
    expect(after.unopened).toEqual([first]);                           // under the master key it was listed with
    expect([...after.keys.keys()].sort()).toEqual([fresh, body.content_key.key_id].sort());
  });

  it("an unreached key and one that does not open are both left as they are", async () => {
    const { ring } = await turnedOn("credAAAA");
    const first = [...ring.keys.keys()][0];
    const fresh = await rotated(ring);
    server.contentKeys.find((k) => k.key_id === first)!.wrapped = junk();
    const stranger = await C.masterOf(C.random(32), ACCOUNT);
    const s = await pair();
    server.contentKeys.push({ key_id: s.id, public_key: C.b64u(s.pub), mk_id: stranger.id, wrapped: await C.wrapContentKey(stranger, s.priv, s.id, ACCOUNT) });
    const now = await K.follow(ring, await view());
    expect([now.unreached, now.unopened]).toEqual([[s.id], [first]]);
    const { body } = await revoked(now, {});
    expect(body.abandon).toEqual([s.id, first]);
    expect(body.content_keys.map((k: Body) => k.key_id)).toEqual([fresh]);
  });

  it("a roster that does not open: the tab reads, makes nothing that seals to it or rewrites it, rotates, and checks no waiting change that would", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    // a revocation that waits, made from a tab whose roster opened
    const r = new K.Revoke(ring, await view(), {});
    await r.prepare();
    await r.signAndSend();
    server.roster = { ...server.roster!, sealed: junk(120) };      // as an enrolment from one who holds the master key could leave it
    const now = await K.follow(ring, await view());
    expect(now.roster).toBeNull();
    expect(now.rosterWhy).toBe(K.ROSTER_NOT_OPEN);
    expect(now.keys.size).toBe(1);
    const [, { EK }] = [...now.keys][0];
    expect(await K.openWith(now, await C.sealEnvelope(EK, said, slot), slot)).toMatchObject({ ok: true });
    const v = await view();
    expect(v.pending?.change).toBe("revoke");
    expect(await K.checkWaiting(now, v)).toBeNull();                   // whom it seals to: nothing to compare it with
    expect(await K.checkWaiting(ring, v)).toMatchObject({ state: "opens" });
    await expect(new K.Enrol(now, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!).prepare()).rejects.toThrow(K.ROSTER_NOT_OPEN);
    await expect(new K.Revoke(now, v, {}).prepare()).rejects.toThrow(K.ROSTER_NOT_OPEN);
    await expect(new K.RenewAnchors(now, v).prepare()).rejects.toThrow(K.ROSTER_NOT_OPEN);
    expect(K.rosterAfter(now, v)).toEqual({ kept: [], leaving: [] });
    expect(K.isNamed(now, v.credentials[0])).toBe(false);
    // a rotation seals to no roster: made, and waits in that one's place
    await new K.Rotate(now, v).prepare();
    // nor does the server list a roster for the current master key at all: a rotation is still made from it
    const bare = { ...v, roster: [] };
    expect((await K.follow(ring, bare)).rosterWhy).toBe(K.ROSTER_NOT_OPEN);
    await new K.Rotate(await K.follow(ring, bare), bare).prepare();
    // a turning-on that makes a new master key, waiting, is no more checked from it than a revocation
    const rekey = { ...v, pending: { ...v.pending!, change: "turn-on", body: { ...v.pending!.body!, shape: "rekey" } } };
    expect(await K.checkWaiting(now, rekey)).toBeNull();
    expect(await K.checkWaiting(ring, rekey)).toMatchObject({ state: "opens" });
  });

  it("checks no waiting passkey being added from a ring without a roster", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    await e.secondTouch();
    auth.choose = prefer("credAAAA");
    await e.confirmAndSend();                                          // it waits on its hold
    server.roster = { ...server.roster!, sealed: junk(120) };
    const now = await K.follow(ring, await view());
    expect((await view()).pending?.change).toBe("credentials");
    expect(await K.checkWaiting(now, await view())).toBeNull();
    expect(await K.checkWaiting(ring, await view())).toMatchObject({ state: "opens" });
  });

  it("a waiting rotation is checked from a ring without a roster", async () => {
    const { ring } = await turnedOn("credAAAA");
    server.roster = { ...server.roster!, sealed: junk(120) };
    const now = await K.follow(ring, await view());
    const r = new K.Rotate(now, await view());
    await r.prepare();
    await r.signAndSend();
    expect(await K.checkWaiting(now, await view())).toMatchObject({ state: "opens" });
  });

  it("refuses an enrolment's last step too, made from a ring whose roster went since", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    await e.secondTouch();
    (ring as { roster: K.Ring["roster"]; rosterWhy: string }).roster = null;
    (ring as { rosterWhy: string }).rosterWhy = K.ROSTER_LACKS_THIS;
    auth.choose = prefer("credAAAA");
    await expect(e.confirmAndSend()).rejects.toThrow(K.ROSTER_LACKS_THIS);
    expect(posted("/api/ade/e2e/credentials")).toHaveLength(0);
  });

  it("does not turn encryption on again from a ring without a roster", async () => {
    const { ring } = await turnedOn("credAAAA");
    await turnedOff();
    server.roster = { ...server.roster!, sealed: junk(120) };
    const now = await K.follow(ring, await view());
    await expect(new K.TurnOnAgain(now, await view()).prepare()).rejects.toThrow(K.ROSTER_NOT_OPEN);
  });
});
