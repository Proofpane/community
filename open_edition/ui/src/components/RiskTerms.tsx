import { useCallback, useEffect, useRef, useState, type RefObject, type SyntheticEvent } from "react";
import { Loader2, ScrollText, X } from "lucide-react";
import { create } from "zustand";
import { Api, type RiskQuotationView, type RiskTextView } from "../api";
import { useStore } from "../store";
import { day } from "../when";

// The terms at a risk point: one component that opens a risk point's text, the design's own words and the clauses
// quoted with their sources, read from GET /api/ade/risks, `{operator}` and `{record_keeping}` filled there. It
// renders the text, never the control, and
// nothing a person does waits on it: no press is asked and no acknowledgement is kept. The one thing that waits on a
// text is the page's own act of kind "before", risk point 16's: the accounts page's read of an account by itself, held
// back until that text has opened in this browser and the owner has closed it; what holds it back is this browser's
// own storage, and nothing of it reaches the app. Rendered by AgentTask.tsx, AgentTriggers.tsx and Connections.tsx,
// by TaskHandOff.tsx (OffersWaiting among it) and TaskCustody.tsx, and by ContinueSession.tsx, each by name.

/** The risk points, 1 to 17 (point 17: a workspace session continued in a new one). */
export type RiskPoint = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17;

export interface RiskTermsProps {
  point: RiskPoint;
  /** "control": rendered beside the person's own control by the component that owns it, the text opening over the
   *  control each time `open` turns true, every time, non-modal, the control working without the text being closed.
   *  "event": what the product did by itself, the text opened once for `event` in this browser, at once if the page is
   *  open, else at the owner's next visit; which events it has opened is kept in this browser's own storage alone, by
   *  the signed-in account and `event`, and sent nowhere; a browser whose storage holds none opens only the events of
   *  the last 7 days, once each.
   *  "before": what the page would do by itself and does not do until the text has opened in this browser and the
   *  owner has closed it (risk point 16: the accounts page's read of an account by itself), rendered where the page
   *  would act; the text opens as `open` turns true, as a control's does, and the owner's Close (its button, or
   *  Escape where the text is on top), made once its words have shown, is kept in this browser's own storage alone,
   *  by the signed-in account and the point, apart from the events' record, and sent nowhere: what waits on it goes
   *  on then (closedHere, untilClosedHere). A Close before its words could be read keeps nothing, nor does the close
   *  of a dialog it is in (Escape in that dialog among them). A browser that keeps no close (a new device, or its
   *  storage cleared) opens it again before the page next acts so. */
  kind: "control" | "event" | "before";
  /** kind "control": whether the control (its form, list, dialog or switch) is open now; kind "before": whether the
   *  page's act waits on the text now */
  open?: boolean;
  /** kind "event": the event's key, for a move "task:<task id>:<journal seq>" (TaskMoveLine.seq) */
  event?: string;
  /** the text's conditional parts to show, from the point's cases (a move line's own TaskMoveLine.cases) */
  cases?: string[];
  /** risk point 13's "{owner}": the owner as the offer or custody view names them */
  owner?: string;
  /** risk point 1's "{home_group}", for Back home */
  homeGroup?: string;
}

/** The class a box takes so that the RiskTerms texts among its own children stand in the page's flow, under what opened
 *  them, rather than hanging over the lines below: where one control opens several (a form, an account chosen, a
 *  confirmation), they stand one under another and none covers a field or a choice. */
export const TERMS_IN_FLOW = "[&>[role=dialog]]:static [&>[role=dialog]]:mt-1 [&>[role=dialog]]:w-full [&>[role=dialog]]:max-w-full [&>[role=dialog]]:shadow-none";

// ---- the texts, read once for the page ------------------------------------------------------------------------------

/** Every risk point's text as the page last read it, by point, null until a read gave them; and whether the last read
 *  failed. Shared by every RiskTerms on the page. */
const useTexts = create<{ texts: Record<number, RiskTextView> | null; failed: boolean }>(() => ({ texts: null, failed: false }));
let reading: Promise<void> | null = null;
// the page's number: a read made for a page before forgetPage() changes nothing on the new one
let page = 0;

