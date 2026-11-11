// The page's quick feedback, press by press: what shows while a press goes, where the server answers late or refuses,
// where its card is drawn again or gone, and what the server says of an item once its press was taken.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type GrantWideningView, type HistorySession, type HookRequest, type LinkAsk, type LinkEvent, type LinkMove,
  type LinkProposal, type LinkView, type Proposal, type SessionAsk, type SessionRecord } from "../api";
import { canonicalJson } from "../passkey";
import { useStore } from "../store";

// the phone app's check of the person (presence.ts), stood in for: in the browser it asks nothing
const presence = vi.hoisted(() => ({ confirmPresence: vi.fn(async () => {}) }));
vi.mock("../presence", () => presence);

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
let calls: Call[] = [];
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
const sent = (path: string, method = "POST") => calls.filter((c) => c.method === method && c.path === path);
function later<T>() {
  let settle!: (v: T) => void;
  let fail!: (e: unknown) => void;
  const answer = new Promise<T>((resolve, reject) => { settle = resolve; fail = reject; });
  return { answer, settle, fail };
}

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
  presence.confirmPresence.mockReset(); presence.confirmPresence.mockImplementation(async () => {});
  useStore.setState(useStore.getInitialState());
});

const SERVER_TIME = 1_790_000_000;
const T = Math.floor(Date.now() / 1000);
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 15, created_at: SERVER_TIME - 3600,
  last_seen_at: SERVER_TIME - 60, last_heartbeat_at: SERVER_TIME - 60, silent: false, acked_seq: 0, checkpoint: null,
  checkpoint_version: 0, checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const routes = (more: Record<string, Route> = {}): Record<string, Route> => ({
  "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }],
  "POST /api/ade/link/commands": () => [200, { seq: 30, expires_at: SERVER_TIME + 3600 }],
  "GET /api/ade/link/sessions": () => [200, { links: [link()], questions: [], public_url: "https://pp.example" }],
  "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }],
  "GET /api/ade/link/history": () => [200, { sessions: [] }],
  ...more,
});
const credentials = {
  get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: {
    clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
    signature: new Uint8Array([3]).buffer, userHandle: new Uint8Array([4]).buffer } })),
};
/** A module as a page loads it, with the api and the store it reads, signed in, passkeys signing. */
const freshly = async <M,>(load: () => Promise<M>) => {
  vi.resetModules();
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  const [{ Api: api }, { useStore: store }, mod] = await Promise.all([import("../api"), import("../store"), load()]);
  store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true }, refresh: async () => {}, refreshApprovals: async () => {} });
  return { api, store, mod };
};
const disabled = (name: string | RegExp, root: HTMLElement = document.body) =>
  (within(root).getByRole("button", { name }) as HTMLButtonElement).disabled;
/** Presses a button twice before the page draws again. */
const twice = (b: HTMLElement) => act(() => { b.click(); b.click(); });
/** Writes `words` in a SignBox labelled `label`, reviews them and waits for the step to sign. */
const reviewed = async (label: string, words: string, review: RegExp = /^Review$/) => {
  fireEvent.change(await screen.findByLabelText(label), { target: { value: words } });
  fireEvent.click(screen.getByRole("button", { name: review }));
  await screen.findByRole("button", { name: /Sign with your passkey/ });
};

describe("the conversation's sending bubble", () => {
  it("is followed to the conversation's end as it shows", async () => {
    const taken = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands": () => taken.answer }));
    const { mod: { Conversation } } = await freshly(() => import("./Channel"));
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const scroller = screen.getByLabelText("The conversation");
    const set = vi.fn();
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, get: () => 5000 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, get: () => 0, set });
    await reviewed("Message mac", "and the docs");
    set.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(scroller.querySelector("[data-sending]")).not.toBeNull());
    expect(set).toHaveBeenCalledWith(5000);
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
  });

  it("goes once the conversation shows what was sent, not after the links are read again", async () => {
    let events: LinkEvent[] = [];
    let holdLinks = false;
    const links = later<Answer>();
    serve(routes({
      "POST /api/ade/link/commands": () => {
        events = [{ seq: 30, time: "2026-11-09T11:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null, text: "and the docs", state: "queued" }];
        holdLinks = true;
        return [200, { seq: 30, expires_at: SERVER_TIME + 3600 }];
      },
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }],
      "GET /api/ade/link/sessions": () => (holdLinks ? links.answer : [200, { links: [link()], questions: [], public_url: "https://pp.example" }]),
    }));
    const { mod: { Conversation } } = await freshly(() => import("./Channel"));
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await reviewed("Message mac", "and the docs");
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    const said = screen.getByLabelText("The conversation");
    await vi.waitFor(() => expect(within(said).queryByText("You signed an instruction")).not.toBeNull());
    expect(said.querySelector("[data-sending]")).toBeNull();            // the links are still being read
    await act(async () => { links.settle([200, { links: [link()], questions: [], public_url: "https://pp.example" }]); });
  });
});

