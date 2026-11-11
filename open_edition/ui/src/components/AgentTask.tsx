import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, ListChecks, Loader2, Play, Plus, Square, Trash2, X } from "lucide-react";
import {
  Api, ApiError,
  type AccountOwnerView, type AccountRecordEntry, type AccountRecordView, type AccountSnapshot, type BackendRef, type HoldRef,
  type ModelProviderView, type TaskAction, type TaskEffectView, type TaskEvent, type TaskMoveLine, type TaskSettings,
  type TaskState, type TaskStepInput, type TaskView,
} from "../api";
import { windowsWords, type Agent, type AgentRun } from "../agents";
import { confirmWays } from "../passkey";
import { useStore } from "../store";
import { clock, dayTime, full, near } from "../when";
import { ConfirmIt, type Change } from "./AccountDialog";
import { Confirm } from "./bits";
import { RiskTerms, settleFirstVisit, TERMS_IN_FLOW, type RiskPoint } from "./RiskTerms";
import { FirstGrant, TaskGrant } from "./TaskGrant";
import { TaskHandOff } from "./TaskHandOff";

// The Task section of an agent's pane:
// New task, the task's steps, attempts, checkpoints and relay with each account's windows, its moves and
// crossing lines (each opening its risk point's text once, with the line's own cases, through RiskTerms: 1, 2, or 12
// for custody's hand-off; a crossing, an automatic resume's among them, being two lines, the relay's and the
// crossing's), what waits on the owner with its remedy (an `ungated` end's unseen calls among the unknown effects,
// Continue waiting until they are resolved), the controls (Start, Continue, Continue on..., Stop, Resolve, Next on...,
// the switches, End), each at a risk point opening its text over it (points 2 to 5, 7, 8, 11 and 14), a received
// task's switch off and not to be turned on, the task's account record (named by its task's id, repository and branch,
// its title until the prune, and how long the deployment keeps it) with its Delete and its confirmation (point 15), and
// the hand-off's part rendered by name: TaskHandOff, given the TaskView itself as its task, since it carries every
// member of its HandOffTask. No route knows of a pop-up and nothing here waits on one: each control works with its text
// open.

export interface AgentTaskProps {
  agent: Agent;
  /** open one of the task's attempts, a run, in the agent's pane, as runs open today */
  onOpenRun(runId: string): void;
}

// ---- words --------------------------------------------------------------------------------------------------------

export const STATE_WORDS: Record<TaskState, string> = {
  ready: "Ready to start", running: "Running", suspended: "Suspended", needs_owner: "Needs you",
  offering: "Being handed to a teammate", handed_over: "With your teammate", returning: "Coming back from your teammate",
  done: "Done", stopped: "Stopped",
};
const ENDED: TaskState[] = ["done", "stopped"];
// while a task is with a teammate, or on its way to or from one, none of its owner's controls changes it
const AWAY: TaskState[] = ["offering", "handed_over", "returning"];
// a call that reaches outside the workspace: saying it did not happen is a two-step control
const OUTWARD = ["git_remote", "github", "external"];
const SEAT_PLANS = ["Claude Team", "Claude Enterprise"];
// the app's words for a task received from a teammate that went back to them (task_offers.RETURNED_REASON), shown for
// one held as returned where the app gave no line of its own
const RETURNED_WORDS = "This task went back to the teammate who handed it to you.";

/** A task received from a teammate that went back to them, held for its user as returned: the app refuses
 *  its Continue and Continue on, so End is its one way out. The owner's own task, held because the work that came back
 *  is to be looked at first, waits with the cause `returned` too but is never a received one, since a received task is
 *  never offered on, and goes on at Continue. */
function heldAsReturned(task: TaskView): boolean {
  return task.state === "needs_owner" && task.cause === "returned" && task.received;
}

/** A task received from a teammate that the way back marked returned (TaskView's `returned`): the app
 *  refuses its Continue, Continue on and Next on in every state (tasks.py's _refuse_returned), so the page offers none
 *  of them, End being its way out, and a hold its attempt's end put first keeps a remedy of its own. */
function markedReturned(task: TaskView): boolean {
  return task.received && task.returned;
}

/** An account as the page names it: its label and the address it reported. */
export function accountWords(a: AccountOwnerView): string {
  const email = a.report?.email;
  return `Claude Code · ${a.name || (a.account === "default" ? "Default" : a.account)}${email ? ` (${email})` : ""}`;
}

/** The kind an account's group is read as where the app decides a crossing (org_kind): its
 *  organisation's, an Enterprise one where the owner also holds an Enterprise seat of it; the seat's own kind where
 *  the view gives none. */
export function readKind(a: AccountOwnerView | undefined): AccountOwnerView["grp_kind"] | undefined {
  return a ? (a.org_kind ?? a.grp_kind) : undefined;
}

/** Its plan and group: "Claude Max, your personal plans", "Claude Team, Acme's seats". */
function planWords(a: AccountOwnerView): string {
  const plan = a.report?.plan ?? "plan not reported";
  if (a.grp_kind === "personal") return `${plan}, your personal plans`;
  if (a.grp_kind === "team" || a.grp_kind === "enterprise") {
    return `${plan}, ${a.report?.organization ?? "an organisation"}'s ${a.grp_kind === "team" ? "Team" : "Enterprise"} seats`;
  }
  return `${plan}, in no group`;
}

/** A backend as the page names it: one of the owner's accounts, or a key kept in the workspace. */
export function backendWords(b: BackendRef | null | undefined, accounts: AccountOwnerView[], keys: ModelProviderView[] = []): string {
  if (!b) return "unknown";
  if (b.kind === "key") {
    const k = keys.find((p) => p.id === b.provider);
    return `API key · ${k ? k.vendor : b.provider === "anthropic" ? "Anthropic" : b.provider ?? "a provider"}`;
  }
  const a = accounts.find((x) => x.account === b.id);
  return a ? accountWords(a) : `Claude Code · ${b.id === "default" ? "Default" : b.id}`;
}

/** A move's backend as its line names it ("account:<id>", "key:<provider>"), or as the app gave it. */
function moveEnd(s: string | null, accounts: AccountOwnerView[], keys: ModelProviderView[]): string {
  if (!s) return "";
  const m = /^(account|key):(.+)$/.exec(s);
  if (!m) return s;
  return backendWords(m[1] === "key" ? { kind: "key", provider: m[2] } : { kind: "account", id: m[2] }, accounts, keys);
}

/** A group as the page names it: "your personal plans", or an organisation's seats. */
export function groupWords(grp: string | null, accounts: AccountOwnerView[]): string {
  if (!grp || grp === "personal") return "your personal plans";
  const a = accounts.find((x) => x.grp === grp && x.report?.organization);
  return a ? `${a.report!.organization}'s seats` : "an organisation's seats";
}

const MOVE_WORDS: Record<TaskMoveLine["kind"], string> = {
  relay: "The relay moved it", crossing: "It crossed organisations", home: "Back home",
  custody: "Custody handed it to your custodian", return: "It came back",
};

/** A move's line in words: what moved it, from and to; custody's hand-off from the backend it was on, and a return to
 *  the one it came back to (the app's view names the custodian's side "custodian", which the words already say). */
function lineWords(m: TaskMoveLine, accounts: AccountOwnerView[], keys: ModelProviderView[]): string {
  const from = m.from ? moveEnd(m.from, accounts, keys) : "";
  const to = moveEnd(m.to, accounts, keys);
  if (m.kind === "custody") return `${MOVE_WORDS.custody}${from ? `, from ${from}` : ""}`;
  if (m.kind === "return") return `${MOVE_WORDS.return}${to ? `, onto ${to}` : ""}`;
  return `${MOVE_WORDS[m.kind]}: ${from ? `${from} to ` : ""}${to}`;
}

/** Why an account is out of the task's relay set, as the app says it (spelled with spaces where it gives a word). */
const leftOutWords = (why: string) => why.replace(/_/g, " ");

/** How long this deployment keeps a task's account record after the task is deleted. */
export function keepingWords(days: number | null): string {
  return days == null ? "This deployment keeps this record for good after the task is deleted."
    : `This deployment keeps this record for ${days} ${days === 1 ? "day" : "days"} after the task is deleted.`;
}

/** What the local provider means for a task's records, said once on a task (risk point 14's text, which opens over
 *  the New task form under that provider, says the same). */
export const LOCAL_PROVIDER_WORDS = "On this machine an agent's runs could reach the secret its records are signed with, so the record of what a task did holds against an agent that follows the rules, not against code written to forge it.";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const short = (s: string | null | undefined, n = 12) => (s ? s.slice(0, n) : "");

/** What of its agent the page knows to hold a task's automatic continuation (tasks.py's _firing_hold): retired, or
 *  paused, by the owner or until a time. */
type AgentHold = Pick<Agent, "retired" | "paused" | "paused_until">;

// ---- the section ----------------------------------------------------------------------------------------------------

/** The Task section of an agent's pane: its open task, or New task; its tasks that ended, each with Delete; and the
 *  account records of its deleted tasks, each with its own Delete. Read from the app every 5 seconds while a task is
 *  live, and every 30 otherwise. */
