import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Api, type AccountOwnerView, type AccountRecordView, type AccountsView, type IdentityReport, type RiskTextView, type SessionRecord } from "../api";
import { useStore } from "../store";
import { eventsToOpen, forgetPage, RiskTerms } from "./RiskTerms";
import { AccountRecords, ClaudeAccounts, forgetAccountReads, ownSignOutHere } from "./Connections";
import { Connections } from "./Connections";

// Claude Code's accounts as one list in Connections: each account one row, its sign-in and its standing for tasks
// together; who an account is read by itself once it is signed in, and not before risk point 16's text has opened in
// this browser and the owner has closed it; the workspace's own sign-in signed out where the workspace is hosted; and
// every task's account record under the accounts. The app's side is a fake of the app's account routes, answering as
// they do and recording every call.

// `query`: what follows the path's "?", "" for none, as ownersApp's fake records it (a call set
// of paths alone cannot tell the list's own read of the task list from one made with an agent or anything else)
type Call = { method: string; path: string; body: Record<string, unknown> | undefined; query?: string };
type Answer = [number, unknown] | undefined;

const DEFAULT = "d".repeat(32);
const WORK = "e".repeat(32);
const SPARE = "f".repeat(32);
const ID: Record<string, string> = { default: DEFAULT, aaaaaaaaaaaa: WORK, bbbbbbbbbbbb: SPARE };

const texts = (): RiskTextView[] => [2, 6, 9, 15, 16].map((point) =>
  ({ point, title: `Point ${point}`, design: [{ case: null, text: `Point ${point}'s own words.` }], quotations: [], unread: false, digest: "0".repeat(64) }));

const personal = (email: string, plan = "Claude Max"): IdentityReport =>
  ({ email, organization: null, plan, token_source: "claude.ai", api_key_source: null, api_provider: "firstParty" });
const seat = (email: string): IdentityReport =>
  ({ email, organization: "Acme", plan: "Claude Team", token_source: "claude.ai", api_key_source: null, api_provider: "firstParty" });

const owner = (account: string, over: Partial<AccountOwnerView> = {}): AccountOwnerView => ({
  id: ID[account], account, name: null, report: personal("me@example.org"), org_id: null, mismatch: null, state: "confirmed",
  grp: "personal", grp_kind: "personal", position: 0, in_relays: true, reserve_lifted: false, ready: true, windows: [], hold: null, ...over,
});

const signedIn = (email: string, subscription = "max") => ({ installed: true, signed_in: true, email, subscription });
const signedOut = () => ({ installed: true, signed_in: false });

/** The workspace's three Claude Code accounts: its own, Work and Spare, each signed in unless said otherwise. */
const accountsView = (over: { work?: boolean | null; spare?: boolean | null } = {}): AccountsView => ({
  claude: {
    active: "default",
    accounts: [
      { id: "default", name: "Default", created_at: null, sessions: 1, status: signedIn("me@example.org"), usage: null },
      // null: a sign-in the store has not read yet
      { id: "aaaaaaaaaaaa", name: "Work", created_at: 1, sessions: 0, status: over.work === null ? null : over.work === false ? signedOut() : signedIn("me@work.example", "team"), usage: null },
      { id: "bbbbbbbbbbbb", name: "Spare", created_at: 1, sessions: 0, status: over.spare === null ? null : over.spare === false ? signedOut() : signedIn("spare@example.org", "pro"), usage: null },
    ],
  },
  codex: { active: "default", accounts: [] },
});

/** The app's words for a confirmation whose report or orgId is not the row's, and the check of the two (`shownIs`),
 *  every field of the report and the orgId, as `confirm_account` compares them. */
const READ_SINCE = "Its identity was read again since the page showed it: look at it again, then confirm.";
const REPORT_FIELDS = ["email", "organization", "plan", "token_source", "api_key_source", "api_provider"] as const;
const shownIs = (body: Record<string, unknown>, o: AccountOwnerView) => {
  const shown = (body.report ?? null) as Record<string, unknown> | null;
  const same = shown === null || o.report === null ? shown === o.report : REPORT_FIELDS.every((k) => shown[k] === o.report![k]);
  return same && (body.org_id ?? null) === (o.org_id ?? null);
};

/** The app's account-owner rows as the account routes keep them, and a record of every call. A report answers as
 *  `report_account` does (tasks.py): 412 while the workspace is stopped; 400 for an account `reports` does not name,
 *  the workspace's own `auth status` saying it is not signed in; else 200, with the report `reports` gives, or none
 *  (`null`: a frozen start that gave nothing, its mismatch "report"), adding a row (reported) for an account with
 *  none, making a confirmed row whose report is not the one it has `changed`, and keeping any other row in its state;
 *  `hold` makes the first report wait for the test to let it go. (The app writes nothing over a
 *  confirmed or held row for a read that found nothing, and answers `kept`; this fake does not play that: the page
 *  reads a confirmed or held account only by the press over a held row with a mismatch, and shows the row as it
 *  stands.) The switches and the order as the routes say; a
 *  wait cancelled after its time refused as `holds.cancel` refuses it (409); the confirmation's ways, a password, and
 *  an account's confirmation as `confirm_account` makes it: refused (400) where it names no report, and (409,
 *  READ_SINCE) where the report or orgId it names is not the row's, then with no report or a mismatch, else held,
 *  waiting to join. "The same identity" is the one a confirmed row was confirmed as (`_same_identity`; the orgId it
 *  also compares is not played here). `tasks` is the answer the task list's route gives (`relayIs`), which carries the
 *  deployment's settings. */
function ownersApp(start: AccountOwnerView[], reports: Record<string, IdentityReport | null> = {}, hold?: Promise<void>, failOwners = 0,
                   tasks?: Answer) {
  const rows = start.map((o) => ({ ...o }));
  const calls: Call[] = [];
  const confirmedAs = new Map(rows.filter((o) => o.state === "confirmed").map((o) => [o.id, o.report]));
  let held = hold;
  let failing = failOwners;                                   // the first reads of the owners refused, as an app away for a moment
  // the next read of the owners held until the test lets it go (`gateOwners`), as a slow network holds it, so that
  // the page renders what it has meanwhile
  let gate: Promise<void> | undefined;
  const gateOwners = () => { let open = () => {}; gate = new Promise<void>((done) => { open = done; }); return open; };
  // the next reads of the confirmation's ways held the same way, one for each `gateWays` in their order, so that a press
  // of Yes, it is mine opens nothing until the test lets it go, with the answer it gives or the app's usual one
  const ways: Promise<Answer>[] = [];
  const gateWays = () => { let open: (given?: Answer) => void = () => {}; ways.push(new Promise<Answer>((done) => { open = done; })); return open; };
  // the next reports held the same way, one for each `gateReport` in their order, each from its call on (a report
  // already waiting on `hold` takes none), so that a read stays under way while the test plays what comes meanwhile
  const reportGates: Promise<void>[] = [];
  const gateReport = () => { let open = () => {}; reportGates.push(new Promise<void>((done) => { open = done; })); return open; };
  const answer = async (method: string, path: string, body: Record<string, unknown>): Promise<Answer> => {
    if (method === "GET" && path === "/api/ade/agents/accounts") {
      if (gate) { const g = gate; gate = undefined; await g; }
      if (failing > 0) { failing -= 1; return [500, { detail: "The app is away for a moment." }]; }
      return [200, { accounts: rows.map((o) => ({ ...o })) }];
    }
    if (method === "GET" && path === "/api/account/confirm") {
      const w = ways.shift();
      if (w) { const given = await w; if (given) return given; }
      return [200, { needed: true, password: true, passkeys: 0 }];
    }
    if (method === "POST" && path === "/api/account/confirm") return [200, { confirmation: "the-confirmation" }];
    const confirm = /^\/api\/ade\/agents\/accounts\/([0-9a-f]{32})\/confirm$/.exec(path);
    if (method === "POST" && confirm) {
      const o = rows.find((r) => r.id === confirm[1])!;
      if (!("report" in body)) return [400, { detail: "This confirmation does not say which report it confirms: reload the page, then confirm again." }];
      if (!shownIs(body, o)) return [409, { detail: READ_SINCE }];
      if (!o.report || o.mismatch) return [412, { detail: "Read its identity first (report)." }];
      Object.assign(o, { state: "held", hold: { id: "hold-" + o.id.slice(0, 8), kind: "task.account_own", takes_effect_at: Date.now() / 1000 + 180 } });
      return [200, { account: { ...o }, hold: o.hold }];
    }
    const cancel = /^\/api\/ade\/holds\/([^/]+)\/cancel$/.exec(path);
    if (method === "POST" && cancel) {
      // the wait ended before the press reached the app: refused, and the account is in the relay already
      const o = rows.find((r) => r.hold?.id === cancel[1]);
      if (o) Object.assign(o, { state: "confirmed", hold: null, in_relays: true, grp: "personal", grp_kind: "personal" });
      return [409, { detail: "Its time came at 2026-10-13 10:00:00 UTC: it has taken effect." }];
    }
    if (method === "POST" && path === "/api/ade/agents/accounts/report") {
      const g = reportGates.shift();
      if (g) await g;
      if (held) { const h = held; held = undefined; await h; }
      if (!useStore.getState().ws?.running) return [412, { detail: "Start the workspace first: an account's identity is read there." }];
      const account = String(body.account);
      if (!(account in reports)) return [400, { detail: "That account is not signed in." }];
      const report = reports[account];
      const mismatch = report ? null : "report";
      let o = rows.find((r) => r.account === account);
      if (!o) {
        rows.push(o = owner(account, { state: "reported", position: rows.length, grp: null, grp_kind: null, report, mismatch }));
      } else {
        const was = confirmedAs.get(o.id);
        const same = !!report && !!was && was.email === report.email && was.organization === report.organization && was.plan === report.plan;
        Object.assign(o, { report, mismatch, state: o.state === "confirmed" && !same ? "changed" : o.state });
      }
      return [200, { account: { ...o } }];
    }
    const m = /^\/api\/ade\/agents\/accounts\/([0-9a-f]{32})$/.exec(path);
    if (method === "PUT" && m) {
      const o = rows.find((r) => r.id === m[1])!;
      Object.assign(o, body);
      return [200, { account: { ...o } }];
    }
    if (method === "POST" && path === "/api/ade/agents/accounts/order") {
      (body.ids as string[] ?? []).forEach((id, i) => { const o = rows.find((r) => r.id === id); if (o) o.position = i; });
      return [200, { accounts: rows.map((o) => ({ ...o })) }];
    }
    // the deployment's settings, as the task list's route gives them (Connections reads whether the relay is on);
    // where a test gives no answer the route does not answer, as every route the fake does not know
    if (method === "GET" && path === "/api/ade/agents/tasks") return tasks;
    return undefined;
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const [path, query = ""] = String(url).split("?");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body, query });
    // each answer on a task of its own, as a browser's fetch is: the page renders what it has between two of them,
    // which an answer within the same microtask run would hide (a second read of an account queued from such a
    // render is what the auto-read's own record of the accounts read guards against)
    await new Promise((r) => setTimeout(r, 0));
    const got = method === "GET" && path === "/api/ade/risks" ? [200, { texts: texts() }] as Answer : await answer(method, path, body ?? {});
    if (!got) return new Promise<Response>(() => {});
    return new Response(JSON.stringify(got[1]), { status: got[0] });
  }));
  return { rows, calls, gateOwners, gateWays, gateReport };
}

/** The task list's answer for a deployment whose relay is `relay` (TaskSettings). */
const relayIs = (relay: boolean): Answer =>
  [200, { tasks: [], settings: { relay, auto_resume: false, teammate: false, cross_groups: false, local_provider: false } }];

