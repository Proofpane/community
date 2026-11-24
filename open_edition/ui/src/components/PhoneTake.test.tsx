// Signing for a linked session, as the owner reads it before and after the touch: how long what is signed lives and
// when its session was last heard, and what became of one that expired untaken; which passkeys the session pinned,
// told before the touch and after it; and a step the session's guard keeps for the owner at the machine, which it may
// take on the owner's Approve from the page where the owner allowed that at the machine, signed with a key his
// allowance names (and, for a deployment or a push, one that cannot be backed up).
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LinkEvent, LinkView } from "../api";
import { textDigest } from "../passkey";
import { useStore } from "../store";
import { dayTime } from "../when";

// passkeys work here, as in a browser at the server's own address; the credential each touch answers with, and its
// authenticator data, are the case's to choose (`passkeys.id`, `passkeys.auth`: by default too short to read)
const passkeys = vi.hoisted(() => {
  const self = {
    id: "AQ",
    auth: new Uint8Array([2]),
    get: vi.fn(async () => ({ id: self.id, type: "public-key", response: {
      clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: self.auth.slice().buffer,
      signature: new Uint8Array([3]).buffer, userHandle: null } })),
  };
  (globalThis as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = function PublicKeyCredential() {};
  Object.defineProperty(navigator, "credentials", { value: { get: self.get }, configurable: true });
  return self;
});

import { Commands, Conversation, LinkCard, pinsWarning, Question, SignBox, type AccountKey } from "./Channel";
import { PromptCard } from "./LinkInbox";

const T = Math.floor(Date.now() / 1000);

type Answer = [number, unknown];
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
let calls: { path: string; method: string; body: Record<string, unknown> | undefined }[] = [];
/** The server as the case has it answer: each "METHOD /path" its route; anything else 404. */
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}
const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

/** A link as the sessions view lists it; `over` may hold what a newer server adds (its pins, its phone_take). */
const link = (over: Partial<LinkView> & Record<string, unknown> = {}): LinkView => ({
  id: "L1", name: "MacBook Pro", kind: "local", session_ref: null, interval_min: 15, created_at: T - 3600,
  last_seen_at: T - 60, last_heartbeat_at: T - 60, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0,
  checkpoint_at: null, events: 0, last_event: null, commands: [], stopped_at: null, ...over,
} as LinkView);
/** A link whose server reports `phone_take` as it is, in a shape the page reads or in any other. */
const reporting = (phone_take: unknown) => link({ phone_take } as unknown as Partial<LinkView>);
/** The linked sessions as the page's store holds them. */
const listing = (...links: LinkView[]) =>
  useStore.setState({ links: { links, questions: [], public_url: "https://pp.example" } });

const SIGNS: Record<string, Route> = {
  "POST /api/ade/link/nonce": () => [200, { nonce: "n1", rp_id: "pp.example", deployment: "pp.example", account: "u",
    server_time: T }],
  "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: T + 3600 }],
};
const UNTIL = dayTime(T + 3600);           // what is signed here lives an hour from the nonce's time

/** An instruction to MacBook Pro, reviewed: the Sign button. */
const instructed = async (done: () => Promise<void> = async () => {}) => {
  render(<SignBox purpose="instruction" to="L1" toName="MacBook Pro" placeholder="What MacBook Pro should do"
    submit="Review" onDone={done} />);
  fireEvent.change(screen.getByLabelText("What MacBook Pro should do"), { target: { value: "run the tests" } });
  fireEvent.click(screen.getByRole("button", { name: /Review/ }));
  return screen.findByRole("button", { name: /Sign with your passkey/ });
};

