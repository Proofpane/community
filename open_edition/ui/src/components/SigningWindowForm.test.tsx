// The signing window in the Instruct form: in a window opened in this tab the form sends with no passkey and says how
// long the instruction lives; a proposal and the window's last minute still take the passkey. The window is opened by
// the module itself (signWindow.test.ts).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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

const posted = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  calls.length = 0; order.length = 0;
  credentials.get.mockClear();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
});

describe("the Instruct form in a window", () => {
  // the window opened in this tab by the module itself (its own cases are signWindow.test.ts), imported after fresh()
  // so that it is the instance the form reads
  const openHere = async () => {
    const window = await import("../signWindow");
    return window.openWindow(await window.prepareWindow(L, FP, 300));
  };

  it("sends with no passkey, says the instruction lives until the window ends, and signs it with the window's key", async () => {
    serve(routes());
    const mod = await fresh();
    const w = await openHere();
    credentials.get.mockClear();
    const done = vi.fn(async () => {});
    render(<mod.SignBox purpose="instruction" to={L} toName="mac" placeholder="What mac should do" submit="Review" onDone={done} />);
    expect(screen.queryByLabelText("Expires after")).toBeNull();
    expect(screen.queryByText(`Lives until the window ends (${clock(w.expires)})`)).not.toBeNull();
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    const send = await screen.findByRole("button", { name: /Send \(signing window\)/ });
    await act(async () => { fireEvent.click(send); });
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    expect(credentials.get).not.toHaveBeenCalled();
    const [{ body }] = posted("/api/ade/link/commands");
    expect(body?.payload).toMatchObject({ text: "run the tests", delegation: w.digest, n: 1, to: L });
    expect((body?.delegated as { digest: string }).digest).toBe(w.digest);
    expect(body).not.toHaveProperty("credential");
  });

  // a settle that gave no ids would have said every lost answer not sent; the form reads this instruction's id from the
  // links list's commands of the link (each command's client_id)
  it("says sent when the answer did not come and the links list holds this very instruction, and not otherwise", async () => {
    let mine = true;
    serve(routes({
      "POST /api/ade/link/commands": () => { throw new TypeError("Failed to fetch"); },
      "GET /api/ade/link/sessions": () => {
        const { payload } = posted("/api/ade/link/commands").at(-1)!.body as { payload: { id: string; delegation: string } };
        return [200, { links: [{ id: L, name: "mac", delegation: { digest: payload.delegation, created_at: now(),
          expires_at: now() + 300, last_n: 1, untaken: 1 },
          commands: [{ seq: 4, kind: "instruction", client_id: mine ? payload.id : "d".repeat(32) }] }],
          questions: [], public_url: "https://pp.example" }];
      },
    }));
    const mod = await fresh();
    await openHere();
    const done = vi.fn(async () => {});
    render(<mod.SignBox purpose="instruction" to={L} toName="mac" placeholder="What mac should do" submit="Review" onDone={done} />);
    const sendOne = async (words: string) => {
      fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: words } });
      fireEvent.click(screen.getByRole("button", { name: /Review/ }));
      const send = await screen.findByRole("button", { name: /Send \(signing window\)/ });
      await act(async () => { fireEvent.click(send); });
    };
    await sendOne("run the tests");
    expect(await screen.findByText("Sent in your signing window: your instruction went to mac.")).toBeTruthy();
    expect(done).toHaveBeenCalledTimes(1);
    mine = false;                                       // the list holds a command, and not this one
    await sendOne("run them again");
    expect(await screen.findByText("Cannot reach the server. Check your connection.")).toBeTruthy();
    expect(posted("/api/ade/link/commands")).toHaveLength(2);
    expect(screen.queryByText("Sent in your signing window: your instruction went to mac.")).toBeNull();
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("says at its review when the session refuses every passkey that can open a window, and sends nothing then", async () => {
    // a passkey's review makes a pin check; without this case, a window's review with none would pass
    const K1 = "1a".repeat(16);
    serve(routes({
      "GET /api/account/passkeys": () => [200, { passkeys: [{ id: "p1", name: "Touch ID", created_at: 1, fingerprint: K1 }],
        high_security: false }],
      "GET /api/ade/e2e": () => [200, { links: [{ link_id: L, name: "mac", key_id: null, last_required_at: null,
        pins: ["9".repeat(32)] }] }],
    }));
    const mod = await fresh();
    await openHere();
    render(<mod.SignBox purpose="instruction" to={L} toName="mac" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByRole("button", { name: /Send \(signing window\)/ });
    expect(screen.queryByRole("note", { name: "Before you sign" })?.textContent ?? "")
      .toContain("mac will refuse what a signing window sends: it has not pinned Touch ID, the only passkey that can open one.");
    expect(posted("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs a proposal with the passkey even while a window is open", async () => {
    serve(routes());
    const mod = await fresh();
    await openHere();
    render(<mod.SignBox purpose="instruction" to={L} toName="mac" submit="Review" start="ship it" onDone={async () => {}}
      proposal={{ id: "c".repeat(32), by: "an app" }} />);
    expect(screen.queryByLabelText("Expires after")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Send \(signing window\)/ })).toBeNull();
  });

  it("in the window's last minute sends nothing under it, says why, and takes the passkey", async () => {
    serve(routes());
    const mod = await fresh();
    await openHere();
    const t0 = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(t0 + 245_000);
    render(<mod.SignBox purpose="instruction" to={L} toName="mac" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "late one" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    expect(await screen.findByText("An instruction needs a minute to be taken: send this one with your passkey.")).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
    expect(posted("/api/ade/link/commands")).toHaveLength(0);
  });
});

