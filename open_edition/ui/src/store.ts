import { create } from "zustand";
import { Api, ApiError, setToken, token, writesSent, type ChatEvent, type ClaudeStatus, type GithubStatus, type HistorySession, type HookRequest, type RepoView, type SessionRecord, type ShellSummary, type Stamped, type User, type WorkspaceState, type Quotas, type QuotaLine, type AccountsView, type ModelProviderView, type LinkSessions, type SessionAsk, type CustomAgent, type Proposal, type Held, type GrantWideningView, NO_PASSWORD } from "./api";
import { MODEL_PROVIDERS, type AgentKind, type Session, type SessionState, type Tab } from "./types";
import { addDelta, settle, type ChatDelta, type Live } from "./live";
import { FLOOR, offered, type Capabilities } from "./chatSettings";

const AGENTS: Record<string, AgentKind> = { claude: "claude", codex: "codex", gemini: "gemini", opencode: "opencode", grok: "grok", qwen: "qwen",
  kimi: "kimicode", mcode: "mcode", shell: "shell", chat: "chat", custom: "custom" };

/** The agent a program of the workspace shows as (its name there is not always the page's: `kimi` is Kimi Code). */
export function agentOfProgram(program: string): AgentKind {
  return AGENTS[program] || "shell";
}

/** The agent a session shows as: a claude session on another provider's model shows as that model. */
export function agentOf(r: SessionRecord): AgentKind {
  if (r.program === "claude" && (MODEL_PROVIDERS as readonly string[]).includes(r.model_provider || "")) return r.model_provider as AgentKind;
  return agentOfProgram(r.program);
}
export const SIGN_IN_PROGRAMS = new Set(["github", "claude-sign-in", "codex-sign-in"]);
/** A sign-in, kept out of the sessions' list: one the workspace started (`sign_in`), or one that has ended. A running
 *  session that only calls itself one is listed, as any session is, and never shown as a sign-in. */
export const isSignIn = (r: SessionRecord) => SIGN_IN_PROGRAMS.has(r.program) && (r.sign_in === true || !r.running);
// the accounts' views, applied in the order they were asked for: one asked before a switch and answered after it
// never puts the old account back
let accountsAsked = 0;
let accountsShown = 0;

const iso = (unix?: number | null) => (unix ? new Date(unix * 1000).toISOString() : undefined);

export function sessionState(r: SessionRecord): SessionState {
  if (r.program === "chat") {
    const s = r.chat?.state;
    if (s === "running") return "working";
    if (s === "needs_you" || (r.chat?.asks?.length ?? 0) > 0) return "waiting";
    if (r.finished) return "done";
    if (s === "error") return "failed";
    return "idle";
  }
  if (r.running) {
    const a = r.activity?.state;
    return a === "working" ? "working" : a === "waiting" ? "waiting" : a === "idle" ? "idle" : "running";
  }
  if (r.finished) return "done";
  // a negative status is the signal that ended it: hung up when the workspace stopped, or by the person
  if (r.exit_code != null) return r.exit_code === 0 ? "done" : r.exit_code < 0 ? "stopped" : "failed";
  return "stopped";
}

/** What a session that needs the person is waiting for: a chat's first question (and how many more), or what a
 *  terminal agent said through its hooks; nothing when it needs no one. */
export function waitingFor(r: SessionRecord): string | undefined {
  const asks = r.chat?.asks || [];
  if (asks.length) {
    const a = asks[0];
    const tool = String(a.name || a.tool || "a tool");
    const what = typeof a.summary === "string" && a.summary.trim() ? `: ${a.summary.trim()}` : "";
    return `Wants to use ${tool}${what}${asks.length > 1 ? ` (and ${asks.length - 1} more)` : ""}`;
  }
  if (r.running && r.activity?.state === "waiting" && r.activity.message) return r.activity.message;
  return undefined;
}

const NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/;

/** What a session that needs the person is waiting for, in a notification's words: the tool it would use, or whether it
 *  is a permission, never the command or what the program said. A notification shows on a phone's lock screen and on
 *  a shared screen, and an agent's command can carry a token; the note in the list, inside the page, keeps it all.
 *  The app's push notifications say the same (push.py `wants`). */
export function waitingAlert(r: SessionRecord): string | undefined {
  const wants = (tool: unknown, kind?: unknown) =>
    typeof tool === "string" && NAME.test(tool) ? `Wants to use ${tool}`
      : typeof kind === "string" && kind.toLowerCase().includes("permission") ? "Wants your permission"
      : "Waits for your answer";
  const asks = r.chat?.asks || [];
  if (asks.length) return wants(asks[0].name || asks[0].tool) + (asks.length > 1 ? ` (and ${asks.length - 1} more)` : "");
  if (r.running && r.activity?.state === "waiting") return wants(r.activity.tool, r.activity.kind);
  return undefined;
}

