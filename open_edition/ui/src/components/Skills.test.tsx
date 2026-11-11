import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browser } from "../shellTestkit";
import { useStore } from "../store";
import { proposalFor, SkillsPanel } from "./Skills";
import type { Candidates, SkillLoop, SkillRow } from "../skills";

// The Skills view (Skills.tsx), read against the app's skill routes as proofpane_open/ade/skill_routes.py gives
// them, on fake timers. Opening it draws what the app holds and asks the app to read the workspace (POST /read), then
// draws again; it does the same every 15 seconds while the page is visible. A server of the test's own answers.

type Dict = Record<string, unknown>;
let srv: {
  view: Dict;
  detail: Dict | null;
  detailStatus: number;
  read: Dict;
  failing: { status: number; detail: string } | null;
  hold: Promise<void> | null;                 // the next reads of the view wait for it
  calls: { method: string; path: string; query: string; body?: Dict }[];
  // the business skills' loop's routes, answered only where the test gives a loop (a 404, as an older server's,
  // otherwise)
  loop: Dict | null;
  record: Dict;
  cands: Record<string, Dict>;
  restored: Dict | null;                      // the record as it stands once the owner restores it
  views: Record<string, Dict>;                // a proposal's own view, by its id (else the loop's row)
};
let visible = true;

const ENDED = { done: 1, failed: 0, stopped: 0, open: 0, unknown: 0 };
const ROW = (over: Dict = {}): Dict => ({
  kind: "core", skill: "proofpane-core:verify", repo: null, calls: 3, failed: 1, typed: 1, sessions: 2, repos: 2, tasks: 1,
  requirements: 1, versions: 2, latest_version: "1.0.0+" + "a".repeat(64), first_at: 1_790_000_000, last_at: 1_790_000_100,
  ended: ENDED, ...over,
});
const READS = (over: Dict = {}): Dict => ({ at: 1_790_000_500, behind_at: null, error: null, error_at: null, unserved_at: null,
  missed: 0, dropped: 0, refused: 0, trimmed: 0, since_at: 1_790_000_000, ...over });
const DAYS = (n: number) => Array.from({ length: n }, (_, i) => ({ day: `2026-10-${String(i + 1).padStart(2, "0")}`,
  core: i === 2 ? 3 : 0, business: i === 2 ? 1 : 0, other: 0 }));
const VIEW = (over: Dict = {}): Dict => ({
  days: 30, from_day: "2026-10-01", to_day: "2026-10-30", now: 1_790_000_600, read: READS(),
  totals: { calls: 8, failed: 1, typed: 1, skills: 4, sessions: 3, first_at: 1, last_at: 2 }, by_day: DAYS(7),
  skills: [ROW(), ROW({ kind: "business", skill: "deploy", repo: "alpha", calls: 2, failed: 0, typed: 0, sessions: 1, repos: 1,
    versions: 1, latest_version: "0123456789abcdef0123456789abcdef01234567", ended: { done: 0, failed: 1, stopped: 0, open: 1, unknown: 0 } }),
    ROW({ kind: "business", skill: "deploy", repo: "beta", calls: 1, failed: 0, typed: 0, latest_version: "uncommitted", versions: 1 }),
    ROW({ kind: "business", skill: "lonely", repo: null, calls: 1, failed: 0, typed: 0, latest_version: "unknown", versions: 1 }),
    ROW({ kind: "other", skill: "plain", calls: 1, failed: 0, typed: 0, sessions: 1, repos: 0, tasks: 0, requirements: 0, versions: 0,
      latest_version: null, ended: { done: 0, failed: 0, stopped: 0, open: 0, unknown: 0 } })],
  more: 0, ...over,
});
const DETAIL = (over: Dict = {}): Dict => ({
  kind: "core", skill: "proofpane-core:verify", repo: null, days: 30, from_day: "2026-10-01", to_day: "2026-10-30", calls: 3, failed: 1, typed: 1,
  first_at: 1_790_000_000, last_at: 1_790_000_100, by_day: [{ day: "2026-10-03", calls: 3, failed: 1 }, { day: "2026-10-04", calls: 0, failed: 0 }],
  versions: [{ version: "1.0.0+" + "b".repeat(64), calls: 2, first_at: 1_790_000_050, last_at: 1_790_000_100 },
    { version: "1.0.0+" + "a".repeat(64), calls: 1, first_at: 1_790_000_000, last_at: 1_790_000_000 }],
  repos: [{ repo: "alpha", calls: 2 }, { repo: null, calls: 1 }], sessions: [{ session: "a".repeat(12), calls: 3, first_at: 1, last_at: 2 }],
  tasks: [{ task: "t1", calls: 2, last_at: 2, state: "done", requirement: "r1", ended_at: 1_790_000_200 },
    { task: "t2", calls: 1, last_at: 2, state: null, requirement: null, ended_at: null }],
  requirements: [{ requirement: "r1", title: "Fix the login", calls: 2, tasks: 1 }], ended: ENDED, ...over,
});

function serve() {
  srv = { view: VIEW(), detail: DETAIL(), detailStatus: 200, read: { state: "read", taken: 2 }, failing: null, hold: null, calls: [],
    loop: null, record: RECORD(), cands: {}, restored: null, views: {} };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const u = new URL(String(input), "http://localhost");
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) as Dict : undefined;
    srv.calls.push({ method, path: u.pathname, query: u.search, ...(body ? { body } : {}) });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    if (srv.failing) return json({ detail: srv.failing.detail }, srv.failing.status);
    if (srv.loop && u.pathname.startsWith("/api/ade/skill-improve")) return loopRoute(method, u.pathname, body ?? {}, json);
    if (srv.hold && u.pathname === "/api/ade/skills" && method === "GET") await srv.hold;
    if (u.pathname === "/api/ade/skills" && method === "GET") return json({ ...srv.view, days: Number(u.searchParams.get("days")) });
    if (u.pathname === "/api/ade/skills/read" && method === "POST") return json(srv.read);
    if (u.pathname === "/api/ade/skills/detail") return srv.detailStatus === 200 ? json(srv.detail) : json({ detail: "No calls of that skill in this window." }, srv.detailStatus);
    return json({ detail: `no route for ${method} ${u.pathname}` }, 404);
  }));
}

