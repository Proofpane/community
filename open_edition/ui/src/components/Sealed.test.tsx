import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistorySession, HistoryTalk, LinkEvent, LinkMove, LinkView } from "../api";
import { eventSlot, historySlot, talkSlot, useE2E } from "../e2e";
import { canonicalJson, textDigest } from "../passkey";
import { useStore } from "../store";
import { Channel, Conversation, conversation, LinkCard, MOVE_SEALED, MoveToWorkspace, Question, SessionConversation } from "./Channel";
import { MachineSessions } from "./History";
import { sealedEvent, useOpened } from "./Sealed";
import { Sidebar } from "./Sidebar";

// The keys behind e2e.ts are another change's. Here a stand-in holds the tab's state, and an "envelope" is the value
// and the slot it was sealed to, so it opens only with that slot, as a real one does. The page signs with a passkey
// here, so its Answer and Move are offered.
const fake = vi.hoisted(() => {
  type State = "off" | "locked" | "unlocked" | "unsupported";
  const self = {
    state: "locked" as State,
    on: true,                        // encryption is on now (e2e.ts's `on`)
    generation: 0,                   // rises at every lock and every unlock, as e2e.ts's does
    listeners: new Set<() => void>(),
    opened: [] as string[],          // each envelope the page asked to open, in order
    hold: false,                     // while set, each opening waits until its release, kept in `held`, is called
    held: [] as (() => void)[],
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
      if (fake.hold) await new Promise<void>((release) => { fake.held.push(release); });
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
const CID = "0123456789abcdef0123456789abcdef";
const CID2 = "fedcba9876543210fedcba9876543210";
const T = 1_790_000_000;
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
const cp = (code: number) => String.fromCodePoint(code);

const ev = (seq: number, over: Partial<LinkEvent> = {}): LinkEvent =>
  ({ seq, time: "2026-10-08T00:00:00Z", kind: "step", from: "mac", to: "owner", ref: null, text: "", from_id: "L1", client_id: CID, ...over });

/** An event as the server serves it once `sealer` sealed its words: the plaintext's session and time are those served
 *  unless `plain` says otherwise, and the slot is the one its served fields make, unless `slot` says otherwise. */
function sealedEv(seq: number, words: string, over: Partial<LinkEvent> = {},
                  { sealer = "L1", account = ACCOUNT, plain = {}, slot = {} }: { sealer?: string; account?: string; plain?: Record<string, unknown>; slot?: Partial<LinkEvent> } = {}): LinkEvent {
  const e = ev(seq, over);
  const s = { ...e, ...slot };
  return { ...e, text: seal({ text: words, session: e.session ?? null, said_at: e.said_at ?? null, ...plain },
                            eventSlot(account, sealer, s.kind, s.mirror, s.ref, s.client_id ?? "")) };
}

const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});

const row = (over: Partial<HistorySession> = {}): HistorySession => ({
  id: "s1", title: "a session", project: "~/work/app", branch: "main", started_at: T - 7200, last_at: T - 3600,
  link_id: "L1", link: "mac", kind: "local", listed_at: T - 60, ...over,
});

/** A History row as the server serves one that came sealed: its fields empty, and its sealed text. */
function sealedRow(id: string, fields: Record<string, unknown>, over: Partial<HistorySession> = {},
                   { sealer = "L1", sealedAs = id, withheldAs }: { sealer?: string; sealedAs?: string; withheldAs?: boolean } = {}): HistorySession {
  const withheld = !!over.withheld;
  return row({ id, title: withheld ? null : "", project: null, branch: null, ...over,
    sealed: seal(fields, historySlot(ACCOUNT, sealer, sealedAs, withheldAs ?? withheld)) });
}

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
let calls: Call[] = [];
function serve(routes: Record<string, (body: Record<string, unknown> | undefined) => unknown>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    return route ? new Response(JSON.stringify(route(body)), { status: 200 }) : new Response(JSON.stringify({ detail: "no route" }), { status: 404 });
  }));
}
const posted = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
const NONCE = { "POST /api/ade/link/nonce": (b: Record<string, unknown> | undefined) => ({ nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: ACCOUNT, server_time: T }) };
const marks = () => screen.queryAllByText("sent in the clear").length;
/** No envelope anywhere in the page's markup, its attributes too: read without the HTML sinks the app's sources never
 *  name (tests/test_ade_app_source.py reads these files as well). */
const noEnvelope = () => expect(new XMLSerializer().serializeToString(document.body)).not.toContain("e2e1.");
/** Encryption turned off, the keys kept: the state stays as it is, so old sealed text can still be opened. */
const turnedOff = () => act(() => { fake.on = false; fake.notify(); });

