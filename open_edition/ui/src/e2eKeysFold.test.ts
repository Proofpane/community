// The fold: a classical account turned on again while a new master key is due, that key
// made as the upgrade makes one, in the same change, after which the account is post-quantum. The page's K.Fold against
// the test server (e2eTestkit.ts), and the test server's own fold rules, as e2e_keys.turn_on takes them.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { CLASSICAL, FakeAuthenticator, FakeServer, FOLD_EVERY, FOLD_OWN, FOLD_REKEY, KEPT_PROOFS, NO_MLKEM_WORDS, NO_PLACE, NOT_CLASSICAL,
  PROOF_STALE, REKEY_OWN, SALT_SAME, UPGRADE_KEM, WORDS } from "./e2eTestkit";
import { confirmWithPasskey } from "./passkey";

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
const posted = (path: string) => server.calls.filter((c) => c.method === "POST" && c.path === path);
type Body = Record<string, any>;           // eslint-disable-line @typescript-eslint/no-explicit-any
const bodyOf = (path: string) => posted(path).at(-1)!.body as Body;
const bytesOf = (b64: string) => C.unb64u(b64)!;
const prefer = (id: string) => (allowed: string[]) => (allowed.includes(id) ? id : allowed[0]);

function passkey(id: string) {
  if (!server.passkeys.some((p) => p.credential_id === id)) server.addPasskey(id, id);
  if (!auth.creds.has(id)) auth.add(id);
}

async function unlockAs(id: string, floor: string[] = []) {
  auth.choose = prefer(id);
  return K.unlockWithTouch(await view(), ACCOUNT, floor);
}

/** A classical account (an older release's keys) turned on with `ids`' passkeys, the first's touch, the others added, then
 *  turned off, and `gone` removed from the account while it was off, as Account removes one (confirmed with the first):
 *  a new master key is then due. The ring unlocked with the first, and the code as it was shown. */
async function classicalDue(ids = ["credAAAA", "credBBBB", "credCCCC"], gone = "credCCCC") {
  passkey(ids[0]);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = prefer(ids[0]);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  for (const id of ids.slice(1)) {
    passkey(id);
    const v = await view();
    const ring = await unlockAs(ids[0]);
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
    await e.prepare();
    auth.choose = prefer(id);
    await e.firstTouch();
    await e.secondTouch();
    auth.choose = prefer(ids[0]);
    await e.confirmAndSend();
    server.settle();
  }
  const off = new K.TurnOff(await view(), ACCOUNT);
  await off.prepare();
  auth.choose = prefer(ids[0]);
  await off.signAndSend();
  server.settle();
  if (gone) await removePasskey(`pk-${gone}`, ids[0]);
  return { ring: await unlockAs(ids[0]), code };
}

/** The passkey `id` removed from the account, as Account removes one while encryption is off: confirmed with `by`'s
 *  passkey for that change. */
async function removePasskey(id: string, by: string) {
  auth.choose = () => by;
  const confirmation = await confirmWithPasskey(false, { change: `passkey-remove:${id}` });
  const r = await server.fetch(`/api/account/passkeys/${id}`, { method: "DELETE", body: JSON.stringify({ confirmation }) });
  expect(r.status).toBe(200);
}

/** The fold made from `ring`, keeping the code (`keep`, the code typed) or making a new one, a touch of each of
 *  `touch`'s passkeys, and the statement signed by `signer`; prepared from the account as read now, or from `from`. */
async function folded(ring: K.Ring, touch: string[], signer: string, keep?: string, seal = true, from?: K.E2EView) {
  const f = new K.Fold(ring, from ?? await view(), !!keep);
  await f.useCode(keep ?? f.codeText().split(" ")[f.group]);
  const newCode = keep ? null : f.codeText();
  await f.prepare();
  for (const id of touch) {
    auth.choose = prefer(id);
    await f.touch(id);
  }
  auth.choose = prefer(signer);
  const sent = await f.signAndSend(seal);
  return { f, sent, newCode, body: bodyOf("/api/ade/e2e/turn-on") };
}

