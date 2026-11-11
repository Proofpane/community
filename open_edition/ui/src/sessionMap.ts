// The control view's map: what the app's two routes give, each a call through to the person's own workspace, whose
// agent reads it from its sessions' own files, and how the page says it. Names, times and counts, and a session's title
// as the list shows it (the first line of its first prompt where it was never named), its repository, branch, model and
// effort, and each call's tool and what it names; never a reply. An agent's name is a model's words (its name, or else
// the description Claude gave it): the map shows it cut to 80 characters, as Claude Code's own map shows it, and
// nothing puts it in a notification, a push or the page's title. Each answer is kept to the fields named here, each of
// its type, as it is read (mapOf, callsOf): a field a workspace adds never reaches what draws the map until a release
// of the page names it, and an answer of another shape draws nothing rather than breaking the map.
import { useEffect, useRef, useState } from "react";
import { api, DESKTOP, PHONE, type AccountsView } from "./api";
import { popOutQuery, SESSION_ID, type PopOut } from "./popOut";
import { accountNameOf, sessionState } from "./store";
import type { SessionState } from "./types";

/** An effort as the workspace gives it: a level's name ("high"), or a number of tokens where the agent was given a token
 *  budget; none where it gave none. */
export type Effort = string | number | null;
export interface MapAgent {
  id: string;
  name: string | null;
  agent_type: string | null;
  /** `running`, `completed`, `failed`, `killed`, or null: not known to be running, how it ended not said. */
  status: string | null;
  started_at: number | null;
  ended_at: number | null;
  tokens: number | null;
  model: string | null;
  effort: Effort;
}
export interface MapSession {
  id: string;
  title: string;
  program: string;
  repo: string | null;
  branch: string | null;
  account: { kind: string; id: string } | null;
  model: string | null;
  effort: Effort;
  state: string;
  started_at: number | null;
  updated_at: number | null;
  context_tokens: number | null;
  agents_read: string;
  agents: MapAgent[];
  /** How many of its agents the workspace left out past those it gave (it gives at most 50, the running ones first,
   *  then the newest): a field beyond the examples' shape, which the workspace's agent serves. */
  agents_more: number;
}
export interface WorkspaceMap { at: number; sessions: MapSession[] }
/** `target` is null where the workspace names nothing for the call (where a shell may read the words otherwise than
 *  its reader does). */
export interface MapCall { tool: string; target: string | null; failed: boolean; at: number | null }
export interface MapCalls { calls: MapCall[]; more: number }
export interface MapTurn { agent: string | null; requests: number; input_tokens: number; output_tokens: number }
export interface SessionCalls {
  at: number;
  session: string;
  calls_read: string;
  main: MapCalls;
  agents: (MapCalls & { id: string })[];
  turns_read: string;
  turns: MapTurn[];
  cut: boolean;
}

const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const list = (v: unknown): Record<string, unknown>[] =>
  (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x)) : []);
const fields = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {});
/** An effort of either kind the workspace gives, kept as it came: a level's name, or a token budget, a whole number not
 *  below 0 (as the workspace's agent writes one); another number is none, since it could not be said as a budget. */
const effortOf = (v: unknown): Effort => text(v) ?? (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);

/** A session's state: as the workspace gives it, or else from the record's fields store.ts sessionState reads, which
 *  the workspace may give in its place (its running, its activity's and its chat's state, how many questions
 *  its chat asks, whether it finished, how it exited); only those are read, and none is kept. */
function stateOf(s: Record<string, unknown>): string {
  const given = text(s.state);
  if (given !== null) return given;
  if (!("running" in s || "chat" in s || "finished" in s || "exit_code" in s)) return "";
  const chat = s.chat && typeof s.chat === "object" ? fields(s.chat) : null;
  return sessionState({
    program: text(s.program) ?? "", running: s.running === true, activity: { state: text(fields(s.activity).state) },
    chat: chat ? { state: text(chat.state), asks: list(chat.asks) } : null, finished: s.finished ? {} : null,
    exit_code: count(s.exit_code),
  } as unknown as Parameters<typeof sessionState>[0]);
}

/** The map's answer kept to its fields, each of its type: a session whose id is not a session's (twelve hex
 *  digits), or an agent with no id, is left out; a list that is not one is empty, a value of another type is none. `now`
 *  is the time to take where the answer gives none. */