/** Every text read again, the page's copy replaced once the answer comes. A read under way is joined, so texts that
 *  open together ask once. */
function read(): Promise<void> {
  if (reading) return reading;
  const mine = page;
  reading = Api.risks()
    .then((r) => {
      if (mine === page) useTexts.setState({ texts: Object.fromEntries(r.texts.map((t) => [t.point, t])), failed: false });
    })
    .catch(() => {
      // the page's copy stays: a text read before still shows
      if (mine === page) useTexts.setState({ failed: true });
    })
    .finally(() => {
      if (mine === page) reading = null;
    });
  return reading;
}

/** What this page holds, forgotten, as a new page starts with none of it: the texts read, and the records it saved
 *  (read where the browser's storage cannot be), the events' and the closes'. For a test, which makes a new page each
 *  time. What waits on a close is not forgotten: it is the waiting act's, and a close on the page settles it. */
export function forgetPage(): void {
  page += 1;
  reading = null;
  useTexts.setState({ texts: null, failed: false });
  held.clear();
  firstLook.clear();
  unkept.clear();
}

// ---- which events this browser has opened ---------------------------------------------------------------------------

/** One account's record in this browser: each event it has opened, with when, in Unix seconds (0 for one taken as
 *  opened without being shown: older than 7 days at a visit where the browser held no opened event). Kept in this
 *  browser's own storage alone, one key per account: nothing of it reaches the app. */
interface Opened { shown: Record<string, number> }

const KEY = "pp-risk-shown:";
const WEEK = 7 * 24 * 3600;
// each account's record as this page last saved it, read in place of the browser's storage where reading that throws
// (storage turned off): so there an event still opens once a page, and one older than 7 days never
const held = new Map<string, Opened>();
// whether this browser held no opened event for an account when this page first looked: one answer for every list on
// the page, whichever lists first, so that the old events one list takes as opened do not decide another's
const firstLook = new Map<string, boolean>();

function load(account: string): Opened | null {
  try {
    const got = JSON.parse(localStorage.getItem(KEY + account) ?? "null") as Partial<Opened> | null;
    const ok = !!got && !!got.shown && typeof got.shown === "object" && !Array.isArray(got.shown);
    return ok ? { shown: got.shown! } : null;
  } catch {
    return held.get(account) ?? null;
  }
}

function save(account: string, r: Opened): void {
  held.set(account, r);
  try { localStorage.setItem(KEY + account, JSON.stringify(r)); } catch { /* not kept by this browser */ }
}

/** A visit where this browser holds no opened event for `account` (a new device, its storage cleared, or one that has
 *  opened none yet) opens only the last 7 days' events: every listed event older than
 *  7 days, by its time (`at`, in Unix seconds), and not opened, is taken as opened without being shown. Whether the
 *  browser held none is read at the page's first call, so that several lists on it answer alike, and holds until an
 *  event is shown on the page. A browser that holds an opened event opens every event it has not opened, however old.
 *  The component that lists events calls it during its render, before their RiskTerms render (an effect of its own
 *  would run after theirs, too late). */
export function settleFirstVisit(account: string, events: { event: string; at: number }[], now = Date.now() / 1000): void {
  if (!account) return;
  const r = load(account) ?? { shown: {} };
  if (!firstLook.has(account)) firstLook.set(account, Object.keys(r.shown).length === 0);
  if (!firstLook.get(account)) return;
  let changed = false;
  for (const e of events) {
    if (e.at < now - WEEK && !Object.hasOwn(r.shown, e.event)) {
      r.shown[e.event] = 0;
      changed = true;
    }
  }
  if (changed) save(account, r);
}

/** The events of `events` whose texts would open now in this browser, its first visit's rule applied first: for a
 *  panel that unfolds what holds them, so that they open at the owner's visit without a further press. */
export function eventsToOpen(account: string, events: { event: string; at: number }[], now = Date.now() / 1000): string[] {
  if (!account) return [];
  settleFirstVisit(account, events, now);
  return events.filter((e) => !wasOpened(account, e.event)).map((e) => e.event);
}

