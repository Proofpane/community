// The live progress board: what the person's tasks, requirements and
// sessions did (History), what runs now and how far it is (Now), and what waits, in order (Next), read from
// GET /api/ade/board every 3 seconds while the board is open and the page is shown, and not at all while it is hidden.
import { useEffect, useState } from "react";
import { api, type AccountsView } from "./api";
import { OFFLINE_MOST } from "./shellPoll";

/** One thing that happened to a row, in the app's own words. */
export interface BoardEvent { at: number; kind: string; words: string }

/** A row of the board: a durable task, a requirement of the controller's, or a workspace session
 *  no task holds. Times are Unix seconds; a missing one is null. */
export interface BoardRow {
  kind: "task" | "requirement" | "session";
  id: string;
  title: string;
  parent: string | null;
  phase: "history" | "now" | "next";
  state: string;
  order: number | null;
  progress: { done: number; of: number } | null;
  account: { kind: "claude" | "codex"; id: string } | null;
  session: string | null;
  created_at: number | null;
  started_at: number | null;
  ended_at: number | null;
  waits_until: number | null;
  events: BoardEvent[];
  more_events: number;
  /** a task's grant as it stands (the app's progress_board): null for none, and absent
   *  from an older server */
  grant?: BoardGrant | null;
}

/** A task's grant on its row: the version that stands, the first 12 hex digits of its digest, and whether it pins the
 *  task's goal as it stands; each null where every version was withdrawn. */
export interface BoardGrant { version: number | null; digest: string | null; ask_current: boolean | null }

/** What a task's row says of its grant: the version that stands with the head of its digest, and that it pins a goal
 *  the task no longer has; or that no version stands. */
export function grantWords(g: BoardGrant): string {
  if (g.version === null) return "no version of its grant stands";
  return `grant version ${g.version}, ${g.digest ?? ""}` + (g.ask_current === false ? ": it pins an older goal" : "");
}

export interface BoardView { at: number; rows: BoardRow[] }

/** How often the open board asks again while the page is shown (milliseconds). */
export const BOARD_EVERY = 3000;

/** How long the board waits before it asks again: BOARD_EVERY, and after reads that failed in a row, twice as long
 *  after each, from BOARD_EVERY up to the shell's OFFLINE_MOST (shellPoll.ts's pollDelay, at the board's pace). */
export function boardDelay(failures: number): number {
  return failures > 0 ? Math.min(BOARD_EVERY * 2 ** (failures - 1), OFFLINE_MOST) : BOARD_EVERY;
}

export const readBoard = () => api<BoardView>("/api/ade/board");

/** Whether a time on the board can be drawn: a Date takes it. A time past a Date's range (a session's program can write
 *  its record, and a source could send one) cannot be, and is left out rather than throw. */
export function drawable(t: number | null | undefined): t is number {
  return typeof t === "number" && !Number.isNaN(new Date(t * 1000).getTime());
}

/** A row's key on the page: its kind and its id. */
export const rowKey = (r: Pick<BoardRow, "kind" | "id">) => `${r.kind}:${r.id}`;

/** The board's three parts: History and Now as the server sends them (newest change first), Next in its order. */
export function parts(rows: BoardRow[]): Record<BoardRow["phase"], BoardRow[]> {
  const out: Record<BoardRow["phase"], BoardRow[]> = { history: [], now: [], next: [] };
  for (const r of rows) out[r.phase]?.push(r);
  out.next = out.next.map((r, i) => [r, i] as const)
    .sort(([a, i], [b, j]) => (a.order ?? Infinity) - (b.order ?? Infinity) || i - j).map(([r]) => r);
  return out;
}

const same = (a: BoardRow, b: BoardRow) => JSON.stringify(a) === JSON.stringify(b);

/** The board a new read gives, keeping what did not change: where every row is as it was, in the same order, the rows
 *  the page holds, so that no row moves (the list keeps its identity, and the parts are not split again); otherwise
 *  the read's rows, in its order, each that did not change being the very row the page held. Each row is still drawn
 *  again with the read's time, which its times count from. */
export function keepRows(held: BoardView | null, read: BoardView): BoardView {
  if (!held) return read;
  const before = new Map(held.rows.map((r) => [rowKey(r), r]));
  let unchanged = held.rows.length === read.rows.length;
  const rows = read.rows.map((r, i) => {
    const old = before.get(rowKey(r));
    const kept = old && same(old, r) ? old : r;
    if (kept !== held.rows[i]) unchanged = false;
    return kept;
  });
  return unchanged ? (held.at === read.at ? held : { at: read.at, rows: held.rows }) : { at: read.at, rows };
}

