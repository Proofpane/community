// The Skills view reads the app's skill routes, under /api/ade/skills
// (proofpane_open/ade/skill_counts.py and skill_routes.py). Each answer is kept to the fields that module names, each of
// its type, as it is read (the parsers below), as the controller's view is (controller.ts): a field the app adds never
// reaches the view until a release names it, and an answer of another shape draws nothing rather than breaking the view.
// What the counts hold is the sessions' word, for display: the words here that say so, and
// that a count can miss calls, are the view's own and are drawn whatever the answer holds.
import { api } from "./api";
import { dayTime } from "./when";

export type SkillKind = "core" | "business" | "other";
export const KINDS: SkillKind[] = ["core", "business", "other"];
/** The days a count is over: the last 7, 30 or 90 UTC days, today's among them (ade/skill_counts.WINDOWS). */
export type Window = 7 | 30 | 90;
export const WINDOWS: Window[] = [7, 30, 90];
export const WINDOW_DEFAULT: Window = 30;

/** How the tasks that called a skill stand: done, failed or stopped as they ended, still open, or not known to the app
 *  (a task it has no record of). Counted by task, never by call. */
export interface Ended { done: number; failed: number; stopped: number; open: number; unknown: number }

/** One row of the view: a skill and what the window holds of it. A business skill is a row for each repository it was
 *  called in (`repo`: none for calls made in no repository); a core or another skill is one row, its `repo` none. */
export interface SkillRow {
  kind: SkillKind;
  skill: string;
  repo: string | null;
  calls: number;
  failed: number;
  typed: number;
  sessions: number;
  repos: number;
  tasks: number;
  requirements: number;
  versions: number;
  latest_version: string | null;
  first_at: number | null;
  last_at: number | null;
  ended: Ended;
}

/** The calls of one UTC day, by kind. */
export interface DayCount { day: string; core: number; business: number; other: number }

/** How the app's reads of the workspace's record stand (null before the app has read it once). `missed`, `dropped` and
 *  `refused` are the counts of lines never counted, and `trimmed` of calls counted and then dropped past the most the
 *  app keeps for one owner (the view says each); `error` is the app's own words. */
export interface Reads {
  at: number | null;
  behind_at: number | null;
  error: string | null;
  error_at: number | null;
  unserved_at: number | null;
  missed: number;
  dropped: number;
  refused: number;
  trimmed: number;
  since_at: number | null;
}

export interface Totals { calls: number; failed: number; typed: number; skills: number; sessions: number; first_at: number | null; last_at: number | null }

export interface SkillsView {
  days: Window;
  from_day: string;
  to_day: string;
  now: number;
  read: Reads | null;
  totals: Totals;
  by_day: DayCount[];
  skills: SkillRow[];
  more: number;
}

export interface DetailVersion { version: string | null; calls: number; first_at: number | null; last_at: number | null }
export interface DetailRepo { repo: string | null; calls: number }
export interface DetailSession { session: string; calls: number; first_at: number | null; last_at: number | null }
export interface DetailTask { task: string; calls: number; last_at: number | null; state: string | null; requirement: string | null; ended_at: number | null }
export interface DetailRequirement { requirement: string; title: string | null; calls: number; tasks: number }
/** Which of the detail's lists are cut: the app lists the sessions and tasks last called, the requirements and repositories
 *  with the most calls and the versions last called, and says when there were more. */
export interface DetailMore { sessions: boolean; tasks: boolean; requirements: boolean; versions: boolean; repos: boolean }

/** One skill over the window: GET /detail. */
export interface SkillDetail {
  kind: SkillKind;
  skill: string;
  repo: string | null;
  days: Window;
  from_day: string;
  to_day: string;
  calls: number;
  failed: number;
  typed: number;
  first_at: number | null;
  last_at: number | null;
  by_day: { day: string; calls: number; failed: number }[];
  versions: DetailVersion[];
  repos: DetailRepo[];
  sessions: DetailSession[];
  tasks: DetailTask[];
  requirements: DetailRequirement[];
  ended: Ended;
  more: DetailMore;
}

export const READ_STATES = ["read", "behind", "unserved", "failed", "busy", "not_running"] as const;
/** How the app's read of the workspace went when the owner asked for it (POST /read), and how many lines it took. */
export interface ReadResult { state: (typeof READ_STATES)[number]; taken: number }

