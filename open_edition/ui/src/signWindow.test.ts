// A signing window's page key and its use (signWindow.ts): made so it cannot be exported, kept in this tab's memory
// alone, used only within the window by this page's own clock and never in its last minute, its place moved only on the
// server's 2xx, and dropped at every end.
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonicalJson } from "./passkey";

type Answer = [number, unknown] | "unreachable";
type Route = (body: Record<string, unknown> | undefined) => Answer | Promise<Answer>;
const L = "1".repeat(32);
const FP = "f".repeat(32);
const calls: { method: string; path: string; body?: Record<string, unknown> }[] = [];
const order: string[] = [];
const now = () => Math.floor(Date.now() / 1000);
const nonceAt = (at: () => number, credentials: string[] = []): Route => (b) =>
  [200, { nonce: `n-${String(b?.purpose)}`, rp_id: "pp.example", deployment: "pp.example", account: "u", server_time: at(), credentials }];

function serve(routes: Record<string, Route>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: String(url), body });
    order.push(`${method} ${url}`);
    const route = routes[`${method} ${url}`];
    const answer = route ? await route(body) : [404, { detail: `no route for ${method} ${url}` }] as Answer;
    if (answer === "unreachable") throw new TypeError("Failed to fetch");
    return new Response(JSON.stringify(answer[1]), { status: answer[0] });
  }));
}

const credentials = {
  get: vi.fn(async () => {
    order.push("get");
    const b = (n: number) => new Uint8Array([n]).buffer;
    return { id: "AQ", type: "public-key", response: { clientDataJSON: b(1), authenticatorData: b(2), signature: b(3), userHandle: b(4) } };
  }),
};

async function fresh() {
  vi.resetModules();
  vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
  Object.defineProperty(navigator, "credentials", { value: credentials, configurable: true });
  return import("./signWindow");
}

