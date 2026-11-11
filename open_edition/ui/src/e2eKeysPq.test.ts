// The page's keys for a post-quantum account, against the
// test server (e2eTestkit.ts): turning on, unlocking and what it refuses, each change in its post-quantum form with
// kem, the hybrid recovery proof, the browser's post-quantum floor, opening texts by their form, and a conversation's
// messages through its key.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { ApiError } from "./api";
import { talkKeySlot, talkSlot } from "./e2e";
import { CLASSICAL, FakeAuthenticator, FakeServer, KEPT_PROOFS, NO_MLKEM_WORDS, NO_PLACE, NOT_CLASSICAL, NOT_STAYING, OLD_PAGE,
  PROOF_STALE, PROOF_WRONG, RESEAL_GONE, SALT_SAME, UPGRADE_OWN, WORDS, nextWords } from "./e2eTestkit";
import { assertionWith, confirmCeremony, confirmWithPasskey } from "./passkey";

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

function passkey(id: string, tier: "device" | "hardware" = "device") {
  if (!server.passkeys.some((p) => p.credential_id === id)) server.addPasskey(id, id, tier);
  if (!auth.creds.has(id)) auth.add(id);
}

async function unlockAs(id: string, floor: string[] = []) {
  auth.choose = prefer(id);
  return K.unlockWithTouch(await view(), ACCOUNT, floor);
}

/** Encryption turned on with `id`'s passkey as the page now turns it on (post-quantum), its hold ended: the ring and
 *  the recovery code as it was shown. */
async function turnedOn(id = "credAAAA") {
  passkey(id);
  const t = new K.TurnOn(ACCOUNT);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = prefer(id);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  return { ring: await unlockAs(id), code };
}

async function enrolled(ring: K.Ring, id: string, by = "credAAAA") {
  passkey(id);
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

describe("turning a post-quantum account on", () => {
  it("makes X-Wing keys alone: a version-3 statement, 60-byte wraps with the anchor beside the credential's, form-2 grants, kem", async () => {
    passkey("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    expect(t.pq).toBe(true);
    const code = t.codeText();
    await t.prepare(await view());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    const b = bodyOf("/api/ade/e2e/turn-on");
    expect(b.kem).toBe("x-wing");
    expect(b.statement.payload.v).toBe(3);
    expect(bytesOf(b.statement.payload.key)).toHaveLength(C.HYBRID_KEY_LEN);
    expect(bytesOf(b.content_key.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    expect(bytesOf(b.content_key.wrapped)).toHaveLength(C.WRAP_LEN);
    expect(bytesOf(b.credential.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    expect(bytesOf(b.credential.wrapped)).toHaveLength(C.WRAP_LEN);
    expect(b.credential.anchor).toBe(b.mk_id);
    for (const g of b.grants) {
      expect(C.envelopeForm(g.sealed)!.form).toBe(2);
      expect(C.envelopeForm(g.sealed)!.body).toHaveLength(C.OVERHEAD2 + 32);
    }
    // the recovery key is the code's X-Wing key (the post-quantum derivation), not the classical X25519 one
    const rk = await C.recoveryHybrid(C.recoveryBytes(code)!, ACCOUNT);
    expect(b.recovery.public_key).toBe(C.b64u(C.hybridPublic(rk)));
    server.settle();
    expect(server.pq).toBe(true);
    const ring = await unlockAs("credAAAA");
    expect(ring.pq).toBe(true);
    expect(ring.via.kind === "credential" && ring.via.ck.pq).toBe(true);
    expect([...ring.keys.values()].every((k) => k.ek.pq)).toBe(true);
  });

  it("is refused before anything is made where the server cannot use ML-KEM", async () => {
    passkey("credAAAA");
    server.mlkem = false;
    await expect(new K.TurnOn(ACCOUNT).prepare(await view())).rejects.toThrow(K.NO_MLKEM);
    expect(posted("/api/ade/link/nonce")).toHaveLength(0);
  });

  it("starting again keeps it post-quantum", async () => {
    const { ring } = await turnedOn();
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    expect(bodyOf("/api/ade/e2e/off").kem).toBe("x-wing");
    expect(bodyOf("/api/ade/e2e/off").statement.payload.v).toBe(2);           // e2e-off names no key: version 2
    server.settle();
    const again = new K.TurnOnAgain(ring, await view());
    await again.prepare();
    await again.signAndSend(false);
    expect(bodyOf("/api/ade/e2e/turn-on").kem).toBe("x-wing");
    expect(bytesOf(bodyOf("/api/ade/e2e/turn-on").content_key.wrapped)).toHaveLength(C.WRAP_LEN);
    server.settle();
    const fresh = new K.TurnOn(ACCOUNT, true);
    expect(fresh.pq).toBe(true);
  });

  it("starting again from a post-quantum account (replace) makes post-quantum keys, and the account stays post-quantum", async () => {
    await turnedOn();
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    const t = new K.TurnOn(ACCOUNT, true);
    const code = t.codeText();
    await t.prepare(await view());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    await t.send(false);
    const b = bodyOf("/api/ade/e2e/turn-on");
    expect([b.replace, b.kem, b.statement.payload.v]).toEqual([true, "x-wing", 3]);
    expect(bytesOf(b.credential.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).pq).toBe(true);
  });

  it("keeps nothing when the second touch gives another PRF output, and sends nothing", async () => {
    server.addPasskey("credAAAA", "Security key", "hardware");
    auth.add("credAAAA", { unstable: true });
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await view());
    await t.firstTouch();
    await expect(t.secondTouch()).rejects.toThrow(K.UNSTABLE);
    await expect(t.send(true)).rejects.toThrow("Encryption is not being turned on.");
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(0);
  });

  it("refuses a passkey that gives no PRF in this browser, before anything is wrapped", async () => {
    server.addPasskey("credAAAA", "Old key");
    auth.add("credAAAA", { noPrf: true });
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await view());
    await expect(t.firstTouch()).rejects.toThrow(K.NO_PRF_ADD);      // turning it on adds the passkey to encryption
    await expect(t.secondTouch()).rejects.toThrow("Sign with the passkey first.");
    expect(posted("/api/ade/e2e/turn-on")).toHaveLength(0);
  });
});

describe("unlocking a post-quantum account", () => {
  it("opens with the code's X-Wing key, and refuses a wrong code", async () => {
    const { code } = await turnedOn();
    const ring = await K.unlockWithCode(await view(), ACCOUNT, code);
    expect(ring.via.kind).toBe("recovery");
    expect(ring.via.kind === "recovery" && ring.via.rk.pq).toBe(true);
    const other = C.recoveryText(C.random(C.CODE_BYTES));
    await expect(K.unlockWithCode(await view(), ACCOUNT, other)).rejects.toThrow(K.WRONG_CODE);
  });

  it("refuses a seed whose key is not the one listed, a changed anchor, and a classical credential", async () => {
    await turnedOn();
    const row = server.credentials[0];
    const listed = row.public_key;
    // another key listed: the seed's key is compared byte for byte
    const other = await C.hybridFromSeed(C.random(32));
    row.public_key = C.b64u(C.hybridPublic(other));
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NOT_LEAD_BACK);
    row.public_key = listed;
    // the anchor beside the wrap is bound by its AAD
    const anchor = row.anchor!;
    row.anchor = "0".repeat(32);
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.OWN_RECORD);
    row.anchor = anchor;
    expect((await unlockAs("credAAAA")).pq).toBe(true);
    // a classical key among a post-quantum account's credentials
    row.public_key = C.b64u((await C.newPair()).pub);
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.CLASSICAL_IN_PQ);
  });

  it("says a classical recovery key listed for a post-quantum account as such, not as a wrong code", async () => {
    const { code } = await turnedOn();
    server.recovery!.public_key = C.b64u((await C.newPair()).pub);
    await expect(K.unlockWithCode(await view(), ACCOUNT, code)).rejects.toThrow(K.CLASSICAL_IN_PQ);
    await expect(K.codeKey(await view(), ACCOUNT, code)).rejects.toThrow(K.CLASSICAL_IN_PQ);
  });

  it("reads, but makes nothing from, a roster that names a classical key", async () => {
    const { ring, code } = await turnedOn();
    const roster = ring.roster!;
    const mk = await masterFromCode(code);
    server.roster = { mk_id: ring.master.id, sealed: await C.sealRoster(mk, { ...roster, recovery: C.b64u((await C.newPair()).pub) }, ACCOUNT) };
    const again = await unlockAs("credAAAA");
    expect(again.roster).toBeNull();
    expect(again.rosterWhy).toBe(K.ROSTER_CLASSICAL);
    expect(again.keys.size).toBe(1);
  });

  it("puts every post-quantum key away when the ring is forgotten: nothing opens with it after", async () => {
    const { ring } = await turnedOn();
    const [keyId, held] = [...ring.keys.entries()][0];
    const slot = "proofpane-e2e/1|acct-1|l|event|step|||x";
    const env = await C.sealEnvelope2(held.EK, C.utf8('{"text":"hi"}'), slot);
    expect(C.envelopeKeyId(env)).toBe(keyId);
    expect(await K.openWith(ring, env, slot)).toEqual({ ok: true, value: { text: "hi" } });
    K.forgetRing(ring);
    expect(await K.openWith(ring, env, slot)).toEqual({ ok: false, why: K.UNREADABLE });
    const via = ring.via.kind === "credential" ? ring.via.ck : null;
    await expect(C.openGrantHybrid(via!.key as C.Hybrid, server.grants[0].sealed, ACCOUNT, "credAAAA", ring.master.id)).rejects.toThrow(C.PUT_AWAY);
  });
});

/** The master key, opened with the recovery code's X-Wing key: what one who holds the code opens. */
async function masterFromCode(code: string) {
  const rk = await C.recoveryHybrid(C.recoveryBytes(code)!, ACCOUNT);
  const mkId = server.roster!.mk_id;
  const g = server.grants.find((x) => x.to === "recovery" && x.mk_id === mkId)!;
  return C.masterOf(await C.openGrantHybrid(rk, g.sealed, ACCOUNT, "recovery", mkId), ACCOUNT);
}

describe("changes to a post-quantum account", () => {
  it("rotates to a new X-Wing content key, and the tab follows it", async () => {
    const { ring } = await turnedOn();
    server.rotateEvery = 0;
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    await r.signAndSend();
    const b = bodyOf("/api/ade/e2e/rotate");
    expect(b.kem).toBe("x-wing");
    expect(b.statement.payload.v).toBe(3);
    expect(bytesOf(b.content_key.wrapped)).toHaveLength(C.WRAP_LEN);
    server.settle();
    const next = await K.follow(ring, await view());
    expect(next.keys.size).toBe(2);
    expect([...next.keys.values()].every((k) => k.ek.pq)).toBe(true);
  });

  it("adds a passkey with a post-quantum key, its anchor beside its wrap, and unlocks with it", async () => {
    const { ring } = await turnedOn();
    await enrolled(ring, "credBBBB");
    const b = bodyOf("/api/ade/e2e/credentials");
    expect(b.kem).toBe("x-wing");
    expect(b.credential.anchor).toBe(ring.master.id);
    expect(bytesOf(b.credential.wrapped)).toHaveLength(C.WRAP_LEN);
    expect(bytesOf(b.credential.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    expect(C.envelopeForm(b.grant)!.form).toBe(2);
    const by = await unlockAs("credBBBB");
    expect(by.master.id).toBe(ring.master.id);
  });

  it("revokes with a new code: every key wrapped again in 60 bytes, the new code's X-Wing key, and the new code unlocks", async () => {
    const { ring } = await turnedOn();
    await enrolled(ring, "credBBBB");
    const now = await K.follow(ring, await view());
    const newCode = C.random(C.CODE_BYTES);
    const text = C.recoveryText(newCode);
    const r = new K.Revoke(now, await view(), { remove: ["pk-credBBBB"], newCode });
    await r.prepare();
    await r.signAndSend();
    const b = bodyOf("/api/ade/e2e/revoke");
    expect(b.kem).toBe("x-wing");
    expect(b.content_keys.every((k: Body) => bytesOf(k.wrapped).length === C.WRAP_LEN)).toBe(true);
    expect(b.grants.every((g: Body) => C.envelopeForm(g.sealed)!.form === 2)).toBe(true);
    expect(bytesOf(b.recovery.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    server.settle();
    const byCode = await K.unlockWithCode(await view(), ACCOUNT, text);
    expect(byCode.keys.size).toBe(2);
  });

  it("renews a credential's anchor: its seed wrapped again under the fresh salt, the anchor beside it the current master key", async () => {
    const { ring } = await turnedOn();
    const r1 = new K.Revoke(ring, await view(), {});
    await r1.prepare();
    await r1.signAndSend();
    server.settle();
    const now = await unlockAs("credAAAA");
    expect(server.credentials[0].anchor).not.toBe(now.master.id);
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    auth.choose = prefer("credAAAA");
    await renew.touch("credAAAA");
    await renew.send();
    const sent = bodyOf("/api/ade/e2e/anchors");
    expect(sent.kem).toBe("x-wing");
    expect(sent.anchors[0].anchor).toBe(now.master.id);
    expect(bytesOf(sent.anchors[0].wrapped)).toHaveLength(C.WRAP_LEN);
    server.settle();
    expect(server.credentials[0].anchor).toBe(now.master.id);
    expect((await unlockAs("credAAAA")).via.anchor).toBe(now.master.id);
  });

  it("refuses the recovery code's cancel of a change that waits before any proof is asked for where the server has no ML-KEM, which the code's proof needs", async () => {
    const { code } = await turnedOn();
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    server.mlkem = false;
    const v = await view();
    // the server still lists the code among who may cancel (cancellers), though it takes no proof from it now (503)
    const c = new K.CancelChange(ACCOUNT, v, v.pending!);
    expect([c.byCode(), c.codeCancels(), c.passkeys().map((p) => p.passkey_id)]).toEqual([true, false, ["pk-credAAAA"]]);
    const calls = server.calls.length;
    await expect(c.withCode({ code })).rejects.toThrow(K.CODE_NEEDS_MLKEM);
    expect(server.calls.length).toBe(calls);
    // once it can use ML-KEM again, the code cancels it
    server.mlkem = true;
    const w = await view();
    const d = new K.CancelChange(ACCOUNT, w, w.pending!);
    expect(d.codeCancels()).toBe(true);
    await d.withCode({ code });
    expect((await view()).pending).toBeNull();
  });

  it("refuses renewing anchors and turning encryption off where the server has no ML-KEM, before anything is asked or touched", async () => {
    const { ring } = await turnedOn();
    const r1 = new K.Revoke(ring, await view(), {});
    await r1.prepare();
    await r1.signAndSend();
    server.settle();
    const now = await unlockAs("credAAAA");
    server.mlkem = false;
    const v = await view();
    const [calls, touches] = [server.calls.length, auth.ceremonies.length];
    // the server would refuse each (503): the code cancels any change, and to a post-quantum key it proves itself by the
    // hybrid proof alone, which needs ML-KEM
    await expect(new K.RenewAnchors(now, v).prepare()).rejects.toThrow(K.NO_MLKEM);
    await expect(new K.TurnOff(v, ACCOUNT).prepare()).rejects.toThrow(K.NO_MLKEM);
    expect([server.calls.length, auth.ceremonies.length]).toEqual([calls, touches]);
    // once the server can use ML-KEM again, each is made
    server.mlkem = true;
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    auth.choose = prefer("credAAAA");
    await renew.touch("credAAAA");
    await renew.send();
    server.settle();
    expect([server.refused, server.credentials[0].anchor]).toEqual([[], now.master.id]);
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    expect([server.refused, (await view()).on]).toEqual([[], false]);
  });

  it("refuses every other change where the server has no ML-KEM, before anything is asked or touched: each makes or checks a post-quantum key", async () => {
    const { ring, code } = await turnedOn();
    passkey("credBBBB");
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    server.mlkem = false;
    const v = await view();
    expect(v.mlkem).toBe(false);
    const [calls, touches] = [server.calls.length, auth.ceremonies.length];
    await expect(new K.Rotate(ring, v).prepare()).rejects.toThrow(K.NO_MLKEM);
    await expect(new K.Revoke(ring, v, { remove: [] }).prepare()).rejects.toThrow(K.NO_MLKEM);
    await expect(new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!).prepare()).rejects.toThrow(K.NO_MLKEM);
    await expect(new K.Reseal(ring, v, SLOTS).run(rk)).rejects.toThrow(K.NO_MLKEM);
    expect([server.calls.length, auth.ceremonies.length]).toEqual([calls, touches]);
    // turned off while the server had ML-KEM, turning it on again states a post-quantum key: refused too
    server.mlkem = true;
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    server.mlkem = false;
    const offView = await view();
    const asked = server.calls.length;
    await expect(new K.TurnOnAgain(await K.follow(ring, offView), offView).prepare()).rejects.toThrow(K.NO_MLKEM);
    expect(server.calls.length).toBe(asked);
    // once the server can use ML-KEM again, turning it on again is prepared
    server.mlkem = true;
    await new K.TurnOnAgain(await K.follow(ring, await view()), await view()).prepare();
  });

  it("refuses no change of a classical account for ML-KEM, which its keys never need", async () => {
    const { ring, code } = await classicalAccount(["credAAAA", "credBBBB"]);
    server.mlkem = false;
    server.rotateEvery = 0;
    const v = await view();
    await new K.Rotate(ring, v).prepare();
    await new K.Revoke(ring, v, { remove: ["pk-credBBBB"] }).prepare();
    await new K.RenewAnchors(ring, v).prepare();
    await new K.TurnOff(v, ACCOUNT).prepare();
    passkey("credCCCC");
    const w = await view();
    await new K.Enrol(ring, w, w.passkeys.find((p) => p.credential_id === "credCCCC")!).prepare();
    // and the recovery code cancels a change that waits, its proof the classical one
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    const x = await view();
    const c = new K.CancelChange(ACCOUNT, x, x.pending!);
    expect(c.codeCancels()).toBe(true);
    await c.withCode({ code });
    expect((await view()).pending).toBeNull();
  });

  it("proves the recovery code with the hybrid proof, and refuses a challenge of the classical form", async () => {
    const { ring, code } = await turnedOn();
    server.rotateEvery = 0;
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    expect(rk.pq).toBe(true);
    await r.signAndSendWithCode(rk);
    const b = bodyOf("/api/ade/e2e/rotate");
    expect(b.recovery_proof).toBeTruthy();
    expect(server.pending?.byCode).toBe(true);
    // a server that answers with an X25519 key for a post-quantum recovery key: refused, never proved with X25519
    const pair = await C.newPair();
    server.answer = (call) => (call.path === "/api/ade/e2e/recovery-proof"
      ? [200, { challenge_id: "x", server_key: C.b64u(pair.pub), nonce: C.b64u(C.random(32)) }] : null);
    await expect(K.recoveryProof(ACCOUNT, "off", { code, pq: true })).rejects.toThrow(K.CHALLENGE_FORM);
  });

  it("refuses to renew a record that opens to another key than the roster names", async () => {
    const { ring } = await turnedOn();
    const r1 = new K.Revoke(ring, await view(), {});
    await r1.prepare();
    await r1.signAndSend();
    server.settle();
    const now = await unlockAs("credAAAA");
    // the server's record now wraps another seed under this passkey's PRF, with the same anchor
    const row = server.credentials[0];
    const kek = await C.credentialKek(await auth.prf("credAAAA", C.unb64u(row.salt)!), ACCOUNT, "credAAAA");
    row.wrapped = await C.sealCredentialSeed(kek, C.random(32), row.anchor!, ACCOUNT, "credAAAA");
    const renew = new K.RenewAnchors(now, await view());
    await renew.prepare();
    auth.choose = prefer("credAAAA");
    await expect(renew.touch("credAAAA")).rejects.toThrow(K.NOT_LISTED);
    expect(renew.rows).toHaveLength(0);
  });

  it("refuses a challenge that carries both forms for a post-quantum recovery key", async () => {
    const { code } = await turnedOn();
    const pair = await C.newPair();
    const real = server.fetch;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const answer = await real(url, init);
      if (url !== "/api/ade/e2e/recovery-proof") return answer;
      const body = await answer.json();
      return new Response(JSON.stringify({ ...body, server_key: C.b64u(pair.pub) }), { status: 200 });
    });
    await expect(K.recoveryProof(ACCOUNT, "off", { code, pq: true })).rejects.toThrow(K.CHALLENGE_FORM);
  });

  it("the test server refuses a write without kem to a post-quantum account, in the words for an older page", async () => {
    const { ring } = await turnedOn();
    server.rotateEvery = 0;
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    server.answer = null;
    const real = server.fetch;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      if (url === "/api/ade/e2e/rotate") {
        const body = JSON.parse(String(init!.body));
        delete body.kem;
        return real(url, { ...init, body: JSON.stringify(body) });
      }
      return real(url, init);
    });
    await expect(r.signAndSend()).rejects.toThrow(OLD_PAGE);
  });
});

