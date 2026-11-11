import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { LinkSessions } from "./api";
import { useStore } from "./store";
import { browser, link, machineSession, shellServer } from "./shellTestkit";
import { clock } from "./when";

// The page's shell as its owner meets it, the whole App against a server of the test's own (shellTestkit.ts), on fake
// timers: what it shows with the workspace stopped or never started, what it asks the server and how often, and what
// it says when the server cannot be reached.

// the panels the tests open load as they would in the browser (lazy); their code is read once here, before the clock
// of any test runs, so a busy machine's first read of it does not count against one
beforeAll(async () => {
  await Promise.all([import("./components/Channel"), import("./components/CommandPalette"), import("./components/ControlView")]);
}, 60000);

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("pp-open-token", "test-token.sig");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.title = "";
  useStore.setState(useStore.getInitialState());
});

/** Time passes on the page, its reads answered meanwhile. */
const pass = async (ms = 1000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const list = () => screen.getByRole("complementary", { name: "Sessions" });
const main = () => screen.getByRole("main");

describe("the owner's Mac sessions, whatever the workspace", () => {
  it("are listed beside a stopped workspace, which is a state of the main panel, and Linked sessions is in the title bar", async () => {
    browser();
    shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(2000);
    expect(within(list()).getByText("Make sign-in work")).toBeTruthy();
    expect(within(main()).getByRole("heading", { name: "Your workspace is stopped" })).toBeTruthy();
    expect(within(main()).getByText(/Its repositories and sessions are kept/)).toBeTruthy();
    expect(within(main()).getByRole("button", { name: /Start workspace/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Linked sessions" })).toBeTruthy();
    // the workspace's own sessions are read from it: the list says they show while it runs, and offers no New
    expect(within(list()).getByText("The workspace's sessions are listed here while it runs.")).toBeTruthy();
    expect(within(list()).queryByRole("button", { name: /New/ })).toBeNull();
    // a Mac is linked: linking one is not offered again
    expect(screen.queryByRole("button", { name: "Link your Mac" })).toBeNull();
  });

  it("are listed the same on a deployment that gives the account no workspace", async () => {
    browser();
    shellServer({ ws: { enabled: true, allowed: false, detail: "On this deployment a workspace is a shell on the app's own machine, so only an admin has one." },
      links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(2000);
    expect(within(list()).getByText("Make sign-in work")).toBeTruthy();
    expect(within(main()).getByRole("heading", { name: "No workspace on this deployment" })).toBeTruthy();
  });

  it("are not hidden while a workspace never started says it can be started, never that it is stopped or keeps anything", async () => {
    browser();
    shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: false, made: false } });
    render(<App />);
    await pass(2000);
    expect(within(main()).getByRole("heading", { name: "Start your workspace" })).toBeTruthy();
    expect(main().textContent).not.toMatch(/stopped|kept/);
    expect(within(main()).getByRole("button", { name: /Start workspace/ })).toBeTruthy();
  });

  it("can be linked from the empty states while none is linked, which opens Linked sessions on linking one", async () => {
    browser();
    const server = shellServer();
    render(<App />);
    await pass(2000);
    expect(screen.getByRole("button", { name: "Linked sessions" })).toBeTruthy();         // before anything is linked
    expect(within(list()).getByText(/Your Mac's Claude Code sessions are listed here once it is linked/)).toBeTruthy();
    expect(within(list()).getByRole("button", { name: "Link your Mac" })).toBeTruthy();
    const linking = async (button: HTMLElement) => {
      fireEvent.click(button);
      // its code comes in as it is fetched, in the time that takes
      await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy(), { timeout: 15000 });
      const dialog = screen.getByRole("dialog", { name: "Linked sessions" });
      // straight to linking one: its name asked first, on this Mac, the keyboard in its field
      const form = within(dialog).getByRole("form", { name: "Link a session" });
      expect(document.activeElement).toBe(within(form).getByLabelText("What to call it"));
      expect((within(form).getByRole("radio", { name: /This Mac/ }) as HTMLInputElement).checked).toBe(true);
      fireEvent.click(within(dialog).getByRole("button", { name: "Close Linked sessions" }));
      expect(screen.queryByRole("dialog", { name: "Linked sessions" })).toBeNull();
    };
    await linking(within(main()).getByRole("button", { name: "Link your Mac" }));          // the main panel's
    await linking(within(list()).getByRole("button", { name: "Link your Mac" }));          // the list's
    // Get started's, with the workspace running and nothing in it yet
    server.state.ws = { ...server.state.ws, running: true, made: true };
    await pass(6000);
    const step = within(main()).getAllByRole("listitem").find((li) => li.textContent?.includes("Link your Mac"))!;
    await linking(within(step).getByRole("button", { name: "Link" }));
    // the title bar's opens it on its list
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy(), { timeout: 15000 });
    expect(screen.queryByRole("form", { name: "Link a session" })).toBeNull();
  });

  it("are not said to be missing before the linked sessions are read: linking a Mac is offered only once none is known", async () => {
    browser();
    shellServer();
    const real = globalThis.fetch;
    let release = () => {};
    const held = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      if (String(input).endsWith("/api/ade/link/sessions")) await held;
      return answer;
    }));
    render(<App />);
    await pass(2000);
    expect(within(main()).getByRole("heading", { name: "Your workspace is stopped" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Link your Mac" })).toBeNull();
    expect(within(list()).queryByText(/once it is linked/)).toBeNull();
    release();
    await pass(1000);
    expect(screen.getAllByRole("button", { name: "Link your Mac" })).toHaveLength(2);    // the main panel's, the list's
  });

  it("are not said to be none before their list is read", async () => {
    browser();
    shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    const real = globalThis.fetch;
    let release = () => {};
    const held = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      if (String(input).endsWith("/api/ade/link/history")) await held;
      return answer;
    }));
    render(<App />);
    await pass(2000);
    expect(useStore.getState().links?.links).toHaveLength(1);
    expect(within(list()).queryByText(/Your Mac is linked/)).toBeNull();
    release();
    await pass(1000);
    expect(within(list()).getByText("Make sign-in work")).toBeTruthy();
  });

  it("are a tap away on a phone with the workspace stopped: the list is in its drawer", async () => {
    browser({ phone: true });
    shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(2000);
    fireEvent.click(screen.getByRole("button", { name: "Sessions" }));
    const drawer = screen.getByRole("dialog", { name: "Sessions" });
    expect(within(drawer).getByText("Make sign-in work")).toBeTruthy();
  });
});