beforeEach(() => {
  useStore.setState({ user: { id: "u", email: "o@example.org", can_sign_in_again: true } });
  passkeys.id = "AQ";
  passkeys.auth = new Uint8Array([2]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  passkeys.get.mockClear();
  useStore.setState(useStore.getInitialState());
});

describe("before the touch: how long what is signed lives, and when its session was last heard", () => {
  it("says when an instruction is good until, when its session was heard, and that unread it expires", async () => {
    serve(SIGNS);
    listing(link({ last_heard_at: T - 120 }));
    await instructed();
    expect(screen.getByRole("note", { name: "When it expires" }).textContent).toBe(
      `MacBook Pro was last heard 2m ago (${dayTime(T - 120)}). If it does not read its link before ${UNTIL}, `
      + "this instruction expires unread, and nothing acts on it.");
    expect(passkeys.get).not.toHaveBeenCalled();
  });

  it("says before an answer is signed that its session is silent, or has not been heard from yet", async () => {
    serve(SIGNS);
    const q: LinkEvent = { seq: 12, time: new Date().toISOString(), kind: "question", from: "MacBook Pro", to: "owner",
      ref: null, text: "Which cap?" };
    // each time a question of its own: an answer typed and not sent is kept as its question's draft
    const reviewed = async (seq: number) => {
      render(<Question q={{ ...q, seq }} linkId="L1" />);
      fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
      fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "per org" } });
      fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
      await screen.findByRole("button", { name: /Sign with your passkey/ });
      return screen.getByRole("note", { name: "When it expires" }).textContent;
    };
    listing(link({ last_heard_at: T - 7200, last_heartbeat_at: T - 7200, silent: true }));
    expect(await reviewed(12)).toBe(`MacBook Pro was last heard 2h ago (${dayTime(T - 7200)}), and is silent: not `
      + `heard for more than two of its 15-minute intervals. If it does not read its link before ${UNTIL}, this answer `
      + "expires unread, and nothing acts on it.");
    cleanup();
    listing(link({ last_heard_at: null, last_heartbeat_at: null }));
    expect(await reviewed(13)).toBe(`MacBook Pro has not been heard from yet. If it does not read its link before `
      + `${UNTIL}, this answer expires unread, and nothing acts on it.`);
  });
});

describe("afterwards: a command that expired untaken", () => {
  const EXPIRED = "It expired before its session took it, so nothing acted on it.";
  const cmd: LinkEvent = { seq: 5, time: "2026-11-20T10:00:00Z", kind: "instruction", from: "owner", to: "MacBook Pro",
    ref: null, text: "ship it", state: "expired" };

  it("says so where the owner's commands are listed, and in the conversation beside its state", async () => {
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [cmd] }] });
    const { unmount } = render(<Commands l={link({ commands: [cmd] })} />);
    expect(screen.getByText("expired").getAttribute("title")).toBe(EXPIRED);
    expect(screen.getByText(EXPIRED)).toBeTruthy();
    unmount();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("ship it");
    expect(screen.getByText(EXPIRED).previousElementSibling?.textContent).toBe("expired");
  });
});

