import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { channelAlerts, linkWaiting, type LinkEvent, type LinkSessions, type LinkView } from "../api";
import { eventSlot } from "../e2e";
import { textDigest } from "../passkey";
import { useStore } from "../store";
import { clock, dayTime } from "../when";
import { Approvals } from "./Approvals";
import { Conversation } from "./Channel";

// Close in the owner's inbox: a session's prompt the owner closes is hidden, not given up.
// It leaves the open list and stays answerable until its deadline, and its session's hook refuses a prompt nobody
// answers by then, so the session goes on. Below the open prompts, the inbox lists what GET
// /api/ade/link/prompts/closed answers: the hidden prompts that still wait, first and with their answer controls, and
// those closed in the last 24 hours, folded under one line, each saying how and when it ended. While nothing else waits
// and a prompt he hid still does, Approvals says so in place of "Nothing is waiting for you.". As in OwnerInbox.test.tsx,
// a stand-in holds the keys' state, and an "envelope" is the value and the slot it was sealed to, so it opens only with
// that slot, as a real one does.
const fake = vi.hoisted(() => {
  type State = "off" | "locked" | "unlocked" | "unsupported";
  const self = {
    state: "unlocked" as State,
    on: true,
    generation: 1,
    listeners: new Set<() => void>(),
    opened: [] as string[],
    notify() { for (const f of self.listeners) f(); },
    set(next: State) {
      if (next === "locked" || next === "unlocked") self.generation += 1;
      self.state = next;
      self.notify();
    },
    unlock: vi.fn(async () => { self.set("unlocked"); }),
    credentials: { get: vi.fn(async () => ({ id: "AQ", type: "public-key", response: {
      clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
      signature: new Uint8Array([3]).buffer, userHandle: null } })) },
  };
  (globalThis as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  Object.defineProperty(navigator, "credentials", { value: self.credentials, configurable: true });
  return self;
});

vi.mock("../e2e", async (importOriginal) => {
  const real = await importOriginal<typeof import("../e2e")>();
  const { useSyncExternalStore } = await import("react");
  const subscribe = (f: () => void) => { fake.listeners.add(f); return () => { fake.listeners.delete(f); }; };
  return {
    ...real,
    useE2E: () => {
      const state = useSyncExternalStore(subscribe, () => fake.state);
      const on = useSyncExternalStore(subscribe, () => fake.on);
      const generation = useSyncExternalStore(subscribe, () => fake.generation);
      return { state, on, generation, unlock: fake.unlock, unlockWithCode: async () => {}, lock: () => fake.set("locked") };
    },
    openSealed: async (envelope: string, slot: string) => {
      fake.opened.push(envelope);
      try {
        const inner = JSON.parse(Buffer.from(envelope.slice(5), "base64url").toString("utf8"));
        return inner.slot === slot ? { ok: true, value: inner.value } : { ok: false, why: "Its tag does not check: it was sealed for another place." };
      } catch {
        return { ok: false, why: "It is not an envelope." };
      }
    },
  };
});

const ACCOUNT = "acct-1";
const OTHER = "acct-2";
const T = 1_790_000_000;
const CID = "0123456789abcdef0123456789abcdef";
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;

const BASH = { tool: "Bash", input: { command: "rm -rf build", description: "Clean the build" }, session_id: "cc-1", agent_id: null, deadline: T + 1800 };
const ASKED = {
  tool: "AskUserQuestion",
  input: { questions: [{ question: "Which store?", header: "Store", multiSelect: false, options: [{ label: "SQLite" }, { label: "Postgres" }] }] },
  questions: [{ question: "Which store?", header: "Store", multiSelect: false, options: [{ n: 1, label: "SQLite" }, { n: 2, label: "Postgres" }] }],
  session_id: "cc-1", agent_id: null, deadline: T + 1800,
};

type Prompt = LinkEvent & { link_id: string; deadline_at: number; look: boolean };
type Ended = "waiting" | "approved" | "refused" | "words" | "deadline" | "report" | "closed";
/** A prompt as GET /api/ade/link/prompts/closed lists it: an open prompt's view, and how and when it ended. */
type Closed = Prompt & { hidden_at: number | null; closed_at: number | null; closed_reason: string | null; open: boolean; ended: Ended };

/** A prompt link `linkId` (the session `from`) posted to `account`, its item sealed to that account's slot for it. */
const promptBy = (account: string, linkId: string, from: string, item: unknown, over: Partial<Prompt> = {}): Prompt => ({
  seq: 7, time: "2026-11-02T10:00:00Z", kind: "prompt", from, to: "owner", ref: null,
  text: seal({ text: JSON.stringify(item), session: null, said_at: null }, eventSlot(account, linkId, "prompt", undefined, null, CID)),
  client_id: CID, from_id: linkId, link_id: linkId, deadline_at: T + 1800, look: false, ...over,
});
const prompt = (item: unknown, over: Partial<Prompt> = {}) => promptBy(ACCOUNT, "L1", "mac", item, over);
const closed = (p: Prompt, ended: Ended, over: Partial<Closed> = {}): Closed => ({
  ...p, hidden_at: null, closed_at: null, closed_reason: null, open: ended === "waiting", ended, ...over,
});
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const sessions = (over: Record<string, unknown> = {}) => ({
  links: [link(), link({ id: "L2", name: "planner" })], questions: [], public_url: "https://pp.example", prompts: [], proposals: [], ...over,
}) as unknown as LinkSessions;

const SESSIONS = "/api/ade/link/sessions";
const CLOSED = "/api/ade/link/prompts/closed";
type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
let calls: Call[] = [];
/** A server of the test's own: each route answers [status, data], at once or when its promise settles; else 404. */
function serve(more: Record<string, Route> = {}) {
  const routes: Record<string, Route> = {
    "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: ACCOUNT, server_time: T }],
    // no signer paired, so Approve is one press; with one, or unread, it opens the review
    "GET /api/ade/signers": () => [200, { signers: [] }],
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
    ...more,
  };
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: "no route" }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const gets = (path: string) => calls.filter((c) => c.method === "GET" && c.path === path).length;
const signed = () => calls.filter((c) => c.method === "POST" && c.path === "/api/ade/link/commands").map((c) => c.body?.payload as Record<string, unknown>);
const noEnvelope = () => expect(new XMLSerializer().serializeToString(document.body)).not.toContain("e2e1.");
const owner = (id = ACCOUNT) => ({ id, email: `${id}@example.org`, can_sign_in_again: true });
/** The inbox as the store last read it, the closed route answering `hidden` (none: no such route, an older server). */
const showing = (links: LinkSessions, hidden: Closed[] | null, more: Record<string, Route> = {}) => {
  useStore.setState({ user: owner(), links });
  serve({
    [`GET ${SESSIONS}`]: () => [200, links],
    ...(hidden ? { [`GET ${CLOSED}`]: (): Answer => [200, { prompts: hidden, server_time: T + 200 }] } : {}),
    ...more,
  });
};
const approvals = () => render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} />);

