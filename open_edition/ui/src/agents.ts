// Agents: a chat kept under a name, with a brief, and run again (the app's ade/agents.py). The list, the records and
// the totals come from the app's store, so reading them never starts a machine; a run's chat is a session's, read
// from the workspace when it runs.
import { Api, api, fetchBlob, type WindowView } from "./api";
import { clock, dayTime } from "./when";

export type RunState = "queued" | "working" | "waiting" | "done" | "failed" | "blocked" | "stopped" | "skipped";
export type AgentState = "idle" | "working" | "waiting" | "paused" | "retired";

export interface RunTokens { input_tokens?: number; output_tokens?: number; cache_creation_input_tokens?: number; cache_read_input_tokens?: number }

export interface AgentRun {
  run_id: string;
  agent_id: string;
  state: RunState;
  reason: string | null;
  cause: string | null;
  trigger: { kind?: string; prompt?: string | null; at?: number; parent_run_id?: string; depth?: number; from_agent?: string | null };
  queued_at: number | null;
  started_at: number | null;
  ended_at: number | null;
  base: string | null;
  head: string | null;
  branch: string | null;
  brief_version: number | null;
  question: string | null;
  steps: number;
  requests: number | null;
  cost_est_usd: number | null;
  tokens: RunTokens | null;
  last_text: string | null;
  bytes: number | null;
  pruned: boolean;
  thumbs: "up" | "down" | null;
  thumbs_note: string | null;
  sent: boolean;
  starting?: boolean;
  context?: RunContext | null;
  /** the run (or chat) that started it, with run_agent; how deep; how many runs it started; the one it waits on */
  parent_run_id?: string | null;
  depth?: number;
  dispatches?: number;
  waiting_on?: string | null;
  subtree?: RunSubtree | null;
  /** its numbers at its end; of them, the checks of its words, its autonomy, its servers, its fan-out */
  metrics?: (RunChecks & Record<string, unknown>) | null;
  /** the judge's verdict, when its agent's runs are graded and this one was asked about */
  judge?: JudgeVerdict | null;
  /** why the judge gave it no verdict when it was asked: the call failed, or its reply was not a verdict */
  judge_error?: string | null;
  anomalies?: Anomaly[] | null;
  acceptance?: AcceptanceResult | null;
  /** the run this one replays, started again as it was */
  replay_of?: string | null;
  /** a run the workspace started outside its bounds (a hook's run's, past the depth or the width): shown, counted */
  flag?: string | null;
  /** its record was rewritten by something other than the workspace: put back, or with only how it stands kept */
  rewritten?: string | null;
}

/** The checks of a run at its end (the workspace's metrics.py, kept by the app's agent_metrics.py). */
export interface RunChecks {
  replayable?: boolean;
  claims?: { flagged: boolean; standing?: { word: string; text: string }[] };
  premise?: { flagged: boolean; counterfactual?: string[]; governance?: string[]; request?: string[] };
  autonomy?: { unattended: boolean; interventions: number } | null;
  mcp_health?: { needs_auth: string[]; failed: string[]; disabled: string[]; tool_changes: number } | null;
  dispatch?: { fan_out: number; waited_s: number } | null;
}
/** A judge's grades of a run, 0 to 1 on each dimension; it passes when each is at least 0.6, and its score is the lowest. */
export interface JudgeVerdict { scores: Record<string, number>; pass: boolean; score: number; version: string; why: string | null; cost_est_usd: number | null }
/** A live rule a run met (the workspace's evaluator.py). */
export interface Anomaly { rule: AnomalyRule; level: "warning" | "critical"; at: number; evidence: string | null }
export type AnomalyRule = "tool_repetition" | "no_progress" | "context_budget" | "spend" | "time_budget";
export const RULE_WORDS: Record<AnomalyRule, string> = {
  tool_repetition: "the same call again and again",
  no_progress: "steps that changed nothing",
  context_budget: "near its compaction point",
  spend: "spending well past its usual run",
  time_budget: "near its time limit",
};
export interface AcceptanceResult { pass: boolean; failures: string[]; retried: boolean }
export type RiskTier = "low" | "medium" | "high" | "critical";
/** An agent's checks of each run's final message, deterministic, and what a failure does. */
export interface AcceptanceSpec {
  required_keys: string[]; json_path_equals: Record<string, unknown>; max_risk_tier: RiskTier | null;
  forbidden_substrings: string[]; on_fail: "fail" | "hitl" | "fallback"; fallback_model: string | null;
}
/** An acceptance spec's values at paths as the form shows them: a line each, `path = JSON value`. */
export function pathsText(paths: Record<string, unknown> | undefined): string {
  return Object.entries(paths ?? {}).map(([p, v]) => `${p} = ${JSON.stringify(v)}`).join("\n");
}