beforeEach(() => {
  fake.state = "locked";
  fake.on = true;
  fake.generation = 0;
  fake.opened.length = 0;
  fake.hold = false;
  fake.held.length = 0;
  fake.unlock.mockClear();
  fake.credentials.get.mockClear();
  useStore.setState({ user: { id: ACCOUNT, email: "o@example.org", can_sign_in_again: true } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

describe("a sealed text on a link's card", () => {
  it("says Encrypted with Unlock while the keys are locked, opens nothing, and shows the words once unlocked", async () => {
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests"), events: 1 })} questions={[]} onTicket={() => {}} />);
    expect(screen.getByText("Encrypted")).toBeTruthy();
    expect(document.body.textContent).not.toContain("ran the tests");
    noEnvelope();
    expect(fake.opened).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Unlock/ }));
    expect(fake.unlock).toHaveBeenCalledTimes(1);
    expect((await screen.findByText(/ran the tests/)).textContent).toBe("Step: ran the tests");
    expect(screen.queryByText("Encrypted")).toBeNull();
    noEnvelope();
  });

  it("opens only as its link sealed it for this account: each part of the slot counts", async () => {
    fake.set("unlocked");
    const right = sealedEv(5, "the right one", { kind: "done", ref: 4, mirror: undefined });
    const wrong: [string, LinkEvent][] = [
      ["another link", sealedEv(5, "x", { kind: "done", ref: 4 }, { sealer: "L2" })],
      ["another account", sealedEv(5, "x", { kind: "done", ref: 4 }, { account: "acct-2" })],
      ["another kind", sealedEv(5, "x", { kind: "done", ref: 4 }, { slot: { kind: "note" } })],
      ["another ref", sealedEv(5, "x", { kind: "done", ref: 4 }, { slot: { ref: 3 } })],
      ["another mirror", sealedEv(5, "x", { kind: "note", mirror: "owner" }, { slot: { mirror: "session" } })],
      ["another client id", sealedEv(5, "x", { kind: "done", ref: 4 }, { slot: { client_id: CID2 } })],
    ];
    render(<LinkCard l={link({ last_event: right })} questions={[]} onTicket={() => {}} />);
    expect(await screen.findByText(/the right one/)).toBeTruthy();
    wrong.push(["no envelope after its prefix", ev(5, { text: "e2e1.not*an*envelope" })]);
    for (const [what, e] of wrong) {
      cleanup();
      render(<LinkCard l={link({ last_event: e })} questions={[]} onTicket={() => {}} />);
      const said = await screen.findByText("This text could not be read");
      expect(said.getAttribute("title"), what).toMatch(/^(Its tag does not check|It is not an envelope)/);
      noEnvelope();
    }
  });

  it("says Opening while it is opened, and what stops it where the keys cannot be had", async () => {
    fake.set("unlocked");
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
    expect(screen.getByText("Opening…")).toBeTruthy();
    await screen.findByText(/ran the tests/);
    for (const [state, words] of [["off", "Encrypted: encryption is not on for this account, so this page holds no key for it"],
      ["unsupported", "Encrypted: this browser cannot open it"]] as const) {
      cleanup();
      fake.set(state);
      render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
      expect(screen.getByText(words)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /Unlock/ })).toBeNull();
      noEnvelope();
    }
  });

  it("says why Unlock did not unlock", async () => {
    fake.unlock.mockRejectedValueOnce(new Error("The passkey request was cancelled or timed out."));
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Unlock/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("The passkey request was cancelled or timed out.");
    expect(screen.getByText("Encrypted")).toBeTruthy();
  });
});