export function mapOf(raw: unknown, now = Date.now() / 1000): WorkspaceMap {
  const m = fields(raw);
  return {
    at: count(m.at) ?? now,
    sessions: list(m.sessions).filter((s) => typeof s.id === "string" && SESSION_ID.test(s.id)).map((s) => {
      const a = fields(s.account);
      return {
        id: s.id as string, title: text(s.title) ?? "", program: text(s.program) ?? "", repo: text(s.repo), branch: text(s.branch),
        account: typeof a.kind === "string" && typeof a.id === "string" ? { kind: a.kind, id: a.id } : null,
        model: text(s.model), effort: effortOf(s.effort), state: stateOf(s), started_at: count(s.started_at),
        updated_at: count(s.updated_at), context_tokens: count(s.context_tokens), agents_read: text(s.agents_read) ?? "",
        agents: list(s.agents).filter((g) => typeof g.id === "string").map((g) => ({
          id: g.id as string, name: text(g.name), agent_type: text(g.agent_type), status: text(g.status),
          started_at: count(g.started_at), ended_at: count(g.ended_at), tokens: count(g.tokens), model: text(g.model),
          effort: effortOf(g.effort),
        })),
        agents_more: count(s.agents_more) ?? 0,
      };
    }),
  };
}

const callsIn = (v: unknown): MapCalls => {
  const c = fields(v);
  return {
    calls: list(c.calls).map((x) => ({ tool: text(x.tool) ?? "", target: text(x.target), failed: x.failed === true, at: count(x.at) })),
    more: count(c.more) ?? 0,
  };
};

/** A session's calls and turns kept to their fields, each of its type, as mapOf keeps the map. */
export function callsOf(raw: unknown, now = Date.now() / 1000): SessionCalls {
  const c = fields(raw);
  return {
    at: count(c.at) ?? now,
    session: text(c.session) ?? "",
    calls_read: text(c.calls_read) ?? "",
    main: callsIn(c.main),
    agents: list(c.agents).filter((a) => typeof a.id === "string").map((a) => ({ id: a.id as string, ...callsIn(a) })),
    turns_read: text(c.turns_read) ?? "",
    turns: list(c.turns).map((t) => ({ agent: text(t.agent), requests: count(t.requests) ?? 0, input_tokens: count(t.input_tokens) ?? 0,
      output_tokens: count(t.output_tokens) ?? 0 })),
    cut: c.cut === true,
  };
}

export const MapApi = {
  map: (signal?: AbortSignal) => api<unknown>("/api/ade/workspace/map", { signal }).then((m) => mapOf(m)),
  session: (id: string, signal?: AbortSignal) => api<unknown>(`/api/ade/workspace/sessions/${encodeURIComponent(id)}/map`, { signal }).then((c) => callsOf(c)),
};

/** How often the map, and a session opened in it, are asked for while the page is visible (milliseconds). */
export const EVERY = 3000;
/** An agent's name is shown cut to this many characters. */
export const NAME_MOST = 80;

/** An agent's name as the map shows it: its name or description as the workspace gave it, cut to 80 characters (by
 *  code point, never inside one, though a run of several drawn as one, an emoji joined by a zero-width joiner or a
 *  letter with its marks, can be cut between them); its type where it gave none. */
export function agentName(a: Pick<MapAgent, "name" | "agent_type">): string {
  const name = typeof a.name === "string" ? a.name.trim() : "";
  if (!name) return a.agent_type ? `An agent (${a.agent_type})` : "An agent";
  const chars = Array.from(name);
  return chars.length > NAME_MOST ? chars.slice(0, NAME_MOST).join("") : name;
}

/** How long an agent ran, or has run, as Claude Code's own map says it: "346m 18s", in minutes past the hour too;
 *  "18s" under a minute. */
export function ranWords(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** Seconds from an agent's start to its end or, while it runs, to `now` (the workspace's time): none where its start
 *  is not known, or where it ended at a time not known. */
export function ranSeconds(a: Pick<MapAgent, "started_at" | "ended_at" | "status">, now: number): number | null {
  if (a.started_at == null) return null;
  const end = a.ended_at ?? (a.status === "running" ? now : null);
  return end == null ? null : Math.max(0, end - a.started_at);
}

/** Tokens as Claude Code's map says them: "200.6k tokens", "950 tokens", "1.2M tokens". */
export function tokenWords(n: number): string {
  const one = (x: number) => x.toFixed(1).replace(/\.0$/, "");
  if (n < 1000) return `${n} ${n === 1 ? "token" : "tokens"}`;
  if (Math.round(n / 100) < 10_000) return `${one(Math.round(n / 100) / 10)}k tokens`;
  return `${one(Math.round(n / 100_000) / 10)}M tokens`;
}

