import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonicalJson } from "../passkey";
import { requestCode } from "../secondScreen";
import { useStore } from "../store";

// Sign on my Mac on the page (SignOnMac.tsx, Signers.tsx): a reviewed step parked for a signer and sent on to the route
// the browser's assertion goes to, with the payload the page composed; Account's Signers; and what a command's row
// says of what signed it. The server is stood in for by the routes each test gives.
type Answer = [number, unknown];
/** What the server answers a request: at once, or when the test says (a promise it settles). */
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
const SERVER_TIME = 1_790_000_000;
let calls: { method: string; path: string; body: Record<string, unknown> | undefined }[] = [];

function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: String(url), body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path);
const nonce = (): Answer => [200, { nonce: "n-instruction", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }];
const credential = { id: "AQ", type: "public-key", response: { clientDataJSON: "x", authenticatorData: "y", signature: "z" } };

async function fresh() {
  vi.resetModules();
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  Object.defineProperty(navigator, "credentials", { value: { get: vi.fn() }, configurable: true });
  const { useStore: store } = await import("../store");
  store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
  return { ...(await import("./Channel")), ...(await import("./Signers")), ...(await import("./SignOnMac")), ...(await import("../secondScreen")) };
}

afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState());
  delete (navigator as unknown as { credentials?: unknown }).credentials;
});