const hiddenList = () => screen.queryByRole("region", { name: "Hidden and closed prompts" });
const cards = () => screen.queryAllByRole("group", { name: /^Reported by the session / });
const hiddenCards = () => cards().filter((c) => !!hiddenList()?.contains(c));
const openCards = () => cards().filter((c) => !hiddenList()?.contains(c));
const names = (cs: HTMLElement[]) => cs.map((c) => c.getAttribute("aria-label"));
const endedLines = () => screen.queryAllByLabelText("How it ended").map((e) => e.textContent);
const buttonsOf = (c: HTMLElement) => within(c).queryAllByRole("button").map((b) => (b.textContent ?? "").trim());
/** The fold of the prompts that ended, opened where there is one: the hidden prompts that still wait are shown, those
 *  that ended folded under one line (a page from before the fold shows them all, and has none to open). */
const unfold = () => {
  const fold = screen.queryByRole("button", { name: /^Closed in the last 24 hours/ });
  if (fold && fold.getAttribute("aria-expanded") !== "true") fireEvent.click(fold);
};
/** The shield beside Approvals' title, by its colour: done while nothing waits, wait while something does. */
const shield = () => within(screen.getByRole("complementary", { name: "Approvals" })).getByText("Approvals").querySelector("svg")?.getAttribute("class") ?? "";
const EMPTY_HIDDEN = "Nothing is waiting in your open list: a prompt you hid waits for your answer below, until its deadline.";
/** The note a button is described by (aria-describedby), as the page shows it. */
const noteOf = (button: HTMLElement) => document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;

