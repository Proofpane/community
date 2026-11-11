import { act, cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { popOutOf, popOutQuery, type PopOut } from "./popOut";
import {
  accountGroups, accountOfMap, agentName, agentsMoreWords, agentsReadWords, agentStatus, callsOf, effortWords, EVERY,
  FAILING_MOST, mapOf, mapState, mayPopOut, NAME_MOST, openPopOut, ranSeconds, ranWords, readWords, tokenWords, useTicking,
  useVisibleEvery,
} from "./sessionMap";
import { ACCOUNTS, AT, EXAMPLE_CALLS, EXAMPLE_MAP, fixtureCalls, fixtureMap, IDS, LONG_NAME } from "./sessionMapFixture";

// The control view's map, its words and its reads: what the page makes of the
// two routes' answers, each worked out here from the example's own numbers.

describe("the fixture", () => {
  it("has the examples' shapes exactly: each session, agent and call has the example's fields and no other", () => {
    const keys = (o: object) => Object.keys(o).sort();
    const map = fixtureMap();
    expect(keys(map)).toEqual(keys(EXAMPLE_MAP));
    for (const s of map.sessions) {
      expect(keys(s)).toEqual(keys(EXAMPLE_MAP.sessions[0]));
      for (const a of s.agents) expect(keys(a)).toEqual(keys(EXAMPLE_MAP.sessions[0].agents[0]));
    }
    const calls = fixtureCalls();
    expect(keys(calls)).toEqual(keys(EXAMPLE_CALLS));
    expect(keys(calls.main)).toEqual(keys(EXAMPLE_CALLS.main));
    for (const c of [...calls.main.calls, ...calls.agents.flatMap((a) => a.calls)]) expect(keys(c)).toEqual(keys(EXAMPLE_CALLS.main.calls[0]));
    for (const a of calls.agents) expect(keys(a)).toEqual(keys(EXAMPLE_CALLS.agents[0]));
    for (const t of calls.turns) expect(keys(t)).toEqual(keys(EXAMPLE_CALLS.turns[0]));
  });
});

describe("an answer, as the page keeps it", () => {
  it("is the named fields alone, so a field a workspace adds reaches nothing that draws", () => {
    const raw = { ...fixtureMap(), extra: "x", sessions: fixtureMap().sessions.map((s) => ({ ...s, prompt: "p",
      agents: s.agents.map((a) => ({ ...a, description: "d", last_assistant_message: "m" })) })) };
    expect(mapOf(raw)).toEqual(fixtureMap());
    const calls = { ...fixtureCalls(), text: "t", main: { ...fixtureCalls().main, calls: fixtureCalls().main.calls.map((c) => ({ ...c, input: { command: "rm" } })) } };
    expect(callsOf(calls)).toEqual(fixtureCalls());
  });

  it("is of the named types: what is not a list is empty, a value of another type none, an entry with no id left out", () => {
    const map = mapOf({ at: "soon", sessions: [
      { id: IDS.mine, title: 7, program: "claude", account: { kind: "claude" }, agents: null, context_tokens: "many", agents_read: "yes",
        effort: true, agents_more: "4" },
      { title: "no id" }, null, "a session",
      { id: IDS.chat, agents: [{ id: "a1", status: 3, tokens: Infinity, started_at: AT, effort: NaN }, { name: "no id" }] },
    ] }, AT);
    expect(map.at).toBe(AT);
    expect(map.sessions.map((s) => s.id)).toEqual([IDS.mine, IDS.chat]);
    expect(map.sessions[0]).toMatchObject({ title: "", account: null, agents: [], context_tokens: null, model: null, effort: null,
      agents_more: 0 });
    expect(map.sessions[1].agents).toEqual([{ id: "a1", name: null, agent_type: null, status: null, started_at: AT, ended_at: null,
      tokens: null, model: null, effort: null }]);
    expect(mapOf(null, AT)).toEqual({ at: AT, sessions: [] });
    expect(mapOf({ sessions: "none" }, AT).sessions).toEqual([]);
    const calls = callsOf({ main: { calls: [{ tool: "Read", failed: "yes" }], more: "12" }, agents: [{ calls: [] }, { id: "a1" }], turns: [{}], cut: 1 }, AT);
    expect(calls.main).toEqual({ calls: [{ tool: "Read", target: null, failed: false, at: null }], more: 0 });
    expect(calls.agents).toEqual([{ id: "a1", calls: [], more: 0 }]);
    expect(calls.turns).toEqual([{ agent: null, requests: 0, input_tokens: 0, output_tokens: 0 }]);
    expect(calls.cut).toBe(false);
  });

  it("keeps a numeric effort only where it is a whole number not below 0, which a budget's words can say", () => {
    const efforts = [31999, 0, 1234.5678, -31999, 1e21, 0.0001234, NaN, Infinity, "high", true, "31999"];
    const map = mapOf({ at: AT, sessions: [{ id: IDS.mine, effort: 1234.5, agents: efforts.map((effort, n) => ({ id: `a${n}`, effort })) }] }, AT);
    expect(map.sessions[0].agents.map((a) => a.effort)).toEqual([31999, 0, null, null, null, null, null, null, "high", null, "31999"]);
    expect(map.sessions[0].effort).toBeNull();
  });
});

describe("an answer's sessions", () => {
  it("are those whose id is a session's, twelve hex digits, so none gets a pop-out or a read under another name", () => {
    const ids = ["0123456789ab", "0123456789AB", "0123456789a", "0123456789abc", "../../sessions", "0123456789ab\n", ""];
    const map = mapOf({ sessions: ids.map((id) => ({ ...fixtureMap().sessions[3], id })) }, AT);
    expect(map.sessions.map((x) => x.id)).toEqual(["0123456789ab"]);
  });

  it("have the state the workspace gives, or else the one the list would give from the record's fields it gives instead", () => {
    const state = (fields: Record<string, unknown>) => mapOf({ sessions: [{ id: IDS.mine, program: "claude", ...fields }] }, AT).sessions[0].state;
    expect(state({ state: "idle", running: true, activity: { state: "working" } })).toBe("idle");         // its own word first
    expect(state({ running: true, activity: { state: "working", message: "WORDS" } })).toBe("working");
    expect(state({ running: true, activity: { state: "waiting" } })).toBe("waiting");
    expect(state({ running: true })).toBe("running");
    expect(state({ running: false, finished: { at: 1 } })).toBe("done");
    expect(state({ running: false, exit_code: -15 })).toBe("stopped");
    expect(state({ running: false, exit_code: 2 })).toBe("failed");
    expect(state({ program: "chat", chat: { state: "running" } })).toBe("working");
    expect(state({ program: "chat", chat: { state: "idle", asks: [{ name: "Bash", summary: "rm" }] } })).toBe("waiting");
    expect(state({})).toBe("");                                                    // neither: not known (the page says stopped)
    expect(mapState(state({}))).toBe("stopped");
    // only the state is kept, none of the fields it came from
    expect(JSON.stringify(mapOf({ sessions: [{ id: IDS.mine, running: true, activity: { state: "working", message: "WORDS" } }] }, AT))).not.toContain("WORDS");
  });
});

describe("an agent's row", () => {
  it("says how long it ran as Claude Code's map does, in minutes past the hour too", () => {
    expect([0, 18, 59, 60, 61, 3599, 3600, 20_778, 18.9].map(ranWords))
      .toEqual(["0s", "18s", "59s", "1m 0s", "1m 1s", "59m 59s", "60m 0s", "346m 18s", "18s"]);
    expect(ranWords(-5)).toBe("0s");
  });

  it("counts a running agent's time to now, an ended one's to its end, and says none it cannot know", () => {
    const now = AT + 10;
    expect(ranSeconds({ status: "running", started_at: AT - 20_778, ended_at: null }, now)).toBe(20_788);
    expect(ranSeconds({ status: "completed", started_at: AT - 3000, ended_at: AT - 1000 }, now)).toBe(2000);
    // an ended agent whose end is not known is never counted to now, which would grow while it is shown
    expect(ranSeconds({ status: "completed", started_at: AT - 3000, ended_at: null }, now)).toBeNull();
    expect(ranSeconds({ status: "killed", started_at: AT - 3000, ended_at: null }, now)).toBeNull();
    expect(ranSeconds({ status: "running", started_at: null, ended_at: null }, now)).toBeNull();
    // a start after the time it is counted to (the workspace's clock moved) is no time, never a negative one
    expect(ranSeconds({ status: "running", started_at: now + 5, ended_at: null }, now)).toBe(0);
  });

  it("says its tokens as \"200.6k tokens\": one decimal, k past a thousand, M past a million", () => {
    expect([0, 1, 950, 999, 1000, 1049, 1050, 200_000, 200_600, 724_500, 999_949, 999_950, 1_000_000, 1_234_567, 25_000_000].map(tokenWords))
      .toEqual(["0 tokens", "1 token", "950 tokens", "999 tokens", "1k tokens", "1k tokens", "1.1k tokens", "200k tokens",
        "200.6k tokens", "724.5k tokens", "999.9k tokens", "1M tokens", "1M tokens", "1.2M tokens", "25M tokens"]);
  });

  it("shows a name cut to 80 characters, by character, and a type or a word where it has none", () => {
    expect(NAME_MOST).toBe(80);
    expect(agentName({ name: LONG_NAME, agent_type: null })).toBe(LONG_NAME.slice(0, 80));
    expect(agentName({ name: "x".repeat(80), agent_type: null })).toBe("x".repeat(80));
    // a character outside the first plane is one character, never cut in half
    const astral = String.fromCodePoint(0x1f600);
    const name = `${"y".repeat(79)}${astral}${astral}`;
    expect(agentName({ name, agent_type: null })).toBe(`${"y".repeat(79)}${astral}`);
    expect(Array.from(agentName({ name, agent_type: null }))).toHaveLength(80);
    expect(agentName({ name: "  Explore the routes  ", agent_type: null })).toBe("Explore the routes");
    expect(agentName({ name: "", agent_type: "Explore" })).toBe("An agent (Explore)");
    expect(agentName({ name: null, agent_type: null })).toBe("An agent");
  });

  it("has a dot of one colour while it runs, another once it ended, a third when it failed or was killed, each with words", () => {
    const running = agentStatus("running"), completed = agentStatus("completed"), failed = agentStatus("failed"), killed = agentStatus("killed");
    expect([running.label, completed.label, failed.label, killed.label]).toEqual(["Running", "Completed", "Failed", "Killed"]);
    expect(new Set([running.color, completed.color, failed.color]).size).toBe(3);
    expect(killed.color).toBe(failed.color);
    const unknown = agentStatus("paused");
    expect(unknown.label).toBe("Status not known");
    expect([running.color, completed.color, failed.color]).not.toContain(unknown.color);
    // none, as the workspace gives it for an agent not known to be running, is not known either
    expect(agentStatus(null)).toEqual(unknown);
  });

  it("says an effort that is a token budget as the effort, its number exact, and a level as the workspace gave it", () => {
    expect(effortWords("high")).toBe("high");
    expect([31999, 16000, 1_234_567, 999, 1, 0].map(effortWords)).toEqual(["effort: 31,999-token budget",
      "effort: 16,000-token budget", "effort: 1,234,567-token budget", "effort: 999-token budget", "effort: 1-token budget",
      "effort: 0-token budget"]);
    expect(effortWords(null)).toBeNull();
    expect(effortWords("")).toBeNull();
  });
});

describe("a session's node", () => {
  it("takes the list's states, and calls one it does not know stopped", () => {
    for (const s of ["starting", "working", "running", "waiting", "idle", "done", "failed", "stopped"]) expect(mapState(s)).toBe(s);
    expect(mapState("thinking")).toBe("stopped");
  });

  it("says in words why its agents, calls or turns are not shown, never an empty list as none", () => {
    expect(agentsReadWords("yes")).toBeNull();
    const said = ["version", "missing", "no", "later"].map(agentsReadWords);
    expect(said.every((w) => typeof w === "string" && w.length > 0)).toBe(true);
    expect(new Set(said).size).toBe(4);
    expect(readWords("yes", "calls")).toBeNull();
    expect(readWords("missing", "calls")).toMatch(/^Its tool calls /);
    expect(readWords("version", "turns")).toMatch(/^Its turns /);
  });

  it("says how many of its agents the workspace left out, as a session's calls past the cut are said", () => {
    expect([[3, 2], [1, 50], [2, 0], [1, 0], [0, 3], [-1, 0], [NaN, 1]].map(([more, shown]) => agentsMoreWords(more, shown)))
      .toEqual(["and 3 more agents, not shown", "and 1 more agent, not shown", "2 agents, not shown", "1 agent, not shown",
        null, null, null]);
  });
});

describe("the accounts", () => {
  it("name each session's account as the list names it, and group the map by them, each program's own sign-in first", () => {
    const groups = accountGroups(fixtureMap().sessions, ACCOUNTS);
    expect(groups.map((g) => [g.kind, g.name, g.sessions.map((s) => s.id)])).toEqual([
      ["claude", "Default", [IDS.mine, IDS.missing]],
      ["codex", "Default", [IDS.codex]],
      ["claude", "Alma", [IDS.chat]],
      ["claude", "another account", [IDS.old]],
      [null, "On no account", [IDS.shell]],
    ]);
  });

  it("keep each program's own sign-in first under a name of its own, ahead of an added account's that sorts before it", () => {
    const renamed = { ...ACCOUNTS, claude: { ...ACCOUNTS.claude, accounts: [{ ...ACCOUNTS.claude.accounts[0], name: "Zed" }, ACCOUNTS.claude.accounts[1]] } };
    const groups = accountGroups(fixtureMap().sessions, renamed);
    expect(groups.map((g) => g.name)).toEqual(["Default", "Zed", "Alma", "another account", "On no account"]);
    expect(groups.map((g) => g.key)).toEqual(["codex:default", "claude:default", "claude:aaaaaaaaaaaa", "claude:bbbbbbbbbbbb", "none"]);
  });

  it("name the program's own sign-in by its name, given or not, where the list would say none", () => {
    const own = { account: { kind: "claude", id: "default" }, program: "claude" };
    const renamed = { ...ACCOUNTS, claude: { ...ACCOUNTS.claude, accounts: [{ ...ACCOUNTS.claude.accounts[0], name: "Lena" }] } };
    expect(accountOfMap(own, renamed)?.name).toBe("Lena");          // one account: the list names none, the map names it
    expect(accountOfMap(own, null)?.name).toBe("Default");            // the accounts not read yet
    expect(accountOfMap({ account: { kind: "claude", id: "aaaaaaaaaaaa" }, program: "claude" }, null)?.name).toBe("another account");
    expect(accountOfMap({ account: null, program: "shell" }, ACCOUNTS)).toBeNull();
    // the same id on Claude Code and on Codex is two accounts
    expect(accountOfMap({ account: { kind: "codex", id: "default" }, program: "codex" }, ACCOUNTS)?.key)
      .not.toBe(accountOfMap(own, ACCOUNTS)?.key);
  });
});

describe("a pop-out's address", () => {
  it("names the map, a session's map or an agent, and reads back as it was written", () => {
    const each: PopOut[] = [{ view: "map" }, { view: "session", session: IDS.mine }, { view: "agent", session: IDS.mine, agent: "a1_B-2" }];
    for (const p of each) expect(popOutOf(popOutQuery(p))).toEqual(p);
    expect(popOutQuery(each[2])).toBe(`?pop=agent&session=${IDS.mine}&agent=a1_B-2`);
  });

  it("is no pop-out where it names what is not a session or an agent", () => {
    for (const q of ["", "?pop=", "?pop=main", "?pop=session", "?pop=session&session=0123456789AB", "?pop=session&session=0123456789a",
      `?pop=agent&session=${IDS.mine}`, `?pop=agent&session=${IDS.mine}&agent=a%2Fb`, `?pop=agent&session=${IDS.mine}&agent=${"a".repeat(65)}`,
      "?session=0123456789ab"]) {
      expect(popOutOf(q), q).toBeNull();
    }
  });

  it("is offered on a computer, never on a phone's width, nor in the phone app", async () => {
    expect(mayPopOut(false)).toBe(true);
    expect(mayPopOut(true)).toBe(false);
    vi.stubEnv("MODE", "phone");
    vi.resetModules();
    try {
      const inThePhoneApp = await import("./sessionMap");
      expect(inThePhoneApp.mayPopOut(false)).toBe(false);
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });

  it("opens a browser tab on the same page, or asks the desktop app for a window of its own", async () => {
    const open = vi.fn(() => ({}) as Window);
    await openPopOut({ view: "agent", session: IDS.mine, agent: "a1" }, { open });
    // a tab in a browser: no features asked for, which would make it a window of its own
    expect(open).toHaveBeenCalledWith(`/?pop=agent&session=${IDS.mine}&agent=a1`, `pp-agent-${IDS.mine}-a1`);
    await openPopOut({ view: "map" }, { open });
    expect(open).toHaveBeenLastCalledWith("/?pop=map", "pp-map");
    await expect(openPopOut({ view: "map" }, { open: () => null })).rejects.toThrow(/allow pop-ups/);
    const invoke = vi.fn(async () => undefined);
    await openPopOut({ view: "session", session: IDS.mine }, { invoke });
    expect(invoke).toHaveBeenCalledWith("open_popout", { view: "session", session: IDS.mine, agent: null });
    await openPopOut({ view: "map" }, { invoke });
    expect(invoke).toHaveBeenLastCalledWith("open_popout", { view: "map", session: null, agent: null });
  });
});

describe("the reads", () => {
  let visible = true;
  beforeEach(() => {
    vi.useFakeTimers();
    visible = true;
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (visible ? "visible" : "hidden") });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  });
  const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
  const show = async (on: boolean) => { visible = on; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); }); };

  function Reader({ on, read }: { on: boolean; read: (s: AbortSignal) => Promise<boolean | void> }) {
    useVisibleEvery(on, EVERY, read);
    return null;
  }

  it("ask at once and every 3 seconds while the page is visible, nothing while it is hidden, and at once when it shows", async () => {
    const read = vi.fn(async () => true);
    render(createElement(Reader, { on: true, read }));
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(2999);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(6000);
    expect(read).toHaveBeenCalledTimes(4);
    await show(false);
    await pass(60_000);
    expect(read).toHaveBeenCalledTimes(4);
    await show(true);
    expect(read).toHaveBeenCalledTimes(5);
    await pass(3000);
    expect(read).toHaveBeenCalledTimes(6);
  });

  it("ask one at a time, the next 3 seconds after the last is answered", async () => {
    let answer = () => {};
    const read = vi.fn(() => new Promise<boolean>((r) => { answer = () => r(true); }));
    render(createElement(Reader, { on: true, read }));
    await pass(10_000);
    expect(read).toHaveBeenCalledTimes(1);
    await show(false);
    await show(true);                                         // shown again while the read is out: no second one
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => { answer(); });
    await pass(2999);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("wait twice as long after each read that failed, up to 30 seconds, and 3 seconds again once one does not", async () => {
    let ok = false;
    const at: number[] = [];
    const read = vi.fn(async () => { at.push(Date.now()); return ok; });
    const start = Date.now();
    render(createElement(Reader, { on: true, read }));
    await pass(0);
    await pass(6000 + 12_000 + 24_000 + 30_000 + 30_000);
    expect(at.map((t) => t - start)).toEqual([0, 6000, 18_000, 42_000, 72_000, 102_000]);
    expect(FAILING_MOST).toBe(30_000);
    ok = true;
    await pass(30_000);
    await pass(3000);
    expect(read).toHaveBeenCalledTimes(8);
    // a read that throws is one that failed too
    const throwing = vi.fn(async () => { throw new Error("no"); });
    cleanup();
    render(createElement(Reader, { on: true, read: throwing }));
    await pass(0);
    await pass(5999);
    expect(throwing).toHaveBeenCalledTimes(1);
    await pass(1);
    expect(throwing).toHaveBeenCalledTimes(2);
  });

  it("ask nothing while off, and stop with the read under way aborted once the part goes", async () => {
    const read = vi.fn(async () => true);
    const { rerender } = render(createElement(Reader, { on: false, read }));
    await pass(10_000);
    expect(read).not.toHaveBeenCalled();
    let signal: AbortSignal | undefined;
    const held = vi.fn((s: AbortSignal) => { signal = s; return new Promise<boolean>(() => {}); });
    rerender(createElement(Reader, { on: true, read: held }));
    await pass(0);
    expect(held).toHaveBeenCalledTimes(1);
    cleanup();
    expect(signal?.aborted).toBe(true);
  });

  it("tick the time each second while on and visible, and not while hidden or off", async () => {
    const seen: number[] = [];
    function Clock({ on }: { on: boolean }) { seen.push(Math.floor(useTicking(on))); return null; }
    const { rerender } = render(createElement(Clock, { on: true }));
    const first = seen[seen.length - 1];
    for (let i = 1; i <= 3; i++) {
      await pass(1000);
      expect(seen[seen.length - 1] - first).toBe(i);
    }
    await show(false);
    await pass(5000);
    expect(seen[seen.length - 1] - first).toBe(3);
    await show(true);
    await pass(1000);
    expect(seen[seen.length - 1] - first).toBe(9);
    rerender(createElement(Clock, { on: false }));
    const off = seen[seen.length - 1];
    await pass(5000);
    expect(seen[seen.length - 1]).toBe(off);
  });
});
