import { act, cleanup, configure, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BOARD_EVERY, type BoardRow, type BoardView } from "../board";
import { useStore } from "../store";
import { useTimeChoice } from "../timeChoice";
import { localFull, localTime } from "../when";
import { Board } from "./Board";
import type { SessionRecord } from "../api";

// The live progress board on the page: its three parts, Next in its order,
// its times in the zone and format kept for it, its reads every 3 seconds while it is open and the page is shown, and
// a row kept where it was when nothing changed.

// a read and its drawing, with real timers, given time on a loaded machine (AgentTask.test.tsx does the same)
configure({ asyncUtilTimeout: 5000 });

const NOW = 1_760_730_000;                   // the board's own time, as the server sends it
const ZONE = "Pacific/Auckland";
const LOCALE = "en-GB";
const SESSION = "0123456789ab";

const row = (over: Partial<BoardRow> = {}): BoardRow => ({
  kind: "task", id: "t-run", title: "Ship the parser", parent: null, phase: "now", state: "running", order: null,
  progress: { done: 2, of: 5 }, account: { kind: "claude", id: "default" }, session: null, created_at: NOW - 3600,
  started_at: NOW - 1800, ended_at: null, waits_until: null, events: [], more_events: 0, ...over,
});

const ROWS: BoardRow[] = [
  row({ id: "t-done", title: "Fix the login", phase: "history", state: "done", progress: { done: 3, of: 3 }, ended_at: NOW - 600,
        events: [{ at: NOW - 900, kind: "task.created", words: "Made, with 3 steps" }, { at: NOW - 600, kind: "task.done", words: "Done: its last step's checkpoint" }],
        more_events: 4 }),
  row(),
  row({ kind: "session", id: SESSION, title: "Look at the logs", state: "waiting", progress: null, session: SESSION, started_at: NOW - 300,
        created_at: NOW - 300, events: [{ at: NOW - 300, kind: "session.started", words: "Started" }] }),
  row({ kind: "requirement", id: "req-1", title: "Second in line", phase: "next", state: "queued", order: 2, progress: null, account: null,
        started_at: null, created_at: NOW - 100 }),
  row({ id: "t-wait", title: "First in line", phase: "next", state: "suspended", order: 1, started_at: null, waits_until: NOW + 12 * 60 }),
];

const choice = { zone: ZONE, locale: LOCALE };
const now = new Date(NOW * 1000);

function serve(next: () => BoardView | Error) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    if (!String(url).endsWith("/api/ade/board")) return new Response("{}", { status: 404 });
    const got = next();
    return got instanceof Error ? new Response(JSON.stringify({ detail: got.message }), { status: 502 })
      : new Response(JSON.stringify(got), { status: 200 });
  }));
  return { asked: () => calls.filter((c) => c.endsWith("/api/ade/board")).length };
}

const part = (name: string) => screen.getByRole("region", { name });
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const hide = (hidden: boolean) => {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue(hidden ? "hidden" : "visible");
  document.dispatchEvent(new Event("visibilitychange"));
};

beforeEach(() => {
  useTimeChoice.setState({ zone: ZONE, locale: LOCALE });
  useStore.setState({ ws: { enabled: true, allowed: true, running: true, made: true } as never,
    records: [{ id: SESSION, closed: false } as unknown as SessionRecord] });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  useStore.setState(useStore.getInitialState());
  useTimeChoice.setState({ zone: null, locale: null });
});

