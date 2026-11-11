import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, api, ApiError, fetchBlob, mayOfferSignOut, NO_PASSWORD, OLD_SIGN_IN, setToken, token, whenSignedOut,
  type AccountsView, type ChatEvent, type ClaudeStatus, type GithubStatus, type HookRequest, type SessionRecord,
  type User } from "./api";
import { SignIn } from "./components/SignIn";
import { link, machineSession } from "./shellTestkit";
import { useStore } from "./store";

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); useStore.setState(useStore.getInitialState()); });

type St = ReturnType<typeof useStore.getState>;
const A: User = { id: "uA", email: "alice@example.org", can_sign_in_again: true };
const B: User = { id: "uB", email: "bob@example.org", can_sign_in_again: true };
const ID = "0123456789ab";
const record = (over: Partial<SessionRecord> = {}) => ({ id: ID, repo: "alice-payroll", program: "chat", task: "Fix Alice's payroll",
  base: "abc", branch: null, created_at: 1, finished: null, running: true, exit_code: null, ended_at: null, chat: null, ...over }) as SessionRecord;
const accounts = (): AccountsView => ({
  claude: { active: "default", accounts: [{ id: "default", name: "Default", created_at: null, sessions: 1,
    status: { installed: true, signed_in: true, email: "alice@claude.example" }, usage: { at: 1, five_hour: { used_percentage: 50, resets_at: null } } }] },
  codex: { active: "default", accounts: [] },
});

/** Everything an account's page holds once it has been used, each of the account's own: the next to sign in on the
 *  page must see none of it. */
const held = (): Partial<St> => ({
  ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true },
  repos: [{ name: "alice-payroll" }],
  records: [record()],
  signIns: [record({ id: "888888888888", program: "claude-sign-in", repo: null })],
  claude: { installed: true, signed_in: true, email: "alice@claude.example" } as ClaudeStatus,
  github: { installed: true, user: "alice", repos: [], capped: false } as GithubStatus,
  quotas: { claude: { at: 1, five_hour: { used_percentage: 50, resets_at: null } } },
  accounts: accounts(),
  programs: { claude: true, codex: true },
  held: "held",
  modelProviders: [{ id: "kimi", label: "Kimi K3", vendor: "Moonshot", docs: "https://example.test", model: "k3", set: true }],
  customAgents: [{ id: "a1b2c3d4e5f6", name: "Alice's reviewer", command: "aider --model sonnet", task: "typed" }],
  caps: { claude_version: "2.1.300", model_aliases: ["opus"], efforts: ["low"], permission_modes: ["default"] },
  approvals: [{ id: "q1", command: "git push", state: "pending", created_at: 1 } as HookRequest],
  proposals: [{ id: "p1" } as never],
  proposalsOff: true,
  sessionAsks: [{ id: "k1" } as never],
  asksOn: true,
  links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] },
  history: [machineSession()],
  agentsWaiting: 2,
  summary: { stamps: {} } as never,
  summaryOff: true,
  read: { links: "stamp" },
  offline: true,
  lastRead: 5,
  chat: { [ID]: [{ seq: 1, kind: "text", text: "Alice's plan" }] },
  live: { [ID]: { blocks: [{ block: 0, text: "Alice's", lost: false }], thinking: false, tool: null } },
  fileFor: { [ID]: { path: "plans/alice.md" } },
  notice: { text: "Verified: Fix Alice's payroll", tone: "done" },
  error: "No such link.",
  selected: ID,
  panes: [ID],
});

/** The page as account A left it, every field the test then checks first shown to hold something of A's. */
function asA() {
  localStorage.setItem("pp-new-session-draft", "Fix the invoice totals");
  useStore.setState({ user: A, ...held() });
  const init = useStore.getInitialState();
  for (const k of Object.keys(held()) as (keyof St)[]) expect(useStore.getState()[k], k).not.toEqual(init[k]);
}

function nothingOfA() {
  const init = useStore.getInitialState();
  for (const k of Object.keys(held()) as (keyof St)[]) expect(useStore.getState()[k], k).toEqual(init[k]);
  expect(localStorage.getItem("pp-new-session-draft")).toBeNull();
}