describe("locking", () => {
  it("drops every opened text: after a lock nothing opened is shown, and an unlock opens it again", async () => {
    fake.set("unlocked");
    const last = sealedEv(5, "ran the tests");
    render(<LinkCard l={link({ last_event: last })} questions={[]} onTicket={() => {}} />);
    await screen.findByText(/ran the tests/);
    act(() => fake.set("locked"));
    expect(document.body.textContent).not.toContain("ran the tests");
    expect(screen.getByText("Encrypted")).toBeTruthy();
    act(() => fake.set("unlocked"));
    await screen.findByText(/ran the tests/);
    expect(fake.opened).toEqual([last.text, last.text]);         // what was opened before was not kept
  });

  it("keeps nothing it opened past a lock: unlocked again, a text shows only once it is opened again", async () => {
    fake.set("unlocked");
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
    await screen.findByText(/ran the tests/);
    act(() => fake.set("locked"));
    fake.hold = true;                                             // the next opening does not answer
    act(() => fake.set("unlocked"));
    expect(screen.getByText("Opening…")).toBeTruthy();
    expect(document.body.textContent).not.toContain("ran the tests");
  });

  it("keeps nothing of an opening still under way when the keys lock", async () => {
    const flush = () => new Promise((r) => setTimeout(r, 0));
    fake.hold = true;
    fake.set("unlocked");
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
    expect(screen.getByText("Opening…")).toBeTruthy();
    act(() => fake.set("locked"));
    act(() => fake.set("unlocked"));                              // a second opening, which waits too
    await vi.waitFor(() => expect(fake.held).toHaveLength(2));
    await act(async () => { fake.held[0](); await flush(); });   // the first answers after the lock
    expect(screen.getByText("Opening…")).toBeTruthy();
    expect(document.body.textContent).not.toContain("ran the tests");
    await act(async () => { fake.held[1](); await flush(); });
    expect(screen.getByText(/ran the tests/)).toBeTruthy();
  });

  // a lock and an unlock in one notice: no render sees the keys locked, only the generation that rose twice
  const lockAndUnlock = () => act(() => {
    fake.state = "locked"; fake.generation += 1;
    fake.state = "unlocked"; fake.generation += 1;
    fake.notify();
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it("drops what it opened at a lock and an unlock that both fall between two renders, and opens it again", async () => {
    fake.set("unlocked");
    const last = sealedEv(5, "ran the tests");
    render(<LinkCard l={link({ last_event: last })} questions={[]} onTicket={() => {}} />);
    await screen.findByText(/ran the tests/);
    fake.hold = true;                                             // the opening again does not answer yet
    lockAndUnlock();
    expect(screen.getByText("Opening…")).toBeTruthy();
    expect(document.body.textContent).not.toContain("ran the tests");
    await vi.waitFor(() => expect(fake.opened).toEqual([last.text, last.text]));   // opened again, by the keys now held
    await act(async () => { fake.held[0](); await flush(); });
    expect(screen.getByText(/ran the tests/)).toBeTruthy();
  });

  it("gives out nothing opened under one generation of the keys under another, not for a single render", async () => {
    const last = sealedEv(5, "ran the tests");
    const slot = eventSlot(ACCOUNT, "L1", "step", undefined, null, CID);
    const seen: [number, boolean | null][] = [];
    function Probe() {
      const { generation } = useE2E();
      const { look } = useOpened([{ envelope: last.text, slot }]);
      seen.push([generation, look(last.text, slot)?.ok ?? null]);
      return null;
    }
    fake.set("unlocked");
    render(<Probe />);
    await vi.waitFor(() => expect(seen.at(-1)).toEqual([1, true]));
    fake.hold = true;
    lockAndUnlock();
    const after = seen.filter(([g]) => g === 3);
    expect(after.length).toBeGreaterThan(0);
    expect(after.every(([, ok]) => ok === null)).toBe(true);
  });

  it("lets an opening by earlier keys that answers late change nothing", async () => {
    fake.hold = true;
    fake.set("unlocked");
    render(<LinkCard l={link({ last_event: sealedEv(5, "ran the tests") })} questions={[]} onTicket={() => {}} />);
    lockAndUnlock();
    await vi.waitFor(() => expect(fake.held).toHaveLength(2));
    await act(async () => { fake.held[1](); await flush(); });   // the keys now held answer first
    expect(screen.getByText(/ran the tests/)).toBeTruthy();
    await act(async () => { fake.held[0](); await flush(); });   // then the earlier keys' opening
    expect(screen.getByText(/ran the tests/)).toBeTruthy();
  });

  it("never writes an opened text to storage", async () => {
    const stored: string[] = [];
    const real = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, k: string, v: string) { stored.push(`${k}=${v}`); real.call(this, k, v); });
    fake.set("unlocked");
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: [sealedEv(2, "the secret plan", { kind: "note", mirror: "session" })] }) });
    render(<><Conversation l={link()} onBack={() => {}} onClose={() => {}} />
      <MachineSessions sessions={[sealedRow("s1", { title: "the secret title", project: "~/work/app", branch: "main", repositories: null })]} /></>);
    await screen.findByText("the secret plan");
    await screen.findByText("the secret title");
    const kept = [...stored, ...Object.values(localStorage), ...Object.values(sessionStorage)].join("\n");
    expect(kept).not.toContain("the secret");
  });
});

