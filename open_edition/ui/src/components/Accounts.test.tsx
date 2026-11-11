import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type AccountsView, type SessionRecord } from "../api";
import { accountNameOf, isSignIn, toSession, useStore } from "../store";
import { AccountRows, accountWords } from "./Connections";
import { NewSession } from "./NewSession";
import { Quota } from "./TitleBar";

// a test's own fetch goes with it, whether it passed or failed
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const NOW = 1790000000;
const view = (over: Partial<AccountsView> = {}): AccountsView => ({
  claude: {
    active: "aaaaaaaaaaaa",
    accounts: [
      { id: "default", name: "Default", created_at: null, sessions: 2,
        status: { installed: true, signed_in: true, email: "me@example.org", subscription: "max" },
        usage: { at: NOW, five_hour: { used_percentage: 97, resets_at: NOW + 600 } } },
      { id: "aaaaaaaaaaaa", name: "Work", created_at: NOW, sessions: 0,
        status: { installed: true, signed_in: true, email: "me@work.example", subscription: "max" },
        usage: { at: NOW, five_hour: { used_percentage: 12, resets_at: NOW + 600 }, seven_day: { used_percentage: 40, resets_at: null } } },
      { id: "bbbbbbbbbbbb", name: "Spare", created_at: NOW, sessions: 0,
        status: { installed: true, signed_in: false }, usage: null },
    ],
  },
  codex: {
    active: "default",
    accounts: [{ id: "default", name: "Default", created_at: null, sessions: 0,
      status: { installed: true, signed_in: false, method: null }, usage: null }],
  },
  ...over,
});

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it", created_at: 1,
     running: true, finished: null, exit_code: null, ended_at: null, chat: null, ...over }) as SessionRecord;

/** Codex's accounts as the workspace lists them, as Claude Code's are above: its own (signed in with ChatGPT, two
 *  sessions), Work (an API key, the one new sessions start on) and Spare (not signed in), with their windows. */
const codexView = (): AccountsView => view({
  codex: {
    active: "aaaaaaaaaaaa",
    accounts: [
      { id: "default", name: "Default", created_at: null, sessions: 2,
        status: { installed: true, signed_in: true, method: "chatgpt" },
        usage: { at: NOW, five_hour: { used_percentage: 97, resets_at: NOW + 600 } } },
      { id: "aaaaaaaaaaaa", name: "Work", created_at: NOW, sessions: 0,
        status: { installed: true, signed_in: true, method: "api_key" },
        usage: { at: NOW, five_hour: { used_percentage: 12, resets_at: NOW + 600 }, seven_day: { used_percentage: 40, resets_at: null } } },
      { id: "bbbbbbbbbbbb", name: "Spare", created_at: NOW, sessions: 0,
        status: { installed: true, signed_in: false, method: null }, usage: null },
    ],
  },
});