const fromB64u = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
const sha256 = async (text: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const delegations = (r: Record<string, Route> = {}): Record<string, Route> => ({
  "POST /api/ade/link/nonce": nonceAt(now),
  "POST /api/ade/link/delegations": async (b) => [200, {
    digest: hex(await sha256(`proofpane-delegation/1\0${canonicalJson(b?.payload)}`)),
    expires_at: (b?.payload as { expires_at: number }).expires_at }],
  ...r,
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  calls.length = 0; order.length = 0;
  credentials.get.mockClear();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
});

describe("a signing window's page key", () => {
  it("is made so that it cannot be exported, and its delegation names that key, this link and its key alone", async () => {
    serve(delegations());
    const w = await fresh();
    const p = await w.prepareWindow(L, FP, 900);
    expect(p.key.extractable).toBe(false);
    await expect(crypto.subtle.exportKey("pkcs8", p.key)).rejects.toBeTruthy();
    const payload = p.prepared.payload as Record<string, unknown> & { delegate: { key: string }; created_at: number; expires_at: number };
    expect(Object.keys(payload).sort()).toEqual(["account", "created_at", "delegate", "deployment", "expires_at", "id", "nonce",
      "purpose", "sessions", "v"]);
    expect(payload).toMatchObject({ v: 2, purpose: "delegation", nonce: "n-delegation", account: "u", deployment: "pp.example",
      sessions: [{ id: L, fingerprint: FP, kinds: ["instruction"] }] });
    expect(payload.expires_at - payload.created_at).toBe(900);
    const spki = await crypto.subtle.importKey("spki", fromB64u(payload.delegate.key), { name: "ECDSA", namedCurve: "P-256" }, true, ["verify"]);
    expect((spki.algorithm as EcKeyAlgorithm).namedCurve).toBe("P-256");
    expect({ ...payload.delegate, key: "" }).toEqual({ kind: "page-key", alg: -7, key: "" });
    expect(new Uint8Array(p.prepared.challenge)).toEqual(await sha256(`proofpane-step/1\0${canonicalJson(payload)}`));
    expect(calls.map((c) => c.body)).toEqual([{ purpose: "delegation" }]);
  });

  it("is refused where the browser would make one that can be copied out", async () => {
    serve(delegations());
    const w = await fresh();
    const real = crypto.subtle.generateKey.bind(crypto.subtle);
    vi.spyOn(crypto.subtle, "generateKey").mockImplementation((async (alg: EcKeyGenParams, _ex: boolean, use: KeyUsage[]) =>
      real(alg, true, use)) as unknown as typeof crypto.subtle.generateKey);
    await expect(w.prepareWindow(L, FP, 300)).rejects.toThrow(/could be copied out/);
    expect(calls).toEqual([]);                                   // nothing asked of the server for it
  });

  it.each([[61, false], [-61, false], [59, true], [-59, true]])("opens only within a minute of the server's clock (%i s)", async (off, ok) => {
    serve(delegations({ "POST /api/ade/link/nonce": nonceAt(() => now() + off) }));
    const w = await fresh();
    const made = w.prepareWindow(L, FP, 300);
    if (ok) await expect(made).resolves.toBeTruthy();
    else await expect(made).rejects.toThrow(w.CLOCK_WORDS);
  });

  it("lasts 5 or 15 minutes and no other length", async () => {
    serve(delegations());
    const w = await fresh();
    for (const life of [0, 60, 299, 301, 600, 901]) await expect(w.prepareWindow(L, FP, life as 300)).rejects.toThrow(/5 or 15 minutes/);
  });
});

describe("opening it", () => {
  it("asks the passkey first, over the delegation's step challenge, and keeps the key only under the digest the server names", async () => {
    serve(delegations());
    const w = await fresh();
    const p = await w.prepareWindow(L, FP, 300);
    order.length = 0;
    const opened = await w.openWindow(p);
    expect(order[0]).toBe("get");                                // the prompt before anything is fetched
    const posted = calls.find((c) => c.path === "/api/ade/link/delegations")!;
    expect(posted.body?.payload).toEqual(p.prepared.payload);
    expect(opened.digest).toBe(hex(await sha256(`proofpane-delegation/1\0${canonicalJson(p.prepared.payload)}`)));
    expect(w.windowOf(L)?.digest).toBe(opened.digest);
    expect(w.windowOf(L)?.n).toBe(0);
  });

  it("keeps nothing when the server names another window than the one signed", async () => {
    serve(delegations({ "POST /api/ade/link/delegations": () => [200, { digest: "0".repeat(64), expires_at: now() + 300 }] }));
    const w = await fresh();
    await expect(w.openWindow(await w.prepareWindow(L, FP, 300))).rejects.toThrow(/another window/);
    expect(w.windowOf(L)).toBeNull();
  });

  it("keeps nothing when the server refuses it (a link whose client does not take windows, high security on)", async () => {
    for (const status of [409, 403]) {
      serve(delegations({ "POST /api/ade/link/delegations": () => [status, { detail: "refused" }] }));
      const w = await fresh();
      await expect(w.openWindow(await w.prepareWindow(L, FP, 300))).rejects.toThrow("refused");
      expect(w.windowOf(L)).toBeNull();
    }
  });
});

describe("an instruction under it", () => {
  const opened = async (more: Record<string, Route> = {}) => {
    serve(delegations(more));
    const w = await fresh();
    const p = await w.prepareWindow(L, FP, 300);
    await w.openWindow(p);
    return { w, p, digest: w.windowOf(L)!.digest };
  };
  const commands = () => calls.filter((c) => c.method === "POST" && c.path === "/api/ade/link/commands");

  it("is signed by the page key over the tag, a zero byte and its canonical bytes, in its window, ending with it", async () => {
    const { w, p, digest } = await opened({ "POST /api/ade/link/commands": () => [200, { seq: 7, expires_at: 0 }] });
    expect(await w.sendInWindow(L, "run the tests")).toEqual({ seq: 7, n: 1 });
    expect(credentials.get).toHaveBeenCalledTimes(1);           // the opening's touch alone
    const [{ body }] = commands();
    const payload = body?.payload as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(["account", "created_at", "delegation", "deployment", "expires_at", "id", "n",
      "nonce", "purpose", "text", "to", "v"]);
    expect(payload).toMatchObject({ purpose: "instruction", to: L, text: "run the tests", delegation: digest, n: 1,
      nonce: "n-instruction", expires_at: p.prepared.payload.expires_at });
    expect(payload.created_at as number).toBeGreaterThanOrEqual(p.prepared.payload.created_at as number);
    const delegated = body?.delegated as { digest: string; sig: string };
    expect(delegated.digest).toBe(digest);
    const key = await crypto.subtle.importKey("spki", fromB64u((p.prepared.payload.delegate as { key: string }).key),
      { name: "ECDSA", namedCurve: "P-256" }, true, ["verify"]);
    const signed = new TextEncoder().encode(`proofpane-delegated/1\0${canonicalJson(payload)}`);
    expect(fromB64u(delegated.sig).length).toBe(64);
    expect(await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, fromB64u(delegated.sig), signed)).toBe(true);
    expect(await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, fromB64u(delegated.sig),
      new TextEncoder().encode(canonicalJson(payload)))).toBe(false);   // the tag is part of what is signed
  });

  it("moves its place only on the server's 2xx", async () => {
    let status = 429;
    const { w } = await opened({ "POST /api/ade/link/commands": () => [status, status === 200 ? { seq: 8 } : { detail: "too many" }] });
    await expect(w.sendInWindow(L, "one", async () => ({ digest: w.windowOf(L)!.digest, last_n: 0, ids: [] }))).rejects.toThrow("too many");
    expect(w.windowOf(L)?.n).toBe(0);
    status = 200;
    expect((await w.sendInWindow(L, "two")).n).toBe(1);
    expect((commands().at(-1)!.body?.payload as { n: number }).n).toBe(1);
  });

  it("settles an answer that did not come from the links list: this instruction among the link's commands, stored", async () => {
    let reach = false;
    const { w, digest } = await opened({ "POST /api/ade/link/commands": () => (reach ? [200, { seq: 9 }] : "unreachable") });
    const mine = () => [(commands()[0].body?.payload as { id: string }).id];
    expect(await w.sendInWindow(L, "one", async () => ({ digest, last_n: 1, ids: mine() }))).toEqual({ seq: 0, n: 1 });
    reach = true;
    expect(await w.sendInWindow(L, "two")).toEqual({ seq: 9, n: 2 });
    expect(commands().map((c) => (c.body?.payload as { n: number }).n)).toEqual([1, 2]);
  });

  // only an answer that did not come is settled, and only from this window's place
  it("never reports as sent a send the server refused for its place, and takes the place the server names", async () => {
    let refuse = true;
    const { w, digest } = await opened({ "POST /api/ade/link/commands": () => (refuse
      ? [409, { detail: "The window's next instruction is 2.", why: "delegation_n", last_n: 1 }] : [200, { seq: 12 }]) });
    await expect(w.sendInWindow(L, "two", async () => ({ digest, last_n: 1, ids: [] }))).rejects.toThrow(/^Not sent/);
    expect(w.windowOf(L)?.n).toBe(1);                   // an earlier send was stored after all: the next follows it
    refuse = false;
    expect(await w.sendInWindow(L, "two again")).toEqual({ seq: 12, n: 2 });
    expect(commands().map((c) => (c.body?.payload as { n: number }).n)).toEqual([1, 2]);
  });

  // the place alone misleads: a first send stored with its answer lost and its settle unread leaves this page a place
  // behind; the next send at that place is refused (409) with its answer lost as well, and the links list's last_n is
  // then that place, the first send's and not this one's
  it("settles an answer that did not come as stored only when the links list holds this very instruction", async () => {
    let lost = 0;
    const { w, digest } = await opened({ "POST /api/ade/link/commands": () => { lost += 1; return "unreachable"; } });
    await expect(w.sendInWindow(L, "one", async () => null)).rejects.toThrow();     // stored, though this page cannot tell
    const first = (commands()[0].body?.payload as { id: string }).id;
    await expect(w.sendInWindow(L, "two", async () => ({ digest, last_n: 1, ids: [first] }))).rejects.toThrow();
    expect(lost).toBe(2);
    expect(w.windowOf(L)?.n).toBe(1);                   // the first's place, the server's: the next send follows it
  });

  it("settles an answer that did not come only from this window's own place, never another window's", async () => {
    const { w } = await opened({ "POST /api/ade/link/commands": () => "unreachable" });
    // this instruction's id listed, under another window's digest: the digest alone refuses it
    const ids = () => [(commands()[0].body?.payload as { id: string }).id];
    await expect(w.sendInWindow(L, "one", async () => ({ digest: "e".repeat(64), last_n: 5, ids: ids() }))).rejects.toThrow();
    expect(w.windowOf(L)?.n).toBe(0);
  });

  it("asks nothing of the links list for a refusal that came: only an answer that did not is settled", async () => {
    const { w, digest } = await opened({ "POST /api/ade/link/commands": () => [422, { detail: "not within the window" }] });
    const settle = vi.fn(async () => ({ digest, last_n: 1, ids: [] as string[] }));
    await expect(w.sendInWindow(L, "one", settle)).rejects.toThrow("not within the window");
    expect(settle).not.toHaveBeenCalled();
    expect(w.windowOf(L)?.n).toBe(0);
  });

  it("drops the window when the server says it ended (410), the link is gone (404) or the sign-in is (401)", async () => {
    for (const status of [410, 404, 401]) {
      const { w } = await opened({ "POST /api/ade/link/commands": () => [status, { detail: "ended" }] });
      await expect(w.sendInWindow(L, "one")).rejects.toMatchObject({ ended: true });
      expect(w.windowOf(L)).toBeNull();
    }
  });

  it("is not sent in the window's last minute by this page's clock, nor after its end", async () => {
    const { w } = await opened({ "POST /api/ade/link/commands": () => [200, { seq: 7 }] });
    const t0 = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(t0 + 241_000);
    calls.length = 0;
    await expect(w.sendInWindow(L, "late")).rejects.toThrow(w.LAST_MINUTE_WORDS);
    expect(calls).toEqual([]);                                   // no nonce, nothing signed
    vi.spyOn(Date, "now").mockReturnValue(t0 + 300_000);
    expect(w.windowOf(L)).toBeNull();
    await expect(w.sendInWindow(L, "later")).rejects.toThrow(w.ENDED_WORDS);
    expect(calls).toEqual([]);
  });
});