// what happens at the deadline is the session's hook's act, not the list's
const WAITING_WORDS = (hidden: number, deadline: number) =>
  `Hidden at ${dayTime(hidden)}, and waiting for your answer until its deadline, ${clock(deadline)}: unanswered by then, its session's hook refuses it and the session goes on, where that hook is this version's and can check that with the ADE then; otherwise it stays in the session's window.`;
const NOTE = "Close decides nothing: it takes this prompt out of your open list, and the session keeps waiting for your answer "
  + "until its deadline; you can still answer it among the hidden ones below. Unanswered by then, its session's hook refuses it "
  + "and the session goes on, where that hook is this version's and can check that with the ADE then; otherwise it stays in the session's "
  + "window.";
const NOTE_ELSEWHERE = "Close decides nothing: it takes this prompt out of your open list, and the session keeps waiting for your "
  + "answer until its deadline; you can still answer it among the hidden prompts in Approvals. Unanswered by then, its session's "
  + "hook refuses it and the session goes on, where that hook is this version's and can check that with the ADE then; otherwise it stays in "
  + "the session's window.";
// the server closes an item to look at on Close, as it did before Close hid prompts: its note is the old one, whole
const NOTE_LOOK = "Closing decides nothing: it takes this out of your list, and the prompt stays in the session's window.";
// a text not in a prompt's form was not posted by this hook, which posts a call whole: no refusal is promised
const NOTE_NOT_A_PROMPT = "Close decides nothing: it takes this prompt out of your open list, to the hidden ones below; it is "
  + "answered where the session shows it.";
/** What the page says of a prompt whose deadline passed with no answer of the owner's: no more than the server knows. */
const UNANSWERED = (deadline: number) => `Unanswered by its deadline, ${dayTime(deadline)}.`;