// AccountRows is Connections' list for Codex alone: Claude Code's accounts are one list with their standing for tasks
// (ClaudeAccounts), whose sign-ins and acts ClaudeAccounts.test.tsx tests there; the product never renders AccountRows
// for Claude Code
describe("Codex's accounts, in Connections", () => {
  it("lists each with its sign-in, its sessions and its windows, the one new sessions start on chosen", () => {
    useStore.setState({ accounts: codexView(), signIns: [] });
    render(<AccountRows kind="codex" />);
    expect(screen.getByText("Signed in with ChatGPT · 2 sessions")).toBeTruthy();
    expect(screen.getByText("Signed in with an API key")).toBeTruthy();
    expect(screen.getByText("Not signed in")).toBeTruthy();
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    expect(screen.getByTitle("5h window: 97% used").textContent).toContain("97%");
    // the program's own is never signed out from here on the person's own machine (no hosted workspace is named)
    expect(screen.queryByLabelText("Sign Default out")).toBeNull();
    expect(screen.getByLabelText("Sign Work out")).toBeTruthy();
    // the program's own is never removed, with sessions or without
    expect(screen.queryByLabelText("Remove Default")).toBeNull();
    cleanup();
    const idle = codexView();
    idle.codex.accounts[0].sessions = 0;
    useStore.setState({ accounts: idle });
    render(<AccountRows kind="codex" />);
    expect(screen.queryByLabelText("Remove Default")).toBeNull();
    expect(screen.getByLabelText("Remove Work")).toBeTruthy();
  });

  it("chooses the one new sessions start on, adds one and starts its sign-in at once, removes one on a second click", async () => {
    useStore.setState({ accounts: codexView(), signIns: [], refreshAccounts: async () => {}, refresh: async () => {},
      refreshQuota: async () => {}, refreshClaude: async () => {} });
    const chose = vi.spyOn(Api, "chooseAccount").mockResolvedValue({ kind: "codex", active: "default" });
    const added = vi.spyOn(Api, "addAccount").mockResolvedValue({ id: "cccccccccccc", kind: "codex", name: "Team", created_at: NOW });
    const started = vi.spyOn(Api, "createSession").mockResolvedValue(rec({ program: "codex-sign-in" }));
    const removed = vi.spyOn(Api, "removeAccount").mockResolvedValue({ removed: "bbbbbbbbbbbb" });
    render(<AccountRows kind="codex" />);
    fireEvent.click(screen.getByLabelText("New Codex sessions start on Default"));
    await vi.waitFor(() => expect(chose).toHaveBeenCalledWith("codex", "default"));
    const addButton = () => screen.getByText(/Add a Codex account/).closest("button") as HTMLButtonElement;
    await vi.waitFor(() => expect(addButton().disabled).toBe(false));
    fireEvent.click(addButton());
    fireEvent.change(screen.getByLabelText("The new Codex account's name"), { target: { value: " Team " } });
    fireEvent.click(screen.getByText(/Add and sign in/));
    await vi.waitFor(() => expect(started).toHaveBeenCalled());
    expect(added).toHaveBeenCalledWith("codex", "Team");
    expect(started).toHaveBeenCalledWith({ repo: null, program: "codex-sign-in", task: "Sign in to Codex", account: "cccccccccccc" });
    await vi.waitFor(() => expect((screen.getByLabelText("Remove Spare") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByLabelText("Remove Spare"));
    expect(removed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Click again to remove Spare"));
    await vi.waitFor(() => expect(removed).toHaveBeenCalledWith("bbbbbbbbbbbb"));
  });

  it("shows a running sign-in under its own account in Codex's own words, only OpenAI's page a link", () => {
    // Claude Code's sign-in, its link and its code, under its own account: ClaudeAccounts.test.tsx, on its one list
    useStore.setState({
      accounts: codexView(),
      signIns: [
        rec({ id: "222222222222", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true,
          sign_in_text: "1. Open https://auth.openai.com/codex/device or https://elsewhere.example/x\n2. Enter ABCD-12345" }),
      ],
    });
    const { container } = render(<AccountRows kind="codex" />);
    const words = within(screen.getByText("Spare").closest("li")!).getByLabelText("Codex's sign-in");
    expect(screen.getAllByLabelText("Codex's sign-in")).toHaveLength(1);                // only under its own account
    expect(words.textContent).toBe("1. Open https://auth.openai.com/codex/device or https://elsewhere.example/x\n2. Enter ABCD-12345");
    const link = within(words).getByText("https://auth.openai.com/codex/device");
    expect([link.tagName, link.getAttribute("rel")]).toEqual(["A", "noreferrer noopener"]);
    // another address it printed is text, never a link
    expect(container.querySelectorAll("a").length).toBe(1);
    expect([...container.querySelectorAll("a")].map((a) => a.getAttribute("href"))).not.toContain("https://elsewhere.example/x");
  });

  it("says a sign-in in words", () => {
    const row = view().claude.accounts[1];
    expect(accountWords("claude", { ...row, status: null })).toBe("Checking…");
    expect(accountWords("codex", { ...row, sessions: 1, status: { installed: true, signed_in: true, method: "chatgpt" } }))
      .toBe("Signed in with ChatGPT · 1 session");
    expect(accountWords("codex", { ...row, status: { installed: true, signed_in: true, method: "api_key" } }))
      .toBe("Signed in with an API key");
    expect(accountWords("claude", { ...row, status: { installed: false, signed_in: false } })).toBe("Not installed in this workspace");
    expect(accountWords("claude", { ...row, status: { installed: true, signed_in: false } })).toBe("Not signed in");
    expect(accountWords("codex", { ...row, sessions: 2, status: { installed: true, signed_in: null } })).toBe("Sign-in not read just now · 2 sessions");
  });
});

describe("the title bar's windows", () => {
  it("are those of the account new sessions start on, named, and switch it when a program has more than one", () => {
    const chose = vi.fn();
    const manage = vi.fn();
    render(<Quota quotas={{ claude: { at: NOW, five_hour: { used_percentage: 12, resets_at: null }, name: "Work" } }}
      accounts={view()} onChoose={chose} onManage={manage} />);
    const pill = screen.getByRole("button", { name: /Claude · Work/ });
    expect(pill.getAttribute("aria-haspopup")).toBe("menu");
    fireEvent.click(pill);
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => [i.textContent, i.getAttribute("aria-checked")])).toEqual([
      ["Default5h 97%", "false"], ["Work5h 12% · 7d 40%", "true"], ["Spareno windows yet", "false"]]);
    fireEvent.click(items[0]);
    expect(chose).toHaveBeenCalledWith("claude", "default");
    fireEvent.click(pill);
    fireEvent.click(screen.getByText("Manage accounts…"));
    expect(manage).toHaveBeenCalled();
  });

  it("are a plain pill with one account of each program, as before", () => {
    const one = view();
    one.claude = { active: "default", accounts: [one.claude.accounts[0]] };
    render(<Quota quotas={{ claude: { at: NOW, five_hour: { used_percentage: 97, resets_at: null } } }} accounts={one} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Claude")).toBeTruthy();
  });
});

describe("a new session's account", () => {
  it("is picked where a program has more than one, starting from the one new sessions start on", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}} accounts={view()}
      agents={[{ kind: "claude", available: true }, { kind: "codex", available: true }]} />);
    const picker = screen.getByLabelText("Claude Code account") as HTMLSelectElement;
    expect(picker.value).toBe("aaaaaaaaaaaa");
    expect([...picker.options].map((o) => o.textContent)).toEqual(["Default · 5h 97%", "Work · 5h 12%", "Spare · not signed in"]);
    fireEvent.click(screen.getByText("Codex").closest("button")!);          // Codex chosen too: one account, no picker
    expect(screen.queryByLabelText("Codex account")).toBeNull();
    fireEvent.change(picker, { target: { value: "bbbbbbbbbbbb" } });
    expect(screen.getByText("Spare is not signed in: sign it in from Connections first.")).toBeTruthy();
    fireEvent.change(picker, { target: { value: "default" } });
    fireEvent.change(screen.getByPlaceholderText(/What should the agent do/), { target: { value: "Fix it" } });
    fireEvent.click(screen.getByRole("button", { name: /^Start 2 sessions$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0].agents).toEqual(["claude", "codex"]);
    expect(onStart.mock.calls[0][0].accounts).toEqual({ claude: "default" });
  });
});

