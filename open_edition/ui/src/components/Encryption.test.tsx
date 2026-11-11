import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "../e2eCrypto";
import * as K from "../e2eKeys";
import { stateOf, useE2EStore } from "../e2e";
import { FakeAuthenticator, FakeServer } from "../e2eTestkit";
import { confirmWithPasskey } from "../passkey";
import { useStore } from "../store";
import { toldWords } from "./Channel";
import { EncryptionPane, holdWords, inPlaceWords, lengthWords, linkWords, noticeWords, OFF_HIGH_WORDS, OFF_KEYS_WORDS, pinsWords, RESTORED_TURN_ON, RevokeFromAccount, waitWords } from "./Encryption";
import { CLASSICAL } from "../e2eTestkit";

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

type Body = Record<string, any>;           // eslint-disable-line @typescript-eslint/no-explicit-any
const bodyOf = (path: string) => server.calls.filter((c) => c.method === "POST" && c.path === path).at(-1)?.body as Body | undefined;

function passkey(id: string, name: string, tier: "device" | "hardware" = "device") {
  server.addPasskey(id, name, tier);
  auth.add(id);
}

/** Encryption turned on with `id`'s passkey, straight through the keys module, its hold ended: the recovery code as it
 *  was shown. */
async function turnedOn(id = "credAAAA", name = "Laptop") {
  passkey(id, name);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await K.E2EApi.view());
  auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  return code;
}

/** Encryption turned on with `id`'s passkey, straight through the keys module, its hold not ended (with `again`, started
 *  again): what unlocks the keys it made. */
async function turnOnWith(id: string, again = false) {
  const t = new K.TurnOn(ACCOUNT, again, CLASSICAL);
  await t.prepare(await K.E2EApi.view());
  auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
  await t.firstTouch();
  await t.secondTouch();
  return (await t.send(true)).via;
}

/** `id`'s passkey added to encryption: two touches of it, then the confirming touch of a passkey that unlocks the keys
 *  already (the first offered), its hold ended. */
async function enrolled(id: string, name: string, tier: "device" | "hardware" = "device") {
  passkey(id, name, tier);
  const v = await K.E2EApi.view();
  const ring = await K.unlockWithTouch(v, ACCOUNT);
  const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
  await e.prepare();
  auth.choose = () => id;
  await e.firstTouch();
  await e.secondTouch();
  auth.choose = (allowed) => allowed[0];
  await e.confirmAndSend();
  server.settle();
}

async function unlock() {
  await act(async () => {
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
  });
}

async function turnedOff() {
  const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
  await off.prepare();
  await off.signAndSend();
  server.settle();
}

/** A confirmation made with `id`'s passkey for the account change `change` (passkeys.CHANGE), as Account makes one. */
async function confirmation(id: string, change: string) {
  auth.choose = () => id;
  try {
    return await confirmWithPasskey(false, { change });
  } finally {
    auth.choose = (allowed) => allowed[0];
  }
}

/** The next read of the keys (GET /api/ade/e2e) held on its way back: answered as the server holds them when it is
 *  asked (`asked` resolves then), and given to the page only once released. */
function holdNextRead() {
  const through = globalThis.fetch;
  let asked!: () => void;
  let release!: () => void;
  const answered = new Promise<void>((r) => { release = r; });
  const read = { asked: new Promise<void>((r) => { asked = r; }), release };
  let armed = true;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (!armed || url !== "/api/ade/e2e" || (init?.method ?? "GET") !== "GET") return through(url, init);
    armed = false;
    const answer = await through(url, init);
    asked();
    await answered;
    return answer;
  });
  return read;
}

/** A revocation made elsewhere (another tab, another device), its hold ended: the new master key's id. */
async function revokedElsewhere() {
  const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
  await r.prepare();
  await r.signAndSend();
  server.settle();
  return server.roster!.mk_id;
}

const button = (name: string | RegExp, within_: HTMLElement = document.body) => within(within_).getByRole("button", { name }) as HTMLButtonElement;