// what the fake server answers on the loop's routes (proofpane_open/ade/skill_improve_routes.py)
const SWITCH = (over: Dict = {}): Dict => ({ repo: "alpha", on: true, account: null, model: "opus", url: null, branch: "refs/heads/main",
  changed_at: 1_790_000_000, reviewed: {}, ...over });
const COUNTS = (calls: number, failed: number): Dict => ({ calls, failed, typed: 0, tasks_done: 1, tasks_failed: failed ? 1 : 0, tasks_stopped: 0,
  tasks_open: 0, tasks_unknown: 0, tasks_checks_failed: 0 });
const PROPOSAL = (over: Dict = {}): Dict => ({
  id: "p1p2p3p4p5p6", repo: "alpha", folder: "deploy", path: ".claude/skills/deploy/SKILL.md", kind: "change", branch: "refs/heads/main",
  base: "a".repeat(40), head: "b".repeat(40), heads: [], current_version: "c".repeat(40), before_version: null, fresh: true,
  hypothesis: "Naming the target first stops the wrong deploys.", counts: { whole: COUNTS(12, 3), current: COUNTS(12, 3), before: null },
  state: "open", reason: null, pr_url: null, found: null, rolls_back: null, ended: null, created_at: 1_790_000_000,
  decided_at: 1_790_000_100, merged_at: null, expires_at: 1_791_000_000,
  notes: { keys: [], builds: [{ claude: "/usr/bin/claude", version: "2.1.0", newer: false }], newer: false, linked: true, url: null,
    critic: { specific: true, why: null } },
  diff: ["@@ -1 +1 @@", "-Deploy it.", "+Name the target, then deploy it."], ways: ["merge", "remake", "reject"], bars_now: null, ...over,
});
const LOOP = (over: Dict = {}): Dict => ({
  proposals: [PROPOSAL()],
  budget: { sessions: 3, usd: 1.5, max_sessions: 40, max_usd: 20, spent: false,
    by_kind: { review: 1, critic: 0, replay: 0, skill_review: 1, skill_critic: 1 } },
  switches: [SWITCH(), SWITCH({ repo: "beta", on: false, model: null, branch: null, changed_at: null })],
  copy: { generation: 3, read_at: 1_790_000_500 }, ...over,
});
const RECORD = (over: Dict = {}): Dict => ({ running: true, copy: { generation: 3, read_at: 1_790_000_500 },
  record: { state: "record", held: null, word: true, generation: 3, refused: [], error: null }, ...over });

function loopRoute(method: string, path: string, body: Dict, json: (data: unknown, status?: number) => Response): Response {
  const loop = srv.loop as Dict;
  const repo = path.match(/^\/api\/ade\/skill-improve\/repos\/([^/]+)\/(switch|candidates)$/);
  const act = path.match(/^\/api\/ade\/skill-improve\/proposals\/([^/]+)\/([a-z-]+)$/);
  if (path === "/api/ade/skill-improve" && method === "GET") return json(loop);
  if (path === "/api/ade/skill-improve/record" && method === "GET") return json(srv.record);
  if (path === "/api/ade/skill-improve/record/restore" && method === "POST") {
    if (srv.restored) srv.record = srv.restored;
    return json({ state: "record", generation: 4 });
  }
  if (repo && repo[2] === "candidates" && method === "GET") {
    return json(srv.cands[decodeURIComponent(repo[1])] ?? { ok: false, why: "its switch is off", candidates: [], not: {} });
  }
  if (repo && repo[2] === "switch" && method === "PUT") {
    const name = decodeURIComponent(repo[1]);
    const turned = SWITCH({ repo: name, on: body.on, account: body.account ?? null, model: body.model ?? null, changed_at: 1_790_001_000 });
    loop.switches = [...(loop.switches as Dict[]).filter((x) => x.repo !== name), turned];
    return json(turned);
  }
  if (act && method === "POST") return json(PROPOSAL({ id: act[1], state: act[2] === "reject" ? "rejected" : "merged" }));
  const one = path.match(/^\/api\/ade\/skill-improve\/proposals\/([^/]+)$/);
  if (one && method === "GET") {
    const id = decodeURIComponent(one[1]);
    return json(srv.views[id] ?? (loop.proposals as Dict[]).find((x) => x.id === id) ?? PROPOSAL({ id }));
  }
  return json({ detail: `no route for ${method} ${path}` }, 404);
}

beforeEach(() => {
  vi.useFakeTimers();
  visible = true;
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (visible ? "visible" : "hidden") });
  localStorage.setItem("pp-open-token", "test-token.sig");
  browser();
  serve();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const show = async (on: boolean) => { visible = on; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); }); };
const paths = () => srv.calls.map((c) => `${c.method} ${c.path}${c.query}`);
const region = () => screen.getByRole("region", { name: "Skills" });
const group = (name: string) => within(region()).getByRole("region", { name });

async function mount(onClose: () => void = () => undefined) {
  render(<SkillsPanel onClose={onClose} />);
  await pass();
}

describe("opening the view", () => {
  it("draws what the app holds at once, asks the app to read the workspace, and draws again", async () => {
    await mount();
    // the business skills' loop is read with the view's first drawing (here a server with no such route: nothing of
    // it drawn, and the record, which only the workspace holds, not asked for)
    expect(paths()).toEqual(["GET /api/ade/skills?days=30", "GET /api/ade/skill-improve", "POST /api/ade/skills/read", "GET /api/ade/skills?days=30"]);
    expect(region().textContent).toContain("Skills called");
  });

  it("shows a spinner until the first answer", async () => {
    render(<SkillsPanel onClose={() => undefined} />);
    expect(screen.getByLabelText("Reading the skills")).toBeTruthy();
    await pass();
    expect(screen.queryByLabelText("Reading the skills")).toBeNull();
  });

  it("reads again every 15 seconds while the page is visible, and not while it is hidden", async () => {
    await mount();
    const n = srv.calls.length;
    await pass(14_000);
    expect(srv.calls.length).toBe(n);
    await pass(1_500);
    expect(srv.calls.length).toBe(n + 4);
    await show(false);
    await pass(60_000);
    expect(srv.calls.length).toBe(n + 4);
    await show(true);
    expect(srv.calls.length).toBe(n + 8);
  });

  it("asks again at once when the owner presses the read button, and says it is reading meanwhile", async () => {
    await mount();
    const n = srv.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Read the workspace again" }));
    await pass();
    expect(srv.calls.length).toBe(n + 4);
  });

  it("closes", async () => {
    const closed = vi.fn();
    await mount(closed);
    fireEvent.click(screen.getByRole("button", { name: "Close the skills view" }));
    expect(closed).toHaveBeenCalledTimes(1);
  });
});

