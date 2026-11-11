import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Loader2, Puzzle, RefreshCw, X } from "lucide-react";
import { ApiError, OLD_SIGN_IN } from "../api";
import { useVisibleEvery } from "../sessionMap";
import { MODEL_NAMES } from "../chatSettings";
import {
  barHeight, beforeCounting, countingWords, dayTotal, endedWords, groupsOf, missedWords, notCandidate,
  PROPOSAL_STATE_WORDS, readNotes, rowKey, SkillImproveApi, SkillsApi, versionWords, whereWords, WINDOW_DEFAULT,
  WINDOWS, type Candidates, type DayCount, type ReadResult, type RecordState, type RepoSwitch, type SkillDetail,
  type SkillLoop, type SkillProposal, type SkillRow, type SkillsView, type Window,
} from "../skills";
import { useStore } from "../store";
import { dayTime } from "../when";
import { BusinessProposal, BusinessProposals } from "./AgentImprovements";

// The Skills view, opened from the control view. It shows each skill the
// workspace's sessions called, core, business (by repository) and other, with its calls by UTC day, where they were made,
// by version and how the tasks that called it ended, from the app's counts (skills.ts; proofpane_open/ade/skill_counts.py).
// Opening it asks the app to read the workspace's record at once, and again every 15 seconds while the page is visible
// (useVisibleEvery). Every word a session, the owner or a model wrote (a skill's name, a repository, a requirement's
// title) is shown as it is, as text, never as markup. What the counts hold is the sessions' word, for display, and the
// view says so, and says what a count can miss, whatever the app answers.

const REFRESH = 15000;

/** What a failed read says: the server's own words, or, where the server has no route for the view (a page newer than the
 *  server it talks to, whose framework says only "Not Found"), that it has none yet; a page not signed in says to sign in. */
function failedWords(e: unknown): string {
  if (e instanceof ApiError && e.status === 404 && e.message === "Not Found") {
    return "This server does not have the skills view yet: it comes with a later version of Proofpane Community.";
  }
  if (e instanceof ApiError && e.status === 401) return "This page is not signed in: sign in again, then reload it.";
  return e instanceof Error ? e.message : String(e);
}
const aborted = (e: unknown) => e instanceof DOMException && e.name === "AbortError";
// a 401 answered to the sign-in the page had before it signed in again: no sign-out, and the next read carries the new one
const staleSignIn = (e: unknown) => e instanceof ApiError && e.status === 401 && e.message === OLD_SIGN_IN;
const signedOut = (e: unknown) => e instanceof ApiError && e.status === 401 && !staleSignIn(e);

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const KIND_COLOUR = { core: "bg-accent", business: "bg-done", other: "bg-muted" } as const;

