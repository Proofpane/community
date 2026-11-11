import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, Clock, Play, RefreshCw, Webhook, X } from "lucide-react";
import { AgentsApi, UNATTENDED_WORDS, type CalendarAnswer, type CalendarFiring, type CalendarSchedule, type FiringState } from "../agents";
import { useStore } from "../store";
import { clock, dayTime, deviceZone, full } from "../when";
import { useEscape } from "./bits";
import { useFocusInto } from "./Modal";
import { RiskTerms, TERMS_IN_FLOW } from "./RiskTerms";

// ---- days in a zone ---------------------------------------------------------------------------------------------------
// The calendar's days are the viewer's: a month, a week (Monday first) or a day in the zone the device keeps, as every
// time on the page is shown (when.ts). A day is told by its date in that zone, so a time late on a UTC day falls on the
// next day where the viewer is ahead of UTC.

/** A date in a zone: the year, the month from 1 to 12, and the day. */
export interface Ymd { y: number; m: number; d: number }

function partsIn(t: number, zone: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date(t))) {
    if (p.type !== "literal") out[p.type] = Number(p.value);
  }
  return out;
}

/** The date a time (unix seconds) falls on in the zone. */
export function ymdOf(t: number, zone: string): Ymd {
  const p = partsIn(t * 1000, zone);
  return { y: p.year, m: p.month, d: p.day };
}

/** "2026-11-03": a date's key, to tell which day of the grid a time falls on. */
export const keyOf = (x: Ymd) => `${x.y}-${String(x.m).padStart(2, "0")}-${String(x.d).padStart(2, "0")}`;
export const dayKey = (t: number, zone: string) => keyOf(ymdOf(t, zone));

/** The date `n` days after `x` (or before, for a negative `n`). */
export function addDays(x: Ymd, n: number): Ymd {
  const d = new Date(Date.UTC(x.y, x.m - 1, x.d + n));
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate() };
}

/** Monday 0 to Sunday 6. */
const weekday = (x: Ymd) => (new Date(Date.UTC(x.y, x.m - 1, x.d)).getUTCDay() + 6) % 7;

/** The zone's offset from UTC at an instant, in milliseconds. */
function offsetAt(ms: number, zone: string): number {
  const p = partsIn(ms, zone);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second) - Math.floor(ms / 1000) * 1000;
}

/** When a date's day starts in the zone, in unix seconds: its first instant there (its midnight, or the first time its
 *  clock shows on a day the clock skips midnight). */
export function startOf(x: Ymd, zone: string): number {
  const wall = Date.UTC(x.y, x.m - 1, x.d);
  let t = wall - offsetAt(wall, zone);
  t = wall - offsetAt(t, zone);
  // a midnight the clock skips: its day starts at the first instant on it
  while (keyOf(ymdOf(Math.floor(t / 1000), zone)) < keyOf(x)) t += 15 * 60 * 1000;
  return Math.floor(t / 1000);
}

export type View = "month" | "week" | "day" | "next";

/** The window a view reads: its first day's start to the start of the day after its last, in the zone. */
export function windowOf(view: View, anchor: Ymd, zone: string): [number, number] {
  if (view === "month") {
    const first = { ...anchor, d: 1 };
    const next = anchor.m === 12 ? { y: anchor.y + 1, m: 1, d: 1 } : { y: anchor.y, m: anchor.m + 1, d: 1 };
    return [startOf(first, zone), startOf(next, zone)];
  }
  if (view === "week") {
    const monday = addDays(anchor, -weekday(anchor));
    return [startOf(monday, zone), startOf(addDays(monday, 7), zone)];
  }
  return [startOf(anchor, zone), startOf(addDays(anchor, 1), zone)];
}

// ---- marks --------------------------------------------------------------------------------------------------------------

/** One mark on the grid: a coming time of a schedule, or a firing the app's store records. */
export interface Mark { at: number; schedule: CalendarSchedule; state: FiringState | "coming"; firing: CalendarFiring | null }

