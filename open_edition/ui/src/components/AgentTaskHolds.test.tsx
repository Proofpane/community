import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskSettings, TaskView } from "../api";
import type { Agent } from "../agents";
import { useStore } from "../store";
import { clock } from "../when";
import { AgentTask } from "./AgentTask";
import { forgetPage } from "./RiskTerms";

// One Cancel for each hold on a task's pane: the task's list of its holds and the hand-off part, both real, on one
// pane. A task's `task.offer` hold is cancelled by the hand-off part's standing offer (its Cancel withdraws the offer)
// while that part shows the offer, and by the list of holds otherwise; custody's `task.crossing` hold, once the task's
// view carries it, by the list alone.

const AGENT = "a1a2a3a4a5a6";
const TASK = "0123456789abcdef0123456789abcdef";
const OFFER = "e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0";
const HOLD = "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0";
const CROSS = "c".repeat(32);
const TM = "/api/ade/teammate";
const OFF = "This deployment does not name its operator, so offers and custody are off.";
const TICK = "Let custody take this task between your personal plans and a Team organisation's seats";
const now = () => Math.floor(Date.now() / 1000);

interface Call { method: string; url: string }

/** The app's side: its task and account routes, its risk texts, the hand-off part's two reads and its withdraw, and
 *  the holds' Cancel; every call kept. A route this page does not need is never answered. */
class Fake {
  calls: Call[] = [];
  tasks: TaskView[] = [];
  settings: TaskSettings = { relay: true, auto_resume: true, teammate: true, cross_groups: true,
                             local_provider: false };
  offers: [number, unknown] = [200, { made: [], received: [] }];
  custody: unknown = { as_owner: null, as_custodian: [], tasks: [] };

  install() {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) =>
      this.answer(String(url), init.method || "GET")));
    return this;
  }

  answer(url: string, method: string): Promise<Response> {
    this.calls.push({ method, url });
    const ok = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status }));
    const path = url.split("?")[0];
    if (path === "/api/ade/agents/tasks" && method === "GET") return ok({ tasks: this.tasks, settings: this.settings });
    if (path === "/api/ade/agents/accounts") return ok({ accounts: [] });
    if (path === "/api/ade/agents/account-records") return ok({ records: [] });
    if (path === "/api/ade/workspace/model-providers") return ok({ providers: [] });
    if (path === "/api/ade/risks") return ok({ texts: [] });
    if (path === `${TM}/offers` && method === "GET") return ok(this.offers[1], this.offers[0]);
    if (path === `${TM}/custody` && method === "GET") return ok(this.custody);
    if (path === `${TM}/offers/${OFFER}/withdraw` && method === "POST") {
      return ok({ offer: { id: OFFER, state: "withdrawn" } });
    }
    if (/^\/api\/ade\/holds\/[^/]+\/cancel$/.test(path)) return ok({ cancelled_at: now() });
    return new Promise<Response>(() => {});
  }

  posted(url: string) { return this.calls.some((c) => c.method === "POST" && c.url === url); }
}

const agent = (): Agent => ({
  id: AGENT, name: "Triage", group: null, brief: "Label them.", repo: "demo", base_ref: null, keep_worktree: true,
  account: null, chat_settings: {}, allow: [], brief_version: 1, paused: false, paused_reason: null, retired: false,
  from_session: null, created_at: 1, updated_at: 1, state: "idle",
  limits: { max_run_seconds: 3600, max_runs_per_day: 24, max_steps: 200, queue_max: 10, storage_mb: 200 },
  totals: { runs: 0, done: 0, failed: 0, blocked: 0, seconds: 0, tokens: 0, cost_est_usd: 0, last_run_at: null,
            last_state: null, runs_today: 0, est_usd_today: 0, success_rate: null },
  runs: [], mcp: { connectors: false, servers: [], accepted: {}, held: [] }, tools: {}, proposals: [],
  unattended_mode: "dontAsk", precheck: null, max_budget_usd: null, est_usd_per_day: null, triggers: [],
  autocompact_window: null, max_compactions: 2, may_dispatch: false, accepts_dispatch: false, max_dispatch_per_run: 5,
});

function task(over: Partial<TaskView> = {}): TaskView {
  return {
    id: TASK, agent_id: AGENT, title: "Ship the fix", goal: "Fix the bug and ship it.", state: "suspended", cause: null,
    reason: null, waits_until: null, state_version: 4, fence: 2, step: 1, backend: { kind: "account", id: "default" },
    home_grp: "personal", bound_org: null, between_steps: "checked", relay: true, auto_resume: true, cross_groups: true,
    custody_cross: false, custody_confirmed: false, crossings_ended: false, received: false, returned: false, continued_from: null,
    repo_address: "https://github.com/o/r", protected: [], external: [],
    steps: [{ n: 1, objective: "Write the fix", check: "pytest -q", state: "doing", evidence: null, checkpoint_id: null,
              attempts: 1, reopened: false }],
    decisions: [], attempts: [], checkpoints: [], effects: [], relay_set: [], moves: [], holds: [],
    local_provider: false, offer: null, ...over,
  };
}