describe("the shell's reads", () => {
  it("are the summary alone on an idle page, once each 5 seconds, and nothing else it already has", async () => {
    browser();
    const server = shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(10000);
    const from = server.calls.length;
    await pass(60000);
    expect(server.api(from)).toEqual(Array(12).fill("GET /api/ade/summary"));
  });

  it("are the summary alone with Linked sessions open too: the panel reads nothing on a timer of its own", async () => {
    browser();
    const server = shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(10000);
    fireEvent.click(screen.getByRole("button", { name: "Linked sessions" }));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy(), { timeout: 15000 });
    await vi.waitFor(() => expect(within(screen.getByRole("dialog", { name: "Linked sessions" })).getByText("Make sign-in work")).toBeTruthy());
    await pass(5000);                         // as it opens: where the account's moves left the Mac's session, read once
    const from = server.calls.length;
    await pass(60000);
    expect(server.api(from)).toEqual(Array(12).fill("GET /api/ade/summary"));
  });

  it("keep their one pace however often the window gains the focus, which reads at once", async () => {
    browser();
    const server = shellServer();
    render(<App />);
    await pass(10000);
    const at = server.calls.length;
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("focus"));
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(server.api(at)).toEqual(["GET /api/ade/summary"]);
    const from = server.calls.length;
    await pass(60000);
    expect(server.api(from)).toEqual(Array(12).fill("GET /api/ade/summary"));
  });

  it("read the summary alone with the workspace running too, but for the usage windows once a minute and the control view's map", async () => {
    browser();
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    render(<App />);
    await pass(10000);
    const from = server.calls.length;
    await pass(60000);
    const asked = server.api(from);
    expect(asked.filter((c) => c === "GET /api/ade/summary")).toHaveLength(12);
    // the control view, the main panel with no session open, reads its map each 3 seconds while it shows
    expect(asked.filter((c) => c === "GET /api/ade/workspace/map")).toHaveLength(20);
    // the controller panel rides the control view and reads its view each 3 seconds too
    expect(asked.filter((c) => c === "GET /api/ade/controller")).toHaveLength(20);
    // once a minute: the control view's accounts, and the controller panel's confirmed accounts for its account picker
    expect(asked.filter((c) => c !== "GET /api/ade/summary" && c !== "GET /api/ade/workspace/map" && c !== "GET /api/ade/controller").sort())
      .toEqual(["GET /api/ade/agents/accounts", "GET /api/ade/workspace/accounts"]);
  });

  it("read a list again once its stamp moves, and only that list", async () => {
    browser();
    const server = shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] } });
    render(<App />);
    await pass(10000);
    expect(document.title).toBe("Proofpane Community");
    const question = { seq: 4, time: "2026-10-09T03:21:00Z", kind: "question", from: "MacBook Pro", to: "owner", ref: null, text: "Push?", link_id: "L1" };
    server.state.links = { ...server.state.links, questions: [question] };
    const from = server.calls.length;
    await pass(5000);
    expect(server.api(from)).toEqual(["GET /api/ade/summary", "GET /api/ade/link/sessions"]);
    expect(document.title).toBe("(1) Proofpane Community");
    expect(screen.getByRole("button", { name: "Linked sessions, 1 needs you" })).toBeTruthy();
  });

  it("are each list on its own from a server that has no summary, as before it", async () => {
    browser();
    const server = shellServer({ summary: false, history: [machineSession()], links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] } });
    render(<App />);
    await pass(10000);
    expect(within(list()).getByText("Make sign-in work")).toBeTruthy();
    const from = server.calls.length;
    await pass(30000);
    const asked = server.api(from);
    expect(asked.filter((c) => c === "GET /api/ade/summary")).toEqual([]);
    expect(asked.filter((c) => c === "GET /api/ade/workspace").length).toBeGreaterThanOrEqual(9);
    expect(asked).toContain("GET /api/ade/approvals");
    expect(asked).toContain("GET /api/ade/link/history");
  });
});