beforeEach(() => {
  fake.state = "unlocked";
  fake.on = true;
  fake.generation += 1;
  fake.opened.length = 0;
  fake.credentials.get.mockClear();
  serve();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

describe("the hidden and closed prompts, in Approvals", () => {
  it("are listed below the open prompts, each naming its session and showing what it was, opened with the account's keys and never raw", async () => {
    const open = prompt(BASH);
    const hidden = closed(promptBy(ACCOUNT, "L2", "planner", { ...BASH, input: { command: "make deploy" } }, { seq: 21 }), "waiting", { hidden_at: T + 60 });
    showing(sessions({ prompts: [open] }), [hidden]);
    approvals();
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session planner"]));
    const card = hiddenCards()[0];
    await vi.waitFor(() => expect(within(card).queryByLabelText("Its input, whole")?.textContent).toContain('"command": "make deploy"'));
    expect(within(card).getByText("Bash")).toBeTruthy();
    expect(fake.opened).toContain(hidden.text);
    // below the open prompt, which stays in the open list
    expect(names(openCards())).toEqual(["Reported by the session mac"]);
    expect(openCards()[0].compareDocumentPosition(hiddenList()!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    noEnvelope();
  });

  const AT = T + 120;
  const done = (seq: number, ended: Ended, reason: string, item: unknown = BASH, over: Partial<Prompt> = {}) =>
    closed(prompt(item, { seq, ...over }), ended, { closed_at: AT, closed_reason: reason });
  const said: [string, () => Closed, () => string][] = [
    ["hidden, waiting for his answer until its deadline", () => closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 }),
      () => WAITING_WORDS(T + 60, T + 1800)],
    ["approved", () => done(22, "approved", "answer"), () => `Approved by your signed answer at ${dayTime(AT)}.`],
    ["a question answered with its options", () => done(23, "approved", "answer", ASKED),
      () => `Answered with your chosen options at ${dayTime(AT)}.`],
    ["refused with Deny", () => done(24, "refused", "answer"), () => `Refused by your signed Deny at ${dayTime(AT)}.`],
    ["answered in his own words", () => done(25, "words", "answer"), () => `Answered in your own words at ${dayTime(AT)}.`],
    ["unanswered by its deadline", () => closed(prompt(BASH, { seq: 26 }), "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" }),
      () => UNANSWERED(T + 1800)],
    ["closed by its session's report", () => done(27, "report", "report:PostToolUse"),
      () => `Closed by its session at ${dayTime(AT)}: the session reported that its tool ran.`],
    ["closed on the page, before a Close hid prompts", () => done(28, "closed", "owner"),
      () => `Closed by you on the page at ${dayTime(AT)}, which decided nothing.`],
    ["an item to look at, hidden", () => closed(prompt(BASH, { seq: 29, look: true }), "waiting", { hidden_at: T + 60 }),
      () => `Hidden at ${dayTime(T + 60)}: it is answered where the session shows it, and nothing here decides it.`],
    ["an item to look at, at its deadline", () => closed(prompt(BASH, { seq: 30, look: true }), "deadline", { closed_at: T + 1800, closed_reason: "deadline" }),
      () => `Closed at its deadline, ${dayTime(T + 1800)}.`],
    // a text not in a prompt's form was not posted by this version's hook: nothing is promised of its deadline
    ["a text not in a prompt's form, hidden", () => closed(prompt({}, { seq: 31, client_id: "", text: "Push to main?" }), "waiting", { hidden_at: T + 60 }),
      () => `Hidden at ${dayTime(T + 60)}: it is answered where the session shows it, and nothing here decides it.`],
  ];
  it.each(said)("say how and when each ended, in plain words: %s", async (_, item, words) => {
    showing(sessions(), [item()]);
    approvals();
    await vi.waitFor(() => { unfold(); expect(endedLines()).toEqual([words()]); });
    noEnvelope();
  });

  it("offer a hidden prompt that still waits its answer controls, as an open prompt has, and no Close; one that ended offers none", async () => {
    showing(sessions(), [
      closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 }),
      closed(prompt(BASH, { seq: 22 }), "approved", { closed_at: T + 120, closed_reason: "answer" }),
      closed(prompt(BASH, { seq: 26 }), "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" }),
    ]);
    approvals();
    await vi.waitFor(() => { unfold(); expect(hiddenCards().map(buttonsOf)).toEqual([["Approve", "Deny", "Answer in your own words"], [], []]); });
  });

  it("answer a hidden prompt that still waits from there, signed for that prompt, and read both lists again after", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const waiting = closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 });
    showing(sessions(), [waiting]);
    approvals();
    await vi.waitFor(() => expect(hiddenCards().map(buttonsOf)).toEqual([["Approve", "Deny", "Answer in your own words"]]));
    const before = [gets(SESSIONS), gets(CLOSED)];
    // one press: its answer prepared ahead, then Undo's window passed a second at a time
    const approve = within(hiddenCards()[0]).getByRole("button", { name: "Approve" });
    await vi.waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false));
    fireEvent.click(approve);
    await screen.findByRole("button", { name: "Undo" });
    for (let i = 0; i < 12 && !signed().length; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    vi.useRealTimers();
    expect(signed()[0]).toMatchObject({ purpose: "answer", to: "L1", ref: 21, text: "Approve", digest: await textDigest(waiting.text) });
    await vi.waitFor(() => expect([gets(SESSIONS) > before[0], gets(CLOSED) > before[1]]).toEqual([true, true]));
  });

  it("take a prompt closed from the open list, read both again after the Close, and show it hidden and waiting, answerable", async () => {
    const p = prompt(BASH);
    let hidden = false;
    useStore.setState({ user: owner(), links: sessions({ prompts: [p] }) });
    serve({
      [`GET ${SESSIONS}`]: () => [200, sessions({ prompts: hidden ? [] : [p] })],
      [`GET ${CLOSED}`]: () => [200, { prompts: hidden ? [closed(p, "waiting", { hidden_at: T + 60 })] : [], server_time: T + 60 }],
      "POST /api/ade/link/questions/7/close": () => { hidden = true; return [200, { seq: 7, hidden_at: T + 60 }]; },
    });
    approvals();
    await vi.waitFor(() => expect(names(openCards())).toEqual(["Reported by the session mac"]));
    await within(openCards()[0]).findByText("Bash");
    fireEvent.click(within(openCards()[0]).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
    expect(openCards()).toEqual([]);
    await vi.waitFor(() => expect(endedLines()).toEqual([WAITING_WORDS(T + 60, T + 1800)]));
    // its controls come once its text is opened, on the new card
    await vi.waitFor(() => expect(buttonsOf(hiddenCards()[0])).toEqual(["Approve", "Deny", "Answer in your own words"]));
    const closedAt = calls.findIndex((c) => c.method === "POST" && c.path === "/api/ade/link/questions/7/close");
    const after = calls.slice(closedAt + 1).filter((c) => c.method === "GET").map((c) => c.path);
    expect(after).toContain(SESSIONS);
    expect(after).toContain(CLOSED);
  });

  it("show a hidden prompt whose deadline passed as unanswered by its deadline, without its controls, once the list is read again", async () => {
    const p = prompt(BASH, { seq: 21 });
    let passed = false;
    showing(sessions(), null, {
      [`GET ${CLOSED}`]: () => [200, { prompts: [passed
        ? closed(p, "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" })
        : closed(p, "waiting", { hidden_at: T + 60 })], server_time: passed ? T + 1801 : T + 60 }],
    });
    approvals();
    await vi.waitFor(() => expect(hiddenCards().map(buttonsOf)).toEqual([["Approve", "Deny", "Answer in your own words"]]));
    passed = true;
    await act(async () => { await useStore.getState().refreshLinks(); });
    await vi.waitFor(() => { unfold(); expect(endedLines()).toEqual([UNANSWERED(T + 1800)]); });
    expect(hiddenCards().map(buttonsOf)).toEqual([[]]);
  });

  it("say of a prompt whose deadline passed only that it went unanswered by then, never that it was refused: the server knows no more", async () => {
    // an older ade_link.py, a clock out of step, an ADE its session could not reach or a window that answered first
    // all leave no refusal; the record never says what the hook did
    showing(sessions(), [closed(prompt(BASH, { seq: 26 }), "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" })]);
    approvals();
    await vi.waitFor(() => { unfold(); expect(endedLines()).toEqual([UNANSWERED(T + 1800)]); });
    expect(endedLines()[0]).not.toMatch(/refus/i);
    expect(hiddenList()!.textContent).not.toMatch(/refus/i);
  });

  it("are only what the route answers for the account signed in: another account's never stay, nor land late", async () => {
    const mine = closed(prompt(BASH, { seq: 31 }), "waiting", { hidden_at: T + 60 });
    const late = closed(prompt({ ...BASH, input: { command: "late" } }, { seq: 32 }), "waiting", { hidden_at: T + 70 });
    const theirs = closed(promptBy(OTHER, "L2", "planner", BASH, { seq: 41 }), "waiting", { hidden_at: T + 80 });
    // the closed route's answers, held where the test says, each released by the test
    const held: ((a: Answer) => void)[] = [];
    let holding = 0;
    useStore.setState({ user: owner(), links: sessions() });
    serve({
      [`GET ${SESSIONS}`]: () => [200, sessions()],
      [`GET ${CLOSED}`]: () => {
        if (holding > 0) { holding -= 1; return new Promise<Answer>((r) => { held.push(r); }); }
        return [200, { prompts: [mine], server_time: T + 90 }];
      },
    });
    const settle = async (answer: Answer) => {
      await act(async () => { held.shift()!(answer); await new Promise((r) => setTimeout(r, 50)); });
    };
    approvals();
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
    // the inbox read again for this account, then another account signed in, both lists' answers held
    holding = 2;
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(gets(CLOSED)).toBe(2));
    act(() => { useStore.setState({ user: owner(OTHER), links: sessions({ links: [link({ id: "L2", name: "planner" })] }) }); });
    await vi.waitFor(() => expect(gets(CLOSED)).toBe(3));
    // the first account's list is gone at once, before the other's answer
    expect(hiddenCards()).toEqual([]);
    // the first account's late answer is never shown
    await settle([200, { prompts: [late], server_time: T + 95 }]);
    expect(hiddenCards()).toEqual([]);
    await settle([200, { prompts: [theirs], server_time: T + 96 }]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session planner"]));
    expect(gets(CLOSED)).toBe(3);
  });

  it("are asked for when the inbox is read; an older server's 404 leaves the inbox as it was, says nothing, and is not asked again", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }), null);
    approvals();
    await screen.findByText("Bash");
    await vi.waitFor(() => expect(gets(CLOSED)).toBe(1));
    await act(async () => { await useStore.getState().refreshLinks(); });
    expect(gets(SESSIONS)).toBe(1);
    expect(gets(CLOSED)).toBe(1);
    expect(useStore.getState().error).toBeNull();
    expect(hiddenList()).toBeNull();
    expect(names(openCards())).toEqual(["Reported by the session mac"]);
  });

  it("put the hidden prompts that still wait first, shown with their controls, and fold those that ended under one line naming how many, opened with a click", async () => {
    // newest first, as the route gives them: one that ended, one that still waits, another that ended
    showing(sessions(), [
      closed(promptBy(ACCOUNT, "L2", "planner", BASH, { seq: 22 }), "approved", { closed_at: T + 120, closed_reason: "answer" }),
      closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 }),
      closed(promptBy(ACCOUNT, "L3", "ci", BASH, { seq: 26 }), "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" }),
    ]);
    approvals();
    await vi.waitFor(() => expect(hiddenCards().map(buttonsOf)).toEqual([["Approve", "Deny", "Answer in your own words"]]));
    expect(names(hiddenCards())).toEqual(["Reported by the session mac"]);
    const fold = within(hiddenList()!).getByRole("button", { name: "Closed in the last 24 hours (2)" });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(hiddenCards()[0].compareDocumentPosition(fold) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(names(hiddenCards())).toEqual(["Reported by the session mac", "Reported by the session planner", "Reported by the session ci"]);
    expect(endedLines()).toEqual([WAITING_WORDS(T + 60, T + 1800), `Approved by your signed answer at ${dayTime(T + 120)}.`,
      UNANSWERED(T + 1800)]);
    // none left out by the server: no line for more
    expect(within(hiddenList()!).queryByText(/more, not listed/)).toBeNull();
    fireEvent.click(fold);
    expect(names(hiddenCards())).toEqual(["Reported by the session mac"]);
  });

  it("count in the fold's line the prompts the server left out (closed_more, past the newest 50), and end the open fold saying how many more there are", async () => {
    showing(sessions(), null, {
      [`GET ${CLOSED}`]: () => [200, { prompts: [
        closed(promptBy(ACCOUNT, "L2", "planner", BASH, { seq: 22 }), "approved", { closed_at: T + 120, closed_reason: "answer" }),
        closed(promptBy(ACCOUNT, "L3", "ci", BASH, { seq: 26 }), "deadline", { hidden_at: T + 60, closed_at: T + 1800, closed_reason: "deadline" }),
      ], server_time: T + 200, closed_more: 3 }],
    });
    approvals();
    const fold = await within(await screen.findByRole("region", { name: "Hidden and closed prompts" }))
      .findByRole("button", { name: /^Closed in the last 24 hours/ });
    expect(fold.textContent?.trim()).toBe("Closed in the last 24 hours (5)");
    expect(within(hiddenList()!).queryByText(/more, not listed/)).toBeNull();
    fireEvent.click(fold);
    const more = within(hiddenList()!).getByText("And 3 more, not listed here: a linked session's conversation shows its own.");
    expect(names(hiddenCards())).toEqual(["Reported by the session planner", "Reported by the session ci"]);
    expect(hiddenCards()[1].compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(fold);
    expect(within(hiddenList()!).queryByText(/more, not listed/)).toBeNull();
  });

  it("show a read's answer however many reads of the inbox followed it out, until a later read's answer is shown; an older answer coming after a newer is dropped", async () => {
    const first = closed(prompt(BASH, { seq: 51 }), "waiting", { hidden_at: T + 60 });
    const second = closed(promptBy(ACCOUNT, "L3", "ci", BASH, { seq: 53 }), "waiting", { hidden_at: T + 65 });
    const third = closed(promptBy(ACCOUNT, "L2", "planner", BASH, { seq: 52 }), "waiting", { hidden_at: T + 70 });
    // every answer of the closed route held, and given in the order the test says
    const held: ((a: Answer) => void)[] = [];
    useStore.setState({ user: owner(), links: sessions() });
    serve({
      [`GET ${SESSIONS}`]: () => [200, sessions()],
      [`GET ${CLOSED}`]: () => new Promise<Answer>((r) => { held.push(r); }),
    });
    const settle = async (i: number, prompts: Closed[]) => {
      await act(async () => { held[i]([200, { prompts, server_time: T + 100 }]); await new Promise((r) => setTimeout(r, 50)); });
    };
    approvals();
    await vi.waitFor(() => expect(held).toHaveLength(1));
    // a slow phone: the inbox read twice more (a heartbeat moves its stamp about once a minute) before the first answer
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(2));
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(3));
    await settle(0, [first]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
    // the third read's answer replaces it; the second's, coming after the third's, is dropped
    await settle(2, [third]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session planner"]));
    await settle(1, [second]);
    expect(names(hiddenCards())).toEqual(["Reported by the session planner"]);
  });

  it("count an older server's 404 only where no later read's answer is shown: one settling after a newer answer stops no later read", async () => {
    const shown = closed(prompt(BASH, { seq: 51 }), "waiting", { hidden_at: T + 60 });
    const held: ((a: Answer) => void)[] = [];
    useStore.setState({ user: owner(), links: sessions() });
    serve({
      [`GET ${SESSIONS}`]: () => [200, sessions()],
      [`GET ${CLOSED}`]: () => new Promise<Answer>((r) => { held.push(r); }),
    });
    const settle = async (i: number, a: Answer) => {
      await act(async () => { held[i](a); await new Promise((r) => setTimeout(r, 50)); });
    };
    approvals();
    await vi.waitFor(() => expect(held).toHaveLength(1));
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(2));
    await settle(1, [200, { prompts: [shown], server_time: T + 100 }]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
    // the first read's 404, from a server a deploy has since replaced, settles after the second read's answer
    await settle(0, [404, { detail: "no route" }]);
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(3));
    expect(names(hiddenCards())).toEqual(["Reported by the session mac"]);
  });

  it("count an older read's 404 only from the newest read sent: one settling while a later read is under way stops no read", async () => {
    const shown = closed(prompt(BASH, { seq: 51 }), "waiting", { hidden_at: T + 60 });
    const held: ((a: Answer) => void)[] = [];
    useStore.setState({ user: owner(), links: sessions() });
    serve({
      [`GET ${SESSIONS}`]: () => [200, sessions()],
      [`GET ${CLOSED}`]: () => new Promise<Answer>((r) => { held.push(r); }),
    });
    const settle = async (i: number, a: Answer) => {
      await act(async () => { held[i](a); await new Promise((r) => setTimeout(r, 50)); });
    };
    approvals();
    await vi.waitFor(() => expect(held).toHaveLength(1));
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(2));
    // the first read's 404 settles while the second read is still under way; the second's answer then decides
    await settle(0, [404, { detail: "no route" }]);
    await settle(1, [200, { prompts: [shown], server_time: T + 100 }]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
    act(() => { useStore.setState({ links: sessions() }); });
    await vi.waitFor(() => expect(held).toHaveLength(3));
  });
});