describe("turning a classical account on again with post-quantum keys (the fold)", () => {
  it("makes the due master key as the upgrade does, in one turn-on: every passkey that stays a new key, a new code, and the account post-quantum after", async () => {
    const { ring } = await classicalDue();
    const [, held] = [...ring.keys.entries()][0];
    const slot = "proofpane-e2e/1|acct-1|l|event|step|||x";
    const before = await C.sealEnvelope(held.EK, C.utf8('{"text":"old"}'), slot);
    const links = server.chain.length;
    const turnOns = posted("/api/ade/e2e/turn-on").length;
    const { body, newCode, sent } = await folded(ring, ["credBBBB"], "credAAAA");
    // one change, a turn-on: no revocation, and no passkey removed
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(turnOns + 1);
    expect(body.upgrade).toBe(true);
    expect(body.kem).toBe("x-wing");
    expect(body.kept).toBe(false);
    expect(body.seal).toBe(true);
    expect(body.recovery_proof).toBeUndefined();
    expect(body.new_recovery_proof).toBeUndefined();
    expect(body.remove).toBeUndefined();
    expect(body.high_security).toBeUndefined();
    expect(body.statement.payload.v).toBe(3);
    expect(body.statement.payload.retires).toBe("all");
    expect(body.chain.previous).toBe(ring.master.id);
    const byId = Object.fromEntries(body.credentials.map((c: Body) => [c.passkey_id, c]));
    expect(Object.keys(byId).sort()).toEqual(["pk-credAAAA", "pk-credBBBB"]);
    expect(byId["pk-credAAAA"].confirmation).toBeUndefined();          // the signer: its signature is its touch
    expect(typeof byId["pk-credBBBB"].confirmation).toBe("string");
    for (const c of body.credentials) {
      expect(c.anchor).toBe(body.mk_id);
      expect(bytesOf(c.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
      expect(bytesOf(c.wrapped)).toHaveLength(C.WRAP_LEN);
      expect(c.salt).not.toBe(server.credentials.find((x) => x.passkey_id === c.passkey_id)!.salt);
    }
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "credBBBB", "recovery"]);
    expect(body.grants.every((g: Body) => C.envelopeForm(g.sealed)!.form === 2)).toBe(true);
    expect(body.content_keys.every((k: Body) => bytesOf(k.wrapped).length === C.WRAP_LEN)).toBe(true);
    expect(bytesOf(body.recovery.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    // the touch's confirmation was asked for the subject bound to what exists before it, as the upgrade's
    const b = byId["pk-credBBBB"];
    const options = posted("/api/account/confirm/options").filter((x) => x.body?.purpose === "e2e-upgrade-touch");
    expect(options.map((x) => x.body!.change)).toContain(await K.upgradeSubject({ credential_id: "credBBBB", mk_id: body.mk_id, public_key: b.public_key,
      salt: b.salt, statement: body.statement.payload }));
    // what unlocks the new keys in this tab is the signer's new key
    expect(sent.via.kind === "credential" && [sent.via.credentialId, sent.via.anchor]).toEqual(["credAAAA", body.mk_id]);
    // it waits a day, as the upgrade does; any passkey and the code cancel it; it removes none and replaces the code
    const v = await view();
    expect(v.pending).toMatchObject({ change: "turn-on", by_code: false, removes: [], replaces_code: true, cancel_by: ["pk-credAAAA", "pk-credBBBB"],
      cancel_by_code: true, takes_effect_at: server.now + server.delays.recovery });
    expect(K.foldWaits(v)).toBe(true);
    expect(K.upgradeWaits(v)).toBe(false);
    // the fold is a turn-on: a revocation that says upgrade is the upgrade, never the fold
    const asUpgrade = { ...v, pending: { ...v.pending!, change: "revoke" } };
    expect([K.foldWaits(asUpgrade), K.upgradeWaits(asUpgrade)]).toEqual([false, true]);
    // nothing of it opens with this tab's keys, so nothing is checked of it, nor said to be
    expect(K.checkable(v.pending)).toBe(false);
    expect(await K.checkWaiting(ring, v)).toBeNull();
    // nothing is in force while it waits
    expect(server.pq).toBe(false);
    expect(v.on).toBe(false);
    server.settle();
    expect(server.refused).toEqual([]);
    const now = await view();
    expect([now.on, now.pq, now.rekey_due]).toEqual([true, true, false]);
    // one new master key, where turning it on again and then upgrading would make two
    expect(server.chain.length - links).toBe(1);
    for (const id of ["credAAAA", "credBBBB"]) expect((await unlockAs(id)).pq).toBe(true);
    expect((await K.unlockWithCode(now, ACCOUNT, newCode!)).pq).toBe(true);
    // a text sealed before opens through its classical key, wrapped again as its scalar
    expect(await K.openWith(await unlockAs("credAAAA"), before, slot)).toEqual({ ok: true, value: { text: "old" } });
  });

  it("keeps the code it is given: proved twice for turning on, held the code's day, and only the code cancels it", async () => {
    const { ring, code } = await classicalDue();
    const wrong = new K.Fold(ring, await view(), true);
    await expect(wrong.useCode(C.recoveryText(C.random(C.CODE_BYTES)))).rejects.toThrow(K.WRONG_CODE);
    const { body } = await folded(ring, ["credBBBB"], "credAAAA", code);
    expect(body.kept).toBe(true);
    expect(body.recovery_proof).toBeTruthy();
    expect(body.new_recovery_proof).toBeTruthy();
    // both proofs made for turning on, the second for the body's new recovery key
    expect(posted("/api/ade/e2e/recovery-proof").map((x) => x.body)).toEqual([{ purpose: "turn-on" }, { purpose: "turn-on", public_key: body.recovery.public_key }]);
    const rk = await C.recoveryHybrid(C.recoveryBytes(code)!, ACCOUNT);
    expect(body.recovery.public_key).toBe(C.b64u(C.hybridPublic(rk)));
    expect((await view()).pending).toMatchObject({ by_code: true, cancel_by: [], replaces_code: false, takes_effect_at: server.now + server.delays.recovery });
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).pq).toBe(true);
  });

  it("is made only while encryption is off, a new master key is due, nothing waits, the server has ML-KEM and the keys are classical", async () => {
    const { ring, code } = await classicalDue();
    const v = await view();
    const [turnOns, nonces] = [posted("/api/ade/e2e/turn-on").length, posted("/api/ade/link/nonce").length];
    const made = async (w: K.E2EView) => {
      const f = new K.Fold(ring, w, true);
      await f.useCode(code);
      return f.prepare();
    };
    await expect(made({ ...v, on: true })).rejects.toThrow(K.FOLD_ON);
    await expect(made({ ...v, mlkem: false })).rejects.toThrow(K.NO_MLKEM);
    await expect(made({ ...v, pq: true })).rejects.toThrow(K.UPGRADE_NOT_CLASSICAL);
    await expect(made({ ...v, pending: { id: "h", change: "turn-on", takes_effect_at: 0, cancel_by: [], cancel_by_code: true, made_by: null, by_code: false,
      removes: [], replaces_code: false, body: null } })).rejects.toThrow(K.FOLD_WAITING);
    await expect(new K.Fold(ring, v, true).prepare()).rejects.toThrow(K.UPGRADE_CODE_FIRST);
    await expect(new K.Fold(ring, v, false).prepare()).rejects.toThrow(K.UPGRADE_CODE_SHOWN);
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(turnOns);
    expect(posted("/api/ade/link/nonce")).toHaveLength(nonces);   // refused before anything is made
  });

  it("is not made where no new master key is due: turning it on again keeps the keys, and the upgrade follows", async () => {
    const { code } = await classicalDue(["credAAAA", "credBBBB"], "");
    const ring = await unlockAs("credAAAA");
    const v = await view();
    expect(v.rekey_due).toBe(false);
    const f = new K.Fold(ring, v, true);
    await f.useCode(code);
    const nonces = posted("/api/ade/link/nonce").length;
    await expect(f.prepare()).rejects.toThrow(K.FOLD_NOT_DUE);
    expect(posted("/api/ade/link/nonce")).toHaveLength(nonces);
  });

  it("is signed only once every passkey but its signer is touched, and removes none", async () => {
    const { ring } = await classicalDue(["credAAAA", "credBBBB", "credCCCC", "credDDDD"], "credDDDD");
    const f = new K.Fold(ring, await view(), false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    expect(f.seats.map((s) => s.credential.credential_id)).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    expect(f.removes()).toEqual([]);
    expect(f.signers()).toEqual([]);
    const touches = auth.ceremonies.length;
    await expect(f.signAndSend()).rejects.toThrow(K.FOLD_TOUCH_EACH);
    expect(auth.ceremonies).toHaveLength(touches);                // refused before any touch
    auth.choose = prefer("credBBBB");
    await f.touch("credBBBB");
    expect(f.signers()).toEqual([]);                              // two are left: neither signature gives the other its key
    await expect(f.signAndSend()).rejects.toThrow(K.FOLD_TOUCH_EACH);
    auth.choose = prefer("credCCCC");
    await f.touch("credCCCC");
    expect(f.signers()).toEqual(["credAAAA"]);                    // the one left signs, and its signature gives it its key
    expect(f.removes()).toEqual([]);
    auth.choose = prefer("credAAAA");
    await f.signAndSend();
    const body = bodyOf("/api/ade/e2e/turn-on");
    expect(body.credentials.map((c: Body) => c.passkey_id).sort()).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credAAAA", "pk-credBBBB", "pk-credCCCC"]);
  });

  it("once every passkey is touched, any of them signs, its own confirmation then not sent", async () => {
    const { ring } = await classicalDue();
    const f = new K.Fold(ring, await view(), false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    for (const id of ["credAAAA", "credBBBB"]) {
      auth.choose = prefer(id);
      await f.touch(id);
    }
    expect(f.signers()).toEqual(["credAAAA", "credBBBB"]);
    auth.choose = prefer("credBBBB");
    await f.signAndSend();
    const byId = Object.fromEntries(bodyOf("/api/ade/e2e/turn-on").credentials.map((c: Body) => [c.passkey_id, c]));
    expect(byId["pk-credBBBB"].confirmation).toBeUndefined();
    expect(typeof byId["pk-credAAAA"].confirmation).toBe("string");
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
  });

  it("a signature that gave its passkey its new key and was not taken leaves that passkey alone to sign it again", async () => {
    const { ring } = await classicalDue();
    const f = new K.Fold(ring, await view(), false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    auth.choose = prefer("credBBBB");
    await f.touch("credBBBB");
    // the request lost on its way, once: the signature gave Laptop its new key, which has no confirmation of its own
    let lost = false;
    server.answer = (call) => (call.path === "/api/ade/e2e/turn-on" && !lost ? ((lost = true), [503, { detail: "Lost on its way." }]) : null);
    auth.choose = prefer("credAAAA");
    await expect(f.signAndSend()).rejects.toThrow("Lost on its way.");
    expect(f.seats.every((x) => !!x.made)).toBe(true);
    expect(f.signers()).toEqual(["credAAAA"]);
    await f.signAndSend();
    const byId = Object.fromEntries(bodyOf("/api/ade/e2e/turn-on").credentials.map((c: Body) => [c.passkey_id, c]));
    expect([byId["pk-credAAAA"].confirmation, typeof byId["pk-credBBBB"].confirmation]).toEqual([undefined, "string"]);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
  });

  it("refuses before any touch where a passkey's new key could not be made, which it says to prepare again", async () => {
    const { ring } = await classicalDue();
    const f = new K.Fold(ring, await view(), false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    f.seats[1].held = null;                                       // its seed spent by a touch that failed after wrapping it
    expect(f.signers()).toEqual([]);                              // its screen offers no signature
    auth.choose = prefer("credAAAA");
    await f.touch("credAAAA");
    expect(f.signers()).toEqual([]);                              // not even once every other passkey is touched
    const touches = auth.ceremonies.length;
    await expect(f.signAndSend()).rejects.toThrow(K.FOLD_SPENT);
    expect(auth.ceremonies).toHaveLength(touches);
    await f.prepare();
    expect(f.seats.every((s) => !!s.held)).toBe(true);
  });

  it("sends whether it seals in place as it is signed", async () => {
    const { ring } = await classicalDue();
    server.sealed = null;                                         // what the first turn-on sealed
    const { body } = await folded(ring, ["credBBBB"], "credAAAA", undefined, false);
    expect(body.seal).toBe(false);
    server.settle();
    expect(server.sealed).toBeNull();
  });

  it("two tabs: the second's fold takes the first's place where its signer may cancel it, and nothing of the first's is spent on it", async () => {
    const { ring } = await classicalDue();
    const one = new K.Fold(ring, await view(), false);
    await one.useCode(one.codeText().split(" ")[one.group]);
    await one.prepare();
    const two = new K.Fold(ring, await view(), false);
    await two.useCode(two.codeText().split(" ")[two.group]);
    await two.prepare();
    auth.choose = prefer("credBBBB");
    await one.touch("credBBBB");
    auth.choose = prefer("credAAAA");
    await one.signAndSend();
    const first = (await view()).pending!;
    // the second, prepared before the first was placed: every one of its own touches, and B, which may cancel the first, signs
    auth.choose = prefer("credAAAA");
    await two.touch("credAAAA");
    auth.choose = prefer("credBBBB");
    await two.signAndSend();
    const second = (await view()).pending!;
    expect(second.id).not.toBe(first.id);
    expect(second.made_by).toBe("pk-credBBBB");
    // the first tab, preparing again, sees a change waiting
    await expect(one.prepare(await view())).rejects.toThrow(K.FOLD_WAITING);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.roster!.mk_id).toBe(bodyOf("/api/ade/e2e/turn-on").mk_id);
  });
});

/** A fold's request as the page sends it, changed on its way by `edit` before the test server reads it. */
function editing(edit: (b: Body) => void) {
  vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
    if (url === "/api/ade/e2e/turn-on" && init.method === "POST") {
      const b = JSON.parse(String(init.body)) as Body;
      edit(b);
      init = { ...init, body: JSON.stringify(b) };
    }
    return server.fetch(url, init);
  });
}

describe("the test server's fold, as e2e_keys.turn_on takes it", () => {
  it("refuses a turn-on that says upgrade without a new master key made from the current one, or with a credential of its own", async () => {
    const { ring } = await classicalDue();
    editing((b) => { delete b.chain; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(FOLD_REKEY);
    editing((b) => { b.credential = { passkey_id: "pk-credAAAA" }; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(REKEY_OWN);
    editing((b) => { b.upgrade = "yes"; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow("The request's upgrade is true or false.");
    expect(server.pending).toBeNull();
  });

  it("asks kem of it, and refuses it for a post-quantum account", async () => {
    const { ring } = await classicalDue();
    editing((b) => { delete b.kem; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(UPGRADE_KEM);
    vi.stubGlobal("fetch", server.fetch);
    server.pq = true;
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(K.UPGRADE_NOT_CLASSICAL);
    // the page refuses it first; the server, for a body that says it anyway
    const f = new K.Fold(ring, { ...(await view()), pq: false }, false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    server.pq = false;
    await f.prepare();
    auth.choose = prefer("credBBBB");
    await f.touch("credBBBB");
    server.pq = true;
    auth.choose = prefer("credAAAA");
    await expect(f.signAndSend()).rejects.toThrow(NOT_CLASSICAL);
    expect(server.pending).toBeNull();
  });

  it("gives every passkey that stays its new key or refuses it in the fold's words, and takes each touch for this change alone", async () => {
    const { ring } = await classicalDue();
    // B left out of what it gives a new key, though granted the new master key: the fold removes none
    editing((b) => { b.credentials = b.credentials.filter((c: Body) => c.passkey_id !== "pk-credBBBB"); });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(FOLD_EVERY);
    // B's confirmation left out
    editing((b) => { for (const c of b.credentials) delete c.confirmation; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(FOLD_OWN);
    // B's new key sent under a salt other than the one its touch was confirmed for
    editing((b) => { b.credentials.find((x: Body) => x.passkey_id === "pk-credBBBB").salt = C.b64u(C.random(32)); });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(WORDS.confirmOther);
    // every new key under the salt its record has
    editing((b) => { for (const c of b.credentials) c.salt = server.credentials.find((x) => x.passkey_id === c.passkey_id)!.salt; });
    await expect(folded(ring, ["credBBBB"], "credAAAA")).rejects.toThrow(SALT_SAME);
    expect(server.pending).toBeNull();
  });

  it("takes a code kept only with both proofs made for turning on", async () => {
    const { ring, code } = await classicalDue();
    editing((b) => { delete b.new_recovery_proof; });
    await expect(folded(ring, ["credBBBB"], "credAAAA", code)).rejects.toThrow(KEPT_PROOFS);
    // proofs made for a revocation are another change's
    vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
      if (url === "/api/ade/e2e/recovery-proof" && init.method === "POST") {
        init = { ...init, body: JSON.stringify({ ...JSON.parse(String(init.body)), purpose: "revoke" }) };
      }
      return server.fetch(url, init);
    });
    await expect(folded(ring, ["credBBBB"], "credAAAA", code)).rejects.toThrow(PROOF_STALE);
    expect(server.pending).toBeNull();
  });

  it("takes a waiting change's place only as rule (v) allows: a new code's fold signed by a passkey does not take a code-backed one's", async () => {
    const { ring, code } = await classicalDue();
    await folded(ring, ["credBBBB"], "credAAAA", code);
    const kept = (await view()).pending!;
    expect(kept.by_code).toBe(true);
    const f = new K.Fold(ring, { ...(await view()), pending: null }, false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    auth.choose = prefer("credBBBB");
    await f.touch("credBBBB");
    auth.choose = prefer("credAAAA");
    await expect(f.signAndSend()).rejects.toThrow(NO_PLACE);
    expect((await view()).pending!.id).toBe(kept.id);
    // refused for its place before what it gives every passkey is read, as the server asks it
    editing((b) => { b.credentials = b.credentials.filter((c: Body) => c.passkey_id !== "pk-credBBBB"); });
    await expect(folded(ring, ["credBBBB"], "credAAAA", undefined, true, { ...(await view()), pending: null })).rejects.toThrow(NO_PLACE);
    vi.stubGlobal("fetch", server.fetch);
    // another fold that keeps the code, with its proof, does
    await folded(ring, ["credBBBB"], "credAAAA", code, true, { ...(await view()), pending: null });
    expect((await view()).pending!.id).not.toBe(kept.id);
  });

  it("checks it again as its hold ends, and keeps nothing of it once a passkey that unlocks encryption left the account", async () => {
    const { ring } = await classicalDue();
    await folded(ring, ["credBBBB"], "credAAAA");
    await removePasskey("pk-credBBBB", "credAAAA");
    expect(server.pending).not.toBeNull();
    server.settle();
    expect(server.refused.at(-1)).toBe("A new master key is granted to every credential that stays enrolled and to the recovery key, and to nothing else.");
    expect([server.pq, (await view()).on]).toEqual([false, false]);
  });

  it("checks it again as its hold ends, and keeps nothing of it once the server has no ML-KEM", async () => {
    const { ring } = await classicalDue();
    await folded(ring, ["credBBBB"], "credAAAA");
    server.mlkem = false;
    server.settle();
    expect(server.refused.at(-1)).toBe(NO_MLKEM_WORDS);
    expect([server.pq, (await view()).on]).toEqual([false, false]);
  });

  it("checks it again as its hold ends, and keeps nothing of it once the recovery code it kept is no longer the account's", async () => {
    const { ring, code } = await classicalDue();
    await folded(ring, ["credBBBB"], "credAAAA", code);
    const other = await C.recoveryKeys(C.random(C.CODE_BYTES), ACCOUNT);
    server.recovery = { ...server.recovery!, public_key: C.b64u(other.RK) };
    server.settle();
    expect(server.refused.at(-1)).toBe(WORDS.codeChanged);
    expect(server.pq).toBe(false);
  });

  it("checks it again as its hold ends, and keeps nothing of it once the account is post-quantum", async () => {
    const { ring } = await classicalDue();
    await folded(ring, ["credBBBB"], "credAAAA");
    server.pq = true;
    server.settle();
    expect(server.refused.at(-1)).toBe(NOT_CLASSICAL);
    expect((await view()).on).toBe(false);
  });
});