/** An effort as the map says it: a level's name as the workspace gave it ("high"); a token budget named as the effort,
 *  its number exact ("effort: 31,999-token budget"), so it is never read as the tokens the agent used, which its row
 *  says beside it; none where it gave none. */
export function effortWords(effort: Effort): string | null {
  if (typeof effort === "number") return `effort: ${String(effort).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}-token budget`;
  return effort || null;
}

/** The agents of a session the workspace left out past those it gave, said after the ones shown, as a session's calls
 *  past the cut are ("and 12 more calls"), or alone where it gave none ("2 agents, not shown"); none where it left none
 *  out. */
export function agentsMoreWords(more: number, shown: number): string | null {
  if (!(more > 0)) return null;
  const agents = more === 1 ? "agent" : "agents";
  return shown > 0 ? `and ${more} more ${agents}, not shown` : `${more} ${agents}, not shown`;
}

/** An agent's status as its dot shows it: one colour while it runs, another once it ended, a third when it failed or
 *  was killed; each with words for a screen reader. A status the page does not know, or none, is said to be not known. */
export function agentStatus(status: string | null): { tone: "running" | "ended" | "failed" | "unknown"; label: string; color: string } {
  switch (status) {
    case "running": return { tone: "running", label: "Running", color: "var(--color-run)" };
    case "completed": return { tone: "ended", label: "Completed", color: "var(--color-done)" };
    case "failed": return { tone: "failed", label: "Failed", color: "var(--color-fail)" };
    case "killed": return { tone: "failed", label: "Killed", color: "var(--color-fail)" };
    default: return { tone: "unknown", label: "Status not known", color: "var(--color-faint)" };
  }
}

const STATES: readonly SessionState[] = ["starting", "working", "running", "waiting", "idle", "done", "failed", "stopped"];
/** A session's state as the list says it (store.ts sessionState's words); one the page does not know is "stopped". */
export function mapState(state: string): SessionState {
  return (STATES as readonly string[]).includes(state) ? (state as SessionState) : "stopped";
}

/** Why a session's agents are not shown, where they are not: in words, never an empty list said as none. */
export function agentsReadWords(read: string): string | null {
  if (read === "yes") return null;
  if (read === "version") return "Its Claude Code is too old to tell its agents to a status line.";
  if (read === "missing") return "Its agents could not be read: their file is not there yet.";
  if (read === "no") return "Not a Claude Code session, so it has no agents to show.";
  return "Its agents could not be read.";
}

/** The same for a session's calls and turns (the second route's `calls_read` and `turns_read`). */
export function readWords(read: string, what: "calls" | "turns"): string | null {
  if (read === "yes") return null;
  const them = what === "calls" ? "Its tool calls" : "Its turns";
  if (read === "version") return `${them} are not given by its Claude Code's version.`;
  if (read === "missing") return `${them} could not be read: their file is not there.`;
  if (read === "no") return `${them} are not read for a session that is not Claude Code's.`;
  return `${them} could not be read.`;
}

/** The order of accounts: each program's own sign-in first, found by its id, so it keeps its place under a name of its
 *  own (the list's groups, Sidebar's byAccount, rank it by the name "Default"); then by name, an account the page does
 *  not know last, and the sessions on no account after it; where two have one name, Claude's before Codex's. */
function byAccount(a: AccountGroup, b: AccountGroup) {
  const rank = (g: AccountGroup) => (g.kind === null ? 3 : g.key.endsWith(":default") ? 0 : g.name === "another account" ? 2 : 1);
  return rank(a) - rank(b) || a.name.localeCompare(b.name) || (a.kind || "").localeCompare(b.kind || "") || a.key.localeCompare(b.key);
}

export interface AccountGroup { key: string; kind: "claude" | "codex" | null; name: string; sessions: MapSession[] }

/** The account a session runs on, named as the list names it (store.ts accountNameOf): an added account's name, or
 *  "another account" for one the page does not know; the program's own sign-in by its name ("Default" until it is
 *  given one), which the list says only where the program has more than one account. Null for a session on no account
 *  (a shell, or another provider's model on the person's key). */