/** A classical account (an older release's keys) turned on with `ids`' passkeys, the first's touch, the others added. */
async function classicalAccount(ids = ["credAAAA"]) {
  passkey(ids[0]);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await view());
  auth.choose = prefer(ids[0]);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  let ring = await unlockAs(ids[0]);
  for (const id of ids.slice(1)) {
    await enrolled(ring, id, ids[0]);
    ring = await K.follow(ring, await view());
  }
  return { ring, code };
}

/** The upgrade made from `ring`, keeping the code (`keep`, the code typed) or making a new one, with a touch of each of
 *  `touch`'s passkeys and the statement signed by the first. */
async function upgraded(ring: K.Ring, touch: string[], keep?: string) {
  const u = new K.Upgrade(ring, await view(), !!keep);
  await u.useCode(keep ?? u.codeText().split(" ")[u.group]);
  const newCode = keep ? null : u.codeText();
  await u.prepare();
  for (const id of touch) {
    auth.choose = prefer(id);
    await u.touch(id);
  }
  auth.choose = prefer(touch[0]);
  const sent = await u.signAndSend();
  return { u, sent, newCode, body: bodyOf("/api/ade/e2e/revoke") };
}

describe("the upgrade of a classical account", () => {
  it("says, where a passkey gives no key as the upgrade keeps it, that the upgrade removes each one not touched", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    auth.creds.get("credBBBB")!.noPrf = true;
    auth.choose = prefer("credBBBB");
    await expect(u.touch("credBBBB")).rejects.toThrow(/the upgrade removes from your account each passkey not touched/);
    // and the touch that signs, which keeps its passkey too
    await expect(u.signAndSend()).rejects.toThrow(/the upgrade removes from your account each passkey not touched/);
  });

  it("has the test server read an upgrade's new master key for its forms before the hardware key's word (_revocation_body)", async () => {
    const { ring } = await classicalAccount(["credAAAA"]);
    // what the page sends, changed on its way: the chain's wrap cut short, and high security asked of a passkey that is
    // not a hardware key; the server reads the forms first, so the wrap is what it says
    vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
      if (url === "/api/ade/e2e/revoke" && init.method === "POST") {
        const b = JSON.parse(String(init.body)) as Body;
        b.chain.wrapped = C.b64u(C.random(27));
        b.high_security = true;
        init = { ...init, body: JSON.stringify(b) };
      }
      return server.fetch(url, init);
    });
    await expect(upgraded(ring, ["credAAAA"])).rejects.toThrow("The chain's wrap is 28 to 1024 bytes, in base64url without padding.");
    expect(server.pending).toBeNull();
  });

  it("puts post-quantum keys in place in one change: each touched passkey a fresh key, the others removed, a new code, every key wrapped again", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const [, held] = [...ring.keys.entries()][0];
    const slot = "proofpane-e2e/1|acct-1|l|event|step|||x";
    const before = await C.sealEnvelope(held.EK, C.utf8('{"text":"old"}'), slot);
    const { u, body, newCode } = await upgraded(ring, ["credAAAA"]);
    expect(body.upgrade).toBe(true);
    expect(body.kem).toBe("x-wing");
    expect(body.kept).toBe(false);
    expect(body.recovery_proof).toBeUndefined();
    expect(body.statement.payload.v).toBe(3);
    expect(body.statement.payload.retires).toBe("all");
    expect(body.remove).toEqual(["pk-credBBBB"]);
    expect(body.high_security).toBe(false);
    expect(body.credentials).toHaveLength(1);
    const c = body.credentials[0];
    expect(c.passkey_id).toBe("pk-credAAAA");
    expect(c.confirmation).toBeUndefined();          // the statement's signer: its signature is its touch
    expect(c.anchor).toBe(body.mk_id);
    expect(bytesOf(c.wrapped)).toHaveLength(C.WRAP_LEN);
    expect(bytesOf(c.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    expect(c.salt).not.toBe(server.credentials.find((x) => x.passkey_id === "pk-credAAAA")!.salt);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(body.grants.every((g: Body) => C.envelopeForm(g.sealed)!.form === 2)).toBe(true);
    expect(body.content_keys.every((k: Body) => bytesOf(k.wrapped).length === C.WRAP_LEN)).toBe(true);
    expect(bytesOf(body.recovery.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    // the touch's confirmation was asked for the subject bound to what exists before it
    const options = posted("/api/account/confirm/options").filter((x) => x.body?.purpose === "e2e-upgrade-touch");
    expect(options.map((x) => x.body!.change)).toContain(await K.upgradeSubject({ credential_id: "credAAAA", mk_id: body.mk_id, public_key: c.public_key,
      salt: c.salt, statement: body.statement.payload }));
    expect(u.removes()).toEqual(["pk-credBBBB"]);
    // nothing is in force while it waits: the account classical, links seal to the classical key
    expect(server.pq).toBe(false);
    expect((await view()).pending?.change).toBe("revoke");
    expect(K.checkable((await view()).pending)).toBe(false);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credAAAA"]);
    const now = await unlockAs("credAAAA");
    expect(now.pq).toBe(true);
    // a text sealed before the upgrade opens through its classical key, wrapped again as its scalar
    expect(await K.openWith(now, before, slot)).toEqual({ ok: true, value: { text: "old" } });
    // the new code unlocks; a form-2 text to the new content key opens
    expect((await K.unlockWithCode(await view(), ACCOUNT, newCode!)).pq).toBe(true);
    const newest = server.contentKeys.at(-1)!;
    expect(bytesOf(newest.public_key)).toHaveLength(C.HYBRID_KEY_LEN);
    const two = await C.sealEnvelope2(bytesOf(newest.public_key), C.utf8('{"text":"new"}'), slot);
    expect(await K.openWith(now, two, slot)).toEqual({ ok: true, value: { text: "new" } });
  });

  it("keeps the code it is given: checked against the recovery key, then sent with both proofs", async () => {
    const { ring, code } = await classicalAccount();
    const wrong = new K.Upgrade(ring, await view(), true);
    await expect(wrong.useCode(C.recoveryText(C.random(C.CODE_BYTES)))).rejects.toThrow(K.WRONG_CODE);
    const { body } = await upgraded(ring, ["credAAAA"], code);
    expect(body.kept).toBe(true);
    expect(body.recovery_proof).toBeTruthy();
    expect(body.new_recovery_proof).toBeTruthy();
    // the hybrid proof's challenge was made for the body's new recovery key
    const asked = posted("/api/ade/e2e/recovery-proof").map((x) => x.body);
    expect(asked).toEqual([{ purpose: "revoke" }, { purpose: "revoke", public_key: body.recovery.public_key }]);
    const rk = await C.recoveryHybrid(C.recoveryBytes(code)!, ACCOUNT);
    expect(body.recovery.public_key).toBe(C.b64u(C.hybridPublic(rk)));
    expect((await view()).pending?.by_code).toBe(true);
    server.settle();
    expect(server.pq).toBe(true);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).pq).toBe(true);
  });

  it("refuses what it cannot make: an account post-quantum already, encryption off, a change waiting, no ML-KEM, no code, no touch", async () => {
    const { ring, code } = await classicalAccount();
    const v = await view();
    const fresh = () => new K.Upgrade(ring, v, true);
    await expect(fresh().prepare()).rejects.toThrow(K.UPGRADE_CODE_FIRST);
    const nocode = new K.Upgrade(ring, v, false);
    await expect(nocode.prepare()).rejects.toThrow(K.UPGRADE_CODE_SHOWN);
    await expect(nocode.useCode(nocode.codeText().split(" ")[nocode.group] === "ZZZZ" ? "YYYY" : "ZZZZ")).rejects.toThrow("Those are not the four characters asked for.");
    const u = fresh();
    await u.useCode(code);
    await expect(new K.Upgrade(ring, { ...v, pq: true }, true).prepare()).rejects.toThrow(K.UPGRADE_NOT_CLASSICAL);
    const off = new K.Upgrade(ring, { ...v, on: false }, true);
    await off.useCode(code);
    await expect(off.prepare()).rejects.toThrow(K.UPGRADE_OFF);
    const waiting = new K.Upgrade(ring, { ...v, pending: { id: "h", change: "rotate", takes_effect_at: 0, cancel_by: [], cancel_by_code: true,
      made_by: null, by_code: false, removes: [], replaces_code: false, body: null } }, true);
    await waiting.useCode(code);
    await expect(waiting.prepare()).rejects.toThrow(K.UPGRADE_WAITING);
    const mlkem = new K.Upgrade(ring, { ...v, mlkem: false }, true);
    await mlkem.useCode(code);
    await expect(mlkem.prepare()).rejects.toThrow(K.NO_MLKEM);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
    // a seat whose new key cannot be made again (its seed spent by a touch that failed after wrapping it) signs nothing
    await u.prepare();
    for (const s of u.seats) s.held = null;
    const touches = auth.ceremonies.length;
    await expect(u.signAndSend()).rejects.toThrow(K.NONE_TO_SIGN);
    expect(auth.ceremonies).toHaveLength(touches);                     // refused before any touch
  });

  it("refuses a touch whose PRF output does not open its current record (asked under another salt), and keeps nothing of it", async () => {
    const { ring, code } = await classicalAccount();
    const u = new K.Upgrade(ring, await view(), true);
    await u.useCode(code);
    await u.prepare();
    u.seats[0].credential = { ...u.seats[0].credential, salt: C.b64u(C.random(32)) };     // another salt: another PRF
    auth.choose = prefer("credAAAA");
    await expect(u.touch("credAAAA")).rejects.toThrow(K.OWN_RECORD);
    expect(u.seats[0].made).toBeUndefined();
  });

  it("the test server refuses a staying passkey's confirmation made for another subject", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    for (const id of ["credAAAA", "credBBBB"]) {
      auth.choose = prefer(id);
      await u.touch(id);
    }
    // each credential's confirmation was made for its own subject: swapped, neither is spent on the other's
    const [a, b] = u.seats;
    [a.made!.confirmation, b.made!.confirmation] = [b.made!.confirmation, a.made!.confirmation];
    auth.choose = prefer("credAAAA");
    await expect(u.signAndSend()).rejects.toThrow(WORDS.confirmOther);
    expect(server.pending).toBeNull();
    // nor one sent with a salt other than the one its touch was confirmed for, though its own passkey made it
    [a.made!.confirmation, b.made!.confirmation] = [b.made!.confirmation, a.made!.confirmation];
    b.made!.salt = C.b64u(C.random(32));
    await expect(u.signAndSend()).rejects.toThrow(WORDS.confirmOther);
    expect(server.pending).toBeNull();
  });

  it("keeps the passkey whose touch signs: N passkeys, N touches, and no confirmation for the signer", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    const touches = auth.ceremonies.length;
    auth.choose = prefer("credAAAA");
    await u.touch("credAAAA");
    expect(u.removes()).toEqual(["pk-credBBBB"]);
    // the signature's ceremony offers both: the one kept already its PRF under its current salt alone, the other its
    // fresh salt too, so the touch that signs keeps it
    auth.choose = prefer("credBBBB");
    const { via } = await u.signAndSend();
    const signing = auth.ceremonies.at(-1)!;
    expect([...signing.allow].sort()).toEqual(["credAAAA", "credBBBB"]);
    expect(Object.keys(signing.seconds)).toEqual(["credBBBB"]);
    expect(auth.ceremonies.length - touches).toBe(2);
    const body = bodyOf("/api/ade/e2e/revoke");
    expect(body.remove).toEqual([]);
    const byId = Object.fromEntries(body.credentials.map((c: Body) => [c.passkey_id, c]));
    expect(byId["pk-credBBBB"].confirmation).toBeUndefined();
    expect(typeof byId["pk-credAAAA"].confirmation).toBe("string");
    // what unlocks the new keys in this tab is the signer's new key
    expect(via.kind === "credential" && [via.credentialId, via.passkeyId]).toEqual(["credBBBB", "pk-credBBBB"]);
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
    expect((await K.openRing(await view(), ACCOUNT, via)).pq).toBe(true);
    for (const id of ["credAAAA", "credBBBB"]) expect((await unlockAs(id)).pq).toBe(true);
  });

  it("after a send that was not taken, the passkey whose signature kept it signs again: it has no confirmation of its own", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    auth.choose = prefer("credBBBB");
    await u.touch("credBBBB");
    let refuse = true;
    server.answer = (call) => (refuse && call.path === "/api/ade/e2e/revoke" ? [503, { detail: "Not now." }] : null);
    auth.choose = prefer("credAAAA");
    await expect(u.signAndSend()).rejects.toThrow("Not now.");
    expect(u.signers()).toEqual(["credAAAA"]);
    refuse = false;
    auth.choose = prefer("credBBBB");                               // offered A alone, so A signs
    await u.signAndSend();
    expect(auth.ceremonies.at(-1)!.allow).toEqual(["credAAAA"]);
    const byId = Object.fromEntries(bodyOf("/api/ade/e2e/revoke").credentials.map((c: Body) => [c.passkey_id, c]));
    expect(byId["pk-credAAAA"].confirmation).toBeUndefined();
    expect(typeof byId["pk-credBBBB"].confirmation).toBe("string");
    expect((await view()).pending?.body?.upgrade).toBe(true);
  });

  it("keeps a lone passkey with the signature's touch alone, asking no confirmation", async () => {
    const { ring } = await classicalAccount();
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    const touches = auth.ceremonies.length;
    const confirms = posted("/api/account/confirm").length;
    auth.choose = prefer("credAAAA");
    await u.signAndSend();
    expect(auth.ceremonies.length - touches).toBe(1);
    expect(posted("/api/account/confirm")).toHaveLength(confirms);
    const body = bodyOf("/api/ade/e2e/revoke");
    expect(body.credentials.map((c: Body) => c.passkey_id)).toEqual(["pk-credAAAA"]);
    expect(body.credentials[0].confirmation).toBeUndefined();
    expect(body.remove).toEqual([]);
    server.settle();
    expect(server.refused).toEqual([]);
    expect((await unlockAs("credAAAA")).pq).toBe(true);
  });

  it("checks the signer's record as it checks a touch's: one that opens to a key the roster does not name keeps nothing", async () => {
    const { ring, code } = await classicalAccount();
    const u = new K.Upgrade(ring, await view(), true);
    await u.useCode(code);
    await u.prepare();
    u.seats[0].listed = { ...u.seats[0].listed, public_key: C.b64u((await C.newPair()).pub) };
    auth.choose = prefer("credAAAA");
    await expect(u.touch("credAAAA")).rejects.toThrow(K.NOT_LISTED);
    expect(u.seats[0].made).toBeUndefined();
    expect(u.seats[0].held).not.toBeNull();                          // refused before its new seed is wrapped
    await expect(u.signAndSend()).rejects.toThrow(K.NOT_LISTED);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
  });

  it("the test server refuses, as the server does, a passkey that stays without its own touch's confirmation, and a kept code without both proofs", async () => {
    const { ring, code } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    auth.choose = prefer("credBBBB");
    await u.touch("credBBBB");
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e/revoke") for (const c of call.body!.credentials as Body[]) delete c.confirmation;
      return null;
    };
    auth.choose = prefer("credAAAA");
    await expect(u.signAndSend()).rejects.toThrow(UPGRADE_OWN);
    expect(server.pending).toBeNull();
    // an upgrade that does not say kem, and a new code's upgrade with a proof of the code kept
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e/revoke") delete call.body!.kem;
      return null;
    };
    await expect(u.signAndSend()).rejects.toThrow("An upgrade to post-quantum keys says so: kem x-wing.");
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e/revoke") call.body!.new_recovery_proof = { challenge_id: "proof-0", proof: "AA" };
      return null;
    };
    await expect(u.signAndSend()).rejects.toThrow("A new_recovery_proof proves the code kept: an upgrade with a new code (kept false) carries none.");
    expect(server.pending).toBeNull();
    // keeping the code: refused without the hybrid proof, and with one made for another recovery key than the body's
    const k = new K.Upgrade(ring, await view(), true);
    await k.useCode(code);
    await k.prepare();
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e/revoke") delete call.body!.new_recovery_proof;
      return null;
    };
    await expect(k.signAndSend()).rejects.toThrow(KEPT_PROOFS);
    const other = C.b64u(C.hybridPublic(await C.recoveryHybrid(C.random(C.CODE_BYTES), ACCOUNT)));
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e/revoke") (call.body!.recovery as Body).public_key = other;
      return null;
    };
    await expect(k.signAndSend()).rejects.toThrow(PROOF_WRONG);
    expect(server.pending).toBeNull();
    server.answer = null;
    await k.signAndSend();
    expect((await view()).pending?.by_code).toBe(true);
    expect((await view()).pending?.replaces_code).toBe(false);          // it keeps the code: a new key from it
  });

  it("while the upgrade waits it is a revocation for rule (v): a change by a passkey that may cancel it takes its place, and one that could not is refused", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB", "credCCCC"]);
    server.rotateEvery = 0;
    const before = await view();
    // an upgrade keeping C and A, signed with C, that removes B
    await upgraded(ring, ["credCCCC", "credAAAA"]);
    const v = await view();
    expect(K.upgradeWaits(v)).toBe(true);
    expect(K.upgradeWaits(before)).toBe(false);
    expect([...v.pending!.cancel_by].sort()).toEqual(["pk-credAAAA", "pk-credCCCC"]);
    expect([K.inPlaceBy(v, "pk-credAAAA"), K.inPlaceBy(v, "pk-credBBBB"), K.codeMakes(v)]).toEqual([true, false, true]);
    // a tab that read the keys before it waited signs a rotation with B, which could not cancel it: refused
    const r = new K.Rotate(ring, before);
    await r.prepare();
    auth.choose = prefer("credBBBB");
    await expect(r.signAndSend()).rejects.toThrow(NO_PLACE);
    expect((await view()).pending?.body?.upgrade).toBe(true);
    // C removed, signed with A, which may cancel it: that revocation takes the upgrade's place and ends it
    const rv = new K.Revoke(ring, v, { remove: ["pk-credCCCC"] });
    await rv.prepare();
    auth.choose = prefer("credAAAA");
    await rv.signAndSend();
    const after = await view();
    expect(K.upgradeWaits(after)).toBe(false);
    expect([after.pending?.change, after.pending?.removes]).toEqual(["revoke", ["pk-credCCCC"]]);
    server.settle();
    expect(server.pq).toBe(false);
    expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credAAAA", "pk-credBBBB"]);
  });

  it("adding a passkey while the upgrade waits is any change in its place: confirmed only by a passkey that may cancel the upgrade", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB", "credCCCC"]);
    // an upgrade keeping C, signed with C, that removes A and B
    await upgraded(ring, ["credCCCC"]);
    passkey("credDDDD");
    const v = await view();
    expect(K.upgradeWaits(v)).toBe(true);
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credDDDD")!);
    // A and B unlock the keys and stay after adding D, but neither may cancel the upgrade: only C confirms it
    expect(e.confirmers().map((p) => p.passkey_id)).toEqual(["pk-credCCCC"]);
    expect(e.byCode).toBe(false);
    await e.prepare();
    auth.choose = prefer("credDDDD");
    await e.firstTouch();
    await e.secondTouch();
    auth.choose = prefer("credCCCC");
    await e.confirmAndSend();
    const after = await view();
    expect(K.upgradeWaits(after)).toBe(false);
    expect(after.pending?.change).toBe("credentials");
  });

  it("is touched and signed within four and a half minutes of being prepared, refused before any touch after, and prepared again with the code kept", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    expect(u.left()).toBe(0);
    await u.useCode(u.codeText().split(" ")[u.group]);
    const start = Date.now();
    await u.prepare();
    expect(u.left()).toBeGreaterThan(K.UPGRADE_WINDOW_MS - 60_000);
    auth.choose = prefer("credBBBB");
    await u.touch("credBBBB");
    const touches = auth.ceremonies.length;
    vi.spyOn(Date, "now").mockReturnValue(start + K.UPGRADE_WINDOW_MS + 1);
    expect(u.left()).toBe(0);
    auth.choose = prefer("credAAAA");
    await expect(u.touch("credAAAA")).rejects.toThrow(K.UPGRADE_STALE);
    await expect(u.signAndSend()).rejects.toThrow(K.UPGRADE_STALE);
    expect(auth.ceremonies).toHaveLength(touches);                     // refused before any touch
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
    // prepared again from the account as read now, the code as it was given: every passkey touched again
    await u.prepare(await view());
    expect(u.left()).toBe(K.UPGRADE_WINDOW_MS);
    expect(u.seats.map((x) => x.made)).toEqual([undefined, undefined]);
    auth.choose = prefer("credBBBB");
    await u.touch("credBBBB");
    auth.choose = prefer("credAAAA");
    await u.signAndSend();
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
  });

  it("asks a touch's confirmation before its new seed is used: one the server does not take leaves the seat to touch again", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    let refuse = true;
    server.answer = (call) => (refuse && call.path === "/api/account/confirm" ? [403, { detail: "That challenge has ended." }] : null);
    auth.choose = prefer("credBBBB");
    await expect(u.touch("credBBBB")).rejects.toThrow("That challenge has ended.");
    const seat = u.seats.find((x) => x.passkey.passkey_id === "pk-credBBBB")!;
    expect(seat.made).toBeUndefined();
    expect(seat.held).not.toBeNull();
    refuse = false;
    await vi.waitFor(() => expect(seat.ceremony).not.toBeNull());     // a fresh challenge for it
    await u.touch("credBBBB");
    expect(typeof seat.made?.confirmation).toBe("string");
    auth.choose = prefer("credAAAA");
    await u.signAndSend();
    server.settle();
    expect(server.refused).toEqual([]);
    expect(server.pq).toBe(true);
  });

  it("keeps nothing of a touch whose preparation was dropped or replaced meanwhile", async () => {
    const { ring } = await classicalAccount(["credAAAA", "credBBBB"]);
    const u = new K.Upgrade(ring, await view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    // dropped while the browser's prompt was open: no fresh challenge is asked for a seat it dropped
    const drop = (allowed: string[]) => {
      u.dispose(false);
      return allowed[0];
    };
    auth.choose = drop;
    const options = posted("/api/account/confirm/options").length;
    await expect(u.touch("credBBBB")).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    await new Promise((r) => setTimeout(r, 20));
    expect(posted("/api/account/confirm/options")).toHaveLength(options);
    // and while the signature's was
    await u.prepare();
    auth.choose = drop;
    await expect(u.signAndSend()).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    expect(posted("/api/ade/e2e/revoke")).toHaveLength(0);
    // while its confirmation was asked for
    await u.prepare();
    auth.choose = prefer("credBBBB");
    server.answer = (call) => {
      if (call.path === "/api/account/confirm") u.dispose(false);
      return null;
    };
    await expect(u.touch("credBBBB")).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    server.answer = null;
    // while its new seed was wrapped: the key that opened is put away, and the seat keeps nothing
    await u.prepare();
    const seat = u.seats.find((x) => x.passkey.passkey_id === "pk-credBBBB")!;
    const held = seat.held!;
    seat.held = {
      seal: async (kek, anchor, account, credentialId) => {
        const wrapped = await held.seal(kek, anchor, account, credentialId);
        u.dispose(false);
        return wrapped;
      },
      drop: () => held.drop(),
    };
    await expect(u.touch("credBBBB")).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    expect([seat.made, seat.key]).toEqual([undefined, undefined]);
    // two preparations at once: the one overtaken keeps nothing; and one whose code was given again meanwhile
    const first = u.prepare();
    const second = u.prepare();
    await expect(first).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    await second;
    const third = u.prepare();
    await u.useCode(u.codeText().split(" ")[u.group]);
    await expect(third).rejects.toThrow(K.UPGRADE_PREPARED_AGAIN);
    await u.prepare();
    auth.choose = prefer("credAAAA");
    await u.signAndSend();
    expect(bodyOf("/api/ade/e2e/revoke").credentials.map((c: Body) => c.passkey_id)).toEqual(["pk-credAAAA"]);
  });

  it("a tab holding the classical keys does not follow the upgrade: it is told the keys are post-quantum now (UPGRADED_SINCE)", async () => {
    const { ring } = await classicalAccount();
    await upgraded(ring, ["credAAAA"]);
    server.settle();
    await expect(K.follow(ring, await view())).rejects.toThrow(K.UPGRADED_SINCE);
    // an upgrade may have kept the code: the words do not send the person to a new one it never made
    expect(K.UPGRADED_SINCE).toContain("or with the recovery code (the new one, if a new one was made)");
  });

  it("a tab holding the classical keys behind a start again elsewhere is told so too: the words do not say upgrade alone", async () => {
    const { ring } = await classicalAccount();
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    auth.choose = prefer("credAAAA");
    await off.signAndSend();
    server.settle();
    const t = new K.TurnOn(ACCOUNT, true);
    const code = t.codeText();
    await t.prepare(await view());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    await t.send(false);
    server.settle();
    expect(server.pq).toBe(true);
    await expect(K.follow(ring, await view())).rejects.toThrow(K.UPGRADED_SINCE);
    expect(K.UPGRADED_SINCE).toContain("(upgraded, or started again elsewhere)");
    // what it sends the person to unlocks them: the passkey that started again, or the new code
    expect((await unlockAs("credAAAA")).pq).toBe(true);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).pq).toBe(true);
  });

  it("after an upgrade that kept the code, a tab behind it is sent to that same code, which unlocks the new keys", async () => {
    const { ring, code } = await classicalAccount();
    await upgraded(ring, ["credAAAA"], code);
    server.settle();
    expect(server.pq).toBe(true);
    await expect(K.follow(ring, await view())).rejects.toThrow(K.UPGRADED_SINCE);
    expect((await K.unlockWithCode(await view(), ACCOUNT, code)).pq).toBe(true);
  });

  it("a classical account opens no post-quantum content key", async () => {
    const { ring } = await classicalAccount();
    const seed = C.random(32);
    const EK = C.hybridPublic(await C.hybridFromSeed(seed));
    const keyId = await C.keyIdOf(EK);
    server.contentKeys.push({ key_id: keyId, public_key: C.b64u(EK), mk_id: ring.master.id,
      wrapped: await C.wrapContentSeed(ring.master, seed, keyId, ACCOUNT) });
    const again = await unlockAs("credAAAA");
    expect(again.keys.has(keyId)).toBe(false);
    expect(again.unopened).toContain(keyId);
  });

  it("reads the server's count of texts still stored classical: its total where it gives one, else the places' sum", () => {
    expect(K.readView({ pq: true, classical_texts: { events: 1, rows: 2, talks: 3, total: 9 } }).classical_texts)
      .toEqual({ events: 1, rows: 2, talks: 3, total: 9 });
    expect(K.classicalTextCount(K.readView({ pq: true, classical_texts: { events: 1, rows: 2, talks: 3 } }))).toBe(6);
    expect(K.classicalTextCount(K.readView({ pq: false, classical_texts: null }))).toBeNull();
  });

  it("lists the links that have not said they can seal post-quantum", async () => {
    const v = K.readView({ links: [{ link_id: "a", name: "A", key_id: null, last_required_at: null, pins: null, seals: [1, 2, 3] },
      { link_id: "b", name: "B", key_id: null, last_required_at: null, pins: null, seals: [1] },
      { link_id: "c", name: "C", key_id: null, last_required_at: null, pins: null }] });
    expect(K.linksNotPq(v).map((l) => l.link_id)).toEqual(["b", "c"]);
  });
});

