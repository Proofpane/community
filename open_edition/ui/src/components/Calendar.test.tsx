import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CalendarAnswer, CalendarSchedule } from "../agents";
import { useStore } from "../store";
import { Calendar, dayKey, span, startOf, windowOf } from "./Calendar";
import { forgetPage } from "./RiskTerms";

// The calendar of the agents' triggers: its marks fall on the
// viewer's own day, a firing that waits for a person says so in words, a schedule past the cap is a band, Run now opens
// risk point 7's text as it goes and is offered where the server says its route takes it, an off schedule's switch
// opens that text before it is turned on, and it reads the app's store again every 10 seconds while a run is live and
// every 30 otherwise, while the page is visible, firing nothing as it does, and drawing no answer for a window it no
// longer shows.

const NZ = "Pacific/Auckland";
const utc = (y: number, m: number, d: number, h = 0, min = 0) => Date.UTC(y, m - 1, d, h, min) / 1000;
const TODAY = { y: 2026, m: 11, d: 3 };

const schedule = (over: Partial<CalendarSchedule> = {}): CalendarSchedule => ({
  id: "t1t2t3t4t5t6", agent_id: "a1a2a3a4a5a6", agent: "Triage", kind: "schedule", enabled: true, prompt: "the new issues",
  agent_paused: false, agent_paused_until: null, agent_retired: false, unattended_mode: "dontAsk", max_runs_per_day: 24,
  held_now: null,
  coming: [utc(2026, 11, 2, 23, 30)], capped: false, past_capped: false, last_at: null, last_run_id: null, last_error: null,
  counts: { missed: 0, on_time: 0, late: 0, refused: 0, skipped: 0 }, cron: "30 23 * * *", tz: "UTC", grace_minutes: 30,
  next_at: utc(2026, 11, 2, 23, 30), more_than_a_day_runs: false, actions: ["switch", "change", "run_now"], ...over,
});

const answer = (over: Partial<CalendarAnswer> = {}): CalendarAnswer => ({
  from: utc(2026, 10, 31, 11), to: utc(2026, 11, 30, 11), now: utc(2026, 11, 2, 20), cap: 200, days: 62, live: 0,
  schedules: [schedule()], firings: [], due_next: [], ...over,
});

const RISKS = { texts: [{ point: 7, title: "Work while nobody watches", design: [{ case: null, text: "A run nobody watches acts by itself." }],
                          quotations: [], unread: false, digest: "7".repeat(64) }] };

let calls: { method: string; path: string; query: URLSearchParams }[] = [];
const serve = (got: () => CalendarAnswer) => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const u = new URL(String(input), "http://localhost");
    calls.push({ method: init.method || "GET", path: u.pathname, query: u.searchParams });
    if (u.pathname === "/api/ade/risks") return new Response(JSON.stringify(RISKS), { status: 200 });
    if (u.pathname.endsWith("/run")) return new Response(JSON.stringify({ run_id: "r1r2r3r4r5r6" }), { status: 200 });
    if (u.pathname === "/api/ade/agents/calendar") return new Response(JSON.stringify(got()), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  }));
};
const reads = () => calls.filter((c) => c.path === "/api/ade/agents/calendar");
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

