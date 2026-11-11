// The Encryption panel's post-quantum section: classical or post-quantum
// said, the texts still stored classical counted, the Upgrade screen end to end against the test server, a view put
// back to classical where this browser saw it post-quantum, and one with no keys at all where it saw the account's.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "../e2eCrypto";
import * as K from "../e2eKeys";
import { useE2EStore } from "../e2e";
import { confirmWithPasskey } from "../passkey";
import { CLASSICAL, FakeAuthenticator, FakeServer } from "../e2eTestkit";
import { useStore } from "../store";
import { classicalTextsWords, CLASSICAL_WORDS, droppedWords, EncryptionPane, leftWords, NEW_CODE_WORDS, notPqWords, PQ_WORDS, RESEALED_DISK, RESTORED_TURN_ON, UPGRADE_ADD_AFTER,
  UPGRADE_BELOW, UPGRADE_CANCEL_WORDS, UPGRADE_MEANWHILE, UPGRADE_OTHER_TABS, UPGRADE_REMOVES_NOW, UPGRADE_WAITS_ABOVE } from "./Encryption";

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
const prefer = (id: string) => (allowed: string[]) => (allowed.includes(id) ? id : allowed[0]);

function passkey(id: string, name: string) {
  server.addPasskey(id, name);
  auth.add(id);
}

/** Encryption turned on with `id`'s passkey (classical keys with `classical`), its hold ended: the recovery code as it
 *  was shown. */
async function turnedOn(id: string, classical: boolean) {
  const t = new K.TurnOn(ACCOUNT, false, classical ? CLASSICAL : {});
  const code = t.codeText();
  await t.prepare(await K.E2EApi.view());
  auth.choose = prefer(id);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  return code;
}

/** Whether a post-quantum key was put away. */
const isPutAway = (k: C.Hybrid) => {
  try {
    C.hybridPublic(k);
    return false;
  } catch (e) {
    return e instanceof Error && e.message === C.PUT_AWAY;
  }
};

async function enrolled(id: string, by: string) {
  const v = await K.E2EApi.view();
  auth.choose = prefer(by);
  const ring = await K.unlockWithTouch(v, ACCOUNT);
  const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
  await e.prepare();
  auth.choose = prefer(id);
  await e.firstTouch();
  await e.secondTouch();
  auth.choose = prefer(by);
  await e.confirmAndSend();
  server.settle();
}

async function unlock(id: string) {
  auth.choose = prefer(id);
  await act(async () => {
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
  });
}

const button = (name: string | RegExp, within_: HTMLElement = document.body) => within(within_).getByRole("button", { name }) as HTMLButtonElement;

