// A server for the page's shell, in a test: the routes the shell reads, answered from a state the test changes, and
// every request it was sent kept in order. The summary is built from that state as the server builds it
// (ade/summary.py): the badges' counts, and a stamp for each list, here the list's own JSON. With `summary` false it is
// a server from before the summary; with `down` it cannot be reached.
import { vi } from "vitest";
import { channelAlerts, proposalAlerts, type HistorySession, type HookRequest, type LinkSessions, type LinkView, type Proposal,
  type RepoView, type SessionAsk, type SessionRecord, type WorkspaceState } from "./api";
import type { SessionCalls, WorkspaceMap } from "./sessionMap";

export interface ShellState {
  ws: WorkspaceState;
  repos: RepoView[];
  sessions: SessionRecord[];
  approvals: HookRequest[];
  proposals: Proposal[] | null;        // null: the deployment has no connector
  asks: SessionAsk[];
  links: LinkSessions;
  history: HistorySession[];
  agentsWaiting: number;
  summary: boolean;
  down: boolean;
  /** a proxy in front of the app answering every request with this status and no body, as Caddy does when the app
   *  behind it is down; null for the app's own answers */
  gateway: number | null;
  /** the control view's map (sessionMap.ts), as the workspace gives it while it runs, and each session's calls */
  map: WorkspaceMap;
  sessionMaps: Record<string, SessionCalls>;
}

export const OWNER = { id: "u1", email: "owner@example.test", admin: true, can_sign_in_again: true };

export function link(over: Partial<LinkView> = {}): LinkView {
  return { id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
    last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null, events: 0,
    last_event: null, commands: [], stopped_at: null, ...over };
}

export function machineSession(over: Partial<HistorySession> = {}): HistorySession {
  return { id: "s1", title: "Make sign-in work", project: "/Users/me/code/demo", branch: "sign-in", client: "terminal", live: null,
    started_at: 1_790_000_000, last_at: 1_790_000_100, link_id: "L1", link: "MacBook Pro", kind: "local", listed_at: 1_790_000_200,
    repositories: ["/Users/me/code/demo"], ...over };
}

