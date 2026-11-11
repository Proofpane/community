// A passkey unlocks the account's keys by each path it is reached by,
// where one path hashes the PRF input once more than another (a phone's passkey from another device's browser over its
// QR code, and in the phone's own browser), and where a path gives the first output alone; and a check of what a
// passkey gives on this device, which sends nothing.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { stateOf, useE2EStore } from "./e2e";
import { CLASSICAL, FakeAuthenticator, FakeServer, webauthnPrfHash } from "./e2eTestkit";
import { canonicalJson, pageChallenge, stepChallenge } from "./passkey";
import { useStore } from "./store";
import { EncryptionPane } from "./components/Encryption";

const ACCOUNT = "acct-1";
let server: FakeServer;
let auth: FakeAuthenticator;
const owner = { id: ACCOUNT, email: "a@example.org", can_sign_in_again: true };

beforeEach(() => {
  server = new FakeServer();
  auth = new FakeAuthenticator();
  vi.stubGlobal("fetch", server.fetch);
  auth.install();
  useStore.setState({ user: owner });
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  useE2EStore.setState(useE2EStore.getInitialState());
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
});

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const fromHex = (h: string) => new Uint8Array((h.match(/../g) ?? []).map((x) => parseInt(x, 16)));
const zeroed = (o: ArrayBuffer) => new Uint8Array(o).every((b) => b === 0);

/** The keys' kinds a turn-on makes: classical (an older release's), and post-quantum, which the page makes now. */
const KINDS = [["classical", CLASSICAL], ["post-quantum", undefined]] as const;

/** Encryption turned on with `id`'s passkey, its hold ended, on the path the authenticator is set to now. */
async function turnedOn(id = "credAAAA", name = "iPhone", kind: typeof CLASSICAL | undefined = CLASSICAL) {
  server.addPasskey(id, name);
  auth.add(id);
  const t = new K.TurnOn(ACCOUNT, false, kind);
  t.codeText();
  await t.prepare(await K.E2EApi.view());
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
}

/** The keys read as a tab reads them before its Unlock is pressed. */
async function loaded() {
  await useE2EStore.getState().load(ACCOUNT);
  return useE2EStore.getState().view!;
}

/** The passkey check, as the keys module offers it: undefined where the module has none. */
type Check = { credentialId: string; name: string; offered: number; attachment: string | null; verified: boolean; a: string; b: string | null; lengths: number[] };
const passkeyCheck = () => (K as unknown as { passkeyCheck?: (view: K.E2EView) => Promise<Check> }).passkeyCheck;
const checkConstants = () => K as unknown as { CHECK_SALT_HEX?: string; CHECK_SALT_HASHED_HEX?: string };

describe.each(KINDS)("unlocking by either path, %s keys", (_, kind) => {
  it("opens, in the phone's own browser, the record made from another device's browser over the phone's QR code, where that path hashes the PRF input once more, and zeroes every output", async () => {
    auth.hashesOnceMore = true;                      // turned on from the Mac's browser, the phone's passkey over its QR code
    await turnedOn("credAAAA", "iPhone", kind);
    auth.hashesOnceMore = false;                     // the phone's own browser
    await loaded();
    const before = auth.outputs.length;
    await expect(useE2EStore.getState().unlock()).resolves.toBeUndefined();
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");
    const given = auth.outputs.slice(before);
    expect(given.length).toBe(2);
    expect(given.every(zeroed)).toBe(true);
  });

  it("keeps opening the record on the path that made it (the Mac's browser over the phone's QR code)", async () => {
    auth.hashesOnceMore = true;
    await turnedOn("credAAAA", "iPhone", kind);
    await loaded();
    await useE2EStore.getState().unlock();
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");
  });

  it("opens the record where the path gives its first output alone, a second asked for", async () => {
    await turnedOn("credAAAA", "iPhone", kind);
    auth.firstOnly = true;                           // a browser or authenticator that evaluates one salt
    await loaded();
    await expect(useE2EStore.getState().unlock()).resolves.toBeUndefined();
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");
  });

  it("says OWN_RECORD where neither output opens the record, and zeroes both", async () => {
    await turnedOn("credAAAA", "iPhone", kind);
    auth.creds.get("credAAAA")!.secret = C.random(32);  // the passkey gives another key than the one it was added with
    await loaded();
    const before = auth.outputs.length;
    await expect(useE2EStore.getState().unlock()).rejects.toThrow(K.OWN_RECORD);
    const given = auth.outputs.slice(before);
    expect(given.length).toBe(2);
    expect(given.every(zeroed)).toBe(true);
    expect(stateOf(useE2EStore.getState())).not.toBe("unlocked");
  });
});

