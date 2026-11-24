import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LinkEvent, LinkSessions, LinkView } from "../api";
import { eventSlot } from "../e2e";
import { textDigest } from "../passkey";
import { useStore } from "../store";
import { Approvals } from "./Approvals";
import { Conversation, latestLead, LinkCard } from "./Channel";
import { sealedEvent } from "./Sealed";

// The owner's inbox: what a linked session reports waits for the
// owner, its prompts, its questions to the owner and its proposals, shown in Approvals beside the held commands, each
// prompt marked as the session's report and opened with the account's keys, and answered with a passkey.
//
// The keys behind e2e.ts are another change's: as in Sealed.test.tsx, a stand-in holds the tab's state, and an
// "envelope" is the value and the slot it was sealed to, so it opens only with that slot, as a real one does. The page
// signs with a passkey here.
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
const T = 1_790_000_000;
const PROMPT_CID = "0123456789abcdef0123456789abcdef";
const PROPOSAL_CID = "fedcba9876543210fedcba9876543210";
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
/** A text a link sealed to the owner, as an event's: its words, bound to its slot. */
const sealedBy = (linkId: string, kind: string, clientId: string, words: string) =>
  seal({ text: words, session: null, said_at: null }, eventSlot(ACCOUNT, linkId, kind, undefined, null, clientId));

const BASH = { tool: "Bash", input: { command: "rm -rf build", description: "Clean the build" }, session_id: "cc-1", agent_id: null, deadline: T + 1800 };
const ASKED = {
  tool: "AskUserQuestion",
  input: { questions: [
    { question: "Which cap?", header: "Cap", multiSelect: false, options: [{ label: "Per user", description: "One cap each" }, { label: "Per org" }] },
    { question: "Which store?", header: "Store", multiSelect: false, options: [{ label: "SQLite" }, { label: "Postgres" }] },
  ] },
  questions: [
    { question: "Which cap?", header: "Cap", multiSelect: false, options: [{ n: 1, label: "Per user", description: "One cap each" }, { n: 2, label: "Per org" }] },
    { question: "Which store?", header: "Store", multiSelect: false, options: [{ n: 3, label: "SQLite" }, { n: 4, label: "Postgres" }] },
  ],
  session_id: "cc-1", agent_id: null, deadline: T + 1800,
};

type Prompt = LinkEvent & { link_id: string; deadline_at: number; look: boolean };
type Proposed = LinkEvent & { link_id: string; for_id: string; for: string; deadline_at: number };

/** A prompt as GET /api/ade/link/sessions lists it: the event's view, its link and deadline, and whether it is to look at. */
const prompt = (item: unknown, over: Partial<Prompt> = {}): Prompt => ({
  seq: 7, time: "2026-11-02T10:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null,
  text: sealedBy("L1", "prompt", PROMPT_CID, JSON.stringify(item)), client_id: PROMPT_CID, from_id: "L1",
  link_id: "L1", deadline_at: T + 1800, look: false, ...over,
});
const proposal = (words: string, over: Partial<Proposed> = {}): Proposed => ({
  seq: 11, time: "2026-11-02T10:01:00Z", kind: "proposal", from: "planner", to: "owner", ref: null,
  text: sealedBy("L2", "proposal", PROPOSAL_CID, words), client_id: PROPOSAL_CID, from_id: "L2",
  link_id: "L2", for_id: "L1", for: "mac", deadline_at: T + 86400, ...over,
});
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const sessions = (over: Record<string, unknown> = {}) => ({
  links: [link(), link({ id: "L2", name: "planner" })], questions: [], public_url: "https://pp.example", prompts: [], proposals: [], ...over,
}) as unknown as LinkSessions;

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
type Answer = [number, unknown];
let calls: Call[] = [];
/** A server of the test's own: each route answers [status, data]; anything else 404. */
function serve(more: Record<string, (body: Record<string, unknown> | undefined) => Answer> = {}) {
  const routes: Record<string, (body: Record<string, unknown> | undefined) => Answer> = {
    "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: ACCOUNT, server_time: T }],
    // no signer paired, so Approve is one press; with one, or unread, it opens the review
    "GET /api/ade/signers": () => [200, { signers: [] }],
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
    "POST /api/ade/link/questions/7/close": () => [200, { closed: 7 }],
    "POST /api/ade/link/questions/11/close": () => [200, { closed: 11 }],
    ...more,
  };
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: "no route" }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
const signed = () => sent("/api/ade/link/commands").map((c) => c.body?.payload as Record<string, unknown>);
const noEnvelope = () => expect(new XMLSerializer().serializeToString(document.body)).not.toContain("e2e1.");
const approvals = () => render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} />);
/** The prompt is listed, as the session's report: checked first, before any of its words are waited for. */
const listed = () => expect(screen.queryByText(/Reported by the session mac/)).not.toBeNull();
/** Review, then Sign with a passkey: the signed answer reaches the commands route. */
const reviewAndSign = async (review: RegExp) => {
  fireEvent.click(screen.getByRole("button", { name: review }));
  fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
  await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
};

