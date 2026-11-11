import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { channelAlerts, type HistorySession, type LinkAsk, type LinkEvent, type LinkMove, type LinkSessions, type LinkView } from "../api";
import { canonicalJson, textDigest } from "../passkey";
import { ago, askLines, commandProblem, Commands, conversation, latestSaid, pairLine, stateWord, visible, waitWords } from "./Channel";
// an idle session picks it up within seconds only while its watcher runs
const QUEUED_SAID = "An idle session with its watcher running picks it up within seconds; a busy one after its current step.";
// The session may be heard by its live rounds while no watcher of its runs: the words name the watcher
const QUEUED_UNHEARD = "Its session's watcher has not been heard from in the last 10 minutes: "
  + "the session picks this up at its next step, or when its watcher is back.";
const STEP_SAID = 'A Bash step names the program its command begins with, never what else it runs: "grep -l x *.yml | xargs rm -f" shows as "Bash: grep". Where it cannot tell the program, as for a command that begins by setting a variable, it shows Bash alone.';
import { clock, dayTime } from "../when";
import { useStore } from "../store";

// The payload a page signs, a server checks and a session verifies offline must be the same bytes in all three: the
// same vectors the server's and the session client's tests read (open_edition/tests/fixtures).
const VECTORS: { name: string; value: unknown; canonical: string }[] = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "..", "tests", "fixtures", "canonical-vectors.json"), "utf8"));

// "sha256:" and the hex of the UTF-8 bytes, as Python's hashlib gives it for the same text
const QUESTION = "Q-1: which cap?\nper user, or per org \u{2014} caf\u{e9} \u{1F600}";
const QUESTION_DIGEST = "sha256:432e749c3dc1c3fd89f9b15f38e1831271cfbe5a94c755f6684720c0af664eb7";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

describe("a payload's canonical form", () => {
  it("is the one the server and the session's client compute, vector by vector", () => {
    expect(VECTORS.length).toBeGreaterThanOrEqual(10);
    for (const v of VECTORS) expect(canonicalJson(v.value), v.name).toBe(v.canonical);
  });

  it("refuses what the server or the session would read another way", () => {
    for (const bad of ["\ud800", "a\udc00b", 1.5, 2 ** 53, Number.NaN, undefined, () => 1]) {
      expect(() => canonicalJson({ text: bad })).toThrow();
    }
    expect(canonicalJson({ n: 2 ** 53 - 1 })).toBe('{"n":9007199254740991}');
  });

  it("gives an answer the digest of the question's own words", async () => {
    expect(await textDigest(QUESTION)).toBe(QUESTION_DIGEST);
  });
});