export function shellServer(over: Partial<ShellState> = {}) {
  const state: ShellState = {
    ws: { enabled: true, allowed: true, provider: "fly", running: false, made: true },
    repos: [], sessions: [], approvals: [], proposals: [], asks: [],
    links: { links: [], questions: [], public_url: "https://pp.example", asks: [] },
    history: [], agentsWaiting: 0, summary: true, down: false, gateway: null, map: { at: 1_790_000_000, sessions: [] },
    sessionMaps: {}, ...over,
  };
  const calls: string[] = [];
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const runs = () => !!(state.ws.enabled && state.ws.allowed !== false && state.ws.running && !state.ws.starting);
  const summary = () => ({
    workspace: state.ws,
    approvals: state.approvals.filter((r) => r.state === "pending").length,
    proposals: state.proposals ? proposalAlerts(state.proposals) : null,
    asks: runs() ? state.asks.length : null,
    links: { live: state.links.links.length, alerts: channelAlerts(state.links) },
    agents: state.ws.enabled && state.ws.allowed !== false ? state.agentsWaiting : null,
    stamps: {
      sessions: runs() ? JSON.stringify(state.sessions) : null,
      repos: runs() ? JSON.stringify(state.repos) : null,
      approvals: JSON.stringify(state.approvals),
      proposals: state.proposals ? JSON.stringify(state.proposals) : null,
      asks: runs() ? JSON.stringify(state.asks) : null,
      links: JSON.stringify(state.links),
      history: JSON.stringify(state.history),
    },
  });
  const routes: Record<string, () => Response> = {
    "GET /api/auth/me": () => json(OWNER),
    "GET /api/ade/summary": () => (state.summary ? json(summary()) : json({ detail: "Not Found" }, 404)),
    "GET /api/ade/workspace": () => json(state.ws),
    "POST /api/ade/workspace/start": () => { state.ws = { ...state.ws, running: true, made: true }; return json(state.ws); },
    "POST /api/ade/workspace/stop": () => { state.ws = { ...state.ws, running: false }; return json(state.ws); },
    "GET /api/ade/workspace/repos": () => (runs() ? json({ repos: state.repos }) : json({ detail: "not running" }, 409)),
    "GET /api/ade/workspace/sessions": () => (runs() ? json({ sessions: state.sessions }) : json({ detail: "not running" }, 409)),
    "GET /api/ade/workspace/asks": () => (runs() ? json({ on: false, asks: state.asks }) : json({ detail: "not running" }, 409)),
    "GET /api/ade/workspace/claude": () => json({ installed: true, signed_in: true }),
    "GET /api/ade/workspace/programs": () => json({ programs: { claude: true } }),
    "GET /api/ade/workspace/capabilities": () => json({}),
    "GET /api/ade/workspace/github": () => json({ installed: true, user: null, repos: [], capped: false }),
    "GET /api/ade/approvals": () => json({ requests: state.approvals }),
    "GET /api/connector/proposals": () => (state.proposals ? json({ proposals: state.proposals }) : json({ detail: "Not Found" }, 404)),
    "GET /api/ade/link/sessions": () => json(state.links),
    "GET /api/ade/link/history": () => json({ sessions: state.history }),
    "GET /api/ade/agents": () => json({ groups: [], waiting: Array.from({ length: state.agentsWaiting }, (_, i) => ({ run_id: `r${i}` })), live: 0 }),
    // the controller panel rides the control view: a controller not set up is quiet (SetUp, no alert), its one
    // region drawn, and the account picker there reads the owner's confirmed accounts
    "GET /api/ade/controller": () => json({ controller: null, requests: [], requirements: [], other_sessions: [], questions: [], decisions: [] }),
    "GET /api/ade/agents/accounts": () => json({ accounts: [] }),
  };
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init.method || "GET"} ${url.pathname}`;
    calls.push(key);
    if (state.down) throw new TypeError("Failed to fetch");
    if (state.gateway) return new Response(null, { status: state.gateway });
    // a session closed, or opened again: its record marked, or the mark taken away, as the workspace does
    const shut = /^POST \/api\/ade\/workspace\/sessions\/([0-9a-f]{12})\/(close|open)$/.exec(key);
    if (shut) {
      const at = state.sessions.findIndex((r) => r.id === shut[1]);
      if (!runs()) return json({ detail: "not running" }, 409);
      if (at < 0) return json({ detail: "no such session" }, 404);
      const { closed: _was, ...rest } = state.sessions[at];
      state.sessions[at] = shut[2] === "close" ? { ...rest, running: false, closed: true } : rest;
      return json(state.sessions[at]);
    }
    // the map's: the map while the workspace runs, and a session's calls where the map has the session
    if (key === "GET /api/ade/workspace/map") return runs() ? json(state.map) : json({ detail: "not running" }, 409);
    const opened = /^GET \/api\/ade\/workspace\/sessions\/([0-9a-f]{12})\/map$/.exec(key);
    if (opened) {
      if (!runs()) return json({ detail: "not running" }, 409);
      return state.sessionMaps[opened[1]] ? json(state.sessionMaps[opened[1]]) : json({ detail: "No such session." }, 404);
    }
    const route = routes[key];
    return route ? route() : json({ detail: `no route for ${key}` }, 404);
  });
  vi.stubGlobal("fetch", fetchStub);
  return {
    state,
    calls,
    /** the requests to the API since `from` (an index into calls) */
    api: (from = 0) => calls.slice(from).filter((c) => c.includes(" /api/")),
  };
}

/** What the page needs of a browser that jsdom does not have: media queries (a phone's width when `phone`; a touch
 *  screen when `touch`), the resize observer the panels use, and an element scrolled into view (the palette's). */
export function browser({ phone = false, touch = false } = {}) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: (phone && query.includes("max-width: 767px")) || (touch && query.includes("pointer: coarse")),
    media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  }));
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView ??= () => {};
}