describe("the page with its server out of reach", () => {
  it("says so in one steady line, with when it last read the server, said once, and tries again less and less often", async () => {
    browser();
    const server = shellServer();
    render(<App />);
    await pass(10000);
    const read = useStore.getState().lastRead!;
    server.state.down = true;
    await pass(5000);
    const line = screen.getByText(`Offline — last read ${clock(new Date(read))}, reconnecting…`);
    const region = line.closest('[role="status"]')!;
    expect(region).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();                    // no toast
    expect(useStore.getState().offline).toBe(true);
    // what needs the server waits for it: Start, here
    expect((within(main()).getByRole("button", { name: /Start workspace/ }) as HTMLButtonElement).disabled).toBe(true);
    const from = server.calls.length;
    await pass(120000);
    // the same region, with the same words: a screen reader said them once
    expect(screen.getByText(`Offline — last read ${clock(new Date(read))}, reconnecting…`).closest('[role="status"]')).toBe(region);
    expect(screen.queryByRole("alert")).toBeNull();
    // tries at 10, 20, 30, 30 seconds apart: not each 5 seconds (24 in two minutes)
    expect(server.api(from).length).toBeLessThanOrEqual(6);
    server.state.down = false;
    await act(async () => { window.dispatchEvent(new Event("online")); await vi.advanceTimersByTimeAsync(100); });
    expect(useStore.getState().offline).toBe(false);
    expect(screen.queryByText(/Offline/)).toBeNull();
    expect(region.isConnected && region.textContent).toBe("");        // the live region stays, empty
  });

  it("says so too when a proxy answers 502 in the app's place, and backs off as it does then", async () => {
    browser();
    const server = shellServer();
    render(<App />);
    await pass(10000);
    const read = useStore.getState().lastRead!;
    server.state.gateway = 502;
    const from = server.calls.length;
    await pass(60000);
    expect(screen.getByText(`Offline — last read ${clock(new Date(read))}, reconnecting…`)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();                    // no toast said again at each poll
    // tries at 5, 10, 20 and 40 seconds: not each 5 seconds (12 in a minute)
    expect(server.api(from).length).toBeLessThanOrEqual(5);
    server.state.gateway = null;
    await act(async () => { window.dispatchEvent(new Event("online")); await vi.advanceTimersByTimeAsync(100); });
    expect(useStore.getState().offline).toBe(false);
    expect(screen.queryByText(/Offline/)).toBeNull();
  });

  it("counts each try once, however often the window gains the focus during it, so it waits 5, 10, then 20 seconds", async () => {
    browser();
    const server = shellServer();
    render(<App />);
    await pass(10000);
    server.state.down = true;
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("focus"));
      await vi.advanceTimersByTimeAsync(10);
    });
    const from = server.calls.length;
    await pass(4980);
    expect(server.api(from)).toEqual([]);
    await pass(40);                                                   // 5 seconds after the try
    expect(server.api(from)).toHaveLength(1);
    await pass(10000);                                                // then 10
    expect(server.api(from)).toHaveLength(2);
    await pass(20000);                                                // then 20
    expect(server.api(from)).toHaveLength(3);
  });
});

