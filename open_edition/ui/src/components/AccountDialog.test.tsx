import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { token } from "../api";
import { useStore } from "../store";
import { useE2EStore } from "../e2e";
import * as K from "../e2eKeys";
import type { CustodyOverview } from "../taskOffersApi";
import { AccountDialog, BuildLine, ConfirmIt, DeviceOrigin, withdrawnWords } from "./AccountDialog";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

describe("where a device's request came from, beside the name it gave", () => {
  const at = Date.UTC(2026, 9, 2, 8, 41) / 1000;

  it("says the same network when the server saw the approver there too", () => {
    const { container } = render(<DeviceOrigin seen={{ created_at: at, asked_from: "203.0.113.7", viewer: "203.0.113.7", same_network: true }} />);
    expect(container.textContent).toMatch(/from 203\.0\.113\.7, this browser's network too\.$/);
    expect(container.querySelector("[role=note]")).toBeNull();
  });

  it("warns when it came from another network, naming both", () => {
    const { container } = render(<DeviceOrigin seen={{ created_at: at, asked_from: "203.0.113.7", viewer: "198.51.100.2", same_network: false }} />);
    const note = container.querySelector("[role=note]");
    expect(note?.textContent).toContain("from 203.0.113.7, which is not this browser's network (198.51.100.2)");
    expect(note?.textContent).toContain("a link or a message from someone else would not");
  });

  it("says only when, if the server could not read an address", () => {
    const { container } = render(<DeviceOrigin seen={{ created_at: at, asked_from: null, viewer: null, same_network: null }} />);
    expect(container.textContent).toMatch(/^It asked at .+\.$/);
    expect(container.textContent).not.toContain("from");
  });
});

describe("the account, in a browser", () => {
  const passkeys = () => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ passkeys: [] }), { status: 200 })));

  it("of this Mac's own account (no password) still signs out and ends every sign-in, and says how it gets back", () => {
    passkeys();
    useStore.setState({ user: { id: "u", email: "you@this.desktop", can_sign_in_again: false } });
    render(<AccountDialog onClose={() => {}} />);
    expect(screen.getByRole("button", { name: /Sign out$/ })).toBeTruthy();
    expect(screen.getByText(/signed out here, this browser signs in again with another device/)).toBeTruthy();
    expect(screen.getByText(/Sign out everywhere/)).toBeTruthy();
    expect(screen.getByText(/The app on this Mac signs in again the next time it starts\./)).toBeTruthy();
    expect(screen.queryByText("Passkeys")).toBeNull();             // none can be made at its server's address
    expect(screen.queryByText("Signers")).toBeNull();              // nor a signer paired, which signs with a security key
  });

  it("of an account with a password has its passkeys too", () => {
    passkeys();
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    render(<AccountDialog onClose={() => {}} />);
    expect(screen.getByRole("button", { name: /Sign out$/ })).toBeTruthy();
    expect(screen.getByText("Passkeys")).toBeTruthy();
    expect(screen.getByText("Signers")).toBeTruthy();              // the Macs that sign the owner's steps (Signers.tsx)
    expect(screen.queryByText(/The app on this Mac/)).toBeNull();
    expect(screen.getByRole("button", { name: /^Account$/ }).getAttribute("aria-current")).toBe("page");     // the section shown
  });
});