const COLOR: Record<FiringState | "coming", string> = {
  coming: "text-muted", queued: "text-muted", running: "text-run", "waits for a person": "text-wait", ran: "text-done",
  failed: "text-fail", stopped: "text-faint", skipped: "text-faint", held: "text-wait", missed: "text-fail",
};

/** The marks of an answer, each at its time: a capped schedule's coming times are drawn as a band, not one by one. */
export function marksOf(answer: CalendarAnswer): Mark[] {
  const by = new Map(answer.schedules.map((s) => [s.id, s]));
  const out: Mark[] = [];
  for (const s of answer.schedules) {
    if (s.capped) continue;
    for (const at of s.coming) out.push({ at, schedule: s, state: "coming", firing: null });
  }
  for (const f of answer.firings) {
    const s = by.get(f.trigger_id);
    const at = f.at ?? f.due;
    if (s && at != null) out.push({ at, schedule: s, state: f.state, firing: f });
  }
  return out.sort((a, b) => a.at - b.at || a.schedule.id.localeCompare(b.schedule.id));
}

/** A span of seconds in words: seconds, minutes, hours or days, whichever reads best. */
export const span = (s: number) => (s < 90 ? `${Math.max(0, Math.round(s))} s` : s < 90 * 60 ? `${Math.round(s / 60)} min`
  : s < 48 * 3600 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} days`);

/** What a stored time already passed comes to: its scheduler's next round fires it while it is within its grace, and
 *  records it missed, running nothing, once it is later than that (triggers.tick). */
export const passedWords = (at: number, now: number, grace: number | null) => (now - at > (grace ?? 0) * 60
  ? `past its grace of ${grace ?? 0} min: the scheduler's next round records it missed and runs nothing`
  : "due now, for the scheduler's next round");

/** A mark's words: its state, and what its record says of it. */
export function markWords(m: Mark, now: number): string {
  const f = m.firing;
  // a stored time already passed is the one the scheduler uses still: its next round fires it, or records it missed
  if (!f) return m.at <= now ? passedWords(m.at, now, m.schedule.grace_minutes) : "coming";
  const parts: string[] = [m.state];
  // the store keeps no time a wait began: when its run started is the latest it keeps before it
  if (m.state === "waits for a person" && f.started_at != null) parts[0] = `waits for a person; its run started ${span(now - f.started_at)} ago`;
  if (m.state === "missed") parts[0] = "missed: the app or the machine was down";
  if (f.late_seconds != null && f.late_seconds > 0) parts.push(`${span(f.late_seconds)} after its time`);
  if (f.run_now) parts.push("by Run now");
  if (f.reason && m.state !== "missed") parts.push(f.reason);
  return parts.join(", ");
}

/** A trigger's name on the calendar: its agent's, and its line (or that it is a hook), an agent having up to ten. */
const scheduleName = (s: CalendarSchedule) => `${s.agent} (${s.kind === "schedule" ? s.cron : "hook"})`;

function MarkLine({ m, now, zone, onAgents }: { m: Mark; now: number; zone: string; onAgents?: () => void }) {
  const words = markWords(m, now);
  return (
    <li className="flex flex-wrap items-baseline gap-x-1 text-[11px] leading-snug" title={full(m.at, zone)}
      aria-label={`${scheduleName(m.schedule)}, ${clock(m.at, zone)}, ${words}`}>
      <span className="font-mono text-faint">{clock(m.at, zone)}</span>
      <span className="truncate font-medium">{m.schedule.agent}</span>
      <span className="font-mono text-faint">{m.schedule.kind === "schedule" ? m.schedule.cron : "hook"}</span>
      <span className={COLOR[m.state]}>{words}</span>
      {m.state === "waits for a person" && onAgents && (
        <button className="btn btn-sm !h-5 !px-1.5 text-[10.5px]" onClick={onAgents}>Answer it in Agents</button>
      )}
    </li>
  );
}

// ---- the calendar -------------------------------------------------------------------------------------------------------

const VIEWS: [View, string][] = [["month", "Month"], ["week", "Week"], ["day", "Day"], ["next", "Next 20"]];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"];
const IN_A_CELL = 4;

