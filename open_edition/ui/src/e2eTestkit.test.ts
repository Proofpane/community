// The page's test server (e2eTestkit.ts) holds to the server's rules as the page's tests rely on it to (ade/e2e_keys.py):
// who may cancel the change that waits, by the second rule, the recovery code the root; a change of any kind taking the
// waiting one's place only as (v) allows, what a refused one was asked with left unused; the signer or confirmer that
// unlocks the keys and stays, or the code's proof; the long holds; the bound on routine rotations; renewed anchors; a
// change whose time has come taking effect first; and what GET serves of the waiting change. The bodies are made here
// as the routes take them, so these hold whatever the page's flows do.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { assertionWith, canonicalJson, confirmCeremony, type ConfirmPurpose, confirmWithPasskey, stepChallenge } from "./passkey";
import { ANCHOR_CODE, ANCHOR_OWN, CONFIRM_STAYING, ENROL_OWN, FakeAuthenticator, FakeServer, NO_PLACE, NOT_STAYING, SALT_SAME, WORDS, nextWords } from "./e2eTestkit";
import { CHALLENGE_TTL, CLASSICAL, CLASSICAL_AFTER, CLASSICAL_FORMS, ERA_CHANGED, EVENT_PLAIN, HISTORY_PLAIN, HISTORY_WITHHELD_PLAIN, NO_DEVICE_WAITING, NO_MLKEM_WORDS,
  NO_PASSKEY, NOT_ON, OLD_PAGE, PROOF_STALE, PROOF_WRONG, RESEAL_CHANGED, RESEAL_GONE, RESEAL_NOT_PQ, RESEAL_OFF, RESEAL_WHERE, TALK_PLAIN } from "./e2eTestkit";

const ACCOUNT = "acct-1";
const DAY = 86400;
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

type Body = Record<string, any>;           // eslint-disable-line @typescript-eslint/no-explicit-any
const view = () => K.E2EApi.view();
const bytes = (n: number) => C.b64u(C.random(n));
/** `n` bytes where only the length is read (a roster beyond what getRandomValues gives at once, 65,536 bytes). */
const sized = (n: number) => C.b64u(new Uint8Array(n).fill(0x5a));
const envelope =() => C.PREFIX + C.b64u(new Uint8Array([1, ...C.random(C.OVERHEAD + 31)]));

async function post(path: string, body: unknown): Promise<{ status: number; detail?: string }> {
  const r = await server.fetch(path, { method: "POST", body: JSON.stringify(body) });
  return { status: r.status, detail: ((await r.json()) as Body).detail };
}

/** One of the account's passkeys, and the browser's credential for it: its id. */
function passkey(id: string, tier: "device" | "hardware" = "device") {
  if (!server.passkeys.some((p) => p.credential_id === id)) server.addPasskey(id, id, tier);
  if (!auth.creds.has(id)) auth.add(id);
  return `pk-${id}`;
}

/** Encryption turned on with `id`'s passkey, its hold ended: the recovery code as it was shown. */
async function turnedOn(id = "credAAAA", tier: "device" | "hardware" = "device") {
  passkey(id, tier);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  return code;
}

/** Encryption turned on with `id`'s passkey as the page turns it on now (post-quantum), its hold ended: the code. */
async function turnedOnPq(id = "credAAAA") {
  passkey(id);
  const t = new K.TurnOn(ACCOUNT);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = () => id;
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  expect(server.pq).toBe(true);
  return code;
}

/** A passkey enrolled as the server's rows keep one, for a test's setup: its credential and the master key's grant. */
function enrolled(id: string, tier: "device" | "hardware" = "device") {
  const passkeyId = passkey(id, tier);
  server.credentials.push({ passkey_id: passkeyId, credential_id: id, salt: bytes(32), public_key: bytes(32), wrapped: bytes(60), created_at: server.now });
  server.grants.push({ mk_id: server.roster!.mk_id, to: id, sealed: envelope(), created_at: server.now });
  return passkeyId;
}

/** A confirmation made with `id`'s passkey over the challenge derived for `purpose`: an account change's, made for the
 *  change `change` names (passkeys.CHANGE), as Account makes one, or one of encryption's touches, as the page's flows
 *  make theirs. The server spends each on a change of its own purpose only (passkeys.CONFIRM_PURPOSES),
 *  and an account change's on the change it was made for alone. */
async function confirmation(id: string, purpose: ConfirmPurpose = "confirm", change?: string) {
  auth.choose = () => id;
  if (purpose === "confirm") return confirmWithPasskey(false, change ? { change } : undefined);
  const c = await confirmCeremony(purpose);
  const { credential } = await assertionWith(c.ask);
  return (await K.E2EApi.confirm(c.challenge_id, credential)).confirmation;
}

const proof = (code: string, purpose: K.RecoveryPurpose) => K.recoveryProof(ACCOUNT, purpose, { code });

/** A statement `signer` signs, the next of the chain as `v` serves it (read now when not given), and its content key
 *  under a master key; `EK`, the key it states (a fresh X25519 key's bytes unless given). */
async function signed(signer: string, purpose: "e2e-key" | "e2e-off", retires: "all" | "grace", v?: K.E2EView, EK: Uint8Array = C.random(32)) {
  const keyId = await C.keyIdOf(EK);
  const p = await K.prepareStatement(v ?? (await view()), purpose, purpose === "e2e-key" ? { EK, keyId, retires } : undefined);
  auth.choose = () => signer;
  return { statement: await K.signStatement(p, [signer]), content: (mk: string) => ({ key_id: keyId, public_key: C.b64u(EK), mk_id: mk, wrapped: bytes(60) }) };
}

interface By { code?: string; v?: K.E2EView }

async function revocation(signer: string, o: By & { remove?: string[]; high?: boolean } = {}) {
  const { statement, content } = await signed(signer, "e2e-key", "all", o.v);
  const mk = C.hex(C.random(16));
  const remove = o.remove ?? [];
  const hardwareOnly = !!o.high || server.high;
  const stay = server.credentials.filter((c) => {
    const p = server.passkeys.find((k) => k.id === c.passkey_id);
    return !!p && !remove.includes(p.id) && (!hardwareOnly || p.tier === "hardware");
  });
  return { statement, mk_id: mk, content_key: content(mk), chain: { previous: server.roster!.mk_id, wrapped: bytes(60) },
    remove, high_security: !!o.high, grants: [...stay.map((c) => c.credential_id), "recovery"].map((to) => ({ to, sealed: envelope() })),
    content_keys: server.contentKeys.map((k) => ({ key_id: k.key_id, wrapped: bytes(60) })), roster: bytes(60),
    ...(o.code ? { recovery_proof: await proof(o.code, "revoke") } : {}) };
}

async function rotation(signer: string, o: By = {}) {
  const { statement, content } = await signed(signer, "e2e-key", "grace", o.v);
  return { statement, content_key: content(server.roster!.mk_id), ...(o.code ? { recovery_proof: await proof(o.code, "rotate") } : {}) };
}

async function turningOff(signer: string, o: By = {}) {
  const { statement } = await signed(signer, "e2e-off", "all", o.v);
  return { statement, ...(o.code ? { recovery_proof: await proof(o.code, "off") } : {}) };
}

/** A confirmation made with `id`'s passkey over the challenge derived for e2e-enrol-own, as the page's second touch of a
 *  passkey being added makes it. */
async function ownTouch(id: string) {
  return confirmation(id, "e2e-enrol-own");
}

/** Adding `id`'s passkey to encryption, confirmed by a passkey or the recovery code; its credential with the added
 *  passkey's own confirmation (ownTouch), or with `own` in its place (null for none). */
async function enrolment(id: string, by: { passkey?: string; code?: string; own?: string | null }) {
  const passkeyId = passkey(id);
  const own = by.own === undefined ? await ownTouch(id) : by.own;
  return { credential: { passkey_id: passkeyId, salt: bytes(32), public_key: bytes(32), wrapped: bytes(60), ...(own === null ? {} : { confirmation: own }) },
    mk_id: server.roster!.mk_id, grant: envelope(), roster: bytes(60),
    ...(by.code ? { recovery_proof: await proof(by.code, "credentials") } : { confirmation: await confirmation(by.passkey!, "e2e-enrol-touch") }) };
}

/** Renewed anchors, each a fresh salt unless one is given, confirmed by a passkey or the recovery code. */
async function renewal(anchors: { passkey_id: string; salt?: string; confirmation?: string; anchor?: string }[], by: { passkey?: string; code?: string }) {
  return { anchors: anchors.map((a) => ({ salt: bytes(32), wrapped: bytes(60), ...a })), mk_id: server.roster!.mk_id,
    ...(by.code ? { recovery_proof: await proof(by.code, "anchors") } : { confirmation: await confirmation(by.passkey!, "e2e-anchor-touch") }) };
}

/** A confirmation made with `id`'s passkey for a renewed anchor, as the page's touch of each renewed passkey makes it. */
const anchorTouch = (id: string) => confirmation(id, "e2e-anchor-touch");

const cancelWithCode = async (code: string) =>
  post(`/api/ade/holds/${server.pending!.id}/cancel`, { recovery_proof: await proof(code, "cancel") });

describe("who may cancel the change that waits, by the server's second rule", () => {
  const waiting = (o: Partial<NonNullable<FakeServer["pending"]>>) => {
    server.pending = { id: "hold-w", change: "revoke", takes_effect_at: server.now + 180, apply: () => {}, ...o };
    return server.view().pending;
  };

  it("the code alone for a code-backed change; else the passkeys it does not remove, and those it removes when it leaves none but its signer", async () => {
    await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    const b = passkey("credBBBB");
    const c = passkey("credCCCC");
    expect(waiting({ removes: [a], madeBy: b })).toMatchObject({ cancel_by: [b, c], cancel_by_code: true, made_by: b, by_code: false });
    expect(waiting({ removes: [a, c], madeBy: b }).cancel_by).toEqual([a, b, c]);             // (iii), the code not asked
    expect(waiting({ removes: [a], madeBy: b, byCode: true })).toMatchObject({ cancel_by: [], cancel_by_code: true, by_code: true });
    expect(waiting({ change: "credentials", madeBy: "recovery" })).toMatchObject({ cancel_by: [], by_code: true });
    expect(waiting({ change: "off", madeBy: b }).cancel_by).toEqual([a, b, c]);               // it removes none
  });

  it("only the hardware keys while high security is on, and every other passkey as removed by a change that really turns it on", async () => {
    await turnedOn("credAAAA");
    const h = passkey("credHHHH", "hardware");
    const j = passkey("credJJJJ", "hardware");
    // turning it on, signed by h: the phone goes with it, and j stays, so the phone may not cancel it; GET says what it
    // removes (R) and whether it replaces the code, which decides nothing
    expect(waiting({ removes: [], turnsHigh: true, madeBy: h }).cancel_by).toEqual([h, j]);
    expect(waiting({ removes: [], turnsHigh: true, madeBy: h, removesCode: true })).toMatchObject(
      { cancel_by: [h, j], removes: ["pk-credAAAA"], replaces_code: true });
    expect(waiting({ change: "rotate", madeBy: h })).toMatchObject({ removes: [], replaces_code: false });
    server.high = true;
    // on: the phone counts nowhere, so h's revocation of j leaves no passkey but h, and j may cancel it too
    expect(waiting({ removes: [j], madeBy: h }).cancel_by).toEqual([h, j]);
  });
});