describe("the progress board", () => {
  it("shows History, Now and Next, each row with its title, state, account, progress and times, Next in its order", async () => {
    serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Ship the parser");
    expect(within(part("History")).getByText("Fix the login")).toBeTruthy();
    expect(within(part("History")).getByText("Done")).toBeTruthy();
    const nowPart = within(part("Now"));
    expect(nowPart.getByText("Running")).toBeTruthy();
    expect(nowPart.getAllByText("Claude · Default")).toHaveLength(2);       // the task's and the session's
    expect(nowPart.getByLabelText("2 of 5 steps done").textContent).toBe("2 of 5 steps");
    expect(nowPart.getByText("Needs you")).toBeTruthy();                 // the session waits for its person
    const next = part("Next");
    expect(within(next).getAllByText(/ in line$/).map((e) => e.textContent)).toEqual(["First in line", "Second in line"]);
    expect(within(next).getByLabelText("Number 1 of next").textContent).toBe("1.");
    // when a row in Next goes on, as its time, written in the zone and format chosen, and how long until it
    const reset = within(next).getByText(/^goes on/);
    expect(reset.textContent).toBe(`goes on ${localTime(NOW + 720, choice, now)} · in 12 min`);
    expect(reset.querySelector("time")!.getAttribute("title")).toBe(localFull(NOW + 720, choice));
    const started = within(part("Now")).getAllByText(/^started/)[0];
    expect(started.textContent).toBe(`started ${localTime(NOW - 1800, choice, now)}`);
    expect(within(part("History")).getByText(/^ended/).textContent).toBe(`ended ${localTime(NOW - 600, choice, now)}`);
  });

  it("says on a task's row the version of its grant that stands, the head of its digest, and an older goal it pins", async () => {
    // the app's progress_board: a version's line in the events drops off after 50 newer
    // ones, so the row carries the grant itself
    const g = (grant: unknown) => ({ grant }) as Partial<BoardRow>;
    const rows = [
      row({ id: "t-1", title: "Ship it", ...g({ version: 2, digest: "0123456789ab", ask_current: true }) }),
      row({ id: "t-2", title: "Older goal", ...g({ version: 1, digest: "ba9876543210", ask_current: false }) }),
      row({ id: "t-3", title: "All withdrawn", ...g({ version: null, digest: null, ask_current: null }) }),
      row({ id: "t-4", title: "Plain task", ...g(null) }),
    ];
    serve(() => ({ at: NOW, rows }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Ship it");
    const of = (title: string) => screen.getByText(title).closest("li")!.textContent ?? "";
    expect(of("Ship it")).toContain("grant version 2, 0123456789ab");
    expect(of("Ship it")).not.toContain("older goal");
    expect(of("Older goal")).toContain("grant version 1, ba9876543210: it pins an older goal");
    expect(of("All withdrawn")).toContain("no version of its grant stands");
    expect(of("Plain task")).not.toMatch(/grant/);
  });

  it("labels a requirement row 'Sub-goal' on the board (the page's word), never the app's own 'Requirement'", async () => {
    serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Ship the parser");
    expect(within(part("Next")).getByText("Sub-goal")).toBeTruthy();   // the requirement row, in the page's word
    expect(within(part("Now")).getByText("Session")).toBeTruthy();
    expect(screen.getAllByText("Task").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Requirement")).toBeNull();              // the app's own word never reaches the board
  });

  it("asks every 3 seconds while it is open and the page is shown, never while hidden, at once when shown again, and no more once closed", async () => {
    expect(BOARD_EVERY).toBe(3000);                                   // 3 seconds
    vi.useFakeTimers();
    const server = serve(() => ({ at: NOW, rows: ROWS }));
    const { unmount } = render(<Board onClose={() => {}} />);
    await pass(0);
    expect(server.asked()).toBe(1);
    await pass(BOARD_EVERY - 100);
    expect(server.asked()).toBe(1);
    await pass(100);
    expect(server.asked()).toBe(2);
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(3);
    hide(true);
    await pass(5 * BOARD_EVERY);
    expect(server.asked()).toBe(3);                                    // hidden: not once
    hide(false);
    await pass(0);
    expect(server.asked()).toBe(4);                                    // shown again: at once
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(5);
    unmount();
    await pass(5 * BOARD_EVERY);
    expect(server.asked()).toBe(5);
  });

  it("asks nothing while the page is hidden when it opens, until it is shown", async () => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const server = serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await pass(3 * BOARD_EVERY);
    expect(server.asked()).toBe(0);
    hide(false);
    await pass(0);
    expect(server.asked()).toBe(1);
  });

  it("keeps a row where it was, and what was opened of it, when a read changes nothing", async () => {
    vi.useFakeTimers();
    let at = NOW;
    serve(() => ({ at: (at += 3), rows: JSON.parse(JSON.stringify(ROWS)) }));
    render(<Board onClose={() => {}} />);
    await pass(0);
    const before = screen.getByText("Fix the login").closest("li")!;
    fireEvent.click(screen.getByRole("button", { name: "Unfold what happened to Fix the login" }));
    const order = () => Array.from(document.querySelectorAll("[data-row]")).map((e) => e.getAttribute("data-row"));
    const first = order();
    await pass(BOARD_EVERY);
    await pass(BOARD_EVERY);
    // the same element in the same place, still open (React's keys keep the element whatever keepRows does: that the
    // rows themselves are kept is board.test.ts's)
    expect(screen.getByText("Fix the login").closest("li")).toBe(before);
    expect(order()).toEqual(first);
    expect(screen.getByRole("list", { name: "What happened to Fix the login" })).toBeTruthy();
  });

  it("opens a row's events, oldest first with the earlier ones counted, and opens a session the page lists", async () => {
    serve(() => ({ at: NOW, rows: ROWS }));
    const opened: string[] = [];
    render(<Board onClose={() => {}} onOpenSession={(id) => opened.push(id)} />);
    await screen.findByText("Fix the login");
    const history = within(part("History"));
    expect(history.getByText("Done: its last step's checkpoint")).toBeTruthy();       // the newest, folded
    fireEvent.click(history.getByRole("button", { name: "Unfold what happened to Fix the login" }));
    const events = within(screen.getByRole("list", { name: "What happened to Fix the login" }));
    expect(events.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "and 4 earlier", `${localTime(NOW - 900, choice, now)}Made, with 3 steps`,
      `${localTime(NOW - 600, choice, now)}Done: its last step's checkpoint`]);
    fireEvent.click(screen.getByRole("button", { name: "Open the session Look at the logs" }));
    expect(opened).toEqual([SESSION]);
    // a task names no session, and one the page does not list cannot be opened from here
    expect(screen.queryByRole("button", { name: "Open the session Ship the parser" })).toBeNull();
  });

  it("offers no session to open that the page does not list", async () => {
    useStore.setState({ records: [] });
    serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} onOpenSession={() => {}} />);
    await screen.findByText("Look at the logs");
    expect(screen.queryByRole("button", { name: "Open the session Look at the logs" })).toBeNull();
  });

  it("keeps showing the board it read last through a read that fails, and says so", async () => {
    vi.useFakeTimers();
    let fail = false;
    serve(() => (fail ? new Error("The workspace answered 502") : { at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await pass(0);
    fail = true;
    await pass(BOARD_EVERY);
    expect(screen.getByText("Ship the parser")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Showing the board as last read: The workspace answered 502");
    fail = false;
    await pass(BOARD_EVERY);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("writes its times again in the zone and format chosen under Times, which keeps them on this device", async () => {
    serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Ship the parser");
    fireEvent.click(screen.getByRole("button", { name: /Times/ }));
    const dialog = within(screen.getByRole("dialog", { name: "Times on the board" }));
    fireEvent.change(dialog.getByLabelText("Time zone"), { target: { value: "America/New_York" } });
    fireEvent.change(dialog.getByLabelText("Format"), { target: { value: "en-US" } });
    const other = { zone: "America/New_York", locale: "en-US" };
    expect(within(part("Now")).getAllByText(/^started/)[0].textContent).toBe(`started ${localTime(NOW - 1800, other, now)}`);
    expect(localTime(NOW - 1800, other, now)).not.toBe(localTime(NOW - 1800, choice, now));
    expect(JSON.parse(localStorage.getItem("pp-board-times")!)).toEqual(other);
    fireEvent.change(dialog.getByLabelText("Time zone"), { target: { value: "" } });
    expect(useTimeChoice.getState().zone).toBeNull();
  });

  it("says the workspace's sessions are on it while the workspace runs", async () => {
    useStore.setState({ ws: { enabled: true, allowed: true, running: false, made: true } as never });
    serve(() => ({ at: NOW, rows: [] }));
    render(<Board onClose={() => {}} />);
    expect(await screen.findByText("The workspace's sessions are on the board while it runs.")).toBeTruthy();
    expect(await screen.findByText("Nothing runs or waits for you.")).toBeTruthy();
  });

  // ---- the Times dialog, the header's counts, the sessions named, and the reads ----

  it("closes the Times dialog alone when its backdrop is pressed, the board staying open", async () => {
    serve(() => ({ at: NOW, rows: ROWS }));
    const closed: string[] = [];
    render(<Board onClose={() => closed.push("board")} />);
    await screen.findByText("Ship the parser");
    fireEvent.click(screen.getByRole("button", { name: /Times/ }));
    const dialog = screen.getByRole("dialog", { name: "Times on the board" });
    fireEvent.mouseDown(dialog.parentElement!);                         // the dialog's own backdrop
    expect(screen.queryByRole("dialog", { name: "Times on the board" })).toBeNull();
    expect(closed).toEqual([]);
    expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy();
  });

  it("counts its parts in its header: what runs now, what is next, what ended", async () => {
    // three in Now, two in Next and one ended: no two counts alike, so none can stand in for another
    serve(() => ({ at: NOW, rows: [...ROWS, row({ id: "t-also", title: "Also running" })] }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Ship the parser");
    expect(screen.getByText(/ now · /).textContent).toBe("3 now · 2 next · 1 ended");
  });

  it("names the session a task or a requirement runs in, by its title where the page lists it, else by its id", async () => {
    useStore.setState({ records: [
      { id: SESSION, closed: false, title: "Look at the logs", task: "x", program: "claude" } as unknown as SessionRecord] });
    const other = "ba9876543210";
    serve(() => ({ at: NOW, rows: [
      row({ kind: "requirement", id: "req-in", title: "Runs in a listed session", session: SESSION }),
      row({ id: "t-in", title: "Runs in another", session: other }),
      row({ kind: "session", id: SESSION, title: "Look at the logs", session: SESSION, phase: "history", state: "done" }),
    ] }));
    render(<Board onClose={() => {}} onOpenSession={() => {}} />);
    await screen.findByText("Runs in a listed session");
    const nowPart = within(part("Now"));
    expect(nowPart.getByText("in session Look at the logs")).toBeTruthy();
    expect(nowPart.getByText(`in session ${other}`)).toBeTruthy();
    // a session's own row is the session: it names none, and opens it
    expect(within(part("History")).queryByText(/^in session/)).toBeNull();
    expect(within(part("History")).getByRole("button", { name: "Open the session Look at the logs" })).toBeTruthy();
  });

  it("offers no Open for a session the page lists as closed", async () => {
    useStore.setState({ records: [{ id: SESSION, closed: true } as unknown as SessionRecord] });
    serve(() => ({ at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} onOpenSession={() => {}} />);
    await screen.findByText("Look at the logs");
    expect(screen.queryByRole("button", { name: "Open the session Look at the logs" })).toBeNull();
  });

  it("says a first read that failed alone, with no empty parts beside it", async () => {
    serve(() => new Error("The workspace answered 502"));
    render(<Board onClose={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toBe("The workspace answered 502");
    expect(screen.queryByRole("region", { name: "Now" })).toBeNull();
    expect(screen.queryByText("Nothing runs or waits for you.")).toBeNull();
  });

  it("asks no second time while a read is under way, though the page is shown again meanwhile", async () => {
    vi.useFakeTimers();
    let release: (() => void) | undefined;
    let asked = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      asked += 1;
      await new Promise<void>((done) => { release = done; });
      return new Response(JSON.stringify({ at: NOW, rows: ROWS }), { status: 200 });
    }));
    render(<Board onClose={() => {}} />);
    await pass(0);
    expect(asked).toBe(1);
    hide(true);
    hide(false);                                                       // shown again while the first read waits
    await pass(2 * BOARD_EVERY);
    expect(asked).toBe(1);
    await act(async () => { release!(); });
    await pass(BOARD_EVERY);
    expect(asked).toBe(2);
  });

  it("waits twice as long after each read that failed in a row, and asks at once, afresh, when shown again", async () => {
    vi.useFakeTimers();
    const server = serve(() => new Error("The workspace answered 502"));
    render(<Board onClose={() => {}} />);
    await pass(0);
    expect(server.asked()).toBe(1);
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(2);                                    // one failure: BOARD_EVERY
    await pass(2 * BOARD_EVERY - 100);
    expect(server.asked()).toBe(2);
    await pass(100);
    expect(server.asked()).toBe(3);                                    // two: twice that
    await pass(4 * BOARD_EVERY);
    expect(server.asked()).toBe(4);                                    // three: four times
    hide(true);
    hide(false);
    await pass(0);
    expect(server.asked()).toBe(5);                                    // shown again: at once
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(6);                                    // and its failures forgotten
  });

  // ---- times no Date can hold, when Next goes on, and the session a row opens ----

  it("draws a row whose times no Date can hold, those times left out, its events still opened", async () => {
    const far = 9_000_000_000_000;                                  // past a Date's range: new Date(far * 1000) is invalid
    serve(() => ({ at: NOW, rows: [
      row({ id: "t-far", title: "From far ahead", started_at: far, created_at: far,
            events: [{ at: far, kind: "session.landed", words: "Landed far ahead" }] }),
      row({ id: "t-far-end", title: "Ended far ahead", phase: "history", state: "done", ended_at: far, started_at: null,
            created_at: null }),
      row({ id: "t-far-wait", title: "Waits far ahead", phase: "next", state: "suspended", order: 1, waits_until: far,
            started_at: null, created_at: null }),
    ] }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("From far ahead");
    expect(screen.getByText("Ended far ahead")).toBeTruthy();
    expect(screen.getByText("Waits far ahead")).toBeTruthy();
    expect(screen.queryByText(/^(started|made|ended|goes on|due since|Continue from)/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Unfold what happened to From far ahead" }));
    expect(within(screen.getByRole("list", { name: "What happened to From far ahead" })).getByText("Landed far ahead"))
      .toBeTruthy();
  });

  it("says when a row in Next goes on, one whose time has come as due, and a Now row's time as Continue's", async () => {
    serve(() => ({ at: NOW, rows: [
      row({ id: "t-due", title: "Due already", phase: "next", state: "suspended", order: 1, waits_until: NOW - 60 }),
      row({ id: "t-own", title: "Its Auto-resume off", state: "suspended", waits_until: NOW + 3600 }),
      row({ kind: "requirement", id: "req-own", title: "A requirement held", state: "held", waits_until: NOW + 3600 }),
      row({ id: "t-ended", title: "Ended with a time", phase: "history", state: "stopped", waits_until: NOW + 3600 }),
    ] }));
    render(<Board onClose={() => {}} />);
    await screen.findByText("Due already");
    expect(within(part("Next")).getByText(/^due since/).textContent).toBe(`due since ${localTime(NOW - 60, choice, now)}`);
    const own = within(part("Now")).getAllByText(/^Continue from/).map((e) => e.textContent);
    expect(own).toEqual([`Continue from ${localTime(NOW + 3600, choice, now)} · in 1 h · Auto-resume is off`,
      `Continue from ${localTime(NOW + 3600, choice, now)} · in 1 h`]);
    expect(within(part("History")).queryByText(/^(goes on|due since|Continue from)/)).toBeNull();
    expect(screen.queryByText(/^resets/)).toBeNull();
  });

  it("names the session a row opens by that session's title, not the row's", async () => {
    useStore.setState({ records: [
      { id: SESSION, closed: false, title: "Look at the logs", task: "x", program: "claude" } as unknown as SessionRecord] });
    const opened: string[] = [];
    serve(() => ({ at: NOW, rows: [row({ kind: "requirement", id: "req-in", title: "Runs in it", session: SESSION })] }));
    render(<Board onClose={() => {}} onOpenSession={(id) => opened.push(id)} />);
    await screen.findByText("Runs in it");
    expect(screen.queryByRole("button", { name: "Open the session Runs in it" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open the session Look at the logs" }));
    expect(opened).toEqual([SESSION]);
  });

  // a listed session with no name of its own is named as the list names it
  it("names a session with no name of its own by its task's first line, as the list does", async () => {
    useStore.setState({ records: [
      { id: SESSION, closed: false, title: null, task: "  Fix the parser  \nand its tests", program: "claude" } as unknown as SessionRecord] });
    serve(() => ({ at: NOW, rows: [row({ kind: "requirement", id: "req-in", title: "Runs in it", session: SESSION })] }));
    render(<Board onClose={() => {}} onOpenSession={() => {}} />);
    await screen.findByText("Runs in it");
    expect(screen.getByText("in session Fix the parser")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open the session Fix the parser" })).toBeTruthy();
  });

  it("forgets its failures once a read gets through: the next read is BOARD_EVERY later", async () => {
    vi.useFakeTimers();
    let fail = true;
    const server = serve(() => (fail ? new Error("The workspace answered 502") : { at: NOW, rows: ROWS }));
    render(<Board onClose={() => {}} />);
    await pass(0);
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(2);                                    // two failures in a row
    fail = false;
    await pass(2 * BOARD_EVERY);
    expect(server.asked()).toBe(3);                                    // this one gets through
    await pass(BOARD_EVERY);
    expect(server.asked()).toBe(4);                                    // BOARD_EVERY after it, not twice that
  });
});