// --- parsers: each field kept of its type ----------------------------------------------------------------------------
const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);
const fields = (v: unknown): Record<string, unknown> =>
  (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const list = (v: unknown): Record<string, unknown>[] =>
  (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x)) : []);
const kindOf = (v: unknown): SkillKind | null => (v === "core" || v === "business" || v === "other" ? v : null);
const windowOf = (v: unknown): Window => (v === 7 || v === 30 || v === 90 ? v : WINDOW_DEFAULT);

function moreOf(raw: unknown): DetailMore {
  const m = fields(raw);
  return { sessions: m.sessions === true, tasks: m.tasks === true, requirements: m.requirements === true, versions: m.versions === true,
    repos: m.repos === true };
}

function endedOf(raw: unknown): Ended {
  const e = fields(raw);
  return { done: num(e.done), failed: num(e.failed), stopped: num(e.stopped), open: num(e.open), unknown: num(e.unknown) };
}

function rowOf(raw: Record<string, unknown>): SkillRow | null {
  const kind = kindOf(raw.kind);
  const skill = text(raw.skill);
  if (!kind || !skill) return null;               // a row of no kind or no name draws nothing
  return {
    kind, skill, repo: kind === "business" ? text(raw.repo) : null, calls: num(raw.calls), failed: num(raw.failed),
    typed: num(raw.typed), sessions: num(raw.sessions), repos: num(raw.repos), tasks: num(raw.tasks),
    requirements: num(raw.requirements), versions: num(raw.versions), latest_version: text(raw.latest_version),
    first_at: count(raw.first_at), last_at: count(raw.last_at), ended: endedOf(raw.ended),
  };
}

function readsOf(raw: unknown): Reads | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  return {
    at: count(r.at), behind_at: count(r.behind_at), error: text(r.error), error_at: count(r.error_at),
    unserved_at: count(r.unserved_at), missed: num(r.missed), dropped: num(r.dropped), refused: num(r.refused),
    trimmed: num(r.trimmed), since_at: count(r.since_at),
  };
}

export function viewOf(raw: unknown): SkillsView {
  const v = fields(raw);
  const t = fields(v.totals);
  return {
    days: windowOf(v.days), from_day: str(v.from_day), to_day: str(v.to_day), now: num(v.now), read: readsOf(v.read),
    totals: {
      calls: num(t.calls), failed: num(t.failed), typed: num(t.typed), skills: num(t.skills), sessions: num(t.sessions),
      first_at: count(t.first_at), last_at: count(t.last_at),
    },
    by_day: list(v.by_day).map((d) => ({ day: str(d.day), core: num(d.core), business: num(d.business), other: num(d.other) })),
    skills: list(v.skills).map(rowOf).filter((r): r is SkillRow => r !== null),
    more: num(v.more),
  };
}

export function detailOf(raw: unknown): SkillDetail {
  const d = fields(raw);
  return {
    kind: kindOf(d.kind) ?? "other", skill: str(d.skill), repo: text(d.repo), days: windowOf(d.days),
    from_day: str(d.from_day), to_day: str(d.to_day), calls: num(d.calls), failed: num(d.failed), typed: num(d.typed),
    first_at: count(d.first_at), last_at: count(d.last_at),
    by_day: list(d.by_day).map((x) => ({ day: str(x.day), calls: num(x.calls), failed: num(x.failed) })),
    versions: list(d.versions).map((x) => ({ version: text(x.version), calls: num(x.calls), first_at: count(x.first_at), last_at: count(x.last_at) })),
    repos: list(d.repos).map((x) => ({ repo: text(x.repo), calls: num(x.calls) })),
    sessions: list(d.sessions).map((x) => ({ session: str(x.session), calls: num(x.calls), first_at: count(x.first_at), last_at: count(x.last_at) })),
    tasks: list(d.tasks).map((x) => ({
      task: str(x.task), calls: num(x.calls), last_at: count(x.last_at), state: text(x.state), requirement: text(x.requirement),
      ended_at: count(x.ended_at),
    })),
    requirements: list(d.requirements).map((x) => ({ requirement: str(x.requirement), title: text(x.title), calls: num(x.calls), tasks: num(x.tasks) })),
    ended: endedOf(d.ended), more: moreOf(d.more),
  };
}

export function readResultOf(raw: unknown): ReadResult {
  const r = fields(raw);
  const state = READ_STATES.find((s) => s === r.state) ?? "failed";
  return { state, taken: num(r.taken) };
}

const P = "/api/ade/skills";
const enc = encodeURIComponent;