/** The name of the account a session runs on, for its header and its group in the list: an added account's name, and
 *  its program's own sign-in's ("Default") too where that program has more than one account in the workspace, so
 *  that every session there says which it is on; none where the program has only its own, or no accounts
 *  at all (a shell), nor for a session on another provider's model, which runs on that provider's key, never on an
 *  account (the workspace binds it to none); "another account" while the accounts are not known yet (or one was
 *  removed). */
export function accountNameOf(r: Pick<SessionRecord, "account" | "model_provider" | "program">, accounts?: AccountsView | null): string | undefined {
  if (!r.account) {
    if (r.model_provider) return undefined;
    const kind = r.program === "codex" ? "codex" : r.program === "claude" || r.program === "chat" ? "claude" : null;
    const rows = kind ? accounts?.[kind].accounts : undefined;
    return rows && rows.length > 1 ? rows.find((a) => a.id === "default")?.name || "Default" : undefined;
  }
  const kind = r.program === "codex" ? "codex" : "claude";
  return accounts?.[kind].accounts.find((a) => a.id === r.account)?.name || "another account";
}

/** A ref's short name, as a person says it: refs/heads/dev is dev, refs/remotes/origin/main is origin/main, and
 *  refs/pull/7/head is pull request #7. */
export function shortRef(ref?: string | null): string | undefined {
  if (!ref) return undefined;
  const pull = /^refs\/pull\/([0-9]+)\/head$/.exec(ref);
  if (pull) return `#${pull[1]}`;
  return ref.replace(/^refs\/(heads|remotes|tags)\//, "");
}

export function toSession(r: SessionRecord, accounts?: AccountsView | null): Session {
  const title = r.title || (r.task || "").split("\n")[0].trim();
  return {
    id: r.id,
    title: title || (r.program === "shell" ? "Shell" : r.program),
    repo: r.repo || "Workspace",
    branch: r.branch || undefined,
    base: r.base ? r.base.slice(0, 7) : undefined,
    baseRef: shortRef(r.base_ref),
    agent: agentOf(r),
    agentName: r.custom?.name || undefined,
    accountName: accountNameOf(r, accounts),
    closed: r.closed ? true : undefined,
    state: sessionState(r),
    updatedAt: iso(r.ended_at || r.activity?.at || r.chat?.last_at || r.finished?.at || r.created_at),
    costUsd: r.chat ? r.chat.cost : undefined,
    group: r.group,
    diff: r.diff ?? undefined,
    shell: !!r.shells?.some((x) => x.running),
    note: waitingFor(r),
    alert: waitingAlert(r),
  };
}

/** What a session's Files tab is asked to open: a file, at a line when one is given; or its search, with a query. */
export type FileAsk = { path: string; line?: number } | { search: string };

/** Whether the workspace runs, as the shell takes it: given to the account, running, and not still starting. */
export function workspaceRuns(ws: WorkspaceState | null | undefined): boolean {
  return !!ws && ws.enabled && ws.allowed !== false && !!ws.running && !ws.starting;
}

/** The statuses a proxy in front of the app (Caddy, Fly's edge) answers with in its place when the app is down. */
export const GATEWAY = [502, 503, 504];

/** A read that failed because the server could not be reached (the network, or the server, down), not a refusal: no
 *  answer at all, or a proxy's 502, 503 or 504 in the app's place. The app answers some of those itself, always with a
 *  JSON object saying why, and that is said as it is. */
export function unreachable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 0 || (GATEWAY.includes(e.status) && !e.fromApp));
}

/** What the asks' route (GET /api/ade/agents/tasks/grant-widenings) answers on a server from before grants, read as no
 *  grants there: 404, or, where the path reaches GET /tasks/{id}, that route's 403 (an account that may have no
 *  workspace) or 405 (a server where it takes no GET). A 401 is not among them: it is the sign-in's. */
const GRANTS_ABSENT = [403, 404, 405];

// the read of the summary under way, which a second read joins rather than asking again
let summaryRead: Promise<void> | null = null;
// The reads of Claude Code's sign-in and of the accounts under way, by kind (the accounts' with each program's status,
// and without it): a read of a kind asked while one is under way joins it rather than asking again, since Connections'
// look asks for both every 4 seconds without waiting for the last, and a status read of the accounts has the workspace
// run each account's `claude auth status`. Never one that went out before the page last wrote (api.ts, writesSent),
// whose answer can be from before the change (an account chosen, a sign-in's code), nor one asked for another account
// signed in on the page since (`joined`, below).
type ReadKind = "claude" | "accounts" | "accounts-plain";
const readsUnderWay = new Map<ReadKind, { user: User | null; writes: number; read: Promise<void> }>();
// the error shown, put away 6 seconds after it was last said: the same error said again keeps it showing, steadily
let errorTimer: ReturnType<typeof setTimeout> | undefined;
// The sessions this page closed, each held closed in every read of the lists that began before the
// workspace had closed it, whenever that read is answered: one under way when the close was pressed, or answered while
// the workspace stops its program (a grace of seconds), would otherwise list it again, and choose it where none is
// chosen. By id: the count of reads begun when the workspace closed it, Infinity until then; a read begun after that
// has the workspace's own word. An open, or a close refused, lets it go; so does a sign-out (forget).
const closing = new Map<string, number>();
let readsBegun = 0;

