import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountsView } from "../api";
import { useStore } from "../store";
import { browser } from "../shellTestkit";
import { ControllerPanel } from "./ControllerPanel";
import { forgetPage } from "./RiskTerms";

// The controller's panel in the control view, read against the controller routes a server of the test's own
// answers. The panel reads its view every 3 seconds while the page is visible;
// the tests run on fake timers. Each action (on/off/stop/end-all, a goal, a hold, a close, an answer, a decision) is a
// call the test records, and the test server mutates its state so the re-read after the action shows the effect.

type Dict = Record<string, unknown>;
let srv: {
  controller: Dict | null;
  requests: Dict[];
  requirements: Dict[];
  other_sessions: Dict[];
  questions: Dict[];
  decisions: Dict[];
  // the linked session kept as the Manager's holder, as the view gives it
  holder: Dict | null;
  accounts: Dict[];
  events: Dict[];
  repos: Dict[];
  sessionChecks: Dict | null;
  attempts: Dict[];
  refuse: { path: RegExp; status: number; detail: string } | null;
  calls: { method: string; path: string; body?: Dict }[];
  // a case's own routes, "METHOD path" to its answer
  more: Record<string, (body?: Dict) => [number, unknown]>;
};
let visible = true;

const view = () => ({
  controller: srv.controller,
  requests: srv.requests,
  requirements: srv.requirements,
  other_sessions: srv.other_sessions,
  questions: srv.questions,
  decisions: srv.decisions,
  holder: srv.holder,
});

// a controller set up: the fields controller.ts keeps, with sane defaults a test overrides
const CONTROLLER = (over: Dict = {}): Dict => ({
  state: "off", state_at: 1_790_000_000, account: "default", account_confirmed: true, cap: 4,
  models: ["opus"], efforts: ["high"], chat_model: "opus", chat_effort: "xhigh", confirm_plans: false,
  session: null, open_sessions: 0, end_all: 0, version: 1, mirrored_version: 1, packet_at: null, packet_digest: null,
  packet_error: null, packet_error_at: null, refused: 0, refused_at: null, read_at: null, read_behind_at: null,
  read_error: null, read_error_at: null, mirror_error: null, mirror_error_at: null, ...over,
});

const RISKS = { texts: [{ point: 7, title: "Automated use of your account",
  design: [{ text: "Turning the controller on is an automated use of your account.", case: null }], quotations: [],
  unread: false, digest: "d7" }] };

function serve() {
  srv = { controller: null, requests: [], requirements: [], other_sessions: [], questions: [], decisions: [],
          holder: null, accounts: [], events: [], repos: [], sessionChecks: null, attempts: [], refuse: null, calls: [],
          // the owner's linked sessions to choose the Manager from: none, unless a case gives some
          more: { "GET /api/ade/controller/holders": () => [200, {
            links: [], settings_digest: "sha256:" + "d".repeat(64), life_most: 604800, holder: null }] } };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const u = new URL(String(input), "http://localhost");
    const path = u.pathname;
    const method = init.method || "GET";
    const body = init.body ? (JSON.parse(String(init.body)) as Dict) : undefined;
    srv.calls.push({ method, path, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    if (srv.refuse && srv.refuse.path.test(`${method} ${path}`)) return json({ detail: srv.refuse.detail }, srv.refuse.status);
    if (path === "/api/ade/risks") return json(RISKS);
    if (path === "/api/ade/agents/accounts") return json({ accounts: srv.accounts });
    if (path === "/api/ade/controller/journal") {
      // the log pages newest-first by seq: `before` returns the events older than it, `limit` caps the page
      const before = u.searchParams.get("before");
      const limit = Number(u.searchParams.get("limit")) || 50;
      const pool = before != null ? srv.events.filter((e) => Number(e.seq) < Number(before)) : srv.events;
      return json({ events: pool.slice(0, limit) });
    }
    if (path === "/api/ade/controller/requests" && method === "GET") return json({ requests: srv.requests });
    if (path === "/api/ade/workspace/repos") return json({ repos: srv.repos });
    const own = /^\/api\/ade\/workspace\/sessions\/([^/]+)$/.exec(path);
    if (own && method === "GET") return json({ id: own[1], checks: srv.sessionChecks });
    if (path === "/api/ade/controller" && method === "GET") return json(view());
    if (path === "/api/ade/controller" && method === "PUT") { srv.controller = { ...(srv.controller ?? CONTROLLER()), ...(body ?? {}) }; return json(view()); }
    if (path === "/api/ade/controller/on") { srv.controller = { ...(srv.controller ?? CONTROLLER()), state: "on" }; return json(view()); }
    if (path === "/api/ade/controller/off") { srv.controller = { ...(srv.controller ?? CONTROLLER()), state: "off" }; return json(view()); }
    if (path === "/api/ade/controller/stop") { srv.controller = { ...(srv.controller ?? CONTROLLER()), state: "stopped" }; return json(view()); }
    if (path === "/api/ade/controller/end-all") { srv.controller = { ...(srv.controller ?? CONTROLLER()), open_sessions: 0 }; return json(view()); }
    const attemptsOf = /^\/api\/ade\/controller\/requirements\/([^/]+)\/attempts$/.exec(path);
    if (attemptsOf && method === "GET") return json({ attempts: srv.attempts });
    const post = /^\/api\/ade\/controller\/(requests|requirements|questions|decisions)(\/([^/]+)\/(withdraw|go|hold|close|answer|resume))?$/.exec(path);
    if (post && method === "POST") {
      if (path === "/api/ade/controller/requests") { const r = { id: "req-new", n: srv.requests.length + 1, text: body?.text, digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: [] }; srv.requests = [...srv.requests, r]; return json(r); }
      if (path === "/api/ade/controller/decisions") { const d = { id: "dec-new", n: srv.decisions.length + 1, question: null, text: body?.text, at: 1 }; srv.decisions = [...srv.decisions, d]; return json(d); }
      if (post[4] === "withdraw") return json({ id: post[3], n: 1, text: "", digest: "d", at: 1, go_at: null, withdrawn_at: 2, requirements: [] });
      if (post[4] === "go") return json({ request: post[3], n: 1, go_at: 2, requirements: [] });
      if (post[4] === "hold") return json({ ...(srv.requirements[0] ?? {}), hold: body?.hold });
      if (post[4] === "close") return json({ ...(srv.requirements[0] ?? {}), state: body?.as });
      if (post[4] === "answer") { const d = { id: "dec-a", n: 1, question: post[3], text: body?.text, at: 1 }; srv.decisions = [...srv.decisions, d]; return json(d); }
      if (post[4] === "resume") return json(srv.requirements[0] ?? {});
    }
    const extra = srv.more[`${method} ${path}`];
    if (extra) { const [status, data] = extra(body); return json(data, status); }
    return json({ detail: `no route for ${method} ${path}` }, 404);
  }));
}

beforeEach(() => {
  vi.useFakeTimers();
  visible = true;
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (visible ? "visible" : "hidden") });
  localStorage.setItem("pp-open-token", "test-token.sig");
  browser();
  forgetPage();
  serve();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  useStore.setState(useStore.getInitialState());
  forgetPage();
});

const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const show = async (on: boolean) => { visible = on; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); }); };
const asked = (method: string, path: string) => srv.calls.filter((c) => c.method === method && c.path === path).length;
const drawn = () => document.body.textContent ?? "";
const region = () => screen.getByRole("region", { name: "The Manager" });

async function mount(setup?: () => void) {
  if (setup) setup();
  render(<ControllerPanel />);
  await pass();
}