/** The calendar of the account's agent triggers:
 *  month, week and day grids in the zone the device keeps, and the next 20 due. Each schedule's coming times are the
 *  server's, worked out by the scheduler's own code from its stored next time, never here; the firings are the ones the
 *  app's store records, each with its state in words; a schedule with more than the cap's times in the view is listed
 *  above the grid as one band. Read from the app's store (never starting a machine), every 10 seconds while a run is
 *  working or waiting and every 30 otherwise, while the page is visible, and at once after the owner's own change; an
 *  answer for a window no longer shown is dropped. It fires nothing by itself: Run now and a schedule's switch call the
 *  trigger's own routes; Run now opens risk point 7's text as it goes, and the switch of one that is off opens it as
 *  the pointer comes to it or it takes the keyboard, before it is turned on, as the agents page does
 *  (AgentTriggers.tsx). */
export function Calendar({ onClose, onAgents, zone = deviceZone(), today }: {
  onClose: () => void; onAgents?: () => void; zone?: string; today?: Ymd;
}) {
  const setError = useStore((s) => s.setError);
  const [view, setView] = useState<View>("month");
  const [anchor, setAnchor] = useState<Ymd>(() => today ?? ymdOf(Math.floor(Date.now() / 1000), zone));
  const [answer, setAnswer] = useState<CalendarAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  // how many times each schedule's Run now or switch was pressed here: each press opens risk point 7's text again
  const [pressed, setPressed] = useState<Record<string, number>>({});
  useEscape(onClose);
  const panel = useRef<HTMLElement>(null);
  useFocusInto(panel);
  const [from, to] = useMemo(() => windowOf(view, anchor, zone), [view, anchor, zone]);
  // the window shown now: a slow answer for one shown before is not drawn over it
  const shown = useRef<[number, number]>([from, to]);
  shown.current = [from, to];

  const load = useCallback(async () => {
    try {
      const got = await AgentsApi.calendar(from, to);
      if (shown.current[0] === from && shown.current[1] === to) setAnswer(got);
    } catch (e) { setError(e); }
  }, [from, to, setError]);
  // read at once for each window, then again on its own clock, the clock set again when a run starts or ends
  const live = answer?.live ?? 0;
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === "visible" && void load(), live > 0 ? 10000 : 30000);
    return () => clearInterval(t);
  }, [load, live]);

  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e) { setError(e); } finally { setBusy(false); await load(); }
  };
  const press = (s: CalendarSchedule) => setPressed((was) => ({ ...was, [s.id]: (was[s.id] ?? 0) + 1 }));
  const runNow = (s: CalendarSchedule) => { press(s); void go(() => AgentsApi.runTrigger(s.agent_id, s.id)); };
  const turn = (s: CalendarSchedule, on: boolean) => {
    if (on) press(s);
    void go(() => AgentsApi.changeTrigger(s.agent_id, s.id, { enabled: on }));
  };
  const move = (n: number) => setAnchor((a) => view === "month"
    ? (() => { const m = a.m - 1 + n; return { y: a.y + Math.floor(m / 12), m: ((m % 12) + 12) % 12 + 1, d: 1 }; })()
    : addDays(a, view === "week" ? 7 * n : n));

  const marks = useMemo(() => (answer ? marksOf(answer) : []), [answer]);
  const byDay = useMemo(() => {
    const m = new Map<string, Mark[]>();
    for (const x of marks) {
      const k = dayKey(x.at, zone);
      m.set(k, [...(m.get(k) ?? []), x]);
    }
    return m;
  }, [marks, zone]);
  const now = answer?.now ?? Math.floor(Date.now() / 1000);
  const capped = (answer?.schedules ?? []).filter((s) => s.capped);
  const title = view === "month" ? `${MONTHS[anchor.m - 1]} ${anchor.y}`
    : view === "week" ? `The week of ${dayTime(from, zone).split(",")[0]}` : view === "day" ? dayTime(from, zone).split(",")[0]
    : "The next firings due";

  return (
    <div className="fixed inset-0 z-40 bg-black/40" onMouseDown={onClose}>
      <aside ref={panel} tabIndex={-1} aria-label="Calendar"
        className="absolute inset-1 flex flex-col overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60 outline-none sm:inset-4"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
          <CalendarDays size={16} className="text-accent" />
          <span className="text-[13.5px] font-semibold">Calendar</span>
          <span className="hidden text-[12px] text-muted sm:inline">your agents' schedules, and what came of their firings, in {zone}</span>
          <span className="ml-auto flex items-center gap-1">
            <button className="icon-btn" onClick={() => void load()} title="Read again" aria-label="Read again"><RefreshCw size={14} /></button>
            <button className="icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close"><X size={15} /></button>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2 text-[12px]">
          {VIEWS.map(([v, w]) => (
            <button key={v} className={`btn btn-sm ${view === v ? "btn-primary" : ""}`} aria-pressed={view === v} onClick={() => setView(v)}>{w}</button>
          ))}
          {view !== "next" && (
            <span className="ml-2 flex items-center gap-1">
              <button className="icon-btn" aria-label="Before" onClick={() => move(-1)}><ChevronLeft size={14} /></button>
              <button className="btn btn-sm" onClick={() => setAnchor(today ?? ymdOf(Math.floor(Date.now() / 1000), zone))}>Today</button>
              <button className="icon-btn" aria-label="After" onClick={() => move(1)}><ChevronRight size={14} /></button>
            </span>
          )}
          <span className="font-medium">{title}</span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <section className="min-h-0 flex-1 overflow-y-auto p-3" aria-label="The times">
            {capped.map((s) => (
              <div key={s.id} role="note" aria-label={`${s.agent}: past the cap`} className="mb-1.5 rounded-md border border-wait/50 bg-wait/[0.06] px-2 py-1 text-[11.5px]">
                <span className="font-medium">{s.agent}</span> <span className="font-mono">{s.cron}</span>{" "}
                <span className="text-muted">has more than {answer?.cap} times in this view: the first {s.coming.length}, from{" "}
                  {dayTime(s.coming[0], zone)} to {dayTime(s.coming[s.coming.length - 1], zone)}, and more after them, listed here as one
                  band and not drawn on the days</span>
              </div>
            ))}
            {answer === null ? <div className="py-10 text-center text-muted">Reading the calendar</div>
              : view === "next" ? <DueNext answer={answer} zone={zone} />
              : view === "month" ? <MonthGrid anchor={anchor} byDay={byDay} zone={zone} now={now} onAgents={onAgents}
                  onDay={(x) => { setAnchor(x); setView("day"); }} />
              : (
                <div className={`grid gap-2 ${view === "week" ? "md:grid-cols-7" : ""}`}>
                  {Array.from({ length: view === "week" ? 7 : 1 }, (_, i) => {
                    const x = view === "week" ? addDays(addDays(anchor, -weekday(anchor)), i) : anchor;
                    const here = byDay.get(keyOf(x)) ?? [];
                    return (
                      <div key={keyOf(x)} role="group" aria-label={`${x.d} ${MONTHS[x.m - 1].slice(0, 3)}`} className="rounded-md border border-line p-1.5">
                        <div className="mb-1 text-[11px] font-semibold text-muted">{DAYS[weekday(x)]} {x.d} {MONTHS[x.m - 1].slice(0, 3)}</div>
                        {here.length === 0 ? <div className="text-[11px] text-faint">nothing</div>
                          : <ul className="space-y-0.5">{here.map((m, j) => <MarkLine key={j} m={m} now={now} zone={zone} onAgents={onAgents} />)}</ul>}
                      </div>
                    );
                  })}
                </div>
              )}
            <p className="mt-3 text-[11px] text-faint">
              The past shows what the app's store records alone: each run one of your triggers queued (a trigger removed
              takes its runs off the calendar), and each trigger's latest firing that was held or missed, the latest only,
              with the counts beside it. A time nothing records is not drawn, and none is worked out again from a
              schedule's line.
            </p>
          </section>
          <section className="min-h-0 flex-none overflow-y-auto border-t border-line p-3 md:w-[38%] md:border-l md:border-t-0" aria-label="The schedules">
            {answer && answer.schedules.length === 0 && <div className="text-[12px] text-muted">No agent of yours has a schedule or a hook.</div>}
            <ul className="space-y-1.5">
              {answer?.schedules.map((s) => (
                <li key={s.id} className={`rounded-md border border-line px-2 py-1.5 text-[12px] ${TERMS_IN_FLOW}`} aria-label={`${s.agent}'s ${s.kind}`}>
                  <ScheduleCard s={s} zone={zone} busy={busy} onRunNow={() => runNow(s)} onTurn={(on) => turn(s, on)}
                    onSoon={() => { if (!s.enabled) press(s); }} onAgents={onAgents} />
                  {/* under the card, Run now and the switch being in it; each press, and each coming to the switch of one
                      that is off, opens it again */}
                  {(pressed[s.id] ?? 0) > 0 && <RiskTerms key={`p-${pressed[s.id]}`} point={7} kind="control" open />}
                </li>
              ))}
            </ul>
          </section>
        </div>
      </aside>
    </div>
  );
}