describe("Sign on my Mac", () => {
  // the routes of a review, a park and its collection: `held` is what the server says it parked, by default the page's
  const routes = (signers: unknown[], held?: (payload: unknown) => unknown): Record<string, Route> => {
    let parked: unknown = null;
    return {
      "POST /api/ade/link/nonce": nonce,
      "GET /api/ade/signers": () => [200, { signers }],
      "POST /api/ade/sign-requests": (b) => { parked = b?.payload; return [200, { id: "r1", code: "AAAA-AAAA", expires_at: Math.floor(Date.now() / 1000) + 290 }]; },
      "GET /api/ade/sign-requests/r1": () => [200, { id: "r1", state: "signed", expires_at: SERVER_TIME + 300, credential, signer: "MacBook",
        payload: held ? held(parked) : null }],
      "DELETE /api/ade/sign-requests/r1": () => [200, { cancelled: 1 }],
      "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: SERVER_TIME + 3600 }],
    };
  };
  const review = async (signers: unknown[], held?: (payload: unknown) => unknown, more: Record<string, Route> = {}) => {
    const m = await fresh();
    serve({ ...routes(signers, held), ...more });
    const done = vi.fn(async () => {});
    render(<m.SignBox purpose="instruction" to="L1" toName="mac" placeholder="What mac should do" submit="Review" onDone={done} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByRole("button", { name: /Sign with your passkey/ });
    return { m, done };
  };

  it("is offered beside Sign only where a signer is paired, and asked only once a step is reviewed", async () => {
    await review([]);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Sign on my Mac/ })).toBeNull();
    expect(calls.findIndex((c) => c.path === "/api/ade/signers")).toBeGreaterThan(calls.findIndex((c) => c.path === "/api/ade/link/nonce"));
    cleanup();
    await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }]);
    expect(await screen.findByRole("button", { name: /Sign on my Mac/ })).toBeTruthy();
  });

  it("parks the step, shows the code the page computed, and sends the page's own payload once a signer signed it", async () => {
    const { done } = await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }], (p) => canonicalJson(p));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/sign-requests")).toHaveLength(1));
    const parked = sent("POST", "/api/ade/sign-requests")[0].body?.payload as Record<string, unknown>;
    const code = await requestCode(parked);
    expect(code).toMatch(/^[A-Z2-7]{4}-[A-Z2-7]{4}$/);
    expect((await screen.findByLabelText("Request code")).textContent).toBe(code);
    expect(screen.getByText(code.slice(0, 4))).toBeTruthy();                     // what the owner types on the Mac
    expect(sent("GET", "/api/ade/sign-requests/r1")).toHaveLength(0);     // nothing asked of the relay before its pace
    expect(code).not.toBe("AAAA-AAAA");                         // the page's own, never the server's
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    const [{ body }] = sent("POST", "/api/ade/link/commands");
    expect(body).toEqual({ payload: parked, credential });
    expect(screen.getByText(/Signed: your instruction went to mac\./)).toBeTruthy();
    // the next step reviewed is offered both ways again, and nothing is parked for it until asked
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "then push" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Sign on my Mac/ })).toBeTruthy();
    expect(sent("POST", "/api/ade/sign-requests")).toHaveLength(1);
  });

  it("says why, and keeps nothing, when the step's own route refuses what a signer signed", async () => {
    const { done } = await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }], (p) => canonicalJson(p), {
      "POST /api/ade/link/commands": () => [409, { detail: "This nonce was used already." }],
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect((await screen.findByRole("alert")).textContent).toBe("This nonce was used already.");
    expect(done).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Request code")).toBeNull();                   // back to the words, to review again
    expect(screen.getByRole("button", { name: /Review/ })).toBeTruthy();
  });

  it("is not offered where the server's list of signers is not one the page reads", async () => {
    await review([], undefined, { "GET /api/ade/signers": () => [200, { signers: "MacBook" }] });      // a length, not a list
    await vi.waitFor(() => expect(sent("GET", "/api/ade/signers")).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Sign on my Mac/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
  });

  it("sends nothing when the payload the server holds is not the one the page parked", async () => {
    const { done } = await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }],
      (p) => JSON.stringify({ ...(p as Record<string, unknown>), text: "push it all" }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect((await screen.findByRole("alert")).textContent).toMatch(/other than the one this page parked/);
    expect(sent("POST", "/api/ade/link/commands")).toHaveLength(0);
    expect(done).not.toHaveBeenCalled();
  });

  it("cancels the request, with any assertion it holds, and goes back to Sign", async () => {
    await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }]);
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    await vi.waitFor(() => expect(sent("DELETE", "/api/ade/sign-requests/r1")).toHaveLength(1));
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
  });

  it("sends nothing once cancelled, even where a signer's assertion arrives after Cancel", async () => {
    let release: (a: Answer) => void = () => {};
    let parked: unknown = null;
    const { done } = await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }], undefined, {
      "POST /api/ade/sign-requests": (b) => { parked = b?.payload; return [200, { id: "r1", code: "x", expires_at: Math.floor(Date.now() / 1000) + 290 }]; },
      "GET /api/ade/sign-requests/r1": () => new Promise<Answer>((settle) => { release = settle; }),
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    await vi.waitFor(() => expect(sent("GET", "/api/ade/sign-requests/r1")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));                // while the page waits for its answer
    await vi.waitFor(() => expect(sent("DELETE", "/api/ade/sign-requests/r1")).toHaveLength(1));
    await act(async () => { release([200, { id: "r1", state: "signed", expires_at: SERVER_TIME + 300, credential, payload: canonicalJson(parked) }]); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    expect(sent("POST", "/api/ade/link/commands")).toHaveLength(0);
    expect(done).not.toHaveBeenCalled();
  });

  it("asks nothing more once the request's time ended, and says to review it again", async () => {
    await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }], undefined, {
      "POST /api/ade/sign-requests": () => [200, { id: "r1", code: "x", expires_at: Math.floor(Date.now() / 1000) + 1 }],
      "GET /api/ade/sign-requests/r1": () => [200, { id: "r1", state: "waiting", expires_at: SERVER_TIME + 300 }],
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect((await screen.findByRole("alert")).textContent).toBe("This request's time ended before a signer signed it: review it again.");
    await act(async () => { await vi.advanceTimersByTimeAsync(6100); });
    expect(sent("GET", "/api/ade/sign-requests/r1")).toHaveLength(0);
    expect(screen.queryByText(/seconds left/)).toBeNull();
  });

  it("parks nothing for a review more than five minutes old", async () => {
    await review([{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }]);
    const onMac = await screen.findByRole("button", { name: /Sign on my Mac/ });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(Date.now() + 291_000);
    fireEvent.click(onMac);
    expect((await screen.findByRole("alert")).textContent).toBe("This review is more than five minutes old: review it again.");
    await act(async () => {});
    expect(sent("POST", "/api/ade/sign-requests")).toHaveLength(0);
  });
});