describe("a change placed", () => {
  it("in the waiting one's place only when its signer or confirmer could cancel that one, or with the code's proof; refused, what it was asked with stays unused", async () => {
    const code = await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    const b = enrolled("credBBBB");
    const c = enrolled("credCCCC");
    // b's revocation of a: c stays, so a may not cancel it
    expect((await post("/api/ade/e2e/revoke", await revocation("credBBBB", { remove: [a] }))).status).toBe(200);
    expect(server.view().pending.cancel_by).toEqual([b, c]);
    const off = await turningOff("credAAAA");
    const enrol = await enrolment("credDDDD", { passkey: "credAAAA" });
    const own = await confirmation("credAAAA", "e2e-cancel");
    const anchors = await renewal([{ passkey_id: b, confirmation: await anchorTouch("credBBBB") }], { passkey: "credAAAA" });
    expect(await post("/api/ade/e2e/off", off)).toMatchObject({ status: 409, detail: NO_PLACE });
    expect(await post("/api/ade/e2e/credentials", enrol)).toMatchObject({ status: 409, detail: NO_PLACE });
    expect(await post("/api/ade/e2e/anchors", anchors)).toMatchObject({ status: 409, detail: NO_PLACE });
    expect(server.pending).toMatchObject({ change: "revoke", madeBy: b });
    // c, which may cancel it, takes its place
    expect((await post("/api/ade/e2e/off", await turningOff("credCCCC"))).status).toBe(200);
    expect(server.pending).toMatchObject({ change: "off", madeBy: c, byCode: false });
    // the statement a refused one was signed with, now with the code's proof: its nonce was left unused
    expect((await post("/api/ade/e2e/off", { ...off, recovery_proof: await proof(code, "off") })).status).toBe(200);
    expect(server.pending).toMatchObject({ change: "off", madeBy: a, byCode: true });
    expect(await post("/api/ade/e2e/off", off)).toMatchObject({ status: 400 });          // used now, as it was placed
    // code-backed: no passkey's change takes its place, nor does any passkey cancel it
    expect(await post("/api/ade/e2e/credentials", enrol)).toMatchObject({ status: 409, detail: NO_PLACE });
    expect(await post(`/api/ade/holds/${server.pending!.id}/cancel`, { confirmation: own })).toMatchObject({ status: 403 });
    expect((await cancelWithCode(code)).status).toBe(200);
    // the confirmations a refused change was asked with, unused, are taken now
    expect((await post("/api/ade/e2e/anchors", anchors)).status).toBe(200);
    server.pending = null;
    expect((await post("/api/ade/e2e/credentials", enrol)).status).toBe(200);
  });

  it("signed or confirmed by a passkey that unlocks the keys and stays after it, or with the code's proof", async () => {
    const code = await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    const b = enrolled("credBBBB");
    passkey("credCCCC");                                                 // the account's, not added to encryption
    expect(await post("/api/ade/e2e/off", await turningOff("credCCCC"))).toMatchObject({ status: 403, detail: NOT_STAYING });
    expect(await post("/api/ade/e2e/rotate", await rotation("credCCCC"))).toMatchObject({ status: 403, detail: NOT_STAYING });
    expect(await post("/api/ade/e2e/revoke", await revocation("credBBBB", { remove: [b] }))).toMatchObject({ status: 403, detail: NOT_STAYING });
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credCCCC" }))).toMatchObject({ status: 403, detail: CONFIRM_STAYING });
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a, confirmation: await anchorTouch("credAAAA") }], { passkey: "credCCCC" })))
      .toMatchObject({ status: 403, detail: CONFIRM_STAYING });
    expect(server.pending).toBeNull();
    // with the code's proof each is taken, code-backed, each in the place of the one before
    expect((await post("/api/ade/e2e/revoke", await revocation("credBBBB", { remove: [b], code }))).status).toBe(200);
    expect(server.view().pending).toMatchObject({ change: "revoke", made_by: b, by_code: true, cancel_by: [] });
    expect((await post("/api/ade/e2e/off", await turningOff("credCCCC", { code }))).status).toBe(200);
    expect((await post("/api/ade/e2e/rotate", await rotation("credCCCC", { code }))).status).toBe(200);
    expect((await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { code }))).status).toBe(200);
    expect(server.view().pending).toMatchObject({ change: "credentials", made_by: "recovery", by_code: true, cancel_by: [] });
    expect(server.pending!.takes_effect_at - server.now).toBe(DAY);      // the code's hold
  });

  it("while high security is on, confirmed by a hardware key that unlocks the keys", async () => {
    await turnedOn("credAAAA");
    enrolled("credJJJJ", "hardware");
    passkey("credHHHH", "hardware");
    server.high = true;                    // the phone's credential no longer stays (the server's _stale), until it is dropped
    await expect(confirmation("credAAAA")).rejects.toThrow(WORDS.needsHardware);      // a phone confirms nothing then
    expect(await post("/api/ade/e2e/credentials", await enrolment("credHHHH", { passkey: "credHHHH" }))).toMatchObject({ status: 403, detail: CONFIRM_STAYING });
    expect((await post("/api/ade/e2e/credentials", await enrolment("credHHHH", { passkey: "credJJJJ" }))).status).toBe(200);
  });

  it("asks its signer or confirmer again as its hold ends, and keeps nothing of it when that one no longer unlocks the keys", async () => {
    const code = await turnedOn("credAAAA");
    const b = enrolled("credBBBB");
    const c = enrolled("credCCCC");
    const unenrol = (id: string) => { server.credentials = server.credentials.filter((k) => k.passkey_id !== id); };
    const statements = () => server.statements.length;
    const before = statements();
    expect((await post("/api/ade/e2e/off", await turningOff("credBBBB"))).status).toBe(200);
    unenrol(b);
    server.settle();
    expect([server.refused.at(-1), statements()]).toEqual([NOT_STAYING, before]);
    enrolled("credBBBB");
    // a revocation's signer gone from the credentials it leaves: its grants no longer match them either, which the
    // server finds first, as it does (_revocation before _signer_still)
    expect((await post("/api/ade/e2e/revoke", await revocation("credBBBB", { remove: [c] }))).status).toBe(200);
    unenrol(b);
    server.settle();
    expect(statements()).toBe(before);
    enrolled("credBBBB");
    passkey("credDDDD");
    expect((await post("/api/ade/e2e/credentials", await enrolment("credDDDD", { passkey: "credBBBB" }))).status).toBe(200);
    unenrol(b);
    server.settle();
    expect(server.refused.at(-1)).toBe(CONFIRM_STAYING);
    expect(server.credentials.some((k) => k.passkey_id === "pk-credDDDD")).toBe(false);
    enrolled("credBBBB");
    expect((await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: b }], { passkey: "credBBBB" }))).status).toBe(200);
    unenrol(b);
    server.settle();
    expect(server.refused.at(-1)).toBe(CONFIRM_STAYING);
    enrolled("credBBBB");
    // sent with the code's proof, its signer is not asked whether it stays
    expect((await post("/api/ade/e2e/off", await turningOff("credBBBB", { code }))).status).toBe(200);
    unenrol(b);
    server.settle();
    expect(statements()).toBe(before + 1);
  });

  it("asks again as its hold ends whether its signer may sign at all: a hardware key's alone, once high security is on", async () => {
    await turnedOn("credAAAA");
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(200);
    server.high = true;
    server.settle();
    expect(server.refused.at(-1)).toBe(WORDS.onlyHardwareSigns);
    expect(server.statements).toHaveLength(1);
  });

  it("waits a day when it turns encryption off or removes no passkey, a turn of high security on where it is on among those", async () => {
    await turnedOn("credHHHH", "hardware");
    const j = enrolled("credJJJJ", "hardware");
    passkey("credAAAA");                                                 // a phone, not added to encryption
    const waits = () => server.pending!.takes_effect_at - server.now;
    expect((await post("/api/ade/e2e/off", await turningOff("credHHHH"))).status).toBe(200);
    expect(waits()).toBe(DAY);
    server.pending = null;
    expect((await post("/api/ade/e2e/revoke", await revocation("credHHHH"))).status).toBe(200);
    expect(waits()).toBe(DAY);                                           // it removes none
    server.pending = null;
    expect((await post("/api/ade/e2e/revoke", await revocation("credHHHH", { high: true }))).status).toBe(200);
    expect(waits()).toBe(180);                                           // it leaves the phone out
    expect(server.view().pending.cancel_by).toEqual(["pk-credHHHH", j]);            // which may not cancel it
    server.pending = null;
    server.high = true;
    expect((await post("/api/ade/e2e/revoke", await revocation("credHHHH", { high: true }))).status).toBe(200);
    expect(waits()).toBe(DAY);                                           // on already: it removes none
    server.pending = null;
    server.high = false;
    enrolled("credKKKK", "hardware");
    expect((await post("/api/ade/e2e/revoke", await revocation("credHHHH", { remove: [j] }))).status).toBe(200);
    expect(waits()).toBe(180);                                           // it removes j, and k stays
  });

  it("a routine rotation at most once in rotateEvery after the last that took effect, and a revocation without that bound", async () => {
    await turnedOn("credAAAA");
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(200);
    server.settle();
    expect(await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).toMatchObject({ status: 429, detail: expect.stringContaining("at most once in 24 hours") });
    expect((await post("/api/ade/e2e/revoke", await revocation("credAAAA"))).status).toBe(200);
    server.pending = null;
    server.now += DAY - 1;
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(429);
    server.now += 1;
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(200);
    server.settle();
    server.rotateEvery = 90;
    expect(await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).toMatchObject({ status: 429, detail: expect.stringContaining("at most once in 90 seconds") });
    server.rotateEvery = 0;
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(200);
  });

  it("renews anchors under fresh salts, each but the confirmer's own with its own passkey's confirmation, used up as they are placed", async () => {
    const code = await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    const b = enrolled("credBBBB");
    expect((await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a }], { passkey: "credAAAA" }))).status).toBe(200);
    server.pending = null;
    // b's anchor takes b's own touch: none, or a's, does not do
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a }, { passkey_id: b }], { passkey: "credAAAA" })))
      .toMatchObject({ status: 403, detail: ANCHOR_OWN });
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a }, { passkey_id: b, confirmation: await anchorTouch("credAAAA") }], { passkey: "credAAAA" })))
      .toMatchObject({ status: 403, detail: ANCHOR_OWN });
    const bs = await anchorTouch("credBBBB");
    expect((await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a }, { passkey_id: b, confirmation: bs }], { passkey: "credAAAA" }))).status).toBe(200);
    server.pending = null;
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: b, confirmation: bs }], { passkey: "credAAAA" })))
      .toMatchObject({ status: 403, detail: WORDS.confirmStale });        // used up: stale, as the server says
    // confirmed with the code, every anchor takes its own passkey's touch
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a }], { code }))).toMatchObject({ status: 403, detail: ANCHOR_OWN });
    expect((await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: a, confirmation: await anchorTouch("credAAAA") }], { code }))).status).toBe(200);
    server.pending = null;
    // never the salt the record has
    const salt = server.credentials.find((c) => c.passkey_id === b)!.salt;
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: b, salt }], { passkey: "credBBBB" }))).toMatchObject({ status: 422, detail: SALT_SAME });
  });

  it("adds a passkey only with a confirmation that passkey made itself, confirmed by a passkey or the code's proof alike, used up as the change is placed", async () => {
    const code = await turnedOn("credAAAA");
    enrolled("credBBBB");
    passkey("credCCCC");                                                 // the account's, not added to encryption
    // none, the confirmer's own, another passkey's: refused, and nothing waits
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own: null })))
      .toMatchObject({ status: 403, detail: ENROL_OWN });
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own: await ownTouch("credAAAA") })))
      .toMatchObject({ status: 403, detail: ENROL_OWN });
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own: await ownTouch("credBBBB") })))
      .toMatchObject({ status: 403, detail: ENROL_OWN });
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { code, own: null })))
      .toMatchObject({ status: 403, detail: ENROL_OWN });
    expect(server.pending).toBeNull();
    // its own, with a passkey's confirmation or the code's proof; used up as the change is placed, so cancelling the
    // change does not give it back
    const own = await ownTouch("credCCCC");
    expect((await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own }))).status).toBe(200);
    expect((await cancelWithCode(code)).status).toBe(200);
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { code, own })))
      .toMatchObject({ status: 403, detail: WORDS.confirmStale });       // used up: stale, as the server says
    expect((await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { code }))).status).toBe(200);
    server.settle();
    expect(server.credentials.some((k) => k.passkey_id === "pk-credCCCC")).toBe(true);
  });

  it("renews the recovery code's anchor only with the code's proof: a passkey's confirmation is refused before it is used, and the code's change waits a day", async () => {
    const code = await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    const codeAnchor = { public_key: server.recovery!.public_key, anchor: bytes(60) };
    const word = await anchorTouch("credAAAA");
    expect(await post("/api/ade/e2e/anchors", { anchors: [], mk_id: server.roster!.mk_id, confirmation: word, recovery_anchor: codeAnchor }))
      .toMatchObject({ status: 403, detail: ANCHOR_CODE });
    expect(await post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: a }], { passkey: "credAAAA" })), recovery_anchor: codeAnchor }))
      .toMatchObject({ status: 403, detail: ANCHOR_CODE });
    expect(server.pending).toBeNull();
    // the refused confirmation is unused: it confirms the passkey's own anchor alone
    expect((await post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: a }], { passkey: "credAAAA" })), confirmation: word })).status).toBe(200);
    server.pending = null;
    expect((await post("/api/ade/e2e/anchors", { ...(await renewal([], { code })), recovery_anchor: codeAnchor })).status).toBe(200);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
    expect(server.pending!.takes_effect_at - server.now).toBe(DAY);
    expect(server.view().pending.cancel_by).toEqual([]);
    server.settle();
    expect(server.recovery!.anchor).toBe(codeAnchor.anchor);
  });

  it("after a change whose time has come takes effect, never in its place", async () => {
    await turnedOn("credAAAA");
    const b = enrolled("credBBBB");
    let applied = 0;
    // one the code made, which no passkey's change could take the place of, its time come
    const due = () => { server.pending = { id: "hold-due", change: "credentials", takes_effect_at: server.now, apply: () => { applied += 1; }, madeBy: "recovery" }; };
    const v = await view();
    const asked: [string, Body][] = [
      ["/api/ade/e2e/off", await turningOff("credAAAA", { v })],
      ["/api/ade/e2e/rotate", await rotation("credAAAA", { v })],
      ["/api/ade/e2e/revoke", await revocation("credAAAA", { remove: [b], v })],
      ["/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA" })],
      ["/api/ade/e2e/anchors", await renewal([{ passkey_id: "pk-credAAAA" }], { passkey: "credAAAA" })],
    ];
    for (const [path, body] of asked) {
      due();
      expect([path, (await post(path, body)).status]).toEqual([path, 200]);
      server.pending = null;
    }
    expect((await post("/api/ade/e2e/off", await turningOff("credAAAA"))).status).toBe(200);
    server.settle();
    const { statement, content } = await signed("credAAAA", "e2e-key", "all");
    due();
    expect((await post("/api/ade/e2e/turn-on", { statement, content_key: content(server.roster!.mk_id) })).status).toBe(200);
    expect(applied).toBe(6);
  });
});

describe("what GET serves of the change that waits", () => {
  /** A statement as the server keeps it in a body (e2e_keys._stored). */
  const kept = (statement: { payload: Record<string, unknown>; credential: unknown }, passkeyId: string) =>
    ({ payload: canonicalJson(statement.payload), credential: JSON.stringify(statement.credential), passkey_id: passkeyId });
  /** The same statement as GET serves it in the body: without its assertion (e2e_keys._served_body). */
  const asServed = (statement: { payload: Record<string, unknown> }, passkeyId: string) =>
    ({ payload: canonicalJson(statement.payload), passkey_id: passkeyId });

  it("its body as the server keeps it, but each statement's signature, without what confirmed it; who made it, and whether the code's proof came with it", async () => {
    const code = await turnedOn("credAAAA");
    const off = await turningOff("credAAAA", { code });
    expect((await post("/api/ade/e2e/off", off)).status).toBe(200);
    const served = server.view().pending;
    expect(served).toMatchObject({ change: "off", made_by: "pk-credAAAA", by_code: true });
    expect(served.body).toEqual({ statement: asServed(off.statement, "pk-credAAAA") });
    expect(server.pending!.body).toEqual({ statement: kept(off.statement, "pk-credAAAA") });      // kept with it
    expect((await cancelWithCode(code)).status).toBe(200);
    // a passkey added: its credential as the server reads it (without its own confirmation), the grant and the roster,
    // and that it was placed with its own touch
    const enrol = await enrolment("credBBBB", { passkey: "credAAAA" });
    expect((await post("/api/ade/e2e/credentials", enrol)).status).toBe(200);
    expect(server.view().pending).toMatchObject({ change: "credentials", made_by: "pk-credAAAA", by_code: false });
    const { confirmation: ownWord, ...sent } = enrol.credential;
    expect(ownWord).toBeTruthy();
    expect(server.view().pending.body).toEqual({ credential: { ...sent, credential_id: "credBBBB", name: "credBBBB" },
      mk_id: enrol.mk_id, grant: enrol.grant, roster: enrol.roster, own_touch: true });
    expect((await cancelWithCode(code)).status).toBe(200);
    // a rotation: {statement, content_key}, as test_the_change_that_waits_is_served_whole_with_what_made_it reads it
    const rotate = await rotation("credAAAA");
    expect((await post("/api/ade/e2e/rotate", rotate)).status).toBe(200);
    expect(server.view().pending.body).toEqual({ statement: asServed(rotate.statement, "pk-credAAAA"), content_key: rotate.content_key });
    expect(server.pending!.body!.statement).toEqual(kept(rotate.statement, "pk-credAAAA"));
    expect((await cancelWithCode(code)).status).toBe(200);
    // a revocation with the code's proof: the grants by `to`, the content keys by key_id, the chain's link apart
    const revoke = await revocation("credAAAA", { code });
    expect((await post("/api/ade/e2e/revoke", revoke)).status).toBe(200);
    expect(server.view().pending).toMatchObject({ change: "revoke", made_by: "pk-credAAAA", by_code: true });
    expect(server.view().pending.body).toEqual({ mk_id: revoke.mk_id, previous: revoke.chain.previous, chain: revoke.chain.wrapped,
      grants: Object.fromEntries(revoke.grants.map((g) => [g.to, g.sealed])), roster: revoke.roster, content_key: revoke.content_key,
      content_keys: Object.fromEntries(revoke.content_keys.map((k) => [k.key_id, k.wrapped])), abandon: [], remove: [],
      high_security: false, recovery: null, statement: asServed(revoke.statement, "pk-credAAAA") });
    expect(server.pending!.body!.statement).toEqual(kept(revoke.statement, "pk-credAAAA"));
  });

  it("statements sent in after a restore are served each without its signature, the kept body untouched", async () => {
    await turnedOn("credAAAA");
    const one = { payload: '{"seq":2}', credential: '{"id":"x"}', passkey_id: "pk-credAAAA" };
    server.pending = { id: "hold-u", change: "upload", takes_effect_at: server.now + 180, apply: () => {},
      body: { statements: [one, { ...one, payload: '{"seq":3}' }] } };
    expect(server.view().pending.body).toEqual({ statements: [{ payload: '{"seq":2}', passkey_id: "pk-credAAAA" },
      { payload: '{"seq":3}', passkey_id: "pk-credAAAA" }] });
    expect(server.pending!.body).toEqual({ statements: [one, { ...one, payload: '{"seq":3}' }] });
  });
});

/** A point of low order: its shared secret with every key is zero, so nothing can be sealed to it. */
const LOW_ORDER = C.b64u(new Uint8Array(32));
/** An envelope of `inside` bytes inside (its plaintext's). */
const sealedOf = (inside: number) => C.PREFIX + C.b64u(new Uint8Array([1, ...C.random(C.OVERHEAD - 1 + inside)]));