export function AgentTask({ agent, onOpenRun }: AgentTaskProps) {
  const keys = useStore((s) => s.modelProviders);
  const refreshModelProviders = useStore((s) => s.refreshModelProviders);
  const [tasks, setTasks] = useState<TaskView[] | null>(null);
  const [settings, setSettings] = useState<TaskSettings | null>(null);
  const [accounts, setAccounts] = useState<AccountOwnerView[]>([]);
  // whether the owner's accounts were read: until they are, the page says nothing of an account's state
  const [accountsRead, setAccountsRead] = useState(false);
  const [records, setRecords] = useState<AccountRecordView[]>([]);
  const [making, setMaking] = useState(false);
  const [unread, setUnread] = useState("");
  // the account new sessions start on, as the workspace last said it ("default", or an id): a task made on an agent
  // with no account of its own begins there, by its id or as `default`, and the page shows which; null while the page
  // has not read the workspace's accounts, and then the New task form asks the owner to choose
  const startsOn = useStore((s) => s.accounts?.claude.active ?? null);

  // read again on a timer: a failure is said here, once, and the last answer stays, rather than raised on every round
  const load = useCallback(async () => {
    try {
      const [t, a, r] = await Promise.all([Api.tasks(agent.id), Api.taskAccounts().catch(() => null), Api.accountRecords(agent.id).catch(() => null)]);
      if (!Array.isArray(t?.tasks) || !t.settings) throw new Error("The app's answer holds no tasks.");
      setTasks(t.tasks.filter((x) => x.agent_id === agent.id));
      setSettings(t.settings);
      if (Array.isArray(a?.accounts)) { setAccounts([...a.accounts].sort((x, y) => x.position - y.position)); setAccountsRead(true); }
      if (Array.isArray(r?.records)) setRecords(r.records.filter((x) => x.agent_id === agent.id));
      setUnread("");
    } catch (e) {
      setUnread(`Its tasks could not be read just now: ${message(e)}`);
    }
  }, [agent.id]);
  useEffect(() => { void refreshModelProviders(); }, [refreshModelProviders]);
  const live = (tasks ?? []).some((t) => t.state === "running" || AWAY.includes(t.state));
  useEffect(() => {
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && void load(), live ? 5000 : 30000);
    return () => clearInterval(t);
  }, [load, live]);

  const open = (tasks ?? []).filter((t) => !ENDED.includes(t.state));
  const ended = (tasks ?? []).filter((t) => ENDED.includes(t.state));
  const deleted = records.filter((r) => r.deleted_at != null);
  const kept = (keys ?? []).filter((k) => k.set);
  return (
    <div id={`agent-task-${agent.id}`} className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s task`}>
      <div className="flex items-center gap-2">
        <ListChecks size={12} className="text-muted" />
        <span className="font-medium">Task</span>
        {tasks === null && !unread && <Loader2 size={12} className="animate-spin text-muted" />}
        {tasks !== null && open.length === 0 && !agent.retired && (
          <button className="btn btn-sm touch-target ml-auto !h-6 text-[11px]" aria-pressed={making} onClick={() => {
            // which account new sessions start on now, read again as the form opens, where the workspace runs: the one a
            // task of an agent with no account begins on is the one "then"
            if (!making && agent.account == null && useStore.getState().ws?.running) void useStore.getState().refreshAccounts(false);
            setMaking(!making);
          }}>
            <Plus size={12} /> New task
          </button>
        )}
      </div>
      {unread && <div className="mt-1 text-[11.5px] text-wait" role="status">{unread}</div>}
      {tasks !== null && open.length === 0 && !making && <div className="mt-1 text-[11.5px] text-faint">No task: its runs are its own.</div>}
      {making && settings && (
        <TaskForm agentId={agent.id} firstAccount={agent.account ?? startsOn} noAccount={agent.account == null} settings={settings}
          accounts={accounts} accountsRead={accountsRead} onDone={async (made) => { setMaking(false); if (made) await load(); }} />
      )}
      {open.map((t) => (
        <TaskPane key={t.id} task={t} agent={agent} settings={settings} accounts={accounts} keys={kept} runs={agent.runs} onOpenRun={onOpenRun} reload={load} />
      ))}
      {ended.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label="Tasks that ended">
          {ended.map((t) => <EndedTask key={t.id} task={t} accounts={accounts} keys={kept} reload={load} />)}
        </ul>
      )}
      {deleted.length > 0 && (
        <div className="mt-2" aria-label="Deleted tasks' account records" role="group">
          <div className="text-[11.5px] font-medium text-muted">Deleted tasks' account records</div>
          {deleted.map((r) => <AccountRecord key={r.task_id} record={r} onChanged={load} />)}
        </div>
      )}
    </div>
  );
}

// ---- New task (and its edit) ----------------------------------------------------------------------------------------

interface StepDraft { objective: string; check: string }

/** The New task form, or a task's edit: its title, goal and steps (each with an optional check), its protected paths
 *  and external commands, and its account (the agent's to begin with, or for an agent with none the account new
 *  sessions start on, the form saying which). Opening it opens the texts of the risk points a
 *  new task carries over it: the relay's (point 2) while Relay is on, crossing's (point 3) while the deployment lets a
 *  task cross, work while nobody watches (point 7) while Auto-resume is on, usage credits (point 8) beside each account
 *  it offers, and the local provider's (point 14). */
function TaskForm({ agentId, firstAccount: first, noAccount = false, settings, accounts, accountsRead = false, task, onDone }: {
  agentId: string;
  /** the account the form starts on: the agent's own, or, for an agent with none (`noAccount`), the account new
   *  sessions start on; null while the page cannot tell which that is, and then the owner chooses */
  firstAccount: string | null; noAccount?: boolean;
  settings: TaskSettings; accounts: AccountOwnerView[]; accountsRead?: boolean; task?: TaskView;
  onDone: (made: boolean, notStopped?: string) => Promise<void>;
}) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [goal, setGoal] = useState(task?.goal ?? "");
  const [steps, setSteps] = useState<StepDraft[]>(task ? task.steps.map((s) => ({ objective: s.objective, check: s.check ?? "" })) : [{ objective: "", check: "" }]);
  const [protectedPaths, setProtected] = useState((task?.protected ?? []).join("\n"));
  const [external, setExternal] = useState((task?.external ?? []).join("\n"));
  // the accounts offered: those confirmed as the owner's own, and the one the form starts on first, confirmed or not, so
  // that it is always among them and its choice is the one sent (none first while the page cannot tell which)
  const own = first == null ? undefined : accounts.find((a) => a.account === first);
  const offered = [...(first == null || own?.state === "confirmed" ? [] : [own ?? ({ account: first } as AccountOwnerView)]),
                   ...accounts.filter((a) => a.state === "confirmed")];
  // an account the workspace no longer lists and the app has no row for: Connections has no row of it to confirm, and
  // the agent is moved off it in its own form; known only once the store has read the workspace's list
  const listed = useStore((s) => s.accounts?.claude.accounts);
  const gone = (id: string) => !!listed && id !== "default" && !listed.some((r) => r.id === id) && !accounts.some((a) => a.account === id);
  const label = (a: AccountOwnerView) => (a.id ? accountWords(a) : `Claude Code · ${a.account === "default" ? "Default" : a.account}`);
  // the owner's own choice, once made; until then the account the form starts on, which can become known while the
  // form is open (the workspace's accounts read meanwhile), and none while it is not known
  const [picked, setPicked] = useState<string | null>(null);
  const account = picked ?? first ?? "";
  const [between, setBetween] = useState<TaskView["between_steps"]>(task?.between_steps ?? "checked");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // a task just made with a repository: its first grant, offered before the form closes
  const [justMade, setJustMade] = useState<TaskView | null>(null);
  const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  const stepsOut = (): TaskStepInput[] => steps.filter((s) => s.objective.trim()).map((s) => ({ objective: s.objective.trim(), check: s.check.trim() || null }));
  const save = async () => {
    setBusy(true); setError("");
    try {
      let notStopped: string | undefined;
      if (task) {
        // a change stops an attempt sent and not started; when the workspace could not take that stop, the change is
        // kept and the app says why, which the task's pane then says
        ({ not_stopped: notStopped } = await Api.putTask(task.id, {
          state_version: task.state_version, title: title.trim(), goal: goal.trim(), steps: stepsOut(),
          protected: lines(protectedPaths), external: lines(external), between_steps: between }));
      } else {
        const made = await Api.newTask({ agent_id: agentId, title: title.trim(), goal: goal.trim(), steps: stepsOut(), protected: lines(protectedPaths),
                                         external: lines(external), backend: { kind: "account", id: account }, between_steps: between });
        // a task with a repository is offered its first grant as it is made; one with none needs none, as before
        if (made.task.grant_for?.repo) { setJustMade(made.task); return; }
      }
      await onDone(true, notStopped);
    } catch (e) {
      // every refusal said here, in the app's own words: a 409, the task changed; a 412, the agent retired or with an
      // open task already, or an account not confirmed as the owner's own
      setError(e instanceof ApiError && e.status === 409 ? e.message || "The task changed; look again." : message(e));
    } finally {
      setBusy(false);
    }
  };
  if (justMade) return <FirstGrant task={justMade} onDone={() => onDone(true)} />;
  const fresh = !task;
  return (
    <form className="mt-1.5 grid gap-2 rounded-md border border-line p-2" aria-label={task ? "Edit the task" : "New task"}
      onSubmit={(e) => { e.preventDefault(); if (title.trim() && goal.trim() && stepsOut().length && (task || account)) void save(); }}>
      {fresh && (
        // the relay clause only where this deployment has the relay on: it is off unless set, and the form
        // says what that means where it lists the relay's setting
        <div className="text-[11.5px] text-muted">
          A task is a job in steps; each step ends at a checkpoint the workspace makes, and is checked when it has a check
          {settings.relay ? "; when an account runs out of room, the task goes on on your next one." : "."}
        </div>
      )}
      {fresh && (
        // the texts a new task carries, opened with the form, before any field: the relay's while Relay is on,
        // crossing's while the deployment lets a task cross, work while nobody watches while Auto-resume is on, and
        // the local provider's under it
        <div className={`grid ${TERMS_IN_FLOW}`}>
          {settings.relay && <RiskTerms point={2} kind="control" open />}
          {settings.cross_groups && <RiskTerms point={3} kind="control" open cases={[]} />}
          {settings.auto_resume && <RiskTerms point={7} kind="control" open />}
          {settings.local_provider && <RiskTerms point={14} kind="control" open />}
        </div>
      )}
      <label className="flex flex-col gap-1 text-[12px] text-muted">Title
        <input className="input !h-8 text-[12.5px]" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">Goal
        <textarea className="input min-h-[64px] text-[12.5px]" value={goal} onChange={(e) => setGoal(e.target.value)} />
      </label>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1 text-[12px] text-muted">Steps, in order (a check is a command that passes when the step is done)</legend>
        {steps.map((s, i) => (
          <div key={i} className="flex flex-wrap items-center gap-1.5">
            <span className="w-5 text-right font-mono text-[11px] text-faint">{i + 1}</span>
            <input className="input !h-7 min-w-[160px] flex-1 text-[12px]" aria-label={`Step ${i + 1}'s objective`} value={s.objective}
              onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, objective: e.target.value } : x)))} />
            <input className="input !h-7 min-w-[120px] flex-1 font-mono text-[11.5px]" aria-label={`Step ${i + 1}'s check`} placeholder="its check, or none"
              value={s.check} onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, check: e.target.value } : x)))} />
            {steps.length > 1 && (
              <button type="button" className="icon-btn touch-target !h-6 !w-6" aria-label={`Remove step ${i + 1}`} onClick={() => setSteps(steps.filter((_, j) => j !== i))}><X size={12} /></button>
            )}
          </div>
        ))}
        {steps.length < 20 && <button type="button" className="btn btn-sm touch-target w-fit !h-6 text-[11px]" onClick={() => setSteps([...steps, { objective: "", check: "" }])}><Plus size={11} /> A step</button>}
      </fieldset>
      <label className="flex flex-col gap-1 text-[12px] text-muted">Protected paths, one a line (optional): the task's attempts may not change them
        <textarea className="input min-h-[40px] font-mono text-[11.5px]" value={protectedPaths} onChange={(e) => setProtected(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">External commands, one a line (optional)
        <textarea className="input min-h-[40px] font-mono text-[11.5px]" value={external} onChange={(e) => setExternal(e.target.value)} />
        <span className="text-[11.5px] text-faint">Commands that reach outside this machine, such as a deploy: an attempt that dies while one runs waits for you to say whether it happened.</span>
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">Between steps
        <select className="input !h-8 text-[12.5px]" value={between} onChange={(e) => setBetween(e.target.value as TaskView["between_steps"])}>
          <option value="checked">Go on after a checked step; wait for me after one only said done</option>
          <option value="any">Go on after any step</option>
          <option value="wait">Wait for me after each step</option>
        </select>
      </label>
      {fresh && (
        <fieldset className="grid gap-1">
          <legend className="mb-1 text-[12px] text-muted">Its account, to begin with</legend>
          {noAccount && (
            // an agent with none: its task begins on the account new sessions start on now, by its id or as default,
            // and the form says which, or, where the page cannot tell, asks the owner to choose
            <div className="text-[11.5px] text-faint" role="note" aria-label="The account it begins on">
              {first != null
                ? `This agent has no account of its own: a task of its begins on the account new sessions start on now, ${label(offered.find((a) => a.account === first) ?? ({ account: first } as AccountOwnerView))}.`
                : "This agent has no account of its own, and which account new sessions start on is not known until the workspace's accounts are read: choose the one this task begins on."}
            </div>
          )}
          {offered.map((a) => (
            <div key={a.account} className={`grid ${TERMS_IN_FLOW}`}>
              <label className="flex flex-wrap items-center gap-x-2 text-[12px]">
                <input type="radio" name={`account-${agentId}`} checked={account === a.account} onChange={() => setPicked(a.account)} />
                <span>{label(a)}</span>
                {a.id && <span className="text-[11px] text-faint">{planWords(a)}</span>}
                {/* a mismatch decides an account's standing before its state does, as on its row in Connections and in
                    the pointer under the list: a held one's wait ends without it joining */}
                {a.mismatch ? <span className="text-[11px] text-wait">cannot be confirmed as yours: {mismatchWords(a.mismatch)}; in no relay</span>
                  : a.state === "held" ? <span className="text-[11px] text-faint">confirmed: it joins your tasks' relay when its hold ends</span>
                  : a.state === "changed" ? <span className="text-[11px] text-wait">its identity changed: in no relay until you confirm it again in Connections</span>
                  : a.state === "reported" || (accountsRead && !a.id) ? <span className="text-[11px] text-wait">not confirmed as yours: in no relay until you confirm it in Connections</span>
                  : null}
              </label>
              {/* usage credits' text (point 8) beside each account the form offers, as
                  the form opens: whether an account's credits are on, the workspace cannot tell */}
              <RiskTerms key={`credits-${a.account}`} point={8} kind="control" open />
            </div>
          ))}
        </fieldset>
      )}
      {fresh && accountsRead && !!account && !offered.some((a) => a.account === account && a.state === "confirmed") && (
        // a task starts only on an account confirmed as the owner's own (the app's create refuses any other): said
        // before Make the task, in the chosen account's own standing, with the way to Connections, where that is settled;
        // nothing while the owner's accounts are not read, since the page then knows no account's standing, nor while
        // no account is chosen
        <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-wait" role="status">
          {gone(account) ? (
            <span>A task starts on an account confirmed as yours, and this one is no longer in the workspace: choose another here, or move the agent off it in its form.</span>
          ) : (
            <>
              <span>{pointerWords(offered.find((a) => a.account === account))}</span>
              <button type="button" className="btn btn-sm touch-target !h-6 text-[11px]" onClick={() => useStore.getState().setConnectionsOpen(true)}>Open Connections</button>
            </>
          )}
        </div>
      )}
      {fresh && (
        <div className="grid gap-0.5 text-[11.5px] text-muted">
          <span>Relay: {settings.relay ? "on. When an account runs out of room, the task goes on on your next account." : `off: this deployment does not set the relay on (OPEN_EDITION_ADE_TASK_RELAY). ${relayOffWords(settings.auto_resume)}`}</span>
          <span>Auto-resume: {settings.auto_resume ? "on" : "off, as this deployment sets it"}. When an account's window resets, the task goes on there by itself, while you are away, as a schedule does.</span>
          <span>Cross organisations: {settings.cross_groups ? (settings.relay ? "on" : "on, and it does nothing while the relay is off: only the relay crosses by itself") : "off: this deployment does not let a task's relay cross organisations"}.</span>
          {settings.local_provider && <span>This workspace runs under the local provider.</span>}
        </div>
      )}
      {error && <div role="alert" className="text-[12px] text-fail">{error}</div>}
      <div className="flex gap-2">
        <button className="btn btn-sm btn-primary touch-target" disabled={busy || !title.trim() || !goal.trim() || !stepsOut().length || (fresh && !account)}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : null} {task ? "Save" : "Make the task"}
        </button>
        <button type="button" className="btn btn-sm touch-target" onClick={() => void onDone(false)}>Cancel</button>
      </div>
    </form>
  );
}

