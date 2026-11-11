// The control view's map in a test: sessions and agents made from the examples
// (sessionMapExamples.json, which the server's tests answer with too), each changed only in its values, so every object
// has exactly the example's fields. Three accounts (Claude's own sign-in, an added Claude account, Codex's own), one the
// page does not know, and a session on none; agents in every status the status line gives.
import examples from "./sessionMapExamples.json";
import type { AccountsView } from "./api";
import type { MapAgent, MapSession, SessionCalls, WorkspaceMap } from "./sessionMap";

export const EXAMPLE_MAP = examples.map as WorkspaceMap;
export const EXAMPLE_CALLS = examples.session_map as SessionCalls;
const SESSION = EXAMPLE_MAP.sessions[0];
const AGENT = SESSION.agents[0];

/** The workspace's time the map was read at, in the fixture. */
export const AT = 1_760_730_000;
export const IDS = { mine: "0123456789ab", chat: "11111111111a", codex: "22222222222b", shell: "33333333333c", old: "44444444444d", missing: "55555555555e" };
/** A name of a model's words, longer than the 80 characters the map shows of it. */
export const LONG_NAME = "Read every route the workspace serves and say which of them could leak a token into a log, with the line";

export const agent = (over: Partial<MapAgent>): MapAgent => ({ ...AGENT, ...over });
export const session = (over: Partial<MapSession>): MapSession => ({ ...SESSION, agents: [], ...over });

export function fixtureMap(at = AT): WorkspaceMap {
  return {
    at,
    sessions: [
      session({ id: IDS.mine, title: "Make the map", program: "claude", account: { kind: "claude", id: "default" }, state: "working",
        context_tokens: 724_500, effort: "max", model: "claude-opus-5-5", agents: [
          agent({ id: "a1", name: "Explore the routes", agent_type: "Explore", status: "running", started_at: AT - 20_778, ended_at: null, tokens: 200_600, effort: "high" }),
          agent({ id: "a2", name: LONG_NAME, agent_type: "general-purpose", status: "completed", started_at: AT - 3000, ended_at: AT - 1000, tokens: 1500, effort: null, model: null }),
        ] }),
      session({ id: IDS.chat, title: "Ask about the gate", program: "chat", account: { kind: "claude", id: "aaaaaaaaaaaa" }, state: "idle",
        context_tokens: 950, agents: [
          agent({ id: "b1", name: "Run the tests", agent_type: "general-purpose", status: "failed", started_at: AT - 500, ended_at: AT - 400, tokens: 950 }),
          agent({ id: "b2", name: "Stopped halfway", agent_type: null, status: "killed", started_at: AT - 60, ended_at: AT - 30, tokens: 1_234_567 }),
        ] }),
      session({ id: IDS.codex, title: "Port the parser", program: "codex", account: { kind: "codex", id: "default" }, state: "running",
        model: null, effort: null, context_tokens: null, agents_read: "no" }),
      session({ id: IDS.shell, title: "Shell", program: "shell", account: null, state: "running", model: null, effort: null,
        context_tokens: null, agents_read: "no" }),
      session({ id: IDS.old, title: "An old Claude Code", program: "claude", account: { kind: "claude", id: "bbbbbbbbbbbb" }, state: "idle",
        agents_read: "version" }),
      session({ id: IDS.missing, title: "Just started", program: "claude", account: { kind: "claude", id: "default" }, state: "starting",
        agents_read: "missing", context_tokens: null }),
    ],
  };
}

/** The opened session's calls and turns, in the second route's shape. */
export function fixtureCalls(): SessionCalls {
  return {
    ...EXAMPLE_CALLS,
    session: IDS.mine,
    main: { calls: [{ tool: "Read", target: "src/a.py", failed: false, at: AT - 100 }, { tool: "Bash", target: "npm", failed: true, at: AT - 90 }], more: 0 },
    agents: [
      { id: "a1", calls: [{ tool: "Grep", target: "src", failed: false, at: AT - 50 }, { tool: "Edit", target: "src/b.py", failed: false, at: AT - 40 }], more: 12 },
      { id: "a2", calls: [], more: 0 },
    ],
    turns: [{ agent: null, requests: 3, input_tokens: 1200, output_tokens: 300 }, { agent: "a1", requests: 5, input_tokens: 4000, output_tokens: 1000 }],
  };
}

const row = (id: string, name: string) => ({ id, name, created_at: null, sessions: 0, status: null, usage: null });
/** The workspace's accounts, as the list names them: Claude's own and Alma, and Codex's own. */
export const ACCOUNTS: AccountsView = {
  claude: { active: "default", accounts: [row("default", "Default"), row("aaaaaaaaaaaa", "Alma")] },
  codex: { active: "default", accounts: [row("default", "Default")] },
};
