import { type ReactNode, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Clock, Loader2, SquareKanban, SquareArrowOutUpRight, X } from "lucide-react";
import { accountWords, drawable, grantWords, parts, rowKey, stateColor, stateWords, useBoard, type BoardRow } from "../board";
import { toSession, useStore, workspaceRuns } from "../store";
import { useTimeChoice } from "../timeChoice";
import { localFull, localTime, untilWords, type TimeChoice } from "../when";
import { useEscape } from "./bits";
import { useFocusInto } from "./Modal";
import { TimeSettings } from "./TimeSettings";

const PARTS: [BoardRow["phase"], string, string][] = [
  ["history", "History", "Nothing has ended yet."],
  ["now", "Now", "Nothing runs or waits for you."],
  ["next", "Next", "Nothing waits to start."],
];
// a requirement of the controller's is a "sub-goal" on the page (an owner's
// request is a goal, a requirement a sub-goal, a controller's task a task); the row's kind keeps the app's own word
const KIND_WORDS: Record<BoardRow["kind"], string> = { task: "Task", requirement: "Sub-goal", session: "Session" };

/** A time on the board, in the person's zone and language's format, its whole date, zone and UTC in its tooltip;
 *  nothing for a time no Date can hold. */
function Time({ t, choice, now }: { t: number; choice: TimeChoice; now: Date }) {
  if (!drawable(t)) return null;
  return <time dateTime={new Date(t * 1000).toISOString()} title={localFull(t, choice)}>{localTime(t, choice, now)}</time>;
}

/** What a row's `waits_until` says: in Next, when it goes on ("goes on 14:00 · in 12 min"), or, that time come and
 *  the scheduler's round not yet, that it is due; in Now, the time from which the owner's Continue is what it waits
 *  for (a task's Auto-resume off: progress_board.py). */
function WaitWords({ r, at, now }: { r: BoardRow; at: (t: number) => ReactNode; now: Date }) {
  const t = r.waits_until;
  if (!drawable(t)) return null;
  const until = untilWords(t, now);
  const ahead = until ? <> · {until}</> : null;
  if (r.phase === "next") return until ? <span>goes on {at(t)}{ahead}</span> : <span>due since {at(t)}</span>;
  return <span>Continue from {at(t)}{ahead}{r.kind === "task" ? " · Auto-resume is off" : ""}</span>;
}

/** The live progress board: what the person's tasks, requirements and
 *  sessions did, what runs now and how far it is, and what waits in its order, read every 3 seconds while it is open
 *  and the page is shown. Its times are in the zone and the language's format the person keeps for it (Times). */