describe("a session's account", () => {
  it("is named where it is not its program's own", () => {
    expect(accountNameOf(rec({ account: "aaaaaaaaaaaa" }), view())).toBe("Work");
    expect(accountNameOf(rec({ account: "cccccccccccc" }), view())).toBe("another account");
    expect(accountNameOf(rec({ account: "aaaaaaaaaaaa" }), null)).toBe("another account");
    expect(toSession(rec({ account: "aaaaaaaaaaaa" }), view()).accountName).toBe("Work");
  });

  it("is named for the program's own sign-in too where the program has more than one account", () => {
    expect(accountNameOf(rec(), view())).toBe("Default");                        // three Claude Code accounts
    expect(accountNameOf(rec({ program: "chat" }), view())).toBe("Default");     // a chat runs on one of them
    expect(toSession(rec(), view()).accountName).toBe("Default");
    expect(accountNameOf(rec({ program: "codex" }), view())).toBeUndefined();    // Codex has its own alone
    // on another provider's model: that provider's key, never an account
    expect(accountNameOf(rec({ model_provider: "deepseek" }), view())).toBeUndefined();
    expect(accountNameOf(rec({ program: "shell" }), view())).toBeUndefined();    // no account at all
    expect(accountNameOf(rec(), null)).toBeUndefined();                          // the accounts not yet known
  });
});