describe("the tab's title", () => {
  it("counts the linked sessions' questions and requests to be linked, and the agents' runs waiting, with the rest", async () => {
    browser();
    const ask = { id: "a1", name: "Cloud planner", kind: "cloud", fingerprint: "f".repeat(32), asked_from: null, created_at: 1,
      expires_at: 2, session_ref: null, state: "pending" as const, hold_id: null, takes_effect_at: null, told: null, failed: null };
    const question = { seq: 4, time: "2026-10-09T03:21:00Z", kind: "question", from: "MacBook Pro", to: "owner", ref: null, text: "Push?", link_id: "L1" };
    shellServer({ links: { links: [link()], questions: [question], public_url: "https://pp.example", asks: [ask] }, agentsWaiting: 2,
      approvals: [{ id: "q1", command: "git push", state: "pending", created_at: 1 }] });
    render(<App />);
    await pass(3000);
    expect(document.title).toBe("(5) Proofpane Community");
  });

  it("counts the linked sessions' open prompts, those to look at too, and their proposals, and Approvals' badge counts them with their questions", async () => {
    browser();
    const question = { seq: 4, time: "2026-10-09T03:21:00Z", kind: "question", from: "MacBook Pro", to: "owner", ref: null, text: "Push?", link_id: "L1" };
    const prompt = (seq: number, look: boolean) => ({ seq, time: "2026-10-09T03:22:00Z", kind: "prompt", from: "MacBook Pro", to: "owner",
      ref: null, text: look ? "ExitPlanMode" : "e2e1.sealed", link_id: "L1", deadline_at: 1_790_001_800, look });
    const proposal = { seq: 9, time: "2026-10-09T03:23:00Z", kind: "proposal", from: "MacBook Pro", to: "owner", ref: null,
      text: "e2e1.sealed", client_id: "0123456789abcdef0123456789abcdef", link_id: "L1", for_id: "L2", for: "Planner", deadline_at: 1_790_086_400 };
    shellServer({ links: { links: [link()], questions: [question], public_url: "https://pp.example", asks: [],
      prompts: [prompt(7, false), prompt(8, true)], proposals: [proposal] } as unknown as LinkSessions });
    render(<App />);
    await pass(3000);
    // the question (as before), two prompts and a proposal
    expect(document.title).toBe("(4) Proofpane Community");
    expect(screen.getByRole("button", { name: "Approvals, 4 waiting" })).toBeTruthy();
  });

  it("counts an ask for more in a task's grant with the approvals, in the title and on the Approvals button", async () => {
    browser();
    shellServer({ approvals: [{ id: "q1", command: "git push", state: "pending", created_at: 1 }] });
    // the server's list of asks for more in its tasks' grants, which the test's kit does not serve itself
    const real = globalThis.fetch;
    const widening = { id: "9".repeat(32), task_id: "0".repeat(32), task_title: "Fix the parser", requester: { kind: "agent", id: "a1a2a3a4a5a6", name: "Triage" },
      base_version: 0, base_digest: "", ask: `sha256:${"ab".repeat(32)}`, adds: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: null, rungs: null },
      scope: { repositories: [], environments: [], secrets: [], egress: ["registry.npmjs.org"], lease_end: 1_790_604_800, rungs: { merge: "L2", deploy: "L2-high" } },
      state: "waiting", created_at: 1, expires_at: 2, decided_at: null, version: null };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      new URL(String(input), "http://localhost").pathname === "/api/ade/agents/tasks/grant-widenings"
        ? new Response(JSON.stringify({ widenings: [widening] }), { status: 200 })
        : real(input, init))));
    render(<App />);
    await pass(3000);
    expect(document.title).toBe("(2) Proofpane Community");
    expect(screen.getByRole("button", { name: "Approvals, 2 waiting" })).toBeTruthy();
  });
});

