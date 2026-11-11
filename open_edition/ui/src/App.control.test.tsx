import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionRecord } from "./api";
import { App } from "./App";
import { fixtureMap, IDS } from "./sessionMapFixture";
import { browser, shellServer } from "./shellTestkit";
import { useStore } from "./store";

// The shell with the control view: the list of sessions collapses on a computer
// too, kept in the browser, and the control view, the map of every session and its agents, is the main panel's default
// where no session is open. The whole App against a server of the test's own (shellTestkit.ts), on fake timers.

beforeAll(async () => {
  await Promise.all([import("./components/ControlView"), import("./components/CommandPalette"), import("./components/NewSession")]);
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
// a test that opens the palette waits for it as App.shell.test.tsx does, up to 15 seconds each time, so its own time
// is longer than vitest's 5 seconds
const list = () => screen.queryByRole("complementary", { name: "Sessions" });
const main = () => screen.getByRole("main");
const RUNNING = { enabled: true, allowed: true, provider: "fly", running: true, made: true };
// a session the map draws, stopped, so that opening it shows its pane and no terminal
const rec = { id: IDS.mine, program: "claude", repo: "demo", branch: `pp/${IDS.mine}`, base: "abc", task: "Make the map", created_at: 1,
  running: false, finished: null, exit_code: null, ended_at: null, chat: null } as unknown as SessionRecord;
const running = () => shellServer({ ws: RUNNING, repos: [{ name: "demo" }], sessions: [rec], map: fixtureMap() });

describe("the list of sessions", () => {
  it("hides on a computer, and stays hidden in this browser until it is shown again", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    expect(list()).toBeTruthy();
    fireEvent.click(within(list()!).getByRole("button", { name: "Hide the list of sessions" }));
    expect(list()).toBeNull();
    expect(localStorage.getItem("pp-list-hidden")).toBe("1");
    expect(within(main()).getByRole("region", { name: "The control view" })).toBeTruthy();
    // the page opened again keeps it hidden
    cleanup();
    useStore.setState(useStore.getInitialState());
    render(<App />);
    await pass(2000);
    expect(list()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show the list of sessions" }));
    expect(list()).toBeTruthy();
    expect(localStorage.getItem("pp-list-hidden")).toBeNull();
    expect(screen.queryByRole("button", { name: "Show the list of sessions" })).toBeNull();
  });

  it("puts the keyboard on the button that undoes a hiding or a showing of the list, never lost with the one pressed", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    fireEvent.click(within(list()!).getByRole("button", { name: "Hide the list of sessions" }));
    await pass(10);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Show the list of sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Show the list of sessions" }));
    await pass(10);
    expect(document.activeElement).toBe(within(list()!).getByRole("button", { name: "Hide the list of sessions" }));
  });

  it("hides and shows from the palette too", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Hide the list of sessions/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Hide the list of sessions/ }));
    expect(list()).toBeNull();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Show the list of sessions/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Show the list of sessions/ }));
    expect(list()).toBeTruthy();
  }, 40000);

  it("hidden, has no rows for ⌥1–9 to open, and leaves the keyboard on the button that shows it when a session is closed", async () => {
    browser();
    const other = { ...rec, id: IDS.chat, task: "Ask about the gate", branch: `pp/${IDS.chat}` } as SessionRecord;
    shellServer({ ws: RUNNING, repos: [{ name: "demo" }], sessions: [rec, other], map: fixtureMap() });
    render(<App />);
    await pass(2000);
    fireEvent.keyDown(window, { altKey: true, code: "Digit1", key: "1" });
    expect(useStore.getState().selected).not.toBeNull();            // shown, ⌥1 opens its first row
    act(() => { useStore.getState().select(null); });
    fireEvent.click(within(list()!).getByRole("button", { name: "Hide the list of sessions" }));
    fireEvent.keyDown(window, { altKey: true, code: "Digit1", key: "1" });
    fireEvent.keyDown(window, { altKey: true, code: "ArrowDown", key: "ArrowDown" });
    expect(useStore.getState().selected).toBeNull();                // hidden: no rows to go to
    // a session open while the list is hidden, closed from its header: the keyboard goes to what shows the list
    act(() => { useStore.getState().select(IDS.mine); });
    await pass();
    // pressed as a person presses it, the keyboard on it (a click alone leaves the keyboard where the hiding put it)
    const close = within(main()).getByRole("button", { name: "Close the session" });
    close.focus();
    expect(document.activeElement).toBe(close);
    fireEvent.click(close);
    await pass(2000);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Show the list of sessions" }));
    // shown again, its rows are there for the keys again
    fireEvent.click(screen.getByRole("button", { name: "Show the list of sessions" }));
    await pass();
    fireEvent.keyDown(window, { altKey: true, code: "Digit1", key: "1" });
    expect(useStore.getState().selected).not.toBeNull();
  });

  it("is a phone's drawer as before, whatever this browser kept: no edge to show it, no button to hide it", async () => {
    localStorage.setItem("pp-list-hidden", "1");
    browser({ phone: true });
    running();
    render(<App />);
    await pass(2000);
    expect(screen.queryByRole("button", { name: "Show the list of sessions" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sessions" }));
    const drawer = screen.getByRole("dialog", { name: "Sessions" });
    expect(within(drawer).getByRole("complementary", { name: "Sessions" })).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: "Hide the list of sessions" })).toBeNull();
    // on a phone, the control view is the main panel's too, with no pop-out
    fireEvent.click(within(drawer).getByRole("button", { name: "Close the list" }));
    expect(within(main()).getByRole("region", { name: "The control view" })).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /in a (tab|window) of its own/ })).toEqual([]);
  });
});

