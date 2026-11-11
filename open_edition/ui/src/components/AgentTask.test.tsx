import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AccountOwnerView, AccountRecordEntry, AccountRecordView, AccountSnapshot, AccountsView, IdentityReport, ModelProviderView,
  RiskTextView, TaskEvent, TaskSettings, TaskView, WindowView,
} from "../api";
import type { Agent, Trigger } from "../agents";
import { useStore } from "../store";
import { clock, dayTime } from "../when";
import { windowWords } from "../agents";
import { AccountDialog } from "./AccountDialog";
import { AgentTask, keepingWords } from "./AgentTask";
import { AgentTriggers } from "./AgentTriggers";
import { exhaustedWords } from "./Agents";
import { Connections } from "./Connections";
import { forgetPage } from "./RiskTerms";

// a machine busy with other runs takes more than the default second to render and answer: what is awaited gets 5
// seconds, and the slowest tests 30 (LONG, below)
configure({ asyncUtilTimeout: 5000 });

// the Custody section stands in for itself: the account dialog renders it by name
vi.mock("./TaskCustody", async () => {
  const { createElement } = await import("react");
  return { TaskCustody: () => createElement("section", { "aria-label": "Custody, its stand-in" }) };
});
// and so do the hand-off and the offers: the task's pane renders the hand-off by name with its task, and what those
// parts read is theirs, outside the calls these tests count as the page's
vi.mock("./TaskHandOff", async () => {
  const { createElement } = await import("react");
  return {
    TaskHandOff: ({ task }: { task: { id: string } }) => createElement("span", { "aria-label": `The hand-off of ${task.id}` }),
    OffersWaiting: () => null,
  };
});

// The task's page against the app's side: a fake of the app's task routes and its risk route, answering as the app
// does and recording every call.

const AGENT = "a1a2a3a4a5a6";
const TASK = "0123456789abcdef0123456789abcdef";
const DAY = 86400;
const now = () => Math.floor(Date.now() / 1000);

interface Call { method: string; url: string; body?: Record<string, unknown> }

/** Each risk point's text as the app's risk route gives it: titled by its number, with the cases it names. */
function riskTexts(): RiskTextView[] {
  const texts: RiskTextView[] = Array.from({ length: 15 }, (_, i) => ({
    point: i + 1, title: `Point ${i + 1}`, design: [{ case: null, text: `Point ${i + 1}'s own words.` }], quotations: [],
    unread: false, digest: (i + 1).toString(16).padStart(64, "0"),
  }));
  texts[0].design.push({ case: "crossing", text: "It crossed by itself." }, { case: "back_home", text: "This task started on {home_group} and goes back there." },
                       { case: "onto_seat", text: "Point 1's seat-settings text." });
  texts[0].quotations.push({ id: "responsibility", case: null, source: "Commercial Terms of Service", section: "D.5",
    address: "https://www.anthropic.com/legal/commercial-terms", source_date: "Effective June 17, 2025", read_on: "2026-10-10",
    words: "Customer is responsible for all activity under its account.", unread: null });
  texts[3].design.push({ case: "onto_seat", text: "Point 4's seat-settings text." });
  texts[4].design.push({ case: "onto_seat", text: "Point 5's seat-settings text." });
  texts[10].design.push({ case: "anthropic_key", text: "Anthropic's key: the cap counts Claude Code's estimate." },
                        { case: "other_vendor", text: "Another vendor's key: its terms were not read." });
  texts[11].design.push({ case: "custody_cross", text: "Custody's crossing: point 1's clauses." });
  return texts;
}

/** The app's side: the task routes, the risk route, a confirmation's two calls, the kept keys and Cancel. */
class Fake {
  calls: Call[] = [];
  tasks: TaskView[] = [];
  settings: TaskSettings = { relay: true, auto_resume: true, teammate: false, cross_groups: true, local_provider: false };
  accounts: AccountOwnerView[] = [];
  records: AccountRecordView[] = [];
  journal: TaskEvent[] = [];
  texts = riskTexts();
  providers: ModelProviderView[] = [];
  // the risk route not answering, as a slow or broken one would not
  hangRisks = false;
  // what a press changes, as the app would: the test says
  onAction: (action: string, body: Record<string, unknown>) => void = () => {};
  // a press's or a change's answer, as the app gives it: Continue on... while an attempt works queues nothing yet, and
  // a stop the workspace could not take is said in `not_stopped`
  queued = true;
  notStopped: string | null = null;
  // what a task made now carries beside its title, as the app would give it: a grant's names, say
  madeWith: Partial<TaskView> = {};

  install() {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => this.answer(String(url), init)));
    return this;
  }

  answer(url: string, init: RequestInit): Promise<Response> {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined;
    this.calls.push({ method, url, body });
    const ok = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status }));
    const path = url.split("?")[0];
    const t = this.tasks.find((x) => path.startsWith(`/api/ade/agents/tasks/${x.id}`));
    if (path === "/api/ade/agents/tasks" && method === "GET") return ok({ tasks: this.tasks, settings: this.settings });
    if (path === "/api/ade/agents/tasks" && method === "POST") {
      const made = task({ id: "fedcba9876543210fedcba9876543210", title: String(body?.title), state: "ready", ...this.madeWith });
      this.tasks.push(made);
      return ok({ task: made });
    }
    // newest first, as the app's journal_of gives it, whatever order a test writes it in
    if (t && path.endsWith("/journal")) return ok({ events: [...this.journal].sort((a, b) => b.seq - a.seq) });
    if (path.endsWith("/account-record")) {
      const id = path.split("/")[5];
      if (method === "DELETE") { this.records = this.records.filter((r) => r.task_id !== id); return ok({ deleted: 2 }); }
      const r = this.records.find((x) => x.task_id === id);
      return r ? ok({ record: r }) : ok({ detail: "no record" }, 404);
    }
    if (t && method === "POST") {
      const action = path.slice(`/api/ade/agents/tasks/${t.id}/`.length);
      this.onAction(action, body ?? {});
      return ok({ task: t, queued: this.queued, hold: null, ...(this.notStopped ? { not_stopped: this.notStopped } : {}) });
    }
    if (t && method === "PUT") return ok({ task: t, ...(this.notStopped ? { not_stopped: this.notStopped } : {}) });
    if (t && method === "DELETE") { this.tasks = this.tasks.filter((x) => x !== t); return ok({ deleted: true }); }
    if (path === "/api/ade/agents/accounts") return ok({ accounts: this.accounts });
    if (path === "/api/ade/agents/account-records") return ok({ records: this.records });
    if (path === "/api/ade/risks") return this.hangRisks ? new Promise<Response>(() => {}) : ok({ texts: this.texts });
    if (path === "/api/account/confirm" && method === "GET") return ok({ needed: true, password: true, passkeys: 0 });
    if (path === "/api/account/confirm" && method === "POST") return ok({ confirmation: "the-confirmation" });
    if (path === "/api/ade/workspace/model-providers") return ok({ providers: this.providers });
    if (/^\/api\/ade\/holds\/[^/]+\/cancel$/.test(path)) return ok({ cancelled_at: now() });
    return new Promise<Response>(() => {});          // a route this page does not need: never answered
  }

  /** The presses of a task's controls, each as [action, body]. */
  presses() {
    return this.calls.filter((c) => c.method === "POST" && c.url.startsWith(`/api/ade/agents/tasks/${TASK}/`))
      .map((c) => [c.url.slice(`/api/ade/agents/tasks/${TASK}/`.length), c.body ?? {}] as const);
  }
}

const agent = (over: Partial<Agent> = {}): Agent => ({
  id: AGENT, name: "Triage", group: null, brief: "Label them.", repo: "demo", base_ref: null, keep_worktree: true,
  account: null, chat_settings: {}, allow: [], brief_version: 1, paused: false, paused_reason: null, retired: false,
  from_session: null, created_at: 1, updated_at: 1, state: "idle",
  limits: { max_run_seconds: 3600, max_runs_per_day: 24, max_steps: 200, queue_max: 10, storage_mb: 200 },
  totals: { runs: 0, done: 0, failed: 0, blocked: 0, seconds: 0, tokens: 0, cost_est_usd: 0, last_run_at: null, last_state: null,
            runs_today: 0, est_usd_today: 0, success_rate: null },
  runs: [], mcp: { connectors: false, servers: [], accepted: {}, held: [] }, tools: {}, proposals: [],
  unattended_mode: "dontAsk", precheck: null, max_budget_usd: null, est_usd_per_day: null, triggers: [], autocompact_window: null,
  max_compactions: 2, may_dispatch: false, accepts_dispatch: false, max_dispatch_per_run: 5, ...over,
});

function task(over: Partial<TaskView> = {}): TaskView {
  return {
    id: TASK, agent_id: AGENT, title: "Ship the fix", goal: "Fix the bug and ship it.", state: "suspended", cause: null, reason: null,
    waits_until: null, state_version: 4, fence: 2, step: 1, backend: { kind: "account", id: "default" }, home_grp: "personal",
    bound_org: null, between_steps: "checked", relay: true, auto_resume: true, cross_groups: true, custody_cross: false,
    custody_confirmed: false, crossings_ended: false, received: false, returned: false, continued_from: null, repo_address: "https://github.com/o/r",
    protected: [], external: [],
    steps: [{ n: 1, objective: "Write the fix", check: "pytest -q", state: "doing", evidence: null, checkpoint_id: null, attempts: 1, reopened: false }],
    decisions: [], attempts: [], checkpoints: [], effects: [], relay_set: [], moves: [], holds: [], local_provider: false, offer: null,
    ...over,
  };
}

const room = (w: Partial<WindowView> = {}): WindowView => ({ window: "five_hour", status: "allowed", resets_at: now() + 3600, utilization: 0.4, at: now() - 60, ...w });

/** One of the owner's accounts as the app answers it: confirmed unless said, in its plan's group. */
function owner(account: string, name: string, plan: string, over: Partial<AccountOwnerView> = {}): AccountOwnerView {
  const seat = plan === "Claude Team" || plan === "Claude Enterprise";
  return {
    id: account === "default" ? "d".repeat(32) : account.repeat(3).slice(0, 32).padEnd(32, "0"), account, name,
    report: { email: `${name.toLowerCase()}@example.com`, organization: seat ? (plan === "Claude Team" ? "Acme" : "Big Co") : null, plan,
              token_source: "claude.ai", api_key_source: null, api_provider: "firstParty" },
    org_id: seat ? "org-1" : null, mismatch: null, state: "confirmed",
    grp: seat ? (plan === "Claude Team" ? "org:acme" : "org:bigco") : "personal",
    grp_kind: seat ? (plan === "Claude Team" ? "team" : "enterprise") : "personal",
    position: 0, in_relays: true, reserve_lifted: false, ready: true, windows: [room()], hold: null, ...over,
  };
}

// B is the task's own account; A another of the personal plans; S a Team seat of Acme; E an Enterprise seat; H held
// back by its weekly reserve
const B = owner("default", "Home", "Claude Pro");
const A = owner("a1a1a1a1a1a1", "Second", "Claude Max");
const S = owner("c1c1c1c1c1c1", "Work", "Claude Team");
const E = owner("e1e1e1e1e1e1", "Corp", "Claude Enterprise");
const H = owner("f1f1f1f1f1f1", "Spare", "Claude Max", { windows: [room({ window: "seven_day", status: "allowed_warning", utilization: 0.93 })] });

/** The workspace's Claude Code accounts as the store holds them once read (AccountsView): `active`, the account new
 *  sessions start on, on which a task of an agent with no account begins. */
const startsOn = (active: string, ids: string[] = ["default", active]) => useStore.setState({ accounts: {
  claude: { active, accounts: [...new Set(ids)].map((id) => ({ id, name: id === "default" ? "Default" : `acct ${id.slice(0, 2)}`, created_at: null,
                                                                sessions: 0, status: null, usage: null })) },
  codex: { active: "default", accounts: [] } } });

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
  forgetPage();                                         // a new page: the risk texts read again
});

const show = async (over: Partial<TaskView> = {}, accounts: AccountOwnerView[] = [B, A, S, E, H], agentOver: Partial<Agent> = {}) => {
  fake.tasks = [task(over)];
  fake.accounts = accounts;
  render(<AgentTask agent={agent(agentOver)} onOpenRun={() => {}} />);
  return screen.findByRole("region", { name: "Task: Ship the fix" });
};
const terms = (n: number) => screen.queryByRole("dialog", { name: `Terms: Point ${n}` });
const findTerms = (n: number) => screen.findByRole("dialog", { name: `Terms: Point ${n}` });
const closeTerms = (n: number) => fireEvent.click(within(terms(n)!).getByRole("button", { name: "Close the terms" }));
/** The texts open beside one row of a list, the row found by its radio's name, each named by its point ("Terms: Point
 *  4"), its words read or not yet (until they are, a text's title is "Risk point 4"). */
const besideRow = (list: HTMLElement, radio: RegExp) =>
  within(within(list).getByRole("radio", { name: radio }).closest("li")!).queryAllByRole("dialog")
    .map((d) => (d.getAttribute("aria-label") ?? "").replace(/^Terms: Risk point /, "Terms: Point "));
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

// ---- each cause of needs_owner, and its remedy ------------------------------------------------------------------------