describe("its ends in this tab", () => {
  const opened = async (more: Record<string, Route> = {}) => {
    serve(delegations(more));
    const w = await fresh();
    await w.openWindow(await w.prepareWindow(L, FP, 300));
    return w;
  };

  it("drops the key before End now is posted", async () => {
    let heldWhenPosted: unknown = "not posted";
    const w = await opened();
    const digest = w.windowOf(L)!.digest;
    serve({ [`POST /api/ade/link/delegations/${digest}/end`]: () => { heldWhenPosted = w.windowOf(L); return [200, { ended_at: now() }]; } });
    await w.endWindow(L, digest);
    expect(heldWhenPosted).toBeNull();
  });

  it("drops every key on pagehide: a page brought back holds none", async () => {
    const w = await opened();
    window.dispatchEvent(new Event("pagehide"));
    expect(w.windowOf(L)).toBeNull();
  });

  // the key outlives none of the ends the page can see
  it("keeps no key from an opening the server answered after the page was put away, and posts its end", async () => {
    let answer: (v: unknown) => void = () => undefined;
    const late = new Promise((resolve) => { answer = resolve; });
    serve(delegations({ "POST /api/ade/link/delegations": async (b) => {
      await late;
      return [200, { digest: hex(await sha256(`proofpane-delegation/1\0${canonicalJson(b?.payload)}`)),
        expires_at: (b?.payload as { expires_at: number }).expires_at }];
    } }));
    const w = await fresh();
    const opening = w.openWindow(await w.prepareWindow(L, FP, 300));
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/ade/link/delegations")).toBe(true));
    window.dispatchEvent(new Event("pagehide"));
    answer(null);
    await expect(opening).rejects.toThrow();
    expect(w.windowOf(L)).toBeNull();
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && /\/delegations\/[0-9a-f]{64}\/end$/.test(c.path))).toBe(true));
  });

  it("sends nothing when the window was dropped while the instruction was being signed", async () => {
    const w = await opened();
    serve({ "POST /api/ade/link/nonce": nonceAt(now), "POST /api/ade/link/commands": () => [200, { seq: 3 }] });
    const sign = crypto.subtle.sign.bind(crypto.subtle);
    vi.spyOn(crypto.subtle, "sign").mockImplementation(async (...args: Parameters<SubtleCrypto["sign"]>) => {
      w.dropWindow(L);                                           // End now in this tab, mid-signature
      return sign(...args);
    });
    await expect(w.sendInWindow(L, "one")).rejects.toMatchObject({ ended: true });
    expect(calls.filter((c) => c.path === "/api/ade/link/commands")).toEqual([]);
  });

  it("drops a window the links list no longer shows at the next refresh, with no card on the screen", async () => {
    const w = await opened();
    const { useStore } = await import("./store");
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    serve({ "GET /api/ade/link/sessions": () => [200, { links: [{ id: L, delegation: null }], questions: [], public_url: "https://pp.example" }] });
    await useStore.getState().refreshLinks();
    expect(w.windowOf(L)).toBeNull();
  });

  // without a window opened during a read, a version that does not read the opening's time against the list's would
  // pass the case below
  it("keeps a window opened while the links list was being read, which that list could not show", async () => {
    let release = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    serve(delegations({ "GET /api/ade/link/sessions": async () => {
      await gate;
      return [200, { links: [{ id: L, delegation: null }], questions: [], public_url: "https://pp.example" }];
    } }));
    const w = await fresh();
    const { useStore } = await import("./store");
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    const reading = useStore.getState().refreshLinks();
    await w.openWindow(await w.prepareWindow(L, FP, 300));
    release();
    await reading;
    expect(w.windowOf(L)).not.toBeNull();
  });

  it("keeps a window the links list shows", async () => {
    const w = await opened();
    const digest = w.windowOf(L)!.digest;
    const { useStore } = await import("./store");
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    serve({ "GET /api/ade/link/sessions": () => [200, { links: [{ id: L, delegation: { digest, created_at: 1, expires_at: now() + 200, last_n: 0, untaken: 0 } }], questions: [], public_url: "https://pp.example" }] });
    await useStore.getState().refreshLinks();
    expect(w.windowOf(L)?.digest).toBe(digest);
  });

  it("ends each window at this page's sign-out, the end posted with the sign-in it had", async () => {
    const w = await opened();
    const digest = w.windowOf(L)!.digest;
    serve({ [`POST /api/ade/link/delegations/${digest}/end`]: () => [200, { ended_at: now() }] });
    const { useStore } = await import("./store");
    const api = await import("./api");
    api.setToken("tok-1");
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    const sent = vi.mocked(globalThis.fetch);
    useStore.getState().signOut();
    expect(w.windowOf(L)).toBeNull();
    await vi.waitFor(() => expect(calls.map((c) => c.path)).toContain(`/api/ade/link/delegations/${digest}/end`));
    const init = sent.mock.calls.find(([u]) => String(u).endsWith("/end"))![1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
  });
});