export function accountOfMap(s: Pick<MapSession, "account" | "program">, accounts?: AccountsView | null): { key: string; kind: "claude" | "codex"; name: string } | null {
  if (!s.account) return null;
  const kind = s.account.kind === "codex" ? "codex" : "claude";
  const program = kind === "codex" ? "codex" : s.program === "chat" ? "chat" : "claude";
  const own = s.account.id === "default";
  const listed = accountNameOf({ program, account: own ? null : s.account.id, model_provider: null }, accounts);
  const name = listed ?? (accounts?.[kind].accounts.find((a) => a.id === "default")?.name || "Default");
  return { key: `${kind}:${s.account.id}`, kind, name };
}

/** The map's sessions by account, each account once, in byAccount's order (each program's own sign-in first, by its
 *  id); the sessions on no account last. */
export function accountGroups(sessions: MapSession[], accounts?: AccountsView | null): AccountGroup[] {
  const groups = new Map<string, AccountGroup>();
  for (const s of sessions) {
    const a = accountOfMap(s, accounts);
    const key = a ? a.key : "none";
    if (!groups.has(key)) groups.set(key, { key, kind: a ? a.kind : null, name: a ? a.name : "On no account", sessions: [] });
    groups.get(key)!.sessions.push(s);
  }
  return Array.from(groups.values()).sort(byAccount);
}

/** The longest wait between two reads while they fail (milliseconds), as the shell's own reads wait at most. */
export const FAILING_MOST = 30000;

/** Read `read` at once and every `every` milliseconds while the page is visible, one read at a time; nothing while it
 *  is hidden, and again at once when it shows. A read that says it failed (false) has the next wait twice the last, up
 *  to FAILING_MOST, until one does not. Off while `on` is false, and once the component goes (a read under way is
 *  then aborted). */
export function useVisibleEvery(on: boolean, every: number, read: (signal: AbortSignal) => Promise<boolean | void>) {
  const latest = useRef(read);
  latest.current = read;
  useEffect(() => {
    if (!on) return;
    let alive = true;
    let busy = false;
    let wait = every;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctl = new AbortController();
    const tick = async () => {
      clearTimeout(timer);
      if (!alive || busy || document.visibilityState !== "visible") return;
      busy = true;
      let ok = true;
      try { ok = (await latest.current(ctl.signal)) !== false; } catch { ok = false; } finally { busy = false; }
      wait = ok ? every : Math.min(wait * 2, FAILING_MOST);
      if (alive && document.visibilityState === "visible") timer = setTimeout(tick, wait);
    };
    const shown = () => { if (document.visibilityState === "visible") void tick(); else clearTimeout(timer); };
    void tick();
    document.addEventListener("visibilitychange", shown);
    return () => { alive = false; clearTimeout(timer); ctl.abort(); document.removeEventListener("visibilitychange", shown); };
  }, [on, every]);
}

/** The time now, in seconds, again each second while `on` and the page is visible: what a running agent's time is
 *  counted to. */
export function useTicking(on: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!on) return;
    setNow(Date.now() / 1000);
    const t = setInterval(() => { if (document.visibilityState === "visible") setNow(Date.now() / 1000); }, 1000);
    return () => clearInterval(t);
  }, [on]);
  return now;
}

/** Whether pop-outs are offered here: in a browser on a computer and in the desktop app, never on a phone's width nor
 *  in the phone app, where the panels stay drawers. */
export function mayPopOut(phoneWidth: boolean): boolean {
  return !PHONE && !phoneWidth;
}

/** Open a pop-out: a window of its own in the desktop app (the shell's open_popout, as a session's preview opens),
 *  else a browser tab on the same page, which shares its sign-in; the main
 *  page keeps its place. Its title names the panel in fixed words, never an agent's name. */
export async function openPopOut(p: PopOut, opts: { invoke?: (cmd: string, args: Record<string, unknown>) => Promise<unknown>; open?: typeof window.open } = {}): Promise<void> {
  if (DESKTOP || opts.invoke) {
    const invoke = opts.invoke ?? (async (cmd: string, args: Record<string, unknown>) => (await import("@tauri-apps/api/core")).invoke(cmd, args));
    await invoke("open_popout", { view: p.view, session: p.view === "map" ? null : p.session, agent: p.view === "agent" ? p.agent : null });
    return;
  }
  const open = opts.open ?? window.open.bind(window);
  const name = p.view === "map" ? "pp-map" : p.view === "session" ? `pp-map-${p.session}` : `pp-agent-${p.session}-${p.agent}`;
  const w = open(`${location.pathname}${popOutQuery(p)}`, name);
  if (!w) throw new Error("The browser did not open the tab: allow pop-ups for this page, then try again.");
}