describe("turning encryption on", () => {
  it("shows the recovery code once and asks a group of it back, then takes a touch that signs and a touch that checks, and says it waits on its hold", async () => {
    passkey("credAAAA", "Laptop");
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const code = within(group).getByLabelText("The recovery code").textContent!;
    expect(code).toMatch(/^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$/);
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    const right = code.split(" ")[asked - 1];
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: (right[0] === "A" ? "B" : "A") + right.slice(1) } });
    fireEvent.click(button("Next", group));
    expect(within(group).getByRole("alert").textContent).toBe(`That is not group ${asked} of the code.`);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: right.toLowerCase() } });
    fireEvent.click(button("Next", group));
    expect(within(group).queryByLabelText("The recovery code")).toBeNull();          // shown once
    const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    expect(group.textContent).toContain("Seal in place what linked sessions sent in the clear before: 6 texts; 1 cannot be");
    const get = vi.spyOn(navigator.credentials, "get");
    fireEvent.click(sign);
    expect(get).toHaveBeenCalledTimes(1);                                              // the touch first, nothing awaited
    const again = await within(group).findByRole("button", { name: "Touch it again" });
    fireEvent.click(again);
    expect(get).toHaveBeenCalledTimes(2);
    const said = await screen.findByText(/Turning encryption on is signed\./);
    expect(said.textContent).toContain("It takes effect at");
    // the code shown takes effect only with the change
    expect(said.textContent).toContain("The recovery code you wrote down unlocks it only if this change takes effect: if it is cancelled or replaced first, that code unlocks nothing.");
    expect(said.textContent).toContain("This tab unlocks it then, while it stays open; elsewhere, unlock it with your passkey. Linked sessions seal what they send from their next run after it, and what they sent in the clear before is sealed in place.");
    expect(bodyOf("/api/ade/e2e/turn-on")?.seal).toBe(true);
    const waits = (await screen.findByText(/Turning encryption on waits until/)).closest("div[role=status]") as HTMLElement;
    // there is no recovery code yet: a passkey's touch cancels it, and nothing is said of a code it would replace
    expect(waits.textContent).toContain("Signing in alone does not cancel it: it takes a touch of Laptop.");
    expect(waits.textContent).not.toContain("recovery code");
    expect(within(waits).queryByLabelText("The recovery code, to cancel it")).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
    const waitsOn = screen.getByText(/Turning it on waits on its hold, below/);
    expect(waitsOn.compareDocumentPosition(waits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // below, as said
    // once its hold ends, this tab unlocks it with no touch, and this browser takes the keys it made
    server.settle();
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(await screen.findByText(/Unlocked with Laptop\./)).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(2);
    expect(K.floorOf(ACCOUNT)).toEqual([server.roster!.mk_id]);
    expect(screen.getByText(/Sealed in place at .*: 6 texts linked sessions had sent in the clear; 1 could not be, sent without an id to seal them by\./)).toBeTruthy();
  });

  it("names a turning-on another passkey put in the place of the one this tab made, above the change it can cancel", async () => {
    passkey("credPHON", "Phone");
    passkey("credTABL", "Tablet");
    await act(() => useE2EStore.getState().load(ACCOUNT));
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON"));
    render(<EncryptionPane />);
    expect(await screen.findByText(/Turning it on waits on its hold, below/)).toBeTruthy();   // its own: nothing more said
    await turnOnWith("credTABL");                                     // the tablet's holder, in another browser
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const said = await screen.findByText(/^The turning-on this tab made no longer waits/);
    expect(said.textContent).toBe("The turning-on this tab made no longer waits: one made with Tablet waits in its place. The recovery code this tab showed will not unlock it, and the keys this tab made are gone. If you did not make it, cancel it below before it takes effect, then turn encryption on again.");
    expect(screen.queryByText(/Turning it on waits on its hold/)).toBeNull();
    const waits = screen.getByText(/Turning encryption on waits until/).closest("div[role=status]") as HTMLElement;
    expect(said.compareDocumentPosition(waits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // below, as said
    expect(waits.textContent).toContain("Made with Tablet.");
    // the owner's phone cancels it: nothing waits, nothing more is said, and turning it on is offered again
    auth.choose = (allowed) => (allowed.includes("credPHON") ? "credPHON" : allowed[0]);
    const cancel = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(cancel.disabled).toBe(false));
    fireEvent.click(cancel);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(await screen.findByRole("button", { name: "Turn on" })).toBeTruthy();
    expect(screen.queryByText(/no longer waits|other than those this tab made/)).toBeNull();
    expect(useE2EStore.getState().waiting).toBeNull();
  });

  it("names a turning-on in the place of the start again this tab made, where encryption's state is", async () => {
    await turnedOn("credPHON", "Phone");
    passkey("credTABL", "Tablet");
    await turnedOff();
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const current = server.roster!.mk_id;
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON", true));
    expect(useE2EStore.getState().waiting?.from).toBe(current);
    render(<EncryptionPane />);
    expect(await screen.findByText(/Turning it on waits on its hold, below/)).toBeTruthy();
    await turnOnWith("credTABL", true);
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const said = await screen.findByText(/^The turning-on this tab made no longer waits: one made with Tablet waits in its place\./);
    const waits = screen.getByText(/Turning encryption on waits until/).closest("div[role=status]") as HTMLElement;
    expect(said.compareDocumentPosition(waits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/Turning it on waits on its hold/)).toBeNull();
  });

  it("names a turning-on in its place with no passkey the server names as another", () => {
    const view = K.readView({ passkeys: [{ passkey_id: "pk-1", name: "Phone" }] });
    const pending = K.readView({ pending: { id: "h", change: "turn-on", takes_effect_at: 1, body: { mk_id: "b".repeat(32) } } }).pending!;
    expect(inPlaceWords(view, pending)).toMatch(/^The turning-on this tab made no longer waits: another waits in its place\./);
    expect(inPlaceWords(view, { ...pending, made_by: "pk-1" })).toMatch(/: one made with Phone waits in its place\./);
    expect(inPlaceWords(view, { ...pending, made_by: "pk-gone" })).toMatch(/: one made with a passkey removed since waits in its place\./);
  });

  it("says when keys other than those this tab waited on took effect", async () => {
    passkey("credPHON", "Phone");
    passkey("credTABL", "Tablet");
    await act(() => useE2EStore.getState().load(ACCOUNT));
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON"));
    render(<EncryptionPane />);
    await turnOnWith("credTABL");
    server.settle();
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(await screen.findByText(K.KEYS_MADE_ELSEWHERE)).toBeTruthy();
    expect(stateOf(useE2EStore.getState())).toBe("locked");
  });

  it("sends seal false when the person leaves the texts sent before as they are", async () => {
    passkey("credAAAA", "Laptop");
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const t = within(group).getByLabelText("The recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: t.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(within(group).getByRole("checkbox"));
    fireEvent.click(sign);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    expect((await screen.findByText(/Turning encryption on is signed\./)).textContent).not.toContain("sealed in place");
    expect(bodyOf("/api/ade/e2e/turn-on")?.seal).toBe(false);
  });

  it("says this browser and key cannot unlock encryption when the second touch gives another key, and keeps nothing", async () => {
    server.addPasskey("credAAAA", "Security key", "hardware");
    auth.add("credAAAA", { unstable: true });
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const t = within(group).getByLabelText("The recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: t.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    expect((await screen.findByRole("alert")).textContent).toBe(K.UNSTABLE);
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/turn-on")).toBeUndefined();
  });
});

describe("with encryption on", () => {
  it("unlocks in this tab with a touch or the recovery code, says with what, and locks", async () => {
    const code = await turnedOn();
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Unlock" }));
    expect(await screen.findByText(/Unlocked with Laptop\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));
    expect(await screen.findByText("locked in this tab")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Your recovery code"), { target: { value: code } });
    fireEvent.click(screen.getByRole("button", { name: "Unlock with it" }));
    expect(await screen.findByText(/Unlocked with the recovery code\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));
    fireEvent.change(screen.getByLabelText("Your recovery code"), { target: { value: C.recoveryText(C.random(20)) } });
    fireEvent.click(screen.getByRole("button", { name: "Unlock with it" }));
    expect((await screen.findByRole("alert")).textContent).toBe(K.WRONG_CODE);
  });

  it("lists each passkey: one that unlocks it, one the roster does not name, one that may be added, and high security's rule", async () => {
    await turnedOn("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    passkey("credCCCC", "Old key");
    server.credentials.push({ passkey_id: "pk-credCCCC", credential_id: "credCCCC", salt: C.b64u(C.random(32)), public_key: C.b64u((await C.newPair()).pub),
      wrapped: C.b64u(C.random(80)), created_at: 1 });
    render(<EncryptionPane />);
    const row = async (name: string) => (await screen.findByText(name)).closest("li") as HTMLElement;
    expect((await row("Laptop")).textContent).toContain("Unlocks encryption");
    expect((await row("Phone")).textContent).toContain("Does not unlock encryption yet");
    const add = button("Add", await row("Phone"));
    expect(add.disabled).toBe(true);
    expect(add.title).toContain("Unlock first");
    expect((await row("Old key")).textContent).not.toContain("Not named by the roster");   // not known until unlocked
    await unlock();
    expect((await row("Old key")).textContent).toContain("Not named by the roster of these keys: no new master key is sealed to it");
    expect(button("Add", await row("Phone")).disabled).toBe(false);
    server.high = true;
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect((await row("Phone")).textContent).toContain("High security is on: only a hardware key unlocks encryption");
    expect(within(await row("Phone")).queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.getByText(/High security is on: only a hardware key unlocks it/)).toBeTruthy();
  });

  it("adds a passkey with two touches of it, then a touch of one that unlocks encryption already confirms it, which then waits on its hold", async () => {
    await turnedOn("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(button("Add", (await screen.findByText("Phone")).closest("li")!));
    const group = await screen.findByRole("group", { name: "Adding “Phone”" });
    const first = await within(group).findByRole("button", { name: "Touch it" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(first.disabled).toBe(false));
    expect(group.textContent).toContain("Touch Phone twice: the first seals the key to it, the second checks it gives the same key again and confirms, from Phone itself, that it is the passkey being added.");
    auth.choose = () => "credBBBB";
    auth.ceremonies.length = 0;
    const get = vi.spyOn(navigator.credentials, "get");
    fireEvent.click(first);
    expect(get).toHaveBeenCalledTimes(1);                                              // each touch first, nothing awaited
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    expect(get).toHaveBeenCalledTimes(2);                                              // its challenge fetched as the first ended
    const confirm = await within(group).findByRole("button", { name: "Confirm with a passkey" }) as HTMLButtonElement;
    expect(group.textContent).toContain("Now confirm it with a touch of Laptop, which unlocks encryption already. It waits 3 minutes before it takes effect, and can be cancelled until then.");
    expect(group.textContent).toContain("Or confirm it with the recovery code instead. With the recovery code it waits a day, and only the code cancels it then.");
    expect(button("Confirm with the recovery code", group).disabled).toBe(true);    // until the code is given
    expect(server.calls.some((c) => c.path === "/api/ade/e2e/credentials")).toBe(false);
    auth.choose = () => "credAAAA";
    fireEvent.click(confirm);
    expect(get).toHaveBeenCalledTimes(3);                                              // the touch first, nothing awaited
    expect((await within(group).findByText(/Phone is added when its hold ends\. It takes effect at/)).textContent).toContain("unless you cancel it first. Its notice was sent toward 1 device with notifications on.");
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"], ["credBBBB"], ["credAAAA"]]);
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "pk-credAAAA" });
    expect(bodyOf("/api/ade/e2e/credentials")?.credential.passkey_id).toBe("pk-credBBBB");
    expect(bodyOf("/api/ade/e2e/credentials")?.credential.confirmation).toMatch(/^confirmation-/);
  });

  it("removes a passkey by a revocation it signs, which names what no longer unlocks it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    expect(group.textContent).toContain("replaces the keys");
    expect(await within(group).findByText("No longer unlocks encryption: Phone.")).toBeTruthy();
    const sign = button("Sign it", group);
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    expect(await within(group).findByText(/Signed\. It takes effect at/)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/revoke")?.remove).toEqual(["pk-credBBBB"]);
    expect(server.pending?.change).toBe("revoke");
  });

  it("rotates the key, makes a new recovery code shown once, and renews anchors, each signed or touched", async () => {
    await turnedOn("credAAAA", "Laptop");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(screen.getByRole("button", { name: "Rotate the key" }));
    let group = await screen.findByRole("group", { name: "Rotating the key" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    fireEvent.click(button("Sign it", group));
    expect(await within(group).findByText(/Signed\. It takes effect at .*the old one is retired an hour after/)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/rotate")?.statement.payload.retires).toBe("grace");
    fireEvent.click(button("Close", group));
    server.pending = null;

    fireEvent.click(screen.getByRole("button", { name: "New recovery code" }));
    group = await screen.findByRole("group", { name: "A new recovery code" });
    // the new code takes effect only with the change it goes with
    expect(group.textContent).toContain("It replaces the old one only when the change it goes with takes effect: until then the old one still opens everything, and if that change is cancelled or replaced first, this one never does.");
    const fresh = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: fresh.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    group = await screen.findByRole("group", { name: "A new recovery code" });
    expect(group.textContent).toContain("When this change takes effect, the new code replaces the old one, which then opens nothing; if it is cancelled or replaced first, the old code still opens everything and the new one never does.");
    expect(group.textContent).not.toContain("The new code replaces the old one, which then opens nothing");
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    fireEvent.click(button("Sign it", group));
    await within(group).findByText(/Signed\./);
    const { RK } = await C.recoveryKeys(C.recoveryBytes(fresh)!, ACCOUNT);
    expect(bodyOf("/api/ade/e2e/revoke")?.recovery.public_key).toBe(C.b64u(RK));
    fireEvent.click(button("Close", group));
    server.settle();
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");

    fireEvent.click(screen.getByRole("button", { name: "Renew anchors" }));
    group = await screen.findByRole("group", { name: "Renewing anchors" });
    const laptop = await within(group).findByRole("button", { name: "Laptop" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(laptop.disabled).toBe(false));
    expect(button("Send", group).disabled).toBe(true);                     // nothing touched yet
    fireEvent.click(laptop);
    await vi.waitFor(() => expect(button("Send", group).disabled).toBe(false));
    expect(laptop.disabled).toBe(true);                                    // touched once
    fireEvent.click(button("Send", group));
    expect(await within(group).findByText(/Signed\. It takes effect at/)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/anchors")?.anchors.map((a: Body) => a.passkey_id)).toEqual(["pk-credAAAA"]);
  });

  it("turns it off, saying what that does, with a signed e2e-off", async () => {
    await turnedOn();
    render(<EncryptionPane />);
    expect(await screen.findByText(/send their titles, conversations and notes in the clear again, from their next run.*Nothing sealed is opened/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    const sign = await screen.findByRole("button", { name: "Sign turning it off" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    expect(await screen.findByText(/Signed\. It takes effect at/)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/off")?.statement.payload.purpose).toBe("e2e-off");
  });

  it("adds a passkey confirmed with the recovery code instead, typed and checked first: no confirming touch, and a day's wait", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(button("Add", (await screen.findByText("Phone")).closest("li")!));
    const group = await screen.findByRole("group", { name: "Adding “Phone”" });
    const first = await within(group).findByRole("button", { name: "Touch it" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(first.disabled).toBe(false));
    auth.choose = () => "credBBBB";
    fireEvent.click(first);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    const withCode = await within(group).findByRole("button", { name: "Confirm with the recovery code" }) as HTMLButtonElement;
    const typed = () => within(group).getByLabelText("The recovery code, to confirm adding it");
    fireEvent.change(typed(), { target: { value: "A".repeat(32) } });
    fireEvent.click(button("Use the code", group));
    expect((await within(group).findByRole("alert")).textContent).toBe(K.WRONG_CODE);
    expect(withCode.disabled).toBe(true);
    fireEvent.change(typed(), { target: { value: code } });
    fireEvent.click(button("Use the code", group));
    await vi.waitFor(() => expect(withCode.disabled).toBe(false));
    expect(group.textContent).toContain("That is the recovery code: it goes with its proof.");
    auth.ceremonies.length = 0;
    fireEvent.click(withCode);
    expect(await within(group).findByText(/Phone is added when its hold ends\. It takes effect at/)).toBeTruthy();
    expect(auth.ceremonies).toHaveLength(0);
    expect(Object.keys(bodyOf("/api/ade/e2e/credentials")!).sort()).toEqual(["credential", "grant", "mk_id", "recovery_proof", "roster"]);
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "recovery", takes_effect_at: server.now + 86400 });
  });

  it("adds a passkey only with the recovery code while a change waits that only the code cancels, and starts again from the first touch when the keys change meanwhile", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(button("Add", (await screen.findByText("Phone")).closest("li")!));
    let group = await screen.findByRole("group", { name: "Adding “Phone”" });
    let first = await within(group).findByRole("button", { name: "Touch it" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(first.disabled).toBe(false));
    auth.choose = () => "credBBBB";
    fireEvent.click(first);
    await within(group).findByRole("button", { name: "Touch it again" });
    // a change the code made waits since: the adding is made again, from its first touch
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    group = await screen.findByRole("group", { name: "Adding “Phone”" });
    first = await within(group).findByRole("button", { name: "Touch it" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(first.disabled).toBe(false));
    fireEvent.click(first);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    await within(group).findByRole("button", { name: "Confirm with the recovery code" });
    expect(group.textContent).toContain("A change waits that none of your passkeys that unlock encryption may take the place of, but the recovery code can: adding it goes with the code. With the recovery code it waits a day, and only the code cancels it then.");
    expect(within(group).queryByRole("button", { name: "Confirm with a passkey" })).toBeNull();
    fireEvent.change(within(group).getByLabelText("The recovery code, to confirm adding it"), { target: { value: code } });
    fireEvent.click(button("Use the code", group));
    await vi.waitFor(() => expect(button("Confirm with the recovery code", group).disabled).toBe(false));
    fireEvent.click(button("Confirm with the recovery code", group));
    expect(await within(group).findByText(/Phone is added when its hold ends\./)).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "credentials", madeBy: "recovery" });
    expect(server.pending!.id).not.toBe("hold-w");                                   // in that one's place
  });

  it("rotates the key saying how long it waits, and only with the recovery code while a change waits that only the code cancels", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(screen.getByRole("button", { name: "Rotate the key" }));
    let group = await screen.findByRole("group", { name: "Rotating the key" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    expect(group.textContent).toContain("It waits 3 minutes before it takes effect, and can be cancelled until then.");
    expect(group.textContent).toContain("The server takes a routine rotation at most once a day unless it is set otherwise, and says so when it refuses one");
    expect(within(group).queryByRole("group", { name: "With the recovery code" })).toBeNull();
    fireEvent.click(button("Cancel", group));
    server.pending = { id: "hold-w", change: "credentials", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    fireEvent.click(screen.getByRole("button", { name: "Rotate the key" }));
    group = await screen.findByRole("group", { name: "Rotating the key" });
    const withCode = within(group).getByRole("group", { name: "With the recovery code" });
    expect(withCode.textContent).toContain("this goes with the code, and any passkey of your account signs it. With the recovery code it waits a day, and only the code cancels it then.");
    expect(within(group).queryByRole("button", { name: "Sign it" })).toBeNull();
    const sign = button("Sign it, with the recovery code", group);
    await vi.waitFor(() => expect(sign.querySelector(".animate-spin")).toBeNull());
    expect(sign.disabled).toBe(true);
    fireEvent.change(within(withCode).getByLabelText("The recovery code, to rotate the key with it"), { target: { value: code } });
    fireEvent.click(button("Use the code", withCode));
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    expect(await within(group).findByText(/Signed\./)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/rotate")?.recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "rotate", byCode: true, takes_effect_at: server.now + 86400 });
  });

  it("turns it off saying it waits a day, and only with the recovery code while a change waits that only the code cancels", async () => {
    const code = await turnedOn();
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(await screen.findByText("It waits a day before it takes effect, and can be cancelled until then.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "With the recovery code" })).toBeNull();
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery", byCode: true };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const withCode = await screen.findByRole("group", { name: "With the recovery code" });
    expect(withCode.textContent).toContain("turning it off goes with the code, and any passkey of your account signs it.");
    expect(screen.queryByRole("button", { name: "Sign turning it off" })).toBeNull();
    const sign = screen.getByRole("button", { name: "Sign turning it off, with the recovery code" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.querySelector(".animate-spin")).toBeNull());
    expect(sign.disabled).toBe(true);
    fireEvent.change(within(withCode).getByLabelText("The recovery code, to turn it off with it"), { target: { value: code } });
    fireEvent.click(button("Use the code", withCode));
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    expect(await screen.findByText(/Signed\. It takes effect at/)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/off")?.recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "off", byCode: true });
  });

  it("renewing anchors asks for a passkey that may take the waiting change's place where none touched may", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await enrolled("credCCCC", "Key");
    let r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();                                                   // every anchor older than the master key
    r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), { remove: ["pk-credBBBB"] });
    await r.prepare();
    auth.choose = () => "credAAAA";
    await r.signAndSend();                                             // Laptop's revocation of Phone waits
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(screen.getByRole("button", { name: "Renew anchors" }));
    const group = await screen.findByRole("group", { name: "Renewing anchors" });
    const phone = await within(group).findByRole("button", { name: "Phone" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(phone.disabled).toBe(false));
    auth.choose = () => "credBBBB";
    fireEvent.click(phone);
    await vi.waitFor(() => expect(group.textContent).toContain("To confirm it, touch Laptop or Key too: none of the passkeys touched may take the place of the change that waits."));
    fireEvent.click(button("Send", group));
    expect((await within(group).findByRole("alert")).textContent).toBe(K.TOUCH_ONE_THAT_MAY);
    const key = button("Key", group);
    auth.choose = () => "credCCCC";
    fireEvent.click(key);
    await vi.waitFor(() => expect(group.textContent).not.toContain("To confirm it, touch"));
    expect(key.disabled).toBe(true);
    fireEvent.click(button("Send", group));
    expect(await within(group).findByText(/Signed\. It takes effect at/)).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "pk-credCCCC" });
    expect(bodyOf("/api/ade/e2e/anchors")?.anchors.map((a: Body) => [a.passkey_id, !!a.confirmation])).toEqual([["pk-credBBBB", true], ["pk-credCCCC", false]]);
  });

  it("names the passkey that signed the newest key and when it was added, and shows a change that waits, which a touch of a passkey cancels", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-9", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    render(<EncryptionPane />);
    const stated = (await screen.findByText(/statement 1, signed with/)).textContent!;
    expect(stated).toContain(`Key ${server.statements[0].key_id!.slice(0, 8)}`);
    expect(stated).toMatch(/signed with Laptop \(added .+\)/);
    const waits = screen.getByText(/A new content key waits until/).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Signing in alone does not cancel it: it takes a touch of Laptop, or the recovery code.");
    const touch = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    const get = vi.spyOn(navigator.credentials, "get");
    fireEvent.click(touch);
    expect(get).toHaveBeenCalledTimes(1);                                              // the touch first, nothing awaited
    await vi.waitFor(() => expect(screen.queryByText(/A new content key waits until/)).toBeNull());
    expect(Object.keys(bodyOf("/api/ade/holds/hold-9/cancel")!)).toEqual(["confirmation"]);
    expect(server.pending).toBeNull();
  });

  it("cancels a revocation only with a passkey it does not remove, the only ones offered, or with the recovery code", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const revoking = (id: string) => ({ id, change: "revoke", takes_effect_at: server.now + 180, apply: () => {}, removes: ["pk-credBBBB"] });
    const waiting = async () => (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    server.pending = revoking("hold-7");
    render(<EncryptionPane />);
    let waits = await waiting();
    expect(waits.textContent).toContain("it takes a touch of Laptop, or the recovery code.");
    await vi.waitFor(() => expect(button("Cancel it with a passkey", waits).disabled).toBe(false));
    // another change takes its place before it is cancelled (it took effect, and another was made): the cancel is its own
    server.pending = revoking("hold-8");
    await act(() => useE2EStore.getState().load(ACCOUNT));
    waits = await waiting();
    const touch = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(touch);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credAAAA"]]);                // never the passkey it removes
    expect(bodyOf("/api/ade/holds/hold-8/cancel")).toBeTruthy();
    // the recovery code, typed in
    server.pending = revoking("hold-9");
    await act(() => useE2EStore.getState().load(ACCOUNT));
    waits = await waiting();
    fireEvent.change(within(waits).getByLabelText("The recovery code, to cancel it"), { target: { value: code } });
    fireEvent.click(button("Cancel it with the code", waits));
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(Object.keys(bodyOf("/api/ade/holds/hold-9/cancel")!)).toEqual(["recovery_proof"]);
    expect(server.calls.filter((c) => c.path === "/api/ade/e2e/recovery-proof").map((c) => c.body?.purpose)).toEqual(["cancel"]);
    // with the keys unlocked by the code, nothing is typed again
    server.pending = revoking("hold-10");
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); await useE2EStore.getState().unlockWithCode(code); });
    waits = await waiting();
    expect(within(waits).queryByLabelText("The recovery code, to cancel it")).toBeNull();
    fireEvent.click(button("Cancel it with the recovery code", waits));
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(Object.keys(bodyOf("/api/ade/holds/hold-10/cancel")!)).toEqual(["recovery_proof"]);
  });

  it("offers only the recovery code for a change that no passkey of the account may cancel", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-5", change: "revoke", takes_effect_at: server.now + 180, apply: () => {}, removes: ["pk-credAAAA"] };
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code.");
    expect(within(waits).queryByRole("button", { name: "Cancel it with a passkey" })).toBeNull();
    const c = new K.CancelChange(ACCOUNT, await K.E2EApi.view(), { id: "hold-5", change: "revoke", takes_effect_at: 1, cancel_by: [], cancel_by_code: true,
      made_by: null, by_code: false, removes: ["pk-credAAAA"], replaces_code: false, body: null });
    await c.prepare();
    await expect(c.withPasskey()).rejects.toThrow(K.NONE_CANCELS);
    fireEvent.change(within(waits).getByLabelText("The recovery code, to cancel it"), { target: { value: code } });
    fireEvent.click(button("Cancel it with the code", waits));
    await vi.waitFor(() => expect(server.pending).toBeNull());
  });

  it("offers the recovery code in force to cancel a change that replaces it, beside the passkeys the server lists", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-6", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {}, removesCode: true };
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Signing in alone does not cancel it: it takes a touch of Laptop, or the recovery code.");
    expect(within(waits).getByRole("button", { name: "Cancel it with a passkey" })).toBeTruthy();
    fireEvent.change(within(waits).getByLabelText("The recovery code, to cancel it"), { target: { value: code } });
    fireEvent.click(button("Cancel it with the code", waits));
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(Object.keys(bodyOf("/api/ade/holds/hold-6/cancel")!)).toEqual(["recovery_proof"]);
  });

  it("offers exactly what the server lists, nothing it leaves out: no passkey but those named, and no code where it says none", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-11", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: { ...v.pending, cancel_by: ["pk-credBBBB"], cancel_by_code: false } }];
    };
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/A new content key waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Signing in alone does not cancel it: it takes a touch of Phone.");
    expect(waits.textContent).not.toContain("Not yours? Remove the passkey that made it");          // a revocation's alone
    expect(within(waits).queryByLabelText("The recovery code, to cancel it")).toBeNull();
    const touch = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(touch);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"]]);
    const c = new K.CancelChange(ACCOUNT, await K.E2EApi.view(), { id: "hold-11", change: "rotate", takes_effect_at: 1, cancel_by: [], cancel_by_code: false,
      made_by: null, by_code: false, removes: [], replaces_code: false, body: null });
    await expect(c.withCode({ code: "A".repeat(32) })).rejects.toThrow(K.NO_CODE_CANCELS);
    expect(server.calls.some((x) => x.path === "/api/ade/e2e/recovery-proof")).toBe(false);
  });

  it("turns it on again with a new master key when a passkey that unlocked it was removed while it was off, and says so where encryption's state is", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credBBBB");       // removed while it was off
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");
    server.grants = server.grants.filter((g) => g.to !== "credBBBB");
    server.rekey = true;
    render(<EncryptionPane />);
    await unlock();
    expect(await screen.findByText(/no longer unlocks encryption: removed, or left out by high security, while it was off/)).toBeTruthy();
    const again = await screen.findByRole("button", { name: "Turn it on again" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(again.disabled).toBe(false));
    fireEvent.click(again);
    expect(await screen.findByText(/^Turning encryption on again is signed\. It takes effect at/)).toBeTruthy();
    const body = bodyOf("/api/ade/e2e/turn-on")!;
    expect(body.chain.previous).toBe(server.roster!.mk_id);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(server.pending?.change).toBe("turn-on");
    // the change waits below, and turning it on is offered no more meanwhile
    expect(screen.queryByRole("button", { name: "Turn it on again" })).toBeNull();
    const waitsOn = screen.getByText(/Turning it on waits on its hold, below/);
    const waits = screen.getByText(/Turning encryption on waits until/).closest("div[role=status]") as HTMLElement;
    expect(waitsOn.compareDocumentPosition(waits) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("asks the browser again, with a fresh challenge, after a cancel the server refused", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-4", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    let refuse = true;
    server.answer = (call) => {
      if (call.path !== "/api/ade/holds/hold-4/cancel" || !refuse) return null;
      refuse = false;
      return [403, { detail: "That confirmation has expired or was used; confirm again." }];
    };
    // each preparation of the cancel's challenge: the button is enabled again while the fresh one is fetched, and a click
    // before it is held is answered "Cancelling is being prepared"
    const prepared = vi.spyOn(K.CancelChange.prototype, "prepare");
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/A new content key waits until/)).closest("div[role=status]") as HTMLElement;
    const touch = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    fireEvent.click(touch);
    expect((await within(waits).findByRole("alert")).textContent).toBe("That confirmation has expired or was used; confirm again.");
    await vi.waitFor(() => expect(server.calls.filter((c) => c.path === "/api/account/confirm/options").length).toBe(2));
    await Promise.all(prepared.mock.results.map((r) => r.value));       // the fresh challenge held, not only asked for
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    fireEvent.click(touch);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    const challenges = server.calls.filter((c) => c.path === "/api/account/confirm").map((c) => c.body?.challenge_id);
    expect(challenges.length).toBe(2);
    expect(new Set(challenges).size).toBe(2);
  });

  it("says, in place of the key links seal to, that the server lists a statement other than it was signed, and names no key from its columns", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.statements[0].key_id = "ab".repeat(16);
    render(<EncryptionPane />);
    const said = await screen.findByText(K.STATED_OTHERWISE);
    expect(said.getAttribute("role")).toBe("alert");
    expect(said.closest("section")!.querySelector("h3")!.textContent).toBe("The key links seal to");
    expect(screen.queryByText(/statement 1, signed with/)).toBeNull();
    expect(document.body.textContent).not.toContain("abababab");
  });

  it("shows each link's key and pins, and for a link that holds or seals to an older key, which passkey to sign with", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.addPasskey("credBBBB", "Security key", "hardware");
    const [laptop, key] = server.passkeys;
    const newest = server.statements[0].key_id!;
    server.links = [
      { link_id: "L1", name: "app on the mac", key_id: newest, last_required_at: null, pins: [laptop.fingerprint] },
      { link_id: "L2", name: "cloud session", key_id: null, last_required_at: server.now - 60, pins: [key.fingerprint, "f".repeat(32)] },
      { link_id: "L3", name: "old box", key_id: "ab".repeat(16), last_required_at: null, pins: [] },
    ];
    render(<EncryptionPane />);
    const item = async (name: string) => (await screen.findByText(name)).closest("li") as HTMLElement;
    expect((await item("app on the mac")).textContent).toContain("Seals to the newest key.");
    await vi.waitFor(async () => expect((await item("app on the mac")).textContent).toContain("Pins: Laptop."));
    const cloud = (await item("cloud session")).textContent!;
    expect(cloud).toContain("Keeps its texts: the ADE refused it one in the clear at");
    expect(cloud).toContain("Pins: Security key; 1 key this account no longer has.");
    expect(cloud).toContain("It did not pin Laptop: rotate the key signing with Security key, which it pinned");
    const old = (await item("old box")).textContent!;
    expect(old).toContain("Seals to an older key (abababab)");
    expect(old).toContain("It pinned none of your passkeys: give it a pin line");
  });

  it("says a link has not reported its pins, and matches no pin to passkeys it could not read", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.addPasskey("credBBBB", "Security key", "hardware");
    const key = server.passkeys[1];
    server.links = [
      { link_id: "L1", name: "new box", key_id: null, last_required_at: null, pins: null },
      { link_id: "L2", name: "cloud session", key_id: null, last_required_at: server.now - 60, pins: [key.fingerprint, "f".repeat(32)] },
    ];
    server.answer = (call) => (call.method === "GET" && call.path === "/api/account/passkeys" ? [500, { detail: "down" }] : null);
    render(<EncryptionPane />);
    const item = async (name: string) => (await screen.findByText(name)).closest("li") as HTMLElement;
    const fresh = (await item("new box")).textContent!;
    expect(fresh).toContain("Pins: not reported yet.");
    expect(fresh).toContain("It has not reported which passkeys it pinned yet, so which key it can take is not known.");
    expect(fresh).not.toContain("It pinned none of your passkeys");
    await vi.waitFor(async () => expect((await item("cloud session")).textContent).toContain("Pins: 2 keys, not matched: this account's passkeys could not be read."));
    const cloud = (await item("cloud session")).textContent!;
    expect(cloud).toContain("Its pins could not be matched to your passkeys, which could not be read: reload this page to try again.");
    expect(cloud).not.toContain("no longer has");
    expect(cloud).not.toContain("It pinned none of your passkeys");
  });

  it("while the account's passkeys are read, says only how many keys a link pinned, and advises nothing on them", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.links = [{ link_id: "L2", name: "cloud session", key_id: null, last_required_at: server.now - 60, pins: ["f".repeat(32)] }];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
      url.endsWith("/api/account/passkeys") && (init?.method ?? "GET") === "GET" ? new Promise<Response>(() => {}) : server.fetch(url, init));
    render(<EncryptionPane />);
    const cloud = (await screen.findByText("cloud session")).closest("li") as HTMLElement;
    expect(cloud.textContent).toContain("Pins: 1 key, being matched to your passkeys.");
    expect(cloud.textContent).not.toContain("could not be read");
    expect(cloud.textContent).not.toContain("It pinned none of your passkeys");
  });
});

