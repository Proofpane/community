import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { dayTime } from "../when";
import { AccountDialog } from "./AccountDialog";

// Deleting the account from Account (account_delete.py): what it ends, how long it waits and that no email will come,
// said before anything is asked; a confirmation made for it, a passkey's while the account has one; the deletion that
// waits shown with its time and a cancel, which takes a confirmation made for that cancel; this browser keeps the hold's
// id, and the next time the page opens signed out, it says the deletion is done, from the server's record of the id
// alone. None of it on this Mac's own account.

type Call = { path: string; method: string; body: unknown };
type Route = (body: Record<string, unknown> | undefined) => [number, unknown];
const serve = (routes: Record<string, Route>) => {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
  return calls;
};
const owner = { id: "u", email: "a@example.org", can_sign_in_again: true };
const HOLD = "0123456789abcdef0123456789abcdef";
const AT = Math.floor(Date.now() / 1000) + 7 * 86400;
const waiting = { id: HOLD, approved_at: AT - 7 * 86400, takes_effect_at: AT, made_with: "password", checked: null, told: 1, cancel_by: ["password"] };
const KEPT = "proofpane.deletion";
const account = (deletion: unknown, passkeys: unknown[] = []): Record<string, Route> => ({
  "GET /api/account/passkeys": () => [200, { passkeys }],
  "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: passkeys.length, hardware: 0 }],
  "GET /api/account/delete": () => [200, { deletion, waits: 7 * 86400 }],
  // the Custody section's read, answered as for an account with no custodian (task_offers.custody)
  "GET /api/ade/teammate/custody": () => [200, { as_owner: null, as_custodian: [], tasks: [] }],
});
const section = async () => (await screen.findByRole("heading", { name: "Delete this account" })).closest("section") as HTMLElement;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.resetModules(); localStorage.clear(); useStore.setState(useStore.getInitialState()); });

