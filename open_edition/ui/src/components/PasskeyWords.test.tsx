// What adding a passkey takes, said before it is
// asked for; a security key added as one; each passkey named by what it is and renamed; its kind in Encryption's list
// as on Account; and the unlock naming the passkeys that unlock encryption, how this device's is added, and where a
// browser gives a security key no key, to use another browser.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as K from "../e2eKeys";
import { useE2EStore } from "../e2e";
import { CLASSICAL, FakeAuthenticator, FakeServer } from "../e2eTestkit";
import { useStore } from "../store";
import { EncryptionPane } from "./Encryption";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useE2EStore.setState(useE2EStore.getInitialState());
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
});

// ---- Account: adding a passkey or a security key, naming and renaming one --------------------------------------

describe("Account's passkeys", () => {
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
  const laptop = { id: "k1", name: "Laptop", created_at: 1, last_used_at: null, backed_up: false, tier: "device",
    kind: "device", maker: null, why: "It gave no attestation (a browser may ask before it shares one), so nothing shows it is a certified security key." };
  const ways = (over: Record<string, unknown> = {}) => [200, { needed: true, password: true, passkeys: 1, hardware: 0, high_security: false, ...over }] as [number, unknown];
  const bytes = (n: number) => new Uint8Array([n]).buffer;
  const created: Record<string, unknown>[] = [];
  let attachment: string | null = null;
  const credentials = {
    get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: null } })),
    create: vi.fn(async (o: { publicKey: Record<string, unknown> }) => {
      created.push(o.publicKey);
      return { id: "Ag", type: "public-key", response: { clientDataJSON: bytes(4), attestationObject: bytes(5) },
        ...(attachment !== null ? { authenticatorAttachment: attachment } : {}) };
    }),
  };
  /** The dialog as a browser that makes passkeys loads it: its modules read navigator.credentials as they load. */
  const fresh = async () => {
    vi.resetModules();
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
    const { useStore: store } = await import("../store");
    store.setState({ user: owner });
    const e2e = await import("../e2e");
    return { Dialog: (await import("./AccountDialog")).AccountDialog, e2e };
  };
  const newOptions = { challenge_id: "reg1", publicKey: {
    rp: { id: "pp.example", name: "Proofpane Community" }, user: { id: "dQ", name: "a@example.org", displayName: "a@example.org" },
    challenge: "AAAA", pubKeyCredParams: [{ type: "public-key", alg: -7 }], timeout: 300000, attestation: "direct",
    authenticatorSelection: { residentKey: "required", requireResidentKey: true, userVerification: "required" }, excludeCredentials: [] } };
  const adding = (over: Record<string, Route> = {}) => serve({
    "GET /api/account/passkeys": () => [200, { passkeys: [laptop] }],
    "GET /api/account/confirm": () => ways(),
    "POST /api/account/confirm": (b) => (b?.password === "pw" ? [200, { confirmation: "c1" }] : [403, { detail: "no" }]),
    "POST /api/account/passkeys/options": (b) => (b?.confirmation === "c1" ? [200, newOptions] : [403, { detail: "no" }]),
    "POST /api/account/passkeys": () => [200, { id: "k2", name: "Security key", created_at: 2, last_used_at: null, backed_up: false }],
    ...over,
  });
  /** Through the confirmation, with the password: the step that makes it is offered next. */
  const confirmWithPassword = async (label: string) => {
    const panel = await screen.findByRole("group", { name: label });
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
  };
  beforeEach(() => { created.length = 0; attachment = null; credentials.get.mockClear(); credentials.create.mockClear(); });
  afterEach(() => { delete (navigator as unknown as { credentials?: unknown }).credentials; vi.resetModules(); });

  const notice = () => screen.queryByRole("note", { name: "What adding a passkey takes" });
  // a view of encryption on, whole enough for Encryption's pane
  const ON = { on: true, pq: false, mlkem: true, classical_texts: null, mk_id: "m1", statements: [], misstated: [], credentials: [],
    recovery: null, grants: [], chain: [], roster: null, content_keys: [], passkeys: [], links: [], pending: null, high_security: false,
    rekey_due: false, compaction_due: false, sealed: null, to_seal: null, holds: null };

  it("says, in colour, before anything is asked, that adding one takes a passkey this account has or its password", async () => {
    adding();
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(notice()!.textContent).toContain("Adding a passkey takes a touch of one this account already has, or your password, first.");
    expect(notice()!.className).toContain("border-accent");
    expect(notice()!.textContent).not.toContain("second step");      // nothing to unlock while encryption is off
  });

  it("says an account with no passkey yet that its first one takes the password", async () => {
    adding({ "GET /api/account/passkeys": () => [200, { passkeys: [] }], "GET /api/account/confirm": () => ways({ passkeys: 0 }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(notice()!.textContent).toContain("Adding your first passkey takes your password first.");
  });

  it("says, while high security is on, that adding one takes a hardware key", async () => {
    adding({ "GET /api/account/passkeys": () => [200, { passkeys: [laptop], high_security: true }],
      "GET /api/account/confirm": () => ways({ hardware: 1, high_security: true }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(notice()!.textContent).toContain("Adding a passkey takes a touch of one of your hardware keys first");
    expect(notice()!.textContent).not.toContain("your password,");
  });

  it("says, while encryption is on, that the password does not confirm it, that it waits, and that unlocking is a second step, with the way there", async () => {
    // a view of encryption on, whole enough for Encryption's pane, which the link to it opens
    const on = { on: true, pq: false, mlkem: true, classical_texts: null, mk_id: "m1", statements: [], misstated: [], credentials: [],
      recovery: null, grants: [], chain: [], roster: null, content_keys: [], passkeys: [], links: [], pending: null, high_security: false,
      rekey_due: false, compaction_due: false, sealed: null, to_seal: null, holds: null };
    adding({ "GET /api/ade/e2e": () => [200, on] });
    const { Dialog, e2e } = await fresh();
    e2e.useE2EStore.setState({ view: on as unknown as K.E2EView });
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()).not.toBeNull());
    const words = notice()!.textContent ?? "";
    expect(words).toContain("your recovery code");
    expect(words).toContain("your password does not confirm it");
    expect(words).toContain("It is added once its wait ends.");
    expect(words).toContain("Unlocking end-to-end encryption with it is a second step");
    fireEvent.click(within(notice()!).getByRole("button", { name: "Go to Encryption" }));
    expect(screen.getByRole("button", { name: /^Encryption$/ }).getAttribute("aria-current")).toBe("page");
  });

  it("offers a security key as its own button, which asks the browser for a roaming authenticator with the security-key hint", async () => {
    const calls = adding();
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Add a security key" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Add a security key" }));
    await confirmWithPassword("Adding a security key");
    const make = await screen.findByRole("button", { name: "Make it on the security key" });
    expect(document.getElementById(make.getAttribute("aria-describedby") ?? "")?.textContent).toContain("Now make it on your security key");
    expect(credentials.create).not.toHaveBeenCalled();                 // nothing made before its own click
    fireEvent.click(make);
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/passkeys")).toBe(true));
    const asked = created[0] as { authenticatorSelection: Record<string, unknown>; hints?: string[] };
    expect(asked.authenticatorSelection).toEqual({ residentKey: "required", requireResidentKey: true, userVerification: "required",
      authenticatorAttachment: "cross-platform" });
    expect(asked.hints).toEqual(["security-key"]);
  });

  it("asks nothing of the authenticator's kind for a passkey added the usual way", async () => {
    const calls = adding();
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a passkey" }));
    await confirmWithPassword("Adding a passkey");
    fireEvent.click(await screen.findByRole("button", { name: "Make the passkey" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/passkeys")).toBe(true));
    const asked = created[0] as { authenticatorSelection: Record<string, unknown>; hints?: string[] };
    expect(asked.authenticatorSelection).toEqual({ residentKey: "required", requireResidentKey: true, userVerification: "required" });
    expect(asked.hints).toBeUndefined();
  });

  it("leaves a passkey given no name for the server to name by what it is, and says so in the field", async () => {
    const calls = adding();
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    const field = await screen.findByLabelText("The passkey's name");
    expect(field.getAttribute("placeholder")).toContain("named by what it is");
    fireEvent.click(screen.getByRole("button", { name: "Add a security key" }));
    await confirmWithPassword("Adding a security key");
    fireEvent.click(await screen.findByRole("button", { name: "Make it on the security key" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/account/passkeys")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/passkeys")?.body?.name).toBe("");
  });

  it("tells the server what the browser said of the authenticator that made the passkey, and null where it said nothing", async () => {
    const calls = serve({ "POST /api/account/passkeys": () => [200, { id: "k3" }] });
    await fresh();
    const { makePasskey } = await import("../passkey");
    attachment = "cross-platform";
    await makePasskey(newOptions as unknown as Parameters<typeof makePasskey>[0], "");
    attachment = null;
    await makePasskey(newOptions as unknown as Parameters<typeof makePasskey>[0], "");
    const sent = calls.filter((c) => c.path === "/api/account/passkeys").map((c) => (c.body?.credential as Record<string, unknown>).authenticatorAttachment);
    expect(sent).toEqual(["cross-platform", null]);
  });

  it("renames a passkey with the person's word made for that rename, and reads the list again", async () => {
    let name = "Laptop";
    const calls = adding({
      "GET /api/account/passkeys": () => [200, { passkeys: [{ ...laptop, name }] }],
      "PATCH /api/account/passkeys/k1": (b) => {
        if (b?.confirmation !== "c1") return [403, { detail: "Confirm it is you first." }];
        name = String(b?.name);
        return [200, { ...laptop, name }];
      },
    });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Rename Laptop" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Rename Laptop" }));
    const field = screen.getByLabelText("New name for Laptop") as HTMLInputElement;
    expect(field.value).toBe("Laptop");
    fireEvent.change(field, { target: { value: "Work laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the name" }));
    // the sign-in alone renames nothing (passkeys.rename): the person's word first, made for this rename
    await waitFor(() => expect(screen.queryByRole("group", { name: "Renaming “Laptop” to “Work laptop”" })).not.toBeNull());
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
    await confirmWithPassword("Renaming “Laptop” to “Work laptop”");
    await waitFor(() => expect(screen.queryByText("Work laptop")).not.toBeNull());
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/confirm")?.body?.change).toBe("passkey-rename:k1");
    expect(calls.filter((c) => c.method === "PATCH")).toEqual([
      { path: "/api/account/passkeys/k1", method: "PATCH", body: { name: "Work laptop", confirmation: "c1" } }]);
    // cancelled, nothing is sent
    fireEvent.click(screen.getByRole("button", { name: "Rename Work laptop" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel renaming" }));
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
    expect(screen.queryByLabelText("New name for Work laptop")).toBeNull();
  });

  it("renames to the name its confirmation named, whatever the field says by the time the word is given", async () => {
    const calls = adding({ "PATCH /api/account/passkeys/k1": (b) => [200, { ...laptop, name: String(b?.name) }] });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Rename Laptop" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Rename Laptop" }));
    fireEvent.change(screen.getByLabelText("New name for Laptop"), { target: { value: "Work laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the name" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Renaming “Laptop” to “Work laptop”" })).not.toBeNull());
    fireEvent.change(screen.getByLabelText("New name for Laptop"), { target: { value: "Something else" } });
    await confirmWithPassword("Renaming “Laptop” to “Work laptop”");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body?.name).toBe("Work laptop");
  });

  it("names, as it asks the person's word, a rename to no name as the server naming it by what it is", async () => {
    adding();
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Rename Laptop" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Rename Laptop" }));
    fireEvent.change(screen.getByLabelText("New name for Laptop"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save the name" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Naming “Laptop” by what it is" })).not.toBeNull());
  });

  it("asks, while encryption is on, a passkey's word alone for a rename: neither the password nor the recovery code", async () => {
    adding({ "GET /api/ade/e2e": () => [200, ON], "GET /api/account/confirm": () => ways({ e2e: true }) });
    const { Dialog, e2e } = await fresh();
    e2e.useE2EStore.setState({ view: ON as unknown as K.E2EView });
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Rename Laptop" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Rename Laptop" }));
    fireEvent.change(screen.getByLabelText("New name for Laptop"), { target: { value: "Old laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the name" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Renaming “Laptop” to “Old laptop”" })).not.toBeNull());
    const panel = screen.getByRole("group", { name: "Renaming “Laptop” to “Old laptop”" });
    expect(within(panel).queryByRole("button", { name: "Use a passkey" })).not.toBeNull();
    expect(within(panel).queryByLabelText("Your password")).toBeNull();
    expect(within(panel).queryByLabelText("Your recovery code")).toBeNull();
    expect(panel.textContent).toContain("your password does not confirm it");
  });

  it("asks a passkey's word alone for a rename by the server's word that encryption is on, before its own view is read", async () => {
    adding({ "GET /api/account/confirm": () => ways({ e2e: true }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()?.textContent ?? "").toContain("your password does not confirm it"));
    fireEvent.click(screen.getByRole("button", { name: "Rename Laptop" }));
    fireEvent.change(screen.getByLabelText("New name for Laptop"), { target: { value: "Old laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the name" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Renaming “Laptop” to “Old laptop”" })).not.toBeNull());
    expect(within(screen.getByRole("group", { name: "Renaming “Laptop” to “Old laptop”" })).queryByLabelText("Your password")).toBeNull();
  });

  it("says what adding takes by the server's word that encryption is on, before encryption's own view is read", async () => {
    adding({ "GET /api/account/confirm": () => ways({ e2e: true }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()?.textContent ?? "").toContain("your password does not confirm it"));
    expect(notice()!.textContent).toContain("Unlocking end-to-end encryption with it is a second step");
  });

  it("says, with high security and encryption both on, that only a hardware key is added to encryption", async () => {
    adding({ "GET /api/account/passkeys": () => [200, { passkeys: [laptop], high_security: true }],
      "GET /api/account/confirm": () => ways({ hardware: 1, high_security: true, e2e: true }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()?.textContent ?? "").toContain("While high security is on, only a hardware key can be added there."));
    expect(notice()!.textContent).toContain("Adding a passkey takes a touch of one of your hardware keys first, or your recovery code. It is added once its wait ends.");
  });

  it("says, while encryption is on and no passkey is left, that adding one takes the recovery code", async () => {
    adding({ "GET /api/account/passkeys": () => [200, { passkeys: [] }], "GET /api/account/confirm": () => ways({ passkeys: 0, e2e: true }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()?.textContent ?? "").toContain("Adding a passkey takes your recovery code first"));
    expect(notice()!.textContent).not.toContain("one this account already has");
  });

  it("says, of an account with passkeys and no password, that adding one takes one of them", async () => {
    adding({ "GET /api/account/confirm": () => ways({ password: false }) });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(notice()!.textContent).toBe("Adding a passkey takes a touch of one this account already has first.");
  });

  const securityKeyAnswered = async (answer: Record<string, unknown>) => {
    adding({ "POST /api/account/passkeys": () => [200, answer] });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a security key" }));
    await confirmWithPassword("Adding a security key");
    fireEvent.click(await screen.findByRole("button", { name: "Make it on the security key" }));
  };
  const notAKey = () => screen.queryByRole("note", { name: "Not a security key" });

  it("says so, and offers Remove, when a security key's request made another kind of passkey", async () => {
    await securityKeyAnswered({ id: "k2", name: "Synced passkey", created_at: 2, last_used_at: null, backed_up: true, kind: "synced" });
    await waitFor(() => expect(notAKey()).not.toBeNull());
    expect(notAKey()!.textContent).toContain("The browser made a synced passkey, not one on a security key");
    expect(within(notAKey()!).queryByRole("button", { name: "Remove it" })).not.toBeNull();
  });

  it("says so of a passkey held while encryption is on, which waits on its hold, with no Remove", async () => {
    await securityKeyAnswered({ held: { id: "h1", takes_effect_at: 100 }, told: 1, kind: "device" });
    await waitFor(() => expect(notAKey()).not.toBeNull());
    expect(notAKey()!.textContent).toContain("The browser made a device-bound passkey, not one on a security key");
    expect(notAKey()!.textContent).toContain("cancel it in Encryption");
    expect(within(notAKey()!).queryByRole("button", { name: "Remove it" })).toBeNull();
  });

  it("says nothing of it when a security key's request made a security key or a hardware key", async () => {
    for (const kind of ["security-key", "hardware"]) {
      await securityKeyAnswered({ id: "k2", name: "Key", created_at: 2, last_used_at: null, backed_up: false, kind });
      await waitFor(() => expect(screen.queryByRole("button", { name: "Add a security key" })?.hasAttribute("disabled")).toBe(false));
      expect(notAKey()).toBeNull();
      cleanup();
    }
  });

  it("says in the Passkeys section that renaming takes the person's word too, and, while encryption is on, no password", async () => {
    adding();
    const first = await fresh();
    render(<first.Dialog onClose={() => {}} />);
    await waitFor(() => expect(document.body.textContent ?? "").toContain("Adding, renaming or removing one asks for one of your passkeys or your password first."));
    cleanup();
    adding({ "GET /api/ade/e2e": () => [200, ON], "GET /api/account/confirm": () => ways({ e2e: true }) });
    const second = await fresh();
    second.e2e.useE2EStore.setState({ view: ON as unknown as K.E2EView });
    render(<second.Dialog onClose={() => {}} />);
    await waitFor(() => expect(document.body.textContent ?? "").toContain("your password confirms no change to a passkey"));
    expect(document.body.textContent).not.toContain("renaming one asks for nothing");
  });

  it("says a security key or phone already holds one, not this device, when the browser refuses a second there", async () => {
    adding();
    const { Dialog } = await fresh();
    credentials.create.mockImplementationOnce(async () => { throw new DOMException("exists", "InvalidStateError"); });
    render(<Dialog onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a security key" }));
    await confirmWithPassword("Adding a security key");
    fireEvent.click(await screen.findByRole("button", { name: "Make it on the security key" }));
    await waitFor(() => expect(document.body.textContent ?? "").toContain("The security key or phone you chose already holds a passkey for your account"));
    expect(document.body.textContent).not.toContain("This device already has a passkey");
  });

  it("says each device passkey's kind beside why it is no hardware key, and a hardware key's model as before", async () => {
    const phone = { ...laptop, id: "k2", name: "Phone", kind: "synced", backed_up: true, why: "It may be copied to other devices." };
    const key = { ...laptop, id: "k3", name: "Blue", kind: "security-key", why: "The FIDO metadata does not list its model." };
    const yubi = { ...laptop, id: "k4", name: "Yubi", kind: "hardware", tier: "hardware", maker: "Test Key 5 NFC", why: null };
    const old = { ...laptop, id: "k5", name: "Old", kind: undefined, backed_up: false, why: "Kept before." };
    adding({ "GET /api/account/passkeys": () => [200, { passkeys: [laptop, phone, key, yubi, old] }] });
    const { Dialog } = await fresh();
    render(<Dialog onClose={() => {}} />);
    await screen.findByText("Laptop");
    const text = document.body.textContent ?? "";
    expect(text).toContain(`Device-bound passkey: ${laptop.why}`);
    expect(text).toContain("Synced passkey: It may be copied to other devices.");
    expect(text).toContain("Security key: The FIDO metadata does not list its model.");
    expect(text).toContain("Hardware key: Test Key 5 NFC");
    expect(text).toContain("Device passkey: Kept before.");            // a server from before says no kind: the tier alone
  });
});

// ---- Encryption: each passkey's kind, and the unlock's words -----------------------------------------------------

describe("Encryption's passkeys and its unlock", () => {
  const ACCOUNT = "acct-1";
  const user = { id: ACCOUNT, email: "a@example.org", can_sign_in_again: true };
  let server: FakeServer;
  let auth: FakeAuthenticator;
  beforeEach(() => {
    server = new FakeServer();
    auth = new FakeAuthenticator();
    vi.stubGlobal("fetch", server.fetch);
    auth.install();
    useStore.setState({ user });
    localStorage.clear();
  });
  afterEach(() => { delete (navigator as unknown as { credentials?: unknown }).credentials; });

  const passkey = (id: string, name: string, tier: "device" | "hardware", extra: { kind?: string; maker?: string | null; prf?: boolean } = {}) => {
    server.addPasskey(id, name, tier, extra);
    auth.add(id);
  };
  /** Encryption turned on with `id`'s passkey, straight through the keys module, its hold ended. */
  const turnedOn = async (id: string) => {
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    t.codeText();
    await t.prepare(await K.E2EApi.view());
    auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
  };
  const keysList = () => {
    const heading = screen.getByRole("heading", { name: "What unlocks it" });
    return heading.parentElement!.querySelector("ul")!;
  };
  const shown = async (props: { onAccount?: () => void } = {}) => {
    render(<EncryptionPane {...props} />);
    await screen.findByRole("heading", { name: "What unlocks it" });
  };

  it("lists each passkey with its kind, as Account does: a hardware key with its model, and the kinds of the others", async () => {
    passkey("credAAAA", "Laptop", "device", { kind: "synced" });
    passkey("credBBBB", "Yubi", "hardware", { kind: "hardware", maker: "Test Key 5 NFC" });
    passkey("credCCCC", "Blue", "device", { kind: "security-key" });
    passkey("credDDDD", "Desk", "device", { kind: "device" });
    passkey("credEEEE", "Old", "device");                                  // a server from before says no kind
    await turnedOn("credAAAA");
    await shown();
    const rows = within(keysList()).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(rows.find((r) => r.startsWith("Laptop"))).toContain("Synced passkey");
    expect(rows.find((r) => r.startsWith("Yubi"))).toContain("Hardware key: Test Key 5 NFC");
    expect(rows.find((r) => r.startsWith("Blue"))).toContain("Security key");
    expect(rows.find((r) => r.startsWith("Desk"))).toContain("Device-bound passkey");
    expect(rows.find((r) => r.startsWith("Old"))).toContain("Device passkey");
  });

  it("says of a security key the browser said gives no PRF that it is added in another browser, and of another passkey as before", async () => {
    passkey("credAAAA", "Laptop", "device", { kind: "synced" });
    passkey("credBBBB", "Blue", "device", { kind: "security-key", prf: false });
    passkey("credCCCC", "Yubi", "hardware", { kind: "hardware", maker: "Test Key 5 NFC", prf: false });
    passkey("credDDDD", "Desk", "device", { kind: "device", prf: false });
    await turnedOn("credAAAA");
    await shown();
    const rows = within(keysList()).getAllByRole("listitem").map((li) => li.textContent ?? "");
    for (const name of ["Blue", "Yubi"]) {
      const row = rows.find((r) => r.startsWith(name)) ?? "";
      expect(row).toContain("Safari among them");
      expect(row).toContain("add it in another browser, such as Chrome or Firefox");
    }
    const desk = rows.find((r) => r.startsWith("Desk")) ?? "";
    expect(desk).toContain("this browser did not say it gives PRF when it was made, and adding it finds out");
    expect(desk).not.toContain("Safari");
  });

  it("names, while locked, the passkeys that unlock it and how this device's is added, with the way to Account", async () => {
    passkey("credAAAA", "Laptop", "device", { kind: "synced" });
    passkey("credBBBB", "Yubi", "hardware", { kind: "hardware", maker: "Test Key 5 NFC" });
    await turnedOn("credAAAA");
    const onAccount = vi.fn();
    await shown({ onAccount });
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(screen.queryByRole("note", { name: "What unlocks encryption" })).not.toBeNull();
    const words = screen.getByRole("note", { name: "What unlocks encryption" });
    expect(words.textContent).toContain("Unlocks with “Laptop” (synced passkey), or the recovery code.");
    expect(words.textContent).not.toContain("Yubi");                       // not added to encryption
    expect(words.textContent).toContain("To unlock with a passkey of this device's");
    expect(words.textContent).toContain("then add it under What unlocks it, below");
    fireEvent.click(within(words).getByRole("button", { name: "Add it on Account" }));
    expect(onAccount).toHaveBeenCalledTimes(1);
  });

  it("says, while high security is on, that this device's must be a hardware key", async () => {
    passkey("credAAAA", "Yubi", "hardware", { kind: "hardware", maker: "Test Key 5 NFC" });
    await turnedOn("credAAAA");
    server.high = true;
    await shown();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    expect(screen.queryByRole("note", { name: "What unlocks encryption" })).not.toBeNull();
    const words = screen.getByRole("note", { name: "What unlocks encryption" });
    expect(words.textContent).toContain("Unlocks with “Yubi” (hardware key), or the recovery code.");
    expect(words.textContent).toContain("While high security is on, it must be a hardware key.");
  });

  it("says, where a security key gives no key in this browser, to unlock with it in another browser", async () => {
    passkey("credAAAA", "Blue", "device", { kind: "security-key" });
    await turnedOn("credAAAA");
    await shown();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    auth.creds.get("credAAAA")!.noPrf = true;                           // Safari gives a security key no PRF
    auth.attachment = "cross-platform";
    fireEvent.click(screen.getByRole("button", { name: /^Unlock$/ }));
    const alert = await screen.findByText(/gave no key in this browser/);
    expect(alert.textContent).toContain("Safari among them");
    expect(alert.textContent).toContain("On a computer, unlock with it in another browser, such as Chrome or Firefox");
    expect(alert.textContent).not.toBe(K.NO_PRF);
  });

  it("says, where a passkey of this device gives no key, what it said before", async () => {
    passkey("credAAAA", "Desk", "device", { kind: "device" });
    await turnedOn("credAAAA");
    await shown();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    auth.creds.get("credAAAA")!.noPrf = true;
    auth.attachment = "platform";
    fireEvent.click(screen.getByRole("button", { name: /^Unlock$/ }));
    expect((await screen.findByText(/gave no key in this browser/)).textContent).toBe(K.NO_PRF);
  });

  it("says, where a security key gives no key as it is added to encryption, to add it in another browser", async () => {
    passkey("credAAAA", "Laptop", "device", { kind: "synced" });
    passkey("credBBBB", "Blue", "device", { kind: "security-key" });
    await turnedOn("credAAAA");
    await shown();
    await act(async () => {
      await useE2EStore.getState().load(ACCOUNT);
      await useE2EStore.getState().unlock();
    });
    const row = within(keysList()).getAllByRole("listitem").find((li) => (li.textContent ?? "").startsWith("Blue"))!;
    fireEvent.click(within(row).getByRole("button", { name: "Add" }));
    auth.creds.get("credBBBB")!.noPrf = true;
    auth.attachment = "cross-platform";
    fireEvent.click(await within(row).findByRole("button", { name: "Touch it" }));
    // under a whole run's load the answer to the touch can come later than testing-library's default 1,000 ms:
    // the wait gets 10 s, inside the case's own 30 s
    const alert = await within(row).findByText(/gave no key in this browser/, {}, { timeout: 10_000 });
    expect(alert.textContent).toContain("so it cannot be added to encryption here");
    expect(alert.textContent).toContain("Add it on a computer, in another browser such as Chrome or Firefox");
  }, 30_000);

  const unlockSays = async () => {
    fireEvent.click(screen.getByRole("button", { name: /^Unlock$/ }));
    let said = "";
    await waitFor(() => { said = screen.queryByText(/gave no key in this browser/)?.textContent ?? ""; expect(said).not.toBe(""); });
    return said;
  };

  it("says of a synced passkey reached by its code that gives no key what it says of any passkey, not a security key's words", async () => {
    passkey("credAAAA", "Phone", "device", { kind: "synced" });
    await turnedOn("credAAAA");
    await shown();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    auth.creds.get("credAAAA")!.noPrf = true;
    auth.attachment = "cross-platform";                                // a phone reached by its code is cross-platform too
    expect(await unlockSays()).toBe(K.NO_PRF);
  });

  it("says a security key's words of a security key by what the server keeps of it, whatever the browser says now", async () => {
    passkey("credAAAA", "Blue", "device", { kind: "security-key" });
    await turnedOn("credAAAA");
    await shown();
    await act(async () => { await useE2EStore.getState().load(ACCOUNT); });
    auth.creds.get("credAAAA")!.noPrf = true;
    auth.attachment = null;                                            // the browser says nothing of it this time
    expect(await unlockSays()).toBe(K.NO_PRF_KEY);
  });

  it("does not send an iPhone or iPad to another browser, whose browsers there use Safari's engine", () => {
    for (const words of [K.NO_PRF_KEY, K.NO_PRF_KEY_ADD]) {
      expect(words.toLowerCase()).toContain("on a computer");
      expect(words).toContain("iPhone or iPad");
    }
  });

  it("says nothing of adding this device's passkey while encryption is off and keeps its keys, where nothing is added", async () => {
    const { thisDeviceWords } = await import("./Encryption");
    const off = { on: false, high_security: false, credentials: [], passkeys: [] } as unknown as K.E2EView;
    expect(thisDeviceWords(off)).toBe("");
    expect(thisDeviceWords({ ...off, on: true } as K.E2EView)).toContain("add it under What unlocks it");
  });
});