describe("the palette with the workspace stopped", () => {
  it("offers starting it first, and nothing that needs it running", async () => {
    browser();
    shellServer();
    render(<App />);
    await pass(2000);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(3), { timeout: 15000 });
    const options = screen.getAllByRole("option").map((o) => o.textContent || "");
    expect(options[0]).toBe("Start the workspace");
    expect(screen.getAllByRole("option")[0].getAttribute("aria-selected")).toBe("true");
    for (const needs of [/^New session/, /^Clone a repository/, /^Add a repository/, /^Stop the workspace/, /^Connections/]) {
      expect(options.some((o) => needs.test(o)), String(needs)).toBe(false);
    }
    expect(options.some((o) => o.startsWith("Linked sessions"))).toBe(true);
  });
});

describe("another account signing in on the tab after a sign-out", () => {
  // the shell is made anew for each account, so nothing the last one had open is shown to the next
  const BOB = { id: "u2", email: "bob@example.test", admin: false, can_sign_in_again: true };
  beforeAll(async () => {
    await Promise.all([import("./components/SignIn"), import("./components/NewSession")]);
  }, 60000);

  /** The server signs Bob in with a password; every other route is shellServer's. */
  const bobSignsIn = () => {
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      if (String(input).endsWith("/api/auth/login") && init.method === "POST") {
        return new Response(JSON.stringify({ token: "token-of-bob.sig", user: BOB }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return real(input, init);
    }));
  };
  const signOutFromThePalette = async () => {
    fireEvent.keyDown(window, { key: "k", metaKey: true });           // the palette opens over any dialog
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Sign out/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Sign out/ }));
    await vi.waitFor(() => expect(screen.getByLabelText("Password")).toBeTruthy(), { timeout: 15000 });
  };
  const signInAsBob = async () => {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: BOB.email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await pass(3000);
    expect(useStore.getState().user?.id).toBe("u2");
  };

  it("is not shown the machine session Linked sessions was reading", async () => {
    browser();
    const server = shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    bobSignsIn();
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()).getByText("Make sign-in work"));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy(), { timeout: 15000 });
    const reading = screen.getByRole("dialog", { name: "Linked sessions" });
    expect(reading.textContent).toContain("Make sign-in work");
    expect(reading.textContent).toContain("MacBook Pro");
    await signOutFromThePalette();
    // what the server has for Bob: no link, nothing listed
    server.state.links = { links: [], questions: [], public_url: "https://pp.example", asks: [] };
    server.state.history = [];
    await signInAsBob();
    expect(screen.queryByRole("dialog", { name: "Linked sessions" })).toBeNull();
    expect(document.body.textContent).not.toContain("Make sign-in work");
    expect(document.body.textContent).not.toContain("MacBook Pro");
  });

  it("is made anew when the store's account changes under it, whatever changed it", async () => {
    browser();
    const server = shellServer({ links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()).getByText("Make sign-in work"));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Linked sessions" })).toBeTruthy(), { timeout: 15000 });
    server.state.links = { links: [], questions: [], public_url: "https://pp.example", asks: [] };
    server.state.history = [];
    act(() => { useStore.getState().signedIn("token-of-bob.sig", BOB); });
    await pass(3000);
    expect(screen.queryByRole("dialog", { name: "Linked sessions" })).toBeNull();
    expect(document.body.textContent).not.toContain("Make sign-in work");
  });

  it("is not shown the task the last one was trying with another agent", async () => {
    browser();
    const task = "Fix the invoice totals for Alice's team";
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true }, repos: [{ name: "demo" }],
      sessions: [{ id: "0123456789ab", repo: "demo", program: "codex", task, base: "abc", branch: "pp/0123456789ab", created_at: 1,
        finished: null, running: false, exit_code: 0, ended_at: 2, chat: null }] });
    bobSignsIn();
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()).getByText(task));      // chosen: the page chooses none by itself
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Try this session's task/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Try this session's task/ }));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Try with another agent" })).toBeTruthy(), { timeout: 15000 });
    expect(screen.getByDisplayValue(task)).toBeTruthy();
    await signOutFromThePalette();
    server.state.sessions = [];
    server.state.repos = [];
    await signInAsBob();
    expect(screen.queryByRole("dialog", { name: "Try with another agent" })).toBeNull();
    expect(screen.queryByDisplayValue(task)).toBeNull();
    expect(document.body.textContent).not.toContain("invoice totals");
  });
});

