import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The desktop app's main window as it loads (`App.tsx` runs the store's boot), in the desktop build (`vite build --mode
// desktop`): the mode is set before any module is read, and no pop-out runs here, so the page's sign-in is the main
// window's own and not a pop-out's copy (api.ts signInOfThisWindowAlone).
vi.hoisted(() => { vi.stubEnv("MODE", "desktop"); });

import { keepTokensWith, place, placeToken, savePlace, setActive, setPlaceToken } from "./places";
import { useStore } from "./store";

const kept: [string, string | null][] = [];

beforeEach(() => {
  kept.length = 0;
  keepTokensWith(async (server, token) => { kept.push([server, token]); });
  savePlace({ id: "local", server: "http://127.0.0.1:4321", label: "This Mac", email: "owner@example.test" });
  savePlace({ id: "h1", server: "https://pp.example", label: "pp.example", email: "owner@example.test" });
  setActive("h1");
  setPlaceToken("h1", "hosted.sig", false);
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  setPlaceToken("local", null, false);
  setPlaceToken("h1", null, false);
  useStore.setState(useStore.getInitialState());
});

describe("the desktop app's main window as it loads", () => {
  it("keeps the hosted place's sign-in, in the keychain too, and its account, when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await useStore.getState().boot();
    expect(useStore.getState().booted).toBe(true);
    expect(placeToken("h1")).toBe("hosted.sig");
    expect(place("h1")?.email).toBe("owner@example.test");
    expect(kept).toEqual([]);                                       // nothing taken out of the keychain
  });

  it("keeps them too when the server answers that it failed, which says nothing of the sign-in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Something failed." }), { status: 500,
      headers: { "Content-Type": "application/json" } })));
    await useStore.getState().boot();
    expect(placeToken("h1")).toBe("hosted.sig");
    expect(place("h1")?.email).toBe("owner@example.test");
    expect(kept).toEqual([]);
  });

  it("lets them go when the server's own answer refuses the sign-in, as before", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Sign in first." }), { status: 401,
      headers: { "Content-Type": "application/json" } })));
    await useStore.getState().boot();
    expect(placeToken("h1")).toBeNull();
    expect(place("h1")?.email).toBeUndefined();
    expect(kept).toEqual([["https://pp.example", null]]);
  });
});