/** Turning encryption on with credAAAA's passkey, its body changed by `change` on the way to the server. */
async function turningOnSent(change: (b: Body) => void) {
  passkey("credAAAA");
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    if (!url.endsWith("/api/ade/e2e/turn-on")) return server.fetch(url, init);
    const b = JSON.parse(String(init.body)) as Body;
    change(b);
    return server.fetch(url, { ...init, body: JSON.stringify(b) });
  });
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  t.codeText();
  await t.prepare(await view());
  auth.choose = () => "credAAAA";
  await t.firstTouch();
  await t.secondTouch();
  return t.send(true);
}

describe("what the server takes in, as it takes it", () => {
  it("spends a passkey's confirmation on a change of the purpose it was made for alone: one made for another is refused and left unused", async () => {
    await turnedOn("credAAAA");
    const a = "pk-credAAAA";
    passkey("credCCCC");                                                 // the account's, to be added to encryption
    const made = { account: await confirmation("credAAAA", "confirm", "passkey-add"), enrol: await confirmation("credAAAA", "e2e-enrol-touch"),
      anchor: await anchorTouch("credAAAA"), cancel: await confirmation("credAAAA", "e2e-cancel") };
    const enrol = async (word: string) => post("/api/ade/e2e/credentials", { ...(await enrolment("credCCCC", { passkey: "credAAAA" })), confirmation: word });
    const renew = async (word: string) => post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: a }], { passkey: "credAAAA" })), confirmation: word });
    const cancel = (word: string) => post(`/api/ade/holds/${server.pending!.id}/cancel`, { confirmation: word });
    for (const word of [made.account, made.anchor, made.cancel]) expect(await enrol(word)).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    for (const word of [made.account, made.enrol, made.cancel]) expect(await renew(word)).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect(server.pending).toBeNull();
    // each one refused is unused: a change of its own purpose takes it
    expect((await renew(made.anchor)).status).toBe(200);
    for (const word of [made.account, made.enrol]) expect(await cancel(word)).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect((await cancel(made.cancel)).status).toBe(200);
    // the passkey being added touches for its own purpose too (e2e-enrol-own), never the confirmer's
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own: await confirmation("credCCCC", "e2e-enrol-touch") })))
      .toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect((await enrol(made.enrol)).status).toBe(200);
    // and an account change takes an account change's: a touch is refused there, and left unused
    expect(await post("/api/account/passkeys/options", { confirmation: await anchorTouch("credAAAA") })).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect((await post("/api/account/passkeys/options", { confirmation: made.account })).status).toBe(200);
  });

  it("turns high security off with an account change's confirmation alone: a hardware key's touch for encryption is refused and left unused", async () => {
    await turnedOn("credHHHH", "hardware");
    const h = "pk-credHHHH";
    server.high = true;
    const touch = await anchorTouch("credHHHH");
    const turnOff = async (confirmation: string) => {
      const r = await server.fetch("/api/account/high-security", { method: "PUT", body: JSON.stringify({ on: false, confirmation }) });
      return { status: r.status, detail: ((await r.json()) as Body).detail };
    };
    expect(await turnOff(touch)).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect(server.high).toBe(true);
    expect((await post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: h }], { passkey: "credHHHH" })), confirmation: touch })).status).toBe(200);
    expect((await turnOff(await confirmation("credHHHH", "confirm", "high-security-off"))).status).toBe(200);
    expect(server.high).toBe(false);
  });

  it("while high security is on, refuses a phone's confirmation as a phone's first, whatever it was made for (_use_confirmation's order)", async () => {
    await turnedOn("credHHHH", "hardware");
    passkey("credAAAA");
    const early = await anchorTouch("credAAAA");                         // a phone's, made while high security was off
    server.high = true;
    expect(await post("/api/account/passkeys/options", { confirmation: early })).toMatchObject({ status: 403, detail: WORDS.needsHardware });
  });

  it("takes a passkey's credential, its grant and the roster in the server's forms alone (_credential, _sealed, _wrap)", async () => {
    await turnedOn("credAAAA");
    passkey("credCCCC");
    const send = async (change: (b: Body) => void) => {
      const b: Body = await enrolment("credCCCC", { passkey: "credAAAA" });
      change(b);
      return post("/api/ade/e2e/credentials", b);
    };
    for (const change of [
      (b: Body) => { b.credential.salt = bytes(31); },
      (b: Body) => { b.credential.salt = bytes(33); },
      (b: Body) => { b.credential.salt = `${bytes(32)}=`; },             // padded
      (b: Body) => { b.credential.public_key = LOW_ORDER; },
      (b: Body) => { b.credential.public_key = bytes(31); },
      (b: Body) => { b.credential.wrapped = bytes(27); },
      (b: Body) => { b.credential.wrapped = bytes(1025); },
    ]) expect(await send(change)).toMatchObject({ status: 422, detail: "The credential is wrong." });
    for (const change of [
      (b: Body) => { b.grant = bytes(109); },                            // not e2e1.
      (b: Body) => { b.grant = sealedOf(513); },
      (b: Body) => { b.roster = bytes(27); },
      (b: Body) => { b.roster = sized(131073); },                        // e2e_keys.ROSTER_MAX, every account's
      (b: Body) => { b.roster = "not base64url!"; },
    ]) expect(await send(change)).toMatchObject({ status: 422, detail: "The grant or the roster is wrong." });
    expect(server.pending).toBeNull();
    // at the edges, taken
    expect((await send((b) => { b.credential.wrapped = bytes(28); b.grant = sealedOf(512); b.roster = sized(131072); })).status).toBe(200);
    server.pending = null;
    expect((await send((b) => { b.credential.wrapped = bytes(1024); b.roster = bytes(28); })).status).toBe(200);
  });

  it("takes a new master key's grants, chain, roster, content keys and recovery key in the server's forms alone (_revocation_body)", async () => {
    await turnedOn("credAAAA");
    const send = async (change: (b: Body) => void) => {
      const b: Body = await revocation("credAAAA");
      change(b);
      return post("/api/ade/e2e/revoke", b);
    };
    expect(await send((b) => { b.grants[0].sealed = sealedOf(513); }))
      .toMatchObject({ status: 422, detail: "The grant is sealed as an envelope is (e2e1. and base64url), at most 512 bytes inside." });
    expect(await send((b) => { b.chain.wrapped = bytes(27); }))
      .toMatchObject({ status: 422, detail: "The chain's wrap is 28 to 1024 bytes, in base64url without padding." });
    expect(await send((b) => { b.roster = sized(131073); }))
      .toMatchObject({ status: 422, detail: "The roster is 28 to 131072 bytes, in base64url without padding." });
    expect(await send((b) => { b.content_keys[0].wrapped = bytes(1025); }))
      .toMatchObject({ status: 422, detail: "The content key's wrap is 28 to 1024 bytes, in base64url without padding." });
    expect(await send((b) => { b.content_key.wrapped = bytes(27); }))
      .toMatchObject({ status: 422, detail: "The content key is the one its statement states (key and key_id)." });
    for (const recovery of [{ public_key: LOW_ORDER, anchor: bytes(60) }, { public_key: bytes(32), anchor: bytes(27) }]) {
      expect(await send((b) => { b.recovery = recovery; })).toMatchObject({ status: 422 });
    }
    expect(server.pending).toBeNull();
    expect((await send((b) => { b.recovery = { public_key: bytes(32), anchor: bytes(60) }; })).status).toBe(200);
  });

  it("takes a renewed anchor of a salt of 32 bytes, and the recovery code's of a key something can be sealed to (_bytes, _key)", async () => {
    const code = await turnedOn("credAAAA");
    for (const salt of [bytes(31), bytes(33)]) {
      expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: "pk-credAAAA", salt }], { passkey: "credAAAA" }))).toMatchObject({ status: 422,
        detail: "The renewed anchor's salt is 32 bytes, in base64url without padding." });
    }
    const renew = async (public_key: string) => post("/api/ade/e2e/anchors", { ...(await renewal([], { code })), recovery_anchor: { public_key, anchor: bytes(60) } });
    expect(await renew(LOW_ORDER)).toMatchObject({ status: 422, detail: "The recovery key is not an X25519 key anything can be sealed to." });
    expect((await renew(server.recovery!.public_key)).status).toBe(200);
  });

  it("reads renewed anchors in e2e_keys.anchors' order and words: the list, the master key's id, each entry in turn, the code's, and only then encryption on", async () => {
    await turnedOn("credAAAA");
    const mk = server.roster!.mk_id;
    const entry = (o: Body = {}) => ({ passkey_id: "pk-credAAAA", salt: bytes(32), wrapped: bytes(60), ...o });
    const send = (body: Body) => post("/api/ade/e2e/anchors", { mk_id: mk, ...body });
    const SHAPE = "Renewed anchors are a list of {passkey_id, salt, wrapped, anchor?, confirmation?}, and the recovery code's {public_key, anchor}: one of them at least.";
    const SALT = "The renewed anchor's salt is 32 bytes, in base64url without padding.";
    for (const anchors of [undefined, "x", [], Array.from({ length: 21 }, (_, i) => entry({ passkey_id: `p${i}` }))]) {
      expect(await send({ anchors })).toEqual({ status: 422, detail: SHAPE });
    }
    // twenty pass the list's shape, and are read on (none of these is enrolled)
    expect(await send({ anchors: Array.from({ length: 20 }, (_, i) => entry({ passkey_id: `p${i}` })) }))
      .toEqual({ status: 422, detail: "A renewed anchor is of a passkey that unlocks end-to-end encryption." });
    // the master key's id before any entry
    expect(await send({ anchors: [7], mk_id: "AB" })).toEqual({ status: 422, detail: "The master key's id is 32 lowercase hex characters." });
    expect(await send({ anchors: [7] })).toEqual({ status: 422, detail: "The anchor is a JSON object." });
    for (const anchors of [[entry(), entry()], [entry({ passkey_id: 5 })]]) {
      expect(await send({ anchors })).toEqual({ status: 422, detail: "Each renewed anchor names one enrolled passkey, once." });
    }
    // each entry's salt before its wrap, and its wrap before its anchor
    expect(await send({ anchors: [entry({ salt: bytes(31), wrapped: bytes(27) })] })).toEqual({ status: 422, detail: SALT });
    expect(await send({ anchors: [entry({ wrapped: bytes(27), anchor: mk })] })).toEqual({ status: 422, detail: "The credential's wrap is 28 to 1024 bytes, in base64url without padding." });
    expect(await send({ anchors: [entry({ anchor: mk })] })).toEqual({ status: 422, detail: CLASSICAL_FORMS });
    // and every entry before the code's
    expect(await send({ anchors: [entry({ salt: bytes(31) })], recovery_anchor: 7 })).toEqual({ status: 422, detail: SALT });
    for (const [recovery_anchor, detail] of [
      [7, "The recovery code's renewed anchor is a JSON object."],
      [{ public_key: bytes(31), anchor: bytes(60) }, "The recovery key is 32 bytes, in base64url without padding."],
      [{ public_key: LOW_ORDER, anchor: bytes(60) }, "The recovery key is not an X25519 key anything can be sealed to."],
      [{ public_key: server.recovery!.public_key, anchor: bytes(27) }, "The recovery anchor is 28 to 1024 bytes, in base64url without padding."],
    ] as const) {
      expect(await send({ anchors: [], recovery_anchor })).toEqual({ status: 422, detail });
    }
    // the master key current and each passkey enrolled come after encryption on; a passkey not enrolled after the key
    expect(await send({ anchors: [entry({ passkey_id: "pk-nope" })], mk_id: "ab".repeat(16) })).toEqual({ status: 409, detail: WORDS.stale });
    expect(await send({ anchors: [entry({ passkey_id: "pk-nope" })] })).toEqual({ status: 422, detail: "A renewed anchor is of a passkey that unlocks end-to-end encryption." });
    // all of it is read before encryption on: turned off, a body out of its forms is still told so, one in them 409
    expect((await post("/api/ade/e2e/off", await turningOff("credAAAA"))).status).toBe(200);
    server.settle();
    expect(await send({ anchors: [entry({ salt: bytes(31) })] })).toEqual({ status: 422, detail: SALT });
    expect(await send({ anchors: [entry()] })).toEqual({ status: 409, detail: NOT_ON });
    expect(await send({ anchors: [entry()], mk_id: "ab".repeat(16) })).toEqual({ status: 409, detail: NOT_ON });  // before the key
  });

  it("reads a post-quantum account's renewed anchors in its forms: a 60-byte wrap, its anchor the master key named, a recovery key of 1,216 bytes", async () => {
    await turnedOnPq();
    const mk = server.roster!.mk_id;
    const entry = (o: Body = {}) => ({ passkey_id: "pk-credAAAA", salt: bytes(32), wrapped: bytes(60), anchor: mk, ...o });
    const send = (body: Body) => post("/api/ade/e2e/anchors", { mk_id: mk, kem: "x-wing", ...body });
    expect(await send({ anchors: [entry({ wrapped: bytes(61) })] })).toEqual({ status: 422, detail: "The credential's wrap is 60 bytes, in base64url without padding." });
    expect(await send({ anchors: [entry({ anchor: "AB" })] })).toEqual({ status: 422, detail: "The renewed anchor's anchor is 32 lowercase hex characters." });
    expect(await send({ anchors: [entry({ anchor: undefined })] })).toEqual({ status: 422, detail: "The renewed anchor's anchor is 32 lowercase hex characters." });
    expect(await send({ anchors: [entry({ anchor: "ab".repeat(16) })] })).toEqual({ status: 422, detail: "A post-quantum credential's anchor is the master key the change names." });
    expect(await send({ anchors: [], recovery_anchor: { public_key: bytes(32), anchor: bytes(60) } })).toEqual({ status: 422, detail: "The recovery key is 1216 bytes, in base64url without padding." });
    // 1,216 bytes of 0xff: every ML-KEM coefficient 4,095, past 3,329
    expect(await send({ anchors: [], recovery_anchor: { public_key: C.b64u(new Uint8Array(1216).fill(0xff)), anchor: bytes(60) } }))
      .toMatchObject({ status: 422, detail: expect.stringMatching(/^The recovery key is not a post-quantum key anything can be sealed to: /) });
    // the list's shape before the era, the era before the entries (without kem, the older page's 400)
    expect(await post("/api/ade/e2e/anchors", { mk_id: mk, anchors: [] })).toMatchObject({ status: 422, detail: expect.stringMatching(/^Renewed anchors are a list/) });
    expect(await post("/api/ade/e2e/anchors", { mk_id: mk, anchors: [entry({ wrapped: bytes(61) })] })).toEqual({ status: 400, detail: OLD_PAGE });
  });

  it("turns encryption on with a credential, its grants, a recovery key and a roster in the server's forms alone (_turn_on_plan)", async () => {
    const CREDENTIAL = "The credential is wrong.", RECOVERY = "The recovery key or the roster is wrong.";
    await expect(turningOnSent((b) => { b.credential.public_key = LOW_ORDER; })).rejects.toThrow(CREDENTIAL);
    await expect(turningOnSent((b) => { b.credential.wrapped = bytes(1025); })).rejects.toThrow(CREDENTIAL);
    await expect(turningOnSent((b) => { b.grants[0].sealed = sealedOf(513); }))
      .rejects.toThrow("Turning encryption on grants the master key to the credential in use and to the recovery key.");
    await expect(turningOnSent((b) => { b.recovery.public_key = LOW_ORDER; })).rejects.toThrow(RECOVERY);
    await expect(turningOnSent((b) => { b.recovery.anchor = bytes(27); })).rejects.toThrow(RECOVERY);
    await expect(turningOnSent((b) => { b.roster = sized(131073); })).rejects.toThrow(RECOVERY);
    expect(server.pending).toBeNull();
    await turningOnSent(() => {});                                       // as the page sends it, taken
    expect(server.pending).toMatchObject({ change: "turn-on" });
  });

  it("takes a statement made as its nonce was given, by the server's time then (2 seconds before it to 30 after), of a key that can be sealed to", async () => {
    await turnedOn("credAAAA");
    const off = async (shift: number) => {
      const p = await K.prepareStatement(await view(), "e2e-off");
      p.payload.created_at = Number(p.payload.created_at) + shift;
      p.payload.expires_at = Number(p.payload.expires_at) + shift;
      p.challenge = await stepChallenge(p.payload);
      auth.choose = () => "credAAAA";
      return post("/api/ade/e2e/off", { statement: await K.signStatement(p, ["credAAAA"]) });
    };
    const WINDOW = "The payload's created_at is not the time its nonce was given (within 30 seconds after it).";
    expect(await off(31)).toMatchObject({ status: 422, detail: WINDOW });
    expect(await off(-3)).toMatchObject({ status: 422, detail: WINDOW });
    expect((await off(30)).status).toBe(200);
    server.pending = null;
    expect((await off(-2)).status).toBe(200);
    server.pending = null;
    // an e2e-key statement's key: one nothing can be sealed to is refused
    const EK = new Uint8Array(32);
    const keyId = await C.keyIdOf(EK);
    const p = await K.prepareStatement(await view(), "e2e-key", { EK, keyId, retires: "grace" });
    auth.choose = () => "credAAAA";
    const statement = await K.signStatement(p, ["credAAAA"]);
    expect(await post("/api/ade/e2e/rotate", { statement, content_key: { key_id: keyId, public_key: LOW_ORDER, mk_id: server.roster!.mk_id, wrapped: bytes(60) } }))
      .toMatchObject({ status: 422, detail: "A statement's key is wrong." });
  });
});