describe("the phone's list of sessions", () => {
  it("is a dialog: the keyboard goes into it and stays there, and Escape closes it and gives the keyboard back", async () => {
    browser({ phone: true });
    shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true },
      links: { links: [link()], questions: [], public_url: "https://pp.example", asks: [] }, history: [machineSession()] });
    render(<App />);
    await pass(2000);
    const opener = screen.getByRole("button", { name: "Sessions" });
    expect(opener.getAttribute("aria-haspopup")).toBe("dialog");
    opener.focus();
    fireEvent.click(opener);
    const drawer = screen.getByRole("dialog", { name: "Sessions" });
    expect(drawer.getAttribute("aria-modal")).toBe("true");
    expect(opener.getAttribute("aria-expanded")).toBe("true");
    expect(drawer.contains(document.activeElement)).toBe(true);
    // round from its last control to its first, and back
    const buttons = within(drawer).getAllByRole("button");
    buttons[buttons.length - 1].focus();
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(drawer.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
    expect(drawer.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Sessions" })).toBeNull();
    expect(document.activeElement).toBe(opener);
    // its own Close does the same
    fireEvent.click(opener);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Sessions" })).getByRole("button", { name: "Close the list" }));
    expect(screen.queryByRole("dialog", { name: "Sessions" })).toBeNull();
  });

  it("opens on the session chosen, so the keyboard starts where the person is", async () => {
    browser({ phone: true });
    const shell = { id: "0123456789ab", repo: "demo", program: "shell", task: "Look around", base: null, branch: null, created_at: 1,
      finished: null, running: false, exit_code: 0, ended_at: null, chat: null };
    shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true }, repos: [{ name: "demo" }],
      sessions: [shell] });
    render(<App />);
    await pass(2000);
    expect(useStore.getState().selected).toBeNull();                  // the page chooses none by itself
    act(() => { useStore.getState().select("0123456789ab"); });
    await pass();
    fireEvent.click(screen.getByRole("button", { name: "Sessions" }));
    const drawer = screen.getByRole("dialog", { name: "Sessions" });
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(document.activeElement?.getAttribute("aria-current")).toBe("true");
    expect(document.activeElement?.textContent).toContain("Look around");
  });
});

