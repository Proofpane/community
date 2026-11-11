import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LinkEvent, LinkSessions, LinkView } from "../api";
import * as C from "../e2eCrypto";
import { eventSlot, useE2EStore } from "../e2e";
import * as K from "../e2eKeys";
import { canonicalJson, textDigest } from "../passkey";
import { useStore } from "../store";
import { Approvals } from "./Approvals";
import { forgetSigners } from "./SignOnMac";
import { SignersSection } from "./Signers";

// One press on Approve, one passkey
// touch, both unlocks and signs; the decrypted words show for a few seconds with Undo before the signed answer is sent;
// and the tab stays unlocked, so the next approvals take one press too.
//
// As in OwnerInbox.test.tsx, a stand-in holds the tab's state and an "envelope" is the value and the slot it was sealed
// to. Its passkey gives PRF outputs where they are asked, and the store's keys are a view of one enrolled credential.
const fake = vi.hoisted(() => {
  type State = "off" | "locked" | "unlocked" | "unsupported";
  const self = {
    state: "unlocked" as State,
    on: true,
    generation: 1,
    listeners: new Set<() => void>(),
    opened: [] as string[],
    asked: [] as CredentialRequestOptions[],
    prf: true,                                       // the passkey gives PRF outputs where asked
    getDelay: 0,                                     // milliseconds the passkey's prompt is up, where a case asks it slow
    answerId: "AQ",                                  // the credential the browser answers with
    openHangs: false,                                // the opening of a sealed text never ends, where a case asks it
    openDelay: 0,                                    // milliseconds an opening takes, where a case asks it slow
    notify() { for (const f of self.listeners) f(); },
    set(next: State) {
      if (next === "locked" || next === "unlocked") self.generation += 1;
      self.state = next;
      self.notify();
    },
    unlock: vi.fn(async () => { self.set("unlocked"); }),
    unlockDelay: 0,                                  // milliseconds the unlock takes, where a case asks it slow
    unlockFrom: vi.fn(async (_t: unknown) => {
      if (self.unlockDelay) await new Promise((r) => setTimeout(r, self.unlockDelay));
      self.set("unlocked");
    }),
    credentials: { get: vi.fn(async (o: CredentialRequestOptions) => {
      self.asked.push(o);
      if (self.getDelay) await new Promise((r) => setTimeout(r, self.getDelay));
      const byCredential = (o.publicKey?.extensions as { prf?: { evalByCredential?: Record<string, unknown> } } | undefined)?.prf?.evalByCredential;
      const results = byCredential && self.prf ? { prf: { results: { first: new Uint8Array(32).fill(7).buffer, second: new Uint8Array(32).fill(9).buffer } } } : {};
      return { id: self.answerId, type: "public-key", response: {
        clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer,
        signature: new Uint8Array([3]).buffer, userHandle: null }, getClientExtensionResults: () => results };
    }) },
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
      return { state, on, generation, unlock: fake.unlock, unlockFrom: fake.unlockFrom, unlockWithCode: async () => {}, lock: () => fake.set("locked") };
    },
    openSealed: async (envelope: string, slot: string) => {
      fake.opened.push(envelope);
      if (fake.openHangs) return new Promise(() => {});
      if (fake.openDelay) await new Promise((r) => setTimeout(r, fake.openDelay));
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
const seal = (value: unknown, slot: string) => `e2e1.${Buffer.from(JSON.stringify({ slot, value }), "utf8").toString("base64url")}`;
const sealedBy = (linkId: string, kind: string, clientId: string, words: string) =>
  seal({ text: words, session: null, said_at: null }, eventSlot(ACCOUNT, linkId, kind, undefined, null, clientId));
const BASH = { tool: "Bash", input: { command: "rm -rf build", description: "Clean the build" }, session_id: "cc-1", agent_id: null, deadline: T + 1800 };
const ASKED = {
  tool: "AskUserQuestion",
  input: { questions: [{ question: "Which cap?", header: "Cap", multiSelect: false, options: [{ label: "Per user" }, { label: "Per org" }] }] },
  questions: [{ question: "Which cap?", header: "Cap", multiSelect: false, options: [{ n: 1, label: "Per user" }, { n: 2, label: "Per org" }] }],
  session_id: "cc-1", agent_id: null, deadline: T + 1800,
};
/** The same prompt's words as a server serving another envelope for it would have them. */
const BASH2 = { ...BASH, input: { command: "rm -rf /", description: "Clean everything" } };
/** A question whose options nothing numbers, neither its item nor its input: answered in the session's window. */
const ASKED_LOOSE = { tool: "AskUserQuestion", input: { prompt: "Which cap: per user or per org?" }, session_id: "cc-1", agent_id: null, deadline: T + 1800 };
type Prompt = LinkEvent & { link_id: string; deadline_at: number; look: boolean };
const prompt = (item: unknown, over: Partial<Prompt> = {}): Prompt => ({
  seq: 7, time: "2026-11-02T10:00:00Z", kind: "prompt", from: "mac", to: "owner", ref: null,
  text: sealedBy("L1", "prompt", PROMPT_CID, JSON.stringify(item)), client_id: PROMPT_CID, from_id: "L1",
  link_id: "L1", deadline_at: T + 1800, look: false, ...over,
});
const link = (over: Partial<LinkView> = {}): LinkView => ({
  id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
  last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
  events: 0, last_event: null, commands: [], stopped_at: null, ...over,
});
const sessions = (over: Record<string, unknown> = {}) => ({
  links: [link()], questions: [], public_url: "https://pp.example", prompts: [], proposals: [], ...over,
}) as unknown as LinkSessions;

type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
let calls: Call[] = [];
let signers: string[] = [];
/** the nonces given, in order; how many requests for one fail first; whether one stalls; whether a Mac signer is paired */
let issued: string[] = [];
let nonceFails = 0;
let stall = false;
let paired = false;
let signersStall = false;
/** the next nonce request's answer held until `release` is called, where a case asks it */
let holdNext = false;
let release: (() => void) | null = null;
/** whether a sent answer's request never comes back (the send in flight) */
let commandsHold = false;
/** whether the digests of texts (not of steps' challenges) wait until `textHeld`'s calls release them */
let holdText = false;
let textHeld: (() => void)[] = [];
/** the digests of each step's challenge the page starts (prepareSigned's last await, which ends off the main thread) */
let steps: Promise<ArrayBuffer>[] = [];
const STEP = new TextEncoder().encode("proofpane-step/1");
function serve(more: Record<string, (body: Record<string, unknown> | undefined) => [number, unknown]> = {}) {
  const routes: Record<string, (body: Record<string, unknown> | undefined) => [number, unknown]> = {
    "POST /api/ade/link/nonce": (b) => {
      if (nonceFails > 0) { nonceFails -= 1; return [500, { detail: "The server is busy for a moment." }]; }
      const nonce = `n-${String(b?.purpose)}-${calls.length}`;
      issued.push(nonce);
      return [200, { nonce, rp_id: "pp.example", deployment: "pp.example", account: ACCOUNT, server_time: T, credentials: signers }];
    },
    "GET /api/ade/signers": () => [200, { signers: paired ? [{ id: "s1", name: "Mac" }] : [] }],
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
    ...more,
  };
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    if (stall && url === "/api/ade/link/nonce") return new Promise<Response>(() => {});
    if (signersStall && url === "/api/ade/signers") return new Promise<Response>(() => {});
    if (commandsHold && url === "/api/ade/link/commands") return new Promise<Response>(() => {});
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: "no route" }];
    if (holdNext && url === "/api/ade/link/nonce") {
      holdNext = false;
      return new Promise<Response>((r) => { release = () => r(new Response(JSON.stringify(data), { status })); });
    }
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
const signed = () => sent("/api/ade/link/commands").map((c) => c.body?.payload as Record<string, unknown>);
const approvals = () => render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} />);
const showing = (links: LinkSessions) => {
  useStore.setState({ user: { id: ACCOUNT, email: "o@example.org", can_sign_in_again: true }, links });
  serve({ "GET /api/ade/link/sessions": () => [200, links] });
};
const bytes = (v: unknown) => (v instanceof ArrayBuffer ? new Uint8Array(v) : new Uint8Array((v as ArrayBufferView).buffer, (v as ArrayBufferView).byteOffset, (v as ArrayBufferView).byteLength));
/** The challenge the server checks an answer by (passkeys.step_challenge), written here from its words. */
const stepOf = (payload: unknown) => C.sha256(C.concat(C.utf8("proofpane-step/1"), new Uint8Array([0]), C.utf8(canonicalJson(payload))));
/** The fake clock moved a second at a time, effects running between, `seconds` in all or until the answer is sent. */
async function stepped(seconds: number, untilSent = false) {
  for (let i = 0; i < seconds && !(untilSent && sent("/api/ade/link/commands").length); i++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  }
}
/** The store's keys: one enrolled credential, AQ, its salt hashed ahead as a read of the keys does. */
const SALT = new Uint8Array(32).fill(5);
async function keysView(kind?: string) {
  const view = { on: true, pq: false, credentials: [{ passkey_id: "pk-1", credential_id: "AQ", salt: C.b64u(SALT), public_key: "", wrapped: "" }],
    passkeys: [{ passkey_id: "pk-1", name: "iPhone", ...(kind ? { kind } : {}) }] } as unknown as K.E2EView;
  await K.primeUnlock(view);
  useE2EStore.setState({ view });
}
/** Waits until a preparation begun after `before` steps' challenges has made its own, and what follows it has run. */
async function prepared(before: number) {
  await vi.waitFor(() => expect(steps.length).toBeGreaterThan(before));
  await act(async () => { await Promise.all(steps); });
  await act(async () => {});
  await act(async () => {});
}
/** The Approve button once its answer is prepared: the nonce asked for, the button no longer waiting. Each wait takes
 *  `wait` ms where given, else vi.waitFor's own 1,000. */
