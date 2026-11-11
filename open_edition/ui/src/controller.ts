// The controller's panel reads the app's controller routes, under /api/ade/controller. On the page the owner's request
// is a goal, a requirement a sub-goal, and a controller's task a task; the routes, records and identifiers keep their
// own words. Each answer is kept to the fields this file names, each of its type, as it is read (the parsers below), as
// the map is (sessionMap.ts): a field the app adds never reaches the panel until a release names it, and an answer of
// another shape draws nothing rather than breaking the panel. Every text the app returns is the app's own, the owner's
// or a model's; the panel shows each as it is, as text, never as markup.
import { Api, api } from "./api";
import type { HoldSettings, Signed } from "./passkey";

export type ControllerState = "off" | "on" | "stopped";

/** The controller's settings and the state of what reached the workspace, from GET/PUT "" and the on/off/stop/end-all
 *  routes' View.controller. The error fields are the app's own words. */
export interface ControllerSettings {
  state: ControllerState;
  state_at: number | null;
  account: string | null;
  account_confirmed: boolean;
  cap: number;
  models: string[];
  efforts: string[];
  chat_model: string;
  chat_effort: string;
  confirm_plans: boolean;
  session: string | null;
  open_sessions: number;
  end_all: number;
  version: number;
  mirrored_version: number | null;
  packet_at: number | null;
  packet_digest: string | null;
  packet_error: string | null;
  packet_error_at: number | null;
  refused: number;
  refused_at: number | null;
  read_at: number | null;
  read_behind_at: number | null;
  read_error: string | null;
  read_error_at: number | null;
  mirror_error: string | null;
  mirror_error_at: number | null;
  /** The managers' route's failure, the app's own words. */
  managers_error: string | null;
  managers_error_at: number | null;
}

/** A sub-goal's own manager: its state, what it waits for besides a
 *  change, how many attempts it had and its failures in a row, why it waits for its owner, its last attempt, and the
 *  newest note an attempt left, a model's words for the owner alone. */
export type ManagerState = "ready" | "running" | "waiting" | "needs_owner" | "done" | "stopped";
export interface Manager {
  state: ManagerState;
  waits: string | null;
  waits_until: number | null;
  attempts: number;
  failures: number;
  for_owner: string | null;
  last: { n: number; outcome: string | null; cause: string | null; ended_at: number | null } | null;
  note: { text: string; cut: boolean; hidden_removed: number; at: number | null; by: "manager" } | null;
}

/** One attempt of a manager's, newest first in its list; `note` a model's words for the owner alone. */
export interface Attempt {
  id: string;
  n: number;
  fence: number;
  state: "queued" | "sent" | "running" | "ended";
  outcome: string | null;
  cause: string | null;
  refused: string[];
  refused_count: number;
  note: { text: string; cut: boolean; hidden_removed: number; by: "manager" } | null;
  late: boolean;
  queued_at: number;
  started_at: number | null;
  ended_at: number | null;
}

/** A check of a goal's: a command the owner wrote with the goal, numbered from
 *  1, run in its repository. Its name and command are the owner's words. */
export interface Check {
  id: number;
  repo: string;
  name: string;
  command: string;
}

/** The protected paths of one repository, the owner's: a change to one since a task's base fails its checks. */
export interface Protected {
  repo: string;
  paths: string[];
}

/** A goal (the owner's request). `text` is the owner's, word for word; `checks` the owner's checks and protected paths
 *  sent with it, null where none; `unchosen` the numbers of its checks no sub-goal chose. */
export interface Request {
  id: string;
  n: number;
  text: string;
  digest: string;
  at: number;
  go_at: number | null;
  withdrawn_at: number | null;
  requirements: string[];
  checks: { commands: Check[]; protected: Protected[] } | null;
  unchosen: number[];
}