const SLOTS = { talk: talkSlot, talkKey: talkKeySlot };
/** A link's id as the server writes one (32 hex): a conversation's where names it, LINK_ID:SESSION_ID. */
const LINK = "0123456789abcdef0123456789abcdef";

/** A classical account with texts sealed to its content key, upgraded with a new code and unlocked: the stored texts
 *  as the server would serve them to re-seal (two events, one that does not open where it is said to be, and a
 *  conversation of two messages), the new code, and the ring. */
async function upgradedWithTexts() {
  const { ring } = await classicalAccount();
  const [, held] = [...ring.keys.entries()][0];
  const slotA = "proofpane-e2e/1|acct-1|l|event|step|||a";
  const slotB = "proofpane-e2e/1|acct-1|l|event|step|||b";
  const a = await C.sealEnvelope(held.EK, C.utf8('{"text":"one"}'), slotA);
  const b = await C.sealEnvelope(held.EK, C.utf8('{"text":"two"}'), slotB);
  const talk = { link_id: LINK, session_id: "s", earlier: 0, messages: [
    { who: "owner", at: 1, ms: null, text: "" }, { who: "session", at: 2, ms: null, text: "" }] };
  for (const [i, m] of talk.messages.entries()) {
    m.text = await C.sealEnvelope(held.EK, C.utf8(JSON.stringify({ text: `m${i}` })), talkSlot(ACCOUNT, LINK, "s", i, 2, 0, m.who, m.at, m.ms));
  }
  const { newCode } = await upgraded(ring, ["credAAAA"]);
  server.settle();
  const now = await unlockAs("credAAAA");
  server.stored = [
    { where: { table: "events", id: 1, field: "text" }, value: a, slot: slotA },
    { where: { table: "events", id: 2, field: "text" }, value: b, slot: slotB },
    { where: { table: "events", id: 3, field: "text" }, value: a, slot: slotB },        // does not open there
    { where: { table: "talks", id: `${LINK}:s` }, value: talk },
  ];
  return { ring: now, code: newCode!, slotA, slotB };
}