describe("the skills it draws", () => {
  it("groups them as core, business by repository and other, each with its calls, where they were made, its version and how its tasks ended", async () => {
    await mount();
    const regions = within(region()).getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(regions).toEqual(["Core skills", "Business skills in alpha", "Business skills in beta", "Business skills in no repository", "Other skills"]);
    const core = within(group("Core skills")).getByRole("listitem", { name: "proofpane-core:verify" });
    expect(core.textContent).toContain("3 calls (1 failed, 1 typed by a person)");
    expect(core.textContent).toContain("called in 2 sessions · 2 repositories · 1 task · 1 requirement");
    expect(core.textContent).toContain("version: 1.0.0+aaaaaaaaaaaa (the latest of 2)");
    expect(core.textContent).toContain("tasks that called it: 1 done");
    const alpha = within(group("Business skills in alpha")).getByRole("listitem", { name: "deploy" });
    expect(alpha.textContent).toContain("2 calls");
    expect(alpha.textContent).toContain("called in 1 session · 1 task · 1 requirement");
    expect(alpha.textContent).toContain("version: 0123456789ab");
    expect(alpha.textContent).toContain("tasks that called it: 1 failed, 1 still open");
    expect(within(group("Business skills in beta")).getByRole("listitem", { name: "deploy" }).textContent).toContain("version: uncommitted changes in its folder");
    expect(within(group("Business skills in no repository")).getByRole("listitem", { name: "lonely" }).textContent).toContain("version: not known");
    expect(group("Other skills").textContent).toContain("neither a core one nor a repository's own: another plugin's, an MCP server's, a bundled one, a person's or an organisation's, a repository's own that one of those shadows or that lies in a folder inside the repository, or a command file of the repository's called as a skill");
    expect(group("Other skills").textContent).not.toContain("without its proofpane-core: prefix");      // a bare core name is the core skill's
    expect(group("Core skills").textContent).toContain("given to the claude terminals and the owner's chats of a paired workspace");
    expect(group("Business skills in alpha").textContent).toContain("skills of this repository's .claude/skills that were called in these days");
    const plain = within(group("Other skills")).getByRole("listitem", { name: "plain" });
    expect(plain.textContent).toContain("version: none: only a repository's own skills and the core skills have one");
    expect(plain.textContent).toContain("tasks that called it: none: no Manager task called it");
  });

  it("says the calls are counted by UTC day, over the window the owner chose", async () => {
    await mount();
    expect(region().textContent).toContain("Counted by UTC day, from 2026-10-01 to 2026-10-30");
    expect(region().textContent).toContain("In these 30 days: 8 calls of 4 skills in 3 sessions, 1 of them failed, 1 typed by a person.");
    srv.view = VIEW({ from_day: "2026-10-24", to_day: "2026-10-30", totals: { calls: 1, failed: 0, typed: 0, skills: 1, sessions: 1, first_at: 1, last_at: 2 } });
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await pass();
    expect(paths().at(-1)).toBe("GET /api/ade/skills?days=7");
    expect(region().textContent).toContain("Counted by UTC day, from 2026-10-24 to 2026-10-30");
    expect(region().textContent).toContain("In these 7 days: 1 call of 1 skill in 1 session.");
    expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "30 days" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("names the window of the numbers it holds, not the one just chosen, until the answer for it comes", async () => {
    await mount();
    expect(region().textContent).toContain("In these 30 days: 8 calls");
    let release: () => void = () => undefined;
    srv.hold = new Promise<void>((resolve) => { release = resolve; });
    srv.view = VIEW({ from_day: "2026-10-24", to_day: "2026-10-30", totals: { calls: 1, failed: 0, typed: 0, skills: 1, sessions: 1, first_at: 1, last_at: 2 } });
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await pass();
    expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");     // chosen at once
    expect(region().textContent).toContain("In these 30 days: 8 calls");                                // the numbers are the 30 days'
    expect(region().textContent).not.toContain("In these 7 days");
    release();
    await pass();
    expect(region().textContent).toContain("In these 7 days: 1 call of 1 skill in 1 session.");
  });

  it("keeps naming the window of the numbers it holds when the read for the one just chosen fails", async () => {
    await mount();
    srv.failing = { status: 500, detail: "broke" };
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await pass();
    expect(within(region()).getByRole("alert")).toBeTruthy();
    expect(region().textContent).toContain("In these 30 days: 8 calls");
    expect(region().textContent).not.toContain("In these 7 days");
  });

  it("offers the three windows, the last 30 days chosen to begin with", async () => {
    await mount();
    const choice = within(screen.getByRole("group", { name: "Days to count over" })).getAllByRole("button");
    expect(choice.map((b) => [b.textContent, b.getAttribute("aria-pressed")])).toEqual([["7 days", "false"], ["30 days", "true"], ["90 days", "false"]]);
    fireEvent.click(screen.getByRole("button", { name: "90 days" }));
    await pass();
    expect(paths().at(-1)).toBe("GET /api/ade/skills?days=90");
  });

  it("draws a column for each day of the window, each saying its day and its counts", async () => {
    await mount();
    const days = within(screen.getByRole("list", { name: "Calls by UTC day" })).getAllByRole("listitem");
    expect(days).toHaveLength(7);
    expect(days[2].getAttribute("aria-label")).toBe("2026-10-03: 4 calls (core 3, business 1, other 0)");
    expect(days[0].getAttribute("aria-label")).toBe("2026-10-01: 0 calls (core 0, business 0, other 0)");
    expect(days[0].getAttribute("title")).toBe(days[0].getAttribute("aria-label"));
    expect(region().textContent).toContain("2026-10-01 to 2026-10-07, UTC");
  });

  it("draws the days before the app began reading faint, says so in their words and what that means", async () => {
    srv.view = VIEW({ read: READS({ since_at: 1_791_115_200 }) });          // the first read: 2026-10-04 at noon UTC
    await mount();
    const days = within(screen.getByRole("list", { name: "Calls by UTC day" })).getAllByRole("listitem");
    expect(days.map((d) => d.getAttribute("data-before-reading")))
      .toEqual(["true", "true", "true", null, null, null, null]);
    expect(days[2].getAttribute("aria-label"))
      .toBe("2026-10-03: 4 calls (core 3, business 1, other 0), before the app began reading");
    expect(days[3].getAttribute("aria-label")).toBe("2026-10-04: 0 calls (core 0, business 0, other 0)");
    expect(days[0].className).toContain("opacity-40");
    expect(days[3].className).not.toContain("opacity-40");
    const text = region().textContent ?? "";
    expect(text).toContain(
      "The app began reading the workspace's record on 2026-10-04 (UTC). The days before it are drawn faint");
    expect(text).toContain("a day of none there may be a day nothing was counted");
  });

  it("says nothing of it where the app began reading before the window", async () => {
    await mount();                                                         // READS: the first read on 2026-09-21
    const days = within(screen.getByRole("list", { name: "Calls by UTC day" })).getAllByRole("listitem");
    expect(days.some((d) => d.hasAttribute("data-before-reading"))).toBe(false);
    expect(region().textContent).not.toContain("began reading");
  });

  it("says no skill call was counted for an empty window, and still draws the warning", async () => {
    srv.view = VIEW({ skills: [], by_day: [], totals: { calls: 0, failed: 0, typed: 0, skills: 0, sessions: 0, first_at: null, last_at: null } });
    await mount();
    expect(region().textContent).toContain("No skill call was counted in these 30 days.");
    expect(screen.getByRole("note", { name: "What these counts are" })).toBeTruthy();
  });

  it("says how many rows it did not list", async () => {
    srv.view = VIEW({ more: 3 });
    await mount();
    expect(region().textContent).toContain("3 more rows not listed: the view lists the most-called.");
    srv.view = VIEW({ more: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Read the workspace again" }));
    await pass();
    expect(region().textContent).toContain("1 more row not listed");
  });

  it("shows every word a session wrote as text, never as markup", async () => {
    const markup = `<img src=x onerror="document.title='owned'"><b>x</b>`;
    srv.view = VIEW({ skills: [ROW({ kind: "business", skill: markup, repo: "<i>repo</i>" })] });
    await mount();
    expect(region().querySelector("img, b, i")).toBeNull();
    expect(region().textContent).toContain(markup);
    expect(region().textContent).toContain("Business skills in <i>repo</i>");
    expect(document.title).not.toBe("owned");
  });
});

describe("what the counts are, and how the reads stand", () => {
  it("says the counts are the sessions' word, for display, and that a count can miss calls, whatever the answer holds", async () => {
    await mount();
    const note = screen.getByRole("note", { name: "What these counts are" }).textContent ?? "";
    expect(note).toContain("These counts are the sessions' word, for display: they are not a record that holds against a session.");
    expect(note).toContain("A program a session runs can write its own file of calls, and the workspace's record of them");
    expect(note).toContain("A count can also miss calls: a line lost to a cut of a session's file, a session removed before its file was read, a line the workspace dropped, past 30 days or 20,000 lines, before the app read it");
    expect(note).toContain("a call made as a chat ended (Claude Code ends a background hook still running when a headless session ends), a call whose session ran a Claude Code older than 2.1.23 or began before the workspace was updated, a session whose settings turn hooks off (the organisation's, or in a workspace machine a repository's or the person's own, or a folder Claude Code does not trust), a chat whose own settings are past 100 KB (it is started without the hook), a call whose result is past 16 MB (the hook cannot read it), and a skill a person typed that is neither a repository's own nor a core one.");
    expect(note).toContain("A skill is counted under the name the model gave it (a core skill named without its prefix, under its full name), a call that failed as one that ran, and a call the person interrupted as one that failed.");
    expect(note).not.toContain("counted only for");
    expect(note).toContain("A call's time and version are those of the hook's run, a little after the call: a call just before midnight UTC can be counted in the next day, and a commit made in between is named for it.");
    expect(note).toContain("This view cannot say whether a session's hook ran: a session whose hook did not run adds nothing, which looks like no calls.");
    expect(note).toContain("Not counted at all: a skill typed in the Manager's own chat, an agent's runs, a task's attempts, a skill Claude Code calls by its folder path (a name with a / in it), and a session of any program but Claude Code.");
  });

  it("says what is known to be missed, and the reads' state", async () => {
    srv.view = VIEW({ read: READS({ missed: 4, dropped: 2, refused: 1, trimmed: 5 }) });
    await mount();
    const note = screen.getByRole("note", { name: "What these counts are" }).textContent ?? "";
    expect(note).toContain("Known to be missed so far: 4 calls the workspace dropped before the app read them; 2 lines the workspace held out of shape; 1 line the app refused as out of shape; 5 calls the app dropped past the most it keeps, the oldest first. Others leave no trace.");
    expect(note).toContain("The app last read to the end of the workspace's record at ");
  });

  it("says a workspace that does not count skill calls does not, a read that failed with the app's own words, and one not yet taken to the end", async () => {
    srv.view = VIEW({ read: READS({ unserved_at: 1, error: "The workspace answered 500.", error_at: 1_790_000_300, behind_at: 1_790_000_600, at: null }) });
    await mount();
    const note = screen.getByRole("note", { name: "What these counts are" }).textContent ?? "";
    expect(note).toContain("This workspace does not count skill calls yet");
    expect(note).toContain("Reading the workspace's record failed (since ");
    expect(note).toContain("The workspace answered 500.");
    expect(note).toContain("has not yet read to the end of the workspace's record");
  });

  it("says the app has not read yet before it has", async () => {
    srv.view = VIEW({ read: null });
    await mount();
    expect(screen.getByRole("note", { name: "What these counts are" }).textContent).toContain("The app has not read the workspace's record of skill calls yet.");
  });

  it("says the machine is not running where the read found it so, and a read under way", async () => {
    srv.read = { state: "not_running", taken: 0 };
    await mount();
    expect(screen.getByRole("note", { name: "What these counts are" }).textContent).toContain("The workspace is not running, so nothing was read just now");
    srv.read = { state: "busy", taken: 0 };
    fireEvent.click(screen.getByRole("button", { name: "Read the workspace again" }));
    await pass();
    expect(screen.getByRole("note", { name: "What these counts are" }).textContent).toContain("A read is already under way");
  });
});

describe("when the app does not answer", () => {
  it("says the server's own words in an alert and draws nothing it holds", async () => {
    srv.failing = { status: 500, detail: "The app could not read its store." };
    await mount();
    expect(within(region()).getByRole("alert").textContent).toBe("The app could not read its store.");
    expect(region().textContent).not.toContain("Counted by UTC day");
  });

  it("says a server without the view has none yet", async () => {
    srv.failing = { status: 404, detail: "Not Found" };
    await mount();
    expect(within(region()).getByRole("alert").textContent).toContain("This server does not have the skills view yet");
  });

  it("says to sign in again where the page is not signed in, and draws none of what the last sign-in showed", async () => {
    await mount();
    expect(region().textContent).toContain("Counted by UTC day");
    srv.failing = { status: 401, detail: "Sign in." };
    await pass(15_500);
    expect(within(region()).getByRole("alert").textContent).toContain("This page is not signed in");
    expect(region().textContent).not.toContain("Counted by UTC day");
  });

  it("waits twice as long after a failure and reads again when the app answers", async () => {
    srv.failing = { status: 500, detail: "down" };
    await mount();
    const n = srv.calls.length;
    srv.failing = null;
    await pass(29_000);
    expect(srv.calls.length).toBe(n);
    await pass(2_000);
    expect(srv.calls.length).toBeGreaterThan(n);
    expect(region().textContent).toContain("Counted by UTC day");
    expect(within(region()).queryByRole("alert")).toBeNull();
  });
});

describe("one skill", () => {
  const open = async (name: string, within_ = region()) => {
    fireEvent.click(within(within_).getByRole("button", { name: `${name}: show its detail` }));
    await pass();
  };

  it("reads a skill's detail when its row is opened, naming its kind, its name and the window", async () => {
    await mount();
    await open("proofpane-core:verify");
    const asked = srv.calls.filter((c) => c.path === "/api/ade/skills/detail");
    expect(asked).toHaveLength(1);
    expect(asked[0].query).toBe("?kind=core&skill=proofpane-core%3Averify&days=30");
  });

  it("names a business skill's repository, and an empty one for calls made in no repository", async () => {
    await mount();
    await open("deploy", group("Business skills in alpha"));
    await open("lonely", group("Business skills in no repository"));
    const asked = srv.calls.filter((c) => c.path === "/api/ade/skills/detail").map((c) => c.query);
    expect(asked).toEqual(["?kind=business&skill=deploy&repo=alpha&days=30", "?kind=business&skill=lonely&repo=&days=30"]);
  });

  it("draws its calls by UTC day, by version, where they were made and how its tasks ended", async () => {
    await mount();
    await open("proofpane-core:verify");
    const d = screen.getByLabelText("proofpane-core:verify: detail").textContent ?? "";
    expect(d).toContain("3 calls (1 failed, 1 typed by a person)");
    expect(d).toContain("2026-10-03: 3 calls, 1 failed");
    expect(d).not.toContain("2026-10-04");               // a day with no call is not listed
    expect(d).toContain("1.0.0+bbbbbbbbbbbb: 2 calls");
    expect(d).toContain("1.0.0+aaaaaaaaaaaa: 1 call");
    expect(d).toContain("alpha: 2 calls");
    expect(d).toContain("no repository: 1 call");
    expect(d).toContain(`${"a".repeat(12)}: 3 calls`);
    expect(d).toContain("t1: 2 calls, done, ended ");
    expect(d).toContain("t2: 1 call, not known to the app");
    expect(d).toContain("Requirements (each title is a model's words)");
    expect(d).toContain("Fix the login: 2 calls in 1 task");
    expect(d).toContain("1 done");
    expect(d).not.toContain("not listed");               // no list of it is cut
  });

  it("says which lists are cut, each in its own words", async () => {
    srv.detail = DETAIL({ more: { sessions: true, tasks: true, requirements: true, versions: true, repos: true } });
    await mount();
    await open("proofpane-core:verify");
    const d = screen.getByLabelText("proofpane-core:verify: detail").textContent ?? "";
    expect(d).toContain("and older versions, not listed");
    expect(d).toContain("and repositories with fewer calls, not listed");
    expect(d).toContain("and older sessions, not listed");
    expect(d).toContain("and older tasks, not listed");
    expect(d).toContain("and requirements with fewer calls, not listed");
  });

  it("says only of the lists that are cut, and not of the repositories of a business skill's row, which has one", async () => {
    srv.detail = DETAIL({ kind: "business", skill: "deploy", repo: "alpha", repos: [{ repo: "alpha", calls: 2 }],
      more: { sessions: true, tasks: false, requirements: false, versions: false, repos: true } });
    await mount();
    await open("deploy", group("Business skills in alpha"));
    const d = screen.getByLabelText("deploy: detail").textContent ?? "";
    expect(d).toContain("and older sessions, not listed");
    expect(d).not.toContain("older tasks");
    expect(d).not.toContain("older versions");
    expect(d).not.toContain("requirements with fewer");
    expect(d).not.toContain("repositories with fewer");
  });

  it("does not list the repositories of a business skill's row: its row is of one", async () => {
    srv.detail = DETAIL({ kind: "business", skill: "deploy", repo: "alpha", repos: [{ repo: "alpha", calls: 2 }] });
    await mount();
    await open("deploy", group("Business skills in alpha"));
    expect(screen.getByLabelText("deploy: detail").textContent).not.toContain("Repositories");
  });

  it("says a requirement the app has no record of, and calls made in no repository", async () => {
    srv.detail = DETAIL({ kind: "business", skill: "lonely", repo: null, requirements: [{ requirement: "r9", title: null, calls: 1, tasks: 1 }] });
    await mount();
    await open("lonely", group("Business skills in no repository"));
    const d = screen.getByLabelText("lonely: detail").textContent ?? "";
    expect(d).toContain("a requirement the app has no record of: 1 call in 1 task");
    expect(d).toContain("Calls made in no repository.");
  });

  it("shows what a model titled a requirement as text, never as markup", async () => {
    srv.detail = DETAIL({ requirements: [{ requirement: "r1", title: "<script>alert(1)</script>", calls: 1, tasks: 1 }] });
    await mount();
    await open("proofpane-core:verify");
    const d = screen.getByLabelText("proofpane-core:verify: detail");
    expect(d.querySelector("script")).toBeNull();
    expect(d.textContent).toContain("<script>alert(1)</script>: 1 call in 1 task");
  });

  it("says a skill with no call in the window any more, without a failure's alarm about the app", async () => {
    srv.detailStatus = 404;
    await mount();
    await open("proofpane-core:verify");
    expect(screen.getByLabelText("proofpane-core:verify: detail").textContent).toContain("No call of this skill in the last 30 days.");
  });

  it("reads a skill's detail again for a window the owner changes, and closes with its row", async () => {
    await mount();
    await open("proofpane-core:verify");
    const before = srv.calls.filter((c) => c.path === "/api/ade/skills/detail").length;
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await pass();
    const asked = srv.calls.filter((c) => c.path === "/api/ade/skills/detail");
    expect(asked.length).toBe(before + 1);
    expect(asked.at(-1)!.query).toContain("days=7");
    fireEvent.click(within(region()).getByRole("button", { name: "proofpane-core:verify: hide its detail" }));
    expect(screen.queryByLabelText("proofpane-core:verify: detail")).toBeNull();
  });
});

// The business skills' loop in the Skills view, against a server that
// answers the loop's routes as proofpane_open/ade/skill_improve_routes.py gives them.
describe("the business skills' loop", () => {
  it("finds a row's proposal by a name its folder is called by, as the candidates say", () => {
    const loop = { proposals: [{ id: "p1", repo: "alpha", folder: "deploy" }], budget: {}, switches: [], copy: null } as unknown as SkillLoop;
    const row = { kind: "business", repo: "alpha", skill: "ship-it" } as unknown as SkillRow;
    const cands: Candidates = { ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy", "ship-it"] }], not: {} };
    expect(proposalFor(row, loop)).toBeNull();                                  // no candidates read: by its folder alone
    expect(proposalFor(row, loop, cands)?.id).toBe("p1");
    expect(proposalFor({ ...row, skill: "deploy" }, loop)?.id).toBe("p1");
    expect(proposalFor({ ...row, repo: "beta" }, loop, cands)).toBeNull();
    expect(proposalFor({ ...row, skill: "other" }, loop, cands)).toBeNull();
  });

  const withLoop = (over: Dict = {}) => {
    srv.loop = LOOP(over);
    srv.cands = { alpha: { ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy"] }],
      not: { release: "its SKILL.md names allowed-tools, which the loop does not change" } } };
    const v = VIEW();
    srv.view = { ...v, skills: [...(v.skills as Dict[]), ROW({ kind: "business", skill: "release", repo: "alpha", calls: 1, failed: 0,
      typed: 0, versions: 1, latest_version: "0123456789abcdef0123456789abcdef01234567" })] };
  };
  const switchRow = (repo: string) => screen.getByRole("group", { name: `Proposals for ${repo}` });
  const count = (p: string) => paths().filter((x) => x === p).length;

  it("reads the loop's list and the record with each round, and a repository's candidates only when the view opens", async () => {
    withLoop();
    await mount();
    expect(count("GET /api/ade/skill-improve")).toBe(1);
    expect(count("GET /api/ade/skill-improve/record")).toBe(1);
    expect(count("GET /api/ade/skill-improve/repos/alpha/candidates")).toBe(1);
    expect(count("GET /api/ade/skill-improve/repos/beta/candidates")).toBe(0);           // its switch is off
    expect(paths().indexOf("GET /api/ade/skill-improve")).toBeLessThan(paths().indexOf("POST /api/ade/skills/read"));
    expect(paths().indexOf("GET /api/ade/skill-improve/record")).toBeGreaterThan(paths().indexOf("POST /api/ade/skills/read"));
    await pass(15_500);
    expect(count("GET /api/ade/skill-improve")).toBe(2);
    expect(count("GET /api/ade/skill-improve/record")).toBe(2);
    expect(count("GET /api/ade/skill-improve/repos/alpha/candidates")).toBe(1);          // not with each round
  });

  it("gives each repository's business skills its switch, with the model and the account the review and the critic run on", async () => {
    withLoop();
    await mount();
    const alpha = switchRow("alpha");
    expect(alpha.textContent).toContain("Proposals for its business skills: on");
    expect(within(alpha).getByRole("button", { name: /Turn off/ }).getAttribute("aria-pressed")).toBe("true");
    expect((within(alpha).getByLabelText(/The model its review and critic run on/) as HTMLSelectElement).value).toBe("opus");
    expect((within(alpha).getByLabelText(/The account they run on/) as HTMLSelectElement).value).toBe("");
    expect(alpha.textContent).toContain("Recorded when turned on: branch refs/heads/main");
    expect(alpha.textContent).toContain("Each proposal is a model's, for you to merge or not");
    const beta = switchRow("beta");
    expect(beta.textContent).toContain("Proposals for its business skills: off");
    expect(within(beta).getByRole("button", { name: /Turn on/ }).getAttribute("aria-pressed")).toBe("false");
    expect(beta.textContent).not.toContain("Recorded when turned on");
    expect(within(group("Business skills in no repository")).queryByRole("group")).toBeNull();
  });

  it("says on a skill's row why it may not be proposed for, and opens the proposal a row has", async () => {
    withLoop();
    await mount();
    const alpha = group("Business skills in alpha");
    const release = within(alpha).getByRole("listitem", { name: "release" });
    expect(release.textContent).toContain("Not proposed for: its SKILL.md names allowed-tools, which the loop does not change");
    expect(release.textContent).not.toContain("A proposal");                            // the proposal is deploy's
    const deploy = within(alpha).getByRole("listitem", { name: "deploy" });
    expect(deploy.textContent).not.toContain("Not proposed for");
    fireEvent.click(within(deploy).getByRole("button", { name: "A proposal: for you to decide" }));
    const card = within(deploy).getByLabelText("alpha: a change to the skill deploy");
    expect(card.textContent).toContain("Naming the target first stops the wrong deploys.");
    expect(within(card).getByRole("button", { name: /Merge into main/ })).toBeTruthy();
    const beta = within(group("Business skills in beta")).getByRole("listitem", { name: "deploy" });
    expect(beta.textContent).not.toContain("A proposal");                                // the proposal is alpha's
    expect(beta.textContent).not.toContain("Not proposed for");                          // and beta's switch is off
  });

  it("opens on a row named otherwise than its folder the proposal the candidates it read say is that row's", async () => {
    withLoop();
    srv.cands.alpha = { ...srv.cands.alpha, candidates: [{ folder: "deploy", names: ["deploy", "ship-it"] }] };
    const v = srv.view as Dict;
    srv.view = { ...v, skills: [...(v.skills as Dict[]), ROW({ kind: "business", skill: "ship-it", repo: "alpha", calls: 1, failed: 0,
      typed: 0, versions: 1, latest_version: "1123456789abcdef0123456789abcdef01234567" })] };
    await mount();
    const shipIt = within(group("Business skills in alpha")).getByRole("listitem", { name: "ship-it" });
    expect(shipIt.textContent).not.toContain("Not proposed for");
    fireEvent.click(within(shipIt).getByRole("button", { name: "A proposal: for you to decide" }));
    expect(within(shipIt).getByLabelText("alpha: a change to the skill deploy").textContent)
      .toContain("Naming the target first stops the wrong deploys.");
  });

  it("says why no skill of a repository may be proposed for, on its switch's row and not on each skill's", async () => {
    withLoop();
    srv.cands = { alpha: { ok: false, why: "its branch could not be read: the clone is detached", candidates: [], not: {} } };
    await mount();
    expect(switchRow("alpha").textContent).toContain("No skill here may be proposed for now: its branch could not be read: the clone is detached");
    expect(within(group("Business skills in alpha")).getByRole("listitem", { name: "release" }).textContent).not.toContain("Not proposed for");
  });

  it("turns a switch with the model and the account chosen, then reads the loop again and the switched repository's candidates", async () => {
    withLoop();
    srv.cands.beta = { ok: true, why: null, candidates: [{ folder: "deploy", names: ["deploy"] }], not: {} };
    await mount();
    const beta = switchRow("beta");
    fireEvent.change(within(beta).getByLabelText(/The model its review and critic run on/), { target: { value: "sonnet" } });
    const loops = count("GET /api/ade/skill-improve");
    fireEvent.click(within(beta).getByRole("button", { name: /Turn on/ }));
    await pass();
    const put = srv.calls.filter((c) => c.method === "PUT");
    expect(put.map((c) => [c.path, c.body])).toEqual([["/api/ade/skill-improve/repos/beta/switch", { on: true, account: null, model: "sonnet" }]]);
    expect(count("GET /api/ade/skill-improve")).toBe(loops + 1);
    expect(count("GET /api/ade/skill-improve/repos/beta/candidates")).toBe(1);
    expect(switchRow("beta").textContent).toContain("Proposals for its business skills: on");
  });

  it("keeps a switch's new model where the switch is on, without turning it off", async () => {
    withLoop();
    await mount();
    const alpha = switchRow("alpha");
    expect(within(alpha).queryByRole("button", { name: "Keep this model and account" })).toBeNull();
    fireEvent.change(within(alpha).getByLabelText(/The model its review and critic run on/), { target: { value: "haiku" } });
    fireEvent.click(within(alpha).getByRole("button", { name: "Keep this model and account" }));
    await pass();
    const put = srv.calls.filter((c) => c.method === "PUT");
    expect(put.map((c) => c.body)).toEqual([{ on: true, account: null, model: "haiku" }]);
  });

  it("lists the proposals for the owner to decide, what became of the others, and the week's budget by kind", async () => {
    withLoop({ proposals: [PROPOSAL(), PROPOSAL({ id: "q1q2q3q4q5q6", folder: "release", state: "merged", ended: "merged", ways: [] })] });
    await mount();
    const list = screen.getByRole("region", { name: "Business skill proposals" });
    expect(list.textContent).toContain("1 for you to decide");
    expect(list.textContent).toContain("This week: 3 of 40 sessions (1 reviews, 0 critics, 0 replays, 1 skill reviews, 1 skill critics), $1.50 of $20.00");
    expect(within(list).getByText("What became of the others (1)")).toBeTruthy();
    expect(within(list).getByLabelText("alpha: a change to the skill deploy")).toBeTruthy();
    expect(within(list).queryByLabelText("alpha: a change to the skill release")).toBeNull();      // merged: among the others
    fireEvent.change(within(list).getByLabelText("Why not"), { target: { value: "too broad" } });
    fireEvent.click(within(list).getByRole("button", { name: /Reject/ }));
    await pass();
    const no = srv.calls.filter((c) => c.method === "POST" && c.path.startsWith("/api/ade/skill-improve/proposals/"));
    expect(no.map((c) => [c.path, c.body])).toEqual([["/api/ade/skill-improve/proposals/p1p2p3p4p5p6/reject", { reason: "too broad" }]]);
  });

  it("merges a proposal into his copy on his tap, and reads the loop again", async () => {
    withLoop();
    await mount();
    const list = screen.getByRole("region", { name: "Business skill proposals" });
    const loops = count("GET /api/ade/skill-improve");
    fireEvent.click(within(list).getByRole("button", { name: /Merge into main/ }));
    await pass();
    const merged = srv.calls.filter((c) => c.method === "POST" && c.path.startsWith("/api/ade/skill-improve/proposals/"));
    expect(merged.map((c) => [c.path, c.body])).toEqual([["/api/ade/skill-improve/proposals/p1p2p3p4p5p6/merge", {}]]);
    expect(count("GET /api/ade/skill-improve")).toBe(loops + 1);
  });

  it("says where the record of proposals is held, lists the starts it refused, and restores it on the owner's tap", async () => {
    withLoop({ proposals: [] });
    const why = "the record of proposals is held";
    srv.record = RECORD({ record: { state: "held", held: "its signature does not verify", word: true, generation: 3, error: null,
      refused: [{ at: 1_790_000_300, kind: "attempt", repo: "alpha", proposal: null, why },
        { at: 1_790_000_301, kind: "controller", repo: "beta", proposal: null, why }] } });
    srv.restored = RECORD();
    await mount();
    const held = screen.getByRole("alert", { name: "The record of skill proposals is held" });
    expect(held.textContent).toContain("(its signature does not verify)");
    expect(held.textContent).toContain("every start it makes unattended, in every repository, is refused until you restore it");
    expect(within(held).getByRole("list", { name: "The starts refused" }).textContent).toContain("attempt in alpha: the record of proposals is held");
    expect(within(held).getByRole("list", { name: "The starts refused" }).textContent)
      .toContain("Manager in beta: the record of proposals is held");
    expect(screen.queryByRole("region", { name: "Starts the bar refused" })).toBeNull();     // listed once, with the restore
    fireEvent.click(within(held).getByRole("button", { name: /Restore it from the app's copy/ }));
    await pass();
    expect(count("POST /api/ade/skill-improve/record/restore")).toBe(1);
    expect(screen.queryByRole("alert", { name: "The record of skill proposals is held" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Business skill proposals" })).toBeNull();
  });

  it("views each repository with a proposal for the owner to decide once, and shows its word on starts from its head", async () => {
    // a proposal's own view, which looks at the branch's head first
    const refused = "a proposed change to .claude/skills/release/SKILL.md that the owner has not merged (proposal f1f2f3f4f5f6) is in what this start would begin from";
    withLoop({ proposals: [
      PROPOSAL({ id: "f1f2f3f4f5f6", folder: "release", path: ".claude/skills/release/SKILL.md", state: "found", ways: ["take", "go_back"] }),
      PROPOSAL(),
      PROPOSAL({ id: "b1b2b3b4b5b6", repo: "beta" }),
      PROPOSAL({ id: "m1m2m3m4m5m6", repo: "gamma", state: "merged", ended: "merged", ways: [] })] });
    srv.views = { f1f2f3f4f5f6: PROPOSAL({ id: "f1f2f3f4f5f6", folder: "release", path: ".claude/skills/release/SKILL.md", state: "found",
      ways: ["take", "go_back"], bars_now: { refused, head: "e".repeat(40), branch: "refs/heads/main" } }) };
    await mount();
    await pass();
    expect(count("GET /api/ade/skill-improve/proposals/f1f2f3f4f5f6")).toBe(1);         // alpha's, once
    expect(count("GET /api/ade/skill-improve/proposals/p1p2p3p4p5p6")).toBe(0);         // alpha is viewed already
    expect(count("GET /api/ade/skill-improve/proposals/b1b2b3b4b5b6")).toBe(1);
    expect(count("GET /api/ade/skill-improve/proposals/m1m2m3m4m5m6")).toBe(0);         // ended: not the owner's to decide
    const found = screen.getByLabelText("alpha: a change to the skill release");
    expect(found.textContent).toContain(`Unattended starts from refs/heads/main in alpha are refused now: ${refused}`);
    await pass(15_500);
    expect(count("GET /api/ade/skill-improve")).toBeGreaterThan(1);                    // a round read the list again
    expect(count("GET /api/ade/skill-improve/proposals/f1f2f3f4f5f6")).toBe(1);         // and viewed nothing again
    expect(screen.getByLabelText("alpha: a change to the skill release").textContent).toContain("are refused now");
  });

  it("views a repository whose first proposal to decide comes at a later round, and none it viewed before", async () => {
    // each repository once while the list is shown, one listed later too
    withLoop({ proposals: [PROPOSAL(), PROPOSAL({ id: "b1b2b3b4b5b6", repo: "beta" })] });
    await mount();
    await pass();
    expect(count("GET /api/ade/skill-improve/proposals/p1p2p3p4p5p6")).toBe(1);
    expect(count("GET /api/ade/skill-improve/proposals/b1b2b3b4b5b6")).toBe(1);
    srv.loop = LOOP({ proposals: [PROPOSAL({ id: "g1g2g3g4g5g6", repo: "gamma" }), PROPOSAL(), PROPOSAL({ id: "b1b2b3b4b5b6", repo: "beta" })] });
    await pass(15_500);
    expect(count("GET /api/ade/skill-improve")).toBeGreaterThan(1);                    // a round read the list again
    expect(count("GET /api/ade/skill-improve/proposals/g1g2g3g4g5g6")).toBe(1);         // gamma's, once it is listed
    expect(count("GET /api/ade/skill-improve/proposals/p1p2p3p4p5p6")).toBe(1);         // alpha's and beta's not again
    expect(count("GET /api/ade/skill-improve/proposals/b1b2b3b4b5b6")).toBe(1);
  });

  it("lists the starts the bar refused while the record is not held too", async () => {
    // a start refused for want of the app's word, or by a proposal's bar
    withLoop({ proposals: [] });
    srv.record = RECORD({ record: { state: "record", held: null, word: true, generation: 3, error: null, refused: [
      { at: 1_790_000_300, kind: "agent", repo: "alpha", proposal: "p1p2p3p4p5p6", why: "a proposed change to it is in what this start would begin from" },
      { at: 1_790_000_301, kind: "controller", repo: "beta", proposal: null, why: "the workspace has not had the app's copy of its record" }] } });
    await mount();
    const list = screen.getByRole("region", { name: "Starts the bar refused" });
    expect(list.textContent).toContain("agent in alpha: a proposed change to it is in what this start would begin from");
    expect(list.textContent).toContain("Manager in beta: the workspace has not had the app's copy of its record");
    expect(screen.queryByRole("alert", { name: "The record of skill proposals is held" })).toBeNull();
  });

  it("draws no list where there is no proposal and the record is not held, and nothing of the loop where the server has none", async () => {
    withLoop({ proposals: [] });
    await mount();
    expect(screen.queryByRole("region", { name: "Business skill proposals" })).toBeNull();
    expect(switchRow("alpha")).toBeTruthy();
    cleanup();
    serve();
    await mount();
    expect(screen.queryByRole("group", { name: "Proposals for alpha" })).toBeNull();
    expect(count("GET /api/ade/skill-improve/record")).toBe(0);                          // no loop: the record is not asked for
  });
});