describe("Sign on my Mac, for a move", () => {
  const session = { id: "s1", title: "Fix the sign-in", project: "~/work/app", branch: "main", started_at: SERVER_TIME - 7200,
    last_at: SERVER_TIME - 3600, link_id: "L1", link: "mac", kind: "local", listed_at: SERVER_TIME - 60 } as never;
  const MOVES = "GET /api/ade/link/moves?session=s1";
  const signer = { id: "s1", name: "MacBook", created_at: 1, last_seen_at: null };
  /** The routes of a move reviewed, parked, signed on the Mac and sent; `parked` is what the page parked. */
  const moveRoutes = (more: Record<string, Route> = {}) => {
    const parked: { body?: Record<string, unknown> } = {};
    const routes: Record<string, Route> = {
      "POST /api/ade/link/nonce": () => [200, { nonce: "n-move", rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }],
      [MOVES]: () => [200, { moves: [] }],
      "GET /api/ade/signers": () => [200, { signers: [signer] }],
      "POST /api/ade/sign-requests": (b) => { parked.body = b; return [200, { id: "r1", code: "x", expires_at: Math.floor(Date.now() / 1000) + 290 }]; },
      "GET /api/ade/sign-requests/r1": () => [200, { id: "r1", state: "signed", expires_at: SERVER_TIME + 300, credential,
        payload: canonicalJson(parked.body?.payload) }],
      "POST /api/ade/link/commands": () => [200, { seq: 40, expires_at: SERVER_TIME + 3600 }],
      ...more,
    };
    return { parked, routes };
  };
  const reviewMove = async (routes: Record<string, Route>) => {
    const m = await fresh();
    serve(routes);
    const { useStore: store } = await import("../store");
    store.setState({ ws: { enabled: true, allowed: true, running: true } as never });
    render(<m.MoveToWorkspace s={session} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(sent("GET", "/api/ade/signers")).toHaveLength(0);                     // asked only once the move is reviewed
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    await screen.findByRole("button", { name: /Sign with your passkey/ });
    return m;
  };

  it("is not offered where no signer is paired", async () => {
    const { routes } = moveRoutes({ "GET /api/ade/signers": () => [200, { signers: [] }] });
    await reviewMove(routes);
    await vi.waitFor(() => expect(sent("GET", "/api/ade/signers")).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Sign on my Mac/ })).toBeNull();
  });

  it("goes back to the move's Sign when the request is cancelled", async () => {
    const { routes } = moveRoutes({ "DELETE /api/ade/sign-requests/r1": () => [200, { cancelled: 1 }] });
    await reviewMove(routes);
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    await vi.waitFor(() => expect(sent("DELETE", "/api/ade/sign-requests/r1")).toHaveLength(1));
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();
    expect(sent("POST", "/api/ade/link/commands")).toHaveLength(0);
  });

  it("parks the move, sends the page's own payload once a signer signed it, and offers both ways again after", async () => {
    const { parked, routes } = moveRoutes();
    await reviewMove(routes);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    expect(Object.keys(parked.body ?? {})).toEqual(["payload"]);                  // no title: the server has the row's
    expect(parked.body?.payload).toMatchObject({ purpose: "move", session: "s1", to: "L1", nonce: "n-move" });
    expect(screen.queryByRole("button", { name: /Sign with your passkey/ })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    await vi.waitFor(() => expect(sent("POST", "/api/ade/link/commands")).toHaveLength(1));
    expect(sent("POST", "/api/ade/link/commands")[0].body).toEqual({ payload: parked.body?.payload, credential });
    expect(await screen.findByText("Signed: mac's machine may take it now.")).toBeTruthy();
    expect(screen.queryByLabelText("Request code")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    expect(await screen.findByRole("button", { name: /Sign with your passkey/ })).toBeTruthy();       // not parked again
    expect(await screen.findByRole("button", { name: /Sign on my Mac/ })).toBeTruthy();
    expect(sent("POST", "/api/ade/sign-requests")).toHaveLength(1);
  });

  it("says why, and goes back to Review, when the move's route refuses what a signer signed", async () => {
    const { routes } = moveRoutes({ "POST /api/ade/link/commands": () => [409, { detail: "This session is moving already." }] });
    await reviewMove(routes);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect((await screen.findByRole("alert")).textContent).toBe("This session is moving already.");
    expect(screen.queryByLabelText("Request code")).toBeNull();
    expect(screen.getByRole("button", { name: /Review the move/ })).toBeTruthy();
  });

  it("parks nothing for a move reviewed more than five minutes ago", async () => {
    const { routes } = moveRoutes();
    await reviewMove(routes);
    const onMac = await screen.findByRole("button", { name: /Sign on my Mac/ });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(Date.now() + 291_000);
    fireEvent.click(onMac);
    expect((await screen.findByRole("alert")).textContent).toBe("This review is more than five minutes old: review it again.");
    await act(async () => {});
    expect(sent("POST", "/api/ade/sign-requests")).toHaveLength(0);
  });

  it("parks a move's title beside its payload, as the page sends it beside the signature", async () => {
    const m = await fresh();
    serve({ "POST /api/ade/sign-requests": () => [200, { id: "r1", code: "x", expires_at: Math.floor(Date.now() / 1000) + 290 }] });
    const payload = { v: 2, purpose: "move", session: "s1", to: "L1" };
    render(<m.SignOnMac prepared={{ payload, challenge: new Uint8Array(32), rpId: "pp.example", words: "", allow: [] } as never}
      title="Fix the sign-in" onSigned={async () => {}} onCancel={() => {}} />);
    await screen.findByLabelText("Request code");
    expect(sent("POST", "/api/ade/sign-requests")[0].body).toEqual({ payload, title: "Fix the sign-in" });
  });
});

describe("what a command's row says of what signed it", () => {
  const command = (over: Record<string, unknown>) => ({ seq: 7, time: "2026-10-03T00:00:00Z", kind: "instruction", from: "owner",
    to: "mac", ref: null, text: "ship it", state: "taken" as const, ...over });
  const link = (commands: unknown[]) => ({ id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1,
    last_seen_at: null, last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
    checkpoint_at: null, events: 0, last_event: null, stopped_at: null, commands } as never);
  const keys = [{ id: "pk1", name: "YubiKey 5C", tier: "hardware" }, { id: "pk2", name: "Phone", tier: "device" }];

  it("names the passkey and the signer, and says what the marker means a click away from the words naming it", async () => {
    const m = await fresh();
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: keys }] });
    render(<m.Commands l={link([command({ passkey_id: "pk1", marked: true, relayed_by: "MacBook" })])} />);
    expect(await screen.findByText(/Signed with YubiKey 5C \(hardware key\), relayed by your signer MacBook, says it was signed outside a browser/)).toBeTruthy();
    expect(screen.queryByText(/An honest browser never says so/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "what this means" }));
    expect(screen.getByText(/An honest browser never says so; a compromised one, or any other program, can\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "what this means" }));
    expect(screen.queryByText(/An honest browser never says so/)).toBeNull();
  });

  it("says nothing of the marker for a passkey held in software, nor for one it does not know", async () => {
    const m = await fresh();
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: keys }] });
    render(<m.Commands l={link([command({ passkey_id: "pk2", marked: true, relayed_by: null }), command({ seq: 8, passkey_id: "x", marked: true })])} />);
    expect(await screen.findByText("Signed with Phone")).toBeTruthy();
    expect(screen.queryByText(/outside a browser/)).toBeNull();
  });

  it("asks for the account's passkeys only where there is a command to name one beside", async () => {
    const m = await fresh();
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: keys }] });
    render(<m.Commands l={link([])} />);
    await act(async () => {});
    expect(sent("GET", "/api/account/passkeys")).toHaveLength(0);
  });
});