/** One press on Approve once its answer is prepared, and Undo's window passed, the fake clock moved a second at a time:
 *  the signed answer goes to the commands route. Fake timers are on. */
const approveAndWait = async () => {
  const approve = screen.getByRole("button", { name: "Approve" });
  await vi.waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false));
  fireEvent.click(approve);
  await screen.findByRole("button", { name: "Undo" });
  for (let i = 0; i < 12 && !sent("/api/ade/link/commands").length; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
};

beforeEach(() => {
  fake.state = "unlocked";
  fake.on = true;
  fake.generation += 1;
  fake.opened.length = 0;
  fake.credentials.get.mockClear();
  serve();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

const showing = (links: LinkSessions) => {
  useStore.setState({ user: { id: ACCOUNT, email: "o@example.org", can_sign_in_again: true }, links });
  serve({ "GET /api/ade/link/sessions": () => [200, links] });
};

describe("a prompt a linked session reports, in Approvals", () => {
  it("is listed beside the held commands, marked as the session's report, with its tool and whole input opened with the account's keys", async () => {
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    expect(screen.queryByText(/Reported by the session mac/)).not.toBeNull();
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    expect(await screen.findByText("Bash")).toBeTruthy();
    expect(screen.getByLabelText("Its input, whole").textContent).toContain('"command": "rm -rf build"');
    expect(screen.getByLabelText("Its input, whole").textContent).toContain('"description": "Clean the build"');
    expect(fake.opened).toContain(p.text);
    noEnvelope();
  });

  it("shows an input its text holds whole in its box, {} as much as any (and null or []), and never says it was not posted", async () => {
    const kept = [["mcp__clock__now", {}, "{}"], ["mcp__clock__zone", null, "null"], ["mcp__clock__list", [], "[]"]] as const;
    showing(sessions({ prompts: kept.map(([tool, input], i) => prompt({ ...BASH, tool, input }, { seq: [7, 12, 13][i] })) }));
    approvals();
    expect(screen.queryAllByText(/Reported by the session mac/)).toHaveLength(3);
    for (const [tool] of kept) await screen.findByText(tool);
    const cards = screen.getAllByRole("group", { name: /Reported by the session mac/ });
    kept.forEach(([tool, , shownAs], i) => {
      expect(within(cards[i]).getByText(tool)).toBeTruthy();
      expect(within(cards[i]).queryByRole("button", { name: "Approve" }), tool).not.toBeNull();
      expect(within(cards[i]).getByLabelText("Its input, whole").textContent, tool).toBe(shownAs);
      expect(within(cards[i]).queryByLabelText("Its input, not posted"), tool).toBeNull();
    });
  });

  it("is never shown raw: a sealed one is Encrypted with Unlock while the keys are locked", async () => {
    fake.state = "locked";
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    expect(screen.queryByText(/Reported by the session mac/)).not.toBeNull();
    expect(screen.getByText("Encrypted")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(document.body.textContent).not.toContain("rm -rf build");
    noEnvelope();
  });

  it("is approved by an answer signed with a passkey, with one press: its ref the prompt, its digest the stored text's, its text Approve", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeNull();
    await approveAndWait();                                // its answer prepared before the press, one press, Undo's window
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    const [payload] = signed();
    expect(payload).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Approve", digest: await textDigest(p.text) });
    expect(String(payload.digest)).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);
  });

  it("says before the signing what goes in the clear: the word Approve, the word Deny, or his words as written", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeNull();
    // one press signs: what goes in the clear is said beside Approve, before any press
    expect(screen.getByRole("note", { name: "In the clear" }).textContent).toMatch(/the word “Approve”.*in the clear/);
    expect(fake.credentials.get).not.toHaveBeenCalled();
    cleanup();
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(screen.getByRole("note", { name: "In the clear" }).textContent).toMatch(/the word “Deny”.*in the clear/);
    cleanup();
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(screen.getByRole("button", { name: "Answer in your own words" }));
    expect(screen.getByRole("note", { name: "In the clear" }).textContent).toMatch(/your words, as you write them.*in the clear/);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("is refused with the word Deny, signed", async () => {
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Deny" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await reviewAndSign(/Review the denial/);
    expect(signed()[0]).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Deny", digest: await textDigest(p.text) });
  });

  it("is answered in his own words, signed as he wrote them", async () => {
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Answer in your own words" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Answer in your own words" }));
    fireEvent.change(screen.getByLabelText("Your words"), { target: { value: "Not the build folder: clean dist instead." } });
    await reviewAndSign(/Review your answer/);
    expect(signed()[0]).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Not the build folder: clean dist instead.",
      digest: await textDigest(p.text) });
  });

  it("refuses his own words where they read as Approve, Deny or a choice of options, saying to use the buttons, and signs nothing", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Answer in your own words" })).not.toBeNull();
    // one press's approval is prepared ahead: a nonce for it, none for the words
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(false));
    const prepared = sent("/api/ade/link/nonce").length;
    fireEvent.click(screen.getByRole("button", { name: "Answer in your own words" }));
    for (const words of ["Approve", "approve", "Approved!", "DENY.", "denied", " Deny ", "'approve'", "1,3", "2", "1, 3", "3,1", "1;3", "2."]) {
      fireEvent.change(screen.getByLabelText("Your words"), { target: { value: words } });
      fireEvent.click(screen.getByRole("button", { name: /Review your answer/ }));
      expect((await screen.findByRole("alert")).textContent, words).toMatch(/may not read as Approve, Deny or a choice of options: use the buttons/);
    }
    expect(sent("/api/ade/link/nonce")).toHaveLength(prepared);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("is closed with the sign-in alone: no words, nothing signed, and the page says the close decides nothing", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByText(/decides nothing/)).not.toBeNull();
    // a Close hides the prompt: its session keeps waiting for his answer until its deadline (OwnerInbox.close.test.tsx)
    expect(screen.getByText(/decides nothing/).textContent).toMatch(/keeps waiting for your answer until its deadline/);
    // one press's approval is prepared ahead: its nonce, and none for the Close
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(false));
    const prepared = sent("/api/ade/link/nonce").length;
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/7/close")).toHaveLength(1));
    expect(sent("/api/ade/link/questions/7/close")[0].body).toBeUndefined();
    expect(sent("/api/ade/link/nonce")).toHaveLength(prepared);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("says, when the server refuses the answer after the session's report, that the prompt was answered where the session shows it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    serve({
      "GET /api/ade/link/sessions": () => [200, sessions({ prompts: [] })],
      "POST /api/ade/link/commands": () => [409, { detail: "The ref is not an open prompt that session posted." }],
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [
        { ...p, closed_reason: "report:PostToolUse", closed_by: "L1" },
        { seq: 8, time: "2026-11-02T10:00:30Z", kind: "note", from: "mac", to: "owner", ref: 7, text: "reported: PostToolUse", client_id: "report:7", from_id: "L1" },
      ] }],
    });
    approvals();
    listed();
    await screen.findByText("Bash");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeNull();
    await approveAndWait();
    // said where it is read, and still said once the prompt has left the list
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/This prompt was answered where the session shows it/));
    await vi.waitFor(() => expect(screen.queryByText(/Reported by the session mac/)).toBeNull());
    expect(document.body.textContent).toMatch(/This prompt was answered where the session shows it/);
  });
});