describe("the post-quantum section", () => {
  it("says a post-quantum account's keys are post-quantum, counts the texts still stored classical, and offers no upgrade", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    server.classicalTexts = { events: 3, rows: 1, talks: 1, total: 5 };
    render(<EncryptionPane />);
    expect(await screen.findByText(PQ_WORDS)).toBeTruthy();
    expect(screen.getByText(classicalTextsWords((await K.E2EApi.view()))!)).toBeTruthy();
    expect(screen.getByText(/The server still stores 5 texts sealed to a classical key/)).toBeTruthy();
    // a question its link sealed is counted, but not sealed again while it may yet be answered: said beside the count
    expect(screen.getByText(/they stay so until sealed again or deleted\. A question a linked session sealed is sealed again only once it is closed for good, by an answer its session has taken, by a done or a note of a linked session that refers to it, or by a note of yours closing it, so until then it is counted here after re-sealing too\.$/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upgrade" })).toBeNull();
  });

  it("offers the upgrade for a classical account only once unlocked, and not while a change waits or without ML-KEM", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${UPGRADE_BELOW}`)).toBeTruthy();
    expect([button("Upgrade").disabled, button("Upgrade").title]).toEqual([true, "Unlock first: the upgrade makes a new master key"]);
    await unlock("credAAAA");
    expect([button("Upgrade").disabled, button("Upgrade").title]).toEqual([false, ""]);
    // a change that waits: the upgrade is not offered until it is cancelled or takes effect
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(button("Upgrade").disabled).toBe(true);
    expect(button("Upgrade").title).toBe(K.UPGRADE_WAITING);
    server.pending = null;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(button("Upgrade").disabled).toBe(false);
    server.mlkem = false;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(button("Upgrade").disabled).toBe(true);
    expect(screen.getByText(K.NO_MLKEM)).toBeTruthy();
    expect(button("Upgrade").title).toBe(K.NO_MLKEM);
  });

  it("upgrades end to end: a new code shown and typed back, a touch of the passkey kept, the other named as removed, one signature", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    server.links = [{ link_id: "l1", name: "Mac", key_id: null, last_required_at: null, pins: null, seals: [1] }];
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Make a new recovery code", group));
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    expect(within(group).getByText(NEW_CODE_WORDS)).toBeTruthy();
    // typed into a field no browser offers to remember or to spell-check (a spelling service may send what is typed away)
    const typed = within(group).getByLabelText(/Type group/) as HTMLInputElement;
    expect([typed.getAttribute("autocomplete"), typed.getAttribute("spellcheck")]).toEqual(["off", "false"]);
    await act(async () => { fireEvent.click(button("Next", group)); });
    const keep = await within(group).findByRole("list", { name: "The passkeys to keep" });
    expect(within(keep).getByText("Laptop")).toBeTruthy();
    expect(within(keep).getByText("Phone")).toBeTruthy();
    auth.choose = prefer("credAAAA");
    const laptop = within(keep).getByText("Laptop").closest("li")!;
    await act(async () => { fireEvent.click(button("Touch it to keep it", laptop)); });
    expect(await within(laptop).findByText("Kept: its new key is made")).toBeTruthy();
    expect(within(group).getByText(/the upgrade removes it from your account: “Phone”, unless you sign with it\./)).toBeTruthy();
    expect(group.textContent).toContain(`${UPGRADE_CANCEL_WORDS} ${UPGRADE_MEANWHILE}`);
    // what signing it does at once for the passkey it removes, said before it is signed
    expect(within(group).getByText(UPGRADE_REMOVES_NOW)).toBeTruthy();
    expect(within(group).getByRole("timer").textContent).toMatch(/^Touch and sign within 4:(2|3)\d: /);
    expect(within(group).getByText(notPqWords((await K.E2EApi.view()))!).getAttribute("role")).toBe("note");
    // other tabs first, and the passkey it removes added again once it has taken effect, not while it waits
    expect(within(group).getByText(UPGRADE_OTHER_TABS)).toBeTruthy();
    expect(group.textContent).toContain(`A passkey neither touched nor signed with is removed from your account. ${UPGRADE_ADD_AFTER} Touch each`);
    expect(within(group).getByText(/the upgrade removes it from your account: “Phone”, unless you sign with it\. Add it again once the upgrade has taken effect\.$/)).toBeTruthy();
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    expect((await screen.findByText(/The upgrade is signed\./)).textContent).toContain("If it is cancelled first, the keys and your passkeys stay as they are, "
      + "though what signing it ended at once stays ended, and the new code unlocks nothing.");
    // it leaves the account no passkey but the one that signed it, so the one it removes cancels it too (rule (iii)), as
    // the words before it said
    expect((await K.E2EApi.view()).pending!.cancel_by).toEqual(["pk-credAAAA", "pk-credBBBB"]);
    expect(UPGRADE_CANCEL_WORDS).toContain("; where it leaves your account no passkey but the one that signs it, the passkeys it removes cancel it too.");
    // under high security the server counts hardware keys alone, for who cancels it and for what it leaves (cancellers)
    expect(UPGRADE_CANCEL_WORDS).toContain("While high security is on, hardware keys alone count, both for who cancels it and for what it leaves.");
    expect(UPGRADE_MEANWHILE).toContain("Until it takes effect the keys do not change");
    expect(UPGRADE_REMOVES_NOW).toContain("Cancelling the upgrade gives none of these back.");
    // signing does part of a removal at once; the passkey's removal, and what it signed withdrawn, come as it takes effect
    expect(UPGRADE_REMOVES_NOW).toContain("signing it does part of what removing that passkey does, at once:");
    expect(UPGRADE_REMOVES_NOW).toContain("The rest comes only as the upgrade takes effect, with the passkey's removal: the commands and moves it signed that no session has taken are withdrawn, and a link it resumed is stopped again.");
    expect(UPGRADE_ADD_AFTER).toContain("where what confirms the adding may cancel the upgrade, and is refused otherwise.");
    const body = bodyOf("/api/ade/e2e/revoke")!;
    expect(body.upgrade).toBe(true);
    expect(body.remove).toEqual(["pk-credBBBB"]);
    expect(useE2EStore.getState().waiting).toBeTruthy();
    // once it takes effect, this tab opens the new keys with the kept passkey's new key, no touch
    server.settle();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect([useE2EStore.getState().why, !!useE2EStore.getState().waiting, useE2EStore.getState().ring?.pq]).toEqual(["", false, true]);
    expect(await screen.findByText(PQ_WORDS)).toBeTruthy();
  });

  it("says nothing of removals once every passkey is touched to keep it, and, signed so, says a cancel leaves the keys and passkeys as they are", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group, keep } = await prepared();
    expect(within(group).getByText(UPGRADE_REMOVES_NOW)).toBeTruthy();         // none touched yet
    for (const [id, name] of [["credAAAA", "Laptop"], ["credBBBB", "Phone"]]) {
      auth.choose = prefer(id);
      const li = within(keep).getByText(name).closest("li")!;
      await act(async () => { fireEvent.click(button("Touch it to keep it", li)); });
      expect(await within(li).findByText("Kept: its new key is made")).toBeTruthy();
    }
    expect(within(group).queryByText(UPGRADE_REMOVES_NOW)).toBeNull();
    expect(within(group).queryByText(/the upgrade removes/)).toBeNull();
    // no link holds back: no note for links, and no note left empty
    expect(notPqWords(await K.E2EApi.view())).toBeNull();
    expect(within(group).getAllByRole("note").filter((n) => !n.textContent)).toEqual([]);
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    expect((await screen.findByText(/The upgrade is signed\./)).textContent)
      .toContain("If it is cancelled first, the keys and your passkeys stay as they are, and the new code unlocks nothing.");
    expect(bodyOf("/api/ade/e2e/revoke")!.remove).toEqual([]);
  });

  /** The Upgrade screen opened with a new code typed back and prepared: its group and its list of passkeys to keep. */
  async function prepared() {
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Make a new recovery code", group));
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    await act(async () => { fireEvent.click(button("Next", group)); });
    return { group, keep: await within(group).findByRole("list", { name: "The passkeys to keep" }) };
  }
  const reload = () => act(async () => { await useE2EStore.getState().load(ACCOUNT); });

  it("counts down the time to touch and sign, refuses both once it is up, and prepares again keeping the code", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const start = Date.now();
    const { group, keep } = await prepared();
    expect(within(group).queryByRole("button", { name: "Prepare again" })).toBeNull();
    expect(within(group).getByText(/the upgrade removes them from your account: “Laptop”, “Phone”, but for the one you sign with, if it is one of them\./)).toBeTruthy();
    // the time left counts down, the screen drawn again each second
    const clock = vi.spyOn(Date, "now").mockReturnValue(start + 60_000);
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)); });
    expect(within(group).getByRole("timer").textContent).toMatch(/^Touch and sign within 3:3\d: /);
    // the time is up: no touch and no signature until it is prepared again (drawn again here by the account read)
    clock.mockReturnValue(start + K.UPGRADE_WINDOW_MS + 1000);
    await reload();
    expect(within(group).getByText(K.UPGRADE_STALE)).toBeTruthy();
    const phone = within(keep).getByText("Phone").closest("li")!;
    expect(button("Touch it to keep it", phone).disabled).toBe(true);
    expect(button("Sign the upgrade", group).disabled).toBe(true);
    fireEvent.click(button("Prepare again", group));
    expect(within(group).getByText("Preparing it again: a moment.")).toBeTruthy();
    expect(within(group).queryByText(K.UPGRADE_STALE)).toBeNull();
    expect((await within(group).findByRole("timer")).textContent).toBe(leftWords(K.UPGRADE_WINDOW_MS));
    expect(within(group).queryByLabelText(/Type group/)).toBeNull();             // the code as it was given
    auth.choose = prefer("credBBBB");
    await act(async () => { fireEvent.click(button("Touch it to keep it", within(group).getByText("Phone").closest("li")!)); });
    expect(await within(group).findByText("Kept: its new key is made")).toBeTruthy();
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    expect(await screen.findByText(/The upgrade is signed\./)).toBeTruthy();
    expect(bodyOf("/api/ade/e2e/revoke")!.remove).toEqual([]);
  });

  it("keeps the recovery code when asked: the code typed whole and checked, then the upgrade signed with both its proofs", async () => {
    passkey("credAAAA", "Laptop");
    const code = await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Keep my recovery code", group));
    const input = within(group).getByLabelText("Your recovery code") as HTMLInputElement;
    expect([input.getAttribute("autocomplete"), input.getAttribute("spellcheck")]).toEqual(["off", "false"]);
    // Next once something is typed
    expect(button("Next", group).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "   " } });
    expect(button("Next", group).disabled).toBe(true);
    fireEvent.change(input, { target: { value: code } });
    expect(button("Next", group).disabled).toBe(false);
    await act(async () => { fireEvent.click(button("Next", group)); });
    await within(group).findByRole("list", { name: "The passkeys to keep" });
    expect(group.textContent).toContain("With the recovery code it waits");
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    expect((await screen.findByText(/The upgrade is signed\./)).textContent).toContain("elsewhere, unlock with a passkey you kept or the recovery code.");
    const body = bodyOf("/api/ade/e2e/revoke")!;
    expect([body.upgrade, body.kept, typeof body.recovery_proof?.proof, typeof body.new_recovery_proof?.proof]).toEqual([true, true, "string", "string"]);
  });

  it("turns off every button of the upgrade while a step is under way, and goes on counting down while a passkey is touched", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Make a new recovery code", group));
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    // the code checked: Next and Cancel off while it is
    const useCode = K.Upgrade.prototype.useCode;
    let checked = () => {};
    const checking = new Promise<void>((r) => { checked = r; });
    vi.spyOn(K.Upgrade.prototype, "useCode").mockImplementationOnce(async function (this: K.Upgrade, text: string) { await checking; return useCode.call(this, text); });
    fireEvent.click(button("Next", group));
    await vi.waitFor(() => expect(button("Next", group).disabled).toBe(true));
    expect(button("Cancel", group).disabled).toBe(true);
    await act(async () => { checked(); });
    const keep = await within(group).findByRole("list", { name: "The passkeys to keep" });
    // a passkey touched: every button off, and the time left still counted down
    const touch = K.Upgrade.prototype.touch;
    let touched = () => {};
    const touching = new Promise<void>((r) => { touched = r; });
    vi.spyOn(K.Upgrade.prototype, "touch").mockImplementationOnce(async function (this: K.Upgrade, id: string) { await touching; return touch.call(this, id); });
    auth.choose = prefer("credBBBB");
    const phone = within(keep).getByText("Phone").closest("li")!;
    const laptop = within(keep).getByText("Laptop").closest("li")!;
    fireEvent.click(button("Touch it to keep it", phone));
    await vi.waitFor(() => expect(button("Touch it to keep it", laptop).disabled).toBe(true));
    expect([button("Touch it to keep it", phone).disabled, button("Sign the upgrade", group).disabled, button("Cancel", group).disabled]).toEqual([true, true, true]);
    expect(within(group).getByRole("timer").textContent).toMatch(/^Touch and sign within /);
    expect(within(group).queryByText("Preparing it again: a moment.")).toBeNull();
    await act(async () => { touched(); });
    expect(await within(phone).findByText("Kept: its new key is made")).toBeTruthy();
    expect([button("Touch it to keep it", laptop).disabled, button("Sign the upgrade", group).disabled, button("Cancel", group).disabled]).toEqual([false, false, false]);
  });

  it("offers to prepare again once a passkey's new key could not be made, off while it prepares, then keeps that passkey", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group, keep } = await prepared();
    expect(within(group).queryByRole("button", { name: "Prepare again" })).toBeNull();
    // a touch that used up its passkey's new key and failed after (as one whose new key would not hold its grant): not
    // kept, and nothing left to keep it with until prepared again
    vi.spyOn(K.Upgrade.prototype, "touch").mockImplementationOnce(async function (this: K.Upgrade, id: string) {
      const seat = this.seats.find((s) => s.credential.credential_id === id)!;
      seat.held?.drop();
      seat.held = null;
      throw new C.E2EError("Its new key does not hold the new master key.");
    });
    await act(async () => { fireEvent.click(button("Touch it to keep it", within(keep).getByText("Phone").closest("li")!)); });
    expect(await within(group).findByText("Its new key could not be made: prepare it again")).toBeTruthy();
    // not while the server cannot use ML-KEM, which it would need
    server.mlkem = false;
    await reload();
    expect(button("Prepare again", group).disabled).toBe(true);
    server.mlkem = true;
    await reload();
    expect(button("Prepare again", group).disabled).toBe(false);
    const prepare = K.Upgrade.prototype.prepare;
    let prepared_ = () => {};
    const preparing = new Promise<void>((r) => { prepared_ = r; });
    vi.spyOn(K.Upgrade.prototype, "prepare").mockImplementationOnce(async function (this: K.Upgrade, ...a: Parameters<K.Upgrade["prepare"]>) { await preparing; return prepare.apply(this, a); });
    fireEvent.click(button("Prepare again", group));
    await vi.waitFor(() => expect(button("Prepare again", group).disabled).toBe(true));
    await act(async () => { prepared_(); });
    await vi.waitFor(() => expect(within(group).queryByRole("button", { name: "Prepare again" })).toBeNull());
    auth.choose = prefer("credBBBB");
    await act(async () => { fireEvent.click(button("Touch it to keep it", within(group).getByText("Phone").closest("li")!)); });
    expect(await within(group).findByText("Kept: its new key is made")).toBeTruthy();
  });

  it("opens the keys of the upgrade it signed only where they lead back to the keys it holds", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group } = await prepared();
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    expect(await screen.findByText(/The upgrade is signed\./)).toBeTruthy();
    server.settle();
    server.chain = [];                                       // a server that leaves out its chain link
    await reload();
    expect([useE2EStore.getState().ring, useE2EStore.getState().waiting]).toEqual([null, null]);
  });

  it("puts away a recovery code's key typed when the tab locks, and when its screen closes", async () => {
    passkey("credAAAA", "Laptop");
    const code = await turnedOn("credAAAA", false);
    // a change made with the code waits: turning it off goes only with the code
    server.pending = { id: "hold-c", change: "rotate", takes_effect_at: server.now + 86400, apply: () => {}, madeBy: "recovery", byCode: true, body: {} };
    const real = K.codeKey;
    const typed: K.Priv[] = [];
    vi.spyOn(K, "codeKey").mockImplementation(async (...a) => {
      const k = await real(...a);
      typed.push(k);
      return k;
    });
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Turn off"));
    const LABEL = "The recovery code, to turn it off with it";
    const typeIt = async () => {
      fireEvent.change(await screen.findByLabelText(LABEL), { target: { value: code } });
      await act(async () => { fireEvent.click(button("Use the code")); });
      expect(await screen.findByText("That is the recovery code: it goes with its proof.")).toBeTruthy();
    };
    await typeIt();
    expect(typed.map((k) => isPutAway(k.key as C.Hybrid))).toEqual([false]);
    act(() => { useE2EStore.getState().lock(); });
    expect(typed.map((k) => isPutAway(k.key as C.Hybrid))).toEqual([true]);
    await typeIt();                                             // typed again, the tab locked
    fireEvent.click(button("Cancel"));
    expect(typed.map((k) => isPutAway(k.key as C.Hybrid))).toEqual([true, true]);
  });

  it("puts away the key a passkey being added opened, when adding it is closed", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const real = C.unwrapCredentialSeed;
    const opened: C.Hybrid[] = [];
    vi.spyOn(C, "unwrapCredentialSeed").mockImplementation(async (...a) => {
      const k = await real(...a);
      opened.push(k);
      return k;
    });
    const row = screen.getByText("Phone").closest("li")!;
    fireEvent.click(button("Add", row));
    const group = await screen.findByRole("group", { name: "Adding “Phone”" });
    auth.choose = prefer("credBBBB");
    const ready = async (name: string) => {
      const b = await within(group).findByRole("button", { name }) as HTMLButtonElement;
      await vi.waitFor(() => expect(b.disabled).toBe(false));
      return b;
    };
    const twice = async () => {
      const touchIt = await ready("Touch it");
      await act(async () => { fireEvent.click(touchIt); });
      const again = await ready("Touch it again");
      await act(async () => { fireEvent.click(again); });
      await within(group).findByRole("button", { name: "Confirm with a passkey" });
    };
    await twice();
    expect(opened.map(isPutAway)).toEqual([false]);
    // the account changed meanwhile: adding it is made again, and what the one replaced opened is put away
    server.addPasskey("credCCCC", "Tablet");
    await reload();
    await ready("Touch it");
    expect(opened.map(isPutAway)).toEqual([true]);
    await twice();
    expect(opened.map(isPutAway)).toEqual([true, false]);
    fireEvent.click(button("Cancel", group));
    expect(opened.map(isPutAway)).toEqual([true, true]);
  });

  it("prepares again from the account as read now", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const start = Date.now();
    const { group } = await prepared();
    vi.spyOn(Date, "now").mockReturnValue(start + K.UPGRADE_WINDOW_MS + 1000);
    server.mlkem = false;                                     // the server can no longer use ML-KEM
    await reload();
    await act(async () => { fireEvent.click(button("Prepare again", group)); });
    expect(await within(group).findByText(K.NO_MLKEM)).toBeTruthy();
    expect(within(group).queryByRole("timer")).toBeNull();
  });

  it("touches and signs nothing while another change waits, placed elsewhere since it was prepared", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group, keep } = await prepared();
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    await reload();
    expect(within(group).getByText(K.UPGRADE_WAITING)).toBeTruthy();
    expect(button("Touch it to keep it", within(keep).getByText("Phone").closest("li")!).disabled).toBe(true);
    expect(button("Sign the upgrade", group).disabled).toBe(true);
    server.pending = null;
    await reload();
    expect(within(group).queryByText(K.UPGRADE_WAITING)).toBeNull();
    expect(button("Sign the upgrade", group).disabled).toBe(false);
  });

  it("says the upgrade it signed while that one waits, and offers the upgrade again once it is cancelled", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group } = await prepared();
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign the upgrade", group)); });
    // it removes nothing: cancelled, everything stays as it was
    expect(await screen.findByText(/The upgrade is signed\..*If it is cancelled first, the keys and your passkeys stay as they are, and the new code unlocks nothing\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upgrade" })).toBeNull();
    expect(screen.queryByText(new RegExp(UPGRADE_BELOW))).toBeNull();
    // where it waits it is named as what it is, and, made with a passkey, a revocation of that passkey takes its place
    expect(screen.getByText(/^The upgrade to post-quantum keys waits until/)).toBeTruthy();
    expect(screen.getByText(/^Not yours\? Remove the passkey that made it/)).toBeTruthy();
    // cancelled: the words go, and the upgrade is offered again
    const placed = server.pending!;
    server.pending = null;
    await reload();
    expect(screen.queryByText(/The upgrade is signed\./)).toBeNull();
    expect(button("Upgrade").disabled).toBe(false);
    // another upgrade, signed elsewhere: said as one that waits, not as the one this tab signed
    server.pending = { ...placed, id: "hold-other" };
    await reload();
    expect(screen.getByText(UPGRADE_WAITS_ABOVE)).toBeTruthy();
    expect(UPGRADE_WAITS_ABOVE).toContain(": the keys do not change until it takes effect.");
    expect(screen.queryByText(/The upgrade is signed\./)).toBeNull();
  });

  it("says why no upgrade is offered while encryption is off", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    const v = await K.E2EApi.view();
    const off = new K.TurnOff(v, ACCOUNT);
    await off.prepare();
    auth.choose = prefer("credAAAA");
    await off.signAndSend();
    server.settle();
    render(<EncryptionPane />);
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${K.UPGRADE_OFF}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upgrade" })).toBeNull();
  });

  /** A classical account with one text stored sealed to its classical key, upgraded with a new code (its hold ended),
   *  the panel shown, and unlocked unless `locked`: the new code. */
  async function upgradedWithOneText(locked = false) {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    auth.choose = prefer("credAAAA");
    const ring = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const [, held] = [...ring.keys.entries()][0];
    const slot = "proofpane-e2e/1|acct-1|l|event|step|||a";
    const old = await C.sealEnvelope(held.EK, C.utf8('{"text":"one"}'), slot);
    const u = new K.Upgrade(ring, await K.E2EApi.view(), false);
    const code = u.codeText();
    await u.useCode(code.split(" ")[u.group]);
    await u.prepare();
    await u.touch("credAAAA");
    await u.signAndSend();
    server.settle();
    server.stored = [{ where: { table: "events", id: 1, field: "text" }, value: old, slot }];
    server.classicalTexts = { events: 1, rows: 0, talks: 0, total: 1 };
    render(<EncryptionPane />);
    if (locked) await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    else await unlock("credAAAA");
    return code;
  }

  it("re-seals with the recovery code typed: what is still stored classical sealed again, and what was done said", async () => {
    const code = await upgradedWithOneText();
    // nothing opens by itself: the button, then its screen, which offers Cancel (Stop only once it runs) and does not
    // re-seal before the code is given
    expect(screen.queryByRole("group", { name: "Re-sealing" })).toBeNull();
    fireEvent.click(button("Re-seal"));
    let group = screen.getByRole("group", { name: "Re-sealing" });
    expect(within(group).queryByRole("button", { name: "Stop" })).toBeNull();
    expect(button("Re-seal", group).disabled).toBe(true);
    fireEvent.click(button("Cancel", group));
    expect(screen.queryByRole("group", { name: "Re-sealing" })).toBeNull();
    fireEvent.click(button("Re-seal"));
    group = screen.getByRole("group", { name: "Re-sealing" });
    fireEvent.change(within(group).getByLabelText("The recovery code, to re-seal with it"), { target: { value: code } });
    await act(async () => { fireEvent.click(button("Use the code", group)); });
    expect(await within(group).findByText("That is the recovery code: it goes with its proof.")).toBeTruthy();
    expect(button("Re-seal", group).disabled).toBe(false);
    await act(async () => { fireEvent.click(button("Re-seal", group)); });
    const said = await screen.findByText(new RegExp(`Sealed again: 1 text\\. ${RESEALED_DISK.slice(0, 20)}`));
    expect(said.getAttribute("role")).toBe("status");
    // done: its screen closes, the words said in its place
    expect(screen.queryByRole("group", { name: "Re-sealing" })).toBeNull();
    expect(C.envelopeForm(server.stored[0].value)!.form).toBe(2);
    expect(server.resealLog).toHaveLength(1);
  });

  it("offers re-sealing only once unlocked, and says why not before", async () => {
    await upgradedWithOneText(true);
    const b = await screen.findByRole("button", { name: "Re-seal" }) as HTMLButtonElement;
    expect([b.disabled, b.title]).toEqual([true, "Unlock first: each text is opened, then sealed again"]);
    fireEvent.click(b);
    expect(screen.queryByRole("group", { name: "Re-sealing" })).toBeNull();
    await unlock("credAAAA");
    expect([button("Re-seal").disabled, button("Re-seal").title]).toEqual([false, ""]);
  });

  it("says no count and offers no re-sealing where the server stores no text sealed to a classical key, or says nothing of it", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    server.classicalTexts = { events: 0, rows: 0, talks: 0, total: 0 };
    render(<EncryptionPane />);
    expect(await screen.findByText(PQ_WORDS)).toBeTruthy();
    await unlock("credAAAA");
    expect(screen.queryByText(/sealed to a classical key: /)).toBeNull();
    expect(screen.queryByRole("button", { name: "Re-seal" })).toBeNull();
    const v = await K.E2EApi.view();
    expect([classicalTextsWords(v), classicalTextsWords({ ...v, classical_texts: null })]).toEqual([null, null]);
  });

  it("ends a re-seal the server refuses at its first batch with the server's words, says nothing was sealed, and offers Cancel, which closes it", async () => {
    const code = await upgradedWithOneText();
    server.answer = (call) => (call.method === "POST" && call.path === "/api/ade/e2e/reseal" ? [409, { detail: K.RESEAL_OFF }] : null);
    fireEvent.click(button("Re-seal"));
    const group = screen.getByRole("group", { name: "Re-sealing" });
    fireEvent.change(within(group).getByLabelText("The recovery code, to re-seal with it"), { target: { value: code } });
    await act(async () => { fireEvent.click(button("Use the code", group)); });
    await within(group).findByText("That is the recovery code: it goes with its proof.");
    await act(async () => { fireEvent.click(button("Re-seal", group)); });
    expect((await within(group).findByRole("alert")).textContent).toBe(K.RESEAL_OFF);
    expect(within(group).getByText("Sealed again: 0. Left as they are: 0.").getAttribute("role")).toBe("status");
    expect(within(group).queryByRole("button", { name: "Stop" })).toBeNull();
    fireEvent.click(button("Cancel", group));
    expect(screen.queryByRole("group", { name: "Re-sealing" })).toBeNull();
    expect(server.resealLog).toHaveLength(0);
  });

  it("turns Cancel off while the recovery code is checked, so the screen does not close on a key being made", async () => {
    const code = await upgradedWithOneText();
    fireEvent.click(button("Re-seal"));
    const group = screen.getByRole("group", { name: "Re-sealing" });
    fireEvent.change(within(group).getByLabelText("The recovery code, to re-seal with it"), { target: { value: code } });
    // the code's key is made from it locally (HKDF first): held there, the step is under way
    const real = crypto.subtle.importKey.bind(crypto.subtle) as (...a: unknown[]) => Promise<CryptoKey>;
    let open = () => {};
    const held = new Promise<void>((r) => { open = r; });
    vi.spyOn(crypto.subtle, "importKey").mockImplementationOnce((async (...a: unknown[]) => { await held; return real(...a); }) as unknown as SubtleCrypto["importKey"]);
    fireEvent.click(button("Use the code", group));
    await vi.waitFor(() => expect(button("Use the code", group).disabled).toBe(true));
    expect(button("Cancel", group).disabled).toBe(true);
    await act(async () => { open(); });
    expect(await within(group).findByText("That is the recovery code: it goes with its proof.")).toBeTruthy();
    expect(button("Cancel", group).disabled).toBe(false);
  });

  /** An account upgraded with a new code, three texts stored classical (one that opens and one that does not, served
   *  with another slot than it was sealed to, on the first page; another that does not on the second), the panel shown
   *  and unlocked, and re-sealing started with the code, its second page held back until `release`: the group. */
  async function resealingThree() {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    auth.choose = prefer("credAAAA");
    const ring = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const [, held] = [...ring.keys.entries()][0];
    const slot = (x: string) => `proofpane-e2e/1|acct-1|l|event|step|||${x}`;
    const old = await C.sealEnvelope(held.EK, C.utf8('{"text":"one"}'), slot("a"));
    const u = new K.Upgrade(ring, await K.E2EApi.view(), false);
    const code = u.codeText();
    await u.useCode(code.split(" ")[u.group]);
    await u.prepare();
    await u.touch("credAAAA");
    await u.signAndSend();
    server.settle();
    server.stored = [{ where: { table: "events", id: 1, field: "text" }, value: old, slot: slot("a") },
      { where: { table: "events", id: 2, field: "text" }, value: old, slot: slot("b") },
      { where: { table: "events", id: 3, field: "text" }, value: old, slot: slot("c") }];
    server.classicalTexts = { events: 3, rows: 0, talks: 0, total: 3 };
    server.resealPageSize = 2;                                    // two on the first page, the third on the second
    const real = K.E2EApi.resealPage;
    let release = () => {};
    const second = new Promise<void>((r) => { release = r; });
    vi.spyOn(K.E2EApi, "resealPage").mockImplementation(async (after) => {
      if (after) await second;
      return real(after);
    });
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Re-seal"));
    const group = screen.getByRole("group", { name: "Re-sealing" });
    fireEvent.change(within(group).getByLabelText("The recovery code, to re-seal with it"), { target: { value: code } });
    await act(async () => { fireEvent.click(button("Use the code", group)); });
    await within(group).findByText("That is the recovery code: it goes with its proof.");
    await act(async () => { fireEvent.click(button("Re-seal", group)); });
    return { group, release };
  }

  it("says what re-sealing left as it is, as it goes and when it ends: the texts that did not open here", async () => {
    const { group, release } = await resealingThree();
    expect(await within(group).findByText("Sealed again: 1. Left as they are: 1.")).toBeTruthy();
    // while it runs: Re-seal off, and Stop in Cancel's place
    expect(button("Re-seal", group).disabled).toBe(true);
    expect(within(group).queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(button("Stop", group)).toBeTruthy();
    await act(async () => { release(); });
    expect(await screen.findByText(new RegExp(`^Sealed again: 1 text; 2 did not open here and are left as they are\\. ${RESEALED_DISK.slice(0, 20)}`))).toBeTruthy();
    expect(server.resealLog).toHaveLength(1);
  });

  it("says what re-sealing did and left when it is stopped part way", async () => {
    const { group, release } = await resealingThree();
    expect(await within(group).findByText("Sealed again: 1. Left as they are: 1.")).toBeTruthy();
    fireEvent.click(button("Stop", group));
    await act(async () => { release(); });
    expect(await within(group).findByText(K.RESEAL_STOPPED)).toBeTruthy();
    expect(within(group).getByText("Sealed again: 1. Left as they are: 1.")).toBeTruthy();
    expect(server.resealLog).toHaveLength(1);
  });

  it("says, where this browser saw the account post-quantum, that a classical view may be an old copy", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    await act(async () => { await K.raiseFloor(ACCOUNT, useE2EStore.getState().ring!); });
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    server.pq = false;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect((await screen.findAllByText(K.ROLLED_BACK_CLASSICAL)).length).toBeGreaterThan(0);
    // nothing is opened or changed from the same keys said to be classical: the tab locks, and turning it off is not offered
    expect(useE2EStore.getState().ring).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    expect(screen.queryByText("Turning it off")).toBeNull();
    // the words say what to do, and nothing offers to take the copy
    expect(K.ROLLED_BACK_CLASSICAL).toContain("Do not unlock here");
    expect(K.ROLLED_BACK_CLASSICAL).toContain("clear this site's data in this browser");
    // and what works where the upgrade cannot be made again: a link sent its version-3 statement back in after the
    // restore, so the classical keys are no longer the newest stated (NOT_NEWEST, the server's CLASSICAL_AFTER)
    expect(K.ROLLED_BACK_CLASSICAL).toContain("If the upgrade is refused there (a linked session sent its post-quantum statement back in, so these keys are no longer the newest the account stated), turn encryption off and start again: that makes new post-quantum keys, and what was sealed to the classical ones is not read after it.");
    expect(screen.queryByRole("button", { name: "Trust the server's keys" })).toBeNull();
  });

  it("says, where this browser saw the account's keys and the server serves none, that it may have been put back, and offers turning it on below that", async () => {
    passkey("credAAAA", "Laptop");
    // the account's keys as a backup made before encryption was turned on holds them: none
    const beforeOn = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    await act(async () => { await K.raiseFloor(ACCOUNT, useE2EStore.getState().ring!); });
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    // the server restored from that backup: the tab holding the keys locks, and the pane says why, in the words
    // for keys put back (no key is served to call classical), and what an owner who did it does; turning it on is
    // offered below that, as to a new account
    Object.assign(server, beforeOn);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(useE2EStore.getState().ring).toBeNull();
    const turnOn = screen.getByRole("button", { name: "Turn on" });
    expect(alert.compareDocumentPosition(turnOn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // below the warning
    expect(screen.queryByRole("button", { name: "Unlock" })).toBeNull();
    // a turning-on placed elsewhere meanwhile: said beside it, with the change below to cancel
    server.pending = { id: "hold-w", change: "turn-on", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(await screen.findByText(/Turning it on waits on its hold, below/)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
    // a browser that never saw the account's keys trusts the server, as at every first look: it offers turning it on
    server.pending = null;
    cleanup();
    useE2EStore.setState(useE2EStore.getInitialState());
    localStorage.clear();
    render(<EncryptionPane />);
    expect(await screen.findByRole("button", { name: "Turn on" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("once keys are served again after none, says what the view now gives, and asks trust only where it is older by its statements", async () => {
    passkey("credAAAA", "Laptop");
    const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    const none = rows();                                                 // as a backup made before encryption was turned on
    await turnedOn("credAAAA", false);
    const one = rows();                                                  // its first statement
    server.rotateEvery = 0;
    auth.choose = prefer("credAAAA");
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const two = rows();                                                  // and a rotation's
    render(<EncryptionPane />);
    const served = async (kept: Record<string, unknown>) => {
      Object.assign(server, JSON.parse(JSON.stringify(kept)));
      await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    };
    const trust = () => screen.queryByRole("group", { name: "Keys older than this browser's" });
    /** Unlocked in the pane (this browser sees two statements, post-quantum), then the server put back to no keys: the
     *  tab locks, and the pane says the server may have been put back. */
    const lockedOverNone = async () => {
      await served(two);
      await unlock("credAAAA");
      expect(await screen.findByRole("button", { name: "Lock" })).toBeTruthy();
      await served(none);
      expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    };
    // the same keys back: nothing is said of the view with no keys, and no trust is asked
    await lockedOverNone();
    await served(two);
    expect(await screen.findByRole("button", { name: "Unlock" })).toBeTruthy();
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(trust()).toBeNull();
    // a classical copy where this browser saw the keys post-quantum: its own words, and no trust asked
    await lockedOverNone();
    await served({ ...two, pq: false });
    expect((await screen.findAllByText(K.ROLLED_BACK_CLASSICAL)).length).toBeGreaterThan(0);
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(trust()).toBeNull();
    // fewer statements than it saw: as for any keys put back so, trust asked
    await lockedOverNone();
    await served(one);
    expect(await screen.findByRole("group", { name: "Keys older than this browser's" })).toBeTruthy();
    expect(screen.getAllByText(K.ROLLED_BACK).length).toBeGreaterThan(0);
  });

  it("says it over no keys whatever this browser kept of the account's keys, turning it on still offered, and not for keys it saw of another account or made itself and never saw take effect", async () => {
    passkey("credAAAA", "Laptop");
    const mk = "a".repeat(32);
    /** The pane as a tab opened now draws it over no keys, with `kept` in this browser's localStorage. */
    const drawn = async (kept: [string, Record<string, unknown>]) => {
      cleanup();
      useE2EStore.setState(useE2EStore.getInitialState());
      localStorage.clear();
      localStorage.setItem(K.floorKey(kept[0]), JSON.stringify({ floor: null, next: null, pq: null, seq: 0, ...kept[1] }));
      render(<EncryptionPane />);
      await screen.findByText(/^What your linked machines and sessions send you is stored encrypted/);
      await vi.waitFor(() => expect(useE2EStore.getState().view).not.toBeNull());
    };
    // the master key it opened alone (a page of an older release), the post-quantum mark alone (after Trust the
    // server's keys), the newest statement alone, or all three
    for (const kept of [{ floor: mk }, { pq: mk }, { seq: 2 }, { floor: mk, pq: mk, seq: 2 }]) {
      await drawn([ACCOUNT, kept]);
      expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
      expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
    }
    // another account's keys, and keys this browser made that never took effect (a first turn-on cancelled): offered
    for (const kept of [["acct-2", { floor: mk, pq: mk, seq: 2 }], [ACCOUNT, { next: mk }]] as [string, Record<string, unknown>][]) {
      await drawn(kept);
      expect(await screen.findByRole("button", { name: "Turn on" })).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    }
  });

  it("turns it on again where the only turn-on this browser made was cancelled before it took effect: it saw no keys", async () => {
    passkey("credAAAA", "Laptop");
    render(<EncryptionPane />);
    /** Turning it on from the pane up to its signature: the code shown, a group typed back, and the statement prepared. */
    const started = async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
      const group = screen.getByRole("group", { name: "Turning encryption on" });
      const shown = within(group).getByLabelText("The recovery code").textContent!;
      const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
      fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: shown.split(" ")[asked - 1] } });
      fireEvent.click(button("Next", group));
      const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
      await vi.waitFor(() => expect(sign.disabled).toBe(false));
      expect(within(group).queryByRole("alert")).toBeNull();
      return { group, sign };
    };
    const { group, sign } = await started();
    auth.choose = prefer("credAAAA");
    fireEvent.click(sign);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    await screen.findByText(/Turning encryption on is signed\./);
    // this browser waits for the keys it made, which it has not seen take effect
    expect(JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!)).toEqual({ floor: null, next: expect.stringMatching(/^[0-9a-f]{32}$/), pq: null, seq: 0 });
    const waits = screen.getByText(/Turning encryption on waits until/).closest("div[role=status]") as HTMLElement;
    const cancel = button("Cancel it with a passkey", waits);
    await vi.waitFor(() => expect(cancel.disabled).toBe(false));
    fireEvent.click(cancel);
    await vi.waitFor(() => expect(server.pending).toBeNull());
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(screen.queryByRole("alert")).toBeNull();
    await started();                                                    // offered again, and prepared, not refused
  });

  /** The account's key rows and statements as the server holds them, as a copy: what a backup of it holds. */
  const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
    recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
  /** The server holding `kept`, and the view read. */
  const servedNow = async (kept: Record<string, unknown>) => {
    Object.assign(server, JSON.parse(JSON.stringify(kept)));
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
  };
  const trustAsked = () => screen.queryByRole("group", { name: "Keys older than this browser's" });
  /** Turn on, from the pane's own button, through its signature: the code shown, a group of it typed back, two touches. */
  const signedTurnOn = async () => {
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const shown = within(group).getByLabelText("The recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: shown.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    auth.choose = prefer("credAAAA");
    fireEvent.click(sign);
    fireEvent.click(await within(group).findByRole("button", { name: "Touch it again" }));
    await screen.findByText(/Turning encryption on is signed\./);
  };
  /** Trust the server's keys, in the box the pane shows with a refusal of keys older than this browser's. */
  const trustTheServer = async () => {
    await act(async () => { fireEvent.click(button("Trust the server's keys", screen.getByRole("group", { name: "Keys older than this browser's" }))); });
  };

  it("drops, as soon as an unlock reads keys that are not older, the reason a view gave before, and asks no trust, though the unlock then fails", async () => {
    passkey("credAAAA", "Laptop");
    const none = rows();                                                 // as a backup made before encryption was turned on
    await turnedOn("credAAAA", false);
    const one = rows();                                                  // its first statement
    server.rotateEvery = 0;
    auth.choose = prefer("credAAAA");
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const two = rows();                                                  // and a rotation's
    render(<EncryptionPane />);
    await servedNow(two);
    await unlock("credAAAA");
    expect(await screen.findByRole("button", { name: "Lock" })).toBeTruthy();
    await servedNow(none);                                               // locked over no keys
    await servedNow(one);                                                // then one statement fewer: trust asked
    expect(await screen.findByRole("group", { name: "Keys older than this browser's" })).toBeTruthy();
    // the keys served again, read only by an unlock given something that is not a recovery code
    Object.assign(server, JSON.parse(JSON.stringify(two)));
    fireEvent.change(screen.getByLabelText("Your recovery code"), { target: { value: "not a code" } });
    fireEvent.click(button("Unlock with it"));
    expect(await screen.findByText(K.CODE_FORM)).toBeTruthy();
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(trustAsked()).toBeNull();
    await servedNow(two);
    await servedNow(two);
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(trustAsked()).toBeNull();
  });

  it("keeps saying keys put back to an older master key do not lead back, and asking trust, once a link sends their statements back in", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const old = rows();                                                  // its first master key and statement
    const r = new K.Revoke(useE2EStore.getState().ring!, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });   // followed: the newest master key opened
    const now = rows();
    await servedNow(old);
    expect(await screen.findByRole("group", { name: "Keys older than this browser's" })).toBeTruthy();
    // as many statements as this browser saw, so the view is older no more, but its master key is the first
    await servedNow({ ...old, statements: now.statements });
    expect(K.rolledBack(ACCOUNT, useE2EStore.getState().view!)).toBeNull();
    expect(screen.getAllByRole("status").some((s) => s.textContent === K.ROLLED_BACK)).toBe(true);   // why it locked
    expect(trustAsked()).toBeTruthy();
    // the newest master key served again: nothing said of it, no trust asked
    await servedNow(now);
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(trustAsked()).toBeNull();
  });

  it("says it over a view that serves the recovery key alone, the other rows deleted: no master key and no credential are no keys, and turning it on is offered below it", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    Object.assign(server, { credentials: [], grants: [], roster: null, contentKeys: [], chain: [] });
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    const v = useE2EStore.getState().view!;
    expect([K.hasKeys(v), !!v.recovery, v.pq, v.statements.length]).toEqual([false, true, true, 1]);
    expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
  });

  it("turns encryption on from the warning over no keys: the tab holds the keys it made once their hold ends", async () => {
    passkey("credAAAA", "Laptop");
    const beforeOn = rows();
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    await servedNow(beforeOn);                                           // the owner restored that backup
    expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    expect(RESTORED_TURN_ON).toContain("turn encryption on again below");
    await signedTurnOn();
    // taken by the server as a first turn-on: once its hold ends, this tab opens the keys it made, which state as many
    // statements as this browser saw (one), so they are not older and nothing is said of them
    server.settle();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(useE2EStore.getState().ring?.pq).toBe(true);
    expect(await screen.findByRole("button", { name: "Lock" })).toBeTruthy();
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(trustAsked()).toBeNull();
  });

  it("turns encryption on from the warning over no keys where this browser saw more statements than the new keys state: they are refused as they take effect, and trusting them opens them", async () => {
    passkey("credAAAA", "Laptop");
    const beforeOn = rows();
    await turnedOn("credAAAA", false);
    server.rotateEvery = 0;
    auth.choose = prefer("credAAAA");
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();                                                     // two statements
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!).seq).toBe(2);
    await servedNow(beforeOn);
    expect((await screen.findByRole("alert")).textContent).toBe(`${K.ROLLED_BACK} ${RESTORED_TURN_ON}`);
    await signedTurnOn();
    server.settle();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    // the keys it made state one statement where this browser saw two: refused as they take effect, in the words
    // for keys put back, with the trust box, which is the owner's word that they put the server back themselves
    const s = useE2EStore.getState();
    expect([s.ring, s.waiting, s.why]).toEqual([null, null, K.ROLLED_BACK]);
    expect(useE2EStore.getState().view?.statements.length).toBe(1);
    expect(await screen.findByRole("group", { name: "Keys older than this browser's" })).toBeTruthy();
    await trustTheServer();
    expect(trustAsked()).toBeNull();
    await unlock("credAAAA");
    expect(useE2EStore.getState().ring?.pq).toBe(true);
    expect(await screen.findByRole("button", { name: "Lock" })).toBeTruthy();
  });

  it("says nothing of a server put back, and starts again, where encryption was turned off and the last passkey that unlocks it removed from the account", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");                                        // a passkey of the account that does not unlock it
    await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    await act(async () => { await K.raiseFloor(ACCOUNT, useE2EStore.getState().ring!); });
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    auth.choose = prefer("credAAAA");
    await off.signAndSend();
    server.settle();
    auth.choose = prefer("credBBBB");
    const confirmation = await confirmWithPasskey(false, { change: "passkey-remove:pk-credAAAA" });
    expect((await server.fetch("/api/account/passkeys/pk-credAAAA", { method: "DELETE", body: JSON.stringify({ confirmation }) })).status).toBe(200);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    // no credential is served, but the master key is: keys, as the pane counts them, not older than this browser saw
    expect([useE2EStore.getState().view?.credentials.length, useE2EStore.getState().view?.mk_id]).toEqual([0, server.roster!.mk_id]);
    const again = await screen.findByRole("button", { name: "Start again" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    // and starting again is prepared, not refused: its code shown and a group typed back, then a passkey to sign with
    fireEvent.click(again);
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const shown = within(group).getByLabelText("The recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: shown.split(" ")[asked - 1] } });
    fireEvent.click(button("Next", group));
    const sign = await within(group).findByRole("button", { name: "Sign with a passkey" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(sign.disabled).toBe(false));
    expect(within(group).queryByRole("alert")).toBeNull();
  });

  it("says nothing of a server put back where a revocation with the recovery code's proof left the code alone to unlock it", async () => {
    passkey("credAAAA", "Laptop");
    const code = await turnedOn("credAAAA", false);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    await act(async () => { await K.raiseFloor(ACCOUNT, useE2EStore.getState().ring!); });
    const r = new K.Revoke(useE2EStore.getState().ring!, await K.E2EApi.view(), { remove: ["pk-credAAAA"], onlyCode: true });
    await r.prepare();
    await r.signAndSendWithCode(await K.codeKey(await K.E2EApi.view(), ACCOUNT, code));
    server.settle();
    act(() => useE2EStore.getState().lock());
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(useE2EStore.getState().view?.credentials).toEqual([]);
    expect(await screen.findByRole("button", { name: "Unlock with it" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // the code unlocks the keys there
    fireEvent.change(screen.getByLabelText("Your recovery code"), { target: { value: code } });
    fireEvent.click(button("Unlock with it"));
    expect(await screen.findByText(/Unlocked with the recovery code\./)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers neither turning it off nor starting again from keys put back, and asks at once whether to trust keys with fewer statements", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain }));
    const beforeRotation = rows();                                        // a copy someone who can write the database keeps
    server.rotateEvery = 0;
    auth.choose = prefer("credAAAA");
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    render(<EncryptionPane />);
    await unlock("credAAAA");
    await act(async () => { await K.raiseFloor(ACCOUNT, useE2EStore.getState().ring!); });   // the newest statement seen: the rotation's
    act(() => useE2EStore.getState().lock());
    expect(await screen.findByRole("button", { name: "Turn off" })).toBeTruthy();
    const now = rows();
    // put back to before the rotation, still on: fewer statements than this browser saw. Said, turning it off not
    // offered, and whether to trust the keys asked at once, with no unlock tried first
    Object.assign(server, beforeRotation);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(await screen.findByText(K.ROLLED_BACK)).toBeTruthy();
    expect(useE2EStore.getState().view?.on).toBe(true);
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    const ask = screen.getByRole("group", { name: "Keys older than this browser's" });
    const reads = () => server.calls.filter((c) => c.method === "GET" && c.path === "/api/ade/e2e").length;
    const before = reads();
    await act(async () => { fireEvent.click(button("Trust the server's keys", ask)); });
    // trusted: what was held back is offered at once, the pane drawn again from the keys as read, with no read of its
    // own (one that came back late would put back what the server served before)
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Turn off" })).toBeTruthy());
    expect(screen.queryByText(K.ROLLED_BACK)).toBeNull();
    expect(reads()).toBe(before);
    Object.assign(server, now);
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    auth.choose = prefer("credAAAA");
    await off.signAndSend();
    server.settle();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(await screen.findByRole("button", { name: "Start again" })).toBeTruthy();
    // a browser that saw more statements than are served: starting again is not offered until the person trusts them
    const floor = JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!);
    localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify({ ...floor, seq: 99 }));
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(await screen.findByText(K.ROLLED_BACK)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start again" })).toBeNull();
    await act(async () => { fireEvent.click(button("Trust the server's keys", screen.getByRole("group", { name: "Keys older than this browser's" }))); });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Start again" })).toBeTruthy());
    // the same keys said to be classical, where this browser saw them post-quantum: starting again is not offered
    server.pq = false;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect((await screen.findAllByText(K.ROLLED_BACK_CLASSICAL)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Start again" })).toBeNull();
    expect(screen.queryByText(/Unlock to turn it on again with these keys, or start again/)).toBeNull();
    expect(screen.queryByRole("group", { name: "Keys older than this browser's" })).toBeNull();
    server.pq = true;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(await screen.findByRole("button", { name: "Start again" })).toBeTruthy();
  });

  it("refuses at unlock a copy from before the upgrade put back after the person trusted the server's keys: what it saw post-quantum is kept", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);                                    // classical
    // the account's keys as the server held them before the upgrade: a copy someone who can write its database keeps
    const classicalCopy = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    auth.choose = prefer("credAAAA");
    const ring = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const u = new K.Upgrade(ring, await K.E2EApi.view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    await u.touch("credAAAA");
    await u.signAndSend();
    server.settle();
    // post-quantum, then its keys replaced (a new master key): this browser opens the newest
    const upgradedCopy = JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    const pqRing = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(pqRing, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    render(<EncryptionPane />);
    await unlock("credAAAA");                                            // this browser sees the account post-quantum
    expect(useE2EStore.getState().ring?.pq).toBe(true);
    act(() => useE2EStore.getState().lock());
    // put back to before the new master key: the keys do not lead back to the newest it opened, and the person trusts them
    Object.assign(server, upgradedCopy);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Unlock" })); });
    // the unlock refused before the person trusts the keys: its button free again and its words beside the status's.
    // The group is there before the unlock is answered, drawn from the view and this browser's floor; an unlock whose
    // touch is answered after Trust has dropped the floor, while the server still holds these keys, opens them and
    // raises the floor again
    await vi.waitFor(() => {
      expect(button("Unlock").disabled).toBe(false);
      expect(screen.getAllByText(K.ROLLED_BACK).length).toBeGreaterThan(1);
    });
    const group = await screen.findByRole("group", { name: "Keys older than this browser's" });
    await act(async () => { fireEvent.click(button("Trust the server's keys", group)); });
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    // then the copy from before the upgrade is put back: unlocking it is refused all the same, and nothing is opened
    Object.assign(server, classicalCopy);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Unlock" })); });
    await vi.waitFor(() => expect(screen.getAllByText(K.ROLLED_BACK_CLASSICAL).length).toBeGreaterThan(1));
    expect(useE2EStore.getState().ring).toBeNull();
  });

  it("says before any code is shown that turning on cannot be done where the server has no ML-KEM", async () => {
    server.mlkem = false;
    render(<EncryptionPane />);
    const on = await screen.findByRole("button", { name: "Turn on" });
    expect((on as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(K.NO_MLKEM)).toBeTruthy();
    fireEvent.click(on);
    expect(screen.queryByLabelText("The recovery code")).toBeNull();
    // with ML-KEM it is offered as before
    cleanup();
    server.mlkem = true;
    useE2EStore.setState(useE2EStore.getInitialState());
    render(<EncryptionPane />);
    const again = await screen.findByRole("button", { name: "Turn on" });
    expect((again as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(K.NO_MLKEM)).toBeNull();
  });
});

describe("sealing in place at a post-quantum turn-on", () => {
  it("says how many conversations it deleted, which their links send again under the new key (the view's sealed.dropped)", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    const said = async (sealed: Record<string, number>) => {
      server.sealed = { ...sealed, at: server.now };
      useE2EStore.setState(useE2EStore.getInitialState());
      render(<EncryptionPane />);
      const line = (await screen.findByText(/^Sealed in place at /)).textContent;
      cleanup();
      return line;
    };
    expect(droppedWords(1)).toBe("1 conversation that held a message sealed already, to an earlier key, could not be sealed whole and was deleted: its linked session sends it again under the new key, if it still runs and lists that session");
    expect(droppedWords(2)).toBe("2 conversations that held a message sealed already, to an earlier key, could not be sealed whole and were deleted: their linked sessions send them again under the new key, if they still run and list those sessions");
    expect(await said({ events: 2, left: 1, rows: 1, talks: 1, dropped: 2 }))
      .toMatch(new RegExp(`: 4 texts linked sessions had sent in the clear; 1 could not be, sent without an id to seal them by; ${droppedWords(2)}\\.`));
    // none deleted, or none said: nothing of it
    for (const sealed of [{ events: 2, left: 0, rows: 1, talks: 1 }, { events: 2, left: 0, rows: 1, talks: 1, dropped: 0 }] as Record<string, number>[]) {
      expect(await said(sealed)).toMatch(/: 4 texts linked sessions had sent in the clear\.$/);
    }
  });
});

describe("the links the upgrade leaves holding their texts", () => {
  it("says apart the links that said they cannot seal post-quantum and those that have not said yet", () => {
    const link = (link_id: string, seals?: number[] | null) => ({ link_id, name: link_id.toUpperCase(), key_id: null, last_required_at: null, pins: null,
      ...(seals === undefined ? {} : { seals }) });
    const words = (...links: ReturnType<typeof link>[]) => notPqWords(K.readView({ links }));
    const CANNOT_ONE = "This linked session says it cannot seal to a post-quantum key, its cryptography library being unable to use ML-KEM: “B”. Once the upgrade takes effect it holds its texts until that library is updated there (a cryptography wheel of 48.0.0 or later from PyPI).";
    const UNSAID_ONE = "This linked session has not said yet what it can seal: “C”. A session of this release says so each time it reads the keys, so it may say it on its next read; one from before this release never does, and once the upgrade takes effect holds its texts until ade_link.py is updated there.";
    const END = "Nothing is sent in the clear.";
    expect(words(link("a", [1, 2, 3]))).toBeNull();
    expect(words(link("a", [1, 2, 3]), link("b", [1]))).toBe(`${CANNOT_ONE} ${END}`);
    expect(words(link("c", null))).toBe(`${UNSAID_ONE} ${END}`);
    expect(words(link("c"))).toBe(`${UNSAID_ONE} ${END}`);                      // a view from before the field
    expect(words(link("b", [1]), link("c", null))).toBe(`${CANNOT_ONE} ${UNSAID_ONE} ${END}`);
    expect(words(link("b", [1]), link("d", [1]), link("c", null), link("e"))).toBe(
      "These linked sessions say they cannot seal to a post-quantum key, their cryptography library being unable to use ML-KEM: “B”, “D”. Once the upgrade takes effect they hold their texts until that library is updated there (a cryptography wheel of 48.0.0 or later from PyPI). "
      + "These linked sessions have not said yet what they can seal: “C”, “E”. A session of this release says so each time it reads the keys, so each may say it on its next read; one from before this release never does, and once the upgrade takes effect holds its texts until ade_link.py is updated there. "
      + END);
  });
});

describe("a post-quantum account where the server has no ML-KEM", () => {
  const reload = () => act(async () => { await useE2EStore.getState().load(ACCOUNT); });

  it("says so on turning encryption off and renewing anchors before any touch, and on a screen opened while it had ML-KEM", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    server.mlkem = false;
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const nonces = () => server.calls.filter((c) => c.path === "/api/ade/link/nonce").length;
    const [asked, touches] = [nonces(), auth.ceremonies.length];
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    expect(await screen.findByText(K.NO_MLKEM)).toBeTruthy();
    expect(button("Sign turning it off").disabled).toBe(true);
    fireEvent.click(button("Cancel"));
    fireEvent.click(button("Renew anchors"));
    const group = await screen.findByRole("group", { name: "Renewing anchors" });
    expect(await within(group).findByText(K.NO_MLKEM)).toBeTruthy();
    expect(within(group).queryByRole("button", { name: "Laptop" })).toBeNull();
    expect(button("Send", group).disabled).toBe(true);
    expect([nonces(), auth.ceremonies.length]).toEqual([asked, touches]);
    // a screen opened while the server could use ML-KEM is prepared again once it cannot, and refused before a touch
    fireEvent.click(button("Cancel", group));
    server.mlkem = true;
    await reload();
    fireEvent.click(button("Renew anchors"));
    const again = await screen.findByRole("group", { name: "Renewing anchors" });
    expect(await within(again).findByRole("button", { name: "Laptop" })).toBeTruthy();
    expect(within(again).queryByText(K.NO_MLKEM)).toBeNull();
    server.mlkem = false;
    await reload();
    expect(await within(again).findByText(K.NO_MLKEM)).toBeTruthy();
    expect(within(again).queryByRole("button", { name: "Laptop" })).toBeNull();
  });

  it("offers neither the recovery code nor a revocation to cancel a change that waits, and says why: the code's proof needs ML-KEM", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", false);
    server.pending = { id: "hold-r", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    render(<EncryptionPane />);
    await reload();
    // while the server can use ML-KEM: the passkey or the code cancels it, and removing the passkey that made it is said
    expect(await screen.findByText("Signing in alone does not cancel it: it takes a touch of Laptop, or the recovery code.")).toBeTruthy();
    expect(screen.getByLabelText("The recovery code, to cancel it")).toBeTruthy();
    expect(screen.getByText(/^Not yours\? Remove the passkey that made it/)).toBeTruthy();
    server.mlkem = false;
    await reload();
    expect(screen.getByText(`Signing in alone does not cancel it: it takes a touch of Laptop. ${K.CODE_NEEDS_MLKEM}`)).toBeTruthy();
    expect(screen.queryByLabelText("The recovery code, to cancel it")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Cancel it with the (recovery )?code$/ })).toBeNull();
    expect(screen.queryByText(/^Not yours\?/)).toBeNull();
    expect(screen.getByRole("button", { name: /Cancel it with a passkey/ })).toBeTruthy();
    // a change the code made: only the code cancels it, which it cannot now
    server.pending = { id: "hold-c", change: "off", takes_effect_at: server.now + 86400, apply: () => {}, madeBy: "recovery", byCode: true, body: {} };
    await reload();
    expect(screen.getByText(`Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code. ${K.CODE_NEEDS_MLKEM}`)).toBeTruthy();
    expect(screen.queryByLabelText("The recovery code, to cancel it")).toBeNull();
    // once it can again, the code is offered again
    server.mlkem = true;
    await reload();
    expect(screen.getByText("Signing in alone does not cancel it, and no passkey of yours can: it takes the recovery code.")).toBeTruthy();
    expect(screen.getByLabelText("The recovery code, to cancel it")).toBeTruthy();
  });

  it("turns off the upgrade's next step, touches and signature once the server loses ML-KEM on a screen opened while it had it", async () => {
    passkey("credAAAA", "Laptop");
    passkey("credBBBB", "Phone");
    await turnedOn("credAAAA", true);
    await enrolled("credBBBB", "credAAAA");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Make a new recovery code", group));
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    // the code typed back, then the server loses ML-KEM: nothing is prepared
    server.mlkem = false;
    await reload();
    expect(within(group).getByText(K.NO_MLKEM)).toBeTruthy();
    expect(button("Next", group).disabled).toBe(true);
    server.mlkem = true;
    await reload();
    expect(within(group).queryByText(K.NO_MLKEM)).toBeNull();
    await act(async () => { fireEvent.click(button("Next", group)); });
    const keep = await within(group).findByRole("list", { name: "The passkeys to keep" });
    // prepared, then the server loses ML-KEM: no passkey is touched and nothing is signed
    server.mlkem = false;
    await reload();
    const phone = within(keep).getByText("Phone").closest("li")!;
    expect(within(group).getByText(K.NO_MLKEM)).toBeTruthy();
    expect([button("Touch it to keep it", phone).disabled, button("Sign the upgrade", group).disabled]).toEqual([true, true]);
    const [calls, touches] = [server.calls.length, auth.ceremonies.length];
    fireEvent.click(button("Touch it to keep it", phone));
    fireEvent.click(button("Sign the upgrade", group));
    expect([server.calls.length, auth.ceremonies.length]).toEqual([calls, touches]);
    // once it can again, both are offered again
    server.mlkem = true;
    await reload();
    expect([button("Touch it to keep it", phone).disabled, button("Sign the upgrade", group).disabled]).toEqual([false, false]);
  });

  it("prepares the upgrade from the account as read when Next is pressed, not as it was when the screen opened", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    fireEvent.click(button("Upgrade"));
    const group = screen.getByRole("group", { name: "Upgrading to post-quantum keys" });
    fireEvent.click(button("Make a new recovery code", group));
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    // a change placed elsewhere since the screen opened: preparing from the account as it is now refuses it
    server.pending = { id: "hold-w", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    await reload();
    await act(async () => { fireEvent.click(button("Next", group)); });
    expect((await within(group).findByRole("alert")).textContent).toBe(K.UPGRADE_WAITING);
    expect(within(group).queryByRole("list", { name: "The passkeys to keep" })).toBeNull();
  });

  it("turns off the turn-on's next step, touch and second touch once the server loses ML-KEM after its code is shown", async () => {
    passkey("credAAAA", "Laptop");
    render(<EncryptionPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    const group = screen.getByRole("group", { name: "Turning encryption on" });
    const code = within(group).getByLabelText("The recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
    server.mlkem = false;
    await reload();
    expect(within(group).getByText(K.NO_MLKEM)).toBeTruthy();
    expect(button("Next", group).disabled).toBe(true);
    server.mlkem = true;
    await reload();
    expect(within(group).queryByText(K.NO_MLKEM)).toBeNull();
    fireEvent.click(button("Next", group));
    await vi.waitFor(() => expect(button(/Sign with a passkey/, group).disabled).toBe(false));
    server.mlkem = false;
    await reload();
    expect(button(/Sign with a passkey/, group).disabled).toBe(true);
    expect(within(group).getByText(K.NO_MLKEM)).toBeTruthy();
    server.mlkem = true;
    await reload();
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button(/Sign with a passkey/, group)); });
    await within(group).findByRole("button", { name: /Touch it again/ });
    server.mlkem = false;
    await reload();
    expect(button(/Touch it again/, group).disabled).toBe(true);
    server.mlkem = true;
    await reload();
    expect(button(/Touch it again/, group).disabled).toBe(false);
  });

  it("still offers a classical account's recovery code and revocation to cancel a change that waits, which need no ML-KEM", async () => {
    passkey("credAAAA", "Laptop");
    await turnedOn("credAAAA", true);
    server.pending = { id: "hold-r", change: "rotate", takes_effect_at: server.now + 180, apply: () => {}, madeBy: "pk-credAAAA", body: {} };
    server.mlkem = false;
    render(<EncryptionPane />);
    await reload();
    expect(await screen.findByText("Signing in alone does not cancel it: it takes a touch of Laptop, or the recovery code.")).toBeTruthy();
    expect(screen.getByLabelText("The recovery code, to cancel it")).toBeTruthy();
    expect(screen.getByText(/^Not yours\? Remove the passkey that made it/)).toBeTruthy();
  });
});