async function preparedApprove(wait?: number) {
  const approve = await screen.findByRole("button", { name: "Approve" });
  await vi.waitFor(() => expect(sent("/api/ade/link/nonce").length).toBeGreaterThan(0), { timeout: wait });
  await vi.waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false), { timeout: wait });
  return approve;
}

beforeEach(() => {
  fake.state = "unlocked";
  fake.on = true;
  fake.generation += 1;
  fake.opened.length = 0;
  fake.asked.length = 0;
  fake.prf = true;
  fake.credentials.get.mockClear();
  fake.unlockFrom.mockClear();
  signers = [];
  issued = [];
  nonceFails = 0;
  stall = false;
  paired = false;
  signersStall = false;
  holdNext = false;
  release = null;
  forgetSigners();                                   // the page's one read of the signers, read again in each case
  commandsHold = false;
  holdText = false;
  textHeld = [];
  steps = [];
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  vi.spyOn(crypto.subtle, "digest").mockImplementation((algorithm: AlgorithmIdentifier, data: BufferSource) => {
    const b = data instanceof Uint8Array ? data : new Uint8Array(data as ArrayBuffer);
    const step = b.length >= STEP.length && STEP.every((x, i) => b[i] === x);
    if (holdText && !step) return new Promise<ArrayBuffer>((r) => { textHeld.push(() => { void digest(algorithm, data).then(r); }); });
    const d = digest(algorithm, data);
    if (step) steps.push(d);
    return d;
  });
  fake.unlockDelay = 0;
  fake.getDelay = 0;
  fake.answerId = "AQ";
  fake.openHangs = false;
  fake.openDelay = 0;
  serve();
});
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  // a preparation a card began before it went may still be digesting its text off the main thread: it asks for its
  // nonce here, from this case's stand-in, and not from the next case's
  await new Promise((r) => setTimeout(r, 50));
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useStore.setState(useStore.getInitialState());
  useE2EStore.setState(useE2EStore.getInitialState());
});