describe("each cause of needs_owner shows its remedy, and the remedy is the right call", () => {
  const effect = (over: Partial<TaskView["effects"][number]> = {}) => ({
    id: "e".repeat(32), run_id: "aaaaaaaaaaaa", call_id: "toolu_1", tool: "Bash", kind: "external", target: "./deploy.sh", state: "unknown" as const,
    exit_status: null, resolved: null, note: null, unseen: false, at: now(), ...over,
  });

  it("an unknown effect: each call resolved, said happened or not, Continue waiting until each is", async () => {
    await show({ state: "needs_owner", cause: "effect", effects: [effect()] });
    const waits = screen.getByRole("note", { name: "What waits on you" });
    expect(within(waits).getByText(/nobody can tell whether it happened/)).toBeTruthy();
    expect((within(waits).getByRole("button", { name: /Continue/ }) as HTMLButtonElement).disabled).toBe(true);
    const calls = screen.getByRole("list", { name: "Calls nobody can tell happened" });
    fireEvent.change(within(calls).getByRole("textbox", { name: "A note on Bash" }), { target: { value: "it ran" } });
    fireEvent.click(within(calls).getByRole("button", { name: "It happened" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["resolve", { state_version: 4, what: "effect", effect_id: "e".repeat(32), outcome: "happened", note: "it ran" }]]));
  });

  it("an attempt ended ungated: its unseen calls among the unknown effects, each with Resolve; 'did not happen' two-step on an outward call", async () => {
    await show({ state: "needs_owner", cause: "ungated", attempts: [attempt({ attempt: 3, state: "blocked", cause: "ungated" })],
                 effects: [effect({ unseen: true, tool: "Bash", target: "git push origin main", kind: "git_remote" })] });
    const waits = screen.getByRole("note", { name: "What waits on you" });
    // its words whole: the batch rule makes some of those calls ones that may have run, so the words, and each unseen
    // call's own, say ran, or may have run
    expect(within(waits).getByText("Attempt 3 ended outside the workspace's checks: the calls below ran, or may have run, without them. Say for each whether it happened; Continue waits until each is resolved.")).toBeTruthy();
    expect((within(waits).getByRole("button", { name: /Continue/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("ran, or may have run, without the workspace's checks")).toBeTruthy();
    const no = screen.getByRole("button", { name: /It did not happen/ });
    fireEvent.click(no);
    expect(fake.presses()).toEqual([]);                 // the first press only arms it
    fireEvent.click(screen.getByRole("button", { name: /Sure it did not/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["resolve", { state_version: 4, what: "effect", effect_id: "e".repeat(32), outcome: "not_happened" }]]));
  });

  it("an unknown effect, and an ungated end, once every call is resolved: Continue is offered again, and goes", async () => {
    for (const cause of ["effect", "ungated"]) {
      fake.calls = [];
      await show({ state: "needs_owner", cause, attempts: [attempt({ attempt: 2 })],
                   effects: [effect({ state: "done", resolved: "page", unseen: cause === "ungated" })] });
      expect(screen.queryByRole("list", { name: "Calls nobody can tell happened" })).toBeNull();
      const go = within(screen.getByRole("note", { name: "What waits on you" })).getByRole("button", { name: /Continue/ }) as HTMLButtonElement;
      expect(go.disabled).toBe(false);
      fireEvent.click(go);
      await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
      cleanup();
    }
  });

  it("the worktree, a resume check failed on it, and a moved branch: keep it, or drop it in two steps, before Continue", async () => {
    await show({ state: "needs_owner", cause: "worktree" });
    expect(screen.getByText(/The workspace stopped under an attempt/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["resolve", { state_version: 4, what: "worktree", outcome: "keep" }]]));
    cleanup();
    fake.calls = [];
    // as the app gives a resume check that found the worktree changed: the worktree's cause, the newest attempt's check
    // saying which, the attempts listed as they ran
    await show({ state: "needs_owner", cause: "worktree", attempts: [
      attempt({ run_id: "aaaaaaaaaaaa", attempt: 1, fence: 1, resume: { outcome: "passed", check: null, found: null } }),
      attempt({ run_id: "bbbbbbbbbbbb", attempt: 2, fence: 2, resume: { outcome: "blocked", check: "worktree", found: "a.txt" } })] });
    expect(screen.getByText(/The resume check found the worktree changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Drop it" }));
    fireEvent.click(screen.getByRole("button", { name: "Drop the change?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["resolve", { state_version: 4, what: "worktree", outcome: "discard" }]]));
    cleanup();
    fake.calls = [];
    // a moved branch: the same two remedies, and no Continue before one of them (the app refuses it, 412)
    await show({ state: "needs_owner", cause: "branch", reason: "Its branch moved to 9f3e2a1." });
    let waits = screen.getByRole("note", { name: "What waits on you" });
    expect(within(waits).getByText(/branch moved by another hand\. Its branch moved to 9f3e2a1\./)).toBeTruthy();
    expect(within(waits).queryByRole("button", { name: /Continue/ })).toBeNull();
    fireEvent.click(within(waits).getByRole("button", { name: "Keep it" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["resolve", { state_version: 4, what: "worktree", outcome: "keep" }]]));
    cleanup();
    fake.calls = [];
    // the resume check's cause, whatever its check: read why, then Continue, with no worktree remedy the app refuses
    await show({ state: "needs_owner", cause: "resume", attempts: [attempt({ resume: { outcome: "blocked", check: "worktree", found: "a.txt" } })] });
    waits = screen.getByRole("note", { name: "What waits on you" });
    expect(within(waits).getByText(/The resume check failed \(worktree\)/)).toBeTruthy();
    expect(within(waits).queryByRole("button", { name: "Keep it" })).toBeNull();
    fireEvent.click(within(waits).getByRole("button", { name: /Continue/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
  });

  it("a watched place, and a window with no reset: each goes on at the owner's one press, which is Continue", async () => {
    // a window's remedy is "Continue when the owner judges it open (pressing is lifting the hold)",
    // and a watch's "look, then resume the agent ...; then Continue". The app's Continue lifts the window itself and
    // queues the next attempt (tasks.continue_); its resolve with a lift would suspend the task, "Resolved: press
    // Continue.", and a button that says "go on" would leave it waiting for a second press
    await show({ state: "needs_owner", cause: "watch", reason: "~/.claude/CLAUDE.md changed." });
    expect(screen.getByText(/~\/\.claude\/CLAUDE\.md changed\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "I have looked: go on" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
    cleanup();
    fake.calls = [];
    await show({ state: "needs_owner", cause: "window" });
    expect(screen.getByText(/when it resets is not known/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "It is open: go on" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
    cleanup();
    fake.calls = [];
    // a watch whose agent is still held: the app's 412 (tasks._paused_agent) said beside the task, nothing else pressed
    await show({ state: "needs_owner", cause: "watch", reason: "A watched place changed during the attempt: look, resume the agent, then Continue." });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url).endsWith("/continue")
      ? (fake.calls.push({ method: "POST", url: String(url), body: JSON.parse(String(init.body)) }),
         new Response(JSON.stringify({ detail: "This agent is paused: resume it first." }), { status: 412 }))
      : fake.answer(String(url), init))));
    fireEvent.click(screen.getByRole("button", { name: "I have looked: go on" }));
    expect(await screen.findByText("This agent is paused: resume it first.")).toBeTruthy();
    expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]);
    expect(useStore.getState().error).toBeNull();
  });

  it("more outward effects than a packet carries: End, two-step", async () => {
    await show({ state: "needs_owner", cause: "packet" });
    expect(screen.getByText(/a new task can start from its branch's head/)).toBeTruthy();
    fireEvent.click(within(screen.getByRole("note", { name: "What waits on you" })).getByRole("button", { name: "End it" }));
    fireEvent.click(screen.getByRole("button", { name: "End the task?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["end", { state_version: 4, outcome: "stopped" }]]));
  });

  // the causes whose remedy is to read why, act outside the task if it says so, and Continue (or Continue on...)
  const READ_THEN_CONTINUE: [string, RegExp][] = [
    ["resume", /The resume check failed/], ["settings", /Remove that setting/],
    ["secret", /shaped like a secret/], ["identity", /Confirm it again in Connections/], ["moves", /10 times within an hour/],
    ["handoff", /could not take the hand-off/], ["returned", /came back from your teammate/],
  ];
  for (const [cause, words] of READ_THEN_CONTINUE) {
    it(`${cause}: says why, then Continue`, async () => {
      // a resume check that failed is `blocked`
      await show({ state: "needs_owner", cause, reason: "The app's one line.", attempts: [attempt({ resume: { outcome: "blocked", check: "fence", found: null } })] });
      const waits = screen.getByRole("note", { name: "What waits on you" });
      expect(within(waits).getByText(words)).toBeTruthy();
      expect(within(waits).getByText(/The app's one line\./)).toBeTruthy();
      fireEvent.click(within(waits).getByRole("button", { name: /Continue/ }));
      await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
      expect(screen.getByRole("button", { name: "Continue on..." })).toBeTruthy();
    });
  }

  it("a received task held as returned says the app's words alone and offers no Continue or Continue on, End, done or stopped, being its way out", async () => {
    const received = { received: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } };
    const wentBack = "This task went back to the teammate who handed it to you.";
    await show({ ...received, state: "needs_owner", cause: "returned", reason: wentBack });
    const waits = screen.getByRole("note", { name: "What waits on you" });
    expect(waits.textContent).toBe(wentBack);
    expect(screen.queryByRole("button", { name: /Continue/ })).toBeNull();    // Continue and Continue on... alike
    expect(screen.getByRole("button", { name: "Mark it done" })).toBeTruthy();   // End in its other outcome
    fireEvent.click(screen.getByRole("button", { name: "End it" }));
    fireEvent.click(screen.getByRole("button", { name: "End the task, stopped?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["end", { state_version: 4, outcome: "stopped" }]]));
    cleanup();
    // the same words where the app gave no line of its own
    await show({ ...received, state: "needs_owner", cause: "returned", reason: null });
    expect(screen.getByRole("note", { name: "What waits on you" }).textContent).toBe(wentBack);
    expect(screen.queryByRole("button", { name: /Continue/ })).toBeNull();
    cleanup();
    // a received task waiting for another cause keeps that cause's remedy and Continue on...
    await show({ ...received, state: "needs_owner", cause: "resume", reason: "The app's one line." });
    expect(within(screen.getByRole("note", { name: "What waits on you" })).getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue on..." })).toBeTruthy();
    cleanup();
    // and so does one held for a watched place, or suspended, that the way back has not marked: the page shows every
    // received task not marked returned (TaskView's `returned`) as it shows any task; a marked one, in the test after
    // this
    await show({ ...received, state: "needs_owner", cause: "watch", reason: "The app's one line." });
    expect(within(screen.getByRole("note", { name: "What waits on you" })).getByRole("button", { name: "I have looked: go on" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue on..." })).toBeTruthy();
    cleanup();
    await show({ ...received, state: "suspended", cause: null, reason: "Resolved: press Continue." });
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue on..." })).toBeTruthy();
  });

  it("a received task the way back marked returned shows no Continue in any state: a hold with a remedy of its own keeps it, each other says the app's words alone", async () => {
    const marked = { received: true, returned: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } };
    const wentBack = "This task went back to the teammate who handed it to you.";
    const noGoingOn = () => expect(screen.queryByRole("button", { name: /Continue|go on|Next on/ })).toBeNull();
    // held for a window whose reset is not known: the app's words alone, no "It is open: go on" and no Continue on...
    await show({ ...marked, state: "needs_owner", cause: "window", reason: "The app's one line." });
    expect(screen.getByRole("note", { name: "What waits on you" }).textContent).toBe(wentBack);
    noGoingOn();
    expect(screen.getByRole("button", { name: "End it" })).toBeTruthy();
    cleanup();
    // held for a watched place: the page's words for its remedy, not the app's line, whose "then Continue" the page
    // offers no way to; no "I have looked: go on"
    await show({ ...marked, state: "needs_owner", cause: "watch", reason: "A watched place changed during the attempt: look, resume the agent, then Continue." });
    expect(screen.getByRole("note", { name: "What waits on you" }).textContent).toBe(`A place the workspace watches changed: look, and resume its agent where its hold asks. ${wentBack}`);
    noGoingOn();
    cleanup();
    // held for a worktree to keep or drop: that remedy stays, in its words, and nothing continues it after; both
    // worktree cases, the machine stopped under an attempt or its resume check found the worktree changed, each in its
    // own wording
    await show({ ...marked, state: "needs_owner", cause: "worktree", reason: null });
    const tree = screen.getByRole("note", { name: "What waits on you" });
    expect(within(tree).getByRole("button", { name: "Keep it" })).toBeTruthy();
    expect(within(tree).getByRole("button", { name: "Drop it" })).toBeTruthy();
    expect(tree.textContent).toContain(`The workspace stopped under an attempt, leaving a change in the task's worktree. Keep it as it is, or drop it to go back to the recorded head. ${wentBack}`);
    noGoingOn();
    cleanup();
    await show({ ...marked, state: "needs_owner", cause: "worktree", reason: null, attempts: [attempt({ resume: { outcome: "blocked", check: "worktree", found: "a.txt" } })] });
    expect(screen.getByRole("note", { name: "What waits on you" }).textContent).toContain(`The resume check found the worktree changed. Keep it as it is (its files become the new end state), or reset it to the recorded head. ${wentBack}`);
    noGoingOn();
    cleanup();
    // suspended once its worktree was kept, the app's line saying Continue: the page says the mark's words instead
    await show({ ...marked, state: "suspended", cause: null, reason: "Resolved: press Continue." });
    expect(screen.getByRole("note", { name: "What waits" }).textContent).toBe(wentBack);
    noGoingOn();
    expect(screen.getByRole("button", { name: "End it" })).toBeTruthy();
    cleanup();
    // running, the attempt the mark found still at work: Stop, and no Continue on... or Next on...
    await show({ ...marked, state: "running" });
    noGoingOn();
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    cleanup();
    // the control: the same received task, not marked, keeps them
    await show({ ...marked, returned: false, state: "suspended", cause: null, reason: "Resolved: press Continue." });
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue on..." })).toBeTruthy();
  });

  it("a marked received task held for its packet keeps the packet's End, its one way out; one held for an effect, an unseen call or a branch keeps that remedy, an effect's and an unseen call's only while one is unresolved", async () => {
    const marked = { received: true, returned: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } };
    const wentBack = "This task went back to the teammate who handed it to you.";
    const noGoingOn = () => expect(screen.queryByRole("button", { name: /Continue|go on|Next on/ })).toBeNull();
    const waits = () => screen.getByRole("note", { name: "What waits on you" });
    // a packet past its bound: its words and its End, and End is there once, not also among the task's controls
    await show({ ...marked, state: "needs_owner", cause: "packet", reason: "The packet would carry something shaped like a secret: end the task." });
    expect(waits().textContent).toBe(`This task has more outward effects than a packet carries. ${wentBack} End it`);
    expect(screen.getAllByRole("button", { name: "End it" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Mark it done" })).toBeNull();
    noGoingOn();
    fireEvent.click(within(waits()).getByRole("button", { name: "End it" }));
    fireEvent.click(within(waits()).getByRole("button", { name: "End the task?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["end", { state_version: 4, outcome: "stopped" }]]));
    cleanup();
    // an unknown effect: its words while one is unresolved, and nothing continues it after
    await show({ ...marked, state: "needs_owner", cause: "effect", reason: "An effect of this task is unknown: say whether it happened (Resolve) before anything continues it.", effects: [effect()] });
    expect(waits().textContent).toBe(`An attempt stopped while it ran a command: nobody can tell whether it happened. Say for each below whether it did. ${wentBack}`);
    noGoingOn();
    cleanup();
    // each resolved: the mark's words alone, End among the task's controls
    await show({ ...marked, state: "needs_owner", cause: "effect", reason: null, effects: [effect({ state: "done" })] });
    expect(waits().textContent).toBe(wentBack);
    expect(screen.getByRole("button", { name: "End it" })).toBeTruthy();
    noGoingOn();
    cleanup();
    // an unseen call, the same
    await show({ ...marked, state: "needs_owner", cause: "ungated", reason: "An attempt ran a tool call the gate did not see, and may have run others beside it: say whether each unseen call happened (Resolve), then Continue.", effects: [effect({ unseen: true })] });
    expect(waits().textContent).toBe(`An attempt ended outside the workspace's checks: the calls below ran, or may have run, without them. Say for each whether it happened. ${wentBack}`);
    noGoingOn();
    cleanup();
    await show({ ...marked, state: "needs_owner", cause: "ungated", reason: null, effects: [effect({ unseen: true, state: "not_done" })] });
    expect(waits().textContent).toBe(wentBack);
    cleanup();
    // either remedy while any effect is unresolved, as the app's push counts any (tasks.RETURNED_WHILE_UNKNOWN): an
    // unseen call's end that also left a gated call unknown, its unseen ones resolved, and an effect's hold whose one
    // unresolved effect is an unseen call
    await show({ ...marked, state: "needs_owner", cause: "ungated", reason: null, effects: [effect({ unseen: true, state: "not_done" }), effect({ id: "f".repeat(32), call_id: "toolu_2", unseen: false })] });
    expect(waits().textContent).toBe(`An attempt ended outside the workspace's checks: the calls below ran, or may have run, without them. Say for each whether it happened. ${wentBack}`);
    cleanup();
    await show({ ...marked, state: "needs_owner", cause: "effect", reason: null, effects: [effect({ unseen: true })] });
    expect(waits().textContent).toBe(`An attempt stopped while it ran a command: nobody can tell whether it happened. Say for each below whether it did. ${wentBack}`);
    cleanup();
    // a branch another hand moved: kept or dropped, as a worktree is
    await show({ ...marked, state: "needs_owner", cause: "branch", reason: "The task's branch moved: keep the worktree as it is or discard it (Resolve)." });
    expect(waits().textContent).toContain(`The task's branch moved by another hand. Keep the worktree as it is (its files become the new end state), or drop it to go back to the recorded head. ${wentBack}`);
    expect(within(waits()).getByRole("button", { name: "Keep it" })).toBeTruthy();
    expect(within(waits()).getByRole("button", { name: "Drop it" })).toBeTruthy();
    noGoingOn();
    cleanup();
    // the control: the same packet on a task not marked, its End in what waits, as before
    await show({ ...marked, returned: false, state: "needs_owner", cause: "packet", reason: null });
    expect(within(waits()).getByRole("button", { name: "End it" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "End it" })).toHaveLength(1);
  });

  it("closes a list of Continue on... opened before the way back's mark arrived", async () => {
    const received = { received: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } };
    for (const markedNow of [true, false]) {
      fake.onAction = (action) => { if (action === "stop") fake.tasks = [task({ ...received, returned: markedNow, state: "running" })]; };
      await show({ ...received, state: "running" });
      fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
      expect(screen.getByRole("group", { name: "Continue on" })).toBeTruthy();
      // the task is read again after a press: Stop, two steps
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      fireEvent.click(screen.getByRole("button", { name: "Stop the attempt?" }));
      await vi.waitFor(() => expect(fake.presses()).toEqual([["stop", {}]]));
      if (markedNow) {
        await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Continue on" })).toBeNull());
        expect(screen.queryByRole("button", { name: "Continue on..." })).toBeNull();
      } else {
        // the control: not marked, the list stays open
        await settle();
        expect(screen.getByRole("group", { name: "Continue on" })).toBeTruthy();
      }
      cleanup();
      fake.calls = [];
    }
  });

  it("a task suspended for a reset says when it goes on; one waiting between steps says so", async () => {
    const at = now() + 2 * 3600;
    await show({ state: "suspended", cause: "window", waits_until: at });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${clock(at)}, and the task goes on then.`)).toBeTruthy();
    cleanup();
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: false });
    expect(screen.getByText(/the earliest resets at .*: Continue then\./)).toBeTruthy();
    cleanup();
    await show({ state: "suspended", cause: "step_done" });
    expect(screen.getByText(/waits for you before the next one, as Between steps says/)).toBeTruthy();
  });

  it("a task waiting for a reset days away says its day, not a time that reads as today's", async () => {
    const at = now() + 3 * DAY;                                            // a seven-day window, three days off
    await show({ state: "suspended", cause: "window", waits_until: at });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${dayTime(at)}, and the task goes on then.`)).toBeTruthy();
    cleanup();
    const accountsOut = "Every account this task may take is out of room; the earliest resets at 2026-10-12 19:00 UTC.";
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: false, reason: accountsOut });
    expect(screen.getByText(`${accountsOut} Continue then (${dayTime(at)}).`)).toBeTruthy();
  });

  it("a task waiting for a reset, its own Auto-resume on, is told to Continue where the deployment has Auto-resume off", async () => {
    // the app goes on by itself only while both switches are on (tasks.py's _auto_resume): every task is made with its
    // own on, so a deployment with Auto-resume off is where the task's own switch alone would promise a resume
    fake.settings = { ...fake.settings, auto_resume: false };
    const at = now() + 2 * 3600;
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: true });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${clock(at)}: Continue then: this deployment has Auto-resume off.`)).toBeTruthy();
    expect(screen.queryByText(/goes on by itself|the task goes on then/)).toBeNull();
    cleanup();
    const accountsOut = "Every account this task may take is out of room; the earliest resets at 2026-10-12 19:00 UTC.";
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: true, reason: accountsOut });
    expect(screen.getByText(`${accountsOut} Continue then (${clock(at)}): this deployment has Auto-resume off.`)).toBeTruthy();
    cleanup();
    // both off: the task's own switch says why, so the deployment's is not named
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: false, reason: accountsOut });
    expect(screen.getByText(`${accountsOut} Continue then (${clock(at)}).`)).toBeTruthy();
    expect(screen.queryByText(/this deployment has Auto-resume off/)).toBeNull();
    cleanup();
    fake.settings = { ...fake.settings, auto_resume: true };               // both on: it goes on by itself
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: true, reason: accountsOut });
    expect(screen.getByText(`${accountsOut} It goes on by itself then (${clock(at)}).`)).toBeTruthy();
  });

  it("a task waiting for a reset is not said to go on by itself then while its agent is paused, nor at all once it is retired", async () => {
    // both switches on, but the app's auto-resume is held as a schedule's firing is (tasks.py's _firing_hold): a paused
    // agent's task waits past the reset for the pause to end, by the owner's resume or at the time it was paused until,
    // and a retired agent's goes on neither by itself nor at Continue
    const retired = "Its agent is retired: the task goes on neither by itself nor at Continue.";
    const at = now() + 2 * 3600;
    const accountsOut = "Every account this task may take is out of room; the earliest resets at 2026-10-12 19:00 UTC.";
    await show({ state: "suspended", cause: "window", waits_until: at, reason: accountsOut }, undefined, { paused: true, state: "paused" });
    expect(screen.getByText(`${accountsOut} It goes on by itself then (${clock(at)}), or when its agent is resumed if that is later.`)).toBeTruthy();
    cleanup();
    const ends = now() + 5 * 3600;
    await show({ state: "suspended", cause: "window", waits_until: at }, undefined, { paused: true, paused_until: ends, state: "paused" });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${clock(at)}, and the task goes on then, or when its agent's pause ends (${clock(ends)}) if that is later.`)).toBeTruthy();
    cleanup();
    // a pause until a time 12 hours or more off is named with its day, as a reset is; and an agent paused with a
    // live run reads working, its pause the app's `paused` all the same
    const far = now() + 20 * 3600;
    await show({ state: "suspended", cause: "window", waits_until: at, reason: accountsOut }, undefined, { paused: true, paused_until: far, state: "working" });
    expect(screen.getByText(`${accountsOut} It goes on by itself then (${clock(at)}), or when its agent's pause ends (${dayTime(far)}) if that is later.`)).toBeTruthy();
    cleanup();
    await show({ state: "suspended", cause: "window", waits_until: at, reason: accountsOut }, undefined, { retired: true, state: "retired" });
    expect(screen.getByText(`${accountsOut} ${retired}`)).toBeTruthy();
    expect(screen.queryByText(/goes on by itself|Continue then/)).toBeNull();
    cleanup();
    fake.settings = { ...fake.settings, auto_resume: false };              // nor Continue then, whatever the switches
    await show({ state: "suspended", cause: "window", waits_until: at, reason: accountsOut }, undefined, { retired: true, state: "retired" });
    expect(screen.getByText(`${accountsOut} ${retired}`)).toBeTruthy();
    expect(screen.queryByText(/Continue then/)).toBeNull();
    cleanup();
    await show({ state: "suspended", cause: "window", waits_until: at }, undefined, { retired: true, state: "retired" });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${clock(at)}. ${retired}`)).toBeTruthy();
    expect(screen.queryByText(/goes on by itself|the task goes on then|Continue then/)).toBeNull();
    cleanup();
    // with Auto-resume off, a paused agent's task is told to Continue then once the agent is resumed: Continue is
    // refused while it is paused (tasks.py's _paused_agent)
    await show({ state: "suspended", cause: "window", waits_until: at, reason: accountsOut }, undefined, { paused: true, state: "paused" });
    expect(screen.getByText(`${accountsOut} Continue then (${clock(at)}), once its agent is resumed: this deployment has Auto-resume off.`)).toBeTruthy();
    cleanup();
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: false }, undefined, { paused: true, paused_until: ends, state: "paused" });
    expect(screen.getByText(`Every account in this task's relay is out of room; the earliest resets at ${clock(at)}: Continue then, once its agent's pause ends (${clock(ends)}).`)).toBeTruthy();
  });

  it("the app's own line says why a task waits, where one cause comes from more than one place", async () => {
    // the app's lines: a continuation held for its agent's day's runs with Auto-resume off, an attempt that ran with
    // the old instructions after a change, and one held until midnight UTC, each under a cause the owner's Stop,
    // Between steps and the accounts' resets also give
    const spent = "Its agent's runs for today are spent (1 of 1), and Auto-resume is off: press Continue when you choose.";
    await show({ state: "suspended", cause: "stopped", reason: spent });
    expect(screen.getByText(spent)).toBeTruthy();
    expect(screen.queryByText(/You stopped it/)).toBeNull();
    cleanup();
    const ran = "The attempt sent before your change ran with the old instructions: look at what it did, then Continue, or End the task.";
    await show({ state: "suspended", cause: "step_done", reason: ran });
    expect(screen.getByText(ran)).toBeTruthy();
    expect(screen.queryByText(/as Between steps says/)).toBeNull();
    cleanup();
    const at = now() + 2 * 3600;
    const midnight = "Its agent's runs for today are spent (1 of 1): it continues after midnight UTC.";
    await show({ state: "suspended", cause: "window", waits_until: at, reason: midnight });
    expect(screen.getByText(`${midnight} It goes on by itself then (${clock(at)}).`)).toBeTruthy();
    expect(screen.queryByText(/Every account in this task's relay is out of room/)).toBeNull();
    cleanup();
    const accountsOut = "Every account this task may take is out of room; the earliest resets at 2026-10-12 19:00 UTC.";
    await show({ state: "suspended", cause: "window", waits_until: at, auto_resume: false, reason: accountsOut });
    expect(screen.getByText(`${accountsOut} Continue then (${clock(at)}).`)).toBeTruthy();
    cleanup();
    // the relay's wait where only a weekly reserve holds the accounts back and Auto-resume is off (tasks.py's
    // HELD_BY_A_RESERVE): suspended with no reset, the app's line alone, never a refused window's remedy (not a task
    // that needs its owner, and not a window that refused it)
    const held = "Every account this task may take is out of room or held back by its weekly reserve, and Auto-resume is off: press Continue when you choose.";
    await show({ state: "suspended", cause: "window", waits_until: null, auto_resume: false, reason: held });
    expect(screen.getByRole("note", { name: "What waits" }).textContent).toBe(held);
    expect(screen.queryByText(/refused it|It is open: go on|Continue then/)).toBeNull();
  });

  it("an attempt whose record the app could not take is not called a failed resume check", async () => {
    const lost = "The app could not take what this attempt's record brought (its checkpoints, its effects): look at its work, in the worktree and on the remote, before you Continue, or End the task.";
    await show({ state: "needs_owner", cause: "resume", reason: lost, attempts: [attempt({ resume: { outcome: "passed", check: null, found: null } })] });
    const waits = screen.getByRole("note", { name: "What waits on you" });
    expect(within(waits).getByText(lost)).toBeTruthy();
    expect(within(waits).queryByText(/The resume check failed/)).toBeNull();
    fireEvent.click(within(waits).getByRole("button", { name: /Continue/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
    cleanup();
    await show({ state: "needs_owner", cause: "resume", reason: lost });               // and with no attempt listed
    expect(screen.getByText(lost)).toBeTruthy();
    expect(screen.queryByText(/The resume check failed/)).toBeNull();
  });
});

describe("a press or a change that stops the live attempt is kept, and says when the workspace could not take the stop", () => {
  // the app's words (`not_stopped`): a press that stops the live attempt answers 200 whatever the
  // workspace says of the stop
  const why = "The attempt could not be stopped: the workspace is not running. The press is kept: the attempt's end, when it comes back, decides as you asked.";

  it("Stop says why", async () => {
    fake.notStopped = why;
    await show({ state: "running" }, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: /^Stop$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Stop the attempt\?/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["stop", {}]]));
    expect(await screen.findByText(why)).toBeTruthy();
  });

  it("Continue on... while it works says why, never that the attempt is stopped; with the stop taken, it says it is", async () => {
    fake.queued = false;
    fake.notStopped = why;
    await show({ state: "running" }, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    let list = screen.getByRole("group", { name: "Continue on" });
    fireEvent.click(within(list).getByRole("radio", { name: /Work/ }));
    fireEvent.click(within(list).getByRole("button", { name: "Stop the attempt, and continue on this" }));
    expect(await screen.findByText(why)).toBeTruthy();
    expect(screen.queryByText(/the attempt that works now is stopped/)).toBeNull();
    cleanup();
    fake.calls = [];
    fake.notStopped = null;
    await show({ state: "running" }, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    list = screen.getByRole("group", { name: "Continue on" });
    fireEvent.click(within(list).getByRole("radio", { name: /Work/ }));
    fireEvent.click(within(list).getByRole("button", { name: "Stop the attempt, and continue on this" }));
    expect(await screen.findByText(/Asked for: the attempt that works now is stopped/)).toBeTruthy();
  });

  it.each([
    ["suspended", {}],
    ["needs the owner", { state: "needs_owner" as const, cause: "resume", reason: "The start reported what every account would: read why, then Continue or Continue on." }],
  ])("Continue on... on a task that waited (%s) and queued nothing says so, never that an attempt that works is stopped", async (_, over) => {
    // the app answers queued: false for a task that was not running when the next attempt's packet could not be built
    // (tasks.continue_on, _go_asked), the task then needing the owner; no attempt worked, and none follows. Every state
    // but running is such a task, one that needed the owner already among them
    fake.queued = false;
    await show({ state: "suspended", ...over }, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    const list = screen.getByRole("group", { name: "Continue on" });
    fireEvent.click(within(list).getByRole("radio", { name: /Second/ }));
    fireEvent.click(within(list).getByRole("button", { name: "Continue on this" }));
    expect(await screen.findByText("Nothing was queued: the task waits for you first, as its line above says.")).toBeTruthy();
    expect(screen.queryByText(/the attempt that works now is stopped/)).toBeNull();
    expect(fake.presses()).toEqual([["continue-on", { state_version: 4, backend: { kind: "account", id: A.account } }]]);
  });

  it("a change from the form says why, and so does Between steps", async () => {
    fake.notStopped = why;
    await show({ state: "running" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(await screen.findByText(why)).toBeTruthy();
    cleanup();
    fake.calls = [];
    await show({ state: "running" });
    fireEvent.change(screen.getByRole("combobox", { name: "Between steps" }), { target: { value: "wait" } });
    expect(await screen.findByText(why)).toBeTruthy();
  });
});

function attempt(over: Partial<TaskView["attempts"][number]> = {}): TaskView["attempts"][number] {
  return { run_id: "bbbbbbbbbbbb", step: 1, attempt: 1, fence: 1, backend: { kind: "account", id: "default" }, state: "done", cause: null,
           reason: null, resume: null, served_model: null, claude_version: "2.1.274", started_at: now() - 600, ended_at: now() - 60,
           packet_digest: "c".repeat(64), ...over };
}

// ---- the relay set ----------------------------------------------------------------------------------------------------

describe("the relay set, in order, names each account left out and why", () => {
  // D, a Team seat, is not left out but taken after every account of
  // the task's group; E, F and H are, each named with why, in the app's words as it gives them
  it("lists each account with its windows, and why E, F and H are out", async () => {
    const D = owner("d1d1d1d1d1d1", "Desk", "Claude Team");
    const Eu = owner("a2a2a2a2a2a2", "Unconfirmed", "Claude Pro", { state: "reported" });
    const F = owner("a3a3a3a3a3a3", "Changed", "Claude Max", { state: "changed" });
    const G = owner("a4a4a4a4a4a4", "Probe", "Claude Pro", { windows: [] });
    const relay_set = [
      { account: A, order: 1, eligible: true, left_out: null }, { account: B, order: 2, eligible: true, left_out: null },
      { account: { ...owner("c2c2c2c2c2c2", "Third", "Claude Max") }, order: 3, eligible: true, left_out: null },
      { account: Eu, order: 4, eligible: false, left_out: "not_confirmed" },
      { account: F, order: 5, eligible: false, left_out: "identity changed" },
      { account: G, order: 6, eligible: true, left_out: null },
      { account: H, order: 7, eligible: false, left_out: "weekly reserve" },
      { account: D, order: 8, eligible: true, left_out: null },
    ];
    const pane = await show({ relay_set });
    const unfold = within(pane).getByRole("button", { name: /Its relay: 5 of 8 accounts/ });
    fireEvent.click(unfold);
    // usage credits' text beside each account of the set, as it is unfolded, and nothing else
    await vi.waitFor(() => expect(screen.getAllByRole("dialog", { name: "Terms: Point 8" }).length).toBe(8));
    expect(screen.getAllByRole("dialog").length).toBe(8);
    const set = within(pane).getByRole("list", { name: "Its relay set" });
    const rows = within(set).getAllByRole("listitem");
    for (const r of rows) expect(within(r).getByRole("dialog", { name: "Terms: Point 8" })).toBeTruthy();
    expect(rows.map((r) => r.textContent?.match(/Claude Code · (\w+)/)?.[1])).toEqual(["Second", "Home", "Third", "Unconfirmed", "Changed", "Probe", "Spare", "Desk"]);
    expect(rows[3].textContent).toContain("left out: not confirmed");
    expect(rows[4].textContent).toContain("left out: identity changed");
    expect(rows[6].textContent).toContain("left out: weekly reserve");
    expect(rows[5].textContent).toContain("unknown");                       // the windows of G: none recorded
    expect(rows[6].textContent).toContain("7d 93%");
    expect(rows[7].textContent).toContain("Acme's Team seats");             // D, after every account of the group
    for (const i of [0, 1, 2, 5, 7]) expect(rows[i].textContent).not.toContain("left out");
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Close the terms" }));
    expect(screen.getAllByRole("dialog", { name: "Terms: Point 8" }).length).toBe(7);
    fireEvent.click(unfold);                                                // folded, then unfolded: each opens again
    fireEvent.click(unfold);
    await vi.waitFor(() => expect(screen.getAllByRole("dialog", { name: "Terms: Point 8" }).length).toBe(8));
  });
});

// ---- notices, presses, the crossing line -------------------------------------------------------------------------------

describe("the relay notice on the task's page and above Continue on..., the organisation notice beside a seat, none waiting", () => {
  it("opens the relay's text over the New task form and above Continue on..., and the organisation's beside a seat; the presses go on", async () => {
    fake.accounts = [B, A, S];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(await findTerms(2)).toBeTruthy();
    cleanup();
    await show({}, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    const list = screen.getByRole("group", { name: "Continue on" });
    expect(await findTerms(2)).toBeTruthy();                                // A is in the task's group
    // the organisation's beside the seat as the list opens, before any account is chosen (point 4)
    expect(besideRow(list, /Work/)).toContain("Terms: Point 4");
    expect(besideRow(list, /Second/)).not.toContain("Terms: Point 4");
    fireEvent.click(within(list).getByRole("radio", { name: /Work/ }));
    const org = await findTerms(4);
    expect(within(org).getByText("Point 4's seat-settings text.")).toBeTruthy();
    fireEvent.click(within(list).getByRole("button", { name: "Continue on this" }));   // with both texts open
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue-on", { state_version: 4, backend: { kind: "account", id: S.account } }]]));
  });
});

describe("the presses go through, held back by a reserve or in another group, and Stop then Continue", () => {
  it("offers an account held back by its reserve and one in another group, and presses each", async () => {
    await show({}, [B, H, S]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    let list = screen.getByRole("group", { name: "Continue on" });
    fireEvent.click(within(list).getByRole("radio", { name: /Spare/ }));
    expect(besideRow(list, /Spare/)).not.toContain("Terms: Point 4");      // H is in the task's own group
    expect(besideRow(list, /Work/)).toContain("Terms: Point 4");          // S is not, chosen or not
    fireEvent.click(within(list).getByRole("button", { name: "Continue on this" }));
    await vi.waitFor(() => expect(fake.presses().length).toBe(1));
    expect(fake.presses()[0]).toEqual(["continue-on", { state_version: 4, backend: { kind: "account", id: H.account } }]);
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Continue on" })).toBeNull());   // the press done
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    list = screen.getByRole("group", { name: "Continue on" });
    fireEvent.click(within(list).getByRole("radio", { name: /Work/ }));
    expect(await findTerms(4)).toBeTruthy();                                // the organisation notice, with the press
    fireEvent.click(within(list).getByRole("button", { name: "Continue on this" }));
    await vi.waitFor(() => expect(fake.presses().length).toBe(2));
    expect(fake.presses()[1]).toEqual(["continue-on", { state_version: 4, backend: { kind: "account", id: S.account } }]);
  });

  it("Next on... while it runs, then Stop with no version, then Continue on the task's own account", async () => {
    fake.providers = [{ id: "deepseek", label: "DeepSeek", vendor: "DeepSeek", docs: "", model: "deepseek-flash", set: true }];
    await show({ state: "running" }, [B, A, S]);
    // a kept key is offered by Continue on..., and not by Next on..., whose route takes no cost cap
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    expect(await within(screen.getByRole("group", { name: "Continue on" })).findByRole("radio", { name: /API key · DeepSeek/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next on..." }));
    const list = screen.getByRole("group", { name: "Next on" });
    expect(within(list).queryByRole("radio", { name: /API key/ })).toBeNull();
    expect(within(list).queryByRole("textbox", { name: /cost cap/ })).toBeNull();
    fireEvent.click(within(list).getByRole("radio", { name: /Work/ }));
    fireEvent.click(within(list).getByRole("button", { name: "Next on this" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["next-on", { state_version: 4, backend: { kind: "account", id: S.account } }]]));
    fireEvent.click(screen.getByRole("button", { name: /^Stop$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Stop the attempt\?/ }));
    await vi.waitFor(() => expect(fake.presses()[1]).toEqual(["stop", {}]));
    cleanup();
    fake.calls = [];
    await show({ state: "suspended", cause: "stopped" }, [B, A, S]);
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
  });
});

describe("the task's own hold listed with its end time, and Cancel", () => {
  it("names an account's confirmation held for tasks' relays by the account's email, read from the account's own row, beside crossing's own", async () => {
    const at = now() + 120;
    const hold = { id: "k".repeat(32), kind: "task.account_own", takes_effect_at: at };
    const cross = { id: "h".repeat(32), kind: "task.cross", takes_effect_at: at };
    const waiting = { ...A, state: "held" as const, hold };
    // the task's view carries crossing's hold; the account's is on its own row
    await show({ holds: [cross] }, [B, waiting]);
    let holds = within(screen.getByRole("list", { name: "Its holds" })).getAllByRole("listitem");
    expect(holds.length).toBe(2);
    expect(holds[0].textContent).toContain(`Crossing organisations turns back on at ${clock(at)}`);
    expect(holds[1].textContent).toContain(`second@example.com joins your tasks' relay at ${clock(at)}, unless you cancel it.`);
    // where the task's view carries the account's hold as well, it is listed once
    cleanup();
    await show({ holds: [hold, cross] }, [B, waiting]);
    holds = within(screen.getByRole("list", { name: "Its holds" })).getAllByRole("listitem");
    expect(holds.length).toBe(2);
    expect(holds.filter((h) => h.textContent!.includes("second@example.com")).length).toBe(1);
  });

  it("two account confirmations held at once: each named by its own email with its own end, each cancelled by its own id", async () => {
    const at = now() + 120, later = now() + 175;
    const one = { ...A, state: "held" as const, hold: { id: "k".repeat(32), kind: "task.account_own", takes_effect_at: at } };
    const two = { ...H, state: "held" as const, hold: { id: "m".repeat(32), kind: "task.account_own", takes_effect_at: later } };
    await show({}, [B, one, two]);
    const holds = within(screen.getByRole("list", { name: "Its holds" })).getAllByRole("listitem");
    expect(holds.length).toBe(2);
    expect(holds[0].textContent).toContain(`second@example.com joins your tasks' relay at ${clock(at)}, unless you cancel it.`);
    expect(holds[1].textContent).toContain(`spare@example.com joins your tasks' relay at ${clock(later)}, unless you cancel it.`);
    fireEvent.click(within(holds[1]).getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "POST" && c.url === `/api/ade/holds/${"m".repeat(32)}/cancel`)).toBe(true));
    expect(fake.calls.some((c) => c.url === `/api/ade/holds/${"k".repeat(32)}/cancel`)).toBe(false);
  });

  it("names an offer of the task to a teammate held before it goes out, with its end and Cancel", async () => {
    const at = now() + 150;
    await show({ holds: [{ id: "f0".repeat(16), kind: "task.offer", takes_effect_at: at }] });
    const holds = within(screen.getByRole("list", { name: "Its holds" })).getAllByRole("listitem");
    expect(holds.length).toBe(1);
    expect(holds[0].textContent).toContain(`Your offer of this task to a teammate goes out at ${clock(at)}, unless you cancel it.`);
    fireEvent.click(within(holds[0]).getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "POST" && c.url === `/api/ade/holds/${"f0".repeat(16)}/cancel`)).toBe(true));
  });

  it("lists crossing turned back on, with its time, and cancels it", async () => {
    const at = now() + 180;
    await show({ holds: [{ id: "h".repeat(32), kind: "task.cross", takes_effect_at: at }] });
    const holds = screen.getByRole("list", { name: "Its holds" });
    expect(within(holds).getByText(/Crossing organisations turns back on/).textContent).toContain(clock(at));
    fireEvent.click(within(holds).getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "POST" && c.url === `/api/ade/holds/${"h".repeat(32)}/cancel`)).toBe(true));
  });
});

describe("the crossing's line, with its time, opening the organisation notice", () => {
  it("shows the relay's and the crossing's lines, each opening its point's text with its cases", async () => {
    const at = now() - 300;
    await show({ moves: [
      { seq: 7, at, kind: "relay", from: "account:default", to: `account:${S.account}`, why: "Home out of room", point: 2, cases: [] },
      { seq: 8, at, kind: "crossing", from: "personal", to: "org:acme", why: null, point: 1, cases: ["crossing", "onto_seat"] },
    ] }, [B, A, S]);
    const moves = screen.getByRole("list", { name: "Its moves" });
    const crossing = within(moves).getByText(/It crossed organisations/).closest("li")!;
    expect(crossing.textContent).toContain(clock(at));
    const one = await findTerms(1);
    expect(within(one).getByText("It crossed by itself.")).toBeTruthy();
    expect(within(one).getByText("Point 1's seat-settings text.")).toBeTruthy();
    expect(within(one).queryByText(/goes back there/)).toBeNull();       // Back home's case is not this line's
    expect(await findTerms(2)).toBeTruthy();
  });
});

// ---- the pop-ups ------------------------------------------------------------------------------------------------------

describe("the local provider's sentence, said once on a task", () => {
  it("is said once on a task the app's view marks as under the local provider, in the design's words, and not otherwise", async () => {
    const words = "On this machine an agent's runs could reach the secret its records are signed with, so the record of what a task did holds against an agent that follows the rules, not against code written to forge it.";
    let pane = await show({ local_provider: true });
    expect(within(pane).getAllByText(words)).toHaveLength(1);
    expect(within(pane).getByRole("note", { name: "Under the local provider" }).textContent).toBe(words);
    cleanup();
    pane = await show({ local_provider: false });
    expect(within(pane).queryByText(words)).toBeNull();
  });
});

describe("each of the owner's controls on the task's page opens its text over it, every time, holding nothing", () => {
  it("the New task form: points 2, 3, 7, 8 and 14, each again when it opens again; the form works with them open", async () => {
    fake.settings = { ...fake.settings, local_provider: true };
    fake.accounts = [B, A];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    const newTask = await screen.findByRole("button", { name: /New task/ });
    const credits = () => screen.queryAllByRole("dialog", { name: "Terms: Point 8" });
    for (let round = 0; round < 2; round++) {
      fireEvent.click(newTask);
      for (const n of [2, 3, 7, 14]) expect(await findTerms(n)).toBeTruthy();
      await vi.waitFor(() => expect(credits().length).toBe(2));           // point 8 beside each account offered
      if (round === 0) fireEvent.click(newTask);                          // closed, then opened again
      await settle();
      if (round === 0) for (const n of [2, 3, 7, 14]) expect(terms(n)).toBeNull();
      if (round === 0) expect(credits()).toEqual([]);
    }
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "Ship it" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Goal" }), { target: { value: "Ship the release." } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Step 1's objective" }), { target: { value: "Build" } });
    const home = within(form).getByRole("radio", { name: /Home/ });
    const second = within(form).getByRole("radio", { name: /Second/ });
    // each in its own account's row, whichever is chosen
    for (const r of [home, second]) expect(within(r.closest("div")!).getByRole("dialog", { name: "Terms: Point 8" })).toBeTruthy();
    fireEvent.click(within(home.closest("div")!).getByRole("button", { name: "Close the terms" }));
    fireEvent.click(second);                                                // the form works with the other open
    expect(credits().length).toBe(1);
    expect(within(second.closest("div")!).getByRole("dialog", { name: "Terms: Point 8" })).toBeTruthy();
    // the text stands outside the choice's label: it is no part of the choice's name
    expect(within(form).getByRole("radio", { name: (n) => n.includes("Second") && n.includes("Claude Max") && !n.includes("Terms") })).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "POST" && c.url === "/api/ade/agents/tasks")).toBe(true));
    const made = fake.calls.find((c) => c.method === "POST" && c.url === "/api/ade/agents/tasks")!.body;
    expect(made).toEqual({ agent_id: AGENT, title: "Ship it", goal: "Ship the release.", steps: [{ objective: "Build", check: null }],
                           protected: [], external: [], backend: { kind: "account", id: A.account }, between_steps: "checked" });
  });

  it("no crossing text where the deployment does not let a task cross, and none of the local provider's elsewhere", async () => {
    fake.settings = { ...fake.settings, cross_groups: false };
    fake.accounts = [B];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(await findTerms(2)).toBeTruthy();
    expect(terms(3)).toBeNull();
    expect(terms(14)).toBeNull();
  });

  it("Relay and Auto-resume turned on open points 2 and 7, every time, and the switch is not held", async () => {
    await show({ relay: false, auto_resume: false });
    const switches = screen.getByRole("group", { name: "Its switches" });
    const [relay, auto] = within(switches).getAllByRole("checkbox");
    fireEvent.click(relay);
    expect(await findTerms(2)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { relay: true }]]));
    // the text stands outside the switch's label: it is no part of the switch's name
    expect(within(switches).getByRole("checkbox", { name: "Relay: when an account runs out of room, the task goes on on your next account." })).toBe(relay);
    closeTerms(2);
    expect(terms(2)).toBeNull();
    fireEvent.click(auto);
    expect(await findTerms(7)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()[1]).toEqual(["switches", { auto_resume: true }]));
    fireEvent.click(relay);                                                 // its state as the page read it: off, so on again
    expect(await findTerms(2)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()[2]).toEqual(["switches", { relay: true }]));
    closeTerms(7);
    fireEvent.click(auto);                                                  // Auto-resume on again: its text again
    expect(await findTerms(7)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()[3]).toEqual(["switches", { auto_resume: true }]));
  });

  it("turns a switch on at once while the terms are still being read: nothing waits on its text, Relay's or Auto-resume's", async () => {
    fake.hangRisks = true;
    await show({ relay: false, auto_resume: false });
    const [relay, auto] = within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(relay);
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { relay: true }]]));
    await vi.waitFor(() => expect(auto.disabled).toBe(false));
    fireEvent.click(auto);
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { relay: true }], ["switches", { auto_resume: true }]]));
    // each text open, named by its point until the texts are read
    expect(within(await screen.findByRole("dialog", { name: "Terms: Risk point 2" })).getByText(/Reading the terms/)).toBeTruthy();
    expect(within(await screen.findByRole("dialog", { name: "Terms: Risk point 7" })).getByText(/Reading the terms/)).toBeTruthy();
  });

  it("opens a switch's text as it is about to be turned on, the pointer coming to it or the keyboard, before any press; none for one on, or one not to be turned on", async () => {
    await show({ relay: false, auto_resume: false, cross_groups: false });
    const [relay, auto, cross] = within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox");
    for (const [box, n] of [[relay, 2], [auto, 7], [cross, 3]] as const) {
      fireEvent.mouseEnter(box.closest("label")!);                         // the pointer comes to it
      expect(await findTerms(n)).toBeTruthy();
      closeTerms(n);
      fireEvent.focus(box);                                                 // the keyboard comes to it
      expect(await findTerms(n)).toBeTruthy();
    }
    await settle();
    expect(fake.calls.filter((c) => c.method !== "GET")).toEqual([]);      // nothing pressed, nothing posted, nothing asked
    expect(screen.queryByRole("group", { name: "Letting this task cross organisations again" })).toBeNull();
    closeTerms(2);
    fireEvent.click(relay);                                                 // the press opens it again, and goes at once
    expect(await findTerms(2)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { relay: true }]]));
    cleanup();
    await show({ relay: true, auto_resume: true, cross_groups: true });     // each on, so about to be turned off: none opens
    for (const c of within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")) {
      fireEvent.mouseEnter(c.closest("label")!);
      fireEvent.focus(c);
    }
    await settle();
    for (const n of [2, 3, 7]) expect(terms(n)).toBeNull();
    cleanup();
    await show({ received: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } });
    const kept = within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")[2];
    fireEvent.mouseEnter(kept.closest("label")!);                          // a received task's, not to be turned on
    fireEvent.focus(kept);
    await settle();
    expect(terms(3)).toBeNull();
  });

  it("a switch whose setting this deployment has off is not to be turned on, opens no text, and says so; one on may be turned off", async () => {
    fake.settings = { ...fake.settings, relay: false, auto_resume: false };
    await show({ relay: false, auto_resume: false });
    let switches = screen.getByRole("group", { name: "Its switches" });
    const [relay, auto] = within(switches).getAllByRole("checkbox") as HTMLInputElement[];
    expect([relay.disabled, auto.disabled]).toEqual([true, true]);
    expect(within(switches).getByText("This deployment has the relay off (OPEN_EDITION_ADE_TASK_RELAY): no task's relay runs, whatever its Relay switch says, and a task made while it is off keeps its switch off after the deployment turns the relay on, until you turn it on. When its account runs out of room, the task waits for that account's reset, and you Continue it then (Auto-resume is off), or for you where no reset is known; Continue on... moves it to another of your accounts now.")).toBeTruthy();
    expect(within(switches).getByText("This deployment has Auto-resume off: a task's Auto-resume stays off.")).toBeTruthy();
    for (const c of [relay, auto]) { fireEvent.mouseEnter(c.closest("label")!); fireEvent.focus(c); }
    await settle();
    expect(terms(2)).toBeNull();
    expect(terms(7)).toBeNull();
    cleanup();
    await show({ relay: true, auto_resume: true });                         // each on from before: it may be turned off
    switches = screen.getByRole("group", { name: "Its switches" });
    // its own Auto-resume on, the deployment's off: it does not go on by itself at the reset
    expect(within(switches).getByText(/When its account runs out of room, the task waits for that account's reset, and you Continue it then \(Auto-resume is off\)/)).toBeTruthy();
    const [on, onToo] = within(switches).getAllByRole("checkbox") as HTMLInputElement[];
    expect([on.disabled, onToo.disabled]).toEqual([false, false]);
    fireEvent.click(on);
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { relay: false }]]));
  });

  it("says what the relay off means, by the deployment's setting or by the task's switch, and nothing of it while the relay is on", async () => {
    // the deployment's relay off, the task's Auto-resume on: it goes on by itself at the reset
    fake.settings = { ...fake.settings, relay: false };
    await show({ relay: false, auto_resume: true });
    let switches = screen.getByRole("group", { name: "Its switches" });
    expect(within(switches).getByText("This deployment has the relay off (OPEN_EDITION_ADE_TASK_RELAY): no task's relay runs, whatever its Relay switch says, and a task made while it is off keeps its switch off after the deployment turns the relay on, until you turn it on. When its account runs out of room, the task waits for that account's reset and goes on there by itself (Auto-resume), or for you where no reset is known; Continue on... moves it to another of your accounts now.")).toBeTruthy();
    expect(within(switches).queryByText(/The relay is off for this task/)).toBeNull();
    cleanup();
    // the deployment's relay on, the task's own switch off, and its Auto-resume off
    fake.settings = { ...fake.settings, relay: true };
    await show({ relay: false, auto_resume: false });
    switches = screen.getByRole("group", { name: "Its switches" });
    expect(within(switches).getByText("The relay is off for this task. When its account runs out of room, the task waits for that account's reset, and you Continue it then (Auto-resume is off), or for you where no reset is known; Continue on... moves it to another of your accounts now. Turn Relay on to let it move by itself the next time its account runs out of room.")).toBeTruthy();
    expect(within(switches).queryByText(/This deployment has the relay off/)).toBeNull();
    cleanup();
    await show({ relay: true, auto_resume: true });
    switches = screen.getByRole("group", { name: "Its switches" });
    expect(within(switches).queryByText(/relay is off|relay off/)).toBeNull();
  });

  it("the New task form says what the relay off means where this deployment has it off, and what the relay does where on", async () => {
    fake.settings = { ...fake.settings, relay: false };
    fake.accounts = [B];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const form = screen.getByRole("form", { name: "New task" });
    expect(within(form).getByText("Relay: off: this deployment does not set the relay on (OPEN_EDITION_ADE_TASK_RELAY). When its account runs out of room, the task waits for that account's reset and goes on there by itself (Auto-resume), or for you where no reset is known; Continue on... moves it to another of your accounts now.")).toBeTruthy();
    await settle();
    expect(terms(2)).toBeNull();                                            // no relay's text where the relay is off
    cleanup();
    // Auto-resume off too: the reset is the owner's Continue (the form's words are pinned with Auto-resume off as
    // well as on)
    fake.settings = { ...fake.settings, relay: false, auto_resume: false };
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(within(screen.getByRole("form", { name: "New task" })).getByText("Relay: off: this deployment does not set the relay on (OPEN_EDITION_ADE_TASK_RELAY). When its account runs out of room, the task waits for that account's reset, and you Continue it then (Auto-resume is off), or for you where no reset is known; Continue on... moves it to another of your accounts now.")).toBeTruthy();
    cleanup();
    fake.settings = { ...fake.settings, auto_resume: true };
    fake.settings = { ...fake.settings, relay: true };
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(within(screen.getByRole("form", { name: "New task" })).getByText("Relay: on. When an account runs out of room, the task goes on on your next account.")).toBeTruthy();
  });

  it("says in the New task form's one sentence that a task goes on on the next account only where this deployment has the relay on", async () => {
    // the form's sentence ends with its relay clause; with the relay off by default the clause says a move the
    // deployment does not make, in the same form as its own "Relay: off: ..." line further down
    const what = "A task is a job in steps; each step ends at a checkpoint the workspace makes, and is checked when it has a check";
    const sentence = (form: HTMLElement) => within(form).getByText((text, el) => el?.tagName === "DIV" && text.startsWith(what));
    fake.accounts = [B];
    const base = fake.settings;
    // the clause follows the relay alone: the relay off and on, each with the fake's four other switches (auto_resume and
    // cross_groups on, teammate and local_provider off), with all four on and with all four off, but local_provider on
    // in the last relay-on case
    const cases: [string, Partial<TaskSettings>, boolean][] = [
      ["off", { relay: false }, false],
      ["off, every other switch on", { relay: false, auto_resume: true, teammate: true, cross_groups: true, local_provider: true }, false],
      ["off, every other switch off", { relay: false, auto_resume: false, teammate: false, cross_groups: false, local_provider: false }, false],
      ["on", { relay: true }, true],
      ["on, every other switch on", { relay: true, auto_resume: true, teammate: true, cross_groups: true, local_provider: true }, true],
      ["on, every other switch off, the provider local", { relay: true, auto_resume: false, teammate: false, cross_groups: false, local_provider: true }, true]];
    for (const [which, over, says] of cases) {
      fake.settings = { ...base, ...over };
      render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
      const form = screen.getByRole("form", { name: "New task" });
      expect([which, sentence(form).textContent]).toEqual(
        [which, says ? `${what}; when an account runs out of room, the task goes on on your next one.` : `${what}.`]);
      if (!says) expect(form.textContent).not.toMatch(/goes on on your next/);
      cleanup();
    }
  });

  it("says that Cross organisations does nothing while the relay is off, on the form and under a task's switch on", async () => {
    const inert = "Its Cross organisations switch does nothing while the relay is off: only the relay crosses by itself.";
    fake.settings = { ...fake.settings, relay: false, cross_groups: true };
    await show({ relay: false, cross_groups: true });
    expect(within(screen.getByRole("group", { name: "Its switches" })).getByText(inert)).toBeTruthy();
    cleanup();
    await show({ relay: false, cross_groups: false });                       // its switch off: nothing to say of it
    expect(within(screen.getByRole("group", { name: "Its switches" })).queryByText(inert)).toBeNull();
    cleanup();
    fake.tasks = [];                                                         // no task yet: the New task form
    fake.accounts = [B];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(within(screen.getByRole("form", { name: "New task" })).getByText("Cross organisations: on, and it does nothing while the relay is off: only the relay crosses by itself.")).toBeTruthy();
    cleanup();
    fake.settings = { ...fake.settings, relay: true };                       // the relay on: the switch is the switch
    await show({ relay: true, cross_groups: true });
    expect(within(screen.getByRole("group", { name: "Its switches" })).queryByText(inert)).toBeNull();
    cleanup();
    fake.tasks = [];
    fake.accounts = [B];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    expect(within(screen.getByRole("form", { name: "New task" })).getByText("Cross organisations: on.")).toBeTruthy();
  });

  it("Cross organisations turned on opens point 3 and takes a confirmation for task-cross; off takes neither; a received task's stays off", async () => {
    await show({ cross_groups: false });
    const cross = within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")[2];
    fireEvent.click(cross);
    expect(await findTerms(3)).toBeTruthy();
    // its confirmation cancelled and the switch turned on again: its text opens again
    closeTerms(3);
    fireEvent.click(within(await screen.findByRole("group", { name: "Letting this task cross organisations again" })).getByRole("button", { name: "Cancel" }));
    fireEvent.click(cross);
    expect(await findTerms(3)).toBeTruthy();
    const confirm = await screen.findByRole("group", { name: "Letting this task cross organisations again" });
    fireEvent.change(within(confirm).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { cross_groups: true, confirmation: "the-confirmation" }]]));
    expect(fake.calls.find((c) => c.method === "POST" && c.url === "/api/account/confirm")!.body).toEqual({ password: "pw", change: `task-cross:${TASK}` });
    cleanup();
    fake.calls = [];
    await show({ cross_groups: true });
    fireEvent.click(within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")[2]);
    await vi.waitFor(() => expect(fake.presses()).toEqual([["switches", { cross_groups: false }]]));
    expect(terms(3)).toBeNull();
    cleanup();
    await show({ received: true, cross_groups: false, continued_from: { task: "1".repeat(32), offer: "2".repeat(32) } });
    expect((within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")[2] as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/A task received from a teammate stays in its group/)).toBeTruthy();
  });

  it("Continue on...: point 2 above the list, 4 beside another group's account, 5 onto an Enterprise seat, 8 beside each account, 11 with a key's cap", async () => {
    fake.providers = [{ id: "anthropic", label: "Anthropic", vendor: "Anthropic", docs: "", model: "claude", set: true },
                      { id: "deepseek", label: "DeepSeek", vendor: "DeepSeek", docs: "", model: "deepseek-flash", set: true }];
    await show({}, [B, A, S, E]);
    const open = async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
      return screen.getByRole("group", { name: "Continue on" });
    };
    let list = await open();
    expect(await findTerms(2)).toBeTruthy();
    // usage credits' text beside each account listed (the three besides the task's own), as the list opens, and no key
    const accountRows = within(list).getAllByRole("radio", { name: (n) => !n.startsWith("API key") }).map((r) => r.closest("li")!);
    expect(accountRows.length).toBe(3);
    await vi.waitFor(() => expect(within(list).getAllByRole("dialog", { name: "Terms: Point 8" }).length).toBe(3));
    for (const li of accountRows) expect(within(li).getByRole("dialog", { name: "Terms: Point 8" })).toBeTruthy();
    // and, as the list opens, before any choice, the organisation's beside the Team seat and the Enterprise seat's beside
    // that seat, each with its seat-settings text; nothing of either beside an account of the task's own group
    expect(besideRow(list, /Second/)).toEqual(["Terms: Point 8"]);
    expect(besideRow(list, /Work/)).toEqual(["Terms: Point 4", "Terms: Point 8"]);
    expect(besideRow(list, /Corp/)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    expect(await within(within(list).getByRole("radio", { name: /Work/ }).closest("li")!).findByText("Point 4's seat-settings text.")).toBeTruthy();
    expect(within(await findTerms(5)).getByText("Point 5's seat-settings text.")).toBeTruthy();
    fireEvent.click(within(list).getByRole("radio", { name: /Second/ }));
    expect(besideRow(list, /Second/)).toEqual(["Terms: Point 8"]);       // a choice changes neither
    expect(besideRow(list, /Work/)).toEqual(["Terms: Point 4", "Terms: Point 8"]);
    fireEvent.click(within(list).getByRole("radio", { name: /Corp/ }));
    expect(besideRow(list, /Corp/)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    fireEvent.click(within(list).getByRole("radio", { name: /API key · Anthropic/ }));
    const eleven = await findTerms(11);
    expect(within(eleven).getByText(/Anthropic's key/)).toBeTruthy();
    expect(within(eleven).queryByText(/Another vendor's key/)).toBeNull();
    const go = within(list).getByRole("button", { name: "Continue on this" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);                                         // a key asks for its cap
    fireEvent.change(within(list).getByRole("textbox", { name: "The cost cap in US dollars" }), { target: { value: "5" } });
    fireEvent.click(go);
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue-on", { state_version: 4, backend: { kind: "key", provider: "anthropic" }, cap_usd: 5 }]]));
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Continue on" })).toBeNull());   // the press done
    expect(terms(2)).toBeNull();
    list = await open();                                                    // opened again: its text opens again
    expect(await findTerms(2)).toBeTruthy();
    fireEvent.click(within(list).getByRole("radio", { name: /API key · DeepSeek/ }));
    expect(within(await findTerms(11)).getByText(/Another vendor's key/)).toBeTruthy();
  });

  it("Continue on...: point 4 for a task away from its home group, point 5 off and onto an Enterprise seat, and another vendor's key, each pressed through", async () => {
    fake.providers = [{ id: "deepseek", label: "DeepSeek", vendor: "DeepSeek", docs: "", model: "deepseek-flash", set: true }];
    const pressOn = async (radio: RegExp) => {
      fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
      const list = screen.getByRole("group", { name: "Continue on" });
      fireEvent.click(within(list).getByRole("radio", { name: radio }));
      return list;
    };
    const press = async (list: HTMLElement, n: number, body: Record<string, unknown>) => {
      fireEvent.click(within(list).getByRole("button", { name: "Continue on this" }));
      await vi.waitFor(() => expect(fake.presses()[n]).toEqual(["continue-on", { state_version: 4, ...body }]));
      await vi.waitFor(() => expect(screen.queryByRole("group", { name: "Continue on" })).toBeNull());
    };
    // a task away on Acme's Team seat, its home the personal plans: from there a personal account is another group, the
    // organisation's text beside each, and the Enterprise seat's beside that seat
    await show({ backend: { kind: "account", id: S.account } }, [B, A, S, E]);
    let list = await pressOn(/Second/);
    expect(besideRow(list, /Second/)).toEqual(["Terms: Point 4", "Terms: Point 8"]);
    expect(besideRow(list, /Home/)).toEqual(["Terms: Point 4", "Terms: Point 8"]);
    expect(besideRow(list, /Corp/)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    await press(list, 0, { backend: { kind: "account", id: A.account } });
    cleanup();
    fake.calls = [];
    // a task on the Enterprise seat: any other account takes it off one, point 5's text beside each without its seat
    // part, and no 4's
    await show({ backend: { kind: "account", id: E.account } }, [B, A, S, E]);
    list = await pressOn(/Second/);
    for (const row of [/Second/, /Home/, /Work/]) expect(besideRow(list, row)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    await vi.waitFor(() => expect(within(list).getAllByText("Point 5's own words.").length).toBe(3));
    expect(within(list).queryByText("Point 5's seat-settings text.")).toBeNull();
    await press(list, 0, { backend: { kind: "account", id: A.account } });
    cleanup();
    fake.calls = [];
    // a personal task onto the Enterprise seat, then onto another vendor's key with its cap, each pressed through
    await show({}, [B, A, S, E]);
    list = await pressOn(/Corp/);
    expect(within(await findTerms(5)).getByText("Point 5's seat-settings text.")).toBeTruthy();
    await press(list, 0, { backend: { kind: "account", id: E.account } });
    list = await pressOn(/API key · DeepSeek/);
    expect(within(await findTerms(11)).getByText(/Another vendor's key/)).toBeTruthy();
    fireEvent.change(within(list).getByRole("textbox", { name: "The cost cap in US dollars" }), { target: { value: "2" } });
    await press(list, 1, { backend: { kind: "key", provider: "deepseek" }, cap_usd: 2 });
  });

  it("Continue on...: a Team seat the app reads as an Enterprise organisation's has point 5 beside it, not 4, and off it point 5 beside each account of another group", async () => {
    // an organisation holding seats of both kinds of the
    // owner's reads as an Enterprise one; the app says so in each account's org_kind, the
    // seat's own kind staying grp_kind, and the page reads org_kind where it says what a move does
    const mixed = { ...S, org_kind: "enterprise" as const };
    await show({}, [B, A, mixed, E]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    let list = screen.getByRole("group", { name: "Continue on" });
    expect(besideRow(list, /Work/)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    expect(await within(within(list).getByRole("radio", { name: /Work/ }).closest("li")!).findByText("Point 5's seat-settings text.")).toBeTruthy();
    expect(besideRow(list, /Second/)).toEqual(["Terms: Point 8"]);
    cleanup();
    fake.calls = [];
    // a task on that seat: a move onto any account of another group is off an Enterprise organisation's seats, point 5
    // beside each without its seat part, and no 4's
    await show({ backend: { kind: "account", id: mixed.account } }, [B, A, mixed, E]);
    fireEvent.click(screen.getByRole("button", { name: "Continue on..." }));
    list = screen.getByRole("group", { name: "Continue on" });
    for (const row of [/Second/, /Home/]) expect(besideRow(list, row)).toEqual(["Terms: Point 5", "Terms: Point 8"]);
    await vi.waitFor(() => expect(within(list).getAllByText("Point 5's own words.").length).toBe(3));
  });

  it("custody's hand-off by itself opens point 12's text on its line, once, with its case; its line and the return's say from and onto which account", async () => {
    // as the app writes them: the hand-off to the custodian, and the task coming back onto the owner's account
    const line = { seq: 41, at: now() - 60, kind: "custody" as const, from: "account:default", to: "custodian", why: null, point: 12, cases: ["custody_cross"] };
    const back = { seq: 44, at: now() - 30, kind: "return" as const, from: "custodian", to: "account:default", why: null, point: null, cases: [] };
    await show({ moves: [line, back] });
    expect(within(await findTerms(12)).getByText("Custody's crossing: point 1's clauses.")).toBeTruthy();
    const [handed, came] = within(screen.getByRole("list", { name: "Its moves" })).getAllByRole("listitem");
    expect(handed.textContent).toContain("Custody handed it to your custodian, from Claude Code · Home");
    expect(handed.textContent).not.toContain("to custodian");
    expect(came.textContent).toContain("It came back, onto Claude Code · Home");
    expect(came.textContent).not.toContain("custodian to");
    cleanup();
    await show({ moves: [line, back] });                                    // the next visit: not again
    await settle();
    expect(terms(12)).toBeNull();
  });

  it("Continue at the bound on moves opens point 2 over it, and again at each press, and the press goes", async () => {
    await show({ state: "needs_owner", cause: "moves" });
    expect(await findTerms(2)).toBeTruthy();
    closeTerms(2);
    expect(terms(2)).toBeNull();                                            // closed by its own Close, it stays closed
    const waits = screen.getByRole("note", { name: "What waits on you" });
    fireEvent.click(within(waits).getByRole("button", { name: /Continue/ }));
    expect(await findTerms(2)).toBeTruthy();
    await vi.waitFor(() => expect(fake.presses()).toEqual([["continue", { state_version: 4 }]]));
  });

  it("a crossing, Back home and a move open their texts at once while the page is open, once each in this browser", async () => {
    const at = now() - 120;
    await show({});
    expect(terms(1)).toBeNull();
    fake.tasks = [task({ moves: [
      { seq: 11, at, kind: "home", from: "org:acme", to: "personal", why: null, point: 1, cases: ["back_home"] },
    ] })];
    fake.onAction = () => {};
    fireEvent.click(within(screen.getByRole("group", { name: "Its switches" })).getAllByRole("checkbox")[0]);   // any press reads it again
    const home = await findTerms(1);
    expect(within(home).getByText("This task started on your personal plans and goes back there.")).toBeTruthy();
    cleanup();                                                              // the next visit, in the same browser
    await show({ moves: [{ seq: 11, at, kind: "home", from: "org:acme", to: "personal", why: null, point: 1, cases: ["back_home"] }] });
    await settle();
    expect(terms(1)).toBeNull();
    const line = within(screen.getByRole("list", { name: "Its moves" })).getByRole("button", { name: "The terms: Point 1" });
    fireEvent.click(line);                                                  // its line keeps a link to it
    expect(await findTerms(1)).toBeTruthy();
  });

  it("a second browser, its storage empty, opens a 3-day-old event's text once and not a 10-day-old one; nothing of it reaches the app", async () => {
    await show({ moves: [
      { seq: 3, at: now() - 10 * DAY, kind: "relay", from: "account:default", to: `account:${A.account}`, why: null, point: 2, cases: [] },
      { seq: 9, at: now() - 3 * DAY, kind: "crossing", from: "personal", to: "org:acme", why: null, point: 1, cases: ["crossing"] },
    ] }, [B, A, S]);
    expect(await findTerms(1)).toBeTruthy();
    await settle();
    expect(terms(2)).toBeNull();
    cleanup();
    await show({ moves: [
      { seq: 3, at: now() - 10 * DAY, kind: "relay", from: "account:default", to: `account:${A.account}`, why: null, point: 2, cases: [] },
      { seq: 9, at: now() - 3 * DAY, kind: "crossing", from: "personal", to: "org:acme", why: null, point: 1, cases: ["crossing"] },
    ] }, [B, A, S]);
    await settle();
    expect(terms(1)).toBeNull();
    expect(terms(2)).toBeNull();
    // what this browser has opened is its own: the page made no call but these reads, each exactly as written here, so
    // that no event rides on one in any spelling
    const reads = [`/api/ade/agents/tasks?agent=${AGENT}`, "/api/ade/agents/accounts", `/api/ade/agents/account-records?agent=${AGENT}`,
                   "/api/ade/workspace/model-providers", "/api/ade/risks", `/api/ade/agents/tasks/${TASK}/journal?limit=100`,
                   `/api/ade/agents/tasks/${TASK}/account-record`];
    for (const c of fake.calls) expect([c.method, reads.includes(c.url) ? "one of the page's reads" : c.url]).toEqual(["GET", "one of the page's reads"]);
  });

  it("automatic resumes: onto another account of the group opens point 2, onto another group's 1 too, on the same account none, listed with its push", async () => {
    const at = now() - 60;
    // the journal as the app writes it: each automatic resume a task.continued by auto_resume, the one into another
    // group a task.crossed beside it, and their lines the view's moves of the same numbers
    const continued = (seq: number, from: string, to: string) => ({ seq, kind: "task.continued", run_id: "cccccccccccc", fence: 5, at,
      payload: { from: { kind: "account", id: from }, to: { kind: "account", id: to }, by: "auto_resume" } });
    // and the last, the task on S since 18 and 19, an automatic resume there, on the same account
    fake.journal = [continued(15, "default", A.account), continued(18, A.account, S.account),
                    { seq: 19, kind: "task.crossed", run_id: "cccccccccccc", fence: 5, at,
                      payload: { from_grp: "personal", to_grp: "org:acme", from: A.account, to: S.account, org: "acme", notice_at: at, home: null } },
                    continued(21, S.account, S.account)];
    await show({ backend: { kind: "account", id: S.account }, bound_org: S.org_id, moves: [
      { seq: 15, at, kind: "relay", from: "account:default", to: `account:${A.account}`, why: "its window reset first", point: 2, cases: [] },
      { seq: 18, at, kind: "relay", from: `account:${A.account}`, to: `account:${S.account}`, why: "its window reset first", point: 2, cases: [] },
      { seq: 19, at, kind: "crossing", from: "personal", to: "org:acme", why: null, point: 1, cases: ["crossing", "onto_seat"] },
    ] }, [B, A, S]);
    expect(await findTerms(1)).toBeTruthy();
    expect(screen.getAllByRole("dialog", { name: "Terms: Point 2" }).length).toBe(2);   // each move its own, once
    expect(await screen.findByText(/It went on by itself on the same account/)).toBeTruthy();
    expect(screen.getByText(/A task of yours continued/)).toBeTruthy();
    expect(screen.getAllByRole("dialog").length).toBe(3);                   // the resume on the same account opens none
  });

  it("a deleted task's record is listed under its agent; its Delete asks for its confirmation, point 15's text over it", async () => {
    fake.tasks = [];
    fake.records = [{
      task_id: TASK, agent_id: AGENT, repo_address: "https://github.com/o/r", branch: `pp/task-${TASK}`, title: "Ship the fix",
      deleted_at: now() - DAY, record_days: null,
      entries: [
        { seq: 1, kind: "attempt", run_id: "bbbbbbbbbbbb", step: 1, fence: 1, via: null, repo_address: "https://github.com/o/r", branch: `pp/task-${TASK}`,
          title: "Ship the fix", at: now() - 2 * DAY, accounts: [{ ref: { kind: "account", id: "default" }, email: "home@example.com", plan: "Claude Pro", organization: null, org_id: null }],
          reduced_at: null, statement: null },
        { seq: 2, kind: "deleted", run_id: null, step: 1, fence: null, via: null, repo_address: "https://github.com/o/r", branch: `pp/task-${TASK}`,
          title: "Ship the fix", at: now() - DAY, accounts: [], reduced_at: null, statement: null },
      ],
    }];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    const records = await screen.findByRole("group", { name: "Deleted tasks' account records" });
    const record = within(records).getByRole("group", { name: /The account record of Ship the fix, task 0123456789ab/ });
    fireEvent.click(within(record).getByRole("button", { name: /Which accounts did this work/ }));
    expect(within(record).getByText(keepingWords(null))).toBeTruthy();
    expect(within(record).getByText(/home@example.com, Claude Pro/)).toBeTruthy();
    fireEvent.click(within(record).getByRole("button", { name: /Delete this record/ }));
    expect(await findTerms(15)).toBeTruthy();
    const confirm = await screen.findByRole("group", { name: "Deleting this task's account record" });
    fireEvent.change(within(confirm).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));      // with point 15's text still open
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "DELETE" && c.url === `/api/ade/agents/tasks/${TASK}/account-record`)).toBe(true));
    expect(fake.calls.find((c) => c.method === "DELETE")!.body).toEqual({ confirmation: "the-confirmation" });
    expect(fake.calls.find((c) => c.method === "POST" && c.url === "/api/account/confirm")!.body).toEqual({ password: "pw", change: `task-account-record:${TASK}` });
  });
});

// ---- the rest of the task's page ----------------------------------------------------------------------------------------

describe("a task's page", () => {
  it("shows its steps with their evidence, its attempts with their backends, its checkpoints and what they left out", async () => {
    const opened = vi.fn();
    fake.tasks = [task({
      step: 2,
      steps: [{ n: 1, objective: "Write the fix", check: "pytest -q", state: "done", evidence: "checked", checkpoint_id: "f".repeat(32), attempts: 1, reopened: false },
              { n: 2, objective: "Ship it", check: null, state: "doing", evidence: null, checkpoint_id: null, attempts: 1, reopened: false }],
      attempts: [attempt({ run_id: "dddddddddddd", step: 2, attempt: 1, fence: 2, backend: { kind: "account", id: A.account }, state: "working" }),
                 attempt({ run_id: "bbbbbbbbbbbb", step: 1, attempt: 1, fence: 1, resume: { outcome: "passed", check: null, found: null } })],
      checkpoints: [{ id: "f".repeat(32), n: 1, step: 1, kind: "step", head: "a".repeat(40), digest: "9".repeat(64), created_at: now() - 600, left_out: ["notes.local"] }],
    })];
    fake.accounts = [B, A];
    render(<AgentTask agent={agent()} onOpenRun={opened} />);
    const steps = await screen.findByRole("list", { name: "Its steps" });
    expect(within(steps).getAllByRole("listitem")[0].textContent).toContain("checked · aaaaaaa");
    const attempts = screen.getByRole("list", { name: "Its attempts" });
    const rows = within(attempts).getAllByRole("listitem");
    expect(rows[0].textContent).toContain("step 2, attempt 1");             // newest first
    expect(rows[0].textContent).toContain("Claude Code · Second");
    expect(rows[1].textContent).toContain("resume check passed");
    fireEvent.click(within(attempts).getByRole("button", { name: "Open attempt 1 of step 2" }));
    expect(opened).toHaveBeenCalledWith("dddddddddddd");
    fireEvent.click(screen.getByText(/Checkpoints \(1\)/));
    expect(screen.getByText("Left out: notes.local")).toBeTruthy();
  });

  it("lists its decisions, and answers the newest attempt's question on the task, kept as a decision", async () => {
    fake.tasks = [task({ attempts: [attempt({ run_id: "eeeeeeeeeeee", attempt: 2, fence: 3 })],
                         decisions: [{ n: 1, text: "Use the staging database.", author: "owner", at: now() - DAY }] })];
    const asking = { run_id: "eeeeeeeeeeee", agent_id: AGENT, state: "done", question: "Which branch do I ship from?" } as unknown as Agent["runs"][number];
    render(<AgentTask agent={agent({ runs: [asking] })} onOpenRun={() => {}} />);
    const decisions = await screen.findByRole("list", { name: "Its decisions" });
    expect(within(decisions).getByText(/Use the staging database\./)).toBeTruthy();
    expect(screen.getByText("Which branch do I ship from?")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Your answer, kept as a decision of the task" }), { target: { value: "main" } });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["answer", { state_version: 4, text: "main" }]]));
  });

  it("a task that ended keeps its moves, a move's text opening once there as on an open task, and its account record", async () => {
    fake.tasks = [task({ state: "done", moves: [{ seq: 31, at: now() - 120, kind: "relay", from: "account:default", to: `account:${A.account}`, why: null, point: 2, cases: [] }] })];
    fake.records = [{ task_id: TASK, agent_id: AGENT, repo_address: null, branch: `pp/task-${TASK}`, title: "Ship the fix", deleted_at: null, record_days: 90, entries: [] }];
    fake.accounts = [B, A];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    let ended = await screen.findByRole("listitem", { name: "Task that ended: Ship the fix" });
    expect(await findTerms(2)).toBeTruthy();
    expect(within(ended).getByRole("list", { name: "Its moves" }).textContent).toContain("Claude Code · Second");
    expect(await within(ended).findByRole("group", { name: /The account record of Ship the fix/ })).toBeTruthy();
    cleanup();
    forgetPage();                                                           // the next visit, in the same browser: not again
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    ended = await screen.findByRole("listitem", { name: "Task that ended: Ship the fix" });
    const link = await within(ended).findByRole("button", { name: "The terms: Point 2" });
    await settle();
    expect(terms(2)).toBeNull();
    fireEvent.click(link);                                                  // its line keeps a link to it
    expect(await findTerms(2)).toBeTruthy();
  });

  it("starts the New task form on the account an agent with none begins on, listed and chosen though not confirmed, and says so", async () => {
    startsOn("default");                                                    // new sessions start on the default account
    fake.accounts = [{ ...B, state: "reported" }, A];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const form = screen.getByRole("form", { name: "New task" });
    const radios = within(form).getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((r) => r.checked)).toEqual([true, false]);
    expect(within(radios[0].closest("div")!).getByText(/not confirmed as yours/)).toBeTruthy();
    expect(await within(radios[0].closest("div")!).findByRole("dialog", { name: "Terms: Point 8" })).toBeTruthy();
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Goal" }), { target: { value: "G" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Step 1's objective" }), { target: { value: "S" } });
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    await vi.waitFor(() => expect(fake.calls.find((c) => c.method === "POST" && c.url === "/api/ade/agents/tasks")?.body?.backend).toEqual({ kind: "account", id: "default" }));
  });

  // a task of an agent with no account begins on the account new sessions start on then, by its id or as
  // default, and the page shows which; the default's is the right answer only where new sessions start there
  const newTaskOf = async (over: Partial<Agent> = {}) => {
    render(<AgentTask agent={agent(over)} onOpenRun={() => {}} />);
    await vi.waitFor(() => expect(fake.calls.some((c) => c.url === "/api/ade/agents/accounts")).toBe(true));
    await settle();
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    return screen.getByRole("form", { name: "New task" });
  };
  const fillIn = (form: HTMLElement) => {
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Goal" }), { target: { value: "G" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Step 1's objective" }), { target: { value: "S" } });
  };
  const radio = (form: HTMLElement, name: RegExp) => within(form).getByRole("radio", { name }) as HTMLInputElement;
  const made = () => fake.calls.find((c) => c.method === "POST" && c.url === "/api/ade/agents/tasks");

  it("starts the New task form of an agent with no account on the account new sessions start on, names it, and sends it", async () => {
    startsOn(S.account);                                     // new sessions start on Work, not on the default account
    fake.accounts = [B, A, S];
    const form = await newTaskOf();
    expect(radio(form, /Work/).checked).toBe(true);
    expect(radio(form, /Home/).checked).toBe(false);
    expect(within(form).getByRole("note", { name: "The account it begins on" }).textContent).toBe(
      "This agent has no account of its own: a task of its begins on the account new sessions start on now, Claude Code · Work (work@example.com).");
    fillIn(form);
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    await vi.waitFor(() => expect(made()?.body?.backend).toEqual({ kind: "account", id: S.account }));
  });

  it("asks the owner to choose, in the New task form of an agent with no account, while which account new sessions start on is not known", async () => {
    fake.accounts = [B, A];                                  // the owner's accounts read; the workspace's not (none in the store)
    const form = await newTaskOf();
    expect((within(form).getAllByRole("radio") as HTMLInputElement[]).map((r) => r.checked)).toEqual([false, false]);
    expect(within(form).getByRole("note", { name: "The account it begins on" }).textContent).toBe(
      "This agent has no account of its own, and which account new sessions start on is not known until the workspace's accounts are read: choose the one this task begins on.");
    expect(within(form).queryByText(/A task starts on an account confirmed as yours/)).toBeNull();   // none chosen: no pointer
    fillIn(form);
    const make = within(form).getByRole("button", { name: /Make the task/ }) as HTMLButtonElement;
    expect(make.disabled).toBe(true);
    fireEvent.submit(form);                                  // Enter in a field: nothing sent either
    await settle();
    expect(made()).toBeUndefined();
    fireEvent.click(radio(form, /Second/));
    expect(make.disabled).toBe(false);
    fireEvent.click(make);
    await vi.waitFor(() => expect(made()?.body?.backend).toEqual({ kind: "account", id: A.account }));
  });

  it("starts an agent with an account of its own on that one, whichever new sessions start on, and says nothing of it", async () => {
    startsOn(S.account);
    fake.accounts = [B, A, S];
    let form = await newTaskOf({ account: A.account });
    expect(radio(form, /Second/).checked).toBe(true);
    expect(radio(form, /Work/).checked).toBe(false);
    expect(within(form).queryByRole("note", { name: "The account it begins on" })).toBeNull();
    cleanup();
    // "default" among them, should the app keep it as a value of an agent's own (today none, "" and "default" are
    // one)
    form = await newTaskOf({ account: "default" });
    expect(radio(form, /Home/).checked).toBe(true);
    expect(radio(form, /Work/).checked).toBe(false);
    expect(within(form).queryByRole("note", { name: "The account it begins on" })).toBeNull();
  });

  it("offers a new task's first grant once it is made, where it has a repository, and closes as before once that is done", async () => {
    // this page's browser has no passkeys (jsdom): it says the task is made without a grant, and where one is given
    fake.accounts = [B, A, S];
    fake.madeWith = { grant: null, grant_widenings: [],
                      grant_for: { repo: "demo", branch: "pp/task-fedcba9876543210fedcba9876543210", ask: "sha256:" + "ab".repeat(32) } };
    const form = await newTaskOf({ account: A.account });
    fillIn(form);
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    // waited for within the case's own time, so that a page that never offers it fails here, on what it reads
    const first = await screen.findByRole("region", { name: "Its first grant" }, { timeout: 3000 });
    expect(first.textContent).toContain("The task is made without a grant: a grant is signed with one of your passkeys, in a browser at");
    expect(made()).toBeTruthy();                                       // made, whatever its grant
    fireEvent.click(within(first).getByRole("button", { name: "Done" }));
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: "Its first grant" })).toBeNull());
    expect(screen.queryByRole("form", { name: "New task" })).toBeNull();
    // a task with no repository needs none: the form closes as it is made (an agent with no open task, as one with an
    // open task offers no New task)
    cleanup();
    fake.tasks = [];
    fake.madeWith = { grant: null, grant_widenings: [], grant_for: { repo: null, branch: "pp/task-x", ask: "sha256:" + "ab".repeat(32) } };
    const again = await newTaskOf({ account: A.account });
    fillIn(again);
    fireEvent.click(within(again).getByRole("button", { name: /Make the task/ }));
    await vi.waitFor(() => expect(screen.queryByRole("form", { name: "New task" })).toBeNull());
    expect(screen.queryByRole("region", { name: "Its first grant" })).toBeNull();
  });

  it("reads again, as the New task form of an agent with no account opens, which account new sessions start on, and follows it until the owner chooses", async () => {
    const refreshAccounts = vi.fn(async () => {});
    useStore.setState({ ws: { enabled: true, running: true } as never, refreshAccounts });
    startsOn("default");
    fake.accounts = [B, A, S];
    const form = await newTaskOf();
    expect(refreshAccounts).toHaveBeenCalledWith(false);
    expect(radio(form, /Home/).checked).toBe(true);
    act(() => startsOn(S.account));                         // the workspace's answer: new sessions start on Work now
    expect(radio(form, /Work/).checked).toBe(true);
    expect(within(form).getByRole("note", { name: "The account it begins on" }).textContent).toContain("Claude Code · Work (work@example.com)");
    fireEvent.click(radio(form, /Second/));                  // the owner's own choice stays, whatever is said after it
    act(() => startsOn("default"));
    expect(radio(form, /Second/).checked).toBe(true);
    cleanup();
    refreshAccounts.mockClear();
    await newTaskOf({ account: A.account });                 // an agent with an account of its own: nothing to ask
    expect(refreshAccounts).not.toHaveBeenCalled();
    cleanup();
    useStore.setState({ ws: { enabled: true, running: false } as never });
    await newTaskOf();                                       // the workspace stopped: nothing it could answer
    expect(refreshAccounts).not.toHaveBeenCalled();
  });

  it("says, while the account chosen is not confirmed, to confirm it in Connections, with a button that opens Connections; nothing for a confirmed one", async () => {
    startsOn("default");
    fake.accounts = [{ ...B, state: "reported" }, A];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const form = screen.getByRole("form", { name: "New task" });
    expect(within(form).getByText(/A task starts on an account confirmed as yours, and this one is not confirmed yet\. Confirm it in Connections first\./)).toBeTruthy();
    expect(useStore.getState().connectionsOpen).toBe(false);
    fireEvent.click(within(form).getByRole("button", { name: "Open Connections" }));
    expect(useStore.getState().connectionsOpen).toBe(true);
    // the confirmed account chosen instead: nothing to say, and no button
    fireEvent.click(within(form).getAllByRole("radio")[1]);
    expect(within(form).queryByText(/not confirmed yet/)).toBeNull();
    expect(within(form).queryByRole("button", { name: "Open Connections" })).toBeNull();
  });

  it("names, over an account a task cannot start on, its own standing: waiting to join, changed since confirmed, or unconfirmable", async () => {
    const open = async (own: AccountOwnerView) => {
      startsOn("default");
      fake.accounts = [own, A];
      render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
      return within(screen.getByRole("form", { name: "New task" })).getByRole("status");
    };
    const held = await open({ ...B, state: "held", in_relays: false, hold: { id: "h1", kind: "join", takes_effect_at: now() + 600 } });
    expect(held.textContent).toMatch(/^A task starts on an account confirmed as yours, and this one is confirmed and waits to join your tasks' relay \(at \d\d:\d\d\): a task cannot start on it until then\. Connections shows the wait, with Cancel\.Open Connections$/);
    expect(within(held).getByRole("button", { name: "Open Connections" })).toBeTruthy();
    cleanup();
    // a wait more than an hour away is said with its day; one the app gave no time for, without one, and without the
    // Cancel the row does not show then
    const far = await open({ ...B, state: "held", in_relays: false, hold: { id: "h1", kind: "join", takes_effect_at: now() + 90000 } });
    expect(far.textContent).toMatch(/waits to join your tasks' relay \(at \d{1,2} [A-Z][a-z]{2}(?: \d{4})?, \d\d:\d\d\): a task cannot start/);
    cleanup();
    const untimed = await open({ ...B, state: "held", in_relays: false, hold: null });
    expect(untimed.textContent).toMatch(/waits to join your tasks' relay: a task cannot start on it until then\. Connections shows the wait\.Open Connections$/);
    cleanup();
    const changed = await open({ ...B, state: "changed", in_relays: false });
    expect(changed.textContent).toContain("this one's identity changed since you confirmed it. Confirm it again in Connections first.");
    cleanup();
    const mismatch = await open({ ...B, state: "reported", in_relays: false, mismatch: "email" });
    expect(mismatch.textContent).toContain("this one cannot be confirmed: its sign-in's own record disagrees with what its start reported, on its email. Connections says more.");
    expect(mismatch.textContent).not.toContain("not confirmed yet");
    cleanup();
    // a held account whose read again found a mismatch: the mismatch, which decides it (the app ends the wait without
    // the account joining), before the wait, as on its row in Connections
    const heldMismatch = await open({ ...B, state: "held", in_relays: false, mismatch: "email", hold: { id: "h1", kind: "join", takes_effect_at: now() + 600 } });
    expect(heldMismatch.textContent).toContain("this one cannot be confirmed: its sign-in's own record disagrees with what its start reported, on its email. Connections says more.");
    expect(heldMismatch.textContent).not.toContain("waits to join");
    // and its own label in the list says the same, not that it joins when its hold ends
    const label = within(screen.getByRole("form", { name: "New task" })).getByRole("radio", { name: /Home/ }).closest("label")!.textContent!;
    expect(label).toContain("cannot be confirmed as yours: its sign-in's own record disagrees with what its start reported, on its email; in no relay");
    expect(label).not.toContain("confirmed: it joins");
    cleanup();
    // what the app found, said as it is and not always as a disagreement (tasks.py's identity_mismatch): a start that
    // did not report both an email and a plan (the app's mismatch "report"), in the pointer and the label alike
    const nothing = await open({ ...B, state: "reported", in_relays: false, report: null, mismatch: "report" });
    expect(nothing.textContent).toContain("this one cannot be confirmed: its start did not report both the email and the plan the app confirms it by. Connections says more.");
    expect(nothing.textContent).not.toContain("disagrees");
    expect(within(screen.getByRole("form", { name: "New task" })).getByRole("radio", { name: /Home/ }).closest("label")!.textContent)
      .toContain("cannot be confirmed as yours: its start did not report both the email and the plan the app confirms it by; in no relay");
    cleanup();
    // and the app's other fields, each in its own words: its record not read with it, no organisation id, a plan
    for (const [field, words] of [["auth status", "its sign-in's own record could not be read with it"],
                                  ["orgId", "its sign-in's own record gives no organisation id the app can keep"],
                                  ["plan", "its plan is one the app does not know, or its sign-in's own record names another"]]) {
      const said = await open({ ...B, state: "reported", in_relays: false, mismatch: field });
      expect(said.textContent).toContain(`this one cannot be confirmed: ${words}. Connections says more.`);
      cleanup();
    }
    // an agent on an account the workspace no longer lists and the app has no row for: Connections has nothing of it to
    // confirm, so the pointer says to choose another or move the agent, with no Open Connections
    useStore.setState({ accounts: { claude: { active: "default", accounts: [{ id: "default", name: "Default", created_at: null, sessions: 0, status: null, usage: null }] },
                                    codex: { active: "default", accounts: [] } } });
    fake.accounts = [A];
    render(<AgentTask agent={agent({ account: "cccccccccccc" })} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const gone = within(screen.getByRole("form", { name: "New task" })).getByRole("status");
    expect(gone.textContent).toBe("A task starts on an account confirmed as yours, and this one is no longer in the workspace: choose another here, or move the agent off it in its form.");
    expect(within(gone).queryByRole("button", { name: "Open Connections" })).toBeNull();
    cleanup();
    // one the app has a row for, though the workspace no longer lists it, is in Connections: said by its standing
    fake.accounts = [owner("cccccccccccc", "Third", "Claude Max", { state: "held", in_relays: false, hold: { id: "h3", kind: "join", takes_effect_at: now() + 600 } }), A];
    render(<AgentTask agent={agent({ account: "cccccccccccc" })} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const kept = within(screen.getByRole("form", { name: "New task" })).getByRole("status");
    expect(kept.textContent).toContain("this one is confirmed and waits to join your tasks' relay");
    expect(within(kept).getByRole("button", { name: "Open Connections" })).toBeTruthy();
    cleanup();
    // and while the store has not read the workspace's list, the page cannot tell: said as not confirmed yet
    useStore.setState({ accounts: null });
    fake.accounts = [A];
    render(<AgentTask agent={agent({ account: "cccccccccccc" })} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const unknown = within(screen.getByRole("form", { name: "New task" })).getByRole("status");
    expect(unknown.textContent).toContain("this one is not confirmed yet. Confirm it in Connections first.");
    expect(within(unknown).getByRole("button", { name: "Open Connections" })).toBeTruthy();
    cleanup();
    // the owner's accounts read, and the app has no row at all (a first task, before any account is read): the account
    // the form starts on, the one new sessions start on, is not confirmed, and the pointer says so
    startsOn("default");
    fake.accounts = [];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const bare = within(screen.getByRole("form", { name: "New task" })).getByRole("status");
    expect(bare.textContent).toContain("A task starts on an account confirmed as yours, and this one is not confirmed yet. Confirm it in Connections first.");
    expect(within(bare).getByRole("button", { name: "Open Connections" })).toBeTruthy();
    cleanup();
    // the owner's accounts not read (the app away): the page knows no account's standing, so it says nothing, and the
    // app's own refusal stands
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url) === "/api/ade/agents/accounts"
      ? (fake.calls.push({ method: "GET", url: String(url) }), new Response(JSON.stringify({ detail: "The app is starting." }), { status: 503 }))
      : fake.answer(String(url), init))));
    startsOn("default");
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    await vi.waitFor(() => expect(fake.calls.some((c) => c.url === "/api/ade/agents/accounts")).toBe(true));
    await settle();
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const unread = screen.getByRole("form", { name: "New task" });
    expect(within(unread).getAllByRole("radio")).toHaveLength(1);
    expect(within(unread).queryByText(/A task starts on an account confirmed as yours/)).toBeNull();
    expect(within(unread).queryByRole("button", { name: "Open Connections" })).toBeNull();
  });

  it("says nothing of it on a task's Edit form, where the account cannot be changed", async () => {
    const pane = await show({}, [{ ...B, state: "reported" }, A]);
    fireEvent.click(within(pane).getByRole("button", { name: "Edit" }));
    expect(within(pane).getByRole("textbox", { name: "Title" })).toBeTruthy();
    expect(within(pane).queryByText(/A task starts on an account confirmed as yours/)).toBeNull();
    expect(within(pane).queryByRole("button", { name: "Open Connections" })).toBeNull();
  });

  it("lists the agent's account in the New task form though the owner's accounts hold no row for it, chosen and sent", async () => {
    startsOn("default");
    fake.accounts = [A];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    await vi.waitFor(() => expect(fake.calls.some((c) => c.url === "/api/ade/agents/accounts")).toBe(true));
    await settle();
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    const form = screen.getByRole("form", { name: "New task" });
    const radios = within(form).getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((r) => r.checked)).toEqual([true, false]);
    expect(radios[0].closest("label")!.textContent).toContain("Claude Code · Default");
    expect(radios[0].closest("label")!.textContent).toContain("not confirmed as yours");   // the accounts read, and it is not among them
    expect(radios[1].closest("label")!.textContent).toContain("Second");
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Goal" }), { target: { value: "G" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Step 1's objective" }), { target: { value: "S" } });
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    await vi.waitFor(() => expect(fake.calls.find((c) => c.method === "POST" && c.url === "/api/ade/agents/tasks")?.body?.backend).toEqual({ kind: "account", id: "default" }));
  });

  it("says in the New task form each account's state as the app gives it: a held one joins at its hold's end, a changed one is confirmed again, and nothing while the accounts are not read", async () => {
    const formFor = async (over: Partial<Agent>) => {
      render(<AgentTask agent={agent(over)} onOpenRun={() => {}} />);
      await vi.waitFor(() => expect(fake.calls.some((c) => c.url === "/api/ade/agents/accounts")).toBe(true));
      await settle();
      fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
      return screen.getByRole("form", { name: "New task" });
    };
    const said = (form: HTMLElement, name: RegExp) => within(form).getByRole("radio", { name }).closest("label")!.textContent!;
    fake.accounts = [B, { ...A, state: "held", hold: { id: "k".repeat(32), kind: "task.account_own", takes_effect_at: now() + 120 } }];
    let form = await formFor({ account: A.account });
    expect(said(form, /Second/)).toContain("confirmed: it joins your tasks' relay when its hold ends");
    expect(said(form, /Second/)).not.toContain("not confirmed");
    expect(said(form, /Home/)).not.toMatch(/confirm/);                     // a confirmed one: nothing to say
    cleanup();
    fake.accounts = [B, { ...A, state: "changed" }];
    form = await formFor({ account: A.account });
    expect(said(form, /Second/)).toContain("its identity changed: in no relay until you confirm it again in Connections");
    cleanup();
    // the accounts' read failing: the agent's account stands in by its name alone, nothing said of its state
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url) === "/api/ade/agents/accounts"
      ? (fake.calls.push({ method: "GET", url: String(url) }), new Response(JSON.stringify({ detail: "The app is starting." }), { status: 503 }))
      : fake.answer(String(url), init))));
    startsOn("default");
    form = await formFor({});
    const radios = within(form).getAllByRole("radio") as HTMLInputElement[];
    expect(radios.length).toBe(1);
    expect(radios[0].checked).toBe(true);
    expect(radios[0].closest("label")!.textContent).toBe("Claude Code · Default");
  });

  it("a task that ended has Delete, two-step; its account record stays", async () => {
    fake.tasks = [task({ state: "done" })];
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    const ended = await screen.findByRole("list", { name: "Tasks that ended" });
    expect(screen.queryByRole("region", { name: "Task: Ship the fix" })).toBeNull();
    expect(screen.getByRole("button", { name: /New task/ })).toBeTruthy();    // no open task: a new one may be made
    fireEvent.click(within(ended).getByRole("button", { name: /Delete/ }));
    fireEvent.click(within(ended).getByRole("button", { name: /Delete the task\? Its account record stays/ }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "DELETE" && c.url === `/api/ade/agents/tasks/${TASK}`)).toBe(true));
    expect(fake.calls.find((c) => c.method === "DELETE")!.body).toEqual({ state_version: 4 });
  });

  it("a live task's account record says how long this deployment keeps it, and offers no Delete before the task is deleted", async () => {
    fake.records = [{ task_id: TASK, agent_id: AGENT, repo_address: null, branch: `pp/task-${TASK}`, title: "Ship the fix", deleted_at: null, record_days: 90, entries: [] }];
    const pane = await show({});
    const record = await within(pane).findByRole("group", { name: /The account record of Ship the fix/ });
    fireEvent.click(within(record).getByRole("button", { name: /Which accounts did this work/ }));
    expect(within(record).getByText("This deployment keeps this record for 90 days after the task is deleted.")).toBeTruthy();
    expect(within(record).getByText("No entry yet.")).toBeTruthy();          // the record of a task no attempt has run for
    expect(within(record).queryByRole("button", { name: /Delete this record/ })).toBeNull();
  });

  it("its account confirmed again into another group: the journal's line says why, read again when the account's group changes, and the record names the account before and now", async () => {
    // the app writes task.account_regrouped
    // and a regrouped entry, and changes none of an offered task's row, so its state_version and moves stay as they were
    const why = "Its account was confirmed again, now as a seat of a Team organisation: from now the task's attempts on it run on that plan's usage, because of that confirmation, not a press or the relay. Its binding changed as a press from the old group onto the new one would. Its relay takes that account now.";
    fake.records = [{ task_id: TASK, agent_id: AGENT, repo_address: null, branch: `pp/task-${TASK}`, title: "Ship the fix", deleted_at: null, record_days: 90, entries: [] }];
    const pane = await show({}, [B, A, S]);
    const record = await within(pane).findByRole("group", { name: /The account record of Ship the fix/ });
    fireEvent.click(within(record).getByRole("button", { name: /Which accounts did this work/ }));
    expect(within(record).getByText("No entry yet.")).toBeTruthy();
    await settle();
    expect(within(pane).queryByText(why)).toBeNull();
    const at = now() - 60;
    fake.journal = [{ seq: 7, kind: "task.account_regrouped", run_id: null, fence: null, at, payload: {
      account: "account:default", from_grp: "personal", from_kind: "personal", to_grp: "org:acme", to_kind: "team",
      bound: { bound_org: "acme" }, left_out: null, why } }];
    const was = { ref: { kind: "account" as const, id: "default" }, email: "home@example.com", plan: "Claude Pro", organization: null, org_id: null };
    fake.records[0].entries = [{ seq: 3, kind: "regrouped", run_id: null, step: 1, fence: null, via: null, repo_address: null, branch: `pp/task-${TASK}`,
      title: "Ship the fix", at, accounts: [was, { ...was, plan: "Claude Team", organization: "Acme", org_id: "org-1" }], reduced_at: null, statement: null }];
    fake.accounts = [owner("default", "Home", "Claude Team"), A, S];              // the task's account, in Acme's group now
    const reads = (end: string) => fake.calls.filter((c) => c.url.split("?")[0] === `/api/ade/agents/tasks/${TASK}/${end}`).length;
    const [journal, kept] = [reads("journal"), reads("account-record")];
    // a reload that changes nothing of the task: its state_version 4 as before, no move
    fireEvent.change(screen.getByRole("combobox", { name: "Between steps" }), { target: { value: "wait" } });
    expect(await within(pane).findByText(why)).toBeTruthy();
    expect([reads("journal") > journal, reads("account-record") > kept]).toEqual([true, true]);
    expect(within(record).getByText(/its account confirmed again, into another group or as another kind of seat, step 1: home@example\.com, Claude Pro to home@example\.com, Claude Team, Acme$/)).toBeTruthy();
    expect(within(record).queryByText(/org-1/)).toBeNull();                   // two organisations' names, not the same
    // read again when the group alone changes (another Team organisation), when the kind of seat alone does, and when
    // the kind its organisation is read as alone does, as another account's confirmation changes it (a reread), its
    // Team seat of an organisation now read as an Enterprise one
    for (const [seq, over] of [[8, { grp: "org:other" }], [9, { grp: "org:other", grp_kind: "enterprise" as const }],
                               [10, { grp: "org:other", grp_kind: "team" as const }],
                               [11, { grp: "org:other", grp_kind: "team" as const, org_kind: "enterprise" as const }]] as const) {
      const next = `${why} (${seq})`;
      fake.journal = [...fake.journal, { ...fake.journal[0], seq, payload: { ...fake.journal[0].payload, why: next } }];
      fake.accounts = [owner("default", "Home", "Claude Team", over), A, S];
      fireEvent.change(screen.getByRole("combobox", { name: "Between steps" }), { target: { value: "wait" } });
      expect(await within(pane).findByText(next)).toBeTruthy();
    }
  });

  it("every task.account_regrouped line of the journal is listed after the task's moves and automatic resumes, newest first as the journal gives them", async () => {
    // the fake serves the journal as the app's journal_of gives it, newest first (served oldest first, a page that
    // kept one line would pass), and every line is kept; with an automatic resume here, as with none a page placing the
    // lines before the resumes would pass
    const at = now() - 600;
    const line = (seq: number, word: string) => ({ seq, kind: "task.account_regrouped", run_id: null, fence: null, at: at + seq, payload: {
      account: "account:default", from_grp: "personal", from_kind: "personal", to_grp: "org:acme", to_kind: "team", bound: {},
      left_out: null, why: `The ${word} confirmation's words.` } });
    fake.journal = [line(3, "first"), line(6, "second"),
                    { seq: 7, kind: "task.continued", run_id: "cccccccccccc", fence: 5, at: at + 7, payload: { from: { kind: "account", id: "default" }, to: { kind: "account", id: A.account }, by: "relay" } },
                    { seq: 8, kind: "task.continued", run_id: "dddddddddddd", fence: 6, at: at + 8, payload: { from: { kind: "account", id: A.account }, to: { kind: "account", id: A.account }, by: "auto_resume" } },
                    line(9, "third")];
    const pane = await show({ moves: [{ seq: 7, at: at + 7, kind: "relay", from: "account:default", to: `account:${A.account}`, why: "its window reset first", point: 2, cases: [] }] }, [B, A, S]);
    const list = await within(pane).findByRole("list", { name: "Its moves" });
    await within(list).findByText("The first confirmation's words.");
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent ?? "");
    const label = (t: string) => ["first", "second", "third"].find((w) => t.includes(`The ${w} confirmation`))
      ?? (t.includes("It went on by itself on the same account") ? "resume" : t.includes("its window reset first") ? "move" : t);
    expect(items.map(label)).toEqual(["move", "resume", "third", "second", "first"]);
  });

  it("a regrouping's entry may be another kind of seat, and two organisations of the same name are each said with its id", async () => {
    // a change of seat kind within one organisation stays in its group, so the entry is not
    // only "into another group"; and two organisations of the same name and plan read as one ("Zed to Zed")
    const seat = { ref: { kind: "account" as const, id: "default" }, email: "home@example.com", plan: "Claude Team", organization: "Zed", org_id: "org-z" };
    const entry = (seq: number, kind: AccountRecordEntry["kind"], accounts: AccountSnapshot[], run_id: string | null = null) => ({
      seq, kind, run_id, step: 1, fence: run_id ? 2 : null, via: null, repo_address: null, branch: `pp/task-${TASK}`, title: "Ship the fix",
      at: now() - 60, accounts, reduced_at: null, statement: null });
    fake.records = [{ task_id: TASK, agent_id: AGENT, repo_address: null, branch: `pp/task-${TASK}`, title: "Ship the fix", deleted_at: null, record_days: 90, entries: [
      entry(2, "attempt", [seat], "bbbbbbbbbbbb"),                                              // one account: no other to match
      entry(3, "regrouped", [seat, { ...seat, plan: "Claude Enterprise" }]),                   // another kind of seat of Zed
      entry(4, "regrouped", [{ ...seat, plan: "Claude Enterprise" }, { ...seat, org_id: "org-y" }]),  // onto another Zed's seat
      entry(5, "crossing", [{ ...seat, plan: "Claude Pro", organization: null, org_id: "personal-1" }, seat], "cccccccccccc"),
      entry(6, "regrouped", [{ ...seat, organization: "Acme", org_id: "org-a" }, seat]),         // onto Zed's seat from Acme's
    ] }];
    const pane = await show({}, [B, A, S]);
    const record = await within(pane).findByRole("group", { name: /The account record of Ship the fix/ });
    fireEvent.click(within(record).getByRole("button", { name: /Which accounts did this work/ }));
    const regrouped = "its account confirmed again, into another group or as another kind of seat, step 1";
    expect(within(record).getByText(`${regrouped}: home@example.com, Claude Team, Zed (org-z) to home@example.com, Claude Enterprise, Zed (org-z)`, { exact: false })).toBeTruthy();
    expect(within(record).getByText(`${regrouped}: home@example.com, Claude Enterprise, Zed (org-z) to home@example.com, Claude Team, Zed (org-y)`, { exact: false })).toBeTruthy();
    // the controls: names that differ, a personal plan's with none among them, and an entry of one account, say no id.
    // two organisations of different names, both named, in a regrouping too
    expect(within(record).getByText(/a crossing, step 1, run cccccccccccc: home@example\.com, Claude Pro to home@example\.com, Claude Team, Zed$/)).toBeTruthy();
    expect(within(record).getByText(/an attempt, step 1, run bbbbbbbbbbbb: home@example\.com, Claude Team, Zed$/)).toBeTruthy();
    expect(within(record).getByText(new RegExp(`${regrouped}: home@example\\.com, Claude Team, Acme to home@example\\.com, Claude Team, Zed$`))).toBeTruthy();
    expect(within(record).queryByText(/org-a/)).toBeNull();
  });

  it("Start, Mark it done and End it each send the version the page read, and the edit form's change sends it with every field", async () => {
    await show({ state: "ready" });
    fireEvent.click(screen.getByRole("button", { name: /Start/ }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["start", { state_version: 4 }]]));
    cleanup();
    fake.calls = [];
    await show({ state: "suspended" });
    fireEvent.click(screen.getByRole("button", { name: "Mark it done" }));
    expect(fake.presses()).toEqual([]);                                     // the first press only arms it
    fireEvent.click(screen.getByRole("button", { name: "Mark the task done?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["end", { state_version: 4, outcome: "done" }]]));
    fireEvent.click(screen.getByRole("button", { name: "End it" }));
    fireEvent.click(screen.getByRole("button", { name: "End the task, stopped?" }));
    await vi.waitFor(() => expect(fake.presses()).toEqual([["end", { state_version: 4, outcome: "done" }], ["end", { state_version: 4, outcome: "stopped" }]]));
    cleanup();
    fake.calls = [];
    const pane = await show({ protected: ["tests/**"], external: ["make deploy"], between_steps: "any" });
    fireEvent.click(within(pane).getByRole("button", { name: "Edit" }));
    const form = within(pane).getByRole("form", { name: "Edit the task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "Ship it" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(fake.calls.find((c) => c.method === "PUT")).toEqual({ method: "PUT", url: `/api/ade/agents/tasks/${TASK}`, body: {
      state_version: 4, title: "Ship it", goal: "Fix the bug and ship it.", steps: [{ objective: "Write the fix", check: "pytest -q" }],
      protected: ["tests/**"], external: ["make deploy"], between_steps: "any" } });
  });

  it("changes Between steps against the version it read", async () => {
    await show({});
    fireEvent.change(screen.getByRole("combobox", { name: "Between steps" }), { target: { value: "wait" } });
    await vi.waitFor(() => expect(fake.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(fake.calls.find((c) => c.method === "PUT")).toEqual({ method: "PUT", url: `/api/ade/agents/tasks/${TASK}`, body: { state_version: 4, between_steps: "wait" } });
    expect(fake.presses()).toEqual([]);                                      // and presses nothing
  });

  it("a 409 and a 412 say in the app's own words what stands in the way, the task changed or the agent paused, and read it again", async () => {
    await show({ state: "ready" });
    // the app's answers (tasks.py): 409 for a version that moved, its only 409 (_check_version), and 412 for a paused
    // agent (_paused_agent)
    let answer = { status: 409, detail: "The task changed; look again." };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url).endsWith("/start")
      ? (fake.calls.push({ method: "POST", url: String(url) }), new Response(JSON.stringify({ detail: answer.detail }), { status: answer.status }))
      : fake.answer(String(url), init))));
    const reads = () => fake.calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/ade/agents/tasks?")).length;
    let before = reads();
    fireEvent.click(screen.getByRole("button", { name: /Start/ }));
    expect(await screen.findByText("The task changed; look again.")).toBeTruthy();
    expect(useStore.getState().error).toBeNull();
    await vi.waitFor(() => expect(reads()).toBeGreaterThan(before));         // and the task read again
    answer = { status: 412, detail: "This agent is paused: resume it first." };
    before = reads();
    fireEvent.click(screen.getByRole("button", { name: /Start/ }));
    expect(await screen.findByText("This agent is paused: resume it first.")).toBeTruthy();
    expect(screen.queryByText("The task changed; look again.")).toBeNull();
    expect(useStore.getState().error).toBeNull();
    await vi.waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it.each([[400, "A task's backend is an account or a key."], [403, "This deployment has the relay off."], [404, "No such task."],
           [500, "The workspace could not read its accounts."]])(
    "any other refusal of a control is the page's error, not a line beside the task (%i)", async (status, detail) => {
      await show({ state: "ready" });
      vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url).endsWith("/start")
        ? new Response(JSON.stringify({ detail }), { status })
        : fake.answer(String(url), init))));
      fireEvent.click(screen.getByRole("button", { name: /Start/ }));
      await vi.waitFor(() => expect(String(useStore.getState().error)).toContain(detail));
      expect(screen.queryByText(detail)).toBeNull();
    });

  it("the New task form says a refusal in the app's own words: an agent with an open task already (412)", async () => {
    startsOn("default");
    render(<AgentTask agent={agent()} onOpenRun={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /New task/ }));
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => (String(url) === "/api/ade/agents/tasks" && init.method === "POST"
      ? new Response(JSON.stringify({ detail: "This agent has an open task already: end it first." }), { status: 412 })
      : fake.answer(String(url), init))));
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Goal" }), { target: { value: "G" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Step 1's objective" }), { target: { value: "S" } });
    fireEvent.click(within(form).getByRole("button", { name: /Make the task/ }));
    expect(await within(form).findByText("This agent has an open task already: end it first.")).toBeTruthy();
    expect(within(form).queryByText("The task changed; look again.")).toBeNull();
  });

  it("words a window as last recorded, its use a fraction, a time days away with its day", () => {
    const t = now();
    expect(windowWords({ window: "five_hour", status: "allowed", resets_at: null, utilization: 0.92, at: t - 60 })).toBe(`5h 92% at ${clock(t - 60)}`);
    expect(windowWords({ window: "seven_day", status: "rejected", resets_at: t + 3600, utilization: null, at: t })).toBe(`7d out of room until ${clock(t + 3600)} (at ${clock(t)})`);
    expect(windowWords({ window: "seven_day", status: "rejected", resets_at: null, utilization: null, at: t })).toBe(`7d out of room with no reset known (at ${clock(t)})`);
    expect(windowWords({ window: "seven_day", status: "allowed", resets_at: t + 3 * DAY, utilization: 0.5, at: t })).toBe(`7d 50% at ${clock(t)}, resets ${dayTime(t + 3 * DAY)}`);
  });
});

