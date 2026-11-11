import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { useStore } from "./store";
import { browser, shellServer } from "./shellTestkit";

// The calendar in the page's shell: the palette opens it, so does a
// link to #calendar and the agents' own header; where the deployment gives the account no workspace, none of them does
// and the calendar is never asked for.

// the windows the shell opens, and what it fetches ahead 3 seconds after it settles, loaded here once (as boardShell's)
beforeAll(async () => {
  await Promise.all([import("./components/Calendar"), import("./components/Agents"), import("./components/CommandPalette"),
    import("./components/NewSession"), import("./components/Channel"), import("./components/Approvals"),
    import("./components/PullRequest"), import("./components/Board"), import("./components/TimeSettings")]);
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
  history.replaceState(null, "", "/");
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 1000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const EMPTY = { from: 0, to: 1, now: 0, cap: 200, days: 62, live: 0, schedules: [], firings: [], due_next: [] };

/** The shell's server, and the calendar's route beside it, its reads counted. */
function withCalendar(over: Parameters<typeof shellServer>[0] = {}) {
  const server = shellServer(over);
  const shell = globalThis.fetch;
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (new URL(String(input), "http://localhost").pathname === "/api/ade/agents/calendar") {
      reads += 1;
      return new Response(JSON.stringify(EMPTY), { status: 200 });
    }
    return shell(input, init);
  }));
  return { server, reads: () => reads };
}

const WS = { enabled: true, allowed: true, provider: "fly", running: true, made: true };

describe("the calendar in the shell", () => {
  it("opens from the palette, and Escape closes it", async () => {
    browser();
    const { reads } = withCalendar({ ws: WS });
    render(<App />);
    await pass(2000);
    expect(reads()).toBe(0);                                 // nothing asked of it until it is opened
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Calendar: your agents' schedules/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Calendar: your agents' schedules/ }));
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Calendar" })).toBeTruthy(), { timeout: 15000 });
    await pass(100);
    expect(reads()).toBe(1);
    fireEvent.keyDown(window, { key: "Escape" });
    await pass(100);
    expect(screen.queryByRole("complementary", { name: "Calendar" })).toBeNull();
  });

  it("opens from a link to #calendar, and from the agents' header, closing the agents", async () => {
    browser();
    history.replaceState(null, "", "/#calendar");
    withCalendar({ ws: WS });
    render(<App />);
    await pass(2000);
    expect(screen.getByRole("complementary", { name: "Calendar" })).toBeTruthy();
    expect(location.hash).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await pass(100);
    fireEvent.click(screen.getByRole("button", { name: "Agents" }));
    await pass(500);
    expect(screen.getByRole("complementary", { name: "Agents" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    await pass(500);
    expect(screen.getByRole("complementary", { name: "Calendar" })).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: "Agents" })).toBeNull();
  });

  it("and the agents are one window at a time: the palette opening either closes the other", async () => {
    browser();
    withCalendar({ ws: WS });
    render(<App />);
    await pass(2000);
    fireEvent.click(screen.getByRole("button", { name: "Agents" }));
    await pass(500);
    expect(screen.getByRole("complementary", { name: "Agents" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Calendar: your agents' schedules/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Calendar: your agents' schedules/ }));
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Calendar" })).toBeTruthy(), { timeout: 15000 });
    expect(screen.queryByRole("complementary", { name: "Agents" })).toBeNull();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Agents: chats kept/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Agents: chats kept/ }));
    await vi.waitFor(() => expect(screen.getByRole("complementary", { name: "Agents" })).toBeTruthy(), { timeout: 15000 });
    expect(screen.queryByRole("complementary", { name: "Calendar" })).toBeNull();
  });

  it("is not offered where the deployment gives the account no workspace, nor opened by a link", async () => {
    browser();
    history.replaceState(null, "", "/#calendar");
    const { reads } = withCalendar({ ws: { enabled: true, allowed: false, detail: "No workspace here." } });
    render(<App />);
    await pass(2000);
    expect(screen.queryByRole("complementary", { name: "Calendar" })).toBeNull();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Linked sessions/ })).toBeTruthy(), { timeout: 15000 });
    expect(screen.queryByRole("option", { name: /^Calendar/ })).toBeNull();
    expect(reads()).toBe(0);
  });
});
