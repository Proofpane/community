// The open edition's own API, as the ADE app uses it. The sign-in token is the one the server issues at
// /api/auth/login and the older pages keep under the same localStorage key, so one sign-in serves both.
import type { Match } from "./search";
import type { TerminalLink } from "./components/TerminalPane";
import type { ChatDelta } from "./live";
import { confirmPresence } from "./presence";
import { activeId, moveHosted, placeToken, setActive, setPlaceToken, signedOut, place, LOCAL } from "./places";

/** What the phone shows when it asks for the person before a chat's tool call is allowed (presence.ts). */
export const ALLOW_TOOL_CALL = "Allow the agent's tool call";

const TOKEN_KEY = "pp-open-token";
const SERVER_KEY = "pp-open-server";

/** The phone app's build (`vite build --mode phone`): its pages are its own, so it talks to the server named at
 *  sign-in, not to the page's own. */
export const PHONE = import.meta.env.MODE === "phone";
/** The desktop app's build (`vite build --mode desktop`): its pages are its own too, and it shows one of its places
 *  (places.ts) at a time: this Mac's workspace, whose server the app starts, or a hosted one. */
export const DESKTOP = import.meta.env.MODE === "desktop";
/** Built into an app (phone or desktop), so its server is one it is told of, never the page's own. */
export const APP = PHONE || DESKTOP;

/** The server the app talks to: "" for the page's own (the browser), in the phone app the address the person gave at
 *  sign-in, kept on the device, and in the desktop app the server of the place the window shows. */
export function server(): string {
  if (DESKTOP) return place(activeId())?.server ?? "";
  try {
    return localStorage.getItem(SERVER_KEY) || "";
  } catch {
    return "";
  }
}

/** In the desktop app, the place the window shows is this Mac's. */
export function onThisMac(): boolean {
  return DESKTOP && activeId() === LOCAL;
}

/** An address the app may be pointed at, as its origin: https, or http on this machine (a developer's server); null
 *  for anything else (a path, a user name, another scheme). */
export function serverAddress(text: string): string | null {
  let u: URL;
  try { u = new URL(text.trim()); } catch { return null; }
  const here = u.hostname === "127.0.0.1" || u.hostname === "localhost" || u.hostname === "[::1]";
  if (!(u.protocol === "https:" || (u.protocol === "http:" && here)) || u.username || u.password) return null;
  if ((u.pathname !== "/" && u.pathname !== "") || u.search || u.hash) return null;
  return u.origin;
}

export function setServer(origin: string | null) {
  if (DESKTOP) {
    // a hosted place not signed in yet takes the address the person gives on its sign-in screen
    const id = activeId();
    if (origin && id !== LOCAL && !placeToken(id)) {
      const moved = moveHosted(id, origin);
      if (moved) setActive(moved.id);
    }
    return;
  }
  try {
    if (origin) localStorage.setItem(SERVER_KEY, origin);
    else localStorage.removeItem(SERVER_KEY);
  } catch { /* private mode: the page's own server */ }
}
export const MAX_UPLOAD = 10 * 1024 * 1024;   // the workspace's own limit on a file sent into a chat

export class ApiError extends Error {
  /** `fromApp`: the answer was the app's own, a JSON object as each of its answers is, not a page from a proxy in front
   *  of it (Caddy, Fly's edge), which answers 502, 503 or 504 in its place when the app cannot be reached. */
  constructor(public status: number, message: string, public fromApp = true) {
    super(message);
  }
}

/** What a 401 says when it answers a request sent with a sign-in this page no longer keeps: the page signed out, or
 *  signed out everywhere and took the new sign-in, or another account signed in, while it was under way. */
export const OLD_SIGN_IN = "That was asked with the sign-in this page had before. Try again.";

/** Whether an answer's body is a JSON object, as each of the app's own answers is: not a page, nor a bare number, string,
 *  true or array, which is how some proxies' text reads when parsed. */
const fromApp = (data: unknown) => !!data && typeof data === "object" && !Array.isArray(data);

/** A 401 to a request sent with token `t`. It signs the page out only while `t` is still the sign-in the page keeps:
 *  one answered after the page took a new sign-in (signing out everywhere hands this device one) leaves that one
 *  alone, as it does a sign-out or another account's sign-in made meanwhile. Whether the page was
 *  signed out. */
function signedOutBy401(t: string): boolean {
  if (token() !== t) return false;
  setToken(null);
  onSignedOut();
  return true;
}

let onSignedOut: () => void = () => {};
export function whenSignedOut(fn: () => void) {
  onSignedOut = fn;
}

export function token(): string | null {
  if (DESKTOP) return placeToken(activeId());
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Whether a change another window made to the storage the page shares took its sign-in away: a browser's tabs and
 *  pop-outs of the page share one sign-in, and whatever removes it there (a sign-out, a 401 one of them was answered)
 *  removes it for each of them (each window of the desktop app keeps its own copy). */
export function signInTakenBy(e: Pick<StorageEvent, "key">): boolean {
  return !DESKTOP && (e.key === TOKEN_KEY || e.key === null) && token() === null;
}

// a pop-out of the control view's map in the desktop app (PopOut.tsx): its sign-in is a copy of its own, which a sign-out
// there (a 401 it was answered) lets go of alone, never the place every window shares, whose account the main window can
// still be signed in to, with a newer sign-in after "Sign out everywhere"
let ownCopyOnly = false;
export function signInOfThisWindowAlone() {
  ownCopyOnly = true;
}

export function setToken(t: string | null) {
  if (DESKTOP) {
    if (t) setPlaceToken(activeId(), t, !ownCopyOnly);
    else if (ownCopyOnly) setPlaceToken(activeId(), null, false);
    else signedOut(activeId());
    return;
  }
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode: the token lives as long as the page */ }
}

/** A sign-in handed to the page in its address, `#signin=TOKEN`, as the desktop app opens it (its server gives the
 *  token to the app, never to a page): kept as any sign-in is, and taken out of the address and the history at once.
 *  Only on the loopback address the desktop server listens on: anywhere else a link carrying one could sign a person
 *  in to someone else's account, and only the server that signed a token takes it. */
export function takeSignIn(loc: Pick<Location, "hash" | "hostname" | "pathname" | "search"> = location,
                           hist: Pick<History, "replaceState"> = history): boolean {
  const m = /^#signin=([A-Za-z0-9_-]{1,2000}\.[A-Za-z0-9_-]{1,200})$/.exec(loc.hash);
  if (!m) return false;
  hist.replaceState(null, "", loc.pathname + loc.search);
  if (loc.hostname !== "127.0.0.1" && loc.hostname !== "localhost" && loc.hostname !== "[::1]") return false;
  setToken(m[1]);
  return true;
}

/** A device sign-in's code in a link to the app (`#device=ABCD-EFGH`, as the tray opens it), in the form the device
 *  shows it; null for anything else. The code only fills in the device pane: approving it is still the person's
 *  click, after comparing it with the device in front of them. */
export function deviceCodeOf(hash: string): string | null {
  const m = /^#device=([A-HJ-NP-Za-hj-np-z2-9]{4})-?([A-HJ-NP-Za-hj-np-z2-9]{4})$/.exec(hash);
  return m ? `${m[1]}-${m[2]}`.toUpperCase() : null;
}

/** A request to connect an app in a link to the app (`#connect=ID`, where the server's /oauth/authorize sends the
 *  owner): its id, 32 hex digits; null for anything else. The id only opens the request: allowing it is still the
 *  owner's click, with the account's confirmation. */
export function connectIdOf(hash: string): string | null {
  const m = /^#connect=([0-9a-f]{32})$/.exec(hash);
  return m ? m[1] : null;
}

