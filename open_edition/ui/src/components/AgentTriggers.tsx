import { useState } from "react";
import { Clock, Copy, Loader2, Trash2, Webhook } from "lucide-react";
import { ago, AgentsApi, localZone, type Agent, type Trigger } from "../agents";
import { useStore } from "../store";
import { dayTime } from "../when";
import { RiskTerms, TERMS_IN_FLOW } from "./RiskTerms";

const when = (unix: number | null | undefined) => dayTime(unix);

/** A schedule's adherence, as counted: its firings on time, late, missed and skipped,
 *  the ones that happened; none before it fired. */
export function adherence(t: Trigger): string {
  const parts = ([["on time", t.on_time], ["late", t.late], ["missed", t.missed], ["skipped", t.skipped]] as const)
    .filter(([, n]) => (n ?? 0) > 0).map(([w, n]) => `${n} ${w}`);
  return parts.join(", ");
}

/** What starts an agent's runs beside Run now: its schedules (kept and fired by the app, the machine started for each)
 *  and its hooks (an address a service posts to, signed). A hook's secret is shown once, when it is made. A schedule or
 *  a hook made or turned on is risk point 7, work while nobody watches: its text opens over the form as it opens, and
 *  over a trigger's switch as it is turned on, every time, and neither waits on it. */