function wasOpened(account: string, event: string): boolean {
  const r = load(account);
  return !!r && Object.hasOwn(r.shown, event);
}

function markOpened(account: string, event: string, now = Date.now() / 1000): void {
  const r = load(account) ?? { shown: {} };
  r.shown[event] = now;
  save(account, r);
  // the browser now holds an opened event: an older one listed later on this page opens too
  firstLook.set(account, false);
}

// ---- which texts this browser's owner has closed, before what the page does by itself --------------------------------

/** A kind "before" text closed by the owner in this browser (risk point 16's): one key per point and account, holding
 *  when it was closed, in Unix seconds. Kept apart from the events' record (KEY), so that neither changes what the other
 *  holds: a close is no opened event, and an event opened is no close. Kept in this browser's own storage alone:
 *  nothing of it reaches the app. */
const CLOSED = "pp-risk-closed:";
// each close this page made that the browser's storage did not keep (storage turned off, or full): so there the text
// opens once a page, and the next page opens it again
const unkept = new Set<string>();
// what waits on a close, by its key, settled by the owner's Close of that text on this page
const waiting = new Map<string, (() => void)[]>();

const closedKey = (point: RiskPoint, account: string) => `${CLOSED}${point}:${account}`;

/** Whether this browser keeps the owner's close of `point`'s text for `account`: false with no account, and in a
 *  browser that keeps none (a new device, or its storage cleared, whenever it was cleared). */
export function closedHere(point: RiskPoint, account: string): boolean {
  if (!account) return false;
  const key = closedKey(point, account);
  if (unkept.has(key)) return true;
  try {
    const got = JSON.parse(localStorage.getItem(key) ?? "null") as { closed?: unknown } | null;
    return !!got && typeof got.closed === "number";
  } catch {
    return false;
  }
}

/** Settles once this browser keeps the owner's close of `point`'s text for `account`: at once where it keeps one,
 *  else at the owner's Close of that text on this page. */
export function untilClosedHere(point: RiskPoint, account: string): Promise<void> {
  if (closedHere(point, account)) return Promise.resolve();
  const key = closedKey(point, account);
  return new Promise((go) => { waiting.set(key, [...(waiting.get(key) ?? []), go]); });
}

function markClosed(point: RiskPoint, account: string, now = Date.now() / 1000): void {
  const key = closedKey(point, account);
  try {
    localStorage.setItem(key, JSON.stringify({ closed: now }));
    unkept.delete(key);
  } catch {
    unkept.add(key);                                                   // not kept by this browser: this page keeps it
  }
  const go = waiting.get(key) ?? [];
  waiting.delete(key);
  for (const g of go) g();
}

// ---- the text on the page -------------------------------------------------------------------------------------------

/** A risk point's text, over the page where it is rendered: in a `relative` box beside the control, or on the event's
 *  line (not in a `p`). It is never modal and never takes the keyboard, and holds no button but its Close: the control
 *  works while it is open, and nothing a person does waits on it. Escape closes it, as it closes what is on top. Kind
 *  "control": it opens each time `open` turns true (at its mount among them), every time, and closes when `open` turns
 *  false; each opening reads the texts again, the page's copy showing meanwhile, and a closed control asks the app
 *  nothing. Kind "event": it opens once for `event` in this browser, for the signed-in account, once its text is read:
 *  at its mount, so at once when the event's line appears on an open page, else at the owner's next visit. Its link on
 *  the line opens it again. Kind "before": it opens and reads as a control's does, and the owner's Close of it, once
 *  its words have shown, is kept for the signed-in account in this browser, which lets what waits on it go on; an
 *  Escape that closes a dialog it is in closes the text with that dialog, and keeps nothing. */
