import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBlob, type SessionRecord } from "./api";
import { EVERY, HIDDEN_EVERY, OFFLINE_MOST, STARTING_EVERY, pollDelay } from "./shellPoll";
import { unreachable, useOffline, useStore } from "./store";
import { shellServer } from "./shellTestkit";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useStore.setState(useStore.getInitialState());
});

describe("the shell's pace", () => {
  it("is a read each 5 seconds while the page is shown, each 2 while the workspace starts, each 15 while hidden", () => {
    const at = (s: Partial<Parameters<typeof pollDelay>[0]>) => pollDelay({ offline: false, failures: 0, hidden: false, starting: false, ...s });
    expect([EVERY, STARTING_EVERY, HIDDEN_EVERY, OFFLINE_MOST]).toEqual([5000, 2000, 15000, 30000]);
    expect(at({})).toBe(5000);
    expect(at({ starting: true })).toBe(2000);
    expect(at({ hidden: true })).toBe(15000);
    expect(at({ hidden: true, starting: true })).toBe(15000);
  });

  it("is twice as long after each try that could not reach the server, up to 30 seconds, hidden or not", () => {
    const off = (failures: number, hidden = false) => pollDelay({ offline: true, failures, hidden, starting: false });
    expect([1, 2, 3, 4, 5, 9].map((n) => off(n))).toEqual([5000, 10000, 20000, 30000, 30000, 30000]);
    expect(off(2, true)).toBe(10000);
  });
});

function Shown() {
  return <span>{useOffline() ? "offline" : "online"}</span>;
}

describe("whether the page is offline", () => {
  it("is the store's (useOffline): set by a read that cannot reach the server, cleared by one that gets through", async () => {
    const server = shellServer();
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    render(<Shown />);
    expect(screen.getByText("online")).toBeTruthy();
    server.state.down = true;
    await act(async () => { await useStore.getState().readSummary(); });
    expect(screen.getByText("offline")).toBeTruthy();
    expect(useStore.getState().error).toBeNull();                 // no toast: the line under the title bar says it
    server.state.down = false;
    await act(async () => { await useStore.getState().readSummary(); });
    expect(screen.getByText("online")).toBeTruthy();
    expect(useStore.getState().lastRead).not.toBeNull();
  });

  it("is said in place of the toast for any request that cannot reach the server, an action's too", () => {
    useStore.getState().setError(new ApiError(0, "Cannot reach the server. Check your connection."));
    expect(useStore.getState().offline).toBe(true);
    expect(useStore.getState().error).toBeNull();
  });

  it("is set by a proxy's 502, 503 or 504 in the app's place, as by no answer at all", async () => {
    const server = shellServer();
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    for (const status of [502, 503, 504]) {
      server.state.gateway = status;
      await act(async () => { await useStore.getState().readSummary(); });
      expect(useStore.getState().offline, String(status)).toBe(true);
      expect(useStore.getState().error, String(status)).toBeNull();
      server.state.gateway = null;
      await act(async () => { await useStore.getState().readSummary(); });
      expect(useStore.getState().offline, String(status)).toBe(false);
    }
  });

  /** The summary answered with `status`, and `body` as the given type. */
  const answer = (status: number, body: string | null, type = "text/html") =>
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status, headers: { "Content-Type": type } })));
  const json = (detail: string) => JSON.stringify({ detail });

  it("is set by a proxy's page of its own too, or any body that is not a JSON object, and not by the app's own 502 or 503, which is said as it is", async () => {
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    for (const [status, body] of [[503, "<html><body><h1>503 Service Unavailable</h1></body></html>"], [502, "502"],
      [504, "\"Gateway Timeout\""], [502, "[]"], [503, "true"]] as const) {
      useStore.setState({ offline: false, error: null });
      answer(status, body);
      await useStore.getState().readSummary();
      expect(useStore.getState().offline, `${status} ${body}`).toBe(true);
    }
    for (const [status, words] of [[502, "The workspace sent something other than an image or a PDF."],
      [503, "Claude Code's sign-in could not be read just now: try again in a moment."]] as const) {
      useStore.setState({ offline: false, error: null });
      answer(status, json(words), "application/json");
      await useStore.getState().readSummary();
      expect(useStore.getState().offline, String(status)).toBe(false);
      expect(useStore.getState().error).toBe(words);
    }
  });

  it("is not set by any other answer, a proxy's 413 or the app's plain 500 among them: those are said as they are", async () => {
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    for (const [status, body] of [[413, null], [500, "Internal Server Error"], [501, null], [505, null]] as const) {
      useStore.setState({ offline: false, error: null });
      answer(status, body, "text/plain");
      await useStore.getState().readSummary();
      expect(useStore.getState().offline, String(status)).toBe(false);
      expect(useStore.getState().error, String(status)).toMatch(new RegExp(`^${status}`));
    }
  });

  it("is set by a file's fetch that a proxy answers in the app's place, and not by the app's own refusal", async () => {
    answer(502, null);
    expect(unreachable(await fetchBlob("/api/ade/workspace/sessions/abc/image?path=a.png").catch((e: unknown) => e))).toBe(true);
    answer(502, json("The workspace sent something other than an image or a PDF."), "application/json");
    const own = await fetchBlob("/api/ade/workspace/sessions/abc/image?path=a.png").catch((e: unknown) => e);
    expect(unreachable(own)).toBe(false);
    expect((own as Error).message).toBe("The workspace sent something other than an image or a PDF.");
  });
});