describe("unlocking by either path", () => {
  it("opens it by the keys module's one-touch unlock too", async () => {
    auth.hashesOnceMore = true;
    await turnedOn();
    auth.hashesOnceMore = false;
    const view = await K.E2EApi.view();
    const ring = await K.unlockWithTouch(view, ACCOUNT).catch((e: unknown) => (e as Error).message);
    expect(typeof ring === "string" ? ring : "opened").toBe("opened");
  });

  it("opens it by the keys module's one-touch unlock where the path gives its first output alone", async () => {
    await turnedOn();
    auth.firstOnly = true;
    const view = await K.E2EApi.view();
    const ring = await K.unlockWithTouch(view, ACCOUNT).catch((e: unknown) => (e as Error).message);
    expect(typeof ring === "string" ? ring : "opened").toBe("opened");
  });

  it("still refuses, where the path gives the first output alone, a touch whose second output is needed, as renewing a record's salt needs it", async () => {
    await turnedOn();
    auth.firstOnly = true;
    const ask = { challenge: pageChallenge(), allow: [{ credentialId: "credAAAA", salt: C.random(32), second: C.random(32) }] };
    await expect(K.prfTouch(ask)).rejects.toThrow(K.NO_PRF);
    const t = await K.prfTouch({ ...ask, secondOptional: true });
    expect([t.second, t.prf.length]).toEqual([null, 32]);
    C.zero(t.prf);
  });

  it("asks, in the unlock's one touch, each enrolled credential for its salt and for WebAuthn's hash of that salt, user verification required", async () => {
    await turnedOn();
    const view = await loaded();
    await useE2EStore.getState().unlock();
    const c = auth.ceremonies.at(-1)!;
    const salt = view.credentials[0].salt;
    expect(c.salts).toEqual({ credAAAA: salt });
    expect(c.seconds).toEqual({ credAAAA: C.b64u(await webauthnPrfHash(C.unb64u(salt)!)) });
    expect(c.userVerification).toBe("required");
  });

  it("zeroes both of the touch's outputs when the first opens the record", async () => {
    await turnedOn();
    await loaded();
    const before = auth.outputs.length;
    await useE2EStore.getState().unlock();
    const given = auth.outputs.slice(before);
    expect(given.length).toBe(2);
    expect(given.every(zeroed)).toBe(true);
  });

  it("says NOT_LEAD_BACK, not OWN_RECORD, where the first output opens a record whose key the server does not list", async () => {
    await turnedOn();
    server.credentials[0].public_key = C.b64u(C.random(32));   // a record put in for a key the server does not list
    await loaded();
    const before = auth.outputs.length;
    await expect(useE2EStore.getState().unlock()).rejects.toThrow(K.NOT_LEAD_BACK);
    expect(auth.outputs.slice(before).every(zeroed)).toBe(true);
  });

  it("zeroes the second output where the first is refused for another reason than the record", async () => {
    await turnedOn();
    const view = await K.E2EApi.view();
    const prf = C.random(32), alt = C.random(32);
    await expect(K.viaFromPrf(view, ACCOUNT, "credZZZZ", prf, alt)).rejects.toThrow("This passkey is not one that unlocks encryption.");
    expect([prf.every((b) => b === 0), alt.every((b) => b === 0)]).toEqual([true, true]);
  });

  it("zeroes the second output where the keys are older than this browser saw, refused before any is opened", async () => {
    await turnedOn();
    await loaded();
    await useE2EStore.getState().unlock();                     // this browser's floor rises to these keys
    const view = await K.E2EApi.view();
    const older = { ...view, statements: [] } as K.E2EView;
    expect(K.rolledBack(ACCOUNT, older)).toBeTruthy();
    const prf = C.random(32), alt = C.random(32);
    await expect(K.unlockWithPrf(older, ACCOUNT, view.credentials[0].credential_id, prf, [], alt)).rejects.toThrow();
    expect([prf.every((b) => b === 0), alt.every((b) => b === 0)]).toEqual([true, true]);
  });

  it("has each salt's hash ready as soon as the keys are read, before any touch", async () => {
    await turnedOn();
    const view = await K.E2EApi.view();
    const allow = K.unlockAllowed(view);
    expect(allow.map((a) => a.second && C.b64u(a.second))).toEqual([C.b64u(await webauthnPrfHash(C.unb64u(view.credentials[0].salt)!))]);
  });

  it("offers the hash of the salt the keys hold now, not of an earlier salt of the same credential", async () => {
    await turnedOn();
    await K.E2EApi.view();
    const renewed = C.random(32);
    server.credentials[0].salt = C.b64u(renewed);             // the credential's salt renewed elsewhere since
    const view = await K.E2EApi.view();
    expect(K.unlockAllowed(view).map((a) => a.second && C.b64u(a.second))).toEqual([C.b64u(await webauthnPrfHash(renewed))]);
  });

  it("reads the keys even where a salt cannot be hashed, offering no second output for it", async () => {
    await turnedOn();
    server.credentials[0].salt = C.b64u(C.random(32));        // a salt this page has not hashed yet
    const digest = vi.spyOn(globalThis.crypto.subtle, "digest").mockRejectedValue(new Error("no hashing here"));
    const view = await K.E2EApi.view().catch((e: unknown) => (e as Error).message);
    digest.mockRestore();
    expect(typeof view === "string" ? view : "read").toBe("read");
    expect(K.unlockAllowed(view as K.E2EView).map((a) => a.second ?? null)).toEqual([null]);
  });

  it("keeps no salt's hash once the store is reset, for another account or none", async () => {
    await turnedOn();
    const view = await K.E2EApi.view();
    expect(K.unlockAllowed(view).map((a) => a.second === undefined)).toEqual([false]);
    useE2EStore.getState().reset(null);
    expect(K.unlockAllowed(view).map((a) => a.second ?? null)).toEqual([null]);
  });
});

