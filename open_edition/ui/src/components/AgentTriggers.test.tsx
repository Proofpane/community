import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Trigger } from "../agents";
import { useStore } from "../store";
import { adherence, AgentTriggers } from "./AgentTriggers";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

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

const hook: Trigger = { id: "h1h2h3h4h5h6", agent_id: "a1a2a3a4a5a6", kind: "hook", enabled: true, prompt: null, last_at: null,
                        last_run_id: null, last_error: null, missed: 0, hook_type: "github", refused: 2,
                        url: "https://oss.example/api/ade/hooks/h1h2h3h4h5h6" };

const serve = (made: Trigger, calls: { method: string; url: string; body?: Record<string, unknown> }[] = []) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(made), { status: 200 });
  }));
  return calls;
};

describe("an agent's triggers", () => {
  it("count a schedule's firings on time, late, missed and skipped, the ones that happened", () => {
    const schedule: Trigger = { ...hook, kind: "schedule", cron: "0 9 * * *", tz: "UTC", missed: 1, on_time: 12, late: 2, skipped: 0 };
    expect(adherence(schedule)).toBe("12 on time, 2 late, 1 missed");
    expect(adherence({ ...schedule, on_time: 0, late: 0, missed: 0, skipped: 3 })).toBe("3 skipped");
    expect(adherence({ ...schedule, on_time: undefined, late: undefined, missed: 0, skipped: undefined })).toBe("");
    render(<AgentTriggers agent={agent({ triggers: [schedule] })} onChanged={async () => {}} />);
    expect(screen.getByText("12 on time, 2 late, 1 missed")).toBeTruthy();
  });

  it("adds a schedule in the browser's timezone, with a prompt for its runs", async () => {
    const calls = serve({ ...hook, kind: "schedule", cron: "0 9 * * 1-5", tz: "Pacific/Auckland" });
    const changed = vi.fn(async () => {});
    render(<AgentTriggers agent={agent()} onChanged={changed} />);
    const group = screen.getByRole("group", { name: "Triage's triggers" });
    expect(within(group).getByText(/it runs when you tap Run/)).toBeTruthy();
    fireEvent.click(within(group).getByRole("button", { name: "Schedule" }));
    fireEvent.change(within(group).getByRole("textbox", { name: /a cron line/ }), { target: { value: "*/30 * * * *" } });
    fireEvent.change(within(group).getByRole("textbox", { name: /In the timezone/ }), { target: { value: "Pacific/Auckland" } });
    fireEvent.change(within(group).getByRole("textbox", { name: /A prompt for its runs/ }), { target: { value: "the new issues" } });
    fireEvent.click(within(group).getByRole("button", { name: /Add the schedule/ }));
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    // the form's risk point 7 reads the terms first (GET /api/ade/risks): the call made is the one posted
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ method: "POST", url: "/api/ade/agents/a1a2a3a4a5a6/triggers",
                                     body: { kind: "schedule", cron: "*/30 * * * *", tz: "Pacific/Auckland", prompt: "the new issues" } });
    expect(within(group).queryByRole("note", { name: "The hook's secret" })).toBeNull();
  });

  it("shows a new hook's secret once, and what each hook did", async () => {
    serve({ ...hook, secret: "s3cr3t-once" });
    render(<AgentTriggers agent={agent({ triggers: [{ ...hook, last_error: "the agent is paused" }] })} onChanged={async () => {}} />);
    const group = screen.getByRole("group", { name: "Triage's triggers" });
    expect(within(group).getByText("2 refused (wrong signature)")).toBeTruthy();
    expect(within(group).getByText("the agent is paused")).toBeTruthy();
    fireEvent.click(within(group).getByRole("button", { name: "Hook" }));
    fireEvent.click(within(group).getByRole("button", { name: /Add the hook/ }));
    const note = await within(group).findByRole("note", { name: "The hook's secret" });
    expect(within(note).getByText("s3cr3t-once")).toBeTruthy();
    fireEvent.click(within(note).getByRole("button", { name: "Done" }));
    expect(within(group).queryByText("s3cr3t-once")).toBeNull();
  });

  it("turns one off, and removes one", async () => {
    const calls = serve(hook);
    render(<AgentTriggers agent={agent({ triggers: [hook] })} onChanged={async () => {}} />);
    fireEvent.click(screen.getByRole("checkbox"));
    await vi.waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]).toMatchObject({ method: "PUT", url: "/api/ade/agents/a1a2a3a4a5a6/triggers/h1h2h3h4h5h6", body: { enabled: false } });
    const remove = screen.getByRole("button", { name: "Remove the hook" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(remove.disabled).toBe(false));          // done with the first
    fireEvent.click(remove);
    await vi.waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1]).toMatchObject({ method: "DELETE", url: "/api/ade/agents/a1a2a3a4a5a6/triggers/h1h2h3h4h5h6" });
  });
});