describe("keys put back, keys that change, and what a change keeps", () => {
  it("refuses keys that do not lead back to the newest this browser opened, and takes them only at the person's word", async () => {
    await turnedOn("credAAAA", "Laptop");
    const first = JSON.parse(JSON.stringify({ grants: server.grants, chain: server.chain, roster: server.roster, contentKeys: server.contentKeys, statements: server.statements }));
    render(<EncryptionPane />);
    await unlock();
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await act(() => useE2EStore.getState().load(ACCOUNT));            // followed: this browser's floor is the new key
    expect(K.floorOf(ACCOUNT)).toEqual([server.roster!.mk_id]);
    Object.assign(server, first);                                      // the server put back to before it
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(stateOf(useE2EStore.getState())).toBe("locked");
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
    // the first unlock's touch answered only once the lines below have run, as a person's comes a moment after the
    // click: the group is drawn before it, from the view and this browser's floor
    let answer!: () => void;
    const answered = new Promise<void>((r) => { answer = r; });
    const touch = navigator.credentials.get;
    const get = vi.spyOn(navigator.credentials, "get").mockImplementationOnce(async (o) => { await answered; return touch(o); });
    fireEvent.click(await screen.findByRole("button", { name: "Unlock" }));
    expect((await screen.findByRole("alert")).textContent).toBe(K.ROLLED_BACK);
    const ask = screen.getByRole("group", { name: "Keys older than this browser's" });
    expect(ask.textContent).toContain("only if you started encryption again yourself, or restored the server from a backup yourself");
    expect(stateOf(useE2EStore.getState())).toBe("locked");
    answer();
    // the unlock ends refused: its words beside the view's, and its button free again. Trust waits until then, so the
    // person's word comes after that unlock, never while it is on its way
    await vi.waitFor(() => {
      expect(screen.getAllByRole("alert").map((a) => a.textContent)).toEqual([K.ROLLED_BACK, K.ROLLED_BACK]);
      expect(button("Unlock").disabled).toBe(false);
    });
    fireEvent.click(button("Trust the server's keys", ask));
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    expect(await screen.findByText(/This browser now takes the keys the server holds: unlock again\./)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Keys older than this browser's" })).toBeNull();
    // the unlock the person is asked for opens the keys: still locked until then, and one touch for it
    expect(stateOf(useE2EStore.getState())).toBe("locked");
    get.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Unlock" }));
    expect(get).toHaveBeenCalledTimes(1);                                              // the touch first, nothing awaited
    expect(await screen.findByText(/Unlocked with Laptop\./)).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(1);
    // keys that do not lead back again, later: the person is asked again
    act(() => useE2EStore.getState().lock());
    localStorage.setItem(`pp-e2e-floor:${ACCOUNT}`, JSON.stringify({ floor: "f".repeat(32), next: null }));
    fireEvent.click(await screen.findByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("group", { name: "Keys older than this browser's" })).toBeTruthy();
  });

  it("offers Trust only once an unlock here has ended: one on its way as the floor is dropped would open the keys with no second click", async () => {
    await turnedOn("credAAAA", "Laptop");
    const first = JSON.parse(JSON.stringify({ grants: server.grants, chain: server.chain, roster: server.roster, contentKeys: server.contentKeys, statements: server.statements }));
    render(<EncryptionPane />);
    await unlock();
    const newer = await revokedElsewhere();
    await act(() => useE2EStore.getState().load(ACCOUNT));            // followed: this browser's floor is the new key
    Object.assign(server, first);                                      // the server put back to before it
    await act(() => useE2EStore.getState().load(ACCOUNT));
    // an unlock on its way, its touch not yet answered: the group is drawn, and Trust waits
    let touch!: () => void;
    const touched = new Promise<void>((r) => { touch = r; });
    const get = navigator.credentials.get;
    vi.spyOn(navigator.credentials, "get").mockImplementation(async (o) => { await touched; return get(o); });
    fireEvent.click(await screen.findByRole("button", { name: "Unlock" }));
    const ask = screen.getByRole("group", { name: "Keys older than this browser's" });
    expect(button("Trust the server's keys", ask).disabled).toBe(true);
    fireEvent.click(button("Trust the server's keys", ask));
    expect(K.floorOf(ACCOUNT)).toEqual([newer]);
    // the touch answered, and the unlock's read of the keys held on its way back: Trust still waits (in a browser the
    // passkey's prompt is modal, so this read is the time a click can fall in)
    const read = holdNextRead();
    touch();
    await read.asked;
    await act(() => new Promise<void>((r) => setTimeout(r, 20)));       // what the page does meanwhile, drawn
    expect(button("Trust the server's keys", ask).disabled).toBe(true);
    fireEvent.click(button("Trust the server's keys", ask));
    expect(K.floorOf(ACCOUNT)).toEqual([newer]);
    // answered, the unlock ends refused, and Trust is offered
    read.release();
    await vi.waitFor(() => expect(button("Trust the server's keys", ask).disabled).toBe(false));
    expect(stateOf(useE2EStore.getState())).toBe("locked");
    expect(screen.getAllByRole("alert").map((a) => a.textContent)).toEqual([K.ROLLED_BACK, K.ROLLED_BACK]);
  });

  it("says the server may have been put back where it serves no keys and this browser opened the account's, and does not offer turning it on", async () => {
    // the account's keys as a backup made before encryption was turned on holds them: none
    const beforeOn = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain }));
    await turnedOn("credAAAA", "Laptop");
    render(<EncryptionPane />);
    await unlock();                                                    // this browser's floor: the master key and statement
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    Object.assign(server, beforeOn);                                   // the server restored from that backup
    await act(() => useE2EStore.getState().load(ACCOUNT));
    // said with what to do for an owner who restored it themselves, and turning it on offered below that
    expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(stateOf(useE2EStore.getState())).toBe("off");
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
    // a browser that kept only the master key it opened (a page of an older release) says the same
    cleanup();
    useE2EStore.setState(useE2EStore.getInitialState());
    localStorage.setItem(`pp-e2e-floor:${ACCOUNT}`, JSON.stringify({ floor: "f".repeat(32), next: null }));
    render(<EncryptionPane />);
    expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
  });

  it("makes a revocation again from the keys as they are when they change while it is open, so a passkey added meanwhile is kept", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    await enrolled("credCCCC", "Key");                                 // elsewhere, while it is open
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(useE2EStore.getState().ring?.roster?.credentials.map((c) => c.credential_id)).toEqual(["credAAAA", "credBBBB", "credCCCC"]);
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    fireEvent.click(button("Sign it", group));
    await within(group).findByText(/Signed\./);
    expect(bodyOf("/api/ade/e2e/revoke")?.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "credCCCC", "recovery"]);
  });

  it("asks the person's word before a revocation leaves only the recovery code, which it then goes with, and says what stays and what it takes the place of", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");                                      // a passkey that does not unlock encryption
    render(<EncryptionPane />);
    await unlock();
    server.pending = { id: "hold-3", change: "credentials", takes_effect_at: server.now + 180, apply: () => {} };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const group = await screen.findByRole("group", { name: "Removing “Laptop”" });
    expect(group.textContent).toContain("No longer unlocks encryption: Laptop.");
    expect(group.textContent).toContain("After it, only the recovery code unlocks encryption.");
    expect((await within(group).findByRole("alert")).textContent).toBe(K.NONE_KEPT);
    expect(button("Sign it", group).disabled).toBe(true);
    expect(within(group).queryByRole("group", { name: "With the recovery code" })).toBeNull();     // not before the person's word
    fireEvent.click(within(group).getByRole("checkbox", { name: /Only the recovery code is to unlock encryption after it/ }));
    // no passkey stays after it to sign it alone: it goes with the code's proof, in the place of the change that waits
    const withCode = await within(group).findByRole("group", { name: "With the recovery code" });
    expect(withCode.textContent).toContain("No passkey that unlocks encryption still does after it to sign it: it goes with the recovery code, and any passkey of your account signs it. With the code it takes the place of the change that waits (A passkey added to encryption) in one step. With the recovery code it waits a day, and only the code cancels it then.");
    expect(within(group).queryByRole("alert")).toBeNull();
    expect(within(group).queryByRole("button", { name: "Sign it" })).toBeNull();
    const sign = button("Sign it, with the recovery code", group);
    expect(sign.disabled).toBe(true);                                  // until the code is given
    fireEvent.change(within(withCode).getByLabelText("The recovery code, for this one to take its place"), { target: { value: code } });
    fireEvent.click(button("Use the code", withCode));
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(sign);
    await within(group).findByText(/Signed\./);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB"]);           // any passkey of the account, with the code
    expect(bodyOf("/api/ade/e2e/revoke")?.grants.map((g: Body) => g.to)).toEqual(["recovery"]);
    expect(bodyOf("/api/ade/e2e/revoke")?.recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "revoke", byCode: true });
  });

  it("puts a revocation in a waiting change's place only signed with a passkey that could cancel that one", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await enrolled("credCCCC", "Key");
    // a stolen Phone's revocation removes Laptop and Key and replaces the code: it leaves Phone alone, so Laptop and Key
    // may still cancel it
    server.pending = { id: "hold-20", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {},
      removes: ["pk-credAAAA", "pk-credCCCC"], removesCode: true, madeBy: "pk-credBBBB" };
    render(<EncryptionPane />);
    await unlock();
    const waits = (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("it takes a touch of Laptop or Phone or Key, or the recovery code.");
    expect(waits.textContent).toContain("Not yours? Remove the passkey that made it (What unlocks it, below): that revocation takes its place in one step");
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    expect(group.textContent).toContain("It takes the place of the change that waits (Replacing the keys), signed with a passkey that could cancel that one.");
    expect(group.textContent).toContain("It waits 3 minutes before it takes effect, and can be cancelled until then.");
    expect(within(group).queryByRole("group", { name: "With the recovery code" })).toBeNull();     // a passkey of theirs does
    const sign = button("Sign it", group);
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(sign);
    await within(group).findByText(/Signed\./);
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credCCCC"]);
    expect(server.pending).toMatchObject({ change: "revoke", removes: ["pk-credBBBB"], madeBy: "pk-credAAAA" });
    expect((await K.E2EApi.view()).pending?.cancel_by).toEqual(["pk-credAAAA", "pk-credCCCC"]);    // Phone cancels it no more
  });

  it("puts a revocation in the place of one only the recovery code cancels with the code, checked before the touch, in one step", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    // a stolen Phone's revocation of Laptop, sent with the code's proof: no passkey cancels it, only the code
    server.pending = { id: "hold-21", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {},
      removes: ["pk-credAAAA"], madeBy: "pk-credBBBB", byCode: true };
    render(<EncryptionPane />);
    await unlock();
    const waits = (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Made with Phone and the recovery code's proof: only the code cancels it. It removes Laptop.");
    expect(waits.textContent).toContain("Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code.");
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    const withCode = await within(group).findByRole("group", { name: "With the recovery code" });
    expect(withCode.textContent).toContain("A change waits (Replacing the keys) that no passkey that unlocks encryption and still does after this one could cancel, but the recovery code can: with the code, this one takes its place in one step.");
    expect(withCode.textContent).toContain("With the recovery code it waits a day, and only the code cancels it then.");
    expect(group.textContent).not.toContain("signed with a passkey that could cancel that one");
    expect(within(group).queryByRole("button", { name: "Sign it" })).toBeNull();
    const sign = button("Sign it, with the recovery code", group);
    const typed = within(withCode).getByLabelText("The recovery code, for this one to take its place");
    await vi.waitFor(() => expect(sign.querySelector(".animate-spin")).toBeNull());     // prepared: it waits for the code
    expect(sign.disabled).toBe(true);
    // a wrong code is refused before any touch, and signing waits for the right one
    fireEvent.change(typed, { target: { value: "A".repeat(32) } });
    fireEvent.click(button("Use the code", withCode));
    expect((await within(group).findByRole("alert")).textContent).toBe(K.WRONG_CODE);
    expect(sign.disabled).toBe(true);
    fireEvent.change(typed, { target: { value: code } });
    fireEvent.click(button("Use the code", withCode));
    expect(await within(withCode).findByText("That is the recovery code: it goes with its proof.")).toBeTruthy();
    // should another code come into force meanwhile, the code is asked again
    const view = useE2EStore.getState().view!;
    act(() => useE2EStore.setState({ view: { ...view, recovery: { ...view.recovery!, public_key: "another" } } }));
    expect(within(group).getByLabelText("The recovery code, for this one to take its place")).toBeTruthy();
    act(() => useE2EStore.setState({ view }));
    const withCodeAgain = await within(group).findByRole("group", { name: "With the recovery code" });
    fireEvent.change(within(withCodeAgain).getByLabelText("The recovery code, for this one to take its place"), { target: { value: code } });
    fireEvent.click(button("Use the code", withCodeAgain));
    expect(await within(withCodeAgain).findByText("That is the recovery code: it goes with its proof.")).toBeTruthy();
    const signAgain = button("Sign it, with the recovery code", group);
    await vi.waitFor(() => expect(signAgain.disabled).toBe(false));
    expect(server.calls.some((c) => c.path === "/api/ade/e2e/revoke" || c.path === "/api/ade/e2e/recovery-proof")).toBe(false);
    auth.ceremonies.length = 0;
    fireEvent.click(signAgain);
    expect(await within(group).findByText(/^Signed\. It takes effect at/)).toBeTruthy();
    expect(auth.ceremonies[0].allow).toEqual(["credAAAA", "credBBBB"]);           // any passkey of the account, with the code
    expect(bodyOf("/api/ade/e2e/revoke")?.recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ change: "revoke", removes: ["pk-credBBBB"], madeBy: "pk-credAAAA", byCode: true, takes_effect_at: server.now + 86400 });
  });

  it("puts it in the waiting one's place with the recovery code this tab was unlocked with, nothing typed again", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-22", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {},
      removes: ["pk-credAAAA"], madeBy: "pk-credBBBB", byCode: true };
    render(<EncryptionPane />);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); await useE2EStore.getState().unlockWithCode(code); });
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    const withCode = await within(group).findByRole("group", { name: "With the recovery code" });
    expect(within(withCode).queryByLabelText("The recovery code, for this one to take its place")).toBeNull();
    expect(withCode.textContent).toContain("It goes with the recovery code this tab was unlocked with.");
    const sign = button("Sign it, with the recovery code", group);
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    fireEvent.click(sign);
    expect(await within(group).findByText(/^Signed\./)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/revoke")?.recovery_proof).toBeTruthy();
    expect(server.pending).toMatchObject({ removes: ["pk-credBBBB"], madeBy: "pk-credAAAA", byCode: true });
  });

  it("warns, before it is signed, that the passkeys a revocation removes can still cancel it when it leaves none but its signer, and offers the code, which only the code then cancels", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await unlock();
    render(<RevokeFromAccount what={{ remove: ["pk-credBBBB"] }} label="Removing Phone" words="Phone goes." onDone={async () => {}} onCancel={() => {}} />);
    let group = await screen.findByRole("group", { name: "Removing Phone" });
    const warning = "It removes Phone, leaving no passkey but the one that signs it, so until it takes effect Phone can still cancel it. Send it with the recovery code instead: it waits a day, and only the code cancels it then.";
    expect(within(group).getByRole("note").textContent).toContain(warning);
    const sign = button("Sign it", group);
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    expect(group.textContent).toContain("It waits a day before it takes effect, and can be cancelled until then.");     // it leaves its signer alone
    fireEvent.click(sign);
    await within(group).findByText(/Signed\./);
    expect((await K.E2EApi.view()).pending?.cancel_by).toEqual(["pk-credAAAA", "pk-credBBBB"]);     // as it warned
    cleanup();
    // with the code instead: only the code cancels it
    server.pending = null;
    render(<RevokeFromAccount what={{ remove: ["pk-credBBBB"] }} label="Removing Phone" words="Phone goes." onDone={async () => {}} onCancel={() => {}} />);
    group = await screen.findByRole("group", { name: "Removing Phone" });
    const note = within(group).getByRole("note");
    const withCode = button("Sign it, with the recovery code", group);
    expect(withCode.disabled).toBe(true);
    fireEvent.change(within(note).getByLabelText("The recovery code, so that only it cancels this"), { target: { value: code } });
    fireEvent.click(button("Use the code", note));
    await vi.waitFor(() => expect(withCode.disabled).toBe(false));
    fireEvent.click(withCode);
    await within(group).findByText(/Signed\./);
    expect((await K.E2EApi.view()).pending).toMatchObject({ by_code: true, cancel_by: [], cancel_by_code: true });
    cleanup();
    // one that leaves another passkey besides its signer, or that removes none, warns of nothing
    server.pending = null;
    await enrolled("credCCCC", "Key");
    await unlock();
    const cases: [K.RevokeWhat, string][] = [[{ remove: ["pk-credBBBB"] }, "Removing Phone"], [{ newCode: C.random(20) }, "A new code"]];
    for (const [what, label] of cases) {
      render(<RevokeFromAccount what={what} label={label} words="" onDone={async () => {}} onCancel={() => {}} />);
      const g = await screen.findByRole("group", { name: label });
      expect(within(g).queryByRole("note")).toBeNull();
      expect(within(g).queryByRole("button", { name: "Sign it, with the recovery code" })).toBeNull();
      cleanup();
    }
  });

  it("never shows a new master key as due while encryption is on, whatever the server says", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.answer = (call) => (call.method === "GET" && call.path === "/api/ade/e2e" ? [200, { ...server.view(), rekey_due: true }] : null);
    render(<EncryptionPane />);
    await unlock();
    await vi.waitFor(() => expect(useE2EStore.getState().view?.on).toBe(true));
    expect(useE2EStore.getState().view?.rekey_due).toBe(false);
    expect(screen.queryByText(/new master key/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn it on again" })).toBeNull();
  });

  it("names what stays after a revocation that keeps passkeys", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    expect(group.textContent).toContain("After it, these unlock encryption: Laptop, and the recovery code.");
  });

  it("while it is off, adds no passkey and makes no new recovery code, and turning it on again after a passkey was removed makes a new master key without it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await turnedOff();
    const removed = await server.fetch("/api/account/passkeys/pk-credBBBB", { method: "DELETE",
      body: JSON.stringify({ confirmation: await confirmation("credAAAA", "passkey-remove:pk-credBBBB") }) });
    expect(removed.status).toBe(200);
    passkey("credCCCC", "Key");
    render(<EncryptionPane />);
    await unlock();
    expect(await screen.findByText(/While encryption is off, a passkey is added to it, or a new recovery code made, once it is on again\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New recovery code" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Laptop" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    const words = await screen.findByText(/no longer unlocks encryption: removed, or left out by high security, while it was off/);
    expect(words.textContent).toContain("A passkey removed since no longer unlocks encryption");
    expect(words.textContent).toContain("Turning it on again makes a new master key without it, as replacing the keys does. After it, these unlock encryption: Laptop, and the recovery code.");
    const again = screen.getByRole("button", { name: "Turn it on again" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(again.disabled).toBe(false));
    fireEvent.click(again);
    await vi.waitFor(() => expect(server.pending?.change).toBe("turn-on"));
    const body = bodyOf("/api/ade/e2e/turn-on")!;
    expect(Object.keys(body).sort()).toEqual(["chain", "content_key", "content_keys", "grants", "mk_id", "roster", "seal", "statement"]);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credAAAA", "recovery"]);
    expect(await screen.findByText(/Turning encryption on waits until/)).toBeTruthy();
  });

  it("zeroes the new recovery code's bytes, which the revocation was made from, when its flow closes", async () => {
    await turnedOn("credAAAA", "Laptop");
    const used: Uint8Array[] = [];
    const prepare = K.Revoke.prototype.prepare;
    vi.spyOn(K.Revoke.prototype, "prepare").mockImplementation(function (this: K.Revoke) {
      if (this.what.newCode) used.push(this.what.newCode);
      return prepare.call(this);
    });
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "New recovery code" }));
    let group = await screen.findByRole("group", { name: "A new recovery code" });
    const fresh = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: fresh.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    group = await screen.findByRole("group", { name: "A new recovery code" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    fireEvent.click(button("Sign it", group));
    await within(group).findByText(/Signed\./);
    expect(used.length).toBeGreaterThan(0);
    expect(used.every((b) => C.same(b, C.recoveryBytes(fresh)!))).toBe(true);
    fireEvent.click(button("Close", group));
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: "A new recovery code" })).toBeNull());
    expect(used.every((b) => b.every((x) => x === 0))).toBe(true);
  });

  it("reads the keys again at once when a change is refused as made from keys older than this browser's floor, and locks when they do not lead back to it", async () => {
    await turnedOn("credAAAA", "Laptop");
    render(<EncryptionPane />);
    await unlock();
    const opened = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, recovery: server.recovery,
      grants: server.grants, chain: server.chain, roster: server.roster, contentKeys: server.contentKeys, passkeys: server.passkeys }));
    // another tab revokes and follows, raising this browser's floor; this tab is served the rows it opened
    const other = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(other, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await K.raiseFloor(ACCOUNT, await K.follow(other, await K.E2EApi.view()));
    Object.assign(server, opened);
    const nonces = server.calls.filter((c) => c.path === "/api/ade/link/nonce").length;
    fireEvent.click(await screen.findByRole("button", { name: "Rotate the key" }));
    await vi.waitFor(() => expect(useE2EStore.getState().ring).toBeNull());
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
    expect(await screen.findByText(/locked in this tab/)).toBeTruthy();
    expect(server.calls.filter((c) => c.path === "/api/ade/link/nonce")).toHaveLength(nonces);   // no statement prepared
  });

  it("reads the keys again at once when a change prepared before another tab raised the floor is refused at its click, before any touch", async () => {
    await turnedOn("credAAAA", "Laptop");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Rotate the key" }));
    const group = await screen.findByRole("group", { name: "Rotating the key" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    const opened = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, recovery: server.recovery,
      grants: server.grants, chain: server.chain, roster: server.roster, contentKeys: server.contentKeys, passkeys: server.passkeys }));
    const other = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(other, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await K.raiseFloor(ACCOUNT, await K.follow(other, await K.E2EApi.view()));
    Object.assign(server, opened);
    auth.ceremonies.length = 0;
    fireEvent.click(button("Sign it", group));
    await vi.waitFor(() => expect(useE2EStore.getState().ring).toBeNull());
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
    expect(auth.ceremonies).toHaveLength(0);
    expect(bodyOf("/api/ade/e2e/rotate")).toBeUndefined();
  });

  it("prepares cancelling again when who may cancel the change that waits changes, the same change still waiting", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-30", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    let by = ["pk-credAAAA"];
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: v.pending && { ...v.pending, cancel_by: by } }];
    };
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/A new content key waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("it takes a touch of Laptop, or the recovery code.");
    by = ["pk-credBBBB"];
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(waits.textContent).toContain("it takes a touch of Phone, or the recovery code.");
    const touch = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(touch.disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(touch);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    expect(auth.ceremonies.map((c) => c.allow)).toEqual([["credBBBB"]]);
  });

  it("makes a revocation again when who may cancel the revocation that waits changes, so it goes with the recovery code once only the code could", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-31", change: "revoke", takes_effect_at: server.now + 180, apply: () => {}, removes: ["pk-credBBBB"] };
    let listed = { cancel_by: ["pk-credAAAA"], cancel_by_code: true };
    server.answer = (call) => {
      if (call.method !== "GET" || call.path !== "/api/ade/e2e") return null;
      const v = server.view();
      return [200, { ...v, pending: v.pending && { ...v.pending, ...listed } }];
    };
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const flow = await screen.findByRole("group", { name: "Removing “Phone”" });
    await vi.waitFor(() => expect(button("Sign it", flow).disabled).toBe(false));
    expect(within(flow).queryByRole("group", { name: "With the recovery code" })).toBeNull();
    // the waiting one's cancellers change: no passkey that may sign this one could cancel it now, the code could
    listed = { cancel_by: [], cancel_by_code: true };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const withCode = within(flow).getByRole("group", { name: "With the recovery code" });
    expect(within(flow).queryByRole("button", { name: "Sign it" })).toBeNull();          // not without the code now
    const sign = button("Sign it, with the recovery code", flow);
    expect(sign.disabled).toBe(true);
    fireEvent.change(within(withCode).getByLabelText("The recovery code, for this one to take its place"), { target: { value: code } });
    fireEvent.click(button("Use the code", withCode));
    await vi.waitFor(() => expect(button("Sign it, with the recovery code", flow).disabled).toBe(false));
    fireEvent.click(button("Sign it, with the recovery code", flow));
    await within(flow).findByText(/Signed\./);
    expect(bodyOf("/api/ade/e2e/revoke")!.recovery_proof).toBeTruthy();
  });

  it("makes a revocation again when the server lists a credential under another id, so it seals to none the roster does not name", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await enrolled("credCCCC", "Key");
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Key" }));
    const flow = await screen.findByRole("group", { name: "Removing “Key”" });
    await vi.waitFor(() => expect(button("Sign it", flow).disabled).toBe(false));
    // Phone's credential listed under another id, with the key it had: not the one the roster names
    server.credentials.find((c) => c.credential_id === "credBBBB")!.credential_id = "credOTHER";
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect((await within(flow).findByRole("alert")).textContent).toBe(K.unnamedWords(["Phone"]));
  });

  it("makes renewing anchors again when the recovery code's anchor changes, so an anchor renewed elsewhere is not renewed again", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Renew anchors" }));
    const flow = screen.getByRole("group", { name: "Renewing anchors" });
    await within(flow).findByLabelText("The recovery code, to renew its anchor");
    // elsewhere, with the code: its anchor renewed
    const byCode = await K.unlockWithCode(await K.E2EApi.view(), ACCOUNT, code);
    const elsewhere = new K.RenewAnchors(byCode, await K.E2EApi.view());
    await elsewhere.prepare();
    await elsewhere.renewCode();
    await elsewhere.send();
    server.settle();
    const posts = server.calls.filter((c) => c.path === "/api/ade/e2e/anchors").length;
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const typed = await within(flow).findByLabelText("The recovery code, to renew its anchor");
    fireEvent.change(typed, { target: { value: code } });
    await vi.waitFor(() => expect(button("Renew the code's anchor", flow).disabled).toBe(false));
    fireEvent.click(button("Renew the code's anchor", flow));
    expect(await within(flow).findByText("The recovery code's anchor is renewed.")).toBeTruthy();
    fireEvent.click(button("Send", flow));
    expect(await within(flow).findByText("Every anchor renewed was current already.")).toBeTruthy();
    expect(server.calls.filter((c) => c.path === "/api/ade/e2e/anchors")).toHaveLength(posts);
  });

  it("says what made the change that waits, and what it removes", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-32", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {}, removes: ["pk-credAAAA"],
      removesCode: true, madeBy: "pk-credBBBB" };
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(waits.textContent).toContain("Made with Phone. It removes Laptop, the recovery code.");
    server.pending = { id: "hold-33", change: "rotate", takes_effect_at: server.now + 86400, apply: () => {}, madeBy: "recovery" };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const again = (await screen.findByText(/A new content key waits until/)).closest("div[role=status]") as HTMLElement;
    expect(again.textContent).toContain("Made with the recovery code: only the code cancels it.");
    expect(again.textContent).not.toContain("It removes");
    expect(again.textContent).not.toContain("Not yours? Remove the passkey that made it");             // no passkey made it
    // a passkey's change sent with the code's proof: the code alone cancels it too
    server.pending = { id: "hold-34", change: "rotate", takes_effect_at: server.now + 86400, apply: () => {}, madeBy: "pk-credBBBB", byCode: true };
    await act(() => useE2EStore.getState().load(ACCOUNT));
    const third = (await screen.findByText(/A new content key waits until/)).closest("div[role=status]") as HTMLElement;
    expect(third.textContent).toContain("Made with Phone and the recovery code's proof: only the code cancels it.");
    expect(third.textContent).toContain("Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code.");
  });

  it("checks the change that waits with the keys this tab unlocked, and again when they or its body change: it opens, is broken, or leaves this tab out; locked, it says unlocking checks it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const v = await K.E2EApi.view();
    auth.choose = (allowed) => (allowed.includes("credAAAA") ? "credAAAA" : allowed[0]);
    const r = new K.Revoke(await K.unlockWithTouch(v, ACCOUNT), v, { remove: ["pk-credBBBB"] });
    await r.prepare();
    await r.signAndSend();
    render(<EncryptionPane />);
    const waits = async () => (await screen.findByText(/Replacing the keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect((await waits()).textContent).toContain("Unlock encryption in this tab to check what it puts in place against your keys.");
    expect((await waits()).textContent).not.toContain("Checked with this tab's keys");
    await unlock();
    await vi.waitFor(async () => expect((await waits()).textContent).toContain(K.WAITING_OPENS));
    expect((await waits()).textContent).not.toContain("Unlock encryption in this tab");
    // its chain changed, as the server serves it: checked again, and broken
    server.pending!.body!.chain = C.b64u(C.random(80));
    await act(() => useE2EStore.getState().load(ACCOUNT));
    await vi.waitFor(async () => expect((await waits()).textContent).toContain(K.WAITING_BROKEN));
    // unlocked with the passkey it removes: this tab is left out
    act(() => useE2EStore.getState().lock());
    auth.choose = (allowed) => (allowed.includes("credBBBB") ? "credBBBB" : allowed[0]);
    await act(async () => { await useE2EStore.getState().unlock(); });
    await vi.waitFor(async () => expect((await waits()).textContent).toContain(K.WAITING_LEAVES));
    expect((await waits()).textContent).not.toContain(K.WAITING_BROKEN);
  });

  it("says turning it off goes with the recovery code while its statement is still being prepared, where only the code may", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-50", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery" };
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/ade/link/nonce") await held;
      return server.fetch(url, init);
    });
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(screen.getByRole("group", { name: "With the recovery code" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Sign turning it off$/ })).toBeNull();
    await act(async () => { release(); await held; });
    expect(await screen.findByRole("button", { name: /Sign turning it off, with the recovery code/ })).toBeTruthy();
  });

  it("does not warn that the passkeys a revocation removes may cancel it while it goes with the recovery code, which alone cancels it then", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.pending = { id: "hold-51", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "recovery" };
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    expect(await within(group).findByRole("group", { name: "With the recovery code" })).toBeTruthy();
    expect(group.textContent).not.toContain("can still cancel it");
    expect(within(group).queryByRole("button", { name: /^Sign it$/ })).toBeNull();
  });

  it("says nothing of a check for a change the keys open nothing of, locked or unlocked", async () => {
    await turnedOn("credAAAA", "Laptop");
    server.pending = { id: "hold-41", change: "anchors", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: { anchors: {} } };
    render(<EncryptionPane />);
    const waits = async () => (await screen.findByText(/Renewing anchors waits until/)).closest("div[role=status]") as HTMLElement;
    expect((await waits()).textContent).not.toContain("Unlock encryption in this tab to check");
    await unlock();
    await vi.waitFor(() => expect(useE2EStore.getState().ring).toBeTruthy());
    expect((await waits()).textContent).not.toContain("Checked with this tab's keys");
    expect((await waits()).textContent).not.toContain("Unlock encryption in this tab to check");
  });

  it("lists a passkey the server lists with another credential than the roster names as one the roster does not name, and says no cause it cannot know", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    render(<EncryptionPane />);
    await unlock();
    const row = () => screen.getByText("Phone").closest("li") as HTMLElement;
    await vi.waitFor(() => expect(row().textContent).toContain("Unlocks encryption"));
    // the same passkey, its credential listed with another key than the one the roster names
    server.credentials.find((c) => c.credential_id === "credBBBB")!.public_key = C.b64u((await C.newPair()).pub);
    await act(() => useE2EStore.getState().load(ACCOUNT));
    await vi.waitFor(() => expect(row().textContent).toContain("Not named by the roster of these keys: no new master key is sealed to it"));
    // the roster's word alone: no cause given
    expect(row().textContent).not.toMatch(/added without the master key/i);
  });

  it("says a revocation leaves as they are the content keys the server lists under a master key that does not lead back to yours", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const stranger = await C.masterOf(C.random(32), ACCOUNT);
    const pair = await C.newPair(true);
    const keyId = await C.keyIdOf(pair.pub);
    server.contentKeys.push({ key_id: keyId, public_key: C.b64u(pair.pub), mk_id: stranger.id, wrapped: await C.wrapContentKey(stranger, pair.priv, keyId, ACCOUNT) });
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const flow = await screen.findByRole("group", { name: "Removing “Phone”" });
    // beside the warning that Phone, removed, could still cancel it (it leaves Laptop alone)
    expect(within(flow).getAllByRole("note").map((n) => n.textContent).join(" ")).toContain("It leaves a content key as it is: the server lists it under a master key that does not lead back to yours");
    await vi.waitFor(() => expect(button("Sign it", flow).disabled).toBe(false));
    fireEvent.click(button("Sign it", flow));
    await within(flow).findByText(/Signed\./);
    expect(bodyOf("/api/ade/e2e/revoke")!.abandon).toEqual([keyId]);
  });

  /** A rotation through the keys module, its hold ended: the key it states. */
  async function rotated(): Promise<string> {
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    return bodyOf("/api/ade/e2e/rotate")!.content_key.key_id;
  }
  const notes = () => screen.getAllByRole("note").map((n) => n.textContent);

  it("says a content key that does not open, and when links seal to it, that no key is stated from these keys", async () => {
    await turnedOn("credAAAA", "Laptop");
    const first = (await K.E2EApi.view()).content_keys[0].key_id;
    const fresh = await rotated();
    server.contentKeys.find((k) => k.key_id === first)!.wrapped = C.b64u(C.random(80));
    render(<EncryptionPane />);
    await unlock();
    await vi.waitFor(() => expect(notes()).toContain("A content key the server lists under one of your master keys does not open: what was sealed to it is not opened here."));
    expect(useE2EStore.getState().ring?.keys.has(fresh)).toBe(true);
    expect(notes()).not.toContain(K.NEWEST_UNOPENED);
    // the newest, the key links seal to, its wrap no longer one either
    server.contentKeys.find((k) => k.key_id === fresh)!.wrapped = C.b64u(C.random(80));
    await act(() => useE2EStore.getState().load(ACCOUNT));
    await vi.waitFor(() => expect(notes()).toContain(K.NEWEST_UNOPENED));
    expect(useE2EStore.getState().ring?.unopened).toEqual([first, fresh]);
    fireEvent.click(button(/Rotate the key/));
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toBe(K.NEWEST_UNOPENED));
  });

  it("says a revocation leaves as it is a content key that does not open", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const first = (await K.E2EApi.view()).content_keys[0].key_id;
    await rotated();
    server.contentKeys.find((k) => k.key_id === first)!.wrapped = C.b64u(C.random(80));
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const flow = await screen.findByRole("group", { name: "Removing “Phone”" });
    expect(within(flow).getAllByRole("note").map((n) => n.textContent)).toContain("It leaves a content key as it is: it does not open, so it is not wrapped again, and what was sealed to it stays unopened here.");
    await vi.waitFor(() => expect(button("Sign it", flow).disabled).toBe(false));
    fireEvent.click(button("Sign it", flow));
    await within(flow).findByText(/Signed\./);
    expect(bodyOf("/api/ade/e2e/revoke")!.abandon).toEqual([first]);
  });

  it("with a roster that does not open: reads, says why, and offers no change that seals to it or rewrites it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    passkey("credCCCC", "Key");
    server.roster = { ...server.roster!, sealed: C.b64u(C.random(120)) };
    render(<EncryptionPane />);
    await unlock();
    await vi.waitFor(() => expect(notes()).toContain(K.ROSTER_NOT_OPEN));
    expect(stateOf(useE2EStore.getState())).toBe("unlocked");
    for (const name of ["Add", "Remove Laptop", "Remove Phone", "New recovery code", "Renew anchors"]) {
      expect(button(name).disabled).toBe(true);
      expect(button(name).title).toBe(K.ROSTER_NOT_OPEN);
    }
    expect(button(/Rotate the key/).disabled).toBe(false);
    // each passkey's line says nothing the roster would say
    expect(screen.getByText("Laptop").closest("li")!.textContent).toContain("Unlocks encryption");
    expect(document.body.textContent).not.toContain("Not named by the roster");
  });

  it("while it is off and a new master key is due, says nothing of whom it would keep from keys whose roster does not open", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    await turnedOff();
    server.passkeys = server.passkeys.filter((p) => p.credential_id !== "credBBBB");   // removed while it was off
    server.roster = { ...server.roster!, sealed: C.b64u(C.random(120)) };
    render(<EncryptionPane />);
    await unlock();
    expect((await K.E2EApi.view()).rekey_due).toBe(true);
    await vi.waitFor(() => expect(screen.getAllByRole("alert").map((a) => a.textContent)).toContain(K.ROSTER_NOT_OPEN));
    expect(document.body.textContent).not.toContain("After it, only the recovery code unlocks encryption");
    expect(document.body.textContent).not.toContain("Turning it on again makes a new master key");
  });

  it("from Account, says why a revocation is not made from keys whose roster does not open", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    server.roster = { ...server.roster!, sealed: C.b64u(C.random(120)) };
    await unlock();
    render(<RevokeFromAccount what={{ remove: ["pk-credBBBB"] }} label="Removing “Phone”" words="Removing it." onDone={async () => {}} onCancel={() => {}} />);
    const group = screen.getByRole("group", { name: "Removing “Phone”" });
    expect(group.textContent).toContain(K.ROSTER_NOT_OPEN);
    expect(within(group).queryByRole("button", { name: "Sign it" })).toBeNull();
  });

  it("renews the recovery code's anchor with nothing typed in a tab unlocked with the code, its proof confirming it", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();                                                   // the code's anchor is older than the master key now
    render(<EncryptionPane />);
    await act(async () => {
      await useE2EStore.getState().load(ACCOUNT);
      await useE2EStore.getState().unlockWithCode(code);
    });
    fireEvent.click(await screen.findByRole("button", { name: "Renew anchors" }));
    const flow = screen.getByRole("group", { name: "Renewing anchors" });
    const renew = await within(flow).findByRole("button", { name: "Recovery code" });
    expect(within(flow).queryByLabelText("The recovery code, to renew its anchor")).toBeNull();
    expect(flow.textContent).toContain("It waits 3 minutes before it takes effect");
    // before the code is given: what renewing its anchor costs
    expect(flow.textContent).toContain("Where the recovery code's anchor needs renewing, that goes only with the code's proof: the renewal then waits a day, and only the code cancels it.");
    fireEvent.click(renew);
    await vi.waitFor(() => expect(button("Send", flow).disabled).toBe(false));
    // the code's anchor renewed: the code's proof confirms it, and it waits the code's hold
    expect(flow.textContent).toContain("The recovery code's anchor is renewed only with the code's proof, so this goes with it: it waits a day, and only the code cancels it then.");
    expect(flow.textContent).not.toContain("It waits 3 minutes");
    fireEvent.click(button("Send", flow));
    await within(flow).findByText(/Signed\./);
    const body = bodyOf("/api/ade/e2e/anchors")!;
    expect(body.recovery_anchor.public_key).toBe(server.recovery!.public_key);
    expect(Object.keys(body.recovery_proof).sort()).toEqual(["challenge_id", "proof"]);
    server.settle();
    expect((await K.unlockWithCode(await K.E2EApi.view(), ACCOUNT, code)).via.anchor).toBe(server.roster!.mk_id);
  });

  it("renews the recovery code's anchor with the code typed beside the passkeys' touches in a tab unlocked with a passkey", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    render(<EncryptionPane />);
    await unlock();
    fireEvent.click(await screen.findByRole("button", { name: "Renew anchors" }));
    const flow = screen.getByRole("group", { name: "Renewing anchors" });
    const typed = await within(flow).findByLabelText("The recovery code, to renew its anchor");
    expect(within(flow).queryByRole("button", { name: "Recovery code" })).toBeNull();
    fireEvent.change(typed, { target: { value: "A".repeat(32) } });
    fireEvent.click(button("Renew the code's anchor", flow));
    expect((await within(flow).findByRole("alert")).textContent).toBe(K.WRONG_CODE);
    fireEvent.change(typed, { target: { value: code } });
    fireEvent.click(button("Renew the code's anchor", flow));
    expect(await within(flow).findByText("The recovery code's anchor is renewed.")).toBeTruthy();
    fireEvent.click(button("Laptop", flow));
    await vi.waitFor(() => expect(button("Laptop", flow).querySelector(".lucide-check")).toBeTruthy());   // touched
    await vi.waitFor(() => expect(button("Send", flow).disabled).toBe(false));
    // Laptop may confirm the change, but the code's anchor goes with the code's proof alone
    expect(flow.textContent).toContain("The recovery code's anchor is renewed only with the code's proof, so this goes with it: it waits a day, and only the code cancels it then.");
    fireEvent.click(button("Send", flow));
    await within(flow).findByText(/Signed\./);
    const body = bodyOf("/api/ade/e2e/anchors")!;
    expect(Object.keys(body).sort()).toEqual(["anchors", "mk_id", "recovery_anchor", "recovery_proof"]);
    expect(body.anchors.map((a: { passkey_id: string; confirmation?: string }) => [a.passkey_id, typeof a.confirmation])).toEqual([["pk-credAAAA", "string"]]);
    expect(server.pending).toMatchObject({ change: "anchors", madeBy: "recovery", byCode: true });
  });
});

