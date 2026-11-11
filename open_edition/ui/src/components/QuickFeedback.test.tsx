// On the workspace's page a
// dismissal or a decision acts on the page at once, its buttons disabled so a second press sends nothing, while the
// request goes on; a refusal, or no answer in time, brings the card back as it was with the reason, and nothing shows
// as decided that the server did not take. A message the owner sends shows at once in the conversation as sending,
// with a moving indicator, its send disabled until it is taken or refused; a refused one keeps its words in the
// composer with the reason, and Review sends it again.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type HookRequest, type LinkAsk, type LinkEvent, type LinkMove, type LinkView, type Proposal, type SessionAsk, type SessionRecord } from "../api";
import { useStore } from "../store";

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Answer = [number, unknown];
/** What the server answers a request: at once, or when the test says (a promise it settles). */
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
let calls: Call[] = [];
/** The server as the test has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
/** An answer the server gives only when the test settles it. */
function later<T>() {
  let settle!: (v: T) => void;
  const answer = new Promise<T>((resolve) => { settle = resolve; });
  return { answer, settle };
}

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
  useStore.setState(useStore.getInitialState());
});

const SERVER_TIME = 1_790_000_000;
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 15, created_at: SERVER_TIME - 3600,
  last_seen_at: SERVER_TIME - 60, last_heartbeat_at: SERVER_TIME - 60, silent: false, acked_seq: 0, checkpoint: null,
  checkpoint_version: 0, checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const WAITING = "Waiting for your word on the release.";
const waiting: LinkEvent = { seq: 9, time: "2026-11-08T00:00:00Z", kind: "waiting", from: "mac", to: "owner", ref: null, text: WAITING };
const QUESTION = "Which cap: per user, or per org?";
const question: LinkEvent = { seq: 12, time: "2026-11-08T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: QUESTION };
const routes = (more: Record<string, Route> = {}): Record<string, Route> => ({
  "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }],
  "POST /api/ade/link/commands": () => [200, { seq: 30, expires_at: SERVER_TIME + 3600 }],
  "GET /api/ade/link/sessions": () => [200, { links: [link()], questions: [], public_url: "https://pp.example" }],
  "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
  ...more,
});
const credentials = {
  get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: {
    clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
    signature: new Uint8Array([3]).buffer, userHandle: new Uint8Array([4]).buffer } })),
};
/** Channel.tsx as a page loads it, signed in, with passkeys that sign. */
const fresh = async () => {
  vi.resetModules();
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  const { useStore: store } = await import("../store");
  store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
  return import("./Channel");
};
/** A component's module as a page loads it, with the api and the store that module itself reads (a fresh graph each
 *  time, as `fresh` makes one: a spy or a state set through another graph's would not reach it). */
const freshly = async <M,>(load: () => Promise<M>) => {
  vi.resetModules();
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  const [{ Api: api }, { useStore: store }, mod] = await Promise.all([import("../api"), import("../store"), load()]);
  store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true }, refresh: async () => {}, refreshApprovals: async () => {} });
  return { api, store, mod };
};
/** The card a line of it is in: the box its "<from> waits for you" or "<from> asks you" line heads. */
const cardOf = (line: RegExp) => screen.getByText(line).parentElement as HTMLElement;

describe("a dismissal acts on the page at once", () => {
  it("closes a waiting's card when Dismiss is pressed, before the server answers, and sends it once", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/9/close": () => close.answer }));
    const { LinkCard } = await fresh();
    const reread = later<void>();                                  // the links read again once taken, held
    (await import("../store")).useStore.setState({ refreshLinks: () => reread.answer });
    render(<LinkCard l={link()} questions={[]} waitings={[waiting]} onTicket={() => {}} />);
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    act(() => { dismiss.click(); dismiss.click(); });             // two presses before the page draws again
    expect(screen.queryByText(WAITING)).toBeNull();               // gone at once: the server has not answered
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1));
    await act(async () => { close.settle([200, { seq: 10 }]); });
    expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1);
    expect(screen.queryByText(WAITING)).toBeNull();
    await act(async () => { reread.settle(); });
  });

  it("closes a question's card when Dismiss question is pressed, before the server answers", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/12/close": () => close.answer }));
    const { Question } = await fresh();
    render(<Question q={question} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    expect(screen.queryByText(QUESTION)).toBeNull();
    await act(async () => { close.settle([200, { seq: 13 }]); });
    expect(sent("/api/ade/link/questions/12/close")).toHaveLength(1);
  });

  // a withdrawn command stays on its list, which keeps every state; it is said withdrawn at once
  it("says a queued command withdrawn when Withdraw is pressed, before the server answers", async () => {
    const withdraw = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands/21/withdraw": () => withdraw.answer }));
    const { Commands } = await fresh();
    const l = link({ commands: [{ seq: 21, time: "2026-11-08T00:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null,
      text: "run the release checks", state: "queued" }] });
    render(<Commands l={l} />);
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(screen.queryByText(/run the release checks/)).not.toBeNull();
    expect(screen.queryByText(/^Withdrawn/)).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
    await act(async () => { withdraw.settle([200, { withdrawn_at: SERVER_TIME }]); });
    expect(sent("/api/ade/link/commands/21/withdraw")).toHaveLength(1);
  });
});