/** The form's lines back to values at paths: a value that is not JSON is taken as text; a line with no `=` is an
 *  error, named. */
export function parsePaths(text: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    if (at <= 0) throw new Error(`"${line}" is not a path and its value, such as status = "ok".`);
    const path = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    try { out[path] = JSON.parse(value); } catch { out[path] = value; }
  }
  return out;
}

/** How far a judge's verdicts agree with the owner's thumbs (the app's quality_stats.py): nothing before 20 labels. */
export interface JudgeTrust {
  labelled: number; agreement: number | null; ac1: number | null; alpha: number | null;
  agreement_interval?: [number, number] | null;
  ac1_interval: [number, number] | null; alpha_interval: [number, number] | null;
  band: "trusted" | "tentative" | "greyed" | "unvaried" | null;
}
/** An agent's quality numbers over 7 or 30 days (the app's quality.window). */
export interface QualityWindow {
  judged: number; judge_pass_rate: number | null; judge_version: string | null; judge_band: JudgeTrust["band"];
  drift: { level: "warning" | "critical" | null; current: number | null; baseline: number | null; drop: number | null };
  acceptance: { checked: number; passed: number; pass_rate: number | null };
  anomalies: { warning: number; critical: number; by_rule: Partial<Record<AnomalyRule, number>> };
  claims_flagged: number; premise_flagged: number;
  autonomy: { unattended_share: number | null; interventions_per_run: number | null };
  mcp_health: { needs_auth: number; failed: number; disabled: number; tool_changes: number };
  dispatch: { coordinating_runs: number; most_fan_out: number; deepest: number; waited_s: number };
  replays: { replays: number; compared: number; agreement: number | null };
  cost_of_improvement: { usd: number; share: number | null };
}

/** What a run started, theirs too: how many, their estimated cost and time, how many are open and failed. */
export interface RunSubtree { runs: number; cost_est_usd: number; seconds: number; open: number; failed: number }

/** Who started a run, in words: another agent's run (by its agent's name), a chat, or nobody (null). */
export function startedBy(run: AgentRun, names: Map<string, string>): string | null {
  if (run.trigger.kind !== "dispatch") return null;
  const from = run.trigger.from_agent;
  return from ? `${names.get(from) ?? "another agent"}'s run` : "a chat";
}

/** A run's context against its compaction point (the workspace's runtree.py). */
export interface RunContext {
  model_window: number | null; compaction_window: number | null; peak_tokens: number | null; peak_pct: number | null;
  last_tokens?: number | null; compactions: number | null; fragmented: boolean; retention: number | null; lost?: string[];
  compaction_log?: { at: number | null; trigger: string; pre_tokens: number | null; post_tokens: number | null }[];
}
export interface TreeTool { id?: string; name: string; target: string; ms: number | null; error: boolean; agent?: TreeAgent }
export interface TreeAgent { type: string; description: string; depth: number | null; requests: number; tokens: RunTokens; tools: TreeTool[]; more?: number }
export interface TreeTurn { at: number | null; text: string; requests: number; tokens: RunTokens; tools: TreeTool[]; more?: number }

export interface AgentTotals {
  runs: number; done: number; failed: number; blocked: number; seconds: number; tokens: number; cost_est_usd: number;
  last_run_at: number | null; last_state: RunState | null; runs_today: number; est_usd_today: number;
  success_rate: number | null;
}

export interface AgentLimits { max_run_seconds: number; max_runs_per_day: number; max_steps: number; queue_max: number; storage_mb: number }

/** One of an agent's own MCP servers: an address (http or sse), an npm package at its exact version, or one of its
 *  repository's servers the owner approved in a chat. Secret values are never here: only their names. */