describe("a change the sign-in alone does not make: removing a passkey, signing out everywhere, adding one", () => {
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
  const laptop = { id: "k1", name: "Laptop", created_at: 1, last_used_at: null, backed_up: false };
  const owner = { id: "u", email: "a@example.org", can_sign_in_again: true };
  const wrong: [number, unknown] = [403, { detail: "That did not confirm it: use one of this account's own passkeys, or its password." }];
  afterEach(() => { localStorage.clear(); });

  it("removes a passkey only once the password is given, after saying why", async () => {
    const calls = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [laptop] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 1 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c1" }] : wrong),
      "DELETE /api/account/passkeys/k1": (b) => (b?.confirmation === "c1" ? [200, { removed: "k1" }] : [403, { detail: "no" }]),
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const panel = await screen.findByRole("group", { name: "Removing \u201cLaptop\u201d" });
    // the keyboard goes to its first field, inside the group whose label is the change
    await waitFor(() => expect(document.activeElement).toBe(within(panel).getByLabelText("Your password")));
    expect(panel.textContent).toContain("takes your password as well as this sign-in, so a sign-in someone else has taken cannot do it");
    expect(within(panel).queryByRole("button", { name: /Use a passkey/ })).toBeNull();      // this browser makes none
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "wrong one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await within(panel).findByText(/That did not confirm it/);
    expect(within(panel).getByRole("alert").textContent).toMatch(/That did not confirm it/);   // announced
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: /Removing/ })).toBeNull());
    expect(calls.filter((c) => c.method === "DELETE")).toEqual([{ path: "/api/account/passkeys/k1", method: "DELETE", body: { confirmation: "c1" } }]);
    // the confirmation is made for removing this passkey, and nothing else
    expect(calls.filter((c) => c.method === "POST" && c.path === "/api/account/confirm").map((c) => c.body))
      .toEqual([{ password: "wrong one", change: "passkey-remove:k1" }, { password: "right one", change: "passkey-remove:k1" }]);
  });

  it("signs out everywhere with a confirmation, and keeps the new sign-in it is given", async () => {
    const calls = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c2" }] : wrong),
      "POST /api/account/sign-out-everywhere": (b) => (b?.confirmation === "c2" ? [200, { token: "new.token" }] : [403, { detail: "no" }]),
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    const panel = await screen.findByRole("group", { name: "Signing out everywhere" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await screen.findByText("Every other device is signed out.");
    expect(token()).toBe("new.token");
    expect(calls.find((c) => c.path.endsWith("/sign-out-everywhere"))?.body).toEqual({ confirmation: "c2" });
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/confirm")?.body).toEqual({ password: "right one", change: "sign-out-everywhere" });
  });

  it("reads its signers again once it has signed out everywhere, which revoked them", async () => {
    let signedOut = 0;
    serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c2" }],
      "POST /api/account/sign-out-everywhere": () => { signedOut += 1; return [200, { token: "new.token" }]; },
      // a Mac paired before each sign-out, which revokes it
      "GET /api/ade/signers": () => [200, { signers: signedOut >= 2 ? [] : [{ id: `s${signedOut}`, name: `Studio Mac ${signedOut}`, created_at: 1, last_seen_at: null }] }],
      "GET /api/ade/sign-requests": () => [200, { requests: [] }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const signOutEverywhere = async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
      const panel = await screen.findByRole("group", { name: "Signing out everywhere" });
      fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(screen.queryByRole("group", { name: "Signing out everywhere" })).toBeNull());
    };
    expect(await screen.findByRole("button", { name: "Revoke Studio Mac 0" })).toBeTruthy();
    await signOutEverywhere();
    expect(await screen.findByRole("button", { name: "Revoke Studio Mac 1" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Revoke Studio Mac 0" })).toBeNull();   // not left to answer 404
    await signOutEverywhere();                                                            // and again, each time
    expect(await screen.findByText("No Mac is paired as a signer.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Revoke Studio Mac/ })).toBeNull();
    expect(signedOut).toBe(2);
  });

  it("of this Mac's own account, which has nothing to confirm with, asks only to go ahead", async () => {
    const calls = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [] }],
      "GET /api/account/confirm": () => [200, { needed: false, password: false, passkeys: 0 }],
      "POST /api/account/sign-out-everywhere": () => [200, { token: "new.token" }],
    });
    useStore.setState({ user: { id: "u", email: "you@this.desktop", can_sign_in_again: false } });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    const panel = await screen.findByRole("group", { name: "Signing out everywhere" });
    expect(panel.textContent).toContain("Signing out everywhere?");
    expect(within(panel).queryByLabelText("Your password")).toBeNull();
    expect(calls.some((c) => c.path.endsWith("/sign-out-everywhere"))).toBe(false);          // not before the click
    fireEvent.click(within(panel).getByRole("button", { name: "Sign out everywhere" }));
    await screen.findByText("Every other device is signed out.");
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/confirm")).toBe(false);
    expect(calls.find((c) => c.path.endsWith("/sign-out-everywhere"))?.body).toEqual({});
  });

  const claude = { id: "g1", client_name: "Claude", returns_to: "claude.ai", described_at: null, scopes: ["ade.read"], state: "active",
    allowed_at: 1_790_000_000, connected_at: 1_790_000_010, takes_effect_at: 1_790_000_180, hold_id: "h1", ends_at: 1_797_776_000,
    revoked_at: null, revoked_why: null, last_used_at: null, last_net: null, proposals_waiting: 0 };
  const said = "Connected apps keep working until you revoke them in Connected apps; allowed in the last 7 days: “Claude”.";

  it("says, while the deployment lets apps connect, that signing out everywhere leaves connected apps working", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [] }], "GET /api/connector": () => [200, { on: true }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    expect(await screen.findByText(/Connected apps keep working until you revoke them in Connected apps\.$/)).toBeTruthy();
    cleanup();
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [] }], "GET /api/connector": () => [200, { on: false }] });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText("No passkeys yet.");
    expect(screen.queryByText(/Connected apps keep working/)).toBeNull();
  });

  it("lists, once every device is signed out, the apps allowed lately, each revoked with two clicks", async () => {
    const calls = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c2" }],
      "POST /api/account/sign-out-everywhere": () => [200, { token: "new.token", connected_apps: [claude], apps: said }],
      "DELETE /api/connector/grants/g1": () => [200, { ...claude, state: "revoked" }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    const panel = await screen.findByRole("group", { name: "Signing out everywhere" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    const left = await screen.findByRole("status", { name: "Connected apps still connected" });
    expect(left.textContent).toContain(said);
    const app = within(left).getByRole("listitem", { name: "Allowed lately: Claude" });
    expect(app.textContent).toContain("Its sign-in returns to claude.ai");
    fireEvent.click(within(app).getByRole("button", { name: /Revoke/ }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);                    // one click arms it only
    fireEvent.click(within(app).getByRole("button", { name: /Click again to revoke it/ }));
    expect(await within(app).findByText("Revoked.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/api/connector/grants/g1"]);
  });

  it("says what removing a passkey did to connected apps, as the server says it", async () => {
    serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [laptop] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 1 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c1" }],
      "DELETE /api/account/passkeys/k1": () => [200, { removed: "k1", disconnected: ["g1"], apps: "1 app connection it allowed was revoked. Other connected apps keep working until you revoke them in Connected apps." }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Laptop" }));
    const panel = await screen.findByRole("group", { name: "Removing “Laptop”" });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    expect((await screen.findByRole("status", { name: "Connected apps" })).textContent).toBe(
      "1 app connection it allowed was revoked. Other connected apps keep working until you revoke them in Connected apps.");
  });

  describe("in a browser that makes passkeys", () => {
    const bytes = (n: number) => new Uint8Array([n]).buffer;
    const order: string[] = [];
    const credentials = {
      get: vi.fn(async () => { order.push("get"); return { id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: null } }; }),
      create: vi.fn(async () => { order.push("create"); return { id: "Ag", type: "public-key", response: { clientDataJSON: bytes(4), attestationObject: bytes(5) } }; }),
    };
    const fresh = async () => {
      vi.resetModules();
      vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
      Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
      const { useStore: store } = await import("../store");
      store.setState({ user: owner });
      return (await import("./AccountDialog")).AccountDialog;
    };
    afterEach(() => {
      order.length = 0;
      credentials.get.mockClear();
      credentials.create.mockClear();
      delete (navigator as unknown as { credentials?: unknown }).credentials;
      vi.resetModules();
    });
    const newOptions = { challenge_id: "reg1", publicKey: {
      rp: { id: "pp.example", name: "Proofpane Community" }, user: { id: "dQ", name: "a@example.org", displayName: "a@example.org" },
      challenge: "AAAA", pubKeyCredParams: [{ type: "public-key", alg: -7 }], timeout: 300000, attestation: "none",
      authenticatorSelection: { userVerification: "required" }, excludeCredentials: [{ type: "public-key", id: "AQ" }] } };

    it("adds one in two steps: a passkey confirms, and the next click alone makes the new one", async () => {
      const calls = serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [laptop] }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 1 }],
        "POST /api/account/confirm/options": () => [200, { challenge_id: "ch1", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }],
        "POST /api/account/confirm": (b) => (b?.challenge_id === "ch1" && (b.credential as { id: string }).id === "AQ" ? [200, { confirmation: "c3" }] : wrong),
        "POST /api/account/passkeys/options": (b) => (b?.confirmation === "c3" ? [200, newOptions] : [403, { detail: "no" }]),
        "POST /api/account/passkeys": () => [200, { id: "k2", name: "Work laptop", created_at: 2, last_used_at: null, backed_up: false }],
      });
      const Dialog = await fresh();
      render(<Dialog onClose={() => {}} />);
      fireEvent.change(await screen.findByLabelText("The passkey's name"), { target: { value: "Work laptop" } });
      fireEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
      const panel = await screen.findByRole("group", { name: "Adding a passkey" });
      expect(panel.textContent).toContain("takes one of your passkeys or your password as well as this sign-in");
      fireEvent.click(within(panel).getByRole("button", { name: "Use a passkey" }));
      await screen.findByText(/Confirmed\. Now make the passkey on this device\./);
      // the next step takes the keyboard and is said
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Make the passkey" }));
      const make = screen.getByRole("button", { name: "Make the passkey" });
      expect(document.getElementById(make.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Confirmed. Now make the passkey on this device.");
      // only the account's own passkeys are offered, and nothing is made before its own click (Safari asks for one)
      expect((credentials.get.mock.calls[0] as unknown as [{ publicKey: { allowCredentials: unknown[] } }])[0].publicKey.allowCredentials).toHaveLength(1);
      expect(order).toEqual(["get"]);
      expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm", change: "passkey-add" });
      expect(calls.find((c) => c.path === "/api/account/passkeys/options")?.body).toEqual({ confirmation: "c3" });
      fireEvent.click(screen.getByRole("button", { name: "Make the passkey" }));
      await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/passkeys")).toBe(true));
      expect(order).toEqual(["get", "create"]);
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/passkeys")?.body).toMatchObject({ challenge_id: "reg1", name: "Work laptop" });
    });

    it("asks an account with no passkey yet only for its password", async () => {
      serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [] }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      });
      const Dialog = await fresh();
      render(<Dialog onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Add a passkey" }));
      const panel = await screen.findByRole("group", { name: "Adding a passkey" });
      expect(panel.textContent).toContain("takes your password as well as this sign-in");
      expect(within(panel).queryByRole("button", { name: /Use a passkey/ })).toBeNull();
      expect(within(panel).getByLabelText("Your password")).toBeTruthy();
    });
  });
});