// ---- one task ---------------------------------------------------------------------------------------------------------

type Run = (what: string, action: TaskAction, body?: Record<string, unknown>) => Promise<boolean>;

/** A task: its title and state; what waits on the owner first, with its remedy; its steps, attempts, checkpoints and
 *  unknown effects; its relay, with each move's line and the relay set; its switches, holds and controls; the
 *  hand-off's part; and its account record. */
function TaskPane({ task, agent, settings, accounts, keys, runs, onOpenRun, reload }: {
  task: TaskView; agent: AgentHold; settings: TaskSettings | null; accounts: AccountOwnerView[]; keys: ModelProviderView[];
  runs: AgentRun[]; onOpenRun(runId: string): void; reload: () => Promise<void>;
}) {
  const setError = useStore((s) => s.setError);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState(false);
  const [said, setSaid] = useState("");
  // whether the hand-off's part shows the task's standing offer with its own Cancel or Withdraw, as it tells: while it
  // does, the list of holds leaves the offer's hold out, so that hold has one Cancel
  const [offerShown, setOfferShown] = useState(false);
  // a 409, the task moved since the page read it, or a 412, its state or its agent's refuses the press (the agent
  // paused or retired, an unknown effect to resolve first, a task that no longer waits): said beside the
  // task in the app's own words, and the task read again; any other refusal is the page's error
  const failed = async (e: unknown) => {
    if (e instanceof ApiError && (e.status === 409 || e.status === 412)) {
      setSaid(e.message || (e.status === 409 ? "The task changed; look again." : "The task's state refuses that just now."));
      await reload();
    }
    else setError(e);
  };
  // every change but Stop and the switches names the version the page read
  const run: Run = async (what, action, body = {}) => {
    setBusy(what); setSaid("");
    try {
      const r = await Api.taskAction(task.id, action, body);
      // a press that stops the live attempt is kept whatever the workspace says of the stop; when it could not take
      // it, the app says why (`not_stopped`), and the attempt is then not said to be stopped.
      // Continue on... queues nothing at once in two cases (tasks.continue_on): on a running task, where the attempt is
      // stopped first and the next follows its end; and on one that waited, where the next attempt's packet could not
      // be built (a secret shape in the owner's words, outward effects past their budget), the task then needing the
      // owner, as its line says
      if (r.not_stopped) setSaid(r.not_stopped);
      else if (action === "continue-on" && r.queued === false) {
        setSaid(task.state === "running"
          ? "Asked for: the attempt that works now is stopped, and this one starts once it ends, unless what it leaves holds the task for you."
          : "Nothing was queued: the task waits for you first, as its line above says.");
      }
      await reload();
      return true;
    } catch (e) {
      await failed(e);
      return false;
    } finally {
      setBusy("");
    }
  };
  const between = async (to: TaskView["between_steps"]) => {
    setBusy("between"); setSaid("");
    try {
      const r = await Api.putTask(task.id, { state_version: task.state_version, between_steps: to });
      await reload();
      if (r.not_stopped) setSaid(r.not_stopped);
    }
    catch (e) { await failed(e); }
    finally { setBusy(""); }
  };
  const unknown = task.effects.filter((e) => e.state === "unknown");
  const away = AWAY.includes(task.state);
  return (
    <div className="mt-1.5 rounded-md border border-line px-2 py-1.5" role="region" aria-label={`Task: ${task.title}`}>
      <div className="flex flex-wrap items-center gap-x-2">
        <span className="font-medium">{task.title}</span>
        <span className={task.state === "needs_owner" ? "text-wait" : "text-muted"}>{STATE_WORDS[task.state]}</span>
        <span className="text-[11px] text-faint">step {task.step} of {task.steps.length}</span>
        {task.received && <span className="text-[11px] text-faint">received from a teammate; it stays in the group it was accepted in</span>}
        {!away && !editing && (
          <button className="btn btn-sm touch-target ml-auto !h-6 text-[11px]" onClick={() => setEditing(true)}>Edit</button>
        )}
      </div>
      {/* said once on the task, under the local provider, as the app's view of it says */}
      {task.local_provider && <div className="mt-0.5 text-[11px] text-faint" role="note" aria-label="Under the local provider">{LOCAL_PROVIDER_WORDS}</div>}
      {editing && settings && (
        <TaskForm agentId={task.agent_id} firstAccount={task.backend.id ?? "default"} settings={settings} accounts={accounts} task={task}
          onDone={async (made, notStopped) => { setEditing(false); setSaid(""); if (made) await reload(); if (notStopped) setSaid(notStopped); }} />
      )}
      <Waits task={task} agent={agent} settings={settings} accounts={accounts} keys={keys} unknown={unknown} busy={busy} run={run} />
      {said && <div className="mt-1 text-[11.5px] text-wait" role="status">{said}</div>}
      {(task.protected.length > 0 || task.external.length > 0) && (
        <div className="mt-1 grid gap-0.5 text-[11px] text-muted">
          {task.protected.length > 0 && <span>Protected: <code className="font-mono">{task.protected.join(", ")}</code></span>}
          {task.external.length > 0 && <span>External commands: <code className="font-mono">{task.external.join(", ")}</code></span>}
        </div>
      )}
      <TaskGrant task={task} onChanged={reload} />
      <Steps task={task} />
      <Decisions task={task} runs={runs} busy={busy} run={run} />
      <Attempts task={task} accounts={accounts} keys={keys} onOpenRun={onOpenRun} />
      <Effects task={task} unknown={unknown} busy={busy} run={run} />
      <Checkpoints task={task} />
      <Relay task={task} accounts={accounts} keys={keys} />
      {!away && <Switches task={task} settings={settings} busy={busy} run={run} />}
      <TaskHolds holds={task.holds} offerShown={offerShown} accounts={accounts} reload={reload} />
      {!away && <Controls task={task} accounts={accounts} keys={keys} busy={busy} run={run} unknown={unknown.length} onBetween={between} />}
      <TaskHandOff task={task} onChanged={() => void reload()} onOfferShown={setOfferShown} />
      <AccountRecordOf task={task} reload={reload} onGroup={groupOf(task, accounts)} />
    </div>
  );
}

// ---- what waits on the owner, and its remedy ------------------------------------------------------------------------

/** What waits on the owner, first, as one sentence and its controls: for `needs_owner`, its cause's remedy; for a task
 *  that waits for a reset, when it goes on. The unknown effects' own Resolve is in Effects, below. */
