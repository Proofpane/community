// The signing window on the link's card and in its Instruct form: where none can be opened, the control says why;
// opening is one passkey touch after the words that say what it means; in the window the form sends with no passkey and
// says how long the instruction lives; a proposal and the last minute still take the passkey; End now says how many it
// drops; each command's line names its window.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LinkEvent, LinkView } from "../api";
import { canonicalJson } from "../passkey";
import { clock } from "../when";

type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
const L = "1".repeat(32);
const FP = "f".repeat(32);
const now = () => Math.floor(Date.now() / 1000);
const calls: { method: string; path: string; body?: Record<string, unknown> }[] = [];
const order: string[] = [];
const sha256 = async (text: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");

const routes = (more: Record<string, Route> = {}): Record<string, Route> => ({
  "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example",
    account: "u", server_time: now(), credentials: [] }],
  "POST /api/ade/link/delegations": async (b) => [200, {
    digest: hex(await sha256(`proofpane-delegation/1\0${canonicalJson(b?.payload)}`)),
    expires_at: (b?.payload as { expires_at: number }).expires_at }],
  "POST /api/ade/link/commands": () => [200, { seq: 11, expires_at: now() + 300 }],
  "GET /api/ade/link/sessions": () => [200, { links: [], questions: [], public_url: "https://pp.example" }],
  "GET /api/account/passkeys": () => [200, { passkeys: [], high_security: false }],
  ...more,
});

function serve(r: Record<string, Route>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: String(url), body });
    order.push(`${method} ${url}`);
    const route = r[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

const credentials = {
  get: vi.fn(async (_o: { publicKey: { challenge: ArrayBuffer | Uint8Array } }) => {
    order.push("get");
    const b = (n: number) => new Uint8Array([n]).buffer;
    return { id: "AQ", type: "public-key", response: { clientDataJSON: b(1), authenticatorData: b(2), signature: b(3), userHandle: b(4) } };
  }),
};

async function fresh(passkeys = true) {
  vi.resetModules();
  if (passkeys) {
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  }
  const { useStore } = await import("../store");
  useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
  return import("./Channel");
}

const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: L, name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, fingerprint: FP, ...over,
});
const posted = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  calls.length = 0; order.length = 0;
  credentials.get.mockClear();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
});