/** The note over the accounts, whole: with its relay clause, said only for a relay known to be on, and without it
 *  (whole: the rest of the note stands, "The order is the relay's" in it). */
const NOTE_ON = "One row is one account: its sign-in, and what your tasks' relay knows of it. The filled circle marks the account "
  + "new sessions start on. The order is the relay's: when an account runs out of room, a task goes on on the next of its "
  + "group; drag a row to change it.";
const NOTE_OFF = "One row is one account: its sign-in, and what your tasks' relay knows of it. The filled circle marks the account "
  + "new sessions start on. The order is the relay's; drag a row to change it.";

const reports = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.path === "/api/ade/agents/accounts/report").map((c) => c.body);

/** The page signed in, its workspace running on Fly (hosted) with Claude Code installed; the store's own reads of the
 *  workspace answered by nothing, the accounts staying as set. */
const workspace = (over: Partial<ReturnType<typeof useStore.getState>> = {}) => useStore.setState({
  user: { id: "u1", email: "owner@example.org", can_sign_in_again: true },
  ws: { enabled: true, running: true, provider: "fly" },
  claude: { installed: true, signed_in: true, email: "me@example.org", subscription: "max" }, accounts: accountsView(),
  programs: { claude: true, codex: false }, signIns: [],
  refresh: async () => {}, refreshClaude: async () => {}, refreshGithub: async () => {}, refreshApprovals: async () => {},
  refreshAccounts: async () => {}, refreshQuota: async () => {}, ...over,
});

const list = () => screen.getByRole("list", { name: "Your accounts for tasks, in the relay's order" });
const itemOf = (name: string) => within(list()).getByText(name).closest("li") as HTMLElement;
const names = () => within(list()).getAllByRole("listitem").map((li) => li.querySelector(".font-medium")?.firstChild?.textContent);
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });
/** A row's Yes, it is mine, and a press of it that fails the test where it waits: a disabled button takes no click, so
 *  a press of one would otherwise pass for nothing. It waits while its account's read by itself runs, and while that
 *  read waits its turn, but not while risk point 16's text is open, when no read by itself is made and every one queued
 *  waits on the text, behind another's or not (a press of Read who it is reads then). */
const yesOf = (name: string) => within(itemOf(name)).getByRole("button", { name: "Yes, it is mine" }) as HTMLButtonElement;
const pressYes = (name: string) => { expect(yesOf(name).disabled).toBe(false); fireEvent.click(yesOf(name)); };

/** This browser as one in which each account given has opened risk point 16's text and closed it before, made as the
 *  page makes it (the text opened, its words read, then its Close), so that the reads by themselves the tests below
 *  play go on as they did before that point had a text. The tests of the text itself start from a browser that keeps
 *  nothing ("Risk point 16", below). The store is left as it was found, the page forgotten. */
async function closedBefore(...users: string[]): Promise<void> {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ texts: texts() }), { status: 200 })));
  for (const id of users) {
    useStore.setState({ user: { id, email: `${id}@example.org`, can_sign_in_again: true } });
    render(<RiskTerms point={16} kind="before" open />);
    const terms = await screen.findByRole("dialog", { name: "Terms: Point 16" });
    await within(terms).findByText("Point 16's own words.");
    fireEvent.click(within(terms).getByRole("button", { name: "Close the terms" }));
    cleanup();
  }
  vi.unstubAllGlobals();
  forgetPage();
  useStore.setState(useStore.getInitialState());
}

beforeEach(async () => { useStore.setState(useStore.getInitialState()); forgetPage(); forgetAccountReads(); await closedBefore("u2", "u1"); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); forgetPage(); forgetAccountReads(); });