function Waits({ task, agent, settings, accounts, keys, unknown, busy, run }: {
  task: TaskView; agent: AgentHold; settings: TaskSettings | null; accounts: AccountOwnerView[]; keys: ModelProviderView[];
  unknown: TaskEffectView[]; busy: string; run: Run;
}) {
  const v = { state_version: task.state_version };
  // pressing Continue at the bound on moves opens the relay's text (point 2) over it, every time
  const [boundPress, setBoundPress] = useState(0);
  if (task.state === "suspended") {
    // a task marked returned goes on neither by itself nor at Continue, whatever it waited for
    if (markedReturned(task)) return <Line tone="muted" label="What waits">{RETURNED_WORDS}</Line>;
    // with its day where the reset is 12 hours or more off: a seven-day window's, days away, is not today's time
    const until = task.waits_until ? near(task.waits_until) : "";
    // the app goes on at a known reset only while both Auto-resume switches are on, the deployment's
    // (OPEN_EDITION_ADE_TASK_AUTO_RESUME) and the task's own (tasks.py's _auto_resume): with the deployment's off, the
    // task's own switch on changes nothing, and the line says why it is Continue then
    const byItself = !!settings?.auto_resume && task.auto_resume;
    const offHere = task.auto_resume && !!settings && !settings.auto_resume ? ": this deployment has Auto-resume off" : "";
    // and only while nothing holds it as a schedule's firing is (tasks.py's _firing_hold): a retired
    // agent's task goes on neither by itself nor at Continue (_queue refuses it), and a paused agent's waits past the
    // reset until the pause ends, by itself or at Continue (_paused_agent refuses Continue until then). The page knows
    // both from the agent, a paused one by `paused` whatever its state says (one with a live run reads working); its day's
    // runs spent and this month's machine time used it cannot tell as the app counts them, and those only make it later.
    // A hand-off to a teammate is TaskHandOff's, and not refused for a retired agent: so not "nothing continues it"
    const retired = agent.retired ? " Its agent is retired: the task goes on neither by itself nor at Continue." : "";
    const pause = !agent.paused ? ""
      : agent.paused_until ? `its agent's pause ends (${near(agent.paused_until)})` : "its agent is resumed";
    const later = pause ? `, or when ${pause} if that is later` : "";
    const once = pause ? `, once ${pause}` : "";
    // the app's own line says why (`reason`, the one line the page shows): one cause comes from
    // more than one place (`stopped` from the owner's Stop, a change, an offer or a hold on its agent; `step_done` from
    // Between steps, a change that ran with the old instructions, or a teammate's return; `window` from the accounts'
    // resets or its agent's day's runs), so the page's own words stand in only where the app gave none, and a known
    // reset adds whether the task goes on by itself then
    const resets = task.cause === "window" && until
      ? retired || (byItself ? ` It goes on by itself then (${until})${later}.` : ` Continue then (${until})${once}${offHere}.`) : "";
    const words = task.reason ? `${task.reason}${resets}`
      : task.cause === "window" && until
      ? `Every account in this task's relay is out of room; the earliest resets at ${until}${retired ? `.${retired}` : byItself ? `, and the task goes on then${later}.` : `: Continue then${once}${offHere}.`}`
      : task.cause === "step_done" ? "A step is done, and the task waits for you before the next one, as Between steps says: Continue when you are ready."
      : task.cause === "no_step" ? "The last attempt ended without finishing its step."
      : task.cause === "failed" ? "The last attempt failed."
      : task.cause === "stopped" ? "It was stopped: Continue when you choose."
      : "The last attempt ended without finishing the task.";
    return <Line tone="muted" label="What waits">{words}</Line>;
  }
  if (task.state !== "needs_owner") return null;
  const why = task.reason ? ` ${task.reason}` : "";
  const cont = (label = "Continue", disabled = false) => (
    <button className="btn btn-sm touch-target !h-6 text-[11px]" disabled={!!busy || disabled} onClick={() => void run("continue", "continue", v)}>
      {busy === "continue" ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} {label}
    </button>
  );
  const worktree = (
    <>
      <button className="btn btn-sm touch-target !h-6 text-[11px]" disabled={!!busy} onClick={() => void run("keep", "resolve", { ...v, what: "worktree", outcome: "keep" })}>Keep it</button>
      <Confirm label="Drop it" again="Drop the change?" className="touch-target !h-6 text-[11px]" onConfirm={async () => { await run("discard", "resolve", { ...v, what: "worktree", outcome: "discard" }); }} />
    </>
  );
  // a packet past its bound is ended here, not among the task's controls (Controls leaves it out), so that it is the
  // one End: in two steps, stopped
  const packetEnd = <Confirm label="End it" again="End the task?" className="touch-target !h-6 text-[11px]" onConfirm={async () => { await run("end", "end", { ...v, outcome: "stopped" }); }} />;
  // the newest attempt, whatever order the view lists them in (the app gives them as they ran)
  const last = [...task.attempts].sort((a, b) => b.fence - a.fence)[0];
  if (markedReturned(task)) {
    // no Continue, whatever it waits for. A hold that keeps a remedy of its own keeps that remedy, in the page's words
    // for it and not the app's line, which ends "then Continue" for some of these: a watched place to look at and its
    // agent to resume; an unknown effect or an unseen call to say whether it happened, while one is unresolved; a
    // worktree or a branch to keep or drop. These are tasks.py's
    // RETURNED_OWN_REMEDY, the causes for which the app still gives the push (an effect's and an unseen call's only
    // while one is unresolved, RETURNED_WHILE_UNKNOWN), kept here as the same list, each pinned by AgentTask.test.tsx.
    // A packet past its bound keeps its words and its End, the task's way out, which Controls leaves to it.
    // For each other cause, the mark's words alone, End among the task's controls.
    const own: Record<string, string> = {
      watch: "A place the workspace watches changed: look, and resume its agent where its hold asks.",
      worktree: last?.resume?.check === "worktree"
        ? "The resume check found the worktree changed. Keep it as it is (its files become the new end state), or reset it to the recorded head."
        : "The workspace stopped under an attempt, leaving a change in the task's worktree. Keep it as it is, or drop it to go back to the recorded head.",
      branch: "The task's branch moved by another hand. Keep the worktree as it is (its files become the new end state), or drop it to go back to the recorded head.",
      ...(unknown.length > 0 ? {
        effect: "An attempt stopped while it ran a command: nobody can tell whether it happened. Say for each below whether it did.",
        ungated: `${last ? `Attempt ${last.attempt}` : "An attempt"} ended outside the workspace's checks: the calls below ran, or may have run, without them. Say for each whether it happened.`,
      } : {}),
      packet: "This task has more outward effects than a packet carries.",
    };
    const remedy = task.cause !== null && Object.prototype.hasOwnProperty.call(own, task.cause) ? own[task.cause] : null;
    return (
      <Line tone="wait" label="What waits on you">
        <span>{remedy ? `${remedy} ${RETURNED_WORDS}` : task.cause === "returned" ? task.reason ?? RETURNED_WORDS : RETURNED_WORDS}</span>
        {(task.cause === "worktree" || task.cause === "branch") && <span className="mt-1 flex flex-wrap items-center gap-1.5">{worktree}</span>}
        {task.cause === "packet" && <span className="mt-1 flex flex-wrap items-center gap-1.5">{packetEnd}</span>}
      </Line>
    );
  }
  let words: ReactNode;
  let controls: ReactNode = null;
  switch (task.cause) {
    case "effect":
      words = `An attempt stopped while it ran a command: nobody can tell whether it happened. Say for each below whether it did.${why}`;
      controls = cont("Continue", unknown.length > 0);
      break;
    case "ungated":
      words = `${last ? `Attempt ${last.attempt}` : "An attempt"} ended outside the workspace's checks: the calls below ran, or may have run, without them. Say for each whether it happened; Continue waits until each is resolved.${why}`;
      controls = cont("Continue", unknown.length > 0);
      break;
    case "worktree":
      // both worktree cases come as this cause: the machine stopped under an attempt, or the newest
      // attempt's resume check found the worktree changed
      words = last?.resume?.check === "worktree"
        ? `The resume check found the worktree changed. Keep it as it is (its files become the new end state), or reset it to the recorded head.${why}`
        : `The workspace stopped under an attempt, leaving a change in the task's worktree. Keep it as it is, or drop it to go back to the recorded head.${why}`;
      controls = worktree;
      break;
    case "resume":
      // a resume check that failed, or an attempt whose record or end the app could not take, which comes as this
      // cause too, its own line saying to look at the work first: only the first is said to be a failed check
      words = last?.resume?.outcome === "blocked"
        ? `The resume check failed${last.resume.check ? ` (${last.resume.check})` : ""}.${why} Read why, then Continue or Continue on...`
        : task.reason ?? "Look at the task's work, then Continue or Continue on...";
      controls = cont();
      break;
    case "branch":
      // a branch something else moved: the worktree kept as it is or dropped, as for the worktree's own cause, before
      // anything continues the task (the app refuses Continue until then)
      words = `The task's branch moved by another hand.${why} Keep the worktree as it is (its files become the new end state), or drop it to go back to the recorded head; then Continue.`;
      controls = worktree;
      break;
    case "settings":
      words = `A setting named another credential or provider.${why} Remove that setting, then Continue.`;
      controls = cont();
      break;
    case "watch":
      // the remedy: look, resume the agent where its hold asks, then Continue, which this press
      // is; an agent still held is said beside the task in the app's words (412)
      words = `A place the workspace watches changed.${why} Look, then go on.`;
      controls = cont("I have looked: go on");
      break;
    case "window":
      // Continue when the owner judges it open, "pressing is lifting the hold": one press, which lifts
      // the refusal and goes on, as its label says (the app's resolve with a lift would leave it to a second press)
      words = `${backendWords(task.backend, accounts, keys)}'s window refused it, and when it resets is not known.${why} Go on when you judge it open.`;
      controls = cont("It is open: go on");
      break;
    case "secret":
      words = `Your own words in this task hold something shaped like a secret.${why} Edit the task, then Continue.`;
      controls = cont();
      break;
    case "identity":
      words = `An account's identity changed, and no other account can take the task.${why} Confirm it again in Connections, then Continue; or Continue on...`;
      controls = cont();
      break;
    case "moves":
      words = `The relay moved this task 10 times within an hour.${why} Read why, then Continue or Continue on...`;
      // a press of Continue opens it again; a click in the text itself (its Close among it) is no press
      controls = (
        <span className={`flex w-full flex-wrap items-center gap-1 ${TERMS_IN_FLOW}`}>
          <span onClickCapture={() => setBoundPress((n) => n + 1)}>{cont()}</span>
          <RiskTerms key={`bound-${boundPress}`} point={2} kind="control" open />
        </span>
      );
      break;
    case "handoff":
      words = `Your teammate's workspace could not take the hand-off, so the task is back with you.${why} Continue, or hand it again.`;
      controls = cont();
      break;
    case "packet":
      words = `This task has more outward effects than a packet carries.${why} End it; a new task can start from its branch's head.`;
      controls = packetEnd;
      break;
    case "returned":
      if (heldAsReturned(task)) {
        // the app's own line alone, and no Continue: End, among the task's controls, is its one way out
        words = task.reason ?? RETURNED_WORDS;
        break;
      }
      words = `It came back from your teammate and waits for your look.${why} Look at what it brings, then Continue.`;
      controls = cont();
      break;
    default:
      words = task.reason ?? "It waits for you.";
      controls = cont();
  }
  return (
    <Line tone="wait" label="What waits on you">
      <span>{words}</span>
      {controls && <span className="mt-1 flex flex-wrap items-center gap-1.5">{controls}</span>}
    </Line>
  );
}