describe("the control view", () => {
  it("is the main panel where no session is open, and comes back from the list or the palette once one is", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    const control = () => within(main()).queryByRole("region", { name: "The control view" });
    expect(control()).toBeTruthy();
    expect(within(main()).getByRole("listitem", { name: "Make the map" })).toBeTruthy();
    expect(within(list()!).getByRole("button", { name: "Control view" }).getAttribute("aria-pressed")).toBe("true");
    // a session opened from the map shows in its place, as one opened from the list does
    fireEvent.click(within(within(main()).getByRole("listitem", { name: "Make the map" })).getByRole("button", { name: "Make the map" }));
    await pass();
    expect(useStore.getState().selected).toBe(IDS.mine);
    expect(control()).toBeNull();
    expect(within(list()!).getByRole("button", { name: "Control view" }).getAttribute("aria-pressed")).toBe("false");
    // back from the list's button
    fireEvent.click(within(list()!).getByRole("button", { name: "Control view" }));
    await pass();
    expect(control()).toBeTruthy();
    expect(useStore.getState().selected).toBeNull();
    // and from the palette, once a session is open again
    act(() => { useStore.getState().select(IDS.mine); });
    await pass();
    expect(control()).toBeNull();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await vi.waitFor(() => expect(screen.getByRole("option", { name: /^Control view/ })).toBeTruthy(), { timeout: 15000 });
    fireEvent.click(screen.getByRole("option", { name: /^Control view/ }));
    await pass();
    expect(control()).toBeTruthy();
  }, 40000);

  it("clears every pane when its button is pressed, the sessions side by side too", async () => {
    browser();
    const other = { ...rec, id: IDS.chat, task: "Ask about the gate", branch: `pp/${IDS.chat}` } as SessionRecord;
    shellServer({ ws: RUNNING, repos: [{ name: "demo" }], sessions: [rec, other], map: fixtureMap() });
    render(<App />);
    await pass(2000);
    act(() => { useStore.setState({ panes: [IDS.mine, IDS.chat], selected: IDS.chat }); });
    await pass();
    expect(within(main()).queryByRole("region", { name: "The control view" })).toBeNull();
    fireEvent.click(within(list()!).getByRole("button", { name: "Control view" }));
    await pass();
    expect(within(main()).getByRole("region", { name: "The control view" })).toBeTruthy();
    expect(useStore.getState().panes).toEqual([]);
  });

  it("offers New session in the newest listed session's repository where none is chosen, as when the page chose it", async () => {
    browser();
    const newer = { ...rec, id: IDS.chat, repo: "parser", task: "Ask about the gate", branch: `pp/${IDS.chat}` } as SessionRecord;
    shellServer({ ws: RUNNING, repos: [{ name: "demo" }, { name: "parser" }], sessions: [rec, newer], map: fixtureMap() });
    render(<App />);
    await pass(2000);
    expect(useStore.getState().selected).toBeNull();
    fireEvent.click(within(within(main()).getByRole("region", { name: "The control view" })).getByRole("button", { name: /New session/ }));
    await vi.waitFor(() => expect(screen.getByRole("combobox", { name: "Repository" })).toBeTruthy(), { timeout: 15000 });
    expect((screen.getByRole("combobox", { name: "Repository" }) as HTMLSelectElement).value).toBe("parser");
  }, 40000);

  it("signs the page out once another window of it takes their sign-in away (a pop-out answered 401, a sign-out in another tab)", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    expect(useStore.getState().user).not.toBeNull();
    // another key changed, or the sign-in still there: nothing happens
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-theme", newValue: "light" })); });
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-token", newValue: "test-token.sig" })); });
    expect(useStore.getState().user).not.toBeNull();
    localStorage.removeItem("pp-open-token");
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-token", newValue: null })); });
    expect(useStore.getState().user).toBeNull();
    expect(within(document.body).queryByRole("region", { name: "The control view" })).toBeNull();
  });

  it("signs the page out too once another tab clears the whole of the site's storage, the sign-in with it", async () => {
    browser();
    running();
    render(<App />);
    await pass(2000);
    // the whole storage cleared is a change of no one key (the event's key is null): with the sign-in still there,
    // nothing happens
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: null })); });
    expect(useStore.getState().user).not.toBeNull();
    localStorage.clear();
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: null })); });
    expect(useStore.getState().user).toBeNull();
  });

  it("keeps its sign-in when the server cannot be reached as it loads, or answers that it failed, so the page's other tabs are not signed out", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await useStore.getState().boot();
    expect(useStore.getState().booted).toBe(true);
    expect(localStorage.getItem("pp-open-token")).toBe("test-token.sig");
    // a server that failed (its own 500) says nothing of the sign-in either
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Something failed." }), { status: 500 })));
    await useStore.getState().boot();
    expect(localStorage.getItem("pp-open-token")).toBe("test-token.sig");
    // the server's own no lets it go, as before
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Sign in first." }), { status: 401 })));
    await useStore.getState().boot();
    expect(localStorage.getItem("pp-open-token")).toBeNull();
  });

  it("keeps it so in the phone app too, which keeps its sign-in as a browser does, with the server it was given", async () => {
    vi.stubEnv("MODE", "phone");
    vi.resetModules();
    try {
      const inThePhoneApp = await import("./store");
      localStorage.setItem("pp-open-server", "https://pp.example");
      const asked: string[] = [];
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => { asked.push(String(input)); throw new TypeError("Failed to fetch"); }));
      await inThePhoneApp.useStore.getState().boot();
      expect(asked).toEqual(["https://pp.example/api/auth/me"]);
      expect(inThePhoneApp.useStore.getState().booted).toBe(true);
      expect(localStorage.getItem("pp-open-token")).toBe("test-token.sig");
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });

  it("puts no agent's name in the tab's title or a notification, while the page notifies what waits", async () => {
    browser();
    const shown: unknown[][] = [];
    class Note { static permission = "granted"; constructor(...args: unknown[]) { shown.push(args); } }
    vi.stubGlobal("Notification", Note);
    const server = running();
    let visible = true;
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (visible ? "visible" : "hidden") });
    try {
      render(<App />);
      await pass(2000);
      expect(within(main()).getByRole("listitem", { name: "Make the map" })).toBeTruthy();
      // the page goes to the background, and the session starts to wait for the person: the page says so
      visible = false;
      server.state.sessions = [{ ...rec, running: true, activity: { state: "waiting", at: 2, tool: "Bash", kind: "permission_prompt" } } as unknown as SessionRecord];
      await pass(20_000);
      expect(shown.length).toBeGreaterThan(0);
      const words = JSON.stringify(shown) + document.title;
      for (const name of ["Explore the routes", "Run the tests", "Stopped halfway", "Read every route"]) expect(words).not.toContain(name);
    } finally {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    }
  }, 40000);

  it("shows the first steps where the workspace has no session to draw yet", async () => {
    browser();
    shellServer({ ws: RUNNING });
    render(<App />);
    await pass(2000);
    expect(within(main()).getByRole("region", { name: "The control view" })).toBeTruthy();
    expect(within(main()).getByText("Get started")).toBeTruthy();
  });

  it("is not there while the workspace does not run: the list's button neither", async () => {
    browser();
    shellServer();
    render(<App />);
    await pass(2000);
    expect(within(main()).queryByRole("region", { name: "The control view" })).toBeNull();
    expect(within(list()!).queryByRole("button", { name: "Control view" })).toBeNull();
  });
});
