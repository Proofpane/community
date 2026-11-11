import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fromRegistry, serverName, type Agent, type RegistryRow } from "../agents";
import { useStore } from "../store";
import { AgentTools } from "./AgentTools";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const ROW: RegistryRow = {
  name: "io.github.acme/notes-mcp", description: "Notes, read and written.", version: "1.4.0", repository: "https://github.com/acme/notes-mcp",
  ways: [
    { kind: "package", registry: "npm", identifier: "@acme/notes-mcp", version: "1.4.0",
      env: [{ name: "NOTES_TOKEN", secret: true, required: true, description: "Its token." }, { name: "NOTES_DIR", secret: false, required: false, description: "" }] },
    { kind: "address", transport: "http", url: "https://notes.example.com/mcp", headers: [{ name: "Authorization", secret: true, required: true, description: "" }] },
    { kind: "other", registry: "pypi", identifier: "notes-mcp", transport: "stdio" },
  ],
};

const agent = (over: Partial<Agent> = {}): Agent => ({
  id: "a1a2a3a4a5a6", name: "Triage", group: null, brief: "Label them.", repo: "demo", base_ref: null, keep_worktree: true,
  account: null, chat_settings: {}, allow: [], brief_version: 1, paused: false, paused_reason: null, retired: false,
  from_session: null, created_at: 1, updated_at: 1, state: "idle",
  limits: { max_run_seconds: 3600, max_runs_per_day: 24, max_steps: 200, queue_max: 10, storage_mb: 200 },
  totals: { runs: 0, done: 0, failed: 0, blocked: 0, seconds: 0, tokens: 0, cost_est_usd: 0, last_run_at: null, last_state: null,
            runs_today: 0, est_usd_today: 0, success_rate: null },
  runs: [], mcp: { connectors: false, servers: [], accepted: {}, held: [] }, tools: {}, proposals: [],
  unattended_mode: "dontAsk", precheck: null, max_budget_usd: null, est_usd_per_day: null, triggers: [], autocompact_window: null, max_compactions: 2, may_dispatch: false, accepts_dispatch: false, max_dispatch_per_run: 5, ...over,
});

const serve = (calls: { method: string; url: string; body?: Record<string, unknown> }[] = []) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (String(url).includes("/mcp-search")) return new Response(JSON.stringify({ servers: [ROW], next: null }), { status: 200 });
    return new Response(JSON.stringify(agent()), { status: 200 });
  }));
  return calls;
};

describe("a server from the registry", () => {
  it("is a pinned package or an address, its secret names named and none of its values", () => {
    expect(fromRegistry(ROW.ways[0], "notes")).toEqual({ name: "notes", kind: "package", registry: "npm", identifier: "@acme/notes-mcp",
      version: "1.4.0", args: [], env: {}, secret_env: ["NOTES_TOKEN"] });
    expect(fromRegistry(ROW.ways[1], "notes")).toEqual({ name: "notes", kind: "address", transport: "http", url: "https://notes.example.com/mcp",
      headers: {}, secret_headers: ["Authorization"] });
    expect(fromRegistry(ROW.ways[2], "notes")).toBeNull();                       // another registry's: shown, not offered
    expect([serverName(ROW.name), serverName(ROW.name, ["notes-mcp"]), serverName("x/proofpane")]).toEqual(["notes-mcp", "notes-mcp-2", "proofpane-2"]);
  });
});