describe("hardware keys and high security", () => {
  type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
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
  const yubikey = { id: "k1", name: "YubiKey", created_at: 1, last_used_at: null, backed_up: false, fingerprint: "a".repeat(32),
    tier: "hardware", maker: "Test Key 5 NFC", why: null, judge_again: false };
  const phone = { id: "k2", name: "iPhone", created_at: 2, last_used_at: null, backed_up: true, fingerprint: "b".repeat(32),
    tier: "device", maker: null, why: "It gave no attestation (a browser may ask before it shares one), so nothing shows it is a certified security key.", judge_again: false };
  const valid = { valid: true, next_update: "2026-11-01" };
  afterEach(() => { localStorage.clear(); });

  it("lists each passkey with its tier: a hardware key with its maker, a device passkey with why", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone], high_security: false, metadata: valid }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    expect(await screen.findByText("Hardware key: Test Key 5 NFC")).toBeTruthy();
    // a device passkey says its kind: this one is backed up, so synced, though the list names no kind
    expect(screen.queryByText(`Synced passkey: ${phone.why}`)).not.toBeNull();
    expect(screen.queryByText(/no valid copy of the FIDO metadata/)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Copy pin line" })).toHaveLength(2);
    expect(screen.queryByText(/rests on the browser's word/)).toBeNull();
  });

  it("gives no pin line for a passkey whose fingerprint is not in the server's form, nor copies it", async () => {
    const odd = { ...phone, id: "k3", name: "Odd", fingerprint: `${"c".repeat(32)} $(touch /tmp/pp-owned)` };
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, odd], high_security: false, metadata: valid }] });
    const written: string[] = [];
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t: string) => { written.push(t); } }, configurable: true });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    const row = (await screen.findByText("Odd")).closest("li") as HTMLElement;
    expect(within(row).queryByRole("button", { name: "Copy pin line" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Copy pin line" })).toHaveLength(1);        // the YubiKey's
    fireEvent.click(screen.getByRole("button", { name: "Copy fingerprints" }));
    await vi.waitFor(() => expect(written).toEqual(["a".repeat(32)]));
  });

  it("says of a hardware key whose model was reported since that it counts only until another is added", async () => {
    const why = "The FIDO metadata has since reported its model revoked.";
    const keys = [{ ...yubikey, reported: why }, { ...phone, reported: why }];
    // GET /api/ade/teammate/custody, which the account pane's Custody section reads (TaskCustody.tsx), as the app
    // answers an account with no custodian, asked by nobody and holding no task (task_offers.custody)
    const noCustody: CustodyOverview = { as_owner: null, as_custodian: [], tasks: [] };
    serve({
      "GET /api/account/passkeys": () => [200, { passkeys: keys, high_security: true, metadata: valid }],
      "GET /api/ade/teammate/custody": () => [200, noCustody],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText("Hardware key: Test Key 5 NFC");
    // the section is drawn from that answer before the alerts are counted, so the count covers it too
    const custodian = await screen.findByRole("group", { name: "Your custodian" });
    expect(within(custodian).getByText("You have no custodian.")).toBeTruthy();
    expect(screen.getAllByRole("alert").map((e) => e.textContent)).toEqual([`${why} It still counts, since high security is on and every hardware key of yours is reported: add another hardware key, then remove this one.`]);
  });

  it("says of a hardware key whose working with a phone rests on the browser's word that it does", async () => {
    const byBrowser = { ...yubikey, phone_by_browser: true };
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [byBrowser, { ...phone, phone_by_browser: true }], high_security: false, metadata: valid }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText("Hardware key: Test Key 5 NFC");
    expect(screen.getAllByText("That it works with a phone rests on the browser's word: the FIDO metadata lists no transports for its model.")).toHaveLength(1);
  });

  it("says when no valid copy of the FIDO metadata is here, so no key counts as a hardware key", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [], high_security: false, metadata: { valid: false, next_update: null } }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    expect((await screen.findByRole("note")).textContent).toContain("no key counts as a hardware key; a security key added now is judged again once it has one");
  });

  it("shows the switch with what it guards and the two keys, and waits for a hardware key to turn it on", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [phone], high_security: false, metadata: valid }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    expect(await screen.findByText("High security")).toBeTruthy();
    expect(screen.getByText(/only a hardware key confirms a risky change: linking a session, revoking one, adding or removing a passkey, approving another device's sign-in, and signing every other device out/)).toBeTruthy();
    expect(screen.getByText(/Keep two hardware keys, one of them kept apart/)).toBeTruthy();
    await screen.findByText("It needs a hardware key first: add one under Passkeys.");
    expect((screen.getByRole("button", { name: "Turn it on" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("waits for a passkey still to be judged before high security can be turned on", async () => {
    const waitingKey = { ...phone, id: "k3", name: "New key", why: "This server has no valid copy of the FIDO metadata yet: the key is judged again once it has one.", judge_again: true };
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, waitingKey], high_security: false, metadata: { valid: false, next_update: null } }] });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText(/A passkey still waits to be judged: high security can be turned on once it is/);
    expect((screen.getByRole("button", { name: "Turn it on" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says what turning it on withdrew, in plain words", () => {
    expect(withdrawnWords(0, 0)).toBe("");
    expect(withdrawnWords(1, 0)).toBe("1 command your other passkeys signed, not yet taken, was withdrawn.");
    expect(withdrawnWords(0, 2)).toBe("2 sessions another passkey had resumed were stopped again: resume them with a hardware key.");
    expect(withdrawnWords(0, 0, 1)).toBe("1 approval still waiting to take effect was cancelled: approve again with a hardware key what you meant.");
    expect(withdrawnWords(0, 0, 3)).toBe("3 approvals still waiting to take effect were cancelled: approve again with a hardware key what you meant.");
  });

  it("while it is on, asks for a hardware key alone for a change, and pins the hardware keys alone", async () => {
    serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone], high_security: true, metadata: valid }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 2, hardware: 1, high_security: true }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove iPhone" }));
    const panel = await screen.findByRole("group", { name: "Removing “iPhone”" });
    expect(panel.textContent).toContain("takes one of your hardware keys");
    expect(within(panel).queryByLabelText("Your password")).toBeNull();          // the account has one: not asked for
    expect(screen.getAllByRole("button", { name: "Copy pin line" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Turn it off" })).toBeTruthy();
    expect(screen.getByText(/run pin again in that session's own window, so it keeps your hardware keys alone\. A hardware key it does not hold yet goes in with that key's pin line/)).toBeTruthy();
    expect(screen.getByText(/It asks for one of your hardware keys first\./)).toBeTruthy();
  });

  it("while it is on, offers no Remove for the last hardware key and says why, before any touch", async () => {
    const calls = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone], high_security: true, metadata: valid }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 2, hardware: 1, high_security: true }],
    });
    useStore.setState({ user: owner });
    render(<AccountDialog onClose={() => {}} />);
    // marked unavailable, still where the keyboard reaches it, its description the reason
    const remove = await screen.findByRole("button", { name: "Remove YubiKey" }) as HTMLButtonElement;
    expect(remove.getAttribute("aria-disabled")).toBe("true");
    expect(remove.disabled).toBe(false);
    const why = document.getElementById(remove.getAttribute("aria-describedby") ?? "");
    expect(why?.textContent).toBe("Your last hardware key: it is not removed while high security is on. Add another hardware key first, or turn high security off.");
    // the page reads what confirms a change as it loads, to say what adding takes: the click asks nothing
    const asked = () => calls.filter((c) => c.path.startsWith("/api/account/confirm")).length;
    await waitFor(() => expect(asked()).toBe(1));
    fireEvent.click(remove);
    await act(async () => {});
    expect(screen.queryByRole("group", { name: /Removing/ })).toBeNull();
    expect(asked()).toBe(1);
    expect(screen.getByRole("button", { name: "Remove iPhone" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("offers Remove for a hardware key while another is kept, and for any while high security is off", async () => {
    const spare = { ...yubikey, id: "k3", name: "Spare key", fingerprint: "c".repeat(32) };
    for (const [keys, high] of [[[yubikey, spare, phone], true], [[yubikey, phone], false]] as const) {
      serve({ "GET /api/account/passkeys": () => [200, { passkeys: keys, high_security: high, metadata: valid }] });
      useStore.setState({ user: owner });
      render(<AccountDialog onClose={() => {}} />);
      expect((await screen.findByRole("button", { name: "Remove YubiKey" })).getAttribute("aria-disabled")).toBeNull();
      expect(screen.queryByText(/Your last hardware key/)).toBeNull();
      cleanup();
    }
  });

  describe("in a browser that makes passkeys", () => {
    const bytes = (n: number) => new Uint8Array([n]).buffer;
    const credentials = {
      get: vi.fn(async (_opts: { publicKey: { allowCredentials: unknown[] } }) => ({ id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: null } })),
      create: vi.fn(async () => ({ id: "Ag", type: "public-key", response: { clientDataJSON: bytes(4), attestationObject: bytes(5), getTransports: () => ["usb", "nfc"] } })),
    };
    const fresh = async () => {
      vi.resetModules();
      vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
      Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
      const { useStore: store } = await import("../store");
      store.setState({ user: owner });
      return import("./AccountDialog");
    };
    afterEach(() => {
      credentials.get.mockClear();
      credentials.create.mockClear();
      delete (navigator as unknown as { credentials?: unknown }).credentials;
      vi.resetModules();
    });

    it("turns high security on with a hardware key's confirmation, never the password", async () => {
      let on = false;
      const calls = serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone], high_security: on, metadata: valid }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 2, hardware: 1, high_security: on }],
        "POST /api/account/confirm/options": (b) => (b?.hardware === true
          ? [200, { challenge_id: "ch9", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }]
          : [403, { detail: "every passkey offered" }]),
        "POST /api/account/confirm": (b) => (b?.challenge_id === "ch9" ? [200, { confirmation: "c9", hardware: true }] : [403, { detail: "no" }]),
        "PUT /api/account/high-security": (b) => { on = b?.on === true && b?.confirmation === "c9"; return [200, { high_security: on, withdrawn: 2, stopped: ["L1"], cancelled: ["h1"] }]; },
      });
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Turn it on" }));
      const panel = await screen.findByRole("group", { name: "Turning high security on" });
      expect(panel.textContent).toContain("takes one of your hardware keys as well as this sign-in; your password and your other passkeys do not confirm it");
      expect(within(panel).queryByLabelText("Your password")).toBeNull();
      fireEvent.click(within(panel).getByRole("button", { name: "Use a hardware key" }));
      await screen.findByRole("button", { name: "Turn it off" });
      expect(screen.getByRole("status").textContent).toBe("2 commands your other passkeys signed, not yet taken, were withdrawn; 1 session another passkey had resumed was stopped again: resume it with a hardware key; 1 approval still waiting to take effect was cancelled: approve again with a hardware key what you meant.");
      expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm", hardware: true, change: "high-security-on" });
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ on: true, confirmation: "c9" });
      expect(calls.some((c) => c.path === "/api/account/confirm" && c.body?.password !== undefined)).toBe(false);
    });

    it("turns high security off with a hardware key's confirmation too", async () => {
      let on = true;
      const calls = serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [yubikey], high_security: on, metadata: valid }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 1, hardware: 1, high_security: on }],
        "POST /api/account/confirm/options": () => [200, { challenge_id: "ch8", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }],
        "POST /api/account/confirm": () => [200, { confirmation: "c8", hardware: true }],
        "PUT /api/account/high-security": (b) => { on = b?.on === true; return [200, { high_security: on }]; },
      });
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Turn it off" }));
      const panel = await screen.findByRole("group", { name: "Turning high security off" });
      expect(panel.textContent).toContain("since high security is on");
      fireEvent.click(within(panel).getByRole("button", { name: "Use a hardware key" }));
      await screen.findByRole("button", { name: "Turn it on" });
      expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm", hardware: true, change: "high-security-off" });
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ on: false, confirmation: "c8" });
    });

    it("says once it is off that sessions linked while it was on still refuse the other passkeys, with their pin lines", async () => {
      let on = true;
      serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone], high_security: on, metadata: valid }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 2, hardware: 1, high_security: on }],
        "POST /api/account/confirm/options": () => [200, { challenge_id: "ch8", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }],
        "POST /api/account/confirm": () => [200, { confirmation: "c8", hardware: true }],
        "PUT /api/account/high-security": (b) => { on = b?.on === true; return [200, { high_security: on, withdrawn: 0, stopped: [], cancelled: [] }]; },
      });
      const { AccountDialog: Dialog, pinLine } = await fresh();
      render(<Dialog onClose={() => {}} />);
      await screen.findByRole("button", { name: "Turn it off" });
      expect(screen.queryByText(/still refuses what your other passkeys sign/)).toBeNull();     // nothing to say yet
      fireEvent.click(screen.getByRole("button", { name: "Turn it off" }));
      fireEvent.click(within(await screen.findByRole("group", { name: "Turning high security off" })).getByRole("button", { name: "Use a hardware key" }));
      const said = (await screen.findByText(/still refuses what your other passkeys sign/)).parentElement as HTMLElement;
      expect(said.getAttribute("role")).toBe("status");
      expect(said.textContent).toContain("A session linked while high security was on pinned your hardware keys alone");
      expect(within(said).getByText(String(pinLine(phone.fingerprint)))).toBeTruthy();
      expect(within(said).queryByText(String(pinLine(yubikey.fingerprint)))).toBeNull();      // a hardware key it holds
      expect(within(said).getAllByRole("button", { name: "Copy pin line" })).toHaveLength(1);
    });

    it("names no pin line once it is off for a passkey whose fingerprint is not in the server's form", async () => {
      let on = true;
      const odd = { ...phone, id: "k3", name: "Odd", fingerprint: `${"c".repeat(32)} $(touch /tmp/pp-owned)` };
      serve({
        "GET /api/account/passkeys": () => [200, { passkeys: [yubikey, phone, odd], high_security: on, metadata: valid }],
        "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 3, hardware: 1, high_security: on }],
        "POST /api/account/confirm/options": () => [200, { challenge_id: "ch8", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }],
        "POST /api/account/confirm": () => [200, { confirmation: "c8", hardware: true }],
        "PUT /api/account/high-security": (b) => { on = b?.on === true; return [200, { high_security: on, withdrawn: 0, stopped: [], cancelled: [] }]; },
      });
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Turn it off" }));
      fireEvent.click(within(await screen.findByRole("group", { name: "Turning high security off" })).getByRole("button", { name: "Use a hardware key" }));
      const said = (await screen.findByText(/still refuses what your other passkeys sign/)).parentElement as HTMLElement;
      expect(within(said).getAllByRole("button", { name: "Copy pin line" })).toHaveLength(1);      // the iPhone's alone
      expect(said.textContent).not.toContain("Odd");
      expect(said.textContent).not.toContain("pp-owned");
    });

    const asking = { name: "Laptop", created_at: 1, asked_from: null, viewer: null, same_network: null };
    const deviceRoutes = (high: boolean, needed = true) => ({
      "POST /api/auth/device/lookup": () => [200, asking] as [number, unknown],
      "GET /api/account/confirm": () => [200, { needed, password: needed, passkeys: needed ? 1 : 0, hardware: needed ? 1 : 0, high_security: high }] as [number, unknown],
      "POST /api/account/confirm/options": (b: Record<string, unknown> | undefined) => (b?.hardware === true || !high
        ? [200, { challenge_id: "ch7", publicKey: { challenge: "AAAA", rpId: "pp.example", timeout: 300000, userVerification: "required", allowCredentials: [{ type: "public-key", id: "AQ" }] } }]
        : [403, { detail: "every passkey offered" }]) as [number, unknown],
      "POST /api/account/confirm": () => [200, { confirmation: "c7", hardware: high }] as [number, unknown],
      "POST /api/auth/device/approve": () => [200, { name: "Laptop", approved: true }] as [number, unknown],
    });

    it("announces a code no device waits with, in the server's words", async () => {
      serve({ ...deviceRoutes(false), "POST /api/auth/device/lookup": () => [400, { detail: "No device is waiting with that code: it may have been used, or have expired." }] });
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
      expect((await screen.findByRole("alert")).textContent).toBe("No device is waiting with that code: it may have been used, or have expired.");
    });

    it("approves a device's sign-in only with the person's word made for its code, the password's or a passkey's, while high security is off", async () => {
      for (const how of ["password", "passkey"] as const) {
        const calls = serve(deviceRoutes(false));
        const { AccountDialog: Dialog } = await fresh();
        render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
        fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
        const panel = await screen.findByRole("group", { name: "Signing in “Laptop” (code ABCD-EFGH)" });
        expect(panel.textContent).toContain("takes one of your passkeys or your password as well as this sign-in");
        expect(panel.textContent).toContain("Once approved, it is signed in with this account for 7 days, or until you sign out everywhere.");
        expect(calls.some((c) => c.path === "/api/auth/device/approve")).toBe(false);      // nothing approved yet
        if (how === "password") {
          fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "hunter2hunter2" } });
          fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
        } else {
          fireEvent.click(within(panel).getByRole("button", { name: "Use a passkey" }));
        }
        await screen.findByText("Laptop is signed in with this account.");
        if (how === "password") {
          expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/confirm")?.body).toEqual({ password: "hunter2hunter2", device: "ABCD-EFGH" });
        } else {
          expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm", device: "ABCD-EFGH" });
        }
        expect(calls.find((c) => c.path === "/api/auth/device/approve")?.body).toEqual({ code: "ABCD-EFGH", confirmation: "c7" });
        cleanup();
      }
    });

    it("approves a device's sign-in with the sign-in alone for an account with nothing to confirm with (this Mac's own)", async () => {
      const calls = serve(deviceRoutes(false, false));
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
      const panel = await screen.findByRole("group", { name: "Signing in “Laptop” (code ABCD-EFGH)" });
      fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
      await screen.findByText("Laptop is signed in with this account.");
      expect(calls.find((c) => c.path === "/api/auth/device/approve")?.body).toEqual({ code: "ABCD-EFGH" });
      expect(calls.some((c) => c.path.startsWith("/api/account/confirm/"))).toBe(false);
    });

    it("approves a device's sign-in with a hardware key's confirmation made for its code while high security is on", async () => {
      const calls = serve(deviceRoutes(true));
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
      const panel = await screen.findByRole("group", { name: "Signing in “Laptop” (code ABCD-EFGH)" });
      expect(panel.textContent).toContain("takes one of your hardware keys as well as this sign-in, since high security is on");
      expect(within(panel).queryByLabelText("Your password")).toBeNull();
      expect(calls.some((c) => c.path === "/api/auth/device/approve")).toBe(false);      // nothing approved yet
      fireEvent.click(within(panel).getByRole("button", { name: "Use a hardware key" }));
      await screen.findByText("Laptop is signed in with this account.");
      expect(calls.find((c) => c.path === "/api/account/confirm/options")?.body).toEqual({ purpose: "confirm", hardware: true, device: "ABCD-EFGH" });
      expect(calls.find((c) => c.path === "/api/auth/device/approve")?.body).toEqual({ code: "ABCD-EFGH", confirmation: "c7" });
    });
    it("closes a confirmation opened for one code once another is typed, so it approves nothing under the other's card", async () => {
      const calls = serve(deviceRoutes(false));
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
      await screen.findByRole("group", { name: "Signing in “Laptop” (code ABCD-EFGH)" });
      fireEvent.change(screen.getByLabelText("The code the device shows"), { target: { value: "WXYZ-2345" } });
      expect(screen.queryByRole("group", { name: /^Signing in/ })).toBeNull();
      // and once the new code's card shows, the first code's confirmation is not under it
      fireEvent.click(screen.getByRole("button", { name: "Look" }));
      expect(await screen.findByRole("button", { name: "Approve" })).toBeTruthy();
      expect(screen.queryByRole("group", { name: /^Signing in/ })).toBeNull();
      expect(calls.some((c) => c.path.startsWith("/api/account/confirm") && c.method === "POST")).toBe(false);
    });

    it("keeps a lookup's answer only for the code it was asked for, never beside another", async () => {
      let late: (r: Response) => void = () => {};
      const other = { name: "Other", created_at: 2, asked_from: "198.51.100.66", viewer: "203.0.113.7", same_network: false };
      vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
        const body = init.body ? JSON.parse(String(init.body)) : undefined;
        if (url === "/api/auth/device/lookup") {
          if (body?.code === "ABCD-EFGH") return new Promise<Response>((done) => { late = done; });
          return new Response(JSON.stringify(other), { status: 200 });
        }
        return new Response(JSON.stringify({ detail: `no route for ${url}` }), { status: 404 });
      }));
      const { AccountDialog: Dialog } = await fresh();
      render(<Dialog tab="device" onClose={() => {}} />);
      const input = screen.getByLabelText("The code the device shows");
      fireEvent.change(input, { target: { value: "ABCD-EFGH" } });
      fireEvent.click(screen.getByRole("button", { name: "Look" }));
      fireEvent.change(input, { target: { value: "WXYZ-2345" } });         // typed over while it is out
      await act(async () => { late(new Response(JSON.stringify(asking), { status: 200 })); });
      expect(screen.queryByText(/asks to be signed in with this account/)).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Look" }));
      expect(await screen.findByText("Other")).toBeTruthy();
      expect(screen.queryByText("Laptop")).toBeNull();
      // nor a refusal: one that comes back for a code typed over says nothing under the new one
      fireEvent.change(input, { target: { value: "ABCD-EFGH" } });
      fireEvent.click(screen.getByRole("button", { name: "Look" }));
      fireEvent.change(input, { target: { value: "WXYZ-2345" } });
      await act(async () => { late(new Response(JSON.stringify({ detail: "No device is waiting with that code." }), { status: 400 })); });
      expect(screen.queryByText(/No device is waiting/)).toBeNull();
    });

    it("takes a new link's code in place of the one it holds, with its card and confirmation", async () => {
      const looked: string[] = [];
      vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
        const body = init.body ? JSON.parse(String(init.body)) : undefined;
        if (url === "/api/auth/device/lookup") {
          looked.push(body?.code);
          return new Response(JSON.stringify(body?.code === "ABCD-EFGH" ? asking : { ...asking, name: "Second" }), { status: 200 });
        }
        if (url === "/api/account/confirm") return new Response(JSON.stringify({ needed: true, password: true, passkeys: 1, hardware: 1, high_security: false }), { status: 200 });
        return new Response(JSON.stringify({ detail: `no route for ${url}` }), { status: 404 });
      }));
      const { AccountDialog: Dialog } = await fresh();
      const { rerender } = render(<Dialog tab="device" deviceCode="ABCD-EFGH" onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
      await screen.findByRole("group", { name: "Signing in “Laptop” (code ABCD-EFGH)" });
      rerender(<Dialog tab="device" deviceCode="WXYZ-2345" onClose={() => {}} />);
      expect(await screen.findByText("Second")).toBeTruthy();
      expect((screen.getByLabelText("The code the device shows") as HTMLInputElement).value).toBe("WXYZ-2345");
      expect(screen.queryByRole("group", { name: /^Signing in/ })).toBeNull();
      expect(screen.getByLabelText("The code from the link").textContent).toBe("WXYZ-2345");
      expect(looked).toEqual(["ABCD-EFGH", "WXYZ-2345"]);
    });

    it("sends the transports the browser reports for a new passkey, which the server judges it by", async () => {
      const calls = serve({ "POST /api/account/passkeys": () => [200, { id: "k3", tier: "hardware" }] });
      await fresh();
      const { makePasskey } = await import("../passkey");
      await makePasskey({ challenge_id: "reg1", publicKey: {
        rp: { id: "pp.example", name: "Proofpane Community" }, user: { id: "dQ", name: "a@example.org", displayName: "a@example.org" },
        challenge: "AAAA", pubKeyCredParams: [{ type: "public-key", alg: -7 }], timeout: 300000, attestation: "direct",
        authenticatorSelection: { userVerification: "required" }, excludeCredentials: [] } }, "YubiKey");
      const sentBody = calls.find((c) => c.path === "/api/account/passkeys")?.body as { credential: { response: Record<string, unknown> } };
      expect(sentBody.credential.response.transports).toEqual(["usb", "nfc"]);
      expect((credentials.create.mock.calls[0] as unknown as [{ publicKey: { attestation: string } }])[0].publicKey.attestation).toBe("direct");
    });

    it("asks a new passkey for WebAuthn's PRF, which end-to-end encryption unlocks with, and tells the server what the browser said of it", async () => {
      const calls = serve({ "POST /api/account/passkeys": () => [200, { id: "k3", tier: "hardware" }] });
      await fresh();
      const { makePasskey } = await import("../passkey");
      const opts = { challenge_id: "reg1", publicKey: {
        rp: { id: "pp.example", name: "Proofpane Community" }, user: { id: "dQ", name: "a@example.org", displayName: "a@example.org" },
        challenge: "AAAA", pubKeyCredParams: [{ type: "public-key" as const, alg: -7 }], timeout: 300000, attestation: "direct" as const,
        authenticatorSelection: { userVerification: "required" as const }, excludeCredentials: [] } };
      credentials.create.mockImplementationOnce(async () => ({ id: "Ag", type: "public-key", response: { clientDataJSON: bytes(4), attestationObject: bytes(5), getTransports: () => ["usb"] },
        getClientExtensionResults: () => ({ prf: { enabled: true } }) }));
      await makePasskey(opts, "YubiKey");
      const asked = (credentials.create.mock.calls[0] as unknown as [{ publicKey: { extensions: unknown } }])[0].publicKey.extensions;
      expect(asked).toEqual({ prf: {} });
      const said = calls.find((c) => c.path === "/api/account/passkeys")?.body as { prf: boolean; credential: { clientExtensionResults: unknown } };
      expect(said.prf).toBe(true);
      expect(said.credential.clientExtensionResults).toEqual({ prf: { enabled: true } });
      // a browser that says nothing of it: false, never left out
      await makePasskey(opts, "Phone");
      const quiet = calls.filter((c) => c.path === "/api/account/passkeys").at(-1)?.body as { prf: boolean; credential: { clientExtensionResults: unknown } };
      expect(quiet.prf).toBe(false);
      expect(quiet.credential.clientExtensionResults).toEqual({ prf: { enabled: false } });
    });
  });
});