describe("the phone's check of the person, before an Allow shows on its card", () => {
  const ID = "0123456789ab";
  const ANSWER = `/api/ade/workspace/sessions/${ID}/chat/answer`;
  /** The chat's pane, its log read again by `loadChat`: at once, unless the case holds it. */
  const chat = async (loadChat: () => Promise<void> = async () => {}) => {
    const { store, mod: { ChatPane } } = await freshly(() => import("./ChatPane"));
    store.setState({ loadChat, chat: { [ID]: [{ seq: 1, kind: "ask", id: "k1", name: "Bash", summary: "rm -rf build" }] } });
    const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1, finished: null,
      running: true, exit_code: null, ended_at: null, chat: null } as SessionRecord;
    render(<ChatPane record={record} />);
    return store;
  };

  it("changes nothing on the card when the person is not confirmed, and says why as the page's error", async () => {
    serve({ [`POST ${ANSWER}`]: () => [200, {}] });
    presence.confirmPresence.mockRejectedValueOnce(new Error("Not approved: Face ID, Touch ID or the passcode was not given."));
    const store = await chat();
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(screen.queryByText(/^Allowed once/)).toBeNull();
    await vi.waitFor(() => expect(store.getState().error).toBe("Not approved: Face ID, Touch ID or the passcode was not given."));
    expect(sent(ANSWER)).toHaveLength(0);
    expect(disabled("Allow once")).toBe(false);
  });

  it("starts the server's time only once the person is confirmed: a slow passcode is not the server's silence", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve({ [`POST ${ANSWER}`]: () => [200, {}] });
    const face = later<void>();
    presence.confirmPresence.mockImplementationOnce(() => face.answer);
    const reread = later<void>();                                  // the chat's log read again once taken, held
    await chat(() => reread.answer);
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await act(async () => { vi.advanceTimersByTime(25_000); });
    expect(screen.queryByText(/did not answer in/)).toBeNull();
    await act(async () => { face.settle(); });
    await vi.waitFor(() => expect(sent(ANSWER)).toHaveLength(1));
    expect(screen.queryByText(/^Allowed once/)).not.toBeNull();
    await act(async () => { reread.settle(); });
  });
});

describe("Start it, and a press the server answers late", () => {
  const ask = (id: string): SessionAsk => ({ id, from: "0123456789ab", repo: "demo", label: "acme/demo", program: "codex",
    task: `Task ${id}.`, base: "main", at: 1, by: { id: "0123456789ab", title: `Asked ${id}`, program: "claude" } });

  it("waits for a start as long as the server gives one, and opens the session it started", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const start = later<SessionRecord>();
    vi.spyOn(api, "startAsk").mockImplementation(() => start.answer);
    const onOpen = vi.fn();
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask("a1b2c3d4e5f6")]} onClose={() => {}} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await act(async () => { vi.advanceTimersByTime(25_000); });
    expect(screen.queryByText(/did not answer in/)).toBeNull();
    expect(screen.queryByText(/^Starting/)).not.toBeNull();
    await act(async () => { start.settle({ id: "fedcba987654" } as SessionRecord); });
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("fedcba987654"));
  });

  it("starts one ask at a time: while one starts, every ask's buttons wait", async () => {
    const { api, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const start = later<SessionRecord>();
    vi.spyOn(api, "startAsk").mockImplementation(() => start.answer);
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask("a1b2c3d4e5f6"), ask("b2c3d4e5f6a1")]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Start it" })[0]);
    const [, second] = screen.getAllByRole("button", { name: "Start it" }) as HTMLButtonElement[];
    const [, refuseSecond] = screen.getAllByRole("button", { name: "Refuse" }) as HTMLButtonElement[];
    expect([second.disabled, refuseSecond.disabled]).toEqual([true, true]);
    await act(async () => { start.settle({ id: "fedcba987654" } as SessionRecord); });
  });

  it("holds a second press while the first goes after no answer in time, and says the late answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/9/close": () => close.answer }));
    const { store, mod: { LinkCard } } = await freshly(() => import("./Channel"));
    const reread = later<void>();                                  // the links read again once taken, held
    store.setState({ refreshLinks: () => reread.answer });
    const waiting: LinkEvent = { seq: 9, time: "2026-11-08T00:00:00Z", kind: "waiting", from: "mac", to: "owner", ref: null, text: "Waiting for your word." };
    render(<LinkCard l={link()} questions={[]} waitings={[waiting]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await act(async () => { vi.advanceTimersByTime(20_000); });
    expect(screen.queryByText(/did not answer in 20 seconds/)).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1);
    expect(screen.queryByText(/still on its way/)).not.toBeNull();
    await act(async () => { close.settle([200, { seq: 10 }]); });
    await vi.waitFor(() => expect(screen.queryByText("Waiting for your word.")).toBeNull());
    await act(async () => { reread.settle(); });
  });
});

