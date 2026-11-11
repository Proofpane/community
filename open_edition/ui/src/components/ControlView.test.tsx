import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GrantWideningView, LinkSessions, LinkView, SessionRecord } from "../api";
import { ranWords, tokenWords, type MapAgent, type SessionCalls, type WorkspaceMap } from "../sessionMap";
import { ACCOUNTS, AT, EXAMPLE_MAP, fixtureCalls, fixtureMap, IDS, LONG_NAME } from "../sessionMapFixture";
import { browser } from "../shellTestkit";
import { useStore } from "../store";
import { ControlView } from "./ControlView";
import { PopOutPage } from "./PopOut";

// The control view: the map of every session and its agents, drawn from the app's two routes (a fixture made from
// their examples), on fake timers. A server of the test's own answers the map at the workspace's time, which runs on
// with the test's clock.

let visible = true;
let server: { map: WorkspaceMap | null; calls: Record<string, SessionCalls>; asked: string[]; failing: number | null; started: number;
  sessions: SessionRecord[] };

// What the page shows, and what it keeps in an attribute (a tooltip's among them): the document's text and every
// attribute's value, as they stand. A test that a value is not shown reads this.
const drawn = () => [document.body.textContent ?? "",
  ...Array.from(document.body.querySelectorAll("*"), (el) => Array.from(el.attributes, (a) => a.value)).flat()].join("\n");

function serve() {
  server = { map: null, calls: { [IDS.mine]: fixtureCalls() }, asked: [], failing: null, started: Date.now(), sessions: [] };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input), "http://localhost").pathname;
    server.asked.push(path);
    const signedInAs = String((init.headers as Record<string, string> | undefined)?.Authorization ?? "");
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    if (server.failing) return json({ detail: "The workspace is not answering." }, server.failing);
    if (path === "/api/auth/me") return json(signedInAs.includes("other") ? { id: "u2", email: "other@example.test" } : { id: "u1", email: "owner@example.test" });
    if (path === "/api/ade/workspace/accounts") return json(ACCOUNTS);
    if (path === "/api/ade/workspace/sessions") return json({ sessions: server.sessions });
    // the controller panel shares this view and reads its own routes; a controller not set up is quiet (no
    // alert, and none of the nested goal/sub-goal/question regions), so the map's tests see only the map plus the
    // panel's own "The Manager" region, which it always draws
    if (path === "/api/ade/controller") return json({ controller: null, requests: [], requirements: [], other_sessions: [], questions: [], decisions: [] });
    if (path === "/api/ade/agents/accounts") return json({ accounts: [] });
    // the workspace's time runs on from AT with the test's clock: a running agent's time is counted to it
    if (path === "/api/ade/workspace/map") return json({ ...(server.map ?? fixtureMap()), at: AT + Math.floor((Date.now() - server.started) / 1000) });
    const one = /^\/api\/ade\/workspace\/sessions\/([0-9a-f]{12})\/map$/.exec(path);
    if (one) return server.calls[one[1]] ? json(server.calls[one[1]]) : json({ detail: "No such session." }, 404);
    return json({ detail: `no route for ${path}` }, 404);
  }));
}

beforeEach(() => {
  vi.useFakeTimers();
  visible = true;
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (visible ? "visible" : "hidden") });
  localStorage.setItem("pp-open-token", "test-token.sig");
  browser();
  serve();
  useStore.setState({ accounts: ACCOUNTS });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.title = "";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  useStore.setState(useStore.getInitialState());
  vi.unstubAllEnvs();
});

const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const show = async (on: boolean) => { visible = on; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); }); };
const asked = (path: string) => server.asked.filter((p) => p === path).length;
const MAP = "/api/ade/workspace/map";
const CALLS = `/api/ade/workspace/sessions/${IDS.mine}/map`;
const ACCOUNTS_ROUTE = "/api/ade/workspace/accounts";
const SESSIONS_ROUTE = "/api/ade/workspace/sessions";
// the control view's own alert (the map's), not the controller panel's, which sits in its own region: when the
// workspace is down both read fail, so the map's alert is the one outside that region
const mapAlert = () => screen.getAllByRole("alert").find((a) => !a.closest('[aria-label="The Manager"]'))!;
const node = (title: string) => screen.getByRole("listitem", { name: title });
const agentRow = (session: string, name: string) =>
  within(within(node(session)).getByRole("list", { name: "Its agents" })).getAllByRole("listitem").find((li) => li.textContent?.includes(name))!;

