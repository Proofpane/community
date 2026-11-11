import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectRequest } from "../api";
import { useStore } from "../store";
import { ACCOUNT_OFF, CLICKED, ConnectApp, ENDED, leaving, OTHER_BROWSER } from "./ConnectApp";

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

const REDIRECT = "https://claude.ai/api/mcp/auth_callback?code=ppcc_x&state=st-1&iss=https%3A%2F%2Fpp.example";
const request = (over: Partial<ConnectRequest> = {}): ConnectRequest => ({
  id: "r".repeat(32), account: "owner@example.org", account_id: "u-1", account_on: true, client_name: "Claude",
  described_at: null, returns_to: "claude.ai", loopback: false, port: null, scopes: ["ade.read", "ade.propose", "offline_access"],
  asked_at: 1_790_000_000, asked_from: "203.0.113.5", viewer: "203.0.113.5", same_network: true, expires_at: 1_790_000_600,
  fetch_site: "cross-site", came_from: "https://claude.ai", from_app_page: true, hold_seconds: 180, lasts_days: 90, ...over,
});
const R = "r".repeat(32);
const routes = (over: Record<string, Route> = {}): Record<string, Route> => ({
  [`GET /api/connector/requests/${R}`]: () => [200, request()],
  "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
  "POST /api/account/confirm": () => [200, { confirmation: "conf-1" }],
  [`POST /api/connector/requests/${R}/allow`]: () => [200, { redirect: REDIRECT, hold: { id: "h", takes_effect_at: 1, told: 1 }, grant: "g" }],
  [`POST /api/connector/requests/${R}/refuse`]: () => [200, { redirect: "https://claude.ai/api/mcp/auth_callback?error=access_denied&state=st-1" }],
  ...over,
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

describe("connecting an app", () => {
  it("says what asks, by its own word, where its sign-in returns, and what it may do", async () => {
    serve(routes());
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect(await screen.findByText(/An app asks to use your ADE, as/)).toBeTruthy();
    expect(screen.getByText("owner@example.org")).toBeTruthy();
    expect(screen.getByText(/It calls itself/).textContent).toMatch(/It calls itself “Claude”: its own word\. Its sign-in returns to claude\.ai\./);
    expect(screen.getByText(/See your sessions as the ADE lists them/).textContent).toContain(
      "with the full text of the commands your sessions wait to run and their folders");
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/What it reads goes to that app and its servers/)).toBeTruthy();
    expect(screen.getByText(/takes effect after 3 minutes, and you can cancel it in Connected apps until then/)).toBeTruthy();
    expect(screen.getByText(/lasts until you revoke it, or 90 days/)).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("draws the app's name with what is not shown as itself written out, in a box clipped to its line", async () => {
    const stack = "Claude" + String.fromCodePoint(0x359).repeat(54) + String.fromCodePoint(0x3164);
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ client_name: stack })] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    const line = await screen.findByText(/It calls itself/);
    const name = line.querySelector("bdi")!;
    expect(name.textContent).toBe("Claude" + String.fromCodePoint(0x359).repeat(54) + "[U+3164]");
    for (const c of ["inline-block", "overflow-hidden", "max-w-full"]) expect(name.classList.contains(c)).toBe(true);
  });

  it("warns when another browser than the one that made the request shows it, whatever the network", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ same_browser: false })] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect((await screen.findByLabelText("Started in another browser")).textContent).toBe(OTHER_BROWSER);
    expect(screen.queryByLabelText("Asked from another network")).toBeNull();          // the same network: only this
    expect(screen.queryByLabelText("How the request came")).toBeNull();                // and from Claude's own page
    expect(screen.getByLabelText("Did you start it").className).toContain("font-medium");
  });

  it("says nothing of the browser when it is the one that made the request, or when the server cannot say", async () => {
    for (const same_browser of [true, null]) {
      serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ same_browser })] }));
      render(<ConnectApp id={R} onClose={() => {}} />);
      expect(await screen.findByLabelText("Did you start it")).toBeTruthy();
      expect(screen.queryByLabelText("Started in another browser")).toBeNull();
      expect(screen.getByLabelText("Did you start it").className).toContain("text-muted");
      cleanup();
    }
  });

  it("allows with the account's confirmation first, then goes to exactly the app's address", async () => {
    serve(routes());
    const go = vi.spyOn(leaving, "to").mockImplementation(() => {});
    render(<ConnectApp id={R} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Allow/ }));
    expect(calls.filter((c) => c.path.endsWith("/allow"))).toEqual([]);          // nothing allowed before the word
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "correct horse" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith(REDIRECT));
    // the confirmation is made for allowing this request, and nothing else
    expect(calls.find((c) => c.path === "/api/account/confirm" && c.method === "POST")?.body).toEqual({ password: "correct horse", change: `connector-allow:${R}:propose` });
    expect(calls.find((c) => c.path.endsWith("/allow"))?.body).toEqual({ propose: true, account: "u-1", confirmation: "conf-1" });
    expect(await screen.findByText("Going back to claude.ai.")).toBeTruthy();
    expect(screen.queryByLabelText("Asked from another network")).toBeNull();
  });

  it("warns when the request was made from another network than this browser's, and binds Allow to the account shown", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ asked_from: "198.51.100.7", same_network: false })] }));
    vi.spyOn(leaving, "to").mockImplementation(() => {});
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect((await screen.findByLabelText("Asked from another network")).textContent).toBe(
      "This request was made from 198.51.100.7, another network than this browser's (203.0.113.5).");
    expect(screen.getByLabelText("Did you start it").textContent).toBe(CLICKED);
    fireEvent.click(screen.getByRole("button", { name: /Allow/ }));
    fireEvent.change(await screen.findByLabelText("Your password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(calls.find((c) => c.path.endsWith("/allow"))?.body).toMatchObject({ account: "u-1" }));
  });

  it("says nothing of the network when either address is unknown", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ asked_from: null, viewer: null, same_network: null })] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect(await screen.findByText(/An app asks to use your ADE, as/)).toBeTruthy();
    expect(screen.queryByLabelText("Asked from another network")).toBeNull();
  });

  it("always says to refuse a request the owner did not just start, and quietly when it came from the app's own page", async () => {
    serve(routes());
    render(<ConnectApp id={R} onClose={() => {}} />);
    const said = await screen.findByLabelText("Did you start it");
    expect(said.textContent).toBe("If you did not just click Connect in Claude, in this browser, or in a Claude app on this device that opened it, refuse.");
    expect(said.className).toContain("text-muted");
    expect(screen.queryByLabelText("How the request came")).toBeNull();
  });

  it.each([
    [{ fetch_site: "none" as const, came_from: null }, "It was opened directly, not from a page of claude.ai: by an app, a bookmark, or an address pasted or sent to you."],
    [{ fetch_site: "cross-site" as const, came_from: "https://mail.example" }, "It came from a page of https://mail.example, not of claude.ai, where its sign-in returns."],
    [{ fetch_site: "cross-site" as const, came_from: null }, "It came from a page of another site, which the browser did not name, not one it says is claude.ai's."],
    [{ fetch_site: "same-origin" as const, came_from: "https://pp.example" }, "It was opened from a page of this ADE's own site, not from claude.ai."],
    [{ fetch_site: null, came_from: null }, "This browser did not say where it came from, so nothing shows it came from claude.ai."],
  ])("warns of a request that did not come as a navigation from the app's own site, as a forwarded address does: %o", async (how, words) => {
    // the owner's own browser opens what was forwarded, on the owner's own network: the network says nothing
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ ...how, from_app_page: false })] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect((await screen.findByLabelText("How the request came")).textContent).toBe(words);
    expect(screen.queryByLabelText("Asked from another network")).toBeNull();
    expect(screen.getByLabelText("Did you start it").className).toContain("text-wait");
  });

  it("allows reading alone when propose is cleared", async () => {
    serve(routes());
    vi.spyOn(leaving, "to").mockImplementation(() => {});
    render(<ConnectApp id={R} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Allow/ }));
    const password = await screen.findByLabelText("Your password");
    // the choice is fixed while its confirmation is asked, and the keyboard is in the prompt
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
    await vi.waitFor(() => expect(document.activeElement).toBe(password));
    fireEvent.change(password, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    await vi.waitFor(() => expect(calls.find((c) => c.path.endsWith("/allow"))?.body).toEqual({ propose: false, account: "u-1", confirmation: "conf-1" }));
    // the confirmation is made for allowing reading alone, as the pane showed it
    expect(calls.find((c) => c.path === "/api/account/confirm" && c.method === "POST")?.body).toEqual({ password: "pw", change: `connector-allow:${R}:read` });
  });

  it("refuses, and the app is told so at its own address", async () => {
    serve(routes());
    const go = vi.spyOn(leaving, "to").mockImplementation(() => {});
    render(<ConnectApp id={R} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Refuse" }));
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("https://claude.ai/api/mcp/auth_callback?error=access_denied&state=st-1"));
  });

  it("says when the request has ended", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [404, { detail: "ended" }] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect(await screen.findByText(ENDED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Allow/ })).toBeNull();
  });

  it("with the account's switch off, says so and offers no Allow", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ account_on: false })] }));
    const apps = vi.fn();
    render(<ConnectApp id={R} onClose={() => {}} onApps={apps} />);
    expect((await screen.findByRole("alert")).textContent).toContain(ACCOUNT_OFF);
    expect(screen.queryByRole("button", { name: /Allow/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open Connected apps" }));
    expect(apps).toHaveBeenCalled();
  });

  it("warns of an app that receives its sign-in on loopback, and says where its description was read", async () => {
    serve(routes({ [`GET /api/connector/requests/${R}`]: () => [200, request({ loopback: true, returns_to: "localhost", port: 3118, described_at: "claude.ai", hold_seconds: 0, scopes: ["ade.read"] })] }));
    render(<ConnectApp id={R} onClose={() => {}} />);
    expect((await screen.findByRole("note")).textContent).toMatch(/receives its sign-in at localhost:3118: any program on that computer can ask in its name/);
    expect(screen.getByText(/It calls itself/).textContent).toContain("It is described at claude.ai.");
    expect(screen.getByText(/takes effect at once/)).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();                    // read only was asked
  });
});