describe("Claude Code's accounts in Connections, one row each", () => {
  it("lists each account once, in the relay's order, with its sign-in and its standing for tasks on the same row", async () => {
    ownersApp([
      owner("default", { position: 1 }),
      owner("aaaaaaaaaaaa", { position: 0, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" }),
    ]);
    workspace({ ws: { enabled: true, running: false, provider: "fly" }, accounts: accountsView({ spare: false }) });
    render(<Connections onClose={() => {}} />);
    await waitFor(() => expect(names()).toEqual(["Work", "Default", "Spare"]));
    expect(screen.getAllByRole("list", { name: /accounts/ })).toHaveLength(1);
    expect(screen.getAllByText("Work")).toHaveLength(1);                          // once: not a second list
    expect(screen.queryByText("Your accounts for tasks")).toBeNull();
    const work = itemOf("Work");
    expect(within(work).getByRole("radio", { name: "New Claude Code sessions start on Work" }).getAttribute("aria-checked")).toBe("false");
    expect(within(work).getByText("Signed in as me@work.example · team")).toBeTruthy();
    expect(work.textContent).toContain("Claude Team · Acme · Acme's seats, Team");
    expect(within(work).getByLabelText("In tasks' relays")).toBeTruthy();
    expect(within(work).getByRole("button", { name: "Move Work down" })).toBeTruthy();
    expect(within(work).getByLabelText("Sign Work out")).toBeTruthy();
    const own = itemOf("Default");
    expect(within(own).getByRole("radio", { name: "New Claude Code sessions start on Default" }).getAttribute("aria-checked")).toBe("true");
    expect(within(own).getByText("Signed in as me@example.org · max · 1 session")).toBeTruthy();
    expect(within(own).getByLabelText("Use to the limit")).toBeTruthy();
    const spare = itemOf("Spare");
    expect(within(spare).getByText("Not signed in")).toBeTruthy();
    expect(within(spare).getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(within(spare).queryByLabelText("Sign Spare out")).toBeNull();                 // signed out: Sign in, never Sign out
    expect(within(spare).getByText(/^Is this account yours\?/)).toBeTruthy();
    expect((within(spare).getByRole("button", { name: /Read who it is/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(spare).getByText("It is not signed in: sign it in, and who it is is then read by itself.")).toBeTruthy();
    expect(within(spare).queryByRole("button", { name: /Move Spare/ })).toBeNull();     // no row of the app's yet: no order
    // the add form and the note, once, under the one list
    expect(screen.getAllByRole("button", { name: /Add a Claude Code account/ })).toHaveLength(1);
    expect(screen.getByText(/Each account signs in on its own/)).toBeTruthy();
    // and every task's account record, folded, under the accounts in the same card, Claude Code's
    const records = screen.getByRole("button", { name: /Which accounts did the work, every task/ });
    expect(list().closest(".rounded-lg")!.contains(records)).toBe(true);
    expect(list().compareDocumentPosition(records) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says a task goes on on the next account of its group only where this deployment has the relay on, and nothing of it where it is off or the page cannot tell", async () => {
    // Connections reads the deployment's setting from the task list's answer; the note's relay clause is shown for a
    // relay known to be on, and for none else: with the relay off by default it said a move that is not made.
    // The note is checked whole, the rest of it standing whichever it is, and its clause follows the relay alone: the
    // relay on and off, each with the deployment's four other switches all off and all on, and on with the workspace
    // stopped
    const others = { auto_resume: true, teammate: true, cross_groups: true, local_provider: true };
    const cases: [string, Answer, boolean, boolean][] = [
      ["on", relayIs(true), true, true],
      ["on, every other switch on", [200, { tasks: [], settings: { relay: true, ...others } }], true, true],
      ["off", relayIs(false), false, true],
      ["off, every other switch on", [200, { tasks: [], settings: { relay: false, ...others } }], false, true],
      ["on, the workspace stopped", relayIs(true), true, false],
      ["refused", [500, { detail: "The app is away for a moment." }], false, true],
      ["not answered", undefined, false, true]];
    for (const [which, tasks, says, running] of cases) {
      const { calls } = ownersApp([owner("default")], {}, undefined, 0, tasks);
      workspace({ accounts: accountsView({ work: false, spare: false }), ws: { enabled: true, running, provider: "fly" } });
      render(<ClaudeAccounts />);
      await waitFor(() => expect(names()).toContain("Default"));
      await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path === "/api/ade/agents/tasks")).toBe(true));
      await settle();
      expect([which, screen.getByText(/^One row is one account/).textContent]).toEqual([which, says ? NOTE_ON : NOTE_OFF]);
      cleanup();
      vi.unstubAllGlobals();
      forgetPage();
      forgetAccountReads();
    }
  });

  it("says no move over the list where this deployment has the relay off, whether or not its read has landed", async () => {
    // checked with no wait for the read of the task list, so that a page that made no such read fails here at the
    // note's words, its note saying the move whatever the deployment set
    ownersApp([owner("default")], {}, undefined, 0, relayIs(false));
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(names()).toContain("Default"));
    expect(screen.getByText(/^One row is one account/).textContent).toBe(NOTE_OFF);
    await settle();
    expect(screen.getByText(/^One row is one account/).textContent).toBe(NOTE_OFF);
  });

  it("says no move over a task list answered with no settings, and throws nothing", async () => {
    // the app's route always gives `settings` (tasks.listing), but an answer without them, or with no body at all, is
    // read as a relay not on, as a refused read is: not as a TypeError in the read's own handler, which nothing catches
    const thrown: unknown[] = [];
    const caught = (reason: unknown) => { thrown.push(reason); };
    process.on("unhandledRejection", caught);
    try {
      for (const tasks of [[200, { tasks: [] }], [200, null]] as Answer[]) {
        const { calls } = ownersApp([owner("default")], {}, undefined, 0, tasks);
        workspace({ accounts: accountsView({ work: false, spare: false }) });
        render(<ClaudeAccounts />);
        await waitFor(() => expect(names()).toContain("Default"));
        await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path === "/api/ade/agents/tasks")).toBe(true));
        await settle();
        expect(screen.getByText(/^One row is one account/).textContent).toBe(NOTE_OFF);
        cleanup();
        vi.unstubAllGlobals();
        forgetPage();
        forgetAccountReads();
      }
      await settle();
      expect(thrown.map((e) => String(e))).toEqual([]);
    } finally {
      process.off("unhandledRejection", caught);
    }
  });

  it("says no move where the task list gives a relay that is not the boolean true", async () => {
    // the app's route gives the relay as a boolean (tasks.py, `on("relay")`): a string or a number in its place is not
    // a relay known to be on. The last case, the boolean true, shows the read landing in this test, so the others do
    // not pass for a read that never came
    const offs = { auto_resume: false, teammate: false, cross_groups: false, local_provider: false };
    const cases: [unknown, string][] = [["true", NOTE_OFF], [1, NOTE_OFF], ["false", NOTE_OFF], [true, NOTE_ON]];
    for (const [relay, note] of cases) {
      const { calls } = ownersApp([owner("default")], {}, undefined, 0, [200, { tasks: [], settings: { relay, ...offs } }]);
      workspace({ accounts: accountsView({ work: false, spare: false }) });
      render(<ClaudeAccounts />);
      await waitFor(() => expect(names()).toContain("Default"));
      await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path === "/api/ade/agents/tasks")).toBe(true));
      await settle();
      expect([relay, screen.getByText(/^One row is one account/).textContent]).toEqual([relay, note]);
      cleanup();
      vi.unstubAllGlobals();
      forgetPage();
      forgetAccountReads();
    }
  });

  it("reads who a signed-in account is by itself, once and one at a time, while the workspace runs; one read before is not read again", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") }, held);
    workspace();
    render(<ClaudeAccounts />);
    // Work and Spare are signed in with no identity read: Work first, and Spare only once Work's read has ended
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    expect(within(itemOf("Work")).getByText("Reading who it is from its start…")).toBeTruthy();
    expect((within(itemOf("Work")).getByRole("button", { name: /Read who it is/ }) as HTMLButtonElement).disabled).toBe(true);
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    expect(within(itemOf("Spare")).queryByText("Reading who it is from its start…")).toBeNull();
    // the store's rows read again twice while Spare waits its turn, as every few seconds: it is not queued again
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    letGo();
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(within(itemOf("Work")).queryByText("Reading who it is from its start…")).toBeNull();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    // the store's rows read again, as every few seconds: nothing is read twice, and Default, read before, is not read
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]);
  });

  it("reads nothing by itself while the workspace is stopped, and a press of Read who it is says the app's refusal", async () => {
    const { calls } = ownersApp([], { aaaaaaaaaaaa: seat("me@work.example") });
    workspace({ ws: { enabled: true, running: false, provider: "fly" } });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ })).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([]);
    fireEvent.click(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    expect((await screen.findByRole("alert")).textContent).toBe("Start the workspace first: an account's identity is read there.");
    expect(within(itemOf("Work")).queryByRole("button", { name: "Yes, it is mine" })).toBeNull();
  });

  it("reads an account not confirmed again by itself once it signs in again, though it was read before; a read that fails is said, once", async () => {
    const given: Record<string, IdentityReport | null> = { aaaaaaaaaaaa: seat("me@work.example") };
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("old@work.example"), grp: null, grp_kind: null })], given);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText("Not signed in")).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([]);                                                 // signed out: nothing to read
    // signed in again: read again, and the identity it reports since shown
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("me@work.example")).toBeTruthy());
    // Spare signs in, and the workspace finds it not signed in (the app's 400): said once, on its row, and the button stays
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    expect(await screen.findByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    await settle();
    expect(reports(calls)).toHaveLength(2);
    expect((within(itemOf("Spare")).getByRole("button", { name: /Read who it is/ }) as HTMLButtonElement).disabled).toBe(false);
    // Work, read by itself in this very list, signs out and in again: read again; and Spare's failure, said on its
    // row, outlives the owners' read that Work's success brings
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    await waitFor(() => expect(within(itemOf("Work")).getByText("Not signed in")).toBeTruthy());
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }, { account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    await settle();
    expect(within(itemOf("Spare")).getByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toHaveLength(3);
    // Read who it is pressed on Spare, which has a report to give now: the failure goes with the press, and the report shows
    given.bbbbbbbbbbbb = personal("spare@example.org", "Claude Pro");
    fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Read who it is/ }));
    expect(within(itemOf("Spare")).queryByText(/could not be read by itself/)).toBeNull();
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(within(itemOf("Spare")).queryByText(/could not be read by itself/)).toBeNull();
    expect(reports(calls)).toHaveLength(4);
  });

  it("clears a failure once the page sees the account signed out, and keeps each account's own", async () => {
    // Work and Spare signed in, and the workspace finding neither signed in (the app's 400): a failure each, both said
    const given: Record<string, IdentityReport | null> = {};
    const { calls } = ownersApp([owner("default")], given);
    workspace();
    render(<ClaudeAccounts />);
    expect(await screen.findByText(/^Work's identity could not be read by itself: /)).toBeTruthy();
    expect(await screen.findByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]);
    // Spare signed out: its failure goes, since the row now says its sign-in reads it; Work's stays
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(within(itemOf("Spare")).getByText("It is not signed in: sign it in, and who it is is then read by itself.")).toBeTruthy());
    expect(within(itemOf("Spare")).queryByText(/could not be read by itself/)).toBeNull();
    expect(within(itemOf("Work")).getByText(/^Work's identity could not be read by itself: /)).toBeTruthy();
    // signed in again, and found signed in with a report now: read again, and nothing of the failure
    given.bbbbbbbbbbbb = personal("spare@example.org", "Claude Pro");
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(within(itemOf("Spare")).queryByText(/could not be read by itself/)).toBeNull();
    expect(within(itemOf("Work")).getByText(/^Work's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toHaveLength(3);
  });

  it("keeps the page's record of the reads across a sign-out and the same account's sign-in again, and forgets it when another account signs in", async () => {
    // Work read before; Spare signed in, and the workspace finding it not signed in (400): its failure is the record's
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" })]);
    workspace();
    render(<ClaudeAccounts />);
    expect(await screen.findByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]);
    const rowsAgain = async () => { act(() => useStore.setState({ accounts: accountsView() })); await settle(); };
    // the same account as the store hands it at each sign-in, a new object with the same id: nothing forgotten
    act(() => useStore.setState({ user: { id: "u1", email: "owner@example.org", can_sign_in_again: true } }));
    await rowsAgain();
    expect(within(itemOf("Spare")).getByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toHaveLength(1);
    // signed out, then in again as the same account: kept, so Spare is not read again
    act(() => useStore.setState({ user: null }));
    act(() => useStore.setState({ user: { id: "u1", email: "owner@example.org", can_sign_in_again: true } }));
    await rowsAgain();
    expect(within(itemOf("Spare")).getByText(/^Spare's identity could not be read by itself: /)).toBeTruthy();
    expect(reports(calls)).toHaveLength(1);
    // another account signs in on the page: forgotten, so the list says nothing of the first's failure, and reads Spare by itself
    act(() => useStore.setState({ user: { id: "u2", email: "other@example.org", can_sign_in_again: true } }));
    expect(within(itemOf("Spare")).queryByText(/could not be read by itself/)).toBeNull();
    await rowsAgain();
    expect(reports(calls)).toHaveLength(2);
  });

  it("lets a read under way when another account signs in end without a word, and reads nothing queued behind it", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") }, held);
    workspace();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));         // Work's read held; Spare's waits its turn
    // another account signs in, its workspace listing Work signed in and Spare out: Work is read for it at once
    act(() => useStore.setState({ user: { id: "u2", email: "other@example.org", can_sign_in_again: true }, accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    const owners = () => calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length;
    await settle();
    const before = owners();
    // the first account's read ends: nothing of it on this page (no owners' read on its account), and Spare, queued
    // behind it on the first account's chain, is not read
    letGo();
    await settle();
    await settle();
    expect(reports(calls)).toHaveLength(2);
    expect(owners()).toBe(before);
    expect(within(itemOf("Work")).queryByText("Reading who it is from its start…")).toBeNull();
    expect(screen.queryByText(/could not be read by itself/)).toBeNull();
  });

  it("says, of a held account whose read again found a mismatch, that its wait ends without it joining, with Cancel the wait", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "held", in_relays: false, mismatch: "email", hold: { id: "h1", kind: "join", takes_effect_at: Date.now() / 1000 + 600 } })]);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    const work = await waitFor(() => itemOf("Work"));
    await waitFor(() => expect(within(work).getByRole("button", { name: /Cancel the wait/ })).toBeTruthy());
    // the mismatch, which decides it, before the wait: the words the New task form's pointer sends the reader to
    expect(work.textContent).toMatch(/It cannot be confirmed: its sign-in's own record disagrees with what its start reported, on its email\. It is in no relay\. It was confirmed before this read: its wait to join your tasks' relay ends at \d\d:\d\d without it joining, unless a read again finds it as it was confirmed\./);
    expect(work.textContent).not.toContain("unless you cancel it first");
    expect(within(work).queryByRole("button", { name: "Yes, it is mine" })).toBeNull();
    expect(within(work).getByRole("button", { name: /Read it again/ })).toBeTruthy();
  });

  it("goes on with a read while Connections is closed, and opening it again starts no second read of the account", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") }, held);
    workspace({ accounts: accountsView({ spare: false }) });
    const first = render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    first.unmount();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText("Reading who it is from its start…")).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);                     // the read under way, not a second
    letGo();
    // the read's end reaches this list: the owners read again, and Work's report shown
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(within(itemOf("Work")).queryByText("Reading who it is from its start…")).toBeNull();
    expect(reports(calls)).toHaveLength(1);
  });

  it("tries the owners' read again at the store's next read of the accounts when the first failed, and only then reads by itself", async () => {
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") }, undefined, 1);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    expect(await screen.findByText(/away for a moment/)).toBeTruthy();
    await settle();
    expect(reports(calls)).toEqual([]);                                                  // no owners: no read by itself
    expect(calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts")).toHaveLength(1);
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(screen.queryByText(/away for a moment/)).toBeNull();
  });

  it("does not read by itself an account whose sign-in the store has not read", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1 })], { bbbbbbbbbbbb: personal("spare@example.org") });
    workspace({ accounts: accountsView({ spare: null }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(itemOf("Spare")).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(within(itemOf("Spare")).queryByText("Reading who it is from its start…")).toBeNull();
  });

  it("reads the relay's rows again after an act the app refused, since a refusal can come with a change", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "held", in_relays: false, hold: { id: "h1", kind: "join", takes_effect_at: Date.now() / 1000 + 3600 } })]);
    // Spare signed out, and both others read before: no read by itself, whose end would read the owners again anyway
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    const cancel = await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: /Cancel/ });
    const owners = () => calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length;
    await settle();
    expect(owners()).toBe(1);
    fireEvent.click(cancel);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("Its time came at 2026-10-13 10:00:00 UTC: it has taken effect.");
    await waitFor(() => expect(within(itemOf("Work")).getByLabelText("In tasks' relays")).toBeTruthy());
    expect(within(itemOf("Work")).queryByRole("button", { name: /Cancel/ })).toBeNull();
    expect(calls.filter((c) => c.method === "POST" && c.path.endsWith("/cancel"))).toHaveLength(1);
    expect(reports(calls)).toEqual([]);
    expect(owners()).toBe(2);                                                            // the refusal's own read, no other
  });

  it("shows a running sign-in under its own account on the one list: Claude's link, and the field for its code", async () => {
    ownersApp([owner("default")]);
    const signing = { id: "111111111111", program: "claude-sign-in", repo: null, branch: null, base: null, task: "Sign in to Claude Code",
      created_at: 1, running: true, finished: null, exit_code: null, ended_at: null, chat: null, account: "bbbbbbbbbbbb", sign_in: true,
      sign_in_url: "https://claude.com/cai/oauth/authorize?code=true&state=x" } as SessionRecord;
    workspace({ accounts: accountsView({ spare: false }), signIns: [signing] });
    render(<ClaudeAccounts />);
    const spare = await waitFor(() => itemOf("Spare"));
    expect(within(spare).getByText(/Open Claude's sign-in page/).closest("a")!.getAttribute("href")).toBe("https://claude.com/cai/oauth/authorize?code=true&state=x");
    expect(within(spare).getByLabelText("The code Claude's page shows")).toBeTruthy();
    expect(screen.getAllByText(/Open Claude's sign-in page/)).toHaveLength(1);       // under its own account alone
  });

  it("chooses for Claude Code, starts each sign-in as Claude Code's, in place of a stuck one and for an account added, and shows a row's windows", async () => {
    // what Accounts.test.tsx held of Claude Code while it rendered AccountRows for it, which the product does for Codex
    // alone: the same acts, on the one list the product shows
    ownersApp([owner("default")]);
    const view = accountsView({ spare: false });
    view.claude.accounts[0].usage = { at: 1, five_hour: { used_percentage: 97, resets_at: null } };
    const stuck = { id: "777777777777", program: "claude-sign-in", repo: null, branch: null, base: null, task: "Sign in to Claude Code",
      created_at: 1, running: true, finished: null, exit_code: null, ended_at: null, chat: null, account: "bbbbbbbbbbbb", sign_in: true } as SessionRecord;
    workspace({ accounts: view, signIns: [stuck] });
    const chose = vi.spyOn(Api, "chooseAccount").mockResolvedValue({ kind: "claude", active: "aaaaaaaaaaaa" });
    const added = vi.spyOn(Api, "addAccount").mockResolvedValue({ id: "cccccccccccc", kind: "claude", name: "Third", created_at: 1 });
    const removed = vi.spyOn(Api, "removeSession").mockResolvedValue({ removed: stuck.id });
    const started = vi.spyOn(Api, "createSession").mockResolvedValue({ ...stuck, id: "888888888888" });
    render(<ClaudeAccounts />);
    expect(within(await waitFor(() => itemOf("Default"))).getByTitle("5h window: 97% used")).toBeTruthy();
    fireEvent.click(within(itemOf("Work")).getByLabelText("New Claude Code sessions start on Work"));
    await waitFor(() => expect(chose).toHaveBeenCalledWith("claude", "aaaaaaaaaaaa"));
    await waitFor(() => expect((within(itemOf("Spare")).getByRole("button", { name: /Start again/ }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Start again/ }));
    await waitFor(() => expect(started).toHaveBeenCalledTimes(1));
    expect(started).toHaveBeenCalledWith({ repo: null, program: "claude-sign-in", task: "Sign in to Claude Code", account: "bbbbbbbbbbbb" });
    expect(removed).toHaveBeenCalledWith(stuck.id);
    expect(removed.mock.invocationCallOrder[0]).toBeLessThan(started.mock.invocationCallOrder[0]);
    const add = () => screen.getByRole("button", { name: /Add a Claude Code account/ }) as HTMLButtonElement;
    await waitFor(() => expect(add().disabled).toBe(false));
    fireEvent.click(add());
    fireEvent.change(screen.getByLabelText("The new Claude Code account's name"), { target: { value: " Third " } });
    fireEvent.click(screen.getByRole("button", { name: /Add and sign in/ }));
    await waitFor(() => expect(started).toHaveBeenCalledTimes(2));
    expect(added).toHaveBeenCalledWith("claude", "Third");
    expect(started).toHaveBeenLastCalledWith({ repo: null, program: "claude-sign-in", task: "Sign in to Claude Code", account: "cccccccccccc" });
  });
});

describe("what the page reads by itself, and what it leaves to a press", () => {
  it("never reads by itself an account confirmed or waiting to join, though the page sees it signed out and in again", async () => {
    const { calls } = ownersApp([owner("default"),
      owner("aaaaaaaaaaaa", { position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" }),
      owner("bbbbbbbbbbbb", { position: 2, state: "held", in_relays: false, report: personal("spare@example.org", "Claude Pro"),
                              hold: { id: "h2", kind: "join", takes_effect_at: Date.now() / 1000 + 600 } })],
      { aaaaaaaaaaaa: null, bbbbbbbbbbbb: null });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText("Not signed in")).toBeTruthy());
    // signed out, each is named by the email its start reported (the app's rows, read after the store's), which its
    // sign-in's line no longer says
    await waitFor(() => expect(within(itemOf("Work")).getByText(/me@work\.example/)).toBeTruthy());
    await waitFor(() => expect(within(itemOf("Spare")).getByText(/spare@example\.org/)).toBeTruthy());
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    await settle();
    // no read, which would start Work's and Spare's claude to learn what their confirmations hold already; and the
    // email once, where the sign-in's own line says it again
    expect(reports(calls)).toEqual([]);
    expect(within(itemOf("Work")).getByLabelText("In tasks' relays")).toBeTruthy();
    expect(within(itemOf("Spare")).getByRole("button", { name: /Cancel/ })).toBeTruthy();
    expect(within(itemOf("Work")).getAllByText(/me@work\.example/)).toHaveLength(1);
  });

  it("reads nothing by itself on the person's own machine (the local provider), where Read who it is reads at a press", async () => {
    const { calls } = ownersApp([], { default: personal("me@example.org"), aaaaaaaaaaaa: seat("me@work.example") });
    workspace({ ws: { enabled: true, running: true, provider: "local" }, accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ })).toBeTruthy());
    // the app's rows read (none), so that a read by itself would have been queued by now
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length).toBeGreaterThan(0));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(within(itemOf("Spare")).getByText("It is not signed in: sign it in, then read who it is.")).toBeTruthy();
    fireEvent.click(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
  });

  it("says of a read whose start reported no email and plan the app keeps that it did not, and does not read that account by itself again", async () => {
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: null });
    workspace({ accounts: accountsView({ spare: false }) });
    const first = render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(itemOf("Work").textContent).toContain("It cannot be confirmed: its start did not report both the email and the plan the app confirms it by. It is in no relay."));
    expect(itemOf("Work").textContent).not.toContain("disagrees");
    expect(within(itemOf("Work")).queryByRole("button", { name: "Yes, it is mine" })).toBeNull();
    expect(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ })).toBeTruthy();
    // a page that looks afresh (its record forgotten, the list mounted again): the app has Work's row now, with no
    // report, and the page has not seen Work signed out, so it is not read by itself again
    first.unmount();
    forgetAccountReads();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(itemOf("Work").textContent).toContain("did not report both the email and the plan"));
    await settle();
    expect(reports(calls)).toHaveLength(1);
  });

  it("takes a sign-in the store has not read for nothing: an account the app has, never seen signed out, is not read when its sign-in is", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") });
    workspace({ accounts: accountsView({ work: null, spare: null }) });
    render(<ClaudeAccounts />);
    // the app's rows read (Work's own row asks it), so that the page has looked at both sign-ins not read yet
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([]);
    // the sign-ins read, both signed in: Spare, which the app has no row for, is read; Work, read before and never seen
    // signed out, is not
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]);
  });

  it("reads once an account whose Read who it is is pressed while it waits its turn", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") }, held);
    workspace();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));          // Work's read held; Spare waits its turn
    fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Read who it is/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    // the press took its read off the queue, so its Yes, it is mine waits for nothing
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
    letGo();
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]);   // Spare once: the press's
  });

  it("does not read by itself an account whose confirmation is open here, and reads it once that is closed", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("other@work.example") });
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // signed out and in again (as another address, say) while the owner reads the confirmation: not read under it
    act(() => useStore.setState({ accounts: accountsView({ work: false, spare: false }) }));
    await settle();
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    // the confirmation closed: read by itself, and the report it gives is the one shown
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("other@work.example")).toBeTruthy());
  });

  /** Work, which the app has no row for, read by itself first, its read held until the test lets it go; and Spare,
   *  reported, seen signed out and then in again, queued behind it, its Yes, it is mine there to press. */
  const spareBehindWork = async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const app = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    const { unmount } = render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    await settle();
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(app.calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    return { ...app, letGo, unmount };
  };
  const workRead = () => waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
  /** As spareBehindWork, but with Spare's Yes, it is mine pressed while it is signed out, before its read is queued,
   *  its confirmation's ways held until the test lets them go (`letWaysGo`), so that its read is queued while the press
   *  opens: the press waits while its account's read waits its turn, so a
   *  confirmation over a read to come is pressed for before that read is queued. */
  const spareOpeningBehindWork = async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const app = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    const { unmount } = render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    await settle();
    const letWaysGo = app.gateWays();
    pressYes("Spare");
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(app.calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(true));      // its read queued behind Work's
    return { ...app, letGo, letWaysGo, unmount };
  };

  it("does not read by itself an account queued behind another's read while its confirmation, opened before that read ends, stays open; and reads it once that is closed", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    // Spare's confirmation opens while Work's read is under way, its read queued while its press opened
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    // Work's read ended with Spare's confirmation open: Spare is not read, nor said to be, for as long as it stays open
    // (that its turn comes and passes it by, the reads behind it going on, is "reads by itself an account queued behind
    // one whose confirmation is open, while that one waits", below)
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    expect(within(itemOf("Spare")).queryByText("Reading who it is from its start…")).toBeNull();
    // the store's rows read again meanwhile, as every few seconds: still not
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // the confirmation closed: queued again, and read by itself; the row shows the report it gives
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(within(within(itemOf("Spare")).getByLabelText("What its start reported")).getByText("other@example.org")).toBeTruthy());
  });

  it("does not read by itself an account whose Yes, it is mine was pressed, though its confirmation has not opened yet", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    // pressed before its read was queued, the confirmation's ways slow to come: nothing has opened when Spare's turn
    // comes
    letGo();
    await workRead();
    await settle();
    await settle();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // it opens over the report the row showed, nothing read under it; closed, Spare is read
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("reads by itself an account its turn passed by while its Yes, it is mine was opening, once that press opened nothing, its confirmation's ways not to be had", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    // Work's read ends while Spare's press reads its ways: Spare's turn passes it by
    letGo();
    await workRead();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // the ways cannot be had: nothing opens, and Spare's read is queued again, and made
    letWaysGo([500, { detail: "The app is away for a moment." }]);
    expect(await screen.findByText("The app is away for a moment.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("makes a read its turn passed by, for its account's confirmation, once Connections closes with that confirmation open", async () => {
    const { calls, letGo, unmount, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // Connections closes, and the confirmation with it: Spare's read, queued while it was open, is made
    unmount();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("keeps a read set aside while the workspace stops and runs again, its confirmation open, and makes it once Connections closes", async () => {
    const { calls, letGo, letWaysGo, unmount } = await spareOpeningBehindWork();
    // Work's read ends: Spare's turn passes it by while its press opens, and its read is set aside
    letGo();
    await workRead();
    await settle();
    // the workspace stops, Spare's confirmation opens, and the workspace runs again: the read stays set aside (were it
    // queued again as the confirmation opened, its turn would come with the workspace stopped, and drop it)
    act(() => useStore.setState({ ws: { enabled: true, running: false, provider: "fly" } }));
    await settle();
    letWaysGo();
    await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    await settle();
    act(() => useStore.setState({ ws: { enabled: true, running: true, provider: "fly" } }));
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // Connections closes with the confirmation open: the read set aside is made
    unmount();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("makes the read of an account whose Yes, it is mine was still reading its ways when Connections closed", async () => {
    const { calls, letGo, letWaysGo, unmount } = await spareOpeningBehindWork();
    // Connections closes before the ways come; when they come, nothing of the press is kept
    unmount();
    letWaysGo();
    await settle();
    // so Spare, queued behind Work, is read in its turn
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("keeps Yes, it is mine waiting on a list opened again while a read queued under the earlier one waits its turn, and offers it once that read is made", async () => {
    const { calls, letGo, unmount } = await spareBehindWork();
    // Connections closes and opens again, Spare's read still waiting its turn behind Work's: its Yes, it is mine waits
    // on the list opened again too, the reads being the page's. A press there opens no confirmation, under which the
    // read would be set aside
    unmount();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(true));
    letGo();
    await workRead();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
  });

  /** The workspace's own account, which the app has no row for, read by itself first, its read held until the test lets
   *  it go; and Work and Spare, reported, seen signed out and then in again, queued behind it, Work first. A press of
   *  Yes, it is mine is made on each row `presses` names, in that order, while Work and Spare are signed out, before
   *  their reads are queued, each press's ways held until the test lets them go (`ways`, by row): a press waits while
   *  its account's read waits its turn, so a press over a read to come is
   *  made before that read is queued, which is then queued while the press opens. */
  const workAndSpareOpeningBehindDefault = async (presses: ("Work" | "Spare")[]) => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const app = ownersApp([owner("aaaaaaaaaaaa", { position: 0, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null }),
                           owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { default: personal("me@example.org"), aaaaaaaaaaaa: seat("other@work.example"), bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(app.calls)).toEqual([{ account: "default" }]));
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    await settle();
    const ways: Record<string, (given?: Answer) => void> = {};
    for (const name of presses) {
      ways[name] = app.gateWays();
      pressYes(name);
    }
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    return { ...app, letGo, ways };
  };

  it("reads by itself an account queued behind one whose confirmation is open, while that one waits", async () => {
    const { calls, letGo, ways } = await workAndSpareOpeningBehindDefault(["Work"]);
    // Work's confirmation opens before the first read ends, its read queued while its press opened
    ways.Work();
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    letGo();
    // Work's turn passes it by, and Spare, behind it, is read while Work's confirmation stays open
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]));
    await settle();
    expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]);
    // closed: Work, set aside, is read then
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }, { account: "aaaaaaaaaaaa" }]));
  });

  it("lets a press of Yes, it is mine on another row take over one still reading its ways: the first opens nothing, and its account is read in its turn", async () => {
    // Spare's Yes, it is mine pressed, its ways slow to come; then Work's, taking over, both before either read is
    // queued; Work's ways come before the first read ends
    const { calls, letGo, ways } = await workAndSpareOpeningBehindDefault(["Spare", "Work"]);
    ways.Work();
    await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // the first read ends: Work, its confirmation open, is passed by, and Spare, whose press gave way, is read
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]));
    // Spare's ways come: its press was taken over, so nothing of it opens, and Work's confirmation stays
    ways.Spare();
    await settle();
    await settle();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
    expect(screen.getByRole("group", { name: "Confirming “Work” as your own" })).toBeTruthy();
  });

  it("queues again at once the read of an account its turn passed by while its press was opening, when a press on another row takes over", async () => {
    // two presses, each's ways held until let go: Spare's, made before its read is queued, and Work's, to come
    const { calls, letGo, gateWays, ways } = await workAndSpareOpeningBehindDefault(["Spare"]);
    const letSparesGo = ways.Spare;
    const letWorksGo = gateWays();
    // the first read ends: Work, which nothing names, is read; Spare's turn passes it by, its press still opening
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(yesOf("Work").disabled).toBe(false));
    await settle();
    // Work's Yes, it is mine pressed: Spare's press gives way, and Spare's read is queued again and made at once,
    // Work's ways still to come
    pressYes("Work");
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    // the ways come: Work's confirmation opens, and nothing of Spare's press does
    letSparesGo();
    letWorksGo();
    await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    await settle();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
  });

  it("keeps a press of Yes, it is mine that took over one whose ways then fail: nothing of the first is said, the second's account is not read while its ways are read, and its confirmation opens", async () => {
    // Spare's Yes, it is mine pressed, its ways slow and then to fail; then Work's, taking over, its ways slow too; both
    // before either read is queued
    const { calls, letGo, ways } = await workAndSpareOpeningBehindDefault(["Spare", "Work"]);
    const failSpares = ways.Spare;
    const letWorksGo = ways.Work;
    // Spare's ways cannot be had: its press gave way to Work's, so nothing of it is said, and Work's goes on
    failSpares([500, { detail: "The app is away for a moment." }]);
    await settle();
    await settle();
    expect(screen.queryByText("The app is away for a moment.")).toBeNull();
    // the first read ends: Work, its press still opening, is passed by, and Spare, which nothing names, is read
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]));
    await settle();
    expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]);
    // Work's ways come: its confirmation opens
    letWorksGo();
    await screen.findByRole("group", { name: "Confirming “Work” as your own" });
  });

  it("reads nothing set aside for the page's account once another account has signed in on the page", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    // another account signs in on this browser: what the first one set aside is forgotten, so the confirmation's close
    // reads nothing for the second
    act(() => useStore.setState({ user: { id: "u2", email: "other@example.org", can_sign_in_again: true } }));
    await settle();
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
  });

  it("drops a read set aside once the page sees its account signed out, and reads it at its sign-in again", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    // Spare signs out while its read is set aside, and the confirmation closes: no read of an account signed out
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // signed in again: read
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("drops a read set aside whose confirmation opens while another read runs, once the page sees its account signed out, and reads it at its sign-in again", async () => {
    // Work's Yes, it is mine pressed before its read is queued, its ways slow to come; Spare's read, behind Work's turn,
    // held at its report
    const { calls, letGo, gateReport, gateOwners, ways } = await workAndSpareOpeningBehindDefault(["Work"]);
    const letWaysGo = ways.Work;
    const letSparesReportGo = gateReport();
    // the first read ends: Work's turn passes it by, its press opening, and Spare's read starts
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]));
    // Work's confirmation opens while Spare's read runs, and Work's read stays set aside: queued again then, it would
    // wait its turn behind Spare's read, where the page's sight of a sign-out does not drop it
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // Work signs out; Spare's read ends, the app's rows slow to come after it, so the list looks at the accounts no
    // more until the confirmation closes: no read of an account signed out
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    await settle();
    expect(within(itemOf("Spare")).getByText("Reading who it is from its start…")).toBeTruthy();
    const letOwnersGo = gateOwners();
    letSparesReportGo();
    await waitFor(() => expect(within(itemOf("Spare")).queryByText("Reading who it is from its start…")).toBeNull());
    await settle();
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }]);
    // the rows come, and Work signs in again: read
    letOwnersGo();
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "default" }, { account: "bbbbbbbbbbbb" }, { account: "aaaaaaaaaaaa" }]));
  });

  it("drops a read set aside once the page sees its account signed out, and makes none when the press it was set aside for then fails; reads it at its sign-in again", async () => {
    // Spare's Yes, it is mine pressed before its read was queued, its ways slow to come and then to fail
    const { calls, letGo, letWaysGo: failWays } = await spareOpeningBehindWork();
    // Work's read ends: Spare's turn passes it by while its press opens, and its read is set aside
    letGo();
    await workRead();
    await settle();
    // Spare signs out while its read is set aside: the read is dropped
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    // the ways cannot be had: nothing opens, and no read is made of Spare, signed out (a press's end that changes no
    // confirmation brings no look at the accounts, so only the drop at the sign-out keeps the read from being made)
    failWays([500, { detail: "The app is away for a moment." }]);
    expect(await screen.findByText("The app is away for a moment.")).toBeTruthy();
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // signed in again: read
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("drops a read waiting its turn once the page sees its account signed out, though its Yes, it is mine is opening when that turn comes, and reads it at its sign-in again", async () => {
    // Spare's Yes, it is mine pressed before its read was queued behind Work's, its ways slow to come and then to fail
    const { calls, letGo, letWaysGo: failWays, gateOwners } = await spareOpeningBehindWork();
    // Spare signs out while its read still waits its turn: the read is dropped, not set aside at that turn
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    // Work's read ends, the app's rows slow to come after it, so the list does not look at the accounts again; Spare's
    // turn comes with its press still opening
    expect(within(itemOf("Work")).getByText("Reading who it is from its start…")).toBeTruthy();
    const letOwnersGo = gateOwners();
    letGo();
    await waitFor(() => expect(within(itemOf("Work")).queryByText("Reading who it is from its start…")).toBeNull());
    await settle();
    // the ways cannot be had: nothing opens, and no read is made of Spare, signed out
    failWays([500, { detail: "The app is away for a moment." }]);
    expect(await screen.findByText("The app is away for a moment.")).toBeTruthy();
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // the rows come, and Spare signs in again: read
    letOwnersGo();
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("makes no read by itself, once its confirmation closes, of an account set aside that a press of Read it again read meanwhile", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    // the owner's press reads Spare at once, its confirmation still open
    fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Read it again/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    // closed: the read set aside is not made as well
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]);
  });

  it("confirms, over a read set aside, the report the row showed; the read queued again then finds the account waiting to join, and is not made", async () => {
    const { calls, letGo, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    // the owner confirms what the row shows, from before Spare's sign-in again
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: /Cancel/ })).toBeTruthy());
    const confirms = () => calls.filter((c) => c.method === "POST" && c.path === `/api/ade/agents/accounts/${SPARE}/confirm`).map((c) => c.body);
    expect(confirms()).toEqual([{ confirmation: "the-confirmation", report: personal("spare@example.org", "Claude Pro"), org_id: null }]);
    // the read set aside is queued again, and decided at its turn by the app's rows, which hold it waiting to join: after
    // the confirmation, the app's rows are read three times (the list's own read then, the read's look at its turn, and
    // the list's read again once that look has ended), and no report is made
    const posted = calls.findIndex((c) => c.method === "POST" && c.path === `/api/ade/agents/accounts/${SPARE}/confirm`);
    const ownersRead = () => calls.slice(posted + 1).filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length;
    await waitFor(() => expect(ownersRead()).toBe(3));
    await settle();
    await settle();
    expect(ownersRead()).toBe(3);
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
  });

  it("makes the read set aside once its confirmation is refused for a read made since on another page", async () => {
    const { calls, letGo, rows, letWaysGo } = await spareOpeningBehindWork();
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    letGo();
    await workRead();
    await settle();
    // read again on another page while the key is given: the app refuses the confirmation, and the read set aside is
    // made
    Object.assign(rows.find((r) => r.account === "bbbbbbbbbbbb")!, { report: personal("third@example.org", "Claude Pro") });
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(itemOf("Spare")).getByText(READ_SINCE)).toBeTruthy());
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("keeps Yes, it is mine waiting while its account's read by itself is under way, as a press's read does", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    const yes = () => within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" }) as HTMLButtonElement;
    await waitFor(() => expect(yes().disabled).toBe(false));
    await settle();
    // signed in again: read by itself, its read held; the row says so, and Yes, it is mine waits for it
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(within(itemOf("Spare")).getByText("Reading who it is from its start…")).toBeTruthy());
    expect(yes().disabled).toBe(true);
    fireEvent.click(yes());
    await settle();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
    // the read ends: the row shows the report as read now, and Yes, it is mine is there to press for it
    letGo();
    await waitFor(() => expect(within(within(itemOf("Spare")).getByLabelText("What its start reported")).getByText("other@example.org")).toBeTruthy());
    await waitFor(() => expect(yes().disabled).toBe(false));
  });

  it("keeps Yes, it is mine waiting while its account's read by itself waits its turn, and offers it once that read is made", async () => {
    const { calls, letGo } = await spareBehindWork();
    // Spare's read waits its turn behind Work's, so its Yes, it is mine waits, the report it would name about to be
    // read again, and a press opens nothing
    expect(yesOf("Spare").disabled).toBe(true);
    fireEvent.click(yesOf("Spare"));
    await settle();
    expect(screen.queryByRole("group", { name: "Confirming “Spare” as your own" })).toBeNull();
    // Work's read ends, then Spare's is made: the row shows the report as read now, and Yes, it is mine is there for it
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(within(within(itemOf("Spare")).getByLabelText("What its start reported")).getByText("other@example.org")).toBeTruthy());
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
  });

  it("keeps Yes, it is mine waiting from the moment its account's read is queued, behind a read already under way", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    // Work's read by itself under way, held; Spare signed out, its Yes, it is mine there to press
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
    // Spare signs in again: its read is queued behind Work's, which no other read's start follows, and its Yes waits
    // from then
    act(() => useStore.setState({ accounts: accountsView() }));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(true));
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
  });

  it("offers Yes, it is mine again at once where the page drops a read waiting its turn, its account seen signed out", async () => {
    const { calls } = await spareBehindWork();
    expect(yesOf("Spare").disabled).toBe(true);
    // Spare signs out while its read waits its turn behind Work's: the read is dropped, so its Yes waits for nothing
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
    pressYes("Spare");
    await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
  });

  it("offers Yes, it is mine again once the page's reads are forgotten, a read that waited its turn among them", async () => {
    await spareBehindWork();
    expect(yesOf("Spare").disabled).toBe(true);
    act(() => forgetAccountReads());
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
  });

  it("lists the app's rows, each with its email and a wait's Cancel, while the store has no list of the workspace's accounts", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { name: "Work", position: 1, state: "held", in_relays: false, report: seat("me@work.example"),
                                                         hold: { id: "h1", kind: "join", takes_effect_at: Date.now() / 1000 + 600 } })]);
    workspace({ accounts: null });
    render(<ClaudeAccounts />);
    const work = await waitFor(() => itemOf("Work"));
    expect(within(work).getByRole("button", { name: /Cancel/ })).toBeTruthy();
    expect(within(work).getByText(/me@work\.example/)).toBeTruthy();
    expect(within(work).getByText("The workspace's accounts are not read yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Add a Claude Code account/ })).toBeNull();
  });

  it("keeps the record of a read under way when the page's account signed in before Connections loaded, as the app loads it", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") }, held);
    workspace({ accounts: accountsView({ spare: false }) });
    // fresh copies of the page's modules, the page's account signed in before Connections loads (the app imports it
    // lazily, after the sign-in)
    vi.resetModules();
    const fresh = await import("../store");
    const st = useStore.getState();
    fresh.useStore.setState({ user: st.user, ws: st.ws, claude: st.claude, accounts: st.accounts, programs: st.programs, signIns: st.signIns,
      refresh: st.refresh, refreshClaude: st.refreshClaude, refreshAccounts: st.refreshAccounts, refreshQuota: st.refreshQuota });
    const page = await import("./Connections");
    render(<page.ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    // the store's first change since the load, of any kind: not another account signing in, so the read under way is
    // kept, and Work is not read a second time
    act(() => fresh.useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    letGo();
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(reports(calls)).toHaveLength(1);
  });

  it("does not read by itself, once it is confirmed here, an account that signed in again while its confirmation was open", async () => {
    const { calls, gateOwners } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: null });
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // signed out and in again while the confirmation is open: no read under it
    act(() => useStore.setState({ accounts: accountsView({ work: false, spare: false }) }));
    await settle();
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await settle();
    // the owner confirms it, and the app holds it, waiting to join; the list's own read of the rows after it is slow,
    // as a network's is, so the page renders the closed confirmation over its older copy (Work, reported) and queues a
    // read: decided at the read by the app's rows, it is not made, since its start giving nothing would void the
    // confirmation just given
    const letRowsGo = gateOwners();
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Confirming “Work” as your own" })).toBeNull());
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length).toBeGreaterThanOrEqual(3));
    await settle();
    letRowsGo();
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: /Cancel/ })).toBeTruthy());
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(itemOf("Work").textContent).not.toContain("cannot be confirmed");
  });

  it("confirms the report its row showed at Yes, it is mine: read again while the confirmation is open, the app refuses it, the confirmation closes, and the row shows the new report with the app's words, to confirm again", async () => {
    // a seat's row, whose orgId the confirmation names beside its report
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), org_id: "org-acme", grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("other@work.example") });
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // read again while the owner gives the key: the list holds the new report
    fireEvent.click(within(itemOf("Work")).getByRole("button", { name: /Read it again/ }));
    await waitFor(() => expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("other@work.example")).toBeTruthy());
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Confirming “Work” as your own" })).toBeNull());
    const confirms = () => calls.filter((c) => c.method === "POST" && c.path === `/api/ade/agents/accounts/${WORK}/confirm`).map((c) => c.body);
    expect(confirms()).toEqual([{ confirmation: "the-confirmation", report: seat("me@work.example"), org_id: "org-acme" }]);
    await waitFor(() => expect(within(itemOf("Work")).getByText(READ_SINCE)).toBeTruthy());
    expect(within(itemOf("Work")).getByText(READ_SINCE).getAttribute("role")).toBe("status");
    expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("other@work.example")).toBeTruthy();
    expect(within(itemOf("Work")).queryByRole("button", { name: /Cancel/ })).toBeNull();      // no wait: nothing was confirmed
    // confirmed again, it names the report as read now, and waits to join; the words go with the press
    pressYes("Work");
    expect(within(itemOf("Work")).queryByText(READ_SINCE)).toBeNull();
    const again = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    fireEvent.change(within(again).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(again).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: /Cancel/ })).toBeTruthy());
    expect(confirms()[1]).toEqual({ confirmation: "the-confirmation", report: seat("other@work.example"), org_id: "org-acme" });
  });

  it("reads the list again where a confirmation is refused for a report read since on another page, so the row shows it as read now", async () => {
    const { rows } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })]);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // read again on another page while the key is given: this list is not told
    Object.assign(rows.find((r) => r.account === "aaaaaaaaaaaa")!, { report: seat("other@work.example") });
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(itemOf("Work")).getByText(READ_SINCE)).toBeTruthy());
    await waitFor(() => expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("other@work.example")).toBeTruthy());
    expect(screen.queryByRole("group", { name: "Confirming “Work” as your own" })).toBeNull();
  });

  it("shows the orgId a row was read with, and confirms the one it showed: one read again since, the report the same, is refused and shown as read now, the app's words on that row alone", async () => {
    const { calls, rows } = ownersApp([owner("default"),
      owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), org_id: "org-acme", grp: null, grp_kind: null }),
      owner("bbbbbbbbbbbb", { position: 2, state: "reported", report: seat("spare@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: seat("spare@work.example") });
    workspace({ accounts: accountsView() });
    render(<ClaudeAccounts />);
    const box = (name: string) => within(itemOf(name)).getByLabelText("What its start reported");
    await waitFor(() => expect(within(box("Work")).getByText("org-acme")).toBeTruthy());
    expect(within(box("Work")).getByText("Organisation id")).toBeTruthy();
    expect(within(box("Spare")).queryByText("Organisation id")).toBeNull();      // none kept: no line for it
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // read again on another page, its orgId alone changed; then read again here, while the key is given, so that the
    // list holds the new orgId when the confirmation is sent
    Object.assign(rows.find((r) => r.account === "aaaaaaaaaaaa")!, { org_id: "org-other" });
    fireEvent.click(within(itemOf("Work")).getByRole("button", { name: /Read it again/ }));
    await waitFor(() => expect(within(box("Work")).getByText("org-other")).toBeTruthy());
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(itemOf("Work")).getByText(READ_SINCE)).toBeTruthy());
    const confirms = calls.filter((c) => c.method === "POST" && c.path === `/api/ade/agents/accounts/${WORK}/confirm`).map((c) => c.body);
    expect(confirms).toEqual([{ confirmation: "the-confirmation", report: seat("me@work.example"), org_id: "org-acme" }]);
    expect(screen.queryByRole("group", { name: "Confirming “Work” as your own" })).toBeNull();
    expect(within(box("Work")).getByText("org-other")).toBeTruthy();
    expect(within(itemOf("Spare")).queryByText(READ_SINCE)).toBeNull();
  });

  it("shows the orgId of an account on a personal plan too, which a confirmation names as it names a seat's", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: personal("me@work.example"), org_id: "4d3c2b1a-personal", grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: personal("me@work.example") });
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    const box = await waitFor(() => within(itemOf("Work")).getByLabelText("What its start reported"));
    expect(within(box).getByText("Claude Max")).toBeTruthy();
    expect(within(box).getByText("Organisation id")).toBeTruthy();
    expect(within(box).getByText("4d3c2b1a-personal")).toBeTruthy();
  });

  it("keeps the confirmation open with the app's words where it is refused for another reason than a read since, and says nothing on the row", async () => {
    const { calls, rows } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })]);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Work");
    const asking = await screen.findByRole("group", { name: "Confirming “Work” as your own" });
    // found not to match on another page, its report the same: the app refuses it (412), not as one read since
    Object.assign(rows.find((r) => r.account === "aaaaaaaaaaaa")!, { mismatch: "email" });
    const reads = () => calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts").length;
    const before = reads();
    fireEvent.change(within(asking).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(asking).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(within(asking).getByRole("alert").textContent).toContain("Read its identity first (report)."));
    await settle();
    expect(screen.getByRole("group", { name: "Confirming “Work” as your own" })).toBe(asking);
    expect(within(itemOf("Work")).queryByText(READ_SINCE)).toBeNull();
    expect(within(itemOf("Work")).queryAllByRole("status")).toEqual([]);
    expect(reads()).toBe(before);
  });

  it("does not read by itself an account the app holds as confirmed, though the page's copy of the rows is older", async () => {
    const { calls, rows } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "reported", report: seat("me@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: null });
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Work"))).findByRole("button", { name: "Yes, it is mine" });
    // confirmed on another page, its wait over there: this page is not told
    Object.assign(rows.find((r) => r.account === "aaaaaaaaaaaa")!, { state: "confirmed", hold: null, grp: "org:o1", grp_kind: "team" });
    act(() => useStore.setState({ accounts: accountsView({ work: false, spare: false }) }));
    await settle();
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    // decided at the read by the app's rows read again: no read, and the list reads the rows again
    await waitFor(() => expect(within(itemOf("Work")).getByLabelText("In tasks' relays")).toBeTruthy());
    await settle();
    expect(reports(calls)).toEqual([]);
  });

  it("reads by itself no account a press is reading, though the app's rows land while it reads", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    // the app's first read of its rows refused (away for a moment); Work signed in, with no row of the app's
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") }, held, 1);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    expect(await screen.findByText(/away for a moment/)).toBeTruthy();
    fireEvent.click(within(itemOf("Work")).getByRole("button", { name: /Read who it is/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));           // the press's read, held
    // the store's next read of the accounts: the app's rows land, with no row of Work's yet
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts")).toHaveLength(2));
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    letGo();
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(reports(calls)).toHaveLength(1);
  });

  it("makes no read queued while Connections was open once the workspace has stopped, and makes it when it runs again", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") }, held);
    workspace();
    const first = render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));          // Work's read held; Spare waits its turn
    // Connections closed, and the workspace stopped, while Spare waits: its read is not made
    first.unmount();
    act(() => useStore.setState({ ws: { enabled: true, running: false, provider: "fly" } }));
    letGo();
    await settle();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    // running again, and Connections open: Spare is read then
    act(() => useStore.setState({ ws: { enabled: true, running: true, provider: "fly" } }));
    render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("lists in Connections the app's rows, with a wait's Cancel, while Claude Code's status is not read (the workspace stopped)", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { name: "Work", position: 1, state: "held", in_relays: false, report: seat("me@work.example"),
                                                         hold: { id: "h1", kind: "join", takes_effect_at: Date.now() / 1000 + 600 } })]);
    workspace({ ws: { enabled: true, running: false, provider: "fly" }, claude: null, accounts: null });
    render(<Connections onClose={() => {}} />);
    const work = await waitFor(() => itemOf("Work"));
    expect(within(work).getByRole("button", { name: /Cancel/ })).toBeTruthy();
    expect(within(work).getByText("The workspace's accounts are not read yet")).toBeTruthy();
  });

  it("shows no list in Connections while neither the store nor the app has an account to show", async () => {
    const { calls } = ownersApp([]);
    workspace({ ws: { enabled: true, running: false, provider: "fly" }, claude: null, accounts: null });
    render(<Connections onClose={() => {}} />);
    await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.path === "/api/ade/agents/accounts")).toBe(true));
    await settle();
    await settle();
    expect(screen.queryByRole("list", { name: "Your accounts for tasks, in the relay's order" })).toBeNull();
  });

  it("reads by itself again an account whose identity changed since it was confirmed, once it signs in again", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "changed", in_relays: false, report: seat("old@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example") });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByRole("button", { name: "Confirm again" })).toBeTruthy());
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(within(within(itemOf("Work")).getByLabelText("What its start reported")).getByText("me@work.example")).toBeTruthy());
    expect(within(itemOf("Work")).getByText(/Its identity changed since you confirmed it/)).toBeTruthy();
  });

  it("names an account by the email its start reported where its sign-in now reads as another address", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" })]);
    const view = accountsView({ spare: false });
    view.claude.accounts[1].status = signedIn("someone.else@work.example", "team");
    workspace({ accounts: view });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText(/· me@work\.example/)).toBeTruthy());
    expect(within(itemOf("Work")).getByText("Signed in as someone.else@work.example · team")).toBeTruthy();
  });

  it("tells a signed-out account waiting to join to read who it is after its sign-in, since none is read by itself", async () => {
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "held", in_relays: false, mismatch: "email",
                                                         hold: { id: "h1", kind: "join", takes_effect_at: Date.now() / 1000 + 600 } })]);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText("It is not signed in: sign it in, then read who it is.")).toBeTruthy());
  });
});