describe("the map", () => {
  it("draws every account with its sessions, named as the list names them, each agent a row as Claude Code's map draws it", async () => {
    render(<ControlView />);
    await pass();
    const regions = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(regions).toEqual(["The control view", "The Manager", "Default, a Claude account", "Default, a Codex account", "Alma, a Claude account",
      "another account, a Claude account", "Sessions on no account"]);
    const own = screen.getByRole("region", { name: "Default, a Claude account" });
    expect(within(own).getAllByRole("listitem").filter((li) => li.getAttribute("aria-label")).map((li) => li.getAttribute("aria-label")))
      .toEqual(["Make the map", "Just started"]);
    expect(within(screen.getByRole("region", { name: "Alma, a Claude account" })).getByRole("listitem", { name: "Ask about the gate" })).toBeTruthy();
    // a session's node: its title, program, model, effort, state and tokens in context
    expect(node("Make the map").textContent).toContain("Claude Code · claude-opus-5-5 · max · Working · 724.5k tokens in context");
    expect(node("Ask about the gate").textContent).toContain("Claude chat · the model the session last reported, or null · max · Idle · 950 tokens in context");
    // each agent: its status dot with words, its name, how long it ran or has run, its tokens
    const a1 = agentRow("Make the map", "Explore the routes");
    expect(within(a1).getByRole("img", { name: "Running" })).toBeTruthy();
    expect(a1.textContent).toContain("346m 18s");
    expect(a1.textContent).toContain("200.6k tokens");
    expect(a1.textContent).toContain("Explore");
    const a2 = agentRow("Make the map", LONG_NAME.slice(0, 80));
    expect(within(a2).getByRole("img", { name: "Completed" })).toBeTruthy();
    expect(a2.textContent).toContain("33m 20s");
    expect(a2.textContent).toContain("1.5k tokens");
    expect(a2.textContent).not.toContain(LONG_NAME.slice(0, 81));
    expect(drawn()).not.toContain(LONG_NAME.slice(0, 81));   // nor in an attribute, its tooltip's among them
    const b1 = agentRow("Ask about the gate", "Run the tests");
    expect(within(b1).getByRole("img", { name: "Failed" })).toBeTruthy();
    expect(b1.textContent).toContain("1m 40s");
    expect(b1.textContent).toContain("950 tokens");
    const b2 = agentRow("Ask about the gate", "Stopped halfway");
    expect(within(b2).getByRole("img", { name: "Killed" })).toBeTruthy();
    expect(b2.textContent).toContain("30s");
    expect(b2.textContent).toContain("1.2M tokens");
  });

  it("says the agents the workspace left out after those it shows, and a token budget as the effort, for a session and an agent", async () => {
    // the examples' second session, as the stand-in serves it: its effort and its agent's each a token budget,
    // and three agents left out past those the workspace gave (agents_more); the first, one left out
    const [one, budget] = EXAMPLE_MAP.sessions;
    server.map = { at: AT, sessions: [budget, { ...one, agents_more: 1 }] };
    render(<ControlView />);
    await pass();
    expect(node(budget.title).textContent).toContain(" · effort: 31,999-token budget · Working · 120k tokens in context");
    expect(agentRow(budget.title, "an agent whose effort is a token budget").textContent).toContain("effort: 16,000-token budget");
    for (const [s, words] of [[budget, "and 3 more agents, not shown"], [one, "and 1 more agent, not shown"]] as const) {
      // after the list of its agents, and no item of it: a screen reader counts only the agents as its items
      const list = within(node(s.title)).getByRole("list", { name: "Its agents" });
      const more = within(node(s.title)).getByText(words);
      expect(list.contains(more)).toBe(false);
      expect(list.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(list).getAllByRole("listitem")).toHaveLength(s.agents.length);
    }
    expect(node(one.title).textContent).toContain(" · max · Working · ");
    expect(agentRow(one.title, "its name, or else its description").textContent).toContain("high");
  });

  it("says the agents left out of a session whose answer shows none of its agents, which the workspace's own agent never gives", async () => {
    const [one] = EXAMPLE_MAP.sessions;
    server.map = { at: AT, sessions: [{ ...one, agents: [], agents_more: 2 },
      { ...one, id: "0123456789ad", title: "One left out", agents: [], agents_more: 1 }] };
    render(<ControlView />);
    await pass();
    // with none drawn before them, never "and ... more"
    expect(within(node(one.title)).getByText("2 agents, not shown")).toBeTruthy();
    expect(within(node("One left out")).getByText("1 agent, not shown")).toBeTruthy();
    expect(within(node(one.title)).queryByRole("list", { name: "Its agents" })).toBeNull();
  });

  it("lets a budget's words give way first on a narrow width, so that the agent's name keeps its room", async () => {
    // the page's tests cannot measure a width (jsdom draws nothing), so this holds the classes and the style that do it:
    // the budget's words may shrink, far sooner than the name, and are cut; the name's room is its own words' width. In
    // the installed Chrome at 390 pixels the name had none without them, and keeps 40 pixels with them
    const [one, budget] = EXAMPLE_MAP.sessions;
    server.map = { at: AT, sessions: [budget, one] };
    render(<ControlView />);
    await pass();
    const row = agentRow(budget.title, "an agent whose effort is a token budget");
    const effort = within(row).getByText("effort: 16,000-token budget");
    expect(effort.className.split(" ")).toEqual(expect.arrayContaining(["min-w-0", "truncate"]));
    expect(effort.className).not.toContain("flex-none");
    expect(Number(effort.style.flexShrink)).toBeGreaterThanOrEqual(100);
    expect(effort.getAttribute("title")).toBe("effort: 16,000-token budget");
    const name = within(row).getByText("an agent whose effort is a token budget");
    expect(name.className.split(" ")).toEqual(expect.arrayContaining(["min-w-0", "flex-auto", "truncate"]));
    // a level's words are as short as before, and stay whole as they were
    expect(within(agentRow(one.title, "its name, or else its description")).getByText("high").className).toContain("flex-none");
  });

  it("names an agent whose name is blank by its type, said once", async () => {
    const map = fixtureMap();
    map.sessions[0].agents[0] = { ...map.sessions[0].agents[0], name: "   " };
    server.map = map;
    render(<ControlView />);
    await pass();
    expect(agentRow("Make the map", "An agent (Explore)").textContent?.match(/Explore/g)).toHaveLength(1);
  });

  it("says no agents left out where the workspace left none out, nor an effort it gave none of", async () => {
    render(<ControlView />);
    await pass();
    expect(document.body.textContent).not.toMatch(/more agents?\b|token budget/);
    expect(agentRow("Make the map", LONG_NAME.slice(0, 80)).textContent).not.toContain("null");
  });

  it("draws the map as the real agent served it: a status not known said so, no time where its end is not known, a call naming nothing drawn with no target, and no \"null\" anywhere", async () => {
    // tests/fixtures/session-map-served.json: what the app's two routes answered with the real workspace agent behind
    // them (open_edition/tests/test_workspace_map_served.py), its ids and times fixed
    const served = JSON.parse(readFileSync(join(__dirname, "..", "..", "..", "tests", "fixtures", "session-map-served.json"), "utf8"));
    // this test's server gives the workspace's time from AT, which must be the file's own for its times to mean the same
    expect(served.map.at).toBe(AT);
    server.map = served.map;
    for (const m of served.session_maps) server.calls[m.session] = m;
    render(<ControlView />);
    await pass();
    const agentOf = (id: string) => served.map.sessions[1].agents.find((a: MapAgent) => a.id === id) as MapAgent;
    const p1 = agentOf("agent-p1");                                          // its status not known, and no end
    const p1Row = agentRow("Plan on a budget", "Read part agent-p1");
    expect(within(p1Row).getByRole("img", { name: "Status not known" })).toBeTruthy();
    expect(p1Row.textContent).toBe(["Read part agent-p1", "Explore", "medium", tokenWords(p1.tokens!)].join(""));   // no time
    const e1 = agentOf("agent-e1");                                          // its status not known, with an end
    const e1Row = agentRow("Plan on a budget", "ended well");
    expect(within(e1Row).getByRole("img", { name: "Status not known" })).toBeTruthy();
    expect(e1Row.textContent).toBe(["ended well", "Explore", "high", ranWords(e1.ended_at! - e1.started_at!), tokenWords(e1.tokens!)].join(""));
    expect(agentRow("Plan on a budget", "on a budget").textContent).toContain("effort: 31,999-token budget");
    expect(within(node("Plan on a budget")).getByText("and 4 more agents, not shown")).toBeTruthy();
    // an agent only the hook saw: named by its type, which is not said twice
    expect(agentRow("Map the parser", "An agent (Plan)").textContent).toMatch(/^An agent \(Plan\)(\d+m )?\d+s$/);
    fireEvent.click(within(node("Map the parser")).getByRole("button", { name: "Show its calls" }));
    await pass();
    // its calls name nothing: each drawn as its tool alone
    const b2 = agentRow("Map the parser", "Plan the change");
    expect(within(b2).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Bash", "Bash", "Bash"]);
    expect(drawn()).not.toContain("null");                  // in an attribute neither
  });

  it("shows sessions with no agents as nodes alone, and says why where their agents were not read", async () => {
    render(<ControlView />);
    await pass();
    for (const title of ["Port the parser", "Shell"]) {
      expect(within(node(title)).queryByRole("list", { name: "Its agents" })).toBeNull();
      expect(node(title).textContent).toContain("Not a Claude Code session, so it has no agents to show.");
    }
    expect(node("An old Claude Code").textContent).toContain("Its Claude Code is too old to tell its agents to a status line.");
    expect(node("Just started").textContent).toContain("Its agents could not be read: their file is not there yet.");
    expect(node("Make the map").textContent).not.toMatch(/could not be read|too old|Not a Claude Code/);
  });

  it("never counts a running agent's time back, where an answer's whole second is behind what was counted on", async () => {
    render(<ControlView />);
    await pass();
    const time = () => /(\d+m \d+s)/.exec(agentRow("Make the map", "Explore the routes").textContent || "")?.[1];
    await pass(2900);                                                    // counted on to 346m 20s from the first answer
    expect(time()).toBe("346m 20s");
    server.map = { ...fixtureMap(), at: AT + 1 };                        // the next answer's second is behind it
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(server.map), { status: 200 })));
    await pass(200);
    expect(time()).toBe("346m 20s");
  });

  it("follows a step back past the 2 seconds an answer can be behind, rather than holding the time for it", async () => {
    render(<ControlView />);
    await pass();
    const time = () => /(\d+m \d+s)/.exec(agentRow("Make the map", "Explore the routes").textContent || "")?.[1];
    await pass(2900);
    expect(time()).toBe("346m 20s");
    server.map = { ...fixtureMap(), at: AT - 1 };                        // 3 seconds behind what was counted on
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(server.map), { status: 200 })));
    await pass(200);
    expect(time()).toBe("346m 17s");
  });

  it("follows a workspace whose own time went back further than an answer's rounding, rather than waiting for it", async () => {
    render(<ControlView />);
    await pass();
    const time = () => /(\d+m \d+s)/.exec(agentRow("Make the map", "Explore the routes").textContent || "")?.[1];
    await pass(2900);
    expect(time()).toBe("346m 20s");
    server.map = { ...fixtureMap(), at: AT - 60 };                       // its clock set back a minute
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(server.map), { status: 200 })));
    await pass(200);
    expect(time()).toBe("345m 18s");
    await pass(1000);
    expect(time()).toBe("345m 19s");                                     // and counts on from there
  });

  it("counts a running agent's time each second, to the workspace's time, and an ended one's not at all", async () => {
    vi.setSystemTime(new Date("2031-05-01T00:00:00Z"));           // this device's clock, years from the workspace's
    serve();
    render(<ControlView />);
    await pass();
    const time = (name: string) => /(\d+m \d+s)/.exec(agentRow("Make the map", name).textContent || "")?.[1];
    expect(time("Explore the routes")).toBe("346m 18s");
    await pass(1000);
    expect(time("Explore the routes")).toBe("346m 19s");
    await pass(2000);                                              // and across a read of the map, which brings its own time
    expect(time("Explore the routes")).toBe("346m 21s");
    await pass(4000);
    expect(time("Explore the routes")).toBe("346m 25s");
    expect(time(LONG_NAME.slice(0, 80))).toBe("33m 20s");
  });

  it("is asked for every 3 seconds while the page is visible, never while it is hidden, and at once when it shows", async () => {
    render(<ControlView />);
    await pass();
    expect(asked(MAP)).toBe(1);
    await pass(3000);
    expect(asked(MAP)).toBe(2);
    await pass(3000);
    expect(asked(MAP)).toBe(3);
    await show(false);
    await pass(60_000);
    expect(asked(MAP)).toBe(3);
    await show(true);
    expect(asked(MAP)).toBe(4);
    await pass(3000);
    expect(asked(MAP)).toBe(5);
  });

  it("says why it is not drawn, and asks less often while it fails", async () => {
    server.failing = 502;
    render(<ControlView empty={<p>Get started</p>} />);
    await pass();
    expect(mapAlert().textContent).toBe("The workspace is not answering.");
    expect(screen.getByText("Get started")).toBeTruthy();
    await pass(6000 + 12_000);
    expect(asked(MAP)).toBe(3);
    server.failing = null;
    await pass(24_000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(node("Make the map")).toBeTruthy();
    // a workspace from before the map: the server says so in its own words, which the page shows as they are
    const notYet = "This workspace does not draw the map yet: it comes with a later version of the workspace.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: notYet }), { status: 404 })));
    await pass(3000);
    expect(mapAlert().textContent).toBe(notYet);
    // a server from before the map, which has no route for it at all and says only Not Found (a newer page's)
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
    await pass(30_000);
    expect(mapAlert().textContent).toBe("This server does not have the map yet: it comes with a later version of Proofpane Community.");
  });

  it("shows the first steps only once its first read is in, never while it waits for it", async () => {
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })));
    render(<ControlView empty={<p>Get started</p>} />);
    await pass(10_000);
    expect(screen.getByLabelText("Reading the map")).toBeTruthy();
    expect(screen.queryByText("Get started")).toBeNull();
    await act(async () => { answer(new Response(JSON.stringify({ at: AT, sessions: [] }), { status: 200 })); });
    await pass();
    expect(screen.getByText("Get started")).toBeTruthy();
  });

  it("keeps what it drew through a read that fails for another reason, and through a 401 answered to an older sign-in", async () => {
    render(<ControlView empty={<p>Get started</p>} />);
    await pass();
    server.failing = 502;
    await pass(3000);
    expect(mapAlert().textContent).toBe("The workspace is not answering.");
    expect(node("Make the map")).toBeTruthy();
    expect(screen.queryByText("Get started")).toBeNull();                 // no first steps beside a map it drew
    server.failing = null;
    await pass(6000);
    expect(screen.queryByRole("alert")).toBeNull();
    // the page signed in again while a read was out ("Sign out everywhere"): that read's 401 is no sign-out
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith("/api/ade/workspace/map")) return real(input, init);
      localStorage.setItem("pp-open-token", "the-new-sign-in.sig");
      return new Response(JSON.stringify({ detail: "Not signed in." }), { status: 401 });
    }));
    await pass(3000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(node("Make the map")).toBeTruthy();
  });

  it("draws nothing it last drew once its sign-in has ended", async () => {
    render(<ControlView />);
    await pass();
    expect(node("Make the map")).toBeTruthy();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not signed in." }), { status: 401 })));
    await pass(3000);
    expect(mapAlert().textContent).toBe("This page is not signed in: sign in again, then reload it.");
    expect(screen.queryByRole("listitem", { name: "Make the map" })).toBeNull();
  });

  it("keeps each answer to the fields the page reads: what a workspace adds is drawn nowhere, and an answer of another shape breaks nothing", async () => {
    const said = ["A DESCRIPTION OF ITS OWN", "THE LAST THING IT SAID", "THE PROMPT IT WAS GIVEN", "RM -RF SOMEWHERE"];
    const map = fixtureMap() as unknown as { sessions: Record<string, unknown>[] };
    map.sessions[0] = { ...map.sessions[0], prompt: said[2], description: said[0] };
    const agents = map.sessions[0].agents as Record<string, unknown>[];
    agents[0] = { ...agents[0], description: said[0], last_assistant_message: said[1] };
    map.sessions[1] = { ...map.sessions[1], agents: null };            // a list that is not one: no agents, no break
    server.map = map as unknown as WorkspaceMap;
    const calls = fixtureCalls() as unknown as { main: { calls: Record<string, unknown>[] } };
    calls.main.calls[0] = { ...calls.main.calls[0], input: { command: said[3] }, output: said[1] };
    server.calls[IDS.mine] = calls as unknown as SessionCalls;
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(within(node("Make the map")).getByText("src/a.py")).toBeTruthy();
    expect(within(node("Ask about the gate")).queryByRole("list", { name: "Its agents" })).toBeNull();
    const everything = drawn();
    for (const words of said) expect(everything).not.toContain(words);
  });

  it("leaves out the list's sign-ins as the list does, and shows the first steps where nothing else is left", async () => {
    const map = fixtureMap();
    map.sessions = [
      { ...map.sessions[0], id: "aaaaaaaaaaa1", title: "claude-sign-in", program: "claude-sign-in", agents: [], agents_read: "no" },
      { ...map.sessions[0], id: "aaaaaaaaaaa2", title: "A sign-in the list holds", program: "github", agents: [], agents_read: "no" },
    ];
    server.map = map;
    useStore.setState({ signIns: [{ id: "aaaaaaaaaaa2", program: "github", repo: null, task: "t", running: true, sign_in: true } as unknown as SessionRecord] });
    render(<ControlView empty={<p>Get started</p>} />);
    await pass();
    expect(screen.getByText("Get started")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
    expect(document.body.textContent).not.toMatch(/claude-sign-in|A sign-in the list holds/);
  });

  it("offers to open a session only where the page has its record, so its panel has what to show", async () => {
    useStore.setState({ records: [{ id: IDS.chat, repo: "name", program: "chat", task: "Ask about the gate", running: true } as unknown as SessionRecord] });
    const opened: string[] = [];
    render(<ControlView onOpenSession={(id) => opened.push(id)} />);
    await pass();
    expect(within(node("Make the map")).queryByRole("button", { name: "Make the map" })).toBeNull();
    fireEvent.click(within(node("Ask about the gate")).getByRole("button", { name: "Ask about the gate" }));
    expect(opened).toEqual([IDS.chat]);
  });

  it("titles each session as the list does where the page has its record, and never leaves one untitled", async () => {
    const map = fixtureMap();
    map.sessions = map.sessions.map((x) => (x.id === IDS.chat || x.id === IDS.codex ? { ...x, title: "" } : x));
    server.map = map;
    useStore.setState({ records: [{ id: IDS.chat, repo: "name", program: "chat", task: "Ask about the gate\n\nwith the rest of the task", running: true } as unknown as SessionRecord] });
    render(<ControlView />);
    await pass();
    expect(node("Ask about the gate")).toBeTruthy();                   // the list's: its task's first line
    expect(node("Codex")).toBeTruthy();                                 // no record and no title: its program's name
    expect(drawn()).not.toContain("with the rest of the task");
  });

  it("leaves out a session the list holds closed, before the workspace does", async () => {
    useStore.setState({ records: [{ id: IDS.chat, repo: "name", program: "chat", task: "Ask about the gate", running: false, closed: true } as unknown as SessionRecord] });
    render(<ControlView />);
    await pass();
    expect(screen.queryByRole("listitem", { name: "Ask about the gate" })).toBeNull();
    expect(node("Make the map")).toBeTruthy();
  });

  it("draws a session a sign-in's program runs that the list has, which is no sign-in", async () => {
    const map = fixtureMap();
    map.sessions = [{ ...map.sessions[3], id: "aaaaaaaaaaa3", title: "Look at GitHub", program: "github" }];
    server.map = map;
    useStore.setState({ records: [{ id: "aaaaaaaaaaa3", repo: null, program: "github", task: "Look at GitHub", running: true } as unknown as SessionRecord] });
    render(<ControlView />);
    await pass();
    expect(node("Look at GitHub")).toBeTruthy();
  });

  it("never shows an agent's description, even as the name of one with none", async () => {
    const map = fixtureMap() as unknown as { sessions: { agents: Record<string, unknown>[] }[] };
    map.sessions[0].agents[0] = { ...map.sessions[0].agents[0], name: null, description: "THE WORDS IT WAS GIVEN" };
    server.map = map as unknown as WorkspaceMap;
    render(<ControlView />);
    await pass();
    expect(within(node("Make the map")).getByText("An agent (Explore)")).toBeTruthy();
    expect(drawn()).not.toContain("THE WORDS IT WAS GIVEN");
  });

  it("names a session's agent as the list does where the page has its record: another provider's model, an agent of the person's own", async () => {
    const records = [
      { id: IDS.mine, repo: "name", program: "claude", model_provider: "deepseek", task: "Make the map", running: true, activity: { state: "working", at: 1 } },
      { id: IDS.chat, repo: "name", program: "custom", custom: { name: "Reviewer", command: "x", task: "argument" }, task: "Ask about the gate", running: true },
    ] as unknown as SessionRecord[];
    useStore.setState({ records });
    render(<ControlView />);
    await pass();
    expect(node("Make the map").textContent).toContain("DeepSeek ·");
    expect(node("Make the map").textContent).not.toContain("Claude Code ·");
    expect(node("Ask about the gate").textContent).toContain("Reviewer ·");
    // with no record, the program's own name
    expect(node("Port the parser").textContent).toContain("Codex ·");
  });

  it("shows the session's state as the list does where the page has its record, so the two never disagree", async () => {
    const waiting = { id: IDS.mine, repo: "name", program: "claude", task: "Make the map", running: true, activity: { state: "waiting", at: 1 } } as unknown as SessionRecord;
    useStore.setState({ records: [waiting] });
    render(<ControlView />);
    await pass();
    expect(within(node("Make the map")).getAllByLabelText("Needs you").length).toBeGreaterThan(0);
    expect(node("Make the map").textContent).toContain("· Needs you ·");
    // a session the page has no record of keeps the workspace's word
    expect(node("Ask about the gate").textContent).toContain("· Idle ·");
  });

  it("opens a session's calls, asked every 3 seconds while it stays open, and draws them under each agent", async () => {
    render(<ControlView />);
    await pass();
    expect(asked(CALLS)).toBe(0);
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(asked(CALLS)).toBe(1);
    const mine = within(node("Make the map")).getByLabelText("Its calls");
    expect(mine.textContent).toContain("The session itself: 1 turn, 3 requests, 1.5k tokens");
    expect(within(mine).getByText("Read")).toBeTruthy();
    expect(within(mine).getByText("src/a.py")).toBeTruthy();
    expect(within(mine).getByText("Bash").className).toContain("text-fail");
    expect(within(mine).getByText("(failed)")).toBeTruthy();
    const a1 = agentRow("Make the map", "Explore the routes");
    expect(a1.textContent).toContain("1 turn, 5 requests, 5k tokens");
    expect(within(a1).getByText("Grep")).toBeTruthy();
    expect(within(a1).getByText("src/b.py")).toBeTruthy();
    expect(a1.textContent).toContain("and 12 more calls");
    expect(agentRow("Make the map", LONG_NAME.slice(0, 80)).textContent).not.toMatch(/more calls|Grep/);
    await pass(3000);
    expect(asked(CALLS)).toBe(2);
    await show(false);
    await pass(30_000);
    expect(asked(CALLS)).toBe(2);
    await show(true);
    expect(asked(CALLS)).toBe(3);
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Hide its calls" }));
    await pass(30_000);
    expect(asked(CALLS)).toBe(3);
    expect(within(node("Make the map")).queryByLabelText("Its calls")).toBeNull();
  });

  it("says in the workspace's own words why an opened session's calls are not there", async () => {
    render(<ControlView />);
    await pass();
    // the workspace no longer has it: its 404 carries its reason, which is not the map's own missing route
    fireEvent.click(within(node("Ask about the gate")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(within(node("Ask about the gate")).getByRole("alert").textContent).toBe("No such session.");
    expect(node("Ask about the gate").textContent).not.toMatch(/does not draw the map yet/);
  });

  it("shows nothing of a session's calls from before, when it is opened again, until they are read again", async () => {
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(within(node("Make the map")).getByText("Grep")).toBeTruthy();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Hide its calls" }));
    await pass();
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => (String(input).endsWith(`/sessions/${IDS.mine}/map`)
      ? new Promise<Response>(() => {}) : real(input, init))));
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(within(node("Make the map")).queryByText("Grep")).toBeNull();
    expect(within(node("Make the map")).getByLabelText("Reading its calls")).toBeTruthy();
  });

  it("keeps an opened session's last calls drawn under each agent and its own when a read of them fails, saying why", async () => {
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (String(input).endsWith(`/sessions/${IDS.mine}/map`)
      ? new Response(JSON.stringify({ detail: "The workspace is not answering." }), { status: 502 }) : real(input, init))));
    await pass(3000);
    const mine = within(node("Make the map")).getByLabelText("Its calls");
    expect(within(mine).getByRole("alert").textContent).toBe("The workspace is not answering.");
    expect(within(mine).getByText("src/a.py")).toBeTruthy();
    expect(within(agentRow("Make the map", "Explore the routes")).getByText("Grep")).toBeTruthy();
  });

  it("lets an opened session's calls go once their own read is refused its sign-in", async () => {
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    expect(within(node("Make the map")).getByText("Grep")).toBeTruthy();
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (String(input).endsWith(`/sessions/${IDS.mine}/map`)
      ? new Response(JSON.stringify({ detail: "Not signed in." }), { status: 401 }) : real(input, init))));
    await pass(3000);
    expect(within(node("Make the map")).getAllByRole("alert").map((a) => a.textContent)).toContain("This page is not signed in: sign in again, then reload it.");
    expect(within(node("Make the map")).queryByText("Grep")).toBeNull();
  });

  it("draws the calls and turns of each agent its node does not show, under the session's own, named by its id", async () => {
    server.calls[IDS.mine] = { ...fixtureCalls(), agents: [...fixtureCalls().agents, { id: "gone1", calls: [{ tool: "Glob", target: "docs", failed: false, at: AT }], more: 3 },
      { id: "gone2", calls: [], more: 0 }, { id: "gone3", calls: [{ tool: "Read", target: "a.md", failed: false, at: AT }], more: 0 }],
      // one with no calls is not drawn, but for its turns (an agent left out past the 50 the map gives is one)
      turns: [...fixtureCalls().turns, { agent: "gone4", requests: 2, input_tokens: 100, output_tokens: 50 }] };
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    const mine = within(node("Make the map")).getByLabelText("Its calls");
    expect(within(mine).getAllByText("An agent the map does not show")).toHaveLength(3);
    expect(mine.textContent).toContain("An agent the map does not show (gone1)");
    expect(mine.textContent).toContain("An agent the map does not show (gone3)");
    expect(mine.textContent).toContain("An agent the map does not show (gone4): 1 turn, 2 requests, 150 tokens");
    expect(mine.textContent).not.toContain("gone2");
    expect(within(mine).getByText("Glob")).toBeTruthy();
    expect(mine.textContent).toContain("and 3 more calls");
  });

  it("says where a session's calls were not read, and that its answer was cut", async () => {
    server.calls[IDS.mine] = { ...fixtureCalls(), calls_read: "missing", turns_read: "version", cut: true };
    render(<ControlView />);
    await pass();
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Show its calls" }));
    await pass();
    const mine = within(node("Make the map")).getByLabelText("Its calls");
    expect(mine.textContent).toContain("Its tool calls could not be read: their file is not there.");
    expect(mine.textContent).toContain("Its turns are not given by its Claude Code's version.");
    expect(mine.textContent).toContain("Cut to 64 KB");
  });

  // the map alone: it sets no title and sends no notice, so this holds it to that; the page's own title and notices,
  // with the map in its main panel, are App.control.test.tsx's
  it("sets no title and sends no notice of its own, however its agents change", async () => {
    const shown = vi.fn();
    class Note { static permission = "granted"; constructor(...args: unknown[]) { shown(...args); } }
    vi.stubGlobal("Notification", Note);
    document.title = "Proofpane Community";
    const endsAs = (status: string) => {
      const m = fixtureMap();
      m.sessions[0].agents[0] = { ...m.sessions[0].agents[0], status, ended_at: AT };
      return m;
    };
    render(<ControlView />);
    await pass();
    // a running agent completes, fails or is killed between two reads, each after it ran again
    for (const status of ["completed", "failed", "killed"]) {
      server.map = endsAs(status);
      await pass(3000);
      expect(within(agentRow("Make the map", "Explore the routes")).getByRole("img", { name: status[0].toUpperCase() + status.slice(1) })).toBeTruthy();
      server.map = fixtureMap();
      await pass(3000);
    }
    await show(false);
    server.map = endsAs("failed");
    await show(true);
    await pass(3000);
    expect(shown).not.toHaveBeenCalled();
    expect(document.title).toBe("Proofpane Community");
    for (const name of ["Explore the routes", LONG_NAME.slice(0, 40), "Run the tests", "Stopped halfway"]) expect(document.title).not.toContain(name);
  });
});