describe("the page's own notifications, while its tab is in the background", () => {
  afterEach(() => { delete (document as { visibilityState?: unknown }).visibilityState; });
  /** The tab put in the background, as the browser tells the page. */
  const hide = () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  };
  const RUNNING = { enabled: true, allowed: true, provider: "fly", running: true, made: true } as const;
  const TASK = "Rotate Globex's payroll key";
  const record = (activity: Record<string, unknown>) => ({ id: "0123456789ab", repo: "acme-payroll", program: "codex", task: TASK,
    base: "abc", branch: "pp/0123456789ab", created_at: 1, finished: null, running: true, exit_code: null, ended_at: null,
    chat: null, activity: { at: 1, ...activity } }) as unknown as import("./api").SessionRecord;

  it("say what kind of thing waits and where to look, in fixed words: never the session's title, its repository, the tool it wants or the agent's words", async () => {
    browser();
    const shown: { title: string; body?: string; tag?: string }[] = [];
    vi.stubGlobal("Notification", class {
      static permission = "granted";
      constructor(title: string, opts: NotificationOptions = {}) { shown.push({ title, body: opts.body, tag: opts.tag }); }
    });
    const server = shellServer({ ws: RUNNING, repos: [{ name: "acme-payroll" }], sessions: [record({ state: "working" })] });
    render(<App />);
    await pass(2000);
    hide();
    server.state.sessions = [record({ state: "waiting", at: 2, tool: "Bash", message: "curl -H 'Authorization: Bearer sk-live-1' https://x" })];
    await pass(16000);
    expect(shown).toEqual([{ title: "A session waits for you", body: "Open the ADE.", tag: "pp-0123456789ab" }]);
    // answered, it works again (nothing to tell), then its turn ends
    server.state.sessions = [record({ state: "working", at: 3 })];
    await pass(16000);
    server.state.sessions = [record({ state: "idle", at: 4 })];
    await pass(16000);
    expect(shown.slice(1)).toEqual([{ title: "A session finished its turn", body: "Open the ADE.", tag: "pp-0123456789ab" }]);
    // an agent asks for a session
    server.state.asks = [{ id: "fedcba987654", from: "0123456789ab", repo: "acme-payroll", label: "Codex", program: "codex",
      task: "Audit Initech's ledgers", base: null, at: 5, by: { id: "0123456789ab", title: TASK, program: "codex" } }];
    await pass(16000);
    expect(shown.slice(2)).toEqual([{ title: "An agent asks for a session", body: "Open Approvals.", tag: "pp-ask-fedcba987654" }]);
    const all = JSON.stringify(shown);
    for (const word of ["Globex", "acme-payroll", "Bash", "sk-live", "Codex", "Initech"]) expect(all).not.toContain(word);
  });

  it("are not shown where the browser refuses to make one (nearly every mobile browser), and the page goes on", async () => {
    browser();
    let refused = 0;
    vi.stubGlobal("Notification", class {
      static permission = "granted";
      constructor() { refused += 1; throw new TypeError("Failed to construct 'Notification': Illegal constructor."); }
    });
    const server = shellServer({ ws: RUNNING, repos: [{ name: "acme-payroll" }], sessions: [record({ state: "working" })] });
    render(<App />);
    await pass(2000);
    hide();
    server.state.sessions = [record({ state: "waiting", at: 2 })];
    await pass(16000);
    expect(refused).toBe(1);
    // still there, and still counting what waits
    expect(within(list()).getByText(TASK)).toBeTruthy();
    expect(document.title).toBe("(1) Proofpane Community");
    server.state.sessions = [record({ state: "idle", at: 3 })];
    await pass(16000);
    expect(document.title).toBe("Proofpane Community");
  });
});

describe("the licenses of what the app carries", () => {
  beforeAll(async () => { await import("./components/Licenses"); }, 60000);

  it("open from the palette and from the account menu", async () => {
    browser();
    shellServer();
    render(<App />);
    await pass(2000);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Licenses/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Licenses/ }));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Licenses" })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Licenses" })).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Licenses" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Your account, / }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Licenses" }));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Licenses" })).toBeTruthy(), { timeout: 15000 });
  });
});