describe("Account's Signers", () => {
  it("lists the signers and what waits, a code only beside what this page parked, and cancels them all", async () => {
    const m = await fresh();
    serve({
      "GET /api/ade/signers": () => [200, { signers: [{ id: "s1", name: `Mac${String.fromCodePoint(0x202e)}Book`, created_at: SERVER_TIME, last_seen_at: null }] }],
      "GET /api/ade/sign-requests": () => [200, { requests: [
        { id: "elsewhere", purpose: "instruction", to: "L1", created_at: SERVER_TIME, expires_at: SERVER_TIME + 300, parked_from: "203.0.113.9", state: "waiting" }] }],
      "DELETE /api/ade/sign-requests": () => [200, { cancelled: 1 }],
      "DELETE /api/ade/signers/s1": () => [409, { detail: "That signer was revoked already." }],
    });
    render(<m.SignersSection />);
    expect(await screen.findByText("Mac[U+202E]Book")).toBeTruthy();
    expect(screen.getByText("not parked by this page")).toBeTruthy();
    expect(screen.getByText(/parked from 203\.0\.113\.9 \(the server's word\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Cancel all/ }));
    await vi.waitFor(() => expect(sent("DELETE", "/api/ade/sign-requests")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: /Revoke Mac\[U\+202E\]Book/ }));
    await vi.waitFor(() => expect(sent("DELETE", "/api/ade/signers/s1")).toHaveLength(1));
    expect((await screen.findByRole("alert")).textContent).toBe("That signer was revoked already.");
  });

  it("writes where a request was parked from with every hidden character shown", async () => {
    const m = await fresh();
    serve({
      "GET /api/ade/signers": () => [200, { signers: [] }],
      "GET /api/ade/sign-requests": () => [200, { requests: [{ id: "r", purpose: "answer", to: "L1", created_at: SERVER_TIME,
        expires_at: SERVER_TIME + 300, parked_from: `203.0.113.9${String.fromCodePoint(0x202e)}`, state: "waiting" }] }],
    });
    render(<m.SignersSection />);
    expect(await screen.findByText("waiting, parked from 203.0.113.9[U+202E] (the server's word)")).toBeTruthy();
    expect(screen.getByText("An answer")).toBeTruthy();
  });

  it("takes lists not in the shape the page reads as lists it could not read", async () => {
    const m = await fresh();
    serve({ "GET /api/ade/signers": () => [200, { signers: { s1: "MacBook" } }], "GET /api/ade/sign-requests": () => [200, { requests: [] }] });
    render(<m.SignersSection />);
    expect(await screen.findByText("The signers could not be read from the server just now.")).toBeTruthy();
    expect(screen.queryByText("No Mac is paired as a signer.")).toBeNull();
  });

  it("shows a code beside a request this page parked alone, with its kind and whether a signer has signed it", async () => {
    const m = await fresh();
    const request = (id: string, purpose: string, state: string, from: string) =>
      ({ id, purpose, to: "L1", created_at: SERVER_TIME, expires_at: SERVER_TIME + 300, parked_from: from, state });
    serve({
      "POST /api/ade/sign-requests": () => [200, { id: "here", code: "AAAA-AAAA", expires_at: SERVER_TIME + 300 }],
      "GET /api/ade/signers": () => [200, { signers: [] }],
      "GET /api/ade/sign-requests": () => [200, { requests: [request("here", "instruction", "signed", "198.51.100.7"),
                                                             request("there", "move", "waiting", "203.0.113.9")] }],
    });
    const parked = await m.parkOnMac({ v: 2, purpose: "instruction", text: "run the tests" });
    render(<m.SignersSection />);
    expect(await screen.findByText(parked.code)).toBeTruthy();
    expect(screen.queryByText("AAAA-AAAA")).toBeNull();                              // the page's code, never the server's
    expect(screen.getAllByText("not parked by this page")).toHaveLength(1);
    expect(screen.getByText("An instruction")).toBeTruthy();
    expect(screen.getByText("A move")).toBeTruthy();
    expect(screen.getByText("signed, not yet sent, parked from 198.51.100.7 (the server's word)")).toBeTruthy();
    expect(screen.getByText("waiting, parked from 203.0.113.9 (the server's word)")).toBeTruthy();
  });

  it("says, as no alert, that the lists could not be read", async () => {
    const m = await fresh();
    serve({ "GET /api/ade/signers": () => [500, { detail: "down" }], "GET /api/ade/sign-requests": () => [200, { requests: [] }] });
    render(<m.SignersSection />);
    expect(await screen.findByText("The signers could not be read from the server just now.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("No Mac is paired as a signer.")).toBeNull();          // nothing said of what was not read
  });

  it("makes a ticket with the account's confirmation and shows the command that pairs, once", async () => {
    const m = await fresh();
    const command = "ade_link.py signer pair --url https://pp.example --ticket PP-ABCDEFGH";
    let reads = 0;
    serve({
      "GET /api/ade/signers": () => (++reads === 1 ? [503, { detail: "busy" }] : [200, { signers: [] }]),   // read again once paired
      "GET /api/ade/sign-requests": () => [200, { requests: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c-1" }],
      "POST /api/ade/signers/tickets": () => [200, { ticket: "PP-ABCDEFGH", expires_at: SERVER_TIME + 600, url: "https://pp.example", account: "u", command }],
    });
    render(<m.SignersSection />);
    expect(await screen.findByText("The signers could not be read from the server just now.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Pair a Mac/ }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "correct horse battery" } });
    expect(sent("POST", "/api/ade/signers/tickets")).toHaveLength(0);     // no ticket before the confirmation
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(await screen.findByText(command)).toBeTruthy();
    // made for a signer ticket (passkeys.CHANGE), so it is spent on no other change and no other's is spent here
    expect(sent("POST", "/api/account/confirm")[0].body).toEqual({ password: "correct horse battery", change: "signer-ticket" });
    expect(sent("POST", "/api/ade/signers/tickets").map((c) => c.body)).toEqual([{ confirmation: "c-1" }]);
    expect(screen.queryByLabelText("Your password")).toBeNull();          // the confirmation asked once, then gone
    // the lists read again once the ticket is made: what could not be read is no longer said
    expect(await screen.findByText("No Mac is paired as a signer.")).toBeTruthy();
    expect(screen.queryByText("The signers could not be read from the server just now.")).toBeNull();
  });
});
