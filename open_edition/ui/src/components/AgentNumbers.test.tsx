import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, AgentWindow } from "../agents";
import { useStore } from "../store";
import { AgentNumbersPanel } from "./AgentNumbers";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const agent = { id: "a1a2a3a4a5a6", name: "Triage", totals: { runs: 3 } } as unknown as Agent;

const window = (over: Partial<AgentWindow> = {}): AgentWindow => ({
  days: 7, since: "2026-09-27", runs: { runs: 4, done: 3, failed: 1, blocked: 0, stopped: 0, skipped: 0 },
  success_rate: 0.75, cost_usd: 1.5, cost_per_success: 0.5,
  time: { wall_s: 600, active_s: 400, model_s: 200, tool_s: 120, waiting_owner_s: 30, queue_wait_s: 6, wall_p50_s: 125, wall_p95_s: 290 },
  calls: 14, errors: 2, refused: 1,
  tools: [{ name: "Read", calls: 9, errors: 0, refused: 1, p50_ms: 40, p95_ms: 2400 }, { name: "Bash", calls: 5, errors: 2, refused: 0, p50_ms: 15000, p95_ms: null }],
  together: { dispatches_sent: 2, dispatches_received: 0, messages_sent: 1, messages_received: 0, questions: 1, answered: 1,
              permission_asks: 3, granted: 2, denied: 1, owner_turns: 4, collaborators: 1 },
  cache: { ratio: 0.72, requests: 30, cold_requests: 2 },
  retrieval: { calls: 11, surfaced: 8, cited: 2, read_then_cited: 0.25 }, ...over,
});

describe("an agent's numbers", () => {
  it("shows where its time went, its tools, its work with others, its cache and its retrieval, for 7 or 30 days", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      asked.push(String(url));
      return new Response(JSON.stringify(window(String(url).endsWith("days=30") ? { days: 30, runs: { runs: 9, done: 9, failed: 0, blocked: 0, stopped: 0, skipped: 0 } } : {})), { status: 200 });
    }));
    render(<AgentNumbersPanel agent={agent} />);
    const group = screen.getByRole("group", { name: "Triage's numbers" });
    await within(group).findByText("4: 3 done, 1 failed");
    expect(within(group).getByText("75%, $0.50 a success")).toBeTruthy();
    expect(within(group).getByText("2m 5s / 4m 50s")).toBeTruthy();
    const tools = within(group).getByRole("table", { name: "Its tool calls" });
    const rows = within(tools).getAllByRole("row").map((r) => r.textContent);
    expect(rows).toEqual(["Toolcallserrorsrefusedp50p95", "Read90140ms2.4s", "Bash52015s"]);
    expect(within(group).getByText("3: 2 yes, 1 no")).toBeTruthy();
    expect(within(group).getByText("72%")).toBeTruthy();
    expect(within(group).getByLabelText("Its retrieval").textContent).toContain("11 calls surfaced 8 sources; its last words named 2 of them (25%)");
    fireEvent.click(within(group).getByRole("tab", { name: "30 days" }));
    await within(group).findByText("9: 9 done, 0 failed");
    expect(asked).toEqual(["/api/ade/agents/a1a2a3a4a5a6/metrics?days=7", "/api/ade/agents/a1a2a3a4a5a6/metrics?days=30"]);
  });

  it("shows its quality: the judge and its trust, its checks, rules met, how often it needed you, its servers, its replays", async () => {
    const quality = {
      judged: 6, judge_pass_rate: 0.8333, judge_version: "a".repeat(16), judge_band: "tentative" as const,
      drift: { level: null, current: null, baseline: null, drop: null },
      acceptance: { checked: 4, passed: 3, pass_rate: 0.75 },
      anomalies: { warning: 2, critical: 1, by_rule: { tool_repetition: 2, spend: 1 } },
      claims_flagged: 1, premise_flagged: 0,
      autonomy: { unattended_share: 0.5, interventions_per_run: 1.25 },
      mcp_health: { needs_auth: 2, failed: 0, disabled: 0, tool_changes: 1 },
      dispatch: { coordinating_runs: 1, most_fan_out: 3, deepest: 2, waited_s: 75 },
      replays: { replays: 2, compared: 2, agreement: 0.5 },
      cost_of_improvement: { usd: 0.21, share: 0.14 },
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).endsWith("/judge-trust")
      ? { ["a".repeat(16)]: { labelled: 20, agreement: 0.95, ac1: 0.9, alpha: 0.9023, ac1_interval: [0.7, 1], alpha_interval: [0.688, 1], band: "tentative" } }
      : window({ quality })), { status: 200 })));
    render(<AgentNumbersPanel agent={{ ...agent, judge_runs: true } as Agent} />);
    const q = await screen.findByLabelText("Its quality");
    expect(within(q).getByText("6, 83% passed")).toBeTruthy();
    const band = within(q).getByText("tentative: shown, drives nothing");
    await vi.waitFor(() => expect(band.closest("div")!.getAttribute("title")).toContain("alpha 0.90 (95%: 0.69 to 1.00)"));
    expect(within(q).getByText("3 of 4 passed")).toBeTruthy();
    expect(within(q).getByText("2 warning, 1 critical").closest("div")!.getAttribute("title")).toBe(
      "the same call again and again: 2\nspending well past its usual run: 1");
    expect(within(q).getByText("50%")).toBeTruthy();
    expect(within(q).getByText("1.25")).toBeTruthy();
    expect(within(q).getByText("2 needing a sign-in, 1 tool changes")).toBeTruthy();
    expect(within(q).getByText("1: up to 3 started, 2 deep, 1m 15s waiting")).toBeTruthy();
    expect(within(q).getByText("2, 50% agree")).toBeTruthy();
    expect(within(q).getByText("$0.21 (14%)")).toBeTruthy();
    expect(within(q).queryByText(/^Drift/)).toBeNull();
  });

  it("says the judge is off for an agent that has it off", async () => {
    const quality = {
      judged: 0, judge_pass_rate: null, judge_version: null, judge_band: null,
      drift: { level: null, current: null, baseline: null, drop: null }, acceptance: { checked: 0, passed: 0, pass_rate: null },
      anomalies: { warning: 0, critical: 0, by_rule: {} }, claims_flagged: 0, premise_flagged: 0,
      autonomy: { unattended_share: 1, interventions_per_run: 0 }, mcp_health: { needs_auth: 0, failed: 0, disabled: 0, tool_changes: 0 },
      dispatch: { coordinating_runs: 0, most_fan_out: 0, deepest: 0, waited_s: 0 }, replays: { replays: 0, compared: 0, agreement: null },
      cost_of_improvement: { usd: 0, share: 0 },
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(window({ quality })), { status: 200 })));
    render(<AgentNumbersPanel agent={agent} />);
    const q = await screen.findByLabelText("Its quality");
    expect(within(q).getByText("off")).toBeTruthy();
    expect(within(q).queryByText("The judge")).toBeNull();
    expect(within(q).getByText("all well")).toBeTruthy();
    expect(within(q).getByText("none set")).toBeTruthy();
  });

  it("says so when no run ended in the window", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(window({ runs: { runs: 0, done: 0, failed: 0, blocked: 0, stopped: 0, skipped: 0 } })), { status: 200 })));
    render(<AgentNumbersPanel agent={agent} />);
    expect(await screen.findByText("No run ended in the last 7 days.")).toBeTruthy();
  });
});