describe("the workspace's own sign-in", () => {
  it("is never removed, though no session runs on it, where another account with none is", async () => {
    ownersApp([owner("default")]);
    const view = accountsView({ work: false, spare: false });
    view.claude.accounts[0].sessions = 0;
    workspace({ accounts: view });
    render(<ClaudeAccounts />);
    const own = await waitFor(() => itemOf("Default"));
    expect(within(own).getByLabelText("Sign Default out")).toBeTruthy();
    expect(within(own).queryByLabelText(/Remove Default/)).toBeNull();
    expect(within(itemOf("Work")).getByLabelText("Remove Work")).toBeTruthy();
  });

  it("of Codex is offered Sign out too where the workspace is hosted, and not on the person's own machine", async () => {
    ownersApp([owner("default")]);
    const view = accountsView();
    view.codex = { active: "default", accounts: [{ id: "default", name: "Default", created_at: null, sessions: 0,
      status: { installed: true, signed_in: true, method: "chatgpt" }, usage: null }] };
    workspace({ accounts: view, programs: { claude: true, codex: true } });
    render(<Connections onClose={() => {}} />);
    const hosted = await screen.findByRole("radiogroup", { name: "The Codex account new sessions start on" });
    expect(within(hosted).getByLabelText("Sign Default out")).toBeTruthy();
    cleanup();
    workspace({ ws: { enabled: true, running: true, provider: "local" }, accounts: view, programs: { claude: true, codex: true } });
    render(<Connections onClose={() => {}} />);
    const own = await screen.findByRole("radiogroup", { name: "The Codex account new sessions start on" });
    expect(within(own).queryByLabelText(/Sign Default out/)).toBeNull();
  });

  it("may be signed out from the page only where the workspace is hosted, in two presses; another account's in one", async () => {
    expect([ownSignOutHere("fly"), ownSignOutHere("local"), ownSignOutHere(undefined), ownSignOutHere(null), ownSignOutHere("")]).toEqual([true, false, false, false, false]);
    const out = vi.spyOn(Api, "accountSignOut").mockResolvedValue({});
    ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, report: seat("me@work.example"), grp: "org:o1", grp_kind: "team" })]);
    workspace({ ws: { enabled: true, running: true, provider: "local" } });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByLabelText("Sign Work out")).toBeTruthy());
    expect(within(itemOf("Default")).queryByLabelText(/Sign Default out/)).toBeNull();
    cleanup();
    workspace();
    render(<ClaudeAccounts />);
    const own = await waitFor(() => itemOf("Default"));
    fireEvent.click(within(own).getByLabelText("Sign Default out"));
    expect(out).not.toHaveBeenCalled();                                                  // the first press arms
    fireEvent.click(within(own).getByLabelText("Click again to sign Default out"));
    await waitFor(() => expect(out).toHaveBeenCalledWith("default", "claude"));
    // one act at a time: Work's Sign out takes its press once Default's act, and the reads after it, have ended
    await waitFor(() => expect((within(itemOf("Work")).getByLabelText("Sign Work out") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(itemOf("Work")).getByLabelText("Sign Work out"));
    await waitFor(() => expect(out).toHaveBeenCalledWith("aaaaaaaaaaaa", "claude"));
    expect(out).toHaveBeenCalledTimes(2);
  });

  it("disarms its Sign out four seconds after a first press with no second, which then arms again", async () => {
    const out = vi.spyOn(Api, "accountSignOut").mockResolvedValue({});
    const timers = vi.spyOn(globalThis, "setTimeout");
    ownersApp([owner("default")]);
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    const own = await waitFor(() => itemOf("Default"));
    fireEvent.click(within(own).getByLabelText("Sign Default out"));
    expect(within(own).getByLabelText("Click again to sign Default out")).toBeTruthy();
    // the four seconds pass (the timer the press set, run here) with no second press: not armed any more
    const disarm = timers.mock.calls.filter((c) => c[1] === 4000);
    expect(disarm).toHaveLength(1);
    act(() => { (disarm[0][0] as () => void)(); });
    expect(within(own).getByLabelText("Sign Default out")).toBeTruthy();
    expect(within(own).queryByLabelText("Click again to sign Default out")).toBeNull();
    // a press now arms again, and signs nothing out
    fireEvent.click(within(own).getByLabelText("Sign Default out"));
    expect(within(own).getByLabelText("Click again to sign Default out")).toBeTruthy();
    expect(out).not.toHaveBeenCalled();
  });
});