describe("the controller panel", () => {
  it("before it is set up, offers to choose the account from the owner's confirmed accounts, and sets it up on choosing", async () => {
    await mount(() => { srv.controller = null; srv.accounts = [{ account: "0123456789ab", name: "Alma", state: "confirmed" },
      { account: "cccccccccccc", name: "Not mine", state: "reported" }]; });
    expect(within(region()).getByText(/not set up yet/)).toBeTruthy();
    const pick = within(region()).getByRole("combobox", { name: "Account" });
    // only the confirmed account is offered, with "default"; the reported one is not
    expect(within(pick).getByRole("option", { name: "Alma" })).toBeTruthy();
    expect(within(pick).queryByRole("option", { name: "Not mine" })).toBeNull();
    fireEvent.change(pick, { target: { value: "0123456789ab" } });
    await pass();
    expect(srv.calls.find((c) => c.method === "PUT" && c.path === "/api/ade/controller")?.body).toEqual({ account: "0123456789ab" });
  });

  it("shows the state and, while off, a Turn-it-on whose risk point 7 notice opens as the owner comes to it", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    expect(within(region()).getByText("Off")).toBeTruthy();
    const on = within(region()).getByRole("button", { name: /Turn it on/ });
    expect(screen.queryByRole("dialog", { name: /Automated use of your account/ })).toBeNull();   // not before
    fireEvent.focus(on);
    await pass();
    expect(screen.getByRole("dialog", { name: /Automated use of your account/ })).toBeTruthy();    // opens over On
    fireEvent.click(on);
    await pass();
    expect(asked("POST", "/api/ade/controller/on")).toBe(1);
  });

  it("opens the risk point 7 notice on hover as well as on focus (the owner may reach On with the mouse)", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    const on = within(region()).getByRole("button", { name: /Turn it on/ });
    expect(screen.queryByRole("dialog", { name: /Automated use of your account/ })).toBeNull();
    fireEvent.mouseEnter(on);
    await pass();
    expect(screen.getByRole("dialog", { name: /Automated use of your account/ })).toBeTruthy();
  });

  it("opens the risk point 7 notice on a bare press too, so a touch tap (no hover or focus) still shows it", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    // refuse the On so the state stays off and the notice stays drawn for the assertion; the press itself is what must arm it
    srv.refuse = { path: /POST \/api\/ade\/controller\/on$/, status: 409, detail: "not now" };
    const on = within(region()).getByRole("button", { name: /Turn it on/ });
    expect(screen.queryByRole("dialog", { name: /Automated use of your account/ })).toBeNull();
    fireEvent.click(on);                                   // a bare tap, with no mouseEnter or focus before it
    await pass();
    expect(screen.getByRole("dialog", { name: /Automated use of your account/ })).toBeTruthy();
  });

  it("does not reopen the risk point 7 notice when the controller is turned off; it reopens only as the owner comes to On", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    const on = within(region()).getByRole("button", { name: /Turn it on/ });
    fireEvent.focus(on);                                   // the owner comes to On: the notice opens
    await pass();
    expect(screen.getByRole("dialog", { name: /Automated use of your account/ })).toBeTruthy();
    fireEvent.click(on);                                   // turns it on
    await pass();
    expect(within(region()).getByText("On")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: /Automated use of your account/ })).toBeNull();   // gone while on
    fireEvent.click(within(region()).getByRole("button", { name: /Turn it off/ }));   // turn it off again
    await pass();
    expect(within(region()).getByText("Off")).toBeTruthy();
    // the notice must NOT pop up just because the controller went off — the owner did not come to On
    expect(screen.queryByRole("dialog", { name: /Automated use of your account/ })).toBeNull();
  });

  it("while on, offers Turn-it-off and a two-click Stop, not Turn-it-on; Stop then leaves On to resume", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on", open_sessions: 2 }); });
    expect(within(region()).getByText("On")).toBeTruthy();
    expect(within(region()).queryByRole("button", { name: /Turn it on/ })).toBeNull();
    expect(within(region()).getByRole("button", { name: /Turn it off/ })).toBeTruthy();
    fireEvent.click(within(region()).getByRole("button", { name: "Stop" }));       // first click arms
    expect(asked("POST", "/api/ade/controller/stop")).toBe(0);
    fireEvent.click(within(region()).getByRole("button", { name: "Stop the Manager" }));
    await pass();
    expect(asked("POST", "/api/ade/controller/stop")).toBe(1);
    expect(within(region()).getByRole("button", { name: /Turn it on/ })).toBeTruthy();   // stopped: On resumes it
  });

  it("turns the controller off", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Turn it off/ }));
    await pass();
    expect(asked("POST", "/api/ade/controller/off")).toBe(1);
  });

  it("ends every session on a two-click End all", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on", open_sessions: 3 }); });
    fireEvent.click(within(region()).getByRole("button", { name: "End all" }));
    expect(asked("POST", "/api/ade/controller/end-all")).toBe(0);
    fireEvent.click(within(region()).getByRole("button", { name: "End every session" }));
    await pass();
    expect(asked("POST", "/api/ade/controller/end-all")).toBe(1);
  });

  it("shows a refused control's own words and leaves the view as it was", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    srv.refuse = { path: /POST \/api\/ade\/controller\/on/, status: 409, detail: "The account is not confirmed as your own." };
    fireEvent.click(within(region()).getByRole("button", { name: /Turn it on/ }));
    await pass();
    expect(within(region()).getByText("The account is not confirmed as your own.")).toBeTruthy();
    expect(within(region()).getByText("Off")).toBeTruthy();                        // unchanged
  });

  it("sends a goal and lists each goal's words as text, never as markup, marked the owner's", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requests = [{ id: "r1", n: 1, text: "Ship <b>fast</b> & safe", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: [] }]; });
    // the owner's words are text: the tags show, nothing is bold
    const goal = within(region()).getByRole("listitem", { name: "Goal 1" });
    expect(goal.textContent).toContain("Ship <b>fast</b> & safe");
    expect(within(goal).queryByText("fast", { selector: "b" })).toBeNull();
    expect(within(goal).getByText("the owner's words")).toBeTruthy();
    fireEvent.change(within(region()).getByRole("textbox", { name: "A goal for the Manager" }), { target: { value: "A new goal" } });
    fireEvent.click(within(region()).getByRole("button", { name: "Send" }));
    await pass();
    expect(srv.calls.find((c) => c.method === "POST" && c.path === "/api/ade/controller/requests")?.body).toEqual({ text: "A new goal" });
  });

  it("gives the go on a goal whose plan waits", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requests = [{ id: "r1", n: 1, text: "Goal one", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: ["q1"] }];
      srv.requirements = [req({ id: "q1", request: { id: "r1", n: 1 }, waits_for_go: true, activity: "waiting for go" })]; });
    const goal = within(region()).getByRole("listitem", { name: "Goal 1" });
    fireEvent.click(within(goal).getByRole("button", { name: "Give the go" }));
    await pass();
    expect(asked("POST", "/api/ade/controller/requests/r1/go")).toBe(1);
  });

  it("withdraws an unplanned goal", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requests = [{ id: "r1", n: 1, text: "Goal one", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: [] }]; });
    const goal = within(region()).getByRole("listitem", { name: "Goal 1" });
    fireEvent.click(within(goal).getByRole("button", { name: "Withdraw" }));       // arms
    fireEvent.click(within(goal).getByRole("button", { name: "Withdraw it" }));
    await pass();
    expect(asked("POST", "/api/ade/controller/requests/r1/withdraw")).toBe(1);
  });

  it("keeps a sub-goal's owner words and the model's title apart, and shows a task's broken bounds", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requirements = [req({ id: "q1", title: { text: "Wire the gate", cut: false, hidden_removed: 0, by: "controller" },
        words: "Make the audit gate run", acceptance: "the gate runs on every call", state: "open", activity: "running",
        tasks: [task({ id: "t1", state: "working", repo: "demo", flag: "on another account than the controller's" })] })]; });
    const card = within(region()).getByRole("listitem", { name: "Wire the gate" });
    // the title sits under "a model's words", the owner's words under "the owner's words" — not merely both present, but
    // each paired with the right label, so a swap of the two labels would fail here
    const titleBlock = within(card).getByText(/a model's words \(the title\)/).parentElement!;
    expect(titleBlock.textContent).toContain("Wire the gate");
    expect(titleBlock.textContent).not.toContain("Make the audit gate run");
    const ownerBlock = within(card).getByText("the owner's words").parentElement!;
    expect(ownerBlock.textContent).toContain("Make the audit gate run");
    expect(ownerBlock.textContent).not.toContain("Wire the gate");
    expect(within(card).getByText("the owner's words (what done means)").parentElement!.textContent).toContain("the gate runs on every call");
    expect(within(card).getByText(/Outside its bounds: on another account than the controller's/)).toBeTruthy();
  });

  it("holds and releases a sub-goal, and closes it done or stopped", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requirements = [req({ id: "q1", title: { text: "A sub-goal", cut: false, hidden_removed: 0, by: "controller" }, state: "open", hold: false })]; });
    const card = within(region()).getByRole("listitem", { name: "A sub-goal" });
    fireEvent.click(within(card).getByRole("button", { name: "Hold" }));
    await pass();
    expect(srv.calls.find((c) => c.path === "/api/ade/controller/requirements/q1/hold")?.body).toEqual({ hold: true });
    fireEvent.click(within(card).getByRole("button", { name: "Close done" }));     // arms
    fireEvent.click(within(card).getByRole("button", { name: "Close as done" }));
    await pass();
    expect(srv.calls.find((c) => c.path === "/api/ade/controller/requirements/q1/close")?.body).toEqual({ as: "done" });
  });

  it("answers a question as a decision, and shows the decisions as the owner's words", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.questions = [{ id: "qq1", requirement: null, text: "Which repo?", cut: false, hidden_removed: 0, state: "open", asked_at: 1, answered_at: null, decision: null }];
      srv.decisions = [{ id: "d1", n: 1, question: null, text: "Use the demo repo", at: 1 }]; });
    expect(within(region()).getByText("Which repo?")).toBeTruthy();
    expect(within(region()).getByText("a model's words (a question)")).toBeTruthy();
    fireEvent.change(within(region()).getByRole("textbox", { name: "Your decision on this question" }), { target: { value: "The demo repo" } });
    fireEvent.click(within(region()).getByRole("button", { name: "Record the decision" }));
    await pass();
    expect(srv.calls.find((c) => c.path === "/api/ade/controller/questions/qq1/answer")?.body).toEqual({ text: "The demo repo" });
    expect(within(region()).getByText("Use the demo repo")).toBeTruthy();
  });

  it("shows the log only when asked, in the app's own words", async () => {
    await mount(() => { srv.controller = CONTROLLER(); srv.events = [{ seq: 2, kind: "plan", requirement: "q1", task: null, at: 1, words: "Planned a sub-goal from goal 1" }]; });
    expect(asked("GET", "/api/ade/controller/journal")).toBe(0);
    fireEvent.click(within(region()).getByRole("button", { name: "Show the log" }));
    await pass();
    expect(asked("GET", "/api/ade/controller/journal")).toBe(1);
    expect(within(region()).getByText("Planned a sub-goal from goal 1")).toBeTruthy();
  });

  it("reads its view every 3 seconds while the page is visible, and not while it is hidden", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    expect(asked("GET", "/api/ade/controller")).toBe(1);
    await pass(3000);
    expect(asked("GET", "/api/ade/controller")).toBe(2);
    await show(false);
    await pass(30_000);
    expect(asked("GET", "/api/ade/controller")).toBe(2);
    await show(true);
    expect(asked("GET", "/api/ade/controller")).toBe(3);
  });

  it("opens the controller's chat once a session is served, and says so until then", async () => {
    const opened: string[] = [];
    srv.controller = CONTROLLER({ session: null });
    render(<ControllerPanel onOpenSession={(id) => opened.push(id)} />);
    await pass();
    expect(within(region()).getByText(/opens here once this workspace serves it/)).toBeTruthy();
    expect(within(region()).queryByRole("button", { name: /Open the Manager's chat/ })).toBeNull();
    cleanup();
    srv.controller = CONTROLLER({ session: "abcabcabcabc" });
    render(<ControllerPanel onOpenSession={(id) => opened.push(id)} />);
    await pass();
    fireEvent.click(within(region()).getByRole("button", { name: /Open the Manager's chat/ }));
    expect(opened).toEqual(["abcabcabcabc"]);
  });

  it("says in words when the server is too old for the controller routes", async () => {
    srv.controller = null;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
    render(<ControllerPanel />);
    await pass();
    expect(within(region()).getByText(/does not have the Manager yet/)).toBeTruthy();
  });

  it("keeps the view to the fields it reads: what the app adds is drawn nowhere, and a wrong shape breaks nothing", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ secret_field: "SHOULD NOT SHOW" } as Dict);
      srv.requests = [{ id: "r1", n: 1, text: "A goal", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: [], injected: "SHOULD NOT SHOW" } as Dict];
      srv.requirements = [{ id: "q1", request: { id: "r1", n: 1 }, title: null, words: "owner words", acceptance: null, repos: null, state: "open", tasks: "not a list" } as unknown as Dict];
    });
    expect(within(region()).getByRole("listitem", { name: "Goal 1" })).toBeTruthy();
    expect(drawn()).not.toContain("SHOULD NOT SHOW");
  });

  it("keeps the Give-the-go button for a sub-goal planned after an earlier go on the same goal", async () => {
    // go_at is the last go and stays set; a sub-goal planned later waits for a go of its own, so the
    // button must still show — not be hidden because a go was once given
    await mount(() => { srv.controller = CONTROLLER();
      srv.requests = [{ id: "r1", n: 1, text: "Goal one", digest: "d", at: 1, go_at: 2, withdrawn_at: null, requirements: ["q1", "q2"] }];
      srv.requirements = [req({ id: "q2", request: { id: "r1", n: 1 }, waits_for_go: true, activity: "waiting for go" })]; });
    const goal = within(region()).getByRole("listitem", { name: "Goal 1" });
    fireEvent.click(within(goal).getByRole("button", { name: "Give the go" }));
    await pass();
    expect(asked("POST", "/api/ade/controller/requests/r1/go")).toBe(1);
  });

  it("shows the controller's sessions that are not under a sub-goal, a start outside its bounds flagged", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.other_sessions = [task({ id: "t9", state: "working", repo: "demo", branch: "x", flag: "while the controller was off" })]; });
    const other = screen.getByRole("region", { name: "Other sessions" });
    expect(within(other).getByText("Working")).toBeTruthy();
    expect(within(other).getByText(/Outside its bounds: while the controller was off/)).toBeTruthy();
  });

  it("edits the settings: cap, a model, an effort, the chat's model, and wait-for-go, each sent as it changes", async () => {
    await mount(() => { srv.controller = CONTROLLER({ cap: 4, models: ["opus"], efforts: ["high"], chat_model: "opus", chat_effort: "xhigh", confirm_plans: false }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    const cap = within(region()).getByRole("spinbutton", { name: "Sessions at once" });
    fireEvent.change(cap, { target: { value: "7" } });
    fireEvent.blur(cap);
    await pass();
    expect(srv.calls.find((c) => c.method === "PUT" && c.body && "cap" in c.body)?.body).toEqual({ cap: 7 });
    fireEvent.click(within(region()).getByRole("checkbox", { name: "Sonnet" }));
    await pass();
    expect(srv.calls.find((c) => c.body && "models" in c.body)?.body).toEqual({ models: ["opus", "sonnet"] });
    fireEvent.click(within(region()).getByRole("checkbox", { name: "Low" }));
    await pass();
    expect(srv.calls.find((c) => c.body && "efforts" in c.body)?.body).toEqual({ efforts: ["high", "low"] });
    fireEvent.change(within(region()).getByRole("combobox", { name: "Its chat's model" }), { target: { value: "sonnet" } });
    await pass();
    expect(srv.calls.find((c) => c.body && "chat_model" in c.body)?.body).toEqual({ chat_model: "sonnet" });
    fireEvent.change(within(region()).getByRole("combobox", { name: "Its chat's effort" }), { target: { value: "max" } });
    await pass();
    expect(srv.calls.find((c) => c.body && "chat_effort" in c.body)?.body).toEqual({ chat_effort: "max" });
    fireEvent.click(within(region()).getByRole("checkbox", { name: /Wait for my go/ }));
    await pass();
    expect(srv.calls.find((c) => c.body && "confirm_plans" in c.body)?.body).toEqual({ confirm_plans: true });
  });

  it("keeps the typed goal when the send is refused, so the owner need not retype it", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    srv.refuse = { path: /POST \/api\/ade\/controller\/requests$/, status: 409, detail: "Plan your earlier goals first." };
    const box = within(region()).getByRole("textbox", { name: "A goal for the Manager" }) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "My goal" } });
    fireEvent.click(within(region()).getByRole("button", { name: "Send" }));
    await pass();
    expect(within(region()).getByText("Plan your earlier goals first.")).toBeTruthy();
    expect(box.value).toBe("My goal");
  });

  it("shows what did not reach the workspace: the app's error words, the workspace a version behind, and refused actions", async () => {
    await mount(() => { srv.controller = CONTROLLER({ version: 5, mirrored_version: 3, packet_error: "the packet was too big", packet_error_at: 1, refused: 2, refused_at: 1 }); });
    expect(within(region()).getByText("the packet was too big")).toBeTruthy();
    // the workspace holds the older mirrored_version (3); the controller is ahead at version (5) — not the other way round
    expect(within(region()).getByText(/The workspace has version 3 of the Manager's plan; the Manager is now at version 5\./)).toBeTruthy();
    // `refused` counts every refused action and every non-action entry, not only starts
    expect(within(region()).getByText(/2 actions refused/)).toBeTruthy();
  });

  it("pages the log: Show more loads the older events, and stops when there are no more", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.events = Array.from({ length: 60 }, (_, i) => ({ seq: 60 - i, kind: "plan", requirement: null, task: null, at: 1, words: `line ${60 - i}` })); });
    fireEvent.click(within(region()).getByRole("button", { name: "Show the log" }));
    await pass();
    expect(within(region()).getByText("line 60")).toBeTruthy();
    expect(within(region()).getByText("line 11")).toBeTruthy();
    expect(within(region()).queryByText("line 10")).toBeNull();
    fireEvent.click(within(region()).getByRole("button", { name: /Show more/ }));
    await pass();
    expect(within(region()).getByText("line 10")).toBeTruthy();
    expect(within(region()).getByText("line 1")).toBeTruthy();
    expect(within(region()).queryByRole("button", { name: /Show more/ })).toBeNull();
  });

  it("offers 'default' only when it is a confirmed account, once, not as a second hardcoded option", async () => {
    await mount(() => { srv.controller = null; srv.accounts = [
      { account: "default", name: null, report: { email: "me@x.test" }, state: "confirmed" },
      { account: "0123456789ab", name: "Alma", state: "confirmed" }]; });
    const pick = within(region()).getByRole("combobox", { name: "Account" });
    const defaults = within(pick).getAllByRole("option").filter((o) => (o as HTMLOptionElement).value === "default");
    expect(defaults).toHaveLength(1);
  });

  it("does not offer an unconfirmed default account (the route would refuse it)", async () => {
    await mount(() => { srv.controller = null; srv.accounts = [{ account: "0123456789ab", name: "Alma", state: "confirmed" }]; });
    const pick = within(region()).getByRole("combobox", { name: "Account" });
    expect(within(pick).getAllByRole("option").some((o) => (o as HTMLOptionElement).value === "default")).toBe(false);
  });

  it("warns and disables Send when a goal is over the 4000-character cap the owner reads", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    const box = within(region()).getByRole("textbox", { name: "A goal for the Manager" });
    fireEvent.change(box, { target: { value: "x".repeat(4001) } });
    expect(within(region()).getByText(/Too long by 1 character/)).toBeTruthy();
    expect((within(region()).getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("warns, outside the collapsed settings, when the controller's account is no longer confirmed", async () => {
    await mount(() => { srv.controller = CONTROLLER({ account: "0123456789ab", account_confirmed: false }); });
    expect(within(region()).getByText(/no longer confirmed as your own/)).toBeTruthy();
  });

  it("keeps the panel drawn through a 401 answered to an older sign-in", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "off" }); });
    expect(within(region()).getByText("Off")).toBeTruthy();
    // the page signed in again while this read was out ("Sign out everywhere"): its 401 is no sign-out
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith("/api/ade/controller")) return real(input, init);
      localStorage.setItem("pp-open-token", "the-new-sign-in.sig");
      return new Response(JSON.stringify({ detail: "Not signed in." }), { status: 401 });
    }));
    await pass(3000);
    expect(within(region()).getByText("Off")).toBeTruthy();
    expect(within(region()).queryByText(/not signed in/i)).toBeNull();
  });

  it("offers Withdraw only while a goal has no plan, not once the controller has planned from it", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.requests = [
        { id: "r1", n: 1, text: "Unplanned", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: [] },
        { id: "r2", n: 2, text: "Planned", digest: "d", at: 1, go_at: null, withdrawn_at: null, requirements: ["q1"] }]; });
    expect(within(within(region()).getByRole("listitem", { name: "Goal 1" })).getByRole("button", { name: "Withdraw" })).toBeTruthy();
    expect(within(within(region()).getByRole("listitem", { name: "Goal 2" })).queryByRole("button", { name: "Withdraw" })).toBeNull();
  });

  it("records a decision of the owner's own, clearing the box on success", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    const box = within(region()).getByRole("textbox", { name: "A decision of your own to record" }) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Use the demo repo for all of them" } });
    fireEvent.click(within(region()).getByRole("button", { name: "Record a decision" }));
    await pass();
    expect(srv.calls.find((c) => c.method === "POST" && c.path === "/api/ade/controller/decisions")?.body).toEqual({ text: "Use the demo repo for all of them" });
    expect(box.value).toBe("");
  });

  it("warns and disables Record when a decision or an answer is over the 2000-character cap", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.questions = [{ id: "qq1", requirement: null, text: "Which repo?", cut: false, hidden_removed: 0, state: "open", asked_at: 1, answered_at: null, decision: null }]; });
    const answer = within(region()).getByRole("textbox", { name: "Your decision on this question" });
    fireEvent.change(answer, { target: { value: "y".repeat(2001) } });
    expect((within(region()).getByRole("button", { name: "Record the decision" }) as HTMLButtonElement).disabled).toBe(true);
    const box = within(region()).getByRole("textbox", { name: "A decision of your own to record" });
    fireEvent.change(box, { target: { value: "z".repeat(2001) } });
    expect((within(region()).getByRole("button", { name: "Record a decision" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(region()).getAllByText(/Too long by 1 character/).length).toBeGreaterThanOrEqual(2);
  });

  it("counts the trimmed answer and decision, and takes exactly 2000 characters in each, as the goal box does", async () => {
    await mount(() => { srv.controller = CONTROLLER();
      srv.questions = [{ id: "qq1", requirement: null, text: "Which repo?", cut: false, hidden_removed: 0, state: "open", asked_at: 1, answered_at: null, decision: null }]; });
    const answer = within(region()).getByRole("textbox", { name: "Your decision on this question" });
    const box = within(region()).getByRole("textbox", { name: "A decision of your own to record" });
    const record = () => within(region()).getByRole("button", { name: "Record the decision" }) as HTMLButtonElement;
    const decide = () => within(region()).getByRole("button", { name: "Record a decision" }) as HTMLButtonElement;
    // the cap itself, then the cap with trailing blank lines, which the server trims before it counts
    for (const value of ["y".repeat(2000), "y".repeat(2000) + "\n\n"]) {
      fireEvent.change(answer, { target: { value } });
      fireEvent.change(box, { target: { value } });
      expect(within(region()).queryByText(/Too long/)).toBeNull();
      expect(record().disabled).toBe(false);
      expect(decide().disabled).toBe(false);
    }
  });

  it("saves the cap's two ends, 10 and 1, and puts back a value past them without sending it", async () => {
    // 10, the app's CAP_MOST: a running attempt of a requirement's manager holds no place in the cap, which leaves
    // one of the workspace's twelve programs for its chat
    await mount(() => { srv.controller = CONTROLLER({ cap: 4 }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    const cap = within(region()).getByRole("spinbutton", { name: "Sessions at once" }) as HTMLInputElement;
    expect(cap.max).toBe("10");
    const sent = () => srv.calls.filter((c) => c.method === "PUT" && c.body && "cap" in c.body).map((c) => c.body);
    for (const value of ["10", "1", "11"]) {
      fireEvent.change(cap, { target: { value } });
      fireEvent.blur(cap);
      await pass();
    }
    expect(sent()).toEqual([{ cap: 10 }, { cap: 1 }]);
    expect(cap.value).toBe("1");                    // 11 is past the top: not sent, and the field shows the cap again
  });

  it("does not clear the account when the owner picks the empty placeholder", async () => {
    await mount(() => { srv.controller = CONTROLLER({ account: "default", account_confirmed: true }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    const pick = within(region()).getByRole("combobox", { name: "Account" });
    fireEvent.change(pick, { target: { value: "" } });
    await pass();
    expect(srv.calls.some((c) => c.method === "PUT" && c.body != null && "account" in c.body)).toBe(false);
  });

  // the health box carries only the version-mismatch line and the error lines; it must not draw an empty bordered box in
  // the healthy state. There is no accessible content to query when it is empty, so its own container is matched by shape.
  const healthBoxes = () => region().querySelectorAll(".px-2.py-1\\.5.text-\\[11px\\]");

  it("draws no health box in the healthy state (the account's version matches and nothing failed)", async () => {
    await mount(() => { srv.controller = CONTROLLER({ version: 4, mirrored_version: 4 }); });
    expect(healthBoxes().length).toBe(0);
    // a real problem brings the box back, so the matcher is not one that can never fire
    srv.controller = CONTROLLER({ version: 5, mirrored_version: 3 });
    await pass(3000);
    expect(within(region()).getByText(/version 3 of the Manager's plan/)).toBeTruthy();
    expect(healthBoxes().length).toBe(1);
  });

  it("draws no health box while the workspace holds no version of the plan yet (none mirrored)", async () => {
    await mount(() => { srv.controller = CONTROLLER({ version: 2, mirrored_version: null }); });
    expect(healthBoxes().length).toBe(0);
    expect(within(region()).queryByText(/of the Manager's plan/)).toBeNull();
    // once the workspace holds an older version the line is said, so the checks above are not ones that can never fail
    srv.controller = CONTROLLER({ version: 2, mirrored_version: 1 });
    await pass(3000);
    expect(within(region()).getByText(/The workspace has version 1 of the Manager's plan; the Manager is now at version 2\./)).toBeTruthy();
  });

  it("holds the picker's 'no longer among your confirmed accounts' warning while the confirmed-accounts read has not returned", async () => {
    srv.refuse = { path: /GET \/api\/ade\/agents\/accounts$/, status: 500, detail: "down" };
    await mount(() => { srv.controller = CONTROLLER({ account: "0123456789ab", account_confirmed: true }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    await pass();
    // the read failed, so whether the account is still confirmed is unknown: hold the warning, say the read failed
    expect(within(region()).queryByText(/no longer among your confirmed accounts/)).toBeNull();
    expect(within(region()).getByText(/could not be read just now/)).toBeTruthy();
  });

  it("warns in the picker once the read returns and the account is not among the confirmed ones", async () => {
    await mount(() => { srv.controller = CONTROLLER({ account: "0123456789ab", account_confirmed: true });
      srv.accounts = [{ account: "cccccccccccc", name: "Someone else", state: "confirmed" }]; });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    await pass();
    expect(within(region()).getByText(/no longer among your confirmed accounts/)).toBeTruthy();
  });

  it("counts the trimmed goal, so trailing blank lines do not falsely read as over the cap", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    const box = within(region()).getByRole("textbox", { name: "A goal for the Manager" });
    fireEvent.change(box, { target: { value: "x".repeat(4000) + "\n\n" } });
    expect(within(region()).queryByText(/Too long/)).toBeNull();
    expect((within(region()).getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("syncs the cap field to a value changed elsewhere while Settings stays open, unless the owner is typing in it", async () => {
    await mount(() => { srv.controller = CONTROLLER({ cap: 4 }); });
    fireEvent.click(within(region()).getByRole("button", { name: /Settings/ }));
    const cap = within(region()).getByRole("spinbutton", { name: "Sessions at once" }) as HTMLInputElement;
    expect(cap.value).toBe("4");
    // the cap changed elsewhere; the poll brings it and the field, not focused, follows so it never disagrees with the header
    srv.controller = CONTROLLER({ cap: 7 });
    await pass(3000);
    expect(cap.value).toBe("7");
    // now the owner is typing in the field: an incoming change must not overwrite the draft under their hands
    act(() => cap.focus());
    fireEvent.change(cap, { target: { value: "9" } });
    srv.controller = CONTROLLER({ cap: 5 });
    await pass(3000);
    expect(cap.value).toBe("9");
  });
});

// a requirement with the fields controller.ts keeps, defaults a test overrides
function req(over: Dict = {}): Dict {
  return { id: "q1", request: { id: "r1", n: 1 }, title: { text: "A sub-goal", cut: false, hidden_removed: 0, by: "controller" },
    words: "the owner's words", acceptance: null, repos: [], state: "open", hold: false, confirm: false, go_at: null,
    waits_for_go: false, activity: "open", planned_at: 1, closed_at: null, tasks: [], more_tasks: 0, ...over };
}
function task(over: Dict = {}): Dict {
  return { id: "t1", requirement: "q1", session: null, repo: null, branch: null, base: null, account: null, program: null,
    model: null, effort: null, state: "working", waits_until: null, started_at: 1, stopped_at: null, ended_at: null,
    continues: 0, flag: null, ...over };
}

// a goal's checks on the page
const R4 = "Protected paths are judged on the commits alone: a check can still be passed by changing a file it runs that " +
  "no protected path covers, or by a program still running, the session's own or another session's, changing its " +
  "checkout's files as they are made or while it runs, or writing the round's clone as it is made, whose filter the " +
  "workspace's own git would then run.";
const GOAL = (over: Dict = {}): Dict => ({ id: "r1", n: 1, text: "Say hello.", digest: "d", at: 1, go_at: null,
  withdrawn_at: null, requirements: ["q1"], checks: null, unchosen: [], ...over });
const CHECKS = { commands: [{ id: 1, repo: "demo", name: "unit tests", command: "make test" },
  { id: 2, repo: "demo", name: "lint", command: "ruff check ." }], protected: [{ repo: "demo", paths: ["tests/**"] }] };
const ROUND = { round: 2, head: "a".repeat(40), at: 1_790_000_100, passed: false,
  results: [{ id: 1, outcome: "passed" }, { id: 2, outcome: "failed", exit: 3 }] };

describe("a goal's checks", () => {
  it("the goal form sends the owner's checks and protected paths as written, beside what a check is", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.repos = [{ name: "demo" }, { name: "other" }]; });
    fireEvent.change(within(region()).getByRole("textbox", { name: "A goal for the Manager" }), { target: { value: "Say hello." } });
    fireEvent.click(within(region()).getByRole("button", { name: /Checks \(optional\)/ }));
    await pass();
    expect(drawn()).toContain("never SSH_AUTH_SOCK");
    expect(drawn()).toContain("made just before the check runs and only while that clone is as it was made");
    expect(drawn()).toContain("a check after one that changed the clone is not run");
    expect(drawn()).toContain("a repository that is a shallow or partial clone gives no clone, so its checks never run");
    fireEvent.click(within(region()).getByRole("button", { name: "Add a check" }));
    fireEvent.change(within(region()).getByRole("combobox", { name: "Check 1's repository" }), { target: { value: "demo" } });
    fireEvent.change(within(region()).getByRole("textbox", { name: "Check 1's name" }), { target: { value: "unit tests" } });
    fireEvent.change(within(region()).getByRole("textbox", { name: "Check 1's command" }), { target: { value: "make test" } });
    fireEvent.change(within(region()).getByRole("textbox", { name: "Protected paths in demo, one a line" }),
      { target: { value: "tests/**\n\nMakefile\n" } });
    fireEvent.click(within(region()).getByRole("button", { name: /Send/ }));
    await pass();
    expect(srv.calls.find((c) => c.method === "POST" && c.path === "/api/ade/controller/requests")?.body).toEqual({
      text: "Say hello.", checks: [{ repo: "demo", name: "unit tests", command: "make test" }],
      protected: [{ repo: "demo", paths: ["tests/**", "Makefile"] }],
    });
    // sent: the form is clear again
    expect(within(region()).queryByRole("textbox", { name: "Check 1's name" })).toBeNull();
  });

  it("a goal with no checks is sent as before, its text alone", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); });
    fireEvent.change(within(region()).getByRole("textbox", { name: "A goal for the Manager" }), { target: { value: "Say hello." } });
    fireEvent.click(within(region()).getByRole("button", { name: /Send/ }));
    await pass();
    expect(srv.calls.find((c) => c.method === "POST" && c.path === "/api/ade/controller/requests")?.body).toEqual({ text: "Say hello." });
  });

  it("gives the words on protected paths beside every set of checks: the goal form, a goal's row, a sub-goal's card and a task's round", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requests = [GOAL({ checks: CHECKS })];
      srv.requirements = [req({ checks: CHECKS.commands, protected: CHECKS.protected, left_out: [],
        tasks: [task({ state: "idle", checks: ROUND, why: null, checking: false })] })];
    });
    fireEvent.click(within(region()).getByRole("button", { name: /Checks \(optional\)/ }));
    await pass();
    for (const where of [screen.getByRole("generic", { name: "The goal's checks" }),
      screen.getByRole("generic", { name: "Goal 1's checks" }), screen.getByRole("generic", { name: "Its checks" }),
      screen.getByRole("generic", { name: "Its checks' last round" })]) {
      expect(where.textContent).toContain(R4);
    }
    expect(drawn().split(R4).length - 1).toBe(4);
  });

  it("a goal's row shows its checks, its protected paths, and those no sub-goal chose", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.requests = [GOAL({ checks: CHECKS, unchosen: [2] })]; });
    const row = screen.getByRole("generic", { name: "Goal 1's checks" });
    expect(row.textContent).toContain("make test");
    expect(row.textContent).toContain("lint · chosen by no sub-goal");
    expect(row.textContent).not.toContain("unit tests · chosen by no sub-goal");
    expect(row.textContent).toContain("Protected in demo: tests/**");
  });

  it("a sub-goal's card shows the checks it chose and those of its goal it left out", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ checks: [CHECKS.commands[0]], protected: CHECKS.protected,
        left_out: [{ id: 2, repo: "demo", name: "lint" }] })];
    });
    const card = screen.getByRole("generic", { name: "Its checks" });
    expect(card.textContent).toContain("Check 1 demo unit tests");
    expect(card.textContent).toContain("Left out: check 2 (lint)");
  });

  it("a task's row gives each outcome by its check's name, and its output only on the owner's press, as a program's", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ checks: CHECKS.commands, protected: [], left_out: [],
        tasks: [task({ repo: "demo", state: "idle", session: "0123456789ab", checks: ROUND, why: null, checking: true })] })];
      srv.sessionChecks = { ...ROUND, results: [
        { id: 1, outcome: "passed", name: "unit tests", output: { text: "ok <b>bold</b>", by: "program" } },
        { id: 2, outcome: "failed", exit: 3, name: "lint", output: null, tail_dropped: true }] };
    });
    const round = screen.getByRole("generic", { name: "Its checks' last round" });
    expect(round.textContent).toContain("round 2: not every check passed");
    expect(round.textContent).toContain("unit tests: passed");
    expect(round.textContent).toContain("lint: failed (exit 3)");
    expect(drawn()).toContain("its checks are queued or running");
    expect(drawn()).toContain("they did not judge that commit");
    expect(drawn()).toContain("it can be the head that program ends on, and the task then fails");
    expect(drawn()).toContain("and fails every later round");
    expect(asked("GET", "/api/ade/workspace/sessions/0123456789ab")).toBe(0);
    expect(drawn()).not.toContain("ok <b>bold</b>");
    fireEvent.click(within(round).getByRole("button", { name: /Show output/ }));
    await pass();
    expect(asked("GET", "/api/ade/workspace/sessions/0123456789ab")).toBe(1);
    expect(round.textContent).toContain("unit tests · A program's output, as it wrote it: the check's, not the workspace's words");
    expect(round.querySelector("pre")?.textContent).toBe("ok <b>bold</b>");       // as text, never markup
    expect(round.querySelector("b")).toBeNull();
    expect(round.textContent).toContain("(none kept)");
  });

  it("gives the notes on Finish and Update only to a task in a repository where its sub-goal chose a check", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ checks: CHECKS.commands, protected: [], left_out: [], tasks: [
        task({ id: "t1", repo: "demo", state: "working", checks: null, why: null, checking: false }),
        task({ id: "t2", repo: "other", state: "working", checks: null, why: null, checking: false })] })];
    });
    expect(drawn().split("they did not judge that commit").length - 1).toBe(1);
    expect(drawn().split("and fails every later round").length - 1).toBe(1);
  });

  it("a task its checks ended without a round says the workspace's own words", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ tasks: [task({ state: "failed", checks: null, why: "no commit since the task's base", checking: false })] })];
    });
    expect(drawn()).toContain("Ended failed: no commit since the task's base");
  });
});