describe("one press's one touch, with the store", () => {
  it("signs a step over its own challenge and opens the keys from the same touch, on the phone's own path, by unlockFrom", async () => {
    auth.hashesOnceMore = true;                      // the record made over the phone's QR code
    await turnedOn();
    auth.hashesOnceMore = false;                     // the phone's own browser
    const view = await loaded();
    const payload = { v: 2, purpose: "answer", text: "Approve", ref: 7 };
    const challenge = await stepChallenge(payload);
    const { credential, touch } = await K.signAndTouch({ challenge, rpId: "pp.example", allow: [] }, view);
    expect(credential.id).toBe("credAAAA");
    const c = auth.ceremonies.at(-1)!;
    expect(C.b64u(c.challenge)).toBe(C.b64u(await C.sha256(C.concat(C.utf8("proofpane-step/1"), new Uint8Array([0]), C.utf8(canonicalJson(payload))))));
    expect(Object.keys(c.salts)).toEqual(["credAAAA"]);
    await useE2EStore.getState().unlockFrom(touch!);
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");
  });

  it("refuses the touch where no passkey that unlocks the keys may sign the step", async () => {
    await turnedOn();
    const view = await loaded();
    const challenge = await stepChallenge({ v: 2, purpose: "answer" });
    await expect(K.signAndTouch({ challenge, rpId: "pp.example", allow: ["credHW"] }, view)).rejects.toThrow(/No passkey that unlocks encryption may sign this here/);
    expect(auth.ceremonies).toHaveLength(2);           // turning on's two touches, and none here
  });
});