/** A durable task's state in words: AgentTask.tsx's STATE_WORDS, kept the same by board.test.ts. */
export const TASK_WORDS: Record<string, string> = {
  ready: "Ready to start", running: "Running", suspended: "Suspended", needs_owner: "Needs you",
  offering: "Being handed to a teammate", handed_over: "With your teammate", returning: "Coming back from your teammate",
  done: "Done", stopped: "Stopped",
};
/** A session's state in words: the list's own (bits.tsx's stateLabel), kept the same by board.test.ts. */
export const SESSION_WORDS: Record<string, string> = {
  starting: "Starting", working: "Working", running: "Running", waiting: "Needs you", idle: "Idle", done: "Done",
  failed: "Failed", stopped: "Stopped",
};

/** A row's state in words: a task's and a session's as the page says them elsewhere; another's (a requirement's) its
 *  name, written as words. */
export function stateWords(r: Pick<BoardRow, "kind" | "state">): string {
  const known = r.kind === "task" ? TASK_WORDS[r.state] : r.kind === "session" ? SESSION_WORDS[r.state] : undefined;
  if (known) return known;
  const words = r.state.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The colour of a row's dot: what needs the person, what runs, what is done, failed, stopped or waits. */
export function stateColor(r: Pick<BoardRow, "phase" | "state">): string {
  // a sub-goal whose own manager needs the person, or is done
  if (r.state === "needs_owner" || r.state === "waiting" || r.state === "manager_needs_you") return "var(--color-wait)";
  if (r.state === "manager_done") return "var(--color-done)";
  if (r.state === "failed") return "var(--color-fail)";
  if (r.phase === "history") return r.state === "done" ? "var(--color-done)" : "var(--color-faint)";
  if (r.phase === "next") return "var(--color-idle)";
  return r.state === "working" || r.state === "running" || r.state === "starting" ? "var(--color-run)" : "var(--color-idle)";
}

/** The account a row runs on: its program, then the account's name as Connections keeps it ("Default" for the
 *  program's own sign-in unnamed, "another account" for one not known here), as the list of sessions names an
 *  account (store.ts's accountNameOf), save that the board names it on every row, with one account too; nothing for
 *  a row on no account. */
export function accountWords(a: BoardRow["account"], accounts?: AccountsView | null): string {
  if (!a) return "";
  const found = accounts?.[a.kind]?.accounts.find((x) => x.id === a.id);
  return `${a.kind === "codex" ? "Codex" : "Claude"} · ${found?.name || (a.id === "default" ? "Default" : "another account")}`;
}

/** Open the board from anywhere on the page (the title bar, the palette, the control view): the shell listens. */
export const OPEN_BOARD = "pp-open-board";
export function openBoard() {
  window.dispatchEvent(new Event(OPEN_BOARD));
}

/** The board while `open`: read at once, then BOARD_EVERY after each answer while the page is shown (boardDelay: longer
 *  after reads that failed in a row), never while it is hidden, and at once when it is shown again, the failures
 *  forgotten; a read under way is never asked again beside it. What it read last stays shown through a read that
 *  fails, whose words are `error` until one gets through. */
export function useBoard(open: boolean): { board: BoardView | null; error: string | null } {
  const [state, setState] = useState<{ board: BoardView | null; error: string | null }>({ board: null, error: null });
  useEffect(() => {
    if (!open) return;
    let alive = true;
    let busy = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const shown = () => document.visibilityState === "visible";
    const tick = async () => {
      clearTimeout(timer);
      if (!alive || busy || !shown()) return;
      busy = true;
      try {
        const read = await readBoard();
        failures = 0;
        if (alive) setState((cur) => ({ board: keepRows(cur.board, read), error: null }));
      } catch (e) {
        failures += 1;
        if (alive) setState((cur) => ({ board: cur.board, error: e instanceof Error ? e.message : String(e) }));
      } finally {
        busy = false;
      }
      if (alive && shown()) timer = setTimeout(() => void tick(), boardDelay(failures));
    };
    const visibility = () => { if (shown()) { failures = 0; void tick(); } else clearTimeout(timer); };
    void tick();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      alive = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [open]);
  return state;
}