describe("pop-outs", () => {
  it("open the map, a session's map or an agent in a tab of its own, on the same page", async () => {
    const open = vi.fn(() => ({}) as Window);
    vi.stubGlobal("open", open);
    render(<ControlView />);
    await pass();
    fireEvent.click(screen.getByRole("button", { name: "Open the map in a tab of its own" }));
    fireEvent.click(within(node("Make the map")).getByRole("button", { name: "Open this session's map in a tab of its own" }));
    fireEvent.click(within(agentRow("Make the map", "Explore the routes")).getByRole("button", { name: "Open this agent in a tab of its own" }));
    await pass();
    expect(open.mock.calls.map((c) => (c as unknown[])[0])).toEqual(["/?pop=map", `/?pop=session&session=${IDS.mine}`,
      `/?pop=agent&session=${IDS.mine}&agent=a1`]);
  });

  it("are not offered for an agent whose id a window cannot be named by, whose row is drawn all the same", async () => {
    const map = fixtureMap();
    map.sessions[0].agents.push({ ...map.sessions[0].agents[1], id: "agent.with.dots", name: "A dotted id" });
    server.map = map;
    render(<ControlView />);
    await pass();
    const dotted = agentRow("Make the map", "A dotted id");
    expect(within(dotted).queryByRole("button", { name: "Open this agent in a tab of its own" })).toBeNull();
    expect(within(agentRow("Make the map", "Explore the routes")).getByRole("button", { name: "Open this agent in a tab of its own" })).toBeTruthy();
  });

  it("are not offered on a phone", async () => {
    browser({ phone: true });
    render(<ControlView />);
    await pass();
    expect(node("Make the map")).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /in a (tab|window) of its own/ })).toEqual([]);
  });

  it("draw the map alone, signed in as the page is, keeping it current, titled in fixed words", async () => {
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    expect(document.title).toBe("The map · Proofpane Community");
    expect(node("Make the map")).toBeTruthy();
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /in a (tab|window) of its own/ })).toEqual([]);     // none from a pop-out
    expect(server.asked).toContain("/api/ade/workspace/accounts");
    const before = asked(MAP);
    await pass(3000);
    expect(asked(MAP)).toBe(before + 1);
  });

  it("draw a session's agents left out and its token budget, as the map does", async () => {
    const budget = EXAMPLE_MAP.sessions[1];
    server.map = { at: AT, sessions: [budget] };
    render(<PopOutPage pop={{ view: "session", session: budget.id }} />);
    await pass();
    await pass();
    expect(node(budget.title).textContent).toContain("effort: 31,999-token budget");
    expect(within(node(budget.title)).getByText("and 3 more agents, not shown")).toBeTruthy();
  });

  it("draw one session's map alone, its calls open", async () => {
    render(<PopOutPage pop={{ view: "session", session: IDS.mine }} />);
    await pass();
    await pass();
    expect(document.title).toBe("A session's map · Proofpane Community");
    expect(node("Make the map")).toBeTruthy();
    expect(screen.queryByRole("listitem", { name: "Ask about the gate" })).toBeNull();
    expect(asked(CALLS)).toBe(1);
    expect(within(node("Make the map")).getByLabelText("Its calls")).toBeTruthy();
  });

  it("draw one agent alone with its calls, its name nowhere in the title", async () => {
    render(<PopOutPage pop={{ view: "agent", session: IDS.mine, agent: "a1" }} />);
    await pass();
    await pass();
    expect(document.title).toBe("An agent · Proofpane Community");
    const region = screen.getByRole("region", { name: "An agent" });
    expect(region.textContent).toContain("Explore the routes");
    expect(region.textContent).toContain("346m 18s");
    expect(within(region).getByText("Grep")).toBeTruthy();
    expect(region.textContent).not.toContain("Run the tests");
    expect(region.textContent).not.toContain(LONG_NAME.slice(0, 20));
    expect(document.title).not.toContain("Explore");
    // a read of its calls that fails leaves the last ones drawn below why, as under a session's node
    server.failing = 502;
    await pass(3000);
    // the map's read and the calls' read each say why
    expect(within(region).getAllByRole("alert").map((a) => a.textContent)).toEqual(["The workspace is not answering.",
      "The workspace is not answering."]);
    expect(within(region).getByText("Grep")).toBeTruthy();
  });

  it("say a session the list holds closed is closed, not that the workspace no longer has it", async () => {
    server.sessions = [{ id: IDS.mine, repo: "name", program: "claude", task: "Make the map", running: false, closed: true } as unknown as SessionRecord];
    for (const pop of [{ view: "session", session: IDS.mine }, { view: "agent", session: IDS.mine, agent: "a1" }] as const) {
      render(<PopOutPage pop={pop} />);
      await pass();
      await pass();
      expect(screen.getByText("This session is closed.")).toBeTruthy();
      expect(screen.queryByText("The workspace no longer has this session.")).toBeNull();
      cleanup();
    }
    server.sessions = [];
    render(<PopOutPage pop={{ view: "session", session: "aaaaaaaaaaa9" }} />);
    await pass();
    await pass();
    expect(screen.getByText("The workspace no longer has this session.")).toBeTruthy();
  });

  it("stop drawing once another window of the page signs out or signs in as another, and ask nothing more", async () => {
    for (const next of [null, "another-account.sig"]) {
      localStorage.setItem("pp-open-token", "test-token.sig");
      render(<PopOutPage pop={{ view: "map" }} />);
      await pass();
      expect(node("Make the map")).toBeTruthy();
      // a change to another key leaves it as it is
      window.dispatchEvent(new StorageEvent("storage", { key: "pp-theme", newValue: "light" }));
      await pass();
      expect(node("Make the map")).toBeTruthy();
      if (next) localStorage.setItem("pp-open-token", next); else localStorage.removeItem("pp-open-token");
      await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-token", newValue: next })); });
      expect(screen.getByRole("status").textContent).toMatch(/has signed out, or shows another account or place now/);
      expect(screen.queryByRole("listitem", { name: "Make the map" })).toBeNull();
      const before = [asked(MAP), asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)];
      await pass(130_000);                                                // past two of its minutes
      expect([asked(MAP), asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)]).toEqual(before);
      cleanup();
    }
  });

  it("read the accounts and the sessions' records once signed in and again each minute while they show, never while hidden", async () => {
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    expect([asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)]).toEqual([1, 1]);
    await pass(59_000);
    expect([asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)]).toEqual([1, 1]);
    await pass(1_000);
    expect([asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)]).toEqual([2, 2]);
    await show(false);
    await pass(120_000);
    expect([asked(ACCOUNTS_ROUTE), asked(SESSIONS_ROUTE)]).toEqual([2, 2]);
  });

  it("name a session's agent from its record as the main page does, and take its state from the map", async () => {
    server.sessions = [{ id: IDS.mine, repo: "name", program: "claude", model_provider: "deepseek", task: "Make the map", running: true,
      activity: { state: "waiting", at: 1 } } as unknown as SessionRecord];
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    expect(node("Make the map").textContent).toContain("DeepSeek ·");
    expect(node("Make the map").textContent).toContain("· Working ·");          // the map's, not the record's "Needs you"
  });

  it("leave out the sign-ins among the records they read, as the list does", async () => {
    const map = fixtureMap();
    map.sessions = [map.sessions[0], { ...map.sessions[3], id: "aaaaaaaaaaa4", title: "Signing in to GitHub", program: "github" }];
    server.map = map;
    server.sessions = [{ id: "aaaaaaaaaaa4", repo: null, program: "github", task: "Signing in to GitHub", running: true, sign_in: true } as unknown as SessionRecord];
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    // the sessions drawn, whatever each is titled by: the sign-in is none of them
    expect(screen.getAllByRole("listitem").filter((li) => li.getAttribute("aria-label")).map((li) => li.getAttribute("aria-label")))
      .toEqual(["Make the map"]);
  });

  it("keep nothing of the account they showed when tried again as another", async () => {
    const view = { ...ACCOUNTS, claude: { ...ACCOUNTS.claude, accounts: ACCOUNTS.claude.accounts.map((a) => ({ ...a, status: { installed: true, signed_in: true, email: "owner@example.test" } })) } };
    useStore.setState({ accounts: view as typeof ACCOUNTS });
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    localStorage.setItem("pp-open-token", "other-account.sig");
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-token", newValue: "other-account.sig" })); });
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    await pass();
    await pass();
    expect(useStore.getState().user?.id).toBe("u2");
    // the accounts read again for the other account carry none of the first one's (its rows' sign-in words)
    expect(useStore.getState().accounts?.claude.accounts.every((a) => a.status === null)).toBe(true);
  });

  it("stop asking for a session's calls too once another window's sign-in changes", async () => {
    render(<PopOutPage pop={{ view: "session", session: IDS.mine }} />);
    await pass();
    await pass();
    expect(asked(CALLS)).toBeGreaterThan(0);
    localStorage.removeItem("pp-open-token");
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-token", newValue: null })); });
    const calls = asked(CALLS);
    const maps = asked(MAP);
    await pass(30_000);
    expect([asked(CALLS), asked(MAP)]).toEqual([calls, maps]);
  });

  it("stop drawing once the server ends their sign-in, as the page's own sign-out ends it", async () => {
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    expect(node("Make the map")).toBeTruthy();
    server.failing = 401;
    await pass(3000);
    expect(screen.getByRole("status").textContent).toMatch(/that page is not signed in now/);
    expect(localStorage.getItem("pp-open-token")).toBeNull();
  });

  it("in the desktop app, are signed in as the place their window showed and the account it named, which a sign-out forgets", async () => {
    vi.stubEnv("MODE", "desktop");
    vi.resetModules();
    const places = await import("../places");
    const { signedInAs } = await import("./PopOut");
    places.savePlace({ id: "local", server: "http://127.0.0.1:1234", label: "This Mac", email: "owner@example.test" });
    places.savePlace({ id: "h1", server: "https://pp.example", label: "pp.example", email: "owner@example.test" });
    places.setActive("local");
    const first = signedInAs();
    expect(signedInAs()).toBe(first);
    places.setActive("h1");                                       // the main window shows another place now
    expect(signedInAs()).not.toBe(first);
    places.setActive("local");
    expect(signedInAs()).toBe(first);
    places.signedOut("local");                                    // signed out there: the account it named is forgotten
    expect(signedInAs()).not.toBe(first);
    vi.resetModules();
  });

  it("say so where the page that opened them is not signed in, and ask nothing of the map", async () => {
    localStorage.clear();
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass(10_000);
    expect(screen.getByText(/that page is not signed in now/)).toBeTruthy();
    expect(asked(MAP)).toBe(0);
  });
});

