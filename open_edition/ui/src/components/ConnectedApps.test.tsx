import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectedApp } from "../api";
import { useStore } from "../store";
import { ConnectedAppsPane, eventWords, mayDo, standing, StillConnected } from "./ConnectedApps";

type Route = (body: Record<string, unknown> | undefined) => [number, unknown];
type Call = { path: string; method: string; body: Record<string, unknown> | undefined };
let calls: Call[] = [];

function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(url), method, body });
    const route = routes[`${method} ${url}`];
    const [status, data] = route ? route(body) : [404, { detail: `no route for ${method} ${url}` }];
    return new Response(JSON.stringify(data), { status });
  }));
}

const app = (over: Partial<ConnectedApp> = {}): ConnectedApp => ({
  id: "g1", client_name: "Claude", returns_to: "claude.ai", described_at: null, scopes: ["ade.read", "ade.propose"], state: "active",
  allowed_at: 1_790_000_000, connected_at: 1_790_000_010, takes_effect_at: 1_790_000_180, hold_id: "h1", ends_at: 1_797_776_000,
  revoked_at: null, revoked_why: null, last_used_at: 1_790_001_000, last_net: "160.79.104.10", proposals_waiting: 0, ...over,
});
const SETUP = { on: true, account_on: true, url: "https://pp.example/mcp", redirects: [], client_hosts: ["claude.ai"], hold_seconds: 180 };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

describe("Connected apps", () => {
  it("says when the deployment's connector is off", async () => {
    serve({ "GET /api/connector": () => [200, { on: false }] });
    render(<ConnectedAppsPane />);
    expect(await screen.findByText(/connector is off on this deployment/)).toBeTruthy();
    expect(calls.map((c) => c.path)).toEqual(["/api/connector"]);
  });

  it("shows the address the ADE answers for, not this page's own, with the steps", async () => {
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [] }] });
    render(<ConnectedAppsPane />);
    expect((await screen.findByLabelText("The connector's address")).textContent).toBe("https://pp.example/mcp");
    expect(location.origin).not.toBe("https://pp.example");
    expect(screen.getByRole("button", { name: /Copy/ })).toBeTruthy();
    expect(screen.getByText(/Customize, Connectors, Add custom connector/)).toBeTruthy();
    expect(screen.getByText(/takes effect 3 minutes later/).textContent).toBe(
      "The connection takes effect 3 minutes later, and each device you turned notifications on in is told at once; until then you can cancel it here.");
    expect(screen.getByText("No app is connected.")).toBeTruthy();
  });

  it("draws an app's name with what is not shown as itself written out, clipped to its line", async () => {
    const named = app({ client_name: `Claude${String.fromCodePoint(0x3164)}` });
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [named] }] });
    render(<ConnectedAppsPane />);
    const card = await screen.findByLabelText(/^Connected app Claude/);
    const name = card.querySelector("bdi")!;
    expect(name.textContent).toBe("Claude[U+3164]");
    expect(name.classList.contains("overflow-hidden")).toBe(true);
  });

  it("draws the names of the apps still connected after signing out everywhere in the same way", () => {
    render(<StillConnected apps={[app({ client_name: `Claude${String.fromCodePoint(0x3164)}` })]} words="" />);
    const name = screen.getByLabelText(/^Allowed lately: Claude/).querySelector("bdi")!;
    expect(name.textContent).toBe("Claude[U+3164]");
    expect(name.classList.contains("overflow-hidden")).toBe(true);
  });

  it("lets apps connect with the account's confirmation, and turns that off with two clicks", async () => {
    let on = false;
    serve({
      "GET /api/connector": () => [200, { ...SETUP, account_on: on }], "GET /api/connector/grants": () => [200, { grants: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
      "PUT /api/connector/account": (b) => { on = !!b?.on; return [200, { on, revoked: 0 }]; },
    });
    render(<ConnectedAppsPane />);
    fireEvent.click(await screen.findByRole("button", { name: "Let apps connect" }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(await screen.findByText("Apps may connect to this account.")).toBeTruthy();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ on: true, confirmation: "conf-1" });
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/confirm")?.body).toEqual({ password: "pw", change: "connector-on" });
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);                  // one click arms it only
    fireEvent.click(screen.getByRole("button", { name: /every connected app is revoked/ }));
    expect(await screen.findByText("Apps may not connect to this account.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ on: false });
  });

  it("lists an app with where it stands, revokes it with two clicks and shows its activity", async () => {
    let gone = false;
    serve({
      "GET /api/connector": () => [200, SETUP],
      "GET /api/connector/grants": () => [200, { grants: [app(gone ? { state: "revoked", revoked_at: 1_790_002_000, revoked_why: "the owner revoked it" } : { proposals_waiting: 2 })] }],
      "DELETE /api/connector/grants/g1": () => { gone = true; return [200, app({ state: "revoked" })]; },
      "GET /api/connector/grants/g1/events": () => [200, { events: [{ id: 9, at: 1_790_001_000, kind: "tool", what: "list_sessions", subject: null, outcome: "ok", net: "160.79.104.10" }], before: 9 }],
      "GET /api/connector/grants/g1/events?before=9": () => [200, { events: [{ id: 3, at: 1_790_000_010, kind: "connected", what: null, subject: null, outcome: "ok", net: null }], before: null }],
    });
    render(<ConnectedAppsPane />);
    const card = await screen.findByLabelText("Connected app Claude");
    expect(card.textContent).toContain("May read and propose");
    expect(card.textContent).toContain("2 of its proposals wait for you in Approvals.");
    fireEvent.click(screen.getByRole("button", { name: "Its activity" }));
    expect((await screen.findByLabelText("Its activity")).textContent).toContain("tool list_sessions from 160.79.104.10");
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByText(/connected$/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Revoke/ }));
    expect(calls.filter((c) => c.method === "DELETE")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Click again to revoke it/ }));
    expect(await screen.findByText(/Revoked .*: the owner revoked it\./)).toBeTruthy();
  });

  it("says each state in words", () => {
    expect(standing(app({ state: "waiting" }))).toMatch(/^Takes effect .+, unless you cancel it first\.$/);
    expect(standing(app({ state: "connecting" }))).toMatch(/has not collected its sign-in yet/);
    expect(standing(app())).toMatch(/^Connected .+\. Last used .+ from 160\.79\.104\.10\. Ends .+\.$/);
    expect(mayDo(["ade.read"])).toBe("May read");
    expect(eventWords({ id: 1, at: 1, kind: "refused", what: "token", subject: "expired", outcome: "refused", net: null })).toBe("refused token (expired): refused");
  });

  it("offers Cancel while an app's connection waits on its hold", async () => {
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ state: "waiting" })] }] });
    render(<ConnectedAppsPane />);
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeTruthy();
  });
});