describe("a question prompt (AskUserQuestion), in Approvals", () => {
  it("shows its options with their numbers, and choosing 1 and 3 signs the answer 1,3", async () => {
    const p = prompt(ASKED);
    showing(sessions({ prompts: [p] }));
    approvals();
    listed();
    expect(await screen.findByText("AskUserQuestion")).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /^1\. Per user/ })).not.toBeNull();
    expect(screen.getByRole("radio", { name: /^2\. Per org/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /^3\. SQLite/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /^4\. Postgres/ })).toBeTruthy();
    // a question's answer is its options, never the word Approve
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    const choose = screen.getByRole("button", { name: "Answer with the chosen options" }) as HTMLButtonElement;
    expect(choose.disabled).toBe(true);                     // one option for each question first
    fireEvent.click(screen.getByRole("radio", { name: /^1\. Per user/ }));
    expect(choose.disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /^3\. SQLite/ }));
    fireEvent.click(choose);
    expect(screen.getByRole("note", { name: "In the clear" }).textContent).toMatch(/the option numbers “1,3”.*in the clear/);
    await reviewAndSign(/Review the choice/);
    expect(signed()[0]).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "1,3", digest: await textDigest(p.text) });
  });

  it("takes several options of a question that allows them, in ascending order", async () => {
    const multi = { ...ASKED, questions: [{ question: "Which checks?", header: "Checks", multiSelect: true,
      options: [{ n: 1, label: "Lint" }, { n: 2, label: "Types" }, { n: 3, label: "Tests" }] }] };
    showing(sessions({ prompts: [prompt(multi)] }));
    approvals();
    listed();
    await screen.findByText("AskUserQuestion");
    expect(screen.queryByRole("checkbox", { name: /^3\. Tests/ })).not.toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /^3\. Tests/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /^1\. Lint/ }));
    fireEvent.click(screen.getByRole("button", { name: "Answer with the chosen options" }));
    await reviewAndSign(/Review the choice/);
    expect(signed()[0]).toMatchObject({ text: "1,3" });
  });
});