/** A row's detail asks for its kind, its name and, for a business skill, its repository (an empty one for calls made in
 *  no repository: the route reads it as none). */
export function detailPath(row: { kind: SkillKind; skill: string; repo: string | null }, days: Window): string {
  const repo = row.kind === "business" ? `&repo=${enc(row.repo ?? "")}` : "";
  return `${P}/detail?kind=${row.kind}&skill=${enc(row.skill)}${repo}&days=${days}`;
}

export const SkillsApi = {
  view: (days: Window, signal?: AbortSignal) => api<unknown>(`${P}?days=${days}`, { signal }).then(viewOf),
  detail: (row: { kind: SkillKind; skill: string; repo: string | null }, days: Window, signal?: AbortSignal) =>
    api<unknown>(detailPath(row, days), { signal }).then(detailOf),
  read: (signal?: AbortSignal) => api<unknown>(`${P}/read`, { method: "POST", signal }).then(readResultOf),
};

// --- the words and the grouping the view draws ------------------------------------------------------------------------

/** A row's identity among the rows: a skill of the same name is one row for each kind, and a business one for each
 *  repository. */
export const rowKey = (r: { kind: SkillKind; skill: string; repo: string | null }) => `${r.kind}\u0000${r.skill}\u0000${r.repo ?? ""}`;

/** The rows as the view groups them: the core skills, the business skills by repository (the repository with the most
 *  calls first, the calls made in no repository last), and the others. Each group keeps the order the rows came in. */
export function groupsOf(rows: SkillRow[]): { core: SkillRow[]; business: { repo: string | null; calls: number; rows: SkillRow[] }[]; other: SkillRow[] } {
  const repos = new Map<string, { repo: string | null; calls: number; rows: SkillRow[] }>();
  for (const r of rows) {
    if (r.kind !== "business") continue;
    const key = r.repo ?? "";
    const g = repos.get(key) ?? { repo: r.repo, calls: 0, rows: [] };
    g.calls += r.calls;
    g.rows.push(r);
    repos.set(key, g);
  }
  const business = [...repos.values()].sort((a, b) =>
    (a.repo === null ? 1 : 0) - (b.repo === null ? 1 : 0) || b.calls - a.calls || (a.repo ?? "").localeCompare(b.repo ?? ""));
  return { core: rows.filter((r) => r.kind === "core"), business, other: rows.filter((r) => r.kind === "other") };
}

const HEX = /^[0-9a-f]+$/;
const shortText = (s: string, most = 40) => (s.length <= most ? s : `${s.slice(0, most - 3)}...`);

/** A version as the view shows it: a commit by its first 12 characters, a core skill's release with the first 12 of its
 *  folder's digest, and in words the versions that are not one (uncommitted changes, unknown, a folder too large to
 *  digest, none). Whatever else the answer holds is shown cut and as text. */
export function versionWords(kind: SkillKind, v: string | null): string {
  if (v === null) return kind === "other" ? "none: only a repository's own skills and the core skills have one" : "none recorded";
  if (kind === "business") {
    if (v === "uncommitted") return "uncommitted changes in its folder";
    if (v === "unknown") return "not known";
    return HEX.test(v) ? v.slice(0, 12) : shortText(v);
  }
  const at = v.lastIndexOf("+");
  if (kind === "core" && at > 0) {
    const release = v.slice(0, at);
    const rest = v.slice(at + 1);
    if (rest === "cut") return `${shortText(release, 32)} (its folder was too large to digest)`;
    if (rest === "unknown") return `${shortText(release, 32)} (its folder could not be read)`;
    if (HEX.test(rest)) return `${shortText(release, 32)}+${rest.slice(0, 12)}`;
  }
  return shortText(v);
}