// A read of the keys that comes back after a later one, held on the network while a change was made elsewhere, must
// not put the view from before that change back, nor an unlock open the keys from it; and the orders where a read
// fails, where two loads follow at once, where a load would lock, and where the account changes.
describe("a read of the keys that comes back after a later one", () => {
  it("keeps the newer view where an unlock's read comes back after it, and opens no key from before a revocation made meanwhile", async () => {
    await turnedOn("credAAAA", "Laptop");
    const before = server.roster!.mk_id;
    render(<EncryptionPane />);
    const unlockButton = await screen.findByRole("button", { name: "Unlock" });
    // the unlock's read, made once the touch is in, held on its way back: the keys as they were when it was asked
    const late = holdNextRead();
    fireEvent.click(unlockButton);
    await late.asked;
    // meanwhile a revocation made elsewhere, and the tab reads the keys as they are after it
    const after = await revokedElsewhere();
    expect(after).not.toBe(before);
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(useE2EStore.getState().view?.mk_id).toBe(after);
    late.release();
    expect(await screen.findByText(/Unlocked with Laptop\./)).toBeTruthy();
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id]).toEqual([after, after]);
  });

  it("keeps the newer view where a load's read comes back after a later load's", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const late = holdNextRead();
    const loading = useE2EStore.getState().load(ACCOUNT);             // a poll's read, held on its way back
    await late.asked;
    const after = await revokedElsewhere();
    await useE2EStore.getState().load(ACCOUNT);                        // a read begun later, answered first
    expect(useE2EStore.getState().view?.mk_id).toBe(after);
    late.release();
    await loading;
    expect(useE2EStore.getState().view?.mk_id).toBe(after);
  });

  it("leaves an unlocked tab on the keys it followed to where a load's read comes back after a later load's, the server not answering since", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const late = holdNextRead();
    const loading = useE2EStore.getState().load(ACCOUNT);             // a poll's read, held on its way back
    await late.asked;
    const after = await revokedElsewhere();
    await useE2EStore.getState().load(ACCOUNT);                        // followed: this browser's floor is the new key
    expect([useE2EStore.getState().ring?.master.id, K.floorOf(ACCOUNT)]).toEqual([after, [after]]);
    // the keys from before the revocation are older than this browser saw: following them fails, and with no read once
    // more to put it right, the tab would lock, saying they were put back. The late read sets nothing and the load
    // follows nothing from it: the later read answers for the keys
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    const follows = vi.spyOn(K, "follow");
    late.release();
    await loading;
    expect(follows).not.toHaveBeenCalled();
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why, stateOf(s)]).toEqual([after, after, "", "unlocked"]);
  });

  it("sets and follows what a load's read gives where a load begun after it failed: a read that fails holds no other back", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const after = await revokedElsewhere();
    const late = holdNextRead();
    const loading = useE2EStore.getState().load(ACCOUNT);             // a poll's read, of the keys after it, held
    await late.asked;
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    await useE2EStore.getState().load(ACCOUNT);                        // a focus's read, begun after it, fails
    server.answer = null;
    late.release();
    await loading;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([after, after, ""]);
  });

  it("sets and opens what an unlock read where a read begun after it is still on its way: newer than the view the tab held", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const after = await revokedElsewhere();                             // since the tab read the keys
    const unlockRead = holdNextRead();
    const unlocking = useE2EStore.getState().unlock();
    await unlockRead.asked;
    const pollRead = holdNextRead();
    const polling = useE2EStore.getState().load(ACCOUNT);             // begun after the unlock's read, still on its way
    await pollRead.asked;
    unlockRead.release();
    await unlocking;
    // the view is the keys the unlock opened, not the older one beside newer keys (which would read as keys put back)
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring?.master.id]).toEqual([after, after]);
    expect(K.rolledBack(ACCOUNT, useE2EStore.getState().view!)).toBeNull();
    pollRead.release();
    await polling;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([after, after, ""]);
  });

  it("sets and follows what a read begun before an unlock's gives where it comes back after the unlock's own read failed", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const after = await revokedElsewhere();                             // since the tab read the keys
    const pollRead = holdNextRead();
    const polling = useE2EStore.getState().load(ACCOUNT);             // a poll's read, of the keys after it, slow
    await pollRead.asked;
    // the unlock's own read fails: it opens the view the store holds, from before the revocation
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    await useE2EStore.getState().unlock();
    server.answer = null;
    // the poll's read, begun before the unlock's, comes back after it: a failed read held nothing back
    pollRead.release();
    await polling;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([after, after, ""]);
  });

  it.each(["unlockWithPrf", "unlockWithCode"] as const)("follows, once it holds the keys, the view a read set while they were opened (%s): a poll begun before the unlock's own read, which failed", async (opener) => {
    const code = await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const before = useE2EStore.getState().view!.mk_id;
    const after = await revokedElsewhere();                             // since the tab read the keys
    expect(after).not.toBe(before);
    const pollRead = holdNextRead();
    const polling = useE2EStore.getState().load(ACCOUNT);             // a poll's read, of the keys after it, slow
    await pollRead.asked;
    // the unlock's own read fails, and its opening of the view the store holds (from before the revocation) is held
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    let go!: () => void;
    const slow = new Promise<void>((r) => { go = r; });
    const unlockWithPrf = K.unlockWithPrf;
    const unlockWithCode = K.unlockWithCode;
    const opening = opener === "unlockWithPrf"
      ? vi.spyOn(K, "unlockWithPrf").mockImplementation(async (...a) => { await slow; return unlockWithPrf(...a); })
      : vi.spyOn(K, "unlockWithCode").mockImplementation(async (...a) => { await slow; return unlockWithCode(...a); });
    const unlocking = opener === "unlockWithPrf" ? useE2EStore.getState().unlock() : useE2EStore.getState().unlockWithCode(code);
    await vi.waitFor(() => expect(opening).toHaveBeenCalled());
    server.answer = null;
    // the poll's read comes back while the keys are being opened: set, with no keys held yet to follow from
    pollRead.release();
    await polling;
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring]).toEqual([after, null]);
    // the unlock holds the keys from before the revocation, then follows the view set meanwhile
    go();
    await unlocking;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([after, after, ""]);
  });

  it("reads the keys once for an unlock where no read set other keys while it opened them", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const through = globalThis.fetch;
    let reads = 0;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/ade/e2e" && (init?.method ?? "GET") === "GET") reads += 1;
      return through(url, init);
    });
    await useE2EStore.getState().unlock();
    expect([reads, stateOf(useE2EStore.getState())]).toEqual([1, "unlocked"]);
  });

  it("opens the view a read gave while the touch was given where the unlock's own read fails, not the view it began with", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const after = await revokedElsewhere();                             // since the tab read the keys
    // the touch held until the tab has read the keys again
    let touch!: () => void;
    const touched = new Promise<void>((r) => { touch = r; });
    const get = navigator.credentials.get;
    vi.spyOn(navigator.credentials, "get").mockImplementation(async (o) => { await touched; return get(o); });
    const unlocking = useE2EStore.getState().unlock();
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().view?.mk_id).toBe(after);
    // then the unlock's own read fails
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    touch();
    await unlocking;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id]).toEqual([after, after]);
  });

  it("sets no view from a load's read once more where a later load has set a newer one, and puts away what it followed to from it: the later load's follow is held", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = server.view();
    await revokedElsewhere();
    const after = server.view();
    // the load's first read torn (the master key from before, named with the chain after it): following it fails, and
    // it reads once more, that read held on its way back
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    let once: ReturnType<typeof holdNextRead> | null = null;
    server.answer = (call) => {
      if (call.path !== "/api/ade/e2e" || once) return null;
      once = holdNextRead();
      return [200, torn];
    };
    const loading = useE2EStore.getState().load(ACCOUNT);
    await vi.waitFor(() => expect(once).not.toBeNull());
    await once!.asked;
    // another revocation, which a later load reads and sets; its follow held until the first load is done
    const third = await revokedElsewhere();
    let go!: () => void;
    const slow = new Promise<void>((r) => { go = r; });
    const follow = K.follow;
    vi.spyOn(K, "follow").mockImplementation(async (ring, view, floor) => {
      if (view.mk_id === third) await slow;
      return follow(ring, view, floor);
    });
    const later = useE2EStore.getState().load(ACCOUNT);
    await vi.waitFor(() => expect(useE2EStore.getState().view?.mk_id).toBe(third));
    once!.release();
    await loading;
    // the first load's follow from its read once more, older than the later read, is put away: the tab holds what it held
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring?.master.id]).toEqual([third, before.mk_id]);
    go();
    await later;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([third, third, ""]);
  });

  it("holds the follow of the read begun last that gave the keys, whichever ends first: a load's own follow that ends after a later load set its view is put away", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = useE2EStore.getState().ring!.master.id;
    // each load's follow held until it is let go
    const go = new Map<string, () => void>();
    const follow = K.follow;
    vi.spyOn(K, "follow").mockImplementation(async (ring, view, floor) => {
      await new Promise<void>((r) => go.set(String(view.mk_id), r));
      return follow(ring, view, floor);
    });
    const first = await revokedElsewhere();
    const loading = useE2EStore.getState().load(ACCOUNT);
    await vi.waitFor(() => expect(go.has(first)).toBe(true));
    const second = await revokedElsewhere();
    const later = useE2EStore.getState().load(ACCOUNT);             // begun after, its view set while the first follows
    await vi.waitFor(() => expect(go.has(second)).toBe(true));
    // the first load's follow ends first: put away, since the later load has set a view; the tab holds what it held
    go.get(first)!();
    await loading;
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring?.master.id]).toEqual([second, before]);
    go.get(second)!();
    await later;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why]).toEqual([second, second, ""]);
  });

  it("counts a load's read once more as begun after a read still on its way, so the newer of the two is the view set and the keys followed", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = server.view();
    const second = await revokedElsewhere();
    const after = server.view();
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    let first = true;
    server.answer = (call) => (call.path === "/api/ade/e2e" && first ? ((first = false), [200, torn]) : null);
    // as the load follows its torn read, a poll begins a read, held on its way back; another revocation is made, and
    // the load's read once more, begun after the poll's, is held too. The poll's follow is held as well
    let third = "";
    let poll: Promise<void> = Promise.resolve();
    let pollRead: ReturnType<typeof holdNextRead> | null = null;
    let onceRead: ReturnType<typeof holdNextRead> | null = null;
    let go!: () => void;
    const slow = new Promise<void>((r) => { go = r; });
    const follow = K.follow;
    vi.spyOn(K, "follow").mockImplementation(async (ring, view, floor) => {
      if (view.mk_id === before.mk_id && !pollRead) {
        pollRead = holdNextRead();
        poll = useE2EStore.getState().load(ACCOUNT);
        await pollRead.asked;
        third = await revokedElsewhere();
        onceRead = holdNextRead();
      } else if (view.mk_id === second) await slow;
      return follow(ring, view, floor);
    });
    const loading = useE2EStore.getState().load(ACCOUNT);
    await vi.waitFor(() => expect(onceRead).not.toBeNull());
    await onceRead!.asked;
    // the poll's read comes back first, from before the third key: set, its follow on its way
    pollRead!.release();
    await vi.waitFor(() => expect(useE2EStore.getState().view?.mk_id).toBe(second));
    // then the load's read once more, newer: set over it, and followed
    onceRead!.release();
    await loading;
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring?.master.id]).toEqual([third, third]);
    go();
    await poll;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why, stateOf(s)]).toEqual([third, third, "", "unlocked"]);
  });

  it("keeps the view a load's read once more set where a read begun before it comes back after it: that read, older, sets nothing and locks nothing", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = server.view();
    await revokedElsewhere();
    const after = server.view();
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    let first = true;
    server.answer = (call) => (call.path === "/api/ade/e2e" && first ? ((first = false), [200, torn]) : null);
    // as the load follows its torn read, a poll begins a read, held on its way back, and another revocation is made
    let third = "";
    let poll: Promise<void> = Promise.resolve();
    let pollRead: ReturnType<typeof holdNextRead> | null = null;
    const follow = K.follow;
    vi.spyOn(K, "follow").mockImplementation(async (ring, view, floor) => {
      if (view.mk_id === before.mk_id && !pollRead) {
        pollRead = holdNextRead();
        poll = useE2EStore.getState().load(ACCOUNT);
        await pollRead.asked;
        third = await revokedElsewhere();
      }
      return follow(ring, view, floor);
    });
    // the load's read once more, begun after the poll's, comes back first: set, and followed
    await useE2EStore.getState().load(ACCOUNT);
    expect([useE2EStore.getState().view?.mk_id, useE2EStore.getState().ring?.master.id]).toEqual([third, third]);
    // then the poll's, from before the third key, the server answering no read once more of its own
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
    pollRead!.release();
    await poll;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why, stateOf(s)]).toEqual([third, third, "", "unlocked"]);
  });

  it("does not lock where a load's follow and its read once more fail, a read begun after it having set a view since: that read answers for the keys", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = server.view();
    const second = await revokedElsewhere();
    const after = server.view();
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    let first = true;
    server.answer = (call) => (call.path === "/api/ade/e2e" && first ? ((first = false), [200, torn]) : null);
    // as the load follows its torn read, a poll reads the keys after the revocation and sets them, its follow held;
    // then the load's read once more fails
    let poll: Promise<void> | null = null;
    let go!: () => void;
    const slow = new Promise<void>((r) => { go = r; });
    const follow = K.follow;
    vi.spyOn(K, "follow").mockImplementation(async (ring, view, floor) => {
      if (view.mk_id === before.mk_id && !poll) {
        poll = useE2EStore.getState().load(ACCOUNT);
        await vi.waitFor(() => expect(useE2EStore.getState().view?.mk_id).toBe(second));
        server.answer = (call) => (call.path === "/api/ade/e2e" ? [500, { detail: "down" }] : null);
      } else if (view.mk_id === second) await slow;
      return follow(ring, view, floor);
    });
    await useE2EStore.getState().load(ACCOUNT);
    expect([stateOf(useE2EStore.getState()), useE2EStore.getState().why]).toEqual(["unlocked", ""]);
    go();
    await poll;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.why, stateOf(s)]).toEqual([second, second, "", "unlocked"]);
  });

  it("gives an unlock begun before another account signed in no view of that account", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const ours = useE2EStore.getState().view!.mk_id;
    const opened: (string | null)[] = [];
    const unlockWithPrf = K.unlockWithPrf;
    vi.spyOn(K, "unlockWithPrf").mockImplementation(async (view, ...rest) => { opened.push(view.mk_id); return unlockWithPrf(view, ...rest); });
    const late = holdNextRead();
    const unlocking = useE2EStore.getState().unlock();
    await late.asked;
    // another account signed in meanwhile, its keys read and set
    const theirs = "e".repeat(32);
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [200, { ...server.view(), mk_id: theirs }] : null);
    await useE2EStore.getState().load("acct-2");
    server.answer = null;
    late.release();
    await unlocking.catch(() => undefined);
    expect(opened).toEqual([ours]);
    const s = useE2EStore.getState();
    expect([s.account, s.view?.mk_id, s.ring]).toEqual(["acct-2", theirs, null]);
  });

  it.each(["the account the unlock was for", "the account signed in since"] as const)("gives an unlock whose touch is answered after another account signed in no view read since, and sets none: the server answering for %s", async (answeredFor) => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const ours = useE2EStore.getState().view!.mk_id;
    const opened: (string | null)[] = [];
    const unlockWithPrf = K.unlockWithPrf;
    vi.spyOn(K, "unlockWithPrf").mockImplementation(async (view, ...rest) => { opened.push(view.mk_id); return unlockWithPrf(view, ...rest); });
    // the touch held while another account signs in and its keys are read and set: the unlock's read begins after
    let touch!: () => void;
    const touched = new Promise<void>((r) => { touch = r; });
    const get = navigator.credentials.get;
    vi.spyOn(navigator.credentials, "get").mockImplementation(async (o) => { await touched; return get(o); });
    const unlocking = useE2EStore.getState().unlock();
    const theirs = "e".repeat(32);
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [200, { ...server.view(), mk_id: theirs }] : null);
    await useE2EStore.getState().load("acct-2");
    if (answeredFor === "the account the unlock was for") server.answer = null;
    touch();
    await unlocking.catch(() => undefined);
    expect(opened).toEqual([ours]);
    const s = useE2EStore.getState();
    expect([s.account, s.view?.mk_id, s.ring]).toEqual(["acct-2", theirs, null]);
  });

  it("keeps no view that a read begun before a sign-out comes back with after it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const late = holdNextRead();
    const unlocking = useE2EStore.getState().unlock();
    await late.asked;
    useE2EStore.getState().reset(null);                               // signed out while the unlock reads the keys
    late.release();
    await unlocking;
    const s = useE2EStore.getState();
    expect([s.account, s.view, s.ring, stateOf(s)]).toEqual([null, null, null, "off"]);
  });

  it("keeps no view that a read begun before a sign-out comes back with after the same account signed in again", async () => {
    await turnedOn("credAAAA", "Laptop");
    await useE2EStore.getState().load(ACCOUNT);
    const late = holdNextRead();
    const loading = useE2EStore.getState().load(ACCOUNT);             // a poll's read, held on its way back
    await late.asked;
    useE2EStore.getState().reset(null);                               // signed out
    const after = await revokedElsewhere();                             // the keys changed meanwhile
    const again = holdNextRead();
    const signingIn = useE2EStore.getState().load(ACCOUNT);           // signed in again, its read held too
    await again.asked;
    // the read from before the sign-out comes back first: it sets nothing for the account signed in again
    late.release();
    await loading;
    expect([useE2EStore.getState().account, useE2EStore.getState().view]).toEqual([ACCOUNT, null]);
    again.release();
    await signingIn;
    expect(useE2EStore.getState().view?.mk_id).toBe(after);
  });

  it("opens the keys this tab made only from the read that set the view: a read a later load overtook opens nothing, though the tab still waits on them", async () => {
    passkey("credPHON", "Phone");
    await useE2EStore.getState().load(ACCOUNT);
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON"));
    const anchor = useE2EStore.getState().waiting!.anchor;
    server.settle();                                                   // the keys this tab made take effect
    const late = holdNextRead();
    const loading = useE2EStore.getState().load(ACCOUNT);             // a poll's read, showing them in effect, held
    await late.asked;
    // a later load sets its view first and opens them, that opening held: the tab still waits on them
    let go!: () => void;
    const slow = new Promise<void>((r) => { go = r; });
    const openRing = K.openRing;
    const opens = vi.spyOn(K, "openRing").mockImplementationOnce(async (...a) => { await slow; return openRing(...a); });
    const later = useE2EStore.getState().load(ACCOUNT);
    await vi.waitFor(() => expect(opens).toHaveBeenCalledTimes(1));
    expect(useE2EStore.getState().waiting?.anchor).toBe(anchor);
    late.release();
    await loading;
    expect(opens).toHaveBeenCalledTimes(1);                            // the overtaken read opens nothing
    go();
    await later;
    const s = useE2EStore.getState();
    expect([s.view?.mk_id, s.ring?.master.id, s.waiting, s.why]).toEqual([anchor, anchor, null, ""]);
  });
});