export function SkillsPanel({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState<Window>(WINDOW_DEFAULT);
  const [view, setView] = useState<SkillsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<ReadResult | null>(null);
  const [reading, setReading] = useState(false);
  // the business skills' loop (skill_improve.py): its proposals, its budget and its switches, from the app's store
  // alone, and the workspace's record of proposals, which only the workspace holds; nothing of either drawn where the
  // server has no such routes, or the loop's list did not come
  const [loop, setLoop] = useState<SkillLoop | null>(null);
  const [record, setRecord] = useState<RecordState | null>(null);
  const looped = useRef(false);
  const daysNow = useRef(days);
  daysNow.current = days;

  // what the app holds, drawn at once; then the app's read of the workspace, and what it holds drawn again (the read can
  // change how the reads stand even where it took no line)
  const draw = useCallback(async (signal?: AbortSignal) => {
    const v = await SkillsApi.view(daysNow.current, signal);
    setView(v);
    setError(null);
  }, []);

  const drawLoop = useCallback(async (signal?: AbortSignal) => {
    try {
      setLoop(await SkillImproveApi.loop(signal));
      looped.current = true;
    } catch (e) {
      if (aborted(e)) throw e;
      setLoop(null);
      looped.current = false;
    }
  }, []);

  // the record as the workspace holds it, read as the skill calls are, with each round: whether it is held, and the
  // starts it refused
  const readRecord = useCallback(async (signal?: AbortSignal) => {
    if (!looped.current) { setRecord(null); return; }
    try {
      setRecord(await SkillImproveApi.record(signal));
    } catch (e) {
      if (aborted(e)) throw e;
      setRecord(null);
    }
  }, []);

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      await draw(signal);
      await drawLoop(signal);
      setReading(true);
      try {
        setLast(await SkillsApi.read(signal));
      } finally {
        setReading(false);
      }
      await draw(signal);
      await readRecord(signal);
      return true;
    } catch (e) {
      if (aborted(e) || staleSignIn(e)) return true;
      setError(failedWords(e));
      if (signedOut(e)) setView(null);                  // a sign-in that ended: none of it is left drawn
      return false;
    }
  }, [draw, drawLoop, readRecord]);
  useVisibleEvery(true, REFRESH, load);

  // after a tap of the owner's: the loop's list and the record read again at once
  const again_loop = useCallback(async () => {
    try { await drawLoop(); await readRecord(); } catch { /* a read that ended: the next round draws it */ }
  }, [drawLoop, readRecord]);

  // another window is drawn from what the app holds, at once
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    draw().catch((e) => { if (!aborted(e) && !staleSignIn(e)) setError(failedWords(e)); });
  }, [days, draw]);

  const again = () => { void load(new AbortController().signal); };

  return (
    <section className="mb-4 rounded-lg border border-line bg-panel" aria-label="Skills">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <Puzzle size={15} className="text-accent" aria-hidden />
        <h3 className="text-[13px] font-semibold">Skills called</h3>
        <span className="ml-auto" />
        <div role="group" aria-label="Days to count over" className="flex items-center gap-1">
          {WINDOWS.map((w) => (
            <button key={w} className={`btn btn-sm ${w === days ? "!border-accent text-fg" : "text-muted"}`} aria-pressed={w === days}
              onClick={() => setDays(w)}>{w} days</button>
          ))}
        </div>
        <button className="icon-btn" onClick={again} disabled={reading} aria-label="Read the workspace again"
          title="Read the workspace's record of skill calls again">
          <RefreshCw size={13} className={reading ? "animate-spin" : ""} />
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close the skills view" title="Close"><X size={14} /></button>
      </div>
      <div className="space-y-3 px-3 py-2.5">
        {error && <p role="alert" className="text-[12px] text-fail">{error}</p>}
        {!view && !error && (
          <div className="flex justify-center py-6 text-muted"><Loader2 size={15} className="animate-spin" aria-label="Reading the skills" /></div>
        )}
        {view && loop && <BusinessProposals loop={loop} record={record} onDone={again_loop} />}
        {view && <Body view={view} last={last} loop={loop} onLoop={again_loop} />}
      </div>
    </section>
  );
}

/** Drawn from the view as it was last read: its own window, which is not the selected one for the moment after a switch,
 *  while the answer for the new window is awaited (or for good, if that read fails). */