describe("a dismissal the server does not take comes back", () => {
  it("brings a waiting's card back as it was, with the server's reason on it", async () => {
    serve(routes({ "POST /api/ade/link/questions/9/close": () => [409, { detail: "That waiting was answered already." }] }));
    const { LinkCard } = await fresh();
    render(<LinkCard l={link()} questions={[]} waitings={[waiting]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1));
    const card = await vi.waitFor(() => cardOf(/^mac waits for you/));
    await vi.waitFor(() => expect(card.textContent).toContain("That waiting was answered already."));
    expect(card.textContent).toContain(WAITING);
    expect((within(card).getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("brings a question's card back, saying so, when the server has not answered in 20 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(routes({ "POST /api/ade/link/questions/12/close": () => new Promise<Answer>(() => {}) }));
    const { Question } = await fresh();
    render(<Question q={question} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/12/close")).toHaveLength(1));
    await act(async () => { vi.advanceTimersByTime(20_000); });
    const card = await vi.waitFor(() => cardOf(/^mac asks you/));
    expect(card.textContent).toContain(QUESTION);
    expect(card.textContent).toMatch(/did not answer in 20 seconds/);
  });
});

describe("a decision acts on the page at once", () => {
  const held: HookRequest = { id: "r1", command: "git push origin main", state: "pending", created_at: SERVER_TIME - 30,
    until: SERVER_TIME + 600, token_name: "Mac", session: "s1", cwd: "/w" } as HookRequest;
  /** Approvals as the app has it: the requests are the store's, read again after an answer. */
  const show = async (decide: () => Promise<unknown>) => {
    const { api, store, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const decided = vi.spyOn(api, "decide").mockImplementation(decide);
    store.setState({ approvals: [held] });
    const Page = () => <Approvals records={[]} requests={store((s) => s.approvals)} onClose={() => {}} onOpen={() => {}} />;
    render(<Page />);
    return { decided, store };
  };

  it("shows a Deny as taken at once, its buttons disabled, and sends it once", async () => {
    const answer = later<unknown>();
    const { decided, store } = await show(() => answer.answer);
    const deny = screen.getByRole("button", { name: "Deny" });
    act(() => { deny.click(); deny.click(); });                   // two presses before the page draws again
    expect(screen.queryByText(/^Denied/)).not.toBeNull();
    expect((screen.getByRole("button", { name: "Deny" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Approve once" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(decided).toHaveBeenCalledTimes(1);
    expect(decided).toHaveBeenCalledWith("r1", false);
    await act(async () => { answer.settle({}); store.setState({ approvals: [] }); });
    expect(screen.queryByText("git push origin main")).toBeNull();
  });

  it("brings the card back as it was, with the server's reason, when the Deny is refused", async () => {
    await show(async () => { throw new ApiError(409, "That command was answered on another device."); });
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await vi.waitFor(() => expect(screen.queryByText(/That command was answered on another device\./)).not.toBeNull());
    expect(screen.queryByText(/^Denied/)).toBeNull();
    expect(screen.getByText("git push origin main")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Deny" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("a message the owner sends shows as sending", () => {
  /** The conversation of L1 with words written and reviewed, ready to sign. */
  const reviewed = async (commands: Route) => {
    serve(routes({ "POST /api/ade/link/commands": commands }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText("Message mac"), { target: { value: "and the docs" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText(/^You are signing an instruction to mac \(link L1\)/);
  };

  it("shows it at once in the conversation as sending, with a moving indicator, and sends it once", async () => {
    const taken = later<Answer>();
    await reviewed(() => taken.answer);
    const sign = screen.getByRole("button", { name: /Sign with your passkey/ });
    act(() => { sign.click(); sign.click(); });                   // two presses before the page draws again
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    const said = screen.getByLabelText("The conversation");
    const row = await vi.waitFor(() => within(said).getByText("and the docs").closest("[data-sending]") as HTMLElement);
    expect(row.textContent).toMatch(/Sending/);
    expect(row.querySelector(".animate-spin")).not.toBeNull();
    const again = screen.getByRole("button", { name: /Sending/ }) as HTMLButtonElement;
    expect(again.disabled).toBe(true);
    fireEvent.click(again);
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
    await vi.waitFor(() => expect(within(said).queryByText(/Sending/)).toBeNull());
  });

  it("keeps a refused message's words in the composer with the reason, and Review signs and sends it again", async () => {
    let refused = true;
    await reviewed(() => (refused ? [409, { detail: "mac is stopped: resume it first." }] : [200, { seq: 31, expires_at: SERVER_TIME + 3600 }]));
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await screen.findByText(/mac is stopped: resume it first\./);
    expect((screen.getByLabelText("Message mac") as HTMLTextAreaElement).value).toBe("and the docs");
    expect(within(screen.getByLabelText("The conversation")).queryByText(/Sending/)).toBeNull();
    expect((screen.getByRole("button", { name: /Review/ }) as HTMLButtonElement).disabled).toBe(false);
    refused = false;
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(2));
    expect(sent("/api/ade/link/commands")[1].body?.payload).toMatchObject({ text: "and the docs" });
  });
});

describe("each card's press on the workspace's page acts at once", () => {
  const ask: LinkAsk = { id: "A1", name: "cloud-2", kind: "cloud", fingerprint: "ab".repeat(16), asked_from: "203.0.113.9",
    created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600, session_ref: null, state: "pending", hold_id: null,
    takes_effect_at: null, told: null, failed: null };
  const disabled = (name: string | RegExp) => (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
  /** Presses the button twice before the page draws again. */
  const twice = (name: string | RegExp) => {
    const b = screen.getByRole("button", { name });
    act(() => { b.click(); b.click(); });
  };

  it("shows a link request's Refuse as taken, its buttons disabled, and sends it once", async () => {
    const refuse = later<Answer>();
    serve(routes({ "POST /api/ade/link/asks/A1/refuse": () => refuse.answer }));
    const { AskCard } = await fresh();
    render(<AskCard a={ask} links={[]} twins={1} />);
    twice("Refuse");
    expect(screen.queryByText(/^Refused/)).not.toBeNull();
    expect([disabled("Refuse"), disabled("Approve")]).toEqual([true, true]);
    await act(async () => { refuse.settle([200, { refused: "A1" }]); });
    expect(sent("/api/ade/link/asks/A1/refuse")).toHaveLength(1);
  });

  it("shows a pairing's Cancel as taken, its button disabled, and sends it once", async () => {
    const cancel = later<Answer>();
    serve(routes({ "POST /api/ade/holds/H2/cancel": () => cancel.answer }));
    const { PairingCard } = await fresh();
    render(<PairingCard a={{ ...ask, id: "A2", via: "ticket", state: "held", hold_id: "H2", takes_effect_at: SERVER_TIME + 180 }} />);
    twice("Cancel");
    expect(screen.queryByText(/^Cancelled/)).not.toBeNull();
    expect(disabled("Cancel")).toBe(true);
    await act(async () => { cancel.settle([200, { cancelled_at: SERVER_TIME }]); });
    expect(sent("/api/ade/holds/H2/cancel")).toHaveLength(1);
  });

  it("shows a held move's Cancel as taken, its button disabled, and sends it once", async () => {
    const cancel = later<Answer>();
    serve(routes({ "POST /api/ade/holds/H3/cancel": () => cancel.answer }));
    const { HeldMoveCard } = await fresh();
    const m: LinkMove = { seq: null, hold_id: "H3", link_id: "L1", session: "s9", state: "held", created_at: SERVER_TIME - 30,
      expires_at: SERVER_TIME + 600, takes_effect_at: SERVER_TIME + 180, told: 1, reason: null, title: "Fix the parser", link: "mac" };
    render(<HeldMoveCard m={m} />);
    twice("Cancel");
    expect(screen.queryByText(/^Cancelled/)).not.toBeNull();
    expect(disabled("Cancel")).toBe(true);
    await act(async () => { cancel.settle([200, { cancelled_at: SERVER_TIME }]); });
    expect(sent("/api/ade/holds/H3/cancel")).toHaveLength(1);
  });

  it("shows a chat's tool ask answered in Approvals as taken, its buttons disabled, and sends it once", async () => {
    const { api, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const answer = later<unknown>();
    const answered = vi.spyOn(api, "chatAnswer").mockImplementation(() => answer.answer);
    const record = { id: "0123456789ab", repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1,
      finished: null, running: true, exit_code: null, ended_at: null,
      chat: { state: "needs_you", seq: 3, pending: [], cost: 0, chosen: {}, asks: [{ id: "k1", tool: "Bash", summary: "rm -rf build" }] } } as unknown as SessionRecord;
    render(<Approvals records={[record]} requests={[]} onClose={() => {}} onOpen={() => {}} />);
    twice("Deny");
    expect(screen.queryByText(/^Denied/)).not.toBeNull();
    expect([disabled("Deny"), disabled("Allow once")]).toEqual([true, true]);
    expect(answered).toHaveBeenCalledTimes(1);
    expect(answered).toHaveBeenCalledWith("0123456789ab", "k1", "deny");
    await act(async () => { answer.settle({}); });
  });

  it("shows a session an agent asked for refused in Approvals at once, its buttons disabled, and sends it once", async () => {
    const { api, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const refusal = later<unknown>();
    const refused = vi.spyOn(api, "refuseAsk").mockImplementation(() => refusal.answer as never);
    const wanted: SessionAsk = { id: "a1b2c3d4e5f6", from: "0123456789ab", repo: "demo", label: "acme/demo", program: "codex",
      task: "Write the parser's tests.", base: "main", at: 1, by: { id: "0123456789ab", title: "Fix the parser", program: "claude" } };
    render(<Approvals records={[]} requests={[]} sessionAsks={[wanted]} onClose={() => {}} onOpen={() => {}} />);
    twice("Refuse");
    expect(screen.queryByText(/^Refused/)).not.toBeNull();
    expect([disabled("Refuse"), disabled("Start it")]).toEqual([true, true]);
    expect(refused).toHaveBeenCalledTimes(1);
    await act(async () => { refusal.settle({ on: true, asks: [] }); });
  });

  it("closes a session's reported prompt when Close is pressed, before the server answers", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/40/close": () => close.answer }));
    const { mod: { PromptCard } } = await freshly(() => import("./LinkInbox"));
    const p = { seq: 40, time: "2026-11-08T00:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null,
      text: "Proceed with the release?", link_id: "L1", deadline_at: SERVER_TIME + 600, look: false } as Parameters<typeof PromptCard>[0]["p"];
    render(<PromptCard p={p} name="mac" />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText(/Proceed with the release\?/)).toBeNull();
    await act(async () => { close.settle([200, { seq: 41 }]); });
    expect(sent("/api/ade/link/questions/40/close")).toHaveLength(1);
  });

  it("shows a chat's own tool ask answered as taken, its buttons disabled, and sends it once", async () => {
    const { api, store, mod: { ChatPane } } = await freshly(() => import("./ChatPane"));
    const ID = "0123456789ab";
    const answer = later<unknown>();
    const answered = vi.spyOn(api, "chatAnswer").mockImplementation(() => answer.answer);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no route" }), { status: 404 })));
    store.setState({ loadChat: async () => {}, chat: { [ID]: [{ seq: 1, kind: "ask", id: "k1", name: "Bash", summary: "rm -rf build" }] } });
    const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1, finished: null,
      running: true, exit_code: null, ended_at: null, chat: null } as SessionRecord;
    render(<ChatPane record={record} />);
    twice("Deny");
    expect(screen.queryByText(/^Denied/)).not.toBeNull();
    expect([disabled("Deny"), disabled("Allow once")]).toEqual([true, true]);
    expect(answered).toHaveBeenCalledTimes(1);
    await act(async () => { answer.settle({}); });
  });

  it("shows a command's Deny under an app's proposed answer as taken, its buttons disabled, and sends it once", async () => {
    const { api, mod: { ProposedAnswer } } = await freshly(() => import("./Proposals"));
    const answer = later<unknown>();
    const decided = vi.spyOn(api, "decideProposed").mockImplementation(() => answer.answer);
    const q = { id: "req-1", command: "git push origin main", cwd: "/work", state: "pending", created_at: SERVER_TIME } as HookRequest;
    const p = { id: "P1", kind: "approval", state: "proposed", target: "request:req-1", session: null, link_id: null, app: "Claude",
      grant_id: "g1", text: null, decision: "deny", why: "it pushes to main", made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600,
      decided_at: null, seq: null, edited: false, said: null, ref: null, request_id: "req-1" } as Proposal;
    render(<ProposedAnswer q={q} p={p} />);
    twice("Deny");
    expect(screen.queryByText(/^Denied/)).not.toBeNull();
    expect([disabled("Deny"), disabled("Approve once")]).toEqual([true, true]);
    expect(decided).toHaveBeenCalledTimes(1);
    expect(decided).toHaveBeenCalledWith("req-1", false, "P1");
    await act(async () => { answer.settle({}); });
  });
});