describe("how long a change waits, in words", () => {
  it("says a hold's length as the server gives it, in its largest whole unit", () => {
    expect([180, 86400, 172800, 3600, 7200, 60, 90, 1, 0, 90000, 86460].map(lengthWords)).toEqual(
      ["3 minutes", "a day", "2 days", "an hour", "2 hours", "a minute", "90 seconds", "a second", "0 seconds", "25 hours", "1441 minutes"]);
    expect(lengthWords(undefined)).toBeNull();
    expect(lengthWords(-1)).toBeNull();
  });

  it("says before a change is signed how long it waits on which hold, or that it waits on its hold where the server did not say", () => {
    const view = K.readView({ holds: { change: 180, recovery: 86400, long: 3600 } });
    expect(waitWords(view, "change")).toBe("It waits 3 minutes before it takes effect, and can be cancelled until then.");
    expect(waitWords(view, "long")).toBe("It waits an hour before it takes effect, and can be cancelled until then.");
    expect(waitWords(view, "recovery")).toBe("It waits a day before it takes effect, and can be cancelled until then.");
    expect(waitWords(K.readView({}), "change")).toBe("It waits on its hold before it takes effect, and can be cancelled until then.");
    // a length that is not a number of seconds is not taken as one
    for (const change of ["180", -5, Number.NaN, null, true]) {
      expect(waitWords(K.readView({ holds: { change } }), "change")).toBe("It waits on its hold before it takes effect, and can be cancelled until then.");
    }
  });
});