export function RiskTerms({ point, kind, open, event, cases, owner, homeGroup }: RiskTermsProps) {
  const text = useTexts((s) => s.texts?.[point] ?? null);
  // no text to show: the last read failed, or the texts were read and this point has none
  const unreadable = useTexts((s) => s.failed || (s.texts !== null && !s.texts[point]));
  const account = useStore((s) => s.user?.id ?? "");
  const [shown, setShown] = useState(false);
  const [wasOpen, setWasOpen] = useState(false);
  // the cases the text was last given (sorted, once each), and how many times it has opened while its control stayed
  // open: a case that arrives later counts as an opening
  const caseKey = [...new Set(cases ?? [])].sort().join("\n");
  const [lastCases, setLastCases] = useState(caseKey);
  const [reopened, setReopened] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  // a control's text opens as the control does, and closes with it; closed by the person, it opens again next time.
  // So does a "before" text, as what waits on it does
  if (kind !== "event" && !!open !== wasOpen) {
    setWasOpen(!!open);
    setShown(!!open);
    setLastCases(caseKey);
  } else if (kind !== "event" && open && caseKey !== lastCases) {
    // while the control stays open, a part that arrives later (an agent chosen in another group, a check adopted, after
    // the text opened with none of them) opens the text again where the person closed it, so that what a case adds is
    // shown at the point it applies, each time it comes to apply; one that only goes changes nothing
    const before = new Set(lastCases ? lastCases.split("\n") : []);
    setLastCases(caseKey);
    if (!shown && (cases ?? []).some((c) => !before.has(c))) {
      setShown(true);
      setReopened((n) => n + 1);
    }
  }

  // the texts are read once for the page, by the first event's line (which opens by itself) or the first control's text
  // to open, a closed control asking nothing; and again each time a control's or a "before" text opens, so that words
  // changed on the server show the next time
  useEffect(() => {
    if (kind === "event" && !useTexts.getState().texts) void read();
  }, [kind]);
  useEffect(() => {
    if (kind !== "event" && open) void read();
  }, [kind, open, reopened]);

  // what the product did by itself: opened once in this browser, marked as it opens, in this browser's storage alone
  useEffect(() => {
    if (kind !== "event" || !event || !account || !text || wasOpened(account, event)) return;
    markOpened(account, event);
    setShown(true);
  }, [kind, event, account, text]);

  // the text closed: by its Close, or by Escape. With `owners`, the owner's own close of it: a "before" text whose words
  // have shown is then kept closed for the signed-in account in this browser, which lets what waits on it go on; one
  // closed before its words could be read keeps nothing, and what waits on it waits on
  const close = useCallback((owners: boolean) => {
    if (owners && kind === "before" && text && account) markClosed(point, account);
    setShown(false);
  }, [kind, text, account, point]);

  useEffect(() => {
    if (!shown) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // a dialog that holds the keyboard, or the palette, opened over this text takes the key, and the text stays. On
      // top, the text takes the key as its Close. In a dialog on top, which closes on the key too, the text goes with
      // that dialog: the owner closed the dialog, not the text, as when they close the dialog by its own Close button
      const layers = document.querySelectorAll('[role="dialog"][aria-modal="true"], [cmdk-root]');
      const top = layers[layers.length - 1];
      if (!top) close(true);
      else if (top.contains(box.current)) close(false);
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [shown, close]);

  const title = text?.title ?? `Risk point ${point}`;
  const panel = shown && (
    <Terms box={box} title={title} text={text} unreadable={unreadable} cases={cases} owner={owner} homeGroup={homeGroup}
      onClose={() => close(true)} />
  );
  if (kind !== "event") return panel || null;
  return (
    <>
      <button type="button" className="inline-flex items-center gap-0.5 text-[11px] text-accent hover:underline"
        aria-label={`The terms: ${title}`} aria-haspopup="dialog" aria-expanded={shown}
        onClick={() => { setShown(true); void read(); }}>
        <ScrollText size={11} /> Terms
      </button>
      {panel}
    </>
  );
}

/** A part's words with the page's placeholders filled: `{owner}` with the owner as named, else "the task's owner";
 *  `{home_group}` with the task's home group, else "its home group". `{operator}` and `{record_keeping}` come filled
 *  from the route, and are left as they come. */
function fill(words: string, owner?: string, homeGroup?: string): string {
  return words.replaceAll("{owner}", owner || "the task's owner").replaceAll("{home_group}", homeGroup || "its home group");
}

/** A date the texts give as YYYY-MM-DD, as the page writes a day ("10 Oct", with its year when not this year): the day
 *  it names, in no time zone. */
const dated = (iso: string) => day(iso, "UTC") || iso;

/** The text itself, layered over the page below the box it is in: its title and its Close, then the design's parts and
 *  the quotations that every case shows (`case` null) or that `cases` names. */
function Terms({ box, title, text, unreadable, cases, owner, homeGroup, onClose }: {
  box: RefObject<HTMLDivElement | null>; title: string; text: RiskTextView | null; unreadable: boolean;
  cases?: string[]; owner?: string; homeGroup?: string; onClose: () => void;
}) {
  const named = new Set(cases ?? []);
  const applies = (c: string | null) => c === null || named.has(c);
  // a click on the words does nothing to what the text sits in: in a label, it would turn the switch or the choice the
  // label names. Its links and its Close work as ever.
  const keep = (e: SyntheticEvent) => {
    if (!(e.target instanceof Element && e.target.closest("a, button"))) e.preventDefault();
  };
  return (
    <div ref={box} role="dialog" aria-modal="false" aria-label={`Terms: ${title}`} onClick={keep}
      className="absolute left-0 top-full z-40 mt-1 w-[26rem] max-w-[calc(100vw-24px)] rounded-lg border border-line-strong bg-raised text-left text-[12px] font-normal shadow-2xl shadow-black/50">
      <div className="flex items-start gap-2 border-b border-line px-3 py-2">
        <ScrollText size={13} className="mt-0.5 flex-none text-wait" />
        <span className="min-w-0 flex-1 text-[12.5px] font-medium leading-snug text-fg">{title}</span>
        {/* a mouse press here leaves the keyboard where it was: in the form the person is filling in */}
        <button type="button" className="icon-btn !h-6 !w-6 flex-none" aria-label="Close the terms" title="Close (Esc)"
          onMouseDown={(e) => e.preventDefault()} onClick={onClose}><X size={13} /></button>
      </div>
      <div className="max-h-[50vh] space-y-2 overflow-y-auto px-3 py-2 leading-relaxed">
        {!text ? (
          <p className="text-muted">
            {unreadable ? "The terms at this point could not be read just now."
              : <><Loader2 size={12} className="mr-1 inline animate-spin" />Reading the terms at this point…</>}
          </p>
        ) : (
          <>
            {text.design.filter((p) => applies(p.case)).map((p, i) => (
              <div key={`design-${i}`} className="border-l-2 border-line-strong pl-2">
                <div className="text-[10.5px] text-faint">This design's words, not a quotation</div>
                <p className="text-fg-dim">{fill(p.text, owner, homeGroup)}</p>
              </div>
            ))}
            {text.quotations.filter((q) => applies(q.case)).map((q) => <Quotation key={q.id} q={q} />)}
          </>
        )}
      </div>
    </div>
  );
}

/** A section as its source names it: a number or a code as "section D.5", words ("information privacy principle 3",
 *  as the Privacy Act's are named) as they are. */
const sectionWords = (s: string) => (/\s/.test(s.trim()) ? s : `section ${s}`);

/** A clause quoted word for word, with its source, its address, the date the source gives and the date it was read;
 *  one not read yet says so instead of its words. */
function Quotation({ q }: { q: RiskQuotationView }) {
  return (
    <figure className="border-l-2 border-wait/60 pl-2">
      {q.words !== null ? <blockquote className="text-fg">“{q.words}”</blockquote> : <p className="text-wait">{q.unread}</p>}
      <figcaption className="mt-0.5 text-[11px] text-muted">
        {q.source}{q.section ? `, ${sectionWords(q.section)}` : ""}:{" "}
        <a className="break-all text-accent hover:underline" href={q.address} target="_blank" rel="noreferrer noopener">{q.address}</a>
        <span className="block text-faint">
          {q.source_date ?? "The page gives no date"}; {q.read_on ? `read on ${dated(q.read_on)}` : "not read"}
        </span>
      </figcaption>
    </figure>
  );
}