describe("an error said again", () => {
  it("keeps showing, steadily, until 6 seconds after it was last said: it does not blink off between two tries", () => {
    vi.useFakeTimers();
    const say = () => useStore.getState().setError(new Error("The workspace answered 502"));
    say();
    vi.advanceTimersByTime(4000);
    say();
    vi.advanceTimersByTime(4000);                                  // 8 seconds after the first, 4 after the second
    expect(useStore.getState().error).toBe("The workspace answered 502");
    vi.advanceTimersByTime(2100);
    expect(useStore.getState().error).toBeNull();
  });
});

describe("the summary's reads", () => {
  it("read each list once its stamp moves and not before; a list read since is not read again for the same stamp", async () => {
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    await useStore.getState().readSummary();
    const first = server.api();
    for (const read of ["GET /api/ade/workspace/repos", "GET /api/ade/workspace/sessions", "GET /api/ade/approvals",
      "GET /api/connector/proposals", "GET /api/ade/workspace/asks", "GET /api/ade/link/sessions", "GET /api/ade/link/history"]) {
      expect(first, read).toContain(read);
    }
    const from = server.calls.length;
    await useStore.getState().refreshApprovals();               // an action's own read, after the summary
    await useStore.getState().readSummary();
    expect(server.api(from)).toEqual(["GET /api/ade/approvals", "GET /api/connector/proposals", "GET /api/ade/workspace/asks",
      "GET /api/ade/summary"]);
    server.state.repos = [{ name: "demo" }];
    const next = server.calls.length;
    await useStore.getState().readSummary();
    expect(server.api(next)).toEqual(["GET /api/ade/summary", "GET /api/ade/workspace/repos", "GET /api/ade/workspace/sessions"]);
    expect(useStore.getState().repos.map((r) => r.name)).toEqual(["demo"]);
  });

  it("note a list read at the stamp the summary had when the read began, so a read that lands late is read again", async () => {
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    await useStore.getState().readSummary();
    // the next read of the repositories is answered as the server is when it asks, and lands after what follows
    const real = globalThis.fetch;
    let release = () => {};
    const held = new Promise<void>((r) => { release = r; });
    let hold = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      if (hold && String(input).endsWith("/api/ade/workspace/repos")) { hold = false; await held; }
      return answer;
    }));
    const late = useStore.getState().refreshLists();
    server.state.repos = [{ name: "demo" }];
    await useStore.getState().readSummary();             // its stamp moved: read again, at once
    expect(useStore.getState().repos.map((r) => r.name)).toEqual(["demo"]);
    release();
    await late;                                           // the first read lands last, with the list as it was
    expect(useStore.getState().repos).toEqual([]);
    await useStore.getState().readSummary();             // so the list is read again, though its stamp did not move
    expect(useStore.getState().repos.map((r) => r.name)).toEqual(["demo"]);
  });

  it("read a list again after a read of it failed, though its stamp did not move", async () => {
    const server = shellServer();
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    let failing = true;
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (failing && String(input).endsWith("/api/ade/link/history")) return new Response("{}", { status: 500 });
      return real(input, init);
    }));
    await useStore.getState().readSummary();
    failing = false;
    const from = server.calls.length;
    await useStore.getState().readSummary();
    expect(server.api(from)).toEqual(["GET /api/ade/summary", "GET /api/ade/link/history"]);
  });

  it("show the sessions whether or not the repositories answer, and the repositories whether or not the sessions do, the failure said as before", async () => {
    // a Claude Code sign-in running, its page waiting for a code: a GET /repos that fails does not keep the sessions
    // from being read, which say how it stands; nor do sessions that fail keep the repositories from showing
    const signing = { id: "111111111111", program: "claude-sign-in", repo: null, branch: null, base: null, task: "Sign in to Claude Code",
      created_at: 1, running: true, finished: null, exit_code: null, ended_at: null, chat: null, sign_in: true } as SessionRecord;
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true }, repos: [{ name: "demo" }],
      sessions: [signing] });
    useStore.setState({ user: { id: "u1", email: "o@example.org" }, claude: { installed: true, signed_in: false } });
    const real = globalThis.fetch;
    let failing = "/api/ade/workspace/repos";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (String(input).endsWith(failing)
      ? new Response(JSON.stringify({ detail: "The workspace is not answering." }), { status: 502, headers: { "Content-Type": "application/json" } })
      : real(input, init))));
    await useStore.getState().refreshLists();
    expect(useStore.getState().error).toBe("The workspace is not answering.");
    await vi.waitFor(() => expect(useStore.getState().signIns.map((r) => [r.id, r.running])).toEqual([["111111111111", true]]));
    expect(useStore.getState().repos).toEqual([]);
    // the other way round, the sign-in ended meanwhile: the repositories show, and the sessions stay as they were read
    failing = "/api/ade/workspace/sessions";
    server.state.sessions = [{ ...signing, running: false, exit_code: 0, ended_at: 2 }];
    useStore.setState({ error: null });
    await useStore.getState().refreshLists();
    expect(useStore.getState().error).toBe("The workspace is not answering.");
    await vi.waitFor(() => expect(useStore.getState().repos.map((r) => r.name)).toEqual(["demo"]));
    expect(useStore.getState().signIns.map((r) => [r.id, r.running])).toEqual([["111111111111", true]]);
  });

  it("let go of a stopped workspace's lists, which would only go stale, and keep the session chosen", async () => {
    const server = shellServer({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true },
      repos: [{ name: "demo" }] });
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    await useStore.getState().readSummary();
    expect(useStore.getState().repos).toHaveLength(1);
    useStore.setState({ selected: "0123456789ab" });
    server.state.ws = { ...server.state.ws, running: false };
    await useStore.getState().readSummary();
    expect(useStore.getState().repos).toEqual([]);
    expect(useStore.getState().selected).toBe("0123456789ab");
  });

  it("drop what lands after the account signed out, or another signed in, while it was read", async () => {
    const server = shellServer({ links: { links: [{ id: "L1", name: "MacBook Pro", kind: "local" } as never], questions: [],
      public_url: "https://pp.example", asks: [] } });
    const real = globalThis.fetch;
    let release = () => {};
    const held = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      await held;
      return answer;
    }));
    useStore.setState({ user: { id: "u1", email: "a@example.org" } });
    const reads = [useStore.getState().readSummary(), useStore.getState().refreshLinks(), useStore.getState().refreshHistory(),
      useStore.getState().refreshApprovals()];
    useStore.getState().signOut();
    useStore.setState({ user: { id: "u2", email: "b@example.org" } });        // another account, on this browser
    release();
    await Promise.all(reads);
    const st = useStore.getState();
    expect([st.ws, st.links, st.history, st.summary]).toEqual([null, null, null, null]);
    expect(server.api().length).toBeGreaterThan(0);
  });

  it("are read afresh for a new sign-in, which never joins a read the account before it began", async () => {
    const server = shellServer();
    const real = globalThis.fetch;
    let release = () => {};
    const held = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      await held;
      return answer;
    }));
    useStore.setState({ user: { id: "u1", email: "a@example.org" } });
    const stale = useStore.getState().readSummary();
    useStore.getState().signOut();
    useStore.setState({ user: { id: "u2", email: "b@example.org" } });
    const fresh = useStore.getState().readSummary();
    expect(fresh).not.toBe(stale);
    release();
    await Promise.all([stale, fresh]);
    expect(server.api().filter((c) => c === "GET /api/ade/summary")).toHaveLength(2);
    expect(useStore.getState().summary).not.toBeNull();                       // the new account's own
  });

  it("are still joined once the read the last account began has ended", async () => {
    shellServer();
    const real = globalThis.fetch;
    const gates: (() => void)[] = [];
    let free = false;                                     // once the test is done holding them, the reads go through
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = real(input, init);
      if (!free) await new Promise<void>((r) => gates.push(r));
      return answer;
    }));
    useStore.setState({ user: { id: "u1", email: "a@example.org" } });
    const stale = useStore.getState().readSummary();
    useStore.getState().signOut();
    useStore.setState({ user: { id: "u2", email: "b@example.org" } });
    const fresh = useStore.getState().readSummary();
    await vi.waitFor(() => expect(gates).toHaveLength(2));
    gates[0]();                                           // the old account's read ends; the new one's goes on
    await stale;
    const third = useStore.getState().readSummary();
    free = true;
    for (const open of gates) open();
    await Promise.all([fresh, third]);
    expect(third).toBe(fresh);
  });

  it("are one request when two ask at once", async () => {
    const server = shellServer();
    useStore.setState({ user: { id: "u1", email: "o@example.org" } });
    await Promise.all([useStore.getState().readSummary(), useStore.getState().readSummary()]);
    expect(server.api().filter((c) => c === "GET /api/ade/summary")).toHaveLength(1);
  });
});
