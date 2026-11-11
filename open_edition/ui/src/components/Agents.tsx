import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bot, CalendarDays, ChevronDown, ChevronRight, Download, Loader2, Pause, Pencil, Play, Plus, RefreshCw, Repeat, ShieldCheck, Square, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { Api, type AccountOwnerView, type PacketNote, type SessionRecord, type TaskPacketView, type TaskView } from "../api";
import { canonicalJson } from "../passkey";
import {
  AGENT_WORDS, AgentsApi, ago, compact, duration, LIVE, matches, RUN_WORDS, runSeconds, saveNumbersCsv, sortAgents, startedBy, stateColor, tokenCount,
  type Agent, type AgentFilter, type AgentRun, type AgentSort, type AgentsTree, type AgentState, type RunState,
} from "../agents";
import { useStore } from "../store";
import { api } from "../api";
import { Confirm, usd, useEscape, verdictWords } from "./bits";
import { useFocusInto } from "./Modal";
import { AgentForm, type FormMode } from "./AgentForm";
import { AgentTools } from "./AgentTools";
import { AgentTriggers } from "./AgentTriggers";
import { AgentNumbersPanel } from "./AgentNumbers";
import { AgentFacts } from "./AgentFacts";
import { AgentMemory } from "./AgentMemory";
import { AgentSuites } from "./AgentSuites";
import { ContextLine, RunTree } from "./RunTree";
import { RunChecksLine } from "./RunChecks";
import { AgentImprovements } from "./AgentImprovements";
import { AgentSkills } from "./AgentSkills";
import { accountWords, AgentTask } from "./AgentTask";
import { eventsToOpen } from "./RiskTerms";
import { OffersWaiting } from "./TaskHandOff";
import { deviceZone, full, near } from "../when";

const ChatPane = lazy(() => import("./ChatPane").then((m) => ({ default: m.ChatPane })));


/** When the account an agent runs on is out of room, what Run now will meet, in words:
 *  "The window of <its account> is exhausted until <time>: this run will stop at once". The account is the agent's own,
 *  or, for an agent with none, `startsOn`, the account new sessions start on as the workspace last said it ("default",
 *  or an id), on which such an agent's run starts (proofpane_workspace/agents.py); null where the page has not read
 *  that. Null too while every window it last recorded has room, or none is recorded. Run now still runs when pressed. */
export function exhaustedWords(agent: Pick<Agent, "account">, owners: AccountOwnerView[], startsOn: string | null,
                               now = Date.now() / 1000): string | null {
  const account = agent.account ?? startsOn;
  const owner = account == null ? undefined : owners.find((o) => o.account === account);
  const out = owner?.windows.filter((w) => w.status === "rejected" && (w.resets_at == null || w.resets_at > now)) ?? [];
  if (!owner || !out.length) return null;
  const until = Math.max(...out.map((w) => w.resets_at ?? 0));
  // with its day where the reset is 12 hours or more off: a seven-day window's, days away, is not today's time
  return `The window of ${accountWords(owner)} is exhausted ${until ? `until ${near(until, deviceZone(), new Date(now * 1000))}` : "and its reset is not known"}: this run will stop at once.`;
}

const RESUME_TAG = "proofpane-resume/1";

/** A packet's digest as the app and the workspace take it (task_shapes.tagged_digest): the hex sha256 of
 *  "proofpane-resume/1", a 0 byte and the packet's RFC 8785 bytes, computed here from the packet as the app sent it, so
 *  that the text a run's pane shows is seen to be the text whose digest its attempt carried. Throws for a value RFC 8785
 *  as this page writes it does not take (a fraction, a number past 2^53 - 1, half a character). */