/** A sub-goal (a requirement). `words` and `acceptance` are the owner's; `title` is the controller's, a model's. */
export interface Requirement {
  id: string;
  request: { id: string; n: number };
  title: { text: string; cut: boolean; hidden_removed: number; by: "controller" };
  words: string;
  acceptance: string | null;
  repos: string[];
  state: "open" | "done" | "stopped";
  hold: boolean;
  confirm: boolean;
  go_at: number | null;
  waits_for_go: boolean;
  activity: string;
  planned_at: number;
  closed_at: number | null;
  tasks: Task[];
  more_tasks: number;
  /** The checks its plan chose, the protected paths of its repositories, and its goal's checks there it left out. */
  checks: Check[];
  protected: Protected[];
  left_out: { id: number; repo: string; name: string }[];
  /** The share of the cap its own manager may hold, and that manager; null for a sub-goal the controller runs. */
  share: number | null;
  manager: Manager | null;
}

/** One check's outcome in a round: passed; failed with its exit code; timed
 *  out; failed for protected paths, with how many changed; or not run, with the workspace's own words for why. */
export interface CheckResult {
  id: number;
  outcome: "passed" | "failed" | "timed out" | "protected" | "not run";
  exit: number | null;
  protected: number | null;
  why: string | null;
}

/** A task's last round of checks, as the app took it: its number, the commit it judged, when it ended, whether every
 *  check passed, and each outcome, never an output. */
export interface Round {
  round: number;
  head: string;
  at: number;
  passed: boolean;
  results: CheckResult[];
}

/** The same round as the task's session's own read gives it to the owner alone: each check's name, the owner's, and
 *  its output's last bytes, a program's, null where the workspace let them go. */
export interface RoundOutput extends Round {
  results: (CheckResult & { name: string | null; output: string | null })[];
}

/** A task (a controller's task): a workspace session the controller started. `flag` is null within the bounds, else the
 *  broken bounds, "; " between them, the app's own words. */
export interface Task {
  id: string;
  requirement: string | null;
  session: string | null;
  repo: string | null;
  branch: string | null;
  base: string | null;
  account: string | null;
  program: string | null;
  model: string | null;
  effort: string | null;
  state: "starting" | "working" | "running" | "waiting" | "idle" | "done" | "failed" | "stopped";
  waits_until: number | null;
  started_at: number;
  stopped_at: number | null;
  ended_at: number | null;
  continues: number;
  flag: string | null;
  /** Its last round of checks, the workspace's own words for an end its checks decided without one, and whether a round
   *  of it is queued or running. */
  checks: Round | null;
  why: string | null;
  checking: boolean;
  /** The task as its sub-goal's manager wrote it, a model's words, and the attempt that started it; null for the
   *  controller's own. */
  text: { text: string; cut: boolean; hidden_removed: number; by: "manager" } | null;
  attempt: string | null;
  /** The linked session whose own report the task is, run on its own machine, or null: the
   *  workspace's. */
  reported_by: string | null;
}

/** A question the controller asked the owner. `text` is a model's, made plain. */
export interface Question {
  id: string;
  requirement: string | null;
  text: string;
  cut: boolean;
  hidden_removed: number;
  state: "open" | "answered";
  asked_at: number;
  answered_at: number | null;
  decision: string | null;
}

/** A decision the owner recorded (an answer to a question, or one of the owner's own). `text` is the owner's. */
export interface Decision {
  id: string;
  n: number;
  question: string | null;
  text: string;
  at: number;
  /** The sub-goal whose manager a Resume gave these words to, which that manager alone is given; null for the rest. */
  requirement: string | null;
}

/** A line of the log (the app's journal). `words` is the app's own, never the owner's or a model's. */
export interface Line {
  seq: number;
  kind: string;
  requirement: string | null;
  task: string | null;
  at: number;
  words: string;
}

/** The control view's whole read of the controller (GET ""). `controller` is null before the owner set it up. */
export interface ControllerView {
  controller: ControllerSettings | null;
  requests: Request[];
  requirements: Requirement[];
  other_sessions: Task[];
  questions: Question[];
  decisions: Decision[];
  holder: Holder | null;
}

/** The linked session kept as the Manager's holder (ade/manager_link.py): `live` while its hold
 *  lasts; once it ended it is kept, holding nothing, until the owner signs again, chooses an account or takes the role
 *  back. */