describe("a question that came sealed", () => {
  it("is answered with the digest of its text as served, the envelope, once its words are shown", async () => {
    fake.set("unlocked");
    serve({ ...NONCE, "POST /api/ade/link/commands": () => ({ seq: 9, expires_at: T + 3600 }) });
    const q = sealedEv(12, "which cap?", { kind: "question" });
    render(<Question q={q} linkId="L1" />);
    expect(await screen.findByText("which cap?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "per org" } });
    fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    const payload = posted("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>;
    expect(q.text.startsWith("e2e1.")).toBe(true);
    expect(payload).toMatchObject({ purpose: "answer", ref: 12, text: "per org", digest: await textDigest(q.text) });
  });

  it("is answered with the digest of the words its session asked where the server sealed it as encryption was turned on", async () => {
    fake.set("unlocked");
    serve({ ...NONCE, "POST /api/ade/link/commands": () => ({ seq: 9, expires_at: T + 3600 }) });
    const q = sealedEv(12, "which cap?", { kind: "question", sealed_in_place: true });
    render(<Question q={q} linkId="L1" />);
    expect(await screen.findByText("which cap?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "per org" } });
    fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    const payload = posted("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>;
    // its session asked it in the clear and keeps the digest of those words: the sealed text's would never verify
    expect(payload).toMatchObject({ purpose: "answer", ref: 12, text: "per org", digest: await textDigest("which cap?") });
    expect(payload.digest).not.toBe(await textDigest(q.text));
  });

  it("is not offered for an answer while the keys are locked, nor when its words break the rules for a question", async () => {
    render(<Question q={sealedEv(12, "which cap?", { kind: "question" })} linkId="L1" />);
    expect(screen.getByText("Encrypted")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Answer/ })).toBeNull();
    cleanup();
    fake.set("unlocked");
    for (const [words, said] of [[`which${cp(0x200b)} cap?`, "This text holds characters the ADE does not show"],
      ["which\rcap?", "This text holds characters the ADE does not show"],
      ["   ", "This text is not shown: it is empty"],
      ["cafe\u{0301}?", "This text is not shown: it is not in Unicode normal form C"],
      ["x".repeat(4001), "This text is not shown: it is longer than 4,000 characters"],
      ["use sk-ant-123", "This text is not shown: it holds something shaped like an Anthropic key (sk-ant-)"]] as const) {
      render(<Question q={sealedEv(12, words, { kind: "question" })} linkId="L1" />);
      expect(await screen.findByText(said), words.slice(0, 10)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /Answer/ })).toBeNull();
      cleanup();
    }
  });

  it("of a link no longer linked opens as its asker sealed it", async () => {
    fake.set("unlocked");
    const q = { ...sealedEv(12, "which tag?", { kind: "question", from_id: undefined }, { sealer: "L9" }), link_id: "L9" };
    serve({
      "GET /api/ade/link/sessions": () => ({ links: [], questions: [q], public_url: "https://pp.example" }),
      "GET /api/ade/link/history": () => ({ sessions: [] }),
    });
    render(<Channel onClose={() => {}} />);
    expect(await screen.findByText("which tag?")).toBeTruthy();
    expect(screen.getByText("Questions from sessions no longer linked")).toBeTruthy();
  });
});

describe("the paths encryption seals", () => {
  // the expected outcomes are those of the server's link.fixed_text on the same values
  it("take an event to no one named as to the owner, and leave in the clear only the two texts the server takes so", () => {
    expect(sealedEvent(ev(1, { to: "" }))).toBe(true);
    expect(sealedEvent(ev(1, { to: undefined as unknown as string }))).toBe(true);
    expect(sealedEvent(ev(1, { to: "all" }))).toBe(false);
    expect(sealedEvent(ev(1, { kind: "waiting", text: "working on it" }))).toBe(false);
    expect(sealedEvent(ev(1, { kind: "step", text: "working on it" }))).toBe(true);
    expect(sealedEvent(ev(1, { kind: "note", text: "working on it" }))).toBe(true);
    const at = 1790000000;
    const iso = (t: number) => new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
    expect(iso(at)).toBe("2026-09-21T14:13:20Z");
    expect(sealedEvent(ev(1, { kind: "note", client_id: `stopped:${at}`, text: `stopped at ${iso(at)}` }))).toBe(false);
    expect(sealedEvent(ev(1, { kind: "note", client_id: `stopped:${at}`, text: `stopped at ${iso(at + 1)}` }))).toBe(true);
    expect(sealedEvent(ev(1, { kind: "note", client_id: `stopped:${at}`, text: "stopped at an unknown time" }))).toBe(true);
    expect(sealedEvent(ev(1, { kind: "step", client_id: `stopped:${at}`, text: `stopped at ${iso(at)}` }))).toBe(true);
    expect(sealedEvent(ev(1, { kind: "note", client_id: "stopped:999999999999", text: "stopped at an unknown time" }))).toBe(true);
    // past the year 9999 no time is a stop's, whatever its text says
    expect(iso(253402300800)).toBe("+010000-01-01T00:00:00Z");
    expect(sealedEvent(ev(1, { kind: "note", client_id: "stopped:253402300800", text: "stopped at +010000-01-01T00:00:00Z" }))).toBe(true);
    expect(sealedEvent(ev(1, { kind: "note", client_id: "stopped:253402300799", text: "stopped at 9999-12-31T23:59:59Z" }))).toBe(false);
  });
});

describe("a link's timeline", () => {
  const stopNote = (at: number) => ev(6, { kind: "note", client_id: `stopped:${at}`, text: `stopped at ${new Date(at * 1000).toISOString().replace(/\.\d{3}Z$/, "Z")}` });
  const events = () => [
    sealedEv(11, `a tag${cp(0xe0041)} inside`, { client_id: CID2 }),
    sealedEv(10, "a sealed step"),
    ev(9, { kind: "step", text: "a clear step" }),
    ev(8, { kind: "note", to: "all", text: "to everyone" }),
    ev(7, { kind: "waiting", ref: 3, text: "working on it" }),
    stopNote(T),
    ev(5, { from: "owner", from_id: "owner", to: "mac", kind: "instruction", text: "ship it", state: "taken" }),
    ev(4, { kind: "note", to: "mac", text: "to its own name" }),
    ev(3, { kind: "question", to: "cloud", text: "to another session" }),
    // the owner's own words are never on a path encryption seals (the note a Close posts, its asker's link gone)
    ev(2, { from: "owner", from_id: "owner", to: "owner", kind: "note", ref: 12, text: "closed on the page", client_id: "close:12" }),
  ];

  it("opens each line, and marks those that came in the clear on a path encryption seals while it is on", async () => {
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: events() }) });
    render(<LinkCard l={link({ events: 8 })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show what it did/ }));
    await screen.findByText(/a clear step/);
    expect(screen.getByText(/Some of this is encrypted/)).toBeTruthy();
    expect(screen.getAllByText("Encrypted")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /Unlock/ })).toHaveLength(1);    // once, above the list
    expect(marks()).toBe(2);
    for (const words of ["a clear step", "to its own name"]) {
      expect(within(screen.getByText(new RegExp(words)) as HTMLElement).queryByText("sent in the clear")).toBeTruthy();
    }
    noEnvelope();
    fireEvent.click(screen.getAllByRole("button", { name: /Unlock/ })[0]);
    expect((await screen.findByText(/a sealed step/)).textContent).toBe("[10] step mac>owner: a sealed step");
    expect(screen.getByText(/a tag/).textContent).toBe("[11] step mac>owner: a tag[U+E0041] inside");   // visible(), once opened
    expect(screen.queryByText(/Some of this is encrypted/)).toBeNull();
    expect(marks()).toBe(2);
    turnedOff();                                                  // the keys kept: what was sealed still opens
    expect(marks()).toBe(0);
    expect(screen.getByText(/a sealed step/)).toBeTruthy();
  });

  it("marks nothing while encryption is off, though the keys kept are locked", async () => {
    fake.on = false;
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: events() }) });
    render(<LinkCard l={link({ events: 9 })} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show what it did/ }));
    await screen.findByText(/a clear step/);
    expect(marks()).toBe(0);
    expect(screen.getAllByText("Encrypted")).toHaveLength(2);        // old sealed text, still to be unlocked
    expect(screen.getAllByRole("button", { name: /Unlock/ })).toHaveLength(1);
  });
});