describe("a command the owner signs", () => {
  type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
  type Answer = [number, unknown];
  /** What the server answers a request: at once, or when the test says (a promise it settles). */
  type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
  const SERVER_TIME = 1_790_000_000;
  const nonce = (b: Record<string, unknown> | undefined): Answer => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: SERVER_TIME }];
  const routes = (more: Record<string, Route> = {}): Record<string, Route> => ({
    "POST /api/ade/link/nonce": nonce,
    "POST /api/ade/link/commands": () => [200, { seq: 9, expires_at: SERVER_TIME + 3600 }],
    "GET /api/ade/link/sessions": () => [200, { links: [], questions: [], public_url: "https://pp.example" }],
    ...more,
  });
  const order: string[] = [];
  let calls: Call[] = [];
  const serve = (r: Record<string, Route>) => {
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method || "GET";
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path: String(url), method, body });
      order.push(`${method} ${url}`);
      const route = r[`${method} ${url}`];
      const [status, data] = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }];
      return new Response(JSON.stringify(data), { status });
    }));
  };
  /** The ADE's timeline of L1 as `all()` holds it at each read: its newest 100, or the 100 before a seq, newest first,
   *  `&session=s1` or not (link.py's timeline). A read before a seq is logged in `older`, its answer as the read found
   *  it, and `hold(before)` may make that answer wait, or fail with the status it settles to; anything else as `routes`
   *  answers it. */
  const serveTimeline = (all: () => LinkEvent[], older: string[], more: Record<string, Route> = {},
                         hold: (before: number) => Promise<number | void> | undefined = () => undefined) => {
    serve(routes(more));
    const rest = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      const m = /^\/api\/ade\/link\/timeline\/L1\?limit=100(?:&before=(\d+))?(?:&session=s1)?$/.exec(String(url));
      if (!m || (init.method ?? "GET") !== "GET") return rest(url, init);
      const before = m[1] === undefined ? Infinity : Number(m[1]);
      const events = all().filter((e) => e.seq < before).sort((a, b) => b.seq - a.seq).slice(0, 100);
      if (m[1] !== undefined) {
        older.push(m[1]);
        const status = await hold(before);
        if (status) return new Response(JSON.stringify({ detail: "failed" }), { status });
      }
      return new Response(JSON.stringify({ events }), { status: 200 });
    }));
  };
  const seqs = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => from + i);
  const bytes = (n: number) => new Uint8Array([n]).buffer;
  const credentials = {
    get: vi.fn(async (_opts: { publicKey: { challenge: ArrayBuffer | Uint8Array; rpId: string; userVerification: string; allowCredentials: unknown[] } }) => {
      order.push("get");
      return { id: "AQ", type: "public-key", response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: bytes(4) } };
    }),
  };
  const fresh = async (passkeys = true) => {
    vi.resetModules();
    if (passkeys) {
      vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
      Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
    }
    const { useStore: store } = await import("../store");
    store.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    return import("./Channel");
  };
  afterEach(() => {
    order.length = 0;
    credentials.get.mockClear();
    delete (navigator as unknown as { credentials?: unknown }).credentials;
    vi.resetModules();
  });
  const sha256 = async (text: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  // a step's challenge as a linked session checks it, written from its words: sha256("proofpane-step/1" 0 canonical)
  const stepped = (canonical: string) => sha256(`proofpane-step/1\0${canonical}`);
  const sent = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
  const link = (over: Partial<LinkView> = {}): LinkView => ({
    id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
    last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
    events: 0, last_event: null, commands: [], stopped_at: null, ...over,
  });

  // A card shows what its checkpoint says, its next and open as they are
  // where they are text; and with no checkpoint, the newest step its session posted, or its live view's newest line,
  // marked as that, never an empty box
  it("shows a checkpoint's next and open as they are where they are text", async () => {
    const { LinkCard } = await fresh();
    const cp = { doing: "the fix", next: "write the cases\nthen the change", open: "the pin" };
    render(<LinkCard l={link({ checkpoint: cp })} questions={[]} onTicket={() => {}} />);
    expect(screen.getByText(/write the cases/).textContent).toBe("write the cases\nthen the change");
    expect(screen.getByText(/write the cases/).className).toContain("whitespace-pre-wrap");    // its line break shown
    expect(screen.getByText(/^open: /).textContent).toBe("open: the pin");
  });

  it("shows what a checkpoint says it is doing as it is, its JSON where it is not text", async () => {
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ checkpoint: { doing: { step: 2, of: 5 } } })} questions={[]} onTicket={() => {}} />);
    expect(screen.getByText('{"step":2,"of":5}')).toBeTruthy();
  });

  it("says a checkpoint names nothing it is doing, rather than that there is none", async () => {
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ checkpoint: { next: ["write the cases"] } })} questions={[]} onTicket={() => {}} />);
    expect(screen.queryByText("(no checkpoint yet)")).toBeNull();
    expect(screen.getByText("(its checkpoint says nothing of what it is doing)")).toBeTruthy();
  });

  it("shows, with no checkpoint, its newest step or its live view's newest line, marked so", async () => {
    const { LinkCard } = await fresh();
    const step: LinkEvent = { seq: 9, time: "2026-11-07T12:00:00Z", kind: "step", from: "mac", to: "all", ref: null,
      text: "built the image" };
    const live: LinkEvent = { seq: 8, time: "2026-11-07T12:01:00Z", kind: "note", from: "mac", to: "owner", ref: null,
      mirror: "live", text: JSON.stringify({ items: [["owner", "go"], ["session", "reading the card"]] }) };
    const card = (over: Partial<LinkView>) => render(<LinkCard l={link(over)} questions={[]} onTicket={() => {}} />);
    let shown = card({ last_step: step, last_live: live });
    expect(screen.getByLabelText("No checkpoint yet; its newest step").textContent).toContain("built the image");
    expect(screen.queryByText("(no checkpoint yet)")).toBeNull();
    shown.unmount();
    // marked in words the owner reads, not in a label alone; and its newest event, the same step, said once
    shown = card({ last_step: step, last_live: live, last_event: step });
    expect(screen.getByLabelText("No checkpoint yet; its newest step").textContent)
      .toContain("No checkpoint yet; its newest step: ");
    expect(screen.queryAllByText(/built the image/)).toHaveLength(1);
    shown.unmount();
    shown = card({ last_step: null, last_live: live });
    expect(screen.getByLabelText("No checkpoint yet; its live view's newest line").textContent)
      .toContain("reading the card");
    shown.unmount();
    card({ last_step: null, last_live: null });
    expect(screen.getByText("(no checkpoint yet)")).toBeTruthy();
  });

  const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent => ({ seq, time: "2026-11-07T12:00:00Z", kind: "note",
    from: "mac", to: "owner", ref: null, text: "", ...over });
  const round = (seq: number, said: string, over: Partial<LinkEvent> = {}) =>
    ev(seq, { mirror: "live", text: JSON.stringify({ items: [["session", said]] }), ...over });

  it("stands in with no round a Stop posted again whole, so its words are said once", async () => {
    const { LinkCard } = await fresh();
    const stop = ev(9, { mirror: "session", text: "final words" });
    render(<LinkCard l={link({ last_live: round(8, "final words", { posted_again: true }), last_event: stop })}
      questions={[]} onTicket={() => {}} />);
    expect(screen.queryAllByText(/final words/)).toHaveLength(1);
    expect(screen.queryByLabelText(/^No checkpoint yet/)).toBeNull();
  });

  it("shows what a checkpoint waits on as it is: a list's items, anything else not text as its JSON", async () => {
    const { LinkCard } = await fresh();
    const card = (waiting_on: unknown) =>
      render(<LinkCard l={link({ checkpoint: { doing: "the fix", waiting_on } })} questions={[]}
        onTicket={() => {}} />);
    const shown = card(["the owner's go", "CI"]);
    expect(screen.queryByText(/^waiting on: /)?.textContent).toBe("waiting on: the owner's go \u00b7 CI");
    shown.unmount();
    card({ who: "owner" });
    expect(screen.queryByText(/^waiting on: /)?.textContent).toBe('waiting on: {"who":"owner"}');
  });

  it("stands in with the newer of its newest step and its live view's newest round", async () => {
    const { LinkCard } = await fresh();
    const step = ev(5, { kind: "step", to: "all", text: "built the image" });
    render(<LinkCard l={link({ last_step: step, last_live: round(9, "reading the card") })} questions={[]}
      onTicket={() => {}} />);
    expect(screen.queryByLabelText("No checkpoint yet; its live view's newest line")).not.toBeNull();
    expect(screen.getByLabelText("No checkpoint yet; its live view's newest line").textContent)
      .toContain("No checkpoint yet; its live view's newest line: Said: reading the card");   // its item, as a line
    expect(screen.queryByText(/built the image/)).toBeNull();
  });

  it("says a step standing in by its words, its mark saying it is a step", async () => {
    const { LinkCard } = await fresh();
    const step = ev(5, { kind: "step", to: "all", text: "built the image" });
    render(<LinkCard l={link({ last_step: step })} questions={[]} onTicket={() => {}} />);
    const said = screen.getByLabelText("No checkpoint yet; its newest step").textContent;
    expect(said).toContain("No checkpoint yet; its newest step: built the image");
    expect(said).not.toContain("Step:");
  });

  it("says how many more next steps and open items a checkpoint has than the card shows", async () => {
    const { LinkCard } = await fresh();
    const many = (n: number) => Array.from({ length: n }, (_, i) => `item ${i + 1}`);
    const card = (cp: Record<string, unknown>) =>
      render(<LinkCard l={link({ checkpoint: { doing: "the fix", ...cp } })} questions={[]} onTicket={() => {}} />);
    const shown = card({ next: many(7) });
    expect(screen.queryByText("and 2 more")).not.toBeNull();
    expect(screen.queryByText("item 6")).toBeNull();
    shown.unmount();
    card({ open: many(22) });
    expect(screen.getByText(/^open: /).textContent).toMatch(/ \u00b7 item 20 \u00b7 and 2 more$/);
  });

  it("takes a checkpoint's blank text for nothing", async () => {
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ checkpoint: { doing: "  \n ", open: " ", waiting_on: "  " } })} questions={[]}
      onTicket={() => {}} />);
    expect(screen.queryByText("(its checkpoint says nothing of what it is doing)")).not.toBeNull();
    expect(screen.queryByText(/^open: /)).toBeNull();
    expect(screen.queryByText(/^waiting on: /)).toBeNull();
  });

  it("shows a checkpoint's next and open that are neither text nor a list as their JSON", async () => {
    const { LinkCard } = await fresh();
    render(<LinkCard l={link({ checkpoint: { doing: "the fix", next: { a: 1 }, open: 3 } })} questions={[]}
      onTicket={() => {}} />);
    expect(screen.getByText('{"a":1}')).toBeTruthy();
    expect(screen.getByText(/^open: /).textContent).toBe("open: 3");
  });

  it("shows a checkpoint where there is one, and no stand-in for it, whatever the session posted", async () => {
    const { LinkCard } = await fresh();
    const step: LinkEvent = { seq: 7, time: "2026-11-07T12:00:00Z", kind: "step", from: "mac", to: "all", ref: null,
      text: "built the image" };
    render(<LinkCard l={link({ checkpoint: { doing: "the fix" }, last_step: step })} questions={[]}
      onTicket={() => {}} />);
    expect(screen.getByText("the fix")).toBeTruthy();
    expect(screen.queryByLabelText(/^No checkpoint yet/)).toBeNull();
  });

  it("signs exactly the words reviewed, and the click on Sign reaches the passkey prompt before anything else", async () => {
    serve(routes());
    const { SignBox } = await fresh();
    const done = vi.fn(async () => {});
    render(<SignBox purpose="instruction" to="L1" toName="mac" placeholder="What mac should do" submit="Review" onDone={done} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests\nthen push" } });
    fireEvent.change(screen.getByLabelText("Expires after"), { target: { value: "24" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    const head = (await screen.findByText(/^You are signing an instruction to mac \(link L1\), good until .+:$/)).textContent;
    expect(screen.getByText(/passkey's prompt does not show these words/)).toBeTruthy();
    expect(screen.getByText(/run the tests/).textContent).toBe("run the tests\nthen push");
    expect(sent("/api/ade/link/nonce").map((c) => c.body)).toEqual([{ purpose: "instruction" }]);
    order.length = 0;
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    expect(order[0]).toBe("get");                                // nothing fetched between the click and the prompt
    const [{ body }] = sent("/api/ade/link/commands");
    const payload = body?.payload as Record<string, unknown>;
    expect(payload).toMatchObject({ v: 2, deployment: "pp.example", purpose: "instruction", account: "u", nonce: "n-instruction",
      to: "L1", text: "run the tests\nthen push" });
    expect(Object.keys(payload).sort()).toEqual(["account", "created_at", "deployment", "expires_at", "id", "nonce", "purpose", "text", "to", "v"]);
    expect(payload.id).toMatch(/^[0-9a-f]{32}$/);
    expect(payload.created_at as number).toBeGreaterThanOrEqual(SERVER_TIME);                  // the server's clock, not this one's
    expect(payload.created_at as number).toBeLessThanOrEqual(SERVER_TIME + 2);
    expect((payload.expires_at as number) - (payload.created_at as number)).toBe(24 * 3600);
    expect(head).toBe(`You are signing an instruction to mac (link L1), good until ${dayTime(payload.expires_at as number)}:`);
    const asked = credentials.get.mock.calls[0][0].publicKey;
    expect(new Uint8Array(asked.challenge as ArrayBuffer)).toEqual(await stepped(canonicalJson(payload)));
    expect(asked.rpId).toBe("pp.example");
    expect(asked.userVerification).toBe("required");
    expect(asked.allowCredentials).toEqual([]);
    expect(body?.credential).toMatchObject({ id: "AQ", type: "public-key" });
  });

  it("asks the prompt for the hardware keys alone when the server names them (high security is on)", async () => {
    serve(routes({ "POST /api/ade/link/nonce": (b) => [200, { ...(nonce(b)[1] as object), credentials: ["AQ", "Ag"] }] }));
    const { SignBox } = await fresh();
    const done = vi.fn(async () => {});
    render(<SignBox purpose="instruction" to="L1" toName="mac" placeholder="What mac should do" submit="Review" onDone={done} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your hardware key/ }));
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    const asked = credentials.get.mock.calls[0][0].publicKey;
    expect(asked.allowCredentials).toHaveLength(2);
    expect((asked.allowCredentials as { type: string; id: Uint8Array }[]).map((c) => [c.type, Array.from(new Uint8Array(c.id as unknown as ArrayBuffer))]))
      .toEqual([["public-key", [1]], ["public-key", [2]]]);
  });

  it("dates a payload at the moment the server gave its nonce, however long the nonce took to arrive", async () => {
    serve(routes());
    await fresh();
    const { prepareSigned: prepare } = await import("../passkey");
    const real = Date.now;
    let calls = 0;
    vi.spyOn(Date, "now").mockImplementation(() => real() + (calls++ > 0 ? 120_000 : 0));   // two minutes asleep
    try {
      const p = await prepare("instruction", "L1", { text: "x" });
      expect(p.payload.created_at).toBe(SERVER_TIME);
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("dates nothing more than five minutes ahead of this device's clock: a nonce's time, or a resume after a stop the server dates ahead", async () => {
    const own = 1_800_000_000;                                    // this device's clock, in seconds
    let at = own;
    serve(routes({ "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: at }] }));
    await fresh();
    const { prepareSigned: prepare, SERVER_AHEAD, SERVER_AHEAD_WORDS } = await import("../passkey");
    vi.spyOn(Date, "now").mockReturnValue(own * 1000);
    try {
      at = own + SERVER_AHEAD + 1;
      await expect(prepare("instruction", "L1", { text: "x" })).rejects.toThrow(SERVER_AHEAD_WORDS);
      at = own + SERVER_AHEAD;                                    // as far ahead as the server's own skew allows
      expect((await prepare("instruction", "L1", { text: "x" })).payload.created_at).toBe(own + SERVER_AHEAD);
      at = own;
      await expect(prepare("resume", "L1", { after: own + SERVER_AHEAD })).rejects.toThrow(SERVER_AHEAD_WORDS);
      expect((await prepare("resume", "L1", { after: own + SERVER_AHEAD - 1 })).payload.created_at).toBe(own + SERVER_AHEAD);
      at = own + 0.5;
      await expect(prepare("instruction", "L1", { text: "x" })).rejects.toThrow("The server's time is not a time.");
      at = own - 86_400;                                          // behind: taken, it only shortens the command's life
      expect((await prepare("instruction", "L1", { text: "x" })).payload.created_at).toBe(own - 86_400);
    } finally {
      vi.restoreAllMocks();
    }
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("says so, with nothing to sign, when the server's clock runs more than five minutes ahead of this device's", async () => {
    serve(routes({ "POST /api/ade/link/nonce": (b) => [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: Math.floor(Date.now() / 1000) + 3600 }] }));
    const { SignBox } = await fresh();
    const { SERVER_AHEAD_WORDS } = await import("../passkey");
    render(<SignBox purpose="instruction" to="L1" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "go" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    expect((await screen.findByRole("alert")).textContent).toBe(SERVER_AHEAD_WORDS);
    expect(screen.queryByRole("button", { name: /Sign with your passkey/ })).toBeNull();
    expect(credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("keeps a command's life within what the server takes, whatever it is asked for", async () => {
    serve(routes());
    await fresh();
    const { prepareSigned: prepare } = await import("../passkey");
    for (const [asked, kept] of [[999_999, 86_400], [10, 60], [7_200, 7_200]]) {
      const p = await prepare("instruction", "L1", { text: "x", life: asked });
      expect((p.payload.expires_at as number) - (p.payload.created_at as number), String(asked)).toBe(kept);
    }
  });

  it("signs the composed form of what was typed, and shows that form", async () => {
    serve(routes());
    const { SignBox } = await fresh();
    const done = vi.fn(async () => {});
    render(<SignBox purpose="instruction" to="L1" placeholder="What mac should do" submit="Review" onDone={done} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "cafe\u{301}" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText(/^You are signing an instruction to the session \(link L1\), good until /);
    expect(screen.getByText(/caf/).textContent).toBe("caf\u{e9}");
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(done).toHaveBeenCalled());
    expect((sent("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>).text).toBe("caf\u{e9}");
  });

  it("says what the server would refuse before anything is signed or asked", async () => {
    serve(routes());
    const { SignBox } = await fresh();
    render(<SignBox purpose="instruction" to="L1" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText("Write what the session should do.");
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run \u{202e}it" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText(/U\+202E/);
    expect(calls).toEqual([]);
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("where passkeys do not work, says where commands are signed and offers nothing to sign", async () => {
    serve(routes());
    const { SignBox } = await fresh(false);
    render(<SignBox purpose="instruction" to="L1" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    expect(screen.getByText(/Commands are signed with one of your passkeys, which work only in a browser at/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Review/ })).toBeNull();
  });

  it("signs a Check's fixed words, with nothing to type", async () => {
    serve(routes());
    const { LinkCard, CHECK_TEXT } = await fresh();
    render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(screen.queryByLabelText("What mac should do")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Review the check/ }));
    await screen.findByText(/^You are signing an instruction to mac \(link L1\), good until /);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    expect((sent("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>).text).toBe(CHECK_TEXT);
  });

  it("withdraws a command no session has taken, and offers nothing for one taken", async () => {
    serve(routes({ "POST /api/ade/link/commands/5/withdraw": () => [200, { withdrawn_at: SERVER_TIME }] }));
    const { Commands } = await fresh();
    const cmd = (seq: number, state: LinkEvent["state"]): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null, text: `command ${seq}`, state });
    render(<Commands l={link({ commands: [cmd(5, "queued"), cmd(6, "taken")] })} />);
    const buttons = screen.getAllByRole("button", { name: "Withdraw" });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    await vi.waitFor(() => expect(sent("/api/ade/link/commands/5/withdraw")).toHaveLength(1));
    expect(sent("/api/ade/link/commands/6/withdraw")).toHaveLength(0);
  });

  it("stops on a second click, with no passkey", async () => {
    serve(routes({ "POST /api/ade/link/links/L1/stop": () => [200, { stopped_at: SERVER_TIME }] }));
    const { StopResume } = await fresh();
    render(<StopResume l={link()} />);
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    expect(sent("/api/ade/link/links/L1/stop")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Click again to stop it/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/links/L1/stop")).toHaveLength(1));
    expect(credentials.get).not.toHaveBeenCalled();
  });

  it("resumes with a signed payload made after the stop, good for ten minutes", async () => {
    const stoppedAt = SERVER_TIME + 500;                         // later than the server's clock says it is now
    serve(routes({ "POST /api/ade/link/links/L1/resume": () => [200, { resumed_at: stoppedAt + 1 }] }));
    const { StopResume } = await fresh();
    render(<StopResume l={link({ stopped_at: stoppedAt })} />);
    expect(screen.getByText(/Stopped .* ago \u{b7} stop sent, not yet acknowledged/u)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Resume/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the resume/ }));
    await screen.findByText(`You are signing a resume of mac (link L1), lifting its stop of ${dayTime(stoppedAt)}, good until ${dayTime(stoppedAt + 601)}.`);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/links/L1/resume")).toHaveLength(1));
    const payload = sent("/api/ade/link/links/L1/resume")[0].body?.payload as Record<string, unknown>;
    expect(payload).toMatchObject({ purpose: "resume", to: "L1", text: "", nonce: "n-resume", created_at: stoppedAt + 1, expires_at: stoppedAt + 601 });
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
  });

  it("answers a question with its seq and the digest of its words", async () => {
    serve(routes());
    const { Question } = await fresh();
    const q: LinkEvent = { seq: 12, time: "2026-10-03T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: QUESTION };
    render(<Question q={q} linkId="L1" />);
    fireEvent.click(screen.getByRole("button", { name: /Answer/ }));
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "per org" } });
    fireEvent.click(screen.getByRole("button", { name: /Review the answer/ }));
    await screen.findByText(/^You are signing an answer to question \[12\] from mac \(link L1\), good until .+:$/);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    const payload = sent("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>;
    expect(payload).toMatchObject({ purpose: "answer", to: "L1", text: "per org", ref: 12, digest: QUESTION_DIGEST });
  });

  // An open waiting to the owner is on its session's card, dismissed as a
  // question is, and counted among what the channel holds for him
  it("shows an open waiting on its session's card, and Dismiss closes it as a question is closed", async () => {
    serve(routes());
    const { LinkCard } = await fresh();
    const w: LinkEvent = { seq: 9, time: "2026-11-08T00:00:00Z", kind: "waiting", from: "mac", to: "owner", ref: null,
      text: "Waiting for your word on the release." };
    render(<LinkCard l={link()} questions={[]} waitings={[w]} onTicket={() => {}} />);
    expect(screen.getByText(/^mac waits for you/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1));
  });

  it("draws each open waiting the channel lists on its session's card, and Dismiss reads the list again", async () => {
    // through the channel's own list, never the card alone
    const w = { seq: 9, time: "2026-11-08T00:00:00Z", kind: "waiting", from: "mac", to: "owner", ref: null,
      text: "Waiting for your word on the release.", link_id: "L1" };
    serve(routes({
      "GET /api/ade/link/sessions": () => [200, { links: [link()], questions: [], waitings: [w],
        public_url: "https://pp.example" }],
      "POST /api/ade/link/questions/9/close": () => [200, { seq: 10 }],
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const said = await screen.findByText(/^mac waits for you/);
    const box = said.parentElement as HTMLElement;
    expect(box.textContent).toContain("Waiting for your word on the release.");
    const read = () => calls.filter((c) => c.method === "GET" && c.path === "/api/ade/link/sessions").length;
    const reads = read();
    fireEvent.click(within(box).getByRole("button", { name: "Dismiss" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/questions/9/close")).toHaveLength(1));
    await vi.waitFor(() => expect(read()).toBeGreaterThan(reads));
  });

  it("counts an open waiting among what the channel holds for the owner", () => {
    const w = { seq: 9, time: "2026-11-08T00:00:00Z", kind: "waiting", from: "mac", to: "owner", ref: null, text: "x",
      link_id: "L1" } as LinkEvent & { link_id: string };
    expect(channelAlerts({ links: [], questions: [], waitings: [w], public_url: "" } as unknown as LinkSessions))
      .toBe(1);
  });

  it("asks for a new review when one is more than five minutes old, before any passkey prompt", async () => {
    serve(routes());
    const { SignBox } = await fresh();
    render(<SignBox purpose="instruction" to="L1" toName="mac" placeholder="What mac should do" submit="Review" onDone={async () => {}} />);
    fireEvent.change(screen.getByLabelText("What mac should do"), { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText(/^You are signing an instruction/);
    const later = Date.now() + 291_000;                          // this browser's clock, whatever the server's says
    vi.spyOn(Date, "now").mockReturnValue(later);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await screen.findByText("This review is more than five minutes old: review it again.");
    expect(credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    vi.restoreAllMocks();
  });

  it("offers nothing to sign where passkeys do not work: no Instruct, Check, Answer or Resume", async () => {
    serve(routes());
    const { LinkCard, Question, StopResume } = await fresh(false);
    render(<LinkCard l={link()} questions={[]} onTicket={() => {}} />);
    expect(screen.queryByRole("button", { name: /Instruct/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check" })).toBeNull();
    cleanup();
    const q: LinkEvent = { seq: 12, time: "2026-10-03T00:00:00Z", kind: "question", from: "mac", to: "owner", ref: null, text: "which?" };
    render(<Question q={q} linkId="L1" />);
    expect(screen.queryByRole("button", { name: /Answer/ })).toBeNull();
    cleanup();
    render(<StopResume l={link({ stopped_at: SERVER_TIME })} />);
    expect(screen.queryByRole("button", { name: /Resume/ })).toBeNull();
    expect(screen.getByText(/Commands are signed with one of your passkeys/)).toBeTruthy();
  });

  it("shows a linked session's conversation, and signs what is written under it", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const events = [
      ev(7, { mirror: "owner", text: "how is the build?" }),
      ev(8, { kind: "step", text: "a step, in the log only" }),
      ev(9, { mirror: "session", text: "**Green**: 12 passed. ![chart](https://example.org/x.png) [the run](https://example.org/run)" }),
      ev(10, { from: "owner", to: "mac", kind: "instruction", text: "ship it", state: "taken" }),
      ev(11, { kind: "done", ref: 10, text: "done: shipped" }),
    ].reverse();                                                  // the timeline gives the newest first
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const mine = await screen.findByText("how is the build?");
    expect(mine.className).toMatch(/bg-active/);                  // the owner's words, on the owner's side
    expect(screen.getByText("You, in mac")).toBeTruthy();
    expect(screen.getByText("Green").tagName).toBe("STRONG");      // the session's words as Markdown
    expect(document.querySelector("img")).toBeNull();             // an image is not fetched
    expect(screen.getByText("[image: chart]")).toBeTruthy();
    const a = screen.getByRole("link", { name: "the run" });
    expect([a.getAttribute("target"), a.getAttribute("rel")]).toEqual(["_blank", "noopener noreferrer"]);
    expect(screen.getByText("You signed an instruction")).toBeTruthy();
    expect(screen.getByText("taken")).toBeTruthy();
    expect(screen.getByText("mac replied to [10]", { exact: false })).toBeTruthy();
    expect(screen.queryByText("a step, in the log only")).toBeNull();
    fireEvent.change(screen.getByLabelText("Message mac"), { target: { value: "and the docs" } });
    fireEvent.click(screen.getByRole("button", { name: /Review/ }));
    await screen.findByText(/^You are signing an instruction to mac \(link L1\)/);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    expect(sent("/api/ade/link/commands")[0].body?.payload).toMatchObject({ purpose: "instruction", to: "L1", text: "and the docs" });
  });

  it("says beside a queued command when a session picks it up: an idle one with its watcher within seconds, a busy one after its step", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [
      ev(11, { from: "owner", to: "mac", kind: "instruction", text: "and the docs", state: "taken" }),
      ev(10, { from: "owner", to: "mac", kind: "instruction", text: "ship it", state: "queued" }),
    ] }] }));
    const { Conversation } = await fresh();
    const heard = Math.floor(Date.now() / 1000);
    // heard from 595 seconds ago, then 605: either side of the ten minutes (60 and 7,200 would pass
    // any threshold between them)
    const { unmount } = render(<Conversation l={link({ last_heartbeat_at: heard - 595 })} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("ship it");
    const said = screen.queryAllByText(QUEUED_SAID);
    expect(said).toHaveLength(1);                                       // beside the queued one alone
    expect(said[0].previousElementSibling?.textContent).toBe("queued");
    expect(said[0].previousElementSibling?.getAttribute("title")).toBe(QUEUED_SAID);
    // a session not heard from lately has no watcher reading for it: the words say so, and
    // the pill's title with them
    unmount();
    render(<Conversation l={link({ last_heartbeat_at: heard - 605 })} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("ship it");
    expect(screen.queryAllByText(QUEUED_SAID)).toHaveLength(0);
    const unheard = screen.queryAllByText(QUEUED_UNHEARD);
    expect(unheard).toHaveLength(1);
    expect(unheard[0].previousElementSibling?.getAttribute("title")).toBe(QUEUED_UNHEARD);
  });

  it("says on a queued command's pill in the list what it says beside it in the conversation", async () => {
    // the list's pill said an idle session picks it up within seconds, heard or not
    const { Commands } = await fresh();
    const cmd: LinkEvent = { seq: 5, time: "2026-10-03T00:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null,
      text: "ship it", state: "queued" };
    const heard = Math.floor(Date.now() / 1000);
    // either side of the ten minutes (60 and 7,200 would pass any threshold between them)
    const { unmount } = render(<Commands l={link({ commands: [cmd], last_heartbeat_at: heard - 595 })} />);
    expect(screen.getByText("queued").getAttribute("title")).toBe(QUEUED_SAID);
    unmount();
    render(<Commands l={link({ commands: [cmd], last_heartbeat_at: heard - 605 })} />);
    expect(screen.getByText("queued").getAttribute("title")).toBe(QUEUED_UNHEARD);
  });

  // A session is heard by every request it signs, a live round, a post, a
  // checkpoint, not its heartbeat alone; the server gives that time (last_heard_at), and an older one none
  it("says on the card and in the conversation when a session was heard, by a request with no heartbeat", async () => {
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [] }] }));
    const { LinkCard, Conversation } = await fresh();
    const now = Math.floor(Date.now() / 1000);
    const l = link({ last_heartbeat_at: null, last_heard_at: now - 120 });
    const { unmount } = render(<LinkCard l={l} questions={[]} onTicket={() => {}} />);
    expect(screen.queryByText("not heard from yet")).toBeNull();
    expect(screen.getByText("heard 2m ago")).toBeTruthy();
    unmount();
    render(<Conversation l={l} onBack={() => {}} onClose={() => {}} />);
    expect(screen.queryByText("not heard from yet")).toBeNull();
    expect(screen.getByText("heard 2m ago")).toBeTruthy();
  });

  it("takes the later of the two as heard, and the heartbeat from a server that gives none", async () => {
    const { LinkCard } = await fresh();
    const now = Math.floor(Date.now() / 1000);
    const { unmount } = render(<LinkCard l={link({ last_heartbeat_at: now - 3600, last_heard_at: now - 600 })}
      questions={[]} onTicket={() => {}} />);
    expect(screen.getByText("heard 10m ago")).toBeTruthy();
    unmount();
    render(<LinkCard l={link({ last_heartbeat_at: now - 600 })} questions={[]} onTicket={() => {}} />);  // none given
    expect(screen.getByText("heard 10m ago")).toBeTruthy();
  });

  it("says a queued command waits on the watcher where only the session's rounds were heard lately", async () => {
    const { Commands } = await fresh();
    const cmd: LinkEvent = { seq: 5, time: "2026-10-03T00:00:00Z", kind: "instruction", from: "owner", to: "mac",
      ref: null, text: "ship it", state: "queued" };
    const now = Math.floor(Date.now() / 1000);
    render(<Commands l={link({ commands: [cmd], last_heartbeat_at: now - 605, last_heard_at: now - 5 })} />);
    expect(screen.getByText("queued").getAttribute("title")).toBe(QUEUED_UNHEARD);
  });

  it("reads again the pages loaded with Older when a Stop's note comes, so a round its Stop posted again goes there too", async () => {
    // a round loaded with Older kept the word it came with, and stood beside its Stop's notes
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let stopped = false;
    const round = () => ev(5, { mirror: "live", text: JSON.stringify({ items: [["session", "the old round's words"]] }),
      ...(stopped ? { posted_again: true } : {}) });
    const lines = (from: number) => Array.from({ length: 300 - from }, (_, i) => ev(from + i, { mirror: "session", text: `line ${from + i}` }));
    // two pages loaded with Older, the second ending at the round; each read again once a Stop's note comes, and no
    // read brings it again without a new one (one page with no loop would pass, and so would a read
    // again at every read); line 200, which leaves the newest page with the Stop's note, goes into Older's and is
    // read again with them (without that, it was in neither, and shown no more)
    const older: string[] = [];
    serveTimeline(() => [round(), ...seqs(100, 200).map((n) => ev(n, { mirror: "session", text: `old ${n}` })),
      ...(stopped ? [...lines(200), ev(300, { mirror: "session", text: "The Stop's words." })] : lines(200))], older);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    await screen.findByText("old 100");
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    expect(older).toEqual(["200", "100"]);
    stopped = true;
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("The Stop's words.");
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    expect(older).toEqual(["200", "100", "201", "101"]);
    expect(screen.getByText("line 200")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Read again"));                       // no new Stop's note: nothing read back
    await new Promise((r) => setTimeout(r, 100));
    expect(older).toEqual(["200", "100", "201", "101"]);
  });

  it("reads what came between two reads that do not meet, once Older's pages are loaded, and keeps what left the newest", async () => {
    // a page hidden for a while finds a newest page that does not meet the last one; what
    // lay between is read as Older reads it, and what left the newest page goes into Older's
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    expect(older).toEqual(["6"]);
    top = 305;                                  // the next newest page, 206 to 305, does not meet the last, 6 to 105
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 305");
    await vi.waitFor(() => expect(screen.queryByText("line 150")).toBeTruthy());
    expect(screen.getByText("line 50")).toBeTruthy();
    expect(screen.getByText("line 250")).toBeTruthy();
    expect(older).toEqual(["6", "206"]);
  });

  it("reads back at a Stop's note that came in what lay between two reads", async () => {
    // only the newest page's notes started a read back, so a Stop's note read in the gap, or
    // one that left the newest page while a read back ran, was never read back for
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string, again = false) =>
      ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }), ...(again ? { posted_again: true } : {}) });
    let stopped = false;
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words", stopped), ...seqs(6, 106).map((n) => round(n, `line ${n}`)),
      ...(stopped ? [ev(106, { mirror: "session", text: "The Stop's words." }), ...seqs(107, 207).map((n) => round(n, `line ${n}`))] : [])],
    older);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    stopped = true;              // the Stop's note, then 100 rounds of the next turn: the newest page holds no Stop's note
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("The Stop's words.");
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    expect(older).toEqual(["6", "107", "107", "7"]);
  });

  it("keeps Older's pages where less than a page lay between two reads", async () => {
    // where Older's pages stay: 45 events between, read in one page
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    top = 250;                   // the newest page, 151 to 250; 106 to 150 between
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 250");
    await vi.waitFor(() => expect(screen.queryByText("line 120")).toBeTruthy());
    expect(screen.getByText("line 50")).toBeTruthy();
    expect(screen.getByText("the old round's words")).toBeTruthy();
    expect(older).toEqual(["6", "151"]);
  });

  it("lets Older's pages go where more than a page lay between two reads, and reads Older from the newest again", async () => {
    // a page hidden for hours read all that lay between, page by page, before showing anything
    // new, each tick starting another copy; past a page, Older's pages give way, and Older reads on from the newest page
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    top = 405;                   // the newest page, 306 to 405; 200 between, past a page
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 405");
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    expect(screen.queryByText("line 50")).toBeNull();
    expect(older).toEqual(["6", "306"]);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    await screen.findByText("line 250");
    expect(older).toEqual(["6", "306", "306"]);
  });

  it("drops Older's answer that comes after Older's pages gave way", async () => {
    // Older's read, out when more than a page came between two reads, landed after Older's
    // pages gave way, a page below the old span, with nothing between it and the newest shown
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older,
      {}, (before) => before === 6 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    await vi.waitFor(() => expect(older).toEqual(["6"]));       // Older's read out, its answer held
    top = 405;
    fireEvent.click(screen.getByLabelText("Read again"));       // more than a page between: Older's pages give way
    await screen.findByText("line 405");
    expect(older).toEqual(["6", "306"]);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));   // Older asked for again (`!asked` passed
    await screen.findByText("line 250");                                     // without this)
    release();
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByText("the old round's words")).toBeNull();
    expect(screen.queryByText("line 50")).toBeNull();
    expect(older).toEqual(["6", "306", "306"]);
  });

  it("drops a read back's answer that comes after Older's pages gave way", async () => {
    // a read back out when Older's pages gave way put the old span back
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105, stopped = false;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, 106).map((n) => round(n, `line ${n}`)),
      ...(stopped ? [ev(106, { mirror: "session", text: "The Stop's words." })] : []),
      ...seqs(107, top + 1).map((n) => round(n, `line ${n}`))], older,
    {}, (before) => before === 7 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    stopped = true; top = 106;                                  // a Stop's note: its read back starts, and waits
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
    top = 406;                                                  // more than a page between: Older's pages give way
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 406");
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));   // Older asked for again
    await screen.findByText("line 250");
    release();
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByText("the old round's words")).toBeNull();
    expect(screen.queryByText("line 50")).toBeNull();
    expect(older).toEqual(["6", "7", "307", "307"]);
    fireEvent.click(screen.getByLabelText("Read again"));       // the dropped answer lowered
    await new Promise((r) => setTimeout(r, 50));                // what was read back for, and this read read back again
    expect(older).toEqual(["6", "7", "307", "307"]);
  });

  it("drops a read back's failure that comes after Older's pages gave way", async () => {
    // a read back out when Older's pages gave way, failing on its return, lowered what was read
    // back for and counted a failure, though the read that let them go had set the one and the other was not Older's
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105, stopped = false, named = false, reads = 0, later = false;
    let release: (status?: number) => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, 106).map((n) => round(n, `line ${n}`)),
      ...(stopped ? [ev(106, { mirror: "session", text: "The Stop's words." })] : []),
      ...seqs(107, top + 1).map((n) => round(n, named && n === top ? `line ${n}, read ${reads}` : `line ${n}`)),
      ...(later ? [ev(407, { mirror: "session", text: "The Stop's later words." })] : [])], older,
    {}, (before) => before === 7 ? new Promise<number | void>((r) => { release = r; }) : undefined);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });   // the reads the case's own (else a wrong version
    try {                                                              // passed it by the page's own read)
      const { Conversation } = await fresh();
      render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));
      expect(await screen.findByText("the old round's words")).toBeTruthy();
      stopped = true; top = 106;                                  // a Stop's note: its read back starts, and waits
      fireEvent.click(screen.getByLabelText("Read again"));
      await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
      top = 406;                                                  // more than a page between: Older's pages give way
      fireEvent.click(screen.getByLabelText("Read again"));
      await screen.findByText("line 406");
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));   // Older asked for again
      await screen.findByText("line 250");
      release(500);                                               // the read back fails
      await new Promise((r) => setTimeout(r, 100));
      expect(older).toEqual(["6", "7", "307", "307"]);
      named = true;
      for (let i = 0; i < 2; i++) {                           // nothing to read back: the read that let them go said so
        reads += 1;
        fireEvent.click(screen.getByLabelText("Read again"));
        await screen.findByText(`line 406, read ${reads}`, {}, { timeout: 5000 });
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
      }
      expect(older).toEqual(["6", "7", "307", "307"]);
      reads += 1;
      later = true;                                        // a later Stop's note: read back at the read that brings it,
      fireEvent.click(screen.getByLabelText("Read again"));    // the late failure counted for no wait.
      await screen.findByText(`line 406, read ${reads}`, {}, { timeout: 5000 });     // Waited for by its line,
      await act(async () => { await new Promise((r) => setTimeout(r, 50)); });      // not by vi.waitFor, which moves
      expect(older).toEqual(["6", "7", "307", "307", "308", "208"]);                // the held clock on: the page's own
                                                                                    // read would then end the wait
                                                                                    // counted here
    } finally {
      vi.useRealTimers();
    }
  }, 20000);

  it("drops a read's answer that comes after another read let Older's pages go", async () => {
    // a read whose read of what lay between was out when another read let Older's pages go
    // put the old span back into Older's pages
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older,
      {}, (before) => before === 151 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    top = 250;                                                  // a read: 45 between, its read of them held
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "151"]));
    top = 405;                                                  // another: more than a page between, Older's pages give way
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 405");
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));   // Older asked for again
    await screen.findByText("line 250");
    release();
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByText("line 50")).toBeNull();
    expect(screen.getByText("line 405")).toBeTruthy();
  });

  it("drops in a session's view Older's answer that comes after Older's pages gave way", async () => {
    // as in the link's own conversation
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", session: "s1",
        ...over } as LinkEvent);
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older,
      { "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }] },
      (before) => before === 6 ? new Promise<void>((r) => { release = r; }) : undefined);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { SessionConversation } = await fresh();
      render(<SessionConversation s={machineSession({ talk_at: undefined })} onBack={() => {}} onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));
      await vi.waitFor(() => expect(older).toEqual(["6"]));
      top = 405;
      await act(async () => { vi.advanceTimersByTime(2000); });
      await screen.findByText("line 405");
      expect(older).toEqual(["6", "306"]);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));   // Older asked for again
      await screen.findByText("line 250");
      release();
      await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
      expect(screen.queryByText("the old round's words")).toBeNull();
      expect(screen.queryByText("line 50")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads back for a Stop's note that came in what lay between two reads while a read back ran", async () => {
    // the Stop's last note came with 100 rounds after it, so a read
    // took it in only from what lay between, during a read back (never on a newest page)
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string, again = false) =>
      ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }), ...(again ? { posted_again: true } : {}) });
    let step = 0;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words", step >= 2), ...seqs(6, 106).map((n) => round(n, `line ${n}`)),
      ...(step >= 1 ? [ev(106, { mirror: "session", text: "The Stop's first words." })] : []),
      ...(step >= 2 ? [ev(107, { mirror: "session", text: "The Stop's last words." }), ...seqs(108, 208).map((n) => round(n, `line ${n}`))] : [])],
    older, {}, (before) => before === 7 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    step = 1;                                                   // the Stop's first note: its read back starts, and waits
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
    step = 2;                                                   // its last note, then 100 rounds: in what lay between
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 207");
    expect(older).toEqual(["6", "7", "108"]);
    release();
    await new Promise((r) => setTimeout(r, 100));
    fireEvent.click(screen.getByLabelText("Read again"));       // no Stop's note on the newest page: read back all the same
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
  });

  it("reads back for a Stop's note that left the newest page while a read back ran", async () => {
    // a note seen during a read back, gone from the newest page by the read after it, was read
    // back for by no read; here on a newest page first
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string, again = false) =>
      ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }), ...(again ? { posted_again: true } : {}) });
    let step = 0;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words", step >= 2), ...seqs(6, 106).map((n) => round(n, `line ${n}`)),
      ...(step >= 1 ? [ev(106, { mirror: "session", text: "The Stop's first words." })] : []),
      ...(step >= 2 ? [ev(107, { mirror: "session", text: "The Stop's last words." })] : []),
      ...(step >= 3 ? seqs(108, 208).map((n) => round(n, `line ${n}`)) : [])],
    older, {}, (before) => before === 7 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    step = 1;                                                   // the Stop's first note: its read back starts, and waits
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
    step = 2;                                                   // its last note, on the newest page
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("The Stop's last words.");
    step = 3;                                                   // then 100 rounds: it leaves the newest page
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 207");
    release();
    await new Promise((r) => setTimeout(r, 100));
    fireEvent.click(screen.getByLabelText("Read again"));       // no Stop's note on the newest page: read back all the same
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
  });

  it("starts the waits of a failing read back again where Older's pages gave way", async () => {
    // after five failures, a later Stop's note in pages Older read again once the server came
    // back waited 16 reads, its rounds standing beside it
    const { takeNewest } = await fresh();
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const lines = (from: number, to: number) => seqs(from, to).map((n) => ev(n, { mirror: "session", text: `line ${n}` }));
    const p = { newest: lines(6, 106), older: [ev(5, { mirror: "live", text: "{}" })], asked: true, stopSeen: 105, rereading: false,
                gen: 0, stopHigh: 105, fails: 5, wait: 16 };
    // a full page wholly past the last, and a full page wholly past it between: Older's pages give way
    await takeNewest(p, lines(400, 500), async () => lines(300, 400), () => {});
    expect([p.gen, p.older.length, p.fails, p.wait]).toEqual([1, 0, 0, 0]);
  });

  it("waits a read after a read back that failed, then two, four and so on to 30, and from the start once one came back", async () => {
    // a version with the wait unbounded, or with failures counted across a read back that came back, fails here:
    // which of the reads try a read back that keeps failing, by their number
    const { takeNewest } = await fresh();
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const lines = (from: number, to: number) => seqs(from, to).map((n) => ev(n, { mirror: "session", text: `line ${n}` }));
    const p = { newest: lines(6, 106), older: [ev(5, { mirror: "live", text: "{}" })], asked: true, stopSeen: 105, rereading: false,
                gen: 0, stopHigh: 105, fails: 0, wait: 0 };
    const tried: number[] = [];
    let n = 0, fail = true;
    const read = async () => {
      tried.push(n);
      if (fail) throw new Error("500");
      return [ev(5, { mirror: "live", text: "{}" })];
    };
    const take = async (next: LinkEvent[], upto: number) => {
      for (n += 1; n <= upto; n += 1) await takeNewest(p, next, read, () => {}).catch(() => {});
      n = upto;
    };
    await take(lines(7, 107), 100);                             // a Stop's note at 106, its read back failing
    expect(tried).toEqual([1, 3, 6, 11, 20, 37, 68, 99]);
    fail = false;
    await take(lines(7, 107), 130);                             // the eighth's wait is 30 reads: the ninth comes back
    expect(tried).toEqual([1, 3, 6, 11, 20, 37, 68, 99, 130]);
    fail = true;
    await take(lines(8, 108), 133);                             // another Stop's note, its read back failing: a read's wait
    expect(tried).toEqual([1, 3, 6, 11, 20, 37, 68, 99, 130, 131, 133]);
  });

  it("leaves a Stop's note to be read back again where its view went away during the read back", async () => {
    // a read back whose view went away returned with its note counted as read back
    const { takeNewest } = await fresh();
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const lines = (from: number, to: number) => seqs(from, to).map((n) => ev(n, { mirror: "session", text: `line ${n}` }));
    let alive = true;
    const p = { newest: lines(6, 106), older: [ev(5, { mirror: "live", text: "{}" })], asked: true, stopSeen: 105, rereading: false,
                gen: 0, stopHigh: 105, fails: 0, wait: 0 };
    await takeNewest(p, lines(7, 107), async () => { alive = false; return []; }, () => {}, () => alive);
    expect(p.stopSeen).toBe(105);
    expect(p.rereading).toBe(false);
  });

  it("keeps what leaves the newest page while Older's own first page is still being read", async () => {
    // at Older's first click: a read that moves the newest page past its oldest while
    // Older's answer is on its way
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older,
      {}, (before) => before === 6 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    await vi.waitFor(() => expect(older).toEqual(["6"]));
    top = 106;
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 106");
    release();
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    expect(screen.getByText("line 6")).toBeTruthy();
  });

  it("reads back, once one running ends, for a Stop's note that came during it; one at a time, keeping what came meanwhile", async () => {
    // the Stop seen during a read back was counted as read back, so its rounds in Older's
    // pages stood beside its notes until another Stop; and a read back that replaced Older's pages dropped what left
    // the newest page meanwhile
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let top = 105, whole = false;
    let release: () => void = () => {};
    const older: string[] = [];
    serveTimeline(() => [ev(5, { mirror: "live", text: JSON.stringify({ items: [["session", "the old round's words"]] }),
      ...(whole ? { posted_again: true } : {}) }), ...seqs(6, top + 1).map((n) => ev(n, { mirror: "session", text: `line ${n}` }))],
    older, {}, (before) => before === 7 ? new Promise<void>((r) => { release = r; }) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    top = 106;                                   // a Stop's first note: its read back starts, and its answer waits
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
    top = 107; whole = true;                     // its last note, which says the round was posted again
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("line 107");
    expect(older).toEqual(["6", "7"]);            // one read back at a time
    release();
    await new Promise((r) => setTimeout(r, 100));            // the read back ends, its pages merged
    expect(screen.getByText("line 7")).toBeTruthy();         // what left the newest page meanwhile stays
    expect(screen.getByText("the old round's words")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Read again"));   // the note seen during it is read back now
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    expect(older).toEqual(["6", "7", "8"]);
  });

  it("reads back again after a read back that failed", async () => {
    // a read back that failed was never tried again
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let top = 105, whole = false, fail = true;
    const older: string[] = [];
    serveTimeline(() => [ev(5, { mirror: "live", text: JSON.stringify({ items: [["session", "the old round's words"]] }),
      ...(whole ? { posted_again: true } : {}) }), ...seqs(6, top + 1).map((n) => ev(n, { mirror: "session", text: `line ${n}` }))],
    older, {}, (before) => before === 7 && fail ? Promise.resolve(500) : undefined);
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Older" }));
    expect(await screen.findByText("the old round's words")).toBeTruthy();
    top = 106; whole = true;                     // the Stop's note, the round posted again; its read back fails
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(older).toEqual(["6", "7"]));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText("the old round's words")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByLabelText("Read again"));   // the read back waits a read after a failure
    await new Promise((r) => setTimeout(r, 50));
    fireEvent.click(screen.getByLabelText("Read again"));   // no new note: the read back is tried again
    await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
    expect(older).toEqual(["6", "7", "7"]);
  });

  it("waits longer before each read back that fails again", async () => {
    // a read back that kept failing was tried at every read, each from the top of Older's
    // span and each with an error said, for as long as Older was loaded. The reads are the case's own, the page's every
    // 2 seconds held, and each ends before the next: its newest line, named for it, shown, and where it tries a read
    // back, that read back's failure said
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let top = 105, reads = 0;
    const older: string[] = [];
    serveTimeline(() => [ev(5, { mirror: "live", text: JSON.stringify({ items: [["session", "the old round's words"]] }) }),
      ...seqs(6, top + 1).map((n) => ev(n, { mirror: "session", text: n === top ? `line ${n}, read ${reads}` : `line ${n}` }))],
    older, {}, (before) => before === 7 ? Promise.resolve(500) : undefined);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { Conversation } = await fresh();
      const { useStore: store } = await import("../store");
      render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));
      expect(await screen.findByText("the old round's words")).toBeTruthy();
      const read = async (tries: boolean) => {
        reads += 1;
        fireEvent.click(screen.getByLabelText("Read again"));
        await screen.findByText(`line ${top}, read ${reads}`, {}, { timeout: 5000 });
        if (tries) {                                            // its read back's failure taken in: said, its wait set;
          for (let k = 0; k < 250 && store.getState().error === null; k++) {      // polled on the real clock, as
            await act(async () => { await new Promise((r) => setTimeout(r, 20)); });   // vi.waitFor would move the held
          }                                                                         // one and read again
          expect(store.getState().error).not.toBeNull();
          store.setState({ error: null });
        }
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
      };
      top = 106;                                                // a Stop's note: its read back fails
      await read(true);
      expect(older).toEqual(["6", "7"]);
      await read(false);                                        // one read waited
      expect(older).toEqual(["6", "7"]);
      await read(true);                                         // tried again, and failed again
      expect(older).toEqual(["6", "7", "7"]);
      await read(false);
      await read(false);                                        // two reads waited
      expect(older).toEqual(["6", "7", "7"]);
      await read(true);
      expect(older).toEqual(["6", "7", "7", "7"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the live view's steps in the conversation as lines of the calls the session made, one under another", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const events = [
      ev(7, { mirror: "owner", text: "how is the build?" }),
      ev(8, { mirror: "step", text: "Bash: pytest -q\nRead: /work/app.py" }),
      ev(9, { mirror: "session", text: "12 passed." }),
    ].reverse();
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const step = await screen.findByLabelText("The calls it made");
    expect(step.textContent).toBe("Bash: pytest -q\nRead: /work/app.py");
    expect(step.className).toMatch(/whitespace-pre-wrap/);
  });

  it("says once, after the newest of its steps, what a Bash step names: its command's program, never what else it runs", async () => {
    // a call's line carries its tool's name and its target alone, and the page says so of a command's, where the owner
    // reads its steps
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const events = [
      ev(7, { mirror: "owner", text: "how is the build?" }),
      ev(8, { mirror: "step", text: "Bash: grep" }),
      ev(9, { mirror: "session", text: "Found it." }),
      ev(10, { mirror: "live", text: JSON.stringify({ items: [["calls", ["Bash: pytest", "Read: /work/app.py"]]] }) }),
      ev(11, { mirror: "session", text: "12 passed." }),
    ].reverse();
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const after = await screen.findByText("12 passed.");
    const steps = screen.getAllByLabelText("The calls it made");
    const said = screen.getAllByText(STEP_SAID);
    expect([steps.length, said.length]).toEqual([2, 1]);
    expect(steps[1].compareDocumentPosition(said[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(said[0].compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(steps.map((s) => s.getAttribute("title"))).toEqual([STEP_SAID, STEP_SAID]);
  });

  it("says nothing of a step where the conversation holds none", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    const events = [ev(7, { mirror: "owner", text: "how is the build?" }), ev(8, { mirror: "session", text: "12 passed." })]
      .reverse();
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    await screen.findByText("12 passed.");
    expect(screen.queryByText(STEP_SAID)).toBeNull();
  });

  it("says once in a session's view too, after the newest of its steps, what a Bash step names", async () => {
    const note = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", session: "s1",
        ...over } as LinkEvent);
    const events = [note(7, { mirror: "step", text: "Bash: grep" }), note(8, { mirror: "session", text: "Found it." })]
      .reverse();
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events }],
      "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }] }));
    const { SessionConversation } = await fresh();
    render(<SessionConversation s={machineSession({ talk_at: undefined })} onBack={() => {}} onClose={() => {}} />);
    const after = await screen.findByText("Found it.");
    const said = screen.getAllByText(STEP_SAID);
    expect(said).toHaveLength(1);
    expect(screen.getByLabelText("The calls it made").compareDocumentPosition(said[0]) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(said[0].compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("follows its newest line while the reader is at its bottom, stays where they scrolled up, and offers the newest", async () => {
    // A sealed line opens after it is shown, so
    // the view follows what it holds, not only how many lines it holds
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let events = [ev(7, { mirror: "session", text: "first" })];
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [...events].reverse() }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("first")).closest(".overflow-y-auto") as HTMLDivElement;    // its scroller
    let height = 1000;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => height });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    events = [ev(7, { mirror: "session", text: "first, and the rest of it" })];   // the same line, longer now, as a
    height = 1400;                                           // sealed one is once it opens
    fireEvent.click(screen.getByLabelText("Read again"));
    await vi.waitFor(() => expect(box.scrollTop).toBe(1400));
    box.scrollTop = 200;                                     // the reader scrolls up
    fireEvent.scroll(box);
    events = [...events, ev(8, { mirror: "session", text: "second" })];
    height = 1800;
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByText("second");
    expect(box.scrollTop).toBe(200);
    fireEvent.click(await screen.findByRole("button", { name: "Newest" }));
    expect(box.scrollTop).toBe(1800);
    expect(box.getAttribute("aria-label")).toBe("The conversation");
  });

  it("leaves a reader just above the bottom where they are when a read changes nothing", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [ev(7, { mirror: "session", text: "first" })] }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("first")).closest(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 1400 });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 1080;                                    // 20 pixels above the bottom: still at it
    fireEvent.scroll(box);
    fireEvent.click(screen.getByLabelText("Read again"));    // the same lines
    await new Promise((r) => setTimeout(r, 50));
    expect(box.scrollTop).toBe(1080);
  });

  it("takes a reader at the bottom to the share's line when it comes, the lines unchanged", async () => {
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });
    let full = false;
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [ev(7, { mirror: "session", text: "first" })], live_full: full }] }));
    const { Conversation } = await fresh();
    render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    const box = (await screen.findByText("first")).closest(".overflow-y-auto") as HTMLDivElement;
    let height = 1000;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => height });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 700;                                     // at the bottom
    fireEvent.scroll(box);
    full = true;
    height = 1100;                                           // the share's line, below the rows
    fireEvent.click(screen.getByLabelText("Read again"));
    await screen.findByRole("status", { name: "Live updates" });
    await vi.waitFor(() => expect(box.scrollTop).toBe(1100));
  });

  it("renders a round's items each under a key of its own", async () => {
    const said = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const held = JSON.stringify({ items: [["owner", "go"], ["session", "Reading."], ["calls", ["Bash: ls"]], ["session", "Done."]] });
      serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [
        { seq: 9, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, mirror: "live", text: held }] }] }));
      const { Conversation } = await fresh();
      render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
      await screen.findByText("Done.");
      expect(said.mock.calls.filter((c) => c.some((x) => String(x).includes("same key")))).toEqual([]);
    } finally {
      said.mockRestore();
    }
  });

  it("says plainly when the live view's share of the hour is used, and reads the conversation every 2 seconds", async () => {
    // The delay, and the live view's own share, said when reached
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let reads = 0, full = false;
      serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => { reads += 1; return [200, { events: [], live_full: full }]; } }));
      const { Conversation } = await fresh();
      render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
      await vi.waitFor(() => expect(reads).toBe(1));
      expect(screen.queryByRole("status", { name: "Live updates" })).toBeNull();
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      full = true;
      await act(async () => { vi.advanceTimersByTime(2000); });
      await vi.waitFor(() => expect(reads).toBe(2));
      expect((await screen.findByRole("status", { name: "Live updates" })).textContent).toBe(
        "Live updates are paused: this link's live view has used its share for this hour. Until it frees, each turn "
        + "comes when it ends, as before the live view, and its questions and prompts still reach you.");
    } finally {
      vi.useRealTimers();
    }
  });

  it("follows a session's newest line in its view too, while the reader is at its bottom", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let note: LinkEvent = { seq: 7, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null,
        mirror: "session", session: "s1", text: "first" } as LinkEvent;
      serve(routes({ "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: [note] }],
        "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }] }));
      const { SessionConversation } = await fresh();
      render(<SessionConversation s={machineSession({ talk_at: undefined })} onBack={() => {}} onClose={() => {}} />);
      const box = (await screen.findByText("first")).closest(".overflow-y-auto") as HTMLDivElement;
      note = { ...note, text: "first, and the rest of it" };             // the same line, longer, as a sealed one opens
      Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 1400 });
      Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      await act(async () => { vi.advanceTimersByTime(5000); });
      await vi.waitFor(() => expect(box.scrollTop).toBe(1400));
      expect(box.getAttribute("aria-label")).toBe("The conversation");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads again in a session's view the pages loaded with Older when a Stop's note comes", async () => {
    // as in the link's own conversation
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", session: "s1",
        ...over } as LinkEvent);
    let stopped = false;
    const round = () => ev(5, { mirror: "live", text: JSON.stringify({ items: [["session", "the old round's words"]] }),
      ...(stopped ? { posted_again: true } : {}) });
    const lines = (from: number) => Array.from({ length: 300 - from }, (_, i) => ev(from + i, { mirror: "session", text: `line ${from + i}` }));
    serve(routes({
      "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: (stopped
        ? [...lines(201), ev(300, { mirror: "session", text: "The Stop's words." })] : lines(200)).reverse() }],
      "GET /api/ade/link/timeline/L1?limit=100&before=200&session=s1": () => [200, { events: [round()] }],
      // Older's pages read again, from just past line 200, which left the newest page and went into Older's
      "GET /api/ade/link/timeline/L1?limit=100&before=201&session=s1": () => [200, { events: [ev(200, { mirror: "session", text: "line 200" }), round()] }],
      "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }],
    }));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { SessionConversation } = await fresh();
      render(<SessionConversation s={machineSession({ talk_at: undefined })} onBack={() => {}} onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));
      expect(await screen.findByText("the old round's words")).toBeTruthy();
      stopped = true;
      await act(async () => { vi.advanceTimersByTime(2000); });
      await screen.findByText("The Stop's words.");
      await vi.waitFor(() => expect(screen.queryByText("the old round's words")).toBeNull());
      expect(screen.getByText("line 200")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads in a session's view what came between two reads that do not meet, once Older's pages are loaded", async () => {
    // as in the link's own conversation
    const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
      ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", session: "s1",
        ...over } as LinkEvent);
    const round = (n: number, words: string) => ev(n, { mirror: "live", text: JSON.stringify({ items: [["session", words]] }) });
    let top = 105;
    const older: string[] = [];
    serveTimeline(() => [round(5, "the old round's words"), ...seqs(6, top + 1).map((n) => round(n, `line ${n}`))], older,
      { "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }] });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { SessionConversation } = await fresh();
      render(<SessionConversation s={machineSession({ talk_at: undefined })} onBack={() => {}} onClose={() => {}} />);
      fireEvent.click(await screen.findByRole("button", { name: "Older" }));
      expect(await screen.findByText("the old round's words")).toBeTruthy();
      top = 305;
      await act(async () => { vi.advanceTimersByTime(2000); });
      await screen.findByText("line 305");
      await vi.waitFor(() => expect(screen.queryByText("line 150")).toBeTruthy());
      expect(screen.getByText("line 50")).toBeTruthy();
      expect(older).toEqual(["6", "206"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads a session's notes every 2 seconds in its view, and its machine's copy every 10", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      let notes = 0, talks = 0;
      serve(routes({
        "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => { notes += 1; return [200, { events: [] }]; },
        "GET /api/ade/link/history/L1/s1": () => { talks += 1; return [200, { messages: [], earlier: 0, updated_at: SERVER_TIME }]; },
        "GET /api/ade/link/moves?session=s1": () => [200, { moves: [] }],
      }));
      const { SessionConversation } = await fresh();
      render(<SessionConversation s={machineSession({ talk_at: SERVER_TIME })} onBack={() => {}} onClose={() => {}} />);
      await vi.waitFor(() => expect([notes, talks]).toEqual([1, 1]));
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
      await act(async () => { vi.advanceTimersByTime(2000); });
      await vi.waitFor(() => expect(notes).toBe(2));
      expect(talks).toBe(1);
      await act(async () => { vi.advanceTimersByTime(8000); });
      await vi.waitFor(() => expect(notes).toBe(6));
      expect(talks).toBe(2);                                  // the fifth read, 10 seconds on
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens a link in a session's words only to another site: one into the page is shown as text", async () => {
    const device = "#device=ABCD-EFGH";
    const connect = `#connect=${"0123456789abcdef".repeat(2)}`;
    const ev: LinkEvent = { seq: 9, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, mirror: "session",
      text: `Build is green. [Review the change](${device}), [allow the report bot](${connect}), [here](https://pp.example/app/${device}) and [the run](https://example.org/run).` };
    serve(routes({ "GET /api/ade/link/timeline/L1?limit=100": () => [200, { events: [ev] }] }));
    const { Conversation } = await fresh();
    const { useStore: store } = await import("../store");
    store.setState({ links: { links: [], questions: [], public_url: "https://pp.example", asks: [] } });   // the ADE's own address
    const { container } = render(<Conversation l={link()} onBack={() => {}} onClose={() => {}} />);
    expect((await screen.findByRole("link", { name: "the run" })).getAttribute("target")).toBe("_blank");
    for (const name of ["Review the change", "allow the report bot", "here"]) expect(screen.queryByRole("link", { name })).toBeNull();
    expect(container.textContent).toContain(`Review the change [link not opened: ${device}], allow the report bot [link not opened: ${connect}]`);
    expect(container.textContent).toContain(`here [link not opened: https://pp.example/app/${device}]`);
  });

  it("makes a ticket only with the account's own word as well as the sign-in", async () => {
    serve(routes({
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c7" }] : [403, { detail: "That did not confirm it." }]),
      "POST /api/ade/link/tickets": (b) => (b?.confirmation === "c7" ? [200, { ticket: "PP-ABCDEFGH", expires_at: SERVER_TIME + 600, url: "https://pp.example" }] : [403, { detail: "no" }]),
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Link a session/ }));
    fireEvent.change(screen.getByLabelText("What to call it"), { target: { value: "mac" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const panel = await screen.findByRole("group", { name: "Linking “mac”" });
    expect(sent("/api/ade/link/tickets")).toHaveLength(0);
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await screen.findByText(/\.proofpane-ade\/ade_link\.py" setup --url https:\/\/pp.example --ticket PP-ABCDEFGH/);
    expect(sent("/api/ade/link/tickets").map((c) => c.body)).toEqual([{ confirmation: "c7" }]);
    // the confirmation is made for a new session's ticket, and nothing else
    expect(sent("/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: "link-ticket:new" }]);
  });

  // ---- a machine's session moved into the workspace ----------------------------------------------------------------
  const machineSession = (over: Partial<HistorySession> = {}): HistorySession => ({
    id: "s1", title: "Fix the sign-in", project: "~/work/app", branch: "main", started_at: SERVER_TIME - 7200,
    last_at: SERVER_TIME - 3600, link_id: "L1", link: "mac", kind: "local", listed_at: SERVER_TIME - 60, ...over,
  });
  const move = (over: Partial<LinkMove> = {}): LinkMove => ({
    seq: 31, link_id: "L1", session: "s1", state: "asked", created_at: SERVER_TIME, expires_at: SERVER_TIME + 3600,
    taken_at: null, uploaded_at: null, updated_at: SERVER_TIME, workspace_session: null, reason: null, ...over,
  });
  const MOVES = "GET /api/ade/link/moves?session=s1";              // by any link of the session's machine
  const moveRoutes = (moves: LinkMove[] = [], more: Record<string, Route> = {}) => routes({
    "GET /api/ade/link/timeline/L1?limit=100&session=s1": () => [200, { events: [] }],
    [MOVES]: () => [200, { moves }],
    ...more,
  });
  const workspaceIs = async (ws: Record<string, unknown> = { enabled: true, allowed: true, running: true }) => {
    const { useStore: store } = await import("../store");
    store.setState({ ws: ws as never });
  };
  /** The seconds a move waits, as the sessions answer gives them (the store keeps that answer). */
  const holdIs = async (moveHold: number) => {
    const { useStore: store } = await import("../store");
    store.setState({ links: { links: [], questions: [], public_url: "https://pp.example", move_hold: moveHold } });
  };
  const moveButton = () => screen.queryByRole("button", { name: /Move to the workspace/ });
  const held = (at: number, over: Partial<LinkMove> = {}) => move({ seq: null, state: "held", hold_id: "H1", takes_effect_at: at, told: 1, ...over });

  it("says what moving does and how long it waits, then signs a move of the session by its id, with no words, for its link", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    let listed: LinkMove[] = [];
    serve(moveRoutes([], {
      [MOVES]: () => [200, { moves: listed }],
      "POST /api/ade/link/commands": () => { listed = [held(at)]; return [200, { held: { id: "H1", takes_effect_at: at, told: 1 }, expires_at: SERVER_TIME + 3600 }]; },
    }));
    const { SessionConversation } = await fresh();
    await workspaceIs();
    await holdIs(180);
    render(<SessionConversation s={machineSession()} onBack={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.getByText("Moving sends this session's conversation and its work not committed to your workspace, and the "
      + "session continues there. End it on mac's machine first: a session still running there is not moved.")).toBeTruthy();
    expect(screen.getByText("It waits 3 minutes before mac's machine may take it: your devices with notifications are told at "
      + "once, and you can cancel it until then.")).toBeTruthy();
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);              // nothing asked before Review
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    const head = await screen.findByText(/^You are signing a move of session s1 into the workspace, for mac \(link L1\) to take until .+\.$/);
    expect(head.textContent).toContain(dayTime(SERVER_TIME + 3600));
    expect(screen.getByText("Its title here: Fix the sign-in")).toBeTruthy();
    expect(sent("/api/ade/link/nonce").map((c) => c.body)).toEqual([{ purpose: "move" }]);
    order.length = 0;
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(sent("/api/ade/link/commands")).toHaveLength(1));
    expect(order[0]).toBe("get");                                       // nothing fetched between the click and the prompt
    const payload = sent("/api/ade/link/commands")[0].body?.payload as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(["account", "created_at", "deployment", "expires_at", "id", "nonce", "purpose", "session", "to", "v"]);
    expect(payload).toMatchObject({ v: 2, deployment: "pp.example", purpose: "move", account: "u", nonce: "n-move", to: "L1",
      session: "s1", created_at: SERVER_TIME, expires_at: SERVER_TIME + 3600 });
    const asked = credentials.get.mock.calls[0][0].publicKey;
    expect(new Uint8Array(asked.challenge as ArrayBuffer)).toEqual(await stepped(canonicalJson(payload)));
    expect(await screen.findByText(`Signed: mac's machine may take it at ${clock(at)} unless you cancel it first.`)).toBeTruthy();
    expect(screen.getByText("Its notice was sent toward 1 device with notifications on.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(moveButton()).toBeNull();                                    // held, it is under way: one move at a time
  });

  it("offers no other move between Sign and the session's moves read again after it", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    let signed = false;
    let answer: (moves: LinkMove[]) => void = () => {};
    serve(moveRoutes([], {
      [MOVES]: () => (signed ? new Promise<Answer>((done) => { answer = (moves) => done([200, { moves }]); }) : [200, { moves: [] }]),
      "POST /api/ade/link/commands": () => { signed = true; return [200, { held: { id: "H1", takes_effect_at: at, told: 1 }, expires_at: SERVER_TIME + 3600 }]; },
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sign with your passkey/ }));
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/api/ade/link/moves?session=s1")).toHaveLength(2));
    await new Promise((r) => setTimeout(r, 30));                         // the page has drawn what it has meanwhile
    expect(moveButton()).toBeNull();                                    // signed, and not read yet: not offered again
    expect((screen.getByRole("button", { name: /Sign with your passkey/ }) as HTMLButtonElement).disabled).toBe(true);
    answer([held(at)]);
    expect(await screen.findByText(`Signed: mac's machine may take it at ${clock(at)} unless you cancel it first.`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Sign with your passkey/ })).toBeNull();
    expect(moveButton()).toBeNull();
  });

  it("reads a session's moves by any link of its machine and shows the one under way, wherever it is among them", async () => {
    serve(moveRoutes([move({ seq: 32, state: "failed", reason: "You withdrew it before its link took it." }), move({ link_id: "L2" })]));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    const { useStore: store } = await import("../store");
    store.setState({ links: { links: [link(), link({ id: "L2", name: "mini" })], questions: [], public_url: "https://pp.example" } });
    const { rerender } = render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    const words = `Asked: waiting for mini to take it, until ${dayTime(SERVER_TIME + 3600)}.`;    // the link it went to
    expect(await screen.findByText(words)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeTruthy();
    expect(moveButton()).toBeNull();                                    // under way, though a newer one failed
    rerender(<MoveToWorkspace s={machineSession({ link_id: "L2", link: "mini" })} onOpen={() => {}} />);   // its other link's list now
    expect(screen.getByText(words)).toBeTruthy();
    expect(moveButton()).toBeNull();
    expect(calls.filter((c) => c.path.startsWith("/api/ade/link/moves")).every((c) => c.path === "/api/ade/link/moves?session=s1")).toBe(true);
  });

  // where the account's work lives: a move goes into the workspace this
  // deployment runs, which starts for an account only where that is its choice
  const HOSTED_SENTENCE = "Your code, your files and your programs' sign-ins live on a machine this deployment runs for you; its "
    + "operator can read them, and the deployment's monthly cap can stop the machine.";
  const storageView = (choice: string) => ({
    choice, chosen: true, refusal: choice === "hosted" ? null : "This account keeps its work on its own machine.",
    choices: [
      { choice: "repository", label: "In your repository", sentence: "In the repository.", offered: true, why: null },
      { choice: "machine", label: "On your own machine", sentence: "On the machine.", offered: true, why: null },
      { choice: "hosted", label: "In a workspace this deployment runs for you", sentence: HOSTED_SENTENCE, offered: true, why: null },
    ],
    left_behind: { repository: [], machine: [], hosted: [] }, e2e: { on: false, not_now: false, ask: false },
  });
  const storagePuts = () => calls.filter((c) => c.method === "PUT" && c.path === "/api/ade/storage").map((c) => c.body);

  it("asks for the hosted choice, with its sentence, before it prepares a move where the account's work lives elsewhere", async () => {
    serve(moveRoutes([], {
      "GET /api/ade/storage": () => [200, storageView("machine")],
      "PUT /api/ade/storage": () => [200, storageView("hosted")],
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs({ enabled: true, allowed: true, running: true, choice: "machine" });
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    await vi.waitFor(() => expect(screen.queryByText(HOSTED_SENTENCE)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Review the move/ })).toBeNull();     // nothing prepared yet
    expect(sent("/api/ade/link/nonce")).toHaveLength(0);
    expect(storagePuts()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Keep my work in a workspace this deployment runs/ }));
    await vi.waitFor(() => expect(storagePuts()).toEqual([{ choice: "hosted" }]));
    expect(await screen.findByRole("button", { name: /Review the move/ })).toBeTruthy();
    expect(screen.queryByText(HOSTED_SENTENCE)).toBeNull();
  });

  it("asks nothing before a move where the account's work lives in the workspace already", async () => {
    serve(moveRoutes([], { "GET /api/ade/storage": () => [200, storageView("hosted")] }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs({ enabled: true, allowed: true, running: true, choice: "hosted" });
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.getByRole("button", { name: /Review the move/ })).toBeTruthy();
    expect(screen.queryByText(HOSTED_SENTENCE)).toBeNull();
  });

  it("says what stays where the work lives now, and takes the keyboard to the hosted choice, before a move", async () => {
    const LEFT = "Your linked session stays linked and goes on sending what it sends here.";
    serve(moveRoutes([], {
      "GET /api/ade/storage": () => [200, { ...storageView("machine"), left_behind: { repository: [], machine: [], hosted: [LEFT] } }],
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs({ enabled: true, allowed: true, running: true, choice: "machine" });
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    await vi.waitFor(() => expect(screen.queryByText(LEFT)).toBeTruthy());
    const keep = screen.queryByRole("button", { name: /Keep my work in a workspace this deployment runs/ });
    expect(keep).toBeTruthy();
    await vi.waitFor(() => expect(document.activeElement).toBe(keep));      // the button itself, not the page around it
  });

  it("asks nothing before a move where the server names no choice", async () => {
    serve(moveRoutes([], { "GET /api/ade/storage": () => [200, storageView("machine")] }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs({ enabled: true, allowed: true, running: true });
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.queryByRole("button", { name: /Review the move/ })).toBeTruthy();
    expect(screen.queryByText(HOSTED_SENTENCE)).toBeNull();
  });

  it("says a move waits on nothing where the deployment holds no move", async () => {
    serve(moveRoutes());
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    await holdIs(0);
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    expect(screen.getByText("mac's machine may take it as soon as it is signed (this deployment holds no move); your devices "
      + "with notifications are told.")).toBeTruthy();
  });

  it("cancels a held move with one click; a cancelled one says nothing was sent, and a move may follow", async () => {
    const at = Math.floor(Date.now() / 1000) + 120;
    let listed: LinkMove[] = [held(at, { told: 0 })];
    serve(moveRoutes([], {
      [MOVES]: () => [200, { moves: listed }],
      "POST /api/ade/holds/H1/cancel": () => { listed = [held(at, { state: "cancelled", told: 0 })]; return [200, { cancelled_at: SERVER_TIME }]; },
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(await screen.findByText(`Signed: mac's machine may take it at ${clock(at)} unless you cancel it first.`)).toBeTruthy();
    expect(screen.getByText("No device of this account has notifications on: only this page shows it.")).toBeTruthy();
    expect(moveButton()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled — it won't move into the workspace: nothing was sent.")).toBeTruthy();
    expect(sent("/api/ade/holds/H1/cancel")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.queryByText(/notifications/)).toBeNull();
    expect(moveButton()).toBeTruthy();                                  // nothing under way now
  });

  it("offers a move only once the session's moves are read, and not while it runs or its link is stopped", async () => {
    serve(moveRoutes());
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    const { unmount } = render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(moveButton()).toBeNull();                                    // its moves not read yet
    expect(await screen.findByRole("button", { name: /Move to the workspace/ })).toBeTruthy();
    unmount();
    const cases: [HistorySession, number | null, string][] = [
      [machineSession({ live: "working" }), null, "It is running on mac's machine: end it there first."],
      [machineSession(), SERVER_TIME, "mac is stopped: it takes nothing until you resume it."],
    ];
    for (const [s, stopped, why] of cases) {
      const { useStore: store } = await import("../store");
      store.setState({ links: { links: [link({ stopped_at: stopped })], questions: [], public_url: "https://pp.example" } });
      const { unmount: off } = render(<MoveToWorkspace s={s} onOpen={() => {}} />);
      const button = await screen.findByRole("button", { name: /Move to the workspace/ });
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText(why)).toBeTruthy();
      fireEvent.click(button);
      expect(screen.queryByRole("button", { name: /Review the move/ })).toBeNull();
      off();
    }
  });

  it("withdraws a move its machine has not taken", async () => {
    let listed: LinkMove[] = [move()];
    serve(moveRoutes([], {
      [MOVES]: () => [200, { moves: listed }],
      "POST /api/ade/link/commands/31/withdraw": () => { listed = [move({ state: "failed", reason: "You withdrew it before its link took it." })]; return [200, { withdrawn_at: SERVER_TIME }]; },
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(moveButton()).toBeNull();                                    // asked: under way
    fireEvent.click(await screen.findByRole("button", { name: "Withdraw" }));
    expect(await screen.findByText("Failed: You withdrew it before its link took it.")).toBeTruthy();
    expect(sent("/api/ade/link/commands/31/withdraw")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
    expect(moveButton()).toBeTruthy();
  });

  it("says why a held move could not be cancelled, and reads it again", async () => {
    const at = Math.floor(Date.now() / 1000) + 1;
    let listed: LinkMove[] = [held(at)];
    serve(moveRoutes([], {
      [MOVES]: () => [200, { moves: listed }],
      "POST /api/ade/holds/H1/cancel": () => { listed = [move()]; return [409, { detail: "Its time came at 2026-10-08 14:03:20 UTC: it has taken effect." }]; },
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect((await screen.findByRole("alert")).textContent).toContain("it has taken effect");
    expect(await screen.findByText(`Asked: waiting for mac to take it, until ${dayTime(SERVER_TIME + 3600)}.`)).toBeTruthy();
  });

  it("shows in Linked sessions each move that waits, with when, who was told and Cancel, and one not sent, with why", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    const withheld = "Its link's History list has the session withheld now: its words stay on its machine.";
    serve(routes({
      "GET /api/ade/link/sessions": () => [200, { links: [], questions: [], public_url: "https://pp.example", move_hold: 180, held_moves: [
        held(at, { told: 2, title: "Fix the sign-in", link: "mac" }),
        held(at, { hold_id: "H2", session: "s2", state: "failed", title: "Tidy up", link: "mac", reason: withheld })] }],
      "POST /api/ade/holds/H1/cancel": () => [200, { cancelled_at: SERVER_TIME }],
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const line = await screen.findByText(/may take it unless/);
    expect(line.textContent).toBe("You signed moving “Fix the sign-in” from mac into the workspace. mac's machine may take it unless you cancel it first.");
    expect(screen.getByText(/^Takes effect in/).textContent).toMatch(new RegExp(`^Takes effect in [23]:\\d\\d \\(${clock(at)}\\)$`));
    expect(screen.getByText("Its notice was sent toward 2 devices with notifications on.")).toBeTruthy();
    expect(screen.getByText(/it was not sent to/).textContent).toBe(`You signed moving “Tidy up” from mac into the workspace, and it was not sent to mac: ${withheld}`);
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);         // the one that waits
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(sent("/api/ade/holds/H1/cancel")).toHaveLength(1));
  });

  it("asks for a move's review again when it is more than five minutes old, before any passkey prompt", async () => {
    serve(moveRoutes());
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Move to the workspace/ }));
    fireEvent.click(screen.getByRole("button", { name: /Review the move/ }));
    await screen.findByText(/^You are signing a move of session s1/);
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 291_000);
    fireEvent.click(screen.getByRole("button", { name: /Sign with your passkey/ }));
    await screen.findByText("This review is more than five minutes old: review it again.");
    expect(credentials.get).not.toHaveBeenCalled();
    expect(sent("/api/ade/link/commands")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /Review the move/ })).toBeTruthy();
    vi.restoreAllMocks();
  });

  it("keeps a move's life within the hour the server takes, whatever it is asked for", async () => {
    serve(moveRoutes());
    await fresh();
    const { prepareSigned: prepare } = await import("../passkey");
    for (const [asked, kept] of [[86_400, 3600], [10, 60], [1800, 1800]]) {
      const p = await prepare("move", "L1", { session: "s1", life: asked });
      expect((p.payload.expires_at as number) - (p.payload.created_at as number), String(asked)).toBe(kept);
      expect("text" in p.payload).toBe(false);
    }
  });

  it("is offered only for a local session its machine lists unwithheld, where the account has a workspace", async () => {
    serve(moveRoutes());
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    for (const s of [machineSession({ withheld: true, title: null }), machineSession({ kind: "cloud" })]) {
      const { unmount } = render(<MoveToWorkspace s={s} onOpen={() => {}} />);
      expect(moveButton()).toBeNull();
      unmount();
    }
    for (const ws of [{ enabled: false }, { enabled: true, allowed: false }]) {
      await workspaceIs(ws);
      const { unmount } = render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
      expect(moveButton()).toBeNull();
      unmount();
    }
    expect(calls.filter((c) => c.path.startsWith("/api/ade/link/moves"))).toEqual([]);    // nothing read for them
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(await screen.findByRole("button", { name: /Move to the workspace/ })).toBeTruthy();
  });

  it("where passkeys do not work, says where moves are signed, and still says where one stands", async () => {
    serve(moveRoutes([move({ state: "failed", reason: "The session is still running: end it first." })]));
    const { MoveToWorkspace } = await fresh(false);
    await workspaceIs();
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(await screen.findByText("Failed: The session is still running: end it first.")).toBeTruthy();
    expect(screen.getByText(/Commands are signed with one of your passkeys/)).toBeTruthy();
    expect(moveButton()).toBeNull();
  });

  it("starts an uploaded move in the workspace, starting the workspace first when it is stopped, and opens it there", async () => {
    serve(moveRoutes([move({ state: "uploaded", reason: "The workspace is not running: start it, and carry the move on." })], {
      "POST /api/ade/link/moves/31/start": () => [200, move({ state: "started", workspace_session: "0123456789ab" })],
      "GET /api/ade/workspace": () => [200, { enabled: true, allowed: true, running: true }],
    }));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs({ enabled: true, allowed: true, running: false });
    const opened = vi.fn();
    render(<MoveToWorkspace s={machineSession()} onOpen={opened} />);
    expect(await screen.findByText("Sent: it waits for the workspace. The workspace is not running: start it, and carry the move on.")).toBeTruthy();
    expect(moveButton()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Start the workspace and carry on/ }));
    await screen.findByText("Started in the workspace: it continues there.");
    expect(sent("/api/ade/link/moves/31/start")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Open it in the workspace/ }));
    expect(opened).toHaveBeenCalledWith("0123456789ab");
    expect(moveButton()).toBeNull();                                    // started: it continues there, not moved twice
  });

  it("carries an uploaded move on in a workspace that runs, and says why one failed or expired", async () => {
    serve(moveRoutes([move({ state: "uploaded" })]));
    const { MoveToWorkspace } = await fresh();
    await workspaceIs();
    const { unmount } = render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(await screen.findByRole("button", { name: /Carry on in the workspace/ })).toBeTruthy();
    unmount();
    serve(moveRoutes([move({ state: "expired", reason: "Its link did not take it before it expired." })]));
    render(<MoveToWorkspace s={machineSession()} onOpen={() => {}} />);
    expect(await screen.findByText("Expired: Its link did not take it before it expired.")).toBeTruthy();
    expect(moveButton()).toBeTruthy();
  });

  it("opens the started session from the conversation: its link into the app, and the panel closes", async () => {
    serve(moveRoutes([move({ state: "started", workspace_session: "0123456789ab" })]));
    const { SessionConversation } = await fresh();
    await workspaceIs();
    const closed = vi.fn();
    render(<SessionConversation s={machineSession()} onBack={() => {}} onClose={closed} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open it in the workspace/ }));
    expect(location.hash).toBe("#session=0123456789ab");
    expect(closed).toHaveBeenCalled();
    history.replaceState(null, "", location.pathname);
  });

  const ask = (over: Partial<LinkAsk> = {}): LinkAsk => ({
    id: "A1", name: "cloud", kind: "cloud", fingerprint: "0123456789abcdef0123456789abcdef", asked_from: "203.0.113.7",
    created_at: SERVER_TIME - 60, expires_at: SERVER_TIME + 1740, session_ref: null, state: "pending", hold_id: null,
    takes_effect_at: null, told: null, failed: null, ...over,
  });
  const withAsks = (asks: LinkAsk[], links: LinkView[] = [], hold = 180): Route =>
    () => [200, { links, questions: [], public_url: "https://pp.example", asks, connect_hold: hold }];

  it("shows a session's request to be linked with its key and where it asked from, and approves it only with the account's word", async () => {
    serve(routes({
      "GET /api/ade/link/sessions": withAsks([ask()]),
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c9" }] : [403, { detail: "That did not confirm it." }]),
      "POST /api/ade/link/asks/A1/approve": (b) => (b?.confirmation === "c9" ? [200, { hold: { id: "H1", takes_effect_at: SERVER_TIME + 180 }, told: 1 }] : [403, { detail: "no" }]),
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const card = (await screen.findByText(/asks to be linked/)).closest("div.rounded-lg") as HTMLElement;
    expect(card.textContent).toContain("A session that calls itself “cloud” asks to be linked (cloud)");
    expect(card.textContent).toContain("key fingerprint 0123456789abcdef0123456789abcdef");
    expect(card.textContent).toContain("from 203.0.113.7");
    expect(card.textContent).toContain("The link is made 3 minutes after you approve it; your devices with notifications are told, and you can cancel it until then.");
    fireEvent.click(within(card).getByRole("button", { name: "Approve" }));
    const panel = await screen.findByRole("group", { name: "Approving “cloud”" });
    expect(sent("/api/ade/link/asks/A1/approve")).toHaveLength(0);
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/asks/A1/approve").map((c) => c.body)).toEqual([{ confirmation: "c9" }]));
    expect(sent("/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: "link-approve:A1" }]);
    expect(sent("/api/ade/link/asks/A1/refuse")).toHaveLength(0);
  });

  it("refuses a request with one click", async () => {
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask()]), "POST /api/ade/link/asks/A1/refuse": () => [200, { refused: "A1" }] }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Refuse" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/asks/A1/refuse")).toHaveLength(1));
    expect(sent("/api/ade/link/asks/A1/approve")).toHaveLength(0);
  });

  it("shows an approval's hold, when the link is made and who was told, and cancels it with one click", async () => {
    const at = Math.floor(Date.now() / 1000) + 180;
    serve(routes({
      "GET /api/ade/link/sessions": withAsks([ask({ state: "held", hold_id: "H1", takes_effect_at: at, told: 0 })]),
      "POST /api/ade/holds/H1/cancel": () => [200, { cancelled_at: SERVER_TIME }],
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const line = await screen.findByText(/is linked unless/);
    expect(line.textContent).toBe("Approved: “cloud” (cloud) is linked unless you cancel it first.");
    expect(screen.getByText(/^Takes effect in/).textContent).toMatch(new RegExp(`^Takes effect in [23]:\\d\\d \\(${clock(at)}\\)$`));
    expect(screen.getByText("No device of this account has notifications on: only this page shows it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await vi.waitFor(() => expect(sent("/api/ade/holds/H1/cancel")).toHaveLength(1));
  });

  it("gives a hold's day as well when it ends more than half a day away", async () => {
    const at = Math.floor(Date.now() / 1000) + 2 * 86400;
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ state: "held", hold_id: "H1", takes_effect_at: at, told: 1 })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    expect((await screen.findByText(/^Takes effect in/)).textContent).toMatch(new RegExp(`^Takes effect in 4[78]:\\d\\d:\\d\\d \\(${dayTime(at)}\\)$`));
  });

  it("says how many devices an approval's notice was sent toward, or that they could not be read, and why one was approved and not linked", async () => {
    serve(routes({
      "GET /api/ade/link/sessions": withAsks([ask({ state: "held", hold_id: "H1", takes_effect_at: SERVER_TIME + 180, told: 2 }),
        ask({ id: "A2", name: "mac", state: "failed", failed: "The account already has a live link named mac." }),
        ask({ id: "A3", name: "web", state: "held", hold_id: "H3", takes_effect_at: SERVER_TIME + 180, told: -1 })]),
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    expect(await screen.findByText("Its notice was sent toward 2 devices with notifications on.")).toBeTruthy();
    expect(screen.getByText("Its notice could not be sent. Only this page shows it.")).toBeTruthy();
    expect(screen.queryByText(/No device of this account has notifications on/)).toBeNull();
    expect(screen.getByText(/was approved, and the link could not be made: The account already has a live link named mac\./)).toBeTruthy();
  });

  it("shows a request's session reference as its own word, with the address it gives", async () => {
    const ref = "https://claude.ai/code/session_01ABC";
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ session_ref: ref }), ask({ id: "A2", name: "mac", kind: "local", session_ref: "local-1" })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    await screen.findAllByText(/asks to be linked/);
    expect(saying(`says its session is ${ref}`)).toHaveLength(1);
    expect(screen.getByRole("link", { name: ref }).getAttribute("href")).toBe(ref);
    expect(saying("says its session is local-1")).toHaveLength(1);
    expect(screen.queryByText(/its session on claude\.ai/)).toBeNull();
  });

  it("offers no Approve while a live link has the name, saying why, and counts an approved request under it as another", async () => {
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ name: "mac", kind: "local" }),
      ask({ id: "A2", state: "held", hold_id: "H2", takes_effect_at: SERVER_TIME + 180, told: 0 }), ask({ id: "A3", fingerprint: "e".repeat(32) })],
    [link({ name: "mac" })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const [mac, cloud] = (await screen.findAllByRole("button", { name: "Approve" })) as HTMLButtonElement[];
    expect(mac.disabled).toBe(true);
    expect(mac.title).toBe("A live link has this name: revoke it first, or refuse this request");
    expect(cloud.disabled).toBe(false);
    expect(cloud.title).toBe("");
    expect(saying("2 requests call themselves cloud: approve only the one whose fingerprint the session printed.")).toHaveLength(1);
  });

  /** The elements whose whole text is `text`, names in their own elements included. */
  const saying = (text: string) => screen.queryAllByText((_, el) => el?.textContent === text && el.tagName === "DIV");

  it("warns of requests under one name, and of a name a live link has, saying why a local one is not replaced", async () => {
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask(), ask({ id: "A2", fingerprint: "f".repeat(32) }), ask({ id: "A3", name: "mac", kind: "local" })],
      [link({ name: "cloud" }), link({ id: "L2", name: "mac", kind: "cloud" })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    await screen.findAllByText(/asks to be linked/);
    expect(saying("2 requests call themselves cloud: approve only the one whose fingerprint the session printed.")).toHaveLength(2);
    expect(saying("A live local link is named cloud already; a local link is never replaced. Revoke it before approving this, or refuse this.")).toHaveLength(2);
    // a local session's request under a cloud link's name replaces nothing either: the server names no link it may
    expect(saying("A live link is named mac already: revoke it before approving this, or refuse this.")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Approve and replace" })).toBeNull();
  });

  it("tells names apart as the server does, whatever their case and spaces, and shows each name's spaces", async () => {
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ name: "My Mac", kind: "local" }), ask({ id: "A2", name: "my mac", kind: "local", fingerprint: "f".repeat(32) })],
      [link({ name: "my  mac" })]) }));            // a name kept before two spaces together were refused
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    await screen.findAllByText(/asks to be linked/);
    expect(saying("2 requests call themselves My Mac: approve only the one whose fingerprint the session printed.")).toHaveLength(1);
    expect(saying("2 requests call themselves my mac: approve only the one whose fingerprint the session printed.")).toHaveLength(1);
    expect(saying("A live local link is named my  mac already; a local link is never replaced. Revoke it before approving this, or refuse this.")).toHaveLength(2);
    // its two spaces kept, not run into one
    const shown = screen.getAllByText((_, el) => el?.tagName === "BDI" && el.textContent === "my  mac");
    expect(shown.length).toBeGreaterThan(0);
    for (const el of shown) expect(el.className).toContain("whitespace-pre");
  });

  it("offers no replacement under a name spelt otherwise, and says the session asks under the link's own name", async () => {
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ name: "Cloud" }), ask({ id: "A2", name: "my cloud", fingerprint: "f".repeat(32) }),
      ask({ id: "A3", name: "Web", kind: "local", fingerprint: "e".repeat(32) })],
    [link({ name: "cloud", kind: "cloud" }), link({ id: "L2", name: "my  cloud", kind: "cloud" }), link({ id: "L3", name: "web", kind: "cloud" })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    await screen.findAllByText(/asks to be linked/);
    expect(saying("A live cloud link is named “cloud”: the names differ only in case or spacing. To replace it, the session asks under the link's own name.")).toHaveLength(1);
    // a name kept from before the rules on spaces cannot be asked under: that link is revoked first
    expect(saying("A live cloud link is named “my  cloud”: the names differ only in case or spacing. A name spaced so cannot be asked for: revoke it before approving this, or refuse this.")).toHaveLength(1);
    // a local session's request replaces nothing, whatever the spelling: asking under the link's name would not help
    expect(saying("A live link is named web already: revoke it before approving this, or refuse this.")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Approve and replace" })).toBeNull();
    for (const b of screen.getAllByRole("button", { name: "Approve" }) as HTMLButtonElement[]) expect(b.disabled).toBe(true);
  });

  // ---- a cloud session's request that may replace the live cloud link of its name ------------------------------------
  const now = () => Math.floor(Date.now() / 1000);
  const replaced = (over: Partial<NonNullable<LinkAsk["replaces"]>> = {}): NonNullable<LinkAsk["replaces"]> => ({
    id: "L9", name: "cloud", created_at: now() - 7200, last_seen_at: now() - 2400, fingerprint: "f".repeat(32), revoked_at: null, ...over,
  });

  it("shows the live cloud link a request may replace beside its key, and replaces it on a second click with the account's word", async () => {
    serve(routes({
      "GET /api/ade/link/sessions": withAsks([ask({ replaces: replaced() })], [link({ id: "L9", name: "cloud", kind: "cloud" })]),
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": (b) => (b?.password === "right one" ? [200, { confirmation: "c9" }] : [403, { detail: "That did not confirm it." }]),
      "POST /api/ade/link/asks/A1/approve": (b) => (b?.confirmation === "c9" ? [200, { hold: { id: "H1", takes_effect_at: SERVER_TIME + 180 }, told: 1 }] : [403, { detail: "no" }]),
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const card = (await screen.findByText(/asks to be linked/)).closest("div.rounded-lg") as HTMLElement;
    expect(card.textContent).toContain("A live cloud link is named cloud: linked 2h ago, last seen 40m ago.");
    expect(card.textContent).toContain(`its key ${"f".repeat(32)}new key 0123456789abcdef0123456789abcdef`);
    expect(card.textContent).toContain("If you approve and replace, that link stops working when the hold ends, and this session takes its name.");
    expect(card.textContent).not.toContain("revoke it before approving this");
    expect(within(card).queryByRole("button", { name: "Approve" })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Approve and replace" }));
    expect(screen.queryByRole("group", { name: "Replacing “cloud”" })).toBeNull();          // a second click first
    fireEvent.click(within(card).getByRole("button", { name: "Click again to replace cloud" }));
    const panel = await screen.findByRole("group", { name: "Replacing “cloud”" });
    expect(sent("/api/ade/link/asks/A1/approve")).toHaveLength(0);
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/asks/A1/approve").map((c) => c.body)).toEqual([{ confirmation: "c9", replace: "L9" }]));
    // made for approving this request in place of that link
    expect(sent("/api/account/confirm").map((c) => c.body)).toEqual([{ password: "right one", change: "link-approve:A1:L9" }]);
  });

  it("names the link shown when the owner clicked, whatever a later read shows", async () => {
    let shown = replaced();
    serve(routes({
      "GET /api/ade/link/sessions": () => [200, { links: [], questions: [], public_url: "https://pp.example", asks: [ask({ replaces: shown })], connect_hold: 180 }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "c9" }],
      "POST /api/ade/link/asks/A1/approve": () => [409, { detail: "The live link named cloud is not the one this approval replaces: read Linked sessions again." }],
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve and replace" }));
    fireEvent.click(screen.getByRole("button", { name: "Click again to replace cloud" }));
    const panel = await screen.findByRole("group", { name: "Replacing “cloud”" });
    shown = replaced({ id: "L10", fingerprint: "e".repeat(32) });            // another cloud link took the name since
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await screen.findByText(`its key ${"e".repeat(32)}`);
    fireEvent.change(within(panel).getByLabelText("Your password"), { target: { value: "right one" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(sent("/api/ade/link/asks/A1/approve").map((c) => c.body)).toEqual([{ confirmation: "c9", replace: "L9" }]));
    expect(await within(panel).findByText(/is not the one this approval replaces/)).toBeTruthy();
  });

  it("shows a replacement's hold: what it replaces, its old key and the new, and that the old link stops then", async () => {
    const at = now() + 180;
    serve(routes({ "GET /api/ade/link/sessions": withAsks([ask({ state: "held", hold_id: "H1", takes_effect_at: at, told: 1, replaces: replaced() }),
      ask({ id: "A2", name: "mac", state: "held", hold_id: "H2", takes_effect_at: at, told: 1, replaces: replaced({ id: "L8", name: "mac", revoked_at: now() - 5 }) }),
      ask({ id: "A3", name: "web", state: "failed", failed: "The account already has a live link named web.", replaces: replaced({ id: "L7", name: "web" }) })]) }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    const [first, second] = await screen.findAllByText(/replaces the link of that name unless/);
    expect(first.textContent).toBe("Approved: “cloud” (cloud) replaces the link of that name unless you cancel it first.");
    const card = first.closest("div.rounded-lg") as HTMLElement;
    expect(card.textContent).toContain("The link it replaces was linked 2h ago; it stops working then.");
    expect(card.textContent).toContain(`old key ${"f".repeat(32)}new key 0123456789abcdef0123456789abcdef`);
    expect((second.closest("div.rounded-lg") as HTMLElement).textContent).toContain("The link it replaces was linked 2h ago; it was revoked since.");
    expect(screen.getByText(/was approved to replace the link of that name, and the link could not be made: The account already has a live link named web\./)).toBeTruthy();
  });

  it("shows the ask address and the command that asks with it, and replaces it on a second click", async () => {
    const first = `ask-${"a".repeat(20)}`, next = `ask-${"b".repeat(20)}`;
    serve(routes({
      "GET /api/ade/link/address": () => [200, { address: first, created_at: SERVER_TIME, url: "https://pp.example" }],
      "POST /api/ade/link/address": () => [200, { address: next, created_at: SERVER_TIME, url: "https://pp.example", refused: 1 }],
    }));
    const { Channel } = await fresh(false);
    render(<Channel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Ask address/ }));
    expect((await screen.findByLabelText("Your ask address")).textContent).toBe(first);
    expect(screen.getByText(/python3 -I "\$HOME\/\.proofpane-ade\/ade_link\.py" pair --url https:\/\/pp.example --ask ask-a{20} --name NAME --kind cloud --ref REF/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(sent("/api/ade/link/address")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Click again: the requests waiting on it are refused/ }));
    await vi.waitFor(() => expect(screen.getByLabelText("Your ask address").textContent).toBe(next));
    expect(sent("/api/ade/link/address")).toHaveLength(1);
  });
});

describe("the command a session asks with", () => {
  it("says a hold's length in words", () => {
    expect([waitWords(180), waitWords(60), waitWords(90), waitWords(1), waitWords(0)]).toEqual(["3 minutes", "1 minute", "90 seconds", "1 second", "0 seconds"]);
  });

  it("names the address the ADE signs for and the account's ask address, and no secret", () => {
    const lines = askLines("https://pp.example", `ask-${"c".repeat(20)}`) ?? [];
    expect(lines[0]).toBe(`python3 -I "$HOME/.proofpane-ade/ade_link.py" pair --url https://pp.example --ask ask-${"c".repeat(20)} --name NAME --kind cloud --ref REF`);
    expect(lines.join("\n")).not.toMatch(/--ticket|--keys/);
    expect(lines.some((l) => l.includes("compare the fingerprints it prints"))).toBe(true);
  });

  it("counts each request waiting for an answer among what needs the owner, and not one approved", () => {
    const q = { to: "owner", link_id: "x" } as unknown as LinkSessions["questions"][number];
    const asks = [{ state: "pending" }, { state: "pending" }, { state: "held" }, { state: "failed" }] as LinkAsk[];
    expect(channelAlerts({ links: [], questions: [q], public_url: "", asks })).toBe(3);
    expect(channelAlerts({ links: [], questions: [], public_url: "" })).toBe(0);
  });
});


// The characters the server refuses as not shown as themselves (link_commands.hidden_character), written by code point
// so no editor or transport turns them into themselves.
const HEART = String.fromCodePoint(0x2764);
const VS16 = String.fromCodePoint(0xfe0f);
const VS15 = String.fromCodePoint(0xfe0e);

describe("characters not shown as themselves", () => {
  const hidden = [0xe0041, 0xe0001, 0x00ad, 0x200e, 0x200f, 0x061c, 0x2062, 0xfe00, 0xfe0d, 0xe0100, 0xe000, 0x202e,
    0x2066, 0x200b, 0xfeff,
    // drawn as nothing, or ending a line
    0x034f, 0x115f, 0x1160, 0x17b4, 0x17b5, 0x180b, 0x180c, 0x180d, 0x180e, 0x180f, 0x2800, 0x3164, 0xffa0, 0x2028,
    0x2029];

  it("are refused before signing, each named, as the server refuses them", () => {
    for (const code of hidden) {
      const problem = commandProblem(`run the tests${String.fromCodePoint(code)} now`);
      expect(problem, code.toString(16)).toContain(`U+${code.toString(16).toUpperCase().padStart(4, "0")}`);
    }
    for (const text of [`${VS16}ship it`, `${HEART}${VS16}${VS16}`, `${HEART}${VS15}${VS16}`]) {
      expect(commandProblem(text), text).not.toBeNull();
    }
  });

  it("let the text and emoji selectors through once after a character", () => {
    expect(commandProblem(`ship it ${HEART}${VS16}`)).toBeNull();
    expect(commandProblem(`x${VS15} y${VS16}`)).toBeNull();
  });

  it("are shown as code points wherever a session's text is shown", () => {
    const tag = String.fromCodePoint(0xe0041);
    expect(visible(`a${tag}b${String.fromCodePoint(0x00ad)}c`)).toBe("a[U+E0041]b[U+00AD]c");
    expect(visible(`${HEART}${VS16}`)).toBe(`${HEART}${VS16}`);
    expect(visible(`${VS16}x`)).toBe("[U+FE0F]x");
  });

  it("count against the limit in the form that is signed", () => {
    const qa = String.fromCodePoint(0x958);           // a letter normal form C writes as two
    expect(qa.normalize("NFC").length).toBe(2);
    expect(commandProblem(qa.repeat(2001))).toBe("A command is at most 4,000 characters.");
    expect(commandProblem(qa.repeat(2000))).toBeNull();
  });
});

describe("marks drawn over or around a character", () => {
  it("are refused before signing, each named, as the server refuses them; an ordinary mark is not", () => {
    for (const code of [0x0336, 0x0337, 0x0338, 0x20dd, 0x20de, 0x20df, 0x20e0, 0x20e2, 0x20e3, 0x20e4, 0x0488, 0xa670]) {
      expect(commandProblem(`do x${String.fromCodePoint(code)} now`), code.toString(16))
        .toBe(`The text holds U+${code.toString(16).toUpperCase().padStart(4, "0")}, a mark drawn over or around the character before it, so it could change how it reads.`);
    }
    for (const code of [0x0301, 0x20e1]) expect(commandProblem(`do x${String.fromCodePoint(code)} now`)).toBeNull();
  });
});

describe("words shaped as an encrypted text", () => {
  it("are refused before signing, as the server refuses them: a command is never sealed", () => {
    expect(commandProblem("e2e1.AAAA")).toBe("The text begins e2e1., as an encrypted text does: one in the clear may not, so it is never read as one.");
    expect(commandProblem("note: e2e1.AAAA")).toBeNull();
  });
});


describe("what is shaped like a secret", () => {
  const shapes: [string, string][] = [
    ["a key sk-ant-api03-abc", "an Anthropic key (sk-ant-)"],
    [`a key sk-${"a".repeat(20)}`, "a key (sk- and 16 or more characters)"],
    ...["ghp_x", "gho_x", "ghu_x", "ghs_x", "ghr_x"].map((t): [string, string] => [t, "a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)"]),
    ["github_pat_x", "a GitHub token (github_pat_)"],
    ["FlyV1 fm2_x", "a Fly token (FlyV1)"],
    ["AKIAxyz", "an AWS key (AKIA)"],
    ["-----BEGIN OPENSSH PRIVATE KEY-----", "a private key (-----BEGIN ... PRIVATE KEY)"],
    ["adeh_x", "an ADE hook token (adeh_)"],
    ["pair with pp-abcdefgh now", "an ADE ticket (PP-)"],
    ["a sk_live_x key", "a Stripe key (sk_live_, rk_live_)"],
    ["xoxb-123", "a Slack token (xoxb-, xoxp-, ...)"],
    [`AIza${"x".repeat(30)}`, "a Google API key (AIza)"],
    ["glpat-x", "a GitLab token (glpat-)"],
    [`npm_${"a".repeat(36)}`, "an npm token (npm_)"],
    ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig", "a signed web token (eyJ...)"],
  ];

  it("is refused before signing, named as the server names it", () => {
    for (const [text, shape] of shapes) {
      expect(commandProblem(text), text).toBe(`The text holds something shaped like ${shape}; the ADE keeps no secret.`);
    }
  });

  it("leaves words that only look near it alone", () => {
    for (const text of ["ask-anthropic about it", "sk-short", "the ghost_ship", "BEGIN the work"]) {
      expect(commandProblem(text), text).toBeNull();
    }
  });
});

describe("a linked session's conversation", () => {
  const ev = (seq: number, over: Partial<LinkEvent>): LinkEvent =>
    ({ seq, time: "2026-10-03T00:00:00Z", kind: "note", from: "mac", to: "owner", ref: null, text: "", ...over });

  it("shows a live view's round, one note, as the lines it holds in their order, each its own row", () => {
    // One event a round
    const held = { items: [["owner", "go"], ["session", "Reading."], ["calls", ["Bash: cat a.md", "Bash: ls"]],
      ["session", "Done."]] };
    // a note after it: the round stands, as the ADE does not say a Stop posted it again (posted_again)
    const got = conversation([ev(4, { mirror: "live", text: JSON.stringify(held) }),
      ev(5, { mirror: "session", text: "After." })], "mac");
    expect(got.map((s) => [s.who, s.text])).toEqual([["you", "go"], ["it", "Reading."], ["step", "Bash: cat a.md\nBash: ls"],
      ["it", "Done."], ["it", "After."]]);
    expect(new Set(got.map((s) => `${s.seq}.${s.sub ?? 0}`)).size).toBe(got.length);   // a key of its own each
    // a message in numbered parts across two rounds' notes is one again; a note that is not one round's items says so
    const part = (n: number, words: string) => JSON.stringify({ items: [["session", `(${n} of 2) ${words}`]] });
    expect(conversation([ev(6, { mirror: "live", text: part(1, "first half, ") }), ev(7, { mirror: "live", text: part(2, "second half.") })], "mac")
      .map((s) => s.text)).toEqual(["first half, second half."]);
    expect(conversation([ev(8, { mirror: "live", text: "not a round" })], "mac").map((s) => [s.who, s.text]))
      .toEqual([["info", "A live update that could not be read."]]);
  });

  it("shows the live view's steps (mirror step) between the session's words; a step it posted itself stays in its log", () => {
    // What the session ran while its turn runs, as the live view posts it
    const got = conversation([
      ev(1, { mirror: "owner", text: "how is the build?" }),
      ev(2, { mirror: "session", text: "Checking the tests." }),
      ev(3, { mirror: "step", text: "Bash: pytest -q\nRead: /work/app.py" }),
      ev(4, { kind: "step", text: "a step, in the log only" }),
      ev(5, { mirror: "session", text: "12 passed." }),
    ], "mac");
    expect(got.map((s) => [s.seq, s.who, s.text])).toEqual([
      [1, "you", "how is the build?"], [2, "it", "Checking the tests."], [3, "step", "Bash: pytest -q\nRead: /work/app.py"],
      [5, "it", "12 passed."]]);
  });

  it("says a live view's round on the card by its last line: a call, or the session's words", () => {
    const held = (items: unknown[]) => JSON.stringify({ items });
    expect(latestSaid({ kind: "note", to: "owner", ref: null, mirror: "live" }, { commands: [] },
      held([["session", "Reading."], ["calls", ["Bash: ls", "Bash: pytest -q"]]]))).toBe("Called: Bash: pytest -q");
    expect(latestSaid({ kind: "note", to: "owner", ref: null, mirror: "live" }, { commands: [] },
      held([["calls", ["Bash: ls"]], ["session", "All green."]]))).toBe("Said: All green.");
  });

  it("says a step note on the card as a call the session made, its newest line: a call is made before it is let run", () => {
    expect(latestSaid({ kind: "note", to: "owner", ref: null, mirror: "step" }, { commands: [] }, "Bash: ls\nBash: pytest -q"))
      .toBe("Called: Bash: pytest -q");
  });

  it("is the mirror's notes, the owner's commands, the replies to them and the questions, oldest first", () => {
    const got = conversation([
      ev(5, { kind: "done", ref: 4, text: "done: shipped" }),
      ev(1, { mirror: "owner", text: "deploy?" }),
      ev(2, { mirror: "session", text: "Deploying." }),
      ev(3, { kind: "step", text: "a step" }),
      ev(4, { from: "owner", to: "mac", kind: "instruction", text: "go", state: "taken" }),
      ev(6, { kind: "question", to: "owner", text: "which tag?" }),
      ev(7, { kind: "question", to: "cloud", text: "for another session" }),
      ev(8, { kind: "note", text: "a plain note" }),
      ev(9, { mirror: "info", text: "3 earlier messages of this session not mirrored" }),
      ev(10, { from: "owner", to: "mac", kind: "answer", ref: 6, text: "v2", state: "answered" }),
    ], "mac");
    expect(got.map((s) => [s.seq, s.who, s.head, s.text])).toEqual([
      [1, "you", "You, in mac", "deploy?"], [2, "it", "mac", "Deploying."], [4, "you", "You signed an instruction", "go"],
      [5, "it", "mac replied to [4]", "done: shipped"], [6, "it", "mac asks you", "which tag?"],
      [9, "info", "", "3 earlier messages of this session not mirrored"], [10, "you", "You answered [6]", "v2"]]);
    expect(got.find((s) => s.seq === 4)?.state).toBe("taken");
  });

  it("joins a message posted in numbered parts, and only a mirrored one", () => {
    const got = conversation([
      ev(1, { mirror: "session", text: "(1 of 3) abc" }), ev(2, { mirror: "session", text: "(2 of 3) def" }),
      ev(3, { mirror: "session", text: "(3 of 3) ghi [... 10 more characters not mirrored]" }),
      ev(4, { mirror: "owner", text: "(2 of 2) not after its first part" }),
      ev(5, { kind: "done", ref: 1, text: "(1 of 2) a reply's own words" }),
    ], "mac");
    expect(got.map((s) => s.text)).toEqual(["abcdefghi [... 10 more characters not mirrored]",
      "not after its first part", "(1 of 2) a reply's own words"]);
  });

  it("says how long ago in words, never 'now ago'", () => {
    const now = Date.parse("2026-10-04T00:00:00Z");
    expect(ago("2026-10-03T23:59:50Z", now)).toBe("just now");
    expect(ago("2026-10-03T23:55:00Z", now)).toBe("5m ago");
    expect(ago(undefined, now)).toBe("");
  });
});

describe("a command's state", () => {
  it("reads 'replied' for what the session says it answered, its word, in no colour of success", () => {
    expect([stateWord("answered"), stateWord("queued"), stateWord(undefined)]).toEqual(["replied", "queued", ""]);
    const l = { id: "L1", name: "mac", kind: "local", session_ref: null, interval_min: 5, created_at: 1, last_seen_at: null,
      last_heartbeat_at: null, silent: false, acked_seq: 0, checkpoint: null, checkpoint_version: 0, checkpoint_at: null,
      events: 0, last_event: null, stopped_at: null,
      commands: [{ seq: 7, time: "2026-10-03T00:00:00Z", kind: "instruction", from: "owner", to: "mac", ref: null, text: "ship it", state: "answered" as const }] };
    render(<Commands l={l} />);
    const pill = screen.getByText("replied");
    expect(pill.className).not.toMatch(/text-done/);
    expect(pill.getAttribute("title")).toBe("The session says it replied: its word, not checked");
  });
});

describe("what a session's text shows", () => {
  it("writes every control and separator but a space, a line break and a tab as a code point, as the server's hides does", () => {
    expect(visible("a\u001bb\u0008c\u0085d\u00a0e\u2007f\u202eg")).toBe("a[U+001B]b[U+0008]c[U+0085]d\u00a0e\u2007f[U+202E]g");
    // the spaces that show as spaces, and a carriage return, are left as they are: an input method's full-width
    // space, the narrow one before PM; a line or paragraph separator, which breaks a line unseen, is not
    expect(visible("x\u3000y\u202fPM\r\nz")).toBe("x\u3000y\u202fPM\r\nz");
    expect(visible("a\u2028b\u2029c\u200bd")).toBe("a[U+2028]b[U+2029]c[U+200B]d");
    expect(visible("one line\nand\ta tab")).toBe("one line\nand\ta tab");
  });
});


describe("the command that links a session", () => {
  const out = { ticket: "PP-ABCDEFGH", expires_at: 1_790_000_000, url: "https://pp.example" };
  it("carries the account's passkeys, so the session pins them as it links", () => {
    expect(pairLine({ ...out, keys: ["a".repeat(32), "b".repeat(32), "not a key; rm -rf"] }, { name: "mac", kind: "local" })).toBe(
      `python3 -I "$HOME/.proofpane-ade/ade_link.py" setup --url https://pp.example --ticket PP-ABCDEFGH --keys ${"a".repeat(32)},${"b".repeat(32)} --name 'mac' --kind local --mirror`);
  });
  it("leaves --keys out when the account has no passkey yet", () => {
    expect(pairLine({ ...out, keys: [] }, { name: "mac", kind: "local" })).not.toContain("--keys");
  });
});


describe("the line that pins a passkey added later", () => {
  it("names the link's directory, so it runs as copied once NAME is the link's name, with the installed client", async () => {
    const { pinLine } = await import("./AccountDialog");
    expect(pinLine("a".repeat(32))).toBe(
      `python3 -I "$HOME/.proofpane-ade/ade_link.py" --dir "$HOME/.proofpane-ade/NAME" pin --add ${"a".repeat(32)}`);
  });
});