export interface Holder {
  link: string;
  name: string | null;
  kind: string | null;
  since: number | null;
  until: number | null;
  live: boolean;
  silent: boolean;
  last_heard_at: number | null;
  /** Where its own calls are none of Proofpane's (a link whose hook does not run, a cloud session among them). */
  notice: string | null;
}

/** One of the owner's live links, as the panel lists them to choose the Manager from: a silent one shown, not
 *  offered. */
export interface HolderLink {
  id: string;
  name: string;
  kind: string;
  last_heard_at: number | null;
  silent: boolean;
  offered: boolean;
  notice: string | null;
}

/** What GET /holders gives: the links, the digest of the settings a holder holds the role under, the longest a hold may
 *  last, and the holder now. */
export interface HolderOffer {
  links: HolderLink[];
  settings_digest: string;
  /** the settings a holder would hold the role under: the review names them, and the page signs their digest itself
   *  (passkey.settingsDigest); null where the server gave them in no form the page reads, and nothing is signed */
  settings: HoldSettings | null;
  life_most: number;
  holder: Holder | null;
}

// --- parsers: each field kept of its type, as sessionMap.ts keeps the map ------------------------------------------
const text = (v: unknown): string | null => (typeof v === "string" ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const bool = (v: unknown): boolean => v === true;
const fields = (v: unknown): Record<string, unknown> =>
  (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const list = (v: unknown): Record<string, unknown>[] =>
  (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x)) : []);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function settingsOf(raw: unknown): ControllerSettings | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  const state = s.state === "on" || s.state === "stopped" ? s.state : "off";
  return {
    state,
    state_at: count(s.state_at),
    account: text(s.account),
    account_confirmed: bool(s.account_confirmed),
    cap: num(s.cap),
    models: strings(s.models),
    efforts: strings(s.efforts),
    chat_model: str(s.chat_model),
    chat_effort: str(s.chat_effort),
    confirm_plans: bool(s.confirm_plans),
    session: text(s.session),
    open_sessions: num(s.open_sessions),
    end_all: num(s.end_all),
    version: num(s.version),
    mirrored_version: count(s.mirrored_version),
    packet_at: count(s.packet_at),
    packet_digest: text(s.packet_digest),
    packet_error: text(s.packet_error),
    packet_error_at: count(s.packet_error_at),
    refused: num(s.refused),
    refused_at: count(s.refused_at),
    read_at: count(s.read_at),
    read_behind_at: count(s.read_behind_at),
    read_error: text(s.read_error),
    read_error_at: count(s.read_error_at),
    mirror_error: text(s.mirror_error),
    mirror_error_at: count(s.mirror_error_at),
    managers_error: text(s.managers_error),
    managers_error_at: count(s.managers_error_at),
  };
}

const numbers = (v: unknown): number[] => (Array.isArray(v) ? v.filter((x): x is number => typeof x === "number" && Number.isFinite(x)) : []);

function checkOf(raw: Record<string, unknown>): Check {
  return { id: num(raw.id), repo: str(raw.repo), name: str(raw.name), command: str(raw.command) };
}

function protectedOf(raw: Record<string, unknown>): Protected {
  return { repo: str(raw.repo), paths: strings(raw.paths) };
}

const OUTCOMES: CheckResult["outcome"][] = ["passed", "failed", "timed out", "protected", "not run"];

function resultOf(raw: Record<string, unknown>): CheckResult {
  const outcome = OUTCOMES.find((o) => o === raw.outcome) ?? "not run";
  return { id: num(raw.id), outcome, exit: count(raw.exit), protected: count(raw.protected), why: text(raw.why) };
}

/** A round, or null where the answer holds none of its form. */
export function roundOf(raw: unknown): Round | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  return { round: num(r.round), head: str(r.head), at: num(r.at), passed: bool(r.passed), results: list(r.results).map(resultOf) };
}

/** The round the session's own read gives, each result with its check's name and output (a program's), or null. */
export function roundOutputOf(raw: unknown): RoundOutput | null {
  const round = roundOf(raw);
  if (!round) return null;
  const given = list(fields(raw).results);
  return {
    ...round,
    results: round.results.map((r, i) => {
      const g = given[i] ?? {};
      const output = fields(g.output);
      return { ...r, name: text(g.name), output: output.by === "program" ? text(output.text) : null };
    }),
  };
}