// a sub-goal's own manager on the page
describe("a sub-goal's own manager", () => {
  const MANAGER = (over: Dict = {}): Dict => ({ state: "needs_owner", waits: null, waits_until: null, attempts: 2,
    failures: 0, for_owner: "asked", last: { n: 2, outcome: "needs_owner", cause: null, ended_at: 1_790_000_200 },
    note: { text: "Which README? <i>this one</i>", cut: false, hidden_removed: 0, at: 1_790_000_200, by: "manager" },
    ...over });

  it("shows a managed sub-goal's own manager, its share, why it needs the owner, and its note as a model's words", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.requirements = [req({ share: 2, manager: MANAGER() })]; });
    const box = screen.getByRole("generic", { name: "Its manager" });
    expect(box.textContent).toContain("Its own manager: Needs you");
    expect(box.textContent).toContain("2 of the cap's sessions at most");
    expect(box.textContent).toContain("Its last attempt needs you: read its note, then Resume it.");
    expect(box.textContent).toContain("Its last attempt (2) ended needs_owner");
    const note = within(box).getByText(/its manager's note, for you alone/).parentElement!;
    expect(note.textContent).toContain("Which README? <i>this one</i>");          // as text, never markup
    expect(box.querySelector("i")).toBeNull();
    expect(note.textContent).toContain("a model's words");
  });

  it("resumes a manager with the owner's words, beside the words saying its manager is never given its note", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.requirements = [req({ share: 1, manager: MANAGER() })]; });
    const box = screen.getByRole("generic", { name: "Its manager" });
    expect(box.textContent).toContain("Its manager is never given its note: write your words so that they say all they mean on their own.");
    fireEvent.change(within(box).getByRole("textbox", { name: "Your words for its manager's next attempt" }), { target: { value: "  The README at the root.  " } });
    fireEvent.click(within(box).getByRole("button", { name: /Resume/ }));
    await pass();
    expect(srv.calls.find((c) => c.path === "/api/ade/controller/requirements/q1/resume")?.body).toEqual({ text: "The README at the root." });
  });

  it("resumes with no words as a bare Resume, and offers none while its manager runs or is stopped", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.requirements = [req({ share: 1, manager: MANAGER({ state: "done", for_owner: "done", note: null }) })]; });
    const box = screen.getByRole("generic", { name: "Its manager" });
    fireEvent.click(within(box).getByRole("button", { name: /Resume/ }));
    await pass();
    const call = srv.calls.find((c) => c.path === "/api/ade/controller/requirements/q1/resume");
    expect(call && call.body).toBeUndefined();
    srv.requirements = [req({ share: 1, manager: MANAGER({ state: "running", for_owner: null, note: null }) })];
    await pass(3000);
    expect(within(screen.getByRole("generic", { name: "Its manager" })).queryByRole("button", { name: /Resume/ })).toBeNull();
  });

  it("says a manager waiting for a place is woken by no place freed, and lists its attempts on the owner's press", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ share: 1, manager: MANAGER({ state: "waiting", waits: "cap", for_owner: null, note: null }) })];
      srv.attempts = [{ id: "a2", n: 2, fence: 2, state: "ended", outcome: "waiting", cause: null, refused: ["cap"], refused_count: 3,
        woken_by: { wakes: [], more: 0 }, note: null, late: false, queued_at: 1, sent_at: 1, started_at: 2, ended_at: 3,
        waits_until: null, session: null, packet_digest: "d" }];
    });
    const box = screen.getByRole("generic", { name: "Its manager" });
    expect(box.textContent).toContain("Waits for a place in the cap.");
    expect(box.textContent).toContain("A place freed wakes no manager: stop a task you are done with, or raise the cap, then Resume it.");
    expect(asked("GET", "/api/ade/controller/requirements/q1/attempts")).toBe(0);
    fireEvent.click(within(box).getByRole("button", { name: "Its attempts" }));
    await pass();
    expect(within(box).getByRole("list", { name: "Its attempts" }).textContent).toContain("Attempt 2: ended, waiting · 3 of its calls refused (cap)");
  });

  it("shows a task's text as a model's words, as its manager wrote it, and a decision given with a Resume as its manager's", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.requirements = [req({ share: 1, manager: MANAGER({ state: "waiting", for_owner: null, note: null }),
        tasks: [task({ text: { text: "Write hello on line one.", cut: true, hidden_removed: 2, by: "manager" }, attempt: "a1" })] })];
      srv.decisions = [{ id: "d1", n: 4, question: null, text: "The root README.", at: 1, requirement: "q1" }];
    });
    const label = screen.getByText(/a model's words \(the task, as its manager wrote it\)/);
    expect(label.textContent).toContain("shortened, 2 hidden characters removed");
    expect(label.parentElement!.textContent).toContain("Write hello on line one.");
    expect(drawn()).toContain("given with a Resume, to that sub-goal's manager alone");
  });

  it("shows the managers' route's failure among what did not reach the workspace", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on", managers_error: "The workspace answered 404 to the managers' route: its agent is from before requirements' managers.", managers_error_at: 1_790_000_000 }); });
    expect(drawn()).toContain("The workspace answered 404 to the managers' route");
  });
});