describe("a linked session's conversation", () => {
  const timeline = () => [
    sealedEv(1, "(1 of 2) abc", { kind: "note", mirror: "session" }),
    sealedEv(2, "(2 of 2) def", { kind: "note", mirror: "session", client_id: CID2 }),
    ev(3, { from: "owner", from_id: "owner", to: "mac", kind: "instruction", text: "ship it", state: "taken", client_id: undefined }),
  ];

  it("joins a message's numbered parts once they are opened, and marks the owner's commands as kept in the clear", async () => {
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: timeline().reverse() }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("ship it");
    expect(screen.getAllByText("Encrypted")).toHaveLength(2);           // each part apart, nothing of it joined
    expect(screen.getByText(/Some of this is encrypted/)).toBeTruthy();
    expect(screen.getByText("kept in the clear")).toBeTruthy();
    expect(marks()).toBe(0);
    noEnvelope();
    fireEvent.click(screen.getAllByRole("button", { name: /Unlock/ })[0]);
    expect(await screen.findByText("abcdef")).toBeTruthy();
    expect(screen.queryByText("Encrypted")).toBeNull();
    turnedOff();
    expect(screen.queryByText("kept in the clear")).toBeNull();
    expect(screen.getByText("abcdef")).toBeTruthy();                     // still unlocked, still open
  });

  // inside the conversation's scroller the bar sits at its top, and unlocking there would leave the reader at the top
  it("keeps the unlock bar in view, outside the conversation's scroller", async () => {
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: timeline().reverse() }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("ship it")).closest(".overflow-y-auto") as HTMLDivElement;
    expect(box.getAttribute("aria-label")).toBe("The conversation");
    expect(box.contains(screen.getByText(/Some of this is encrypted/))).toBe(false);
  });

  it("takes the conversation to its newest line once unlocking opens it, wherever the reader was", async () => {
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: timeline().reverse() }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("ship it")).closest(".overflow-y-auto") as HTMLDivElement;
    let height = 1000;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => height });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 0;                                       // at the top, where the bar was
    fireEvent.scroll(box);
    fireEvent.click(screen.getAllByRole("button", { name: /Unlock/ })[0]);
    height = 1400;      // the lines, opened, take more room: after the click, so a jump at the click alone falls short
    await screen.findByText("abcdef");
    await vi.waitFor(() => expect(box.scrollTop).toBe(1400));
  });

  it.each(["off", "unsupported"] as const)("leaves the reader where they are when the keys go from locked to %s, never unlocked", async (to) => {
    // a key read that finds none, or a browser that cannot, unlocks nothing
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: timeline().reverse() }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("ship it")).closest(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 1000 });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 0;
    fireEvent.scroll(box);
    act(() => fake.set(to));
    await new Promise((r) => setTimeout(r, 50));
    expect(box.scrollTop).toBe(0);
  });

  it("leaves the reader where they are when its sealed lines leave the newest read and the keys stay locked", async () => {
    // only the keys' unlocking takes the conversation to its newest line
    let events = timeline().reverse();
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("ship it")).closest(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 1000 });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 0;
    fireEvent.scroll(box);
    events = [ev(9, { from: "owner", from_id: "owner", to: "mac", kind: "instruction", text: "and the docs", state: "taken", client_id: undefined })];
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("and the docs");
    await new Promise((r) => setTimeout(r, 50));
    expect(box.scrollTop).toBe(0);
  });

  it("joins only parts that are shown: a part that cannot be read stays a message of its own", () => {
    const parts = [ev(1, { kind: "note", mirror: "session", text: "(1 of 2) abc" }), ev(2, { kind: "note", mirror: "session", text: "(2 of 2) def" })];
    const got = conversation(parts, "mac", undefined, {
      open: (e) => (e.seq === 1 ? { ok: false, seal: { at: "refused", refusal: { why: "unreadable" } } } : { ok: true, value: String(e.text), inClear: false }),
    });
    expect(got.map((s) => [s.seq, s.text, s.seal?.at ?? null])).toEqual([[1, "", "refused"], [2, "def", null]]);
    const clear = conversation(parts, "mac", undefined, { open: (e) => ({ ok: true, value: String(e.text), inClear: e.seq === 2 }) });
    expect(clear.map((s) => [s.text, s.clear])).toEqual([["abcdef", "sent"]]);       // one part in the clear marks the message
  });

  it("refuses a mirrored note whose session or time inside is not what the server serves", async () => {
    fake.set("unlocked");
    const said = T * 1000;
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => ({ events: [
      sealedEv(4, "named another session", { kind: "note", mirror: "owner", session: "s1", said_at: said }, { plain: { session: "s2" } }),
      sealedEv(3, "said at another time", { kind: "note", mirror: "owner", session: "s1", said_at: said }, { plain: { said_at: said + 1 } }),
      sealedEv(2, "a session not tagged", { kind: "note", mirror: "owner", said_at: said, client_id: CID2 }, { plain: { session: "s9" } }),
      sealedEv(1, "a step naming a session", { kind: "step", client_id: CID2 }, { plain: { session: "s9" } }),
    ] }) });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    expect(await screen.findByText("a session not tagged")).toBeTruthy();
    expect(screen.getAllByText("This text could not be read")).toHaveLength(2);    // the step is in the log, not here
    expect(document.body.textContent).not.toContain("named another session");
    expect(document.body.textContent).not.toContain("said at another time");
  });
});