describe("the test server's upgrade rules, one by one, and its checks again as the hold ends", () => {
  /** An upgrade the page makes from a classical account of `ids` (the first signs; `touch` the passkeys touched), its
   *  body caught before the server places it: nothing it was asked with is used, so each change of it is sent again. */
  async function caught(ids: string[], touch: string[], keep = false) {
    const { ring, code } = await classicalAccount(ids);
    let body: Body | null = null;
    server.answer = (call) => {
      if (call.path !== "/api/ade/e2e/revoke") return null;
      body = call.body as Body;
      return [200, { hold: { id: "caught", takes_effect_at: server.now + 1, told: 0 }, told: 0 }];
    };
    await upgraded(ring, touch, keep ? code : undefined);
    server.answer = null;
    return { body: body! as Body, ring, code };
  }
  const send = async (b: Body) => {
    const r = await server.fetch("/api/ade/e2e/revoke", { method: "POST", body: JSON.stringify(b) });
    return { status: r.status, detail: ((await r.json()) as Body).detail as string };
  };
  const copy = (b: Body): Body => JSON.parse(JSON.stringify(b));

  it("refuses each part out of its form, and places the body as the page made it", async () => {
    const { body } = await caught(["credAAAA", "credBBBB"], ["credAAAA", "credBBBB"]);
    const refusals: [string, (b: Body) => void, number, string?][] = [
      ["another kem", (b) => { b.kem = "kyber"; }, 422, "A write's kem is x-wing, or none."],
      ["no kem", (b) => { delete b.kem; }, 422, "An upgrade to post-quantum keys says so: kem x-wing."],
      ["a content key's wrap not 60 bytes", (b) => { b.content_key.wrapped = C.b64u(C.random(76)); }, 422],
      ["no recovery", (b) => { delete b.recovery; }, 422, "The upgrade makes the recovery key post-quantum too: it carries recovery, from the code kept or a new one."],
      ["a classical recovery key", (b) => { b.recovery.public_key = C.b64u(C.random(32)); }, 422],
      ["no credentials", (b) => { b.credentials = []; }, 422],
      ["a passkey twice", (b) => { b.credentials.push(b.credentials[0]); }, 422, "Each credential of the upgrade names one passkey, once."],
      ["an anchor not the new master key", (b) => { b.credentials[0].anchor = "0".repeat(32); }, 422],
      ["a confirmation not text", (b) => { b.credentials[1].confirmation = 7; }, 422],
      ["kept not true or false", (b) => { b.kept = "yes"; }, 422, "The request's kept is true or false."],
      ["a staying passkey left out", (b) => { b.credentials = b.credentials.slice(0, 1); }, 422],
      ["a salt the record has", (b) => { b.credentials[0].salt = server.credentials.find((c) => c.passkey_id === b.credentials[0].passkey_id)!.salt; }, 422, SALT_SAME],
      ["a new proof with a new code", (b) => { b.new_recovery_proof = { challenge_id: "x", proof: "y" }; }, 422],
      ["the code kept without its proofs", (b) => { b.kept = true; }, 403, KEPT_PROOFS],
      ["a staying passkey without its confirmation", (b) => { delete b.credentials.find((c: Body) => c.confirmation).confirmation; }, 403, UPGRADE_OWN],
    ];
    for (const [what, change, status, detail] of refusals) {
      const b = copy(body);
      change(b);
      const r = await send(b);
      expect([what, r.status], `${what}: ${r.detail}`).toEqual([what, status]);
      if (detail) expect([what, r.detail]).toEqual([what, detail]);
    }
    expect(server.pending).toBeNull();
    server.mlkem = false;
    expect(await send(copy(body))).toMatchObject({ status: 503, detail: NO_MLKEM_WORDS });
    server.mlkem = true;
    expect((await send(copy(body))).status).toBe(200);
    expect(K.upgradeWaits(await view())).toBe(true);
  });

  it("takes each staying passkey's confirmation only from that passkey, made for its own touch and subject", async () => {
    const { body } = await caught(["credAAAA", "credBBBB"], ["credAAAA", "credBBBB"]);
    const other = body.credentials.find((c: Body) => c.confirmation) as Body;
    const row = server.credentials.find((c) => c.passkey_id === other.passkey_id)!;
    const subject = await K.upgradeSubject({ credential_id: row.credential_id, mk_id: body.mk_id, public_key: other.public_key, salt: other.salt,
      statement: body.statement.payload });
    const touchBy = async (id: string, change?: string) => {
      auth.choose = prefer(id);
      const c = await confirmCeremony("e2e-upgrade-touch", false, change ? { change } : undefined);
      return (await K.E2EApi.confirm(c.challenge_id, (await assertionWith(c.ask)).credential)).confirmation;
    };
    const signer = body.credentials.find((c: Body) => !c.confirmation).passkey_id as string;
    const signerCred = server.credentials.find((c) => c.passkey_id === signer)!.credential_id;
    const cases: [string, string, string][] = [
      ["made by the signer, for this subject", await touchBy(signerCred, subject), UPGRADE_OWN],
      ["made for no change", await touchBy(row.credential_id), WORDS.confirmForNothing],
      ["made for another subject", await touchBy(row.credential_id, `e2e-upgrade:${"ab".repeat(16)}`), WORDS.confirmOther],
      ["made for another touch", await (async () => { auth.choose = prefer(row.credential_id); const c = await confirmCeremony("e2e-anchor-touch");
        return (await K.E2EApi.confirm(c.challenge_id, (await assertionWith(c.ask)).credential)).confirmation; })(), WORDS.confirmOther],
      ["one this server never gave", "confirmation-nope", WORDS.confirmStale],
    ];
    for (const [what, confirmation, detail] of cases) {
      const b = copy(body);
      b.credentials.find((c: Body) => c.passkey_id === other.passkey_id).confirmation = confirmation;
      expect([what, (await send(b)).detail]).toEqual([what, detail]);
    }
    expect((await send(copy(body))).status).toBe(200);
  });

  it("keeping the code: its new proof of the hybrid kind, made to the body's new key", async () => {
    const { body } = await caught(["credAAAA"], ["credAAAA"], true);
    expect(body.kept).toBe(true);
    const wrongKey = copy(body);
    wrongKey.recovery.public_key = C.b64u(C.hybridPublic(await C.hybridFromSeed(C.random(32))));
    expect((await send(wrongKey)).detail).toBe(PROOF_WRONG);
    const classicalKind = copy(body);
    classicalKind.new_recovery_proof = body.recovery_proof;
    expect((await send(classicalKind)).detail).toBe(PROOF_STALE);
    const withoutNew = copy(body);
    delete withoutNew.new_recovery_proof;
    expect(await send(withoutNew)).toMatchObject({ status: 403, detail: KEPT_PROOFS });
    const withoutOld = copy(body);
    delete withoutOld.recovery_proof;
    expect(await send(withoutOld)).toMatchObject({ status: 403, detail: KEPT_PROOFS });
    expect((await send(copy(body))).status).toBe(200);
  });

  it("refuses an account post-quantum already, and checks it again as its hold ends", async () => {
    const { body } = await caught(["credAAAA"], ["credAAAA"]);
    server.pq = true;
    expect(await send(copy(body))).toMatchObject({ status: 409, detail: NOT_CLASSICAL });
    server.pq = false;
    // placed, then the account made post-quantum before it takes effect: dropped, nothing of it kept
    expect((await send(copy(body))).status).toBe(200);
    const before = server.credentials.map((c) => c.public_key);
    server.pq = true;
    server.settle();
    expect(server.refused).toEqual([NOT_CLASSICAL]);
    expect(server.credentials.map((c) => c.public_key)).toEqual(before);
  });

  it("drops it as its hold ends when another statement was stated meanwhile: it is no longer the next", async () => {
    const { body } = await caught(["credAAAA"], ["credAAAA"]);
    expect((await send(copy(body))).status).toBe(200);
    const stated = server.statements.at(-1)!.seq + 1;
    server.statements.push({ ...server.statements.at(-1)!, seq: stated, payload: '{"other":1}', key_id: "f".repeat(32) });
    server.settle();
    // in the server's words (_check_next): the seq the account's next now has, after the one stated meanwhile
    expect(server.refused).toEqual([nextWords(stated + 1)]);
    expect(nextWords(stated + 1)).toBe(`A statement is the account's next: seq ${stated + 1}, and prev the sha256 of statement ${stated}.`);
    expect(server.pq).toBe(false);
  });

  it("drops it as its hold ends when a passkey it keeps no longer unlocks the keys: its grants are no longer to exactly those that stay", async () => {
    const { body } = await caught(["credAAAA", "credBBBB"], ["credAAAA", "credBBBB"]);
    expect((await send(copy(body))).status).toBe(200);
    // the signer's credential gone meanwhile: the new master key's grants name it, so they are checked first and
    // refused (the signer's own check after them can then never refuse an upgrade, as on the server's _apply_revoke)
    const signer = body.credentials.find((c: Body) => !c.confirmation).passkey_id as string;
    server.credentials = server.credentials.filter((c) => c.passkey_id !== signer);
    server.settle();
    expect(server.refused).toEqual(["A new master key is granted to every credential that stays enrolled and to the recovery key, and to nothing else."]);
    expect(server.pq).toBe(false);
  });

  it("refuses a version-2 statement: the upgrade states a post-quantum key", async () => {
    const { body } = await caught(["credAAAA"], ["credAAAA"]);
    const EK = C.random(32);
    const p = await K.prepareStatement(await view(), "e2e-key", { EK, keyId: await C.keyIdOf(EK), retires: "all" });
    expect(p.payload.v).toBe(2);
    auth.choose = prefer("credAAAA");
    const b = copy(body);
    b.statement = await K.signStatement(p, ["credAAAA"]);
    expect(await send(b)).toMatchObject({ status: 422, detail: `The content key is ${C.HYBRID_KEY_LEN} bytes, in base64url without padding.` });
    expect(server.pending).toBeNull();
    expect((await send(copy(body))).status).toBe(200);
  });

  it("refuses an account whose encryption is off: there is nothing to upgrade while it is", async () => {
    const { body } = await caught(["credAAAA"], ["credAAAA"]);
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    auth.choose = prefer("credAAAA");
    await off.signAndSend();
    server.settle();
    expect((await view()).on).toBe(false);
    // the same upgrade, its statement and content key made again after the turning off, so they are the account's next
    const EK = C.hybridPublic(await C.hybridFromSeed(C.random(32)));
    const keyId = await C.keyIdOf(EK);
    const p = await K.prepareStatement(await view(), "e2e-key", { EK, keyId, retires: "all" });
    const b = copy(body);
    b.statement = await K.signStatement(p, ["credAAAA"]);
    b.content_key = { key_id: keyId, public_key: C.b64u(EK), mk_id: body.mk_id, wrapped: C.b64u(C.random(C.WRAP_LEN)) };
    expect(await send(b)).toMatchObject({ status: 409, detail: "End-to-end encryption is not on for this account: turn it on first." });
    expect(server.pending).toBeNull();
  });

  it("refuses an upgrade its signer does not stay in, without the code's proof (NOT_STAYING)", async () => {
    const { body } = await caught(["credAAAA", "credBBBB"], ["credAAAA", "credBBBB"]);
    const signer = body.credentials.find((c: Body) => !c.confirmation) as Body;
    const signerCred = server.credentials.find((c) => c.passkey_id === signer.passkey_id)!.credential_id;
    // the signer removed, with its credential and its grant: the rest is the upgrade as the page made it
    const b = copy(body);
    b.remove = [...b.remove, signer.passkey_id];
    b.credentials = b.credentials.filter((c: Body) => c.passkey_id !== signer.passkey_id);
    b.grants = b.grants.filter((g: Body) => g.to !== signerCred);
    expect(await send(b)).toMatchObject({ status: 403, detail: NOT_STAYING });
    expect(server.pending).toBeNull();
    expect((await send(copy(body))).status).toBe(200);
  });
});