describe("a sign-in, as the workspace says it started it", () => {
  it("is shown only when the workspace started it, under an account not signed in yet, newest first", () => {
    const forged = rec({ id: "333333333333", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: false,
      sign_in_text: "Enter FORG-ED000 at https://auth.openai.com/codex/device" });
    useStore.setState({ accounts: codexView(), signIns: [forged] });
    render(<AccountRows kind="codex" />);
    expect(screen.queryByLabelText("Codex's sign-in")).toBeNull();
    cleanup();
    // three the workspace started for Spare, the newest neither first nor last in the store's order, and its id the
    // middle one, so that neither id order gives it: the newest one, by when each was made; none under Work, which is
    // signed in (with two, the newer listed first, taking the first in the store's order would pass; with the newest's
    // id the lowest, sorting by id downwards would pass)
    const oldest = rec({ id: "999999999999", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true,
      created_at: 1, sign_in_text: "Enter OLDE-ST000" });
    const newest = rec({ id: "666666666666", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true,
      created_at: 3, sign_in_text: "Enter NEWE-ST000" });
    const between = rec({ id: "333333333333", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true,
      created_at: 2, sign_in_text: "Enter BETW-EEN00" });
    const onWork = rec({ id: "121212121212", program: "codex-sign-in", repo: null, account: "aaaaaaaaaaaa", sign_in: true,
      created_at: 4, sign_in_text: "Enter WORK-00000" });
    useStore.setState({ accounts: codexView(), signIns: [oldest, newest, between, onWork] });
    render(<AccountRows kind="codex" />);
    expect(screen.getAllByLabelText("Codex's sign-in").map((w) => w.textContent)).toEqual(["Enter NEWE-ST000"]);
    cleanup();
    // one started with no account named, as the workspace records the program's own: under its Default, signed out
    // here, and not under Spare, signed out too and the one new sessions start on
    const own = rec({ id: "abcdefabcdef", program: "codex-sign-in", repo: null, account: null, sign_in: true,
      sign_in_text: "Enter DEFA-ULT00" });
    const ownOut = codexView();
    ownOut.codex.accounts[0].status = { installed: true, signed_in: false, method: null };
    ownOut.codex.active = "bbbbbbbbbbbb";
    useStore.setState({ accounts: ownOut, signIns: [own] });
    render(<AccountRows kind="codex" />);
    expect(screen.getAllByLabelText("Codex's sign-in")).toHaveLength(1);
    expect(within(screen.getByText("Default").closest("li")!).getAllByLabelText("Codex's sign-in")
      .map((w) => w.textContent)).toEqual(["Enter DEFA-ULT00"]);
  });

  it("is offered once an account is known not to be signed in, and started again in place of a stuck one", async () => {
    const pending = codexView();
    pending.codex.accounts[2].status = null;                        // Spare: not known yet
    useStore.setState({ accounts: pending, signIns: [], refreshAccounts: async () => {}, refresh: async () => {},
      refreshQuota: async () => {}, refreshClaude: async () => {} });
    const { unmount } = render(<AccountRows kind="codex" />);
    expect(within(screen.getByText("Spare").closest("li")!).queryByText("Sign in")).toBeNull();
    unmount();
    const stuck = rec({ id: "777777777777", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true });
    useStore.setState({ accounts: codexView(), signIns: [stuck] });
    const removed = vi.spyOn(Api, "removeSession").mockResolvedValue({ removed: stuck.id });
    const started = vi.spyOn(Api, "createSession").mockResolvedValue(rec({ program: "codex-sign-in" }));
    render(<AccountRows kind="codex" />);
    fireEvent.click(within(screen.getByText("Spare").closest("li")!).getByText("Start again"));
    await vi.waitFor(() => expect(started).toHaveBeenCalled());
    expect(removed).toHaveBeenCalledWith(stuck.id);
    expect(started).toHaveBeenCalledWith({ repo: null, program: "codex-sign-in", task: "Sign in to Codex", account: "bbbbbbbbbbbb" });
    expect(removed.mock.invocationCallOrder[0]).toBeLessThan(started.mock.invocationCallOrder[0]);
  });
});

describe("the accounts' views, in order", () => {
  it("never put back an account chosen before a switch that answered after it", async () => {
    const before = view();
    const after = view();
    after.claude.active = "default";
    let first: (v: AccountsView) => void = () => {};
    vi.spyOn(Api, "accounts")
      .mockImplementationOnce(() => new Promise((r) => { first = r; }))
      .mockResolvedValueOnce(after);
    // the switch itself, as the page sends it: a read asked once it is made is a read of its own, never joined to the
    // one asked before it, whose answer can be from before the switch
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ kind: "claude", active: "default" }), { status: 200 })));
    const real = useStore.getInitialState();                        // the store's own, which other tests stood in for
    // signed in, as the page is when it reads them: what the store reads is kept only for the account signed in
    useStore.setState({ user: { id: "u1", email: "owner@example.test", can_sign_in_again: true }, accounts: null, refreshAccounts: real.refreshAccounts });
    const slow = useStore.getState().refreshAccounts();             // asked first, answered last
    await Api.chooseAccount("claude", "default");
    await useStore.getState().refreshAccounts();
    first(before);
    await slow;
    expect(useStore.getState().accounts!.claude.active).toBe("default");
  });
});

