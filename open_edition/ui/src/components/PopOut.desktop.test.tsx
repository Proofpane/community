import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A pop-out in the desktop app: each of the app's windows holds its own copy of a sign-in, and shares the place
// list with the others in its web storage. Every module here is the desktop build's (`vite build --mode desktop`): the
// mode is set before any of them is read.
vi.hoisted(() => { vi.stubEnv("MODE", "desktop"); });
// the shell's commands, as the page reaches them (desktop.ts tauriInvoke, sessionMap.ts openPopOut)
const shell = vi.hoisted(() => ({ invoke: vi.fn(async (_cmd: string, _args?: Record<string, unknown>) => undefined as unknown) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: shell.invoke }));

import { DESKTOP } from "../api";
import { place, placeToken, savePlace, setActive, setPlaceToken, signedOut } from "../places";
import { ACCOUNTS, fixtureCalls, fixtureMap, IDS } from "../sessionMapFixture";
import { browser } from "../shellTestkit";
import { useStore } from "../store";
import { ControlView } from "./ControlView";
import { again, PopOutPage } from "./PopOut";

const HERE = "http://127.0.0.1:4321";
let asked: string[] = [];
let refuse = false;

beforeEach(() => {
  vi.useFakeTimers();
  browser();
  asked = [];
  refuse = false;
  savePlace({ id: "local", server: HERE, label: "This Mac", email: "owner@example.test" });
  savePlace({ id: "h1", server: "https://pp.example", label: "pp.example", email: "owner@example.test" });
  setActive("local");
  setPlaceToken("local", "this-mac.sig", false);
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input));
    asked.push(`${url.origin}${url.pathname}`);
    if (refuse) return json({ detail: "Sign in first." }, 401);
    if (url.pathname === "/api/auth/me") return json({ id: "u1", email: "owner@example.test" });
    if (url.pathname === "/api/ade/workspace/accounts") return json(ACCOUNTS);
    if (url.pathname === "/api/ade/workspace/sessions") return json({ sessions: [] });
    if (url.pathname === "/api/ade/workspace/map") return json(fixtureMap());
    if (url.pathname === `/api/ade/workspace/sessions/${IDS.mine}/map`) return json(fixtureCalls());
    return json({ detail: "no route" }, 404);
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  setPlaceToken("local", null, false);
  setPlaceToken("h1", null, false);
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const storageChanged = async () => { await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: "pp-open-places", newValue: "" })); }); };
const maps = () => asked.filter((a) => a.endsWith("/api/ade/workspace/map")).length;

describe("a pop-out in the desktop app", () => {
  it("is built as the desktop app's", () => {
    expect(DESKTOP).toBe(true);
  });

  it("stops drawing once the main window shows another place, and lets its copy of the first place's sign-in go", async () => {
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    expect(screen.getByRole("listitem", { name: "Make the map" })).toBeTruthy();
    expect(asked.every((a) => a.startsWith(HERE))).toBe(true);           // this Mac's server, as its window shows it
    setActive("h1");
    await storageChanged();
    expect(screen.getByRole("status").textContent).toMatch(/shows another account or place now/);
    expect(placeToken("local")).toBeNull();
    const before = maps();
    await pass(30_000);
    expect(maps()).toBe(before);
  });

  it("stops drawing once the main window signs out of the place it shows", async () => {
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    expect(screen.getByRole("listitem", { name: "Make the map" })).toBeTruthy();
    signedOut("local");                                         // the main window's sign-out: the account is forgotten
    setPlaceToken("local", "this-mac.sig", false);              // ... while this window still holds its own copy
    await storageChanged();
    expect(screen.getByRole("status").textContent).toMatch(/has signed out, or shows another account or place now/);
    expect(placeToken("local")).toBeNull();
  });

  it("lets go of its own copy alone when its sign-in is refused, never the account of the place every window shares", async () => {
    // after "Sign out everywhere" in the main window, which holds a new sign-in, this window's copy is the old one
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    await pass();
    expect(screen.getByRole("listitem", { name: "Make the map" })).toBeTruthy();
    refuse = true;
    await pass(3000);
    // its own copy refused says nothing of the main window, which may hold a newer sign-in: it says so, not that the
    // main window is signed out
    expect(screen.getByRole("status").textContent).toMatch(/no longer taken, while the page that opened it may hold a newer one/);
    expect(screen.getByRole("status").textContent).not.toMatch(/not signed in now/);
    expect(placeToken("local")).toBeNull();
    expect(place("local")?.email).toBe("owner@example.test");    // the place keeps its account: the main window's
  });

  it("loads itself again to try again, so it reads its sign-in afresh as when it opened", async () => {
    const load = vi.spyOn(again, "load").mockImplementation(() => {});
    setPlaceToken("local", null, false);
    render(<PopOutPage pop={{ view: "map" }} />);
    await pass();
    expect(screen.getByRole("status").textContent).toMatch(/not signed in now/);
    act(() => { screen.getByRole("button", { name: /Try again/ }).click(); });
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("is opened from the main window's map by the shell's open_popout, never by the page's own window.open", async () => {
    const open = vi.fn(() => null);
    vi.stubGlobal("open", open);
    shell.invoke.mockClear();
    render(<ControlView />);
    await pass();
    fireEvent.click(screen.getByRole("button", { name: "Open the map in a window of its own" }));
    // a pass between the two clicks: here a second import of the mocked module, begun before the first is in, comes
    // back empty (this test runner's mocks), where in the app both share one module, as every import of one does
    await pass(100);
    fireEvent.click(within(screen.getByRole("listitem", { name: "Make the map" }))
      .getByRole("button", { name: "Open this session's map in a window of its own" }));
    await pass(100);
    expect(shell.invoke.mock.calls).toEqual([["open_popout", { view: "map", session: null, agent: null }],
      ["open_popout", { view: "session", session: IDS.mine, agent: null }]]);
    expect(open).not.toHaveBeenCalled();
  });
});