export interface McpServer {
  name: string;
  kind: "address" | "package" | "repository";
  transport?: "http" | "sse"; url?: string; headers?: Record<string, string>; secret_headers?: string[];
  registry?: "npm"; identifier?: string; version?: string; args?: string[]; env?: Record<string, string>; secret_env?: string[];
}
export interface AgentMcp { connectors: boolean; servers: McpServer[]; accepted: Record<string, string>; held: string[] }
export interface ToolStatus { name: string; scope: string; status: string; error: string | null; tools: number | null }
export interface ToolsState { status?: ToolStatus[]; unavailable?: Record<string, string>; seen?: Record<string, string>; run_id?: string; at?: number }
export interface RegistryField { name: string; secret: boolean; required: boolean; description: string }
export interface RegistryWay {
  kind: "package" | "address" | "other";
  registry?: string; identifier?: string; version?: string; env?: RegistryField[];
  transport?: string; url?: string; headers?: RegistryField[];
}
export interface RegistryRow { name: string; description: string; version: string; repository: string | null; ways: RegistryWay[] }
export interface Proposal { id: string; agent_id: string; run_id: string; name: string; why: string | null; entry: RegistryRow; created_at: number }

export interface Agent {
  id: string;
  name: string;
  group: string | null;
  brief: string;
  repo: string | null;
  base_ref: string | null;
  keep_worktree: boolean;
  account: string | null;
  chat_settings: { model?: string; effort?: string; permission_mode?: string };
  allow: string[];
  limits: AgentLimits;
  brief_version: number;
  paused: boolean;
  paused_reason: string | null;
  retired: boolean;
  from_session: string | null;
  created_at: number;
  updated_at: number;
  state: AgentState;
  totals: AgentTotals;
  runs: AgentRun[];
  mcp: AgentMcp;
  tools: ToolsState;
  proposals: Proposal[];
  unattended_mode: Unattended;
  precheck: string | null;
  max_budget_usd: number | null;
  est_usd_per_day: number | null;
  paused_until?: number | null;
  triggers: Trigger[];
  autocompact_window: number | null;
  max_compactions: number;
  may_dispatch: boolean;
  accepts_dispatch: boolean;
  max_dispatch_per_run: number;
  /** its last 7 days, for the list (agent_metrics.summaries); none before a run ended */
  numbers?: AgentNumbers | null;
  /** its facts' relations, each one value at a time (functional) or several (multi, the default) */
  relations?: Record<string, "functional" | "multi">;
  /** runs a memory file may go unused before the sweep archives it; 0: never */
  decay_runs?: number;
  /** a weekly run that re-verifies what the sweep flagged, from each item's source */
  reverify?: boolean;
  freshness?: Freshness | null;
  /** a run that meets a live rule at its critical level waits for the owner (off: the rule is only told) */
  anomaly_pause?: boolean;
  /** a judge grades a tenth of its runs and each one the owner labels (off by default: the judge's calls cost) */
  judge_runs?: boolean;
  acceptance?: AcceptanceSpec | null;
  /** the improve loop reviews it and proposes changes, each tried on replays and made only on the owner's tap */
  improve?: boolean;
  reviewed_at?: number | null;
  /** when and why the loop last passed it over (its digest too long for the workspace); cleared once it is reviewed */
  review_skipped?: { at: number; reason: string } | null;
  /** its skills, written into its worktree by the workspace alone: each a name and its SKILL.md */
  skills?: { name: string; content: string }[];
}

/** A change the improve loop proposed (the app's improve.py), and what became of it. */
export interface Improvement {
  id: string; agent_id: string | null; review_run_id: string | null; kind: "change" | "rollback" | "meta";
  target: "brief" | "skill" | "effort" | "autocompact_window" | "model" | "review_prompt"; name: string | null;
  current: unknown; proposed: unknown; hypothesis: string | null; evidence: { runs?: string[]; step?: string; metric?: string };
  edits: number | null;
  state: "refused" | "pruned" | "critic" | "waiting" | "sandbox" | "not_evaluated" | "not_eligible" | "eligible" | "approved"
    | "rejected" | "expired" | "rolled_back";
  reason: string | null;
  result: {
    wins?: number; losses?: number; p?: number | null; regressions?: number; cost_control?: number | null; cost_treatment?: number | null;
    after?: number; before?: number; pairs?: { run_id: string; group: string; control: boolean | null; treatment: boolean | null }[];
  } | null;
  created_at: number; decided_at: number | null; approved_at: number | null; rolls_back: string | null; expires_at: number | null;
  diff: string[];
}
export interface ImproveBudget { sessions: number; usd: number; max_sessions: number; max_usd: number; spent: boolean }
export interface Improvements { proposals: Improvement[]; budget: ImproveBudget; review_prompt: { version: number; prompt: string } }