describe("the release and the commit the deployment runs, under the account's address", () => {
  const COMMIT = "0123456789abcdef0123456789abcdef01234567";
  // GET /api/health answers `said` (or `status`), as the server does to anyone; every other read, no passkeys
  const health = (said: unknown, status = 200) => {
    const fetched = vi.fn(async (url: string) => (url === "/api/health"
      ? new Response(JSON.stringify(said), { status })
      : new Response(JSON.stringify({ passkeys: [] }), { status: 200 })));
    vi.stubGlobal("fetch", fetched);
    return fetched;
  };
  const asked = async (fetched: ReturnType<typeof health>) => {
    await waitFor(() => expect(fetched.mock.calls.map(([url]) => url)).toContain("/api/health"));
    await new Promise((r) => setTimeout(r, 0));             // its answer read
  };

  it("says them small under the address, as the server's: the commit cut to 12 characters, the whole in the title", async () => {
    health({ ok: true, version: "0.1.0.dev0", release: "58a", commit: COMMIT });
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    render(<AccountDialog onClose={() => {}} />);
    const line = await screen.findByText("Server release 58a · commit 0123456789ab");
    expect(line.getAttribute("title")).toBe(COMMIT);
    // in the section that names the account, after its address
    const section = line.closest("section");
    expect(section?.querySelector("h3")?.textContent).toBe("Signed in");
    expect(section?.textContent?.indexOf("a@example.org")).toBeLessThan(section?.textContent?.indexOf("Server release 58a") ?? -1);
  });

  it("asks with this account's sign-in, since the server says the commit to a signed-in account alone, and says the release alone without it", async () => {
    localStorage.setItem("pp-open-token", "test-token.sig");
    const fetched = health({ ok: true, version: "0.1.0.dev0", release: "58a" });      // as to anyone: no commit
    const { container } = render(<BuildLine />);
    expect(await screen.findByText("Server release 58a")).toBeTruthy();
    expect(container.textContent).toBe("Server release 58a");
    const [, init] = fetched.mock.calls.find(([url]) => url === "/api/health") as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-token.sig");
    localStorage.removeItem("pp-open-token");
  });

  it("says the release alone where the commit is unknown, and unknown where the build kept no release", async () => {
    health({ ok: true, version: "0.1.0.dev0", release: "unknown", commit: "unknown" });
    const { container } = render(<BuildLine />);
    expect(await screen.findByText("Server release unknown")).toBeTruthy();
    expect(container.textContent).toBe("Server release unknown");
    expect(container.querySelector("[title]")).toBeNull();
    cleanup();
    // a release known and its commit not (a build given the release alone): the release, and no commit said
    health({ ok: true, version: "0.1.0.dev0", release: "58a", commit: "unknown" });
    const known = render(<BuildLine />).container;
    expect(await screen.findByText("Server release 58a")).toBeTruthy();
    expect(known.textContent).toBe("Server release 58a");
    expect(known.querySelector("[title]")).toBeNull();
  });

  it("says the release alone where the commit is not text, and draws without failing", async () => {
    for (const commit of [1234567, null, ["0123456"], { id: COMMIT }]) {
      health({ ok: true, version: "0.1.0.dev0", release: "58a", commit });
      const { container } = render(<BuildLine />);
      expect(await screen.findByText("Server release 58a")).toBeTruthy();
      expect(container.textContent).toBe("Server release 58a");
      cleanup();
    }
  });

  it("says nothing where the answer names no release, or none comes", async () => {
    for (const [said, status] of [[{ ok: true, version: "0.1.0.dev0" }, 200], [{ detail: "down" }, 503], [{ release: 58, commit: COMMIT }, 200]] as const) {
      const fetched = health(said, status);
      const { container } = render(<BuildLine />);
      await asked(fetched);
      expect(container.textContent).toBe("");
      cleanup();
    }
  });
});

describe("an account change confirmed with the recovery code while encryption's keys are not read yet", () => {
  afterEach(() => { useE2EStore.setState(useE2EStore.getInitialState()); });

  it("makes no proof of a guessed form: it says the keys are still loading, and asks the server nothing", async () => {
    const fetched = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetched);
    useE2EStore.setState({ view: null, ring: null });
    const confirmed = vi.fn(async () => {});
    render(<ConfirmIt change={{ what: "add", label: "Adding a passkey", action: "Add", e2e: true, ways: { needed: true, password: false, passkeys: 0 } }}
      onConfirmed={confirmed} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Your recovery code"), { target: { value: "ABCD".repeat(8) } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Use the recovery code" })); });
    expect((await screen.findByRole("alert")).textContent).toBe(K.STILL_LOADING);
    expect(confirmed).not.toHaveBeenCalled();
    expect(fetched).not.toHaveBeenCalled();
  });
});