/** The task offering, its offer in its hold, as the app's view gives it: the hold among the task's holds. */
const offering = (at: number): Partial<TaskView> => ({
  state: "offering", offer: { id: OFFER, state: "held", teammate: "t@example.org" },
  holds: [{ id: HOLD, kind: "task.offer", takes_effect_at: at }],
});
/** The hand-off part's view of that offer, as its read lists it. */
const heldOffer = (at: number) => ({
  id: OFFER, kind: "pressed", state: "held", task_id: TASK, title: "Ship the fix", goal: "Fix the bug and ship it.",
  steps: [], from_email: null, to_email: "t@example.org", repo_slug: "o/r", branch: `pp/task-${TASK}`, commit: null,
  packet_digest: null, loaded_changes: [], hold: { id: HOLD, kind: "task.offer", takes_effect_at: at }, why: null,
  created_at: at - 10, expires_at: at + 7 * 86400, terms: null, other_group_agents: [],
});

let fake: Fake;
beforeEach(() => {
  fake = new Fake().install();
  useStore.setState({ user: { id: "u1", email: "owner@example.com" } as never });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
  forgetPage();
});

const show = async (over: Partial<TaskView>) => {
  fake.tasks = [task(over)];
  render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
  return screen.findByRole("region", { name: "Task: Ship the fix" });
};
const cancels = (pane: HTMLElement) => within(pane).queryAllByRole("button", { name: "Cancel" });

describe("a task's offer hold, listed once", () => {
  it("has one Cancel while the hand-off part shows the offer: its own, which withdraws the offer", async () => {
    const at = now() + 150;
    fake.offers = [200, { made: [heldOffer(at)], received: [] }];
    const pane = await show(offering(at));
    const words = `Your offer to t@example.org goes ahead at ${clock(at)} unless you cancel it.`;
    expect(await within(pane).findByText(words)).toBeTruthy();
    await waitFor(() => expect(cancels(pane)).toHaveLength(1));
    expect(within(pane).queryByRole("list", { name: "Its holds" })).toBeNull();
    fireEvent.click(cancels(pane)[0]);
    await waitFor(() => expect(fake.posted(`${TM}/offers/${OFFER}/withdraw`)).toBe(true));
    expect(fake.calls.some((c) => c.url.startsWith("/api/ade/holds/"))).toBe(false);
  });

  it("has one Cancel while the hand-off part cannot show the offer, its read failing: the list's, for the hold", async () => {
    const at = now() + 150;
    fake.offers = [502, { detail: "The app did not answer." }];
    const pane = await show(offering(at));
    expect(await within(pane).findByRole("button", { name: "Try again" })).toBeTruthy();
    const list = within(pane).getByRole("list", { name: "Its holds" });
    expect(within(list).getByText(/Your offer of this task to a teammate goes out/).textContent).toContain(clock(at));
    expect(cancels(pane)).toHaveLength(1);
    fireEvent.click(cancels(pane)[0]);
    await waitFor(() => expect(fake.posted(`/api/ade/holds/${HOLD}/cancel`)).toBe(true));
  });

  it("has one Cancel with the teammate part off: the hand-off part's, beside why the part is off", async () => {
    const at = now() + 150;
    fake.offers = [200, { made: [heldOffer(at)], received: [], off: OFF }];
    fake.custody = { as_owner: null, as_custodian: [], tasks: [], off: OFF };
    const pane = await show(offering(at));
    expect(await within(pane).findByText(OFF)).toBeTruthy();
    await waitFor(() => expect(cancels(pane)).toHaveLength(1));
    expect(within(pane).queryByRole("list", { name: "Its holds" })).toBeNull();
    fireEvent.click(cancels(pane)[0]);
    await waitFor(() => expect(fake.posted(`${TM}/offers/${OFFER}/withdraw`)).toBe(true));
  });
});

describe("custody's crossing hold, listed once", () => {
  it("has one Cancel once the task's view carries it: the list's, the tick shown on its way in the hand-off part", async () => {
    const at = now() + 150;
    fake.custody = {
      as_owner: { id: "c0".repeat(16), state: "accepted", owner_email: null, custodian_email: "t@example.org",
                  confirmed_at: at, agents: [], why: null, created_at: at, updated_at: at, terms: null },
      as_custodian: [], tasks: [],
    };
    const pane = await show({ holds: [{ id: CROSS, kind: "task.crossing", takes_effect_at: at }] });
    const tick = await within(pane).findByRole("checkbox", { name: TICK }) as HTMLInputElement;
    expect([tick.checked, tick.disabled]).toEqual([true, true]);
    const list = within(pane).getByRole("list", { name: "Its holds" });
    expect(within(list).getByText(/Custody's crossing takes effect/).textContent).toContain(clock(at));
    expect(cancels(pane)).toHaveLength(1);
    expect(within(within(pane).getByRole("group", { name: "Custody's crossing" })).queryByRole("button")).toBeNull();
  });
});