// The Manager set up from an empty start. With no
// account confirmed as the owner's own, the account picker takes the owner through signing one in, the workspace
// started first where it is stopped, confirming it, and coming back with it chosen; its list ends with Sign in another
// account.
describe("the Manager's account from an empty start", () => {
  const NONE = (): AccountsView => ({
    claude: { active: "default", accounts: [] }, codex: { active: "default", accounts: [] },
  });
  const place = () => screen.queryByRole("dialog", { name: "An account for the Manager" });
  // the button that opens the place, the first thing each case asserts
  const button = () => screen.queryByRole("button", { name: "Sign in an account" });
  const press = async (b: HTMLElement | null) => { fireEvent.click(b!); await pass(); };

  it("offers, with no confirmed account, one button that opens the place to sign an account in", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    expect(place()).toBeTruthy();
  });

  it("starts a stopped workspace there first, since an account signs in inside it", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      srv.more["POST /api/ade/workspace/start"] = () => [200, { enabled: true, running: true }];
      useStore.setState({ ws: { enabled: true, running: false } });
    });
    expect(button()).toBeTruthy();
    await press(button());
    fireEvent.click(within(place()!).getByRole("button", { name: "Start the workspace" }));
    await pass();
    expect(asked("POST", "/api/ade/workspace/start")).toBe(1);
  });

  it("shows Connections' own Claude Code accounts there once the workspace runs, to sign one in", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: true }, accounts: NONE() });
    });
    expect(button()).toBeTruthy();
    await press(button());
    expect(within(place()!).getByRole("button", { name: /Add a Claude Code account/ })).toBeTruthy();
  });

  it("chooses for the Manager an account confirmed while it is open, and closes", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    srv.accounts = [{ account: "0123456789ab", name: "Work", state: "confirmed" }];
    await pass(3000);
    const put = srv.calls.find((c) => c.method === "PUT" && c.path === "/api/ade/controller");
    expect(put?.body).toEqual({ account: "0123456789ab" });
    expect(place()).toBeNull();
  });

  it("ends the list with Sign in another account, which opens the place and chooses no earlier account", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [{ account: "0123456789ab", name: "Alma", state: "confirmed" }];
    });
    const pick = screen.getByRole("combobox", { name: "Account" });
    const options = within(pick).getAllByRole("option");
    const last = options[options.length - 1];
    expect(last.textContent).toBe("Sign in another account\u2026");
    fireEvent.change(pick, { target: { value: last.getAttribute("value") } });
    await pass(3000);
    expect(place()).toBeTruthy();
    expect(srv.calls.filter((c) => c.method === "PUT" && c.path === "/api/ade/controller")).toHaveLength(0);
  });

  // an account only reported is not chosen; the place closes itself once it has chosen,
  // where the picker stays (the settings of a Manager set up already); and it reads Connections' accounts itself
  it("chooses only an account confirmed as the owner's own, not one only reported while it is open", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    srv.accounts = [{ account: "0123456789ab", name: "Work", state: "reported" }];
    await pass(3000);
    expect(srv.calls.filter((c) => c.method === "PUT" && c.path === "/api/ade/controller")).toHaveLength(0);
    expect(place()).toBeTruthy();
    srv.accounts = [{ account: "0123456789ab", name: "Work", state: "confirmed" }];
    await pass(3000);
    expect(srv.calls.find((c) => c.method === "PUT" && c.path === "/api/ade/controller")?.body)
      .toEqual({ account: "0123456789ab" });
  });

  it("closes itself once it has chosen, where the Manager is set up and its settings stay open", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ account: "default" });
      srv.accounts = [{ account: "default", name: null, state: "confirmed" }];
    });
    fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
    await pass();
    const pick = screen.getByRole("combobox", { name: "Account" });
    const options = within(pick).getAllByRole("option");
    fireEvent.change(pick, { target: { value: options[options.length - 1].getAttribute("value") } });
    await pass(3000);
    expect(place()).toBeTruthy();
    srv.accounts = [{ account: "default", name: null, state: "confirmed" },
      { account: "0123456789ab", name: "Work", state: "confirmed" }];
    await pass(3000);
    expect(srv.calls.find((c) => c.method === "PUT" && c.path === "/api/ade/controller")?.body)
      .toEqual({ account: "0123456789ab" });
    expect(screen.getByRole("combobox", { name: "Account" })).toBeTruthy();         // the picker stays
    expect(place()).toBeNull();
  });

  it("reads Connections' accounts itself as it opens, so a sign-in shows there without Connections open", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: true } });
    });
    expect(button()).toBeTruthy();
    const before = asked("GET", "/api/ade/workspace/accounts");
    await press(button());
    expect(asked("GET", "/api/ade/workspace/accounts")).toBeGreaterThan(before);
  });

  // the list as fresh as the place's reads, what the list showed as what was there before, two accounts at once, a
  // refused save, no reads while hidden, its words
  const WORK = { account: "0123456789ab", name: "Work", state: "confirmed" };
  const putBodies = () => srv.calls.filter((c) => c.method === "PUT" && c.path === "/api/ade/controller").map((c) => c.body);

  it("names the account it chose in the list at once, with no word that it is not among the confirmed ones", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ account: "default" });
      srv.accounts = [{ account: "default", name: null, state: "confirmed" }];
    });
    fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
    await pass();
    const pick = () => screen.getByRole("combobox", { name: "Account" }) as HTMLSelectElement;
    const options = within(pick()).getAllByRole("option");
    fireEvent.change(pick(), { target: { value: options[options.length - 1].getAttribute("value") } });
    await pass();
    srv.accounts = [{ account: "default", name: null, state: "confirmed" }, WORK];
    await pass(3000);
    await pass();
    expect(putBodies()).toEqual([{ account: "0123456789ab" }]);
    expect(pick().selectedOptions[0]?.textContent).toBe("Work");
    expect(screen.queryByText(/no longer among your confirmed accounts/)).toBeNull();
  });

  it("chooses an account confirmed after the list was read, though its own first read already finds it", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    srv.accounts = [WORK];                                   // confirmed in Connections since the list's last read
    await press(button());
    await pass();
    expect(putBodies()).toEqual([{ account: "0123456789ab" }]);
    expect(place()).toBeNull();
  });

  it("chooses neither of two accounts confirmed at once: it closes, and the list offers both and says to choose", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    srv.accounts = [WORK, { account: "ba9876543210", name: "Home", state: "confirmed" }];
    await pass(3000);
    await pass();
    expect(putBodies()).toEqual([]);
    expect(place()).toBeNull();
    const pick = screen.getByRole("combobox", { name: "Account" });
    expect(within(pick).getByRole("option", { name: "Work" })).toBeTruthy();
    expect(within(pick).getByRole("option", { name: "Home" })).toBeTruthy();
    expect(screen.getByText(/More than one account was confirmed/)).toBeTruthy();
  });

  it("closes on a refused save, the refusal said on the panel and the account offered in the list", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      srv.refuse = { path: /^PUT \/api\/ade\/controller$/, detail: "The account could not be set.", status: 409 };
    });
    expect(button()).toBeTruthy();
    await press(button());
    srv.accounts = [WORK];
    await pass(3000);
    await pass();
    expect(putBodies()).toEqual([{ account: "0123456789ab" }]);
    expect(place()).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("The account could not be set.");
    expect(within(screen.getByRole("combobox", { name: "Account" })).getByRole("option", { name: "Work" })).toBeTruthy();
  });

  it("reads nothing of the workspace's while the page is hidden", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: true } });
    });
    expect(button()).toBeTruthy();
    await press(button());
    await pass(4000);
    visible = false;
    document.dispatchEvent(new Event("visibilitychange"));
    const reads = () => srv.calls.filter((c) => c.path.startsWith("/api/ade/workspace/")).length;
    const before = reads();
    await pass(12000);
    expect(reads()).toBe(before);
    visible = true;
  });

  it("says the workspace is starting while it starts, and why a start failed", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: false, starting: true, made: true } });
    });
    expect(button()).toBeTruthy();
    await press(button());
    expect(place()!.textContent).toContain("Starting your workspace");
    expect(place()!.textContent).not.toContain("stopped");
    act(() => { useStore.setState({ ws: { enabled: true, running: false, made: true, start_error: "The machine was not made." } }); });
    await pass();
    expect(within(place()!).getByRole("alert").textContent).toContain("The machine was not made.");
  });

  it("offers no start where the deployment gives the account no workspace, and says so", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, allowed: false, running: false, detail: "No workspace is offered here." } });
    });
    expect(button()).toBeTruthy();
    await press(button());
    expect(within(place()!).queryByRole("button", { name: /Start/ })).toBeNull();
    expect(place()!.textContent).toContain("No workspace is offered here.");
  });

  it("says a confirmed account waits before it counts as the owner's, and that the Manager is set to it then", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    expect(place()!.textContent).toContain("waits before it counts as yours");
  });

  it("takes the workspace as the shell does: one that runs but is still starting is starting, not signed in to", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: true, starting: true, made: true } });
    });
    expect(button()).toBeTruthy();
    await press(button());
    expect(place()!.textContent).toContain("Starting your workspace");
    expect(within(place()!).queryByRole("button", { name: /Add a Claude Code account/ })).toBeNull();
  });

  it("clears the list's word that its accounts could not be read once the place has read them", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      srv.refuse = { path: /^GET \/api\/ade\/agents\/accounts$/, detail: "Not now.", status: 500 };
    });
    expect(screen.getByText(/could not be read just now/)).toBeTruthy();
    srv.refuse = null;
    const pick = screen.getByRole("combobox", { name: "Account" });
    const options = within(pick).getAllByRole("option");
    fireEvent.change(pick, { target: { value: options[options.length - 1].getAttribute("value") } });
    await pass();
    expect(place()).toBeTruthy();
    expect(screen.queryByText(/could not be read just now/)).toBeNull();
  });

  it("keeps the list to confirmed accounts as it takes the place's reads: one only reported is not offered", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(button()).toBeTruthy();
    await press(button());
    srv.accounts = [{ ...WORK, state: "reported" }];
    await pass(3000);
    expect(within(screen.getByRole("combobox", { name: "Account" })).queryByRole("option", { name: "Work" })).toBeNull();
  });
});