describe("the passkey check", () => {
  it("checks with an input no record is made with, the same on every device, and WebAuthn's hash of that input", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    const k = checkConstants();
    expect(k.CHECK_SALT_HEX).toBe(hex(await C.sha256(C.utf8("proofpane-e2e/1 passkey check"))));
    expect(k.CHECK_SALT_HASHED_HEX).toBe(hex(await webauthnPrfHash(fromHex(k.CHECK_SALT_HEX!))));
  });

  it("asks one touch of the enrolled credentials for the check's input and its hash, user verification required, and sends the server nothing", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    await turnedOn();
    const view = await loaded();
    const calls = server.calls.length;
    await passkeyCheck()!(view);
    const c = auth.ceremonies.at(-1)!;
    const k = checkConstants();
    expect(c.allow).toEqual(["credAAAA"]);
    expect(c.salts).toEqual({ credAAAA: C.b64u(fromHex(k.CHECK_SALT_HEX!)) });
    expect(c.seconds).toEqual({ credAAAA: C.b64u(fromHex(k.CHECK_SALT_HASHED_HEX!)) });
    expect(c.userVerification).toBe("required");
    expect(server.calls.length).toBe(calls);
  });

  it("shows each output as the first six bytes of its sha256, in hex, with the passkey's name, and zeroes the outputs", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    await turnedOn();
    const view = await loaded();
    const before = auth.outputs.length;
    const r = await passkeyCheck()!(view);
    const k = checkConstants();
    const want = async (salt: string) => hex((await C.sha256(await auth.prf("credAAAA", fromHex(salt)))).slice(0, 6));
    expect(r.a).toBe(await want(k.CHECK_SALT_HEX!));
    expect(r.b).toBe(await want(k.CHECK_SALT_HASHED_HEX!));
    expect(r.lengths).toEqual([32, 32]);
    expect(r.name).toBe("iPhone");
    expect(r.offered).toBe(1);
    const given = auth.outputs.slice(before);
    expect(given.length).toBe(2);
    expect(given.every(zeroed)).toBe(true);
  });

  it("gives, where the path hashes the input once more, as A what the other path gives as B", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    await turnedOn();
    const view = await loaded();
    const own = await passkeyCheck()!(view);
    auth.hashesOnceMore = true;
    const overQr = await passkeyCheck()!(view);
    expect(overQr.a).toBe(own.b);
    expect(overQr.a).not.toBe(own.a);
  });

  it("says which authenticator answered and whether it verified the person", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    await turnedOn();
    const view = await loaded();
    auth.attachment = "platform";
    auth.flags = 0x05;
    const here = await passkeyCheck()!(view);
    expect([here.attachment, here.verified]).toEqual(["platform", true]);
    auth.attachment = null;
    auth.flags = 0x01;
    const said = await passkeyCheck()!(view);
    expect([said.attachment, said.verified]).toEqual([null, false]);
  });

  it("says B is not given where the path gives its first output alone, A as before", async () => {
    expect(typeof passkeyCheck()).toBe("function");
    await turnedOn();
    const view = await loaded();
    const both = await passkeyCheck()!(view);
    auth.firstOnly = true;
    const r = await passkeyCheck()!(view).catch((e: unknown) => (e as Error).message);
    expect(typeof r === "string" ? r : [r.a, r.b, r.lengths]).toEqual([both.a, null, [32]]);
  });

  it("is offered beside Unlock while the keys are locked, and shows what it found", async () => {
    await turnedOn();
    auth.attachment = "platform";
    auth.flags = 0x05;
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Check a passkey on this device" }));
    const shown = await screen.findByRole("status", { name: "Passkey check" });
    expect(shown.textContent).toMatch(/iPhone/);
    expect(shown.textContent).toMatch(/\bA [0-9a-f]{12} \(for the test input\)/);
    expect(shown.textContent).toMatch(/\bB [0-9a-f]{12} \(for its hash\)/);
    expect(shown.textContent).toMatch(/this device's own authenticator/);
    expect(shown.textContent).toMatch(/, which verified you:/);
  });

  it("starts the check's passkey prompt on the click itself, nothing awaited first", async () => {
    await turnedOn();
    const get = vi.spyOn(navigator.credentials, "get");
    render(<EncryptionPane />);
    const button = await screen.findByRole("button", { name: "Check a passkey on this device" });
    get.mockClear();
    fireEvent.click(button);
    expect(get).toHaveBeenCalledTimes(1);                                              // the touch first, nothing awaited
    expect(await screen.findByRole("status", { name: "Passkey check" })).toBeTruthy();
  });
});