describe("a machine's sessions", () => {
  const rows = () => [
    sealedRow("s1", { title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: ["~/work/app", "~/work/lib"] }),
    sealedRow("s2", { project: "~/secret" }, { withheld: true }),
    row({ id: "s3", title: "in the clear", project: "~/work/app", branch: "dev" }),
  ];

  it("are listed under Encrypted until unlocked, then by repository, a row in the clear marked while encryption is on", async () => {
    // a withheld row in the clear with no folder says nothing in the clear: nothing to mark
    render(<MachineSessions sessions={[...rows(), row({ id: "s4", title: null, project: null, branch: null, withheld: true })]} />);
    const sealed = screen.getByText("Encrypted", { selector: "div > span" }).parentElement as HTMLElement;
    expect(within(sealed).getByRole("button", { name: /Unlock/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Unlock/ })).toHaveLength(1);    // once, for the group
    expect(screen.getAllByText("Encrypted")).toHaveLength(3);          // the group, and each row's title
    expect(marks()).toBe(1);
    noEnvelope();
    fireEvent.click(within(sealed).getByRole("button", { name: /Unlock/ }));
    expect(await screen.findByText("Fix the sign-in")).toBeTruthy();
    const groups = screen.getAllByTitle("The repository its sessions worked in first").map((g) => g.textContent?.trim());
    expect(groups).toEqual(["~/work/app", "~/secret", "No folder"]);
    expect(screen.queryByText("Encrypted")).toBeNull();
    expect(screen.getByText("also in ~/work/lib")).toBeTruthy();
    expect(screen.getAllByText("Title kept on mac's machine")).toHaveLength(2);    // the withheld rows, sealed and not
    expect(screen.getAllByText("~/secret")).toHaveLength(2);            // its group's name, and its folder in its row
    expect(screen.getByText("~/work/app · main")).toBeTruthy();         // its folder and branch, opened
    turnedOff();
    expect(marks()).toBe(0);
    expect(screen.getByText("Fix the sign-in")).toBeTruthy();
  });

  it("show a row's fields only as the rules allow, and never the server's own beside a sealed text", async () => {
    fake.set("unlocked");
    render(<MachineSessions sessions={[
      sealedRow("s1", { title: `a${cp(0x00a0)}b`, project: "~/work/app", branch: null, repositories: null }),
      sealedRow("s2", { title: "Sealed for another session", project: null, branch: null, repositories: null }, {}, { sealedAs: "s9" }),
      sealedRow("s3", { title: "x".repeat(121), project: null, branch: null, repositories: null }),
      sealedRow("s4", { title: "the sealed title", project: null, branch: null, repositories: null }, { title: "the server's own words", project: "~/server" }),
      sealedRow("s5", { project: "~/withheld" }, { withheld: true }, { withheldAs: false }),
      row({ id: "s6", title: "", project: null, sealed: "not an envelope" }),
      sealedRow("s7", { title: "t", project: null, branch: null, repositories: null },
        { project: "~/server-project", branch: "server-branch", repositories: ["~/server-repo"] }, { sealedAs: "s9" }),
    ]} />);
    expect(await screen.findByText("the sealed title")).toBeTruthy();
    expect(screen.getByText("This text holds characters the ADE does not show")).toBeTruthy();
    expect(screen.getAllByText("This text could not be read")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /Unlock/ })).toBeNull();      // unlocked: what is under Encrypted cannot be read
    for (const served of ["~/server-project", "server-branch", "~/server-repo"]) expect(document.body.textContent).not.toContain(served);
    expect(screen.getByText("This text is not shown: it is longer than 120 characters")).toBeTruthy();
    expect(document.body.textContent).not.toContain("the server's own words");
    expect(document.body.textContent).not.toContain("~/server");
    expect(document.body.textContent).not.toContain("~/withheld");
  });

  it("give whatever reads one the row as served, never what it opened to", async () => {
    fake.set("unlocked");
    const onRead = vi.fn();
    const s = sealedRow("s1", { title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: null }, { notes: 2 });
    render(<MachineSessions sessions={[s]} onRead={onRead} />);
    fireEvent.click(await screen.findByText("Fix the sign-in"));
    fireEvent.click(screen.getByText("Read the conversation"));
    expect(onRead).toHaveBeenCalledWith(s);
    expect(onRead.mock.calls[0][0]).toMatchObject({ title: "", sealed: s.sealed });
  });
});