describe("the linked sessions, on the map (the owner's inbox)", () => {
  const view = (over: Partial<LinkView>): LinkView => ({
    id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
    last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
    events: 0, last_event: null, commands: [], stopped_at: null, ...over,
  });
  const links = {
    links: [view({}), view({ id: "L2", name: "planner", kind: "cloud" }), view({ id: "L3", name: "quiet" })],
    public_url: "https://pp.example",
    questions: [
      { seq: 5, time: "t", kind: "question", from: "mac", to: "owner", ref: null, text: "Push?", link_id: "L1" },
      { seq: 6, time: "t", kind: "question", from: "mac", to: "planner", ref: null, text: "Done?", link_id: "L1" },
    ],
    prompts: [
      { seq: 7, time: "t", kind: "prompt", from: "mac", to: "owner", ref: null, text: "e2e1.x", link_id: "L1", deadline_at: 2, look: false },
      { seq: 8, time: "t", kind: "prompt", from: "mac", to: "owner", ref: null, text: "Session paused", link_id: "L1", deadline_at: 2, look: true },
    ],
    // a proposal counts for the session that posted it, not the one it names
    proposals: [
      { seq: 9, time: "t", kind: "proposal", from: "planner", to: "owner", ref: null, text: "e2e1.y", link_id: "L2", for_id: "L1", for: "mac", deadline_at: 3 },
    ],
  } as unknown as LinkSessions;

  it("draws each linked session with its count of what waits for the owner, each opening Approvals", async () => {
    useStore.setState({ links });
    const onApprovals = vi.fn();
    render(<ControlView onApprovals={onApprovals} />);
    await pass();
    const linked = screen.queryByRole("region", { name: "Linked sessions" });
    expect(linked).not.toBeNull();
    const row = (name: string) => within(linked!).getByRole("listitem", { name });
    // its open prompts, look ones too, and its questions to the owner: 3; a question to another session is not his
    expect(within(row("mac")).getByRole("button", { name: /3 waiting/ })).toBeTruthy();
    expect(within(row("planner")).getByRole("button", { name: /1 waiting/ })).toBeTruthy();
    expect(within(row("quiet")).getByRole("button", { name: /0 waiting/ })).toBeTruthy();
    // a count, never the words any of it carries
    expect(linked!.textContent).not.toMatch(/Push\?|Session paused|e2e1\./);
    fireEvent.click(within(row("mac")).getByRole("button", { name: /3 waiting/ }));
    expect(onApprovals).toHaveBeenCalledTimes(1);
  });

  // an ask for more in a task's grant that a linked session made
  // waits in Approvals too, so it counts for that session; one it made and the owner answered does not
  it("counts a linked session's waiting ask for more in a task's grant among what waits from it", async () => {
    const widening = (id: string, link: string, state: string, kind = "link") => ({ id, task_id: "T1",
      task_title: null, requester: { kind, id: link }, base_version: 1, base_digest: "d", ask: null, adds: {},
      scope: {}, state, created_at: 1, expires_at: 2, decided_at: null, version: null });
    // an agent's ask, whatever its id, is no linked session's; and each session counts its own alone (mac's ask
    // counts for mac, never for quiet or planner)
    useStore.setState({ links, grantWidenings: [widening("W1", "L3", "waiting"), widening("W2", "L3", "refused"),
      widening("W3", "L3", "waiting", "agent"), widening("W4", "L1", "waiting")] as unknown as GrantWideningView[] });
    render(<ControlView onApprovals={() => {}} />);
    await pass();
    const linked = screen.getByRole("region", { name: "Linked sessions" });
    const count = (name: string, n: number) => expect(within(within(linked).getByRole("listitem", { name }))
      .getByRole("button", { name: new RegExp(`^${n} waiting`) })).toBeTruthy();
    count("quiet", 1);
    count("mac", 4);
    count("planner", 1);
  });

  it("draws none in a pop-out, which has no Approvals to open", async () => {
    useStore.setState({ links });
    render(<ControlView pop={{ view: "map" }} />);
    await pass();
    expect(screen.queryByRole("region", { name: "Linked sessions" })).toBeNull();
  });
});