describe("the signing window on a link's card", () => {
  it("is offered, and disabled with why, on a link whose client does not say it takes windows", async () => {
    serve(routes());
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: [] })} questions={[]} onTicket={() => {}} />);
    const open = screen.queryByRole("button", { name: /Open a signing window/ });
    expect(open).not.toBeNull();
    expect((open as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/mac's client has not said it takes signing windows, so each instruction/)).not.toBeNull();
  });

  it("is disabled where this browser cannot sign", async () => {
    serve(routes());
    const { LinkCard } = await fresh(false);
    render(<LinkCard l={link({ features: ["delegation-1"] })} questions={[]} onTicket={() => {}} />);
    const open = screen.queryByRole("button", { name: /Open a signing window/ });
    expect(open && (open as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("This browser cannot sign here.")).not.toBeNull();
  });

  it("says what a window means before the passkey is asked, and opens with one touch over the delegation", async () => {
    serve(routes());
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: ["delegation-1"] })} questions={[]} onTicket={() => {}} />);
    const open = screen.queryByRole("button", { name: /Open a signing window/ });
    expect(open).not.toBeNull();
    fireEvent.click(open!);
    expect(screen.queryByText(/For the next 5 minutes, instructions you send to mac from this tab are signed by this tab without your passkey\. Whoever controls this page in that time can instruct mac without your touch\./)).not.toBeNull();
    fireEvent.change(screen.getByLabelText("Window length"), { target: { value: "900" } });
    expect(screen.queryByText(/For the next 15 minutes/)).not.toBeNull();
    const go = await screen.findByRole("button", { name: /Open the window with your passkey/ });
    await vi.waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    expect(credentials.get).not.toHaveBeenCalled();             // nothing signed before the click
    order.length = 0;
    await act(async () => { fireEvent.click(go); });
    expect(order[0]).toBe("get");                                // the prompt before anything is fetched
    expect(credentials.get).toHaveBeenCalledTimes(1);
    const [{ body }] = posted("/api/ade/link/delegations");
    const payload = body?.payload as Record<string, unknown>;
    expect(payload.expires_at as number - (payload.created_at as number)).toBe(900);
    const asked = credentials.get.mock.calls[0][0].publicKey;
    expect(new Uint8Array(asked.challenge as ArrayBuffer)).toEqual(await sha256(`proofpane-step/1\0${canonicalJson(payload)}`));
    expect(await screen.findByText(/A signing window is open for mac: 1[45]:\d\d left by this device's clock/)).toBeTruthy();
  });

  it("makes the window again when its preparation is more than half a minute old, so it opens for its whole length", async () => {
    // a window composed when the words were first read, and opened minutes later,
    // would open with that much of its length gone
    serve(routes());
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: ["delegation-1"] })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Open a signing window/ }));
    const go = await screen.findByRole("button", { name: /Open the window with your passkey/ });
    await vi.waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    const asked = posted("/api/ade/link/nonce").length;
    const t0 = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(t0 + 31_000);
    await act(async () => { fireEvent.click(go); });
    expect(credentials.get).not.toHaveBeenCalled();
    expect(await screen.findByText(/made again so that the window lasts its whole length/)).toBeTruthy();
    await vi.waitFor(() => expect(posted("/api/ade/link/nonce").length).toBe(asked + 1));
  });

  it("says before the touch which passkey the session has not pinned: a window that key opens sends nothing it takes", async () => {
    // without this case, an opener that warns nothing would pass; the form says "Sent" for each instruction the session
    // refuses as unpinned
    const K1 = "1a".repeat(16), K2 = "2b".repeat(16);
    serve(routes({
      "GET /api/account/passkeys": () => [200, { passkeys: [{ id: "p1", name: "Touch ID", created_at: 1, fingerprint: K1 },
        { id: "p2", name: "iPhone", created_at: 2, fingerprint: K2 }], high_security: false }],
      "GET /api/ade/e2e": () => [200, { links: [{ link_id: L, name: "mac", key_id: null, last_required_at: null, pins: [K1] }] }],
    }));
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: ["delegation-1"] })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Open a signing window/ }));
    const note = () => screen.queryByRole("note", { name: "Before you sign" })?.textContent ?? "";
    await vi.waitFor(() => expect(note()).toContain("mac takes what a signing window sends only if Touch ID opened it: it "
      + "has not pinned iPhone, and refuses what a window that key opens sends."));
    expect(note()).toContain(`pin --add ${K2}`);
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("opens none while high security is on: the nonce names the hardware keys alone", async () => {
    serve(routes({ "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example",
      deployment: "pp.example", account: "u", server_time: now(), credentials: ["AQ"] }] }));
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: ["delegation-1"] })} questions={[]} onTicket={() => {}} />);
    const open = screen.queryByRole("button", { name: /Open a signing window/ });
    expect(open).not.toBeNull();
    fireEvent.click(open!);
    expect(await screen.findByText("High security is on: every command takes your hardware key.")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Open the window with your passkey/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("ends a window opened on another device, saying how many it drops", async () => {
    const digest = "ab".repeat(32);
    serve(routes({ [`POST /api/ade/link/delegations/${digest}/end`]: () => [200, { ended_at: now() }] }));
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ features: ["delegation-1"], delegation: { digest, created_at: now() - 60, expires_at: now() + 240,
      last_n: 3, untaken: 2 } })} questions={[]} onTicket={() => {}} />);
    const end = screen.queryByRole("button", { name: "End now (drops the 2 not yet taken)" });
    expect(end).not.toBeNull();
    await act(async () => { fireEvent.click(end!); });
    expect(posted(`/api/ade/link/delegations/${digest}/end`)).toHaveLength(1);
  });
});

describe("each command's line", () => {
  const command = (over: Partial<LinkEvent>): LinkEvent => ({ seq: 5, time: "2026-11-10T17:00:00Z", kind: "instruction",
    from: "owner", to: "mac", ref: null, text: "run the tests", ...over } as LinkEvent);

  it.each([
    ["withdrawn", { ended_at: 1_794_326_500, ended_reason: "end_now" }, "withdrawn: you ended the window"],
    ["withdrawn", { ended_at: 1_794_326_500, ended_reason: "high_security" }, "withdrawn: high security was turned on"],
    ["expired", {}, "not taken: the window ended"],
  ])("names the window it was sent in, and why it was dropped (%s)", async (state, ended, why) => {
    serve(routes());
    const { Commands } = await fresh();
    const digest = "ab12cd34".repeat(8);
    render(<Commands l={link({ commands: [command({ state: state as LinkEvent["state"],
      delegation: { digest, created_at: 1_794_326_400, expires_at: 1_794_326_700, ...ended } })] })} />);
    expect(screen.queryByText(`You sent an instruction in a signing window (opened ${clock(1_794_326_400)}, id ab12cd34) · ${why}`)).not.toBeNull();
  });
});