describe("a change placed while the server had ML-KEM, as its hold ends once it has none", () => {
  /** The change `place` makes, placed while the server has ML-KEM; then it has none, and the change's hold ends: refused
   *  as the server's _era_still refuses it (503), since the recovery code could not cancel it since, and nothing it
   *  would make is made. */
  async function failsAsItEnds(place: () => Promise<unknown>) {
    await place();
    expect(server.pending).not.toBeNull();
    const keys = () => JSON.stringify([server.statements, server.credentials, server.contentKeys, server.grants, server.recovery, server.pq]);
    const before = keys();
    server.mlkem = false;
    server.settle();
    expect(server.refused).toEqual([NO_MLKEM_WORDS]);
    expect(keys()).toBe(before);
  }

  it("a rotation", async () => {
    const { ring } = await turnedOn();
    server.rotateEvery = 0;
    await failsAsItEnds(async () => {
      const r = new K.Rotate(ring, await view());
      await r.prepare();
      await r.signAndSend();
    });
  });

  it("a revocation", async () => {
    const { ring } = await turnedOn();
    await failsAsItEnds(async () => {
      const r = new K.Revoke(ring, await view(), {});
      await r.prepare();
      await r.signAndSend();
    });
  });

  it("a passkey added", async () => {
    const { ring } = await turnedOn();
    passkey("credBBBB");
    await failsAsItEnds(async () => {
      const v = await view();
      const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
      await e.prepare();
      auth.choose = prefer("credBBBB");
      await e.firstTouch();
      await e.secondTouch();
      auth.choose = prefer("credAAAA");
      await e.confirmAndSend();
    });
  });

  it("renewed anchors", async () => {
    const { ring } = await turnedOn();
    const r = new K.Revoke(ring, await view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const now = await unlockAs("credAAAA");
    await failsAsItEnds(async () => {
      const renew = new K.RenewAnchors(now, await view());
      await renew.prepare();
      auth.choose = prefer("credAAAA");
      await renew.touch("credAAAA");
      await renew.send();
    });
  });

  it("turning encryption off", async () => {
    await turnedOn();
    await failsAsItEnds(async () => {
      const off = new K.TurnOff(await view(), ACCOUNT);
      await off.prepare();
      await off.signAndSend();
    });
    expect((await view()).on).toBe(true);
  });

  it("turning it on again", async () => {
    const { ring } = await turnedOn();
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await failsAsItEnds(async () => {
      const again = new K.TurnOnAgain(ring, await view());
      await again.prepare();
      await again.signAndSend(false);
    });
    expect((await view()).on).toBe(false);
  });

  it("the first turn-on, which makes the account post-quantum", async () => {
    passkey("credAAAA");
    await failsAsItEnds(async () => {
      const t = new K.TurnOn(ACCOUNT);
      t.codeText();
      await t.prepare(await view());
      auth.choose = prefer("credAAAA");
      await t.firstTouch();
      await t.secondTouch();
      await t.send(true);
    });
    expect((await view()).on).toBe(false);
  });

  it("the upgrade", async () => {
    const { ring } = await classicalAccount();
    await failsAsItEnds(() => upgraded(ring, ["credAAAA"]));
    expect((await view()).pq).toBe(false);
  });
});

describe("re-sealing what is stored classical", () => {
  it("seals every text again to the newest key, a conversation whole, with the code's proof bound to each batch", async () => {
    const { ring, code, slotA } = await upgradedWithTexts();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    const r = new K.Reseal(ring, await view(), SLOTS);
    const seen: number[] = [];
    await r.run(rk, (x) => seen.push(x.sealed));
    expect([r.sealed, r.skipped]).toEqual([3, 1]);
    expect(seen.at(-1)).toBe(3);
    const newest = server.contentKeys.at(-1)!.key_id;
    const [one, two, left, talk] = server.stored;
    expect(C.envelopeForm(one.value)!.form).toBe(2);
    expect(C.envelopeKeyId(one.value)).toBe(newest);
    expect(C.envelopeForm(two.value)!.form).toBe(2);
    expect(C.envelopeForm(left.value)!.form).toBe(1);                  // did not open: left as it is
    expect(await K.openWith(ring, one.value as string, slotA)).toEqual({ ok: true, value: { text: "one" } });
    const t = talk.value as { messages: { who: string; at: number; text: string }[]; key: string; key_id: string };
    const keySlot = talkKeySlot(ACCOUNT, LINK, "s", 2, 0);
    for (const [i, m] of t.messages.entries()) {
      expect(await K.openTalkText(ring, { key: t.key, key_id: t.key_id, keySlot }, m.text, talkSlot(ACCOUNT, LINK, "s", i, 2, 0, m.who, m.at, null)))
        .toEqual({ ok: true, value: { text: `m${i}` } });
    }
    expect(server.resealLog).toHaveLength(3);
    // each batch's proof was asked for its binding, the hashes of what it swaps
    const asked = posted("/api/ade/e2e/recovery-proof").map((x) => x.body!);
    const sent = posted("/api/ade/e2e/reseal").map((x) => x.body!);
    expect(asked).toHaveLength(sent.length);
    for (const [i, s] of sent.entries()) {
      expect(s.kem).toBe("x-wing");
      expect(asked[i]).toEqual({ purpose: "reseal", digest: await K.resealBinding(s.items as K.ResealSwap[]) });
    }
  });

  it("leaves a conversation whole when one of its messages does not open, and never seals again what is not form 1", async () => {
    const { ring, code, slotA } = await upgradedWithTexts();
    server.resealPageSize = 2;                                      // two pages: the first two events, then the rest
    const talk = server.stored[3].value as { messages: { text: string }[] };
    talk.messages[1].text = server.stored[0].value as string;       // form 1, but sealed where it does not open here
    const before = JSON.stringify(talk);
    // a form-2 text served to re-seal (the server serves form 1 alone): it is left, never sent again
    const newest = server.contentKeys.at(-1)!;
    const form2 = await C.sealEnvelope2(bytesOf(newest.public_key), C.utf8('{"text":"new"}'), slotA);
    server.stored.push({ where: { table: "events", id: 5, field: "text" }, value: form2, slot: slotA });
    // the second page as the server serves it (the third event and the conversation), and the form-2 text beside them
    const served = (x: (typeof server.stored)[number]) => ({ where: x.where, value: x.value, ...(x.slot === undefined ? {} : { slot: x.slot }) });
    server.answer = (call) => (call.method === "GET" && call.path === "/api/ade/e2e/reseal?after=0%3A2"
      ? [200, JSON.parse(JSON.stringify({ items: [2, 3, 4].map((i) => served(server.stored[i])), next: null }))] : null);
    const r = new K.Reseal(ring, await view(), SLOTS);
    await r.run(await K.codeKey(await view(), ACCOUNT, code));
    expect([r.sealed, r.skipped]).toEqual([2, 3]);
    expect(JSON.stringify(server.stored[3].value)).toBe(before);
    expect(server.stored[4].value).toBe(form2);
    expect(server.resealLog.map((x) => (x.where as { id: number }).id)).toEqual([1, 2]);
  });

  it("seals a conversation sent half sealed again whole: its messages in the clear sealed for the first time, as their words", async () => {
    const { ring, code } = await upgradedWithTexts();
    // a client other than a link sent the second message in the clear while encryption was off, and a turn-on did not
    // seal it in place: 4,100 characters of four bytes, 16,411 bytes as its words, past the 16,400 a message holds
    const words = String.fromCodePoint(0x1f600).repeat(4100);
    const talk = server.stored[3].value as { messages: { text: string }[] };
    talk.messages[1].text = words;
    const r = new K.Reseal(ring, await view(), SLOTS);
    await r.run(await K.codeKey(await view(), ACCOUNT, code));
    expect([r.sealed, r.skipped]).toEqual([3, 1]);
    const t = server.stored[3].value as { messages: { who: string; at: number; text: string }[]; key: string; key_id: string };
    expect(t.messages.map((m) => C.envelopeForm(m.text)?.form)).toEqual([3, 3]);
    const keySlot = talkKeySlot(ACCOUNT, LINK, "s", 2, 0);
    const opened = await Promise.all(t.messages.map((m, i) =>
      K.openTalkText(ring, { key: t.key, key_id: t.key_id, keySlot }, m.text, talkSlot(ACCOUNT, LINK, "s", i, 2, 0, m.who, m.at, null))));
    expect(opened).toEqual([{ ok: true, value: { text: "m0" } }, { ok: true, value: { text: words } }]);
    // exactly its words' bytes, in form 3
    expect(C.envelopeForm(t.messages[1].text)!.body.length).toBe(C.OVERHEAD3 + C.canonicalBytes({ text: words }).length);
  });

  it("leaves a conversation sent half sealed whole when its message in form 1 does not open here", async () => {
    const { ring, code } = await upgradedWithTexts();
    const talk = server.stored[3].value as { messages: { text: string }[] };
    talk.messages[0].text = "in the clear";
    talk.messages[1].text = server.stored[0].value as string;       // form 1, but sealed where it does not open here
    const before = JSON.stringify(talk);
    const r = new K.Reseal(ring, await view(), SLOTS);
    await r.run(await K.codeKey(await view(), ACCOUNT, code));
    expect([r.sealed, r.skipped]).toEqual([2, 2]);
    expect(JSON.stringify(server.stored[3].value)).toBe(before);
    expect(server.resealLog.map((x) => (x.where as { id: number }).id)).toEqual([1, 2]);
  });

  it("the server swaps nothing where it stores no text sealed to a classical key", async () => {
    const { ring, code, slotA } = await upgradedWithTexts();
    const served = server.stored[0];
    const newest = server.contentKeys.at(-1)!;
    // sealed again since it was read: the page re-seals what it read, the server finds form 2 there
    server.stored = [{ ...served, value: await C.sealEnvelope2(bytesOf(newest.public_key), C.utf8('{"text":"x"}'), slotA) }];
    server.answer = (call) => (call.method === "GET" && call.path.startsWith("/api/ade/e2e/reseal") ? [200, { items: [served], next: null }] : null);
    await expect(new K.Reseal(ring, await view(), SLOTS).run(await K.codeKey(await view(), ACCOUNT, code))).rejects.toThrow(RESEAL_GONE);
    expect(server.resealLog).toHaveLength(0);
  });

  it("is refused for a classical account, and while encryption is off, before anything is asked", async () => {
    const { ring: classical } = await classicalAccount(["credZZZZ"]);
    await expect(new K.Reseal(classical, await view(), SLOTS).run({ pq: false, key: (await C.newPair()).priv })).rejects.toThrow(K.RESEAL_NOT_PQ);
    server.pq = true;
    const off = { ...(await view()), on: false };
    const calls = server.calls.length;
    await expect(new K.Reseal({ ...classical, pq: true }, off, SLOTS).run({ pq: false, key: (await C.newPair()).priv })).rejects.toThrow(K.RESEAL_OFF);
    expect(server.calls).toHaveLength(calls);
  });

  it("seals a conversation past 300,000 bytes again whole: what it held as stored and its key's leave it room", async () => {
    const { ring, code } = await upgradedWithTexts();
    const classicalKey = bytesOf(server.contentKeys.find((k) => bytesOf(k.public_key).length === 32)!.public_key);
    // nineteen messages of 16,000 bytes: 306,052 in form 3 beside its key (19 x 16,045 and 1,197), within the 306,660
    // they hold as stored and their key's (19 x 16,077 and 1,197; e2e_keys._reseal_swap)
    const talk = { link_id: LINK, session_id: "t", earlier: 0, messages: [] as { who: string; at: number; ms: null; text: string }[] };
    for (let i = 0; i < 19; i++) talk.messages.push({ who: "session", at: i, ms: null, text: "" });
    for (const [i, m] of talk.messages.entries()) {
      m.text = await C.sealEnvelope(classicalKey, C.canonicalBytes({ text: "x".repeat(16000 - 11) }), talkSlot(ACCOUNT, LINK, "t", i, 19, 0, m.who, m.at, m.ms));
    }
    server.stored.push({ where: { table: "talks", id: `${LINK}:t` }, value: talk });
    const r = new K.Reseal(ring, await view(), SLOTS);
    await r.run(await K.codeKey(await view(), ACCOUNT, code));
    expect([r.sealed, r.skipped]).toEqual([4, 1]);
    const sealed = server.stored[4].value as { key: string; messages: { text: string }[] };
    expect(sealed.messages.every((m) => C.envelopeForm(m.text)!.form === 3)).toBe(true);
    const total = C.envelopeForm(sealed.key)!.body.length + sealed.messages.reduce((n, m) => n + C.envelopeForm(m.text)!.body.length, 0);
    expect(total).toBeGreaterThan(300000);
    expect(server.resealLog.some((x) => (x.where as { id: unknown }).id === `${LINK}:t`)).toBe(true);
  });

  it("stops before its next page or batch once stopped or once its keys are not the tab's, and ends on a page the server gave before", async () => {
    const { ring, code } = await upgradedWithTexts();
    server.resealPageSize = 2;                                      // pages of two, so there is a next page to stop before
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    let alive = true;
    const r = new K.Reseal(ring, await view(), SLOTS);
    await expect(r.run(rk, () => { alive = false; }, () => alive)).rejects.toThrow(K.RESEAL_STOPPED);
    expect([r.batches, server.resealLog.length]).toEqual([1, 2]);          // the first batch went, nothing after it
    const s = new K.Reseal(ring, await view(), SLOTS);
    s.stop();
    const gets = () => server.calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/ade/e2e/reseal")).length;
    const before = gets();
    await expect(s.run(rk)).rejects.toThrow(K.RESEAL_STOPPED);
    expect(gets()).toBe(before);
    // a server whose pages lead back to one it gave: the run ends there
    let n = 0;
    server.answer = (call) => (call.method === "GET" && call.path.startsWith("/api/ade/e2e/reseal") ? [200, { items: [], next: n++ % 2 ? "A" : "B" }] : null);
    await new K.Reseal(ring, await view(), SLOTS).run(rk);
    expect(gets() - before).toBe(3);
  });

  it("sends at most 200 texts in a batch", async () => {
    const { ring, code, slotA } = await upgradedWithTexts();
    const one = server.stored[0];
    server.stored = Array.from({ length: 250 }, (_, i) => ({ ...one, where: { table: "events", id: i + 1, field: "text" }, slot: slotA }));
    // the server serves at most 100 a page: a page of 250 is a server that serves more, which the page holds to its own
    // batches all the same
    server.answer = (call) => (call.method === "GET" && call.path === "/api/ade/e2e/reseal"
      ? [200, JSON.parse(JSON.stringify({ items: server.stored.map((x) => ({ where: x.where, value: x.value, slot: x.slot })), next: null }))] : null);
    const r = new K.Reseal(ring, await view(), SLOTS);
    await r.run(await K.codeKey(await view(), ACCOUNT, code));
    expect(posted("/api/ade/e2e/reseal").map((x) => (x.body!.items as unknown[]).length)).toEqual([200, 50]);
    expect([r.sealed, r.batches]).toEqual([250, 2]);
  });

  it("the server swaps nothing for a batch changed after its proof was made, or a value changed since it was read", async () => {
    const { ring, code } = await upgradedWithTexts();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    const real = server.fetch;
    let tamper = true;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      if (tamper && url === "/api/ade/e2e/reseal" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        body.items = body.items.slice(1);                           // a batch other than the one proved
        return real(url, { ...init, body: JSON.stringify(body) });
      }
      return real(url, init);
    });
    const gets = () => server.calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/ade/e2e/reseal")).length;
    await expect(new K.Reseal(ring, await view(), SLOTS).run(rk)).rejects.toThrow(PROOF_WRONG);
    expect(server.resealLog).toHaveLength(0);
    expect(gets()).toBe(1);                                        // a proof refused is not a page to read again
    tamper = false;
    const before = server.stored[0].value;
    server.stored[0].value = server.stored[1].value;              // changed between the read and the swap
    // a server that serves what was read before, each time: the page is read again, RESEAL_READS times in all, and
    // then the server's words are given
    let reads = 0;
    server.answer = (call) => {
      if (call.method !== "GET" || !call.path.startsWith("/api/ade/e2e/reseal")) return null;
      reads++;
      return [200, { items: [{ where: server.stored[0].where, value: before, slot: "proofpane-e2e/1|acct-1|l|event|step|||a" }], next: null }];
    };
    await expect(new K.Reseal(ring, await view(), SLOTS).run(rk)).rejects.toThrow(/changed since/);
    expect([reads, K.RESEAL_READS]).toEqual([3, 3]);
    expect(server.resealLog).toHaveLength(0);
  });

  it("reads a page again where something in it changed since it was read, seals what is stored now, and counts what it leaves once", async () => {
    const { ring, code, slotA } = await upgradedWithTexts();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    server.resealPageSize = 3;
    // between the first read and its batch, the first event is sealed again elsewhere (another tab): the batch is
    // refused whole (404), and the page read again no longer serves it
    const real = K.E2EApi.reseal;
    let first = true;
    vi.spyOn(K.E2EApi, "reseal").mockImplementation(async (body) => {
      if (first) {
        first = false;
        server.stored[0].value = await C.sealEnvelope2(bytesOf(server.contentKeys.at(-1)!.public_key), C.utf8('{"text":"one"}'), slotA);
      }
      return real(body);
    });
    const r = new K.Reseal(ring, await view(), SLOTS);
    const told: number[][] = [];
    await r.run(rk, (x) => told.push([x.sealed, x.skipped]));
    // the third event, which does not open here, was on both reads: counted once
    expect([r.sealed, r.skipped]).toEqual([2, 1]);
    expect(server.resealLog.map((x) => (x.where as { id: unknown }).id)).toEqual([2, `${LINK}:s`]);
    expect(posted("/api/ade/e2e/reseal")).toHaveLength(2);
    expect(told.at(-1)).toEqual([2, 1]);
  });

  it("reads a page again for the server's two refusals alone, each in its words and with its status: any other ends it at once", async () => {
    const { ring, code } = await upgradedWithTexts();
    const rk = await K.codeKey(await view(), ACCOUNT, code);
    const gets = () => server.calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/ade/e2e/reseal")).length;
    const at = (path: string, status: number, body: unknown) => (call: { method: string; path: string }): [number, unknown] | null =>
      (call.method === "POST" && call.path === path ? [status, body] : null);
    const others: [string, ReturnType<typeof at>][] = [
      ["the batch refused in other words (encryption off since)", at("/api/ade/e2e/reseal", 409, { detail: K.RESEAL_OFF })],
      ["its proof's challenge refused (no recovery code)", at("/api/ade/e2e/recovery-proof", 409, { detail: "This account has no recovery code: end-to-end encryption was never turned on." })],
      ["a proxy's 404, which carries no words of the app", at("/api/ade/e2e/reseal", 404, "<html>Not Found</html>")],
      ["RESEAL_GONE's words with 409", at("/api/ade/e2e/reseal", 409, { detail: K.RESEAL_GONE })],
      ["RESEAL_CHANGED's words with 404", at("/api/ade/e2e/reseal", 404, { detail: K.RESEAL_CHANGED })],
    ];
    for (const [what, answer] of others) {
      const before = gets();
      server.answer = answer;
      await expect(new K.Reseal(ring, await view(), SLOTS).run(rk), what).rejects.toBeInstanceOf(ApiError);
      expect([what, gets() - before]).toEqual([what, 1]);
    }
    expect(server.resealLog).toHaveLength(0);
    // each of the two in its words and with its status: read again, RESEAL_READS times in all, then its words
    for (const [status, detail] of [[404, K.RESEAL_GONE], [409, K.RESEAL_CHANGED]] as const) {
      const before = gets();
      server.answer = at("/api/ade/e2e/reseal", status, { detail });
      await expect(new K.Reseal(ring, await view(), SLOTS).run(rk)).rejects.toThrow(detail);
      expect([status, gets() - before]).toEqual([status, K.RESEAL_READS]);
    }
  });
});