describe("every task's account record, in Connections", () => {
  it("is read when opened, not before, and listed in the order the app gives (its newest task first), each with its own record", async () => {
    const records: AccountRecordView[] = [
      { task_id: "1".repeat(32), agent_id: "a1a2a3a4a5a6", repo_address: "github.com/x/y", branch: `pp/task-${"1".repeat(32)}`, title: "Ship the fix",
        deleted_at: 1760000000, record_days: null, entries: [{ seq: 1, kind: "attempt", run_id: "r1r1r1r1r1r1", step: 1, fence: 1, via: null, repo_address: "github.com/x/y",
        branch: `pp/task-${"1".repeat(32)}`, title: "Ship the fix", at: 1759990000, accounts: [{ ref: { kind: "account", id: "default" }, email: "me@example.org", plan: "Claude Max", organization: null, org_id: null }], reduced_at: null, statement: null }] },
      { task_id: "2".repeat(32), agent_id: "b1b2b3b4b5b6", repo_address: null, branch: `pp/task-${"2".repeat(32)}`, title: "Write the notes", deleted_at: null, record_days: 90, entries: [] },
    ];
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = String(url).split("?")[0];
      const method = init.method || "GET";
      calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (path === "/api/ade/agents/account-records") return json({ records });
      if (path === "/api/ade/risks") return json({ texts: texts() });
      if (path === "/api/account/confirm") return method === "GET" ? json({ needed: true, password: true, passkeys: 0 }) : json({ confirmation: "the-confirmation" });
      if (method === "DELETE" && path.endsWith("/account-record")) {
        records.splice(records.findIndex((r) => r.task_id === path.split("/")[5]), 1);
        return json({ deleted: 1 });
      }
      return new Promise<Response>(() => {});
    }));
    workspace();
    render(<AccountRecords />);
    const open = screen.getByRole("button", { name: /Which accounts did the work, every task/ });
    expect(open.getAttribute("aria-expanded")).toBe("false");
    expect(calls.filter((c) => c.path === "/api/ade/agents/account-records")).toHaveLength(0);       // read when opened, not before
    fireEvent.click(open);
    const all = await screen.findByRole("group", { name: "Every task's account record" });
    await waitFor(() => expect(within(all).getAllByRole("group", { name: /^The account record of / })).toHaveLength(2));
    const groups = within(all).getAllByRole("group", { name: /^The account record of / }).map((g) => g.getAttribute("aria-label"));
    expect(groups[0]).toContain("Ship the fix, task 111111111111 · github.com/x/y");
    expect(groups[1]).toContain("Write the notes, task 222222222222");
    // the deleted task's record carries its Delete; the live one says when it will
    fireEvent.click(within(all).getAllByRole("button", { name: /Which accounts did this work/ })[0]);
    expect(await within(all).findByRole("button", { name: /Delete this record/ })).toBeTruthy();
    expect(within(all).getByText(/me@example\.org, Claude Max/)).toBeTruthy();
    fireEvent.click(within(all).getAllByRole("button", { name: /Which accounts did this work/ })[1]);
    expect(await within(all).findByText("Its Delete is offered once the task is deleted.")).toBeTruthy();
    // the deleted task's record deleted here, through its confirmation: the list read again, without it
    fireEvent.click(within(all).getByRole("button", { name: /Delete this record/ }));
    const confirm = await screen.findByRole("group", { name: "Deleting this task's account record" });
    fireEvent.change(within(confirm).getByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([`/api/ade/agents/tasks/${"1".repeat(32)}/account-record`]));
    await waitFor(() => expect(within(all).getAllByRole("group", { name: /^The account record of / })).toHaveLength(1));
    expect(calls.filter((c) => c.path === "/api/ade/agents/account-records")).toHaveLength(2);
    expect(within(all).getByRole("group", { name: /^The account record of / }).getAttribute("aria-label")).toContain("Write the notes");
  });
});