describe("an agent's tools", () => {
  it("shows each server's state as its last run found it, and one held until the owner accepts it", async () => {
    const calls = serve();
    const changed = vi.fn(async () => {});
    render(<AgentTools onChanged={changed} agent={agent({
      mcp: { connectors: false, accepted: {}, held: ["fs"], servers: [
        { name: "notes", kind: "address", transport: "http", url: "https://notes.example.com/mcp", headers: {}, secret_headers: [] },
        { name: "fs", kind: "package", registry: "npm", identifier: "@acme/fs", version: "1.0.0", args: [], env: {}, secret_env: [] }] },
      tools: { status: [{ name: "notes", scope: "dynamic", status: "connected", error: null, tools: 3 }] } })} />);
    const tools = screen.getByRole("group", { name: "Triage's tools" });
    expect(within(tools).getByText("connected, 3 tools")).toBeTruthy();
    expect(within(tools).getByText(/its tools changed since you accepted them/)).toBeTruthy();
    fireEvent.click(within(tools).getByRole("button", { name: "Accept" }));
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/ade/agents/a1a2a3a4a5a6/tools/fs/accept"))).toBe(true);
  });

  it("adds a server a run proposed, filled in from its entry, and settles the proposal", async () => {
    const calls = serve();
    const proposal = { id: "p1p2p3p4p5p6", agent_id: "a1a2a3a4a5a6", run_id: "r1r2r3r4r5r6", name: ROW.name, why: "to read the notes", entry: ROW, created_at: 1 };
    render(<AgentTools onChanged={async () => {}} agent={agent({ proposals: [proposal] })} />);
    const note = screen.getByRole("note", { name: `Proposed: ${ROW.name}` });
    expect(note.textContent).toContain("to read the notes");
    fireEvent.click(within(note).getByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("dialog", { name: "Triage's tools" });
    expect(within(dialog).getByText("npm @acme/notes-mcp@1.4.0")).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText("notes-mcp NOTES_TOKEN"), { target: { value: "t0ken" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Save/ }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/proposals/p1p2p3p4p5p6"))).toBe(true));
    const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/api/ade/agents/a1a2a3a4a5a6"))!;
    expect(put.body).toEqual({ connectors: false, mcp_servers: [{ name: "notes-mcp", kind: "package", registry: "npm", identifier: "@acme/notes-mcp",
                                                                  version: "1.4.0", args: [], env: {}, secret_env: ["NOTES_TOKEN"] }] });
    expect(JSON.stringify(put.body)).not.toContain("t0ken");                         // the secret goes apart, to the workspace
    const secrets = calls.find((c) => c.url.endsWith("/mcp-secrets"))!;
    expect(secrets.body).toEqual({ secrets: { "notes-mcp": { NOTES_TOKEN: "t0ken" } } });
    expect(calls.find((c) => c.url.endsWith("/proposals/p1p2p3p4p5p6"))!.body).toEqual({ state: "added" });
  });

  it("finds a server in the registry and adds one way of running it, never another registry's", async () => {
    serve();
    render(<AgentTools onChanged={async () => {}} agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Triage's tools" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Find in the registry" }));
    fireEvent.change(within(dialog).getByLabelText("Search the MCP registry"), { target: { value: "notes" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /Search/ }));
    expect(await within(dialog).findByText("Notes, read and written.")).toBeTruthy();
    expect(within(dialog).getByText("pypi notes-mcp: not offered")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add http https://notes.example.com/mcp" }));
    expect(within(dialog).getByText("http https://notes.example.com/mcp")).toBeTruthy();
    expect(within(dialog).getByLabelText("notes-mcp Authorization")).toBeTruthy();
  });

  it("refuses a package by hand without its exact version", async () => {
    serve();
    render(<AgentTools onChanged={async () => {}} agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Triage's tools" });
    fireEvent.click(within(dialog).getByRole("button", { name: "An npm package" }));
    fireEvent.change(within(dialog).getByPlaceholderText("notes"), { target: { value: "fs" } });
    fireEvent.change(within(dialog).getByPlaceholderText("@scope/name@1.2.3"), { target: { value: "@acme/fs@^1.0.0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add it" }));
    expect(within(dialog).getByText(/its exact version/)).toBeTruthy();
    fireEvent.change(within(dialog).getByPlaceholderText("@scope/name@1.2.3"), { target: { value: "@acme/fs@1.0.0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add it" }));
    expect(within(dialog).getByText("npm @acme/fs@1.0.0")).toBeTruthy();
  });
});