describe("each era's forms, as the server reads them (e2e_keys._era, _key, _key_wrap, _check_key_new)", () => {
  const hybridKey = async () => C.hybridPublic(await C.hybridFromSeed(C.random(32)));
  /** A rotation `signer` signs stating `EK`, its content key wrapped in `wrap` bytes under the current master key. */
  async function rotationOf(signer: string, EK: Uint8Array, wrap = 60) {
    const { statement, content } = await signed(signer, "e2e-key", "grace", undefined, EK);
    return { statement, content_key: { ...content(server.roster!.mk_id), wrapped: bytes(wrap) } };
  }

  it("refuses a post-quantum key on a classical account, kem there, and a classical key after a post-quantum one was stated", async () => {
    await turnedOn("credAAAA");
    server.rotateEvery = 0;
    // a version-3 statement on a classical account: its key is not the 32 bytes a classical content key is
    const pq = await rotationOf("credAAAA", await hybridKey());
    expect(await post("/api/ade/e2e/rotate", pq)).toMatchObject({ status: 422, detail: "The content key is 32 bytes, in base64url without padding." });
    expect(await post("/api/ade/e2e/rotate", { ...pq, kem: "x-wing" })).toMatchObject({ status: 422, detail: CLASSICAL_FORMS });
    expect(await post("/api/ade/e2e/rotate", { ...pq, kem: "kyber" })).toMatchObject({ status: 422, detail: "A write's kem is x-wing, or none." });
    // a database put back to before an upgrade keeps the post-quantum statements made since: no classical key after them
    const classical = await rotationOf("credAAAA", C.random(32));
    server.statements[0].public_key = C.b64u(await hybridKey());
    expect(await post("/api/ade/e2e/rotate", classical)).toMatchObject({ status: 409, detail: CLASSICAL_AFTER });
    expect(server.pending).toBeNull();
  });

  it("refuses a classical account's renewed anchor that names an anchor beside it, a post-quantum one's form (CLASSICAL_FORMS)", async () => {
    await turnedOn("credAAAA");
    const named = await renewal([{ passkey_id: "pk-credAAAA", anchor: server.roster!.mk_id }], { passkey: "credAAAA" });
    expect(await post("/api/ade/e2e/anchors", named)).toEqual({ status: 422, detail: CLASSICAL_FORMS });
    expect(await post("/api/ade/e2e/anchors", { ...named, anchors: [{ ...named.anchors[0], anchor: null }] })).toEqual({ status: 200, detail: undefined });
  });

  it("takes a post-quantum account's keys in their forms alone: kem, a version-3 statement, a 60-byte wrap", async () => {
    await turnedOnPq();
    server.rotateEvery = 0;
    const good = await rotationOf("credAAAA", await hybridKey());
    expect(await post("/api/ade/e2e/rotate", good)).toMatchObject({ status: 400, detail: OLD_PAGE });
    expect(await post("/api/ade/e2e/rotate", { ...(await rotationOf("credAAAA", C.random(32))), kem: "x-wing" }))
      .toMatchObject({ status: 422, detail: `The content key is ${C.HYBRID_KEY_LEN} bytes, in base64url without padding.` });
    expect(await post("/api/ade/e2e/rotate", { ...(await rotationOf("credAAAA", await hybridKey(), 76)), kem: "x-wing" }))
      .toMatchObject({ status: 422, detail: `The content key's wrap is ${C.WRAP_LEN} bytes, in base64url without padding.` });
    expect(server.pending).toBeNull();
    expect((await post("/api/ade/e2e/rotate", { ...good, kem: "x-wing" })).status).toBe(200);
  });

  it("takes a post-quantum grant of the master key's 32 bytes alone: form 2, exactly 1,197 bytes (e2e_keys._sealed)", async () => {
    await turnedOnPq();
    const form2Of = (inside: number) => C.PREFIX + C.b64u(new Uint8Array([2, ...C.random(C.OVERHEAD2 - 1 + inside)]));
    const enrolling = async (grant: string) => post("/api/ade/e2e/credentials", {
      credential: { passkey_id: passkey("credCCCC"), salt: bytes(32), public_key: C.b64u(await hybridKey()), wrapped: bytes(60),
        anchor: server.roster!.mk_id, confirmation: await ownTouch("credCCCC") },
      mk_id: server.roster!.mk_id, grant, roster: bytes(60), confirmation: await confirmation("credAAAA", "e2e-enrol-touch"), kem: "x-wing" });
    for (const grant of [form2Of(33), form2Of(31), sealedOf(32)]) {
      expect(await enrolling(grant)).toMatchObject({ status: 422, detail: "The grant or the roster is wrong." });
    }
    expect(server.pending).toBeNull();
    expect((await enrolling(form2Of(32))).status).toBe(200);
  });

  it("asks a recovery proof as the server takes the ask: a digest for re-sealing alone, a public key for the upgrade alone, kem on a post-quantum answer", async () => {
    await turnedOn("credAAAA");
    const ask = (b: Body) => post("/api/ade/e2e/recovery-proof", b);
    const digest = "ab".repeat(32);
    const DIGEST = "A recovery proof for re-sealing names its batch's digest (64 lowercase hex), and no other proof names one.";
    expect(await ask({ purpose: "reseal" })).toMatchObject({ status: 422, detail: DIGEST });
    expect(await ask({ purpose: "revoke", digest })).toMatchObject({ status: 422, detail: DIGEST });
    expect(await ask({ purpose: "reseal", digest: "AB".repeat(32) })).toMatchObject({ status: 422, detail: DIGEST });
    expect(await ask({ purpose: "steal" })).toMatchObject({ status: 422 });
    const newKey = await hybridKey();
    const FOR_KEY = "A recovery proof for a new recovery key is the upgrade's, purpose revoke (or turn-on, as encryption is turned on again), on a classical account.";
    expect(await ask({ purpose: "rotate", public_key: C.b64u(newKey) })).toMatchObject({ status: 422, detail: FOR_KEY });
    const classical = await K.E2EApi.recoveryChallenge("off") as Body;
    expect([typeof classical.server_key, classical.server_ct, classical.kem]).toEqual(["string", undefined, undefined]);
    const upgrade = await K.E2EApi.recoveryChallenge("revoke", newKey) as Body;
    expect([typeof upgrade.server_ct, upgrade.server_key, upgrade.kem]).toEqual(["string", undefined, "x-wing"]);
    // the fold that keeps the code proves its new key for turning on (e2e_keys.proof_challenge)
    const fold = await K.E2EApi.recoveryChallenge("turn-on", newKey) as Body;
    expect([typeof fold.server_ct, fold.server_key, fold.kem, fold.purpose]).toEqual(["string", undefined, "x-wing", "turn-on"]);
    const code = await K.E2EApi.recoveryChallenge("turn-on") as Body;
    expect([typeof code.server_key, code.server_ct, code.kem]).toEqual(["string", undefined, undefined]);
  });

  it("asks a post-quantum account's recovery proof for its own key alone, with kem", async () => {
    await turnedOnPq();
    const FOR_KEY = "A recovery proof for a new recovery key is the upgrade's, purpose revoke (or turn-on, as encryption is turned on again), on a classical account.";
    expect(await post("/api/ade/e2e/recovery-proof", { purpose: "revoke", public_key: C.b64u(await hybridKey()) }))
      .toMatchObject({ status: 422, detail: FOR_KEY });
    expect(await post("/api/ade/e2e/recovery-proof", { purpose: "turn-on", public_key: C.b64u(await hybridKey()) }))
      .toMatchObject({ status: 422, detail: FOR_KEY });
    const own = await K.E2EApi.recoveryChallenge("off") as Body;
    expect([typeof own.server_ct, own.server_key, own.kem]).toEqual(["string", undefined, "x-wing"]);
  });

  it("serves the count of texts still stored classical for a post-quantum account alone (e2e_keys.account_view)", async () => {
    await turnedOn("credAAAA");
    server.classicalTexts = { events: 1, rows: 0, talks: 0, total: 1 };
    expect((await view()).classical_texts).toBeNull();
    server.pq = true;
    expect((await view()).classical_texts).toEqual({ events: 1, rows: 0, talks: 0, total: 1 });
  });
});

describe("a turn-on that makes the account's keys anew, where the server takes none without kem (classicalWholeTurnOn false, e2e_keys._whole_era)", () => {
  /** TurnOn's turn-on made and sent with credAAAA's passkey: CLASSICAL's (`classical`), or the page's own. */
  async function turningOn(replace: boolean, classical: boolean) {
    passkey("credAAAA");
    const t = new K.TurnOn(ACCOUNT, replace, classical ? CLASSICAL : {});
    t.codeText();
    await t.prepare(await view());
    auth.choose = () => "credAAAA";
    await t.firstTouch();
    await t.secondTouch();
    return t.send(true);
  }
  const sent = () => server.calls.filter((c) => c.path.endsWith("/api/ade/e2e/turn-on")).at(-1)!.body as Body;

  it("takes the page's own turn-on, which says kem: a first one and a start again, each post-quantum", async () => {
    server.classicalWholeTurnOn = false;
    await turningOn(false, false);
    expect(sent().kem).toBe("x-wing");
    server.settle();
    expect(server.pq).toBe(true);
    expect((await post("/api/ade/e2e/off", { ...(await turningOff("credAAAA")), kem: "x-wing" })).status).toBe(200);
    server.settle();
    await turningOn(true, false);
    expect([sent().replace, sent().kem]).toEqual([true, "x-wing"]);
    server.settle();
    expect(server.refused).toEqual([]);
    expect([(await view()).on, server.pq]).toEqual([true, true]);
  });

  it("refuses CLASSICAL's in the server's words as it is asked, and one placed while it was taken as its hold ends; turning on again keeps its era", async () => {
    server.classicalWholeTurnOn = false;
    await expect(turningOn(false, true)).rejects.toThrow(OLD_PAGE);
    expect(sent().kem).toBeUndefined();
    expect([server.pending, server.statements]).toEqual([null, []]);
    // placed by a server that took it, its hold ending once the server refuses it: nothing of it kept
    server.classicalWholeTurnOn = true;
    await turningOn(false, true);
    expect(server.pending).toMatchObject({ change: "turn-on" });
    server.classicalWholeTurnOn = false;
    server.settle();
    expect(server.refused).toEqual([OLD_PAGE]);
    expect([server.statements, server.credentials, server.recovery, server.pq]).toEqual([[], [], null, false]);
    expect((await view()).on).toBe(false);
    // a classical account made before (the test's stand-in) starts again only in post-quantum keys
    server.classicalWholeTurnOn = true;
    await turnedOn("credAAAA");
    expect((await post("/api/ade/e2e/off", await turningOff("credAAAA"))).status).toBe(200);
    server.settle();
    server.classicalWholeTurnOn = false;
    await expect(turningOn(true, true)).rejects.toThrow(OLD_PAGE);
    expect(server.pending).toBeNull();
    // turning it on again under its master key takes no kem, and keeps it classical
    const { statement, content } = await signed("credAAAA", "e2e-key", "all");
    expect((await post("/api/ade/e2e/turn-on", { statement, content_key: content(server.roster!.mk_id) })).status).toBe(200);
    server.settle();
    expect(server.refused).toEqual([OLD_PAGE]);
    expect([(await view()).on, server.pq]).toEqual([true, false]);
  });
});

describe("what a change's request is read for before whether encryption is on and against the account's keys, in the server's order", () => {
  /** Encryption turned off on a classical account (`pq`: a post-quantum one), its hold ended. */
  async function turnedOff(pq = false) {
    if (pq) await turnedOnPq();
    else await turnedOn("credAAAA");
    expect((await post("/api/ade/e2e/off", { ...(await turningOff("credAAAA")), ...(pq ? { kem: "x-wing" } : {}) })).status).toBe(200);
    server.settle();
    expect((await view()).on).toBe(false);
  }

  it("reads a rotation's era and its content key's forms before whether encryption is on (e2e_keys.rotate: _era, _content_key, _ready)", async () => {
    await turnedOff();
    expect(await post("/api/ade/e2e/rotate", { ...(await rotation("credAAAA")), kem: "x-wing" })).toEqual({ status: 422, detail: CLASSICAL_FORMS });
    const r: Body = await rotation("credAAAA");
    expect(await post("/api/ade/e2e/rotate", { ...r, content_key: { ...r.content_key, mk_id: "not hex" } }))
      .toEqual({ status: 422, detail: "The content key is the one its statement states (key and key_id)." });
    expect(await post("/api/ade/e2e/rotate", r)).toEqual({ status: 409, detail: NOT_ON });
    expect(server.pending).toBeNull();
  });

  it("reads a post-quantum account's era before whether encryption is on: a rotation without kem is from a page older than the server", async () => {
    await turnedOff(true);
    expect(await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).toEqual({ status: 400, detail: OLD_PAGE });
  });

  it("reads a revocation's body for its forms, then the hardware key's signature, then whether encryption is on (e2e_keys.revoke)", async () => {
    await turnedOff();
    const send = async (change: (b: Body) => void) => {
      const b: Body = await revocation("credAAAA");
      change(b);
      return post("/api/ade/e2e/revoke", b);
    };
    // in the order _revocation_body reads them: each refused before any after it, and before the hardware key's word
    const faults: [(b: Body) => void, string][] = [
      [(b) => { b.content_key.wrapped = bytes(27); }, "The content key is the one its statement states (key and key_id)."],
      [(b) => { b.mk_id = "not hex"; }, "The new content key is wrapped under the new master key."],
      [(b) => { b.content_keys[0].wrapped = bytes(1025); }, "The content key's wrap is 28 to 1024 bytes, in base64url without padding."],
      [(b) => { b.chain.wrapped = bytes(27); }, "The chain's wrap is 28 to 1024 bytes, in base64url without padding."],
      [(b) => { b.grants[0].sealed = sealedOf(513); }, "The grant is sealed as an envelope is (e2e1. and base64url), at most 512 bytes inside."],
      [(b) => { b.roster = sized(131073); }, "The roster is 28 to 131072 bytes, in base64url without padding."],
      [(b) => { b.recovery = { public_key: bytes(32), anchor: bytes(27) }; }, "The new recovery key is {public_key, anchor}: a key that can be sealed to, and its anchor."],
    ];
    for (let i = 0; i < faults.length; i++) {
      const these = faults.slice(i).map(([f]) => f);
      expect(await send((b) => { for (const f of these) f(b); })).toEqual({ status: 422, detail: faults[i][1] });
      // turning high security on, signed by a passkey that is not a hardware key: its forms are read first all the same
      expect(await send((b) => { for (const f of these) f(b); b.high_security = true; })).toEqual({ status: 422, detail: faults[i][1] });
    }
    expect(await send((b) => { b.high_security = true; })).toEqual({ status: 403, detail: WORDS.needsHardwareToTurn });
    expect(await send(() => {})).toEqual({ status: 409, detail: NOT_ON });
    expect(server.pending).toBeNull();
  });

  it("reads an enrolment's era, master key id and credential before whether its passkey is enrolled or the master key current (e2e_keys.enrol)", async () => {
    await turnedOn("credAAAA");
    const made: Body = await enrolment("credCCCC", { passkey: "credAAAA" });
    const send = (change: (b: Body) => void) => {
      const b: Body = JSON.parse(JSON.stringify(made));
      change(b);
      return post("/api/ade/e2e/credentials", b);
    };
    const stale = "ab".repeat(16);
    expect(await send((b) => { b.kem = "x-wing"; b.mk_id = "not hex"; })).toEqual({ status: 422, detail: CLASSICAL_FORMS });
    expect(await send((b) => { b.mk_id = "not hex"; b.credential.passkey_id = "pk-nobody"; })).toEqual({ status: 422, detail: "The master key's id is 32 lowercase hex characters." });
    expect(await send((b) => { b.mk_id = stale; b.credential.passkey_id = "pk-credAAAA"; b.credential.salt = bytes(31); }))
      .toEqual({ status: 422, detail: "The credential is wrong." });
    expect(await send((b) => { b.mk_id = stale; b.credential.passkey_id = "pk-credAAAA"; })).toEqual({ status: 409, detail: "That passkey unlocks end-to-end encryption already." });
    expect(await send((b) => { b.mk_id = stale; b.grant = "not an envelope"; })).toEqual({ status: 409, detail: WORDS.stale });
    expect(server.pending).toBeNull();
  });

  it("reads a post-quantum account's era before the master key: an enrolment without kem is from a page older than the server", async () => {
    await turnedOnPq();
    const made: Body = await enrolment("credCCCC", { passkey: "credAAAA" });
    expect(await post("/api/ade/e2e/credentials", { ...made, mk_id: "ab".repeat(16) })).toEqual({ status: 400, detail: OLD_PAGE });
  });
});