describe("Run now, and a run's pane", () => {
  it("says when the agent's account is out of room, and nothing while it has room", () => {
    const out = owner("default", "Home", "Claude Pro", { windows: [room({ status: "rejected", resets_at: now() + 3600 })] });
    expect(exhaustedWords({ account: null }, [out], "default")).toBe(`The window of Claude Code · Home (home@example.com) is exhausted until ${clock(now() + 3600)}: this run will stop at once.`);
    expect(exhaustedWords({ account: null }, [B], "default")).toBeNull();
    expect(exhaustedWords({ account: "a1a1a1a1a1a1" }, [out], "default")).toBeNull();
  });

  it("gives a reset days away with its day, never as a time that reads as today's", () => {
    const t = now();
    const week = owner("default", "Home", "Claude Pro", { windows: [room({ window: "seven_day", status: "rejected", resets_at: t + 3 * DAY })] });
    expect(exhaustedWords({ account: null }, [week], "default", t)).toBe(
      `The window of Claude Code · Home (home@example.com) is exhausted until ${dayTime(t + 3 * DAY)}: this run will stop at once.`);
    // near by the moment it is asked about, not the page's clock: 13 hours off then is a day's time
    expect(exhaustedWords({ account: null }, [week], "default", t + 3 * DAY - 13 * 3600)).toContain(`until ${dayTime(t + 3 * DAY)}:`);
    expect(exhaustedWords({ account: null }, [week], "default", t + 3 * DAY - 2 * 3600)).toContain(`until ${clock(t + 3 * DAY)}:`);
  });

  it("reads, for an agent with no account, the windows of the account new sessions start on, not the default's", () => {
    // an agent with none runs on the account new sessions start on (proofpane_workspace/agents.py), here Work
    const homeOut = owner("default", "Home", "Claude Pro", { windows: [room({ status: "rejected", resets_at: now() + 3600 })] });
    const workOut = owner("c1c1c1c1c1c1", "Work", "Claude Team", { windows: [room({ status: "rejected", resets_at: now() + 1800 })] });
    expect(exhaustedWords({ account: null }, [homeOut, S], S.account)).toBeNull();          // Work has room: no word
    expect(exhaustedWords({ account: null }, [B, workOut], S.account)).toBe(
      `The window of Claude Code · Work (work@example.com) is exhausted until ${clock(now() + 1800)}: this run will stop at once.`);
    // while the page has not read which account new sessions start on, it says nothing, never the default's windows
    expect(exhaustedWords({ account: null }, [homeOut], null)).toBeNull();
    // an agent's own account is its own, whichever new sessions start on: "default" among them, should the app keep it
    // as a value of its own (today none, "" and "default" are one)
    expect(exhaustedWords({ account: "default" }, [homeOut, S], S.account)).toContain("The window of Claude Code · Home");
  });
});

