// The Encryption panel's fold: where a classical account is turned on again while a new
// master key is due, the page offers to make that key as the upgrade does, in the same change. Offered and said where it
// may be made, and not where it may not; its screen end to end against the test server; and where it waits, named.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as K from "../e2eKeys";
import { useE2EStore } from "../e2e";
import { confirmWithPasskey } from "../passkey";
import { CLASSICAL, FakeAuthenticator, FakeServer } from "../e2eTestkit";
import { useStore } from "../store";
import { CLASSICAL_WORDS, EncryptionPane, FOLD_ABOVE, FOLD_CANCEL_WORDS, FOLD_INTRO, FOLD_MEANWHILE, FOLD_NEW_CODE_WORDS, FOLD_OTHER_TABS,
  FOLD_UNLOCK, FOLD_WAITS_ABOVE, foldInPlaceWords, foldOfferWords, notPqWords, PQ_WORDS, waitWords } from "./Encryption";

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
const button = (name: string | RegExp, within_: HTMLElement = document.body) => within(within_).getByRole("button", { name }) as HTMLButtonElement;
const NAMES: Record<string, string> = { credAAAA: "Laptop", credBBBB: "Phone", credCCCC: "Key" };

/** A classical account (an older release's keys; post-quantum ones with `pq`) turned on with `ids`' passkeys, the first's
 *  touch, the others added, turned off, and `gone` removed from the account while it was off, as Account removes one:
 *  a new master key is then due. The recovery code as it was shown. */
async function classicalDue(ids = ["credAAAA", "credBBBB", "credCCCC"], gone: string | null = "credCCCC", pq = false) {
  for (const id of ids) {
    server.addPasskey(id, NAMES[id]);
    auth.add(id);
  }
  const t = new K.TurnOn(ACCOUNT, false, pq ? {} : CLASSICAL);
  const code = t.codeText();
  await t.prepare(await K.E2EApi.view());
  auth.choose = prefer(ids[0]);
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  for (const id of ids.slice(1)) {
    const v = await K.E2EApi.view();
    auth.choose = prefer(ids[0]);
    const ring = await K.unlockWithTouch(v, ACCOUNT);
    const e = new K.Enrol(ring, v, v.passkeys.find((p) => p.credential_id === id)!);
    await e.prepare();
    auth.choose = prefer(id);
    await e.firstTouch();
    await e.secondTouch();
    auth.choose = prefer(ids[0]);
    await e.confirmAndSend();
    server.settle();
  }
  const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
  await off.prepare();
  auth.choose = prefer(ids[0]);
  await off.signAndSend();
  server.settle();
  if (gone) {
    auth.choose = prefer(ids[0]);
    const confirmation = await confirmWithPasskey(false, { change: `passkey-remove:pk-${gone}` });
    expect((await server.fetch(`/api/account/passkeys/pk-${gone}`, { method: "DELETE", body: JSON.stringify({ confirmation }) })).status).toBe(200);
  }
  return code;
}

async function unlock(id: string) {
  auth.choose = prefer(id);
  await act(async () => {
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
  });
}

/** The fold's screen opened, the recovery code chosen (kept, typed, or a new one, typed back) and Next pressed: the
 *  screen, and its list of the passkeys that unlock encryption. */
async function opened(keep?: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Turn it on again with post-quantum keys" }));
  const group = screen.getByRole("group", { name: "Turning encryption on again with post-quantum keys" });
  if (keep) {
    fireEvent.click(button("Keep my recovery code", group));
    fireEvent.change(within(group).getByLabelText("Your recovery code"), { target: { value: keep } });
  } else {
    fireEvent.click(button("Make a new recovery code", group));
    expect(within(group).getByText(FOLD_NEW_CODE_WORDS)).toBeTruthy();
    const code = within(group).getByLabelText("The new recovery code").textContent!;
    const asked = Number(/Type group (\d) of the code/.exec(group.textContent!)![1]);
    fireEvent.change(within(group).getByLabelText(/Type group/), { target: { value: code.split(" ")[asked - 1] } });
  }
  await act(async () => { fireEvent.click(button("Next", group)); });
  const list = await within(group).findByRole("list", { name: "The passkeys that unlock it" });
  return { group, list };
}