function requestOf(raw: unknown): Request {
  const r = fields(raw);
  const checks = r.checks && typeof r.checks === "object" && !Array.isArray(r.checks) ? fields(r.checks) : null;
  return {
    id: str(r.id), n: num(r.n), text: str(r.text), digest: str(r.digest), at: num(r.at),
    go_at: count(r.go_at), withdrawn_at: count(r.withdrawn_at), requirements: strings(r.requirements),
    checks: checks ? { commands: list(checks.commands).map(checkOf), protected: list(checks.protected).map(protectedOf) } : null,
    unchosen: numbers(r.unchosen),
  };
}

function taskOf(raw: unknown): Task {
  const t = fields(raw);
  const states = ["starting", "working", "running", "waiting", "idle", "done", "failed", "stopped"];
  const state = typeof t.state === "string" && states.includes(t.state) ? (t.state as Task["state"]) : "starting";
  return {
    id: str(t.id), requirement: text(t.requirement), session: text(t.session), repo: text(t.repo), branch: text(t.branch),
    base: text(t.base), account: text(t.account), program: text(t.program), model: text(t.model), effort: text(t.effort),
    state, waits_until: count(t.waits_until), started_at: num(t.started_at), stopped_at: count(t.stopped_at),
    ended_at: count(t.ended_at), continues: num(t.continues), flag: text(t.flag),
    checks: roundOf(t.checks), why: text(t.why), checking: bool(t.checking),
    text: modelText(t.text), attempt: text(t.attempt), reported_by: text(t.reported_by),
  };
}

/** A holder, or null where the answer holds none of its form. */
export function holderOf(raw: unknown): Holder | null {
  const h = fields(raw);
  if (typeof h.link !== "string" || !h.link) return null;
  return {
    link: h.link, name: text(h.name), kind: text(h.kind), since: count(h.since), until: count(h.until),
    live: bool(h.live), silent: bool(h.silent), last_heard_at: count(h.last_heard_at), notice: text(h.notice),
  };
}

/** The settings a holder holds the role under, each of its type, or null where one is not. */
function holdSettingsOf(raw: unknown): HoldSettings | null {
  const o = fields(raw);
  const names = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
  if (!Number.isSafeInteger(o.cap) || !names(o.models) || !names(o.efforts) || typeof o.confirm_plans !== "boolean") {
    return null;
  }
  return { cap: o.cap as number, models: [...o.models], efforts: [...o.efforts], confirm_plans: o.confirm_plans };
}

export function holderOfferOf(raw: unknown): HolderOffer {
  const o = fields(raw);
  return {
    links: list(o.links).map((l) => ({
      id: str(l.id), name: str(l.name), kind: str(l.kind), last_heard_at: count(l.last_heard_at),
      silent: bool(l.silent), offered: bool(l.offered), notice: text(l.notice),
    })).filter((l) => l.id),
    settings_digest: str(o.settings_digest), settings: holdSettingsOf(o.settings), life_most: num(o.life_most),
    holder: holderOf(o.holder),
  };
}

/** A model's text the app keeps with its marks, or null where the answer holds none of its form. */
function modelText(raw: unknown): { text: string; cut: boolean; hidden_removed: number; by: "manager" } | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const t = raw as Record<string, unknown>;
  return { text: str(t.text), cut: bool(t.cut), hidden_removed: num(t.hidden_removed), by: "manager" };
}

const MANAGER_STATES: ManagerState[] = ["ready", "running", "waiting", "needs_owner", "done", "stopped"];

function managerOf(raw: unknown): Manager | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const m = raw as Record<string, unknown>;
  const last = m.last && typeof m.last === "object" && !Array.isArray(m.last) ? fields(m.last) : null;
  const note = modelText(m.note);
  return {
    state: MANAGER_STATES.find((s) => s === m.state) ?? "waiting", waits: text(m.waits),
    waits_until: count(m.waits_until), attempts: num(m.attempts), failures: num(m.failures),
    for_owner: text(m.for_owner),
    last: last ? { n: num(last.n), outcome: text(last.outcome), cause: text(last.cause), ended_at: count(last.ended_at) } : null,
    note: note ? { ...note, at: count(fields(m.note).at) } : null,
  };
}