// The page's requests that are not reads (every method but GET), counted as each goes out and again as it is answered
// or fails: a read that went out before the count last moved may answer from before a change the page made (an account
// chosen, a sign-in's code), so the store joins no read asked since to it (store.ts, `joined`).
let writes = 0;
export function writesSent(): number {
  return writes;
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; raw?: Blob; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (opts.raw !== undefined) headers["Content-Type"] = "application/octet-stream";
  else if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const write = (opts.method || "GET") !== "GET";
  if (write) writes += 1;
  let resp: Response;
  try {
    const body = opts.raw !== undefined ? opts.raw : opts.body === undefined ? undefined : JSON.stringify(opts.body);
    resp = await fetch(server() + path, { method: opts.method || "GET", headers, body, signal: opts.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the server. Check your connection.");
  } finally {
    if (write) writes += 1;
  }
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!resp.ok) {
    if (resp.status === 401 && t && !signedOutBy401(t)) throw new ApiError(401, OLD_SIGN_IN, fromApp(data));
    const detail = (data && typeof data === "object" && "detail" in data && (data as { detail: unknown }).detail) || `${resp.status} ${resp.statusText}`;
    throw new ApiError(resp.status, typeof detail === "string" ? detail : JSON.stringify(detail), fromApp(data));
  }
  return data as T;
}

// --- the shapes the server sends -----------------------------------------------------------------------------

/** A device asking to be signed in, as its approver sees it: its name is its own word; the time and the addresses are
 *  the server's (`same_network` null when either address is unknown). */
export interface DeviceRequest {
  name: string;
  created_at: number;
  expires_at: number;
  asked_from?: string | null;
  viewer?: string | null;
  same_network?: boolean | null;
}

export interface User {
  id: string; email: string; admin?: boolean;
  /** a password or a passkey to sign in with after signing out: the desktop server's own account has neither */
  can_sign_in_again?: boolean;
}

/** What GET /api/health says: to anyone, the release this deployment runs (such as 58a); to a signed-in account, the
 *  commit its image was built from too; as the build kept them, each "unknown" where it kept none. */
export interface Health { ok: boolean; version: string; release?: string; commit?: string }

/** Whether signing out is offered: everywhere but the desktop app's window onto this Mac, which signs itself in. A
 *  browser signed in as this Mac's own account (which has no password) may sign out too, to end its sign-in on a device
 *  it no longer trusts; it signs in again with another device, approved in the app (NO_PASSWORD, the sign-in screen). */
export function mayOfferSignOut(): boolean {
  return !onThisMac();
}

/** Kept by a sign-out of an account with no password, for the sign-in screen to say how it signs in again. */
export const NO_PASSWORD = "pp-signed-out-no-password";

export interface WorkspaceState {
  enabled: boolean;
  allowed?: boolean;
  detail?: string;
  /** Where the account's work lives, as it chose (StorageView): a workspace is started only for `hosted`. None from a
   *  server from before the choice. */
  choice?: StorageChoiceName;
  provider?: string;
  running?: boolean;
  /** ever started, so it keeps what its sessions left; false for one never started (a server from before it says none) */
  made?: boolean;
  starting?: boolean;
  /** why the last start failed, until the next start, or until it runs */
  start_error?: string;
  region?: string;
  error?: string | null;
  [k: string]: unknown;
}

/** Where an account's new work lives: its repository, its own machine, or a
 *  workspace this deployment runs for it. */
export type StorageChoiceName = "repository" | "machine" | "hosted";

/** One of the three choices as GET /api/ade/storage gives it: offered here or not, and why not. */
export interface StorageOption {
  choice: StorageChoiceName;
  label: string;
  sentence: string;
  offered: boolean;
  why: string | null;
}

/** The account's workspace as GET /api/ade/storage gives it under the fly provider: its month against its
 *  share of the cap, when a workspace it left is kept until (a time in seconds), how many days one is kept, and the last
 *  one removed, when and why. Each null where it does not apply; an older server sends none. */
export interface StorageWorkspace {
  share: { month: string; spent_usd: number; share_usd: number } | null;
  kept_until: number | null;
  keep_days: number | null;
  removed: { removed_at: number; why: string } | null;
}

/** GET and PUT /api/ade/storage: the account's choice, whether it made it (else it is its default), the words a start
 *  is refused with now (null where it is `hosted`), the three choices, what a move to each leaves where the account's
 *  work is now, the question to turn end-to-end encryption on (`ask`), with the account's "not now" kept, and the
 *  account's workspace (StorageWorkspace). */
export interface StorageView {
  choice: StorageChoiceName;
  chosen: boolean;
  refusal: string | null;
  choices: StorageOption[];
  left_behind: Record<StorageChoiceName, string[]>;
  e2e: { on: boolean; not_now: boolean; ask: boolean };
  workspace?: StorageWorkspace;
}

export interface RepoView {
  name: string; label?: string; branch?: string | null; head?: string | null;
  /** Added from a folder on the workspace's machine and used where it is (the local provider's): the folder. */
  linked?: string | null;
  /** Its folder is no longer there (moved or deleted): only taking it out is left. */
  missing?: boolean;
  /** Whether it has a remote to push to (its origin): Ship pushes there; without one, Finish is what is offered. */
  origin?: boolean;
  /** Whether that remote is GitHub's, where Ship opens its pull request: a remote elsewhere is offered Finish too. */
  github?: boolean;
}

export interface ChatAsk { id: string; tool?: string; summary?: string; input?: unknown; at?: number; [k: string]: unknown }

export interface ChatView {
  state: string;          // idle, running, needs_you, ... as chat.py reports it
  seq: number;
  pending: string[];
  cost: number;
  chosen: Record<string, string>;
  last_text?: string | null;
  last_at?: number | null;
  asks: ChatAsk[];
}

/** One of the other providers' models (providers.py): its vendor, its model, and whether a key is kept for it. */
export interface ModelProviderView { id: string; label: string; vendor: string; docs: string; model: string; set: boolean }

export interface SessionRecord {
  id: string;
  repo: string | null;
  program: string;        // claude, codex, shell, chat; github and claude-sign-in are sign-ins
  task: string;
  /** The name the person gave it, shown in place of its task's first line (workspace.rename). */
  title?: string;
  base: string | null;
  /** The ref it started from, in full (refs/heads/dev, refs/remotes/origin/main, refs/pull/7/head), when it was one. */
  base_ref?: string | null;
  branch: string | null;
  created_at: number;
  finished: { head: string; at: number } | null;
  running: boolean;
  exit_code: number | null;
  ended_at: number | null;
  device_code?: string | null;
  sign_in_url?: string | null;
  chat: ChatView | null;
  chat_settings?: Record<string, string>;
  /** Sessions started together on one task (a fan-out), to compare. */
  group?: string;
  /** A claude session on another provider's model (Kimi K3, MiniMax M3, DeepSeek): which one. */
  model_provider?: string | null;
  /** An agent of the person's own: its name, its command as it was started, and how its task reached it. */
  custom?: { name: string; argv: string[]; task: "argument" | "typed" } | null;
  /** The Claude Code or Codex account it runs on, by its id; none for the program's own (accounts.py). */
  account?: string | null;
  /** Closed: its program stopped and itself left out of the list, nothing of it removed; an open, or a
   *  start again, takes the mark away. */
  closed?: boolean;
  /** Its change against where it started, in size, as the workspace last worked it out (and whether anything is left
   *  to commit: what a Finish would commit); none until it has. */
  diff?: { files: number; added: number; removed: number; uncommitted?: boolean } | null;
  /** A running Codex sign-in's own words (where to sign in, with what code), as plain text. */
  sign_in_text?: string | null;
  /** A sign-in the workspace started as the person asked: the one kind whose link, code or words are shown, or that
   *  takes a code (a session's own record says which program it runs, and its programs can write it). */
  sign_in?: boolean;
  /** The pull request this session started from, by its number in the repository on GitHub (workspace.prepare), and
   *  its address: as the app recorded them when the person started it, none when it did not. */
  pull?: number | null;
  pull_url?: string | null;
  /** The session whose agent asked for this one, which the person started (the agent's asks.py). */
  asked_by?: string | null;
  /** The pull request the owner shipped this session as (workspace.ship), as the app recorded it: none when it did
   *  not (a record in the session's folder alone, which its programs could have written). */
  shipped?: { url: string; at: number; commits: number; merge?: { method: string; auto: boolean; at: number } } | null;
  /** Merged into the branch it follows, in a repository added from this machine (workspace.land): where, and its head. */
  landed?: { into: string; head: string; at: number } | null;
  /** What a terminal claude said it is doing through its hooks, since its program started (the session's own word). */
  // its note, and the names of the tool and the kind of notification, which are all a notification says of it
  activity?: { state: "working" | "waiting" | "idle"; at: number; message?: string; tool?: string; kind?: string } | null;
  /** The person's own shells in the session's worktree, beside its program, by number (workspace.shell_start). */
  shells?: ShellView[];
  /** In a start's answer only: the words saying that a Claude Code session an agent asked for did
   *  not start at its effort, the installed Claude Code no longer offering it at a start again, or a workspace agent
   *  older than the app taking none at Start; the page says them. None when it started as chosen. */
  effort_notice?: string | null;
}

export interface ChatEvent { seq: number; kind: string; at?: number; [k: string]: unknown }

/** One of a session's shells: its number, when it started, and how it ended once it has. */
export interface ShellView { n: number; running: boolean; started_at: number; exit_code: number | null; ended_at: number | null }

/** A chat's MCP servers (the agent's mcp.py): the repository's own, each run only once approved as it was shown, and
 *  what the running claude says it has. Names and command lines, never the values of what a server is given. */
export interface McpDeclared { name: string; scope?: string; files?: string[]; kind?: string; line?: string; names?: string[]; approved?: boolean; conflict?: boolean; fingerprint?: string }
export interface McpLive { name: string; scope?: string; status?: string; tools?: number; error?: string; removed?: boolean; kind?: string; line?: string; names?: string[] }
export interface McpView { running?: boolean; strict?: string | null; servers?: McpDeclared[]; live?: McpLive[] }

/** A repository's open issue on GitHub, as the workspace's gh lists it (workspace.issues). */
export interface Issue { number: number; title: string; body: string; cut: boolean; url: string | null; labels: string[]; author: string | null; updated_at: string | null }
/** An open pull request as the workspace lists it (workspace.pulls), to start a session from its head. */
export interface PullListed {
  number: number; title: string; body: string; cut: boolean; url: string | null; branch: string | null;
  base: string | null; author: string | null; draft: boolean; updated_at: string | null;
}
export interface PrCheck { name: string; state: "passed" | "failed" | "running"; url: string | null }
/** A shipped session's pull request as GitHub has it now (workspace.pull_request). */
export interface PullRequest {
  session: string; url: string; number: number | null; title: string; state: "OPEN" | "CLOSED" | "MERGED" | null; draft: boolean;
  mergeable: string | null; merge_state: string | null; review: string | null; merged_at: string | null; base: string | null;
  checks: { passed: number; failed: number; running: number; items: PrCheck[] };
  /** Who opened it, as GitHub names them. */
  author?: string | null;
}

/** The person's own review of a pull request on GitHub: approve it, ask for changes, or comment. */
export type ReviewEvent = "approve" | "request_changes" | "comment";

/** Who wrote a comment on a pull request: `trusted` when they can write to the repository or are a bot installed on it;
 *  anyone at all can comment on a public repository's pull request. */
/** Who wrote something on a pull request: `trusted` when GitHub shows they can write to the repository (its owner, or
 *  a member or collaborator with write or admin permission), never for a bot. */
export interface FeedbackAuthor { author: string; bot: boolean; association: string | null; trusted: boolean }
/** One thing a shipped session's reviewers or checks said (workspace.pr_feedback): a review thread not yet resolved,
 *  with the code it is on; a review or a comment with words in it; or a failed check, with the part of its job's log
 *  that says why, when it ran in GitHub Actions and GitHub still keeps its log. */
export type FeedbackItem =
  | { kind: "thread"; path: string | null; line: number | null; outdated: boolean; url: string | null; hunk: string; at: string | null;
      trusted: boolean; comments: (FeedbackAuthor & { body: string })[] }
  | (FeedbackAuthor & { kind: "review"; state: "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED"; body: string; url: string | null; at: string | null })
  | (FeedbackAuthor & { kind: "comment"; body: string; url: string | null; at: string | null })
  | { kind: "check"; name: string; url: string | null; log: string | null; log_error: string | null; trusted: boolean };
/** `fork` when the pull request is from another repository, whose checks then ran its author's code (none trusted). */
export interface PrFeedback { session: string; url: string; number: number; fork: boolean; items: FeedbackItem[]; resolved: number }

/** A Linear issue, to start a session's task from (the agent's linear.py): the person's own, or one a search found. */
export interface LinearIssue {
  identifier: string; title: string; description: string; cut: boolean; url: string | null; state: string | null;
  state_type: string | null; team: string | null; team_key: string | null; assignee: string | null; priority: string | null;
  updated_at: string | null; labels: string[];
}
/** Whether a Linear key is kept in the workspace, and whose; never the key. */
export interface LinearStatus { set: boolean; user?: { name: string | null; email: string | null } | null; error?: string }

/** The workspace's pairing with the owner's Proofpane organisation (the agent's core_plugin.py), never the device's
 *  token; what each claude start there is given of Proofpane's core (the proofpane-core plugin: its skills and hooks,
 *  and Proofpane's MCP server where the daemon is installed); and, for each Claude Code account whose organisation's
 *  managed settings leave something out, what (`refused`: no plugin at all; `hooks_off`: its hooks do not run). */
export interface ProofpaneStatus {
  paired: boolean; plugin: boolean; name: string; skills: string[];
  cloud_url?: string; device_id?: string; device_name?: string | null; paired_at?: string | null; holds?: string[];
  mcp: { given: boolean; why: "machine" | "no daemon" | "not paired" | null };
  accounts: { account: string; refused: string[]; hooks_off: string[] }[];
}

/** A new session an agent asked the person for (the agent's asks.py): it starts only when the person starts it. The
 *  task is the agent's words, shown as text. */
export interface SessionAsk {
  /** `program` is the workspace's name for the agent asked for (kimi is Kimi Code): store.agentOfProgram names it. */
  id: string; from: string; repo: string; label: string; program: string; task: string; base: string | null; at: number;
  by: { id: string; title: string | null; program: string | null };
  /** the effort it asks for, a Claude Code ask's alone: a request, which the person may change at Start;
   *  null or absent when it names none (and from an older workspace) */
  effort?: string | null;
}
/** Whether the workspace's agents may ask for sessions, and the asks waiting for the person. */
export interface AsksView { on: boolean; asks: SessionAsk[] }

export interface FileEntry { name: string; type: "dir" | "file" | "link"; size: number | null }
export interface FolderView { path: string; entries: FileEntry[]; truncated: boolean }
/** One file of a worktree (workspace.file): its text, and for a text file its sha256 (the version a save replaces) and
 *  whether the page may edit it (only UTF-8 it can give back byte for byte). */
export interface FileView { path: string; size: number; binary: boolean; too_large: boolean; link: boolean; text: string | null; sha?: string; editable?: boolean }

export interface Window { used_percentage: number; resets_at: number | null }
/** What a claude's status line last recorded (statusline.py): the account's windows, the model, the context used. */
export interface StatusLine { at: number; model?: string; context_used?: number; five_hour?: Window; seven_day?: Window; spend_limit?: Window }
export interface Usage { available: boolean; status?: StatusLine | null; input_tokens?: number; output_tokens?: number; reason?: string;
  program?: string; conversations?: number; cached_input_tokens?: number; reasoning_output_tokens?: number; total_tokens?: number }
/** Each program's windows on the account new sessions start on (accounts.py in the workspace): Claude's status line,
 *  Codex's own files; `name` the account's when it is not the program's own. */
export type QuotaLine = StatusLine & { name?: string };
export type Quotas = { claude?: QuotaLine; codex?: QuotaLine };

export type AccountKind = "claude" | "codex";
export interface CodexStatus { installed: boolean; signed_in: boolean | null; method?: "chatgpt" | "api_key" | null }
/** One sign-in to Claude Code or Codex in the workspace: the program's own ("default") or one the person added, each in a
 *  folder of its own there. `status` is null when the workspace was not asked it (the title bar's frequent look). */
export interface AccountRow {
  id: string;
  name: string;
  created_at: number | null;
  sessions: number;
  status: ClaudeStatus | CodexStatus | null;
  usage: { at: number; five_hour?: Window | null; seven_day?: Window | null } | null;
}
export interface AccountsView { claude: { active: string; accounts: AccountRow[] }; codex: { active: string; accounts: AccountRow[] } }

/** Whether a workspace's claude terminals and chats hold their gated commands for the approval queue
 *  (workspace.held): "held", "refused" (its hook is missing a part, so each is refused), or "none". */
export type Held = "held" | "refused" | "none";

/** The programs a workspace session is continued in, and from: a terminal Claude Code or Codex, or a chat. */
export type HandoverProgram = "claude" | "codex" | "chat";
/** One Continue in a new session, as the app kept it: the session it continued and the one it started, the commit the
 *  Finish made, the program and account chosen, and how many commits and files the first prompt named. */
export interface HandoverRow { id: string; from_session: string; to_session: string; head: string; program: HandoverProgram; account: string; finish_id: string | null; commits: number; files: number; at: number }
export interface HandoverView { task: string | null; continued_from: HandoverRow | null; continued_in: HandoverRow[] }

export interface ClaudeStatus { installed: boolean; signed_in: boolean | null; email?: string | null; method?: string | null; subscription?: string | null }

export interface GithubRepo { full_name: string; private: boolean; url: string; description: string; cloned: boolean; pushed: string | null }
export interface GithubStatus { installed: boolean; user: string | null; repos: GithubRepo[]; capped: boolean }

/** Claude's sign-in link, only when it is Claude's own OAuth page: the page never opens an address a program printed
 *  unless it is that one (the account pages check the same). */
export function claudeSignInLink(url: unknown): string | null {
  return typeof url === "string" && (url.startsWith("https://claude.com/cai/oauth/authorize?") || url.startsWith("https://claude.ai/oauth/authorize?")) ? url : null;
}
export const GITHUB_DEVICE_PAGE = "https://github.com/login/device";

/** A command a session's hook holds for the owner's answer (ade/approvals.py's view): `token_name` the hook token's
 *  name, `until` when it stops waiting (unix seconds). */
export interface HookRequest { id: string; command: string; tool?: string; session?: string | null; cwd?: string | null; state: string; created_at: string | number; token_name?: string; until?: number; [k: string]: unknown }

/** A merge an update started that stopped on conflicts: the branch merged, its commit, and the files in conflict. */
export interface UpdateMerging { ref: string; target: string; at: number; conflicts: string[] }

/** How far a session is behind the branch it started from, as last known (no fetch): `ref` null, with why, when it
 *  has none to update from. */
export interface UpdateStatus { ref: string | null; behind: number; merging: UpdateMerging | null; why?: string }

/** What bringing a session up to date did: merged (`merged` commits, `base` the new base), nothing new, or stopped. */
export interface UpdateResult extends UpdateStatus { updated: boolean; merged?: number; base?: string; note?: string | null }

/** An agent of the person's own (custom_agents.py): any command-line agent, kept by the app on the account. */
export interface CustomAgent {
  id: string; name: string; command: string; task: "argument" | "typed";
  /** whether the workspace finds its program; null while the workspace is not running (custom_agents.py) */
  available?: boolean | null;
}

export interface DiffReport {
  base: string;
  branch: string | null;
  diff: string | null;
  too_large: boolean;
  files: { path: string; added: number | null; removed: number | null }[];
}

const W = "/api/ade/workspace";

/** The session channel (the open edition's ade/link.py): the owner's coding sessions, linked to the ADE, post their
 *  steps, questions and a checkpoint there. Everything a session posts is data, shown as text. */
export interface LinkEvent {
  seq: number;
  time: string;              // ISO, when the ADE received it
  kind: string;              // step, question, note, ...
  from: string;
  to: string;
  ref: number | null;
  /** its words; or, where the account's encryption is on, their sealed text ("e2e1." and more, e2e.ts) */
  text: string;
  /** the id the link that posted it gave it: part of what a sealed text is bound to */
  client_id?: string;
  /** the server sealed it as encryption was turned on, its link having sent it in the clear: an answer to such a
   *  question carries the digest of the words its session asked, which that session keeps, not of the sealed text */
  sealed_in_place?: boolean;
  needs_turn?: boolean;
  closed_by?: string | null;
  from_id?: string;
  to_id?: string | null;
  /** a command from the owner (an instruction or an answer): where it stands, and when it lapses */
  state?: "queued" | "taken" | "answered" | "expired" | "withdrawn";
  expires_at?: number | null;
  taken_at?: number | null;
  withdrawn_at?: number | null;
  renewed_at?: number | null;
  signed?: boolean;
  /** a note the session's mirror posted, and whose words it reports: what the owner typed there, its own, or the
   *  mirror's word on what it left out (the session's report, posted by its link) */
  // "step": the calls the session made, as a Stop posts them (and an earlier client's live view did); "live": a round
  // of its live view, the turn since the last in one note, its items in order
  mirror?: "owner" | "session" | "info" | "step" | "live";
  /** the session a mirrored note is of, where its machine registered that session: the note is that session's
   *  conversation's, not its link's own */
  session?: string;
  /** a round of the live view (mirror "live") that a Stop of its session posted again whole, as the ADE says: the
   *  page leaves it out, so each turn shows once */
  posted_again?: boolean;
  said_at?: number;          // when its first words were said there, in milliseconds
  /** a prompt's or a proposal's close: the owner's answer, his close, the session's own report (report:EVENT), its
   *  deadline, or (a proposal) his signed instruction; null while it is open */
  closed_reason?: string | null;
  closed_at?: number | null;
  /** an answer of the owner's to a prompt: never a command for the model, claimed by the prompt hook alone */
  for_prompt?: boolean;
}
/** A linked session's report of a prompt Claude Code showed it, as the sessions
 *  view lists it while it is open and not hidden: the session that posted it, its deadline (the server's clock, unix
 *  seconds), and whether it is an item to look at, which nothing in the list decides. Its text is the session's report,
 *  sealed to the account where the owner may answer it. */
export interface LinkPrompt extends LinkEvent {
  link_id: string;
  deadline_at: number;
  look: boolean;
}
/** How a prompt in the hidden and closed list ended: `waiting`, hidden by the owner's Close and still waiting for his
 *  answer; by his signed answer, `approved`, `refused` (his "Deny") or `words` (his own words); `deadline`, its deadline
 *  passed with no answer of his, which is all the server knows (not what its session's hook did then); `report`, closed
 *  by its session's report; `closed`, closed on the page, as a Close closes an item to look at, and closed a prompt
 *  before it hid one. */
export type LinkPromptEnded = "waiting" | "approved" | "refused" | "words" | "deadline" | "report" | "closed";
/** A prompt of the account's, as GET /api/ade/link/prompts/closed lists it: one the owner hid that still waits, or one
 *  closed in the last 24 hours. The view an open prompt has, and when he hid it (null where he did not), when and why
 *  it closed (null while it waits), whether it still waits, answerable until its deadline, and how it ended. */
export interface LinkClosedPrompt extends LinkPrompt {
  hidden_at: number | null;
  closed_at: number | null;
  closed_reason: string | null;
  open: boolean;
  ended: LinkPromptEnded;
}
/** A linked session's proposal of a message to another of the owner's sessions, while it is open: the session that
 *  posted it (`link_id`, `from`), the one it is for (`for_id`, `for`, its name), and its deadline. Its words are
 *  sealed to the owner, and reach that session only as his own signed instruction. */
export interface LinkProposal extends LinkEvent {
  link_id: string;
  for_id: string;
  for: string | null;
  deadline_at: number;
}
/** A linked session's report of its own Claude Code session (ade/link.py, _report_check): its context used of its
 *  window, its model and its rate limit, where its client read them; its task list and its background tasks and
 *  agents, in the clear, or `sealed` as one envelope (e2e.ts, reportedSlot) while encryption is on. */
export interface LinkReported {
  usage?: { context_used?: number; context_window?: number; model?: string;
    rate_limit?: { state: "ok" | "limited"; resets_at?: number } };
  tasks?: ReportedTask[];
  background?: ReportedBackground[];
  sealed?: string;
}
export interface ReportedTask { text: string; state: "pending" | "in_progress" | "completed" }
export interface ReportedBackground {
  id: string; kind: "shell" | "agent"; text: string; state: "running" | "completed" | "failed" | "killed" | "stopped";
}
export interface LinkView {
  id: string;
  name: string;
  kind: string;              // cloud or local
  /** its key's fingerprint, as the session prints it: another once a pairing by ticket moves the link to a new key */
  fingerprint?: string;
  machine?: string | null;   // its machine's own random id, as its History list last said it: links of one machine
  session_ref: string | null;
  interval_min: number;
  created_at: number;        // unix seconds, as are the times below
  last_seen_at: number | null;
  last_heartbeat_at: number | null;
  /** when it was last heard: the later of the last request it signed, of any kind, and its last heartbeat;
   *  none from an older server */
  last_heard_at?: number | null;
  /** how it is heard: "hook" where its hook was heard lately; "keeper" where its client's background
   *  heartbeat keeps it heard while its session runs (a cloud session runs no hook of the ADE's); "client" where it is
   *  heard only when its session calls its client; null where the server cannot tell (a client older than the marks),
   *  none from an older server */
  heard_through?: "hook" | "keeper" | "client" | null;
  /** its newest step and its live view's newest round, its own, which its card shows where it has no checkpoint;
   *  none from an older server */
  last_step?: LinkEvent | null;
  last_live?: LinkEvent | null;
  silent: boolean;           // not heard from for two of its intervals
  acked_seq: number;
  checkpoint: Record<string, unknown> | null;
  checkpoint_version: number;
  checkpoint_at: number | null;
  /** what its client last reported of its own Claude Code session with a heartbeat (ade/link.py,
   *  heartbeat), and when; none from a client that reports nothing, or from an older server */
  reported?: LinkReported | null;
  reported_at?: number | null;
  events: number;
  last_event: LinkEvent | null;
  /** whether the owner lets it read the other sessions' whole checkpoints (any session sees who else is linked, when
   *  each was heard and what each is doing) */
  reads_others?: boolean;
  /** whether the owner lets it propose messages to his other sessions (off unless he turns it on) */
  proposes?: boolean;
  /** the owner's stop: held by the session's own hook until a resume the owner signs */
  stopped_at?: number | null;
  stop_acked_at?: number | null;
  resumed_at?: number | null;
  /** the owner's newest commands to it, any state */
  commands?: LinkEvent[];
}
/** A session's request to be linked, as its owner sees it (the open edition's ade/link_asks.py): what it calls itself,
 *  its kind, its key's fingerprint, the address it asked from and when; once approved, its hold (when the link is
 *  made, and how many devices its notice was sent toward); approved and not made, why. */
export interface LinkAsk {
  id: string;
  name: string;
  kind: string;              // cloud or local, as the session says
  fingerprint: string;       // the first 32 hex characters of the sha256 of its key, as the session prints it
  asked_from: string | null; // the network address it asked from, as the server saw it; null when it could not read one
  created_at: number;        // unix seconds, as are the times below
  expires_at: number;
  session_ref: string | null;
  state: "pending" | "held" | "failed";
  hold_id: string | null;
  takes_effect_at: number | null;
  told: number | null;       // the devices its notice was sent toward; -1 when it could not be sent
  failed: string | null;
  /** waiting: the live cloud link of its name it may replace, a cloud session's request (Approve and replace names
   *  it); approved: the link its approval replaces, revoked when the hold ends, or the link a ticket's pairing moves
   *  to its new key; else null */
  replaces?: LinkReplaced | null;
  /** "ticket": a session that paired by a ticket, approved as it paired, linked when its hold ends; null:
   *  a request to an ask address */
  via?: "ticket" | null;
  ticket_id?: string | null; // the ticket it paired by, as the ticket's answer named it
}
/** The link a request replaces, or may, as the page shows it beside the request. */
export interface LinkReplaced {
  id: string;
  name: string;
  created_at: number;        // unix seconds, as are the times below
  last_seen_at: number | null;
  fingerprint: string;       // of its key, as the request's own
  revoked_at: number | null;
}
export interface LinkSessions {
  links: LinkView[]; questions: (LinkEvent & { link_id: string })[]; public_url: string;
  /** the open waitings to the owner, or to all, that refer to no command, oldest first: each told to his
   *  devices once, until its session posts again or he dismisses it */
  waitings?: (LinkEvent & { link_id: string })[];
  /** the open prompts the linked sessions reported, those to look at too, oldest first; one the owner hid is left out
   *  (closedLinkPrompts lists it) */
  prompts?: LinkPrompt[];
  /** the seqs of the account's prompts a Close hid that are still open: never counted as waiting (he hid them), but
   *  Approvals' empty state says one waits below; none from a server from before it */
  hidden_waiting?: number[];
  /** the open proposals the linked sessions made */
  proposals?: LinkProposal[];
  asks?: LinkAsk[];
  connect_hold?: number;     // the seconds a pairing, or an approval of a request to link, waits before the link is made
  server_time?: number;      // the server's time as it answered: the countdowns run by it, not this browser's clock
  /** a ticket's pairings that ended in the last ten minutes, linked or cancelled, by their ticket's id: how the ticket's
   *  box learns what became of its pairing once it is no longer listed */
  ended_pairings?: { ticket_id: string; state: "linked" | "cancelled" }[];
  move_hold?: number;        // the seconds a move waits before its machine may take it
  /** the moves that wait on their holds, and those whose holds ended in the last half hour without their being sent,
   *  oldest first, each with its session's title and its link's name */
  held_moves?: LinkMove[];
}
/** One Claude Code session of a linked machine, as its link listed it for the History list. */
export interface HistorySession {
  id: string;
  title: string | null;      // null when withheld
  withheld?: boolean;        // it touched a project its machine's history.json excepts: title, branch and words stay there
  project: string | null;
  branch: string | null;
  client?: "vscode" | "terminal" | "desktop" | "sdk" | "other" | null;   // where it ran
  live?: "working" | "open" | null;   // running when its machine last listed it: working, or open and waiting
  started_at: number | null;
  last_at: number | null;
  link_id: string;
  link: string;              // the linked session whose machine listed it
  kind: string;              // that link's kind: local (a machine of the owner's), or cloud
  listed_at: number;
  talk_at?: number | null;   // when its conversation was last sent here; none when it is not kept here
  machine?: string | null;   // its link's machine's own id
  registered?: boolean;      // it ran this machine's start hook in its process, as of its machine's last list
  repositories?: string[];   // the repositories it worked in, first first, those its machine's history.json lists
  notes?: number;            // its mirrored notes kept here
  /** where the account's encryption is on: its title, folder, branch and repositories (its folder alone, withheld) as
   *  one sealed text, in place of those fields, which come empty */
  sealed?: string | null;
}
/** A listed session's conversation as its machine sent it: what the owner typed there and the session's words. */
export interface HistoryTalk {
  /** at: a message's last entry's time in seconds; ms: its first entry's, in milliseconds, where it was sent */
  messages: { who: "owner" | "session"; text: string; at: number | null; ms?: number }[];
  earlier: number;           // the messages before these, not sent
  updated_at: number;
  /** a post-quantum account's conversation: the key its messages are sealed under, sealed in form 2 to a content key,
   *  and the key_id that names it; null or absent for one sealed message by message */
  key?: string | null;
  key_id?: string | null;
}
/** A move of one of a machine's sessions into the hosted workspace (the server's ade/moves.py): signed as a command and
 *  held first (its hold: it waits, a notice goes to the owner's devices, and Cancel stops it), then stored and taken by
 *  the session's link, whose client sends its conversation and repository, then started in the workspace. Until it is
 *  stored nothing of it reaches the link: its seq is null. */
export interface LinkMove {
  seq: number | null;        // null while it is held, and when its hold ended without its being stored
  hold_id?: string;          // a move not stored: its hold, which Cancel stops while it is held
  link_id: string;
  session: string;
  /** held (it waits on its hold), cancelled (while it was held: nothing was sent), asked (stored, not taken), taken (its
   *  link is sending it), uploaded (it waits for the workspace), starting (being handed to it), started, failed (with
   *  why), expired */
  state: "held" | "cancelled" | "asked" | "taken" | "uploaded" | "starting" | "started" | "failed" | "expired";
  created_at: number;        // unix seconds, as are the times below
  expires_at: number;        // the last time its link may take it
  takes_effect_at?: number | null;    // held: when its link may take it, unless it is cancelled first
  told?: number | null;      // held: how many devices its notice went to
  taken_at?: number | null;
  uploaded_at?: number | null;
  updated_at?: number;
  workspace_session?: string | null;  // the workspace's session, once it started there
  reason: string | null;     // why it failed or expired, or why an uploaded one is not started yet
  title?: string | null;     // in held_moves: the session's title as its link listed it, and the link's name
  link?: string | null;
  branch?: string | null;    // what its link said of its repository
  head?: string | null;
  changes?: string | null;
  bundle?: boolean | null;
}

/** The Claude app connector (the server's oauth.py and ade/connector.py): whether the deployment and the account let
 *  apps connect, and the address an app is added with (the one the ADE answers for, not this page's). */
export interface ConnectorSetup {
  on: boolean;
  account_on?: boolean;
  url?: string;
  redirects?: string[];
  client_hosts?: string[];
  hold_seconds?: number;
}

/** A request to connect an app, as the consent pane shows it: what the app calls itself (its own word), where its
 *  sign-in returns and where its description was read (what the server knows), what it asks for, and where the request
 *  came from as the server saw it: whether that is this browser's network (null when either address is unknown);
 *  whether this browser is the one that made it, which holds the cookie the server set for it (null for a request
 *  kept with none); and how its browser said it arrived: its Sec-Fetch-Site, the origin of the page
 *  it came from, and whether that was a cross-site navigation from a page of the site the app's sign-in returns to. */
export interface ConnectRequest {
  id: string;
  account: string;
  account_id: string;          // the account the pane shows, sent back with Allow
  account_on: boolean;
  client_name: string;
  described_at: string | null;
  returns_to: string;
  loopback: boolean;
  port: number | null;         // a loopback sign-in's port
  scopes: string[];
  asked_at: number;
  asked_from: string | null;
  viewer: string | null;       // this browser's address, as the server sees it
  same_network: boolean | null;
  same_browser?: boolean | null;
  fetch_site?: "cross-site" | "same-site" | "same-origin" | "none" | null;
  came_from?: string | null;   // the origin of the page it came from (its Referer), when the browser said
  from_app_page?: boolean;
  expires_at: number;
  hold_seconds: number;
  lasts_days: number;
}

/** A connected app, as Connected apps lists it. */
export interface ConnectedApp {
  id: string;
  client_name: string;
  returns_to: string;
  described_at: string | null;
  scopes: string[];
  /** connecting (its code not collected yet), waiting (on its hold), active, ended (its 90 days) or revoked */
  state: "connecting" | "waiting" | "active" | "ended" | "revoked";
  allowed_at: number;
  connected_at: number | null;
  takes_effect_at: number | null;
  hold_id: string | null;
  ends_at: number;
  revoked_at: number | null;
  revoked_why: string | null;
  last_used_at: number | null;
  last_net: string | null;
  proposals_waiting: number;
}

/** One of the account's connector events: a use of an app's tokens, or a change to its connection. */
export interface ConnectorEvent { id: number; at: number; kind: string; what: string | null; subject: string | null; outcome: string; net: string | null }

/** What a connected app proposed (the server's ade/proposals.py): it does nothing until the owner signs or accepts it
 *  here. Its words are the app's; its reason is the app's own word. */
export interface Proposal {
  id: string;
  kind: "instruction" | "answer" | "approval" | "stop" | "resume";
  state: "proposed" | "sent" | "refused" | "expired" | "withdrawn" | "void";
  target: string;
  session: string | null;
  link_id: string | null;
  app: string | null;
  grant_id: string;
  text: string | null;
  decision: "approve" | "deny" | null;
  why: string | null;
  made_at: number;
  expires_at: number;
  decided_at: number | null;
  seq: number | null;
  edited: boolean;
  said: string | null;
  ref: number | null;
  request_id: string | null;
  question?: string | null;
  /** An answer's question was sealed end to end (encryption turned on since): the server gives no words for it, and
   *  the proposal is void once read again. */
  question_encrypted?: boolean;
  stopped_at?: number | null;
}

/** What connected apps proposed that needs the owner, as the badge counts it: each waiting proposal but an answer to a
 *  held command, which is counted as the command it is shown on. */
export function proposalAlerts(ps: Proposal[] | null | undefined): number {
  return (ps ?? []).filter((p) => p.state === "proposed" && p.kind !== "approval").length;
}

/** What the channel has that needs the owner: each silent session, each open question to the owner or to all, each
 *  open waiting to him that refers to no command, and each session waiting for an answer to its request to
 *  be linked. */
export function channelAlerts(s: LinkSessions | null | undefined): number {
  if (!s) return 0;
  return s.links.filter((l) => l.silent).length + s.questions.filter((q) => q.to === "owner" || q.to === "all").length
    + (s.waitings ?? []).length + (s.asks ?? []).filter((a) => a.state === "pending").length;
}

/** A question to the owner, or to all: one his inbox holds (one to another session is that session's). */
export const toOwner = (q: Pick<LinkEvent, "to">) => q.to === "owner" || q.to === "all";

/** What the link channel holds for the owner's one list (Approvals): its open prompts, those to look at too, its open
 *  proposals, and its open questions to the owner. Of one linked session, with `linkId`: what it posted. */
export function linkWaiting(s: LinkSessions | null | undefined, linkId?: string): number {
  if (!s) return 0;
  const its = (x: { link_id: string }) => linkId === undefined || x.link_id === linkId;
  return (s.prompts ?? []).filter(its).length + (s.proposals ?? []).filter(its).length
    + s.questions.filter((q) => toOwner(q) && its(q)).length;
}

/** The asks for more in a task's grant that the linked session `linkId` made and that wait for the owner's answer in
 *  Approvals: what its own count of what waits adds to linkWaiting. */
export function linkGrantAsks(widenings: GrantWideningView[] | null | undefined, linkId: string): number {
  return (widenings ?? []).filter((w) => w.state === "waiting" && w.requester.kind === "link"
    && w.requester.id === linkId).length;
}

/** The lists the page's shell keeps, each read again when the summary's stamp for it moves. */
export type Stamped = "sessions" | "repos" | "approvals" | "proposals" | "asks" | "links" | "history";

/** What the page's shell shows, in one read (the server's ade/summary.py): the workspace's state (null, with why, when
 *  its provider could not say); the counts the title bar's badges show (proposals as the badge counts them, null with
 *  no connector; asks null while the workspace does not run; agents null where it gives the account no workspace); and
 *  each list's stamp, which moves when the list does and says nothing of what it holds (null: not read this time). */
export interface ShellSummary {
  workspace: WorkspaceState | null;
  workspace_error?: string;
  approvals: number;
  proposals: number | null;
  asks: number | null;
  links: { live: number; alerts: number };
  agents: number | null;
  stamps: Partial<Record<Stamped, string | null>>;
}

// --- an agent's tasks, as the app's ade/tasks.py gives them --------------------------------------------------

export type TaskState = "ready" | "running" | "suspended" | "needs_owner" | "offering" | "handed_over" | "returning" | "done" | "stopped";
export interface BackendRef { kind: "account" | "key"; id?: string; provider?: string }
export interface IdentityReport { email: string; organization: string | null; plan: string; token_source: string | null; api_key_source: string | null; api_provider: string }
export interface WindowView { window: string; status: string; resets_at: number | null; utilization: number | null; at: number; overage_status?: string | null }
export interface HoldRef { id: string; kind: string; takes_effect_at: number }
export interface AccountOwnerView { id: string; account: string; name: string | null; report: IdentityReport | null; org_id: string | null; mismatch: string | null; state: "reported" | "held" | "confirmed" | "changed"; grp: string | null; grp_kind: "personal" | "team" | "enterprise" | null; org_kind?: "personal" | "team" | "enterprise" | null; position: number; in_relays: boolean; reserve_lifted: boolean; ready: boolean; windows: WindowView[]; hold: HoldRef | null }
export interface RelayAccountView { account: AccountOwnerView; order: number; eligible: boolean; left_out: string | null }
export interface TaskStepView { n: number; objective: string; check: string | null; state: "todo" | "doing" | "done"; evidence: "checked" | "said_done" | null; checkpoint_id: string | null; attempts: number; reopened: boolean }
export interface TaskCheckpointView { id: string; n: number; step: number; kind: "zero" | "progress" | "step"; head: string | null; digest: string; created_at: number; left_out: string[] }
export interface TaskEffectView { id: string; run_id: string; call_id: string; tool: string; kind: string; target: string | null; state: "done" | "not_done" | "unknown" | "ran"; exit_status: number | null; resolved: "page" | "workspace" | null; note: string | null; unseen: boolean; at: number }
export interface TaskAttemptView { run_id: string; step: number; attempt: number; fence: number; backend: BackendRef; state: string; cause: string | null; reason: string | null; resume: { outcome: string; check: string | null; found: string | null } | null; served_model: string | null; claude_version: string | null; started_at: number | null; ended_at: number | null; packet_digest: string | null }
export interface TaskMoveLine { seq: number; at: number; kind: "relay" | "crossing" | "home" | "custody" | "return"; from: string | null; to: string; why: string | null; point: number | null; cases: string[] }
export interface TaskView { id: string; agent_id: string; title: string; goal: string; state: TaskState; cause: string | null; reason: string | null; waits_until: number | null; state_version: number; fence: number; step: number; backend: BackendRef; home_grp: string; bound_org: string | null; between_steps: "wait" | "checked" | "any"; relay: boolean; auto_resume: boolean; cross_groups: boolean; custody_cross: boolean; custody_confirmed: boolean; crossings_ended: boolean; received: boolean; returned: boolean; continued_from: { task: string; offer: string } | null; repo_address: string | null; protected: string[]; external: string[]; steps: TaskStepView[]; decisions: { n: number; text: string; author: "owner" | "teammate"; at: number }[]; attempts: TaskAttemptView[]; checkpoints: TaskCheckpointView[]; effects: TaskEffectView[]; relay_set: RelayAccountView[]; moves: TaskMoveLine[]; holds: HoldRef[]; local_provider: boolean; offer: { id: string; state: string; teammate: string | null } | null;
  /** its grant, what waits to widen it, and what a grant of it names: absent from an older server */
  grant?: TaskGrantView | TaskGrantNoneStands | null; grant_widenings?: GrantWideningView[]; grant_for?: GrantFor }
export interface TaskSettings { relay: boolean; auto_resume: boolean; teammate: boolean; cross_groups: boolean; local_provider: boolean }
// --- a task's grant (the app's ade/task_grants.py): the owner's statement of a task's scope, signed with a passkey
/** A repository a grant names, by its name in the workspace: the branches it may push and those a pull request of it
 *  may merge into. */
export interface GrantRepository { repo: string; push: string[]; merge: string[] }
export type GrantRung = "L2" | "L2-high" | "L3";
/** A grant's scope: each list sorted and naming each entry once, as the server holds it, so one scope has one form. */
export interface GrantScope {
  repositories: GrantRepository[]; environments: { name: string; rung: GrantRung }[];
  secrets: { name: string; lease_seconds: number }[]; egress: string[]; lease_end: number;
  rungs: { merge: GrantRung; deploy: GrantRung };
}
/** Who asked for a version, or for more: the owner, the task's agent, or a linked session; in the owner's list, whether
 *  that linked session was revoked since, so that its ask is not signed. */
export interface GrantRequester { kind: "owner" | "agent" | "link"; id?: string; name?: string | null; revoked?: boolean }
/** A version of a task's grant withdrawn as the passkey that signed it went (the app's task_grants.passkeys_going):
 *  removed, or not a hardware key as high security was turned on. */
export interface GrantWithdrawn { version: number; digest: string; why: "removed" | "high_security"; at: number }
/** Where the next version of a task's grant follows: after the newest kept, withdrawn or not, naming its digest. */
export interface GrantNext { version: number; prev: string }
/** The task's grant as the page shows it: the version that stands (its newest version not withdrawn), the head of what
 *  the attempt's message names (`digest`), and whether it pins the task's goal as it stands now; the versions newer
 *  than it, withdrawn; and where the next version follows. */
export interface TaskGrantView extends GrantScope {
  version: number; digest: string; signed_at: number; requester: GrantRequester; widening: string | null; ask: string;
  ask_current: boolean; lease_ended: boolean; passkey: string | null; withdrawn: GrantWithdrawn[]; next: GrantNext;
}
/** A task's grant whose every version was withdrawn: no version stands, and nothing of a scope is said. */
export interface TaskGrantNoneStands { version: null; digest: null; withdrawn: GrantWithdrawn[]; next: GrantNext }
/** An ask for more in a task's grant, waiting for the owner's passkey: what it adds, the whole scope it would make,
 *  and what the grant that signs it names (the version it widens and its digest, the task's ask); and the scope of the
 *  grant that stands (null where none does) and the task's goal as it stands (absent from an older server). */
export interface GrantWideningView {
  id: string; task_id: string; task_title: string | null; requester: GrantRequester; base_version: number;
  base_digest: string; ask: string | null; adds: Omit<GrantScope, "lease_end" | "rungs"> & { lease_end: number | null; rungs: GrantScope["rungs"] | null };
  scope: GrantScope; state: "waiting" | "signed" | "refused" | "expired" | "void"; created_at: number; expires_at: number;
  decided_at: number | null; version: number | null; stands?: GrantScope | null; goal?: string | null;
}
/** What a grant of the task names, for the page to compose one: its repository (null for none), its own branch, its
 *  ask. */
export interface GrantFor { repo: string | null; branch: string; ask: string }
export interface AccountSnapshot { ref?: BackendRef; email?: string | null; plan?: string | null; organization?: string | null; org_id?: string | null; vendor?: string; label?: string; teammate?: string | null }
export interface AccountRecordEntry { seq: number; kind: "attempt" | "crossing" | "handoff" | "deleted" | "regrouped"; run_id: string | null; step: number; fence: number | null; via: "offer" | "custody" | null; repo_address: string | null; branch: string; title: string | null; at: number; accounts: AccountSnapshot[]; reduced_at: number | null; statement: { text: string; at: number } | null }
export interface AccountRecordView { task_id: string; agent_id: string; repo_address: string | null; branch: string; title: string | null; deleted_at: number | null; record_days: number | null; entries: AccountRecordEntry[] }
export interface RiskPartView { case: string | null; text: string }
export interface RiskQuotationView { id: string; case: string | null; source: string; section: string | null; address: string; source_date: string | null; read_on: string | null; words: string | null; unread: string | null }
export interface RiskTextView { point: number; title: string; design: RiskPartView[]; quotations: RiskQuotationView[]; unread: boolean; digest: string }
export interface RiskTextsView { texts: RiskTextView[] }

/** A step as the New task form and an edit send it: its objective, and the command that checks it, or none. */
export interface TaskStepInput { objective: string; check: string | null }
/** A new task (POST /api/ade/agents/tasks): its agent, its words and steps, what it may not touch and what reaches
 *  outside, and the backend it starts on. */
export interface NewTask { agent_id: string; title: string; goal: string; steps: TaskStepInput[]; protected: string[]; external: string[]; backend: BackendRef; between_steps?: TaskView["between_steps"] }
/** An edit of a task, made against the version the page read (a 409 when it moved since). */
export interface TaskEdit { state_version: number; title?: string; goal?: string; steps?: TaskStepInput[]; protected?: string[]; external?: string[]; between_steps?: TaskView["between_steps"] }
/** The task's controls, each a POST to /api/ade/agents/tasks/{id}/<action>. */
export type TaskAction = "start" | "continue" | "continue-on" | "next-on" | "stop" | "switches" | "resolve" | "end" | "answer";
/** One event of a task's journal, as GET .../journal gives it: its kind and payload. */
export interface TaskEvent { seq: number; kind: string; run_id: string | null; fence: number | null; at: number; payload: Record<string, unknown> }
/** An earlier attempt's own words in a packet: a model's, marked by its attempt, cut and its hidden characters counted
 *  where the app did so. */
export interface PacketNote { by: "model"; attempt: number | null; text: string; cut?: boolean; hidden_removed?: number }
/** The packet an attempt of a task is served (`ade.resume.v1`) as the app keeps it, each part by who wrote
 *  it: the app's schema, nonce, header and next; the owner's instructions (a teammate's, with their address, on a task
 *  received from one); and `information`, never an instruction: what the workspace reported and the app recorded as it
 *  queued the attempt (its `attempt` among them: run, number and fence), and what earlier attempts wrote. Only the
 *  members the page reads by name are typed; the rest it shows as they came. */
export interface ResumePacket {
  schema: string; nonce: string; header: string;
  instructions: { by: "owner" | "teammate"; from?: string } & Record<string, unknown>;
  information: { attempt?: { run: string; n: number; fence: number }; open_questions?: PacketNote[]; notes?: PacketNote[]; dropped_notes?: number } & Record<string, unknown>;
  next: { step: number; action: string };
}
/** GET /tasks/{id}/packets/{run_id}: the packet and its
 *  digest, the hex sha256 of "proofpane-resume/1", a 0 byte and its RFC 8785 bytes. */
export interface TaskPacketView { run_id: string; digest: string; packet: ResumePacket }

const T = "/api/ade/agents";

export const Api = {
  login: (email: string, password: string) => api<{ token: string; user: User }>("/api/auth/login", { method: "POST", body: { email, password } }),
  me: () => api<User>("/api/auth/me"),
  health: () => api<Health>("/api/health"),

  summary: () => api<ShellSummary>("/api/ade/summary"),
  workspace: () => api<WorkspaceState>(W),
  startWorkspace: () => api<WorkspaceState>(`${W}/start`, { method: "POST" }),
  stopWorkspace: () => api<WorkspaceState>(`${W}/stop`, { method: "POST" }),
  storage: () => api<StorageView>("/api/ade/storage"),
  chooseStorage: (choice: StorageChoiceName) => api<StorageView>("/api/ade/storage", { method: "PUT", body: { choice } }),
  storageNotNow: () => api<StorageView>("/api/ade/storage", { method: "PUT", body: { e2e_not_now: true } }),
  /** Delete the workspace the account left behind at once, with a confirmation made for that (workspace-delete). */
  deleteWorkspace: (confirmation?: string) =>
    api<StorageView>("/api/ade/storage/workspace", { method: "DELETE", body: { confirmation } }),
  /** The sign-in of the account new Claude sessions start on (its `account`: "default", or an id). */
  claude: () => api<ClaudeStatus & { account?: string }>(`${W}/claude`),
  accounts: (status = true) => api<AccountsView>(`${W}/accounts${status ? "" : "?status=false"}`),
  addAccount: (kind: AccountKind, name: string) =>
    api<{ id: string; kind: AccountKind; name: string; created_at: number }>(`${W}/accounts`, { method: "POST", body: { kind, name } }),
  chooseAccount: (kind: AccountKind, account: string) =>
    api<{ kind: AccountKind; active: string }>(`${W}/accounts/choose`, { method: "POST", body: { kind, account } }),
  accountSignOut: (id: string, kind: AccountKind) =>
    api<unknown>(`${W}/accounts/${encodeURIComponent(id)}/sign-out`, { method: "POST", body: { kind } }),
  removeAccount: (id: string) => api<{ removed: string }>(`${W}/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),
  github: () => api<GithubStatus>(`${W}/github`),
  signInCode: (id: string, code: string) => api<unknown>(`${W}/sessions/${id}/sign-in-code`, { method: "POST", body: { code } }),
  claudeSignOut: () => api<unknown>(`${W}/claude/sign-out`, { method: "POST" }),
  capabilities: () => api<Record<string, unknown>>(`${W}/capabilities`),

  repos: () => api<{ repos: RepoView[] }>(`${W}/repos`),
  clone: (url: string) => api<RepoView>(`${W}/repos`, { method: "POST", body: { url } }),
  /** A repository already on the workspace's machine, added where it is (the local provider's only). */
  linkRepo: (path: string) => api<RepoView>(`${W}/repos/link`, { method: "POST", body: { path } }),
  /** Take one added from the machine out of the workspace: its link, never its folder. */
  unlinkRepo: (name: string) => api<{ unlinked: string }>(`${W}/repos/${encodeURIComponent(name)}`, { method: "DELETE" }),

  sessions: () => api<{ sessions: SessionRecord[] }>(`${W}/sessions`),
  /** A session's own read: what the list gives, and for a session the owner's controller started its last round of
   *  checks with each check's output, which the list never carries. */
  session: (id: string, signal?: AbortSignal) => api<unknown>(`${W}/sessions/${encodeURIComponent(id)}`, { signal }),
  createSession: (b: { repo: string | null; program: string; task: string; base?: string | null; pull?: number; cols?: number; rows?: number; chat_settings?: Record<string, string>; group?: string; model_provider?: string; custom?: string; account?: string; teleport?: string }) =>
    api<SessionRecord>(`${W}/sessions`, { method: "POST", body: b }),
  issues: (repo: string, search?: string) =>
    api<{ repo: string; issues: Issue[] }>(`${W}/repos/${encodeURIComponent(repo)}/issues?limit=50${search ? `&search=${encodeURIComponent(search)}` : ""}`),
  pulls: (repo: string, search?: string) =>
    api<{ repo: string; pulls: PullListed[] }>(`${W}/repos/${encodeURIComponent(repo)}/pulls?limit=50${search ? `&search=${encodeURIComponent(search)}` : ""}`),
  pullRequest: (id: string) => api<PullRequest>(`${W}/sessions/${id}/pr`),
  /** Review the pull request a session started from, on GitHub, as the person: `url` is the one they were shown, and
   *  the workspace refuses if the session's is another now. */
  reviewPr: (id: string, event: ReviewEvent, body: string, url: string) =>
    api<{ reviewed: ReviewEvent; url: string }>(`${W}/sessions/${id}/pr/review`, { method: "POST", body: { event, body, url } }),
  prFeedback: (id: string) => api<PrFeedback>(`${W}/sessions/${id}/pr/feedback`),
  programs: () => api<{ programs: Record<string, boolean>; held?: string }>(`${W}/programs`),
  discard: (id: string, path: string) => api<{ path: string; restored?: boolean; removed?: boolean }>(`${W}/sessions/${id}/discard`, { method: "POST", body: { path } }),
  linear: () => api<LinearStatus>(`${W}/linear`),
  setLinearKey: (key: string) => api<LinearStatus>(`${W}/linear/key`, { method: "PUT", body: { key } }),
  forgetLinearKey: () => api<LinearStatus>(`${W}/linear/key`, { method: "DELETE" }),
  linearIssues: (search?: string) =>
    api<{ issues: LinearIssue[] }>(`${W}/linear/issues${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  proofpane: () => api<ProofpaneStatus>(`${W}/proofpane`),
  /** Pair the workspace with a code made in Proofpane; `cloud_url` null for Proofpane's own address. */
  pairProofpane: (code: string, cloud_url: string | null, holds: string[]) =>
    api<ProofpaneStatus>(`${W}/proofpane/pair`, { method: "PUT", body: { code, cloud_url, holds } }),
  unpairProofpane: () => api<ProofpaneStatus>(`${W}/proofpane/pair`, { method: "DELETE" }),
  setProofpaneHolds: (holds: string[]) => api<ProofpaneStatus>(`${W}/proofpane/holds`, { method: "PUT", body: { holds } }),
  asks: () => api<AsksView>(`${W}/asks`),
  setAsks: (on: boolean) => api<AsksView>(`${W}/asks/settings`, { method: "POST", body: { on } }),
  /** Start the session an agent asked for; `effort`, for a Claude Code ask, the person's choice ("" Claude Code's
   *  default, never the ask's), sent as chosen; none for any other ask. */
  startAsk: (id: string, effort?: string) => api<SessionRecord>(`${W}/asks/${encodeURIComponent(id)}/start`,
    { method: "POST", body: effort === undefined ? {} : { effort } }),
  refuseAsk: (id: string) => api<AsksView>(`${W}/asks/${encodeURIComponent(id)}/refuse`, { method: "POST" }),
  claudeUpdate: (body?: { now?: boolean; mode?: string }) =>
    api<{ enabled?: boolean; updating?: boolean; mode?: string; version?: string | null; last?: { ok?: boolean; changed?: boolean; version?: string | null; at?: number; why?: string } | null }>(
      `${W}/claude/update`, body ? { method: "POST", body } : {}),
  /** A file from this device into a chat's worktree, at most MAX_UPLOAD: its path there goes into the message. */
  upload: (id: string, file: File, toChat = false) => {
    if (file.size > MAX_UPLOAD) return Promise.reject(new Error(`${file.name || "A file"} is larger than 10 MB, the most the page sends.`));
    return api<{ path: string; name: string; size: number }>(`${W}/sessions/${id}/upload?name=${encodeURIComponent(file.name || "pasted")}${toChat ? "&to=chat" : ""}`, { method: "POST", raw: file });
  },
  chatSettings: (id: string, body: Record<string, string | boolean>) => api<unknown>(`${W}/sessions/${id}/chat/settings`, { method: "POST", body }),
  chatMcp: (id: string) => api<McpView>(`${W}/sessions/${id}/chat/mcp`),
  chatMcpAct: (id: string, body: Record<string, unknown>) => api<McpView>(`${W}/sessions/${id}/chat/mcp`, { method: "POST", body }),
  paths: (id: string) => api<{ session: string; paths: string[]; truncated: boolean }>(`${W}/sessions/${id}/paths`),
  search: (id: string, q: string, matchCase: boolean, regex: boolean) =>
    api<{ session: string; query: string; matches: Match[]; truncated: boolean }>(
      `${W}/sessions/${id}/search?q=${encodeURIComponent(q)}${matchCase ? "&case=true" : ""}${regex ? "&regex=true" : ""}`),
  merge: (id: string, method: "squash" | "merge" | "rebase", auto: boolean) =>
    api<PullRequest>(`${W}/sessions/${id}/pr/merge`, { method: "POST", body: { method, auto } }),
  removeSession: (id: string) => api<{ removed: string; branch?: string | null; branch_kept?: boolean | null }>(`${W}/sessions/${id}`, { method: "DELETE" }),
  startSession: (id: string, cols = 100, rows = 30) => api<SessionRecord>(`${W}/sessions/${id}/start`, { method: "POST", body: { cols, rows } }),
  stopSession: (id: string) => api<SessionRecord>(`${W}/sessions/${id}/stop`, { method: "POST" }),
  closeSession: (id: string) => api<SessionRecord>(`${W}/sessions/${id}/close`, { method: "POST" }),
  openSession: (id: string) => api<SessionRecord>(`${W}/sessions/${id}/open`, { method: "POST" }),
  renameSession: (id: string, title: string) => api<SessionRecord>(`${W}/sessions/${id}/title`, { method: "POST", body: { title } }),
  /** Other providers' models for claude sessions, and whether the workspace keeps a key for each (never the key). */
  modelProviders: () => api<{ providers: ModelProviderView[] }>(`${W}/model-providers`),
  saveModelKey: (id: string, key: string) => api<{ providers: ModelProviderView[] }>(`${W}/model-providers/${id}`, { method: "PUT", body: { key } }),
  removeModelKey: (id: string) => api<{ providers: ModelProviderView[] }>(`${W}/model-providers/${id}`, { method: "DELETE" }),
  /** Join a session with no group to one, to compare it with sessions started on its task. */
  joinGroup: (id: string, group: string) => api<SessionRecord>(`${W}/sessions/${id}/group`, { method: "POST", body: { group } }),
  /** A new shell in the session's worktree, or (`n`) that one again: the session, and the shell's number. */
  openShell: (id: string, n?: number, cols = 120, rows = 32) =>
    api<SessionRecord & { opened: number }>(`${W}/sessions/${id}/shell`, { method: "POST", body: { cols, rows, ...(n ? { n } : {}) } }),
  stopShell: (id: string, n: number) => api<SessionRecord>(`${W}/sessions/${id}/shell/${n}/stop`, { method: "POST" }),
  closeShell: (id: string, n: number) => api<SessionRecord>(`${W}/sessions/${id}/shell/${n}`, { method: "DELETE" }),
  finish: (id: string) => api<Record<string, unknown>>(`${W}/sessions/${id}/finish`, { method: "POST" }),
  /** Continue in a new session: what it starts from, the task as the owner gave the session, which the app
   *  kept (none for one made before it kept them), and the handovers that name the session */
  handover: (id: string) => api<HandoverView>(`${W}/sessions/${id}/handover`),
  /** the owner's press of Continue in a new session: the session finished, a new one cut from that commit, on the
   *  program and the account chosen (`account` an id or "default"; none, the one its program's new sessions start on) */
  continueSession: (id: string, b: { program: HandoverProgram; account?: string; task: string; cols?: number; rows?: number }) =>
    api<{ finish: Record<string, unknown>; session: SessionRecord; handover: HandoverRow }>(`${W}/sessions/${id}/continue`, { method: "POST", body: b }),
  /** a finished session's branch into the one it follows, a fast-forward, in a repository added from this machine */
  land: (id: string) => api<{ into: string; head: string; commits: number }>(`${W}/sessions/${id}/land`, { method: "POST" }),
  ship: (id: string, b: { title?: string; body?: string; base?: string; draft?: boolean }) =>
    api<{ session: string; branch: string; commits: number; url: string; existing: boolean }>(`${W}/sessions/${id}/ship`, { method: "POST", body: b }),
  diff: (id: string) => api<DiffReport>(`${W}/sessions/${id}/diff`),
  customAgents: () => api<{ agents: CustomAgent[] }>(`${W}/custom-agents`),
  addCustomAgent: (a: Omit<CustomAgent, "id">) => api<CustomAgent>(`${W}/custom-agents`, { method: "POST", body: a }),
  changeCustomAgent: (id: string, a: Omit<CustomAgent, "id">) => api<CustomAgent>(`${W}/custom-agents/${encodeURIComponent(id)}`, { method: "PUT", body: a }),
  forgetCustomAgent: (id: string) => api<{ forgotten: string }>(`${W}/custom-agents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** A session and the branch it started from: how far behind it is, and a merge stopped on conflicts. */
  updateStatus: (id: string) => api<UpdateStatus>(`${W}/sessions/${id}/update`),
  /** Bring it up to date with that branch: its work committed first, then the branch merged in. */
  update: (id: string) => api<UpdateResult>(`${W}/sessions/${id}/update`, { method: "POST", body: {} }),
  /** Give up a merge an update stopped on. */
  updateAbort: (id: string) => api<{ aborted: boolean; finished?: boolean }>(`${W}/sessions/${id}/update/abort`, { method: "POST", body: {} }),
  usage: (id: string) => api<Usage>(`${W}/sessions/${id}/usage`),
  /** What a session's app listens on, from a process working in its worktree: for the desktop app's preview. */
  ports: (id: string) => api<{ ports: { port: number; address: string; pid: number; command: string }[] }>(`${W}/sessions/${id}/ports`),
  files: (id: string, path = "") => api<FolderView>(`${W}/sessions/${id}/files?path=${encodeURIComponent(path)}`),
  file: (id: string, path: string) => api<FileView>(`${W}/sessions/${id}/file?path=${encodeURIComponent(path)}`),
  /** Save a file from the editor over the version it opened (`base`, its sha); an ApiError 409 when it changed since. */
  saveFile: (id: string, path: string, text: string, base: string) =>
    api<FileView>(`${W}/sessions/${id}/file`, { method: "PUT", body: { path, text, base } }),

  chatEvents: (id: string, after = 0) => api<ChatView & { events: ChatEvent[]; more?: boolean }>(`${W}/sessions/${id}/chat?after=${after}`),
  chatSend: (id: string, text: string) => api<unknown>(`${W}/sessions/${id}/chat`, { method: "POST", body: { text } }),
  // an approval, in the phone app, only once the person is confirmed on it (presence.ts)
  // `confirmed`: the person was confirmed already, before the card showed the answer, so not asked twice
  chatAnswer: async (id: string, askId: string, decision: string, { confirmed = false }: { confirmed?: boolean } = {}) => {
    if (!confirmed && (decision === "allow" || decision === "allow_session")) await confirmPresence(ALLOW_TOOL_CALL);
    return api<unknown>(`${W}/sessions/${id}/chat/answer`, { method: "POST", body: { id: askId, decision } });
  },
  chatInterrupt: (id: string) => api<unknown>(`${W}/sessions/${id}/chat/interrupt`, { method: "POST" }),

  // a device signed in by another, signed-in one: the asking device's two calls need no sign-in
  deviceStart: (name: string) =>
    api<{ id: string; code: string; poll: string; expires_at: number; interval: number }>("/api/auth/device/start", { method: "POST", body: { name } }),
  /** `withdrawn`, with a refusal: the device was approved, and the approval withdrawn before it collected its sign-in */
  devicePoll: (id: string, poll: string) =>
    api<{ status: string; token?: string; user?: User; withdrawn?: boolean }>("/api/auth/device/poll", { method: "POST", body: { id, poll } }),
  deviceLookup: (code: string) => api<DeviceRequest>("/api/auth/device/lookup", { method: "POST", body: { code } }),
  /** Approving takes a confirmation made for this code (passkey.ts), a hardware key's while the account's high security
   *  is on; this Mac's own account, which has nothing to confirm with, approves without one. */
  deviceApprove: async (code: string, confirmation?: string) => {
    await confirmPresence("Sign in another device with your account");
    return api<{ name: string; approved: boolean }>("/api/auth/device/approve", { method: "POST", body: { code, confirmation } });
  },
  deviceRefuse: (code: string) => api<{ name: string; approved: boolean }>("/api/auth/device/refuse", { method: "POST", body: { code } }),
  linkSessions: () => api<LinkSessions>("/api/ade/link/sessions"),
  linkHistory: () => api<{ sessions: HistorySession[] }>("/api/ade/link/history"),
  linkTalk: (linkId: string, sessionId: string) =>
    api<HistoryTalk>(`/api/ade/link/history/${encodeURIComponent(linkId)}/${encodeURIComponent(sessionId)}`),
  /** A link's own events, or one of its machine's sessions' mirrored notes alone. */
  linkTimeline: (id: string, before?: number, session?: string) =>
    api<{ events: LinkEvent[]; live_full?: boolean }>(`/api/ade/link/timeline/${encodeURIComponent(id)}?limit=100${before ? `&before=${before}` : ""}${session ? `&session=${encodeURIComponent(session)}` : ""}`),
  /** A ticket uses up a confirmation (passkey.ts) when the account has a way to give one. `e2e`: the account's newest
   *  statement of its encryption keys, for the pair command's --e2e. */
  linkTicket: (linkId?: string, confirmation?: string) =>
    api<{ ticket: string; expires_at: number; ticket_id?: string; url: string; keys?: string[]; high_security?: boolean; e2e?: { seq: number; key_id: string | null } | null; pair_hold?: number }>("/api/ade/link/tickets",
      { method: "POST", body: { ...(linkId ? { link_id: linkId } : {}), ...(confirmation ? { confirmation } : {}) } }),
  /** Close an open question, prompt or proposal: the sign-in alone, and no words; it decides nothing. A prompt's Close
   *  hides it (its seq, and when): it leaves the open list and stays open, answerable until its deadline. An item to
   *  look at is closed, as before. */
  closeLinkQuestion: (seq: number) => api<{ seq: number; hidden_at?: number }>(`/api/ade/link/questions/${seq}/close`, { method: "POST" }),
  /** The account's hidden prompts that still wait and its prompts closed in the last 24 hours, newest first: the newest
   *  50, `closed_more` saying how many of those closed it left out (none from a server from before it). */
  closedLinkPrompts: () => api<{ prompts: LinkClosedPrompt[]; server_time: number; closed_more?: number }>("/api/ade/link/prompts/closed"),
  revokeLink: (id: string, confirmation?: string) =>
    api<unknown>(`/api/ade/link/links/${encodeURIComponent(id)}`, { method: "DELETE", body: confirmation ? { confirmation } : {} }),
  /** A command signed with one of the account's passkeys (passkey.ts, prepareSigned and signPrepared): its seq; a move
   *  is held instead, and stored only when its hold ends. A move of a session whose History row came sealed carries
   *  its opened title beside what is signed, for the workspace to name it by. */
  sendLinkCommand: (signed: { payload: Record<string, unknown>; credential: unknown; title?: string }) =>
    api<{ seq?: number; held?: { id: string; takes_effect_at: number; told: number }; expires_at: number }>(
      "/api/ade/link/commands", { method: "POST", body: signed }),
  withdrawLinkCommand: (seq: number) => api<{ withdrawn_at: number }>(`/api/ade/link/commands/${seq}/withdraw`, { method: "POST" }),
  stopLink: (id: string) => api<{ stopped_at: number }>(`/api/ade/link/links/${encodeURIComponent(id)}/stop`, { method: "POST" }),
  resumeLink: (id: string, signed: { payload: Record<string, unknown>; credential: unknown }) =>
    api<{ resumed_at: number }>(`/api/ade/link/links/${encodeURIComponent(id)}/resume`, { method: "POST", body: signed }),
  setLinkReadsOthers: (id: string, allowed: boolean) =>
    api<{ reads_others: boolean }>(`/api/ade/link/links/${encodeURIComponent(id)}/reads-others`, { method: "PUT", body: { allowed } }),
  /** The owner's switch, by the sign-in: whether a linked session may propose messages to his other sessions. */
  setLinkProposes: (id: string, allowed: boolean) =>
    api<{ proposes: boolean }>(`/api/ade/link/links/${encodeURIComponent(id)}/proposes`, { method: "PUT", body: { allowed } }),
  /** A machine session's moves into the workspace, by any of the account's links, newest first. */
  sessionMoves: (session: string) =>
    api<{ moves: LinkMove[] }>(`/api/ade/link/moves?session=${encodeURIComponent(session)}`),
  /** Start an uploaded move in the workspace, the workspace started first when it is not running: the move after. */
  startMove: (seq: number) => api<LinkMove>(`/api/ade/link/moves/${seq}/start`, { method: "POST" }),
  /** The account's ask address (made the first time it is read), and the address sessions link with. */
  askAddress: () => api<{ address: string; created_at: number; url: string }>("/api/ade/link/address"),
  /** A new ask address: the requests waiting on the old one are refused. */
  newAskAddress: () => api<{ address: string; created_at: number; url: string; refused: number }>("/api/ade/link/address", { method: "POST" }),
  /** With `replace`, the id of the live cloud link the approval replaces (LinkAsk.replaces). */
  approveLinkAsk: (id: string, confirmation?: string, replace?: string) =>
    api<{ hold: { id: string; takes_effect_at: number }; told: number }>(`/api/ade/link/asks/${encodeURIComponent(id)}/approve`,
      { method: "POST", body: { ...(confirmation ? { confirmation } : {}), ...(replace ? { replace } : {}) } }),
  refuseLinkAsk: (id: string) => api<{ refused: string }>(`/api/ade/link/asks/${encodeURIComponent(id)}/refuse`, { method: "POST" }),
  /** Cancel an approval that waits (the open edition's ade/holds.py), before it takes effect. */
  cancelHold: (id: string) => api<{ cancelled_at: number }>(`/api/ade/holds/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  approvals: () => api<{ requests: HookRequest[] }>("/api/ade/approvals"),
  /** Approving takes a confirmation made for this request (passkey.ts, `request-approve:<id>`); this Mac's own account,
   *  which has nothing to confirm with, approves without one. Denying takes the sign-in alone. */
  decide: async (id: string, approve: boolean, confirmation?: string) => {
    if (approve) await confirmPresence("Approve the held command");
    return api<unknown>(`/api/ade/approvals/${id}`, { method: "POST", body: { approve, ...(confirmation ? { confirmation } : {}) } });
  },
  /** A held command answered with a connected app's proposal on it: approving takes a confirmation made for this
   *  request whatever the app proposed, as `decide` does, and the phone asks for the person first; denying takes the
   *  sign-in alone. */
  decideProposed: async (id: string, approve: boolean, proposal: string, confirmation?: string) => {
    if (approve) await confirmPresence("Approve the held command");
    return api<unknown>(`/api/ade/approvals/${id}`, { method: "POST", body: { approve, proposal, ...(confirmation ? { confirmation } : {}) } });
  },
  connector: () => api<ConnectorSetup>("/api/connector"),
  /** The account's switch: on takes its confirmation; off revokes every connected app. */
  setConnectorAccount: (on: boolean, confirmation?: string) =>
    api<{ on: boolean; revoked: number }>("/api/connector/account", { method: "PUT", body: { on, ...(confirmation ? { confirmation } : {}) } }),
  connectRequest: (id: string) => api<ConnectRequest>(`/api/connector/requests/${encodeURIComponent(id)}`),
  /** Allow a request, bound to the account the pane showed (`account`, its account_id): the server refuses another. */
  allowConnect: (id: string, propose: boolean, account: string, confirmation?: string) =>
    api<{ redirect: string; hold: { id: string; takes_effect_at: number; told: number }; grant: string }>(
      `/api/connector/requests/${encodeURIComponent(id)}/allow`, { method: "POST", body: { propose, account, ...(confirmation ? { confirmation } : {}) } }),
  refuseConnect: (id: string) => api<{ redirect: string }>(`/api/connector/requests/${encodeURIComponent(id)}/refuse`, { method: "POST" }),
  connectedApps: () => api<{ grants: ConnectedApp[] }>("/api/connector/grants"),
  revokeApp: (id: string) => api<ConnectedApp>(`/api/connector/grants/${encodeURIComponent(id)}`, { method: "DELETE" }),
  appEvents: (id: string, before?: number | null) =>
    api<{ events: ConnectorEvent[]; before: number | null }>(`/api/connector/grants/${encodeURIComponent(id)}/events${before ? `?before=${before}` : ""}`),
  proposals: () => api<{ proposals: Proposal[] }>("/api/connector/proposals"),
  refuseProposal: (id: string, words?: string) =>
    api<Proposal>(`/api/connector/proposals/${encodeURIComponent(id)}/refuse`, { method: "POST", body: words ? { words } : {} }),
  refuseAllProposals: (grant: string, words?: string) =>
    api<{ refused: number }>(`/api/connector/grants/${encodeURIComponent(grant)}/refuse-all`, { method: "POST", body: words ? { words } : {} }),
  acceptStop: (id: string) => api<Proposal>(`/api/connector/proposals/${encodeURIComponent(id)}/accept`, { method: "POST" }),

  // an agent's tasks: each change with the state_version the page read, but Stop and the
  // switches, which take none; a 409 says the task moved since, and a 412 that its state refuses the change
  /** The user's tasks, or one agent's, with the deployment's switches and provider (never guessed by the page). */
  tasks: (agent?: string) => api<{ tasks: TaskView[]; settings: TaskSettings }>(`${T}/tasks${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`),
  task: (id: string) => api<{ task: TaskView }>(`${T}/tasks/${encodeURIComponent(id)}`),
  newTask: (body: NewTask) => api<{ task: TaskView }>(`${T}/tasks`, { method: "POST", body }),
  /** A change; `not_stopped` says why the attempt it asked to stop (one sent and not started) could not be, the change
   *  kept all the same. */
  putTask: (id: string, body: TaskEdit) =>
    api<{ task: TaskView; not_stopped?: string }>(`${T}/tasks/${encodeURIComponent(id)}`, { method: "PUT", body }),
  /** Only a done or stopped task; its account record stays, under its agent, with a "deleted" entry. */
  deleteTask: (id: string, stateVersion: number) =>
    api<{ deleted: boolean }>(`${T}/tasks/${encodeURIComponent(id)}`, { method: "DELETE", body: { state_version: stateVersion } }),
  /** A control of the task's (TaskAction): `queued` from Continue on..., `hold` from the switches, and `not_stopped` from
   *  a press that stops the live attempt (Stop, Continue on... while it works) when the workspace could not take the
   *  stop, the press kept all the same. */
  taskAction: (id: string, action: TaskAction, body: Record<string, unknown> = {}) =>
    api<{ task: TaskView; queued?: boolean; hold?: HoldRef | null; not_stopped?: string }>(
      `${T}/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST", body }),
  taskJournal: (id: string, before?: number, limit = 100) =>
    api<{ events: TaskEvent[] }>(`${T}/tasks/${encodeURIComponent(id)}/journal?limit=${limit}${before ? `&before=${before}` : ""}`),
  /** The packet one of the task's attempts was sent, for the run's pane; 404 for a run of another task's, or
   *  whose packet went with its run. */
  taskPacket: (taskId: string, runId: string) =>
    api<TaskPacketView>(`${T}/tasks/${encodeURIComponent(taskId)}/packets/${encodeURIComponent(runId)}`),
  // a task's grant: signed with a passkey over the payload the page composed (passkey.ts, prepareGrant); an ask
  // for more waits for that signature, or is refused with the sign-in alone
  /** A version of the task's grant the owner signed: at its first, to widen it, or to sign an ask as it was asked. */
  signTaskGrant: (taskId: string, signed: { payload: Record<string, unknown>; credential: unknown }) =>
    api<{ task: TaskView; grant: TaskGrantView }>(`${T}/tasks/${encodeURIComponent(taskId)}/grant`, { method: "POST", body: signed }),
  /** Every ask for more in a task's grant that waits for the owner's passkey. */
  grantWidenings: () => api<{ widenings: GrantWideningView[] }>(`${T}/tasks/grant-widenings`),
  refuseGrantWidening: (id: string) =>
    api<{ widening: GrantWideningView }>(`${T}/tasks/grant-widenings/${encodeURIComponent(id)}/refuse`, { method: "POST" }),
  // the workspace's Claude Code accounts as the owner's own, for tasks' relays (named so beside `accounts` above,
  // which reads the workspace's sign-ins)
  taskAccounts: () => api<{ accounts: AccountOwnerView[] }>(`${T}/accounts`),
  /** Read an account's identity from its frozen start, for the owner to confirm as it came. */
  reportAccount: (account: string) => api<{ account: AccountOwnerView }>(`${T}/accounts/report`, { method: "POST", body: { account } }),
  /** "Is this account yours?": a confirmation made for task-account-own:<id>, naming the report and orgId the page
   *  showed (`shown`), which the app refuses, 409, where the account was read again since and either differs; the
   *  account joins relays when its hold ends. */
  confirmAccount: (id: string, confirmation: string | undefined, shown: { report: IdentityReport | null; org_id: string | null }) =>
    api<{ account: AccountOwnerView; hold: HoldRef }>(`${T}/accounts/${encodeURIComponent(id)}/confirm`,
      { method: "POST", body: { confirmation, report: shown.report, org_id: shown.org_id } }),
  putAccount: (id: string, body: { in_relays?: boolean; reserve_lifted?: boolean }) =>
    api<{ account: AccountOwnerView }>(`${T}/accounts/${encodeURIComponent(id)}`, { method: "PUT", body }),
  /** The relay's order: the account-owner ids, first to last. */
  orderAccounts: (ids: string[]) => api<{ accounts: AccountOwnerView[] }>(`${T}/accounts/order`, { method: "POST", body: { ids } }),
  // its shape is not one the page reads member by member (the app answers `windows` keyed by backend): the page builds
  // Continue on...'s list from taskAccounts and the workspace's kept keys instead
  backends: () => api<{ backends: unknown[]; windows: Record<string, WindowView[]> }>(`${T}/backends`),
  /** Every task's account record, a deleted task's among them (newest task first), or one agent's tasks'. */
  accountRecords: (agent?: string) =>
    api<{ records: AccountRecordView[] }>(`${T}/account-records${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`),
  taskAccountRecord: (id: string) => api<{ record: AccountRecordView }>(`${T}/tasks/${encodeURIComponent(id)}/account-record`),
  /** The owner's Delete of a deleted task's account record: a confirmation made for task-account-record:<task id>. */
  deleteAccountRecord: (id: string, confirmation?: string) =>
    api<{ deleted: number }>(`${T}/tasks/${encodeURIComponent(id)}/account-record`, { method: "DELETE", body: { confirmation } }),
  /** Every risk point's text (the app's ade/risk_routes.py), the operator and the record's keeping filled in. */
  risks: () => api<RiskTextsView>("/api/ade/risks"),
};

/** An image of a session's worktree, as its bytes: fetched with the account's token (never in its address), for the
 *  page to show from memory. */
export function worktreeImage(id: string, path: string, signal?: AbortSignal): Promise<Blob> {
  return fetchBlob(`${W}/sessions/${id}/image?path=${encodeURIComponent(path)}`, signal);
}

/** A file the app serves, as its bytes, fetched with the account's token (never in its address). */
export async function fetchBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const headers: Record<string, string> = {};
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  let resp: Response;
  try {
    resp = await fetch(`${server()}${path}`, { headers, signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the server. Check your connection.");
  }
  if (!resp.ok) {
    let data: unknown = null;
    try { data = await resp.json(); } catch { /* not the app's: the status says it */ }
    if (resp.status === 401 && t && !signedOutBy401(t)) throw new ApiError(401, OLD_SIGN_IN, fromApp(data));
    const said = fromApp(data) ? (data as { detail?: unknown }).detail : undefined;
    throw new ApiError(resp.status, typeof said === "string" ? said : `${resp.status} ${resp.statusText}`, fromApp(data));
  }
  return resp.blob();
}

/** Files into a session's worktree, one after another (each at most MAX_UPLOAD): their paths there. */
export async function uploadFiles(id: string, files: File[]): Promise<string[]> {
  const out: string[] = [];
  for (const f of files) out.push((await Api.upload(id, f)).path);
  return out;
}

// --- WebSockets ----------------------------------------------------------------------------------------------

function wsUrl(path: string) {
  const s = server();
  if (s) {
    const u = new URL(s);
    return (u.protocol === "https:" ? "wss://" : "ws://") + u.host + path;
  }
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path;
}

/** A session's terminal, its program's or (`shell`) that shell's: binary both ways, JSON text for control (resize up;
 *  ready, exit, dropped, error down). */
export function terminalLink(session: string, onExit?: (code: number | null) => void, shell?: number): TerminalLink {
  return {
    attach(io) {
      const enc = new TextEncoder();
      let ws: WebSocket | null = null;
      let size = { cols: 100, rows: 30 };
      let closed = false;
      let tries = 0;
      const open = () => {
        ws = new WebSocket(wsUrl(`${W}/terminal`));
        ws.binaryType = "arraybuffer";
        ws.onopen = () => ws!.send(JSON.stringify({ token: token(), session, cols: size.cols, rows: size.rows, ...(shell ? { shell } : {}) }));
        ws.onmessage = (ev) => {
          if (typeof ev.data !== "string") {
            io.write(new Uint8Array(ev.data as ArrayBuffer));
            return;
          }
          let msg: { type?: string; code?: number; detail?: string } = {};
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === "ready") tries = 0;
          else if (msg.type === "exit") { closed = true; io.status(`[exited${typeof msg.code === "number" ? ` with ${msg.code}` : ""}]`); onExit?.(msg.code ?? null); }
          else if (msg.type === "dropped") io.status("[output dropped while this page could not keep up]");
          else if (msg.type === "error") { closed = true; io.status(msg.detail || "The workspace refused the terminal."); }
        };
        ws.onclose = () => {
          if (closed) return;
          // a dropped connection reconnects; the workspace replays what the program printed meanwhile
          const wait = Math.min(8000, 500 * 2 ** tries++);
          setTimeout(() => !closed && open(), wait);
        };
      };
      open();
      return {
        send: (d) => ws?.readyState === WebSocket.OPEN && ws.send(enc.encode(d)),
        resize: (cols, rows) => {
          size = { cols, rows };
          if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols, rows }));
        },
        close: () => { closed = true; ws?.close(); },
      };
    },
  };
}

/** Type `text` into a session's terminal program as one bracketed paste and then Enter, as a person pasting would (how
 *  Orca hands review notes to an agent too); with `enter` false, the paste alone, for the person to send. The hello
 *  names no size, so the program's terminal is not resized; an escape character is dropped so nothing in the text can
 *  end the paste early. */
export function sendToTerminal(session: string, text: string, enter = true): Promise<void> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl(`${W}/terminal`));
    ws.binaryType = "arraybuffer";
    const enc = new TextEncoder();
    let done = false;
    const end = (err?: Error) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      ws.close();
      if (err) reject(err);
      else resolve();
    };
    const timer = setTimeout(() => end(new Error("The terminal did not answer.")), 10000);
    ws.onopen = () => ws.send(JSON.stringify({ token: token(), session }));
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;         // what the program printed before: not for this sender
      let msg: { type?: string; detail?: string } = {};
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "ready") {
        ws.send(enc.encode("\x1b[200~" + text.split("\x1b").join("") + "\x1b[201~"));
        if (enter) setTimeout(() => { ws.send(enc.encode("\r")); setTimeout(() => end(), 150); }, 120);
        else setTimeout(() => end(), 150);
      } else if (msg.type === "error" || msg.type === "exit") {
        end(new Error(msg.detail || "The session's program has ended."));
      }
    };
    ws.onerror = () => end(new Error("The terminal could not be reached."));
  });
}

/** Every chat's events, live, and what claude is writing as it writes it. Returns a function that closes the stream. */
export function chatStream(
  onEvent: (session: string, ev: ChatEvent) => void,
  onGap: () => void,
  onDelta?: (session: string, d: ChatDelta) => void,
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let tries = 0;
  const open = () => {
    ws = new WebSocket(wsUrl(`${W}/chat`));
    ws.onopen = () => ws!.send(JSON.stringify({ token: token() }));
    ws.onmessage = (ev) => {
      let msg: { type?: string; session?: string; event?: ChatEvent; delta?: ChatDelta; detail?: string } = {};
      try { msg = JSON.parse(String(ev.data)); } catch { return; }
      if (msg.type === "ready") tries = 0;
      else if (msg.type === "event" && msg.session && msg.event) onEvent(msg.session, msg.event);
      else if (msg.type === "delta" && msg.session && msg.delta) onDelta?.(msg.session, msg.delta);
      else if (msg.type === "dropped") onGap();
      else if (msg.type === "error") closed = closed || /Sign in/.test(msg.detail || "");
    };
    ws.onclose = () => {
      if (closed) return;
      onGap();
      setTimeout(() => !closed && open(), Math.min(10000, 1000 * 2 ** tries++));
    };
  };
  open();
  return () => { closed = true; ws?.close(); };
}