/** How current an agent's knowledge is (the app's freshness.py): the last sweep's part and the ledger's. */
export interface Freshness {
  day: string | null; current_share: number | null; stale_count: number; told_stale: number; contradictions: number;
  items: number; stale_share: number | null; archived: string[];
  knowledge: { files: number; current: number; historical: number; deprecated: number; untiered: number;
               stale: { path: string; verify_by: string | null; source: string | null }[] } | null;
}

/** A fact of the agent's ledger (the app's facts.py): when it was true, when it was learned and retired, and how. */
export interface Fact {
  id: string; subject: string; relation: string; object: string; valid_at: number; invalid_at: number | null;
  recorded_at: number; expired_at: number | null; superseded_by: string | null; source: string | null;
  basis: "observed" | "told"; run_id: string | null; contradicts: string | null; retracted: string | null;
}
export interface Facts { current: Fact[]; contradictions: Fact[]; retired: Fact[] }

/** The agent's memory: its versions (each a run that changed it, or a rollback), and its folder now. */
export interface MemoryVersion { version: number; run_id: string | null; kind: "run" | "rollback"; from_version: number | null; made_at: number; files: number; bytes: number }
export interface MemoryVersionFull extends MemoryVersion {
  contents: Record<string, string | null>;
  changes: { added: string[]; removed: string[]; changed: { path: string; diff: string | null }[] };
}
export interface MemoryNow { runs: number; files: { path: string; bytes: number; runs_ago: number | null }[]; archived: { path: string; bytes: number }[] }

export interface AgentNumbers { days: number; wall_p50_s: number | null; wall_p95_s: number | null; calls: number; errors: number; refused: number; cache_ratio: number | null }

/** An agent's numbers over 7 or 30 days, from its days added up (the app's agent_metrics.window). */
export interface AgentWindow {
  days: number; since: string;
  runs: { runs: number; done: number; failed: number; blocked: number; stopped: number; skipped: number };
  success_rate: number | null; cost_usd: number; cost_per_success: number | null;
  time: { wall_s: number; active_s: number; model_s: number; tool_s: number; waiting_owner_s: number; queue_wait_s: number; wall_p50_s: number | null; wall_p95_s: number | null };
  calls: number; errors: number; refused: number;
  tools: { name: string; calls: number; errors: number; refused: number; p50_ms: number | null; p95_ms: number | null }[];
  together: Record<"dispatches_sent" | "dispatches_received" | "messages_sent" | "messages_received" | "questions" | "answered" | "permission_asks" | "granted" | "denied" | "owner_turns" | "collaborators", number>;
  cache: { ratio: number | null; requests: number; cold_requests: number };
  retrieval: { calls: number; surfaced: number; cited: number; read_then_cited: number | null };
  quality?: QualityWindow;
}

/** An agent's suite: questions, each with the sources that answer it, and its last evals (the app's suites.py). */
export interface SuiteItem {
  id?: string; question: string; sources: string[]; group: "rag" | "stale"; held_out: boolean;
  /** a stale item's: the current answer, and the superseded values a reply should not give */
  answer?: string | null; superseded?: string[];
}
export interface EvalResult { item_id: string; run_id: string | null; state: "pending" | "queued" | "scored" | "failed"; hit_at_5: boolean | null; rr: number | null; seen: string[] }
export interface Eval {
  id: string; suite_id: string; agent_id: string; started_at: number; brief_version: number | null; held_out: boolean;
  items: number; scored: number; open: number; failed: number; hit_at_5: number | null; mrr: number | null;
  stale_accuracy?: number | null;
  by_group: Record<string, { items: number; hit_at_5: number; mrr: number | null }>; results: EvalResult[];
}
export interface Suite { id: string; agent_id: string; name: string; created_at: number; updated_at: number; items: SuiteItem[]; evals: Eval[] }