describe("how many devices a change's notice went toward, in words", () => {
  it("says the count the server gave, in the words a hold's card on the Sessions page gives, and nothing for no whole number", () => {
    expect(noticeWords(1)).toBe("Its notice was sent toward 1 device with notifications on.");
    expect(noticeWords(3)).toBe("Its notice was sent toward 3 devices with notifications on.");
    expect(noticeWords(0)).toBe("No device of this account has notifications on: only this page shows it.");
    expect(noticeWords(-1)).toBe("Its notice could not be sent. Only this page shows it.");
    for (const n of [-1, 0, 1, 2, 7]) expect(noticeWords(n)).toBe(toldWords(n));
    for (const told of [undefined, null, "2", 1.5, Number.NaN, true]) expect(noticeWords(told)).toBe("");
  });

  it("says after a change is signed how many devices were told, and never that every device was", () => {
    const at = Date.now() / 1000 + 180;
    const words = (told: unknown) => holdWords({ hold: { id: "h", takes_effect_at: at }, told });
    expect(words(2)).toMatch(/^It takes effect at .+, unless you cancel it first\. Its notice was sent toward 2 devices with notifications on\.$/);
    expect(words(0)).toMatch(/unless you cancel it first\. No device of this account has notifications on: only this page shows it\.$/);
    expect(words(undefined)).toMatch(/^It takes effect at .+, unless you cancel it first\.$/);
    for (const told of [2, 0, -1, undefined]) expect(words(told)).not.toMatch(/every device/i);
  });

  it("says in the change that waits how many devices its hold says were told, and nothing when it kept no count", async () => {
    await turnedOn("credAAAA", "Laptop");
    const shown = async (told: number | null) => {
      server.pending = { id: "hold-9", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, ...(told === null ? {} : { told }) };
      render(<EncryptionPane />);
      const text = (await screen.findByText(/A new content key waits until/)).textContent!;
      cleanup();
      return text;
    };
    expect(await shown(3)).toMatch(/waits until .+\. Its notice was sent toward 3 devices with notifications on\. Cancel it if it is not yours\.$/);
    expect(await shown(0)).toMatch(/\. No device of this account has notifications on: only this page shows it\. Cancel it if it is not yours\.$/);
    expect(await shown(-1)).toContain("Its notice could not be sent. Only this page shows it.");
    expect(await shown(null)).toMatch(/waits until [^.]+\. Cancel it if it is not yours\.$/);
    for (const told of [3, 0, -1, null]) expect(await shown(told)).not.toMatch(/every device/i);
  });
});