describe("deleting the account, from Account", () => {
  it("says what it ends, how long it waits and that no email will come, before anything is asked", async () => {
    const calls = serve(account(null));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const box = await section();
    await waitFor(() => expect(box.textContent).toContain("waits 7 days"));
    for (const ends of ["Standing offers and custody", "nothing is revoked in Proofpane", "Delete the workspace now",
      "a workspace that is a folder on the server's own machine stays", "Connected apps, signers, hook tokens and links"])
      expect(box.textContent).toContain(ends);
    expect(box.textContent).toContain("No email will come");
    // what stays is said, so the words claim no more than the deletion does
    expect(box.textContent).toContain("other people's records that name it by id are kept as their own rules keep them, an address they typed for it cleared");
    expect(box.textContent).not.toContain("everything");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("places it with a confirmation made for it, then shows when it happens with a cancel, and keeps the hold's id", async () => {
    let placed = false;
    serve({
      ...account(null),
      // read again once it is placed: now it waits, its notice told to the devices the server counted by then
      "GET /api/account/delete": () => [200, { deletion: placed ? { ...waiting, told: 2 } : null, waits: 7 * 86400 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c1" }] : [403, { detail: "no" }]),
      "POST /api/account/delete": (b) => { placed = b?.confirmation === "c1"; return placed ? [200, { deletion: waiting, told: 1 }] : [403, { detail: "no" }]; },
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const box = await section();
    fireEvent.click(within(box).getByRole("button", { name: "Delete this account" }));
    const panel = await screen.findByRole("group", { name: "Deleting this account" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(box.textContent).toContain(`deleted at ${dayTime(AT)}`));
    expect(within(box).getByRole("button", { name: "Cancel the deletion" })).toBeTruthy();
    expect(box.textContent).toContain("No email will come");
    // how many devices its notice was sent toward, which is the owner's chance to cancel it from elsewhere
    expect(box.textContent).toContain("Its notice was sent toward 2 devices with notifications on.");      // the read's
    expect(JSON.parse(localStorage.getItem(KEPT) ?? "null")).toEqual({ hold: HOLD, account: "u" });
  });

  it("forgets nothing kept when its read finds the account gone, a sign-in that ended", async () => {
    localStorage.setItem(KEPT, JSON.stringify({ hold: HOLD, account: "u" }));
    const calls = serve({ ...account(waiting), "GET /api/account/delete": () => [401, { detail: "This account was deleted." }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/account/delete")).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(JSON.parse(localStorage.getItem(KEPT) ?? "null")).toEqual({ hold: HOLD, account: "u" });
  });

  it("names the deletion's own change in its confirmation", async () => {
    const calls = serve({
      ...account(null),
      "POST /api/account/confirm": () => [200, { confirmation: "c1" }],
      "POST /api/account/delete": () => [200, { deletion: waiting, told: 0 }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(within(await section()).getByRole("button", { name: "Delete this account" }));
    const panel = await screen.findByRole("group", { name: "Deleting this account" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/delete")).toBe(true));
    expect(calls.filter((c) => c.method === "POST" && c.path === "/api/account/confirm").map((c) => c.body))
      .toEqual([{ password: "right one", change: "account-delete" }]);
    expect(calls.filter((c) => c.path === "/api/account/delete" && c.method === "POST").map((c) => c.body))
      .toEqual([{ confirmation: "c1" }]);
  });

  it("does not offer the password while the account has a passkey", async () => {
    serve(account(null, [{ id: "k1", name: "Laptop", created_at: 1, last_used_at: null, backed_up: false }]));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(within(await section()).getByRole("button", { name: "Delete this account" }));
    const panel = await screen.findByRole("group", { name: "Deleting this account" });
    expect(within(panel).queryByLabelText("Your password")).toBeNull();
    expect(panel.textContent).toContain("your password does not confirm it");
  });

  it("cancels the deletion that waits with a confirmation made for that cancel, and forgets the hold's id", async () => {
    localStorage.setItem(KEPT, JSON.stringify({ hold: HOLD, account: "u" }));
    let cancelled = false;
    const calls = serve({
      ...account(waiting),
      "GET /api/account/delete": () => [200, { deletion: cancelled ? null : waiting, waits: 7 * 86400 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c2" }],
      [`POST /api/ade/holds/${HOLD}/cancel`]: (b) => { cancelled = b?.confirmation === "c2"; return cancelled ? [200, { id: HOLD }] : [403, { detail: "no" }]; },
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const box = await section();
    fireEvent.click(await within(box).findByRole("button", { name: "Cancel the deletion" }));
    const panel = await screen.findByRole("group", { name: "Cancelling the deletion" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await within(box).findByRole("button", { name: "Delete this account" });
    expect(calls.filter((c) => c.method === "POST" && c.path === "/api/account/confirm").map((c) => c.body))
      .toEqual([{ password: "right one", change: `account-delete-cancel:${HOLD}` }]);
    expect(localStorage.getItem(KEPT)).toBeNull();
  });

  it("says the operator placed one it did, and the check it was placed on", async () => {
    serve(account({ ...waiting, made_with: "admin", checked: "Asked by email from the address on file." }));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const box = await section();
    await waitFor(() => expect(box.textContent).toContain("This deployment's operator placed it, on a request checked so: Asked by email from the address on file."));
  });

  it("is not shown by a server that offers no deletion, an earlier release's, and raises no alert", async () => {
    const { "GET /api/account/delete": _, ...earlier } = account(null);
    const calls = serve(earlier);
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/account/delete")).toBe(true));
    await screen.findByText(/Sign out everywhere/);
    expect(screen.queryByRole("heading", { name: "Delete this account" })).toBeNull();
    expect(screen.queryAllByRole("alert")).toEqual([]);
  });

  it("offers no Delete while its read has failed, and says why", async () => {
    serve({ ...account(null), "GET /api/account/delete": () => [409, { detail: "A deletion of this account waits, but it could not be read back as it was written: ask this deployment's operator." }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const box = await section();
    await waitFor(() => expect(within(box).getByRole("alert").textContent).toContain("could not be read back"));
    expect(within(box).queryByRole("button", { name: "Delete this account" })).toBeNull();
    expect(box.textContent).not.toContain("its wait");                   // nothing said of a wait it has not read
  });

  it("is not on this Mac's own account, which has nothing to confirm a deletion with", async () => {
    serve(account(null));
    useStore.setState({ user: { ...owner, can_sign_in_again: false } });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText(/Sign out everywhere/);
    expect(screen.queryByRole("heading", { name: "Delete this account" })).toBeNull();
  });

  it("near miss: in a browser, whose passkeys are the page's own, the words link nothing", async () => {
    // a server named, as the phone app names its own, so that a link would have an address: only an app build gives
    // one (AccountDelete.app.test.tsx); jsdom has no passkeys, so the words say where they work
    localStorage.setItem("pp-open-server", "https://pp.example");
    const at = (routes: Record<string, Route>) => Object.fromEntries(
      Object.entries(routes).map(([key, route]) => [key.replace(" /", " https://pp.example/"), route]));
    serve(at(account(null, [{ id: "k1", name: "Laptop", created_at: 1, last_used_at: null, backed_up: false }])));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await within(await section()).findByRole("button", { name: "Delete this account" }));
    const panel = await screen.findByRole("group", { name: "Deleting this account" });
    expect(panel.textContent).toContain("Passkeys work only in a browser at pp.example.");
    expect(within(panel).queryByRole("link")).toBeNull();
  });
});

describe("the next time the page opens, signed out", () => {
  it("says the deletion this browser placed is done, from the server's record of its id, and forgets the id", async () => {
    localStorage.setItem(KEPT, JSON.stringify({ hold: HOLD, account: "u" }));
    const done = AT;
    const calls = serve({ [`GET /api/account/deletion/${HOLD}`]: () => [200, { deleted_at: done }] });
    const { SignIn } = await import("./SignIn");
    render(<SignIn />);
    expect((await screen.findByRole("status")).textContent).toBe(`Your account was deleted at ${dayTime(done)}. Signing in with it no longer works.`);
    expect(localStorage.getItem(KEPT)).toBeNull();
    expect(calls.map((c) => c.path)).toEqual([`/api/account/deletion/${HOLD}`]);
  });

  it("says nothing while the deletion waits, and keeps the id to ask again", async () => {
    localStorage.setItem(KEPT, JSON.stringify({ hold: HOLD, account: "u" }));
    const calls = serve({});                                       // the server records no deletion done under it
    const { SignIn } = await import("./SignIn");
    render(<SignIn />);
    await vi.waitFor(() => expect(calls.map((c) => c.path)).toEqual([`/api/account/deletion/${HOLD}`]));
    expect(screen.queryByText(/Your account was deleted/)).toBeNull();
    expect(localStorage.getItem(KEPT)).not.toBeNull();
  });
});

describe("cancelling, in a browser that makes passkeys", () => {
  /** The dialog from modules loaded afresh in a browser that makes passkeys (passkeysHere is read as they load). */
  const fresh = async () => {
    vi.resetModules();
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: { get: async () => null }, configurable: true });
    const store = await import("../store");
    store.useStore.setState({ user: owner });
    const { AccountDialog } = await import("./AccountDialog");
    return AccountDialog;
  };
  const laptop = { id: "k1", name: "Laptop", created_at: 1, last_used_at: null, backed_up: false };
  afterEach(() => { delete (navigator as unknown as { credentials?: unknown }).credentials; });

  it("names who may cancel it, and offers no passkey where only the password may", async () => {
    serve(account({ ...waiting, cancel_by: ["password"] }, [laptop]));
    const Dialog = await fresh();
    render(<Dialog onClose={() => {}} />);
    const box = await section();
    await waitFor(() => expect(box.textContent).toContain("It can be cancelled with your password."));
    fireEvent.click(within(box).getByRole("button", { name: "Cancel the deletion" }));
    const panel = await screen.findByRole("group", { name: "Cancelling the deletion" });
    expect(within(panel).queryByRole("button", { name: /Use a passkey/ })).toBeNull();
    expect(within(panel).getByLabelText("Your password")).toBeTruthy();
  });

  it("says nothing of who may cancel before the passkeys are read, and still offers Cancel", async () => {
    const hardware = { ...waiting, made_with: "passkey", cancel_by: ["hw1"] };       // the high tier: hardware keys alone
    serve({ ...account(hardware, [laptop]), "GET /api/account/passkeys": () => [500, { detail: "The store is busy." }] });
    const Dialog = await fresh();
    render(<Dialog onClose={() => {}} />);
    const box = await section();
    await within(box).findByRole("button", { name: "Cancel the deletion" });
    expect(box.textContent).not.toContain("Nothing the account still has may cancel it");
  });

  it("names the passkeys that may cancel it, and offers them where they may", async () => {
    serve(account({ ...waiting, made_with: "passkey", cancel_by: ["k1", "password"] }, [laptop]));
    const Dialog = await fresh();
    render(<Dialog onClose={() => {}} />);
    const box = await section();
    await waitFor(() => expect(box.textContent).toContain("It can be cancelled with your password or the passkey Laptop."));
    fireEvent.click(within(box).getByRole("button", { name: "Cancel the deletion" }));
    const panel = await screen.findByRole("group", { name: "Cancelling the deletion" });
    expect(within(panel).getByRole("button", { name: /Use a passkey/ })).toBeTruthy();
  });
});