// ---- Connections, an agent's triggers and the account dialog ----------------------------------------------------------
//
// The task's parts there: the workspace's Claude Code accounts
// confirmed as the owner's own for tasks' relays, the terms at risk points 2, 6, 7, 9 and 10 opened over their
// controls, and the account dialog rendering the Custody section by name. Its own fake of the app's account routes,
// scoped to this block, as are its hooks.

describe("Connections, an agent's triggers and the account dialog", () => {
  const LONG = 30_000;

  type Call = { method: string; path: string; body: Record<string, unknown> | undefined };
  type Answer = [number, unknown] | undefined;
  type Body = Record<string, unknown> & { account?: string; change?: string; confirmation?: string; ids?: string[]; password?: string };

  const text = (point: number, design = [{ case: null as string | null, text: `Point ${point}'s own words.` }]): RiskTextView =>
    ({ point, title: `Point ${point}`, design, quotations: [], unread: false, digest: "0".repeat(64) });
  const TEXTS: RiskTextView[] = [text(2), text(6, [{ case: null, text: "Point 6's own words." }, { case: "seat", text: "Point 6's seat words." }]),
    text(7), text(9), text(10)];

  /** A fetch that answers GET /api/ade/risks with TEXTS, and the rest by `answer`, at once or once its promise settles;
   *  what neither answers never answers, as the workspace's reads the page makes meanwhile. Each call is kept, its path
   *  without its query. */
  const serve = (answer: (method: string, path: string, body: Body) => Answer | Promise<Answer> = () => undefined) => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method || "GET";
      const path = String(url).split("?")[0];
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path, body });
      const got = method === "GET" && path === "/api/ade/risks" ? [200, { texts: TEXTS }] as Answer : await answer(method, path, body ?? {});
      if (!got) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(got[1]), { status: got[0] });
    }));
    return calls;
  };

  const DEFAULT = "d".repeat(32);
  const WORK = "e".repeat(32);
  const SPARE = "f".repeat(32);
  const ID: Record<string, string> = { default: DEFAULT, aaaaaaaaaaaa: WORK, bbbbbbbbbbbb: SPARE };

  const personal = (email: string, plan = "Claude Max"): IdentityReport =>
    ({ email, organization: null, plan, token_source: "claude.ai", api_key_source: null, api_provider: "firstParty" });
  const seat = (email: string, plan = "Claude Team"): IdentityReport =>
    ({ email, organization: "Acme", plan, token_source: "claude.ai", api_key_source: null, api_provider: "firstParty" });

  const owner = (account: string, over: Partial<AccountOwnerView> = {}): AccountOwnerView => ({
    id: ID[account], account, name: null, report: personal("me@example.org"), org_id: null, mismatch: null, state: "confirmed",
    grp: "personal", grp_kind: "personal", position: 0, in_relays: true, reserve_lifted: false, ready: true, windows: [], hold: null, ...over,
  });

  /** The app's account-owner rows as its routes keep them: a held row settled at a read past its end; a cancel leaving
   *  its row unconfirmed; a confirmation spent on task-account-own:<that row's id> holding its row for 180 seconds; a
   *  report adding or replacing a row; the switches and the order. And the account's confirmation: the password "right
   *  one", for the change named. */
  function ownersApp(start: AccountOwnerView[], reports: Record<string, IdentityReport> = {}) {
    const rows = start.map((o) => ({ ...o }));
    const now = () => Date.now() / 1000;
    const answer = (method: string, path: string, body: Body): Answer => {
      if (method === "GET" && path === "/api/ade/agents/accounts") {
        for (const o of rows) if (o.state === "held" && o.hold && o.hold.takes_effect_at <= now()) Object.assign(o, { state: "confirmed", hold: null });
        return [200, { accounts: rows.map((o) => ({ ...o })) }];
      }
      if (method === "POST" && path === "/api/ade/agents/accounts/report") {
        const account = String(body.account);
        if (!reports[account]) return [400, { detail: "That account is not signed in." }];
        let o = rows.find((r) => r.account === account);
        if (!o) rows.push(o = owner(account, { state: "reported", position: rows.length, grp: null, grp_kind: null }));
        Object.assign(o, { report: reports[account], state: "reported" });
        return [200, { account: { ...o } }];
      }
      if (method === "GET" && path === "/api/account/confirm") return [200, { needed: true, password: true, passkeys: 0 }];
      if (method === "POST" && path === "/api/account/confirm") {
        return body.password === "right one" ? [200, { confirmation: `conf:${body.change}` }] : [403, { detail: "That did not confirm it." }];
      }
      let m = /^\/api\/ade\/agents\/accounts\/([0-9a-f]{32})\/confirm$/.exec(path);
      if (method === "POST" && m) {
        const o = rows.find((r) => r.id === m![1]);
        if (!o || body.confirmation !== `conf:task-account-own:${o.id}`) return [403, { detail: "That confirmation was made for another change." }];
        Object.assign(o, { state: "held", hold: { id: `hold-${o.account}`, kind: "task.account_own", takes_effect_at: Math.floor(now()) + 180 } });
        return [200, { account: { ...o }, hold: o.hold }];
      }
      m = /^\/api\/ade\/holds\/([^/]+)\/cancel$/.exec(path);
      if (method === "POST" && m) {
        const o = rows.find((r) => r.hold?.id === decodeURIComponent(m![1]));
        if (!o) return [404, { detail: "No such hold." }];
        Object.assign(o, { state: "reported", hold: null });
        return [200, { cancelled_at: Math.floor(now()) }];
      }
      m = /^\/api\/ade\/agents\/accounts\/([0-9a-f]{32})$/.exec(path);
      if (method === "PUT" && m) {
        const o = rows.find((r) => r.id === m![1])!;
        Object.assign(o, body);
        return [200, { account: { ...o } }];
      }
      if (method === "POST" && path === "/api/ade/agents/accounts/order") {
        (body.ids ?? []).forEach((id, i) => { const o = rows.find((r) => r.id === id); if (o) o.position = i; });
        return [200, { accounts: rows.map((o) => ({ ...o })) }];
      }
      return undefined;
    };
    return { rows, answer };
  }

  const signedIn = (email: string, subscription = "max") => ({ installed: true, signed_in: true, email, subscription });
  const accountsView = (codex = false): AccountsView => ({
    claude: {
      active: "default",
      accounts: [
        { id: "default", name: "Default", created_at: null, sessions: 0, status: signedIn("me@example.org"), usage: null },
        { id: "aaaaaaaaaaaa", name: "Work", created_at: 1, sessions: 0, status: signedIn("me@work.example", "team"), usage: null },
        { id: "bbbbbbbbbbbb", name: "Spare", created_at: 1, sessions: 0, status: signedIn("spare@example.org", "pro"), usage: null },
      ],
    },
    codex: { active: "default", accounts: codex ? [{ id: "default", name: "Default", created_at: null, sessions: 0, status: { installed: true, signed_in: true, method: "chatgpt" }, usage: null }] : [] },
  });

  /** The page signed in, its workspace's Claude Code installed with three accounts; the store's reads of the workspace
   *  answered by nothing, the accounts staying as set. */
  const workspace = (over: Partial<ReturnType<typeof useStore.getState>> = {}) => useStore.setState({
    user: { id: "u1", email: "owner@example.org", can_sign_in_again: true },
    claude: { installed: true, signed_in: true, email: "me@example.org", subscription: "max" }, accounts: accountsView(),
    programs: { claude: true, codex: false }, signIns: [],
    refresh: async () => {}, refreshClaude: async () => {}, refreshGithub: async () => {}, refreshApprovals: async () => {},
    refreshAccounts: async () => {}, refreshQuota: async () => {}, ...over,
  });

  const taskList = () => screen.getByRole("list", { name: "Your accounts for tasks, in the relay's order" });
  const itemOf = (name: string) => within(taskList()).getByText(name).closest("li") as HTMLElement;
  const terms = (point: number) => screen.queryByRole("dialog", { name: `Terms: Point ${point}` });
  const closeTerms = (point: number) => fireEvent.click(within(terms(point)!).getByRole("button", { name: "Close the terms" }));
  /** A text as it opens over its control: never modal, holding no button but its Close (nothing asks for a press). */
  const opened = async (point: number) => {
    const dialog = await screen.findByRole("dialog", { name: `Terms: Point ${point}` });
    expect(dialog.getAttribute("aria-modal")).toBe("false");
    expect(within(dialog).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Close the terms"]);
    return dialog;
  };
  const sent = (calls: Call[], method: string, path: string) => calls.filter((c) => c.method === method && c.path === path);

  beforeEach(() => { useStore.setState(useStore.getInitialState()); forgetPage(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); forgetPage(); });

  describe("the workspace's Claude Code accounts as your own, for tasks (Connections)", () => {
    it("two confirmations held at once are each listed with its email and end, one cancelled, the other joining at its end", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const now = Math.floor(Date.now() / 1000);
      const app = ownersApp([
        owner("default"),
        owner("aaaaaaaaaaaa", { state: "held", position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team",
          hold: { id: "hold-work", kind: "task.account_own", takes_effect_at: now + 150 } }),
        owner("bbbbbbbbbbbb", { state: "held", position: 2, report: personal("spare@example.org", "Claude Pro"),
          hold: { id: "hold-spare", kind: "task.account_own", takes_effect_at: now + 5 } }),
      ]);
      const calls = serve(app.answer);
      workspace();
      render(<Connections onClose={() => {}} />);
      await screen.findByText(`It joins your tasks' relay at ${clock(now + 150)}, unless you cancel it first.`);
      const work = itemOf("Work");
      const spare = itemOf("Spare");
      expect(within(work).getByText(/me@work\.example/)).toBeTruthy();
      expect(within(work).getByText(`It joins your tasks' relay at ${clock(now + 150)}, unless you cancel it first.`)).toBeTruthy();
      expect(within(spare).getByText(/spare@example\.org/)).toBeTruthy();
      expect(within(spare).getByText(`It joins your tasks' relay at ${clock(now + 5)}, unless you cancel it first.`)).toBeTruthy();
      fireEvent.click(within(work).getByRole("button", { name: /Cancel/ }));
      await waitFor(() => expect(within(itemOf("Work")).getByText(/^Is this account yours\?/)).toBeTruthy());
      expect(sent(calls, "POST", "/api/ade/holds/hold-work/cancel")).toHaveLength(1);
      expect(within(itemOf("Spare")).queryByLabelText("In tasks' relays")).toBeNull();          // still waiting
      // its end passed, the list is read again: the other has joined, and the cancelled one has not
      await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
      await waitFor(() => expect(within(itemOf("Spare")).getByLabelText("In tasks' relays")).toBeTruthy());
      expect(within(itemOf("Work")).getByText(/^Is this account yours\?/)).toBeTruthy();
      expect(within(itemOf("Work")).queryByLabelText("In tasks' relays")).toBeNull();
      expect(sent(calls, "POST", "/api/ade/holds/hold-spare/cancel")).toHaveLength(0);
    }, LONG);

    it("one not confirmed yet is read from its start, shown as it came, and confirmed for task-account-own:<its row's id>", async () => {
      const app = ownersApp([], { bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") });
      const calls = serve(app.answer);
      workspace();
      render(<Connections onClose={() => {}} />);
      await waitFor(() => expect(within(itemOf("Spare")).getByText(/^Is this account yours\? Use only your own accounts here, never a colleague's\.$/)).toBeTruthy());
      expect(within(itemOf("Spare")).getByText(/it is in no relay\.$/)).toBeTruthy();
      expect(within(itemOf("Spare")).queryByRole("button", { name: "Yes, it is mine" })).toBeNull();     // nothing read yet
      fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Read who it is/ }));
      const report = await waitFor(() => within(itemOf("Spare")).getByLabelText("What its start reported"));
      expect(sent(calls, "POST", "/api/ade/agents/accounts/report").map((c) => c.body)).toEqual([{ account: "bbbbbbbbbbbb" }]);
      expect([...report.querySelectorAll("dt")].map((d) => [d.textContent, d.nextElementSibling?.textContent])).toEqual([
        ["Email", "spare@example.org"], ["Organisation", "none"], ["Plan", "Claude Pro"],
        ["Token source", "claude.ai"], ["Key source", "none"], ["Provider", "firstParty"]]);
      // the first account confirmed, on a personal plan: point 6's text alone, without its seat part
      fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" }));
      const six = await opened(6);
      await within(six).findByText("Point 6's own words.");
      expect(within(six).queryByText("Point 6's seat words.")).toBeNull();
      expect(terms(2)).toBeNull();
      const group = screen.getByRole("group", { name: "Confirming “Spare” as your own" });
      fireEvent.change(within(group).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(group).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(within(itemOf("Spare")).getByText(/^It joins your tasks' relay at /)).toBeTruthy());
      expect(sent(calls, "POST", "/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: `task-account-own:${SPARE}` }]);
      // the report and orgId its row showed, which the app compares with the account's own
      expect(sent(calls, "POST", `/api/ade/agents/accounts/${SPARE}/confirm`).map((c) => c.body)).toEqual([
        { confirmation: `conf:task-account-own:${SPARE}`, report: personal("spare@example.org", "Claude Pro"), org_id: null }]);
      expect(terms(6)).toBeNull();                                    // gone with its confirmation
    }, LONG);

    it("a changed identity is said and confirmed again, and a mismatch says which field, each in no relay", async () => {
      const app = ownersApp([
        owner("aaaaaaaaaaaa", { state: "reported", report: seat("me@work.example"), mismatch: "email", grp: null, grp_kind: null }),
        owner("bbbbbbbbbbbb", { state: "changed", position: 1, report: personal("other@example.org", "Claude Pro") }),
      ]);
      const calls = serve(app.answer);
      workspace();
      render(<Connections onClose={() => {}} />);
      await waitFor(() => expect(within(itemOf("Work")).getByText(/^It cannot be confirmed:/)).toBeTruthy());
      const work = itemOf("Work");
      expect(work.textContent).toContain("It cannot be confirmed: its sign-in's own record disagrees with what its start reported, on its email. It is in no relay.");
      expect(within(work).queryByRole("button", { name: "Yes, it is mine" })).toBeNull();
      expect(within(work).getByRole("button", { name: /Read it again/ })).toBeTruthy();
      const spare = itemOf("Spare");
      expect(within(spare).getByText("Its identity changed since you confirmed it: it is in no relay until you confirm it again.")).toBeTruthy();
      expect(within(spare).getByText(/^Is this account yours\?/)).toBeTruthy();
      expect(within(within(spare).getByLabelText("What its start reported")).getByText("other@example.org")).toBeTruthy();
      fireEvent.click(within(spare).getByRole("button", { name: "Confirm again" }));
      const group = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
      fireEvent.change(within(group).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(group).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(within(itemOf("Spare")).getByText(/^It joins your tasks' relay at /)).toBeTruthy());
      expect(sent(calls, "POST", "/api/account/confirm").map((c) => c.body?.change)).toEqual([`task-account-own:${SPARE}`]);
    }, LONG);

    it("the relay's order is Connections' order, set with Move up and Move down, or by dragging", async () => {
      const app = ownersApp([
        owner("default"),
        owner("aaaaaaaaaaaa", { position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" }),
        owner("bbbbbbbbbbbb", { position: 2, report: personal("spare@example.org", "Claude Pro") }),
      ]);
      // the first order's answer waits until the test lets it go
      let letGo = () => {};
      const held = new Promise<void>((done) => { letGo = done; });
      let orders = 0;
      const calls = serve((method, path, body) => (method === "POST" && path === "/api/ade/agents/accounts/order" && orders++ === 0
        ? held.then(() => app.answer(method, path, body)) : app.answer(method, path, body)));
      workspace();
      render(<Connections onClose={() => {}} />);
      const names = () => within(taskList()).getAllByRole("listitem").map((li) => li.querySelector(".font-medium")?.firstChild?.textContent);
      await waitFor(() => expect(names()).toEqual(["Default", "Work", "Spare"]));
      expect(itemOf("Work").textContent).toContain("Claude Team · Acme · Acme's seats, Team");
      expect(itemOf("Spare").textContent).toContain("Claude Pro · no organisation · your personal plans");
      expect((within(itemOf("Default")).getByRole("button", { name: "Move Default up" }) as HTMLButtonElement).disabled).toBe(true);
      expect((within(itemOf("Spare")).getByRole("button", { name: "Move Spare down" }) as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: "Move Spare up" }));
      await waitFor(() => expect(sent(calls, "POST", "/api/ade/agents/accounts/order")).toHaveLength(1));
      // a drop while that order goes is not made from the list as it was
      fireEvent.dragStart(within(itemOf("Default")).getByTitle("Drag it to change the order"));
      fireEvent.dragOver(itemOf("Work"));
      fireEvent.drop(itemOf("Work"));
      await act(async () => { await Promise.resolve(); });
      expect(sent(calls, "POST", "/api/ade/agents/accounts/order")).toHaveLength(1);
      letGo();
      await waitFor(() => expect(names()).toEqual(["Default", "Spare", "Work"]));
      expect(sent(calls, "POST", "/api/ade/agents/accounts/order").map((c) => c.body)).toEqual([{ ids: [DEFAULT, SPARE, WORK] }]);
      await waitFor(() => expect((within(itemOf("Default")).getByRole("button", { name: "Move Default down" }) as HTMLButtonElement).disabled).toBe(false));
      // Default dragged onto Work, the last: it goes last
      fireEvent.dragStart(within(itemOf("Default")).getByTitle("Drag it to change the order"));
      fireEvent.dragOver(itemOf("Work"));
      fireEvent.drop(itemOf("Work"));
      await waitFor(() => expect(names()).toEqual(["Spare", "Work", "Default"]));
      expect(sent(calls, "POST", "/api/ade/agents/accounts/order").map((c) => c.body)).toEqual([{ ids: [DEFAULT, SPARE, WORK] }, { ids: [SPARE, WORK, DEFAULT] }]);
    }, LONG);
  });

  describe("the terms over the owner's own controls (Connections, an agent's triggers)", () => {
    it("adding a Claude Code account opens point 6's text over its form each time it opens, the form working with it open; a Codex account's opens none", async () => {
      const app = ownersApp([]);
      const calls = serve((method, path, body) => {
        if (method === "POST" && path === "/api/ade/workspace/accounts") return [200, { id: "cccccccccccc", kind: body.kind, name: body.name, created_at: 1 }];
        if (method === "POST" && path === "/api/ade/workspace/sessions") return [200, { id: "s1s1s1s1s1s1", program: body.program, repo: null, task: body.task, created_at: 1 }];
        return app.answer(method, path, body);
      });
      workspace({ programs: { claude: true, codex: true }, accounts: accountsView(true) });
      render(<Connections onClose={() => {}} />);
      const add = () => screen.getByRole("button", { name: /Add a Claude Code account/ }) as HTMLButtonElement;
      fireEvent.click(add());
      const six = await opened(6);
      await within(six).findByText("Point 6's own words.");
      // the form works with the text open: named and sent, nothing pressed in the text
      fireEvent.change(screen.getByLabelText("The new Claude Code account's name"), { target: { value: "Third" } });
      fireEvent.click(screen.getByRole("button", { name: /Add and sign in/ }));
      await waitFor(() => expect(sent(calls, "POST", "/api/ade/workspace/accounts").map((c) => c.body)).toEqual([{ kind: "claude", name: "Third" }]));
      await waitFor(() => expect(terms(6)).toBeNull());              // gone with its form
      // opened again, it opens again; closed by the person, it stays closed while the form stays open
      await waitFor(() => expect(add().disabled).toBe(false));
      fireEvent.click(add());
      await opened(6);
      closeTerms(6);
      expect(terms(6)).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      fireEvent.click(add());
      await opened(6);
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      fireEvent.click(screen.getByRole("button", { name: /Add a Codex account/ }));
      expect(screen.getByLabelText("The new Codex account's name")).toBeTruthy();
      await act(async () => { await Promise.resolve(); });
      expect(screen.queryByRole("dialog", { name: /^Terms:/ })).toBeNull();
    }, LONG);

    it("confirming a second account, a Team seat, opens point 6's text with its seat part and the relay's (point 2), each time, the confirmation working with them open", async () => {
      const app = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { state: "reported", position: 1, report: seat("me@work.example"), grp: null, grp_kind: null })]);
      const calls = serve(app.answer);
      workspace();
      render(<Connections onClose={() => {}} />);
      const yes = () => within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" }) as HTMLButtonElement;
      await waitFor(() => expect(yes()).toBeTruthy());
      fireEvent.click(yes());
      const six = await opened(6);
      await within(six).findByText("Point 6's seat words.");
      expect(within(six).getByText("Point 6's own words.")).toBeTruthy();
      await opened(2);
      closeTerms(6);
      expect(terms(6)).toBeNull();
      expect(terms(2)).not.toBeNull();
      fireEvent.click(within(screen.getByRole("group", { name: "Confirming “Work” as your own" })).getByRole("button", { name: "Cancel" }));
      expect(terms(2)).toBeNull();
      // opened again, both open again
      await waitFor(() => expect(yes().disabled).toBe(false));
      fireEvent.click(yes());
      await opened(6);
      await opened(2);
      const group = screen.getByRole("group", { name: "Confirming “Work” as your own" });
      fireEvent.change(within(group).getByLabelText("Your password"), { target: { value: "right one" } });
      fireEvent.click(within(group).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(within(itemOf("Work")).getByText(/^It joins your tasks' relay at /)).toBeTruthy());
      expect(sent(calls, "POST", "/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: `task-account-own:${WORK}` }]);
      expect(sent(calls, "POST", `/api/ade/agents/accounts/${WORK}/confirm`)).toHaveLength(1);
    }, LONG);

    it("Use to the limit opens point 9's text as it is about to be turned on and as it is, every time, the switch working with it open; on, or turned off, it opens nothing", async () => {
      const now = Math.floor(Date.now() / 1000);
      const app = ownersApp([owner("default", { windows: [{ window: "five_hour", status: "allowed", resets_at: now + 3600, utilization: 0.92, at: now - 60 }] })]);
      const calls = serve(app.answer);
      workspace();
      render(<Connections onClose={() => {}} />);
      const limit = () => within(itemOf("Default")).getByLabelText("Use to the limit") as HTMLInputElement;
      await waitFor(() => expect(limit()).toBeTruthy());
      expect(itemOf("Default").textContent).toContain(`Windows as last recorded: 5h 92% at ${clock(now - 60)}, resets ${clock(now + 3600)}`);
      const puts = () => sent(calls, "PUT", `/api/ade/agents/accounts/${DEFAULT}`).map((c) => c.body);
      // about to be turned on: the pointer coming to it, or the keyboard, opens its text before the press
      fireEvent.mouseEnter(limit().closest("label")!);
      await opened(9);
      closeTerms(9);
      fireEvent.focus(limit());
      await opened(9);
      closeTerms(9);
      expect(puts()).toEqual([]);
      fireEvent.click(limit());
      await opened(9);
      await waitFor(() => expect(puts()).toEqual([{ reserve_lifted: true }]));
      await waitFor(() => expect(limit().checked).toBe(true));
      closeTerms(9);
      fireEvent.mouseEnter(limit().closest("label")!);                    // on, so about to be turned off: none
      fireEvent.focus(limit());
      await act(async () => { await Promise.resolve(); });
      expect(terms(9)).toBeNull();
      fireEvent.click(limit());
      await waitFor(() => expect(limit().checked).toBe(false));
      expect(puts()).toEqual([{ reserve_lifted: true }, { reserve_lifted: false }]);
      expect(terms(9)).toBeNull();
      fireEvent.click(limit());
      await opened(9);
      await waitFor(() => expect(puts()).toEqual([{ reserve_lifted: true }, { reserve_lifted: false }, { reserve_lifted: true }]));
      // taking it out of relays is no risk point
      closeTerms(9);
      fireEvent.click(within(itemOf("Default")).getByLabelText("In tasks' relays"));
      await waitFor(() => expect(puts()).toContainEqual({ in_relays: false }));
      expect(screen.queryByRole("dialog", { name: /^Terms:/ })).toBeNull();
    }, LONG);

    it("Anthropic's key form opens point 10's text each time it opens, and keeps the key with it open; another vendor's opens none", async () => {
      const providers: ModelProviderView[] = [
        { id: "anthropic", label: "Anthropic", vendor: "Anthropic", docs: "https://docs.example/anthropic", model: "claude-opus", set: false },
        { id: "kimi", label: "Kimi K3", vendor: "Moonshot AI", docs: "https://docs.example/kimi", model: "kimi-k3", set: false },
      ];
      const calls = serve((method, path, body) => {
        if (method === "GET" && path === "/api/ade/workspace/model-providers") return [200, { providers }];
        if (method === "PUT" && path === "/api/ade/workspace/model-providers/anthropic") return [200, { providers: providers.map((p) => (p.id === "anthropic" ? { ...p, set: !!body.key } : p)) }];
        return ownersApp([]).answer(method, path, body);
      });
      localStorage.setItem("pp-connections-more", "1");
      workspace();
      render(<Connections onClose={() => {}} />);
      const card = (label: string) => screen.getByText(label, { selector: "span" }).closest("li") as HTMLElement;
      await waitFor(() => expect(card("Anthropic")).toBeTruthy());
      fireEvent.click(within(card("Anthropic")).getByRole("button", { name: "Add your key" }));
      await opened(10);
      fireEvent.click(within(card("Anthropic")).getByRole("button", { name: "Cancel" }));
      expect(terms(10)).toBeNull();
      fireEvent.click(within(card("Anthropic")).getByRole("button", { name: "Add your key" }));
      await opened(10);
      fireEvent.change(screen.getByLabelText("Your Anthropic API key"), { target: { value: "a-test-key" } });
      fireEvent.click(within(card("Anthropic")).getByRole("button", { name: /Keep/ }));
      await waitFor(() => expect(sent(calls, "PUT", "/api/ade/workspace/model-providers/anthropic").map((c) => c.body)).toEqual([{ key: "a-test-key" }]));
      await waitFor(() => expect(terms(10)).toBeNull());
      fireEvent.click(within(card("Kimi K3")).getByRole("button", { name: "Add your key" }));
      expect(screen.getByLabelText("Your Kimi K3 API key")).toBeTruthy();
      await act(async () => { await Promise.resolve(); });
      expect(screen.queryByRole("dialog", { name: /^Terms:/ })).toBeNull();
    }, LONG);

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

    it("an agent's schedule and hook forms open point 7's text as each opens, and a trigger's switch as it is about to be turned on and as it is, every time, each working with it open", async () => {
      const triggers: Trigger[] = [{ id: "h1h2h3h4h5h6", agent_id: "a1a2a3a4a5a6", kind: "hook", enabled: false, prompt: null, last_at: null,
        last_run_id: null, last_error: null, missed: 0, hook_type: "github", url: "https://oss.example/api/ade/hooks/h1h2h3h4h5h6" }];
      const calls = serve((method, path, body) => {
        if (method === "POST" && path === "/api/ade/agents/a1a2a3a4a5a6/triggers") {
          const id = `t${triggers.length}`.padEnd(12, "0");
          const made = { ...triggers[0], id, kind: body.kind, enabled: true, url: `https://oss.example/api/ade/hooks/${id}` } as Trigger;
          triggers.push(made);
          return [200, made];
        }
        const m = /^\/api\/ade\/agents\/a1a2a3a4a5a6\/triggers\/(.+)$/.exec(path);
        if (method === "PUT" && m) {
          const t = triggers.find((x) => x.id === m[1])!;
          Object.assign(t, body);
          return [200, t];
        }
        return undefined;
      });
      // the agent as the app gives it after each change
      function Pane() {
        const [now, setNow] = useState(agent({ triggers: triggers.map((t) => ({ ...t })) }));
        return <AgentTriggers agent={now} onChanged={async () => setNow(agent({ triggers: triggers.map((t) => ({ ...t })) }))} />;
      }
      render(<Pane />);
      const group = screen.getByRole("group", { name: "Triage's triggers" });
      const posts = () => sent(calls, "POST", "/api/ade/agents/a1a2a3a4a5a6/triggers").map((c) => c.body?.kind);
      fireEvent.click(within(group).getByRole("button", { name: "Schedule" }));
      await opened(7);
      fireEvent.click(within(group).getByRole("button", { name: /Add the schedule/ }));
      await waitFor(() => expect(posts()).toEqual(["schedule"]));
      await waitFor(() => expect(terms(7)).toBeNull());              // gone with its form
      fireEvent.click(within(group).getByRole("button", { name: "Schedule" }));
      await opened(7);
      closeTerms(7);
      // the hook's form, in the schedule's place, opens it again
      fireEvent.click(within(group).getByRole("button", { name: "Hook" }));
      await opened(7);
      fireEvent.click(within(group).getByRole("button", { name: /Add the hook/ }));
      await waitFor(() => expect(posts()).toEqual(["schedule", "hook"]));
      await waitFor(() => expect(terms(7)).toBeNull());
      // the hook's form opened again opens it again, and closed, its text goes with it
      fireEvent.click(within(group).getByRole("button", { name: "Hook" }));
      await opened(7);
      fireEvent.click(within(group).getByRole("button", { name: "Hook" }));
      await waitFor(() => expect(terms(7)).toBeNull());
      // the switch: turned on, the text opens and the change goes; turned off, nothing opens; on again, it opens again
      const puts = () => sent(calls, "PUT", "/api/ade/agents/a1a2a3a4a5a6/triggers/h1h2h3h4h5h6").map((c) => c.body);
      const box = () => within(screen.getByTitle("https://oss.example/api/ade/hooks/h1h2h3h4h5h6").closest("li")!).getByRole("checkbox") as HTMLInputElement;
      // about to be turned on: the pointer coming to it, or the keyboard, opens its text before the press
      fireEvent.mouseEnter(box().closest("label")!);
      await opened(7);
      closeTerms(7);
      fireEvent.focus(box());
      await opened(7);
      closeTerms(7);
      expect(puts()).toEqual([]);
      fireEvent.click(box());
      await opened(7);
      await waitFor(() => expect(puts()).toEqual([{ enabled: true }]));
      await waitFor(() => expect(box().checked).toBe(true));
      closeTerms(7);
      fireEvent.mouseEnter(box().closest("label")!);                        // on, so about to be turned off: none
      fireEvent.focus(box());
      await act(async () => { await Promise.resolve(); });
      expect(terms(7)).toBeNull();
      await waitFor(() => expect(box().disabled).toBe(false));
      fireEvent.click(box());
      await waitFor(() => expect(box().checked).toBe(false));
      expect(puts()).toEqual([{ enabled: true }, { enabled: false }]);
      expect(terms(7)).toBeNull();
      await waitFor(() => expect(box().disabled).toBe(false));
      fireEvent.click(box());
      await opened(7);
      await waitFor(() => expect(puts()).toEqual([{ enabled: true }, { enabled: false }, { enabled: true }]));
    }, LONG);
  });

  describe("the account dialog's Custody section", () => {
    it("is TaskCustody, rendered by name in the account tab", async () => {
      serve((method, path) => (method === "GET" && path === "/api/account/passkeys" ? [200, { passkeys: [] }] : undefined));
      useStore.setState({ user: { id: "u1", email: "owner@example.org", can_sign_in_again: true } });
      render(<AccountDialog onClose={() => {}} />);
      expect(screen.getByLabelText("Custody, its stand-in")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
      expect(screen.queryByLabelText("Custody, its stand-in")).toBeNull();
    }, LONG);
  });
});
