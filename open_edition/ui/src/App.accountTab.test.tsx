import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { browser, OWNER, shellServer } from "./shellTestkit";
import { useStore } from "./store";

// The browser page the phone app's deletion step links to (STORE-READY, queue 4.328; AccountDelete.app.test.tsx):
// SERVER/app/#account opens Account's tab, the deletion at its foot, once the person has signed in there, as a phone's
// browser that is not signed in yet opens it; the address alone opens nothing. The whole App, against the shell's
// server of the test's own, its sign-in and Account's reads added.

// Account loads as it would in the browser (lazy); its code is read once here, so the near miss waits on no load
beforeAll(async () => { await import("./components/AccountDialog"); }, 60000);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, "", "/");
  useStore.setState(useStore.getInitialState());
});

function signInServer() {
  browser();
  shellServer();
  const shell = globalThis.fetch;
  const json = (data: unknown) =>
    new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
  const added: Record<string, () => Response> = {
    "POST /api/auth/login": () => json({ token: "test-token.sig", user: OWNER }),
    "GET /api/account/passkeys": () => json({ passkeys: [] }),
    "GET /api/account/confirm": () => json({ needed: true, password: true, passkeys: 0, hardware: 0 }),
    "GET /api/account/delete": () => json({ deletion: null, waits: 7 * 86400 }),
    "GET /api/ade/teammate/custody": () => json({ as_owner: null, as_custodian: [], tasks: [] }),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const route = added[`${init.method || "GET"} ${new URL(String(input), "http://localhost").pathname}`];
    return route ? route() : shell(input, init);
  }));
}

async function signIn() {
  fireEvent.change(await screen.findByLabelText("Email", {}, { timeout: 10000 }), { target: { value: OWNER.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a password" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("the browser page an app's deletion step links to", () => {
  it("opens Account's tab, with Delete this account, once the person signs in there", async () => {
    signInServer();
    history.replaceState(null, "", "/app/#account");
    render(<App />);
    await signIn();
    expect(await screen.findByRole("heading", { name: "Delete this account" }, { timeout: 10000 })).toBeTruthy();
  });

  it("near miss: the same page with no #account opens no Account once signed in", async () => {
    signInServer();
    history.replaceState(null, "", "/app/");
    render(<App />);
    await signIn();
    const menu = await screen.findByRole("button", { name: `Your account, ${OWNER.email}` }, { timeout: 10000 });
    expect(menu).toBeTruthy();
    await new Promise((r) => setTimeout(r, 300));               // time for an Account the address opened to show
    expect(screen.queryByRole("heading", { name: "Delete this account" })).toBeNull();
  });
});