/** A read's records, as the page holds them: each session it closed marked closed (above). */
function heldClosed(records: SessionRecord[], begun: number): SessionRecord[] {
  if (!closing.size) return records;
  return records.map((r) => {
    const done = closing.get(r.id);
    return done === undefined || begun > done || r.closed ? r : { ...r, closed: true };
  });
}

interface Store {
  user: User | null;
  booted: boolean;
  ws: WorkspaceState | null;
  repos: RepoView[];
  records: SessionRecord[];
  signIns: SessionRecord[];          // the workspace's Claude and GitHub sign-ins, which run as programs
  claude: ClaudeStatus | null;
  github: GithubStatus | null;
  quotas: Quotas;                    // each program's windows on the account new sessions start on
  accounts: AccountsView | null;     // Claude Code's and Codex's accounts in the workspace (accounts.py)
  programs: Record<string, boolean> | null;   // which terminal agents the workspace has; null until it says
  held: Held | null;                          // whether its claude terminals and chats hold their gated commands
                                              // for the approval queue; null until it says
  modelProviders: ModelProviderView[] | null;  // other providers' models for claude, and whether each has a key
  customAgents: CustomAgent[] | null;          // the person's own agents (custom_agents.py), null until read
  caps: Capabilities;                         // what the installed Claude Code offers a chat; the floor until it says
  approvals: HookRequest[];
  /** what connected apps proposed, waiting for the owner (the server's ade/proposals.py); off once the deployment
   *  says it has no connector, so it is not asked again */
  proposals: Proposal[];
  proposalsOff: boolean;
  /** new sessions agents asked for, waiting for the person (the agent's asks.py), and whether agents may ask */
  sessionAsks: SessionAsk[];
  /** the asks for more in a task's grant, waiting for the owner's passkey (the server's ade/task_grants.py), read
   *  with the approvals and under their stamp; off once the server says it has none (one from before grants), so it
   *  is not asked again */
  grantWidenings: GrantWideningView[];
  grantWideningsOff: boolean;
  asksOn: boolean | null;            // null until the workspace has said
  /** the session channel: the owner's linked coding sessions, their checkpoints and questions; null until read */
  links: LinkSessions | null;
  /** when the read that gave `links` went out, by the page's monotonic clock (performance.now): a countdown runs by the
   *  server's time in that answer from then (Channel's readAtOf), not from whenever the panel shows it; null until read */
  linksAt: number | null;
  /** the linked machines' Claude Code sessions, as each link last listed them (the History list, in the sidebar);
   *  null until read, so an empty list is not said before it is known */
  history: HistorySession[] | null;
  /** the agents' runs waiting for the owner, which the title bar's Agents badge counts */
  agentsWaiting: number;
  /** the server's summary as the shell last read it (GET /api/ade/summary); null until it has */
  summary: ShellSummary | null;
  /** the server has no summary (one from before it): the shell reads each list on its own, as it did */
  summaryOff: boolean;
  /** each list's stamp as of the read that gave the page its copy: once the summary's stamp moves, it is read again */
  read: Partial<Record<Stamped, string | null>>;
  /** the server could not be reached at the page's last try (useOffline), and when the page last read it (ms) */
  offline: boolean;
  lastRead: number | null;
  chat: Record<string, ChatEvent[]>;
  live: Record<string, Live | undefined>;   // what each chat's claude is writing, until the log has it
  fileFor: Record<string, FileAsk | undefined>;  // what a session's Files tab was asked to open: a file (at a line), or a search
  error: string | null;
  /** what just happened, said for a moment (a finish's verdict, say) */
  notice: { text: string; tone: "done" | "wait" | "fail" } | null;
  selected: string | null;
  tab: Tab;
  panes: string[];                  // sessions open side by side; the selected one is always among them
  boot(): Promise<void>;
  signIn(email: string, password: string): Promise<void>;
  /** The user a sign-in gave, with its token. Another account signed in on this page before it (an invitation opened
   *  while signed in) has what it held cleared first, as a sign-out clears it. */
  signedIn(token: string, user: User): void;
  signOut(): void;
  /** Connections (the sign-ins and the accounts) open over the page: opened from the title bar, the sessions list and
   *  any form that needs a sign-in or an account, closed from itself, and closed with the account's sign-out. */
  connectionsOpen: boolean;
  setConnectionsOpen(open: boolean): void;
  refresh(): Promise<void>;
  /** the summary, then each list whose stamp moved since the page read it */
  readSummary(): Promise<void>;
  /** the workspace's repositories and sessions, while it runs */
  refreshLists(): Promise<void>;
  refreshApprovals(): Promise<void>;
  refreshLinks(): Promise<void>;
  refreshHistory(): Promise<void>;
  refreshClaude(): Promise<void>;
  refreshGithub(): Promise<void>;
  refreshQuota(): Promise<void>;
  refreshAccounts(status?: boolean): Promise<void>;
  refreshPrograms(): Promise<void>;
  refreshModelProviders(): Promise<void>;
  refreshCustomAgents(): Promise<void>;
  refreshCaps(): Promise<void>;
  startWorkspace(): Promise<void>;
  stopWorkspace(): Promise<void>;
  select(id: string | null): void;
  setTab(t: Tab): void;
  openBeside(id: string): void;
  closePane(id: string): void;
  /** Close a session: its pane shut, its program stopped and itself taken out of the list, nothing of it
   *  removed. Search finds it, and opening it there (reopenSession) puts it back. */
  closeSession(id: string): Promise<void>;
  /** A closed session back in the list. */
  reopenSession(id: string): Promise<void>;
  setError(e: unknown): void;
  say(text: string, tone?: "done" | "wait" | "fail", ms?: number): void;
  addChat(session: string, ev: ChatEvent): void;
  addDelta(session: string, d: ChatDelta): void;
  dropLive(): void;
  openFile(session: string, path: string, line?: number): void;
  openSearch(session: string, query?: string): void;
  tookFile(session: string): void;
  loadChat(session: string): Promise<void>;
}