describe("Connections asked for by a form", () => {
  beforeAll(async () => { await import("./components/Connections"); }, 60000);

  it("opens over the page on the store's flag, which the New task form's Open Connections sets, and closing it clears the flag", async () => {
    browser();
    shellServer();
    render(<App />);
    await pass(2000);
    expect(screen.queryByRole("dialog", { name: "Connections" })).toBeNull();
    act(() => useStore.getState().setConnectionsOpen(true));
    await vi.waitFor(() => expect(screen.getByRole("dialog", { name: "Connections" })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Connections" })).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Connections" })).toBeNull();
    expect(useStore.getState().connectionsOpen).toBe(false);
  });
});

describe("a closed session", () => {
  const RUNNING = { enabled: true, allowed: true, provider: "fly", running: true, made: true };
  const rec = (id: string, task: string, more: Record<string, unknown> = {}) => ({ id, repo: "demo", program: "claude", task,
    base: null, branch: `pp/${id}`, created_at: 1, finished: null, running: false, exit_code: 0, ended_at: 2, chat: null, ...more });

  it("is closed from the list: taken out of it, and the control view in its place, never another chosen by itself", async () => {
    browser();
    const server = shellServer({ ws: RUNNING, repos: [{ name: "demo" }],
      sessions: [rec("aaaaaaaaaaaa", "First"), rec("bbbbbbbbbbbb", "Second", { running: true })] });
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()).getByText("Second"));
    expect(useStore.getState().selected).toBe("bbbbbbbbbbbb");
    fireEvent.click(within(list()).getByRole("button", { name: "Close Second" }));
    await pass(2000);
    expect(server.calls).toContain("POST /api/ade/workspace/sessions/bbbbbbbbbbbb/close");
    expect(within(list()).queryByText("Second")).toBeNull();
    expect(within(list()).getByText("First")).toBeTruthy();
    // the page chooses none by itself: the main panel is the control view
    expect(useStore.getState().selected).toBeNull();
    expect(within(main()).getByRole("region", { name: "The control view" })).toBeTruthy();
  });

  it("is found by the palette, and opening it there puts it back in the list", async () => {
    browser();
    const server = shellServer({ ws: RUNNING, repos: [{ name: "demo" }],
      sessions: [rec("aaaaaaaaaaaa", "Still open"), rec("bbbbbbbbbbbb", "Closed earlier", { closed: true })] });
    render(<App />);
    await pass(2000);
    expect(within(list()).queryByText("Closed earlier")).toBeNull();
    expect(useStore.getState().selected).toBeNull();                  // none chosen by the page, the closed one least of all
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getAllByRole("option").some((o) => o.textContent?.includes("Closed earlier"))).toBe(true), { timeout: 15000 });
    fireEvent.click(screen.getAllByRole("option").find((o) => o.textContent?.includes("Closed earlier"))!);
    await pass(2000);
    expect(server.calls).toContain("POST /api/ade/workspace/sessions/bbbbbbbbbbbb/open");
    expect(within(list()).getByText("Closed earlier")).toBeTruthy();
    expect(useStore.getState().selected).toBe("bbbbbbbbbbbb");
  });

  it("leaves the keyboard in the list, whether closed from its row or from its header", async () => {
    browser();
    shellServer({ ws: RUNNING, repos: [{ name: "demo" }],
      sessions: [rec("aaaaaaaaaaaa", "First"), rec("bbbbbbbbbbbb", "Second"), rec("cccccccccccc", "Third")] });
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()).getByText("Third"));      // chosen: the page chooses none by itself
    const row = within(list()).getByRole("button", { name: "Close Second" });
    row.focus();
    fireEvent.click(row);
    await pass(2000);
    expect(document.activeElement).not.toBe(document.body);
    expect(list().contains(document.activeElement)).toBe(true);
    const header = within(main()).getByRole("button", { name: "Close the session" });     // Third's, the one chosen
    header.focus();
    fireEvent.click(header);
    await pass(2000);
    expect(document.activeElement).not.toBe(document.body);
    expect(list().contains(document.activeElement)).toBe(true);
  });
});