describe("what a flow made is put away when it is not kept", () => {
  /** Each post-quantum key e2eCrypto's `name` makes from here on, as it is made. */
  function captured(name: "unwrapCredentialSeed" | "recoveryHybrid") {
    const keys: C.Hybrid[] = [];
    const real = C[name] as (...a: unknown[]) => Promise<C.Hybrid>;
    vi.spyOn(C, name).mockImplementation((async (...a: unknown[]) => {
      const k = await real(...a);
      keys.push(k);
      return k;
    }) as never);
    return keys;
  }
  const isPutAway = (k: C.Hybrid) => {
    try {
      C.hybridPublic(k);
      return false;
    } catch (e) {
      return e instanceof Error && e.message === C.PUT_AWAY;
    }
  };
  const keyOf = (p: K.Priv) => p.key as C.Hybrid;

  it("unlocking: the key a touch or the code opened, when the keys do not open from it", async () => {
    const { code } = await turnedOn();
    const keys = captured("unwrapCredentialSeed");
    const codeKeys = captured("recoveryHybrid");
    server.grants = [];                                      // nothing the master key opens from
    await expect(unlockAs("credAAAA")).rejects.toThrow(K.NO_GRANT);
    expect(keys.map(isPutAway)).toEqual([true]);
    await expect(K.unlockWithCode(await view(), ACCOUNT, code)).rejects.toThrow(K.NO_GRANT);
    expect(codeKeys.map(isPutAway)).toEqual([true]);
  });

  it("unlocking with the code: its key, when the anchor the server holds does not open with it", async () => {
    const { code } = await turnedOn();
    const codeKeys = captured("recoveryHybrid");
    server.recovery = { ...server.recovery!, anchor: C.b64u(C.random(60)) };
    await expect(K.unlockWithCode(await view(), ACCOUNT, code)).rejects.toThrow(K.ANCHOR_NOT_OPEN);
    expect(codeKeys.map(isPutAway)).toEqual([true]);
  });

  it("turning on: the code's recovery key, when the second touch gives another PRF output", async () => {
    server.addPasskey("credAAAA", "Security key");
    auth.add("credAAAA", { unstable: true });
    const keys = captured("recoveryHybrid");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await view());
    await t.firstTouch();
    expect(keys.map(isPutAway)).toEqual([false]);
    await expect(t.secondTouch()).rejects.toThrow(K.UNSTABLE);
    expect(keys.map(isPutAway)).toEqual([true]);
  });

  it("turning on: the key the second touch opened, at dispose; once sent it is the caller's, and stays", async () => {
    passkey("credAAAA");
    const keys = captured("unwrapCredentialSeed");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await view());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    t.dispose();
    expect(keys.map(isPutAway)).toEqual([true]);
    // sent, but the account not read after it: what would unlock it is the caller's no more, and is put away
    const failing = new K.TurnOn(ACCOUNT);
    await failing.prepare(await view());
    await failing.firstTouch();
    await failing.secondTouch();
    let sent = false;
    server.answer = (call) => {
      if (call.method === "POST" && call.path === "/api/ade/e2e/turn-on") sent = true;
      else if (sent && call.method === "GET") return [503, { detail: "Not now." }];
      return null;
    };
    await expect(failing.send(true)).rejects.toThrow("Not now.");
    expect(keys.map(isPutAway)).toEqual([true, true]);
    server.answer = null;
    server.pending = null;
    const again = new K.TurnOn(ACCOUNT);
    await again.prepare(await view());
    await again.firstTouch();
    await again.secondTouch();
    const { via } = await again.send(true);
    again.dispose();
    expect(isPutAway(keyOf((via as Extract<K.Via, { kind: "credential" }>).ck))).toBe(false);
  });

  it("adding a passkey: the key its second touch opened, at dispose", async () => {
    const { ring } = await turnedOn("credAAAA");
    passkey("credBBBB");
    const keys = captured("unwrapCredentialSeed");
    const v = await view();
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    auth.choose = prefer("credBBBB");
    await e.firstTouch();
    await e.secondTouch();
    expect(keys.map(isPutAway)).toEqual([false]);
    e.dispose();
    expect(keys.map(isPutAway)).toEqual([true]);
  });

  it("the upgrade keeping the code: a wrong code's bytes, zeroed once it is refused", async () => {
    const { ring, code } = await classicalAccount();
    const u = new K.Upgrade(ring, await view(), true);
    const read = vi.spyOn(C, "recoveryBytes");
    const other = C.recoveryText(C.random(C.CODE_BYTES));
    await expect(u.useCode(other)).rejects.toThrow(K.WRONG_CODE);
    const wrong = read.mock.results.at(-1)!.value as Uint8Array;
    expect(wrong.length).toBe(C.CODE_BYTES);
    expect(wrong.every((b) => b === 0)).toBe(true);
    // the right one is kept, unzeroed, to be used
    await u.useCode(code);
    const right = read.mock.results.at(-1)!.value as Uint8Array;
    expect(right.some((b) => b !== 0)).toBe(true);
  });

  it("renewing anchors: a code typed, when typed again and at dispose; never the code that unlocked the tab", async () => {
    const { ring, code } = await turnedOn("credAAAA");
    const codeKeys = captured("recoveryHybrid");
    const r = new K.RenewAnchors(ring, await view());
    await r.prepare();
    await r.renewCode(code);
    await r.renewCode(code);
    expect(codeKeys.map(isPutAway)).toEqual([true, false]);
    r.dispose();
    expect(codeKeys.map(isPutAway)).toEqual([true, true]);
    const withCode = await K.unlockWithCode(await view(), ACCOUNT, code);
    const own = new K.RenewAnchors(withCode, await view());
    await own.prepare();
    await own.renewCode();
    own.dispose();
    expect(isPutAway(keyOf((withCode.via as Extract<K.Via, { kind: "recovery" }>).rk))).toBe(false);
  });
});

