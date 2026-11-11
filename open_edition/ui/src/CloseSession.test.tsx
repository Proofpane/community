import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, ApiError, type SessionRecord } from "./api";
import { SessionActions } from "./App";
import { useStore } from "./store";

// Closing a session (like stop running plus hide: not delete, and not archive): the
// store's close and reopen, and a session's header's own button for each.

afterEach(() => { cleanup(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it", created_at: 1,
     running: true, finished: null, exit_code: null, ended_at: null, chat: null, ...over }) as SessionRecord;

describe("the store", () => {
  it("closes a session: its pane shut before the workspace is asked, then the lists read again", async () => {
    const said: string[] = [];
    useStore.setState({ panes: ["0123456789ab", "ba9876543210"], selected: "0123456789ab",
      refresh: vi.fn(async () => { said.push("read"); }) });
    vi.spyOn(Api, "closeSession").mockImplementation(async (id) => {
      said.push(`close ${id}, shown: ${useStore.getState().panes.join(" ")}`);
      return rec({ closed: true, running: false });
    });
    await useStore.getState().closeSession("0123456789ab");
    expect(said).toEqual(["close 0123456789ab, shown: ba9876543210", "read"]);
    expect(useStore.getState().selected).toBe("ba9876543210");
  });

  it("says a close the workspace refuses, and reads the lists again all the same", async () => {
    const refresh = vi.fn(async () => {});
    useStore.setState({ refresh });
    vi.spyOn(Api, "closeSession").mockRejectedValue(new Error("A task's attempt is stopped from its task."));
    await useStore.getState().closeSession("0123456789ab");
    expect(useStore.getState().error).toBe("A task's attempt is stopped from its task.");
    expect(refresh).toHaveBeenCalled();
  });

  it("opens a closed one again, then reads the lists", async () => {
    const said: string[] = [];
    useStore.setState({ refresh: vi.fn(async () => { said.push("read"); }) });
    vi.spyOn(Api, "openSession").mockImplementation(async (id) => { said.push(`open ${id}`); return rec(); });
    await useStore.getState().reopenSession("0123456789ab");
    expect(said).toEqual(["open 0123456789ab", "read"]);
  });
});

describe("a close under way, and the lists read meanwhile", () => {
  const A = "aaaaaaaaaaaa";
  const B = "bbbbbbbbbbbb";
  const C = "cccccccccccc";
  const RUNNING = { enabled: true, allowed: true, provider: "fly", running: true, made: true };
  const reading = (server: () => SessionRecord[]) => {
    vi.spyOn(Api, "workspace").mockResolvedValue(RUNNING as never);
    vi.spyOn(Api, "repos").mockResolvedValue({ repos: [] } as never);
    const held: (() => void)[] = [];
    let hold = false;
    vi.spyOn(Api, "sessions").mockImplementation(() => {
      const now = { sessions: server().map((r) => ({ ...r })) };     // the workspace's word as the read begins
      return hold ? new Promise((resolve) => held.push(() => resolve(now as never))) : Promise.resolve(now as never);
    });
    return { hold: (on: boolean) => { hold = on; }, answer: () => held.forEach((go) => go()) };
  };
  const signedIn = (records: SessionRecord[], selected: string) => useStore.setState({
    user: { id: "u1", email: "owner@example.test" } as never, claude: { installed: true } as never, records, selected, panes: [] });
  const shown = () => useStore.getState().records.find((r) => r.id === B);

  it("holds the session closed in every read begun before the workspace closed it, and in none after", async () => {
    let server = [rec({ id: A }), rec({ id: B })];                    // B the one chosen, then closed
    const reads = reading(() => server);
    let closed: (r: SessionRecord) => void = () => {};
    vi.spyOn(Api, "closeSession").mockImplementation(() => new Promise((resolve) => { closed = resolve; }));
    signedIn(server, B);
    const closing = useStore.getState().closeSession(B);
    expect(shown()?.closed).toBe(true);                               // out of the list at once
    expect(useStore.getState().selected).toBeNull();
    await useStore.getState().refreshLists();                         // read while its program stops: B still open there
    expect(shown()?.closed).toBe(true);
    expect(useStore.getState().selected).toBeNull();                  // none chosen in its place, never the one closing
    reads.hold(true);
    const stale = useStore.getState().refreshLists();                 // begun before the workspace closed it
    reads.hold(false);
    server = [rec({ id: A }), rec({ id: B, closed: true, running: false })];
    closed(server[1]);
    await closing;
    reads.answer();
    await stale;
    expect(shown()?.closed).toBe(true);                               // its word from before does not list it again
    server = [rec({ id: A }), rec({ id: B })];                        // opened again elsewhere since
    await useStore.getState().refreshLists();                         // begun after: the workspace's own word stands
    expect(shown()?.closed).toBeFalsy();
  });

  it("lets the session go at once when it is opened again while it closes, and at a sign-out", async () => {
    const server = [rec({ id: A }), rec({ id: B })];
    reading(() => server);
    vi.spyOn(Api, "closeSession").mockImplementation(() => new Promise(() => {}));   // the program still stopping
    vi.spyOn(Api, "openSession").mockResolvedValue(rec({ id: B }));
    signedIn(server, B);
    void useStore.getState().closeSession(B);
    expect(shown()?.closed).toBe(true);
    await useStore.getState().reopenSession(B);
    expect(shown()?.closed).toBeFalsy();                              // opened: the workspace's word, at once
    void useStore.getState().closeSession(B);
    expect(shown()?.closed).toBe(true);
    useStore.getState().signOut();
    signedIn(server, A);
    await useStore.getState().refreshLists();
    expect(shown()?.closed).toBeFalsy();                              // nothing of the last sign-in's held
  });

  it("lists the session again, as the workspace has it, when the close is refused", async () => {
    const server = [rec({ id: A }), rec({ id: B })];
    reading(() => server);
    vi.spyOn(Api, "closeSession").mockRejectedValue(new Error("refused"));
    signedIn(server, B);
    await useStore.getState().closeSession(B);
    expect(shown()?.closed).toBeFalsy();
    expect(useStore.getState().error).toBe("refused");
  });

  it("lists it again at once when the server cannot be reached, no read of the lists following (the second read's 2)", async () => {
    const server = [rec({ id: A }), rec({ id: B })];
    reading(() => server);
    const offline = () => Promise.reject(new ApiError(0, "Failed to fetch"));
    vi.spyOn(Api, "closeSession").mockImplementation(offline);
    vi.spyOn(Api, "workspace").mockImplementation(offline);          // the close's own read of the lists fails too
    const lists = vi.spyOn(Api, "sessions");
    signedIn(server, B);
    await useStore.getState().closeSession(B);
    expect(lists).not.toHaveBeenCalled();
    expect(shown()?.closed).toBeFalsy();
  });

  it("hides none but the one it closes: one closed before, and back since, stays listed (the second read's 1)", async () => {
    let server = [rec({ id: A }), rec({ id: B }), rec({ id: C })];
    reading(() => server);
    vi.spyOn(Api, "closeSession").mockImplementation(async (id) => {
      server = server.map((r) => (r.id === id ? { ...r, closed: true, running: false } : r));
      return server.find((r) => r.id === id)!;
    });
    signedIn(server, B);
    await useStore.getState().closeSession(B);
    expect(shown()?.closed).toBe(true);
    server = server.map((r) => (r.id === B ? rec({ id: B }) : r));     // opened again elsewhere, or started again
    await useStore.getState().refreshLists();
    expect(shown()?.closed).toBeFalsy();
    useStore.getState().select(B);
    void useStore.getState().closeSession(C);
    expect(shown()?.closed).toBeFalsy();
    expect(useStore.getState().selected).toBe(B);
  });
});

describe("a session's header", () => {
  it("closes it with a button of its own, beside stop", () => {
    const closeSession = vi.fn(async () => {});
    useStore.setState({ closeSession });
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    expect(screen.queryByRole("button", { name: "Put the session back in the list" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close the session" }));
    expect(closeSession).toHaveBeenCalledWith("0123456789ab");
  });

  it("puts one that is closed, open all the same, back in the list, through the store, which lets go of it", async () => {
    const reopenSession = vi.fn(async () => {});
    useStore.setState({ refresh: vi.fn(async () => {}), reopenSession });
    render(<SessionActions rec={rec({ closed: true, running: false })} grouped={false} onShip={() => {}} />);
    expect(screen.queryByRole("button", { name: "Close the session" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Put the session back in the list" }));
    await vi.waitFor(() => expect(reopenSession).toHaveBeenCalledWith("0123456789ab"));
  });
});