export const useStore = create<Store>((set, get) => ({
  user: null,
  booted: false,
  ws: null,
  repos: [],
  records: [],
  signIns: [],
  claude: null,
  github: null,
  quotas: {},
  accounts: null,
  connectionsOpen: false,
  programs: null,
  held: null,
  modelProviders: null,
  customAgents: null,
  caps: FLOOR,
  approvals: [],
  proposals: [],
  proposalsOff: false,
  sessionAsks: [],
  grantWidenings: [],
  grantWideningsOff: false,
  asksOn: null,
  links: null,
  linksAt: null,
  history: null,
  agentsWaiting: 0,
  summary: null,
  summaryOff: false,
  read: {},
  offline: false,
  lastRead: null,
  chat: {},
  live: {},
  fileFor: {},
  error: null,
  notice: null,
  selected: null,
  tab: "agent",
  panes: [],

  async boot() {
    if (!token()) return set({ booted: true });
    try {
      const user = await Api.me();
      set({ user, booted: true });
    } catch {
      // a 401 is the server saying no to this sign-in, which the api has let go of already. Anything else (no network,
      // a server that failed) says nothing of it: it is kept for the next load, rather than thrown away from under every
      // other tab of the page, which share it and sign out once it goes
      set({ user: null, booted: true });
      return;
    }
    await firstRead(get);
  },

  async signIn(email, password) {
    const r = await Api.login(email, password);
    get().signedIn(r.token, r.user);
    await firstRead(get);
  },

  signedIn(token, user) {
    const was = get().user;
    if (was && was.id !== user.id) set(forget());
    setToken(token);
    set({ user, error: null });
  },

  signOut() {
    // an account with no password (this Mac's own, signed out of in a browser): the sign-in screen says how to get back
    try { if (get().user?.can_sign_in_again === false) localStorage.setItem(NO_PASSWORD, "1"); } catch { /* this page only */ }
    setToken(null);
    set({ user: null, ...forget() });
  },

  setConnectionsOpen(open) {
    set({ connectionsOpen: open });
  },

  async refresh() {
    if (!get().user) return;
    const same = sameUser(get);
    try {
      const ws = await Api.workspace();
      if (!same()) return;
      set({ ws, offline: false, lastRead: Date.now() });
      if (workspaceRuns(ws)) await readLists(set, get);
      else stopped(set, get);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) get().setError(e);
    }
  },

  readSummary() {
    // a read already under way is joined: the page's first read and its poll's first tick are one request
    if (summaryRead) return summaryRead;
    const reading: Promise<void> = (async () => {
      if (!get().user || get().summaryOff) return;
      const same = sameUser(get);
      let s: ShellSummary;
      try {
        s = await Api.summary();
      } catch (e) {
        if (!same()) return;
        // a server from before the summary: the shell reads each list on its own, as it did (shellPoll.ts)
        if (e instanceof ApiError && e.status === 404) set({ summaryOff: true });
        else if (!(e instanceof ApiError && e.status === 401)) get().setError(e);
        return;
      }
      if (!same()) return;                    // signed out, or in as another, while it was read
      set({ summary: s, offline: false, lastRead: Date.now(), agentsWaiting: s.agents ?? 0, ...(s.workspace ? { ws: s.workspace } : {}) });
      if (s.workspace_error) get().setError(new Error(s.workspace_error));
      // each list whose stamp moved since the page read it, read again; the rest are as the page has them
      const due = (k: Stamped) => typeof s.stamps[k] === "string" && s.stamps[k] !== get().read[k];
      const reads: Promise<void>[] = [];
      if (s.workspace && !workspaceRuns(s.workspace)) stopped(set, get);
      else if (due("sessions") || due("repos")) reads.push(get().refreshLists());
      if (due("approvals") || due("proposals") || due("asks")) reads.push(get().refreshApprovals());
      if (due("links")) reads.push(get().refreshLinks());
      if (due("history")) reads.push(get().refreshHistory());
      await Promise.all(reads);
    })().finally(() => { if (summaryRead === reading) summaryRead = null; });
    summaryRead = reading;
    return reading;
  },

  async refreshLists() {
    if (!get().user) return;
    try {
      await readLists(set, get);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) get().setError(e);
    }
  },

  async refreshApprovals() {
    if (!get().user) return;
    const same = sameUser(get);
    const stamps = get().summary?.stamps;
    const read: Stamped[] = [];
    let approvals = false;
    try {
      const r = await Api.approvals();
      if (!same()) return;
      set({ approvals: r.requests.filter((q) => q.state === "pending") });
      approvals = true;
    } catch { /* the next poll tries again */ }
    // the asks for more in a task's grant wait for the owner in the same list, under the same stamp: that list is read
    // only once both are. A server from before grants answers 404, or reaches GET /tasks/{id} with this path, which
    // answers 403 for an account that may have no workspace and 405 where that route takes no GET: each is read as no
    // grants here, and the route is not asked again. A 401 stays the sign-in's, and the list waits for the next poll.
    if (!get().grantWideningsOff) {
      try {
        const g = await Api.grantWidenings();
        if (!same()) return;
        set({ grantWidenings: g.widenings });
      } catch (e) {
        if (same() && e instanceof ApiError && GRANTS_ABSENT.includes(e.status)) set({ grantWidenings: [], grantWideningsOff: true });
        else approvals = false;
      }
    }
    if (approvals) read.push("approvals");
    // what connected apps proposed: the account's, whether or not the workspace runs
    if (!get().proposalsOff) {
      try {
        const p = await Api.proposals();
        if (!same()) return;
        set({ proposals: p.proposals });
        read.push("proposals");
      } catch (e) {
        if (same() && e instanceof ApiError && e.status === 404) set({ proposals: [], proposalsOff: true });
      }
    }
    // what agents asked for, while the workspace runs: its agent keeps the asks
    if (!get().ws?.running) {
      if (get().sessionAsks.length) set({ sessionAsks: [] });
    } else {
      try {
        const a = await Api.asks();
        if (!same()) return;
        set({ sessionAsks: a.asks, asksOn: a.on });
        read.push("asks");
      } catch { /* the next poll tries again */ }
    }
    noteRead(set, get, stamps, read);
  },

  async refreshLinks() {
    if (!get().user) return;
    const same = sameUser(get);
    const stamps = get().summary?.stamps;
    // taken as the read goes out: the server's time in its answer is from after it, so a countdown counted from here
    // never shows more time left than there is, but for the part of a second that time leaves out
    const at = performance.now();
    try {
      const links = await Api.linkSessions();
      if (!same()) return;
      set({ links, linksAt: at });
      noteRead(set, get, stamps, ["links"]);
    } catch { /* the next poll tries again */ }
  },

  async refreshHistory() {
    if (!get().user) return;
    const same = sameUser(get);
    const stamps = get().summary?.stamps;
    try {
      const got = await Api.linkHistory();
      if (!same()) return;
      set({ history: got.sessions });
      noteRead(set, get, stamps, ["history"]);
    } catch { /* the next poll tries again */ }
  },

  refreshClaude() {
    return joined("claude", get, async () => {
      const same = sameUser(get);
      try {
        const claude = await Api.claude();
        if (same()) set({ claude });
      } catch { /* stays as it was; the next refresh asks again */ }
    });
  },

  async refreshGithub() {
    const same = sameUser(get);
    try {
      const github = await Api.github();
      if (same()) set({ github });
    } catch { /* idem */ }
  },

  refreshAccounts(status = true) {
    // a status read under way covers a plain one: its view is the plain one's, with each program's status besides
    const covering = status ? null : underWay("accounts", get);
    return covering ?? joined(status ? "accounts" : "accounts-plain", get, async () => {
      const same = sameUser(get);
      const asked = ++accountsAsked;
      try {
        const view = await Api.accounts(status);
        if (!same() || asked < accountsShown) return;
        accountsShown = asked;
        // a look without the programs' status keeps what the last full look said of each
        const was = get().accounts;
        if (!status && was) {
          for (const kind of ["claude", "codex"] as const) {
            for (const row of view[kind].accounts) row.status = was[kind].accounts.find((r) => r.id === row.id)?.status ?? null;
          }
        }
        set({ accounts: view });
      } catch { /* a workspace from before its accounts has none to say; the next look asks again */ }
    });
  },

  async refreshQuota() {
    // the windows are an account's: those of the account new sessions start on, which the workspace reads from each
    // account's own records (accounts.py)
    let view: AccountsView | null = null;
    const same = sameUser(get);
    const asked = ++accountsAsked;
    try { view = await Api.accounts(false); } catch { /* a workspace from before its accounts: as before, below */ }
    if (!same() || (view && asked < accountsShown)) return;
    if (view) {
      accountsShown = asked;
      const was = get().accounts;
      for (const kind of ["claude", "codex"] as const) {
        for (const row of view[kind].accounts) row.status = was?.[kind].accounts.find((r) => r.id === row.id)?.status ?? null;
      }
      const next: Quotas = {};
      for (const kind of ["claude", "codex"] as const) {
        const row = view[kind].accounts.find((r) => r.id === view![kind].active);
        if (row?.usage && (row.usage.five_hour || row.usage.seven_day)) {
          const line: QuotaLine = { at: row.usage.at, ...(row.usage.five_hour ? { five_hour: row.usage.five_hour } : {}), ...(row.usage.seven_day ? { seven_day: row.usage.seven_day } : {}) };
          if (row.id !== "default") line.name = row.name;
          next[kind] = line;
        }
      }
      set({ accounts: view, quotas: next });
      return;
    }
    // the freshest status of any of the program's recent sessions says them: Claude's status line for claude and chat
    // sessions, Codex's own files for codex ones
    const accounts: [keyof Quotas, string[]][] = [["claude", ["claude", "chat"]], ["codex", ["codex"]]];
    const next: Quotas = { ...get().quotas };
    for (const [account, programs] of accounts) {
      const recent = get().records
        .filter((r) => programs.includes(r.program))
        .sort((a, b) => (b.chat?.last_at || b.activity?.at || b.created_at) - (a.chat?.last_at || a.activity?.at || a.created_at))
        .slice(0, 3);
      for (const r of recent) {
        try {
          const u = await Api.usage(r.id);
          const best = next[account];
          if (u.status && (u.status.five_hour || u.status.seven_day) && (!best || u.status.at > best.at)) next[account] = u.status;
        } catch { /* the next refresh asks again */ }
      }
    }
    if (!same()) return;
    const was = get().quotas;
    if (next.claude !== was.claude || next.codex !== was.codex) set({ quotas: next });
  },

  // each of these four keeps what it read only while the account it read for is still the one signed in (sameUser)
  async refreshCaps() {
    const same = sameUser(get);
    try { const caps = offered(await Api.capabilities()); if (same()) set({ caps }); } catch { /* the floor stays */ }
  },

  async refreshPrograms() {
    const same = sameUser(get);
    try {
      const { programs, held } = await Api.programs();
      // an older agent says nothing of it: its machine holds nothing
      if (same()) set({ programs, held: held === "held" || held === "refused" ? held : "none" });
    } catch { /* asked again when the workspace starts */ }
  },

  async refreshModelProviders() {
    const same = sameUser(get);
    try { const { providers } = await Api.modelProviders(); if (same()) set({ modelProviders: providers }); } catch { /* asked again when it is needed */ }
  },

  async refreshCustomAgents() {
    const same = sameUser(get);
    try { const { agents } = await Api.customAgents(); if (same()) set({ customAgents: agents }); } catch { /* asked again when it is needed */ }
  },

  async startWorkspace() {
    try {
      set({ ws: { ...(get().ws || { enabled: true }), starting: true } });
      const ws = await Api.startWorkspace();
      set({ ws });
    } catch (e) {
      get().setError(e);
    }
    await get().refresh();
  },

  async stopWorkspace() {
    try {
      set({ ws: await Api.stopWorkspace(), records: [], repos: [], signIns: [], claude: null, github: null });
    } catch (e) {
      get().setError(e);
    }
  },

  select(id) {
    const panes = get().panes;
    set({ selected: id, panes: id && panes.length > 1 && !panes.includes(id) ? [...panes.slice(0, -1), id] : panes });
  },
  setTab(tab) {
    set({ tab });
  },
  openBeside(id) {
    const panes = get().panes.length ? get().panes : get().selected ? [get().selected!] : [];
    if (!panes.includes(id)) set({ panes: [...panes, id].slice(-4), selected: id });
  },
  closePane(id) {
    const panes = get().panes.filter((p) => p !== id);
    set({ panes, selected: get().selected === id ? panes[panes.length - 1] || null : get().selected });
  },
  async closeSession(id) {
    // held closed from now (heldClosed): out of the list at once, and in no read while it closes listed or chosen
    closing.set(id, Infinity);
    get().closePane(id);
    const shown = (closed: boolean) =>
      set({ records: get().records.map((r) => (r.id === id ? { ...r, closed: closed || undefined } : r)) });
    shown(true);                          // this one alone: one closed before and back since stays listed
    try {
      await Api.closeSession(id);
      // closed, unless opened again meanwhile: a read begun from here on says so itself
      if (closing.get(id) === Infinity) closing.set(id, readsBegun);
    } catch (e) {
      // not closed: listed again at once, as the workspace has it, whether or not a read of the lists follows (one
      // that cannot reach the server does not, nor does the poll while the lists' stamps stay as they were)
      closing.delete(id);
      shown(false);
      get().setError(e);
    }
    await get().refresh();
  },
  async reopenSession(id) {
    closing.delete(id);
    try { await Api.openSession(id); } catch (e) { get().setError(e); }
    await get().refresh();
  },
  setError(e) {
    // the server could not be reached: the line under the title bar says so, steadily, until a read gets through
    // (App's Offline), in place of a toast said again at each poll that failed
    if (unreachable(e)) {
      if (!get().offline) set({ offline: true });
      return;
    }
    const msg = e instanceof Error ? e.message : String(e);
    set({ error: msg });
    clearTimeout(errorTimer);
    errorTimer = setTimeout(() => get().error === msg && set({ error: null }), 6000);
  },
  say(text, tone = "done", ms = 6000) {
    const notice = { text, tone };
    set({ notice });
    setTimeout(() => get().notice === notice && set({ notice: null }), ms);
  },
  addChat(session, ev) {
    const cur = get().chat[session] || [];
    if (cur.length && cur[cur.length - 1].seq >= ev.seq) return;
    const live = get().live[session];
    const after = settle(live, ev);
    set(after === live ? { chat: { ...get().chat, [session]: [...cur, ev] } }
      : { chat: { ...get().chat, [session]: [...cur, ev] }, live: { ...get().live, [session]: after } });
  },
  addDelta(session, d) {
    const cur = get().chat[session] || [];
    const live = get().live[session];
    const next = addDelta(live, d, cur.length ? cur[cur.length - 1].seq : 0);
    if (next !== live) set({ live: { ...get().live, [session]: next } });
  },
  dropLive() {
    set({ live: {} });
  },
  openFile(session, path, line) {
    set({ fileFor: { ...get().fileFor, [session]: { path, line } } });
  },
  openSearch(session, query = "") {
    set({ fileFor: { ...get().fileFor, [session]: { search: query } } });
  },
  tookFile(session) {
    if (get().fileFor[session] !== undefined) set({ fileFor: { ...get().fileFor, [session]: undefined } });
  },
  async loadChat(session) {
    const same = sameUser(get);
    try {
      // pages until the log has no more; each page starts after the last event already held
      for (let page = 0; page < 50; page++) {
        const cur = get().chat[session] || [];
        const after = cur.length ? cur[cur.length - 1].seq : 0;
        const r = await Api.chatEvents(session, after);
        if (!same()) return;              // a page of the account's chat is not kept for the one after it
        const fresh = r.events.filter((e) => e.seq > after);
        if (fresh.length) {
          const live = fresh.reduce(settle, get().live[session]);
          set({ chat: { ...get().chat, [session]: [...cur, ...fresh] }, live: { ...get().live, [session]: live } });
        }
        if (!r.more || !fresh.length) break;
      }
    } catch (e) {
      if (same()) get().setError(e);
    }
  },
}));