function Line({ tone, label, children }: { tone: "wait" | "muted"; label: string; children: ReactNode }) {
  return (
    <div role="note" aria-label={label}
      className={`mt-1 flex flex-col rounded-md px-2 py-1 text-[12px] ${tone === "wait" ? "border border-wait/50 bg-wait/[0.06]" : "text-muted"}`}>
      {children}
    </div>
  );
}

// ---- steps, attempts, effects, checkpoints --------------------------------------------------------------------------

const STEP_WORDS = { todo: "to do", doing: "in progress", done: "done" } as const;

function Steps({ task }: { task: TaskView }) {
  const byId = new Map(task.checkpoints.map((c) => [c.id, c]));
  return (
    <ol className="mt-1.5 space-y-0.5" aria-label="Its steps">
      {task.steps.map((s) => {
        const cp = s.checkpoint_id ? byId.get(s.checkpoint_id) : undefined;
        return (
          <li key={s.n} className="flex flex-wrap items-baseline gap-x-2">
            <span className="w-5 text-right font-mono text-[11px] text-faint">{s.n}</span>
            <span className={s.state === "done" ? "text-muted" : ""}>{s.objective}</span>
            <span className={`text-[11px] ${s.state === "doing" ? "text-run" : "text-faint"}`}>{STEP_WORDS[s.state]}</span>
            {s.state === "done" && s.evidence && (
              <span className="text-[11px] text-faint" title={s.evidence === "checked" ? "its check passed" : "it has no check: only the model's word"}>
                {s.evidence === "checked" ? "checked" : "said done"}{cp?.head ? ` · ${short(cp.head, 7)}` : ""}
              </span>
            )}
            {s.check && <code className="font-mono text-[11px] text-faint" title="Its check">{s.check}</code>}
            {s.reopened && <span className="text-[11px] text-wait">reopened</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** The owner's answers to the task's questions so far (its decisions, part of its instructions), and the newest
 *  attempt's question, when its run asked one, answered here: kept as a decision, the next attempt's instruction. */
function Decisions({ task, runs, busy, run }: { task: TaskView; runs: AgentRun[]; busy: string; run: Run }) {
  const [text, setText] = useState("");
  const newest = [...task.attempts].sort((a, b) => b.fence - a.fence)[0];
  const asked = newest ? runs.find((r) => r.run_id === newest.run_id)?.question ?? null : null;
  if (!task.decisions.length && !asked) return null;
  return (
    <div className="mt-1.5">
      {task.decisions.length > 0 && (
        <ol className="space-y-0.5" aria-label="Its decisions">
          {task.decisions.map((d) => (
            <li key={d.n} className="text-[11.5px]">
              <span className="text-faint">{d.author === "owner" ? "You" : "Your teammate"}, <span title={full(d.at)}>{dayTime(d.at)}</span>:</span> {d.text}
            </li>
          ))}
        </ol>
      )}
      {asked && AWAY.indexOf(task.state) < 0 && !ENDED.includes(task.state) && (
        <form className="mt-1 rounded-md border border-wait/50 bg-wait/[0.06] px-2 py-1"
          onSubmit={(e) => { e.preventDefault(); if (text.trim()) void run("answer", "answer", { state_version: task.state_version, text: text.trim() }).then((ok) => ok && setText("")); }}>
          <div className="text-[11px] text-wait">Attempt {newest.attempt} asks you</div>
          <div className="whitespace-pre-wrap text-[12px]">{asked}</div>
          <div className="mt-1 flex gap-1.5">
            <input className="input !h-7 min-w-0 flex-1 text-[12px]" aria-label="Your answer, kept as a decision of the task" maxLength={2000}
              value={text} onChange={(e) => setText(e.target.value)} />
            <button className="btn btn-sm touch-target !h-7" disabled={!!busy || !text.trim()}>Answer</button>
          </div>
        </form>
      )}
    </div>
  );
}

function Attempts({ task, accounts, keys, onOpenRun }: { task: TaskView; accounts: AccountOwnerView[]; keys: ModelProviderView[]; onOpenRun(runId: string): void }) {
  if (!task.attempts.length) return null;
  const newest = [...task.attempts].sort((a, b) => b.fence - a.fence);
  return (
    <div className="mt-1.5">
      <ul className="space-y-0.5" aria-label="Its attempts">
        {newest.map((a) => (
          <li key={a.run_id} className="flex flex-wrap items-baseline gap-x-2 text-[11.5px]">
            <button className="underline decoration-dotted underline-offset-2 hover:text-fg" onClick={() => onOpenRun(a.run_id)}
              aria-label={`Open attempt ${a.attempt} of step ${a.step}`}>step {a.step}, attempt {a.attempt}</button>
            <span className="rounded border border-line px-1 text-[11px]">{backendWords(a.backend, accounts, keys)}</span>
            {a.started_at && <span className="text-faint" title={full(a.started_at)}>{clock(a.started_at)}{a.ended_at ? `–${clock(a.ended_at)}` : ""}</span>}
            <span className="text-muted">{a.state}{a.cause ? `, ${a.cause}` : ""}{a.reason ? `: ${a.reason}` : ""}</span>
            {a.resume && <span className="text-faint">resume check {a.resume.outcome}{a.resume.check ? ` (${a.resume.check})` : ""}</span>}
          </li>
        ))}
      </ul>
      <div className="mt-0.5 text-[11px] text-faint">
        A command the workspace cannot read, such as a script, is told to the next attempt as having run, not as done; name
        the ones that reach outside as external commands.
      </div>
    </div>
  );
}

/** The task's unknown effects, each with Resolve: an `ungated` end's unseen calls among them, named by their tool and
 *  target. "It did not happen" on a call that reaches outside is two-step, as the page's other controls that cannot be
 *  undone are. */
function Effects({ task, unknown, busy, run }: { task: TaskView; unknown: TaskEffectView[]; busy: string; run: Run }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!unknown.length) return null;
  const resolve = (e: TaskEffectView, outcome: "happened" | "not_happened") =>
    run(`effect-${e.id}`, "resolve", { state_version: task.state_version, what: "effect", effect_id: e.id, outcome, ...(notes[e.id] ? { note: notes[e.id] } : {}) });
  return (
    <ul className="mt-1.5 space-y-1" aria-label="Calls nobody can tell happened">
      {unknown.map((e) => (
        <li key={e.id} className="rounded-md border border-wait/40 px-2 py-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[11px]">{e.tool}</span>
            {e.target && <code className="min-w-0 truncate font-mono text-[11px] text-muted" title={e.target}>{e.target}</code>}
            {e.unseen && <span className="text-[11px] text-wait">ran, or may have run, without the workspace's checks</span>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="text-[11.5px]">Did it happen?</span>
            <button className="btn btn-sm touch-target !h-6 text-[11px]" disabled={!!busy} onClick={() => void resolve(e, "happened")}>It happened</button>
            {OUTWARD.includes(e.kind)
              ? <Confirm label="It did not happen" again="Sure it did not?" className="touch-target !h-6 text-[11px]" onConfirm={async () => { await resolve(e, "not_happened"); }} />
              : <button className="btn btn-sm touch-target !h-6 text-[11px]" disabled={!!busy} onClick={() => void resolve(e, "not_happened")}>It did not happen</button>}
            <input className="input !h-6 min-w-[120px] flex-1 text-[11.5px]" aria-label={`A note on ${e.tool}`} placeholder="A note, for the journal" maxLength={2000}
              value={notes[e.id] ?? ""} onChange={(x) => setNotes({ ...notes, [e.id]: x.target.value })} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Checkpoints({ task }: { task: TaskView }) {
  if (!task.checkpoints.length) return null;
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer text-[11.5px] text-muted">Checkpoints ({task.checkpoints.length})</summary>
      <ul className="mt-0.5 space-y-0.5" aria-label="Its checkpoints">
        {task.checkpoints.map((c) => (
          <li key={c.id} className="font-mono text-[11px] text-faint">
            <span>{c.n} · step {c.step} · {short(c.head, 7) || "no commit"} · <span title={full(c.created_at)}>{dayTime(c.created_at)}</span> · {short(c.digest)}</span>
            {c.left_out.length > 0 && <div className="font-sans text-wait">Left out: {c.left_out.join(", ")}</div>}
          </li>
        ))}
      </ul>
    </details>
  );
}

// ---- the relay ----------------------------------------------------------------------------------------------------------

/** The relay: each move as one line, each opening its risk point's text once in this browser (the crossing and Back
 *  home lines point 1's, each relay move point 2's, custody's hand-off point 12's), with a link that opens it again;
 *  each automatic resume on the same account, which is no move, listed with its push; and the relay set in order,
 *  with each account's windows as last recorded and why any is left out, usage credits' text (point 8) opening beside
 *  each of its accounts each time it is unfolded. */
function Relay({ task, accounts, keys, linesOnly = false }: {
  task: TaskView; accounts: AccountOwnerView[]; keys: ModelProviderView[];
  /** a task that ended: its moves' lines alone, its relay set no longer anything to act on */
  linesOnly?: boolean;
}) {
  const user = useStore((s) => s.user?.id ?? "");
  const [resumes, setResumes] = useState<TaskEvent[]>([]);
  const [regrouped, setRegrouped] = useState<TaskEvent[]>([]);
  const [setOpen, setSetOpen] = useState(false);
  const lines = useMemo(() => [...task.moves].sort((a, b) => a.seq - b.seq), [task.moves]);
  // before any line's text decides whether to open: a browser that has opened none takes those older than 7 days as
  // opened (RiskTerms keeps it), so its first visit opens the last 7 days' alone, once each
  useMemo(() => settleFirstVisit(user, lines.filter((m) => m.point != null).map((m) => ({ event: `task:${task.id}:${m.seq}`, at: m.at }))),
    [user, task.id, lines]);
  const seqs = lines.map((m) => m.seq).join(",");
  // the group its account is in: a confirmation that moves the account writes the task's journal and changes none of
  // an offered task's row, so the journal is read again when the group changes, not only its state_version
  const onGroup = groupOf(task, accounts);
  useEffect(() => {
    let gone = false;
    Api.taskJournal(task.id).then((j) => {
      if (gone || !Array.isArray(j?.events)) return;
      // an automatic resume on the account the task was on is no move of its own: it is listed, with its push
      setResumes(j.events.filter((e) => e.kind === "task.continued" && e.payload?.by === "auto_resume"
        && JSON.stringify(e.payload.from) === JSON.stringify(e.payload.to)));
      // its account confirmed again, into another group or as another kind of seat: whose usage its attempts on it now
      // run on, and why, in the app's own words (task.account_regrouped's `why`); no move of its own. Every such line
      // of the newest page of the journal, in its order, newest first, after the moves and the automatic resumes
      setRegrouped(j.events.filter((e) => e.kind === "task.account_regrouped" && typeof e.payload?.why === "string"));
    }).catch(() => { /* the resumes are listed when the journal can be read */ });
    return () => { gone = true; };
  }, [task.id, task.state_version, seqs, onGroup]);
  const home = groupWords(task.home_grp, accounts);
  return (
    <div className="mt-1.5">
      {(lines.length > 0 || resumes.length > 0 || regrouped.length > 0) && (
        <ul className="space-y-0.5" aria-label="Its moves">
          {lines.map((m) => (
            <li key={m.seq} className={`flex flex-wrap items-baseline gap-x-1.5 text-[11.5px] ${TERMS_IN_FLOW}`}>
              <span className="font-mono text-[11px] text-faint" title={full(m.at)}>{clock(m.at)}</span>
              <span>{lineWords(m, accounts, keys)}</span>
              {m.why && <span className="text-muted">({m.why})</span>}
              {m.point != null && (
                <RiskTerms point={m.point as RiskPoint} kind="event" event={`task:${task.id}:${m.seq}`} cases={m.cases} homeGroup={home} />
              )}
            </li>
          ))}
          {resumes.map((e) => (
            <li key={`r${e.seq}`} className="text-[11.5px] text-muted">
              <span className="font-mono text-[11px] text-faint" title={full(e.at)}>{clock(e.at)}</span> It went on by itself on the same account, its
              window reset; your devices were told "A task of yours continued".
            </li>
          ))}
          {regrouped.map((e) => (
            <li key={`g${e.seq}`} className="text-[11.5px] text-muted">
              <span className="font-mono text-[11px] text-faint" title={full(e.at)}>{clock(e.at)}</span> {String(e.payload.why)}
            </li>
          ))}
        </ul>
      )}
      {!linesOnly && task.relay_set.length > 0 && (
        <div className="mt-1">
          <button className="touch-target flex items-center gap-1 text-[11.5px] text-muted hover:text-fg" aria-expanded={setOpen} onClick={() => setSetOpen(!setOpen)}>
            {setOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Its relay: {task.relay_set.filter((r) => r.eligible).length} of {task.relay_set.length} accounts
          </button>
          {setOpen && (
            <ol className="mt-0.5 space-y-0.5" aria-label="Its relay set">
              {task.relay_set.map((r) => (
                <li key={r.account.id} className={`flex flex-wrap items-baseline gap-x-2 text-[11.5px] ${TERMS_IN_FLOW}`}>
                  <span className="w-4 text-right font-mono text-[11px] text-faint">{r.order}</span>
                  <span className={r.eligible ? "" : "text-muted"}>{accountWords(r.account)}</span>
                  <span className="text-[11px] text-faint">{planWords(r.account)}</span>
                  <span className="text-[11px] text-faint">{windowsWords(r.account.windows)}</span>
                  {!r.eligible && <span className="text-[11px] text-wait">left out: {leftOutWords(r.left_out ?? "not eligible")}</span>}
                  {/* usage credits' text (point 8) beside each account of the set, as the set is unfolded */}
                  <RiskTerms point={8} kind="control" open />
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

// ---- switches, holds, controls --------------------------------------------------------------------------------------

/** What the relay off means for a task (the relay is off unless
 *  the deployment sets it): when its account runs out of room it waits for that account's reset, going on there by
 *  itself while Auto-resume is on and at Continue otherwise, or for the owner where no reset is known (tasks.py's
 *  _wait_for_reset: the task needs its owner), and Continue on... moves it to another account now. */
export function relayOffWords(autoResume: boolean): string {
  return `When its account runs out of room, the task waits for that account's reset${autoResume ? " and goes on there by itself (Auto-resume)" : ", and you Continue it then (Auto-resume is off)"}, or for you where no reset is known; Continue on... moves it to another of your accounts now.`;
}

/** Relay and Auto-resume, turned on or off with no version and no confirmation; each opens its risk point's text (2 and
 *  7) as it is about to be turned on, the pointer coming to it or the keyboard, and again as it is. "Cross
 *  organisations" turned on takes a confirmation and a hold (task-cross), its text (point 3) opening the same way; off
 *  on a task received from a teammate, and not to be turned on. A switch whose setting the deployment has off is not
 *  to be turned on either (the app refuses it, 403), and says so; one already on may still be turned off. */
function Switches({ task, settings, busy, run }: { task: TaskView; settings: TaskSettings | null; busy: string; run: Run }) {
  const setError = useStore((s) => s.setError);
  // how many times each switch's text was opened here: each time opens it again, under the switch
  const [on, setOn] = useState({ relay: 0, auto_resume: 0, cross: 0 });
  const [asking, setAsking] = useState<Change | null>(null);
  // a switch about to be turned on: its text opens as the pointer comes to it or it takes the
  // keyboard, before the press, holding nothing; a touch screen, where no pointer comes first, opens it at the press
  const soon = (which: "relay" | "auto_resume" | "cross", isOn: boolean) => () => {
    if (!isOn) setOn((o) => ({ ...o, [which]: o[which] + 1 }));
  };
  const turn = (which: "relay" | "auto_resume", to: boolean) => {
    if (to) setOn((o) => ({ ...o, [which]: o[which] + 1 }));
    void run(`switch-${which}`, "switches", { [which]: to });
  };
  const crossOn = async () => {
    setOn((o) => ({ ...o, cross: o.cross + 1 }));
    try {
      setAsking({ what: "task-cross", label: "Letting this task cross organisations again", action: "Turn it on", subject: `task-cross:${task.id}`, ways: await confirmWays(),
                  note: "It turns on when its hold ends, unless you cancel it." });
    } catch (e) { setError(e); }
  };
  const crossAllowed = !task.received && (settings?.cross_groups ?? true);
  // the deployment's own settings: a task's switch is turned on only while its setting is on
  const relayAllowed = settings?.relay ?? true;
  const autoAllowed = settings?.auto_resume ?? true;
  return (
    <div className="mt-1.5 grid gap-1 text-[11.5px]" role="group" aria-label="Its switches">
      <label className="flex items-center gap-1.5" onMouseEnter={soon("relay", task.relay || !relayAllowed)}>
        <input type="checkbox" checked={task.relay} disabled={!!busy || (!task.relay && !relayAllowed)}
          onFocus={soon("relay", task.relay || !relayAllowed)} onChange={(e) => turn("relay", e.target.checked)} />
        <span><span className="font-medium">Relay</span>: when an account runs out of room, the task goes on on your next account.</span>
      </label>
      <label className="flex items-center gap-1.5" onMouseEnter={soon("auto_resume", task.auto_resume || !autoAllowed)}>
        <input type="checkbox" checked={task.auto_resume} disabled={!!busy || (!task.auto_resume && !autoAllowed)}
          onFocus={soon("auto_resume", task.auto_resume || !autoAllowed)} onChange={(e) => turn("auto_resume", e.target.checked)} />
        <span><span className="font-medium">Auto-resume</span>: when an account's window resets, the task goes on there by itself, while you are away, as a schedule does.</span>
      </label>
      <label className="flex items-center gap-1.5" onMouseEnter={soon("cross", task.cross_groups || !crossAllowed)}>
        <input type="checkbox" checked={task.cross_groups} disabled={!!busy || (!task.cross_groups && !crossAllowed)}
          onFocus={soon("cross", task.cross_groups || !crossAllowed)}
          onChange={(e) => (e.target.checked ? void crossOn() : void run("switch-cross", "switches", { cross_groups: false }))} />
        <span><span className="font-medium">Cross organisations</span>: when no account of this task's group has room, the relay may move it between your personal plans and one Team organisation's seats.</span>
      </label>
      {/* under the switches, outside their labels, so a text is no part of a switch's name */}
      <div className={`grid ${TERMS_IN_FLOW}`}>
        {on.relay > 0 && <RiskTerms key={`relay-${on.relay}`} point={2} kind="control" open />}
        {on.auto_resume > 0 && <RiskTerms key={`auto-${on.auto_resume}`} point={7} kind="control" open />}
        {on.cross > 0 && <RiskTerms key={`cross-${on.cross}`} point={3} kind="control" open />}
      </div>
      {/* the relay off for this task, by the deployment's setting or by its own switch: what that means, and the two
          ways to move it */}
      {!relayAllowed && <div className="text-[11px] text-faint">This deployment has the relay off (OPEN_EDITION_ADE_TASK_RELAY): no task's relay runs, whatever its Relay switch says, and a task made while it is off keeps its switch off after the deployment turns the relay on, until you turn it on. {relayOffWords(task.auto_resume && autoAllowed)}</div>}
      {!relayAllowed && task.cross_groups && <div className="text-[11px] text-faint">Its Cross organisations switch does nothing while the relay is off: only the relay crosses by itself.</div>}
      {relayAllowed && !task.relay && <div className="text-[11px] text-faint">The relay is off for this task. {relayOffWords(task.auto_resume && autoAllowed)} Turn Relay on to let it move by itself the next time its account runs out of room.</div>}
      {!autoAllowed && <div className="text-[11px] text-faint">This deployment has Auto-resume off: a task's Auto-resume stays off.</div>}
      {task.received && <div className="text-[11px] text-faint">A task received from a teammate stays in its group: its crossing switch stays off.</div>}
      {!task.received && settings && !settings.cross_groups && <div className="text-[11px] text-faint">This deployment does not let a task's relay cross organisations.</div>}
      {task.crossings_ended && <div className="text-[11px] text-faint">After your press onto another organisation's seats, or onto or off an Enterprise seat, it crosses nowhere by itself, whatever this switch says.</div>}
      {asking && (
        <ConfirmIt key={asking.subject} change={asking} onCancel={() => setAsking(null)}
          onConfirmed={async (confirmation) => { if (await run("switch-cross", "switches", { cross_groups: true, confirmation })) setAsking(null); }} />
      )}
    </div>
  );
}

/** The holds that wait on this task, each with what it does, the time it takes effect and Cancel: the crossing switch
 *  turned back on (`task.cross`), custody's crossing tick (`task.crossing`), an offer of the task to a teammate
 *  (`task.offer`: cancelled, the task is back as it was), and an account's confirmation
 *  (`task.account_own`), named by the account's email. */
export function holdWords(h: HoldRef, accounts: AccountOwnerView[]): string {
  if (h.kind === "task.cross") return "Crossing organisations turns back on";
  if (h.kind === "task.crossing") return "Custody's crossing takes effect";
  if (h.kind === "task.offer") return "Your offer of this task to a teammate goes out";
  if (h.kind === "task.account_own") {
    const a = accounts.find((x) => x.hold?.id === h.id);
    return `${a?.report?.email ?? (a ? accountWords(a) : "An account")} joins your tasks' relay`;
  }
  return "A change takes effect";
}

function TaskHolds({ holds, offerShown, accounts, reload }: {
  holds: HoldRef[]; offerShown: boolean; accounts: AccountOwnerView[]; reload: () => Promise<void>;
}) {
  const setError = useStore((s) => s.setError);
  // the task's own holds, and every account's confirmation that waits to join tasks' relays, read from the account's
  // own row, which the task's view need not carry: each hold once; an offer's hold left
  // out while the hand-off's part shows that offer with its own Cancel, which withdraws it, so the hold has one Cancel
  const own = offerShown ? holds.filter((h) => h.kind !== "task.offer") : holds;
  const all = [...own, ...accounts.filter((a) => a.state === "held" && a.hold).map((a) => a.hold!)]
    .filter((h, i, list) => list.findIndex((x) => x.id === h.id) === i);
  if (!all.length) return null;
  const cancel = async (h: HoldRef) => { try { await Api.cancelHold(h.id); await reload(); } catch (e) { setError(e); } };
  return (
    <ul className="mt-1 space-y-0.5" aria-label="Its holds">
      {all.map((h) => (
        <li key={h.id} className="flex items-center gap-2 text-[11.5px] text-wait">
          <span>{holdWords(h, accounts)} at <span title={full(h.takes_effect_at)}>{clock(h.takes_effect_at)}</span>, unless you cancel it.</span>
          <button className="btn btn-sm touch-target !h-6 text-[11px]" onClick={() => void cancel(h)}>Cancel</button>
        </li>
      ))}
    </ul>
  );
}

/** The owner's controls by the task's state: Start; Continue; Continue on... and Next on..., each from
 *  the list of the owner's own backends; Stop, two-step; and End, done or stopped, two-step (a packet past its bound
 *  has its End in what waits on the owner, Waits, instead). A task received from a teammate that went back to them
 *  (marked returned, or held as returned) has no Continue, Continue on... or Next on..., which the app refuses on it in
 *  every state, and keeps Stop and End; a list of Continue on... or Next on... opened before the mark arrived closes
 *  with it. */
function Controls({ task, accounts, keys, busy, run, unknown, onBetween }: {
  task: TaskView; accounts: AccountOwnerView[]; keys: ModelProviderView[]; busy: string; run: Run; unknown: number;
  onBetween: (to: TaskView["between_steps"]) => Promise<void>;
}) {
  const [choosing, setChoosing] = useState<"continue-on" | "next-on" | null>(null);
  const v = { state_version: task.state_version };
  const s = task.state;
  const goesOn = !markedReturned(task) && !heldAsReturned(task);
  return (
    <div className="mt-1.5">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Its controls">
        {s === "ready" && (
          <button className="btn btn-sm btn-primary touch-target !h-6 text-[11px]" disabled={!!busy || unknown > 0} onClick={() => void run("start", "start", v)}>
            {busy === "start" ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Start
          </button>
        )}
        {s === "suspended" && goesOn && (
          <button className="btn btn-sm btn-primary touch-target !h-6 text-[11px]" disabled={!!busy || unknown > 0} onClick={() => void run("continue", "continue", v)}>
            {busy === "continue" ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} Continue
          </button>
        )}
        {(s === "suspended" || s === "running" || s === "needs_owner") && goesOn && (
          <button className="btn btn-sm touch-target !h-6 text-[11px]" aria-expanded={choosing === "continue-on"} disabled={!!busy}
            onClick={() => setChoosing(choosing === "continue-on" ? null : "continue-on")}>Continue on...</button>
        )}
        {s === "running" && goesOn && (
          <button className="btn btn-sm touch-target !h-6 text-[11px]" aria-expanded={choosing === "next-on"} disabled={!!busy}
            onClick={() => setChoosing(choosing === "next-on" ? null : "next-on")}>Next on...</button>
        )}
        {s === "running" && (
          <Confirm label="Stop" again="Stop the attempt?" icon={<Square size={11} />} className="touch-target !h-6 text-[11px]"
            onConfirm={async () => { await run("stop", "stop", {}); }} />
        )}
        {(s === "ready" || s === "suspended" || (s === "needs_owner" && task.cause !== "packet")) && (
          <>
            <Confirm label="Mark it done" again="Mark the task done?" className="touch-target !h-6 text-[11px]" onConfirm={async () => { await run("end", "end", { ...v, outcome: "done" }); }} />
            <Confirm label="End it" again="End the task, stopped?" className="touch-target !h-6 text-[11px]" onConfirm={async () => { await run("end", "end", { ...v, outcome: "stopped" }); }} />
          </>
        )}
        <label className="ml-auto flex items-center gap-1 text-[11px] text-muted">Between steps
          <select className="input !h-6 !py-0 text-[11px]" value={task.between_steps} disabled={!!busy}
            onChange={(e) => void onBetween(e.target.value as TaskView["between_steps"])}>
            <option value="checked">go on after a checked step</option>
            <option value="any">go on after any step</option>
            <option value="wait">wait for me</option>
          </select>
        </label>
      </div>
      {unknown > 0 && (s === "ready" || s === "suspended" || s === "needs_owner") && (
        <div className="mt-0.5 text-[11px] text-wait">Nothing is queued while a call above is unresolved: resolve each first.</div>
      )}
      {choosing && goesOn && (
        <BackendList task={task} accounts={accounts} keys={keys} next={choosing === "next-on"} busy={busy}
          onChoose={async (backend, cap) => {
            const ok = await run(choosing, choosing, { ...v, backend, ...(cap != null ? { cap_usd: cap } : {}) });
            if (ok) setChoosing(null);
          }} />
      )}
    </div>
  );
}

/** Continue on...'s list (and Next on...'s): every account the owner confirmed, one in another group or held back by
 *  its reserve among them (a press goes through either), each with its label, whether it is
 *  ready, its email, plan and organisation, and its windows as last recorded, or "unknown"; and, for Continue on...,
 *  the keys the workspace keeps, each asking a cost cap for that one attempt. As the list opens, the relay's text
 *  (point 2) opens above it where it holds another account of the task's group, usage credits' (point 8) beside each
 *  account, and the organisation's beside each account of another group (point 4, or 5 onto or off an Enterprise
 *  seat, each with the seat-settings text onto a seat); beside a key chosen, its own (point 11). Next on...'s route
 *  takes no cap, so it offers the accounts alone. */
function BackendList({ task, accounts, keys, next, busy, onChoose }: {
  task: TaskView; accounts: AccountOwnerView[]; keys: ModelProviderView[]; next: boolean; busy: string;
  onChoose: (backend: BackendRef, cap: number | null) => Promise<void>;
}) {
  const current = task.backend.kind === "account" ? accounts.find((a) => a.account === task.backend.id) : undefined;
  const here = current?.grp ?? task.home_grp;
  // the kinds as the app reads them where it decides a crossing: a seat of an organisation that also holds an
  // Enterprise seat of the owner's is read as an Enterprise one (AccountOwnerView's org_kind)
  const onEnterprise = readKind(current) === "enterprise";
  const confirmed = accounts.filter((a) => a.state === "confirmed" && a.account !== current?.account);
  const [chosen, setChosen] = useState<string>("");
  const [cap, setCap] = useState("");
  const choices: { id: string; backend: BackendRef; account?: AccountOwnerView; key?: ModelProviderView }[] = [
    ...confirmed.map((a) => ({ id: `account:${a.account}`, backend: { kind: "account", id: a.account } as BackendRef, account: a })),
    ...(next ? [] : keys.map((k) => ({ id: `key:${k.id}`, backend: { kind: "key", provider: k.id } as BackendRef, key: k }))),
  ];
  const pick = choices.find((c) => c.id === chosen);
  const capNumber = cap.trim() ? Number(cap) : NaN;
  const ready = pick && (!pick.key || (Number.isFinite(capNumber) && capNumber > 0));
  const sameGroup = confirmed.some((a) => a.grp === here);
  return (
    <div className={`mt-1.5 rounded-md border border-line p-2 ${TERMS_IN_FLOW}`} role="group" aria-label={next ? "Next on" : "Continue on"}>
      {sameGroup && <RiskTerms point={2} kind="control" open />}
      {choices.length === 0 && <div className="text-[11.5px] text-faint">No other account of yours is confirmed, and no key is kept: add one in Connections.</div>}
      <ul className="grid gap-1">
        {choices.map((c) => {
          const a = c.account;
          const isChosen = chosen === c.id;
          const otherGroup = !!a && a.grp !== here;
          const enterprise = !!a && (readKind(a) === "enterprise" || (onEnterprise && a.grp !== here));
          const seat = !!a && SEAT_PLANS.includes(a.report?.plan ?? "");
          return (
            <li key={c.id} className={TERMS_IN_FLOW}>
              <label className="flex flex-wrap items-center gap-x-2 text-[12px]">
                <input type="radio" name={`backend-${task.id}`} checked={isChosen} onChange={() => setChosen(c.id)} />
                {a ? (
                  <>
                    <span>{accountWords(a)}</span>
                    <span className="text-[11px] text-faint">{planWords(a)}</span>
                    <span className={`text-[11px] ${a.ready ? "text-done" : "text-faint"}`}>{a.ready ? "ready" : "not ready"}</span>
                    {!a.in_relays && <span className="text-[11px] text-faint">out of relays</span>}
                    <span className="text-[11px] text-faint">{windowsWords(a.windows)}</span>
                  </>
                ) : (
                  <span>API key · {c.key!.vendor} <span className="text-[11px] text-faint">({c.key!.label})</span></span>
                )}
              </label>
              {/* the organisation's text beside each account of another group, and an Enterprise seat's beside each
                  account onto or off one, as the list opens (points 4 and 5, "beside such an account"), whichever
                  is chosen */}
              {a && enterprise && <RiskTerms point={5} kind="control" open cases={readKind(a) === "enterprise" ? ["onto_seat"] : []} />}
              {a && otherGroup && !enterprise && <RiskTerms point={4} kind="control" open cases={seat ? ["onto_seat"] : []} />}
              {/* usage credits' text (point 8) beside each account, as the list opens */}
              {a && <RiskTerms point={8} kind="control" open />}
              {isChosen && c.key && (
                <div className={`mt-1 flex flex-wrap items-center gap-1.5 pl-5 text-[11.5px] ${TERMS_IN_FLOW}`}>
                  <label className="flex items-center gap-1">A cost cap for this one attempt, in US dollars
                    <input className="input !h-6 w-20 text-[11.5px]" inputMode="decimal" aria-label="The cost cap in US dollars" value={cap} onChange={(e) => setCap(e.target.value)} />
                  </label>
                  <RiskTerms point={11} kind="control" open cases={[c.key.id === "anthropic" ? "anthropic_key" : "other_vendor"]} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <button className="btn btn-sm btn-primary touch-target mt-1.5 !h-6 text-[11px]" disabled={!ready || !!busy}
        onClick={() => pick && void onChoose(pick.backend, pick.key ? capNumber : null)}>
        {busy ? <Loader2 size={11} className="animate-spin" /> : null} {next ? "Next on this" : task.state === "running" ? "Stop the attempt, and continue on this" : "Continue on this"}
      </button>
    </div>
  );
}

// ---- tasks that ended, and the account record ------------------------------------------------------------------------

/** A task that ended: its title, how it ended and on which backend, and Delete, two-step; under it its moves, each
 *  opening its text once in this browser as an open task's do (a move made while the owner was away, on a task that
 *  then ended, opens at the next visit all the same), and its account record. */
function EndedTask({ task, accounts, keys, reload }: { task: TaskView; accounts: AccountOwnerView[]; keys: ModelProviderView[]; reload: () => Promise<void> }) {
  const setError = useStore((s) => s.setError);
  return (
    <li className="text-[11.5px]" aria-label={`Task that ended: ${task.title}`}>
      <div className="flex flex-wrap items-center gap-x-2">
        <span>{task.title}</span>
        <span className="text-faint">{STATE_WORDS[task.state]}, on {backendWords(task.backend, accounts, keys)}</span>
        <span className="ml-auto">
          <Confirm label="Delete" again="Delete the task? Its account record stays" icon={<Trash2 size={11} />} className="touch-target !h-6 text-[11px]"
            onConfirm={async () => { try { await Api.deleteTask(task.id, task.state_version); await reload(); } catch (e) { setError(e); } }} />
        </span>
      </div>
      {task.moves.length > 0 && <Relay task={task} accounts={accounts} keys={keys} linesOnly />}
      <AccountRecordOf task={task} reload={reload} />
    </li>
  );
}

/** The group and kind of seat of the account a task is on, and the kind its organisation is read as, as the page
 *  last read its accounts ("" for a key, or an account not listed): what changes when a confirmation moves the
 *  account into another group, and when one of another account changes how its organisation is read (a reread's
 *  journal line, which changes neither the account's own group and kind nor, for an offered task or one whose binding
 *  needed no change, the task's row). */
function groupOf(task: TaskView, accounts: AccountOwnerView[]): string {
  const on = task.backend.kind === "account" ? accounts.find((a) => a.account === task.backend.id) : undefined;
  return on ? `${on.grp ?? ""}|${on.grp_kind ?? ""}|${on.org_kind ?? ""}` : "";
}

/** A live task's account record, read from its own route, again when its state_version or its account's group
 *  changes (a regrouping's entry changes no offered task's row). */
function AccountRecordOf({ task, reload, onGroup = "" }: { task: TaskView; reload: () => Promise<void>; onGroup?: string }) {
  const [record, setRecord] = useState<AccountRecordView | null>(null);
  useEffect(() => {
    let gone = false;
    Api.taskAccountRecord(task.id).then((r) => { if (!gone && r?.record) setRecord(r.record); })
      .catch(() => { /* none read: the record is shown once it can be */ });
    return () => { gone = true; };
  }, [task.id, task.state_version, onGroup]);
  return record ? <AccountRecord record={record} onChanged={reload} /> : null;
}

// a regrouping is a confirmation that put the account into another group, or made it another kind of seat of its
// organisation, which stays in its group (tasks._account_moved)
const ENTRY_WORDS: Record<AccountRecordEntry["kind"], string> = { attempt: "an attempt", crossing: "a crossing", handoff: "a hand-off", deleted: "the task deleted", regrouped: "its account confirmed again, into another group or as another kind of seat" };

/** One account of an entry: a key's vendor and label, a teammate's user id, or an account's email, each with its plan
 *  and organisation; with `withOrgId`, the organisation's id too, for an entry whose two accounts name organisations
 *  of the same name, which their names alone would show as one. The flag is given every time, so that no caller
 *  passes a map's index for it. */
function snapshotWords(s: AccountSnapshot, withOrgId: boolean): string {
  if (s.vendor || s.label) return `API key · ${s.vendor ?? ""}${s.label ? ` (${s.label})` : ""}`;
  const org = s.organization ? `, ${s.organization}${withOrgId && s.org_id ? ` (${s.org_id})` : ""}` : "";
  if ("teammate" in s) return `${s.teammate ? `a teammate (user ${s.teammate})` : "a teammate"}, ${s.plan ?? "plan not kept"}${org}`;
  return `${s.email ?? "an account"}, ${s.plan ?? "plan not kept"}${org}`;
}

/** What the New task form says over the account chosen when a task cannot start on it (the app's create refuses any
 *  account not confirmed as the owner's own), in the account's own standing with tasks' relays: not read or not
 *  confirmed yet; confirmed and waiting to join; its identity changed since it was confirmed; or one that cannot be
 *  confirmed, which comes first, before a wait, as on its row in Connections (a held account whose read again found
 *  a mismatch: the app ends its wait without it joining). Each names Connections, where that is settled. */
export function pointerWords(a: AccountOwnerView | undefined, now = Date.now() / 1000): string {
  const start = "A task starts on an account confirmed as yours, and this one";
  if (a?.mismatch) return `${start} cannot be confirmed: ${mismatchWords(a.mismatch)}. Connections says more.`;
  if (a?.state === "held") {
    const at = a.hold ? ` (at ${a.hold.takes_effect_at - now > 3600 ? dayTime(a.hold.takes_effect_at) : clock(a.hold.takes_effect_at)})` : "";
    return `${start} is confirmed and waits to join your tasks' relay${at}: a task cannot start on it until then. Connections shows the wait${a.hold ? ", with Cancel" : ""}.`;
  }
  if (a?.state === "changed") return `${start}'s identity changed since you confirmed it. Confirm it again in Connections first.`;
  return `${start} is not confirmed yet. Confirm it in Connections first.`;
}

/** Why an account cannot be confirmed, from the field the app's last read of it names (`mismatch`, tasks.py's
 *  identity_mismatch), each said as what the app found, which is not always a disagreement: "report", a start that did
 *  not report both an email and a plan, which the app keeps a report by (it gave nothing, or a sign-in gave no plan);
 *  "auth status", its sign-in's own record not read with it; "orgId", that record giving no organisation id the app
 *  can keep; "plan", a plan the app does not know or that record naming another; any other field, the two
 *  disagreeing on it. The same words on its row in Connections, on the New task form's label and on its pointer. */
export function mismatchWords(field: string): string {
  if (field === "report") return "its start did not report both the email and the plan the app confirms it by";
  if (field === "auth status" || field === "auth_status") return "its sign-in's own record could not be read with it";
  if (field === "orgId") return "its sign-in's own record gives no organisation id the app can keep";
  if (field === "plan") return "its plan is one the app does not know, or its sign-in's own record names another";
  return `its sign-in's own record disagrees with what its start reported, on its ${field}`;
}

/** Whether an entry's two accounts name organisations of the same name (a crossing's, or a regrouping's from one
 *  organisation to another of that name, or to another kind of seat of the same one): then each is said with its
 *  organisation's id (snapshotWords). */
function sameOrganisationName(accounts: AccountSnapshot[]): boolean {
  return accounts.length === 2 && !!accounts[0].organization && accounts[0].organization === accounts[1].organization;
}

/** Which accounts did a task's work: each entry (an attempt, a crossing, a hand-off, the task's deletion, its account
 *  confirmed again into another group or as another kind of seat, as it was confirmed before and is now) with its run,
 *  step and accounts as they stood; named by the task's id, repository and branch (and its title until the journal's
 *  prune), saying how long this deployment keeps it. A deleted task's has the owner's Delete, which takes its
 *  confirmation (task-account-record), risk point 15's text opening over it. */
export function AccountRecord({ record, onChanged }: { record: AccountRecordView; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<Change | null>(null);
  const [error, setError] = useState("");
  const name = `${record.title ? `${record.title}, ` : ""}task ${short(record.task_id)}${record.repo_address ? ` · ${record.repo_address}` : ""} · ${record.branch}`;
  const ask = async () => {
    setError("");
    try {
      setAsking({ what: "task-account-record", label: "Deleting this task's account record", action: "Delete it", subject: `task-account-record:${record.task_id}`, ways: await confirmWays() });
    } catch (e) { setError(message(e)); }
  };
  return (
    <div className="mt-1.5 rounded-md border border-line px-2 py-1" role="group" aria-label={`The account record of ${name}`}>
      <button className="touch-target flex w-full items-center gap-1 text-left text-[11.5px]" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">Which accounts did this work</span>
        <span className="min-w-0 truncate text-faint">{name}{record.deleted_at ? ` · deleted ${dayTime(record.deleted_at)}` : ""}</span>
      </button>
      {open && (
        <div className="mt-1">
          <div className="text-[11.5px] text-muted">{keepingWords(record.record_days)}</div>
          {record.entries.length === 0 && <div className="mt-0.5 text-[11.5px] text-faint">No entry yet.</div>}
          <ol className="mt-0.5 space-y-0.5">
            {record.entries.map((e) => (
              <li key={e.seq} className="text-[11.5px]">
                <span className="font-mono text-[11px] text-faint" title={full(e.at)}>{dayTime(e.at)}</span>{" "}
                {ENTRY_WORDS[e.kind]}{e.kind !== "deleted" ? `, step ${e.step}` : ""}{e.run_id ? `, run ${e.run_id}` : ""}{e.via ? `, by ${e.via === "offer" ? "an offer" : "custody"}` : ""}
                {e.accounts.length > 0 && <>: {e.accounts.map((s) => snapshotWords(s, sameOrganisationName(e.accounts))).join(e.kind === "crossing" || e.kind === "regrouped" ? " to " : "; ")}</>}
                {e.statement && <div className="text-[11px] text-muted">A statement: {e.statement.text}</div>}
              </li>
            ))}
          </ol>
          {record.deleted_at != null ? (
            <div className={`mt-1 ${TERMS_IN_FLOW}`}>
              {!asking && <button className="btn btn-sm touch-target !h-6 text-[11px]" onClick={() => void ask()}><Trash2 size={11} /> Delete this record</button>}
              {asking && <RiskTerms point={15} kind="control" open />}
              {asking && (
                <ConfirmIt key={asking.subject} change={asking} onCancel={() => setAsking(null)}
                  onConfirmed={async (confirmation) => { await Api.deleteAccountRecord(record.task_id, confirmation); setAsking(null); await onChanged(); }} />
              )}
            </div>
          ) : (
            <div className="mt-0.5 text-[11px] text-faint">Its Delete is offered once the task is deleted.</div>
          )}
          {error && <div role="alert" className="text-[11.5px] text-fail">{error}</div>}
        </div>
      )}
    </div>
  );
}