// The role is named Manager. No word the panel shows says controller, in its
// text or its labels, titles and placeholders, set up or not; code names, routes and stored names stay. Words the
// server sends (a refusal, a flag, the log) are the server's, so these cases send none of its that say controller.
describe("the Manager's words", () => {
  const shown = () => [document.body.textContent ?? "", ...Array.from(document.body.querySelectorAll("*"))
    .flatMap((n) => ["aria-label", "title", "placeholder", "alt"].map((a) => n.getAttribute(a) ?? ""))].join("\n");

  it("never say controller before the Manager is set up", async () => {
    await mount(() => { srv.controller = null; srv.accounts = []; });
    expect(screen.getByText(/not set up yet/)).toBeTruthy();                       // what the guard reads is drawn
    expect(screen.getByRole("button", { name: "Sign in an account" })).toBeTruthy();
    expect(shown()).not.toMatch(/controller/i);
  });

  it("never say controller in the place to sign an account in, the workspace stopped or running", async () => {
    await mount(() => {
      srv.controller = null; srv.accounts = [];
      useStore.setState({ ws: { enabled: true, running: false, made: true } });
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in an account" }));
    await pass();
    expect(screen.getByRole("dialog", { name: "An account for the Manager" })).toBeTruthy();
    expect(shown()).not.toMatch(/controller/i);
    act(() => {
      useStore.setState({ ws: { enabled: true, running: true },
        accounts: { claude: { active: "default", accounts: [] }, codex: { active: "default", accounts: [] } } });
    });
    await pass();
    expect(screen.getByRole("button", { name: /Add a Claude Code account/ })).toBeTruthy();
    expect(shown()).not.toMatch(/controller/i);
  });

  it("never say controller once it is set up and on, with its goals, its settings and its log open", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on", session: "s1", account_confirmed: false, mirrored_version: 1,
        version: 2 });
      srv.accounts = [{ account: "default", name: null, state: "confirmed" }];
      srv.requests = [GOAL()];
      srv.requirements = [req({ tasks: [task()] })];
      srv.other_sessions = [task({ id: "t9", requirement: null })];
      srv.questions = [{ id: "qq1", requirement: null, text: "Which repo?", cut: false, hidden_removed: 0, state: "open",
        asked_at: 1, answered_at: null, decision: null }];
      srv.decisions = [{ id: "d1", n: 1, question: null, text: "Use the demo repo", at: 1 }];
      srv.events = [{ seq: 2, kind: "plan", requirement: "q1", task: null, at: 1, words: "Planned a sub-goal" }];
    });
    fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
    fireEvent.click(screen.getByRole("button", { name: "Show the log" }));
    await pass();
    expect(shown()).not.toMatch(/controller/i);
  });

  it("never say controller while it is read, set up and off, or folded away", async () => {
    srv.controller = CONTROLLER({ state: "off" });
    srv.accounts = [{ account: "default", name: null, state: "confirmed" }];
    render(<ControllerPanel />);
    expect(screen.getByLabelText(/^Reading the /)).toBeTruthy();                 // before its first read answers
    expect(shown()).not.toMatch(/controller/i);
    await pass();
    expect(screen.getByRole("button", { name: /Turn it on/ })).toBeTruthy();
    expect(shown()).not.toMatch(/controller/i);
    fireEvent.click(screen.getByRole("button", { name: /^Hide the / }));
    expect(screen.getByRole("button", { name: /^Show the / })).toBeTruthy();
    expect(shown()).not.toMatch(/controller/i);
  });

  it("never say controller with its chat served, a goal waiting for the go, a sub-goal waiting for it", async () => {
    srv.controller = CONTROLLER({ state: "off", session: "s1" });
    srv.requests = [GOAL()];
    srv.requirements = [req({ waits_for_go: true, activity: "waiting for go", share: 1, manager: { state: "waiting",
      waits: "on", waits_until: null, attempts: 1, failures: 0, for_owner: null, last: null, note: null } })];
    render(<ControllerPanel onOpenSession={() => undefined} />);
    await pass();
    expect(screen.getByRole("button", { name: /^Open the .*'s chat$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Give the go" })).toBeTruthy();
    expect(screen.getByRole("generic", { name: "Its manager" }).textContent).toContain("Waits for the ");
    expect(shown()).not.toMatch(/controller/i);
  });
});

// ---- a running linked session made the Manager ---------------------------------------------------------------------

describe("a linked session as the Manager", () => {
  const credentials = {
    get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: {
      clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
      signature: new Uint8Array([3]).buffer, userHandle: new Uint8Array([4]).buffer } })),
  };
  const LANE = "a".repeat(32), QUIET = "c".repeat(32), CLOUD = "b".repeat(32);
  // the settings a holder holds the role under, and their digest as the server's canonical form gives it
  const SETTINGS = { cap: 3, models: ["opus", "sonnet"], efforts: ["max"], confirm_plans: true };
  const DIGEST = "sha256:9336348a86929bac1cb6c5d47b234dd755beed13d82bfd9f21db53c0ce50b76f";
  const NOTICE = "Proofpane holds none of this session's own calls: only the acts it makes as the Manager, through " +
    "the app's checked routes.";
  const OFFER = (holder: Dict | null = null): Dict => ({ settings_digest: DIGEST, settings: SETTINGS, life_most: 604800,
    holder, links: [
    { id: LANE, name: "Lane1", kind: "local", last_heard_at: 1_790_000_000, silent: false, offered: true,
      notice: null },
    { id: QUIET, name: "Quiet", kind: "local", last_heard_at: 1_780_000_000, silent: true, offered: false,
      notice: null },
    { id: CLOUD, name: "cloud", kind: "cloud", last_heard_at: 1_790_000_000, silent: false, offered: true,
      notice: NOTICE },
  ] });
  const HOLDER = (over: Dict = {}): Dict => ({ link: LANE, name: "Lane1", kind: "local", since: 1_790_000_000,
    until: 1_790_604_800, live: true, silent: false, last_heard_at: 1_790_000_000, notice: null, ...over });
  const nonce = (b?: Dict): [number, unknown] => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example",
    deployment: "pp.example", account: "u", server_time: Math.floor(Date.now() / 1000) }];
  const linked = () => within(region()).getByRole("group", { name: "A linked session as the Manager" });
  const chooser = async () => {
    fireEvent.click(within(linked()).getByRole("button", { name: /Choose a linked session/ }));
    await pass();
  };
  const goals = () => within(region()).getByRole("region", { name: "Goals" });
  const sent = (method: string, path: string) => srv.calls.find((c) => c.method === method && c.path === path)?.body;

  beforeEach(() => {
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
    credentials.get.mockClear();
  });
  afterEach(() => { delete (navigator as unknown as { credentials?: unknown }).credentials; });

  it("lists the owner's linked sessions, a silent one shown and not offered, a cloud one with its notice", async () => {
    await mount(() => {
      srv.controller = CONTROLLER();
      srv.more["GET /api/ade/controller/holders"] = () => [200, OFFER()];
    });
    await chooser();
    const make = within(linked()).getAllByRole("button", { name: "Make it the Manager" });
    expect(make.map((b) => (b as HTMLButtonElement).disabled)).toEqual([false, true, false]);
    expect(linked().textContent).toContain("silent: not offered");
    expect(linked().textContent).toContain(NOTICE);
  });

  it("reviews the choice first, then signs it: the link, the settings' digest and a week", async () => {
    await mount(() => {
      srv.controller = CONTROLLER();
      srv.more["GET /api/ade/controller/holders"] = () => [200, OFFER()];
      srv.more["POST /api/ade/link/nonce"] = nonce;
      srv.more["POST /api/ade/controller/holder"] = () => [200, HOLDER()];
    });
    await chooser();
    fireEvent.click(within(linked()).getAllByRole("button", { name: "Make it the Manager" })[0]);
    // the payload's digest is crypto.subtle's, which ends on a real turn of the loop: waited for, not timed
    await vi.waitFor(() =>
      expect(linked().textContent).toContain("You are making the linked session Lane1 the Manager"));
    expect(credentials.get).not.toHaveBeenCalled();                     // reviewed, not signed yet
    fireEvent.click(within(linked()).getByRole("button", { name: "Sign" }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/controller/holder")).toBeTruthy());
    const p = (sent("POST", "/api/ade/controller/holder") as Dict).payload as Dict;
    expect([p.purpose, p.to, p.settings, Number(p.expires_at) - Number(p.created_at)])
      .toEqual(["manager", LANE, DIGEST, 604800]);
    expect(credentials.get).toHaveBeenCalledTimes(1);
  });

  it("says which session holds the role, until when, and where its tasks run; taking it back needs no passkey",
    async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ account: null });
      srv.holder = HOLDER();
      srv.more["GET /api/ade/controller/holders"] = () => [200, OFFER(HOLDER())];
      srv.more["DELETE /api/ade/controller/holder"] = () => { srv.holder = null; return [200, { holder: null }]; };
    });
    expect(linked().textContent).toContain("The Manager is the linked session Lane1");
    expect(linked().textContent).toContain("on its own machine");
    fireEvent.click(within(linked()).getByRole("button", { name: "Take the role back" }));
    await pass();
    expect(asked("DELETE", "/api/ade/controller/holder")).toBe(1);
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("reads the linked sessions only once the owner opens the chooser, never in the background", async () => {
    await mount(() => { srv.controller = CONTROLLER(); });
    await pass(60000);
    expect(asked("GET", "/api/ade/controller/holders")).toBe(0);
    await chooser();
    expect(asked("GET", "/api/ade/controller/holders")).toBe(1);
  });

  it("an ended hold says it holds nothing now and what the owner may do", async () => {
    await mount(() => { srv.controller = CONTROLLER(); srv.holder = HOLDER({ live: false }); });
    expect(linked().textContent).toContain("Its hold ended");
    expect(linked().textContent).toContain("it holds nothing now");
  });

  it("a silent holder: goals sent wait for it", async () => {
    await mount(() => { srv.controller = CONTROLLER(); srv.holder = HOLDER({ silent: true }); });
    expect(linked().textContent).toContain("goals sent wait for it");
  });

  it("while a linked session holds the role, a goal is reviewed, then signed to it, its words alone", async () => {
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.holder = HOLDER();
      srv.more["POST /api/ade/link/nonce"] = nonce;
    });
    expect(within(goals()).queryByRole("button", { name: /Checks \(optional\)/ })).toBeNull();
    expect(goals().textContent).toContain("Checks and protected paths are no part of what you sign");
    fireEvent.change(within(goals()).getByRole("textbox", { name: "A goal for the Manager" }),
      { target: { value: "Make the README say hello." } });
    fireEvent.click(within(goals()).getByRole("button", { name: /Review/ }));
    await vi.waitFor(() => expect(within(goals()).getByRole("button", { name: "Sign and send" })).toBeTruthy());
    expect(credentials.get).not.toHaveBeenCalled();
    fireEvent.click(within(goals()).getByRole("button", { name: "Sign and send" }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/controller/requests")).toBeTruthy());
    const body = sent("POST", "/api/ade/controller/requests") as Dict;
    const p = body.payload as Dict;
    expect([p.purpose, p.to, p.text, Object.keys(body).sort()])
      .toEqual(["instruction", LANE, "Make the README say hello.", ["credential", "payload"]]);
  });

  it("a task a linked session reported is marked as its own report, by its name", async () => {
    await mount(() => {
      srv.controller = CONTROLLER();
      srv.holder = HOLDER();
      srv.other_sessions = [{ id: "t".repeat(32), requirement: null, session: null, repo: "demo", branch: null,
        base: null, account: null, program: "claude", model: "opus", effort: "max", state: "working", waits_until: null,
        started_at: 1_790_000_000, stopped_at: null, ended_at: null, continues: 0, flag: null, checks: null, why: null,
        checking: false, text: null, attempt: null, reported_by: LANE }];
    });
    expect(drawn()).toContain("As Lane1 reported it, from its own machine");
  });

  // the settings a hold is signed under, and how long a signed goal lasts
  it("the review names the settings the session will hold the role under", async () => {
    await mount(() => {
      srv.controller = CONTROLLER();
      srv.more["GET /api/ade/controller/holders"] = () => [200, OFFER()];
      srv.more["POST /api/ade/link/nonce"] = nonce;
    });
    await chooser();
    fireEvent.click(within(linked()).getAllByRole("button", { name: "Make it the Manager" })[0]);
    await vi.waitFor(() => expect(linked().textContent).toContain("You are making the linked session Lane1"));
    expect(linked().textContent).toContain("the cap 3, the models Opus, Sonnet, the efforts Max, and your go");
  });

  it("signs the digest of the settings it shows, never one the server gives beside them", async () => {
    await mount(() => {
      srv.controller = CONTROLLER();
      srv.more["GET /api/ade/controller/holders"] = () => [200, { ...OFFER(), settings_digest: "sha256:" +
        "e".repeat(64) }];
      srv.more["POST /api/ade/link/nonce"] = nonce;
      srv.more["POST /api/ade/controller/holder"] = () => [200, HOLDER()];
    });
    await chooser();
    fireEvent.click(within(linked()).getAllByRole("button", { name: "Make it the Manager" })[0]);
    await vi.waitFor(() => expect(within(linked()).getByRole("button", { name: "Sign" })).toBeTruthy());
    fireEvent.click(within(linked()).getByRole("button", { name: "Sign" }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/controller/holder")).toBeTruthy());
    expect(((sent("POST", "/api/ade/controller/holder") as Dict).payload as Dict).settings).toBe(DIGEST);
  });

  it("with a hold that ended, the goal box sends nothing and says why", async () => {
    await mount(() => { srv.controller = CONTROLLER({ state: "on" }); srv.holder = HOLDER({ live: false }); });
    expect(within(goals()).queryByRole("button", { name: /Send|Review/ })).toBeNull();
    expect(goals().textContent).toContain("Its hold has ended");
  });

  it("a goal is signed for a day, within the hold's own end", async () => {
    const now = Math.floor(Date.now() / 1000);
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.holder = HOLDER({ until: now + 7 * 86400 });
      srv.more["POST /api/ade/link/nonce"] = nonce;
    });
    fireEvent.change(within(goals()).getByRole("textbox", { name: "A goal for the Manager" }),
      { target: { value: "Make the README say hello." } });
    fireEvent.click(within(goals()).getByRole("button", { name: /Review/ }));
    await vi.waitFor(() => expect(within(goals()).getByRole("button", { name: "Sign and send" })).toBeTruthy());
    fireEvent.click(within(goals()).getByRole("button", { name: "Sign and send" }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/controller/requests")).toBeTruthy());
    const p = (sent("POST", "/api/ade/controller/requests") as Dict).payload as Dict;
    expect(Number(p.expires_at) - Number(p.created_at)).toBe(86400);
  });

  it("a goal is signed to end with the hold, where the hold ends within a day", async () => {
    const now = Math.floor(Date.now() / 1000);
    await mount(() => {
      srv.controller = CONTROLLER({ state: "on" });
      srv.holder = HOLDER({ until: now + 7200 });
      srv.more["POST /api/ade/link/nonce"] = nonce;
    });
    fireEvent.change(within(goals()).getByRole("textbox", { name: "A goal for the Manager" }),
      { target: { value: "Make the README say hello." } });
    fireEvent.click(within(goals()).getByRole("button", { name: /Review/ }));
    await vi.waitFor(() => expect(within(goals()).getByRole("button", { name: "Sign and send" })).toBeTruthy());
    fireEvent.click(within(goals()).getByRole("button", { name: "Sign and send" }));
    await vi.waitFor(() => expect(sent("POST", "/api/ade/controller/requests")).toBeTruthy());
    const p = (sent("POST", "/api/ade/controller/requests") as Dict).payload as Dict;
    const life = Number(p.expires_at) - Number(p.created_at);
    expect(life > 7000 && life <= 7200).toBe(true);
  });
});