function MonthGrid({ anchor, byDay, zone, now, onAgents, onDay }: {
  anchor: Ymd; byDay: Map<string, Mark[]>; zone: string; now: number; onAgents?: () => void; onDay: (x: Ymd) => void;
}) {
  const first = { ...anchor, d: 1 };
  const days: Ymd[] = [];
  for (let x = first; x.m === first.m; x = addDays(x, 1)) days.push(x);
  return (
    <div className="grid grid-cols-7 gap-1">
      {DAYS.map((d) => <div key={d} className="text-center text-[10.5px] font-semibold uppercase text-faint">{d}</div>)}
      {Array.from({ length: weekday(first) }, (_, i) => <div key={`blank-${i}`} />)}
      {days.map((x) => {
        const here = byDay.get(keyOf(x)) ?? [];
        return (
          <div key={keyOf(x)} role="group" aria-label={`${x.d} ${MONTHS[x.m - 1].slice(0, 3)}`} className="min-h-[72px] rounded-md border border-line p-1">
            <div className="text-[11px] font-semibold text-muted">{x.d}</div>
            <ul className="space-y-0.5">{here.slice(0, IN_A_CELL).map((m, j) => <MarkLine key={j} m={m} now={now} zone={zone} onAgents={onAgents} />)}</ul>
            {here.length > IN_A_CELL && (
              <button className="text-[10.5px] text-accent hover:underline" onClick={() => onDay(x)}>{here.length - IN_A_CELL} more</button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function DueNext({ answer, zone }: { answer: CalendarAnswer; zone: string }) {
  const grace = new Map(answer.schedules.map((s) => [s.id, s.grace_minutes]));
  if (answer.due_next.length === 0) return <div className="text-[12px] text-muted">No schedule of yours is on, with a time to come.</div>;
  return (
    <ol className="space-y-1" aria-label="The next firings due">
      {answer.due_next.map((d, i) => (
        <li key={`${d.trigger_id}-${d.at}-${i}`} className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
          <span className="font-mono text-faint" title={full(d.at, zone)}>{dayTime(d.at, zone)}</span>
          <span className="font-medium">{d.agent}</span>
          {d.at <= answer.now && <span className="text-wait">{passedWords(d.at, answer.now, grace.get(d.trigger_id) ?? null)}</span>}
          {d.held_now ? <span className="text-wait">would be held now: {d.held_now}</span> : <span className="text-muted">runs unattended</span>}
        </li>
      ))}
    </ol>
  );
}

/** A trigger as the calendar shows it beside the grid: what it is, what it runs as, what would hold it now, its switch
 *  (the same route as its agent's page, PUT .../triggers/{t}) and its Run now (the trigger's own route, POST
 *  .../triggers/{t}/run, which its agent's page does not offer: that page's Run now is a run by hand). */
function ScheduleCard({ s, zone, busy, onRunNow, onTurn, onSoon, onAgents }: {
  s: CalendarSchedule; zone: string; busy: boolean; onRunNow: () => void; onTurn: (on: boolean) => void; onSoon: () => void;
  onAgents?: () => void;
}) {
  // the trigger's own counts are kept since it was made; its skipped runs are counted among the runs kept
  const counts = ([["on time", s.counts.on_time], ["late", s.counts.late], ["missed", s.counts.missed],
                   ["refused (wrong signature)", s.counts.refused]] as const).filter(([, n]) => n > 0).map(([w, n]) => `${n} ${w}`);
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-2">
        {s.kind === "schedule" ? <Clock size={11} className="text-muted" /> : <Webhook size={11} className="text-muted" />}
        <span className="font-medium">{s.agent}</span>
        {s.kind === "schedule"
          ? <span><span className="font-mono">{s.cron}</span>{s.tz !== zone && <span className="text-faint"> in {s.tz}</span>}</span>
          : <span className="text-muted">a hook, signed {s.hook_type === "github" ? "as GitHub signs" : "with a bearer secret"}</span>}
        <span className="ml-auto flex items-center gap-1">
          {s.actions.includes("run_now") && (
            <button className="btn btn-sm !h-6 text-[11px]" disabled={busy} onClick={onRunNow}
              title="One firing now, as its schedule would run it: unattended, held as a firing is; its next time does not move">
              <Play size={11} /> Run now</button>
          )}
          {s.actions.includes("switch") && (
            <label className="flex items-center gap-1 text-[11px] text-muted" onMouseEnter={onSoon}>
              <input type="checkbox" checked={s.enabled} disabled={busy} onFocus={onSoon} onChange={(e) => onTurn(e.target.checked)} /> on
            </label>
          )}
        </span>
      </div>
      {s.prompt && <div className="text-[11px] text-muted">with "{s.prompt}"</div>}
      <div className="text-[11px] text-muted">
        Runs as "{s.unattended_mode}": {UNATTENDED_WORDS[s.unattended_mode]}. A run may still stop to ask you a question,
        and then waits for you.
      </div>
      {s.kind === "schedule" && (
        <div className="text-[11px] text-faint">
          At most {s.max_runs_per_day} of its agent's runs a day, counted by UTC day
          {s.more_than_a_day_runs === true && <span className="text-wait">: its line has more times than that in a UTC day of this view, and past that its firings are held</span>}
          {s.more_than_a_day_runs === null && <span>; its times in each UTC day of this view were not all counted</span>}
          .{s.enabled && s.next_at != null && <> Next {dayTime(s.next_at, zone)}.</>}
          {s.enabled && s.next_at == null && <> On, but its line gives no time to come: it cannot fire.</>}
          {!s.enabled && <> Off: no time to come.</>}
        </div>
      )}
      {s.agent_retired && <div className="text-[11px] text-faint">Its agent is retired.</div>}
      {s.agent_paused && !s.agent_retired && (
        <div className="text-[11px] text-wait">{s.agent_paused_until != null
          ? `Its agent is paused until ${dayTime(s.agent_paused_until, zone)}: no time of it comes before then.`
          : "Its agent is paused: no time of it comes until it is resumed."}</div>
      )}
      {s.held_now && <div className="text-[11px] text-wait">A firing now would be held: {s.held_now}</div>}
      {counts.length > 0 && <div className="text-[11px] text-faint">{counts.join(", ")}, since it was made</div>}
      {s.counts.skipped > 0 && <div className="text-[11px] text-faint">{s.counts.skipped} skipped by its precheck, of its runs kept (a run that ended over 90 days ago is not kept)</div>}
      {s.last_error && <div className="text-[11px] text-wait">Its latest: {s.last_error}</div>}
      {s.past_capped && <div className="text-[11px] text-faint">More of its firings in this view than the cap: the first ones are drawn.</div>}
      {onAgents && s.actions.includes("change") && (
        <button className="mt-0.5 text-[11px] text-accent hover:underline" onClick={onAgents}>Change it in Agents</button>
      )}
    </>
  );
}