describe("an item to look at, in Approvals", () => {
  it("shows its words and that it is answered where the session shows it, with no decision to make here", async () => {
    showing(sessions({ prompts: [
      prompt({}, { look: true, text: sealedBy("L1", "prompt", PROMPT_CID, "Session paused") }),
      prompt({}, { seq: 12, look: true, client_id: "", text: "ExitPlanMode" }),
    ] }));
    approvals();
    expect(screen.queryAllByText(/Reported by the session mac/)).toHaveLength(2);
    expect(await screen.findByText("Session paused")).toBeTruthy();
    expect(screen.getByText("ExitPlanMode")).toBeTruthy();
    expect(screen.getAllByText(/answered where the session shows it/i)).toHaveLength(2);
    for (const name of ["Approve", "Deny", "Answer in your own words", "Answer with the chosen options"]) {
      expect(screen.queryByRole("button", { name }), name).toBeNull();
    }
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("says its input was not posted to the list where its text is the tool's name alone, and never shows it as \"(no input)\"", async () => {
    // what the session's client posts where a call's text may not go to the list (ade_link._prompt_look): the tool's
    // name alone, with no `input` key, which is a withheld input and not a call that has none
    showing(sessions({ prompts: [prompt({ tool: "Bash", deadline: T + 1800, session_id: "cc-1", agent_id: null }, { look: true })] }));
    approvals();
    listed();
    expect(await screen.findByText("Bash")).toBeTruthy();
    const card = screen.getByRole("group", { name: /Reported by the session mac/ });
    expect(within(card).queryByLabelText("Its input, not posted")?.textContent)
      .toBe("Its input was not posted to the list: the session's window shows the whole call.");
    expect(within(card).queryByLabelText("Its input, whole")).toBeNull();
    expect(document.body.textContent).not.toContain("(no input)");
  });
});

describe("an item Claude Code raised itself (a notification, a turn an API error ended, a model switch), in Approvals", () => {
  const raisedBy = (opened: unknown, over: Partial<Prompt> = {}) =>
    prompt({}, { look: true, text: sealedBy("L1", "prompt", PROMPT_CID, JSON.stringify(opened)), ...over });

  it("is shown as Claude Code's event and each of its own words, field by field, never as JSON, with nothing to decide", async () => {
    showing(sessions({ prompts: [raisedBy({ event: "Notification", session_id: "cc-1", agent_id: null, deadline: T + 1800,
      words: { message: "Claude needs your permission to use Bash", title: "Claude Code", notification_type: "permission_prompt" } })] }));
    approvals();
    listed();
    await vi.waitFor(() => expect(screen.queryByText("Claude Code: Notification")).not.toBeNull());
    const card = screen.getByRole("group", { name: /Reported by the session mac/ });
    for (const [key, value] of [["message", "Claude needs your permission to use Bash"], ["title", "Claude Code"],
      ["notification_type", "permission_prompt"]]) {
      expect(within(card).getByText(key)).toBeTruthy();
      expect(within(card).getByText(value)).toBeTruthy();
    }
    expect(card.textContent).not.toContain("{");
    expect(card.textContent).not.toContain("cc-1");
    expect(within(card).getByText(/answered where the session shows it/i)).toBeTruthy();
    for (const name of ["Approve", "Deny", "Answer in your own words", "Answer with the chosen options"]) {
      expect(within(card).queryByRole("button", { name }), name).toBeNull();
    }
    noEnvelope();
  });

  it("shows the event alone where its words broke the rules, and an item in the clear as the one line it is", async () => {
    showing(sessions({ prompts: [
      raisedBy({ event: "StopFailure", deadline: T + 1800 }),
      prompt({}, { seq: 12, look: true, client_id: "", text: "StopFailure: rate_limit" }),
    ] }));
    approvals();
    await vi.waitFor(() => expect(screen.queryByText("Claude Code: StopFailure")).not.toBeNull());
    const [sealed, clear] = screen.getAllByRole("group", { name: /Reported by the session mac/ });
    expect(within(sealed).getByText("Claude Code: StopFailure")).toBeTruthy();
    expect(sealed.textContent).not.toContain("{");
    expect(sealed.textContent).not.toContain("deadline");
    expect(within(clear).getByText("StopFailure: rate_limit")).toBeTruthy();
    expect(within(clear).queryByText(/^Claude Code:/)).toBeNull();
  });
});

// What a session's client seals is its item's JSON, and JSON may write any character as an escape: the text the server
// keeps, and a sealed text once opened, is then plain ASCII, and the character is in the texts the card shows. The
// session's own client holds each of an item's texts and names to the rule on characters before it seals
// (ade_link.prompt_text_refusal), so only another program holding a link's key posts such an item; the card holds
// each text and name within the item to the same clauses, sealed or in the clear, and shows none of an item that
// breaks one. Each character below is written by its code point, so no editor or transport turns it into itself.
describe("an item whose text within holds a character that does not show itself, written in its JSON as an escape", () => {
  const cp = (code: number) => String.fromCodePoint(code);
  /** A value's JSON with every character past ASCII written as an escape. */
  const escaped = (value: unknown) => JSON.stringify(value).replace(/[^\x00-\x7f]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  const DRAWN = "This text is not shown: it holds a mark drawn over or around a character, which could change how it reads";
  /** The four: how each is put into a text, and what the card says in the item's place. */
  const FOUR: [string, (text: string) => string, string][] = [
    ["a solidus drawn over a character (U+0338)", (t) => `${t[0]}${cp(0x0338)}${t.slice(1)}`, DRAWN],
    ["a stroke drawn over a character (U+0336)", (t) => `${t[0]}${cp(0x0336)}${t.slice(1)}`, DRAWN],
    ["an accent after its letter, the text not in normal form C (U+0301)", (t) => `${t}e${cp(0x0301)}`, "This text is not shown: it is not in Unicode normal form C"],
    ["a selector at the text's start (U+FE0F)", (t) => `${cp(0xfe0f)}${t}`, "This text holds characters the ADE does not show"],
  ];
  const SEQS = [7, 12, 13, 14];
  const ANSWERS = ["Approve", "Deny", "Answer in your own words", "Answer with the chosen options"];
  /** As a session seals an item, to be answered here. */
  const sealedItem = (text: string): Partial<Prompt> => ({ text: sealedBy("L1", "prompt", PROMPT_CID, text) });
  /** As a link posts an item to look at in the clear. */
  const clearItem = (text: string): Partial<Prompt> => ({ look: true, client_id: "", text });

  /** One card for each of the four, its item made by `item` from how the character is put into a text: each says in
   *  its item's place why it is not shown, shows none of `never` and offers no answer. */
  async function notShown(item: (put: (text: string) => string) => unknown, as: (text: string) => Partial<Prompt>, never: string[],
                          written: (value: unknown) => string = escaped) {
    showing(sessions({ prompts: FOUR.map(([, put], i) => prompt({}, { seq: SEQS[i], ...as(written(item(put))) })) }));
    approvals();
    const cards = screen.getAllByRole("group", { name: /Reported by the session mac/ });
    expect(cards).toHaveLength(4);
    const look = as("").look === true;
    for (const [i, [name, , said]] of FOUR.entries()) {
      expect(await within(cards[i]).findByText(said), name).toBeTruthy();
      // where it is answered: said of an item to look at as of any, and of one to answer since nothing here answers it
      const LOOK = "It is answered where the session shows it, its window or its page: nothing here decides it.";
      const NOT_SHOWN = "Its text is not shown, so nothing here answers it: it is answered where the session shows it.";
      expect(within(cards[i]).queryAllByText(LOOK), name).toHaveLength(look ? 1 : 0);
      expect(within(cards[i]).queryAllByText(NOT_SHOWN), name).toHaveLength(look ? 0 : 1);
      expect(within(cards[i]).queryByLabelText("Its input, whole"), name).toBeNull();
      for (const answer of ANSWERS) expect(within(cards[i]).queryByRole("button", { name: answer }), `${name}: ${answer}`).toBeNull();
      expect(within(cards[i]).queryByRole("radio"), name).toBeNull();
      for (const word of never) expect(cards[i].textContent, `${name}: ${word}`).not.toContain(word);
    }
    noEnvelope();
  }

  it("is not shown where a sealed prompt opens to a Bash call whose command holds one: why is said in its place, and nothing answers it here", async () => {
    await notShown((put) => ({ ...BASH, input: { ...BASH.input, command: put("rm -rf build") } }), sealedItem, ["rf build", "Bash", "Clean the build"]);
  });

  it("is not shown where a name of its input holds one", async () => {
    await notShown((put) => ({ ...BASH, input: { [put("command")]: "rm -rf build", description: "Clean the build" } }), sealedItem, ["ommand", "rm -rf build", "Bash"]);
  });

  it("is not shown where the tool's name holds one", async () => {
    await notShown((put) => ({ ...BASH, tool: put("Bash") }), sealedItem, ["ash", "rm -rf build"]);
  });

  it("is not shown where a question's words hold one: the question, its header, an option's label or its description", async () => {
    const [first, second] = ASKED.questions;
    const asked = (q: Record<string, unknown>) => ({ ...ASKED, questions: [{ ...first, ...q }, second] });
    const [one, two] = first.options;
    await notShown((put) => asked({ question: put("Which cap?") }), sealedItem, ["hich cap?", "Per user", "Which store?"]);
    cleanup();
    await notShown((put) => asked({ header: put("Cap") }), sealedItem, ["Which cap?", "Per user"]);
    cleanup();
    await notShown((put) => asked({ options: [{ ...one, label: put("Per user") }, two] }), sealedItem, ["Which cap?", "er user"]);
    cleanup();
    await notShown((put) => asked({ options: [{ ...one, description: put("One cap each") }, two] }), sealedItem, ["Which cap?", "ne cap each"]);
  });

  it("is not shown where it is nested in the input, in a list or in an object, at any depth", async () => {
    await notShown((put) => ({ ...BASH, tool: "mcp__files__move", input: { moves: [{ from: "a", to: ["b", { deep: [[put("build")]] }] }] } }),
                   sealedItem, ["uild", "mcp__files__move"]);
  });

  it("is not shown where a text of the item that the card does not show holds one: its session's id, a field the card does not read", async () => {
    await notShown((put) => ({ ...BASH, session_id: put("cc-1") }), sealedItem, ["rm -rf build", "Bash"]);
    cleanup();
    await notShown((put) => ({ ...BASH, [put("where")]: { folder: "/srv/app" } }), sealedItem, ["rm -rf build", "Bash"]);
  });

  it("is not shown where the item came in the clear either, written as an escape or as itself, nor where a sealed one is only to look at", async () => {
    const item = (put: (text: string) => string) => ({ ...BASH, input: { ...BASH.input, command: put("rm -rf build") } });
    await notShown(item, clearItem, ["rf build", "Bash"]);
    cleanup();
    await notShown(item, clearItem, ["rf build", "Bash"], JSON.stringify);
    cleanup();
    await notShown(item, (text) => ({ look: true, ...sealedItem(text) }), ["rf build", "Bash"]);
  });

  it("offers Close alone on an item not shown, which hides it with the sign-in alone: nothing is signed, and its card goes", async () => {
    showing(sessions({ prompts: [prompt({}, sealedItem(escaped({ ...BASH, input: { command: FOUR[0][1]("rm -rf build") } })))] }));
    approvals();
    const card = screen.getByRole("group", { name: /Reported by the session mac/ });
    expect(await within(card).findByText(DRAWN)).toBeTruthy();
    expect(within(card).getAllByRole("button").map((b) => b.textContent)).toEqual(["Close"]);
    fireEvent.click(within(card).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/7/close")).toHaveLength(1));
    expect(sent("/api/ade/link/questions/7/close")[0].body).toBeUndefined();
    expect(signed()).toHaveLength(0);
    await vi.waitFor(() => expect(screen.queryByRole("group", { name: /Reported by the session mac/ })).toBeNull());
  });

  it("holds the event's name of an item Claude Code raised as it holds each of its words, and shows the rest", async () => {
    const raised = (put: (text: string) => string) => ({ event: put("Notification"), session_id: "cc-1", agent_id: null, deadline: T + 1800,
      words: { message: "Claude needs your attention", title: put("Claude Code") } });
    showing(sessions({ prompts: FOUR.map(([, put], i) => prompt({}, { seq: SEQS[i], look: true, text: sealedBy("L1", "prompt", PROMPT_CID, escaped(raised(put))) })) }));
    approvals();
    const cards = screen.getAllByRole("group", { name: /Reported by the session mac/ });
    for (const [i, [name, , said]] of FOUR.entries()) {
      expect(await within(cards[i]).findByText("Claude needs your attention"), name).toBeTruthy();
      // the event's name and the title, each in its own place
      expect(within(cards[i]).getAllByText(said), name).toHaveLength(2);
      expect(cards[i].textContent, name).toContain(`Claude Code: ${said}`);
      expect(cards[i].textContent, name).not.toContain("otification");
      expect(within(cards[i]).getByText("message"), name).toBeTruthy();
    }
  });

  it("is still shown whole, and answered here, where its texts hold what the session's own client seals", async () => {
    // a line break and a tab, an accented letter in normal form C, an emoji with its selector after it, a character
    // of two code units, a letter past the Latin ones, an empty text, an empty name, a text that begins as an
    // envelope does, and a combining mark in a text of its own: none of them breaks the rule on characters
    const command = `printf 'a\\tb'\n\tcaf${cp(0xe9)} ${cp(0x2764)}${cp(0xfe0f)} ${cp(0x1f600)} ${cp(0x4e2d)}`;
    const input = { command, description: "", "": "x", list: ["", "e2e1.x", `x${cp(0x0323)}`, 7, true, null] };
    showing(sessions({ prompts: [JSON.stringify, escaped].map((written, i) =>
      prompt({}, { seq: SEQS[i], ...sealedItem(written({ ...BASH, tool: `mcp__caf${cp(0xe9)}__run`, input })) })) }));
    approvals();
    const cards = screen.getAllByRole("group", { name: /Reported by the session mac/ });
    expect(cards).toHaveLength(2);
    for (const card of cards) {
      expect(await within(card).findByText(`mcp__caf${cp(0xe9)}__run`)).toBeTruthy();
      expect(within(card).getByLabelText("Its input, whole").textContent).toBe(JSON.stringify(input, null, 2));
      expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeNull();
      expect(within(card).queryByRole("button", { name: "Deny" })).not.toBeNull();
    }
  });

  it("shows as the words they are a text that is no item, its escapes as it writes them", async () => {
    // not a tool call and not what Claude Code raised: nothing is parsed out of it, so nothing in it reads otherwise
    const text = escaped({ note: FOUR[0][1]("not a tool call") });
    showing(sessions({ prompts: [prompt({}, { look: true, ...sealedItem(text) })] }));
    approvals();
    expect(await screen.findByText(text)).toBeTruthy();
    expect(text).toContain("\\u0338");
  });
});

describe("a question a linked session asks the owner, in Approvals", () => {
  it("is listed there too, with Answer", async () => {
    const q = { seq: 5, time: "2026-11-02T09:59:00Z", kind: "question", from: "mac", to: "owner", ref: null,
      text: sealedBy("L1", "question", PROMPT_CID, "Push to staging?"), client_id: PROMPT_CID, from_id: "L1", link_id: "L1" };
    showing(sessions({ questions: [q] }));
    approvals();
    expect(screen.queryByText(/mac asks you/)).not.toBeNull();
    expect(await screen.findByText("Push to staging?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Answer/ })).toBeTruthy();
  });
});

describe("a linked session's proposal, in Approvals", () => {
  it("shows its proposer and the session it names, and is signed as an instruction to that session alone, its first line naming the proposer", async () => {
    showing(sessions({ proposals: [proposal("Run the migration tests, then report.")] }));
    approvals();
    expect(screen.queryByText(/Proposed by the session planner, for mac/)).not.toBeNull();
    expect(await screen.findByText("Run the migration tests, then report.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sign it as your instruction" }));
    // its first line is fixed, the words below it his to change
    expect(screen.getByLabelText("Its first line, which stays").textContent).toBe("Proposed by planner:");
    const words = screen.getByLabelText("Its words, which you may change") as HTMLTextAreaElement;
    expect(words.value).toBe("Run the migration tests, then report.");
    fireEvent.change(words, { target: { value: "Run the migration tests." } });
    await reviewAndSign(/Review the instruction/);
    const [payload] = signed();
    expect(payload).toMatchObject({ purpose: "instruction", id: PROPOSAL_CID, to: "L1", text: "Proposed by planner:\nRun the migration tests." });
    expect(payload.to).not.toBe("L2");
  });

  it("is closed with the sign-in alone, with no words", async () => {
    showing(sessions({ proposals: [proposal("Run the migration tests.")] }));
    approvals();
    expect(screen.queryByText(/Proposed by the session planner, for mac/)).not.toBeNull();
    await screen.findByText("Run the migration tests.");
    const card = screen.getByRole("group", { name: /Proposed by the session planner/ });
    fireEvent.click(within(card).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/11/close")).toHaveLength(1));
    expect(sent("/api/ade/link/questions/11/close")[0].body).toBeUndefined();
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });
});

describe("the switch that lets a linked session propose", () => {
  it("is off by default and turned on with the sign-in, by its own route", async () => {
    serve({ "PUT /api/ade/link/links/L1/proposes": () => [200, { proposes: true }] });
    useStore.setState({ user: { id: ACCOUNT, email: "o@example.org", can_sign_in_again: true } });
    render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    const box = screen.queryByRole("checkbox", { name: /may propose/ }) as HTMLInputElement | null;
    expect(box).not.toBeNull();
    expect(box!.checked).toBe(false);
    fireEvent.click(box!);
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({ path: "/api/ade/link/links/L1/proposes", body: { allowed: true } });
  });
});

describe("a linked session's conversation", () => {
  it("shows its own prompts in its thread, with the same buttons as Approvals", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    serve({
      "GET /api/ade/link/sessions": () => [200, sessions({ prompts: [p] })],
      "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [p] }],
    });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await vi.waitFor(() => expect(screen.queryByText(/Reported by the session mac/)).not.toBeNull());
    expect(await screen.findByText("Bash")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
    await approveAndWait();
    expect(signed()[0]).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Approve", digest: await textDigest(p.text) });
  });
});

describe("what a session posts of its prompts, outside their cards", () => {
  it("is its report note, a fixed text in the clear, as the server's link.fixed_text takes it, and marked only when it is not that", () => {
    const report = (over: Partial<LinkEvent>): LinkEvent => ({ seq: 8, time: "t", kind: "note", from: "mac", to: "owner", ref: 7,
      text: "reported: PostToolUse", client_id: "report:7", from_id: "L1", ...over });
    for (const event of ["PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "UserPromptSubmit"]) {
      expect(sealedEvent(report({ text: `reported: ${event}` })), event).toBe(false);
    }
    // anything else on that path is one encryption seals
    expect(sealedEvent(report({ text: "reported: PreToolUse" }))).toBe(true);
    expect(sealedEvent(report({ text: "reported: PostToolUse ran rm -rf build" }))).toBe(true);
    expect(sealedEvent(report({ mirror: "session" }))).toBe(true);
    expect(sealedEvent(report({ client_id: "report:x" }))).toBe(true);
  });

  it("never reads a prompt's or a proposal's words as a card's newest line: those are read on their cards", () => {
    const l = { commands: [] };
    expect(latestLead({ kind: "prompt", to: "owner", ref: null, client_id: PROMPT_CID }, l).words).toBe(false);
    expect(latestLead({ kind: "proposal", to: "owner", ref: null, client_id: PROPOSAL_CID }, l).words).toBe(false);
    expect(latestLead({ kind: "note", to: "owner", ref: 7, client_id: "report:7" }, l).lead).toBe("Reported on its prompt [7]");
  });

  // The link card's log, "Show what it did", writes each event as a line. A prompt's and a proposal's line says what
  // it is, as the card's newest line does, and holds none of its words: a text its card refuses is read nowhere there.
  const cp = (code: number) => String.fromCodePoint(code);
  const LEAD = "Reported a prompt in its window";
  const logLines = () => [...document.querySelectorAll("div.font-mono")].map((d) => d.textContent ?? "");
  const openLog = async (events: LinkEvent[], count: number) => {
    showing(sessions());
    serve({ "GET /api/ade/link/sessions": () => [200, sessions()], "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }] });
    render(<LinkCard l={link({ events: events.length })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show what it did/ }));
    await vi.waitFor(() => expect(logLines()).toHaveLength(count));
  };
  const step = (seq: number, words: string): LinkEvent => ({ seq, time: "2026-11-02T10:02:00Z", kind: "step", from: "mac", to: "owner", ref: null,
    text: sealedBy("L1", "step", PROMPT_CID, words), client_id: PROMPT_CID, from_id: "L1" });

  it("writes a prompt and a proposal in its link's log as the line that says what each is, with none of their words: what their cards refuse is read nowhere there", async () => {
    // three sealed prompts whose cards refuse their text whole: a mark drawn over a character, a selector at the
    // command's start, an accent after its letter, each as itself; one whose JSON writes the mark as an escape; one
    // whose text nothing refuses; one in the clear; and a proposal
    const texts = [
      JSON.stringify({ ...BASH, input: { command: `r${cp(0x0338)}m -rf build` } }),
      JSON.stringify({ ...BASH, input: { command: `${cp(0xfe0f)}rm -rf build` } }),
      JSON.stringify({ ...BASH, input: { command: `rm -rf builde${cp(0x0301)}` } }),
      JSON.stringify({ ...BASH, input: { command: `r${cp(0x0338)}m -rf build` } }).replace(/[^\x00-\x7f]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`),
      JSON.stringify(BASH),
    ];
    expect(texts[3]).toContain("r\\u0338m");
    expect(JSON.parse(texts[3]).input.command).toBe(`r${cp(0x0338)}m -rf build`);
    const prompts = texts.map((text, i) => prompt({}, { seq: 20 + i, text: sealedBy("L1", "prompt", PROMPT_CID, text) }));
    const clear = prompt({}, { seq: 25, look: true, client_id: "", text: `ExitPlan${cp(0x0338)}Mode` });
    const words = `ship i${cp(0x0338)}t to main`;
    const proposed = proposal(words, { seq: 26, from: "mac", from_id: "L1", link_id: "L1", text: sealedBy("L1", "proposal", PROPOSAL_CID, words) });
    await openLog([step(27, "ran the tests"), proposed, clear, ...prompts.slice().reverse()], 8);
    await screen.findByText(/ran the tests/);
    const lines = logLines();
    expect(lines).toEqual([
      "[27] step mac>owner: ran the tests",
      "[26] proposal mac>owner: Proposed a message for one of your sessions",
      `[25] prompt mac>owner: ${LEAD}`,
      ...[24, 23, 22, 21, 20].map((seq) => `[${seq}] prompt mac>owner: ${LEAD}`),
    ]);
    for (const line of lines) {
      for (const code of [0x0338, 0xfe0f, 0x0301]) expect(line, line).not.toContain(cp(code));
      for (const word of ["rm -rf", "u0338", "ExitPlan", "ship", "Bash"]) expect(line, line).not.toContain(word);
    }
    // their texts are not opened for the log: the step's alone is
    for (const p of [...prompts, proposed]) expect(fake.opened, String(p.seq)).not.toContain(p.text);
    expect(fake.opened).toContain(step(27, "ran the tests").text);
    noEnvelope();
  });

  it("says what a sealed prompt is in the log while the keys are locked too, and asks for no unlock on its account", async () => {
    fake.state = "locked";
    const p = prompt(BASH, { seq: 20 });
    await openLog([p], 1);
    expect(logLines()).toEqual([`[20] prompt mac>owner: ${LEAD}`]);
    expect(screen.queryByText(/Some of this is encrypted/)).toBeNull();
    expect(screen.queryByText("Encrypted")).toBeNull();
    cleanup();
    // a sealed step beside it is still Encrypted, with the one Unlock above the list
    await openLog([step(21, "ran the tests"), p], 2);
    expect(logLines()[1]).toBe(`[20] prompt mac>owner: ${LEAD}`);
    expect(screen.getAllByText("Encrypted")).toHaveLength(1);
    expect(screen.getByText(/Some of this is encrypted/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("ran the tests");
    noEnvelope();
  });
});