/** How the tasks that called a skill stand, in words: the parts that are not none. */
export function endedWords(e: Ended): string {
  const parts = [
    e.done ? `${e.done} done` : "", e.failed ? `${e.failed} failed` : "", e.stopped ? `${e.stopped} stopped` : "",
    e.open ? `${e.open} still open` : "", e.unknown ? `${e.unknown} not known to the app` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "none: no Manager task called it";
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What a row says of where its calls were made: the sessions, repositories, tasks and requirements, the parts that are
 *  not none. A business skill's row is of one repository, so it does not name how many. */
export function whereWords(r: SkillRow): string {
  const parts = [plural(r.sessions, "session")];
  if (r.kind !== "business") parts.push(plural(r.repos, "repository", "repositories"));
  parts.push(plural(r.tasks, "task"), plural(r.requirements, "requirement"));
  return parts.join(" · ");
}

/** The lines the view says about how the app's reads stand: when it last reached the end of the workspace's record,
 *  whether it is behind, why a read failed and since when, a workspace that does not count skill calls, and what the
 *  last read the owner asked for did. */
export function readNotes(reads: Reads | null, last: ReadResult | null): string[] {
  const out: string[] = [];
  if (reads === null) {
    out.push("The app has not read the workspace's record of skill calls yet.");
  } else {
    if (reads.unserved_at !== null) {
      out.push("This workspace does not count skill calls yet: it is older than this page, so there is nothing to show from it until it is updated.");
    }
    if (reads.error) out.push(`Reading the workspace's record failed${reads.error_at ? ` (since ${dayTime(reads.error_at)})` : ""}: ${reads.error}`);
    if (reads.behind_at !== null && (reads.at === null || reads.behind_at > reads.at)) {
      out.push("The app has not yet read to the end of the workspace's record, so these counts may be short.");
    }
    if (reads.at !== null) out.push(`The app last read to the end of the workspace's record at ${dayTime(reads.at)}.`);
  }
  if (last?.state === "not_running") out.push("The workspace is not running, so nothing was read just now: the counts are as the app last read them.");
  if (last?.state === "busy") out.push("A read is already under way: the counts update when it ends.");
  return out;
}

/** What the counts are known to have missed, in words, and the reminder that some misses leave no trace. */
export function missedWords(reads: Reads | null): string {
  const parts: string[] = [];
  if (reads && reads.missed > 0) parts.push(`${plural(reads.missed, "call")} the workspace dropped before the app read ${reads.missed === 1 ? "it" : "them"}`);
  if (reads && reads.dropped > 0) parts.push(`${plural(reads.dropped, "line")} the workspace held out of shape`);
  if (reads && reads.refused > 0) parts.push(`${plural(reads.refused, "line")} the app refused as out of shape`);
  if (reads && reads.trimmed > 0) parts.push(`${plural(reads.trimmed, "call")} the app dropped past the most it keeps, the oldest first`);
  return parts.length
    ? `Known to be missed so far: ${parts.join("; ")}. Others leave no trace.`
    : "None known to be missed so far, but a line lost to a cut, or a session removed before its file was read, leaves no trace.";
}

/** The UTC day (YYYY-MM-DD) the app began reading the workspace's record for this owner (`since_at`), or null where
 *  it has not read yet. */
export function countingSince(reads: Reads | null): string | null {
  if (!reads || reads.since_at === null) return null;
  const at = new Date(reads.since_at * 1000);
  // a number out of the range of dates is none
  return Number.isNaN(at.getTime()) ? null : at.toISOString().slice(0, 10);
}

/** Whether a day of the chart is before the app began reading: such a day holds only the calls the workspace still
 *  held at that first read (it keeps 30 days), so a day of none there may be a day nothing was counted, not a day of
 *  no calls. */
export function beforeCounting(day: string, reads: Reads | null): boolean {
  const since = countingSince(reads);
  return since !== null && day < since;
}

/** What the chart says of the days before the app began reading, or null where the window holds none of them. */
export function countingWords(reads: Reads | null, fromDay: string): string | null {
  const since = countingSince(reads);
  if (since === null || since <= fromDay) return null;
  return `The app began reading the workspace's record on ${since} (UTC). The days before it are drawn faint: `
    + "they hold only the calls the workspace still held at that first read (it keeps 30 days), so a day of none "
    + "there may be a day nothing was counted.";
}

/** The height of a day's bar as a fraction of the window's tallest, 0 to 1 (a day with calls is never drawn as none). */
export function barHeight(day: DayCount, most: number): number {
  const n = day.core + day.business + day.other;
  return n === 0 || most === 0 ? 0 : Math.max(0.04, n / most);
}

export const dayTotal = (d: DayCount) => d.core + d.business + d.other;

// --- the improve loop's proposals for a repository's business skills -------------------------------------------------
// (proofpane_open/ade/skill_improve.py and skill_improve_routes.py, under
// /api/ade/skill-improve). Read as the counts are: each field kept of its type, an answer of another shape drawing
// nothing. Every text a model or a session wrote (a hypothesis, a SKILL.md, a reason a workspace gave) is shown as text.

export const PROPOSAL_STATES = ["critic", "open", "refused", "pruned", "pull_request", "merged", "found", "outdated",
  "changed", "rejected", "expired", "withdrawn"] as const;
export type ProposalState = (typeof PROPOSAL_STATES)[number];
export type ProposalWay = "merge" | "remake" | "pull_request" | "reject" | "take" | "go_back";
const WAYS: ProposalWay[] = ["merge", "remake", "pull_request", "reject", "take", "go_back"];

/** The counts a proposal carries, for the whole, the current version and the version before (none where there is no
 *  such version): the sessions' word, for display. */
export interface ProposalCounts {
  calls: number; typed: number; failed: number; tasks_done: number; tasks_failed: number; tasks_stopped: number;
  tasks_open: number; tasks_unknown: number; tasks_checks_failed: number;
}

/** A build of Claude Code the workspace would start, as one read found it: its version (none where it could not be
 *  read, and why), and whether it is newer than the newest the checks were read on. */
export interface ProposalBuild { claude: string | null; version: string | null; newer: boolean; why: string | null }

/** One read of the builds: at the review, at a view of the proposal, or at its acceptance. */
export interface BuildsRead { when: "review" | "view" | "acceptance"; at: number | null; builds: ProposalBuild[] }

export interface SkillProposal {
  id: string; repo: string; folder: string; path: string; kind: "change" | "back"; branch: string | null;
  base: string | null; head: string | null; heads: { base: string | null; head: string | null; at: number | null }[];
  current_version: string | null; before_version: string | null; fresh: boolean | null; hypothesis: string | null;
  counts: { whole: ProposalCounts | null; current: ProposalCounts | null; before: ProposalCounts | null } | null;
  state: ProposalState; reason: string | null; pr_url: string | null;
  found: { by: string | null; commit: string | null } | null; rolls_back: string | null;
  ended: "merged" | "taken" | "back" | null; created_at: number | null; decided_at: number | null;
  merged_at: number | null; expires_at: number | null;
  notes: { keys: string[]; builds: ProposalBuild[]; newer: boolean; linked: boolean; url: string | null;
    critic: { specific: boolean; why: string | null } | null; builds_read: string[]; reads: BuildsRead[];
    merge: { brought_besides: string[]; checked: boolean; why: string | null; first_parent_was_head: boolean | null;
      changed_to_blob: boolean | null } | null };
  diff: string[]; ways: ProposalWay[];
  bars_now: { refused: string | null; head: string | null; branch: string | null; at: number | null } | null;
}

export interface LoopBudget { sessions: number; usd: number; max_sessions: number; max_usd: number; spent: boolean; by_kind: Record<string, number> }
export interface RepoSwitch { repo: string; on: boolean; account: string | null; model: string | null; url: string | null; branch: string | null; changed_at: number | null }
export interface SkillLoop { proposals: SkillProposal[]; budget: LoopBudget; switches: RepoSwitch[]; copy: { generation: number; read_at: number | null } | null }
export interface RefusedStart { at: number | null; kind: string; repo: string | null; proposal: string | null; why: string }
/** A refused start's kind as the page says it: the server's `controller` is the Manager. */
export const startKindWords = (kind: string): string => (kind === "controller" ? "Manager" : kind);
export interface RecordState {
  running: boolean;
  record: { state: string | null; held: string | null; word: boolean; generation: number | null; refused: RefusedStart[]; error: string | null } | null;
  copy: { generation: number; read_at: number | null } | null;
}
export interface Candidates { ok: boolean; why: string | null; candidates: { folder: string; names: string[] }[]; not: Record<string, string> }

const buildOf = (b: Record<string, unknown>): ProposalBuild =>
  ({ claude: text(b.claude), version: text(b.version), newer: b.newer === true, why: text(b.why) });

const countsOf = (raw: unknown): ProposalCounts | null => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const c = raw as Record<string, unknown>;
  return { calls: num(c.calls), typed: num(c.typed), failed: num(c.failed), tasks_done: num(c.tasks_done),
    tasks_failed: num(c.tasks_failed), tasks_stopped: num(c.tasks_stopped), tasks_open: num(c.tasks_open),
    tasks_unknown: num(c.tasks_unknown), tasks_checks_failed: num(c.tasks_checks_failed) };
};

export function proposalOf(raw: Record<string, unknown>): SkillProposal | null {
  const state = PROPOSAL_STATES.find((s) => s === raw.state);
  const id = text(raw.id);
  if (!state || !id) return null;
  const n = fields(raw.notes);
  const c = raw.counts && typeof raw.counts === "object" ? fields(raw.counts) : null;
  const critic = n.critic && typeof n.critic === "object" ? fields(n.critic) : null;
  const found = raw.found && typeof raw.found === "object" ? fields(raw.found) : null;
  const bars = raw.bars_now && typeof raw.bars_now === "object" ? fields(raw.bars_now) : null;
  const merge = n.merge && typeof n.merge === "object" ? fields(n.merge) : null;
  const ended = raw.ended === "merged" || raw.ended === "taken" || raw.ended === "back" ? raw.ended : null;
  return {
    id, repo: str(raw.repo), folder: str(raw.folder), path: str(raw.path), kind: raw.kind === "back" ? "back" : "change",
    branch: text(raw.branch), base: text(raw.base), head: text(raw.head),
    heads: list(raw.heads).map((h) => ({ base: text(h.base), head: text(h.head), at: count(h.at) })),
    current_version: text(raw.current_version), before_version: text(raw.before_version),
    fresh: typeof raw.fresh === "boolean" ? raw.fresh : null, hypothesis: text(raw.hypothesis),
    counts: c ? { whole: countsOf(c.whole), current: countsOf(c.current), before: countsOf(c.before) } : null,
    state, reason: text(raw.reason), pr_url: text(raw.pr_url),
    found: found ? { by: text(found.by), commit: text(found.commit) } : null, rolls_back: text(raw.rolls_back), ended,
    created_at: count(raw.created_at), decided_at: count(raw.decided_at), merged_at: count(raw.merged_at),
    expires_at: count(raw.expires_at),
    notes: {
      keys: Array.isArray(n.keys) ? n.keys.filter((k): k is string => typeof k === "string") : [],
      builds: list(n.builds).map(buildOf),
      newer: n.newer === true, linked: n.linked === true, url: text(n.url),
      critic: critic ? { specific: critic.specific === true, why: text(critic.why) } : null,
      builds_read: Array.isArray(n.builds_read) ? n.builds_read.filter((v): v is string => typeof v === "string") : [],
      reads: list(n.reads).filter((r) => r.when === "review" || r.when === "view" || r.when === "acceptance")
        .map((r) => ({ when: r.when as BuildsRead["when"], at: count(r.at), builds: list(r.builds).map(buildOf) })),
      merge: merge ? { brought_besides: Array.isArray(merge.brought_besides)
        ? merge.brought_besides.filter((f): f is string => typeof f === "string") : [],
        checked: merge.checked !== false, why: text(merge.why),
        first_parent_was_head: typeof merge.first_parent_was_head === "boolean" ? merge.first_parent_was_head : null,
        changed_to_blob: typeof merge.changed_to_blob === "boolean" ? merge.changed_to_blob : null } : null,
    },
    diff: Array.isArray(raw.diff) ? raw.diff.filter((l): l is string => typeof l === "string") : [],
    ways: Array.isArray(raw.ways) ? raw.ways.filter((w): w is ProposalWay => WAYS.includes(w as ProposalWay)) : [],
    bars_now: bars ? { refused: text(bars.refused), head: text(bars.head), branch: text(bars.branch), at: count(bars.at) } : null,
  };
}

const switchOf = (s: Record<string, unknown>): RepoSwitch => ({
  repo: str(s.repo), on: s.on === true, account: text(s.account), model: text(s.model), url: text(s.url),
  branch: text(s.branch), changed_at: count(s.changed_at),
});

export function loopOf(raw: unknown): SkillLoop {
  const v = fields(raw);
  const b = fields(v.budget);
  const byKind: Record<string, number> = {};
  for (const [k, n] of Object.entries(fields(b.by_kind))) byKind[k] = num(n);
  const copy = v.copy && typeof v.copy === "object" ? fields(v.copy) : null;
  return {
    proposals: list(v.proposals).map(proposalOf).filter((p): p is SkillProposal => p !== null),
    budget: { sessions: num(b.sessions), usd: num(b.usd), max_sessions: num(b.max_sessions), max_usd: num(b.max_usd),
      spent: b.spent === true, by_kind: byKind },
    switches: list(v.switches).map(switchOf),
    copy: copy ? { generation: num(copy.generation), read_at: count(copy.read_at) } : null,
  };
}

export function recordStateOf(raw: unknown): RecordState {
  const v = fields(raw);
  const r = v.record && typeof v.record === "object" ? fields(v.record) : null;
  const copy = v.copy && typeof v.copy === "object" ? fields(v.copy) : null;
  return {
    running: v.running === true,
    record: r ? {
      state: text(r.state), held: text(r.held), word: r.word === true, generation: count(r.generation), error: text(r.error),
      refused: list(r.refused).map((x) => ({ at: count(x.at), kind: str(x.kind), repo: text(x.repo), proposal: text(x.proposal), why: str(x.why) })),
    } : null,
    copy: copy ? { generation: num(copy.generation), read_at: count(copy.read_at) } : null,
  };
}

export function candidatesOf(raw: unknown): Candidates {
  const v = fields(raw);
  const not: Record<string, string> = {};
  for (const [k, w] of Object.entries(fields(v.not))) if (typeof w === "string") not[k] = w;
  return {
    ok: v.ok === true, why: text(v.why), not,
    candidates: list(v.candidates).map((c) => ({ folder: str(c.folder),
      names: Array.isArray(c.names) ? c.names.filter((x): x is string => typeof x === "string") : [] })),
  };
}

/** Why a business skill's row may not be proposed for, as the workspace said of the candidates: none where its
 *  folder is a candidate, or where the workspace said nothing of it; a folder is matched by its name or by a name it is
 *  called by. Where no skill of the repository may be (the answer is not ok), the switch's row says why, not each row. */
export function notCandidate(skill: string, cands: Candidates | null): string | null {
  if (!cands || !cands.ok) return null;
  if (cands.candidates.some((c) => c.folder === skill || c.names.includes(skill))) return null;
  return Object.prototype.hasOwnProperty.call(cands.not, skill) ? cands.not[skill] : null;
}

const L = "/api/ade/skill-improve";
export const SkillImproveApi = {
  loop: (signal?: AbortSignal) => api<unknown>(L, { signal }).then(loopOf),
  record: (signal?: AbortSignal) => api<unknown>(`${L}/record`, { signal }).then(recordStateOf),
  restore: () => api<unknown>(`${L}/record/restore`, { method: "POST" }),
  turn: (repo: string, on: boolean, account: string | null = null, model: string | null = null) =>
    api<unknown>(`${L}/repos/${enc(repo)}/switch`, { method: "PUT", body: { on, account, model } }).then((s) => switchOf(fields(s))),
  candidates: (repo: string) => api<unknown>(`${L}/repos/${enc(repo)}/candidates`).then(candidatesOf),
  proposal: (id: string) => api<unknown>(`${L}/proposals/${enc(id)}`).then((p) => proposalOf(fields(p))),
  act: (id: string, way: ProposalWay, body?: unknown) => {
    const path = way === "pull_request" ? "pull-request" : way === "go_back" ? "go-back" : way;
    return api<unknown>(`${L}/proposals/${enc(id)}/${path}`, { method: "POST", body: body ?? {} }).then((p) => proposalOf(fields(p)));
  },
};

export const PROPOSAL_STATE_WORDS: Record<ProposalState, string> = {
  critic: "with the critic", open: "for you to decide", refused: "refused", pruned: "pruned",
  pull_request: "its pull request is open", merged: "merged by you", found: "found on the branch, not merged by you",
  outdated: "outdated: its skill changed on the branch", changed: "changed: its ref was moved", rejected: "rejected",
  expired: "expired", withdrawn: "withdrawn: its repository's switch was turned off",
};

/** A count's words, the parts that are not none: the sessions' word, for display. */
export function proposalCountWords(c: ProposalCounts | null): string {
  if (!c) return "no such version";
  const parts = [plural(c.calls, "call"), c.failed ? `${c.failed} failed` : "", c.typed ? `${c.typed} typed by a person` : "",
    c.tasks_checks_failed ? `${plural(c.tasks_checks_failed, "task")} whose last checks did not pass` : "",
    c.tasks_failed ? `${plural(c.tasks_failed, "task")} failed` : "", c.tasks_stopped ? `${plural(c.tasks_stopped, "task")} stopped` : ""];
  return parts.filter(Boolean).join(", ");
}

/** What the page says beside a proposal, whatever else it holds: each note that applies, in order. */
const readWords = (r: BuildsRead): string =>
  r.when === "review" ? "the review" : r.when === "acceptance" ? "the acceptance"
    : `the view of ${r.at === null ? "an unknown time" : new Date(r.at * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC`;

export function proposalNotes(p: SkillProposal): string[] {
  const holding = p.fresh === false
    ? "its commit (its text is one the branch held before, so the text alone bars nothing)"
    : "its commit or, at its path, its text, byte for byte (the same instructions written otherwise, with other line "
      + "ends say, are not barred)";
  const out: string[] = [
    "Its counts are the sessions' word, for display; a task that called both versions is counted at each.",
    ...(p.kind === "change" ? ["Its hypothesis is a model's words."] : []),
    "It was not tried: no sandbox replays a business skill's change, and nothing calls it tested.",
    "Its SKILL.md is read as the project's text, whoever wrote it: a session's work can have written it.",
    `Until you merge it, no start the workspace makes unattended in ${p.repo || "this repository"}, under any name the `
      + `workspace's repositories give it, begins from a base or a worktree holding ${holding}.`
      + (p.fresh === false ? "" : " A worktree is read just before its start begins: a program that writes the text "
        + "into it after that read is not seen.")
      + " Your own New session, a session already running, a copy of the text at another path, a copy of the "
      + "repository and another repository are not covered.",
    "A skill with no construct still runs a command and reads a file its arguments name, and the gate never sees a "
      + "command the command scan runs.",
  ];
  if (p.notes.keys.length) out.push(`The skill holds ${p.notes.keys.join(", ")}: the change alters when ${p.notes.keys.length === 1 ? "it applies" : "they apply"}.`);
  out.push(`Its checks were read on the builds ${p.notes.builds_read.length ? p.notes.builds_read.join(", ") : "this server does not name"}; a session of another build may read the skill otherwise.`);
  for (const r of p.notes.reads) {
    const newer = r.builds.filter((b) => b.newer && b.version).map((b) => b.version as string);
    if (newer.length) out.push(`A newer build of Claude Code than those, ${newer.join(", ")}, was found at ${readWords(r)}: it may read the skill otherwise.`);
    for (const b of r.builds.filter((x) => !x.version)) {
      out.push(`At ${readWords(r)}, a claude's version could not be read${b.claude ? ` (${b.claude})` : ""}: ${b.why ?? "it did not say"}.`);
    }
  }
  if (p.counts && (!p.counts.current || p.counts.current.calls === 0)) {
    out.push(p.notes.linked ? "Its current version has no calls."
      : "Its current version has no calls: a clone's sessions call a newer version only once brought up to date.");
  }
  if (!p.notes.linked) out.push("For a clone, sessions call a merged version only once brought up to date.");
  if (p.state === "merged" && p.notes.linked) out.push("Your hooks did not run for this merge.");
  if (p.state === "merged" && !p.notes.linked && p.notes.merge) {
    const m = p.notes.merge;
    if (!m.checked) out.push(`What the merge on GitHub brought was not checked: ${m.why ?? "it did not say"}.`);
    else {
      if (m.brought_besides.length) out.push(`The merge on GitHub brought besides: ${m.brought_besides.join(", ")}.`);
      if (m.first_parent_was_head === false) out.push("The merge on GitHub's commit does not have the branch's head before it as its first parent.");
      if (m.changed_to_blob === false) out.push(`The merge on GitHub did not change ${p.path} to the proposal's text.`);
    }
  }
  if (p.state === "found") {
    out.push("It reached the branch without your tap: it is not your acceptance.");
    if (p.bars_now) {
      // what the shared repository has checked out, where the workspace read it, not the proposal's branch, and when it
      // was read, the list viewing a repository once while it shows
      const from = p.bars_now.branch ?? "the commit checked out";
      const when = p.bars_now.at === null ? "now" : `as read at ${new Date(p.bars_now.at * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC`;
      out.push(p.bars_now.refused
        ? `Unattended starts from ${from} in ${p.repo} are refused ${when}: ${p.bars_now.refused}`
        : `Unattended starts from ${from} in ${p.repo} are not refused ${when}.`);
    }
  }
  if ((p.state === "outdated" || p.state === "changed" || p.state === "rejected") && p.reason) out.push(`Why: ${p.reason}`);
  if (p.state === "pull_request") out.push("A pull request left open on GitHub is left there: the loop closes nothing on a remote.");
  return out;
}

export const WAY_WORDS: Record<ProposalWay, string> = {
  merge: "Merge", remake: "Bring it up to date", pull_request: "Open a pull request", reject: "Reject",
  take: "Take it as mine", go_back: "Go back",
};