/** Whether the page could not reach its server at its last try, until a read gets through: what waits on the server
 *  (signing a command, a Cancel) is not offered meanwhile. */
export function useOffline(): boolean {
  return useStore((s) => s.offline);
}

type Getter = () => Store;
type Setter = (partial: Partial<Store>) => void;

/** A check that the account signed in as a read began is still the one signed in: what the read brings back is then
 *  still its own, and is kept; a read that lands after a sign-out (or another sign-in) is dropped. */
function sameUser(get: Getter): () => boolean {
  const who = get().user;
  return () => who !== null && get().user === who;
}

/** The read of `kind` under way that a read asked now joins: one asked for the account signed in now, while nothing has
 *  been written since it went out; else none. */
function underWay(kind: ReadKind, get: Getter): Promise<void> | null {
  const was = readsUnderWay.get(kind);
  return was && was.user === get().user && was.writes === writesSent() ? was.read : null;
}

/** A read of `kind`: the one under way, where a read asked now joins it (underWay); else `read`'s, which reads afresh
 *  and is the one under way until it ends. */
function joined(kind: ReadKind, get: Getter, read: () => Promise<void>): Promise<void> {
  const was = underWay(kind, get);
  if (was) return was;
  const now = { user: get().user, writes: writesSent(), read: read().finally(() => {
    if (readsUnderWay.get(kind) === now) readsUnderWay.delete(kind);
  }) };
  readsUnderWay.set(kind, now);
  return now.read;
}