function Body({ view, last, loop, onLoop }: { view: SkillsView; last: ReadResult | null; loop: SkillLoop | null; onLoop: () => Promise<void> }) {
  const days = view.days;
  const g = groupsOf(view.skills);
  const notes = readNotes(view.read, last);
  return (
    <div className="space-y-3">
      <p className="text-[12px] text-muted">
        Counted by UTC day, from {view.from_day} to {view.to_day} (today's UTC day is not over). In these {days} days:{" "}
        {plural(view.totals.calls, "call")} of {plural(view.totals.skills, "skill")} in {plural(view.totals.sessions, "session")}
        {view.totals.failed > 0 && `, ${view.totals.failed} of them failed`}
        {view.totals.typed > 0 && `, ${view.totals.typed} typed by a person`}.
      </p>
      {view.by_day.length > 0 && <Days days={view.by_day} view={view} />}
      <Warning view={view} notes={notes} />
      {view.skills.length === 0 && <p className="text-[12px] text-muted">No skill call was counted in these {days} days.</p>}
      {g.core.length > 0 && (
        <Group title="Core skills" note="Proofpane's own, given to the claude terminals and the owner's chats of a paired workspace" rows={g.core} days={days} />
      )}
      {g.business.map((b) => (b.repo !== null && loop
        ? <BusinessGroup key={b.repo} repo={b.repo} rows={b.rows} days={days} loop={loop} onLoop={onLoop} />
        : <Group key={b.repo ?? "\u0000"} title={`Business skills in ${b.repo ?? "no repository"}`}
            note={b.repo === null ? "skills of a folder that is no repository the workspace names" : BUSINESS_NOTE}
            rows={b.rows} days={days} />
      ))}
      {g.other.length > 0 && (
        <Group title="Other skills" note="a skill that is neither a core one nor a repository's own: another plugin's, an MCP server's, a bundled one, a person's or an organisation's, a repository's own that one of those shadows or that lies in a folder inside the repository, or a command file of the repository's called as a skill" rows={g.other} days={days} />
      )}
      {view.more > 0 && <p className="text-[11.5px] text-faint">{plural(view.more, "more row")} not listed: the view lists the most-called.</p>}
    </div>
  );
}

/** The calls of each UTC day of the window, a column for each, stacked by kind. Each column says its day and its counts
 *  (to a screen reader and on hover); the window's days are all there, a day with no calls a column of none, and a day
 *  before the app began reading drawn faint and said to be (a day of none there may be a day nothing was counted). */
function Days({ days, view }: { days: DayCount[]; view: SkillsView }) {
  const most = days.reduce((m, d) => Math.max(m, dayTotal(d)), 0);
  const before = (d: DayCount) => beforeCounting(d.day, view.read);
  const words = (d: DayCount) =>
    `${d.day}: ${plural(dayTotal(d), "call")} (core ${d.core}, business ${d.business}, other ${d.other})`
    + (before(d) ? ", before the app began reading" : "");
  const since = countingWords(view.read, view.from_day);
  return (
    <div>
      <ol className="flex h-16 items-end gap-px rounded border border-line px-1 pt-1" aria-label="Calls by UTC day">
        {days.map((d) => (
          <li key={d.day} data-before-reading={before(d) ? "true" : undefined} aria-label={words(d)} title={words(d)}
            className={`flex h-full min-w-[2px] flex-1 flex-col justify-end${before(d) ? " opacity-40" : ""}`}>
            <div className="flex flex-col-reverse" style={{ height: `${barHeight(d, most) * 100}%` }}>
              {(["core", "business", "other"] as const).map((k) => d[k] > 0 && (
                <div key={k} className={KIND_COLOUR[k]} style={{ flexGrow: d[k] }} />
              ))}
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 text-[10.5px] text-faint">
        <span><span className={`mr-1 inline-block h-2 w-2 ${KIND_COLOUR.core}`} />core</span>
        <span><span className={`mr-1 inline-block h-2 w-2 ${KIND_COLOUR.business}`} />business</span>
        <span><span className={`mr-1 inline-block h-2 w-2 ${KIND_COLOUR.other}`} />other</span>
        <span className="ml-auto">{days[0]?.day} to {days[days.length - 1]?.day}, UTC</span>
      </div>
      {since && <p className="mt-1 text-[10.5px] text-faint">{since}</p>}
    </div>
  );
}

/** The design's own warning, and what the app knows of how its reads stand: the counts are the sessions' word, for
 *  display, and a count can miss calls. Drawn whatever the answer holds. */
function Warning({ view, notes }: { view: SkillsView; notes: string[] }) {
  return (
    <div className="rounded border border-line px-2.5 py-2 text-[11.5px] text-muted" role="note" aria-label="What these counts are">
      <p>
        These counts are the sessions' word, for display: they are not a record that holds against a session. A program a
        session runs can write its own file of calls, and the workspace's record of them, so a count can be made up as well as
        missed.
      </p>
      <p className="mt-1">
        A count can also miss calls: a line lost to a cut of a session's file, a session removed before its file was
        read, a line the workspace dropped, past 30 days or 20,000 lines, before the app read it, a call made as a chat
        ended (Claude Code ends a background hook still running when a headless session ends), a call whose session ran
        a Claude Code older than 2.1.23 or began before the workspace was updated, a session whose settings turn hooks
        off (the organisation's, or in a workspace machine a repository's or the person's own, or a folder Claude Code
        does not trust), a chat whose own settings are past 100 KB (it is started without the hook), a call whose
        result is past 16 MB (the hook cannot read it), and a skill a person typed that is neither a repository's own
        nor a core one. A skill is counted under the name the model gave it (a core skill named without its prefix,
        under its full name), a call that failed as one that ran, and a call the person interrupted as one that failed.
        A call's time and version are those of the hook's run, a little after the call: a call just before midnight UTC
        can be counted in the next day, and a commit made in between is named for it. This view cannot say whether a
        session's hook ran: a session whose hook did not run adds nothing, which looks like no calls. Not counted at
        all: a skill typed in the Manager's own chat, an agent's runs, a task's attempts, a skill Claude Code calls
        by its folder path (a name with a / in it), and a session of any program but Claude Code.
      </p>
      <p className="mt-1">{missedWords(view.read)}</p>
      {notes.map((n) => <p key={n} className="mt-1">{n}</p>)}
    </div>
  );
}

function callWords(r: { calls: number; failed: number; typed: number }): string {
  const extra = [r.failed > 0 ? `${r.failed} failed` : "", r.typed > 0 ? `${r.typed} typed by a person` : ""].filter(Boolean);
  return `${plural(r.calls, "call")}${extra.length ? ` (${extra.join(", ")})` : ""}`;
}

const BUSINESS_NOTE = "skills of this repository's .claude/skills that were called in these days";

/** What a row of a group carries beyond its counts: a business skill's newest proposal, and why it may not be proposed
 *  for, where the workspace said it may not. */
interface RowExtra { proposal: SkillProposal | null; notWhy: string | null }

function Group({ title, note, rows, days, onLoop, head = null, extra }: {
  title: string; note: string; rows: SkillRow[]; days: Window; onLoop?: () => Promise<void>; head?: ReactNode;
  extra?: (row: SkillRow) => RowExtra;
}) {
  return (
    <section aria-label={title}>
      <h4 className="flex flex-wrap items-baseline gap-2 text-[12.5px] font-medium text-fg-dim">
        {title}
        <span className="text-[11px] font-normal text-faint">{note}</span>
      </h4>
      {head}
      <ul className="mt-1.5 space-y-1.5">
        {rows.map((r) => <Item key={rowKey(r)} row={r} days={days} {...(extra ? extra(r) : {})} onLoop={onLoop} />)}
      </ul>
    </section>
  );
}

/** The newest proposal for a business skill's row: its repository's, for the folder of the row's name, a folder matched
 *  by its name or by a name it is called by as the workspace's candidates say, as notCandidate matches one (else a
 *  row named otherwise than its folder shows no proposal). */
export function proposalFor(row: SkillRow, loop: SkillLoop | null, cands: Candidates | null = null): SkillProposal | null {
  if (!loop || row.kind !== "business" || row.repo === null) return null;
  const folders = new Set([row.skill, ...(cands?.candidates ?? []).filter((c) => c.names.includes(row.skill)).map((c) => c.folder)]);
  return loop.proposals.find((p) => p.repo === row.repo && folders.has(p.folder)) ?? null;
}

/** A repository's business skills with its switch. Where the switch is on, the workspace's candidates are
 *  read when the view opens and after each turn of the switch, not with each round (each read reaches the workspace,
 *  which reads the branch and makes a clone of its head), and each skill's row says why it may not be proposed for. */
function BusinessGroup({ repo, rows, days, loop, onLoop }: {
  repo: string; rows: SkillRow[]; days: Window; loop: SkillLoop; onLoop: () => Promise<void>;
}) {
  const sw = loop.switches.find((s) => s.repo === repo) ?? null;
  const on = sw?.on === true;
  const changed = sw?.changed_at ?? null;
  const [cands, setCands] = useState<Candidates | null>(null);
  const [candError, setCandError] = useState("");
  useEffect(() => {
    setCands(null);
    setCandError("");
    if (!on) return undefined;
    let live = true;
    SkillImproveApi.candidates(repo)
      .then((c) => { if (live) setCands(c); })
      .catch((e) => { if (live) setCandError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [repo, on, changed]);
  return (
    <Group title={`Business skills in ${repo}`} note={BUSINESS_NOTE} rows={rows} days={days} onLoop={onLoop}
      head={<RepoSwitchRow repo={repo} sw={sw} cands={cands} candError={candError} onLoop={onLoop} />}
      extra={(r) => ({ proposal: proposalFor(r, loop, cands), notWhy: on ? notCandidate(r.skill, cands) : null })} />
  );
}

/** A repository's switch, only the owner's to turn: off by default. It names the model and the account the
 *  review and the critic run on, each kept with the switch; on, the loop may propose a change to the repository's
 *  business skills, each for the owner's tap. For a clone, it shows origin's address and branch as recorded. */
function RepoSwitchRow({ repo, sw, cands, candError, onLoop }: {
  repo: string; sw: RepoSwitch | null; cands: Candidates | null; candError: string; onLoop: () => Promise<void>;
}) {
  const on = sw?.on === true;
  const caps = useStore((s) => s.caps);
  const accountRows = useStore((s) => s.accounts?.claude.accounts);
  const [account, setAccount] = useState(sw?.account ?? "");
  const [model, setModel] = useState(sw?.model ?? "");
  useEffect(() => { setAccount(sw?.account ?? ""); setModel(sw?.model ?? ""); }, [sw?.account, sw?.model]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (next: boolean) => {
    setBusy(true);
    setError("");
    try {
      await SkillImproveApi.turn(repo, next, account || null, model || null);
      await onLoop();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const edited = on && ((sw?.account ?? "") !== account || (sw?.model ?? "") !== model);
  return (
    <div className="mt-1 rounded border border-line px-2 py-1.5 text-[11.5px] text-muted" role="group" aria-label={`Proposals for ${repo}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span>Proposals for its business skills: {on ? "on" : "off"}</span>
        <button className="btn btn-sm" disabled={busy} aria-pressed={on} onClick={() => void send(!on)}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : null} {on ? "Turn off" : "Turn on"}
        </button>
        {edited && <button className="btn btn-sm" disabled={busy} onClick={() => void send(true)}>Keep this model and account</button>}
      </div>
      <div className="mt-1 flex flex-wrap gap-2">
        <label className="flex items-center gap-1">The model its review and critic run on
          <select className="input !h-6 !py-0 text-[11.5px]" value={model} disabled={busy} onChange={(e) => setModel(e.target.value)}>
            <option value="">Claude Code's own</option>
            {caps.model_aliases.map((m) => <option key={m} value={m}>{MODEL_NAMES[m] ?? m}</option>)}
            {model && !caps.model_aliases.includes(model) && <option value={model}>{model}</option>}
          </select>
        </label>
        <label className="flex items-center gap-1">The account they run on
          <select className="input !h-6 !py-0 text-[11.5px]" value={account} disabled={busy} onChange={(e) => setAccount(e.target.value)}>
            <option value="">The account new sessions start on</option>
            {(accountRows ?? []).filter((r) => r.id !== "default").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            {account && !(accountRows ?? []).some((r) => r.id === account) && <option value={account}>{account}</option>}
          </select>
        </label>
      </div>
      {on && sw && (sw.url || sw.branch) && (
        <div className="mt-0.5 break-all text-faint">Recorded when turned on: {sw.url ? `origin ${sw.url}` : ""}{sw.url && sw.branch ? ", " : ""}{sw.branch ? `branch ${sw.branch}` : ""}</div>
      )}
      {on && <div className="mt-0.5 text-faint">Each proposal is a model's, for you to merge or not; while it is not merged, no start the workspace makes unattended here begins from it.</div>}
      {on && candError && <div className="mt-0.5 text-fail">The workspace did not say which skills may be proposed for: {candError}</div>}
      {on && cands && !cands.ok && <div className="mt-0.5">No skill here may be proposed for now: {cands.why ?? "the workspace did not say why"}</div>}
      {error && <div className="mt-0.5 text-fail" role="alert">{error}</div>}
    </div>
  );
}

function Item({ row, days, proposal = null, notWhy = null, onLoop }: {
  row: SkillRow; days: Window; proposal?: SkillProposal | null; notWhy?: string | null; onLoop?: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(false);
  return (
    <li className="rounded-md border border-line" aria-label={row.skill}>
      <button className="flex w-full items-start gap-2 px-2.5 py-1.5 text-left" aria-expanded={open} onClick={() => setOpen(!open)}
        aria-label={`${row.skill}: ${open ? "hide" : "show"} its detail`}>
        {open ? <ChevronDown size={13} className="mt-0.5 flex-none" /> : <ChevronRight size={13} className="mt-0.5 flex-none" />}
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="break-all text-[12.5px] font-medium">{row.skill}</span>
            <span className="text-[11.5px] text-fg-dim">{callWords(row)}</span>
            {row.last_at !== null && <span className="text-[10.5px] text-faint">last {dayTime(row.last_at)}</span>}
          </span>
          <span className="block text-[11px] text-muted">called in {whereWords(row)}</span>
          <span className="block break-all text-[11px] text-muted">
            version: {versionWords(row.kind, row.latest_version)}{row.versions > 1 ? ` (the latest of ${row.versions})` : ""}
          </span>
          <span className="block text-[11px] text-muted">tasks that called it: {endedWords(row.ended)}</span>
        </span>
      </button>
      {notWhy && <div className="px-2.5 pb-1.5 text-[11px] text-faint">Not proposed for: {notWhy}</div>}
      {proposal && (
        <div className="px-2.5 pb-1.5 text-[11px]">
          <button className="text-accent underline" aria-expanded={shown} onClick={() => setShown(!shown)}>
            A proposal: {PROPOSAL_STATE_WORDS[proposal.state]}
          </button>
          {shown && <BusinessProposal p={proposal} onDone={onLoop ?? (async () => undefined)} />}
        </div>
      )}
      {open && <Detail row={row} days={days} />}
    </li>
  );
}

/** One skill over the window, read when it is opened and again every 15 seconds while it is open and the page is visible. */
function Detail({ row, days }: { row: SkillRow; days: Window }) {
  const [got, setGot] = useState<{ detail: SkillDetail | null; error: string | null }>({ detail: null, error: null });
  const now = useRef({ row, days });
  now.current = { row, days };
  const read = useCallback(async (signal: AbortSignal) => {
    try {
      const detail = await SkillsApi.detail(now.current.row, now.current.days, signal);
      setGot({ detail, error: null });
      return true;
    } catch (e) {
      if (aborted(e) || staleSignIn(e)) return true;
      // a skill with no call in this window any more (the window changed under it): said, not read as a failure
      if (e instanceof ApiError && e.status === 404) {
        setGot({ detail: null, error: `No call of this skill in the last ${now.current.days} days.` });
        return true;
      }
      setGot((g) => ({ detail: signedOut(e) ? null : g.detail, error: failedWords(e) }));
      return false;
    }
  }, []);
  useVisibleEvery(true, REFRESH, read);
  // another window: read it at once (the timer reads the window it holds, in its own time)
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    void read(new AbortController().signal);
  }, [days, read]);
  const d = got.detail;
  return (
    <div className="space-y-2 border-t border-line px-3 py-2 text-[11.5px] text-fg-dim" aria-label={`${row.skill}: detail`}>
      {got.error && <p role="alert" className="text-fail">{got.error}</p>}
      {!d && !got.error && <div className="flex justify-center py-3 text-muted"><Loader2 size={14} className="animate-spin" aria-label="Reading its detail" /></div>}
      {d && (
        <>
          <p className="text-muted">
            {callWords(d)}, first {d.first_at !== null ? dayTime(d.first_at) : "none"}, last {d.last_at !== null ? dayTime(d.last_at) : "none"}.
            {d.kind === "business" && d.repo === null && " Calls made in no repository."}
          </p>
          <DetailList title="Calls by UTC day">
            {d.by_day.filter((x) => x.calls > 0).map((x) => (
              <li key={x.day}>{x.day}: {plural(x.calls, "call")}{x.failed > 0 ? `, ${x.failed} failed` : ""}</li>
            ))}
          </DetailList>
          <DetailList title="By version">
            {d.versions.map((v, i) => (
              <li key={`${v.version ?? ""}:${i}`} className="break-all">
                {versionWords(d.kind, v.version)}: {plural(v.calls, "call")}
                {v.first_at !== null && `, from ${dayTime(v.first_at)}`}{v.last_at !== null && v.last_at !== v.first_at && ` to ${dayTime(v.last_at)}`}
              </li>
            ))}
            {d.more.versions && <li className="text-muted">and older versions, not listed</li>}
          </DetailList>
          {d.kind !== "business" && d.repos.length > 0 && (
            <DetailList title="Repositories">
              {d.repos.map((x, i) => <li key={`${x.repo ?? ""}:${i}`} className="break-all">{x.repo ?? "no repository"}: {plural(x.calls, "call")}</li>)}
              {d.more.repos && <li className="text-muted">and repositories with fewer calls, not listed</li>}
            </DetailList>
          )}
          <DetailList title="Sessions">
            {d.sessions.map((x) => <li key={x.session}>{x.session}: {plural(x.calls, "call")}</li>)}
            {d.more.sessions && <li className="text-muted">and older sessions, not listed</li>}
          </DetailList>
          <DetailList title="Tasks and how they ended">
            {d.tasks.map((x) => (
              <li key={x.task} className="break-all">{x.task}: {plural(x.calls, "call")}, {x.state ?? "not known to the app"}
                {x.ended_at !== null && `, ended ${dayTime(x.ended_at)}`}</li>
            ))}
            {d.more.tasks && <li className="text-muted">and older tasks, not listed</li>}
            {d.tasks.length > 0 && <li className="text-muted">{endedWords(d.ended)}</li>}
          </DetailList>
          <DetailList title="Requirements (each title is a model's words)">
            {d.requirements.map((x) => (
              <li key={x.requirement} className="break-words">{x.title ?? "a requirement the app has no record of"}: {plural(x.calls, "call")} in {plural(x.tasks, "task")}</li>
            ))}
            {d.more.requirements && <li className="text-muted">and requirements with fewer calls, not listed</li>}
          </DetailList>
        </>
      )}
    </div>
  );
}

/** A list the detail draws under a heading, nothing at all while it has no item. */
function DetailList({ title, children }: { title: string; children: ReactNode }) {
  const items = (Array.isArray(children) ? children.flat() : [children]).filter(Boolean);
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-[10.5px] text-faint">{title}</div>
      <ul className="space-y-0.5">{children}</ul>
    </div>
  );
}