describe("what is stale, of another kind, or for another change, in the server's words", () => {
  const hybridKey = async () => C.hybridPublic(await C.hybridFromSeed(C.random(32)));
  const options = (b: Body) => post("/api/account/confirm/options", b);

  it("on a classical account, a hybrid challenge given as the code's proof is stale, not wrong: the server cannot open its id", async () => {
    const code = await turnedOn("credAAAA");
    // the upgrade's hybrid challenge (purpose revoke, made to a new key), given with a revocation
    const hybrid = await K.E2EApi.recoveryChallenge("revoke", await hybridKey()) as Body;
    const body = await revocation("credAAAA", { code });
    expect(await post("/api/ade/e2e/revoke", { ...body, recovery_proof: { challenge_id: hybrid.challenge_id, proof: bytes(32) } }))
      .toMatchObject({ status: 403, detail: PROOF_STALE });
    expect(server.pending).toBeNull();
    // and with its own proof, of the classical kind, the same revocation is placed
    expect((await post("/api/ade/e2e/revoke", body)).status).toBe(200);
  });

  it("on a post-quantum account, a classical challenge given as the code's proof is stale, not wrong", async () => {
    const code = await turnedOnPq();
    // a challenge of the classical kind, asked while the server served the account as classical
    server.pq = false;
    const classical = await K.E2EApi.recoveryChallenge("passkey") as Body;
    server.pq = true;
    expect([typeof classical.server_key, classical.server_ct]).toEqual(["string", undefined]);
    expect(await post("/api/account/passkeys/options", { recovery_proof: { challenge_id: classical.challenge_id, proof: bytes(32) } }))
      .toMatchObject({ status: 403, detail: PROOF_STALE });
    // and the right kind passes
    expect((await post("/api/account/passkeys/options", { recovery_proof: await K.recoveryProof(ACCOUNT, "passkey", { code, pq: true }) })).status).toBe(200);
  });

  it("a recovery proof whose 300 seconds are up is stale; a post-quantum account's needs this server's ML-KEM", async () => {
    const code = await turnedOnPq();
    const made = await K.recoveryProof(ACCOUNT, "passkey", { code, pq: true });
    server.now += CHALLENGE_TTL;
    expect(await post("/api/account/passkeys/options", { recovery_proof: made })).toMatchObject({ status: 403, detail: PROOF_STALE });
    server.now -= 1;                                                     // the last second it lasts
    expect((await post("/api/account/passkeys/options", { recovery_proof: made })).status).toBe(200);
    const again = await K.recoveryProof(ACCOUNT, "passkey", { code, pq: true });
    server.mlkem = false;
    expect(await post("/api/account/passkeys/options", { recovery_proof: again })).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    server.mlkem = true;
    expect((await post("/api/account/passkeys/options", { recovery_proof: again })).status).toBe(200);
  });

  it("a confirmation this server did not give, used, or whose 300 seconds are up is stale; one asked with no confirmation is the caller's own refusal", async () => {
    await turnedOn("credAAAA");
    const renew = async (word?: string) => post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: "pk-credAAAA" }], { passkey: "credAAAA" })),
      ...(word === undefined ? { confirmation: undefined } : { confirmation: word }) });
    expect(await renew("confirmation-nope")).toMatchObject({ status: 403, detail: WORDS.confirmStale });
    // none at all: a passkey's is asked for (e2e_keys._confirm, passkeys.NEEDS_PASSKEY), never the password's
    expect(await renew()).toMatchObject({ status: 403, detail: WORDS.needsPasskey });
    const word = await anchorTouch("credAAAA");
    server.now += CHALLENGE_TTL;
    expect(await renew(word)).toMatchObject({ status: 403, detail: WORDS.confirmStale });
    server.now -= 1;
    expect((await renew(word)).status).toBe(200);
  });

  it("a confirmation's challenge this server did not give, or whose 300 seconds are up, is stale (400); one used already confirms nothing (403)", async () => {
    await turnedOn("credAAAA");
    auth.choose = () => "credAAAA";
    const confirm = async (challengeId: string, credential: unknown) => {
      const r = await server.fetch("/api/account/confirm", { method: "POST", body: JSON.stringify({ challenge_id: challengeId, credential }) });
      return { status: r.status, detail: ((await r.json()) as Body).detail as string };
    };
    const late = await confirmCeremony("e2e-anchor-touch");
    const lateAnswer = (await assertionWith(late.ask)).credential;
    expect(await confirm("confirm-nope", lateAnswer)).toMatchObject({ status: 400, detail: WORDS.passkeyRequestStale });
    server.now += CHALLENGE_TTL;
    expect(await confirm(late.challenge_id, lateAnswer)).toMatchObject({ status: 400, detail: WORDS.passkeyRequestStale });
    server.now -= CHALLENGE_TTL;
    // answered in time it confirms once; the same answer again confirms nothing
    expect((await confirm(late.challenge_id, lateAnswer)).status).toBe(200);
    expect(await confirm(late.challenge_id, lateAnswer)).toMatchObject({ status: 403, detail: "That did not confirm it: use one of this account's own passkeys, or its password." });
    // a phone's answer while high security is on uses its challenge before it is refused (passkeys.confirm records the
    // challenge with the answer): a hardware key's answer to the same challenge then confirms nothing
    passkey("credHHHH", "hardware");
    server.high = true;
    const asked = await confirmCeremony("e2e-anchor-touch");
    const phone = (await assertionWith(asked.ask)).credential;
    auth.choose = () => "credHHHH";
    const hardware = (await assertionWith(asked.ask)).credential;
    expect(await confirm(asked.challenge_id, phone)).toMatchObject({ status: 403, detail: WORDS.needsHardware });
    expect(await confirm(asked.challenge_id, hardware)).toMatchObject({ status: 403, detail: "That did not confirm it: use one of this account's own passkeys, or its password." });
  });

  it("while high security is on, a change asked with no confirmation is told a hardware key's is needed, as any refusal of a phone's", async () => {
    await turnedOn("credHHHH", "hardware");
    server.high = true;
    const renewal0 = { ...(await renewal([{ passkey_id: "pk-credHHHH" }], { passkey: "credHHHH" })) } as Body;
    delete renewal0.confirmation;
    expect(await post("/api/ade/e2e/anchors", renewal0)).toMatchObject({ status: 403, detail: WORDS.needsHardware });
    server.high = false;
    expect(await post("/api/ade/e2e/anchors", renewal0)).toMatchObject({ status: 403, detail: WORDS.needsPasskey });
  });

  it("encryption's own touches (a passkey added, a renewed anchor, a cancel) are made for no change: one made for a change is for something else", async () => {
    await turnedOn("credAAAA");
    passkey("credCCCC");
    const b = enrolled("credBBBB");
    const touchFor = async (id: string, purpose: ConfirmPurpose) => {
      auth.choose = () => id;
      const c = await confirmCeremony(purpose, false, { change: "passkey-add" });
      return (await K.E2EApi.confirm(c.challenge_id, (await assertionWith(c.ask)).credential)).confirmation;
    };
    // the passkey being added touching for its own purpose, but for a change
    expect(await post("/api/ade/e2e/credentials", await enrolment("credCCCC", { passkey: "credAAAA", own: await touchFor("credCCCC", "e2e-enrol-own") })))
      .toMatchObject({ status: 403, detail: WORDS.confirmOther });
    // a renewed anchor's own touch, for a change
    expect(await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: "pk-credAAAA" }, { passkey_id: b, confirmation: await touchFor("credBBBB", "e2e-anchor-touch") }],
      { passkey: "credAAAA" }))).toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect(server.pending).toBeNull();
    // a cancel's touch, for a change
    expect((await post("/api/ade/e2e/anchors", await renewal([{ passkey_id: "pk-credAAAA" }], { passkey: "credAAAA" }))).status).toBe(200);
    expect(await post(`/api/ade/holds/${server.pending!.id}/cancel`, { confirmation: await touchFor("credAAAA", "e2e-cancel") }))
      .toMatchObject({ status: 403, detail: WORDS.confirmOther });
    expect((await post(`/api/ade/holds/${server.pending!.id}/cancel`, { confirmation: await confirmation("credAAAA", "e2e-cancel") })).status).toBe(200);
  });

  it("an encryption touch made for a change of its own is for something else: only the upgrade's touch names one", async () => {
    await turnedOn("credAAAA");
    auth.choose = () => "credAAAA";
    const c = await confirmCeremony("e2e-anchor-touch", false, { change: "passkey-add" });
    const named = (await K.E2EApi.confirm(c.challenge_id, (await assertionWith(c.ask)).credential)).confirmation;
    expect(await post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: "pk-credAAAA" }], { passkey: "credAAAA" })), confirmation: named }))
      .toMatchObject({ status: 403, detail: WORDS.confirmOther });
  });

  it("asks a confirmation's options for a change of passkeys.CHANGE's forms alone (BAD_CHANGE)", async () => {
    await turnedOn("credAAAA");
    for (const change of ["steal", "e2e-upgrade:pk-credAAAA", `e2e-upgrade:${"A".repeat(32)}`, "passkey-add "]) {
      expect(await options({ purpose: "confirm", change })).toMatchObject({ status: 422, detail: WORDS.badChange });
    }
    for (const change of ["passkey-add", `e2e-upgrade:${"ab".repeat(16)}`, "passkey-remove:pk-credAAAA", "sign-out-everywhere"]) {
      expect((await options({ purpose: "confirm", change })).status).toBe(200);
    }
    expect((await options({ purpose: "e2e-upgrade-touch" })).status).toBe(200);
  });

  it("reads a confirmation's options in the server's order: what is not text (FastAPI's list), one change, its form, then the purpose", async () => {
    await turnedOn("credAAAA");
    const notText = (f: string, input: unknown) => ({ type: "string_type", loc: ["body", f], msg: "Input should be a valid string", input });
    expect(await options({ purpose: "confirm", change: 7 })).toEqual({ status: 422, detail: [notText("change", 7)] });
    // each field that is not text, in the model's order, before anything else is read
    expect(await options({ purpose: 1, device: true, change: "steal" })).toEqual({ status: 422, detail: [notText("purpose", 1), notText("device", true)] });
    expect(await options({ purpose: "confirm", device: "ABCD-EFGH", change: "passkey-add" })).toEqual({ status: 422, detail: WORDS.oneChange });
    // the change is named (main._subject) before the purpose is read: a bad one is BAD_CHANGE with no purpose, or one
    // it does not know
    expect(await options({ change: "steal" })).toEqual({ status: 422, detail: WORDS.badChange });
    expect(await options({ purpose: "nope", change: "steal" })).toEqual({ status: 422, detail: WORDS.badChange });
    expect(await options({ change: "passkey-add" })).toEqual({ status: 400, detail: OLD_PAGE });
  });

  it("reads hardware first, as pydantic reads a bool, and refuses a device's code with no device waiting (400), then offers the keys it may", async () => {
    await turnedOn("credAAAA");
    passkey("credHHHH", "hardware");
    const bad = (type: string, input: unknown) => ({ type, loc: ["body", "hardware"],
      msg: type === "bool_type" ? "Input should be a valid boolean" : "Input should be a valid boolean, unable to interpret input", input });
    const notText = (f: string, input: unknown) => ({ type: "string_type", loc: ["body", f], msg: "Input should be a valid string", input });
    // hardware is ConfirmOptionsIn's first field: listed before the others, as pydantic 2.13 refuses each kind
    expect(await options({ hardware: "maybe", purpose: 1 })).toEqual({ status: 422, detail: [bad("bool_parsing", "maybe"), notText("purpose", 1)] });
    // an integral number pydantic reads as a 64-bit integer, -2**63 to 2**63 - 1, is one it cannot interpret; past those
    // it is not a boolean at all (pydantic 2.13.5: 10**19 bool_type, 2**63 - 1 bool_parsing)
    for (const [input, type] of [[2, "bool_parsing"], [-1, "bool_parsing"], ["", "bool_parsing"], [" true", "bool_parsing"], [1.5, "bool_type"], [null, "bool_type"], [[], "bool_type"], [{}, "bool_type"],
      [2 ** 62, "bool_parsing"], [-(2 ** 63), "bool_parsing"], [2 ** 63, "bool_type"], [1e19, "bool_type"], [-(2 ** 64), "bool_type"], [1e300, "bool_type"]] as const) {
      expect(await options({ hardware: input, purpose: "confirm" })).toEqual({ status: 422, detail: [bad(type, input)] });
    }
    const offered = async (b: Body) => {
      const r = await server.fetch("/api/account/confirm/options", { method: "POST", body: JSON.stringify({ purpose: "confirm", ...b }) });
      return ((await r.json()) as Body).publicKey.allowCredentials.map((c: Body) => c.id);
    };
    // what it takes as true offers the hardware keys alone; as false, or none given, every passkey
    for (const yes of [true, 1, "1", "TRUE", "yes", "On", "t", "Y"]) expect(await offered({ hardware: yes })).toEqual(["credHHHH"]);
    for (const no of [false, 0, "0", "False", "no", "off", "f", "N"]) expect(await offered({ hardware: no })).toEqual(["credAAAA", "credHHHH"]);
    expect(await offered({})).toEqual(["credAAAA", "credHHHH"]);
    // a device's code alone: no device asks to be signed in here, so none waits with it (auth.approval_subject)
    expect(await options({ purpose: "confirm", device: "ABCD-EFGH" })).toEqual({ status: 400, detail: NO_DEVICE_WAITING });
    expect(await options({ device: "ABCD-EFGH" })).toEqual({ status: 400, detail: NO_DEVICE_WAITING });
    // none to offer: 409, after the purpose is read
    server.passkeys = server.passkeys.filter((p) => p.tier !== "hardware");
    expect(await options({ purpose: "confirm", hardware: true })).toEqual({ status: 409, detail: WORDS.noHardware });
    server.passkeys = [];
    expect(await options({ purpose: "nope" })).toMatchObject({ status: 422 });
    expect(await options({ purpose: "confirm" })).toEqual({ status: 409, detail: NO_PASSKEY });
  });

  it("a statement's nonce whose 300 seconds are up is not one it gave; a version-3 key is checked with ML-KEM, 503 without", async () => {
    await turnedOnPq();
    server.rotateEvery = 0;
    const { statement, content } = await signed("credAAAA", "e2e-key", "grace", undefined, await hybridKey());
    const body = { statement, content_key: content(server.roster!.mk_id), kem: "x-wing" };
    server.now += CHALLENGE_TTL;
    expect(await post("/api/ade/e2e/rotate", body)).toMatchObject({ status: 400, detail: WORDS.nonceStale });
    server.now -= CHALLENGE_TTL;
    // without ML-KEM a version-3 statement is refused as the server checks its key, before the write's era (no kem: 503,
    // not the older page's 400)
    server.mlkem = false;
    const { kem: _kem, ...noKem } = body;                                 // eslint-disable-line @typescript-eslint/no-unused-vars
    expect(await post("/api/ade/e2e/rotate", noKem)).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    server.mlkem = true;
    expect(await post("/api/ade/e2e/rotate", noKem)).toMatchObject({ status: 400, detail: OLD_PAGE });
    expect((await post("/api/ade/e2e/rotate", body)).status).toBe(200);
  });
});

