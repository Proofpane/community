import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { openBoard } from "./board";
import { useStore } from "./store";
import { browser, shellServer } from "./shellTestkit";

// The progress board in the page's shell: a button in the title bar opens it,
// so does board.openBoard() from anywhere on the page (the control view's button, once there is one), and its times'
// setting is in the account menu beside the theme; a deployment that gives the account no workspace shows neither.

// the board's windows, and what the shell fetches ahead 3 seconds after it settles (App.tsx's AHEAD), loaded here once:
// a test that runs past those 3 seconds would otherwise end while that code is still loading
beforeAll(async () => {
  await Promise.all([import("./components/Board"), import("./components/TimeSettings"), import("./components/CommandPalette"),
    import("./components/NewSession"), import("./components/Channel"), import("./components/Approvals"),
    import("./components/PullRequest")]);
}, 60000);
beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("pp-open-token", "test-token.sig");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.title = "";
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 1000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

/** The shell's server, and the board's route beside it, its reads counted; `rows` what it answers. */
function withBoard(over: Parameters<typeof shellServer>[0] = {}, rows: () => unknown[] = () => []) {
  const server = shellServer(over);
  const shell = globalThis.fetch;
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (new URL(String(input), "http://localhost").pathname === "/api/ade/board") {
      reads += 1;
      return new Response(JSON.stringify({ at: 1_760_730_000, rows: rows() }), { status: 200 });
    }
    return shell(input, init);
  }));
  return { server, reads: () => reads };
}

describe("the progress board in the shell", () => {
  it("opens from its button in the title bar, and from openBoard() anywhere on the page", async () => {
    browser();
    const { reads } = withBoard({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    render(<App />);
    await pass(2000);
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
    expect(reads()).toBe(0);                                 // nothing asked of the board until it is opened
    fireEvent.click(screen.getByRole("button", { name: "Progress board" }));
    await pass(100);
    expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy();
    expect(reads()).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await pass(100);
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
    await act(async () => { openBoard(); });
    await pass(100);
    expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy();
  });

  it("keeps its times' setting in the account menu, beside the theme", async () => {
    browser();
    withBoard({ ws: { enabled: true, allowed: true, provider: "fly", running: false, made: true } });
    render(<App />);
    await pass(2000);
    fireEvent.click(screen.getByRole("button", { name: /Your account/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Times on the board" }));
    await pass(100);
    expect(screen.getByRole("dialog", { name: "Times on the board" })).toBeTruthy();
  });

  it("is not opened later by an openBoard() made while the account had no workspace", async () => {
    browser();
    const { server } = withBoard({ ws: { enabled: true, allowed: false, detail: "No workspace here." } });
    render(<App />);
    await pass(2000);
    await act(async () => { openBoard(); });
    await pass(100);
    server.state.ws = { enabled: true, allowed: true, provider: "fly", running: true, made: true };
    await pass(6000);                                       // the summary read again: a workspace now
    expect(screen.getByRole("button", { name: "Progress board" })).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
  });

  it("opens from the palette, and Escape closes it", async () => {
    browser();
    withBoard({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    render(<App />);
    await pass(2000);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Progress board/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Progress board/ }));
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy(), { timeout: 15000 });
    fireEvent.keyDown(window, { key: "Escape" });
    await pass(100);
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
  });

  // a board open when the workspace stops being offered does not come back by itself
  it("stays closed once the workspace stops being offered, when it is offered again", async () => {
    browser();
    const { server, reads } = withBoard({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } });
    render(<App />);
    await pass(2000);
    fireEvent.click(screen.getByRole("button", { name: "Progress board" }));
    await pass(100);
    expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy();
    server.state.ws = { enabled: true, allowed: false, detail: "No workspace here." };
    await pass(6000);                                       // the summary read again: no workspace now
    expect(screen.queryByRole("button", { name: "Progress board" })).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
    const before = reads();
    server.state.ws = { enabled: true, allowed: true, provider: "fly", running: true, made: true };
    await pass(6000);                                       // and again: a workspace, its button back
    expect(screen.getByRole("button", { name: "Progress board" })).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
    // never drawn again, not even for one render, so the board is not asked for
    expect(reads()).toBe(before);
  });

  it("is not offered where the deployment gives the account no workspace", async () => {
    browser();
    withBoard({ ws: { enabled: true, allowed: false, detail: "No workspace here." } });
    render(<App />);
    await pass(2000);
    expect(screen.queryByRole("button", { name: "Progress board" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Your account/ }));
    expect(screen.queryByRole("menuitem", { name: "Times on the board" })).toBeNull();
    await act(async () => { openBoard(); });
    await pass(100);
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
  });

  it("closes alone when it fails on what it read, the shell's other windows kept", async () => {
    browser();
    vi.spyOn(console, "error").mockImplementation(() => {});
    let bad = true;
    // a row the board cannot draw (no events at all: never the server's own, as a source's slip could be)
    const rows = () => [{ kind: "task", id: "t-bad", title: "Out of shape", parent: null, phase: "now", state: "running",
      order: null, progress: null, account: null, session: null, created_at: null, started_at: null, ended_at: null,
      waits_until: null, events: bad ? null : [], more_events: 0 }];
    withBoard({ ws: { enabled: true, allowed: true, provider: "fly", running: true, made: true } }, rows);
    render(<App />);
    await pass(2000);
    // another of the shell's windows open first, the times' setting, beside which the board then fails
    fireEvent.click(screen.getByRole("button", { name: /Your account/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Times on the board" }));
    await pass(100);
    expect(screen.getByRole("dialog", { name: "Times on the board" })).toBeTruthy();
    await act(async () => { openBoard(); });
    await pass(100);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("The progress board stopped on an error");
    // the line is a toast fixed to the window, over whatever is open, not a block in the shell's layout
    expect(alert.className).toMatch(/(^| )fixed( |$)/);
    expect(screen.queryByText(/^A window stopped on an error/)).toBeNull();
    expect(screen.getByRole("dialog", { name: "Times on the board" })).toBeTruthy();     // the other window kept
    fireEvent.click(within(alert).getByRole("button", { name: "Close" }));
    await pass(100);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Progress board" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Times on the board" })).toBeTruthy();
    bad = false;
    fireEvent.click(screen.getByRole("button", { name: "Progress board" }));     // opened again: a fresh board
    await pass(100);
    expect(screen.getByRole("complementary", { name: "Progress board" })).toBeTruthy();
  });
});