describe("the change that waits on a post-quantum account, checked with this tab's keys", () => {
  const OPENS = { state: "opens", why: K.WAITING_OPENS };
  const BROKEN = { state: "broken", why: K.WAITING_BROKEN };
  const kept = () => server.pending!.body as Body;

  it("a rotation: its X-Wing content key opens under the master key held, as its statement states; a classical one is broken", async () => {
    const { ring } = await turnedOn("credAAAA");
    server.rotateEvery = 0;
    const r = new K.Rotate(ring, await view());
    await r.prepare();
    auth.choose = prefer("credAAAA");
    await r.signAndSend();
    expect(await K.checkWaiting(ring, await view())).toEqual(OPENS);
    kept().content_key.wrapped = C.b64u(C.random(C.WRAP_LEN));
    expect(await K.checkWaiting(ring, await view())).toEqual(BROKEN);
  });

  it("a revocation: the new master key opens by the grant to this tab's key, its chain, every key wrapped again in 60 bytes, and its roster", async () => {
    const { ring } = await turnedOn("credAAAA");
    await enrolled(ring, "credBBBB");
    const a = await unlockAs("credAAAA");
    const r = new K.Revoke(a, await view(), { remove: ["pk-credBBBB"] });
    await r.prepare();
    auth.choose = prefer("credAAAA");
    await r.signAndSend();
    expect(server.pending?.change).toBe("revoke");
    expect(await K.checkWaiting(a, await view())).toEqual(OPENS);
    const [keyId] = Object.keys(kept().content_keys);
    kept().content_keys[keyId] = C.b64u(C.random(C.WRAP_LEN));
    expect(await K.checkWaiting(a, await view())).toEqual(BROKEN);
  });

  it("a passkey added: its X-Wing key's grant is the master key's, its anchor the master key, and the roster names it", async () => {
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
    expect(await K.checkWaiting(ring, await view())).toEqual(OPENS);
    const body = JSON.parse(JSON.stringify(kept()));
    kept().credential.anchor = "0".repeat(32);
    expect(await K.checkWaiting(ring, await view())).toEqual(BROKEN);
    // a classical key for it, made to agree in all else (its grant, as the master key seals it, and the roster): not
    // the account's kind
    server.pending!.body = JSON.parse(JSON.stringify(body));
    const classical = C.b64u((await C.newPair()).pub);
    const mkId = server.roster!.mk_id;
    const recovery = server.grants.find((x) => x.to === "recovery" && x.mk_id === mkId)!.sealed;
    const bytes = await C.openGrantHybrid(await C.recoveryHybrid(C.recoveryBytes(code)!, ACCOUNT), recovery, ACCOUNT, "recovery", mkId);
    const master = await C.masterOf(bytes, ACCOUNT);
    const roster = await C.openRoster(master, kept().roster, ACCOUNT);
    kept().credential.public_key = classical;
    kept().grant = await C.sealGrant(C.unb64u(classical)!, bytes, ACCOUNT, "credBBBB", mkId);
    kept().roster = await C.sealRoster(master, { ...roster,
      credentials: roster.credentials.map((c) => (c.credential_id === "credBBBB" ? { ...c, public_key: classical } : c)) }, ACCOUNT);
    expect(await K.checkWaiting(ring, await view())).toEqual(BROKEN);
    server.pending!.body = body;
    expect(await K.checkWaiting(ring, await view())).toEqual(OPENS);
  });
});

describe("the browser's post-quantum floor", () => {
  it("refuses a view put back to classical, or with fewer statements, where this browser saw the account post-quantum", async () => {
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    expect(K.rolledBack(ACCOUNT, await view())).toBeNull();
    server.pq = false;
    expect(K.rolledBack(ACCOUNT, await view())).toBe(K.ROLLED_BACK_CLASSICAL);
    await expect(unlockAs("credAAAA", K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK_CLASSICAL);
    // a browser that never saw it trusts the server, as at every first look
    expect(K.rolledBack("acct-2", await view())).toBeNull();
    server.pq = true;
    const statements = server.statements;
    server.statements = [];
    expect(K.rolledBack(ACCOUNT, await view())).toBe(K.ROLLED_BACK);
    server.statements = statements;
    // the person trusts the server's keys (they started again, or restored it themselves): fewer statements are taken,
    // but what this browser saw post-quantum is kept, so a copy from before the upgrade is still refused
    await K.dropFloor(ACCOUNT);
    server.statements = [];
    expect(K.rolledBack(ACCOUNT, await view())).toBeNull();
    server.statements = statements;
    server.pq = false;
    expect(K.rolledBack(ACCOUNT, await view())).toBe(K.ROLLED_BACK_CLASSICAL);
  });

  it("refuses turning encryption off, and starting again, from a view put back, classical or with fewer statements, before any request", async () => {
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    /** `attempt` on each view older than what this browser saw, with the words it is refused in (classical where it saw
     *  the keys post-quantum; fewer statements than it saw), with and without ML-KEM on the server: put back first. */
    const putBack = async (attempt: (v: K.E2EView, why: string) => Promise<unknown>) => {
      const statements = server.statements;
      const ways = [[() => { server.pq = false; }, K.ROLLED_BACK_CLASSICAL], [() => { server.statements = []; }, K.ROLLED_BACK]] as const;
      for (const mlkem of [true, false]) {
        for (const [put, why] of ways) {
          server.mlkem = mlkem;
          put();
          const v = await view();
          const calls = server.calls.length;
          await attempt(v, why);
          expect(server.calls.length).toBe(calls);                       // no statement prepared: no nonce asked for
          server.pq = true;
          server.statements = statements;
          server.mlkem = true;
        }
      }
    };
    await putBack((v, why) => expect(new K.TurnOff(v, ACCOUNT).prepare()).rejects.toThrow(why));
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();                                                 // as served, it is turned off
    await off.signAndSend();
    server.settle();
    await putBack((v, why) => expect(new K.TurnOn(ACCOUNT, true).prepare(v)).rejects.toThrow(why));
    await new K.TurnOn(ACCOUNT, true).prepare(await view());             // and started again as served
  });

  it("does not refuse a first turn-on from a view put back: only starting again is refused there", async () => {
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();                                                     // turned off, its keys kept
    passkey("credBBBB");                                                 // one a first turn-on may enrol
    const statements = server.statements;
    for (const [put, why] of [[() => { server.pq = false; }, K.ROLLED_BACK_CLASSICAL], [() => { server.statements = []; }, K.ROLLED_BACK]] as const) {
      put();
      const v = await view();
      expect(K.rolledBack(ACCOUNT, v)).toBe(why);
      // a first turn-on whose read serves keys put back is made: the server takes the whole body only over no keys
      // (FROM_BEFORE), and the page's rule refuses only starting again
      await expect(new K.TurnOn(ACCOUNT).prepare(v)).resolves.toBeUndefined();
      await expect(new K.TurnOn(ACCOUNT, true).prepare(v)).rejects.toThrow(why);
      server.pq = true;
      server.statements = statements;
    }
  });

  it("calls a view with no keys older where this browser saw the account's keys, by whatever it kept of them, and still turns it on there", async () => {
    passkey("credAAAA");
    const v = await view();
    expect([K.hasKeys(v), v.pq, v.statements.length]).toEqual([false, false, 0]);
    // what this browser may keep of an account whose keys it saw: the master key it opened alone (all a page of an
    // older release keeps), the post-quantum mark alone (all Trust the server's keys keeps), the newest statement alone
    // (a record whose other parts do not read), a classical account's master key and statement, or all three
    const mk = "a".repeat(32);
    for (const kept of [{ floor: mk }, { pq: mk }, { seq: 2 }, { floor: mk, seq: 2 }, { floor: mk, pq: mk, seq: 2 }]) {
      localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify({ floor: null, next: null, pq: null, seq: 0, ...kept }));
      // in the words for keys put back: no key is served to call classical
      expect(K.rolledBack(ACCOUNT, v)).toBe(K.ROLLED_BACK);
      // a first turn-on is not refused: Encryption warns, and offers it below the warning
      await expect(new K.TurnOn(ACCOUNT).prepare(v)).resolves.toBeUndefined();
      // starting again from it is
      await expect(new K.TurnOn(ACCOUNT, true).prepare(v)).rejects.toThrow(K.ROLLED_BACK);
    }
  });

  it("calls the account put back to a copy from before encryption was turned on older: a tab holding its keys follows it no further", async () => {
    passkey("credAAAA");
    // the account's keys as a backup made before encryption was turned on holds them: none
    const before = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    Object.assign(server, before);
    const v = await view();
    expect([K.hasKeys(v), v.pq, v.statements.length]).toEqual([false, false, 0]);
    expect(K.rolledBack(ACCOUNT, v)).toBe(K.ROLLED_BACK);
    await expect(K.follow(ring, v, K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK);
    await expect(new K.TurnOn(ACCOUNT).prepare(v)).resolves.toBeUndefined();   // a first turn-on is not refused
  });

  it("calls a view older that keeps the account's statements but serves none of its keys (their rows deleted), by whatever this browser kept", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    const floor = JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!);
    const keys = JSON.parse(JSON.stringify({ credentials: server.credentials, grants: server.grants, recovery: server.recovery,
      roster: server.roster, contentKeys: server.contentKeys, chain: server.chain }));
    Object.assign(server, { credentials: [], grants: [], recovery: null, roster: null, contentKeys: [], chain: [] });
    const v = await view();
    // as many statements as this browser saw, and still post-quantum, so neither fewer statements nor a classical view
    // says it: that no key is served where it saw some does, by the master key it opened, the post-quantum mark or the
    // newest statement, each kept alone, or all of them
    expect([K.hasKeys(v), v.pq, v.statements.length, floor.seq]).toEqual([false, true, 1, 1]);
    for (const kept of [floor, { floor: floor.floor }, { pq: floor.pq }, { seq: floor.seq }]) {
      localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify({ floor: null, next: null, pq: null, seq: 0, ...kept }));
      expect(K.rolledBack(ACCOUNT, v)).toBe(K.ROLLED_BACK);
      await expect(new K.TurnOn(ACCOUNT).prepare(v)).resolves.toBeUndefined();
    }
    // the recovery key's row kept alone: no master key and no credential are no keys, as the pane counts them, so the
    // same, though nothing else served is older (the recovery key is post-quantum, the statements all there)
    localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify(floor));
    Object.assign(server, { recovery: keys.recovery });
    const codeKeyAlone = await view();
    expect([K.hasKeys(codeKeyAlone), !!codeKeyAlone.recovery, codeKeyAlone.pq, codeKeyAlone.statements.length]).toEqual([false, true, true, 1]);
    expect(K.rolledBack(ACCOUNT, codeKeyAlone)).toBe(K.ROLLED_BACK);
    await expect(new K.TurnOn(ACCOUNT).prepare(codeKeyAlone)).resolves.toBeUndefined();
    // keys served without a master key (its rows deleted), or a master key with no credential (a revocation may leave
    // the recovery code alone), are keys, as the pane counts them: judged as before, not by this; the code unlocks the
    // second
    localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify(floor));
    Object.assign(server, { ...keys, roster: null, grants: [] });
    const noMaster = await view();
    expect([K.hasKeys(noMaster), noMaster.mk_id, noMaster.credentials.length]).toEqual([true, null, 1]);
    expect(K.rolledBack(ACCOUNT, noMaster)).toBeNull();
    Object.assign(server, { ...keys, credentials: [] });
    const codeOnly = await view();
    expect([K.hasKeys(codeOnly), codeOnly.mk_id, codeOnly.credentials.length]).toEqual([true, ring.master.id, 0]);
    expect(K.rolledBack(ACCOUNT, codeOnly)).toBeNull();
    expect((await K.unlockWithCode(codeOnly, ACCOUNT, code, K.floorOf(ACCOUNT))).master.id).toBe(ring.master.id);
  });

  it("turns it on over no keys where this browser saw none of the account's: none at all, only keys it made itself that never took effect, or another account's", async () => {
    passkey("credAAAA");
    const turnsOn = async () => {
      const v = await view();
      expect([K.hasKeys(v), v.pending]).toEqual([false, null]);
      expect(K.rolledBack(ACCOUNT, v)).toBeNull();
      await new K.TurnOn(ACCOUNT).prepare(v);
    };
    await turnsOn();                                                     // a first look: the server is trusted
    // a first turn-on this browser made, cancelled before its hold ended: the master key it waited for, never seen
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await view());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    expect(JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!)).toEqual({ floor: null, next: expect.stringMatching(/^[0-9a-f]{32}$/), pq: null, seq: 0 });
    const w = await view();
    const c = new K.CancelChange(ACCOUNT, w, w.pending!);
    await c.prepare();
    await c.withPasskey();
    await turnsOn();
    // another account's keys, seen in this browser
    localStorage.clear();
    localStorage.setItem(K.floorKey("acct-2"), JSON.stringify({ floor: "a".repeat(32), next: null, pq: "a".repeat(32), seq: 2 }));
    await turnsOn();
  });

  it("still cancels a change that waits from a view put back, by a passkey and by the recovery code: cancelling changes no key", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    const waits = () => { server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} }; };
    waits();
    server.pq = false;
    const v = await view();
    expect(K.rolledBack(ACCOUNT, v)).toBe(K.ROLLED_BACK_CLASSICAL);
    expect(K.ROLLED_BACK_CLASSICAL).toContain("No key is opened from them and no change is made from them, though a change that waits may still be cancelled.");
    const c = new K.CancelChange(ACCOUNT, v, v.pending!);
    await c.prepare();
    auth.choose = prefer("credAAAA");
    await c.withPasskey();
    expect((await view()).pending).toBeNull();
    server.pq = true;
    waits();
    const statements = server.statements;
    server.statements = [];
    const w = await view();
    expect(K.rolledBack(ACCOUNT, w)).toBe(K.ROLLED_BACK);
    await new K.CancelChange(ACCOUNT, w, w.pending!).withCode({ code });
    expect((await view()).pending).toBeNull();
    server.statements = statements;
  });

  it("refuses at unlock a copy from before the upgrade put back after the person trusted the server's keys: no floor, but what it saw post-quantum kept", async () => {
    const { ring, code } = await classicalAccount();
    // the account's keys as the server held them before the upgrade: a copy someone who can write its database keeps
    const keys = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    const before = keys();
    await upgraded(ring, ["credAAAA"], code);                              // keeping the code
    server.settle();
    const pq = await unlockAs("credAAAA");
    expect(pq.pq).toBe(true);
    await K.raiseFloor(ACCOUNT, pq);
    // keys that do not lead back to the newest it opened, which the person trusts (Trust the server's keys)
    await K.dropFloor(ACCOUNT);
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    Object.assign(server, before);
    const v = await view();
    expect(v.pq).toBe(false);
    // with no master key to lead back to, the copy is refused all the same, by a passkey and by the code, before any key
    // is opened from it (no decrypt, no unwrap), the PRF output given zeroed; and to a tab opening a ring with no floor
    let opened = 0;
    const subtle = crypto.subtle;
    const [decrypt, unwrap] = [subtle.decrypt.bind(subtle), subtle.unwrapKey.bind(subtle)];
    vi.spyOn(subtle, "decrypt").mockImplementation((...a: Parameters<SubtleCrypto["decrypt"]>) => { opened++; return decrypt(...a); });
    vi.spyOn(subtle, "unwrapKey").mockImplementation((...a: Parameters<SubtleCrypto["unwrapKey"]>) => { opened++; return unwrap(...a); });
    await expect(unlockAs("credAAAA", K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK_CLASSICAL);
    await expect(K.unlockWithCode(v, ACCOUNT, code, K.floorOf(ACCOUNT))).rejects.toThrow(K.ROLLED_BACK_CLASSICAL);
    const prf = await auth.prf("credAAAA", C.unb64u(v.credentials[0].salt)!);
    await expect(K.unlockWithPrf(v, ACCOUNT, "credAAAA", prf, [])).rejects.toThrow(K.ROLLED_BACK_CLASSICAL);
    expect([opened, prf.every((x) => x === 0)]).toEqual([0, true]);
    await expect(K.openRing(v, ACCOUNT, await K.viaFromCode(v, ACCOUNT, code), [])).rejects.toThrow(K.ROLLED_BACK_CLASSICAL);
    // a browser that never saw the account trusts the server, as at every first look: the copy opens there
    localStorage.clear();
    expect((await unlockAs("credAAAA")).pq).toBe(false);
  });

  it("judges a view classical from its keys, whatever its pq says", async () => {
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    const v = await view();
    const classical = C.b64u((await C.newPair()).pub);
    expect(K.rolledBack(ACCOUNT, v)).toBeNull();
    expect(K.rolledBack(ACCOUNT, { ...v, credentials: v.credentials.map((c) => ({ ...c, public_key: classical })) })).toBe(K.ROLLED_BACK_CLASSICAL);
    expect(K.rolledBack(ACCOUNT, { ...v, recovery: { ...v.recovery!, public_key: classical } })).toBe(K.ROLLED_BACK_CLASSICAL);
    // its newest key stated classical
    const top = server.statements.at(-1)!;
    server.statements.push({ ...top, seq: top.seq + 1, payload: JSON.stringify({ ...JSON.parse(String(top.payload)), key: classical }) });
    expect(K.rolledBack(ACCOUNT, await view())).toBe(K.ROLLED_BACK_CLASSICAL);
  });
});

