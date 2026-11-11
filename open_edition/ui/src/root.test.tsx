import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ACCOUNTS, fixtureCalls, fixtureMap, IDS } from "./sessionMapFixture";
import { browser } from "./shellTestkit";
import { useStore } from "./store";
import { Root } from "./root";

// The page the address asks for (root.tsx, which main.tsx renders): a pop-out's query draws its one panel of the
// control view's map, any other address the app, stood in here so that what is tested is the choice alone.
vi.mock("./App", () => ({ App: () => <p>The whole app</p> }));

beforeAll(async () => { await import("./components/PopOut"); }, 60000);
beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("pp-open-token", "test-token.sig");
  browser();
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
  asked = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    asked.push(path);
    if (path === "/api/auth/me") return json({ id: "u1", email: "owner@example.test" });
    if (path === "/api/ade/workspace/accounts") return json(ACCOUNTS);
    if (path === "/api/ade/workspace/map") return json(fixtureMap());
    if (path === `/api/ade/workspace/sessions/${IDS.mine}/map`) return json(fixtureCalls());
    return new Response(JSON.stringify({ detail: "no route" }), { status: 404 });
  }));
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

let asked: string[] = [];
const at = (address: string) => history.replaceState(null, "", address);
const pass = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe("the page an address asks for", () => {
  it("is a pop-out's one panel for each pop-out's query, and nothing of the app", async () => {
    for (const [address, region, title] of [
      ["/app/?pop=map", "The control view", "The map · Proofpane Community"],
      [`/app/?pop=session&session=${IDS.mine}`, "A session's map", "A session's map · Proofpane Community"],
      [`/app/?pop=agent&session=${IDS.mine}&agent=a1`, "An agent", "An agent · Proofpane Community"],
    ]) {
      at(address);
      render(<Root />);
      await pass();
      await pass();
      expect(screen.getByRole("region", { name: region })).toBeTruthy();
      expect(document.title).toBe(title);
      expect(screen.queryByText("The whole app")).toBeNull();
      // and nothing of the app runs there while it is open: the end-to-end keys live around the app alone, and a
      // pop-out reads none
      await pass(65_000);
      expect(asked.filter((p) => p.startsWith("/api/ade/e2e"))).toEqual([]);
      cleanup();
    }
  });

  it("is the app for any other address: none, one that names what is not a session or an agent, a pop-out's words in its hash", async () => {
    for (const address of ["/app/", "/app/?pop=session&session=NOT-A-SESSION", `/app/?pop=agent&session=${IDS.mine}&agent=a.b`,
      "/app/?pop=elsewhere", "/app/#pop=map", `/app/#session=${IDS.mine}`]) {
      at(address);
      render(<Root />);
      await pass();
      expect(screen.getByText("The whole app")).toBeTruthy();
      expect(screen.queryByRole("region", { name: /The control view|A session's map|An agent/ })).toBeNull();
      cleanup();
    }
  });
});
