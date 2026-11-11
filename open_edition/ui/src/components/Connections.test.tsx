import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { Connections, mcpWords } from "./Connections";
import type { AccountsView, ProofpaneStatus, SessionRecord } from "../api";

beforeEach(() => {
  // nothing answers: each card stays at "Checking…", which is all this layout needs
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  useStore.setState(useStore.getInitialState());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

const OTHERS = ["Models in Claude Code", "Linear", "Proofpane", "Programs that ask for sessions", "Your own programs"];
const more = () => screen.getByRole("button", { name: /^More/ });

describe("Connections", () => {
  it("shows the sign-ins first, and the rest under More, closed until opened", () => {
    render(<Connections onClose={() => {}} />);
    for (const t of ["Claude Code", "Codex", "GitHub"]) expect(screen.getByText(t)).toBeTruthy();
    for (const t of OTHERS) expect(screen.queryByText(t)).toBeNull();
    expect(more().getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more());
    for (const t of OTHERS) expect(screen.getByText(t)).toBeTruthy();
    expect(more().getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps More open on this device once it is opened, and closed once it is closed again", () => {
    render(<Connections onClose={() => {}} />);
    fireEvent.click(more());
    cleanup();
    render(<Connections onClose={() => {}} />);
    expect(screen.getByText("Linear")).toBeTruthy();
    fireEvent.click(more());
    cleanup();
    render(<Connections onClose={() => {}} />);
    expect(screen.queryByText("Linear")).toBeNull();
  });
});

describe("Connections opened from elsewhere", () => {
  it("is a flag of the store, so that a form that needs an account can open it, and closes with the account's sign-out", () => {
    expect(useStore.getState().connectionsOpen).toBe(false);
    useStore.getState().setConnectionsOpen(true);
    expect(useStore.getState().connectionsOpen).toBe(true);
    useStore.getState().setConnectionsOpen(false);
    expect(useStore.getState().connectionsOpen).toBe(false);
    useStore.setState({ user: { id: "u1", email: "owner@example.test", can_sign_in_again: true } });
    useStore.getState().setConnectionsOpen(true);
    useStore.getState().signOut();
    expect(useStore.getState().connectionsOpen).toBe(false);
  });
});

describe("a Claude Code sign-in's code, sent from Connections", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("gives way to signed in as soon as the account's status read says so, its program still running, the looks joining the reads under way meanwhile", async () => {
    vi.useFakeTimers();
    const signing = { id: "111111111111", program: "claude-sign-in", repo: null, branch: null, base: null, task: "Sign in to Claude Code",
      created_at: 1, running: true, finished: null, exit_code: null, ended_at: null, chat: null, account: "bbbbbbbbbbbb", sign_in: true,
      sign_in_url: "https://claude.com/cai/oauth/authorize?code=true&state=x" } as SessionRecord;
    // the workspace's accounts, with each program's status as Claude Code's own says it (`status`) or without: Spare's
    // signed in once its code is taken
    let spareIn = false;
    const accounts = (status: boolean): AccountsView => ({
      claude: { active: "default", accounts: [
        { id: "default", name: "Default", created_at: null, sessions: 1, usage: null,
          status: status ? { installed: true, signed_in: true, email: "me@example.org", subscription: "max" } : null },
        { id: "bbbbbbbbbbbb", name: "Spare", created_at: 1, sessions: 0, usage: null,
          status: !status ? null : spareIn ? { installed: true, signed_in: true, email: "spare@example.org", subscription: "pro" }
            : { installed: true, signed_in: false } },
      ] },
      codex: { active: "default", accounts: [] },
    });
    // a workspace as the canary's was: GET /repos failing; GET /claude and the first status read of the accounts not
    // coming back, as a `claude auth status` that does not end; the sign-in's program running throughout
    const asked: string[] = [];
    let stuck = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const key = `${init.method || "GET"} ${url}`;
      asked.push(key);
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (key === "GET /api/ade/workspace") return json({ enabled: true, allowed: true, provider: "local", running: true, made: true });
      if (key === "GET /api/ade/workspace/repos") return json({ detail: "The workspace is not answering." }, 502);
      if (key === "GET /api/ade/workspace/sessions") return json({ sessions: [signing] });
      if (key === "GET /api/ade/workspace/accounts") {
        if (stuck) { stuck = false; return new Promise<Response>(() => {}); }
        return json(accounts(true));
      }
      if (key === "GET /api/ade/workspace/accounts?status=false") return json(accounts(false));
      if (key === `POST /api/ade/workspace/sessions/${signing.id}/sign-in-code`) { spareIn = true; return json(signing); }
      if (key === "GET /api/ade/agents/accounts") return json({ accounts: [] });
      return new Promise<Response>(() => {});
    }));
    useStore.setState({ user: { id: "u1", email: "owner@example.org", can_sign_in_again: true },
      ws: { enabled: true, allowed: true, provider: "local", running: true }, programs: { claude: true, codex: false },
      claude: { installed: true, signed_in: true, email: "me@example.org", subscription: "max" }, accounts: accounts(true),
      signIns: [signing] });
    render(<Connections onClose={() => {}} />);
    const spare = () => screen.getByText("Spare").closest("li") as HTMLElement;
    const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
    const reads = (key: string) => asked.filter((k) => k === key).length;
    expect(within(spare()).getByLabelText("The code Claude's page shows")).toBeTruthy();
    await pass(4000);
    await pass(4000);
    // two looks while Claude Code's sign-in and the accounts' status are being read: neither is asked again meanwhile
    expect(reads("GET /api/ade/workspace/claude")).toBe(1);
    expect(reads("GET /api/ade/workspace/accounts")).toBe(1);
    fireEvent.change(within(spare()).getByLabelText("The code Claude's page shows"), { target: { value: "abc#def" } });
    fireEvent.click(within(spare()).getByRole("button", { name: "Send" }));
    await pass(10);
    expect(within(spare()).getByText(/^Code sent; Claude Code is checking it/)).toBeTruthy();
    await pass(4000);
    // the read under way went out before the code: the look after it reads the accounts' status afresh, and the row
    // says Spare is signed in as soon as that read says so, the sign-in's program still running, with no reload
    expect(reads("GET /api/ade/workspace/accounts")).toBe(2);
    expect(within(spare()).getByText("Signed in as spare@example.org · pro")).toBeTruthy();
    expect(within(spare()).queryByText(/Code sent/)).toBeNull();
    expect(within(spare()).queryByLabelText("The code Claude's page shows")).toBeNull();
    expect(useStore.getState().signIns.map((r) => [r.id, r.running])).toEqual([[signing.id, true]]);
    await pass(4000);
    expect(reads("GET /api/ade/workspace/accounts")).toBe(3);                // each read answered: one a look
    expect(reads("GET /api/ade/workspace/claude")).toBe(2);                  // the one after the code, still under way
  });
});

