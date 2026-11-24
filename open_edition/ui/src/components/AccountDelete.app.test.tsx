import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Deleting the account in the phone app (STORE-READY, queue 4.328; the integrating session's ruling of 21:25 UTC on
// 2026-11-23): where a passkey or high security makes the deletion, or its cancel, a browser's step, the words name the
// server's address as a link straight to Account's tab there (App.tsx's #account), opened in the phone's browser as the
// privacy link is, since Apple's page on account deletion asks for "a link directly to the page on your website where
// they can complete the process". Where the password confirms it in the app there is none, and no other change's
// words link anywhere. The mode is set before any module is read, as the phone build sets it.
vi.hoisted(() => { vi.stubEnv("MODE", "phone"); });

import { useStore } from "../store";
import { AccountDialog } from "./AccountDialog";

const SERVER = "https://pp.example";
const ACCOUNT_TAB = "https://pp.example/app/#account";
type Route = () => [number, unknown];
const serve = (routes: Record<string, Route>) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(String(url), SERVER).pathname;
    const route = routes[`${init.method || "GET"} ${path}`];
    const [status, data] = route ? route() : [404, { detail: `no route for ${init.method || "GET"} ${path}` }];
    return new Response(JSON.stringify(data), { status });
  }));
};
const owner = { id: "u", email: "a@example.org", can_sign_in_again: true };
const KEY = { id: "k1", name: "Phone", created_at: 1, last_used_at: null, backed_up: true };
const HOLD = "0123456789abcdef0123456789abcdef";
const AT = Math.floor(Date.now() / 1000) + 7 * 86400;
type Ways = Record<string, unknown>;
const account = (deletion: unknown, passkeys: unknown[], ways: Ways = {}): Record<string, Route> => ({
  "GET /api/account/passkeys": () => [200, { passkeys }],
  "GET /api/account/confirm": () =>
    [200, { needed: true, password: true, passkeys: passkeys.length, hardware: 0, ...ways }],
  "GET /api/account/delete": () => [200, { deletion, waits: 7 * 86400 }],
  "GET /api/ade/teammate/custody": () => [200, { as_owner: null, as_custodian: [], tasks: [] }],
});
const section = async () =>
  (await screen.findByRole("heading", { name: "Delete this account" })).closest("section") as HTMLElement;
const opened = async (button: string, group: string) => {
  fireEvent.click(await within(await section()).findByRole("button", { name: button }));
  return screen.findByRole("group", { name: group });
};
/** The link to Account's tab at the server's address, a new window's link, which the opener plugin's script opens in
 *  the phone's browser under the app's one grant (phone/src-tauri/capabilities/default.json; lib.rs's test of it). */
const toAccountTab = (panel: HTMLElement) => {
  const link = within(panel).getByRole("link", { name: "pp.example" });
  expect(link.getAttribute("href")).toBe(ACCOUNT_TAB);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  useStore.setState(useStore.getInitialState());
});

describe("deleting the account in the phone app, where a browser must confirm it", () => {
  it("names the server's address as a link to Account's tab there, for an account with a passkey", async () => {
    localStorage.setItem("pp-open-server", SERVER);
    serve(account(null, [KEY]));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const panel = await opened("Delete this account", "Deleting this account");
    expect(panel.textContent).toContain("Passkeys work only in a browser at pp.example.");
    toAccountTab(panel);
    // nothing in the app confirms it: no password, no passkey of the app's own
    expect(within(panel).queryByLabelText("Your password")).toBeNull();
    expect(within(panel).queryByRole("button", { name: /Use a passkey|Use a hardware key/ })).toBeNull();
  });

  it("names it so too under high security, which takes a hardware key in that browser", async () => {
    localStorage.setItem("pp-open-server", SERVER);
    serve(account(null, [KEY], { hardware: 1, high_security: true }));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const panel = await opened("Delete this account", "Deleting this account");
    expect(panel.textContent)
      .toContain("takes one of your hardware keys, and they work only in a browser at pp.example: confirm it there.");
    toAccountTab(panel);
  });

  it("names it so for cancelling a deletion that only a passkey may cancel", async () => {
    localStorage.setItem("pp-open-server", SERVER);
    const waiting = { id: HOLD, approved_at: AT - 7 * 86400, takes_effect_at: AT, made_with: "passkey", checked: null,
      told: 1, cancel_by: [KEY.id] };
    serve(account(waiting, [KEY]));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const panel = await opened("Cancel the deletion", "Cancelling the deletion");
    expect(panel.textContent).toContain("Passkeys work only in a browser at pp.example.");
    toAccountTab(panel);
  });
});

describe("near misses in the phone app", () => {
  it("an account with no passkey confirms its deletion here with its password: no link", async () => {
    localStorage.setItem("pp-open-server", SERVER);
    serve(account(null, []));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const panel = await opened("Delete this account", "Deleting this account");
    expect(within(panel).getByLabelText("Your password")).toBeTruthy();
    expect(within(panel).queryByRole("link")).toBeNull();
  });

  it("another change a browser must confirm names the server's address in words, not as a link", async () => {
    localStorage.setItem("pp-open-server", SERVER);
    serve(account(null, [KEY], { hardware: 1, high_security: true }));
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await section();
    fireEvent.click(await screen.findByRole("button", { name: /Sign out everywhere/ }));
    const panel = await screen.findByRole("group", { name: "Signing out everywhere" });
    expect(panel.textContent).toContain("they work only in a browser at pp.example: confirm it there.");
    expect(within(panel).queryByRole("link")).toBeNull();
  });
});
