import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activeId, addHosted, dropUnsigned, hostLabel, LOCAL, moveHosted, place, places, placeToken, removePlace,
  savePlace, setActive, setPlaceToken,
} from "./places";
import { checkLocal, startDesktop } from "./desktop";

const TOKEN = "eyJhIjoxfQ.c2ln";
let n = 0;
const ids = () => `h${++n}`;

beforeEach(() => { localStorage.clear(); n = 0; });

describe("the desktop app's places", () => {
  it("lists this Mac's first and ignores what is not a place", () => {
    localStorage.setItem("pp-open-places", JSON.stringify([
      { id: "h1", server: "https://a.example", label: "a.example" },
      { id: LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" },
      { id: "", server: "x", label: "no id" }, "not a place", { id: "h2", server: 7, label: "bad server" },
    ]));
    expect(places().map((p) => p.id)).toEqual([LOCAL, "h1"]);
    localStorage.setItem("pp-open-places", "{not json");
    expect(places()).toEqual([]);
  });

  it("adds a hosted place once for an address, named by its host", () => {
    const a = addHosted("https://proofpane-oss.fly.dev", ids);
    expect(a).toEqual({ id: "h1", server: "https://proofpane-oss.fly.dev", label: "proofpane-oss.fly.dev" });
    expect(addHosted("https://proofpane-oss.fly.dev", ids).id).toBe("h1");
    expect(hostLabel("http://127.0.0.1:8899")).toBe("127.0.0.1:8899");
  });

  it("moves a place being signed in to another address, or into the place already there", () => {
    const a = addHosted("https://one.example", ids);
    expect(moveHosted(a.id, "https://two.example")).toEqual({ id: "h1", server: "https://two.example", label: "two.example" });
    const b = addHosted("https://three.example", ids);
    expect(moveHosted(b.id, "https://two.example")?.id).toBe("h1");
    expect(place(b.id)).toBeNull();
    // this Mac's never moves
    savePlace({ id: LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    expect(moveHosted(LOCAL, "https://two.example")?.server).toBe("http://127.0.0.1:5000");
  });

  it("shows this Mac's unless another place is chosen and still there", () => {
    expect(activeId()).toBe(LOCAL);
    const a = addHosted("https://one.example", ids);
    setActive(a.id);
    expect(activeId()).toBe(a.id);
    setActive("gone");
    expect(activeId()).toBe(LOCAL);
  });

  it("forgets a place with its sign-in, but never this Mac's", () => {
    savePlace({ id: LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    setPlaceToken(LOCAL, TOKEN);
    const a = addHosted("https://one.example", ids);
    setPlaceToken(a.id, "other.token");
    setActive(a.id);
    removePlace(a.id);
    expect(place(a.id)).toBeNull();
    expect(placeToken(a.id)).toBeNull();
    expect(activeId()).toBe(LOCAL);
    removePlace(LOCAL);
    expect(place(LOCAL)).not.toBeNull();
    expect(placeToken(LOCAL)).toBe(TOKEN);
  });

  it("drops hosted places nobody signed in to, but the one the window goes to", () => {
    savePlace({ id: LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    const signed = addHosted("https://one.example", ids);
    setPlaceToken(signed.id, "a.b");
    const left = addHosted("https://two.example", ids);
    const going = addHosted("https://three.example", ids);
    dropUnsigned(going.id);
    expect(places().map((p) => p.id)).toEqual([LOCAL, signed.id, going.id]);
    expect(place(left.id)).toBeNull();
  });
});

describe("this Mac's server, as the shell says it is", () => {
  it("is an http address on this machine's loopback, with a sign-in", () => {
    expect(checkLocal({ server: "http://127.0.0.1:63242", token: TOKEN })).toEqual({ server: "http://127.0.0.1:63242", token: TOKEN });
    for (const server of ["https://127.0.0.1:1", "http://localhost:1", "http://10.0.0.2:80", "http://127.0.0.1:1/app/",
                          "https://proofpane-oss.fly.dev", "javascript:alert(1)", 7]) {
      expect(() => checkLocal({ server, token: TOKEN }), String(server)).toThrow(/not this Mac's/);
    }
    for (const token of ["", "no-dot", "a.b c", 7, null]) {
      expect(() => checkLocal({ server: "http://127.0.0.1:1", token }), String(token)).toThrow(/no sign-in/);
    }
    expect(() => checkLocal(null)).toThrow();
  });

  it("is kept as this Mac's place, with whom it was signed in as", async () => {
    savePlace({ id: LOCAL, server: "http://127.0.0.1:1111", label: "This Mac", email: "you@this.desktop" });
    const asked: string[] = [];
    const invoke = async <T,>(cmd: string) => { asked.push(cmd); return { server: "http://127.0.0.1:2222", token: TOKEN } as T; };
    await startDesktop(invoke);
    expect(asked).toEqual(["local_server"]);
    expect(place(LOCAL)).toEqual({ id: LOCAL, server: "http://127.0.0.1:2222", label: "This Mac", email: "you@this.desktop" });
    expect(placeToken(LOCAL)).toBe(TOKEN);
  });

  it("says why when the shell says the server did not start", async () => {
    const invoke = async <T,>(): Promise<T> => { throw "The server did not start in 90 seconds."; };
    await expect(startDesktop(invoke)).rejects.toThrow("The server did not start in 90 seconds.");
    expect(place(LOCAL)).toBeNull();
  });
});

describe("the desktop build talks to the place the window shows", () => {
  beforeEach(() => { vi.stubEnv("MODE", "desktop"); vi.resetModules(); });
  afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });

  it("with that place's server and sign-in, and a new place takes the address given at its sign-in", async () => {
    const api = await import("./api");
    const p = await import("./places");
    expect(api.DESKTOP && api.APP).toBe(true);
    p.savePlace({ id: p.LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    p.setPlaceToken(p.LOCAL, TOKEN);
    expect([api.server(), api.token(), api.onThisMac()]).toEqual(["http://127.0.0.1:5000", TOKEN, true]);
    const h = p.addHosted("https://proofpane-oss.fly.dev", ids);
    p.setActive(h.id);
    expect([api.server(), api.token(), api.onThisMac()]).toEqual(["https://proofpane-oss.fly.dev", null, false]);
    api.setServer("https://other.example");
    expect(api.server()).toBe("https://other.example");
    api.setToken("hosted.token");
    expect(p.placeToken(h.id)).toBe("hosted.token");
    expect(p.placeToken(p.LOCAL)).toBe(TOKEN);
    // once signed in, a place keeps its address
    api.setServer("https://elsewhere.example");
    expect(api.server()).toBe("https://other.example");
    // and the browser's own keys are not touched
    expect(localStorage.getItem("pp-open-token")).toBeNull();
    expect(localStorage.getItem("pp-open-server")).toBeNull();
  });
});

describe("the desktop app's sign-ins, out of the web view's storage", () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  it("are held in memory, and a hosted one is kept through the shell by its server", async () => {
    const p = await import("./places");
    const kept: [string, string | null][] = [];
    p.keepTokensWith(async (server, token) => { kept.push([server, token]); });
    p.savePlace({ id: p.LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    const h = p.addHosted("https://proofpane-oss.fly.dev", () => "h1");
    p.setPlaceToken(p.LOCAL, "local.token");
    p.setPlaceToken(h.id, "hosted.token");
    expect([p.placeToken(p.LOCAL), p.placeToken(h.id)]).toEqual(["local.token", "hosted.token"]);
    expect(kept).toEqual([["https://proofpane-oss.fly.dev", "hosted.token"]]);     // this Mac's is never kept
    expect(Object.keys(localStorage).filter((k) => k.includes("token"))).toEqual([]);
    // read back without keeping it again
    p.setPlaceToken(h.id, "from.keychain", false);
    expect(kept.length).toBe(1);
  });

  it("a place signed out of forgets its sign-in and its account, so leaving it drops it", async () => {
    const p = await import("./places");
    const kept: [string, string | null][] = [];
    p.keepTokensWith(async (server, token) => { kept.push([server, token]); });
    p.savePlace({ id: p.LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    p.savePlace({ id: "h1", server: "https://proofpane-oss.fly.dev", label: "proofpane-oss.fly.dev", email: "you@example.com" });
    p.setPlaceToken("h1", "hosted.token", false);
    p.dropUnsigned(p.LOCAL);
    expect(p.place("h1")).not.toBeNull();            // signed in: it stays
    p.signedOut("h1");
    expect(p.place("h1")?.email).toBeUndefined();
    expect(p.placeToken("h1")).toBeNull();
    expect(kept).toEqual([["https://proofpane-oss.fly.dev", null]]);
    p.dropUnsigned(p.LOCAL);
    expect(p.place("h1")).toBeNull();
  });

  it("at the start, old sign-ins are moved to the keychain, and the shown place's is read from it", async () => {
    const p = await import("./places");
    const d = await import("./desktop");
    p.savePlace({ id: p.LOCAL, server: "http://127.0.0.1:5000", label: "This Mac" });
    p.savePlace({ id: "h1", server: "https://proofpane-oss.fly.dev", label: "proofpane-oss.fly.dev", email: "you@example.com" });
    p.setActive("h1");
    localStorage.setItem("pp-open-token@h1", "old.hosted");
    localStorage.setItem("pp-open-token@local", "old.local");
    localStorage.setItem("pp-open-token@gone", "old.orphan");
    const asked: [string, unknown][] = [];
    const invoke = async <T,>(cmd: string, args?: Record<string, unknown>) => {
      asked.push([cmd, args]);
      return (cmd === "token_get" ? "kept.hosted" : null) as T;
    };
    await d.loadHosted(invoke);
    expect(asked).toEqual([
      ["token_set", { server: "https://proofpane-oss.fly.dev", token: "old.hosted" }],
      ["token_get", { server: "https://proofpane-oss.fly.dev" }],
    ]);
    expect(Object.keys(localStorage).filter((k) => k.startsWith("pp-open-token"))).toEqual([]);
    expect(p.placeToken("h1")).toBe("kept.hosted");
    expect(p.placeToken(p.LOCAL)).toBeNull();      // this Mac's comes from the shell at each start, not storage
  });
});