describe("an account change's confirmation, and a statement, checked in the server's order", () => {
  const call = async (path: string, method: string, body: unknown) => {
    const r = await server.fetch(path, { method, body: JSON.stringify(body) });
    return { status: r.status, detail: ((await r.json()) as Body).detail };
  };
  const removing = (id: string, confirmation?: string) => call(`/api/account/passkeys/${id}`, "DELETE", confirmation === undefined ? {} : { confirmation });
  const turning = (on: boolean, confirmation?: string) => call("/api/account/high-security", "PUT", confirmation === undefined ? { on } : { on, confirmation });
  const forChange = (id: string, change: string) => confirmation(id, "confirm", change);

  it("removes a passkey with a confirmation made for removing it alone, checked before the passkey is looked up (passkeys.remove)", async () => {
    passkey("credAAAA");
    passkey("credBBBB");
    passkey("credCCCC");
    expect(await removing("pk-credCCCC")).toEqual({ status: 403, detail: WORDS.needsConfirm });
    expect(await removing("pk-credCCCC", "confirmation-nope")).toEqual({ status: 403, detail: WORDS.confirmStale });
    expect(await removing("pk-credCCCC", await forChange("credAAAA", "passkey-remove:pk-credBBBB"))).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await removing("pk-credCCCC", await confirmation("credAAAA"))).toEqual({ status: 403, detail: WORDS.confirmForNothing });
    // one that is not there is refused for its confirmation first; with one made for it, 404, and that one left unused
    expect(await removing("pk-nope")).toEqual({ status: 403, detail: WORDS.needsConfirm });
    const forNone = await forChange("credAAAA", "passkey-remove:pk-nope");
    expect(await removing("pk-nope", forNone)).toEqual({ status: 404, detail: "No such passkey." });
    expect(await removing("pk-nope", forNone)).toEqual({ status: 404, detail: "No such passkey." });
    expect((await removing("pk-credCCCC", await forChange("credAAAA", "passkey-remove:pk-credCCCC"))).status).toBe(200);
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credAAAA", "pk-credBBBB"]);
  });

  it("tells a confirmation it was used only after every other check: made for another change, or its passkey gone, first (_use_challenge last)", async () => {
    passkey("credAAAA");
    passkey("credBBBB");
    passkey("credCCCC");
    const forC = await forChange("credAAAA", "passkey-remove:pk-credCCCC");
    expect((await removing("pk-credCCCC", forC)).status).toBe(200);
    expect(await removing("pk-credCCCC", forC)).toEqual({ status: 403, detail: WORDS.confirmStale });
    expect(await removing("pk-credBBBB", forC)).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect((await removing("pk-credAAAA", await forChange("credBBBB", "passkey-remove:pk-credAAAA"))).status).toBe(200);
    expect(await removing("pk-credCCCC", forC)).toEqual({ status: 403, detail: WORDS.confirmGone });
  });

  it("keeps the last hardware key while high security is on, a hardware key's word asked for first (passkeys.remove_in after _use_confirmation)", async () => {
    passkey("credHHHH", "hardware");
    passkey("credAAAA");
    const early = await forChange("credAAAA", "passkey-remove:pk-credHHHH");            // a phone's, made before
    expect((await turning(true, await forChange("credHHHH", "high-security-on"))).status).toBe(200);
    expect(await removing("pk-credHHHH", early)).toEqual({ status: 403, detail: WORDS.needsHardware });
    const own = await forChange("credHHHH", "passkey-remove:pk-credHHHH");
    expect(await removing("pk-credHHHH", own)).toEqual({ status: 409, detail: WORDS.lastHardware });
    expect(await removing("pk-credHHHH", own)).toEqual({ status: 409, detail: WORDS.lastHardware });      // left unused
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credHHHH", "pk-credAAAA"]);
  });

  it("turns high security on or off with a hardware key's confirmation made for that change alone, spent once", async () => {
    passkey("credHHHH", "hardware");
    expect(await turning(true)).toEqual({ status: 403, detail: WORDS.needsHardwareToTurn });
    expect(await turning(true, await forChange("credHHHH", "high-security-off"))).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await turning(true, await forChange("credHHHH", "passkey-add"))).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await turning(true, await confirmation("credHHHH"))).toEqual({ status: 403, detail: WORDS.confirmForNothing });
    expect(server.high).toBe(false);
    const on = await forChange("credHHHH", "high-security-on");
    expect((await turning(true, on)).status).toBe(200);
    expect(await turning(false, on)).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await turning(true, on)).toEqual({ status: 403, detail: WORDS.confirmStale });
    expect((await turning(false, await forChange("credHHHH", "high-security-off"))).status).toBe(200);
    expect(server.high).toBe(false);
  });

  it("while encryption is on, removes no passkey and turns high security on never from Account, in the server's words (REVOKES_ONLY)", async () => {
    await turnedOn("credAAAA");
    passkey("credHHHH", "hardware");
    expect(await removing("pk-credAAAA", await forChange("credAAAA", "passkey-remove:pk-credAAAA"))).toEqual({ status: 409, detail: WORDS.revokesOnly });
    expect(await removing("pk-credAAAA")).toEqual({ status: 409, detail: WORDS.revokesOnly });
    expect(await turning(true, await forChange("credHHHH", "high-security-on"))).toEqual({ status: 409, detail: WORDS.revokesOnly });
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credAAAA", "pk-credHHHH"]);
  });

  it("adds a passkey while encryption is off with an account change's confirmation made for adding one alone; the code's proof counts for nothing then", async () => {
    passkey("credAAAA");
    passkey("credHHHH", "hardware");
    const adding = (body: Body) => post("/api/account/passkeys/options", body);
    // as passkeys.registration_options: _use_confirmation for "passkey-add", since the account can make one
    expect(await adding({})).toEqual({ status: 403, detail: WORDS.needsConfirm });
    expect(await adding({ recovery_proof: { challenge_id: "c", proof: "p" } })).toEqual({ status: 403, detail: WORDS.needsConfirm });
    expect(await adding({ confirmation: await forChange("credAAAA", "passkey-remove:pk-credAAAA") })).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await adding({ confirmation: await confirmation("credAAAA") })).toEqual({ status: 403, detail: WORDS.confirmForNothing });
    const add = await forChange("credAAAA", "passkey-add");
    expect((await adding({ confirmation: add })).status).toBe(200);
    expect(await adding({ confirmation: add })).toEqual({ status: 403, detail: WORDS.confirmStale });
    // while high security is on, a hardware key's
    const early = await forChange("credAAAA", "passkey-add");                // a device passkey's, made before it was on
    expect((await turning(true, await forChange("credHHHH", "high-security-on"))).status).toBe(200);
    expect(await adding({ confirmation: early })).toEqual({ status: 403, detail: WORDS.needsHardware });
    expect((await adding({ confirmation: await forChange("credHHHH", "passkey-add") })).status).toBe(200);
  });

  it("refuses passkey options to an account that keeps 20 passkeys, before any confirmation is read (passkeys.registration_options)", async () => {
    passkey("credAAAA");
    for (let i = 2; i <= 20; i++) passkey(`credP${i}`);
    expect(server.passkeys).toHaveLength(20);
    const adding = (body: Body) => post("/api/account/passkeys/options", body);
    const add = await forChange("credAAAA", "passkey-add");
    expect(await adding({})).toEqual({ status: 409, detail: "An account keeps at most 20 passkeys; remove one first." });
    expect(await adding({ confirmation: add })).toEqual({ status: 409, detail: "An account keeps at most 20 passkeys; remove one first." });
    // the confirmation refused there is unused: with one passkey fewer, it is taken
    server.passkeys.pop();
    expect((await adding({ confirmation: add })).status).toBe(200);
  });

  it("settles a change whose time has come before Account removes a passkey (passkeys.remove)", async () => {
    await turnedOn("credAAAA");
    passkey("credBBBB");
    expect((await post("/api/ade/e2e/off", await turningOff("credAAAA"))).status).toBe(200);
    expect(await removing("pk-credBBBB", await forChange("credAAAA", "passkey-remove:pk-credBBBB"))).toEqual({ status: 409, detail: WORDS.revokesOnly });
    server.now = server.pending!.takes_effect_at;
    expect((await removing("pk-credBBBB", await forChange("credAAAA", "passkey-remove:pk-credBBBB"))).status).toBe(200);
    expect(server.pending).toBeNull();
    expect(server.statements.at(-1)!.purpose).toBe("e2e-off");
  });

  it("settles a change whose time has come before turning high security on, not off (passkeys.set_high_security)", async () => {
    await turnedOn("credAAAA");
    passkey("credHHHH", "hardware");
    expect((await post("/api/ade/e2e/off", await turningOff("credAAAA"))).status).toBe(200);
    expect(await turning(true, await forChange("credHHHH", "high-security-on"))).toEqual({ status: 409, detail: WORDS.revokesOnly });
    server.now = server.pending!.takes_effect_at;
    expect((await turning(false, await forChange("credHHHH", "high-security-off"))).status).toBe(200);
    expect(server.pending?.change).toBe("off");
    expect((await turning(true, await forChange("credHHHH", "high-security-on"))).status).toBe(200);
    expect([server.pending, server.high]).toEqual([null, true]);
  });

  it("reads high security's body as FastAPI does: on, a bool as pydantic reads one, required (HighSecurityIn)", async () => {
    passkey("credHHHH", "hardware");
    const put = (body: Body) => call("/api/account/high-security", "PUT", body);
    expect(await put({})).toEqual({ status: 422, detail: [{ type: "missing", loc: ["body", "on"], msg: "Field required", input: {} }] });
    expect(await put({ on: "maybe" })).toEqual({ status: 422, detail: [{ type: "bool_parsing", loc: ["body", "on"], msg: "Input should be a valid boolean, unable to interpret input", input: "maybe" }] });
    expect(await put({ on: null })).toEqual({ status: 422, detail: [{ type: "bool_type", loc: ["body", "on"], msg: "Input should be a valid boolean", input: null }] });
    expect(server.high).toBe(false);
    // a word it reads as true turns it on, and is the change its confirmation is for
    expect((await put({ on: "yes", confirmation: await forChange("credHHHH", "high-security-on") })).status).toBe(200);
    expect(server.high).toBe(true);
    expect((await put({ on: 0, confirmation: await forChange("credHHHH", "high-security-off") })).status).toBe(200);
    expect(server.high).toBe(false);
  });

  it("says encryption is not on in the server's words, NOT_ON, to each change made while it is off", async () => {
    expect(NOT_ON).toBe("End-to-end encryption is not on for this account: turn it on first.");      // e2e_keys.NOT_ON
    await turnedOn("credAAAA");
    server.rotateEvery = 0;
    const off = await turningOff("credAAAA");
    expect((await post("/api/ade/e2e/off", off)).status).toBe(200);
    server.settle();
    expect(await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).toEqual({ status: 409, detail: NOT_ON });
    expect(await post("/api/ade/e2e/revoke", await revocation("credAAAA"))).toEqual({ status: 409, detail: NOT_ON });
    expect(await post("/api/ade/e2e/off", await turningOff("credAAAA"))).toEqual({ status: 409, detail: NOT_ON });
    expect(await post("/api/ade/e2e/credentials", await enrolment("credBBBB", { passkey: "credAAAA" }))).toEqual({ status: 409, detail: NOT_ON });
  });

  it("adds a passkey while encryption is on with a passkey's confirmation made for adding one alone (passkeys.register_options)", async () => {
    await turnedOn("credAAAA");
    const adding = (body: Body) => post("/api/account/passkeys/options", body);
    expect(await adding({})).toEqual({ status: 403, detail: WORDS.needsPasskey });
    expect(await adding({ confirmation: await forChange("credAAAA", "passkey-remove:pk-credAAAA") })).toEqual({ status: 403, detail: WORDS.confirmOther });
    expect(await adding({ confirmation: await confirmation("credAAAA") })).toEqual({ status: 403, detail: WORDS.confirmForNothing });
    const add = await forChange("credAAAA", "passkey-add");
    expect((await adding({ confirmation: add })).status).toBe(200);
    expect(await adding({ confirmation: add })).toEqual({ status: 403, detail: WORDS.confirmStale });
  });

  it("checks a statement's time, life and key before its nonce (link_commands._check_payload, then _verified)", async () => {
    await turnedOnPq();
    server.rotateEvery = 0;
    const { statement, content } = await signed("credAAAA", "e2e-key", "grace", undefined, C.hybridPublic(await C.hybridFromSeed(C.random(32))));
    const body = { statement, content_key: content(server.roster!.mk_id), kem: "x-wing" };
    const made = Number(statement.payload.created_at);
    const rotating = (payload: Body) => post("/api/ade/e2e/rotate", { ...body, statement: { ...statement, payload: { ...statement.payload, ...payload } } });
    // its nonce's 300 seconds are up: a server without ML-KEM refuses its key first (503), one with it the nonce
    server.now = made + CHALLENGE_TTL;
    server.mlkem = false;
    expect(await rotating({})).toEqual({ status: 503, detail: NO_MLKEM_WORDS });
    server.mlkem = true;
    expect(await rotating({})).toEqual({ status: 400, detail: WORDS.nonceStale });
    // and before the nonce, its time, then its life, then whether it expired
    expect(await rotating({ expires_at: made + 601 })).toEqual({ status: 422, detail: "A e2e-key expires 60 to 600 seconds after it is made." });
    expect(await rotating({ expires_at: made + 59 })).toEqual({ status: 422, detail: "A e2e-key expires 60 to 600 seconds after it is made." });
    expect(await rotating({ expires_at: made + CHALLENGE_TTL })).toEqual({ status: 422, detail: "The payload has expired." });
    server.now = made + CHALLENGE_TTL + 1;
    expect(await rotating({})).toEqual({ status: 422, detail: "The payload's time is more than 5 minutes from the ADE's." });
    server.now = made - CHALLENGE_TTL - 1;
    expect(await rotating({})).toEqual({ status: 422, detail: "The payload's time is more than 5 minutes from the ADE's." });
    // in time, it is placed
    server.now = made;
    expect((await rotating({})).status).toBe(200);
  });

  it("refuses a statement that is not the account's next as it is placed, naming the seq and prev it must have (_check_next)", async () => {
    await turnedOn("credAAAA");
    server.rotateEvery = 0;
    const body = await rotation("credAAAA");
    const top = server.statements.at(-1)!;
    server.statements.push({ ...top, seq: top.seq + 1, payload: '{"other":1}', key_id: "f".repeat(32) });
    expect(await post("/api/ade/e2e/rotate", body)).toEqual({ status: 409, detail: nextWords(top.seq + 2) });
    expect(nextWords(top.seq + 2)).toBe(`A statement is the account's next: seq ${top.seq + 2}, and prev the sha256 of statement ${top.seq + 1}.`);
    expect(nextWords(1)).toBe("A statement is the account's next: seq 1, and prev the sha256 of statement 0 (none: this is the first).");
  });
});