describe("what a link holds, in words", () => {
  const newest = { seq: 3, id: "x", purpose: "e2e-key" as const, key_id: "k".repeat(32), payload: {}, passkey_id: "pk-1", created_at: 1 };
  const passkeys = [{ passkey_id: "pk-1", credential_id: "c1", name: "Laptop", created_at: 1, fingerprint: "1".repeat(32) },
    { passkey_id: "pk-2", credential_id: "c2", name: "Key", created_at: 2, fingerprint: "2".repeat(32) }];
  it("says a link that pinned the newest key's signer takes it at its next run", () => {
    const w = linkWords({ link_id: "L", name: "n", key_id: "o".repeat(32), last_required_at: null, pins: ["1".repeat(32)] }, newest, passkeys);
    expect(w.advice).toBe("It pinned Laptop, which signed the newest key: it takes it at its next run.");
  });
  it("gives no advice to a link that seals to the newest key", () => {
    expect(linkWords({ link_id: "L", name: "n", key_id: "k".repeat(32), last_required_at: 5, pins: [] }, newest, passkeys)).toEqual({ state: "Seals to the newest key.", advice: null });
  });

  // a link that has not reported its pins (null) is not one that pins none, and pins are not matched
  // to passkeys the page could not read
  const link = (pins: string[] | null) => ({ link_id: "L", name: "n", key_id: "o".repeat(32), last_required_at: null, pins });
  it("tells a link that has not reported its pins from one that pins none, and matches no pin to passkeys it could not read", () => {
    const one = ["1".repeat(32)], two = ["1".repeat(32), "f".repeat(32)];
    expect(pinsWords(link(null), passkeys)).toBe("Pins: not reported yet.");
    expect(pinsWords(link([]), passkeys)).toBe("Pins: none of your passkeys.");
    expect(pinsWords(link(two), passkeys)).toBe("Pins: Laptop; 1 key this account no longer has.");
    expect(pinsWords(link(two), passkeys, "failed")).toBe("Pins: 2 keys, not matched: this account's passkeys could not be read.");
    expect(pinsWords(link(one), passkeys, "reading")).toBe("Pins: 1 key, being matched to your passkeys.");
    expect(pinsWords(link([]), passkeys, "failed")).toBe("Pins: none of your passkeys.");
    expect(pinsWords(link(null), passkeys, "failed")).toBe("Pins: not reported yet.");
  });

  it("advises on a link's pins only once it reported them and the account's passkeys were read", () => {
    const one = ["1".repeat(32)];
    expect(linkWords(link(null), newest, passkeys).advice).toBe("It has not reported which passkeys it pinned yet, so which key it can take is not known.");
    expect(linkWords(link(one), newest, passkeys, "failed").advice).toBe("Its pins could not be matched to your passkeys, which could not be read: reload this page to try again.");
    expect(linkWords(link(one), newest, passkeys, "reading").advice).toBeNull();
    expect(linkWords(link(one), newest, passkeys).advice).toBe("It pinned Laptop, which signed the newest key: it takes it at its next run.");
    expect(linkWords(link([]), newest, passkeys, "failed").advice).toBe("It pinned none of your passkeys: give it a pin line (Passkeys, in Account) in its own window.");
    expect(linkWords(link(["f".repeat(32)]), newest, passkeys).advice).toBe("It pinned none of your passkeys: give it a pin line (Passkeys, in Account) in its own window.");
  });

  it("keeps a link's pins as the server serves them: null until it reports them, never turned into none", () => {
    const pinsOf = (pins: unknown) => K.readView({ links: [{ link_id: "L", name: "n", key_id: null, last_required_at: null, pins }] }).links[0].pins;
    expect(pinsOf(null)).toBeNull();
    expect(pinsOf(undefined)).toBeNull();
    expect(pinsOf([])).toEqual([]);
    expect(pinsOf(["a"])).toEqual(["a"]);
    expect(pinsOf("a")).toEqual([]);
  });
});