async function touch(list: HTMLElement, id: string) {
  auth.choose = prefer(id);
  const li = within(list).getByText(NAMES[id]).closest("li")!;
  await act(async () => { fireEvent.click(button("Touch it", li)); });
  expect(await within(li).findByText("Its new key is made")).toBeTruthy();
}

describe("the fold, where turning a classical account on again makes a new master key", () => {
  it("is offered there once unlocked, beside turning it on again with these keys, and said so under Post-quantum keys", async () => {
    await classicalDue();
    render(<EncryptionPane />);
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${FOLD_UNLOCK}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    await unlock("credAAAA");
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${FOLD_ABOVE}`)).toBeTruthy();
    const v = useE2EStore.getState().view!;
    expect(screen.getByText(foldOfferWords(v))).toBeTruthy();
    expect(foldOfferWords(v)).toContain("and it waits a day.");
    const fold = button("Turn it on again with post-quantum keys");
    const again = button("Turn it on again");
    expect(fold.className).toContain("btn-primary");
    expect(again.className).not.toContain("btn-primary");
    await vi.waitFor(() => expect(again.disabled).toBe(false));
  });

  it("is not offered where the server has no ML-KEM, which is said; turning it on again with these keys still is", async () => {
    await classicalDue();
    server.mlkem = false;
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${K.UPGRADE_OFF}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    expect(screen.getAllByText(K.NO_MLKEM).some((n) => n.getAttribute("role") === "note")).toBe(true);
    const again = button("Turn it on again");
    expect(again.className).toContain("btn-primary");
  });

  it("is not offered to a post-quantum account, whose new master key is made post-quantum as it is turned on again", async () => {
    await classicalDue(["credAAAA", "credBBBB", "credCCCC"], "credCCCC", true);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByText(PQ_WORDS)).toBeTruthy();
    expect(screen.getByText(/no longer unlocks encryption: removed, or left out by high security, while it was off/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    expect(button("Turn it on again").className).toContain("btn-primary");
    expect(screen.queryByText(K.NO_MLKEM)).toBeNull();
  });

  it("is said under Post-quantum keys wherever turning it on again offers it: a new master key due by this tab's roster, though the server says none", async () => {
    await classicalDue(["credAAAA", "credBBBB"], null);
    // Phone's credential gone from what the server lists, the server not saying a new master key is due: the roster this
    // tab opened names it, so turning it on again makes one (K.rekeyDue)
    server.credentials = server.credentials.filter((c) => c.credential_id !== "credBBBB");
    server.grants = server.grants.filter((g) => g.to !== "credBBBB");
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(useE2EStore.getState().view!.rekey_due).toBe(false);
    expect(await screen.findByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeTruthy();
    expect(screen.getByText(`${CLASSICAL_WORDS} ${FOLD_ABOVE}`)).toBeTruthy();
  });

  it("is not offered where no new master key is due: the upgrade follows turning it on again", async () => {
    await classicalDue(["credAAAA", "credBBBB"], null);
    render(<EncryptionPane />);
    await unlock("credAAAA");
    expect(await screen.findByText(`${CLASSICAL_WORDS} ${K.UPGRADE_OFF}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    expect(button("Turn it on again").className).toContain("btn-primary");
  });

  it("is not offered, nor said to be offered above, while another change waits: that one is cancelled first", async () => {
    await classicalDue();
    render(<EncryptionPane />);
    await unlock("credAAAA");
    // offered while nothing waits
    expect(await screen.findByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeTruthy();
    expect(screen.getByText(`${CLASSICAL_WORDS} ${FOLD_ABOVE}`)).toBeTruthy();
    // place a classical turn-on again with a new master key, which waits on its hold; encryption stays off meanwhile
    auth.choose = prefer("credAAAA");
    const ring = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const again = new K.TurnOnAgain(ring, await K.E2EApi.view());
    await again.prepare();
    await again.signAndSend(true);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    const v = useE2EStore.getState().view!;
    expect(v.pending).toBeTruthy();
    expect(v.on).toBe(false);
    // the fold button is gone and Post-quantum keys no longer says it is offered above (both without the !view.pending
    // guards would still show: the button enabled, the line FOLD_ABOVE, a new code shown before the first step refuses)
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    expect(screen.queryByText(`${CLASSICAL_WORDS} ${FOLD_ABOVE}`)).toBeNull();
  });

  it("folds end to end: a new code shown and typed back, each passkey touched but one, one signature; named where it waits, and the new keys opened once it takes effect", async () => {
    await classicalDue();
    server.links = [{ link_id: "l1", name: "Mac", key_id: null, last_required_at: null, pins: null, seals: [1] }];
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group, list } = await opened();
    expect(group.textContent).toContain(`${FOLD_INTRO} Touch each`);
    expect(within(group).getByText(FOLD_OTHER_TABS).getAttribute("role")).toBe("note");
    expect([...list.querySelectorAll("li")].map((li) => li.textContent!.replace("Touch it", "").trim())).toEqual(["Laptop", "Phone"]);
    // two untouched: neither signature would give the other its new key
    const sign = button("Sign turning it on again", group);
    expect(sign.disabled).toBe(true);
    await touch(list, "credBBBB");
    expect(sign.disabled).toBe(false);
    expect(group.textContent).toContain(`${waitWords(useE2EStore.getState().view!, "long")} ${FOLD_CANCEL_WORDS} ${FOLD_MEANWHILE}`);
    expect(within(group).queryByText(/^Not touched yet/)).toBeNull();          // the upgrade's removals: the fold makes none
    // a link that cannot seal post-quantum holds its texts once it takes effect, and sends in the clear until then
    const links = within(group).getByText(notPqWords(useE2EStore.getState().view!, true)!);
    expect(links.getAttribute("role")).toBe("note");
    expect(links.textContent).toContain("“Mac”. Once turning it on again takes effect it holds its texts until that library is updated there");
    expect(links.textContent).toMatch(/ Nothing is sent in the clear once it takes effect\.$/);
    const seal = within(group).getByLabelText("Seal in place what linked sessions sent in the clear since it was off.") as HTMLInputElement;
    expect(seal.checked).toBe(true);
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(sign); });
    const said = await screen.findByText(/^Turning encryption on again with post-quantum keys is signed\. It takes effect at/);
    expect(said.textContent).toContain("Linked sessions seal what they send from their next run after it, and what they sent in the clear while it was off is sealed in place. "
      + "If it is cancelled first, encryption stays off and the keys stay classical, and the new code unlocks nothing.");
    const body = bodyOf("/api/ade/e2e/turn-on")!;
    expect([body.upgrade, body.kept, body.seal, body.kem]).toEqual([true, false, true, "x-wing"]);
    // where it waits: named, made with Laptop, replacing the code; nothing checked of it; turning it on waits below
    const waits = screen.getByText(/^Turning encryption on again with post-quantum keys waits until/).closest("div[role=status]") as HTMLElement;
    expect(within(waits).getByText("Made with Laptop. It removes the recovery code.")).toBeTruthy();
    expect(within(waits).queryByText(/Checked with this tab's keys/)).toBeNull();
    expect(screen.getByText(`${CLASSICAL_WORDS} ${FOLD_WAITS_ABOVE}`)).toBeTruthy();
    expect(screen.getByText(/Turning it on waits on its hold, below/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn it on again with post-quantum keys" })).toBeNull();
    const waiting = useE2EStore.getState().waiting!;
    expect([waiting.chains, waiting.anchor]).toEqual([true, body.mk_id]);
    // its day passed: this tab opens the new keys with the signer's new key, no touch
    server.now = server.pending!.takes_effect_at;
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(server.refused).toEqual([]);
    const s = useE2EStore.getState();
    expect([s.why, !!s.waiting, s.ring?.pq, s.ring?.master.id]).toEqual(["", false, true, body.mk_id]);
    // they led back to the keys held, so this browser's floor rose to them at once
    expect(K.atFloor(s.ring!)).toBe(true);
    expect(K.floorOf(ACCOUNT)[0]).toBe(body.mk_id);
    expect(await screen.findByText(PQ_WORDS)).toBeTruthy();
  });

  it("keeps the code: typed and checked, sent with its proofs, held the code's day, which only the code cancels", async () => {
    const code = await classicalDue(["credAAAA", "credBBBB"], "credBBBB");    // Laptop alone unlocks it, so it signs at once
    render(<EncryptionPane />);
    await unlock("credAAAA");
    // not sealing in place, said before the screen opens: the screen starts so
    fireEvent.click(await screen.findByLabelText("Seal in place what linked sessions sent in the clear since it was off."));
    fireEvent.click(await screen.findByRole("button", { name: "Turn it on again with post-quantum keys" }));
    const group = screen.getByRole("group", { name: "Turning encryption on again with post-quantum keys" });
    fireEvent.click(button("Keep my recovery code", group));
    expect(within(group).getByText(/^Type the recovery code you have: it is checked against your keys, and its post-quantum key is made from it\. Keeping it, turning it on again goes with the code's proof, waits a day, and only the code cancels it\.$/)).toBeTruthy();
    fireEvent.change(within(group).getByLabelText("Your recovery code"), { target: { value: code } });
    await act(async () => { fireEvent.click(button("Next", group)); });
    await within(group).findByRole("list", { name: "The passkeys that unlock it" });
    expect(group.textContent).toContain(`With the recovery code it waits a day, and only the code cancels it then. ${FOLD_MEANWHILE}`);
    expect((within(group).getByLabelText("Seal in place what linked sessions sent in the clear since it was off.") as HTMLInputElement).checked).toBe(false);
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign turning it on again", group)); });
    const said = await screen.findByText(/^Turning encryption on again with post-quantum keys is signed\./);
    expect(said.textContent).toContain("elsewhere, unlock with a passkey that unlocks it now or the recovery code.");
    expect(said.textContent).toMatch(/encryption stays off and the keys stay classical\.$/);
    expect([bodyOf("/api/ade/e2e/turn-on")!.kept, bodyOf("/api/ade/e2e/turn-on")!.seal]).toEqual([true, false]);
    expect(said.textContent).toContain("Linked sessions seal what they send from their next run after it. If it is cancelled first");
    const waits = screen.getByText(/^Turning encryption on again with post-quantum keys waits until/).closest("div[role=status]") as HTMLElement;
    expect(within(waits).getByText("Made with Laptop and the recovery code's proof: only the code cancels it.")).toBeTruthy();
    expect(within(waits).queryByRole("button", { name: "Cancel it with a passkey" })).toBeNull();
  });

  it("a fold another tab signed is named where it waits, checked by none, and unlocking is not said to check it", async () => {
    await classicalDue();
    auth.choose = prefer("credAAAA");
    const ring = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const f = new K.Fold(ring, await K.E2EApi.view(), false);
    await f.useCode(f.codeText().split(" ")[f.group]);
    await f.prepare();
    await f.touch("credAAAA");
    auth.choose = prefer("credBBBB");
    await f.signAndSend();
    render(<EncryptionPane />);
    const waits = (await screen.findByText(/^Turning encryption on again with post-quantum keys waits until/)).closest("div[role=status]") as HTMLElement;
    expect(within(waits).queryByText(/Unlock encryption in this tab to check/)).toBeNull();
    expect(screen.getByText(`${CLASSICAL_WORDS} ${FOLD_WAITS_ABOVE}`)).toBeTruthy();
    await unlock("credAAAA");
    expect(within(waits).queryByText(/Checked with this tab's keys/)).toBeNull();
    expect(screen.getByText(`${CLASSICAL_WORDS} ${FOLD_WAITS_ABOVE}`)).toBeTruthy();
  });

  it("says, with the fold's words, that another turning-on took the place of the fold this tab signed", async () => {
    await classicalDue();
    render(<EncryptionPane />);
    await unlock("credAAAA");
    const { group, list } = await opened();
    await touch(list, "credBBBB");
    auth.choose = prefer("credAAAA");
    await act(async () => { fireEvent.click(button("Sign turning it on again", group)); });
    await screen.findByText(/^Turning encryption on again with post-quantum keys is signed\./);
    // another tab, with Phone, which may cancel it, turns it on again with these keys in its place
    auth.choose = prefer("credBBBB");
    const other = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const again = new K.TurnOnAgain(other, await K.E2EApi.view());
    await again.prepare();
    await again.signAndSend(true);
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    const v = useE2EStore.getState().view!;
    expect(screen.getByText(foldInPlaceWords(v, v.pending!))).toBeTruthy();
    expect(foldInPlaceWords(v, v.pending!)).toBe("Turning it on again with post-quantum keys, which this tab signed, no longer waits: one made with Phone waits in its place. "
      + "The keys this tab made are gone, and a new recovery code it showed unlocks nothing. If you did not make it, cancel it below before it takes effect.");
    expect(screen.queryByText(/The recovery code this tab showed will not unlock it/)).toBeNull();
  });
});