describe("one press on Approve", () => {
  it("approves an opened prompt with one touch over the answer's own challenge, sent once the Undo window ends, with no Review", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);                              // the touch first, nothing awaited
    expect(screen.queryByRole("button", { name: /Review/ })).toBeNull();
    await screen.findByRole("button", { name: "Undo" });
    // the window: nothing sent in its first seconds, then sent by its end
    await stepped(3);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    await stepped(12, true);
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    const [payload] = signed();
    expect(payload).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Approve", digest: await textDigest(p.text) });
    expect(C.b64u(bytes(fake.asked[0].publicKey!.challenge))).toBe(C.b64u(await stepOf(payload)));
  });

  it("sends nothing where Undo is pressed before the window ends, and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(await screen.findByText(/Not sent: you undid it\./)).toBeTruthy();
  });

  it("approves a sealed prompt in a locked tab with one touch that signs the answer and unlocks the keys, its words then shown with Undo, then sent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    const p = prompt(BASH);
    showing(sessions({ prompts: [p] }));
    approvals();
    expect(screen.getByText("Encrypted")).toBeTruthy();
    fireEvent.click(await preparedApprove());
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);                              // one touch, nothing awaited
    const asked = fake.asked[0].publicKey!;
    expect((asked.allowCredentials ?? []).map((c) => C.b64u(bytes(c.id)))).toEqual(["AQ"]);
    const by = (asked.extensions as { prf: { evalByCredential: Record<string, { first: unknown; second?: unknown }> } }).prf.evalByCredential;
    expect(Object.keys(by)).toEqual(["AQ"]);
    expect([C.b64u(bytes(by.AQ.first)), C.b64u(bytes(by.AQ.second))]).toEqual([C.b64u(SALT), C.b64u(await K.prfInputHash(SALT))]);
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Bash")).toBeTruthy();                               // its words opened, shown
    await screen.findByRole("button", { name: "Undo" });
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    await stepped(12, true);
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    const [payload] = signed();
    expect(payload).toMatchObject({ purpose: "answer", to: "L1", ref: 7, text: "Approve", digest: await textDigest(p.text) });
    expect(C.b64u(bytes(asked.challenge))).toBe(C.b64u(await stepOf(payload)));
  });

  it("unlocks but sends nothing where the sealed prompt turns out to ask the owner to choose, and shows its choices", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(ASKED)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Which cap?")).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(await screen.findByText(/It asks you to choose: nothing was sent\./)).toBeTruthy();
  });

  it("says, before the press, what goes in the clear: the word Approve", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    expect(screen.getByRole("note", { name: "In the clear" }).textContent).toMatch(/the word “Approve”.*in the clear/);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("prepares the answer before the press, and again before its nonce is five minutes old", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    const first = sent("/api/ade/link/nonce").length;
    for (let i = 0; i < 5; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });   // 250 s
    await vi.waitFor(() => expect(sent("/api/ade/link/nonce").length).toBeGreaterThan(first));
  });

  it("offers no one-press approval in a locked tab where no passkey that unlocks the keys may sign it (high security)", async () => {
    fake.state = "locked";
    signers = ["Ag"];                                  // the account's hardware key, which unlocks nothing
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await vi.waitFor(() => expect(sent("/api/ade/link/nonce").length).toBeGreaterThan(0));
    expect(screen.getByText("Encrypted")).toBeTruthy();
    // prepared, and saying why it offers none
    expect(await screen.findByText(/No passkey that unlocks encryption may sign this here: unlock first, then approve\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("sends nothing after Undo, even where the card then goes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    cleanup();                                         // the card gone after Undo
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("sends nothing where the card goes inside Undo's window: closing the panel cancels it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    cleanup();                                         // the panel dismissed (Escape, its backdrop) inside the window
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("sends nothing in a locked tab where the passkey gives no PRF output, and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.prf = false;
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);
    await stepped(12);
    expect(fake.unlockFrom).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(await screen.findByText(/This passkey gave no key in this browser.*Nothing was sent\./)).toBeTruthy();
  });

  it("says a security key's words where a security key gives no PRF output in a locked tab, and sends nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.prf = false;
    await keysView("security-key");
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    await vi.waitFor(() => expect(screen.queryByText(/This security key gave no key in this browser.*Nothing was sent\./)).not.toBeNull());
  });
});

describe("one press on Approve: a changed prompt, a preparation too old or failed, and what it says", () => {
  it("signs nothing in a locked tab where the prompt's envelope changed after its answer was prepared", async () => {
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    const before = sent("/api/ade/link/nonce").length;
    stall = true;                                      // the new envelope's preparation never comes back
    act(() => { useStore.setState({ links: sessions({ prompts: [prompt(BASH2)] }) }); });
    await vi.waitFor(() => expect(sent("/api/ade/link/nonce").length).toBeGreaterThan(before));
    fireEvent.click(approve);
    expect(fake.credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs nothing where a prompt in the clear changed its text after its answer was prepared", async () => {
    const clear = (item: unknown) => prompt(item, { text: JSON.stringify(item) });
    showing(sessions({ prompts: [clear(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    stall = true;
    act(() => { useStore.setState({ links: sessions({ prompts: [clear(BASH2)] }) }); });
    await screen.findByText(/rm -rf \//);
    fireEvent.click(approve);
    expect(fake.credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("sends nothing where the prompt's stored text changes inside Undo's window, and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    act(() => { useStore.setState({ links: sessions({ prompts: [prompt(BASH2)] }) }); });
    await stepped(1);                                  // the window closes at the change, not at its end
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(screen.getByText(/Its text changed after this approval was prepared: nothing was sent\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs nothing where the prepared answer is older than a nonce lives, says so, and prepares it again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    const approve = await preparedApprove();
    const before = sent("/api/ade/link/nonce").length;
    vi.setSystemTime(new Date(Date.now() + 280_000));  // the tab asleep: no timer ran, the clock moved on
    fireEvent.click(approve);
    expect(fake.credentials.get).not.toHaveBeenCalled();
    expect(await screen.findByText(/Nothing was signed: this approval was prepared too long ago for its nonce/)).toBeTruthy();
    await vi.waitFor(() => expect(sent("/api/ade/link/nonce").length).toBeGreaterThan(before));
  });

  it("says why Approve waits where its answer could not be prepared, and tries again soon", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    nonceFails = 2;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    expect(await screen.findByText(/Approve waits: .*It is tried again in 15 seconds\./)).toBeTruthy();
    await stepped(35);
    // a failed try is seen only once its text's digest, which ends off the main thread, comes back, and the steps above
    // go on meanwhile: under a whole run's load the second is seen late and the third try falls after the 35 s. Each
    // check of a wait moves the fake clock on by 50 ms, so a wait of 10 s reaches that try and its preparation, inside
    // the case's own 30 s.
    await preparedApprove(10_000);
    expect(screen.queryByText(/Approve waits: /)).toBeNull();
  }, 30_000);

  it("keeps Review and Sign, where Sign on my Mac is offered, for an account with a Mac signer paired", async () => {
    paired = true;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    const approve = await screen.findByRole("button", { name: "Approve" });
    await vi.waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false));
    fireEvent.click(approve);
    expect(await screen.findByRole("button", { name: /Review the approval/ })).toBeTruthy();
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("sends nothing, and says so, where the words do not open after the unlock", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    const elsewhere = { ...prompt(BASH), text: seal({ text: JSON.stringify(BASH), session: null, said_at: null }, "another-place") };
    showing(sessions({ prompts: [elsewhere] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Its words did not open here: nothing was sent\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("counts Undo's window down, and says closing the view cancels it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    const status = await screen.findByRole("status", { name: "Approving" });
    expect(status.textContent).toMatch(/it is sent in 5 s unless you press Undo\. Closing this view cancels it\./);
    await stepped(1);
    expect(screen.getByRole("status", { name: "Approving" }).textContent).toMatch(/it is sent in 4 s/);
  });

  it("says beside Approve, in a locked tab, that one press opens the words and sends them unless Undo is pressed", async () => {
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await preparedApprove();
    expect(screen.getByText(/One press unlocks your keys and signs this approval; its words then show for 5 seconds with Undo before it is sent\. Closing this view before then cancels it\./)).toBeTruthy();
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("says a question whose options are not numbered is answered in the session's window, nothing sent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(ASKED_LOOSE)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    expect(await screen.findByText(/It asks you to choose, in the session's window: nothing was sent\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs the newest preparation, not one before it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    const before = steps.length;
    for (let i = 0; i < 5; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });
    await prepared(before);
    const newest = issued.at(-1);
    expect(issued.length).toBeGreaterThan(1);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await screen.findByRole("button", { name: "Undo" });
    await stepped(12, true);
    expect(signed()[0].nonce).toBe(newest);
  });

  it("starts Undo's window only once the words opened, and never for a question, where the unlock takes its time", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.unlockDelay = 2500;
    await keysView();
    showing(sessions({ prompts: [prompt(ASKED)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    for (let i = 0; i < 10; i++) {
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    }
    expect(await screen.findByText(/It asks you to choose: nothing was sent\. Choose its options above, then answer\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });
});

describe("one press on Approve: a tab shown again, the panel closed, offline, and Undo too late", () => {
  it("prepares again when the tab is shown again, so a press after the phone slept signs a fresh nonce", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    const before = steps.length;
    vi.setSystemTime(new Date(Date.now() + 300_000));  // asleep: no timer ran, the clock moved on
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await prepared(before);
    const newest = issued.at(-1);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await screen.findByRole("button", { name: "Undo" });
    await stepped(12, true);
    expect(signed()[0].nonce).toBe(newest);
  });

  it("sends nothing, and says so, where the words a locked tab's touch opened are not a prompt", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt({ note: "not a tool call" })] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Its text is not in the form a prompt takes: nothing was sent\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  for (const how of ["Escape", "a press on the backdrop"]) {
    it(`sends nothing where the panel is closed with ${how} inside Undo's window`, async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      showing(sessions({ prompts: [prompt(BASH)] }));
      function Panel() {
        const [open, setOpen] = useState(true);
        return open ? <Approvals records={[]} requests={[]} onClose={() => setOpen(false)} onOpen={() => {}} /> : null;
      }
      const { container } = render(<Panel />);
      await screen.findByText("Bash");
      fireEvent.click(await preparedApprove());
      await screen.findByRole("button", { name: "Undo" });
      if (how === "Escape") fireEvent.keyDown(window, { key: "Escape" });
      else fireEvent.mouseDown(container.firstElementChild!);
      await vi.waitFor(() => expect(screen.queryByRole("complementary", { name: "Approvals" })).toBeNull());
      await stepped(12);
      expect(sent("/api/ade/link/commands")).toHaveLength(0);
    });
  }

  it("says the approval was not sent where its card leaves the list inside Undo's window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    act(() => { useStore.setState({ links: sessions({ prompts: [] }) }); });
    expect(await screen.findByText(/Your approval was not sent: its card closed before it went\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("offers no press while the page is offline, saying so as the review does", async () => {
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    const approve = await preparedApprove();
    act(() => { useStore.setState({ offline: true }); });
    expect(await screen.findByText("Offline: signing waits until this page reaches the server again.")).toBeTruthy();
    expect(approve.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(approve);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("says beside Approve, before the press, which passkey the session refuses, with the pin line for its window", async () => {
    const KEY = "3".repeat(32);
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    serve({ "GET /api/ade/link/sessions": () => [200, links],
            "GET /api/account/passkeys": () => [200, { passkeys: [{ id: "p3", name: "YubiKey", fingerprint: KEY, created_at: 5, tier: "hardware" }], high_security: false }] });
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    const note = await screen.findByRole("note", { name: "Before you sign" });
    expect(note.textContent).toContain("YubiKey was added after mac was linked: unless you gave mac its pin line, it refuses this.");
    expect(within(note).getByText(/pin --add/).textContent).toContain(`pin --add ${KEY}`);
    expect(fake.credentials.get).not.toHaveBeenCalled();
  });

  it("says Undo came too late, not that it undid anything, where it comes after the window ended and the answer went", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    const undo = await screen.findByRole("button", { name: "Undo" });
    await stepped(4);
    vi.advanceTimersByTime(1000);                      // the window ends and the answer goes, before the card is drawn again
    fireEvent.click(undo);
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    await stepped(1);
    expect(screen.queryByText(/Not sent: you undid it\./)).toBeNull();
    expect(screen.getByText(/Too late to undo: it was already on its way\./)).toBeTruthy();
  });

  it("opens the review in place of one press where the account's signers cannot be read", async () => {
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    serve({ "GET /api/ade/link/sessions": () => [200, links], "GET /api/ade/signers": () => [500, { detail: "Not now." }] });
    approvals();
    await screen.findByText("Bash");
    const approve = await screen.findByRole("button", { name: "Approve" });
    await vi.waitFor(() => expect(approve.hasAttribute("disabled")).toBe(false));
    fireEvent.click(approve);
    expect(await screen.findByRole("button", { name: /Review the approval/ })).toBeTruthy();
    expect(fake.credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
  });
});

describe("one press on Approve: what changes while the passkey's prompt or the unlock runs", () => {
  it("sends nothing, and opens no Undo window, where the text changed while the passkey's prompt was up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.getDelay = 3000;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    act(() => { useStore.setState({ links: sessions({ prompts: [prompt(BASH2)] }) }); });
    for (let i = 0; i < 12; i++) {
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    }
    expect(screen.getByText(/Its text changed after this approval was prepared: nothing was sent\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("sends nothing, and opens no Undo window, where the envelope changed while a locked tab's unlock ran", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.unlockDelay = 2500;
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    act(() => { useStore.setState({ links: sessions({ prompts: [prompt(BASH2)] }) }); });
    for (let i = 0; i < 12; i++) {
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    }
    expect(screen.getByText(/Its text changed after this approval was prepared: nothing was sent\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("says the words did not open where they are still opening ten seconds after the unlock", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    fake.openHangs = true;                             // nothing is opened while the tab is locked: the unlock's opening hangs
    fireEvent.click(approve);
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    await stepped(12);
    expect(screen.getByText(/Its words did not open here: nothing was sent\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("offers nothing to press until the account's signers are read, says why, and gives the review after ten seconds without them", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signersStall = true;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await stepped(3);
    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Approve waits: your account's Mac signers are being read.")).toBeTruthy();
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
    await stepped(8);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve.hasAttribute("disabled")).toBe(false);
    fireEvent.click(approve);
    expect(await screen.findByRole("button", { name: /Review the approval/ })).toBeTruthy();
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
  });
});

describe("one press on Approve: preparations out of order", () => {
  it("keeps the newest preparation where an older one comes back after it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    await preparedApprove();
    holdNext = true;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });   // this one's answer is held
    await vi.waitFor(() => expect(release).not.toBeNull());
    const older = issued.at(-1);
    const before = steps.length;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });   // this one's comes at once
    await prepared(before);
    const newest = issued.at(-1);
    expect(newest).not.toBe(older);
    const later = steps.length;
    await act(async () => { release!(); });
    await prepared(later);                             // the older one made after it, and not kept
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await screen.findByRole("button", { name: "Undo" });
    await stepped(12, true);
    expect(signed()[0].nonce).toBe(newest);
  });
});

describe("one press on Approve: a nonce's age, a failed send and a tab locked again", () => {
  it("sends nothing, and says so, where Undo's window ends more than 295 seconds after the nonce was asked for", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    const approve = await preparedApprove();
    vi.setSystemTime(new Date(Date.now() + 260_000));  // pressed within its 270 seconds
    fake.getDelay = 40_000;                            // and the passkey's prompt up for 40 of them
    fireEvent.click(approve);
    await stepped(41);                                 // the touch ended 300 seconds after the nonce was asked for
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(screen.getByText(/Not sent: it was signed too long ago for the server to take it\. Press Approve again\./)).toBeTruthy();
    await stepped(9);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("counts a preparation's age from when its nonce was asked for, not from when it came", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    holdNext = true;
    approvals();
    await screen.findByText("Bash");
    await vi.waitFor(() => expect(release).not.toBeNull());
    await stepped(30);                                 // its answer on the way for 30 seconds
    const before = steps.length;
    await act(async () => { release!(); });
    await prepared(before);
    vi.setSystemTime(new Date(Date.now() + 245_000));  // 275 seconds since it was asked for, 245 since it came
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(fake.credentials.get).not.toHaveBeenCalled();
    expect(await screen.findByText(/Nothing was signed: this approval was prepared too long ago/)).toBeTruthy();
  });

  it("says why Approve waits while offline, reads the account's signers again once back online, and offers one press then", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    serve({ "GET /api/ade/link/sessions": () => [200, links],
            "GET /api/ade/signers": () => (useStore.getState().offline ? [503, { detail: "offline" }] : [200, { signers: [] }]) });
    act(() => { useStore.setState({ offline: true }); });
    approvals();
    await screen.findByText("Bash");
    await stepped(2);
    // offline, the Approve that waits for the signers says why, as the review does
    expect(screen.getByText("Offline: signing waits until this page reaches the server again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true);
    act(() => { useStore.setState({ offline: false }); });
    fireEvent.click(await preparedApprove());
    expect(await screen.findByRole("button", { name: "Undo" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Review the approval/ })).toBeNull();
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);
  });

  it("keeps Approve unpressable while the answer is being sent, so nothing is signed twice", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    commandsHold = true;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(8);                                  // the window over, the answer on its way
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    const again = screen.getByRole("button", { name: "Approve" });
    expect(again.hasAttribute("disabled")).toBe(true);
    fireEvent.click(again);
    await stepped(2);
    expect(fake.credentials.get).toHaveBeenCalledTimes(1);
  });

  it("says the approval was not sent where its card leaves the list while a locked tab's unlock runs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.unlockDelay = 2500;
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    act(() => { useStore.setState({ links: sessions({ prompts: [] }) }); });
    expect(await screen.findByText(/Your approval was not sent: its card closed before it went\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("sends nothing, and says so, where the tab is locked again inside Undo's window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    act(() => { fake.set("locked"); });
    await stepped(1);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(screen.getByText(/Its words were locked again before you read them: nothing was sent\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs a second press with a new nonce where the first answer's send failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    let refused = 1;
    serve({ "GET /api/ade/link/sessions": () => [200, links],
            "POST /api/ade/link/commands": () => (refused-- > 0 ? [500, { detail: "Not now." }] : [200, { seq: 9, expires_at: T + 3600 }]) });
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(6);
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(12);                                 // not "until sent": the first was sent already
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(2));
    const [first, second] = signed();
    expect(second.nonce).not.toBe(first.nonce);
  });

  it("sends nothing, and opens no Undo window, where the tab is locked while the passkey's prompt is up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.getDelay = 3000;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    act(() => { fake.set("locked"); });
    for (let i = 0; i < 10; i++) {
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    }
    expect(screen.getByText(/Its words were locked again before you read them: nothing was sent\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("says the passkey is not one that unlocks encryption where the browser answers with one not asked for", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.answerId = "Ag";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    expect(await screen.findByText(/This passkey is not one that unlocks encryption here\. Nothing was sent\./)).toBeTruthy();
    expect(fake.unlockFrom).not.toHaveBeenCalled();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });
});

describe("one press on Approve: a refused or unreachable send, a late timer and the signers' reads", () => {
  it("says, where its send is refused, that the approval was not sent and to press Approve again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    serve({ "GET /api/ade/link/sessions": () => [200, links], "POST /api/ade/link/commands": () => [500, { detail: "Not now." }] });
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(8);
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    expect(screen.getByText("Not sent: Not now. Press Approve again.")).toBeTruthy();
    expect(useStore.getState().error).toBeNull();
  });

  it("opens no window, and says so, where a locked tab's words open more than 290 seconds after the nonce was asked for", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    vi.setSystemTime(new Date(Date.now() + 260_000));  // pressed within its 270 seconds
    fake.getDelay = 20_000;                            // the touch ends at 280 seconds,
    fake.unlockDelay = 5_000;                          // the unlock at 285,
    fake.openDelay = 8_000;                            // and the words open at 293: late, within the opening's 10 seconds
    fireEvent.click(approve);
    for (let i = 0; i < 45; i++) {
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    }
    expect(fake.unlockFrom).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Not sent: it was signed too long ago for the server to take it\. Press Approve again\./)).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("drops the answer, and says so, where Undo's window ends past 295 seconds because its timer came late", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    const approve = await preparedApprove();
    vi.setSystemTime(new Date(Date.now() + 260_000));  // pressed within its 270 seconds
    fake.getDelay = 25_000;                            // the window opens at 285 seconds, its end due at 290
    fireEvent.click(approve);
    await stepped(26);
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
    vi.setSystemTime(new Date(Date.now() + 15_000));   // a starved page: the clock runs on, the window's timer waits
    const undo = screen.getByRole("button", { name: "Undo" });
    vi.advanceTimersByTime(5000);                      // the overdue timer fires, before the card is drawn again
    fireEvent.click(undo);                             // and a tap lands on the Undo still drawn
    await stepped(6);
    expect(screen.queryByText(/Too late to undo/)).toBeNull();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(screen.getByText(/Not sent: it was signed too long ago for the server to take it\. Press Approve again\./)).toBeTruthy();
  });

  it("says the approval was not sent where its card goes after a locked tab's unlock, its words still opening", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    fake.openHangs = true;                             // nothing is opened while the tab is locked: the unlock's opening hangs
    fireEvent.click(approve);
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    await stepped(2);                                  // the touch and the unlock over, the words still opening
    act(() => { useStore.setState({ links: sessions({ prompts: [] }) }); });
    expect(await screen.findByText(/Your approval was not sent: its card closed before it went\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("says, where the server cannot be reached for its send, that the approval may not have reached it, and reads the list again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    // a gateway's own 502, its body no object of the app's (store.ts, unreachable)
    serve({ "GET /api/ade/link/sessions": () => [200, links], "POST /api/ade/link/commands": () => [502, "Bad gateway"] });
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(8);
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    expect(screen.getByText("It may not have reached the server: the connection failed before an answer came. If the prompt still waits once the list is read again, press Approve again.")).toBeTruthy();
    expect(screen.queryByText("Offline: signing waits until this page reaches the server again.")).toBeNull();
    const sendAt = calls.findIndex((c) => c.path === "/api/ade/link/commands");
    expect(calls.slice(sendAt + 1).some((c) => c.path === "/api/ade/link/sessions")).toBe(true);
  });

  it("sends nothing, and says so, where the page is hidden inside Undo's window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    try {
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      await stepped(1);
      expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
      expect(screen.getByText("Not sent: this page was hidden before Undo's window ended. Press Approve again.")).toBeTruthy();
      await stepped(12);
      expect(sent("/api/ade/link/commands")).toHaveLength(0);
    } finally {
      delete (document as unknown as { visibilityState?: string }).visibilityState;
    }
  });

  it("offers no Close while Undo's window runs, and offers it again after", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("asks for no nonce once its card is gone while its text is being digested", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    holdText = true;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await vi.waitFor(() => expect(textHeld.length).toBeGreaterThan(0));
    cleanup();
    await act(async () => { for (const go of textHeld) go(); await new Promise((r) => setTimeout(r, 30)); });
    await stepped(1);
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
  });

  it("asks for the account's signers once for all its cards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const second = { ...prompt(BASH2), seq: 8, link_id: "L2", from: "planner", from_id: "L2",
                     text: sealedBy("L2", "prompt", PROMPT_CID, JSON.stringify(BASH2)) };
    showing(sessions({ links: [link(), link({ id: "L2", name: "planner" })], prompts: [prompt(BASH), second] }));
    approvals();
    await vi.waitFor(() => expect(screen.getAllByRole("button", { name: "Approve" }).every((b) => !b.hasAttribute("disabled"))).toBe(true));
    expect(calls.filter((c) => c.path === "/api/ade/signers")).toHaveLength(1);
  });

  it("reads the account's signers again 15 seconds after a read that failed while online, and offers one press then", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    let failed = 1;
    serve({ "GET /api/ade/link/sessions": () => [200, links],
            "GET /api/ade/signers": () => (failed-- > 0 ? [500, { detail: "Not now." }] : [200, { signers: [] }]) });
    approvals();
    await screen.findByText("Bash");
    await stepped(2);
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
    await stepped(15);
    fireEvent.click(await preparedApprove());
    expect(await screen.findByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("says the approval was not sent where its card goes while the passkey's prompt is up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.getDelay = 3000;
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await screen.findByText("Bash");
    fireEvent.click(await preparedApprove());
    act(() => { useStore.setState({ links: sessions({ prompts: [] }) }); });
    expect(await screen.findByText(/Your approval was not sent: its card closed before it went\./)).toBeTruthy();
    await stepped(12);
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("signs a second press with a new nonce where the first touch answered with a credential not asked for", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.answerId = "Ag";
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    const approve = await preparedApprove();
    const first = issued.at(-1);
    fireEvent.click(approve);
    expect(await screen.findByText(/This passkey is not one that unlocks encryption here\. Nothing was sent\./)).toBeTruthy();
    fake.answerId = "AQ";
    fireEvent.click(await preparedApprove());
    await screen.findByRole("button", { name: "Undo" });
    await stepped(12, true);
    expect(signed()[0].nonce).not.toBe(first);
  });
});

describe("one press on Approve: a hidden page, the signers read again, and an item a link names", () => {
  it("opens no window, and says so, where the page is hidden when a locked tab's words open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fake.state = "locked";
    fake.unlockDelay = 2500;
    await keysView();
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    fireEvent.click(await preparedApprove());
    await vi.waitFor(() => expect(fake.unlockFrom).toHaveBeenCalledTimes(1));
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    try {
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      for (let i = 0; i < 12; i++) {
        await stepped(1);
        expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
      }
      expect(screen.getByText("Not sent: this page was hidden before Undo's window ended. Press Approve again.")).toBeTruthy();
      expect(sent("/api/ade/link/commands")).toHaveLength(0);
    } finally {
      delete (document as unknown as { visibilityState?: string }).visibilityState;
    }
  });

  it("reads the account's signers again, after a read that failed, once for all its cards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const second = { ...prompt(BASH2), seq: 8, link_id: "L2", from: "planner", from_id: "L2",
                     text: sealedBy("L2", "prompt", PROMPT_CID, JSON.stringify(BASH2)) };
    const links = sessions({ links: [link(), link({ id: "L2", name: "planner" })], prompts: [prompt(BASH), second] });
    showing(links);
    let failed = 1;
    serve({ "GET /api/ade/link/sessions": () => [200, links],
            "GET /api/ade/signers": () => (failed-- > 0 ? [500, { detail: "Not now." }] : [200, { signers: [] }]) });
    approvals();
    await screen.findAllByText("Bash");
    await stepped(17);
    await vi.waitFor(() => expect(screen.getAllByRole("button", { name: "Approve" }).every((b) => !b.hasAttribute("disabled"))).toBe(true));
    expect(calls.filter((c) => c.path === "/api/ade/signers")).toHaveLength(2);
  });

  it("reads the account's signers again for a new card once the Signers list was read, a signer perhaps paired since", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await preparedApprove();
    const signersRead = () => calls.filter((c) => c.path === "/api/ade/signers").length;
    expect(signersRead()).toBe(1);
    render(<SignersSection />);
    await vi.waitFor(() => expect(signersRead()).toBe(2));
    const second = { ...prompt(BASH2), seq: 8 };
    act(() => { useStore.setState({ links: sessions({ prompts: [prompt(BASH), second] }) }); });
    await vi.waitFor(() => expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(2));
    await vi.waitFor(() => expect(signersRead()).toBe(3));
  });

  it("reads the account's signers again for another account signed in on the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    approvals();
    await preparedApprove();
    const signersRead = () => calls.filter((c) => c.path === "/api/ade/signers").length;
    expect(signersRead()).toBe(1);
    cleanup();
    act(() => { useStore.setState({ user: { id: "acct-2", email: "p@example.org", can_sign_in_again: true } }); });
    approvals();
    await vi.waitFor(() => expect(signersRead()).toBe(2));
  });

  it("does not say the item a link names is not waiting while the hidden ones are still being read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const links = sessions({ prompts: [prompt(BASH)] });
    showing(links);
    let answer: (() => void) | null = null;
    const hidden = { ...prompt(BASH2), seq: 8, hidden_at: T - 60, open: true, closed_reason: null, closed_at: null, ended: "waiting" };
    serve({ "GET /api/ade/link/sessions": () => [200, links] });
    const stub = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).startsWith("/api/ade/link/prompts/closed")) {
        calls.push({ path: String(url), method: "GET", body: undefined });
        return new Promise<Response>((r) => { answer = () => r(new Response(JSON.stringify({ prompts: [hidden], server_time: T, closed_more: 0 }), { status: 200 })); });
      }
      return stub(url, init);
    }));
    render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} item={8} />);
    await screen.findByText("Bash");
    await vi.waitFor(() => expect(answer).not.toBeNull());
    await stepped(2);
    expect(screen.queryByText(/The item the link names is not waiting here/)).toBeNull();
    await act(async () => { answer!(); });
    await stepped(1);
    expect(screen.queryByText(/The item the link names is not waiting here/)).toBeNull();
  });

  it("does not say the item a link names is not waiting once it was shown and then answered", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    showing(sessions({ prompts: [prompt(BASH)] }));
    render(<Approvals records={[]} requests={[]} onClose={() => {}} onOpen={() => {}} item={7} />);
    await screen.findByText("Bash");
    await stepped(1);
    act(() => { useStore.setState({ links: sessions({ prompts: [] }) }); });
    await stepped(1);
    expect(screen.queryByText(/The item the link names is not waiting here/)).toBeNull();
  });
});
