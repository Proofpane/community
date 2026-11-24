import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectedApp } from "../api";
import { useStore } from "../store";
import {
  ALL_REVOKED_ENDS, ConnectedAppsPane, eventWords, LINKS_OFF, LINKS_WAITING, LINKS_ON, LINKS_READS_ONLY, mayDo, REVOKE_ENDS, standing,
  StillConnected,
} from "./ConnectedApps";

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
  revoked_at: null, revoked_why: null, last_used_at: 1_790_001_000, last_net: "160.79.104.10", proposals_waiting: 0, links: false, ...over,
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

  it("says on the second click of Revoke what it ends and deletes, where sessions may be linked through the app", async () => {
    serve({
      "GET /api/connector": () => [200, SETUP],
      "GET /api/connector/grants": () => [200, { grants: [app({ links: true }), app({ id: "g2", client_name: "Other", links: false })] }],
    });
    render(<ConnectedAppsPane />);
    const linked = await screen.findByLabelText("Connected app Claude");
    fireEvent.click(within(linked).getByRole("button", { name: "Revoke" }));
    expect(within(linked).getByRole("button", { name: REVOKE_ENDS })).toBeTruthy();
    expect(REVOKE_ENDS).toBe("Click again to revoke it: the sessions linked through it end, and their posts and checkpoints are deleted");
    // an app sessions may not link through has none to end: its second click says only that it revokes
    const plain = screen.getByLabelText("Connected app Other");
    fireEvent.click(within(plain).getByRole("button", { name: "Revoke" }));
    expect(within(plain).getByRole("button", { name: "Click again to revoke it" })).toBeTruthy();
    // the account's own switch revokes every app: it says the same while one of them has linking on
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(screen.getByRole("button", { name: ALL_REVOKED_ENDS })).toBeTruthy();
    expect(ALL_REVOKED_ENDS).toBe(
      "Click again: every connected app is revoked, and the sessions linked through them end, their posts and checkpoints deleted");
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);                       // nothing was done by one click
  });

  it("says only that every app is revoked where none has linking on, and the same of an app allowed lately", async () => {
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app()] }] });
    render(<ConnectedAppsPane />);
    await screen.findByLabelText("Connected app Claude");
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(screen.getByRole("button", { name: "Click again: every connected app is revoked" })).toBeTruthy();
    cleanup();
    expect(typeof REVOKE_ENDS).toBe("string");
    render(<StillConnected apps={[app({ links: true }), app({ id: "g2", client_name: "Other" })]} words="w" />);
    fireEvent.click(within(screen.getByLabelText("Allowed lately: Claude")).getByRole("button", { name: "Revoke" }));
    expect(within(screen.getByLabelText("Allowed lately: Claude")).getByRole("button", { name: REVOKE_ENDS })).toBeTruthy();
    fireEvent.click(within(screen.getByLabelText("Allowed lately: Other")).getByRole("button", { name: "Revoke" }));
    expect(within(screen.getByLabelText("Allowed lately: Other")).getByRole("button", { name: "Click again to revoke it" })).toBeTruthy();
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