export function Board({ onClose, onOpenSession }: { onClose: () => void; onOpenSession?: (id: string) => void }) {
  const { board, error } = useBoard(true);
  const zone = useTimeChoice((s) => s.zone);
  const locale = useTimeChoice((s) => s.locale);
  const choice = useMemo(() => ({ zone, locale }), [zone, locale]);
  const accounts = useStore((s) => s.accounts);
  const running = useStore((s) => workspaceRuns(s.ws));
  // the sessions the page lists, the ones a row can open
  const listed = useStore((s) => s.records);
  const openable = useMemo(() => new Set(listed.filter((r) => !r.closed).map((r) => r.id)), [listed]);
  // the session a row runs in, by its title: a session's own row's, else as the list shows it (toSession), else its id
  const titles = useMemo(() => new Map(listed.map((r) => [r.id, toSession(r).title])), [listed]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [times, setTimes] = useState(false);
  useEscape(onClose, !times);
  const panel = useRef<HTMLElement>(null);
  useFocusInto(panel);
  const split = useMemo(() => parts(board?.rows ?? []), [board?.rows]);
  const now = new Date((board?.at ?? Date.now() / 1000) * 1000);
  const toggle = (key: string) => setOpen((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  // the Times dialog is drawn beside the board, not inside it: a press on its backdrop closes it alone
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onMouseDown={onClose}>
        <aside ref={panel} tabIndex={-1} aria-label="Progress board"
          className="absolute inset-1 flex flex-col overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60 outline-none sm:inset-4"
          onMouseDown={(e) => e.stopPropagation()}>
          <div className="flex h-12 flex-none items-center gap-2 border-b border-line px-4">
            <SquareKanban size={16} className="text-accent" />
            <span className="text-[13.5px] font-semibold">Progress board</span>
            {board && (
              <span className="hidden text-[12px] text-muted sm:inline">
                {split.now.length} now · {split.next.length} next · {split.history.length} ended
              </span>
            )}
            <span className="ml-auto flex items-center gap-1">
              <button className="btn btn-sm" onClick={() => setTimes(true)} title="The zone and format of the board's times">
                <Clock size={13} /> Times
              </button>
              <button className="icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close"><X size={15} /></button>
            </span>
          </div>
          {!running && (
            <div className="flex-none border-b border-line px-4 py-1.5 text-[12px] text-muted">
              The workspace's sessions are on the board while it runs.
            </div>
          )}
          {error && (
            <div role="status" className="flex-none border-b border-fail/40 bg-fail/[0.06] px-4 py-1.5 text-[12px] text-fg-dim">
              {board ? "Showing the board as last read: " : ""}{error}
            </div>
          )}
          {board === null ? (
            // nothing read yet: a spinner, or, where the first read failed, the line above alone (no empty parts)
            !error && <div className="flex flex-1 justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>
          ) : (
            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto md:grid-cols-3 md:overflow-hidden">
              {PARTS.map(([phase, title, empty]) => (
                <section key={phase} aria-label={title} className="flex min-h-0 flex-col border-line md:overflow-y-auto md:border-r md:last:border-r-0">
                  <h2 className="sticky top-0 z-[1] bg-panel/95 px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
                    {title} <span className="font-normal normal-case">{split[phase].length}</span>
                  </h2>
                  {split[phase].length === 0 ? (
                    <p className="px-3 py-3 text-[12px] text-muted">{empty}</p>
                  ) : (
                    <ul>
                      {split[phase].map((r) => (
                        <Row key={rowKey(r)} r={r} choice={choice} now={now} account={accountWords(r.account, accounts)}
                          session={r.session ? (r.kind === "session" ? r.title : titles.get(r.session) || r.session) : undefined}
                          open={open.has(rowKey(r))} onToggle={() => toggle(rowKey(r))}
                          onOpen={r.session && onOpenSession && openable.has(r.session) ? () => onOpenSession(r.session!) : undefined} />
                      ))}
                    </ul>
                  )}
                </section>
              ))}
            </div>
          )}
        </aside>
      </div>
      {times && <TimeSettings onClose={() => setTimes(false)} />}
    </>
  );
}

function Row({ r, choice, now, account, session, open, onToggle, onOpen }: {
  r: BoardRow; choice: TimeChoice; now: Date; account: string; session?: string; open: boolean; onToggle: () => void;
  onOpen?: () => void;
}) {
  const last = r.events[r.events.length - 1];
  const color = stateColor(r);
  const at = (t: number) => <Time t={t} choice={choice} now={now} />;
  return (
    <li className="border-t border-line first:border-t-0" data-row={rowKey(r)}>
      <div className="flex items-start gap-2 px-3 py-2 hover:bg-hover">
        {r.phase === "next" && r.order != null && (
          <span className="mt-0.5 w-5 flex-none text-right font-mono text-[11px] text-muted" aria-label={`Number ${r.order} of next`}>{r.order}.</span>
        )}
        <span className="dot mt-1.5 inline-block flex-none rounded-full" style={{ background: color, width: 8, height: 8 }} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="min-w-0 truncate text-[13px] font-medium" title={r.title}>{r.title}</span>
            <span className="text-[11.5px]" style={{ color }}>{stateWords(r)}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted">
            <span>{KIND_WORDS[r.kind] ?? r.kind}</span>
            {account && <span>{account}</span>}
            {session && r.kind !== "session" && <span className="min-w-0 truncate" title={session}>in session {session}</span>}
            {r.progress && (
              <span className="flex items-center gap-1.5" aria-label={`${r.progress.done} of ${r.progress.of} steps done`}>
                <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-active" aria-hidden="true">
                  <span className="absolute inset-y-0 left-0 rounded-full bg-done" style={{ width: `${r.progress.of ? (100 * r.progress.done) / r.progress.of : 0}%` }} />
                </span>
                {r.progress.done} of {r.progress.of} steps
              </span>
            )}
            {r.kind === "task" && r.grant && <span className={r.grant.version === null || r.grant.ask_current === false ? "text-wait" : undefined}>{grantWords(r.grant)}</span>}
            {r.phase !== "history" && <WaitWords r={r} at={at} now={now} />}
            {drawable(r.started_at) && <span>started {at(r.started_at)}</span>}
            {!drawable(r.started_at) && drawable(r.created_at) && <span>made {at(r.created_at)}</span>}
            {drawable(r.ended_at) && <span>ended {at(r.ended_at)}</span>}
          </div>
          {last && !open && <div className="mt-0.5 truncate text-[11.5px] text-fg-dim">{last.words}</div>}
          {open && (
            <ol className="mt-1 flex flex-col gap-0.5 text-[11.5px]" aria-label={`What happened to ${r.title}`}>
              {r.more_events > 0 && <li className="text-faint">and {r.more_events} earlier</li>}
              {r.events.map((e, i) => (
                <li key={`${e.at}-${i}`} className="flex gap-2">
                  <span className="w-20 flex-none text-muted">{at(e.at)}</span>
                  <span className="min-w-0 flex-1 text-fg-dim">{e.words}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
        <span className="flex flex-none items-center gap-0.5">
          {onOpen && (
            <button className="icon-btn !h-6 !w-6" onClick={onOpen} title="Open the session" aria-label={`Open the session ${session ?? r.title}`}>
              <SquareArrowOutUpRight size={13} />
            </button>
          )}
          {r.events.length > 0 && (
            <button className="icon-btn !h-6 !w-6" onClick={onToggle} aria-expanded={open}
              aria-label={`${open ? "Fold" : "Unfold"} what happened to ${r.title}`}>
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          )}
        </span>
      </div>
    </li>
  );
}