describe("a post-quantum account where the server has no ML-KEM (e2e_keys._era and _era_still)", () => {
  it("places no change to its keys, renewed anchors and turning encryption off too, and gives the code no challenge (503); one placed with ML-KEM fails as its hold ends; a passkey still cancels", async () => {
    const code = await turnedOnPq();
    const mk = server.roster!.mk_id;
    const wrapped = server.credentials[0].wrapped;
    const anchors = async () => post("/api/ade/e2e/anchors", { ...(await renewal([{ passkey_id: "pk-credAAAA", anchor: mk }], { passkey: "credAAAA" })), kem: "x-wing" });
    const off = async () => post("/api/ade/e2e/off", { ...(await turningOff("credAAAA")), kem: "x-wing" });
    // signed or confirmed by a passkey, which make and check no key, and by the code's hybrid proof made while it had ML-KEM
    const offProof = await K.recoveryProof(ACCOUNT, "off", { code, pq: true });
    const anchorsProof = await K.recoveryProof(ACCOUNT, "anchors", { code, pq: true });
    server.mlkem = false;
    for (const place of [anchors, off]) expect(await place()).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    expect(await post("/api/ade/e2e/off", { ...(await turningOff("credAAAA")), recovery_proof: offProof, kem: "x-wing" })).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    const anchorsByCode: Body = { ...(await renewal([{ passkey_id: "pk-credAAAA", anchor: mk }], { passkey: "credAAAA" })), recovery_proof: anchorsProof, kem: "x-wing" };
    delete anchorsByCode.confirmation;
    expect(await post("/api/ade/e2e/anchors", anchorsByCode)).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    expect(await post("/api/ade/e2e/recovery-proof", { purpose: "cancel" })).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    expect(server.pending).toBeNull();
    // placed while it had ML-KEM, each fails as its hold ends once it has none: the code could not cancel it since
    for (const place of [anchors, off]) {
      server.mlkem = true;
      expect((await place()).status).toBe(200);
      server.mlkem = false;
      expect((await view()).pending?.cancel_by_code).toBe(true);
      server.now = server.pending!.takes_effect_at;
      expect((await view()).pending).toBeNull();                            // its time has come: settled as GET is asked
    }
    expect(server.refused).toEqual([NO_MLKEM_WORDS, NO_MLKEM_WORDS]);
    const v = await view();
    expect([v.on, v.pending, v.credentials[0].wrapped]).toEqual([true, null, wrapped]);
    // a passkey's cancel needs no ML-KEM
    server.mlkem = true;
    expect((await off()).status).toBe(200);
    server.mlkem = false;
    expect((await post(`/api/ade/holds/${server.pending!.id}/cancel`, { confirmation: await confirmation("credAAAA", "e2e-cancel") })).status).toBe(200);
    expect(server.pending).toBeNull();
    expect((await view()).on).toBe(true);
  });

  it("refuses as its hold ends a classical change once the account is post-quantum (409, ERA_CHANGED)", async () => {
    await turnedOn("credAAAA");                                            // classical
    server.rotateEvery = 0;
    expect((await post("/api/ade/e2e/rotate", await rotation("credAAAA"))).status).toBe(200);
    server.pq = true;                                                      // made post-quantum meanwhile, as a test stands it in
    server.settle();
    expect(server.refused).toEqual([ERA_CHANGED]);
    expect(server.contentKeys).toHaveLength(1);
  });

  it("refuses as its hold ends a post-quantum change once the account is classical (409), asking for ML-KEM first (503)", async () => {
    await turnedOnPq();
    server.rotateEvery = 0;
    const rotating = async () => {
      const { statement, content } = await signed("credAAAA", "e2e-key", "grace", undefined, C.hybridPublic(await C.hybridFromSeed(C.random(32))));
      return post("/api/ade/e2e/rotate", { statement, content_key: content(server.roster!.mk_id), kem: "x-wing" });
    };
    expect((await rotating()).status).toBe(200);
    // put back to classical meanwhile, as a test stands it in, by a server that has lost ML-KEM: 503, asked first
    server.pq = false;
    server.mlkem = false;
    server.settle();
    server.pq = true;
    server.mlkem = true;
    expect((await rotating()).status).toBe(200);
    server.pq = false;
    server.settle();
    expect(server.refused).toEqual([NO_MLKEM_WORDS, ERA_CHANGED]);
    expect(server.contentKeys).toHaveLength(1);
  });
});