describe("signing out", () => {
  it("is offered everywhere but the desktop app's window onto this Mac, which signs itself in", () => {
    // a browser, here: even signed in as this Mac's own account (no password), it may end its sign-in on a device
    // it no longer trusts
    expect(mayOfferSignOut()).toBe(true);
  });

  it("of an account with no password says on the sign-in screen how it signs in again", () => {
    useStore.setState({ user: { id: "u", email: "you@this.desktop", can_sign_in_again: false } });
    useStore.getState().signOut();
    expect(localStorage.getItem(NO_PASSWORD)).toBe("1");
    render(<SignIn />);
    expect(screen.getByRole("status").textContent).toMatch(/this Mac's own, which has no password: sign in again with Sign in with another device/);
  });

  it("of an account with a password says nothing more", () => {
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    useStore.getState().signOut();
    expect(localStorage.getItem(NO_PASSWORD)).toBeNull();
    render(<SignIn />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("leaves no unsent task behind for the next account on this browser", () => {
    localStorage.setItem("pp-new-session-draft", "Fix the invoice totals");
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    useStore.getState().signOut();
    expect(localStorage.getItem("pp-new-session-draft")).toBeNull();
  });
});

describe("what one account's page held", () => {
  it("is all cleared when it signs out: its workspace's programs, providers, agents and chat choices too", () => {
    asA();
    useStore.getState().signOut();
    expect(useStore.getState().user).toBeNull();
    nothingOfA();
  });

  it("is all cleared when another account signs in on the page in its place, which keeps the new one's token", () => {
    asA();
    useStore.getState().signedIn("token-of-b.sig", B);
    expect(useStore.getState().user).toEqual(B);
    expect(token()).toBe("token-of-b.sig");
    nothingOfA();
  });

  it("stays when the same account signs in again: nothing of another's is there to clear", () => {
    asA();
    useStore.getState().signedIn("token-of-a.sig", { ...A });
    expect(useStore.getState().records).toEqual([record()]);
    expect(useStore.getState().customAgents).toEqual(held().customAgents);
    expect(useStore.getState().error).toBeNull();            // a sign-in clears what was said, whoever it is
  });
});

// Each read of the page's store, answered after the account it was asked for signed out or another signed in: what it
// brings back is that account's, and is dropped. Answered while the same account is signed in, it is
// kept, which shows each case is one the store does keep.
const reads: { what: string; api: string; answer: unknown; run: () => Promise<void>; field: (s: St) => unknown; setup?: () => void }[] = [
  { what: "its usage windows, from a workspace before accounts, read session by session", api: "usage",
    answer: { available: true, status: { at: 1, five_hour: { used_percentage: 50, resets_at: null } } },
    setup: () => {
      vi.spyOn(Api, "accounts").mockRejectedValue(new Error("no accounts here"));
      useStore.setState({ records: [record({ program: "claude" })] });
    },
    run: () => useStore.getState().refreshQuota(), field: (s) => s.quotas },
  { what: "what its Claude Code offers a chat", api: "capabilities",
    answer: { claude_version: "2.1.300", model_aliases: ["opus"], efforts: ["low"], permission_modes: ["default"] },
    run: () => useStore.getState().refreshCaps(), field: (s) => s.caps },
  { what: "its workspace's programs", api: "programs", answer: { programs: { claude: true } },
    run: () => useStore.getState().refreshPrograms(), field: (s) => s.programs },
  { what: "its model providers", api: "modelProviders", answer: { providers: held().modelProviders },
    run: () => useStore.getState().refreshModelProviders(), field: (s) => s.modelProviders },
  { what: "its own agents", api: "customAgents", answer: { agents: held().customAgents },
    run: () => useStore.getState().refreshCustomAgents(), field: (s) => s.customAgents },
  { what: "its Claude sign-in", api: "claude", answer: held().claude,
    run: () => useStore.getState().refreshClaude(), field: (s) => s.claude },
  { what: "whether its sessions hold their gated commands", api: "programs", answer: { programs: {}, held: "held" },
    run: () => useStore.getState().refreshPrograms(), field: (s) => s.held },
  { what: "its GitHub sign-in", api: "github", answer: held().github,
    run: () => useStore.getState().refreshGithub(), field: (s) => s.github },
  { what: "its programs' accounts", api: "accounts", answer: accounts(),
    run: () => useStore.getState().refreshAccounts(), field: (s) => s.accounts },
  { what: "its usage windows", api: "accounts", answer: accounts(),
    run: () => useStore.getState().refreshQuota(), field: (s) => s.quotas },
  { what: "a page of one of its chats", api: "chatEvents", answer: { events: [{ seq: 1, kind: "text", text: "Alice's plan" } as ChatEvent], more: false },
    run: () => useStore.getState().loadChat(ID), field: (s) => s.chat },
];

describe("a read that lands after its account is gone", () => {
  const answering = (api: string, answer: Promise<unknown>) =>
    vi.spyOn(Api as unknown as Record<string, () => Promise<unknown>>, api).mockImplementation(() => answer);

  it.each(reads)("is kept while the account is still the one signed in: $what", async (c) => {
    useStore.setState({ user: A });
    c.setup?.();
    answering(c.api, Promise.resolve(c.answer));
    await c.run();
    expect(c.field(useStore.getState())).not.toEqual(c.field(useStore.getInitialState()));
  });

  /** The read started for A, its account gone (`gone`) while the read's own request is under way, then answered. */
  const answeredAfter = async (c: (typeof reads)[number], gone: () => void) => {
    useStore.setState({ user: A });
    c.setup?.();
    let answer: (v: unknown) => void = () => {};
    const asked = answering(c.api, new Promise((r) => { answer = r; }));
    const reading = c.run();
    await vi.waitFor(() => expect(asked).toHaveBeenCalled());       // the request is out: what it brings is A's
    gone();
    answer(c.answer);
    await reading;
    expect(c.field(useStore.getState())).toEqual(c.field(useStore.getInitialState()));
  };

  it.each(reads)("is dropped once it signed out: $what", (c) => answeredAfter(c, () => useStore.getState().signOut()));

  it.each(reads)("is dropped once another account signed in: $what",
    (c) => answeredAfter(c, () => useStore.getState().signedIn("token-of-b.sig", B)));

  it("says nothing to the next account when it fails: a page of a chat", async () => {
    useStore.setState({ user: A });
    let fail: (e: Error) => void = () => {};
    vi.spyOn(Api, "chatEvents").mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
    const reading = useStore.getState().loadChat(ID);
    useStore.getState().signOut();
    fail(new Error("No such session."));
    await reading;
    expect(useStore.getState().error).toBeNull();
  });
});

describe("a 401 to a request sent with a sign-in the page no longer keeps", () => {
  afterEach(() => { vi.unstubAllGlobals(); whenSignedOut(() => {}); });

  /** `ask` sent with the sign-in "before.sig", `meanwhile` done while it is under way, then the server's 401. What it
   *  threw, and whether the page was told it was signed out. */
  const answered401After = async (ask: () => Promise<unknown>, meanwhile: () => void) => {
    setToken("before.sig");
    const out = vi.fn();
    whenSignedOut(out);
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { answer = r; })));
    const asked = ask();
    await Promise.resolve();
    meanwhile();
    answer(new Response(JSON.stringify({ detail: "Sign in first." }), { status: 401, headers: { "Content-Type": "application/json" } }));
    const thrown = await asked.then(() => null, (e: unknown) => e);
    return { thrown: thrown as ApiError, out };
  };
  const asks: [string, () => Promise<unknown>][] = [
    ["a read", () => api("/api/ade/summary")],
    ["a file's fetch", () => fetchBlob("/api/ade/workspace/sessions/abc/image?path=a.png")],
  ];

  it.each(asks)("leaves the new sign-in that signing out everywhere handed this device: %s", async (_, ask) => {
    const { thrown, out } = await answered401After(ask, () => setToken("after.sig"));
    expect(token()).toBe("after.sig");
    expect(out).not.toHaveBeenCalled();
    expect(thrown).toBeInstanceOf(ApiError);
    expect([thrown.status, thrown.message]).toEqual([401, OLD_SIGN_IN]);
  });

  it.each(asks)("signs the page out, as before, when it answers the sign-in the page still keeps: %s", async (_, ask) => {
    const { thrown, out } = await answered401After(ask, () => {});
    expect(token()).toBeNull();
    expect(out).toHaveBeenCalledTimes(1);
    expect([thrown.status, thrown.message]).toEqual([401, "Sign in first."]);
  });

  it.each(asks)("tells the page nothing more when it signed out while the request was under way: %s", async (_, ask) => {
    const { thrown, out } = await answered401After(ask, () => setToken(null));
    expect(out).not.toHaveBeenCalled();
    expect(token()).toBeNull();
    expect([thrown.status, thrown.message]).toEqual([401, OLD_SIGN_IN]);
  });

  it.each(asks)("says the server's own words, and tells nothing, for a request sent with no sign-in at all: %s", async (_, ask) => {
    const out = vi.fn();
    whenSignedOut(out);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Sign in first." }),
      { status: 401, headers: { "Content-Type": "application/json" } })));
    const thrown = await ask().then(() => null, (e: unknown) => e) as ApiError;
    expect([thrown.status, thrown.message]).toEqual([401, "Sign in first."]);
    expect(out).not.toHaveBeenCalled();
  });

  it("keeps the account on the page through the shell's own read that lands late", async () => {
    whenSignedOut(() => useStore.getState().signOut());
    useStore.setState({ user: A });
    setToken("before.sig");
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { answer = r; })));
    const reading = useStore.getState().readSummary();
    await Promise.resolve();
    setToken("after.sig");
    answer(new Response(JSON.stringify({ detail: "Sign in first." }), { status: 401, headers: { "Content-Type": "application/json" } }));
    await reading;
    expect(useStore.getState().user).toEqual(A);
    expect(token()).toBe("after.sig");
    expect(useStore.getState().error).toBeNull();
  });
});

describe("an invitation taken while another account is signed in on the tab", () => {
  it("leaves nothing of the account signed in before for the new one", async () => {
    asA();
    useStore.setState({ refresh: async () => {} });               // the new account's first read is not this test's
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = path === "/api/auth/invitation" ? { email: B.email } : path === "/api/auth/register" ? { token: "token-of-b.sig", user: B } : { detail: "no route" };
      return new Response(JSON.stringify(body), { status: "detail" in body ? 404 : 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<SignIn invite="an-invitation-token" />);
    const create = await screen.findByRole("button", { name: "Create account" });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-new-password" } });
    fireEvent.click(create);
    await waitFor(() => expect(useStore.getState().user).toEqual(B));
    expect(token()).toBe("token-of-b.sig");
    nothingOfA();
    vi.unstubAllGlobals();
  });
});