describe("Connections' look", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("reads the workspace and its lists again only once the last look's read of them has answered", async () => {
    vi.useFakeTimers();
    // the first read of the sessions not answered until the test lets it go, as a workspace slow to answer; the rest at once
    const asked: string[] = [];
    let held = true;
    let answer = () => {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const key = `${init.method || "GET"} ${url}`;
      asked.push(key);
      const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
      if (key === "GET /api/ade/workspace") return json({ enabled: true, allowed: true, provider: "local", running: true, made: true });
      if (key === "GET /api/ade/workspace/repos") return json({ repos: [] });
      if (key === "GET /api/ade/workspace/sessions") {
        if (!held) return json({ sessions: [] });
        held = false;
        return new Promise<Response>((r) => { answer = () => r(json({ sessions: [] })); });
      }
      return new Promise<Response>(() => {});
    }));
    useStore.setState({ user: { id: "u1", email: "owner@example.org", can_sign_in_again: true },
      ws: { enabled: true, allowed: true, provider: "local", running: true }, programs: { claude: true, codex: false } });
    render(<Connections onClose={() => {}} />);
    const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
    const reads = (key: string) => asked.filter((k) => k === key).length;
    await pass(4000);                                   // the first look: its read of the sessions under way
    await pass(4000);                                   // a second look while it is
    expect(reads("GET /api/ade/workspace")).toBe(1);
    expect(reads("GET /api/ade/workspace/sessions")).toBe(1);
    await act(async () => { answer(); });
    await pass(4000);                                   // the next look, that read answered: read again
    expect(reads("GET /api/ade/workspace")).toBe(2);
    expect(reads("GET /api/ade/workspace/sessions")).toBe(2);
  });
});

describe("the Codex card", () => {
  it("is never shown while its accounts are read when the workspace already said Codex is not there", () => {
    useStore.setState({ programs: { claude: true, codex: false } });
    render(<Connections onClose={() => {}} />);
    expect(screen.queryByText("Codex")).toBeNull();
    expect(screen.getByText("Claude Code")).toBeTruthy();
  });
});