describe("letting sessions link through a connected app", () => {
  it("is off until the owner turns it on with a confirmation made for that app, and goes off with two clicks", async () => {
    let links = false;
    serve({
      "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ links })] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
      "PUT /api/connector/grants/g1/links": (b) => { links = !!b?.on; return [200, app({ links })]; },
    });
    render(<ConnectedAppsPane />);
    const card = await screen.findByLabelText("Connected app Claude");
    const line = within(card).getByRole("group", { name: "Linking sessions through it" });
    expect(within(line).getByText(LINKS_OFF)).toBeTruthy();
    expect(card.textContent).not.toContain("sessions may link through it");
    fireEvent.click(within(line).getByRole("button", { name: "Let sessions link through it" }));
    fireEvent.change(await within(line).findByLabelText("Your password"), { target: { value: "pw" } });
    expect(calls.filter((c) => c.method === "PUT")).toEqual([]);                    // nothing is turned on before the word
    fireEvent.click(within(line).getByRole("button", { name: /Confirm/ }));
    expect(await within(line).findByText(LINKS_ON)).toBeTruthy();
    // the confirmation is made for letting sessions link through this app, and nothing else
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/account/confirm")?.body).toEqual({ password: "pw", change: "connector-links:g1" });
    expect(calls.filter((c) => c.method === "PUT")).toEqual([{ path: "/api/connector/grants/g1/links", method: "PUT", body: { on: true, confirmation: "conf-1" } }]);
    expect(card.textContent).toContain("May read and propose; sessions may link through it");
    expect(within(line).queryByText(LINKS_OFF)).toBeNull();
    fireEvent.click(within(line).getByRole("button", { name: "Turn linking off" }));
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);                // one click arms it only
    fireEvent.click(within(line).getByRole("button", { name: "Click again: every session linked through it ends, and its posts and checkpoint are deleted" }));
    expect(await within(line).findByText(LINKS_OFF)).toBeTruthy();
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ on: false });   // off takes the sign-in alone
    expect(card.textContent).not.toContain("sessions may link through it");
  });

  it("says why no session links through an app that may only read, and nothing of linking once a connection is over", async () => {
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ scopes: ["ade.read"] })] }] });
    render(<ConnectedAppsPane />);
    let card = await screen.findByLabelText("Connected app Claude");
    expect(within(card).getByRole("group", { name: "Linking sessions through it" }).textContent).toBe(LINKS_READS_ONLY);
    expect(within(card).queryByRole("button", { name: /link/i })).toBeNull();        // nothing to turn on
    // a connection that waits on its hold, or whose app has not collected its sign-in, can be let link already
    for (const state of ["waiting", "connecting"] as const) {
      cleanup();
      serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ state })] }] });
      render(<ConnectedAppsPane />);
      expect(await screen.findByRole("button", { name: "Let sessions link through it" })).toBeTruthy();
    }
    // one that was revoked or has ended: the server keeps its switch as it was, and the card says nothing of it
    for (const over of [{ state: "revoked", revoked_at: 1_790_002_000, revoked_why: "the owner revoked it" }, { state: "ended" }] as const) {
      cleanup();
      serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ ...over, links: true })] }] });
      render(<ConnectedAppsPane />);
      card = await screen.findByLabelText("Connected app Claude");
      expect(within(card).queryByRole("group", { name: "Linking sessions through it" })).toBeNull();
      expect(card.textContent).toContain("May read and propose");
      expect(card.textContent).not.toMatch(/link/i);
      expect(within(card).queryByRole("button", { name: /link/i })).toBeNull();
    }
  });

  it("says what linking would allow before it asks for the confirmation", async () => {
    // an app connected before linking existed was told it could never link anything: the card says what turning it on
    // allows where the owner is asked, not only once it is on
    for (const said of ["under names of their own", "any chat of that Claude account that uses this app can post under a name linked through it", "they take nothing you sign", "carries none of your words"]) {
      expect(LINKS_OFF).toContain(said);
    }
    serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app()] }] });
    render(<ConnectedAppsPane />);
    const line = within(await screen.findByLabelText("Connected app Claude")).getByRole("group", { name: "Linking sessions through it" });
    const said = within(line).getByText(LINKS_OFF);
    const button = within(line).getByRole("button", { name: "Let sessions link through it" });
    expect(said.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // said first
  });

  it("says a connection not yet in effect lets none link yet, though its switch is on", async () => {
    for (const state of ["waiting", "connecting"] as const) {
      cleanup();
      serve({ "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ state, links: true })] }] });
      render(<ConnectedAppsPane />);
      const card = await screen.findByLabelText("Connected app Claude");
      const line = within(card).getByRole("group", { name: "Linking sessions through it" });
      expect(within(line).getByText(LINKS_WAITING)).toBeTruthy();
      expect(within(line).queryByText(LINKS_ON)).toBeNull();
      expect(card.textContent).not.toContain("; sessions may link through it");
      expect(within(line).getByRole("button", { name: "Turn linking off" })).toBeTruthy();
    }
  });

  it("says in what an app may do that sessions may link through it, once they may", () => {
    expect(mayDo(["ade.read", "ade.propose"], true)).toBe("May read and propose; sessions may link through it");
    expect(mayDo(["ade.read", "ade.propose"], false)).toBe("May read and propose");
    expect(mayDo(["ade.read", "ade.propose"])).toBe("May read and propose");
  });

  it("says in the card why linking was not turned on, when the server refuses it", async () => {
    serve({
      "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app()] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
      "PUT /api/connector/grants/g1/links": () => [404, { detail: "No such app." }],
    });
    render(<ConnectedAppsPane />);
    const card = await screen.findByLabelText("Connected app Claude");
    fireEvent.click(within(card).getByRole("button", { name: "Let sessions link through it" }));
    fireEvent.change(await within(card).findByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(within(card).getByRole("button", { name: /Confirm/ }));
    expect((await within(card).findByRole("alert")).textContent).toBe("No such app.");
    expect(within(card).getByText(LINKS_OFF)).toBeTruthy();
  });

  it("says in the card why linking was not turned off, and no longer once it is", async () => {
    let links = true;
    let busy = true;
    serve({
      "GET /api/connector": () => [200, SETUP], "GET /api/connector/grants": () => [200, { grants: [app({ links })] }],
      "PUT /api/connector/grants/g1/links": (b) => {
        if (busy) return [500, { detail: "The database is busy: try again." }];
        links = !!b?.on;
        return [200, app({ links })];
      },
    });
    render(<ConnectedAppsPane />);
    const card = await screen.findByLabelText("Connected app Claude");
    const turnOff = async () => {
      await waitFor(() => expect((within(card).getByRole("button", { name: "Turn linking off" }) as HTMLButtonElement).disabled).toBe(false));
      fireEvent.click(within(card).getByRole("button", { name: "Turn linking off" }));
      fireEvent.click(within(card).getByRole("button", { name: "Click again: every session linked through it ends, and its posts and checkpoint are deleted" }));
    };
    await turnOff();
    expect((await within(card).findByRole("alert")).textContent).toBe("The database is busy: try again.");
    expect(within(card).getByText(LINKS_ON)).toBeTruthy();
    busy = false;
    await turnOff();
    expect(await within(card).findByText(LINKS_OFF)).toBeTruthy();
    expect(within(card).queryByRole("alert")).toBeNull();
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ on: false }, { on: false }]);
  });
});