describe("the list of sessions", () => {
  const list = (machine: HistorySession[], onMachine = vi.fn()) =>
    render(<Sidebar repos={["app"]} sessions={[{ id: "w1", title: "a workspace session", repo: "app", agent: "claude", state: "done" }]}
      selected={null} onSelect={vi.fn()} onNew={vi.fn()} onSettings={vi.fn()} machine={machine} onMachine={onMachine} />);

  it("lists a machine's sealed sessions under Encrypted until unlocked, then under their repository, and opens the row as served", async () => {
    const onMachine = vi.fn();
    const s = sealedRow("m1", { title: "on the Mac", project: "~/work/app", branch: "main", repositories: ["~/work/app"] }, { client: "vscode" });
    list([s, row({ id: "m2", title: "in the clear", project: "~/work/app" })], onMachine);
    const sealed = screen.getByLabelText("Encrypted sessions");
    expect(within(sealed).getAllByText("Encrypted")).toHaveLength(2);
    expect(screen.getAllByText("Encrypted")).toHaveLength(2);          // there alone, in no repository's group
    expect(screen.getAllByRole("button", { name: /Unlock/ })).toHaveLength(1);
    expect(marks()).toBe(1);
    noEnvelope();
    fireEvent.click(within(sealed).getAllByText("Encrypted")[1]);      // a sealed row opens as served while locked too
    expect(onMachine).toHaveBeenLastCalledWith(s);
    fireEvent.click(within(sealed).getByRole("button", { name: /Unlock/ }));
    const opened = await screen.findByText("on the Mac");
    expect(screen.queryByLabelText("Encrypted sessions")).toBeNull();
    const app = screen.getByText("app").closest("div.mb-1\\.5") as HTMLElement;
    expect(within(app).getByText("on the Mac")).toBeTruthy();
    expect(within(app).getByText("mac · VS Code · main")).toBeTruthy();
    fireEvent.click(opened);
    expect(onMachine).toHaveBeenCalledTimes(2);
    expect(onMachine).toHaveBeenLastCalledWith(s);
  });
});

describe("a listed session's conversation", () => {
  const s = sealedRow("s1", { title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: null }, { talk_at: T, notes: 1, client: "terminal" });
  const talk = (): HistoryTalk => {
    const messages: HistoryTalk["messages"] = [
      { who: "owner", text: "", at: T - 300, ms: (T - 300) * 1000 },
      { who: "session", text: "", at: T - 250 },
      { who: "session", text: "", at: T - 200 },
      { who: "session", text: "a reply in the clear", at: T - 100 },
    ];
    const t: HistoryTalk = { earlier: 3, updated_at: T, messages };
    messages[0].text = seal({ text: "how is the build?" }, talkSlot(ACCOUNT, "L1", "s1", 0, 4, 3, "owner", T - 300, (T - 300) * 1000));
    messages[1].text = seal({ text: "Green: 12 passed." }, talkSlot(ACCOUNT, "L1", "s1", 1, 4, 3, "session", T - 250, undefined));
    messages[2].text = seal({ text: "sealed for a list of three" }, talkSlot(ACCOUNT, "L1", "s1", 2, 3, 3, "session", T - 200, undefined));
    return t;
  };

  it("opens what its machine sent and its mirrored notes, each by its own slot, and its title", async () => {
    serve({
      "GET /api/ade/link/history/L1/s1": () => talk(),
      "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => ({ events: [sealedEv(20, "Mirrored since.", { kind: "note", mirror: "session", session: "s1", said_at: T * 1000 })] }),
    });
    render(<SessionConversation s={s} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("a reply in the clear");
    expect(screen.getAllByText("Encrypted").length).toBeGreaterThanOrEqual(3);    // its title, a message, a note
    noEnvelope();
    fireEvent.click(screen.getAllByRole("button", { name: /Unlock/ })[0]);
    expect(await screen.findByText("how is the build?")).toBeTruthy();
    expect(screen.getByText("Green: 12 passed.")).toBeTruthy();
    expect(await screen.findByText("Mirrored since.")).toBeTruthy();
    expect(screen.getByText("Fix the sign-in")).toBeTruthy();
    expect(screen.getByText("This text could not be read")).toBeTruthy();       // sealed for a list of another length
    expect(document.body.textContent).not.toContain("sealed for a list of three");
    const clear = screen.getByText("a reply in the clear").closest("div.mb-3") as HTMLElement;
    expect(within(clear).getByText("sent in the clear")).toBeTruthy();
    expect(marks()).toBe(1);
  });

  const routes = () => ({
    "GET /api/ade/link/history/L1/s1": () => talk(),
    "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => ({ events: [sealedEv(20, "Mirrored since.", { kind: "note", mirror: "session", session: "s1", said_at: T * 1000 })] }),
  });

  it("keeps the unlock bar in view, outside its scroller, as the link's own conversation does", async () => {
    serve(routes());
    render(<SessionConversation s={s} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("a reply in the clear")).closest(".overflow-y-auto") as HTMLDivElement;
    expect(box.getAttribute("aria-label")).toBe("The conversation");
    expect(box.contains(screen.getByText(/Some of this is encrypted/))).toBe(false);
  });

  it("takes its conversation to the newest line once unlocking opens it, wherever the reader was", async () => {
    serve(routes());
    render(<SessionConversation s={s} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("a reply in the clear")).closest(".overflow-y-auto") as HTMLDivElement;
    let height = 1000;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => height });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 0;
    fireEvent.scroll(box);
    fireEvent.click(screen.getAllByRole("button", { name: /Unlock/ })[0]);
    height = 1400;
    await screen.findByText("Mirrored since.");
    await vi.waitFor(() => expect(box.scrollTop).toBe(1400));
  });
});