describe("a press whose card is not there when the server answers", () => {
  const held = { id: "r1", command: "git push origin main", state: "pending", created_at: SERVER_TIME - 30, until: SERVER_TIME + 600,
    token_name: "Mac", session: "s1", cwd: "/w" } as HookRequest;

  it("says a refusal that comes once its card has gone as the page's error", async () => {
    const { api, store, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const answer = later<unknown>();
    vi.spyOn(api, "decide").mockImplementation(() => answer.answer);
    const { unmount } = render(<Approvals records={[]} requests={[held]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    unmount();                                                     // Approvals closed while the Deny goes
    await act(async () => { answer.fail(new ApiError(409, "That command was answered on another device.")); });
    await vi.waitFor(() => expect(store.getState().error).toBe("That command was answered on another device."));
  });

  it("sends a press once across its card being drawn again while it goes", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/12/close": () => close.answer }));
    const { mod: { Question } } = await freshly(() => import("./Channel"));
    const q: LinkEvent = { seq: 12, time: "2026-11-08T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: "Which cap?" };
    const first = render(<Question q={q} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    first.unmount();                                               // Open conversation, then back
    render(<Question q={q} linkId="L1" />);
    const again = screen.queryByRole("button", { name: "Dismiss question" });
    if (again) fireEvent.click(again);
    expect(sent("/api/ade/link/questions/12/close")).toHaveLength(1);
    await act(async () => { close.settle([200, { seq: 13 }]); });
  });
});

describe("every decision button of a card waits while its press goes", () => {
  it("disables Approve and replace while a Refuse goes", async () => {
    const refuse = later<Answer>();
    serve(routes({ "POST /api/ade/link/asks/A1/refuse": () => refuse.answer }));
    const { mod: { AskCard } } = await freshly(() => import("./Channel"));
    const a: LinkAsk = { id: "A1", name: "cloud-2", kind: "cloud", fingerprint: "ab".repeat(16), asked_from: "203.0.113.9",
      created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600, session_ref: null, state: "pending", hold_id: null,
      takes_effect_at: null, told: null, failed: null,
      replaces: { id: "L9", name: "cloud-2", created_at: SERVER_TIME - 7200, last_seen_at: SERVER_TIME - 600, fingerprint: "cd".repeat(16), revoked_at: null } };
    render(<AskCard a={a} links={[]} twins={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    expect(disabled(/Approve and replace/)).toBe(true);
    await act(async () => { refuse.settle([200, { refused: "A1" }]); });
  });

  it("disables the proposal's own Refuse while a Deny under it goes", async () => {
    const { api, mod: { ProposedAnswer } } = await freshly(() => import("./Proposals"));
    const answer = later<unknown>();
    vi.spyOn(api, "decideProposed").mockImplementation(() => answer.answer);
    const q = { id: "req-1", command: "git push origin main", cwd: "/work", state: "pending", created_at: SERVER_TIME } as HookRequest;
    const p = { id: "P1", kind: "approval", state: "proposed", target: "request:req-1", session: null, link_id: null, app: "Claude",
      grant_id: "g1", text: null, decision: "deny", why: "it pushes to main", made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600,
      decided_at: null, seq: null, edited: false, said: null, ref: null, request_id: "req-1" } as Proposal;
    render(<ProposedAnswer q={q} p={p} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(disabled("Refuse")).toBe(true);
    await act(async () => { answer.settle({}); });
  });
});

describe("what the server says of an item once its press was taken", () => {
  it("keeps a withdrawn command on its list, said withdrawn at once, then as the server says", async () => {
    const withdraw = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands/21/withdraw": () => withdraw.answer }));
    const { mod: { Commands } } = await freshly(() => import("./Channel"));
    const c: LinkEvent = { seq: 21, time: "2026-11-08T00:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null,
      text: "run the release checks", state: "queued" };
    const { rerender } = render(<Commands l={link({ commands: [c] })} />);
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(screen.queryByText(/run the release checks/)).not.toBeNull();
    expect(screen.queryByText(/^Withdrawn/)).not.toBeNull();
    await act(async () => { withdraw.settle([200, { withdrawn_at: SERVER_TIME }]); });
    rerender(<Commands l={link({ commands: [{ ...c, state: "withdrawn" }] })} />);
    expect(screen.queryByText(/run the release checks/)).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
  });

  it("shows a prompt closed in its conversation as closed, once the server says so", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/40/close": () => close.answer }));
    const { mod: { PromptCard } } = await freshly(() => import("./LinkInbox"));
    const p = { seq: 40, time: "2026-11-08T00:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null,
      text: "Proceed with the release?", link_id: "L1", deadline_at: SERVER_TIME + 600, look: false } as Parameters<typeof PromptCard>[0]["p"];
    const { rerender } = render(<PromptCard p={p} name="mac" live />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText(/Proceed with the release\?/)).toBeNull();
    await act(async () => { close.settle([200, { seq: 41 }]); });
    rerender(<PromptCard p={p} name="mac" live={false} />);
    expect(screen.queryByText(/Proceed with the release\?/)).not.toBeNull();
  });

  it("shows a proposal closed in its conversation as closed, once the server says so", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/50/close": () => close.answer }));
    const { mod: { ProposalCard } } = await freshly(() => import("./LinkInbox"));
    const p: LinkProposal = { seq: 50, time: "2026-11-08T00:00:00Z", kind: "proposal", from: "mac", to: "owner", ref: null,
      text: "Run the release checks on cloud-2.", link_id: "L1", for_id: "L2", for: "cloud-2", deadline_at: SERVER_TIME + 600 };
    const { rerender } = render(<ProposalCard p={p} live />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText(/Run the release checks on cloud-2\./)).toBeNull();
    await act(async () => { close.settle([200, { seq: 51 }]); });
    rerender(<ProposalCard p={p} live={false} />);
    expect(screen.queryByText(/Run the release checks on cloud-2\./)).not.toBeNull();
  });
});

describe("the page's other dismissals and decisions act at once", () => {
  it("shows a grant's Refuse taken at once, its button disabled", async () => {
    const { api, mod: { GrantWidening } } = await freshly(() => import("./TaskGrant"));
    const refusal = later<unknown>();
    vi.spyOn(api, "refuseGrantWidening").mockImplementation(() => refusal.answer as never);
    const w = { id: "W1", task_id: "T1", task_title: "Fix the parser", requester: { kind: "agent", id: "A1", name: "Triage" },
      base_version: 1, base_digest: "sha256:" + "0".repeat(64), ask: "push to release",
      adds: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
      scope: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
      state: "waiting", created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 86400, decided_at: null, version: null } as unknown as GrantWideningView;
    render(<GrantWidening w={w} onChanged={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    expect(screen.queryByText(/^Refused/)).not.toBeNull();
    expect(disabled("Refuse")).toBe(true);
    await act(async () => { refusal.settle({}); });
  });

  it("shows a proposal's Refuse it taken at once, and back with its words and the reason on a refusal", async () => {
    const { api, mod: { RefuseIt } } = await freshly(() => import("./Proposals"));
    const refusal = later<unknown>();
    vi.spyOn(api, "refuseProposal").mockImplementation(() => refusal.answer as never);
    const p = { id: "P2", kind: "instruction", state: "proposed", target: "link:L1", session: "mac", link_id: "L1", app: "Claude",
      grant_id: "g1", text: "run the tests", decision: null, why: null, made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600,
      decided_at: null, seq: null, edited: false, said: null, ref: null, request_id: null } as Proposal;
    render(<RefuseIt p={p} onDone={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    fireEvent.change(screen.getByLabelText(/Words for the app/), { target: { value: "not today" } });
    fireEvent.click(screen.getByRole("button", { name: /Refuse it/ }));
    expect(screen.queryByText(/^Refused/)).not.toBeNull();
    await act(async () => { refusal.fail(new ApiError(409, "That proposal was answered already.")); });
    await vi.waitFor(() => expect(screen.queryByText("That proposal was answered already.")).not.toBeNull());
    expect((screen.getByLabelText(/Words for the app/) as HTMLInputElement).value).toBe("not today");
  });

  describe("a session's move bar", () => {
    const session = (over: Partial<HistorySession> = {}): HistorySession => ({
      id: "s1", title: "Make sign-in work", project: "~/code/demo", branch: "sign-in", started_at: T - 7200, last_at: T - 3600,
      link_id: "L1", link: "mac", kind: "local", listed_at: T - 60, ...over,
    });
    const bar = async (move: LinkMove, more: Record<string, Route>) => {
      serve(routes({ "GET /api/ade/link/moves?session=s1": () => [200, { moves: [move] }], ...more }));
      const { store, mod: { MoveToWorkspace } } = await freshly(() => import("./Channel"));
      store.setState({ ws: { enabled: true, allowed: true, running: true } as never, repos: [{ name: "demo" }] as never });
      render(<MoveToWorkspace s={session()} onOpen={() => {}} />);
    };

    it("shows a held move's Cancel taken at once, its button disabled", async () => {
      const cancel = later<Answer>();
      await bar({ seq: null, hold_id: "H1", link_id: "L1", session: "s1", state: "held", created_at: T, expires_at: T + 3600,
        takes_effect_at: T + 120, told: 0, reason: null }, { "POST /api/ade/holds/H1/cancel": () => cancel.answer });
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
      expect(screen.queryByText(/^Cancelled/)).not.toBeNull();
      expect(disabled("Cancel")).toBe(true);
      await act(async () => { cancel.settle([200, { cancelled_at: T }]); });
    });

    it("shows an asked move's Withdraw taken at once, its button disabled", async () => {
      const withdraw = later<Answer>();
      await bar({ seq: 31, link_id: "L1", session: "s1", state: "asked", created_at: T - 60, expires_at: T + 3000, reason: null },
        { "POST /api/ade/link/commands/31/withdraw": () => withdraw.answer });
      fireEvent.click(await screen.findByRole("button", { name: "Withdraw" }));
      expect(screen.queryByText(/^Withdrawn/)).not.toBeNull();
      expect(disabled("Withdraw")).toBe(true);
      await act(async () => { withdraw.settle([200, { withdrawn_at: T }]); });
    });
  });

  it("shows a link's Stop it taken at once, and sends it once", async () => {
    const stop = later<Answer>();
    serve(routes({ "POST /api/ade/link/links/L1/stop": () => stop.answer }));
    const { mod: { LinkCard } } = await freshly(() => import("./Channel"));
    render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "More for mac" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Stop it/ }));
    twice(screen.getByRole("button", { name: /^Stop it$/ }));
    expect(screen.queryByText(/^Stopping/)).not.toBeNull();
    expect(sent("/api/ade/link/links/L1/stop")).toHaveLength(1);
    await act(async () => { stop.settle([200, { stopped_at: SERVER_TIME }]); });
  });

  it("shows an app's proposed stop, once confirmed, taken at once", async () => {
    const { api, mod: { Proposals } } = await freshly(() => import("./Proposals"));
    const accepted = later<unknown>();
    vi.spyOn(api, "acceptStop").mockImplementation(() => accepted.answer as never);
    const p = { id: "P3", kind: "stop", state: "proposed", target: "link:L1", session: "mac", link_id: "L1", app: "Claude", grant_id: "g1",
      text: null, decision: null, why: "it is stuck", made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600, decided_at: null,
      seq: null, edited: false, said: null, ref: null, request_id: null } as Proposal;
    render(<Proposals proposals={[p]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /Stop mac/ }));
    fireEvent.click(screen.getByRole("button", { name: /Click again to stop mac/ }));
    expect(screen.queryByText(/^Stopped/)).not.toBeNull();
    await act(async () => { accepted.settle({}); });
  });
});

describe("the chat's own message box", () => {
  const ID = "0123456789ab";
  const SEND = `/api/ade/workspace/sessions/${ID}/chat`;
  const chat = async (answer: Route) => {
    serve({ [`POST ${SEND}`]: answer });
    const { store, mod: { ChatPane } } = await freshly(() => import("./ChatPane"));
    store.setState({ loadChat: async () => {}, chat: { [ID]: [] } });
    const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1, finished: null,
      running: true, exit_code: null, ended_at: null, chat: { state: "idle", seq: 0, pending: [], cost: 0, chosen: {}, asks: [] } } as unknown as SessionRecord;
    const view = render(<ChatPane record={record} />);
    fireEvent.change(screen.getByLabelText("Message to Claude"), { target: { value: "run the tests" } });
    return view;
  };

  it("shows a message at once in the chat as sending, its send disabled, and sends it once", async () => {
    const taken = later<Answer>();
    const { container } = await chat(() => taken.answer);
    twice(screen.getByRole("button", { name: /Send/ }));
    const bubble = container.querySelector("[data-sending]") as HTMLElement | null;
    expect(bubble?.textContent ?? "").toContain("run the tests");
    expect(bubble?.querySelector(".animate-spin")).not.toBeNull();
    expect(disabled(/Send/)).toBe(true);
    expect(sent(SEND)).toHaveLength(1);
    await act(async () => { taken.settle([200, {}]); });
  });

  it("keeps a refused message in the box, with the reason under it", async () => {
    await chat(() => [409, { detail: "The chat has ended: start a new one." }]);
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await vi.waitFor(() => expect(screen.queryByText("The chat has ended: start a new one.")).not.toBeNull());
    expect((screen.getByLabelText("Message to Claude") as HTMLTextAreaElement).value).toBe("run the tests");
  });
});

describe("an answer shows as sending wherever it is signed", () => {
  const question: LinkEvent = { seq: 12, time: "2026-11-08T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: "Which cap?" };

  it("on a link's card, beside its question", async () => {
    const taken = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands": () => taken.answer }));
    const { mod: { LinkCard } } = await freshly(() => import("./Channel"));
    const { container } = render(<LinkCard l={link()} questions={[question]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
    await reviewed("Your answer", "per org", /Review the answer/);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(container.querySelector("[data-sending]")?.textContent ?? "").toContain("per org"));
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
  });

  it("from a prompt's card, to the place the card is in", async () => {
    const taken = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands": () => taken.answer }));
    const { mod: { PromptCard } } = await freshly(() => import("./LinkInbox"));
    // a tool's prompt, as a session's client sends one: what makes it answerable here
    const p = { seq: 40, time: "2026-11-08T00:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null,
      text: JSON.stringify({ tool: "Bash", input: { command: "git push" } }), link_id: "L1", deadline_at: SERVER_TIME + 600,
      look: false } as Parameters<typeof PromptCard>[0]["p"];
    const sending = vi.fn();
    render(<PromptCard p={p} name="mac" {...({ onSending: sending } as object)} />);
    fireEvent.click(await screen.findByRole("button", { name: /Answer in your own words/ }));
    await reviewed("Your words", "go ahead", /Review/);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sending).toHaveBeenCalledWith("go ahead"));
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
  });
});

describe("Sign on my Mac, while the signed step is on its way", () => {
  it("puts the Mac's request away and says Sending", async () => {
    const taken = later<Answer>();
    const credential = { id: "AQ", type: "public-key", response: { clientDataJSON: "x", authenticatorData: "y", signature: "z" } };
    let parked: unknown = null;                                    // the relay hands back what the page parked
    serve(routes({
      "GET /api/ade/signers": () => [200, { signers: [{ id: "s1", name: "MacBook", created_at: 1, last_seen_at: null }] }],
      "POST /api/ade/sign-requests": (b) => { parked = b?.payload; return [200, { id: "r1", code: "AAAA-AAAA", expires_at: Math.floor(Date.now() / 1000) + 290 }]; },
      "GET /api/ade/sign-requests/r1": () => [200, { id: "r1", state: "signed", expires_at: SERVER_TIME + 300, credential, signer: "MacBook",
        payload: canonicalJson(parked) }],
      "DELETE /api/ade/sign-requests/r1": () => [200, { cancelled: 1 }],
      "POST /api/ade/link/commands": () => taken.answer,
    }));
    const { mod: { SignBox } } = await freshly(() => import("./Channel"));
    render(<SignBox purpose="instruction" to="L1" toName="mac" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    await reviewed("What mac should do", "run the tests");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await screen.findByLabelText("Request code");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    expect(screen.queryByLabelText("Request code")).toBeNull();
    expect(screen.queryByText(/^Sending/)).not.toBeNull();
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
  });
});

describe("Start it's words, and a refused Start", () => {
  const ask: SessionAsk = { id: "a1b2c3d4e5f6", from: "0123456789ab", repo: "demo", label: "acme/demo", program: "codex",
    task: "Write the parser's tests.", base: "main", at: 1, by: { id: "0123456789ab", title: "Fix the parser", program: "claude" } };

  it("reads the approvals again after a refused Start", async () => {
    const { api, store, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    vi.spyOn(api, "startAsk").mockRejectedValue(new ApiError(404, "That ask has been answered, or is gone."));
    const reread = vi.fn(async () => {});
    store.setState({ refreshApprovals: reread });
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await vi.waitFor(() => expect(screen.queryByText("That ask has been answered, or is gone.")).not.toBeNull());
    await vi.waitFor(() => expect(reread).toHaveBeenCalled());
  });

  it("says Started once the server started it, while the approvals are read again", async () => {
    const { api, store, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    vi.spyOn(api, "startAsk").mockResolvedValue({ id: "fedcba987654" } as SessionRecord);
    const reread = later<void>();                                  // the approvals read again once taken, held
    store.setState({ refreshApprovals: () => reread.answer });
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    await vi.waitFor(() => expect(screen.queryByText(/^Started/)).not.toBeNull());
    await act(async () => { reread.settle(); });
  });

  it("says an instruction signed on a link's card as the list beside it says one", async () => {
    const taken = later<Answer>();
    serve(routes({ "POST /api/ade/link/commands": () => taken.answer }));
    const { mod: { LinkCard } } = await freshly(() => import("./Channel"));
    const { container } = render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Instruct/ }));
    await reviewed("What mac should do", "run the tests");
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(container.querySelector("[data-sending]")?.textContent ?? "").toContain("Your instruction:"));
    await act(async () => { taken.settle([200, { seq: 30, expires_at: SERVER_TIME + 3600 }]); });
  });
});

// A press is the page's, by its item, until the server refuses it or, once the server took it,
// until its list has been read again; from then its card is as the list says.
describe("a press is the page's until its list is read again, and its card is then as the list says", () => {
  const ID = "0123456789ab";
  const record = { id: ID, repo: "app", program: "chat", task: "a task", base: "abc", branch: null, created_at: 1, finished: null,
    running: true, exit_code: null, ended_at: null, chat: null } as SessionRecord;
  const proposal = (over: Partial<Proposal>): Proposal => ({ id: "P0", kind: "instruction", state: "proposed", target: "link:L1",
    session: "mac", link_id: "L1", app: "Claude", grant_id: "g1", text: "run the tests", decision: null, why: null,
    made_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 600, decided_at: null, seq: null, edited: false, said: null, ref: null,
    request_id: null, ...over }) as Proposal;

  it("lets a chat's tool ask be answered again once its log, read again after the answer was taken, still asks it", async () => {
    serve({ [`POST /api/ade/workspace/sessions/${ID}/chat/answer`]: () => [200, {}] });
    const { store, mod: { ChatPane } } = await freshly(() => import("./ChatPane"));
    const reread = later<void>();
    store.setState({ loadChat: () => reread.answer, chat: { [ID]: [{ seq: 1, kind: "ask", id: "k1", name: "Bash", summary: "rm -rf build" }] } });
    render(<ChatPane record={record} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await vi.waitFor(() => expect(screen.queryByText(/^Denied$/)).not.toBeNull());   // taken, its log being read again
    expect(disabled("Allow once")).toBe(true);
    await act(async () => { reread.settle(); });                  // the log still asks it: the chat ended first
    expect(screen.queryByText(/^Denied/)).toBeNull();
    expect(disabled("Allow once")).toBe(false);
  });

  it("keeps a dismissed question closed when drawn again after the server took it, until the links are read again", async () => {
    const close = later<Answer>();
    serve(routes({ "POST /api/ade/link/questions/12/close": () => close.answer }));
    const { store, mod: { Question } } = await freshly(() => import("./Channel"));
    const reread = later<void>();
    store.setState({ refreshLinks: () => reread.answer });
    const q: LinkEvent = { seq: 12, time: "2026-11-08T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: "Which cap?" };
    const first = render(<Question q={q} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    await act(async () => { close.settle([200, { seq: 13 }]); });
    first.unmount();                                               // Open conversation, then back
    render(<Question q={q} linkId="L1" />);
    expect(screen.queryByText("Which cap?")).toBeNull();
    await act(async () => { reread.settle(); });
  });

  it("says Stopped in a link's stop box until the links are read again", async () => {
    serve(routes({ "POST /api/ade/link/links/L1/stop": () => [200, { stopped_at: SERVER_TIME }] }));
    const { store, mod: { LinkCard } } = await freshly(() => import("./Channel"));
    const reread = later<void>();
    store.setState({ refreshLinks: () => reread.answer });
    render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "More for mac" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Stop it/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Stop it$/ }));
    await vi.waitFor(() => expect(screen.queryByText(/^Stopped$/)).not.toBeNull());
    expect(disabled(/^Stop it$/)).toBe(true);
    await act(async () => { reread.settle(); });
    expect(screen.queryByRole("button", { name: /^Stop it$/ })).toBeNull();
  });

  it("opens a link's stop box as it was when it is stopped again after a resume", async () => {
    serve(routes({ "POST /api/ade/link/links/L1/stop": () => [200, { stopped_at: SERVER_TIME }] }));
    const { store, mod: { LinkCard } } = await freshly(() => import("./Channel"));
    store.setState({ refreshLinks: async () => {} });
    const { rerender } = render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "More for mac" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Stop it/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Stop it$/ }));
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: /^Stop it$/ })).toBeNull());   // taken, read again
    rerender(<LinkCard l={link({ stopped_at: SERVER_TIME })} questions={[]} onTicket={() => {}} />);
    rerender(<LinkCard l={link({ stopped_at: null })} questions={[]} onTicket={() => {}} />);          // resumed since
    fireEvent.click(screen.getByRole("button", { name: "More for mac" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Stop it/ }));
    expect(disabled(/^Stop it$/)).toBe(false);
    expect(screen.queryByText(/^Stopped/)).toBeNull();
  });

  it("shows an app's Refuse all taken at once, and offers it again for what the app proposes after", async () => {
    const { api, mod: { Proposals } } = await freshly(() => import("./Proposals"));
    vi.spyOn(api, "refuseAllProposals").mockResolvedValue({} as never);
    const { rerender } = render(<Proposals proposals={[proposal({ id: "P4" }), proposal({ id: "P5" })]} requests={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse all" }));
    fireEvent.click(screen.getByRole("button", { name: /Click again to refuse all 2/ }));
    expect(screen.queryByText(/^Refused all/)).not.toBeNull();
    await vi.waitFor(() => expect(screen.queryByText(/^Refused all/)).toBeNull());   // taken, and read again
    rerender(<Proposals proposals={[]} requests={[]} />);
    rerender(<Proposals proposals={[proposal({ id: "P6" }), proposal({ id: "P7" })]} requests={[]} />);
    expect(disabled("Refuse all")).toBe(false);
    expect(screen.queryByText(/^Refused all/)).toBeNull();
  });

  it("disables Approve once and Deny while the proposal's own Refuse goes", async () => {
    const { api, mod: { ProposedAnswer } } = await freshly(() => import("./Proposals"));
    const refusal = later<unknown>();
    vi.spyOn(api, "refuseProposal").mockImplementation(() => refusal.answer as never);
    const q = { id: "req-1", command: "git push origin main", cwd: "/work", state: "pending", created_at: SERVER_TIME } as HookRequest;
    render(<ProposedAnswer q={q} p={proposal({ id: "P1", kind: "approval", target: "request:req-1", session: null, link_id: null,
      text: null, decision: "deny", why: "it pushes to main", request_id: "req-1" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    fireEvent.click(screen.getByRole("button", { name: /Refuse it/ }));
    expect([disabled("Approve once"), disabled("Deny")]).toEqual([true, true]);
    await act(async () => { refusal.settle({}); });
  });

  it("waits an app proposal's other buttons while its stop or its Refuse goes", async () => {
    const { api, mod: { Proposals } } = await freshly(() => import("./Proposals"));
    const accepted = later<unknown>();
    vi.spyOn(api, "acceptStop").mockImplementation(() => accepted.answer as never);
    const refusal = later<unknown>();
    vi.spyOn(api, "refuseProposal").mockImplementation(() => refusal.answer as never);
    render(<Proposals proposals={[proposal({ id: "P3", kind: "stop", text: null, why: "it is stuck" }), proposal({ id: "P8" })]} requests={[]} />);
    const stopCard = screen.getByLabelText("A stop of mac");
    fireEvent.click(within(stopCard).getByRole("button", { name: /Stop mac/ }));
    fireEvent.click(within(stopCard).getByRole("button", { name: /Click again to stop mac/ }));
    expect(disabled("Refuse", stopCard)).toBe(true);
    const instruction = screen.getByLabelText("An instruction for mac");
    fireEvent.click(within(instruction).getByRole("button", { name: "Refuse" }));
    fireEvent.click(within(instruction).getByRole("button", { name: /Refuse it/ }));
    expect(disabled(/Review and sign/, instruction)).toBe(true);
    await act(async () => { accepted.settle({}); refusal.settle({}); });
  });

  it("puts a grant's signing step away while its Refuse goes, and brings it back with the reason on a refusal", async () => {
    const { api, mod: { GrantWidening } } = await freshly(() => import("./TaskGrant"));
    const refusal = later<unknown>();
    vi.spyOn(api, "refuseGrantWidening").mockImplementation(() => refusal.answer as never);
    const w = { id: "W2", task_id: "T1", task_title: "Fix the parser", requester: { kind: "agent", id: "A1", name: "Triage" },
      base_version: 1, base_digest: "sha256:" + "0".repeat(64), ask: "push to release",
      adds: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
      scope: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
      state: "waiting", created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 86400, decided_at: null, version: null } as unknown as GrantWideningView;
    render(<GrantWidening w={w} onChanged={async () => {}} />);
    expect(screen.queryByRole("button", { name: /Review and sign/ })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Refuse" }));
    expect(screen.queryByRole("button", { name: /Review and sign/ })).toBeNull();
    await act(async () => { refusal.fail(new ApiError(409, "That ask was answered already.")); });
    await vi.waitFor(() => expect(screen.queryByText("That ask was answered already.")).not.toBeNull());
    expect(screen.queryByRole("button", { name: /Review and sign/ })).not.toBeNull();
    expect(disabled("Refuse")).toBe(false);
  });

  it("says a Deny under an app's proposed answer once on its command's card", async () => {
    const { api, mod: { Approvals } } = await freshly(() => import("./Approvals"));
    const answer = later<unknown>();
    vi.spyOn(api, "decideProposed").mockImplementation(() => answer.answer);
    const q = { id: "req-1", command: "git push origin main", state: "pending", created_at: SERVER_TIME - 30, until: SERVER_TIME + 600,
      token_name: "Mac", session: "s1", cwd: "/w" } as HookRequest;
    const p = proposal({ id: "P1", kind: "approval", target: "request:req-1", session: null, link_id: null, text: null,
      decision: "deny", why: "it pushes to main", request_id: "req-1" });
    render(<Approvals records={[]} requests={[q]} proposals={[p]} onClose={() => {}} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(screen.getAllByText(/^Denied/)).toHaveLength(1);
    await act(async () => { answer.settle({}); });
  });
});