/** What one account's page holds, cleared when it signs out or another account signs in on the page in its place, so
 *  nothing of one is shown to the next: its lists, its workspace's programs, providers, agents and
 *  chat choices, what its chats were writing, and what was said to it. Its unsent task in this browser goes with it. */
function forget(): Partial<Store> {
  // an unsent task is the account's: the next one to sign in on this browser never sees it
  try { localStorage.removeItem("pp-new-session-draft"); } catch { /* this page only */ }
  summaryRead = null;             // a read under way is the account's: the next sign-in reads afresh
  closing.clear();                // so are the sessions it was closing
  return {
    ws: null, repos: [], records: [], signIns: [], claude: null, github: null, approvals: [], proposals: [], proposalsOff: false,
    sessionAsks: [], grantWidenings: [], grantWideningsOff: false, asksOn: null, links: null, linksAt: null, chat: {}, selected: null, panes: [], accounts: null, quotas: {},
    history: null, agentsWaiting: 0, summary: null, summaryOff: false, read: {}, offline: false, lastRead: null,
    programs: null, held: null, modelProviders: null, customAgents: null, caps: FLOOR, live: {}, fileFor: {},
    notice: null, error: null,
    connectionsOpen: false,
  };
}

/** The workspace's repositories and sessions, read while it runs, each on its own: each kept as it comes, with the
 *  stamp it was read under noted, so that a GET /repos that is slow or fails keeps neither the sessions (a sign-in
 *  running, or ended) from showing nor their stamp from being noted, nor the other way round. The first to fail is
 *  thrown as it fails, to be said as before. */