describe("moving a session that came sealed", () => {
  const s = sealedRow("s1", { title: "Fix the sign-in", project: "~/work/app", branch: "main", repositories: null });
  const routes = (more = {}) => ({
    ...NONCE,
    "GET /api/ade/link/moves?session=s1": () => ({ moves: [] as LinkMove[] }),    // by any link of its machine (Api.sessionMoves)
    "POST /api/ade/link/commands": () => ({ held: { id: "H1", takes_effect_at: T + 180, told: 1 }, expires_at: T + 3600 }),
    ...more,
  });
  const workspace = () => useStore.setState({ ws: { enabled: true, allowed: true, running: true } as never });

  it("waits for Unlock, says the session goes to the workspace in the clear, and sends its opened title beside what is signed", async () => {
    serve(routes());
    workspace();
    render(<MoveToWorkspace s={s} onOpen={() => {}} />);
    expect(await screen.findByText(/Its title is encrypted: unlock to move it/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Move to the workspace/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Unlock/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.getByText(MOVE_SEALED)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    expect(await screen.findByText("Its title, sent with the move in the clear to name it there: Fix the sign-in")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    const body = posted("/api/ade/link/commands")[0].body as Record<string, unknown>;
    expect(body.title).toBe("Fix the sign-in");
    expect(Object.keys(body).sort()).toEqual(["credential", "payload", "title"]);
    const payload = body.payload as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(["account", "created_at", "deployment", "expires_at", "id", "nonce", "purpose", "session", "to", "v"]);
    expect(canonicalJson(payload)).not.toContain("Fix the sign-in");
  });

  it("parks its opened title beside the move for Sign on my Mac, and sends it beside the signature", async () => {
    let parked: Record<string, unknown> | undefined;
    const credential = { id: "AQ", type: "public-key", response: { clientDataJSON: "x", authenticatorData: "y", signature: "z" } };
    serve(routes({
      "GET /api/ade/signers": () => ({ signers: [{ id: "g1", name: "MacBook", created_at: T, last_seen_at: null }] }),
      "POST /api/ade/sign-requests": (b: Record<string, unknown> | undefined) => {
        parked = b;
        return { id: "r1", code: "x", expires_at: Math.floor(Date.now() / 1000) + 290 };
      },
      "GET /api/ade/sign-requests/r1": () => ({ id: "r1", state: "signed", expires_at: T + 300, credential, payload: canonicalJson(parked?.payload) }),
    }));
    workspace();
    fake.set("unlocked");
    render(<MoveToWorkspace s={s} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign on my Mac/ }));
    await vi.waitFor(() => expect(posted("/api/ade/sign-requests")).toHaveLength(1));
    expect(parked?.title).toBe("Fix the sign-in");                       // the ADE's word on the Mac, never signed
    expect(canonicalJson(parked?.payload)).not.toContain("Fix the sign-in");
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1), { timeout: 4000 });
    expect(posted("/api/ade/link/commands")[0].body).toEqual({ payload: parked?.payload, credential, title: "Fix the sign-in" });
  });

  it("stops a move under way where the keys lock, and sends no title for a session in the clear", async () => {
    serve(routes());
    workspace();
    fake.set("unlocked");
    render(<MoveToWorkspace s={s} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    await screen.findByText(/Its title, sent with the move/);
    act(() => fake.set("locked"));
    expect(screen.queryByText(/Its title, sent with the move/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Sign with your passkey/ })).toBeNull();
    expect(screen.getByText(/Its title is encrypted: unlock to move it/)).toBeTruthy();
    cleanup();
    serve(routes());
    render(<MoveToWorkspace s={row({ title: "Fix the sign-in" })} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.queryByText(MOVE_SEALED)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    expect("title" in (posted("/api/ade/link/commands")[0].body as object)).toBe(false);
  });

  it("goes without a title where its title cannot be opened, and says so", async () => {
    serve(routes());
    workspace();
    fake.set("unlocked");
    render(<MoveToWorkspace s={sealedRow("s1", { title: "x", project: null, branch: null, repositories: null }, {}, { sealedAs: "s9" })} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    expect(await screen.findByText("Its title could not be opened here, so none goes with it: the workspace names it (untitled).")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(posted("/api/ade/link/commands")).toHaveLength(1));
    expect("title" in (posted("/api/ade/link/commands")[0].body as object)).toBe(false);
  });
});