export async function resumeDigest(packet: unknown): Promise<string> {
  const tag = new TextEncoder().encode(RESUME_TAG);
  const text = new TextEncoder().encode(canonicalJson(packet));
  const bytes = new Uint8Array(tag.length + 1 + text.length);
  bytes.set(tag, 0);
  bytes.set(text, tag.length + 1);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function StateDot({ state, size = 8 }: { state: RunState | AgentState; size?: number }) {
  const live = state === "working";
  return (
    <span className={`dot inline-block flex-none rounded-full ${live ? "animate-pulse-dot" : ""}`}
      style={{ background: stateColor(state), width: size, height: size,
               boxShadow: state === "waiting" ? `0 0 0 3px color-mix(in srgb, ${stateColor(state)} 22%, transparent)` : undefined }}
      aria-hidden="true" />
  );
}

/** The agents: groups, each agent with its totals and its last runs, and a run's own pane beside them. Read from the
 *  app's store (never starting a machine), every 10 seconds while a run is live and every 30 otherwise. */
export function Agents({ onClose, keep, onCalendar }: { onClose: () => void; keep?: string | null; onCalendar?: () => void }) {
  const setError = useStore((s) => s.setError);
  const running = useStore((s) => !!s.ws?.running);
  const [tree, setTree] = useState<AgentsTree | null>(null);
  // the owner's accounts as tasks' relays see them, for Run now's word on an account out of room; and the account new
  // sessions start on, the one an agent with no account of its own runs on, as the workspace last said it
  const [owners, setOwners] = useState<AccountOwnerView[]>([]);
  const startsOn = useStore((s) => s.accounts?.claude.active ?? null);
  const user = useStore((s) => s.user?.id ?? "");
  // the moves whose agents this page has unfolded so that their texts open: each unfolds its agent once, and a row the
  // owner folds again stays folded
  const unfolded = useRef(new Set<string>());
  const [filter, setFilter] = useState<AgentFilter>("all");
  const [sort, setSort] = useState<AgentSort>("state");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [run, setRun] = useState<string | null>(null);
  const [form, setForm] = useState<FormMode | null>(keep ? { mode: "keep", session: keep } : null);
  useEscape(() => (form ? setForm(null) : run && window.innerWidth < 768 ? setRun(null) : onClose()));
  const panel = useRef<HTMLElement>(null);
  useFocusInto(panel);

  const load = useCallback(async () => {
    try { setTree(await AgentsApi.tree()); } catch (e) { setError(e); }
    try {
      const got = await Api.taskAccounts();
      if (Array.isArray(got?.accounts)) setOwners(got.accounts);
    } catch { /* no word on Run now without them */ }
    // what the product did by itself opens its text at the owner's visit: an
    // agent whose task holds a move this browser has not opened is unfolded, so that its task's section shows the line
    // and the text opens there, with no press of the owner's
    try {
      const got = await Api.tasks();
      const due = new Set<string>();
      for (const t of Array.isArray(got?.tasks) ? got.tasks : []) {
        const events = (t.moves ?? []).filter((m) => m.point != null).map((m) => ({ event: `task:${t.id}:${m.seq}`, at: m.at }));
        const fresh = eventsToOpen(user, events).filter((e) => !unfolded.current.has(e));
        fresh.forEach((e) => unfolded.current.add(e));
        if (fresh.length) due.add(t.agent_id);
      }
      if (due.size) setOpen((s) => new Set([...s, ...due]));
    } catch { /* its rows unfold by hand */ }
  }, [setError, user]);
  useEffect(() => {
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && void load(), tree && tree.live > 0 ? 10000 : 30000);
    return () => clearInterval(t);
  }, [load, tree?.live]);    // eslint-disable-line react-hooks/exhaustive-deps

  const agents = useMemo(() => (tree?.groups ?? []).flatMap((g) => g.agents), [tree]);
  const byRun = useMemo(() => {
    const m = new Map<string, { run: AgentRun; agent: Agent }>();
    for (const a of agents) for (const r of a.runs) m.set(r.run_id, { run: r, agent: a });
    return m;
  }, [agents]);
  const names = useMemo(() => new Map(agents.map((a) => [a.id, a.name])), [agents]);
  const chosen = run ? byRun.get(run) : undefined;
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); await load(); } catch (e) { setError(e); } };
  const total = agents.filter((a) => !a.retired).length;
  const waiting = tree?.waiting.length ?? 0;

  return (
    <div className="fixed inset-0 z-40 bg-black/40" onMouseDown={onClose}>
      <aside ref={panel} tabIndex={-1} aria-label="Agents"
        className="absolute inset-1 flex flex-col overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60 outline-none sm:inset-4"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
          <Bot size={16} className="text-accent" />
          <span className="text-[13.5px] font-semibold">Agents</span>
          <span className="text-[12px] text-muted">{total} {total === 1 ? "agent" : "agents"}{waiting ? ` · ${waiting} waiting for you` : ""}</span>
          <span className="ml-auto flex items-center gap-1">
            <button className="btn btn-sm btn-primary" onClick={() => setForm({ mode: "new" })}><Plus size={13} /> New agent</button>
            {onCalendar && (
              <button className="icon-btn" onClick={onCalendar} title="The calendar: your agents' schedules, their coming times and what came of their firings"
                aria-label="Calendar"><CalendarDays size={14} /></button>
            )}
            <button className="icon-btn" onClick={() => void saveNumbersCsv(30).catch(setError)} title="Every agent's numbers by day, the last 30 days, as CSV"
              aria-label="Save the numbers as CSV"><Download size={14} /></button>
            <button className="icon-btn" onClick={() => void load()} title="Read again" aria-label="Read again"><RefreshCw size={14} /></button>
            <button className="icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close"><X size={15} /></button>
          </span>
        </div>
        <div className="flex min-h-0 flex-1">
          <section className={`min-h-0 flex-col overflow-y-auto border-line md:flex md:w-[56%] md:border-r ${chosen ? "hidden" : "flex w-full"}`} aria-label="The agents">
            <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2 text-[12px]">
              {(["all", "waiting", "failed"] as AgentFilter[]).map((f) => (
                <button key={f} className={`btn btn-sm ${filter === f ? "btn-primary" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {f === "all" ? "All" : f === "waiting" ? "Waiting for you" : "Failed"}
                </button>
              ))}
              <label className="ml-auto flex items-center gap-1 text-muted">Sort
                <select className="input !h-7 !py-0 text-[12px]" value={sort} onChange={(e) => setSort(e.target.value as AgentSort)}>
                  <option value="state">by state</option>
                  <option value="last">by last run</option>
                  <option value="cost">by today's est. cost</option>
                </select>
              </label>
            </div>
            {tree && <OffersWaiting agents={agents} onChanged={() => void load()} />}
            {tree && agents.some((x) => x.improve) && <AgentImprovements names={names} onChanged={load} />}
            {tree === null && <div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>}
            {tree && agents.length === 0 && (
              <div className="mx-auto max-w-[420px] px-4 py-12 text-center text-[12.5px] text-muted">
                An agent is a chat kept under a name: its brief is its standing instruction, and each run is a new chat
                in its own worktree, which remembers across runs. Keep one from a chat (Keep as an agent, beside
                Finish) or make one here.
              </div>
            )}
            {tree?.groups.map((g) => {
              const shown = sortAgents(g.agents.filter((a) => matches(a, filter)), sort);
              if (!shown.length) return null;
              return (
                <div key={g.name} className="border-b border-line last:border-b-0">
                  <div className="sticky top-0 z-[1] bg-panel/95 px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-faint">{g.name}</div>
                  <ul>
                    {shown.map((a) => (
                      <AgentRow key={a.id} a={a} names={names} open={open.has(a.id)} chosen={run} onToggle={() => toggle(a.id)}
                        exhausted={exhaustedWords(a, owners, startsOn)}
                        onRun={(prompt) => act(() => AgentsApi.run(a.id, prompt))}
                        onPause={() => act(() => (a.paused ? AgentsApi.resume(a.id) : AgentsApi.pause(a.id)))}
                        onEdit={() => setForm({ mode: "edit", agent: a })}
                        onRetire={() => act(() => AgentsApi.retire(a.id))}
                        onOpenRun={(id) => { setRun(id); setOpen((s) => new Set(s).add(a.id)); }} onChanged={load} />
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>
          <section className={`min-h-0 flex-1 flex-col ${chosen ? "flex" : "hidden md:flex"}`} aria-label="A run">
            {chosen ? (
              <RunPane key={chosen.run.run_id} run={chosen.run} agent={chosen.agent} running={running} onBack={() => setRun(null)}
                onChanged={load} names={names} onOpenRun={(id) => (byRun.has(id) ? setRun(id) : null)} known={(id) => byRun.has(id)}
                onOpenTask={() => {
                  // the task is in its agent's row, which a phone's run pane covers: open the row, then show it
                  setOpen((s) => new Set(s).add(chosen.agent.id));
                  if (window.innerWidth < 768) setRun(null);
                  setTimeout(() => document.getElementById(`agent-task-${chosen.agent.id}`)?.scrollIntoView?.({ block: "nearest" }), 0);
                }} />
            ) : (
              <div className="m-auto max-w-[360px] px-6 text-center text-[12.5px] text-muted">
                Open a run to read its chat, answer it, or verify what it changed.
              </div>
            )}
          </section>
        </div>
      </aside>
      {form && <AgentForm mode={form} onClose={() => setForm(null)} onSaved={(a) => { setForm(null); setOpen((s) => new Set(s).add(a.id)); void load(); }} />}
    </div>
  );
}

function AgentRow({ a, names, open, chosen, exhausted, onToggle, onRun, onPause, onEdit, onRetire, onOpenRun, onChanged }: {
  a: Agent; names: Map<string, string>; open: boolean; chosen: string | null; exhausted: string | null; onToggle: () => void;
  onRun: (prompt?: string) => Promise<void>; onPause: () => Promise<void>; onEdit: () => void; onRetire: () => Promise<void>;
  onOpenRun: (id: string) => void; onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [prompt, setPrompt] = useState("");
  const last = a.runs[0];
  const t = a.totals;
  const go = async (fn: () => Promise<void>) => { setBusy(true); try { await fn(); } finally { setBusy(false); } };
  return (
    <li className="border-t border-line first:border-t-0">
      <div className="flex items-start gap-2 px-3 py-2 hover:bg-hover">
        <button className="icon-btn mt-0.5 !h-6 !w-6 flex-none" onClick={onToggle} aria-expanded={open} aria-label={`${open ? "Fold" : "Unfold"} ${a.name}'s runs`}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <StateDot state={a.state} />
            <span className="truncate text-[13px] font-medium">{a.name}</span>
            <span className="text-[11.5px]" style={{ color: stateColor(a.state) }}>{AGENT_WORDS[a.state]}</span>
          </div>
          {a.paused && a.paused_reason && (a.paused_reason === "paused by you" ? null : (
            // held by the workspace at a run's end: what changed, for the owner to look at before resuming it
            <div className="mt-1 rounded-md border border-wait/50 bg-wait/[0.06] px-2 py-1 text-[11.5px]" role="note"
              aria-label={`Why ${a.name} is held`}>
              <span className="text-wait">Held: </span>{a.paused_reason}
            </div>
          ))}
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11.5px] text-muted">
            <span>{a.triggers.filter((t) => t.enabled).map((t) => (t.kind === "schedule" ? t.cron : `${t.hook_type} hook`)).join(", ") || "by hand"}</span>
            {last ? (
              <span title={full(last.queued_at)}>last run {ago(last.started_at ?? last.queued_at)}, {RUN_WORDS[last.state].toLowerCase()}
                {runSeconds(last) != null ? `, ${duration(runSeconds(last))}` : ""}</span>
            ) : <span>no run yet</span>}
            {a.improve && a.review_skipped && (
              // folded too: the improve loop passed it over (why, unfolded, beside when it was last reviewed)
              <span className="text-wait" title={a.review_skipped.reason}>passed over by the improve loop {ago(a.review_skipped.at)}</span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[11px] text-faint">
            <span title="Runs, of which done">{t.runs} {t.runs === 1 ? "run" : "runs"}</span>
            <span title="Time, start to end, of its runs">{duration(t.seconds) || "0s"}</span>
            <span title="Tokens, each request counted once">{compact(t.tokens)} tok</span>
            <span title="What Claude Code estimates its runs cost at API prices">{usd(t.cost_est_usd)} est.</span>
            {t.success_rate != null && <span title="Done over done and failed: a blocked or stopped run is not counted">{Math.round(t.success_rate * 100)}% ok</span>}
            {t.runs_today > 0 && <span title="Today, in UTC">today {t.runs_today}, {usd(t.est_usd_today)}</span>}
          </div>
          {a.freshness && (a.freshness.items > 0 || a.freshness.archived.length > 0) && (
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-faint" aria-label={`${a.name}'s knowledge`}>
              {a.freshness.current_share != null && <span title="Current knowledge files over the tiered ones">{Math.round(a.freshness.current_share * 100)}% current</span>}
              <span className={a.freshness.stale_share != null && a.freshness.stale_share > 0.2 ? "text-wait" : ""}
                title="Knowledge files past their check, and told facts older than 30 days">{a.freshness.stale_count} stale</span>
              {a.freshness.contradictions > 0 && <span className="text-wait">{a.freshness.contradictions} contradicting</span>}
              {a.freshness.archived.length > 0 && <span title="Memory files the sweep archived">{a.freshness.archived.length} archived</span>}
            </div>
          )}
          {a.numbers && (
            <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[11px] text-faint" aria-label={`${a.name}'s last 7 days`}>
              <span title="A run's time, p50 and p95 over 7 days">{duration(Math.round(a.numbers.wall_p50_s ?? 0)) || "0s"} / {duration(Math.round(a.numbers.wall_p95_s ?? 0)) || "0s"}</span>
              <span>{a.numbers.calls} calls</span>
              {a.numbers.errors > 0 && <span className="text-fail">{a.numbers.errors} errors</span>}
              {a.numbers.refused > 0 && <span className="text-wait">{a.numbers.refused} refused</span>}
              {a.numbers.cache_ratio != null && <span title="Cache reads over all input tokens">cache {Math.round(a.numbers.cache_ratio * 100)}%</span>}
            </div>
          )}
        </div>
        {!a.retired && (
          <div className="flex flex-none items-center gap-1">
            <button className="btn btn-sm" disabled={busy || a.paused} onClick={() => (asking ? void go(() => onRun(prompt).then(() => { setPrompt(""); setAsking(false); })) : void go(() => onRun()))}
              title={a.paused ? "Paused: resume it first" : exhausted ?? "Run it now: its brief, in its own worktree"} aria-label={`Run ${a.name} now`}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}<span className="btn-label"> Run</span>
            </button>
            <button className="icon-btn" title={asking ? "Run without a prompt" : "Run with a prompt of its own, after the brief"} aria-label={`Run ${a.name} with a prompt`}
              aria-pressed={asking} onClick={() => setAsking((x) => !x)}><Plus size={13} /></button>
            <button className="icon-btn" disabled={busy} onClick={() => void go(onPause)} title={a.paused ? "Resume: runs start again" : "Pause: no new run starts; one working goes on"}
              aria-label={`${a.paused ? "Resume" : "Pause"} ${a.name}`}>
              {a.paused ? <Play size={13} /> : <Pause size={13} />}
            </button>
            <button className="icon-btn" onClick={onEdit} title="Edit its brief and settings" aria-label={`Edit ${a.name}`}><Pencil size={13} /></button>
          </div>
        )}
      </div>
      {exhausted && !a.retired && (
        <div className="px-3 pb-1.5 pl-11 text-[11.5px] text-wait" role="note" aria-label={`Run now on ${a.name}`}>{exhausted} Run still runs it when you press it.</div>
      )}
      {asking && (
        <form className="flex gap-2 px-3 pb-2 pl-11" onSubmit={(e) => { e.preventDefault(); void go(() => onRun(prompt).then(() => { setPrompt(""); setAsking(false); })); }}>
          <input aria-label={`A prompt for ${a.name}'s run`} className="input flex-1 !h-8 text-[12.5px]" placeholder="What this run should do, after its brief" value={prompt}
            onChange={(e) => setPrompt(e.target.value)} maxLength={4000} autoFocus />
          <button className="btn btn-sm btn-primary" disabled={busy || !prompt.trim()}>Run</button>
        </form>
      )}
      {open && (
        <div className="pb-2 pl-11 pr-3">
          {a.runs.length === 0 && <div className="py-1 text-[12px] text-faint">No run yet.</div>}
          <ul className="space-y-1">
            {a.runs.map((r) => (
              <li key={r.run_id}>
                <button className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-hover ${chosen === r.run_id ? "bg-active" : ""}`}
                  onClick={() => onOpenRun(r.run_id)} aria-label={`Open the run of ${ago(r.queued_at)}, ${RUN_WORDS[r.state]}`}>
                  <StateDot state={r.state} size={7} />
                  <span className="w-[86px] flex-none" style={{ color: stateColor(r.state) }}>{RUN_WORDS[r.state]}</span>
                  {startedBy(r, names) && <span className="flex-none text-[11px] text-accent" title={`Started by ${startedBy(r, names)}`}>from {startedBy(r, names)}</span>}
                  {r.replay_of && <span className="flex-none text-[11px] text-faint" title="Started again as an earlier run was, to measure it">replay</span>}
                  {r.flag && <span className="flex-none text-[11px] text-fail" title={`Started outside its bounds: ${r.flag}`}>outside its bounds</span>}
                  {r.rewritten && <span className="flex-none text-[11px] text-fail" title={r.rewritten}>record rewritten</span>}
                  {((r.judge && !r.judge.pass) || (r.acceptance && !r.acceptance.pass)) && (
                    <span className="flex-none text-[11px] text-fail" title={r.acceptance && !r.acceptance.pass ? "Its acceptance checks failed" : "The judge failed it"}>check failed</span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-muted">{r.question || r.reason || r.last_text || r.trigger.prompt || "by hand"}</span>
                  {r.subtree && <span className="flex-none font-mono text-[11px] text-faint" title="The runs it started, theirs too">+{r.subtree.runs} {r.subtree.runs === 1 ? "run" : "runs"}</span>}
                  <span className="flex-none font-mono text-[11px] text-faint">{duration(runSeconds(r))}</span>
                  <span className="hidden flex-none font-mono text-[11px] text-faint sm:inline">{r.cost_est_usd != null ? usd(r.cost_est_usd) : ""}</span>
                  {r.thumbs && (r.thumbs === "up" ? <ThumbsUp size={11} className="text-done" /> : <ThumbsDown size={11} className="text-fail" />)}
                </button>
              </li>
            ))}
          </ul>
          <AgentTask agent={a} onOpenRun={onOpenRun} />
          <AgentTriggers agent={a} onChanged={onChanged} />
          <AgentTools agent={a} onChanged={onChanged} />
          <AgentSkills agent={a} onChanged={onChanged} />
          <AgentNumbersPanel agent={a} />
          <AgentSuites agent={a} />
          <AgentFacts agent={a} onChanged={onChanged} />
          <AgentMemory agent={a} />
          {a.improve && (
            // the improve loop on it: when it last reviewed it, and, when it last passed it over, why (the app's
            // reviewed_at and review_skipped, improve.py)
            <div className="mt-1.5 text-[11.5px] text-muted" role="note" aria-label={`The improve loop on ${a.name}`}>
              {a.reviewed_at ? <span title={full(a.reviewed_at)}>Last reviewed by the improve loop {ago(a.reviewed_at)}</span>
                : <span>Not yet reviewed by the improve loop</span>}
              {a.review_skipped && (
                <span className="text-wait" title={full(a.review_skipped.at)}> · passed over {ago(a.review_skipped.at)}: {a.review_skipped.reason}</span>
              )}
            </div>
          )}
          <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-faint">
            <span className="min-w-0 flex-1 truncate" title={a.brief}>Brief v{a.brief_version}: {a.brief.split("\n")[0]}</span>
            <Confirm label="Retire" again="Retire it? Its records stay" onConfirm={onRetire} className="!h-6 text-[11px]" />
          </div>
        </div>
      )}
    </li>
  );
}

/** A run: its numbers, the owner's hands on it (stop, verify, thumbs), and its chat, read from the workspace while it
 *  runs (the conversation lives there). */
function RunPane({ run, agent, running, onBack, onChanged, names, onOpenRun, known, onOpenTask }: {
  run: AgentRun; agent: Agent; running: boolean; onBack: () => void; onChanged: () => Promise<void>;
  names: Map<string, string>; onOpenRun: (id: string) => void; known: (id: string) => boolean; onOpenTask: () => void;
}) {
  const setError = useStore((s) => s.setError);
  const startWorkspace = useStore((s) => s.startWorkspace);
  const [record, setRecord] = useState<SessionRecord | null>(null);
  // a run that is one of a task's attempts says so, with a link to the task
  const [task, setTask] = useState<{ task: TaskView; step: number; attempt: number; digest: string | null } | null>(null);
  useEffect(() => {
    let gone = false;
    Api.tasks(agent.id).then((got) => {
      for (const t of Array.isArray(got?.tasks) ? got.tasks : []) {
        const a = (t.attempts ?? []).find((x) => x.run_id === run.run_id);
        if (a && !gone) { setTask({ task: t, step: a.step, attempt: a.attempt, digest: a.packet_digest }); return; }
      }
    }).catch(() => { /* not a task's attempt that the app can say: no line */ });
    return () => { gone = true; };
  }, [agent.id, run.run_id]);
  const [verdict, setVerdict] = useState<{ text: string; tone: "done" | "wait" | "fail" } | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState(run.thumbs_note ?? "");
  const [view, setView] = useState<"chat" | "tree" | "packet">("chat");
  const live = LIVE.includes(run.state);
  // a task's attempt has its packet from the moment it is queued: its tab is offered then too, the chat and the
  // tree once the run is sent
  const tabs = [...(run.state !== "queued" ? (["chat", "tree"] as const) : []), ...(task ? (["packet"] as const) : [])];

  useEffect(() => {
    if (!running || run.pruned) { setRecord(null); return; }
    let gone = false;
    const read = async () => {
      try {
        const r = await api<SessionRecord>(`/api/ade/workspace/sessions/${run.run_id}`);
        if (!gone) setRecord(r);
      } catch { /* the run not sent yet, or its machine stopping: shown from the store */ }
    };
    void read();
    const t = setInterval(read, live ? 3000 : 15000);
    return () => { gone = true; clearInterval(t); };
  }, [run.run_id, running, live, run.pruned]);

  const go = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    try { await fn(); } catch (e) { setError(e); } finally { setBusy(""); }
  };
  const verify = () => go("verify", async () => {
    const r = await Api.finish(run.run_id) as Record<string, unknown> & { status?: string; reason?: string | null; files?: number };
    if (r.status === "skipped" || r.status === "error") setVerdict({ text: `Not verified: ${r.reason || r.status}.`, tone: r.status === "error" ? "fail" : "wait" });
    else {
      const said = verdictWords(r as never);
      setVerdict({ text: `${r.files ?? 0} ${r.files === 1 ? "file" : "files"} changed in this run. ${said.text}.`, tone: said.tone });
    }
  });
  const thumbs = (value: "up" | "down" | null) => go("thumbs", async () => { await AgentsApi.thumbs(run.run_id, value, note); await onChanged(); });
  const tok = tokenCount(run.tokens);
  const by = startedBy(run, names);
  const tree = run.subtree;
  const link = (id: string, words: string) => (known(id)
    ? <button className="underline decoration-dotted underline-offset-2 hover:text-fg" onClick={() => onOpenRun(id)}>{words}</button>
    : <span>{words}</span>);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-none border-b border-line px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-sm md:hidden" onClick={onBack}>Agents</button>
          <StateDot state={run.state} />
          <span className="text-[13px] font-medium">{agent.name}</span>
          <span className="text-[12px]" style={{ color: stateColor(run.state) }}>{RUN_WORDS[run.state]}</span>
          {run.reason && <span className="text-[12px] text-muted">{run.reason}</span>}
          <span className="ml-auto flex items-center gap-1">
            {(live || run.state === "queued") && (
              <button className="btn btn-sm" disabled={!!busy} onClick={() => void go("stop", async () => { await AgentsApi.stop(run.run_id); await onChanged(); })}>
                {busy === "stop" ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />} Stop
              </button>
            )}
            {!live && run.state !== "queued" && run.head && running && (
              <button className="btn btn-sm" disabled={!!busy} onClick={() => void verify()} title="Have this deployment verify and sign what this run changed: its base to its head">
                {busy === "verify" ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />} Verify
              </button>
            )}
            {!live && run.state !== "queued" && run.metrics?.replayable && !agent.retired && (
              <button className="btn btn-sm" disabled={!!busy || run.brief_version !== agent.brief_version}
                onClick={() => void go("replay", async () => { await AgentsApi.replay(run.run_id); await onChanged(); })}
                title={run.brief_version !== agent.brief_version
                  ? "Its brief changed since this run: a replay would measure the new brief"
                  : "Start it again as it was, to see whether it does the same: it stayed inside its worktree, so a replay is safe"}>
                {busy === "replay" ? <Loader2 size={12} className="animate-spin" /> : <Repeat size={12} />} Replay
              </button>
            )}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-[11px] text-faint">
          {run.started_at && <span title={full(run.started_at)}>started {ago(run.started_at)}</span>}
          {runSeconds(run) != null && <span>{duration(runSeconds(run))}</span>}
          <span>{run.steps} {run.steps === 1 ? "step" : "steps"}</span>
          {tok > 0 && <span title={`in ${run.tokens?.input_tokens ?? 0}, out ${run.tokens?.output_tokens ?? 0}, cache read ${run.tokens?.cache_read_input_tokens ?? 0}, cache written ${run.tokens?.cache_creation_input_tokens ?? 0}${run.requests != null ? `, ${run.requests} requests` : ""}`}>{compact(tok)} tok</span>}
          {run.cost_est_usd != null && <span>{usd(run.cost_est_usd)} est.</span>}
          {run.brief_version != null && <span>brief v{run.brief_version}</span>}
        </div>
        {(by || run.waiting_on || tree || run.replay_of) && (
          <div className="mt-1 flex flex-wrap gap-x-3 text-[11.5px] text-muted" aria-label="Its runs together">
            {run.replay_of && <span>A replay of {link(run.replay_of, "an earlier run")}</span>}
            {by && run.parent_run_id && <span>Started by {link(run.parent_run_id, by)}{run.depth ? `, ${run.depth} deep` : ""}</span>}
            {run.waiting_on && <span className="text-wait">Waiting on {link(run.waiting_on, "a run it started")}</span>}
            {tree && (
              <span title="The runs it started, theirs too">
                It started {tree.runs} {tree.runs === 1 ? "run" : "runs"}{tree.open ? `, ${tree.open} open` : ""}{tree.failed ? `, ${tree.failed} failed` : ""}:
                {" "}{duration(tree.seconds) || "0s"}, {usd(tree.cost_est_usd)} est.
              </span>
            )}
          </div>
        )}
        {task && (
          <div className="mt-1 text-[12px] text-muted" aria-label="Its task">
            <button className="underline decoration-dotted underline-offset-2 hover:text-fg" onClick={onOpenTask}>Task: {task.task.title}, step {task.step}, attempt {task.attempt}</button>
            {task.digest && <span className="ml-2 font-mono text-[11px] text-faint" title="The digest of the packet this attempt was given">packet {task.digest.slice(0, 12)}</span>}
            <div className="text-[11.5px] text-faint">Text an earlier attempt wrote is given to the next as information. Marking it does not stop a model from following it.</div>
          </div>
        )}
        {run.flag && (
          <div className="mt-1 text-[12px] text-fail" role="alert">Started outside its bounds: it was {run.flag}. The workspace started it, so it is shown and counted.</div>
        )}
        {run.rewritten && <div className="mt-1 text-[12px] text-fail" role="alert">{run.rewritten}</div>}
        {run.context && <ContextLine context={run.context} />}
        <RunChecksLine run={run} />
        {tabs.length > 0 && (
          <div className="mt-2 flex gap-1" role="tablist" aria-label="What of the run">
            {tabs.map((v) => (
              <button key={v} role="tab" aria-selected={view === v} className={`btn btn-sm !h-6 text-[11px] ${view === v ? "btn-primary" : ""}`}
                onClick={() => setView(v)}>{v === "chat" ? "Chat" : v === "tree" ? "Tree" : "Packet"}</button>
            ))}
          </div>
        )}
        {run.question && run.state === "waiting" && (
          <div className="mt-2 rounded-md border border-wait/50 bg-wait/[0.06] px-3 py-2 text-[12.5px]">
            <div className="mb-0.5 text-[11px] text-wait">It asks you</div>
            <div className="whitespace-pre-wrap">{run.question}</div>
            <div className="mt-1 text-[11px] text-muted">Answer in its chat below: your message is its next one.</div>
          </div>
        )}
        {verdict && <div className={`mt-2 text-[12.5px] ${verdict.tone === "done" ? "text-done" : verdict.tone === "fail" ? "text-fail" : "text-wait"}`}>{verdict.text}</div>}
        {!live && run.state !== "queued" && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px]">
            <span className="text-muted">Your word on it:</span>
            <button className={`icon-btn ${run.thumbs === "up" ? "!text-done" : ""}`} aria-pressed={run.thumbs === "up"} aria-label="Good run" title="Good run"
              onClick={() => void thumbs(run.thumbs === "up" ? null : "up")}><ThumbsUp size={13} /></button>
            <button className={`icon-btn ${run.thumbs === "down" ? "!text-fail" : ""}`} aria-pressed={run.thumbs === "down"} aria-label="Bad run" title="Bad run"
              onClick={() => void thumbs(run.thumbs === "down" ? null : "down")}><ThumbsDown size={13} /></button>
            <input aria-label="A note on the run" className="input !h-7 min-w-[140px] flex-1 text-[12px]" placeholder="Why, for the run's record" maxLength={500}
              value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => run.thumbs && note !== (run.thumbs_note ?? "") && void thumbs(run.thumbs)} />
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1">
        {view === "packet" && task ? (
          <TaskPacket taskId={task.task.id} runId={run.run_id} recorded={task.digest} />
        ) : view === "tree" ? (
          <RunTree runId={run.run_id} />
        ) : run.pruned ? (
          <div className="m-auto max-w-[380px] px-6 py-10 text-center text-[12.5px] text-muted">
            This run's folder and transcript were removed for its agent's storage cap; its record and numbers stay here.
          </div>
        ) : !running ? (
          <div className="m-auto flex max-w-[380px] flex-col items-center gap-3 px-6 py-10 text-center text-[12.5px] text-muted">
            The run's conversation is in the workspace, which is stopped.
            <button className="btn btn-sm" onClick={() => void startWorkspace()}>Start the workspace to read it</button>
          </div>
        ) : record ? (
          <Suspense fallback={<div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>}>
            <ChatPane record={record} run={{ live }} />
          </Suspense>
        ) : (
          <div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>
        )}
      </div>
    </div>
  );
}

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

/** The packet a task's attempt read, read from the app (`GET /tasks/{id}/packets/{run_id}`): each part under
 *  who wrote it, as the packet holds it; an earlier attempt's words each under its attempt; and the whole as the one
 *  line of text the attempt was served. Its digest is computed here and compared with the one the app recorded for the
 *  attempt: the workspace serves an attempt only a packet with that digest (task_attempt.py), so a match says the text
 *  shown is what the attempt could read. The workspace's own `served_digest` is not kept by the app, so it is not read
 *  here. */
function TaskPacket({ taskId, runId, recorded }: { taskId: string; runId: string; recorded: string | null }) {
  const [got, setGot] = useState<{ view: TaskPacketView; computed: string | null; line: string | null } | null>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {
    let gone = false;
    setGot(null);
    setSaid("");
    Api.taskPacket(taskId, runId).then(async (view) => {
      let computed: string | null = null;
      let line: string | null = null;
      try {
        line = canonicalJson(view.packet);
        computed = await resumeDigest(view.packet);
      } catch { /* a value this page cannot write in RFC 8785: said below, the parts still shown */ }
      if (!gone) setGot({ view, computed, line });
    }).catch((e: unknown) => { if (!gone) setSaid(e instanceof Error ? e.message : String(e)); });
    return () => { gone = true; };
  }, [taskId, runId]);
  if (said) return <div className="px-4 py-3 text-[12.5px] text-muted" role="status">This attempt's packet could not be read: {said}</div>;
  if (!got) return <div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>;
  const { view, computed, line } = got;
  const p = view.packet;
  // what is under information but the model's words is what the workspace reported and what the app set as it queued
  // the attempt (its run, number and fence; the checkpoint and the head the worktree must hold; why it starts past its
  // checkpoint), one part, named for both, as the packet's header names the whole of information as data
  const { open_questions: questions = [], notes = [], dropped_notes: dropped = 0, ...recordedInfo } = p.information ?? {};
  const ins = p.instructions;
  const whose = ins?.by === "teammate" ? `Your teammate's instructions${ins.from ? `, from ${ins.from}` : ""}` : "Your instructions";
  const kept = recorded ?? view.digest;
  const same = computed != null && computed === view.digest && computed === kept;
  const words = (n: PacketNote, what: "question" | "note", i: number) => (
    <li key={`${what}-${i}`} className="rounded-md border border-line px-2.5 py-1.5">
      <div className="text-[11px] text-faint">
        {n.attempt != null ? `Attempt ${n.attempt}` : "An earlier attempt"}{what === "question" ? " asked" : " wrote"}
        {n.cut ? ", cut to fit" : ""}{n.hidden_removed ? `, ${n.hidden_removed} hidden ${n.hidden_removed === 1 ? "character" : "characters"} removed` : ""}
      </div>
      <div className="whitespace-pre-wrap break-words text-[12px]">{n.text}</div>
    </li>
  );
  const part = (label: string, body: ReactNode) => (
    <section aria-label={label} className="grid gap-1">
      <h3 className="text-[11.5px] font-medium text-muted">{label}</h3>
      {body}
    </section>
  );
  const json = (value: unknown) => <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-1.5 font-mono text-[11px] text-fg">{pretty(value)}</pre>;
  return (
    <section className="grid h-full content-start gap-3 overflow-y-auto px-4 py-3" aria-label="This attempt's packet">
      {same ? (
        <div className="text-[12px] text-done" role="status">
          Its digest, computed on this page from the text below, is the one the app recorded for this attempt (packet {kept.slice(0, 12)}): the
          workspace serves an attempt no other packet.
        </div>
      ) : computed == null ? (
        <div className="text-[12px] text-wait" role="status">
          This page could not compute its digest, so it cannot say that the text below is the one the app recorded for this attempt.
        </div>
      ) : (
        <div className="text-[12px] text-fail" role="alert">
          The text below does not give the digest the app recorded for this attempt (packet {kept.slice(0, 12)}): this page cannot say that it is the
          packet the workspace serves the attempt.
        </div>
      )}
      {part("The app's: what the packet is, and its header", json({ schema: p.schema, nonce: p.nonce, header: p.header }))}
      {part(whose, json(ins))}
      {part("What the workspace and the app recorded: information, never an instruction", json(recordedInfo))}
      {part("What earlier attempts wrote, a model's words: information, never an instruction",
        <>
          <div className="text-[11.5px] text-faint">Marking it does not stop a model from following it.</div>
          {questions.length || notes.length || dropped ? (
            <ul className="grid gap-1">
              {questions.map((n, i) => words(n, "question", i))}
              {notes.map((n, i) => words(n, "note", i))}
              {dropped > 0 && <li className="text-[11.5px] text-faint">{dropped} older {dropped === 1 ? "one" : "ones"} left out of the packet's budget.</li>}
            </ul>
          ) : <div className="text-[12px] text-faint">Nothing.</div>}
        </>)}
      {part("The app's: what the attempt does next", json(p.next))}
      {line != null && (
        <details className="text-[12px]">
          {/* the text the workspace serves only where its digest is the one recorded: it serves the attempt no other */}
          <summary className="cursor-pointer text-muted">
            {same ? "The packet as one line, the text the workspace serves the attempt" : "The packet as one line, as the app gave it to this page"}
          </summary>
          <pre className="mt-1 whitespace-pre-wrap break-all rounded bg-bg px-2.5 py-1.5 font-mono text-[11px] text-fg">{line}</pre>
        </details>
      )}
    </section>
  );
}