// Risk point 16: the page's read of an account by itself starts that account's claude
// without a press, so no such read runs in a browser until the point's text has opened there and the owner has closed
// it; a browser that keeps no close opens it again first, and the owner's own press of Read who it is reads at once.
describe("Risk point 16: no read of an account by itself before its text has opened here and the owner has closed it", () => {
  // a browser that keeps nothing: a new device, or its storage cleared
  beforeEach(() => { localStorage.clear(); forgetPage(); });

  const SIXTEEN = "Terms: Point 16";
  const sixteen = () => screen.queryByRole("dialog", { name: SIXTEEN });
  /** The text as it opens over the accounts, its words read: never modal, holding no button but its Close. */
  const opened = async () => {
    const dialog = await screen.findByRole("dialog", { name: SIXTEEN });
    await within(dialog).findByText("Point 16's own words.");
    expect(dialog.getAttribute("aria-modal")).toBe("false");
    expect(within(dialog).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(["Close the terms"]);
    return dialog;
  };
  const close = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "Close the terms" }));
  const twoToRead = () => ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("spare@example.org", "Claude Pro") });

  it("opens its text over the accounts as the page would first read one by itself, and sends no read until the owner has closed it", async () => {
    const { calls } = twoToRead();
    workspace();
    render(<ClaudeAccounts />);
    // the list's read of the task list is sent at its mount, before any answer of the app's has landed, its rows' among
    // them (the check at the end places it before the first read by itself)
    expect(calls.map((c) => `${c.method} ${c.path}`)).toContain("GET /api/ade/agents/tasks");
    const terms = await opened();
    expect(terms.compareDocumentPosition(list()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // above the list
    // open, and for as long as it is: no read, and no row says one is under way
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(within(itemOf("Work")).queryByText("Reading who it is from its start…")).toBeNull();
    // the store's rows read again meanwhile, as every few seconds: still no read, and the one text
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(screen.getAllByRole("dialog", { name: SIXTEEN })).toHaveLength(1);
    // closed: the reads by themselves go on, one after another, as before, and the text is gone
    close(terms);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    expect(sixteen()).toBeNull();
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    // nothing of the text reached the app: its words were read, and every other call is the list's own (its rows, the
    // reads by themselves, and the deployment's settings, which the note over the list reads)
    expect(calls.filter((c) => c.path === "/api/ade/risks").every((c) => c.method === "GET" && c.body === undefined)).toBe(true);
    expect(new Set(calls.map((c) => `${c.method} ${c.path}`))).toEqual(
      new Set(["GET /api/ade/risks", "GET /api/ade/agents/accounts", "GET /api/ade/agents/tasks", "POST /api/ade/agents/accounts/report"]));
    // and of the task list, one read, the list's own (sent at its mount, as checked above), with no query, and before the
    // first read by itself: the text's close went to the app on that route no more than on any other, and no read by
    // itself reads it again
    const listings = calls.flatMap((c, i) => (c.path === "/api/ade/agents/tasks" ? [{ ...c, at: i }] : []));
    expect(listings.map((c) => `${c.method} ${c.query}`)).toEqual(["GET "]);
    expect(listings[0].at).toBeLessThan(calls.findIndex((c) => c.path === "/api/ade/agents/accounts/report"));
  });

  it("takes an Escape that closes Connections for no close of the text: the read waits, and the text opens again with Connections", async () => {
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") });
    workspace({ accounts: accountsView({ spare: false }) });
    const closed = vi.fn();
    const first = render(<Connections onClose={closed} />);
    const terms = await opened();
    expect(screen.getByRole("dialog", { name: "Connections" }).contains(terms)).toBe(true);
    // Escape closes the dialog on top, Connections, and the text goes with it: the owner closed Connections, as with
    // its own Close button, not the text
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(closed).toHaveBeenCalledTimes(1);
    expect(sixteen()).toBeNull();
    first.unmount();                                                               // as the app takes it away
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    // Connections opened again: the text first, and the read once the owner has closed it
    render(<Connections onClose={() => {}} />);
    const again = await opened();
    await settle();
    expect(reports(calls)).toEqual([]);
    close(again);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
  });

  it("is closed by Escape where it is on top as by its Close; a Close before its words could be read keeps nothing, the read waits, and the text opens again", async () => {
    const { calls } = ownersApp([owner("default")], { aaaaaaaaaaaa: seat("me@work.example") });
    // the texts' route away for a moment; every other call answered by the app's fake
    const app = globalThis.fetch;
    let down = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => (down && String(url).split("?")[0] === "/api/ade/risks"
      ? new Response(JSON.stringify({ detail: "The app is starting." }), { status: 503 }) : app(url, init))));
    workspace({ accounts: accountsView({ spare: false }) });
    const first = render(<ClaudeAccounts />);
    const unread = await screen.findByRole("dialog", { name: "Terms: Risk point 16" });
    await within(unread).findByText("The terms at this point could not be read just now.");
    close(unread);
    await settle();
    await settle();
    expect(sixteen()).toBeNull();
    expect(reports(calls)).toEqual([]);                                          // its words never shown: no read
    // Connections opened again, the route answering: the text opens again, and Escape closes it as its Close does
    first.unmount();
    down = false;
    render(<ClaudeAccounts />);
    await opened();
    await settle();
    expect(reports(calls)).toEqual([]);
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    expect(sixteen()).toBeNull();
  });

  it("keeps the close in this browser, so the next page reads by itself at once; a browser whose storage was cleared opens the text again first", async () => {
    const { calls } = twoToRead();
    workspace({ accounts: accountsView({ spare: false }) });
    let page = render(<ClaudeAccounts />);
    close(await opened());
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    // the next page in this browser, Spare signed in by now: read by itself at once, and no text
    page.unmount();
    forgetPage();
    forgetAccountReads();
    workspace();
    page = render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await settle();
    expect(sixteen()).toBeNull();
    // this browser's storage cleared, then a page on which Work signs out and in again, which reads it by itself: the
    // text opens first, and no read is sent until it is closed again
    page.unmount();
    localStorage.clear();
    forgetPage();
    forgetAccountReads();
    workspace({ accounts: accountsView({ work: false }) });
    render(<ClaudeAccounts />);
    await waitFor(() => expect(within(itemOf("Work")).getByText("Not signed in")).toBeTruthy());
    act(() => useStore.setState({ accounts: accountsView() }));
    const again = await opened();
    await settle();
    await settle();
    expect(reports(calls)).toHaveLength(2);
    close(again);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }, { account: "aaaaaaaaaaaa" }]));
  });

  it("does not open for an account that signs in again while its confirmation is open here, whose read is not queued then, and opens once that is closed", async () => {
    const { calls } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    pressYes("Spare");
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    // Spare signs in again with its confirmation open: no read of it is queued, so nothing waits on the text
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    await settle();
    await settle();
    expect(sixteen()).toBeNull();
    expect(reports(calls)).toEqual([]);
    // closed: its read is queued, and the text opens before it
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    close(await opened());
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
  });

  it("does not make a read that waited on it once the owner has opened that account's confirmation meanwhile, and makes it once that is closed", async () => {
    const { calls, gateWays } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    await settle();
    // the owner presses Spare's Yes, it is mine, its ways slow to come; then Spare signs in again: its read is queued
    // while the press opens, and waits on the text, which opens (a press made once that read is queued would be offered
    // too: the text holds no Yes; one for a read waiting its turn behind another's read under way, the
    // text closed, would wait)
    const letWaysGo = gateWays();
    pressYes("Spare");
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    const terms = await opened();
    // Spare's confirmation opens, then the owner closes the text: Spare is not read under the confirmation
    letWaysGo();
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    close(terms);
    await settle();
    await settle();
    expect(sixteen()).toBeNull();
    expect(reports(calls)).toEqual([]);
    // the confirmation closed: read, the text closed here already
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
    expect(sixteen()).toBeNull();
  });

  it("offers Yes, it is mine for an account whose read by itself waits on the text while the text is open, and sets that read aside under the confirmation", async () => {
    // the text holds nothing but itself: it is the page's own read, never a person's control, and the control works
    // without the owner closing it. Yes is not held for an account whose read waits its turn on the text; a read
    // waiting its turn behind another's read under way still holds it (tests above)
    const { calls } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    await settle();
    // Spare signs in again in a browser that has closed nothing: its read is queued, and the text opens before it
    act(() => useStore.setState({ accounts: accountsView({ work: false }) }));
    const terms = await opened();
    await settle();
    expect(reports(calls)).toEqual([]);
    // the text open over the list, the read waiting on it: Yes, it is mine is there, and a press opens its confirmation
    expect(yesOf("Spare").disabled).toBe(false);
    pressYes("Spare");
    const asking = await screen.findByRole("group", { name: "Confirming “Spare” as your own" });
    expect(screen.getByRole("dialog", { name: SIXTEEN })).toBe(terms);                  // the text is not closed by it
    expect(yesOf("Spare").disabled).toBe(true);                                         // its own confirmation is open: no second
    // the owner closes the text: the read is set aside under the confirmation, not made
    close(terms);
    await settle();
    await settle();
    expect(reports(calls)).toEqual([]);
    expect(sixteen()).toBeNull();
    // the confirmation closed: read, the text closed here already
    fireEvent.click(within(asking).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
  });

  it("offers Confirm again too, for a changed account whose read by itself waits on the text while the text is open", async () => {
    const { calls } = ownersApp([owner("default"), owner("aaaaaaaaaaaa", { position: 1, state: "changed", in_relays: false, report: seat("old@work.example"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example") });
    workspace({ accounts: accountsView({ work: false, spare: false }) });
    render(<ClaudeAccounts />);
    const again = () => within(itemOf("Work")).getByRole("button", { name: "Confirm again" }) as HTMLButtonElement;
    await waitFor(() => expect(again()).toBeTruthy());
    await settle();
    // Work signs in again in a browser that has closed nothing: its read is queued, and the text opens before it
    act(() => useStore.setState({ accounts: accountsView({ spare: false }) }));
    const terms = await opened();
    await settle();
    expect(reports(calls)).toEqual([]);
    // the text open over the list, the read waiting on it: Confirm again is offered, as Yes, it is mine is
    expect(again().disabled).toBe(false);
    // closed: the read is made
    close(terms);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
  });

  it("keeps Yes, it is mine waiting for an account queued behind a read already under way once the text is closed, and not before (the hold lifted is the text's alone)", async () => {
    let letGo = () => {};
    const held = new Promise<void>((done) => { letGo = done; });
    const { calls } = ownersApp([owner("default"), owner("bbbbbbbbbbbb", { position: 1, state: "reported", report: personal("spare@example.org", "Claude Pro"), grp: null, grp_kind: null })],
      { aaaaaaaaaaaa: seat("me@work.example"), bbbbbbbbbbbb: personal("other@example.org", "Claude Pro") }, held);
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    // the text opens for Work's read, which waits on it; Spare is signed out, its Yes, it is mine there
    const terms = await opened();
    await within(await waitFor(() => itemOf("Spare"))).findByRole("button", { name: "Yes, it is mine" });
    // Spare signs in again, its read queued behind Work's, which waits on the text: its Yes is there while the text is open
    act(() => useStore.setState({ accounts: accountsView() }));
    await settle();
    expect(yesOf("Spare").disabled).toBe(false);
    // the text closed: Work's read is under way, held, and Spare's read waits its turn behind it, so its Yes waits
    close(terms);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(true));
    letGo();
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(yesOf("Spare").disabled).toBe(false));
  });

  it("reads at once at the owner's press of Read who it is while the text is open, and does not read that account by itself after", async () => {
    const { calls } = twoToRead();
    workspace();
    render(<ClaudeAccounts />);
    const terms = await opened();
    await settle();
    expect(reports(calls)).toEqual([]);
    // the press is the owner's own act: read at once, the text still open over the list
    fireEvent.click(within(itemOf("Spare")).getByRole("button", { name: /Read who it is/ }));
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]));
    await waitFor(() => expect(within(itemOf("Spare")).getByRole("button", { name: "Yes, it is mine" })).toBeTruthy());
    expect(screen.getByRole("dialog", { name: SIXTEEN })).toBe(terms);
    await settle();
    expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }]);              // Work's read by itself still waits
    close(terms);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "bbbbbbbbbbbb" }, { account: "aaaaaaaaaaaa" }]));
    await settle();
    await settle();
    expect(reports(calls)).toHaveLength(2);                                       // Spare once: the press's
  });

  it("opens the text again for another account signed in on this browser, whose close it does not keep", async () => {
    const { calls } = twoToRead();
    workspace({ accounts: accountsView({ spare: false }) });
    render(<ClaudeAccounts />);
    close(await opened());
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    // another account signs in on the page, Spare signed in on its workspace: no read for it before the text
    act(() => useStore.setState({ user: { id: "u2", email: "other@example.org", can_sign_in_again: true }, accounts: accountsView() }));
    const theirs = await opened();
    await settle();
    expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]);
    close(theirs);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
  });

  it("keeps its own record, apart from the events' texts: a close is no opened event, and an event opened is no close", async () => {
    const now = Date.now() / 1000;
    const DAY = 24 * 3600;
    const { calls } = twoToRead();
    workspace({ accounts: accountsView({ spare: false }) });
    const page = render(<ClaudeAccounts />);
    close(await opened());
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }]));
    page.unmount();
    forgetPage();
    // a browser that has closed point 16's text and opened no event's: at its first visit to a task's moves, one older
    // than 7 days is taken as opened, as in a browser that keeps nothing
    expect(eventsToOpen("u1", [{ event: "task:t1:2", at: now - 10 * DAY }], now)).toEqual([]);
    // a move's text opened in this browser, then a read by itself on the next page: the event took nothing of the
    // close, so the read goes at once, with no text
    forgetPage();
    render(<RiskTerms point={2} kind="event" event="task:t1:3" />);
    await screen.findByRole("dialog", { name: "Terms: Point 2" });
    cleanup();
    forgetPage();
    forgetAccountReads();
    workspace();
    render(<ClaudeAccounts />);
    await waitFor(() => expect(reports(calls)).toEqual([{ account: "aaaaaaaaaaaa" }, { account: "bbbbbbbbbbbb" }]));
    expect(sixteen()).toBeNull();
    // and the close took nothing of the events: the move opened is not opened again at the next visit
    cleanup();
    forgetPage();
    render(<RiskTerms point={2} kind="event" event="task:t1:3" />);
    await screen.findByRole("button", { name: "The terms: Point 2" });
    await settle();
    expect(screen.queryByRole("dialog", { name: "Terms: Point 2" })).toBeNull();
  });
});