describe("the passkeys a session pinned, told before the touch and after it", () => {
  const TOUCH = "1".repeat(32), PHONE = "2".repeat(32), KEY = "3".repeat(32);
  const mine: AccountKey[] = [
    { id: "p1", name: "Touch ID", fingerprint: TOUCH, created_at: T - 7200, tier: "device" },
    { id: "p2", name: "iPhone", fingerprint: PHONE, created_at: T - 60, tier: "device" },
  ];
  /** The server: the account's passkeys, and encryption's view (none where `view` is false: it cannot be read) with
   *  the session's pins and each passkey's credential id, Touch ID's "Ag" and iPhone's "AQ". */
  const routes = (pins: string[] | null, view = true): Record<string, Route> => ({
    ...SIGNS,
    "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: false }],
    ...(view ? { "GET /api/ade/e2e": (): Answer => [200, {
      links: [{ link_id: "L1", name: "MacBook Pro", key_id: null, last_required_at: null, pins }],
      passkeys: [{ id: "p1", credential_id: "Ag", name: "Touch ID", tier: "device", created_at: T - 7200 },
                 { id: "p2", credential_id: "AQ", name: "iPhone", tier: "device", created_at: T - 60 }] }] } : {}),
  });
  const done = vi.fn(async () => {});
  beforeEach(() => { done.mockClear(); });

  it("reads a session that reports it pinned none of your passkeys as refusing each of them", async () => {
    const all = [...mine, { id: "p3", name: "YubiKey", fingerprint: KEY, created_at: T - 9000, tier: "hardware" }];
    expect(pinsWarning(all, false, [], T - 3600)).toEqual({ refused: [{ name: "Touch ID", fingerprint: TOUCH },
      { name: "iPhone", fingerprint: PHONE }, { name: "YubiKey", fingerprint: KEY }], all: true, known: true,
      pinned: [] });
    serve(routes([]));
    await instructed();
    expect(screen.getByRole("note", { name: "Before you sign" }).textContent).toContain(
      "MacBook Pro will refuse this: it has not pinned Touch ID and iPhone, the passkeys that can sign it.");
  });

  it("knows a session's pins from Linked sessions where encryption's view cannot be read", async () => {
    serve(routes(null, false));
    listing(link({ pins: [TOUCH] }));
    await instructed();
    expect(screen.getByRole("note", { name: "Before you sign" }).textContent).toContain(
      "MacBook Pro takes this only if Touch ID signs it: it has not pinned iPhone, and refuses what that key signs.");
  });

  it("sends nothing an unpinned passkey signed until you choose, naming it; Send anyway sends it", async () => {
    serve(routes([TOUCH]));
    passkeys.id = "AQ";                                       // iPhone, which it has not pinned
    fireEvent.click(await instructed(done));
    const held = await screen.findByRole("group", { name: "Not sent yet" });
    expect(held.textContent).toContain("MacBook Pro would refuse this: it has not pinned iPhone, the passkey you "
      + "signed with. Nothing is sent until you choose. It takes what Touch ID signs.");
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    fireEvent.click(within(held).getByRole("button", { name: "Send anyway" }));
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    expect(sent("/api/ade/link/commands")).toHaveLength(1);
    expect(sent("/api/ade/link/commands")[0].body?.credential).toMatchObject({ id: "AQ" });
    expect(passkeys.get).toHaveBeenCalledTimes(1);
  });

  it("asks the passkey again on Sign again, and sends only what a passkey it pinned signed", async () => {
    serve(routes([TOUCH]));
    fireEvent.click(await instructed(done));
    const held = await screen.findByRole("group", { name: "Not sent yet" });
    passkeys.id = "Ag";                                       // Touch ID, which it pinned
    fireEvent.click(within(held).getByRole("button", { name: "Sign again" }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    expect(passkeys.get).toHaveBeenCalledTimes(2);
    expect(sent("/api/ade/link/commands").map((c) => (c.body?.credential as { id: string }).id)).toEqual(["Ag"]);
    expect(screen.queryByRole("group", { name: "Not sent yet" })).toBeNull();
  });
});

// what the page says of every Approve it signs (the one text the session's client, its status and the manual say too)
const PAGE_TRUST = "An Approve on the page is your passkey's signature over what that page composed and shows you. If "
  + "the page, or the server or browser behind it, is not honest, it can show you one command while this session runs "
  + "the one it asked: the session checks that you signed its own question, never what you saw.";
const NO_BACKUP = "a key that cannot be backed up, such as a security key";
/** Authenticator data as a passkey signs it: its rp id's hash, its flags (user present and verified, and `more`) and
 *  its count, whose bytes carry the BE bit too, so only the flags' byte is read for it. */
const authData = (more: number) => {
  const d = new Uint8Array(37).fill(0x08, 33);
  d[32] = 0x05 | more;
  return d;
};
const BE = 0x08;              // the credential can be backed up

describe("a step the session's guard keeps for you at the machine, asked in a question", () => {
  const ALLOWED_AT = Date.UTC(2026, 10, 20, 9, 30) / 1000;
  const PUSH = "a push to a protected branch";
  // as the client writes it: the keys that count, and for a deployment or a push only one that cannot be backed up
  const bound = (kinds: string) => /a deployment|a push to a protected branch/.test(kinds);
  const allowedLine = (kinds = PUSH, backedUp = bound(kinds)) => "You allowed this session, at the machine at "
    + `2026-11-20 09:30 UTC, to take ${kinds} on your Approve from the page, signed with a key you named there`
    + `${backedUp ? " that cannot be backed up" : ""}: Approve lets it run this once, before your answer expires.`;
  const notAllowedLine = (kinds = PUSH) => `You have not allowed this session to take ${kinds} on your answer from the `
    + "page: an Approve here will not let it run.";
  /** A question as the session's client posts one with its step: its words, a blank line, then the step's block (by
   *  default a push to a protected branch, which the owner allowed). */
  const ask = (last = allowedLine(), kinds = PUSH, command = "git push origin release:main") => [
    "May I push the release branch to main?", "", `The step, as it will run (kept for you at the machine: ${kinds}):`,
    command, "in /Users/o/code/demo", last].join("\n");
  const question = (text: string): LinkEvent => ({ seq: 12, time: new Date().toISOString(), kind: "question",
    from: "MacBook Pro", to: "owner", ref: null, text });
  /** What the link reports of the owner's allowance: its kinds, the keys whose Approve counts, and when it was given
   *  (by default in the minute the step's last line names). */
  const take = (kinds: string[], keys: string[], at = ALLOWED_AT + 17) => ({ kinds, keys, at });
  const lets = (who: string, kinds = PUSH) => "Approve lets MacBook Pro run this command once, in that folder, before "
    + `your answer expires, if you sign it with ${who}: as it said when it asked, you allowed it at the machine, on `
    + `${dayTime(ALLOWED_AT)}, to take ${kinds} on your Approve from the page.`;
  const mayNot = (kinds = PUSH) => "When MacBook Pro asked, it said you had allowed it, at the machine on "
    + `${dayTime(ALLOWED_AT)}, to take ${kinds} on your Approve from the page; but what it reports now does not show `
    + "that allowance still covering this, so an Approve here may not let it run.";
  const keeps = (kinds = PUSH) => "MacBook Pro keeps this step for you at the machine: an Approve here will not let "
    + `it run. As it said when it asked, you had not allowed it to take ${kinds} on your Approve from the page. You `
    + "can allow that only at the machine, in a terminal of your own (pin --phone-take), and only for what it asks "
    + "after that.";
  // the account's passkeys: Touch ID bound to this device, iPhone a synced passkey, YubiKey a hardware key; each by
  // its fingerprint and, in encryption's view, its credential id ("Ag", "AQ", "Aw"); the session pinned all three
  const TOUCH = "1".repeat(32), PHONE = "2".repeat(32), KEY = "3".repeat(32), GONE = "4".repeat(32);
  const mine = [
    { id: "p1", name: "Touch ID", fingerprint: TOUCH, created_at: T - 7200, tier: "device", kind: "device" },
    { id: "p2", name: "iPhone", fingerprint: PHONE, created_at: T - 60, tier: "device", kind: "synced" },
    { id: "p3", name: "YubiKey", fingerprint: KEY, created_at: T - 9000, tier: "hardware", kind: "hardware" },
  ];
  const keyRoutes = (high = false): Record<string, Route> => ({
    ...SIGNS,
    "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: high }],
    "GET /api/ade/e2e": () => [200, {
      links: [{ link_id: "L1", name: "MacBook Pro", key_id: null, last_required_at: null, pins: [TOUCH, PHONE, KEY] }],
      passkeys: [{ id: "p1", credential_id: "Ag", name: "Touch ID", tier: "device", created_at: T - 7200 },
                 { id: "p2", credential_id: "AQ", name: "iPhone", tier: "device", created_at: T - 60 },
                 { id: "p3", credential_id: "Aw", name: "YubiKey", tier: "hardware", created_at: T - 9000 }] }],
  });
  /** Approve pressed: what its dialog says it lets the session do, once the account's passkeys are read (or found
   *  unreadable). */
  const approving = async (said: string) => {
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const note = screen.getByRole("note", { name: "What an Approve lets it do" });
    await vi.waitFor(() => expect(note.textContent).toBe(said));
    return note;
  };
  /** Approve pressed, and the keys its note names read (or found unreadable): the box that signs it. */
  const pressApprove = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const note = screen.getByRole("note", { name: "What an Approve lets it do" });
    await vi.waitFor(() => expect(note.textContent).not.toContain("a key your allowance names"));
  };
  /** The approval reviewed and signed with the passkey `id`, its authenticator data `auth`. */
  const signed = async (id: string, auth = new Uint8Array([2])) => {
    fireEvent.click(screen.getByRole("button", { name: /Review the approval/ }));
    const sign = await screen.findByRole("button", { name: /Sign with your passkey/ });
    passkeys.id = id;
    passkeys.auth = auth;
    fireEvent.click(sign);
  };
  const credentials = () => sent("/api/ade/link/commands").map((c) => (c.body?.credential as { id: string }).id);

  it("shows the step's command and folder as a step, with Approve beside Answer", () => {
    serve({});
    listing(link());
    render(<Question q={question(ask())} linkId="L1" />);
    const step = screen.getByRole("group", { name: "The step" });
    expect(within(step).getByLabelText("Its command").textContent).toBe("git push origin release:main");
    expect(step.textContent).toContain("kept for you at the machine: a push to a protected branch");
    expect(step.textContent).toContain("in /Users/o/code/demo");
    expect(screen.getByText("May I push the release branch to main?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Answer/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  // a forced push and a recursive or forced delete stay the owner's at the machine: no step of them is taken from the
  // page, so a block that names them is none the page reads
  it("reads no step from a block that names a forced push or a delete, and takes no Approve for it", async () => {
    serve(SIGNS);
    listing(link({ phone_take: take(["push"], [TOUCH]) }));
    const blocks = [["a forced push", "git push --force origin release:main"],
      ["a recursive or forced delete outside the working folder", "rm -rf /tmp/build"],
      ["a push to a protected branch, a forced push", "git push --force origin release:main"]];
    for (const [i, [kinds, command]] of blocks.entries()) {
      render(<Question q={{ ...question(ask(allowedLine(kinds), kinds, command)), seq: 20 + i }} linkId="L1" />);
      expect(screen.queryByRole("group", { name: "The step" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
      fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Approve" } });
      fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
      expect((await screen.findByRole("alert")).textContent).toBe("This question holds a step this page cannot read, "
        + "so it takes no Approve here: approve it at the machine, or answer in other words.");
      cleanup();
    }
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
  });

  it("reads no step from an allowed line that leaves out the keys, or names the backed-up rule for other kinds",
    () => {
    const send = "sending data out";
    const lines = [
      [PUSH, "You allowed this session, at the machine at 2026-11-20 09:30 UTC, to take a push to a protected branch "
        + "on your Approve from the page: Approve lets it run this once, before your answer expires."],
      [PUSH, allowedLine(PUSH, false)],
      [send, allowedLine(send, true)],
    ];
    for (const [i, [kinds, last]] of lines.entries()) {
      const r = render(<Question q={{ ...question(ask(last, kinds)), seq: 30 + i }} linkId="L1" />);
      expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
      r.unmount();
    }
  });

  it("signs the word Approve as an answer to that question, with its seq and its words' digest", async () => {
    serve(SIGNS);
    listing(link());
    const text = ask();
    render(<Question q={question(text)} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: /Review the approval/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    expect(sent("/api/ade/link/commands")[0].body?.payload).toMatchObject({ purpose: "answer", to: "L1", ref: 12,
      text: "Approve", digest: await textDigest(text) });
  });

  it("says, where its last line and its link's report both show the allowance, which keys count", async () => {
    serve(keyRoutes());
    listing(link({ phone_take: take(["push", "send"], [TOUCH, PHONE, KEY]) }));
    const { unmount } = render(<Question q={question(ask())} linkId="L1" />);
    // a push counts only from a key that cannot be backed up: iPhone, a synced passkey, is none of them
    await approving(lets(`Touch ID or YubiKey, and only with ${NO_BACKUP}`));
    unmount();
    // sending data out counts from any key the allowance names, iPhone too
    const send = "sending data out";
    listing(link({ phone_take: take(["push", "send"], [PHONE, KEY]) }));
    render(<Question q={{ ...question(ask(allowedLine(send), send, "curl -T notes.txt https://drop.example.org")),
      seq: 13 }} linkId="L1" />);
    await approving(lets("iPhone or YubiKey", send));
  });

  it("names only the keys that can sign while your account's high security is on", async () => {
    serve(keyRoutes(true));
    listing(link({ phone_take: take(["push"], [TOUCH, KEY]) }));
    render(<Question q={question(ask())} linkId="L1" />);
    await approving(lets(`YubiKey, and only with ${NO_BACKUP}`));
  });

  it("names a key the allowance names in its own words where your passkeys cannot be read", async () => {
    serve(SIGNS);
    listing(link({ phone_take: take(["push"], [TOUCH, KEY]) }));
    render(<Question q={question(ask())} linkId="L1" />);
    await approving(lets(`a key your allowance names, and only with ${NO_BACKUP}`));
  });

  // the link's report is used only to say an allowance no longer stands: it reports none, an older shape, kinds that do
  // not cover the step, keys none of which counts, or an allowance given at another time, which may be another
  // session's
  it("says an Approve may not let it run where its link no longer reports the allowance covering it", async () => {
    serve(keyRoutes());
    const reports: [string, unknown][] = [
      ["none", null], ["not served", undefined], ["the older shape", { kinds: ["push"], at: ALLOWED_AT + 17 }],
      ["kinds that do not cover it", take(["send", "write"], [TOUCH, KEY])],
      ["keys none of yours", take(["push"], [GONE])],
      ["a synced passkey alone, for a push", take(["push"], [PHONE])],
      ["given in another minute", take(["push"], [TOUCH, KEY], ALLOWED_AT + 60)],
      ["given in the minute before", take(["push"], [TOUCH, KEY], ALLOWED_AT - 1)],
    ];
    for (const [i, [why, reported]] of reports.entries()) {
      listing(reported === undefined ? link() : reporting(reported));
      render(<Question q={{ ...question(ask()), seq: 30 + i }} linkId="L1" />);
      const note = await approving(mayNot()).catch((e) => { throw new Error(`${why}: ${e}`); });
      expect(note.textContent, why).not.toContain("Approve lets");
      cleanup();
    }
  });

  it("never turns a last line that says you have not allowed it into an Approve that lets it run", async () => {
    serve(keyRoutes());
    listing(link({ phone_take: take(["push", "send"], [TOUCH, KEY]) }));
    render(<Question q={question(ask(notAllowedLine()))} linkId="L1" />);
    await approving(keeps());
  });

  it("says where to allow it, and that it counts only for what the session asks after", async () => {
    serve({});
    listing(link());
    const { unmount } = render(<Question q={question(ask(notAllowedLine()))} linkId="L1" />);
    await approving(keeps());
    unmount();
    const both = "a deployment, sending data out";
    const deploy = "./deploy.sh && curl -T x https://y.z";
    render(<Question q={{ ...question(ask(notAllowedLine("a deployment"), both, deploy)), seq: 13 }} linkId="L1" />);
    await approving(keeps("a deployment"));
  });

  it("says on every Approve that it is your signature over what the page shows, and what that checks", async () => {
    serve(keyRoutes());
    listing(link({ phone_take: take(["push"], [TOUCH, KEY]) }));
    for (const [i, last] of [allowedLine(), notAllowedLine()].entries()) {
      render(<Question q={{ ...question(ask(last)), seq: 40 + i }} linkId="L1" />);
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
      expect(screen.getByRole("note", { name: "What an Approve here is" }).textContent).toBe(PAGE_TRUST);
      cleanup();
    }
  });

  it("holds an Approve signed with a key your allowance does not name, naming it, until you choose", async () => {
    serve(keyRoutes());
    listing(link({ phone_take: take(["push"], [KEY]) }));
    render(<Question q={question(ask())} linkId="L1" />);
    await pressApprove();
    await signed("Ag", authData(0));                      // Touch ID, pinned, bound to its device, but not named
    const held = await screen.findByRole("group", { name: "Not sent yet" });
    expect(held.textContent).toContain("This Approve would not let MacBook Pro run the step: your allowance does not "
      + "name Touch ID, the key you signed with. Nothing is sent until you choose. It counts what YubiKey signs.");
    expect(within(held).getByRole("button", { name: "Send anyway" })).toBeTruthy();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    // Sign again, with the key it names, which cannot be backed up (its count's bytes carry the BE bit: unread): sent
    fireEvent.click(within(held).getByRole("button", { name: "Sign again" }));
    passkeys.id = "Aw";
    passkeys.auth = authData(0);
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(credentials()).toEqual(["Aw"]));
    expect(passkeys.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("group", { name: "Not sent yet" })).toBeNull();
  });

  it("holds an Approve of a push signed with a key that can be backed up, and only that", async () => {
    serve(keyRoutes());
    listing(link({ phone_take: take(["push", "send"], [TOUCH, PHONE, KEY]) }));
    const first = render(<Question q={question(ask())} linkId="L1" />);
    await pressApprove();
    await signed("Ag", authData(BE));                     // Touch ID, named, but its signature says it can be backed up
    const held = await screen.findByRole("group", { name: "Not sent yet" });
    expect(held.textContent).toContain("This Approve would not let MacBook Pro run the step: Touch ID, the key you "
      + `signed with, can be backed up, and for a push to a protected branch only ${NO_BACKUP} counts. Nothing is sent `
      + "until you choose.");
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    fireEvent.click(within(held).getByRole("button", { name: "Send anyway" }));
    await vi.waitFor(() => expect(credentials()).toEqual(["Ag"]));
    expect(passkeys.get).toHaveBeenCalledTimes(1);
    first.unmount();
    // sending data out, signed with iPhone, named, which can be backed up: that counts for it, so it goes at once
    const send = "sending data out";
    const curl = "curl -T notes.txt https://d.example";
    const second = render(<Question q={{ ...question(ask(allowedLine(send), send, curl)), seq: 13 }} linkId="L1" />);
    await approving(lets("Touch ID, iPhone or YubiKey", send));
    await signed("AQ", authData(BE));
    await vi.waitFor(() => expect(credentials()).toEqual(["Ag", "AQ"]));
    second.unmount();
    // where its link does not report that allowance, nothing is held for its keys: the note said it may not run
    listing(link({ phone_take: take(["send"], [PHONE]) }));
    render(<Question q={{ ...question(ask()), seq: 14 }} linkId="L1" />);
    await approving(mayNot());
    await signed("Ag", authData(BE));
    await vi.waitFor(() => expect(credentials()).toEqual(["Ag", "AQ", "Ag"]));
    expect(screen.queryByRole("group", { name: "Not sent yet" })).toBeNull();
  });

  it("refuses Approve written as an answer to a step, and says so where it cannot read the step", async () => {
    serve(SIGNS);
    listing(link());
    // each time a question of its own: an answer typed and not sent is kept as its question's draft
    const written = async (text: string, seq: number) => {
      render(<Question q={{ ...question(text), seq }} linkId="L1" />);
      fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
      fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Approve" } });
      fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
      return (await screen.findByRole("alert")).textContent;
    };
    expect(await written(ask(), 12)).toBe("To approve this step, use Approve: it says first what an Approve lets the "
      + "session do.");
    cleanup();
    // its last line in no form the session's client writes: no step is shown, and no Approve is offered
    expect(await written(ask("Approve this, please."), 13)).toBe("This question holds a step this page cannot read, "
      + "so it takes no Approve here: approve it at the machine, or answer in other words.");
    expect(screen.queryByRole("group", { name: "The step" })).toBeNull();
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
    expect(passkeys.get).not.toHaveBeenCalled();
  });

  it("shows the step on its row in the conversation, with Approve beside Answer", async () => {
    const q = { ...question(ask()), link_id: "L1" };
    serve({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [q] }] });
    useStore.setState({ links: { links: [link()], questions: [q], public_url: "https://pp.example" } });
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const step = await screen.findByRole("group", { name: "The step" });
    expect(within(step).getByLabelText("Its command").textContent).toBe("git push origin release:main");
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("in the apps, shows the step and offers no Approve: nothing is signed there", async () => {
    vi.resetModules();
    vi.doMock("../api", async (original) => ({ ...(await original<typeof import("../api")>()), APP: true }));
    try {
      const { useStore: store } = await import("../store");
      store.setState({ user: { id: "u", email: "o@example.org", can_sign_in_again: true } });
      const { Question: InApp } = await import("./Channel");
      serve({});
      render(<InApp q={question(ask())} linkId="L1" />);
      expect(screen.getByRole("group", { name: "The step" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
      expect(screen.queryByRole("button", { name: /Answer/ })).toBeNull();
    } finally {
      vi.doUnmock("../api");
      vi.resetModules();
    }
  });
});

describe("what a link may take on your Approve from the page", () => {
  const TOUCH = "1".repeat(32), PHONE = "2".repeat(32), GONE = "4".repeat(32);
  const mine = [
    { id: "p1", name: "Touch ID", fingerprint: TOUCH, created_at: T - 7200, tier: "device", kind: "device" },
    { id: "p2", name: "iPhone", fingerprint: PHONE, created_at: T - 60, tier: "device", kind: "synced" },
  ];
  const card = (phone_take: unknown) =>
    render(<LinkCard l={reporting(phone_take)} questions={[]} onTicket={() => {}} />);
  const allowed = (kinds: string) => `You allowed it at the machine, on ${dayTime(T - 3600)}, to take ${kinds} on your `
    + "Approve from the page";

  it("says on the link's card which steps it may take on your Approve from the page, since when, and by which keys",
    async () => {
      serve({ "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: false }] });
      const { unmount } = card({ kinds: ["deploy", "send"], keys: [TOUCH, PHONE], at: T - 3600 });
      expect(await screen.findByText(`${allowed("a deployment or sending data out")}, signed with Touch ID or iPhone. `
        + `For a deployment, only ${NO_BACKUP} counts.`)).toBeTruthy();
      unmount();
      card({ kinds: ["send", "write"], keys: [PHONE], at: T - 3600 });
      expect(await screen.findByText(`${allowed("sending data out or a write outside the working folder")}, signed `
        + "with iPhone.")).toBeTruthy();
    });

  // what stays open, under the allowance: the link reads files a session's own programs can still write
  const GUARD_OPEN = "What stays open: its guard holds the session's shell and file-tool calls that write the files "
    + "the link reads to let a step through, judged by their words and paths, but a program the session starts, "
    + "another tool or another path to those files can still write them, and the manual says what else stays open.";

  it("says under the allowance what stays open, a program the session starts writing what its guard reads, and not "
    + "without one", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: false }] });
    const { unmount } = card({ kinds: ["send"], keys: [TOUCH], at: T - 3600 });
    expect(await screen.findByText(GUARD_OPEN)).toBeTruthy();
    unmount();
    const other = card({ kinds: ["send"], keys: [GONE], at: T - 3600 });
    expect(await screen.findByText(GUARD_OPEN)).toBeTruthy();
    other.unmount();
    card({ kinds: ["force-push"], keys: [TOUCH], at: T - 3600 });
    await Promise.resolve();
    expect(screen.queryByText(GUARD_OPEN)).toBeNull();
  });

  it("says so on the card where none of the keys it names is a passkey of yours, or they cannot be read", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: false }] });
    const { unmount } = card({ kinds: ["send"], keys: [GONE], at: T - 3600 });
    expect(await screen.findByText(`${allowed("sending data out")}, but none of the keys your allowance names is a `
      + "passkey of yours that can sign here.")).toBeTruthy();
    unmount();
    serve({});
    card({ kinds: ["send"], keys: [TOUCH], at: T - 3600 });
    expect(await screen.findByText(`${allowed("sending data out")}, signed with a key your allowance names.`))
      .toBeTruthy();
  });

  it("says nothing on the card of a report in no shape the session sends", async () => {
    serve({ "GET /api/account/passkeys": () => [200, { passkeys: mine, high_security: false }] });
    for (const reported of [{ kinds: ["deploy", "send"], at: T - 3600 },
      { kinds: ["force-push"], keys: [TOUCH], at: T - 3600 }, { kinds: ["send"], keys: [], at: T - 3600 },
      { kinds: ["send"], keys: ["AB".repeat(16)], at: T - 3600 },
      { kinds: ["send"], keys: [TOUCH, TOUCH], at: T - 3600 },
      { kinds: ["send"], keys: [TOUCH], at: T - 3600, session: "cc-1" }]) {
      const { unmount } = card(reported);
      await Promise.resolve();
      expect(screen.queryByText(/You allowed it at the machine/), JSON.stringify(reported)).toBeNull();
      unmount();
    }
  });

  it("says on a prompt the guard kept that it is kept for you at the machine, and that you allowed it", () => {
    serve({});
    const words = JSON.stringify({ tool: "Bash", input: { command: "git push origin main" }, session_id: "cc-1",
      agent_id: null, deadline: T + 1800, kept: ["a push to a protected branch", "sending data out"] });
    const p = { seq: 7, time: new Date().toISOString(), kind: "prompt", from: "MacBook Pro", to: "owner", ref: null,
      text: words, link_id: "L1", deadline_at: T + 1800, look: false };
    render(<PromptCard p={p} name="MacBook Pro" />);
    expect(screen.getByRole("note", { name: "Kept at the machine" }).textContent).toBe("Kept for you at the "
      + "machine: a push to a protected branch, sending data out. You allowed this session to take it on your Approve "
      + "from the page; your Approve allows it only while that allowance stands.");
  });

  it("says nothing of an Approve from the page on a prompt kept for a forced push or a delete", () => {
    serve({});
    for (const kept of [["a forced push"], ["a push to a protected branch", "a forced push"],
      ["a recursive or forced delete outside the working folder"]]) {
      const words = JSON.stringify({ tool: "Bash", input: { command: "git push --force origin main" },
        session_id: "cc-1", agent_id: null, deadline: T + 1800, kept });
      const p = { seq: 7, time: new Date().toISOString(), kind: "prompt", from: "MacBook Pro", to: "owner", ref: null,
        text: words, link_id: "L1", deadline_at: T + 1800, look: false };
      const { unmount } = render(<PromptCard p={p} name="MacBook Pro" />);
      expect(screen.queryByRole("note", { name: "Kept at the machine" }), JSON.stringify(kept)).toBeNull();
      unmount();
    }
  });

  it("shows the folder a kept prompt runs in, as its text names it, and none on another prompt", () => {
    serve({});
    const prompt = (over: Record<string, unknown>) => ({ seq: 7, time: new Date().toISOString(), kind: "prompt",
      from: "MacBook Pro", to: "owner", ref: null, link_id: "L1", deadline_at: T + 1800, look: false,
      text: JSON.stringify({ tool: "Bash", input: { command: "git push origin main" }, session_id: "cc-1",
        agent_id: null, deadline: T + 1800, ...over }) });
    const push = ["a push to a protected branch"];
    const { unmount } = render(<PromptCard p={prompt({ kept: push, cwd: "/Users/o/work" })} name="MacBook Pro" />);
    expect(screen.getByRole("note", { name: "Its folder" }).textContent).toBe("in /Users/o/work");
    unmount();
    // a prompt not kept names no folder the page shows, whatever its text holds; nor a kept one whose text names none
    for (const over of [{ cwd: "/Users/o/work" }, { kept: push }, { kept: push, cwd: "" }]) {
      const again = render(<PromptCard p={prompt(over)} name="MacBook Pro" />);
      expect(screen.queryByRole("note", { name: "Its folder" }), JSON.stringify(over)).toBeNull();
      again.unmount();
    }
  });
});