const ATTEMPT_STATES: Attempt["state"][] = ["queued", "sent", "running", "ended"];

export function attemptOf(raw: unknown): Attempt {
  const a = fields(raw);
  return {
    id: str(a.id), n: num(a.n), fence: num(a.fence), state: ATTEMPT_STATES.find((s) => s === a.state) ?? "queued",
    outcome: text(a.outcome), cause: text(a.cause), refused: strings(a.refused), refused_count: num(a.refused_count),
    note: modelText(a.note), late: bool(a.late), queued_at: num(a.queued_at), started_at: count(a.started_at),
    ended_at: count(a.ended_at),
  };
}

function requirementOf(raw: unknown): Requirement {
  const r = fields(raw);
  const req = fields(r.request);
  const title = fields(r.title);
  const state = r.state === "done" || r.state === "stopped" ? r.state : "open";
  return {
    id: str(r.id), request: { id: str(req.id), n: num(req.n) },
    title: { text: str(title.text), cut: bool(title.cut), hidden_removed: num(title.hidden_removed), by: "controller" },
    words: str(r.words), acceptance: text(r.acceptance), repos: strings(r.repos), state, hold: bool(r.hold),
    confirm: bool(r.confirm), go_at: count(r.go_at), waits_for_go: bool(r.waits_for_go), activity: str(r.activity),
    planned_at: num(r.planned_at), closed_at: count(r.closed_at),
    tasks: list(r.tasks).map(taskOf), more_tasks: num(r.more_tasks),
    checks: list(r.checks).map(checkOf), protected: list(r.protected).map(protectedOf),
    left_out: list(r.left_out).map((x) => ({ id: num(x.id), repo: str(x.repo), name: str(x.name) })),
    share: count(r.share), manager: managerOf(r.manager),
  };
}

function questionOf(raw: unknown): Question {
  const q = fields(raw);
  return {
    id: str(q.id), requirement: text(q.requirement), text: str(q.text), cut: bool(q.cut),
    hidden_removed: num(q.hidden_removed), state: q.state === "answered" ? "answered" : "open",
    asked_at: num(q.asked_at), answered_at: count(q.answered_at), decision: text(q.decision),
  };
}

function decisionOf(raw: unknown): Decision {
  const d = fields(raw);
  return { id: str(d.id), n: num(d.n), question: text(d.question), text: str(d.text), at: num(d.at),
    requirement: text(d.requirement) };
}

function lineOf(raw: unknown): Line {
  const l = fields(raw);
  return {
    seq: num(l.seq), kind: str(l.kind), requirement: text(l.requirement), task: text(l.task), at: num(l.at),
    words: str(l.words),
  };
}

export function viewOf(raw: unknown): ControllerView {
  const v = fields(raw);
  return {
    controller: settingsOf(v.controller),
    requests: list(v.requests).map(requestOf),
    requirements: list(v.requirements).map(requirementOf),
    other_sessions: list(v.other_sessions).map(taskOf),
    questions: list(v.questions).map(questionOf),
    decisions: list(v.decisions).map(decisionOf),
    holder: holderOf(v.holder),
  };
}

/** Settings the owner may change (PUT ""): only those sent are changed. A model's name shaped like a secret is refused
 *  by the route (400), which the panel shows as given. */
export interface SettingsPatch {
  account?: string | null;
  cap?: number;
  models?: string[];
  efforts?: string[];
  chat_model?: string;
  chat_effort?: string;
  confirm_plans?: boolean;
}

const P = "/api/ade/controller";
const enc = encodeURIComponent;