describe("Approvals with nothing open", () => {
  it("says, while nothing else waits and a prompt he hid still does, that nothing waits in his open list and the prompt he hid waits below until its deadline; the shield and the counts stay as they were", async () => {
    const links = sessions({ prompts: [], hidden_waiting: [21] });
    showing(links, [closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 })]);
    approvals();
    expect(screen.queryByText(EMPTY_HIDDEN)).not.toBeNull();
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    expect(shield()).toContain("text-done");
    // a prompt he hid is not counted, since he hid it
    expect([linkWaiting(links), channelAlerts(links)]).toEqual([0, 0]);
    await vi.waitFor(() => expect(names(hiddenCards())).toEqual(["Reported by the session mac"]));
  });

  it("says, as before, that nothing is waiting where nothing he hid still waits or the server does not say, and neither while something else waits", async () => {
    for (const over of [{}, { hidden_waiting: [] }]) {
      showing(sessions({ prompts: [], ...over }), []);
      approvals();
      expect(screen.queryByText("Nothing is waiting for you."), JSON.stringify(over)).not.toBeNull();
      expect(screen.queryByText(EMPTY_HIDDEN), JSON.stringify(over)).toBeNull();
      expect(shield()).toContain("text-done");
      cleanup();
    }
    showing(sessions({ prompts: [prompt(BASH)], hidden_waiting: [21] }), []);
    approvals();
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    expect(screen.queryByText(EMPTY_HIDDEN)).toBeNull();
    expect(shield()).toContain("text-wait");
  });
});