// Every way the server changes an account's keys after they exist keeps a master key: turning it off stores a
// statement alone (e2e_keys._apply_off); starting again deletes the keys and stores the new ones in one transaction
// (_forget, then the turn-on's); a revocation, or a turn-on that makes a new master key, replaces the master key
// (_new_master_key); removing a passkey from the account, or turning high security on, deletes credentials and their
// grants but not the roster that names the master key (credentials_dropped). There is no account reset. So none leaves
// the view with no keys that the browser's floor calls older: each is checked here in a browser that saw the keys.
describe("what the server does to keys that exist leaves keys, which the browser that saw them does not call older", () => {
  /** The account's keys as served now: still keys, as the pane counts them, and not older than what this browser kept
   *  of them. */
  const stillKeys = async () => {
    const v = await view();
    expect(K.hasKeys(v)).toBe(true);
    expect(K.rolledBack(ACCOUNT, v)).toBeNull();
    return v;
  };
  /** Encryption turned off as served now, signed with a passkey or, given the code, with its proof beside it. */
  const turnedOff = async (code?: string) => {
    const off = new K.TurnOff(await view(), ACCOUNT);
    await off.prepare();
    if (code) await off.signAndSendWithCode(await K.codeKey(await view(), ACCOUNT, code));
    else await off.signAndSend();
    server.settle();
  };
  /** A change to the account confirmed with `by`'s passkey, as Account confirms one (passkeys.CHANGE), and sent. */
  const accountChange = async (by: string, change: string, path: string, method: string, body: Record<string, unknown> = {}) => {
    auth.choose = prefer(by);
    const confirmation = await confirmWithPasskey(false, { change });
    expect((await server.fetch(path, { method, body: JSON.stringify({ ...body, confirmation }) })).status).toBe(200);
  };

  it("turning it off, with a passkey or with the recovery code's proof: the keys stay, and neither turning it on again nor starting again is refused", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    await turnedOff();
    const off = await stillKeys();
    expect(off.on).toBe(false);
    const held = await K.follow(ring, off, K.floorOf(ACCOUNT));
    await K.raiseFloor(ACCOUNT, held);
    await new K.TurnOn(ACCOUNT, true).prepare(await stillKeys());       // starting again
    const again = new K.TurnOnAgain(held, await stillKeys());          // turning it on again, from the keys unlocked
    await again.prepare();
    await again.signAndSend(false);
    server.settle();
    expect((await stillKeys()).on).toBe(true);
    await turnedOff(code);
    expect((await stillKeys()).on).toBe(false);
    await new K.TurnOn(ACCOUNT, true).prepare(await stillKeys());
  });

  it("starting again: the keys are replaced in one change, and the new ones are not called older", async () => {
    const { ring } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    await turnedOff();
    const t = new K.TurnOn(ACCOUNT, true);
    await t.prepare(await stillKeys());
    auth.choose = prefer("credAAAA");
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    expect((await stillKeys()).pending?.change).toBe("turn-on");       // while it waits, the keys from before
    server.settle();
    const v = await stillKeys();
    expect(v.mk_id).not.toBe(ring.master.id);
    expect((await unlockAs("credAAAA", K.floorOf(ACCOUNT))).master.id).toBe(v.mk_id);
  });

  it("a revocation that removes a passkey and makes a new recovery code: the master key is replaced, never removed", async () => {
    const { ring } = await turnedOn();
    await enrolled(ring, "credBBBB");
    const held = await K.follow(ring, await view());
    await K.raiseFloor(ACCOUNT, held);
    const r = new K.Revoke(held, await view(), { remove: ["pk-credBBBB"], newCode: C.random(C.CODE_BYTES) });
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const v = await stillKeys();
    expect(v.mk_id).not.toBe(held.master.id);
    expect((await K.follow(held, v, K.floorOf(ACCOUNT))).master.id).toBe(v.mk_id);
  });

  it("a revocation of the last passkey, with the recovery code's proof: no credential is served, the master key is, and the code unlocks it", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    const r = new K.Revoke(ring, await view(), { remove: ["pk-credAAAA"], onlyCode: true });
    await r.prepare();
    await r.signAndSendWithCode(await K.codeKey(await view(), ACCOUNT, code));
    server.settle();
    const v = await stillKeys();
    expect([v.credentials.length, v.mk_id === null]).toEqual([0, false]);
    expect((await K.unlockWithCode(v, ACCOUNT, code, K.floorOf(ACCOUNT))).master.id).toBe(v.mk_id);
  });

  it("the last passkey that unlocks it removed from the account while it is off: its credential goes, the master key stays, and starting again is not refused", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    passkey("credBBBB");                                                // a passkey of the account that does not unlock it
    await turnedOff();
    await accountChange("credBBBB", "passkey-remove:pk-credAAAA", "/api/account/passkeys/pk-credAAAA", "DELETE");
    const v = await stillKeys();
    expect([v.credentials.length, v.mk_id, v.rekey_due]).toEqual([0, ring.master.id, true]);
    expect((await K.unlockWithCode(v, ACCOUNT, code, K.floorOf(ACCOUNT))).master.id).toBe(ring.master.id);
    await new K.TurnOn(ACCOUNT, true).prepare(v);
  });

  it("high security turned on while it is off, with no hardware key that unlocks it: every credential goes, the master key stays, and starting again is not refused", async () => {
    const { ring, code } = await turnedOn();
    await K.raiseFloor(ACCOUNT, ring);
    passkey("credHHHH", "hardware");
    await turnedOff();
    await accountChange("credHHHH", "high-security-on", "/api/account/high-security", "PUT", { on: true });
    const v = await stillKeys();
    expect([v.credentials.length, v.mk_id, v.high_security]).toEqual([0, ring.master.id, true]);
    expect((await K.unlockWithCode(v, ACCOUNT, code, K.floorOf(ACCOUNT))).master.id).toBe(ring.master.id);
    await new K.TurnOn(ACCOUNT, true).prepare(v);
  });
});

describe("opening texts by their form", () => {
  it("form 2 opens under a post-quantum content key, and a form-1 text under it is refused", async () => {
    const { ring } = await turnedOn();
    const [, held] = [...ring.keys.entries()][0];
    const slot = "s";
    const two = await C.sealEnvelope2(held.EK, C.utf8('{"a":1}'), slot);
    expect(await K.openWith(ring, two, slot)).toEqual({ ok: true, value: { a: 1 } });
    // a form-1 text that names this key's id: refused, never opened with the X25519 half
    const one = C.envelopeForm(await C.sealEnvelope((await C.newPair()).pub, C.utf8('{"a":1}'), slot))!.body;
    one.set(C.envelopeForm(two)!.body.subarray(1, 17), 1);
    expect(await K.openWith(ring, "e2e1." + C.b64u(one), slot)).toEqual({ ok: false, why: K.UNREADABLE });
    // a form-3 message is opened only through its conversation's key
    const tk = await C.talkKeyOf(C.random(32));
    expect(await K.openWith(ring, await C.sealTalkMessage(tk, C.utf8('{"a":1}'), slot), slot)).toEqual({ ok: false, why: K.NOT_ITS_FORM });
  });

  it("opens a conversation's messages through its key, once, and refuses another key_id or a form-2 message", async () => {
    const { ring } = await turnedOn();
    const [, held] = [...ring.keys.entries()][0];
    const tkBytes = C.random(32);
    const tk = await C.talkKeyOf(tkBytes);
    const keySlot = talkKeySlot(ACCOUNT, "l", "s", 2, 0);
    const key = await C.sealTalkKey(held.EK, tkBytes, keySlot);
    const slot0 = talkSlot(ACCOUNT, "l", "s", 0, 2, 0, "user", 1, null);
    const message = await C.sealTalkMessage(tk, C.utf8('{"text":"hi"}'), slot0);
    const talk = { key, key_id: tk.id };
    const decapsulate = vi.spyOn(crypto.subtle, "deriveBits");
    expect(await K.openTalkWith(ring, talk, keySlot, message, slot0)).toEqual({ ok: true, value: { text: "hi" } });
    const once = decapsulate.mock.calls.length;
    expect(await K.openTalkWith(ring, talk, keySlot, message, slot0)).toEqual({ ok: true, value: { text: "hi" } });
    expect(decapsulate.mock.calls.length).toBe(once);              // the key is opened once
    // another key_id named beside the key, another conversation's key slot, a form-2 message
    expect(await K.openTalkWith(ring, { key, key_id: "0".repeat(32) }, keySlot, message, slot0)).toEqual({ ok: false, why: K.UNREADABLE });
    const otherSlot = talkKeySlot(ACCOUNT, "l", "s", 3, 0);
    expect((await K.openTalkWith(ring, talk, otherSlot, message, slot0)).ok).toBe(false);
    expect(await K.openTalkWith(ring, talk, keySlot, await C.sealEnvelope2(held.EK, C.utf8('{"text":"hi"}'), slot0), slot0))
      .toEqual({ ok: false, why: K.NOT_ITS_FORM });
    // a message moved to another place does not open
    expect((await K.openTalkWith(ring, talk, keySlot, message, talkSlot(ACCOUNT, "l", "s", 1, 2, 0, "user", 1, null))).ok).toBe(false);
  });

  it("opens a conversation by what it holds: form 3 beside its key, form 1 alone without one, never a mix or form 2", async () => {
    const { ring } = await turnedOn();
    const [, held] = [...ring.keys.entries()][0];
    const tkBytes = C.random(32);
    const tk = await C.talkKeyOf(tkBytes);
    const keySlot = talkKeySlot(ACCOUNT, "l", "s", 1, 0);
    const slot = talkSlot(ACCOUNT, "l", "s", 0, 1, 0, "user", 1, null);
    const ref = { key: await C.sealTalkKey(held.EK, tkBytes, keySlot), key_id: tk.id, keySlot };
    const three = await C.sealTalkMessage(tk, C.utf8('{"text":"hi"}'), slot);
    const two = await C.sealEnvelope2(held.EK, C.utf8('{"text":"hi"}'), slot);
    expect(await K.openTalkText(ring, ref, three, slot)).toEqual({ ok: true, value: { text: "hi" } });
    expect((await K.openTalkText(ring, ref, two, slot)).ok).toBe(false);                     // beside a key: form 3 alone
    const none = { key: null, key_id: null, keySlot };
    expect((await K.openTalkText(ring, none, three, slot)).ok).toBe(false);                  // form 3 needs its key
    expect((await K.openTalkText(ring, none, two, slot)).ok).toBe(false);                    // never form 2
    expect((await K.openTalkText(ring, { key: null, key_id: tk.id, keySlot }, three, slot)).ok).toBe(false);
    // a classical ring opens a conversation sealed message by message in form 1, and nothing else
    const classical = (await C.newPair());
    const one = await C.sealEnvelope(classical.pub, C.utf8('{"text":"hi"}'), slot);
    const keyId = await C.keyIdOf(classical.pub);
    const fake = { ...ring, keys: new Map([[keyId, { ek: { pq: false as const, key: classical.priv }, EK: classical.pub }]]) };
    expect(await K.openTalkText(fake, none, one, slot)).toEqual({ ok: true, value: { text: "hi" } });
    expect((await K.openTalkText(fake, ref, one, slot)).ok).toBe(false);                     // form 1 beside a key
    expect((await K.openTalkText(fake, { key: null, key_id: tk.id, keySlot }, one, slot)).ok).toBe(false);   // half a key
  });
});