export function AgentTriggers({ agent, onChanged }: { agent: Agent; onChanged: () => Promise<void> }) {
  const setError = useStore((s) => s.setError);
  const [adding, setAdding] = useState<"schedule" | "hook" | null>(null);
  const [made, setMade] = useState<Trigger | null>(null);
  const [busy, setBusy] = useState(false);
  // how many times each trigger was turned on here: each time opens risk point 7's text again
  const [turnedOn, setTurnedOn] = useState<Record<string, number>>({});
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await onChanged(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  // turned on, its text opens under the switch as it is, every time; turned off, it opens nothing
  const turn = (t: Trigger, on: boolean) => {
    if (on) setTurnedOn((was) => ({ ...was, [t.id]: (was[t.id] ?? 0) + 1 }));
    void go(() => AgentsApi.changeTrigger(agent.id, t.id, { enabled: on }));
  };
  // about to be turned on (before a schedule or hook is made or turned on): its text opens as
  // the pointer comes to the switch or it takes the keyboard, before the press, holding nothing
  const soon = (t: Trigger) => () => { if (!t.enabled) setTurnedOn((was) => ({ ...was, [t.id]: (was[t.id] ?? 0) + 1 })); };
  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s triggers`}>
      <div className="flex items-center gap-2">
        <Clock size={12} className="text-muted" />
        <span className="font-medium">Triggers</span>
        <span className="text-[11.5px] text-faint">runs it starts by itself, as "{agent.unattended_mode}"</span>
        {!agent.retired && (
          <span className="ml-auto flex gap-1">
            <button className="btn btn-sm !h-6 text-[11px]" aria-pressed={adding === "schedule"} onClick={() => setAdding(adding === "schedule" ? null : "schedule")}>Schedule</button>
            <button className="btn btn-sm !h-6 text-[11px]" aria-pressed={adding === "hook"} onClick={() => setAdding(adding === "hook" ? null : "hook")}>Hook</button>
          </span>
        )}
      </div>
      {agent.triggers.length === 0 && !adding && <div className="mt-1 text-[11.5px] text-faint">None: it runs when you tap Run.</div>}
      <ul className="mt-1 space-y-1">
        {agent.triggers.map((t) => (
          <li key={t.id} className={`rounded-md border border-line px-2 py-1 ${TERMS_IN_FLOW}`}>
            <div className="flex flex-wrap items-center gap-x-2">
              {t.kind === "schedule" ? <Clock size={11} className="text-muted" /> : <Webhook size={11} className="text-muted" />}
              {t.kind === "schedule" ? (
                <span><span className="font-mono">{t.cron}</span> <span className="text-faint">{t.tz}</span></span>
              ) : (
                <span className="min-w-0 flex-1 truncate font-mono text-[11px]" title={t.url}>{t.hook_type} {t.url}</span>
              )}
              {t.kind === "schedule" && t.enabled && t.next_at && <span className="text-[11px] text-muted">next {when(t.next_at)}</span>}
              <span className="ml-auto flex items-center gap-1">
                <label className="flex items-center gap-1 text-[11px] text-muted" onMouseEnter={soon(t)}>
                  <input type="checkbox" checked={t.enabled} disabled={busy} onFocus={soon(t)} onChange={(e) => turn(t, e.target.checked)} />
                  on
                </label>
                <button className="icon-btn !h-6 !w-6" disabled={busy} aria-label={`Remove the ${t.kind}`} title="Remove"
                  onClick={() => void go(() => AgentsApi.removeTrigger(agent.id, t.id))}><Trash2 size={12} /></button>
              </span>
            </div>
            {t.prompt && <div className="text-[11px] text-muted">with "{t.prompt}"</div>}
            <div className="flex flex-wrap gap-x-3 text-[11px] text-faint">
              {t.last_at ? <span>last {ago(t.last_at)}</span> : <span>not fired yet</span>}
              {t.kind === "schedule" && adherence(t) ? (
                <span title="On time: started within 5 minutes of its time. Late: after. Missed: past its grace, the machine or the app down. Skipped: by its precheck">
                  {adherence(t)}
                </span>
              ) : t.missed > 0 && <span>{t.missed} missed</span>}
              {t.kind === "hook" && (t.refused ?? 0) > 0 && <span>{t.refused} refused (wrong signature)</span>}
            </div>
            {t.last_error && <div className="text-[11px] text-wait">{t.last_error}</div>}
            {/* under the trigger's row, its switch being at the row's far end */}
            {(turnedOn[t.id] ?? 0) > 0 && <RiskTerms key={`on-${turnedOn[t.id]}`} point={7} kind="control" open />}
          </li>
        ))}
      </ul>
      {made?.secret && (
        <div className="mt-1.5 rounded-md border border-wait/50 bg-wait/[0.06] px-2 py-1.5" role="note" aria-label="The hook's secret">
          <div className="text-wait">The hook's secret, shown this once:</div>
          <div className="flex items-center gap-1.5">
            <code className="min-w-0 flex-1 truncate font-mono text-[11px]">{made.secret}</code>
            <button className="icon-btn !h-6 !w-6" aria-label="Copy the secret" onClick={() => void navigator.clipboard?.writeText(made.secret!)}><Copy size={12} /></button>
            <button className="btn btn-sm !h-6 text-[11px]" onClick={() => setMade(null)}>Done</button>
          </div>
          <div className="text-[11px] text-muted">{made.hook_type === "github"
            ? "In GitHub's webhook settings: the payload URL above, content type application/json, and this secret."
            : "Each delivery sends it as Authorization: Bearer <secret>."}</div>
        </div>
      )}
      {adding && (
        <div className={TERMS_IN_FLOW}>
          <TriggerForm agent={agent} kind={adding} onMade={async (t) => { setAdding(null); setMade(t); await onChanged(); }} />
          {/* made with the form, and again for the other kind's, so the text opens each time a form does */}
          <RiskTerms key={adding} point={7} kind="control" open />
        </div>
      )}
    </div>
  );
}

function TriggerForm({ agent, kind, onMade }: { agent: Agent; kind: "schedule" | "hook"; onMade: (t: Trigger) => Promise<void> }) {
  const [line, setLine] = useState("0 9 * * 1-5");
  const [tz, setTz] = useState(localZone);
  const [hookType, setHookType] = useState<"github" | "bearer">("github");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    setError("");
    try {
      const body = kind === "schedule" ? { kind, cron: line, tz, prompt: prompt.trim() || null } : { kind, hook_type: hookType, prompt: prompt.trim() || null };
      await onMade(await AgentsApi.addTrigger(agent.id, body));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-1.5 grid gap-2 rounded-md border border-line p-2 sm:grid-cols-2">
      {kind === "schedule" ? (<>
        <label className="flex flex-col gap-1 text-[12px] text-muted">When: a cron line (minute, hour, day, month, day of the week)
          <input className="input !h-8 font-mono text-[12px]" value={line} onChange={(e) => setLine(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-muted">In the timezone
          <input className="input !h-8 text-[12px]" value={tz} onChange={(e) => setTz(e.target.value)} />
        </label>
      </>) : (
        <label className="flex flex-col gap-1 text-[12px] text-muted">Signed
          <select className="input !h-8 text-[12.5px]" value={hookType} onChange={(e) => setHookType(e.target.value as "github" | "bearer")}>
            <option value="github">as GitHub signs its webhooks</option>
            <option value="bearer">with a bearer secret</option>
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-[12px] text-muted sm:col-span-2">A prompt for its runs, after the brief
        <input className="input !h-8 text-[12.5px]" value={prompt} maxLength={4000} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      {kind === "hook" && <div className="text-[11.5px] text-muted sm:col-span-2">A delivery reaches the run as a file, as data, never as an instruction:
        anyone can write the text an issue or a commit carries.</div>}
      {error && <div className="text-[12px] text-fail sm:col-span-2">{error}</div>}
      <div className="sm:col-span-2"><button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void make()}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : null} Add the {kind}</button></div>
    </div>
  );
}