describe("the Account tab while encryption is on", () => {
  /** The dialog in a browser that makes passkeys, from modules loaded afresh (passkeysHere is read as they load). */
  const fresh = async () => {
    vi.resetModules();
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    auth.install();
    const store = await import("../store");
    store.useStore.setState({ user: owner });
    const e2e = await import("../e2e");
    const { AccountDialog } = await import("./AccountDialog");
    await act(() => e2e.useE2EStore.getState().load(ACCOUNT));
    return { e2e, AccountDialog };
  };
  afterEach(() => { vi.resetModules(); });

  it("removes a passkey only by a revocation: locked, it says to unlock first and asks nothing of the server", async () => {
    await turnedOn("credAAAA", "Laptop");
    const { AccountDialog } = await fresh();
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const group = await screen.findByRole("group", { name: "Removing “Laptop”" });
    expect(group.textContent).toContain("End-to-end encryption is on: removing “Laptop” replaces its keys, so unlock it first");
    expect(server.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(server.calls.some((c) => c.path === "/api/account/confirm/options")).toBe(false);
  });

  it("says beside Add a passkey that one added while a change to the keys waits takes its place and ends it, an upgrade by name", async () => {
    const said = async () => {
      const { AccountDialog } = await fresh();
      render(<AccountDialog onClose={() => {}} />);
      await screen.findByRole("button", { name: "Add a passkey" });
      const note = screen.queryByText(/takes its place and ends it/);
      cleanup();
      return note?.textContent ?? null;
    };
    const rest = "waits: a passkey added now takes its place and ends it, where what confirms the adding may cancel it, and is refused otherwise. To keep it, add the passkey once it has taken effect.";
    // encryption off, a turn-on waiting: a passkey added is not held (e2e_keys.hold_passkey is for encryption on)
    server.pending = { id: "hold-t", change: "turn-on", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    expect(await said()).toBeNull();
    server.pending = null;
    await turnedOn("credAAAA", "Laptop");
    // the server's rule (v): e2e_keys.hold_passkey takes the place of the change that waits, or is refused
    server.pending = { id: "hold-u", change: "revoke", takes_effect_at: server.now + 86400, apply: () => {}, madeBy: "pk-credAAAA", body: { upgrade: true } };
    expect(await said()).toBe(`The upgrade to post-quantum keys ${rest}`);
    // each other change named as the change that waits is named where it is shown (changeName), not all as one
    const named: [string, string][] = [["rotate", "A new content key"], ["revoke", "Replacing the keys"], ["credentials", "A passkey added to encryption"],
      ["off", "Turning encryption off"], ["anchors", "Renewing anchors"], ["passkey", "A passkey added to your account"],
      ["upload", "An upload of statements from a linked session"]];
    for (const [change, name] of named) {
      server.pending = { id: `hold-${change}`, change, takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
      expect(await said()).toBe(`${name} ${rest}`);
    }
    server.pending = null;
    expect(await said()).toBeNull();
  });

  it("removes a passkey by a revocation once unlocked, never the DELETE", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Phone");
    const { e2e, AccountDialog } = await fresh();
    await act(() => e2e.useE2EStore.getState().unlock());
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Phone" }));
    const group = await screen.findByRole("group", { name: "Removing “Phone”" });
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    fireEvent.click(button("Sign it", group));
    await within(group).findByText(/Signed\./);
    expect(bodyOf("/api/ade/e2e/revoke")?.remove).toEqual(["pk-credBBBB"]);
    expect(server.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("turns high security on by a revocation that a hardware key signs, keeping the hardware keys alone", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Security key", "hardware");
    const { e2e, AccountDialog } = await fresh();
    await act(() => e2e.useE2EStore.getState().unlock());
    render(<AccountDialog onClose={() => {}} />);
    const on = await screen.findByRole("button", { name: "Turn it on" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(on.disabled).toBe(false));
    fireEvent.click(on);
    const group = await screen.findByRole("group", { name: "Turning high security on" });
    expect(group.textContent).toContain("Your other passkeys no longer unlock encryption");
    await vi.waitFor(() => expect(button("Sign it", group).disabled).toBe(false));
    auth.ceremonies.length = 0;
    fireEvent.click(button("Sign it", group));
    await within(group).findByText(/Signed\./);
    expect(auth.ceremonies[0].allow).toEqual(["credBBBB"]);
    const body = bodyOf("/api/ade/e2e/revoke")!;
    expect(body.high_security).toBe(true);
    expect(body.grants.map((g: Body) => g.to).sort()).toEqual(["credBBBB", "recovery"]);
    expect(server.calls.some((c) => c.path === "/api/account/high-security")).toBe(false);
  });

  it("turning high security on with no hardware key that unlocks encryption offers to add one first, in Encryption", async () => {
    await turnedOn("credAAAA", "Laptop");
    passkey("credHHHH", "Security key", "hardware");                   // the account's, not added to encryption
    const { e2e, AccountDialog } = await fresh();
    await act(() => e2e.useE2EStore.getState().unlock());
    render(<AccountDialog onClose={() => {}} />);
    const on = await screen.findByRole("button", { name: "Turn it on" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(on.disabled).toBe(false));
    fireEvent.click(on);
    const group = await screen.findByRole("group", { name: "Turning high security on" });
    expect((await within(group).findByRole("alert")).textContent).toBe(K.NO_HARDWARE_KEPT);
    expect(button("Sign it", group).disabled).toBe(true);
    fireEvent.click(button("Add a hardware key to encryption", group));
    expect(await screen.findByText("What unlocks it")).toBeTruthy();
  });

  it("while encryption is off and keeps its keys, removes a passkey and turns high security on as Account does, saying what that does to the keys", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Security key", "hardware");
    await turnedOff();
    const { AccountDialog } = await fresh();
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const group = await screen.findByRole("group", { name: "Removing “Laptop”" });
    expect(group.textContent).toContain(OFF_KEYS_WORDS);
    expect(within(group).getByLabelText("Your password")).toBeTruthy();        // encryption is off: as Account confirms any change
    fireEvent.click(button("Cancel", group));
    const on = await screen.findByRole("button", { name: "Turn it on" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(on.disabled).toBe(false));
    fireEvent.click(on);
    expect((await screen.findByRole("group", { name: "Turning high security on" })).textContent).toContain(OFF_HIGH_WORDS);
    expect(server.calls.some((c) => c.path === "/api/ade/e2e/revoke")).toBe(false);
  });

  it("while encryption is off, remembers in this browser a passkey it removes, with its key, so the next master key leaves it", async () => {
    await turnedOn("credAAAA", "Laptop");
    await enrolled("credBBBB", "Security key", "hardware");
    await turnedOff();
    const key = server.credentials.find((c) => c.credential_id === "credAAAA")!.public_key;
    const { AccountDialog } = await fresh();
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const group = await screen.findByRole("group", { name: "Removing “Laptop”" });
    auth.choose = () => "credBBBB";
    fireEvent.click(within(group).getByRole("button", { name: /Use a passkey/ }));
    await vi.waitFor(() => expect(server.passkeys.map((p) => p.id)).toEqual(["pk-credBBBB"]));
    await vi.waitFor(() => expect(K.removedHere(ACCOUNT)).toEqual([{ passkey_id: "pk-credAAAA", credential_id: "credAAAA", public_key: key }]));
  });

  it("adds a passkey with a passkey's word or the recovery code's, never the password", async () => {
    const code = await turnedOn("credAAAA", "Laptop");
    const { AccountDialog } = await fresh();
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a passkey" }));
    const group = await screen.findByRole("group", { name: "Adding a passkey" });
    expect(group.textContent).toContain("takes one of your passkeys or your recovery code as well as this sign-in, since end-to-end encryption is on: your password does not confirm it.");
    expect(within(group).queryByLabelText("Your password")).toBeNull();
    expect(within(group).getByRole("button", { name: /Use a passkey/ })).toBeTruthy();
    // a notice goes only to devices with notifications on (the same words in the Account dialog)
    expect(group.textContent).toContain("What the recovery code confirms waits a day before it is made, and each device of yours with notifications on is told.");
    expect(group.textContent).not.toContain("every device of yours is told");
    fireEvent.change(within(group).getByLabelText("Your recovery code"), { target: { value: code } });
    fireEvent.click(within(group).getByRole("button", { name: "Use the recovery code" }));
    expect(await screen.findByText(/Confirmed\. Now make the passkey on this device\./)).toBeTruthy();
    expect(server.calls.find((c) => c.path === "/api/ade/e2e/recovery-proof")?.body).toEqual({ purpose: "passkey" });
    expect(Object.keys(bodyOf("/api/account/passkeys/options")!)).toEqual(["recovery_proof"]);
  });
});