/** Every agent's numbers by day, as CSV, fetched with the account's token and saved. */
export async function saveNumbersCsv(days = 30) {
  const url = URL.createObjectURL(await fetchBlob(`${A}/metrics.csv?days=${days}`));
  const a = document.createElement("a");
  a.href = url;
  a.download = `agents-numbers-${days}-days.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** What starts an agent's runs beside Run now: a schedule the app keeps, or a hook a service posts to. */
export interface Trigger {
  id: string; agent_id: string; kind: "schedule" | "hook"; enabled: boolean; prompt: string | null;
  last_at: number | null; last_run_id: string | null; last_error: string | null; missed: number;
  cron?: string; tz?: string; grace_minutes?: number; next_at?: number | null;
  hook_type?: "github" | "bearer"; refused?: number; url?: string;
  /** a schedule's adherence: started on time, late (5 minutes or more past its time), skipped by its precheck */
  on_time?: number; late?: number; skipped?: number;
  /** a hook's secret: in the answer that made it, and never again */
  secret?: string;
}

/** The calendar's answer for a window of the account's triggers (the app's triggers.calendar):
 *  each trigger with its coming times, worked out by the scheduler's own code from its stored next time
 *  and at most `cap` of them, none while its agent is paused (from the time a pause lifts by itself); the firings the
 *  app's store records in the window; and the next ones due. */
export interface CalendarSchedule {
  id: string; agent_id: string; agent: string; kind: "schedule" | "hook"; enabled: boolean; prompt: string | null;
  agent_paused: boolean; agent_retired: boolean; unattended_mode: Unattended; max_runs_per_day: number;
  /** when a pause of its agent lifts by itself; null for one its owner made, which lasts until they resume it */
  agent_paused_until: number | null;
  /** what would hold a firing of it now, its agent's queue full among it, or null (none for one that is off) */
  held_now: string | null;
  coming: number[]; capped: boolean; past_capped: boolean;
  last_at: number | null; last_run_id: string | null; last_error: string | null;
  counts: { missed: number; on_time: number; late: number; refused: number; skipped: number };
  cron: string | null; tz: string | null; grace_minutes: number | null; next_at: number | null;
  hook_type?: "github" | "bearer";
  /** more of its line's times in a UTC day the window touches, counted whole, than its agent's day's runs; null: not
   *  known, the count past its bound, or a hook */
  more_than_a_day_runs: boolean | null;
  actions: ("switch" | "change" | "run_now")[];
}
export type FiringState = "queued" | "running" | "waits for a person" | "ran" | "failed" | "stopped" | "skipped" | "held" | "missed";
export interface CalendarFiring {
  trigger_id: string; agent_id: string; record: "run" | "trigger"; run_id: string | null;
  /** when it fired; null for a missed one, which fired no round */
  at: number | null;
  /** when its run started, if it has: the latest time the store keeps before a wait, which has none of its own */
  started_at: number | null;
  /** when it was due, where a record keeps it */
  due: number | null; late_seconds: number | null; run_now: boolean; state: FiringState; run_state: RunState | null;
  reason: string | null;
}
export interface CalendarDue { trigger_id: string; agent_id: string; agent: string; at: number; held_now: string | null }
export interface CalendarAnswer {
  from: number; to: number; now: number; cap: number; days: number; live: number;
  schedules: CalendarSchedule[]; firings: CalendarFiring[]; due_next: CalendarDue[];
}

/** How a run nobody watches (a schedule's or a hook's) may act. */
export type Unattended = "dontAsk" | "auto" | "bypassPermissions";
export const UNATTENDED_WORDS: Record<Unattended, string> = {
  dontAsk: "Only what its allow rules allow: it never waits, and a step they do not allow fails",
  auto: "A model reviews each action before it runs",
  bypassPermissions: "Everything, asking nothing (for an isolated machine, as a workspace's is)",
};

export interface AgentsTree { groups: { name: string; agents: Agent[] }[]; waiting: AgentRun[]; live: number; polled_at: number | null }

export interface AgentDraft {
  name: string; brief: string; repo: string | null; base_ref: string | null; from_session: string;
  chat_settings: Agent["chat_settings"]; candidates: string[];
}

export interface AgentInput {
  name: string; brief: string; group?: string | null; repo?: string | null; base_ref?: string | null;
  keep_worktree?: boolean; account?: string | null; chat_settings?: Agent["chat_settings"]; allow?: string[];
  limits?: Partial<AgentLimits>; from_session?: string | null; mcp_servers?: McpServer[]; connectors?: boolean;
  unattended_mode?: Unattended; precheck?: string | null; max_budget_usd?: number | null; est_usd_per_day?: number | null;
  autocompact_window?: number | null; max_compactions?: number;
  may_dispatch?: boolean; accepts_dispatch?: boolean; max_dispatch_per_run?: number;
  relations?: Record<string, "functional" | "multi">; decay_runs?: number; reverify?: boolean;
  anomaly_pause?: boolean; judge_runs?: boolean; acceptance?: AcceptanceSpec | null; improve?: boolean;
}

const A = "/api/ade/agents";
export const AgentsApi = {
  tree: () => api<AgentsTree>(A),
  one: (id: string, before?: number) => api<Agent>(`${A}/${id}${before ? `?before=${before}` : ""}`),
  create: (body: AgentInput) => api<Agent>(A, { method: "POST", body }),
  update: (id: string, body: Partial<AgentInput>) => api<Agent>(`${A}/${id}`, { method: "PUT", body }),
  pause: (id: string) => api<Agent>(`${A}/${id}/pause`, { method: "POST" }),
  resume: (id: string) => api<Agent>(`${A}/${id}/resume`, { method: "POST" }),
  retire: (id: string) => api<Agent>(`${A}/${id}/retire`, { method: "POST" }),
  run: (id: string, prompt?: string) => api<AgentRun>(`${A}/${id}/runs`, { method: "POST", body: { prompt: prompt || null } }),
  stop: (runId: string) => api<AgentRun>(`${A}/runs/${runId}/stop`, { method: "POST" }),
  thumbs: (runId: string, value: "up" | "down" | null, note?: string) =>
    api<AgentRun>(`${A}/runs/${runId}/thumbs`, { method: "POST", body: { value, note: note || null } }),
  draft: (sessionId: string) => api<AgentDraft>(`${A}/draft?session=${encodeURIComponent(sessionId)}`),
  search: (q: string, cursor?: string | null) =>
    api<{ servers: RegistryRow[]; next: string | null }>(`${A}/mcp-search?q=${encodeURIComponent(q)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
  acceptTools: (id: string, name: string) => api<Agent>(`${A}/${id}/tools/${encodeURIComponent(name)}/accept`, { method: "POST" }),
  secrets: (id: string) => api<Record<string, string[]>>(`${A}/${id}/mcp-secrets`),
  putSecrets: (id: string, secrets: Record<string, Record<string, string>>) =>
    api<Record<string, string[]>>(`${A}/${id}/mcp-secrets`, { method: "PUT", body: { secrets } }),
  settleProposal: (id: string, state: "added" | "dismissed") => api(`${A}/proposals/${id}`, { method: "POST", body: { state } }),
  runTree: (runId: string) => api<{ tree: { turns: TreeTurn[]; requests: number }; copy: boolean }>(`${A}/runs/${runId}/tree`),
  addTrigger: (id: string, body: Record<string, unknown>) => api<Trigger>(`${A}/${id}/triggers`, { method: "POST", body }),
  changeTrigger: (id: string, tid: string, body: Record<string, unknown>) => api<Trigger>(`${A}/${id}/triggers/${tid}`, { method: "PUT", body }),
  removeTrigger: (id: string, tid: string) => api(`${A}/${id}/triggers/${tid}`, { method: "DELETE" }),
  /** one firing of a schedule now, as its scheduler would fire it: unattended, held as a firing is */
  runTrigger: (id: string, tid: string) => api<{ run_id: string }>(`${A}/${id}/triggers/${tid}/run`, { method: "POST" }),
  calendar: (from: number, to: number) => api<CalendarAnswer>(`${A}/calendar?from=${from}&to=${to}`),
  numbers: (id: string, days: 7 | 30) => api<AgentWindow>(`${A}/${id}/metrics?days=${days}`),
  replay: (runId: string) => api<AgentRun>(`${A}/runs/${runId}/replay`, { method: "POST" }),
  improvements: () => api<Improvements>(`${A}/improvements`),
  approveImprovement: (id: string) => api<Improvement>(`${A}/improvements/${id}/approve`, { method: "POST" }),
  rejectImprovement: (id: string, reason: string) => api<Improvement>(`${A}/improvements/${id}/reject`, { method: "POST", body: { reason } }),
  removeSkill: (agentId: string, name: string) => api<Agent>(`${A}/${agentId}/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  judgeTrust: () => api<Record<string, JudgeTrust>>(`${A}/judge-trust`),
  suites: (id: string) => api<Suite[]>(`${A}/${id}/suites`),
  newSuite: (id: string, name: string, items: SuiteItem[]) => api<Suite>(`${A}/${id}/suites`, { method: "POST", body: { name, items } }),
  changeSuite: (suiteId: string, body: { name?: string; items?: SuiteItem[] }) => api<Suite>(`${A}/suites/${suiteId}`, { method: "PUT", body }),
  removeSuite: (suiteId: string) => api(`${A}/suites/${suiteId}`, { method: "DELETE" }),
  runEval: (suiteId: string, heldOut = false) => api<Suite>(`${A}/suites/${suiteId}/evals`, { method: "POST", body: { held_out: heldOut } }),
  facts: (id: string) => api<Facts>(`${A}/${id}/facts`),
  retractFact: (id: string, factId: string, reason: string) => api(`${A}/${id}/facts/${factId}/retract`, { method: "POST", body: { reason } }),
  memory: (id: string) => api<{ versions: MemoryVersion[]; now: MemoryNow | null }>(`${A}/${id}/memory`),
  memoryVersion: (id: string, version: number) => api<MemoryVersionFull>(`${A}/${id}/memory/${version}`),
  rollbackMemory: (id: string, version: number) => api(`${A}/${id}/memory/${version}/rollback`, { method: "POST" }),
  unarchiveMemory: (id: string, path: string) => api(`${A}/${id}/memory/unarchive`, { method: "POST", body: { path } }),
};

/** A browser's own timezone, as the IANA database names it: a new schedule's default. */
export const localZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };

/** A server from a registry entry's way of running it: an npm package at the version the entry names, or an address;
 *  what the entry marks secret is named secret, to be given on the page, never kept in the agent's record. */
export function fromRegistry(way: RegistryWay, name: string): McpServer | null {
  if (way.kind === "package" && way.identifier && way.version) {
    const env = way.env ?? [];
    return { name, kind: "package", registry: "npm", identifier: way.identifier, version: way.version, args: [],
             env: {}, secret_env: env.filter((e) => e.secret).map((e) => e.name) };
  }
  if (way.kind === "address" && way.url && (way.transport === "http" || way.transport === "sse")) {
    return { name, kind: "address", transport: way.transport, url: way.url, headers: {},
             secret_headers: (way.headers ?? []).filter((h) => h.secret).map((h) => h.name) };
  }
  return null;
}

/** A short name for a registry entry, as an agent's server is named: its last part, in the letters a name may have. */
export function serverName(registryName: string, taken: string[] = []): string {
  const base = (registryName.split("/").pop() || "server").replace(/[^A-Za-z0-9_.-]/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 60) || "server";
  let name = base;
  for (let i = 2; taken.includes(name) || name === "proofpane"; i++) name = `${base}-${i}`;
  return name;
}

/** How a server runs, in one line: its address, or its package at its version, or the repository's. */
export function serverLine(s: McpServer): string {
  if (s.kind === "address") return `${s.transport} ${s.url}`;
  if (s.kind === "package") return `npm ${s.identifier}@${s.version}${s.args?.length ? " " + s.args.join(" ") : ""}`;
  return "the repository's own, approved in a chat";
}

// a run's state in words, and the colour the console gives the same meaning
export const RUN_WORDS: Record<RunState, string> = {
  queued: "Queued", working: "Working", waiting: "Waits for you", done: "Done", failed: "Failed",
  blocked: "Blocked", stopped: "Stopped", skipped: "Skipped",
};
export const AGENT_WORDS: Record<AgentState, string> = {
  idle: "Idle", working: "Working", waiting: "Waits for you", paused: "Paused", retired: "Retired",
};
const COLOR: Record<RunState | AgentState, string> = {
  queued: "var(--color-idle)", working: "var(--color-run)", waiting: "var(--color-wait)", done: "var(--color-done)",
  failed: "var(--color-fail)", blocked: "var(--color-fail)", stopped: "var(--color-faint)", skipped: "var(--color-faint)",
  idle: "var(--color-idle)", paused: "var(--color-faint)", retired: "var(--color-faint)",
};
export const stateColor = (s: RunState | AgentState) => COLOR[s] ?? "var(--color-faint)";
export const LIVE: RunState[] = ["working", "waiting"];

/** How long a run took, or has been going: "45s", "3m 20s", "1h 4m". */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60 ? ` ${s % 60}s` : ""}`;
  const m = Math.floor((s % 3600) / 60);
  return `${Math.floor(s / 3600)}h${m ? ` ${m}m` : ""}`;
}

/** A run's length: its end less its start, or, while it goes, now less its start. */
export function runSeconds(r: Pick<AgentRun, "started_at" | "ended_at">, now = Date.now() / 1000): number | null {
  if (r.started_at == null) return null;
  return (r.ended_at ?? now) - r.started_at;
}

/** Tokens in one number: all four kinds, as the console counts a chat's. */
export function tokenCount(t: RunTokens | null | undefined): number {
  if (!t) return 0;
  return (t.input_tokens ?? 0) + (t.output_tokens ?? 0) + (t.cache_creation_input_tokens ?? 0) + (t.cache_read_input_tokens ?? 0);
}

/** 12,345 as "12.3k", 3,400,000 as "3.4M". */
export function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export type AgentSort = "state" | "last" | "cost";
const RANK: Record<AgentState, number> = { waiting: 0, working: 1, idle: 2, paused: 3, retired: 4 };

/** The agents of a group in the order asked: by state (waiting first), by their last run, or by today's est. cost. */
export function sortAgents(agents: Agent[], by: AgentSort): Agent[] {
  const out = [...agents];
  if (by === "state") out.sort((a, b) => RANK[a.state] - RANK[b.state] || a.name.localeCompare(b.name));
  else if (by === "last") out.sort((a, b) => (b.totals.last_run_at ?? 0) - (a.totals.last_run_at ?? 0) || a.name.localeCompare(b.name));
  else out.sort((a, b) => b.totals.est_usd_today - a.totals.est_usd_today || a.name.localeCompare(b.name));
  return out;
}

export type AgentFilter = "all" | "waiting" | "failed";

/** An agent shown under a filter: one waiting for the owner, or one whose last run failed or was blocked. */
export function matches(a: Agent, filter: AgentFilter): boolean {
  if (filter === "waiting") return a.state === "waiting";
  if (filter === "failed") return a.totals.last_state === "failed" || a.totals.last_state === "blocked";
  return true;
}

/** Whether anything is live, so the panel reads again soon. */
export const anyLive = (t: AgentsTree | null) => !!t && t.live > 0;

export const DRAFT_ASK = "Write the standing instruction you would give a successor who does this same job from now on, " +
  "with what you learned in this chat: what to do, what to check, and what to ask the owner about. Reply with the " +
  "instruction alone, in at most 4000 characters.";

/** One more turn of the chat, asking for the brief it would hand a successor; its reply's text. The chat must be
 *  idle: a chat at work takes no message. `wait` polls its log until the turn's result. */
export async function draftBrief(session: string, wait = (ms: number) => new Promise((r) => setTimeout(r, ms)), most = 300): Promise<string> {
  let after = 0;
  for (;;) {
    const log = await Api.chatEvents(session, after);
    for (const e of log.events) after = Math.max(after, e.seq);
    if (!log.more) break;
  }
  await Api.chatSend(session, DRAFT_ASK);
  const words: string[] = [];
  for (let i = 0; i < most; i++) {
    const log = await Api.chatEvents(session, after);
    for (const e of log.events) {
      after = Math.max(after, e.seq);
      if (e.kind === "text" && typeof e.text === "string") words.push(e.text);
      if (e.kind === "result") {
        if (e.error) throw new Error("The chat's turn ended with an error: write the brief yourself.");
        return words.join("\n\n").trim().slice(0, 4000);
      }
    }
    await wait(1000);
  }
  throw new Error("The chat did not answer in time: write the brief yourself.");
}

// a time near now by its clock alone, else with its day: a week's window resets days away
const near = (t: number) => (Math.abs(t - Date.now() / 1000) < 12 * 3600 ? clock(t) : dayTime(t));
const WINDOW_NAMES: Record<string, string> = {
  five_hour: "5h", seven_day: "7d", seven_day_opus: "7d Opus", seven_day_sonnet: "7d Sonnet", overage: "usage credits",
};

/** One of an account's windows as tasks' relays last recorded it (WindowView): "5h 92% at 14:02, resets
 *  16:00", its use being the SDK's fraction of the window; one out of room says until when, where its reset is known. */
export function windowWords(w: WindowView): string {
  const name = WINDOW_NAMES[w.window] ?? w.window;
  if (w.status === "rejected") return `${name} out of room ${w.resets_at ? `until ${near(w.resets_at)}` : "with no reset known"} (at ${near(w.at)})`;
  const used = w.utilization != null ? ` ${Math.round(w.utilization * 100)}%` : "";
  return `${name}${used} at ${near(w.at)}${w.resets_at ? `, resets ${near(w.resets_at)}` : ""}`;
}

/** An account's windows as last recorded, or "unknown" when none was. */
export function windowsWords(ws: WindowView[] | null | undefined): string {
  return ws && ws.length ? ws.map(windowWords).join(" · ") : "unknown";
}

/** "just now", "5m ago", "3h ago": when something happened, from its unix time. */
export function ago(unix: number | null | undefined, now = Date.now()): string {
  if (!unix) return "";
  const s = Math.max(0, Math.round(now / 1000 - unix));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