describe("the note under a prompt's Close", () => {
  it("says, in Approvals, that Close takes the prompt out of the open list, the session keeps waiting for his answer until its deadline, he can still answer it among the hidden ones below, and unanswered by then its session's hook refuses it and the session goes on", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }), []);
    approvals();
    await screen.findByText("Bash");
    expect(noteOf(screen.getByRole("button", { name: "Close" }))).toBe(NOTE);
  });

  it("says, for an item to look at, which a Close closes as it did before, the words it had then", async () => {
    showing(sessions({ prompts: [prompt(BASH, { look: true })] }), []);
    approvals();
    await screen.findByText("Bash");
    expect(noteOf(screen.getByRole("button", { name: "Close" }))).toBe(NOTE_LOOK);
  });

  it("says, for a prompt whose text is not in a prompt's form, that it waits for an answer, without offering one here", async () => {
    showing(sessions({ prompts: [prompt({}, { client_id: "", text: "Push to main?" })] }), []);
    approvals();
    await screen.findByText(/Its text is not in the form a prompt takes/);
    expect(noteOf(screen.getByRole("button", { name: "Close" }))).toBe(NOTE_NOT_A_PROMPT);
  });

  it("points, in a session's conversation, to the hidden prompts in Approvals", async () => {
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }), [], { "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }] });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("Bash");
    expect(noteOf(screen.getByRole("button", { name: "Close" }))).toBe(NOTE_ELSEWHERE);
  });
});