describe("the reads of Claude Code's sign-in and of the accounts", () => {
  it("join one of their kind under way, a status read of the accounts covering a plain one; never one that went out before the page wrote, or for another account", async () => {
    // the workspace, slow: each read answered only when the test lets it go; a write answered at once
    const asked: string[] = [];
    const open: (() => void)[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit = {}) => {
      const method = init.method || "GET";
      asked.push(`${method} ${url}`);
      const body = url.endsWith("/claude") ? { installed: true, signed_in: true, email: "me@example.org", subscription: "max" }
        : url.endsWith("/accounts/choose") ? { kind: "claude", active: "default" } : view();
      const answer = () => new Response(JSON.stringify(body), { status: 200 });
      return method === "GET" ? new Promise<Response>((r) => open.push(() => r(answer()))) : Promise.resolve(answer());
    }));
    const real = useStore.getInitialState();
    useStore.setState({ user: { id: "u1", email: "owner@example.test", can_sign_in_again: true }, accounts: null, claude: null,
      refreshClaude: real.refreshClaude, refreshAccounts: real.refreshAccounts });
    const st = () => useStore.getState();
    const answered = async (reads: Promise<void>[]) => { while (open.length) open.shift()!(); await Promise.all(reads); asked.length = 0; };
    // asked again while the first is under way, as Connections' look asks every 4 seconds: one request of each kind,
    // and a plain read of the accounts joins the status read under way, whose view has each program's status besides
    let reads = [st().refreshClaude(), st().refreshClaude(), st().refreshAccounts(), st().refreshAccounts(true), st().refreshAccounts(false)];
    expect(asked).toEqual(["GET /api/ade/workspace/claude", "GET /api/ade/workspace/accounts"]);
    await answered(reads);
    expect(st().claude?.email).toBe("me@example.org");
    expect(st().accounts?.claude.accounts.map((a) => a.status?.signed_in)).toEqual([true, true, false]);
    // a plain read joins a plain one under way; a status read does not, the plain one saying no program's status
    reads = [st().refreshAccounts(false), st().refreshAccounts(false), st().refreshAccounts(true)];
    expect(asked).toEqual(["GET /api/ade/workspace/accounts?status=false", "GET /api/ade/workspace/accounts"]);
    await answered(reads);
    // once the page has written (an account chosen, a sign-in's code), a read is its own: the one under way went out
    // before the change, and its answer can be from before it; the next one asked joins it
    reads = [st().refreshClaude(), st().refreshAccounts()];
    await Api.chooseAccount("claude", "default");
    reads.push(st().refreshClaude(), st().refreshAccounts(), st().refreshClaude());
    expect(asked).toEqual(["GET /api/ade/workspace/claude", "GET /api/ade/workspace/accounts", "POST /api/ade/workspace/accounts/choose",
      "GET /api/ade/workspace/claude", "GET /api/ade/workspace/accounts"]);
    await answered(reads);
    // and a read for another account signed in on the page is its own: what the one under way brings is not kept for it
    reads = [st().refreshClaude()];
    st().signOut();
    useStore.setState({ user: { id: "u2", email: "other@example.test", can_sign_in_again: true } });
    reads.push(st().refreshClaude());
    expect(asked).toEqual(["GET /api/ade/workspace/claude", "GET /api/ade/workspace/claude"]);
    await answered(reads);
    expect(st().claude?.email).toBe("me@example.org");
  });
});

describe("which sessions are kept out of the list as sign-ins", () => {
  it("are the workspace's own sign-ins and ended ones; one that only calls itself so, running, stays listed", () => {
    expect(isSignIn(rec({ program: "claude-sign-in", running: true, sign_in: true }))).toBe(true);
    expect(isSignIn(rec({ program: "codex-sign-in", running: false }))).toBe(true);
    expect(isSignIn(rec({ program: "claude-sign-in", running: true, sign_in: false }))).toBe(false);
    expect(isSignIn(rec({ program: "claude", running: true, sign_in: false }))).toBe(false);
  });
});

describe("a sign-in that ended without signing in", () => {
  it("says so under its account with what it said last, and only when it just ended", () => {
    const now = Math.floor(Date.now() / 1000);
    const ended = rec({ id: "888888888888", program: "codex-sign-in", repo: null, account: "bbbbbbbbbbbb", sign_in: true,
      running: false, created_at: now - 60, ended_at: now - 30, sign_in_text: "Error logging in: the device code expired" });
    useStore.setState({ accounts: codexView(), signIns: [ended] });
    const { unmount } = render(<AccountRows kind="codex" />);
    const spare = screen.getByText("Spare").closest("li")!;
    expect(within(spare).getByText(/ended without signing it in/)).toBeTruthy();
    expect(within(spare).getByText(/the device code expired/)).toBeTruthy();
    expect(within(spare).getByText("Sign in")).toBeTruthy();
    unmount();
    // long ago: nothing said of it
    useStore.setState({ accounts: codexView(), signIns: [{ ...ended, ended_at: now - 3600 }] });
    render(<AccountRows kind="codex" />);
    expect(screen.queryByText(/ended without signing it in/)).toBeNull();
  });
});