describe("the Proofpane card", () => {
  const unpaired: ProofpaneStatus = { paired: false, plugin: true, name: "proofpane-core",
    skills: ["ai-governance-intake", "ai-risk-assessment"], mcp: { given: false, why: "machine" }, accounts: [] };
  const paired: ProofpaneStatus = { ...unpaired, paired: true, cloud_url: "https://pp.example.org", device_id: "d1",
    device_name: "Proofpane Community workspace on box", paired_at: "2026-10-18T01:02:03Z", holds: ["^deploy"] };

  function serve(answers: ProofpaneStatus[]) {
    const calls: { method: string; url: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit = {}) => {
      const u = String(url);
      if (!u.includes("/api/ade/workspace/proofpane")) return new Promise(() => {});      // the other cards wait
      calls.push({ method: init.method || "GET", url: u, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const next = answers.length > 1 ? answers.shift()! : answers[0];
      return Promise.resolve(new Response(JSON.stringify(next), { status: 200 }));
    }));
    return calls;
  }

  function open() {
    useStore.setState({ ws: { running: true } as never });
    render(<Connections onClose={() => {}} />);
    fireEvent.click(more());
  }

  it("pairs with a code and the address given, and says what each start is then given", async () => {
    const calls = serve([unpaired, paired]);
    open();
    expect(await screen.findByText("Not paired")).toBeTruthy();
    expect(screen.getByText(/\/proofpane-core:ai-governance-intake, \/proofpane-core:ai-risk-assessment/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pair with Proofpane" }));
    fireEvent.change(screen.getByLabelText("The pairing code Proofpane shows"), { target: { value: " abc-def-ghj " } });
    fireEvent.change(screen.getByLabelText("Proofpane's address"), { target: { value: "https://pp.example.org" } });
    fireEvent.change(screen.getByLabelText("Commands to hold for your approval, one pattern a line"),
      { target: { value: "^deploy\n\n  \\bgit\\s+push  \n" } });
    fireEvent.click(screen.getByRole("button", { name: /^Pair$/ }));
    expect(await screen.findByText("Paired as Proofpane Community workspace on box")).toBeTruthy();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toMatch(/\/api\/ade\/workspace\/proofpane\/pair$/);
    expect(put.body).toEqual({ code: "abc-def-ghj", cloud_url: "https://pp.example.org", holds: ["^deploy", "\\bgit\\s+push"] });
    expect(screen.getByText(/^Paired with https:\/\/pp\.example\.org/)).toBeTruthy();
    expect(screen.getByText(mcpWords(paired))).toBeTruthy();
    expect(screen.getByText("Held for your approval: commands matching ^deploy.")).toBeTruthy();
    // what the hooks refuse, what they leave to Claude Code's own prompt, and the way back from a revocation
    expect(screen.getByText(/the Proofpane daemon's nine fixed patterns name as dangerous/)).toBeTruthy();
    expect(screen.getByText(/A command over 64 KB is not checked, and Claude Code asks you about it instead/)).toBeTruthy();
    expect(screen.getByText(/a revoked one cannot be restored there, nor an old token brought back, so after either, unpair here and pair again with a new code/)).toBeTruthy();
  });

  it("sends no address when none is typed, so the workspace takes Proofpane's own", async () => {
    const calls = serve([unpaired, paired]);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Pair with Proofpane" }));
    fireEvent.change(screen.getByLabelText("The pairing code Proofpane shows"), { target: { value: "ABC-DEF-GHJ" } });
    fireEvent.click(screen.getByRole("button", { name: /^Pair$/ }));
    await screen.findByText("Paired as Proofpane Community workspace on box");
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ code: "ABC-DEF-GHJ", cloud_url: null, holds: [] });
  });

  it("names an account whose organisation's managed settings leave the plugin or its hooks out", async () => {
    serve([{ ...paired, accounts: [{ account: "default", refused: ["organisation"], hooks_off: [] },
                                   { account: "aaaaaaaaaaaa", refused: [], hooks_off: ["file"] }] }]);
    useStore.setState({ accounts: { claude: { active: "default", accounts: [
      { id: "default", name: "Default", created_at: null, sessions: 0, status: null, usage: null },
      { id: "aaaaaaaaaaaa", name: "Work", created_at: 1, sessions: 0, status: null, usage: null }] },
      codex: { active: "default", accounts: [] } } });
    open();
    // where each was read is named, the account's cached organisation settings said to be a file a session can write
    expect(await screen.findByText(/^On Default, the organisation's settings as Claude Code keeps them in the account's folder \(a file a session can also write\) refuse plugins named for one session/)).toBeTruthy();
    expect(screen.getByText(/^On Work, this machine's managed settings keep plugins' hooks from running \(allowManagedHooksOnly or disableAllHooks\)/)).toBeTruthy();
  });

  it("unpairs and changes its holds, each from the card", async () => {
    const calls = serve([paired, { ...paired, holds: [] }, unpaired]);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Change holds" }));
    fireEvent.change(screen.getByLabelText("Commands to hold for your approval, one pattern a line"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^Keep$/ }));
    expect(await screen.findByText("No command is held for your approval.")).toBeTruthy();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ holds: [] });
    fireEvent.click(screen.getByRole("button", { name: /Unpair/ }));
    expect(await screen.findByText("Not paired")).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/proofpane/pair"))).toBe(true);
  });

  it("says why Proofpane's MCP server is or is not given", () => {
    expect(mcpWords({ ...paired, mcp: { given: true, why: null } })).toMatch(/is given too, with the plugin,/);
    expect(mcpWords({ ...paired, mcp: { given: true, why: null } })).toMatch(/policy for the agent named default, which refuses every tool until your organisation has one/);
    expect(mcpWords({ ...paired, mcp: { given: false, why: "machine" } })).toMatch(/a workspace machine has no Proofpane daemon/);
    expect(mcpWords({ ...paired, mcp: { given: false, why: "no daemon" } })).toMatch(/not found where its installer puts it \(\/opt\/proofpane, \/opt\/homebrew\/bin, \/usr\/local\/bin or ~\/\.local\/bin\)/);
    expect(mcpWords({ ...unpaired, mcp: { given: false, why: "not paired" } })).toMatch(/given once the workspace is paired/);
  });
});