beforeEach(() => { forgetPage(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

describe("the calendar's days", () => {
  it("are the viewer's own: a time late on a UTC day falls on the next day where the viewer is ahead of UTC", () => {
    expect(dayKey(utc(2026, 11, 2, 23, 30), NZ)).toBe("2026-11-03");
    expect(dayKey(utc(2026, 11, 2, 23, 30), "UTC")).toBe("2026-11-02");
    // a day's start in the zone: New Zealand's summer time, 13 hours ahead of UTC
    expect(startOf(TODAY, NZ)).toBe(utc(2026, 11, 2, 11));
    // a month is read from its first day's start to the next month's, in the zone; a week from its Monday
    expect(windowOf("month", TODAY, NZ)).toEqual([utc(2026, 10, 31, 11), utc(2026, 11, 30, 11)]);
    expect(windowOf("week", TODAY, NZ)).toEqual([utc(2026, 11, 1, 11), utc(2026, 11, 8, 11)]);
    expect(windowOf("day", TODAY, NZ)).toEqual([utc(2026, 11, 2, 11), utc(2026, 11, 3, 11)]);
    // the day the clock goes forward: 2026-09-27 is 23 hours long in New Zealand
    expect(windowOf("day", { y: 2026, m: 9, d: 27 }, NZ)).toEqual([utc(2026, 9, 26, 12), utc(2026, 9, 27, 11)]);
    // and behind UTC: a time early on a UTC day falls on the day before in New York, whose 1 November 2026 is 25 hours
    expect(dayKey(utc(2026, 11, 2, 3), "America/New_York")).toBe("2026-11-01");
    expect(windowOf("day", { y: 2026, m: 11, d: 1 }, "America/New_York")).toEqual([utc(2026, 11, 1, 4), utc(2026, 11, 2, 5)]);
    expect(windowOf("week", { y: 2026, m: 11, d: 4 }, "America/New_York")).toEqual([utc(2026, 11, 2, 5), utc(2026, 11, 9, 5)]);
  });

  it("are told in seconds, minutes, hours or days", () => {
    expect([span(45), span(600), span(3 * 3600), span(3 * 86400)]).toEqual(["45 s", "10 min", "3 h", "3 days"]);
  });
});

describe("the calendar", () => {
  it("reads the viewer's month and puts each mark on the viewer's day", async () => {
    serve(() => answer());
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const read = reads()[0];
    expect([Number(read.query.get("from")), Number(read.query.get("to"))]).toEqual([utc(2026, 10, 31, 11), utc(2026, 11, 30, 11)]);
    const third = screen.getByRole("group", { name: "3 Nov" });
    expect(within(third).getByRole("listitem", { name: "Triage (30 23 * * *), 12:30, coming" })).toBeTruthy();
    expect(within(screen.getByRole("group", { name: "2 Nov" })).queryByRole("listitem")).toBeNull();
  });

  it("says a stored time already passed is due now within its grace, and to be recorded missed past it", async () => {
    const due = [{ trigger_id: "t1t2t3t4t5t6", agent_id: "a1a2a3a4a5a6", agent: "Triage", at: utc(2026, 11, 2, 23, 30), held_now: null }];
    serve(() => answer({ now: utc(2026, 11, 2, 23, 40), due_next: due }));
    const shown = render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    expect(within(screen.getByRole("group", { name: "3 Nov" })).getByRole("listitem",
      { name: "Triage (30 23 * * *), 12:30, due now, for the scheduler's next round" })).toBeTruthy();
    shown.unmount();
    serve(() => answer({ now: utc(2026, 11, 3, 1, 20), due_next: due }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const words = "past its grace of 30 min: the scheduler's next round records it missed and runs nothing";
    expect(within(screen.getByRole("group", { name: "3 Nov" })).getByRole("listitem",
      { name: `Triage (30 23 * * *), 12:30, ${words}` })).toBeTruthy();
    // and so in the next 20
    fireEvent.click(screen.getByRole("button", { name: "Next 20" }));
    expect(within(screen.getByRole("list", { name: "The next firings due" })).getByText(words)).toBeTruthy();
  });

  it("says in words that a firing waits for a person, and when its run started, the store keeping no time a wait began", async () => {
    const onAgents = vi.fn();
    serve(() => answer({ now: utc(2026, 11, 3, 1, 20), firings: [{ trigger_id: "t1t2t3t4t5t6", agent_id: "a1a2a3a4a5a6", record: "run", run_id: "r1r2r3r4r5r6",
      at: utc(2026, 11, 3, 1), started_at: utc(2026, 11, 3, 1, 10), due: utc(2026, 11, 3, 1), late_seconds: 0, run_now: false,
      state: "waits for a person", run_state: "waiting", reason: "it asks its owner a question" }] }));
    render(<Calendar onClose={() => {}} onAgents={onAgents} zone={NZ} today={TODAY} />);
    await flush();
    const third = screen.getByRole("group", { name: "3 Nov" });
    expect(within(third).getByRole("listitem",
      { name: "Triage (30 23 * * *), 14:00, waits for a person; its run started 10 min ago, it asks its owner a question" })).toBeTruthy();
    fireEvent.click(within(third).getByRole("button", { name: "Answer it in Agents" }));
    expect(onAgents).toHaveBeenCalledOnce();
  });

  it("draws a missed firing on the day it was due and a held one at its time, each in words", async () => {
    const mark = { trigger_id: "t1t2t3t4t5t6", agent_id: "a1a2a3a4a5a6", record: "trigger" as const, run_id: null,
      started_at: null, late_seconds: null, run_now: false, run_state: null };
    serve(() => answer({ now: utc(2026, 11, 3, 1, 20), firings: [
      { ...mark, state: "missed", at: null, due: utc(2026, 11, 3, 0, 30),
        reason: "missed the firing of 2026-11-03 00:30 UTC: the app or the machine was down" },
      { ...mark, state: "held", at: utc(2026, 11, 3, 1), due: null, reason: "the agent is paused" }] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const third = within(screen.getByRole("group", { name: "3 Nov" }));
    expect(third.getByRole("listitem", { name: "Triage (30 23 * * *), 13:30, missed: the app or the machine was down" })).toBeTruthy();
    expect(third.getByRole("listitem", { name: "Triage (30 23 * * *), 14:00, held, the agent is paused" })).toBeTruthy();
  });

  it("draws a schedule past the cap as a band, and none of its times one by one", async () => {
    const start = utc(2026, 11, 2, 11);
    serve(() => answer({ schedules: [schedule({ id: "b1b2b3b4b5b6", agent: "Busy", cron: "* * * * *", capped: true,
      coming: Array.from({ length: 200 }, (_, i) => start + 60 * i), more_than_a_day_runs: true })] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const band = screen.getByRole("note", { name: "Busy: past the cap" });
    expect(band.textContent).toMatch(
      /more than 200 times in this view: the first 200, from\s+3 Nov, 00:00 to 3 Nov, 03:19, and more after them, listed here as one\s+band/);
    expect(within(screen.getByRole("group", { name: "3 Nov" })).queryByRole("listitem")).toBeNull();
    expect(screen.getByRole("listitem", { name: "Busy's schedule" }).textContent).toMatch(/its line has more times than that in a UTC day/);
  });

  it("opens risk point 7's text as Run now goes, and offers it only where its route takes it", async () => {
    serve(() => answer({ schedules: [schedule(),
      schedule({ id: "o1o2o3o4o5o6", agent: "Nightly", enabled: false, actions: ["switch", "change"], next_at: null, coming: [] }),
      schedule({ id: "h1h2h3h4h5h6", agent: "Hooked", kind: "hook", cron: null, tz: null, next_at: null, coming: [],
                 hook_type: "github", more_than_a_day_runs: null, actions: ["switch", "change"] })] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    expect(within(screen.getByRole("listitem", { name: "Nightly's schedule" })).queryByRole("button", { name: /Run now/ })).toBeNull();
    expect(within(screen.getByRole("listitem", { name: "Hooked's hook" })).queryByRole("button", { name: /Run now/ })).toBeNull();
    expect(screen.queryByRole("dialog", { name: /Work while nobody watches/ })).toBeNull();     // not before
    const card = screen.getByRole("listitem", { name: "Triage's schedule" });
    fireEvent.click(within(card).getByRole("button", { name: /Run now/ }));
    await vi.waitFor(() => expect(within(card).getByRole("dialog", { name: /Work while nobody watches/ })).toBeTruthy());
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/ade/agents/a1a2a3a4a5a6/triggers/t1t2t3t4t5t6/run"]);
    // read again at once after the owner's own change
    await vi.waitFor(() => expect(reads().length).toBe(2));
  });

  it("opens risk point 7's text as the pointer comes to an off schedule's switch, or it takes the keyboard, before it is turned on", async () => {
    const off = { enabled: false, next_at: null, coming: [], actions: ["switch", "change"] as CalendarSchedule["actions"] };
    serve(() => answer({ schedules: [schedule({ id: "o1o2o3o4o5o6", agent: "Nightly", ...off }),
      schedule({ id: "k1k2k3k4k5k6", agent: "Weekly", ...off }), schedule()] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const on = within(screen.getByRole("listitem", { name: "Triage's schedule" }));
    fireEvent.mouseEnter(on.getByText("on"));                // a schedule already on: nothing to turn on
    fireEvent.focus(on.getByRole("checkbox"));
    const nightly = within(screen.getByRole("listitem", { name: "Nightly's schedule" }));
    expect(screen.queryByRole("dialog", { name: /Work while nobody watches/ })).toBeNull();
    fireEvent.mouseEnter(nightly.getByText("on"));
    await vi.waitFor(() => expect(nightly.getByRole("dialog", { name: /Work while nobody watches/ })).toBeTruthy());
    // the keyboard taking an off schedule's switch, the pointer never there
    const weekly = within(screen.getByRole("listitem", { name: "Weekly's schedule" }));
    fireEvent.focus(weekly.getByRole("checkbox"));
    await vi.waitFor(() => expect(weekly.getByRole("dialog", { name: /Work while nobody watches/ })).toBeTruthy());
    // the text read and shown, and still not on the schedule already on
    expect(on.queryByRole("dialog", { name: /Work while nobody watches/ })).toBeNull();
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);                       // shown before anything is sent
    fireEvent.click(nightly.getByRole("checkbox"));
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "PUT").map((c) => c.path)).toEqual(
      ["/api/ade/agents/a1a2a3a4a5a6/triggers/o1o2o3o4o5o6"]));
  });

  it("says when a schedule cannot fire: its agent paused, until a time or until resumed, or its line giving no time", async () => {
    serve(() => answer({ schedules: [
      schedule({ agent: "Paused", agent_paused: true, agent_paused_until: utc(2026, 11, 2, 17), coming: [] }),
      schedule({ id: "p1p2p3p4p5p6", agent: "Held", agent_paused: true, coming: [] }),
      schedule({ id: "n1n2n3n4n5n6", agent: "Never", cron: "0 0 31 2 *", next_at: null, coming: [],
        counts: { missed: 0, on_time: 2, late: 0, refused: 0, skipped: 3 } })],
      due_next: [{ trigger_id: "p1p2p3p4p5p6", agent_id: "a1a2a3a4a5a6", agent: "Held", at: utc(2026, 11, 2, 23, 30),
        held_now: "the agent is paused" }] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    expect(screen.getByRole("listitem", { name: "Paused's schedule" }).textContent).toMatch(
      /Its agent is paused until 3 Nov, 06:00: no time of it comes before then\./);
    expect(screen.getByRole("listitem", { name: "Held's schedule" }).textContent).toMatch(
      /Its agent is paused: no time of it comes until it is resumed\./);
    const never = screen.getByRole("listitem", { name: "Never's schedule" }).textContent;
    expect(never).toMatch(/On, but its line gives no time to come: it cannot fire\./);
    // its own counts since it was made; its skipped runs among the runs kept
    expect(never).toMatch(/2 on time, since it was made/);
    expect(never).toMatch(/3 skipped by its precheck, of its runs kept \(a run that ended over 90 days ago is not kept\)/);
    // and the next 20 say what would hold each now
    fireEvent.click(screen.getByRole("button", { name: "Next 20" }));
    expect(within(screen.getByRole("list", { name: "The next firings due" })).getByText(
      "would be held now: the agent is paused")).toBeTruthy();
  });

  it("says of a schedule whether its line has more times in a UTC day than its agent's day's runs, or that they were not all counted", async () => {
    serve(() => answer({ schedules: [schedule({ agent: "Halves", cron: "*/30 * * * *", more_than_a_day_runs: true }),
      schedule({ id: "d1d2d3d4d5d6", agent: "Dense", cron: "* * * * *", more_than_a_day_runs: null }),
      schedule({ id: "e1e2e3e4e5e6", agent: "Even", more_than_a_day_runs: false })] }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    const words = (agent: string) => screen.getByRole("listitem", { name: `${agent}'s schedule` }).textContent;
    expect(words("Halves")).toMatch(/its line has more times than that in a UTC day of this view, and past that its firings are held/);
    expect(words("Dense")).toMatch(/its times in each UTC day of this view were not all counted/);
    expect(words("Even")).not.toMatch(/more times than that|not all counted/);
    expect(words("Even")).toMatch(/At most 24 of its agent's runs a day, counted by UTC day\./);
  });

  it("draws no answer for a window it no longer shows, however late that answer comes", async () => {
    let release = () => {};
    const late = new Promise<void>((done) => { release = done; });
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
      const u = new URL(String(input), "http://localhost");
      calls.push({ method: init.method || "GET", path: u.pathname, query: u.searchParams });
      const from = Number(u.searchParams.get("from"));
      if (from === utc(2026, 10, 31, 11)) {                  // the month's answer, held back until the week's is drawn
        await late;
        return new Response(JSON.stringify(answer({ schedules: [schedule({ agent: "Monthly" })] })), { status: 200 });
      }
      return new Response(JSON.stringify(answer({ from, to: Number(u.searchParams.get("to")),
        schedules: [schedule({ agent: "Weekly" })] })), { status: 200 });
    }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Week" }));
    await vi.waitFor(() => expect(screen.getByRole("listitem", { name: "Weekly's schedule" })).toBeTruthy());
    release();
    await flush();
    await flush();
    expect(reads().map((c) => Number(c.query.get("from")))).toEqual([utc(2026, 10, 31, 11), utc(2026, 11, 1, 11)]);
    expect(screen.getByRole("listitem", { name: "Weekly's schedule" })).toBeTruthy();
    expect(screen.queryByRole("listitem", { name: "Monthly's schedule" })).toBeNull();
  });

  it("reads again every 10 seconds while a run is live and every 30 otherwise, only while the page shows, firing nothing", async () => {
    vi.useFakeTimers();
    let live = 0;
    serve(() => answer({ live }));
    render(<Calendar onClose={() => {}} zone={NZ} today={TODAY} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(reads().length).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(29_000); });
    expect(reads().length).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(reads().length).toBe(2);
    live = 1;                                                // a run now working: the next read brings it
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(reads().length).toBe(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(reads().length).toBe(4);
    const shown = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(reads().length).toBe(4);
    shown.mockRestore();
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });
});