async function readLists(set: Setter, get: Getter) {
  const same = sameUser(get);
  const stamps = get().summary?.stamps;
  const begun = ++readsBegun;
  const repos = Api.repos().then((got) => {
    if (!same()) return;
    set({ repos: got.repos });
    noteRead(set, get, stamps, ["repos"]);
  });
  const sessions = Api.sessions().then((got) => {
    if (!same()) return;
    const records = heldClosed(got.sessions.filter((r) => !isSignIn(r)), begun);
    const signIns = got.sessions.filter(isSignIn);
    if (get().claude === null) void get().refreshClaude();
    const { selected, panes } = get();
    const ids = new Set(records.map((r) => r.id));
    // a session is chosen by the person alone (from the list, the map, search, a link, or as it starts): with none
    // chosen the main panel is the control view, so a read never chooses one,
    // and a closed one is never chosen in another's place. One chosen that the workspace no longer has is
    // let go.
    set({
      records,
      signIns,
      selected: selected && ids.has(selected) ? selected : null,
      panes: panes.filter((p) => ids.has(p)),
    });
    noteRead(set, get, stamps, ["sessions"]);
  });
  await Promise.all([repos, sessions]);
}

/** The workspace does not run, so its lists are not there to read: the page keeps none (what it showed would only go
 *  stale), nor the asks of its agents. They are read again when it runs; the session chosen stays chosen till then. */
function stopped(set: Setter, get: Getter) {
  const st = get();
  if (st.records.length || st.repos.length || st.signIns.length || st.sessionAsks.length) {
    set({ records: [], repos: [], signIns: [], sessionAsks: [] });
  }
}

/** The lists just read, each noted at the stamp the summary gave before the read: what was read is at least as new. */
function noteRead(set: Setter, get: Getter, stamps: ShellSummary["stamps"] | undefined, keys: Stamped[]) {
  if (!stamps) return;
  const read = { ...get().read };
  let moved = false;
  for (const k of keys) {
    if (typeof stamps[k] === "string" && read[k] !== stamps[k]) {
      read[k] = stamps[k];
      moved = true;
    }
  }
  if (moved) set({ read });
}

/** The page's first read once it is signed in: the summary, and the lists it says to read; a server without one gives
 *  the workspace and its lists, as before. */
async function firstRead(get: Getter) {
  await get().readSummary();
  if (get().summaryOff) await get().refresh();
}