describe("a hidden prompt in its session's conversation", () => {
  it("is said to be still open and answerable in Approvals, never closed, where it is out of the open list with no close recorded", async () => {
    const p = prompt(BASH);
    showing(sessions({ prompts: [] }), [], { "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }] });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("Bash");
    const [card] = cards();
    expect(card.textContent).toContain("Still open: it waits for your answer in Approvals until its deadline.");
    expect(card.textContent).not.toContain("Closed.");
    expect(buttonsOf(card)).toEqual([]);
  });

  it("is said, for a text not in a prompt's form, to be still open and answered where the session shows it, never in Approvals", async () => {
    const p = prompt({}, { client_id: "", text: "Push to main?" });
    showing(sessions({ prompts: [] }), [], { "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }] });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText(/Its text is not in the form a prompt takes|Push to main/);
    const [card] = cards();
    expect(card.textContent).toContain("Still open: it is answered where the session shows it.");
    expect(card.textContent).not.toContain("in Approvals");
  });

  it("is said, for an item to look at, to be still open and answered where the session shows it, never closed", async () => {
    const p = prompt(BASH, { look: true });
    showing(sessions({ prompts: [] }), [], { "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }] });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("Bash");
    const [card] = cards();
    expect(card.textContent).toContain("Still open: it is answered where the session shows it.");
    expect(card.textContent).not.toContain("Closed.");
    expect(buttonsOf(card)).toEqual([]);
  });
});

describe("a sealed prompt whose text is not opened yet", () => {
  // keys locked (and always in the desktop and phone apps, which never open a text): it is a prompt of this version's
  // hook as far as the page can tell, so the promise and the way to answer it stand
  it("keeps, hidden and waiting, the waiting words, never those of a text not in a prompt's form", async () => {
    fake.state = "locked";
    showing(sessions(), [closed(prompt(BASH, { seq: 21 }), "waiting", { hidden_at: T + 60 })]);
    approvals();
    await vi.waitFor(() => expect(cards()).toHaveLength(1));
    expect(endedLines()).toEqual([WAITING_WORDS(T + 60, T + 1800)]);
  });

  it("is said, out of the open list in its session's conversation, to wait for his answer in Approvals", async () => {
    fake.state = "locked";
    const p = prompt(BASH);
    showing(sessions({ prompts: [] }), [], { "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }] });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await vi.waitFor(() => expect(cards()).toHaveLength(1));
    expect(cards()[0].textContent).toContain("Still open: it waits for your answer in Approvals until its deadline.");
  });
});
