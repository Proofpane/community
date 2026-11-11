import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ACCOUNTS, AT, fixtureMap } from "../sessionMapFixture";
import { browser } from "../shellTestkit";
import { useStore } from "../store";
import { ControlView } from "./ControlView";

// The Skills view is opened from the control view (a button in its header), drawn between the controller's panel
// and the map, and not in a pop-out, which draws one panel. The panel itself is Skills.test.tsx's.

let asked: string[];

function serve() {
  asked = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input), "http://localhost").pathname;
    asked.push(`${init.method || "GET"} ${path}`);
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    if (path === "/api/ade/workspace/accounts") return json(ACCOUNTS);
    if (path === "/api/ade/workspace/sessions") return json({ sessions: [] });
    if (path === "/api/ade/controller") return json({ controller: null, requests: [], requirements: [], other_sessions: [], questions: [], decisions: [] });
    if (path === "/api/ade/agents/accounts") return json({ accounts: [] });
    if (path === "/api/ade/workspace/map") return json({ ...fixtureMap(), at: AT });
    if (path === "/api/ade/skills") {
      return json({ days: 30, from_day: "2026-10-01", to_day: "2026-10-30", now: 1, read: null,
        totals: { calls: 0, failed: 0, typed: 0, skills: 0, sessions: 0, first_at: null, last_at: null }, by_day: [], skills: [], more: 0 });
    }
    if (path === "/api/ade/skills/read") return json({ state: "read", taken: 0 });
    return json({ detail: `no route for ${path}` }, 404);
  }));
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("pp-open-token", "test-token.sig");
  browser();
  serve();
  useStore.setState({ accounts: ACCOUNTS });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  useStore.setState(useStore.getInitialState());
});

const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe("the Skills view in the control view", () => {
  it("is closed to begin with and reads nothing of the skills", async () => {
    render(<ControlView />);
    await pass();
    expect(screen.queryByRole("region", { name: "Skills" })).toBeNull();
    expect(asked.filter((p) => p.includes("/api/ade/skills"))).toEqual([]);
    expect(screen.getByRole("button", { name: "Skills" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("opens from the header button, between the controller's panel and the map, and closes with it", async () => {
    render(<ControlView />);
    await pass();
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    await pass();
    const regions = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(regions.slice(0, 3)).toEqual(["The control view", "The Manager", "Skills"]);
    expect(asked.filter((p) => p.includes("/api/ade/skills"))).toEqual(["GET /api/ade/skills", "POST /api/ade/skills/read", "GET /api/ade/skills"]);
    expect(screen.getByRole("button", { name: "Skills" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.queryByRole("region", { name: "Skills" })).toBeNull();
    expect(screen.getByRole("button", { name: "Skills" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("closes from the panel's own close button", async () => {
    render(<ControlView />);
    await pass();
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    await pass();
    fireEvent.click(within(screen.getByRole("region", { name: "Skills" })).getByRole("button", { name: "Close the skills view" }));
    expect(screen.queryByRole("region", { name: "Skills" })).toBeNull();
  });

  it("is not in a pop-out, which draws one panel", async () => {
    render(<ControlView pop={{ view: "map" }} />);
    await pass();
    expect(screen.queryByRole("button", { name: "Skills" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Skills" })).toBeNull();
  });
});