describe("re-sealing, as e2e_keys.reseal_page and reseal take it", () => {
  const LINK = "0123456789abcdef0123456789abcdef";
  const EVENT = { table: "events", id: 1, field: "text" };
  const TALK = { table: "talks", id: `${LINK}:s` };
  /** An envelope written in form 1 holding `plain` bytes (it need not open: the server reads its form alone). */
  const form1 = (plain = 21) => C.PREFIX + C.b64u(new Uint8Array([1, ...C.random(C.OVERHEAD - 1 + plain)]));
  const resealGet = async () => {
    const r = await server.fetch("/api/ade/e2e/reseal");
    return { status: r.status, detail: ((await r.json()) as Body).detail };
  };
  const someBatch = () => [{ where: EVENT, old_sha256: "ab".repeat(32), new: form1() }];
  /** A post-quantum account turned on, its recovery code's key, and the newest key's public bytes. */
  async function account() {
    const code = await turnedOnPq();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    return { rk, EK: C.unb64u(server.contentKeys.at(-1)!.public_key)! };
  }
  const send = async (rk: K.Priv, items: K.ResealSwap[]) => post("/api/ade/e2e/reseal", { items, recovery_proof: await K.resealProof(rk, items), kem: "x-wing" });

  it("refuses a classical account, and one whose encryption is off: its pages, its batches and its proof (409)", async () => {
    await turnedOn("credAAAA");
    expect(await resealGet()).toMatchObject({ status: 409, detail: RESEAL_NOT_PQ });
    expect(await post("/api/ade/e2e/reseal", { items: someBatch(), recovery_proof: { challenge_id: "c", proof: "p" } })).toMatchObject({ status: 409, detail: RESEAL_NOT_PQ });
    expect(await post("/api/ade/e2e/recovery-proof", { purpose: "reseal", digest: "ab".repeat(32) })).toMatchObject({ status: 409, detail: RESEAL_NOT_PQ });
    // post-quantum, encryption off: the newest statement turns it off
    server.pq = true;
    const top = server.statements.at(-1)!;
    server.statements.push({ ...top, seq: top.seq + 1, id: "f".repeat(32), purpose: "e2e-off", key_id: null, public_key: null, retires: null });
    expect(await resealGet()).toMatchObject({ status: 409, detail: RESEAL_OFF });
    expect(await post("/api/ade/e2e/reseal", { items: someBatch(), recovery_proof: { challenge_id: "c", proof: "p" }, kem: "x-wing" }))
      .toMatchObject({ status: 409, detail: RESEAL_OFF });
  });

  it("takes a batch only in its form (422), read before the account and the proof", async () => {
    await turnedOn("credAAAA");                                // classical: its 409 comes after the forms
    const batch = (items: unknown) => post("/api/ade/e2e/reseal", { items });
    const one = someBatch()[0];
    const BATCH = "A re-seal batch is a list of 1 to 200 items, each {where, old_sha256, new}.";
    expect(await batch([])).toMatchObject({ status: 422, detail: BATCH });
    expect(await batch(Array.from({ length: 201 }, (_, i) => ({ ...one, where: { ...EVENT, id: i + 1 } })))).toMatchObject({ status: 422, detail: BATCH });
    expect(await batch([{ ...one, slot: "x" }])).toMatchObject({ status: 422, detail: "A re-seal item is {where, old_sha256, new}." });
    for (const where of [{ ...EVENT, id: "1" }, { ...EVENT, field: "sealed" }, { table: "rows", id: "nope", field: "sealed" }, { table: "rows", id: `${LINK}:s`, field: "text" },
      { ...TALK, field: "text" }, { table: "talks", id: 4 }, { table: "proposals", id: 1 }]) {
      expect(await batch([{ ...one, where }])).toMatchObject({ status: 422, detail: RESEAL_WHERE });
    }
    expect(await batch([{ ...one, old_sha256: "AB".repeat(32) }])).toMatchObject({ status: 422 });
    expect(await batch([{ where: TALK, old_sha256: one.old_sha256, new: form1() }])).toMatchObject({ status: 422, detail: "A conversation sealed again is {messages, key, key_id}." });
    expect(await batch([{ ...one, new: { text: form1() } }])).toMatchObject({ status: 422, detail: "A text sealed again is an envelope's text." });
    expect(await batch([one, { ...one }])).toMatchObject({ status: 422, detail: "A re-seal batch names each stored text once." });
    expect(await batch(someBatch())).toMatchObject({ status: 409, detail: RESEAL_NOT_PQ });
  });

  it("checks the proof first, then each item: 404 where nothing classical is stored, 409 where it changed", async () => {
    const { rk, EK } = await account();
    const stored = form1();
    server.stored = [{ where: EVENT, value: stored, slot: "s" }];
    const old = await K.storedDigest(stored);
    const swap = async (o: Partial<K.ResealSwap> = {}) => ({ where: EVENT, old_sha256: old, new: await C.sealEnvelope2(EK, C.utf8('{"t":1}'), "s"), ...o });
    const good = [await swap()];
    expect(await post("/api/ade/e2e/reseal", { items: good, kem: "x-wing" })).toMatchObject({ status: 422, detail: "A recovery proof is {challenge_id, proof}." });
    // a proof made for another batch: refused before the items are read, here one that names nothing stored
    const elsewhere = [await swap({ where: { ...EVENT, id: 2 } })];
    expect(await post("/api/ade/e2e/reseal", { items: elsewhere, recovery_proof: await K.resealProof(rk, good), kem: "x-wing" }))
      .toMatchObject({ status: 403, detail: PROOF_WRONG });
    expect(await send(rk, elsewhere)).toMatchObject({ status: 404, detail: RESEAL_GONE });
    expect(await send(rk, [await swap({ old_sha256: "cd".repeat(32) })])).toMatchObject({ status: 409, detail: RESEAL_CHANGED });
    // sealed in form 2 already: nothing to seal again there
    server.stored = [{ where: EVENT, value: good[0].new as string, slot: "s" }];
    expect(await send(rk, [await swap({ old_sha256: await K.storedDigest(good[0].new) })])).toMatchObject({ status: 404, detail: RESEAL_GONE });
    server.stored = [{ where: EVENT, value: stored, slot: "s" }];
    expect(await send(rk, [await swap({ new: await C.sealEnvelope2(C.hybridPublic(await C.hybridFromSeed(C.random(32))), C.utf8("{}"), "s") })]))
      .toMatchObject({ status: 422, detail: "An event is not sealed again under the key it must be." });
    expect(await send(rk, [await swap({ new: stored })])).toMatchObject({ status: 422, detail: "An event is not sealed again in its form: An encrypted text here is of form 2." });
    expect(await send(rk, good)).toMatchObject({ status: 200 });
    expect(server.stored[0].value).toBe(good[0].new);
  });

  it("leaves out a question its link sealed while it may yet be answered: not served, and 404 in a batch (RESEAL_GONE)", async () => {
    const { rk, EK } = await account();
    const asked = form1();
    const other = form1();
    server.stored = [{ where: EVENT, value: asked, slot: "s", open: true }, { where: { ...EVENT, id: 2 }, value: other, slot: "s" }];
    const r = await server.fetch("/api/ade/e2e/reseal");
    const page = (await r.json()) as Body;
    expect(page.items.map((x: Body) => x.where.id)).toEqual([2]);
    const swap = { where: EVENT, old_sha256: await K.storedDigest(asked), new: await C.sealEnvelope2(EK, C.utf8('{"t":1}'), "s") };
    expect(await send(rk, [swap])).toMatchObject({ status: 404, detail: RESEAL_GONE });
    expect(RESEAL_GONE).toContain("or it is a question that may yet be answered, whose answer is signed over its text as stored.");
    // closed for good (an answer its session took, a done or a note of a session that refers to it, or the owner's note),
    // it is served and sealed again
    server.stored[0].open = false;
    expect(((await (await server.fetch("/api/ade/e2e/reseal")).json()) as Body).items.map((x: Body) => x.where.id)).toEqual([1, 2]);
    expect(await send(rk, [swap])).toMatchObject({ status: 200 });
  });

  /** A GET of re-sealing's pages, after `after`: its status, the ids it serves, its next, and its words. */
  const page = async (after?: string) => {
    const r = await server.fetch(`/api/ade/e2e/reseal${after === undefined ? "" : `?after=${encodeURIComponent(after)}`}`);
    const b = (await r.json()) as Body;
    return { status: r.status, ids: (b.items ?? []).map((x: Body) => x.where.id), next: b.next, detail: b.detail };
  };
  const talkOf = (id: string, texts: string[]) => ({ where: { table: "talks", id: `${LINK}:${id}` },
    value: { link_id: LINK, session_id: id, earlier: 0, messages: texts.map((text, i) => ({ who: "owner", at: i, text })) } });
  /** An envelope written in form 2 (served nowhere: what re-sealing serves is form 1). */
  const form2 = () => C.PREFIX + C.b64u(new Uint8Array([2, ...C.random(C.OVERHEAD2 + 4)]));

  it("serves what is stored in form 1 alone, events by id, then History rows, then conversations, each page after the cursor the one before gave", async () => {
    await account();
    server.resealPageSize = 2;                                            // small pages, as a test may lower them
    const row =(id: string) => ({ where: { table: "rows", id: `${LINK}:${id}`, field: "sealed" }, value: form1(), slot: "s" });
    server.stored = [talkOf("s", [form1(), "in the clear"]), row("b"), { where: { ...EVENT, id: 9 }, value: form1(), slot: "s" }, row("a"),
      { where: { ...EVENT, id: 2 }, value: form2(), slot: "s" }, talkOf("t", ["in the clear", form2()]), { where: { ...EVENT, id: 3 }, value: form1(), slot: "s" }];
    expect(await page()).toMatchObject({ status: 200, ids: [3, 9], next: "0:9" });
    expect(await page("0:9")).toMatchObject({ ids: [`${LINK}:a`, `${LINK}:b`], next: `1:${LINK}:b` });
    expect(await page(`1:${LINK}:b`)).toMatchObject({ ids: [`${LINK}:s`], next: null });
    // from wherever a cursor points, in its table and on
    expect(await page("0:3")).toMatchObject({ ids: [9, `${LINK}:a`], next: `1:${LINK}:a` });
    expect(await page(`2:${LINK}:a`)).toMatchObject({ ids: [`${LINK}:s`], next: null });
    for (const bad of ["3", "0:0", "0:01", "x:1", `1:${LINK}`, `3:${LINK}:a`, `1:${LINK.slice(1)}:a`]) {
      expect(await page(bad)).toMatchObject({ status: 422, detail: "A re-seal page's after is the next the page before it gave." });
    }
  });

  it("serves at most 100 texts a page, and past its first at most 512 KiB of values, its first whatever its size", async () => {
    await account();
    server.stored = Array.from({ length: 150 }, (_, i) => ({ where: { ...EVENT, id: i + 1 }, value: form1(), slot: "s" }));
    expect(await page()).toMatchObject({ ids: Array.from({ length: 100 }, (_, i) => i + 1), next: "0:100" });    // by default
    server.resealPageSize = 1000;                                          // and no more where a test asks for more
    expect(await page()).toMatchObject({ ids: Array.from({ length: 100 }, (_, i) => i + 1), next: "0:100" });
    expect((await page("0:100")).ids).toHaveLength(50);
    // a text's characters count: two coming to 524,288 fit; one character more does not (filled bytes: only the
    // length is read, and getRandomValues gives 65,536 at most)
    const form1Of = (plain: number) => {
      const b = new Uint8Array(C.OVERHEAD + plain).fill(0x5a);
      b[0] = 1;
      return C.PREFIX + C.b64u(b);
    };
    const [big, small, more] = [form1Of(393033), form1Of(21), form1Of(22)];
    expect([big.length, small.length, more.length]).toEqual([524152, 136, 137]);
    const events = (...values: string[]) => values.map((value, i) => ({ where: { ...EVENT, id: i + 1 }, value, slot: "s" }));
    server.stored = events(big, small, small);
    expect(await page()).toMatchObject({ ids: [1, 2], next: "0:2" });
    server.stored = events(big, more, small);
    expect(await page()).toMatchObject({ ids: [1], next: "0:1" });
    // a first one past it all is served alone
    server.stored = events(form1Of(600 * 1024), small);
    expect(await page()).toMatchObject({ ids: [1], next: "0:1" });
    expect(await page("0:1")).toMatchObject({ ids: [2], next: null });
  });

  it("counts what is stored in form 1 as the server counts it, a question that may yet be answered among them, until a test says otherwise", async () => {
    await account();
    server.stored = [{ where: EVENT, value: form1(), slot: "s", open: true }, { where: { ...EVENT, id: 2 }, value: form2(), slot: "s" },
      { where: { table: "rows", id: `${LINK}:a`, field: "sealed" }, value: form1(), slot: "s" }, talkOf("s", [form1()]), talkOf("t", ["in the clear"])];
    expect((await view()).classical_texts).toEqual({ events: 1, rows: 1, talks: 1, total: 3 });
    expect((await page()).ids).toEqual([`${LINK}:a`, `${LINK}:s`]);           // the open question is counted, not served
    server.classicalTexts = { events: 0, rows: 0, talks: 0, total: 0 };
    expect((await view()).classical_texts).toEqual({ events: 0, rows: 0, talks: 0, total: 0 });
    server.classicalTexts = undefined;
    server.pq = false;
    expect((await view()).classical_texts).toBeNull();
  });

  it("holds a text sealed again to its path's cap, or to what its stored envelope held where that is more (e2e_keys._held)", async () => {
    const { rk, EK } = await account();
    const ROW = { table: "rows", id: `${LINK}:s`, field: "sealed" };
    type Where = { table: string; id: string | number; field?: string };
    const sealedAs = async (where: Where, stored: string, plain: number) =>
      send(rk, [{ where, old_sha256: await K.storedDigest(stored), new: await C.sealEnvelope2(EK, new Uint8Array(plain).fill(0x61), "s") }]);
    const held = async (where: Where, plain: number, most: number, what: string, withheld?: boolean) => {
      const stored = form1(plain);
      server.stored = [{ where, value: stored, slot: "s", ...(withheld === undefined ? {} : { withheld }) }];
      expect(await sealedAs(where, stored, most + 1)).toMatchObject({ status: 422, detail: `${what} is not sealed again in its form: An encrypted text here holds at most ${most} bytes.` });
      expect(await sealedAs(where, stored, most)).toMatchObject({ status: 200 });
    };
    // within its cap: the cap; sealed in place past it: as many bytes as it held, never more
    await held(EVENT, 21, EVENT_PLAIN, "An event");
    await held(EVENT, 20000, 20000, "An event");
    await held(ROW, 21, HISTORY_PLAIN, "A History row", false);
    await held(ROW, 21, HISTORY_WITHHELD_PLAIN, "A History row", true);
    await held(ROW, 7000, 7000, "A History row", false);
    // what a row's cap follows stays the server's: GET serves where, value and slot alone
    server.stored = [{ where: ROW, value: form1(), slot: "s", withheld: true }];
    const page = await (await server.fetch("/api/ade/e2e/reseal")).json() as { items: Record<string, unknown>[] };
    expect(Object.keys(page.items[0]).sort()).toEqual(["slot", "value", "where"]);
  });

  it("holds a conversation past 300,000 bytes as stored to what it holds and its key's: each sealed message as it decodes, each clear one as form 3 holds its words", async () => {
    const { rk, EK } = await account();
    // 4,004 characters: 4,000 of four bytes, then a quote, a backslash, a tab and U+0001, which RFC 8785 escapes in 12
    const astral = String.fromCodePoint(0x1f600).repeat(4000) + '"\\\t' + String.fromCharCode(1);
    expect(C.canonicalBytes({ text: astral }).length).toBe(11 + 16000 + 12);
    const talk = { link_id: LINK, session_id: "s", earlier: 0, messages: [form1(20000), ...Array(17).fill("a".repeat(16400)), astral]
      .map((text, i) => ({ who: "owner", at: i, text })) };
    server.stored = [{ where: TALK, value: talk }];
    // 1,197 + 20,077 (as it decodes) + 17 x (45 + 16,411) + (45 + 16,023), each clear message's {"text"} as RFC 8785
    // bytes beside form 3's 45 (e2e_keys._talk_size): the whole's bound
    const most = 1197 + 20077 + 17 * (45 + 16411) + 45 + 16023;
    const as = async (last: number) => {
      const tkBytes = C.random(C.TALK_KEY_LEN);
      const tk = await C.talkKeyOf(tkBytes);
      const sizes = [20000, ...Array(17).fill(16400), last];
      const n = { key: await C.sealTalkKey(EK, tkBytes, "k"), key_id: tk.id,
        messages: await Promise.all(talk.messages.map(async (m, i) => ({ ...m, text: await C.sealTalkMessage(tk, new Uint8Array(sizes[i]).fill(0x61), "m") }))) };
      return send(rk, [{ where: TALK, old_sha256: await K.storedDigest(talk), new: n }]);
    };
    // 1,197 + 20,045 + 17 x 16,445 + (45 + last): exactly the bound with last 16,242, one past it with 16,243
    expect(await as(16243)).toMatchObject({ status: 422,
      detail: `A conversation's envelopes come to at most ${most} bytes: 300000, or what it holds now and its key's where that is more.` });
    expect(server.resealLog).toHaveLength(0);
    expect(await as(16242)).toMatchObject({ status: 200 });
  });

  it("holds a message in the clear sealed again to the cap, or to its words as they are sealed where that is more (e2e_keys._message_held)", async () => {
    const { rk, EK } = await account();
    // 4,100 characters of four bytes, as put_talk takes them: 16,411 bytes as {"text"}, past a message's 16,400
    const long = String.fromCodePoint(0x1f600).repeat(4100);
    expect(C.canonicalBytes({ text: long }).length).toBe(TALK_PLAIN + 11);
    const swap = async (texts: string[], sizes: number[], total = 300000) => {
      const talk = { link_id: LINK, session_id: "s", earlier: 0, messages: texts.map((text, i) => ({ who: "session", at: i, text })) };
      server.stored = [{ where: TALK, value: talk }];
      server.talkSealedTotal = total;
      const tkBytes = C.random(C.TALK_KEY_LEN);
      const tk = await C.talkKeyOf(tkBytes);
      const n = { key: await C.sealTalkKey(EK, tkBytes, "k"), key_id: tk.id,
        messages: await Promise.all(talk.messages.map(async (m, i) => ({ ...m, text: await C.sealTalkMessage(tk, new Uint8Array(sizes[i]).fill(0x61), "m") }))) };
      return send(rk, [{ where: TALK, old_sha256: await K.storedDigest(talk), new: n }]);
    };
    const past = (most: number) => ({ status: 422, detail: `A message is not sealed again in its form: An encrypted text here holds at most ${most} bytes.` });
    // half sealed: one in form 1, then one in the clear past the cap, which may hold its words and no more
    expect(await swap([form1(), long], [21, TALK_PLAIN + 12])).toEqual(past(TALK_PLAIN + 11));
    expect(await swap([form1(), long], [21, TALK_PLAIN + 11])).toEqual({ status: 200, detail: undefined });
    // one in the clear whose words come to less: what any message holds, 16,400 bytes, and no more
    expect(await swap([form1(), "green"], [21, TALK_PLAIN + 1])).toEqual(past(TALK_PLAIN));
    expect(await swap([form1(), "green"], [21, TALK_PLAIN])).toEqual({ status: 200, detail: undefined });
    // and in all, with the total lowered as the server's tests lower it: 21 bytes as stored come to 98 in form 1, its
    // words in the clear to 45 and their 16 bytes ({"text":"green"}); the same words sealed again leave 32 bytes
    const most = 98 + 45 + 16 + C.TALK_KEY_SEALED_LEN;
    expect(await swap([form1(), "green"], [21 + 33, 16], 1000)).toEqual({ status: 422,
      detail: `A conversation's envelopes come to at most ${most} bytes: 1000, or what it holds now and its key's where that is more.` });
    expect(await swap([form1(), "green"], [21 + 32, 16], 1000)).toEqual({ status: 200, detail: undefined });
  });

  it("holds a conversation's messages to a message's cap or their stored size, and the whole to 300,000 bytes", async () => {
    const { rk, EK } = await account();
    // its first message sealed in place past a message's cap
    const talkOf = (texts: string[]) => ({ link_id: LINK, session_id: "s", earlier: 0, messages: texts.map((text, i) => ({ who: "owner", at: i, text })) });
    const talk = talkOf(Array.from({ length: 19 }, (_, i) => form1(i === 0 ? 20000 : 21)));
    server.stored = [{ where: TALK, value: talk }];
    const as = async (sizes: number[], t = talk) => {
      const tkBytes = C.random(C.TALK_KEY_LEN);
      const tk = await C.talkKeyOf(tkBytes);
      const n = { key: await C.sealTalkKey(EK, tkBytes, "k"), key_id: tk.id,
        messages: await Promise.all(t.messages.map(async (m, i) => ({ ...m, text: await C.sealTalkMessage(tk, new Uint8Array(sizes[i]).fill(0x61), "m") }))) };
      return send(rk, [{ where: TALK, old_sha256: await K.storedDigest(t), new: n }]);
    };
    const sizes = (first: number, rest: number, last = rest) => [first, ...Array(17).fill(rest), last];
    const ONE = "A message is not sealed again in its form: An encrypted text here holds at most";
    expect(await as(sizes(20001, 1))).toMatchObject({ status: 422, detail: `${ONE} 20000 bytes.` });
    expect(await as(sizes(1, 1, TALK_PLAIN + 1))).toMatchObject({ status: 422, detail: `${ONE} 16400 bytes.` });
    // 1,197 + 20,045 + 17 x 15,486 + 15,497: one byte past the whole's bound, each message within its own
    expect(await as(sizes(20000, 15441, 15452))).toMatchObject({ status: 422,
      detail: "A conversation's envelopes come to at most 300000 bytes: 300000, or what it holds now and its key's where that is more." });
    expect(server.resealLog).toHaveLength(0);
    expect(await as(sizes(20000, 15441, 15451))).toMatchObject({ status: 200 });
    // a message stored in another form than 1 (a mix, which the server never makes) is held to a message's cap alone
    const mixed = talkOf([form1(), C.PREFIX + C.b64u(new Uint8Array([3, ...C.random(C.OVERHEAD3 - 1 + 20000)]))]);
    server.stored = [{ where: TALK, value: mixed }];
    expect(await as([1, 20000], mixed)).toMatchObject({ status: 422, detail: `${ONE} 16400 bytes.` });
    expect(await as([1, TALK_PLAIN], mixed)).toMatchObject({ status: 200 });
  });

  it("takes a conversation sealed again only whole and as it was: its messages' fields, in form 3 under its key, the key in form 2 of 1,197 bytes", async () => {
    const { rk, EK } = await account();
    const messages = [{ who: "owner", at: 1, text: form1() }, { who: "session", at: 2, ms: 5, text: form1() }];
    const talk = { link_id: LINK, session_id: "s", earlier: 0, messages };
    server.stored = [{ where: TALK, value: talk }];
    const old = await K.storedDigest(talk);
    const sealed = async (o: { plain?: Uint8Array; key?: Uint8Array } = {}) => {
      const tkBytes = C.random(C.TALK_KEY_LEN);
      const tk = await C.talkKeyOf(tkBytes);
      return { key: await C.sealTalkKey(o.key ?? EK, tkBytes, "k"), key_id: tk.id,
        messages: await Promise.all(messages.map(async (m) => ({ ...m, text: await C.sealTalkMessage(tk, o.plain ?? C.utf8("{}"), "m") }))) };
    };
    const as = async (n: unknown) => send(rk, [{ where: TALK, old_sha256: old, new: n as Record<string, unknown> }]);
    const ok = await sealed();
    expect(await as({ ...ok, messages: ok.messages.slice(1) })).toMatchObject({ status: 422, detail: "A conversation is sealed again whole: each of its messages, in their order." });
    expect(await as({ ...ok, messages: [{ ...ok.messages[0], at: 9 }, ok.messages[1]] }))
      .toMatchObject({ status: 422, detail: "A conversation sealed again keeps each message's place, whose it is and its times as they were." });
    expect(await as({ ...ok, messages: [ok.messages[0], { who: "session", at: 2, text: ok.messages[1].text }] })).toMatchObject({ status: 422 });   // ms left out
    expect(await as({ ...ok, messages: [{ ...ok.messages[0], text: await C.sealEnvelope2(EK, C.utf8("{}"), "m") }, ok.messages[1]] }))
      .toMatchObject({ status: 422, detail: "A message is not sealed again in its form: An encrypted text here is of form 3." });
    const other = await sealed();                               // its messages under another conversation key
    expect(await as({ ...ok, messages: [other.messages[0], ok.messages[1]] })).toMatchObject({ status: 422, detail: "A message is not sealed again under the key it must be." });
    const KEY = "A conversation's key is not sealed again in its form: ";
    expect(await as({ ...ok, key: await C.sealEnvelope2(EK, C.random(33), "k") })).toMatchObject({ status: 422, detail: `${KEY}An encrypted text here holds at most 32 bytes.` });
    expect(await as({ ...ok, key: await C.sealEnvelope2(EK, C.random(31), "k") })).toMatchObject({ status: 422, detail: `${KEY}A conversation's key is sealed in exactly 1197 bytes.` });
    expect(await as({ ...ok, key: (await sealed({ key: C.hybridPublic(await C.hybridFromSeed(C.random(32))) })).key }))
      .toMatchObject({ status: 422, detail: "A conversation's key is not sealed again under the key it must be." });
    expect(await as({ ...ok, key_id: "nope" })).toMatchObject({ status: 422, detail: "The conversation's key_id is 32 lowercase hex characters." });
    expect(server.resealLog).toHaveLength(0);
    expect(await as(ok)).toMatchObject({ status: 200 });
    expect((server.stored[0].value as { key: string }).key).toBe(ok.key);
  });
});