export const ControllerApi = {
  // a linked session as the Manager: the links to choose from, a holder signed, the role taken back,
  // and a goal signed to the holder, each the owner's own instruction to it
  holders: (signal?: AbortSignal) => api<unknown>(`${P}/holders`, { signal }).then(holderOfferOf),
  setHolder: (signed: Signed, signal?: AbortSignal) =>
    api<unknown>(`${P}/holder`, { method: "POST", body: signed, signal }),
  dropHolder: (signal?: AbortSignal) => api<unknown>(`${P}/holder`, { method: "DELETE", signal }),
  postSignedGoal: (signed: Signed, signal?: AbortSignal) =>
    api<unknown>(`${P}/requests`, { method: "POST", body: signed, signal }),
  view: (signal?: AbortSignal) => api<unknown>(P, { signal }).then(viewOf),
  save: (body: SettingsPatch, signal?: AbortSignal) => api<unknown>(P, { method: "PUT", body, signal }).then(viewOf),
  on: (signal?: AbortSignal) => api<unknown>(`${P}/on`, { method: "POST", signal }).then(viewOf),
  off: (signal?: AbortSignal) => api<unknown>(`${P}/off`, { method: "POST", signal }).then(viewOf),
  stop: (signal?: AbortSignal) => api<unknown>(`${P}/stop`, { method: "POST", signal }).then(viewOf),
  endAll: (signal?: AbortSignal) => api<unknown>(`${P}/end-all`, { method: "POST", signal }).then(viewOf),
  // a goal's checks and protected paths go only where the owner wrote some: the route takes each as optional
  postRequest: (textVal: string, checks?: Omit<Check, "id">[], protectedPaths?: Protected[], signal?: AbortSignal) =>
    api<unknown>(`${P}/requests`, {
      method: "POST", signal,
      body: { text: textVal, ...(checks?.length ? { checks } : {}), ...(protectedPaths?.length ? { protected: protectedPaths } : {}) },
    }).then(requestOf),
  withdraw: (id: string, signal?: AbortSignal) =>
    api<unknown>(`${P}/requests/${enc(id)}/withdraw`, { method: "POST", signal }).then(requestOf),
  // the go's answer is not read: the panel reloads the whole view after it, so it is taken as unknown rather than given
  // a shape the module does not then parse (its contract is to keep each answer it reads to its fields)
  go: (id: string, signal?: AbortSignal) =>
    api<unknown>(`${P}/requests/${enc(id)}/go`, { method: "POST", signal }),
  hold: (id: string, hold: boolean, signal?: AbortSignal) =>
    api<unknown>(`${P}/requirements/${enc(id)}/hold`, { method: "POST", body: { hold }, signal }).then(requirementOf),
  close: (id: string, as: "done" | "stopped", signal?: AbortSignal) =>
    api<unknown>(`${P}/requirements/${enc(id)}/close`, { method: "POST", body: { as }, signal }).then(requirementOf),
  answer: (qid: string, textVal: string, signal?: AbortSignal) =>
    api<unknown>(`${P}/questions/${enc(qid)}/answer`, { method: "POST", body: { text: textVal }, signal }).then(decisionOf),
  decide: (textVal: string, signal?: AbortSignal) =>
    api<unknown>(`${P}/decisions`, { method: "POST", body: { text: textVal }, signal }).then(decisionOf),
  // a sub-goal's own manager resumed, with words of the owner's that manager alone is given, or none
  resume: (id: string, textVal: string | null, signal?: AbortSignal) =>
    api<unknown>(`${P}/requirements/${enc(id)}/resume`, {
      method: "POST", signal, ...(textVal != null && textVal.trim() ? { body: { text: textVal } } : {}),
    }).then(requirementOf),
  attempts: (id: string, signal?: AbortSignal) =>
    api<unknown>(`${P}/requirements/${enc(id)}/attempts`, { signal }).then((r) => list(fields(r).attempts).map(attemptOf)),
  // a task's last round with each check's output, from its session's own read (the workspace's route), on the owner's
  // press alone: the view, read every 3 seconds, carries the outcomes and never an output
  checksOutput: (session: string, signal?: AbortSignal) =>
    Api.session(session, signal).then((r) => roundOutputOf(fields(r).checks)),
  journal: (before: number | null, limit: number, signal?: AbortSignal) =>
    api<unknown>(`${P}/journal?${before != null ? `before=${before}&` : ""}limit=${limit}`, { signal })
      .then((r) => list(fields(r).events).map(lineOf)),
};
