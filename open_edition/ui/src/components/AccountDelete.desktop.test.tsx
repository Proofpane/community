import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The desktop app, signed in to a hosted place (the ruling of 2026-11-22: no deletion control in the desktop app): the
// mode is set before any module is read, as the desktop build sets it.
vi.hoisted(() => { vi.stubEnv("MODE", "desktop"); });

import { savePlace, setActive, setPlaceToken } from "../places";
import { useStore } from "../store";
import { AccountDialog } from "./AccountDialog";

type Route = () => [number, unknown];
const serve = (routes: Record<string, Route>) => {
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(String(url), "http://page.test").pathname;
    paths.push(path);
    const route = routes[`${init.method || "GET"} ${path}`];
    const [status, data] = route ? route() : [404, { detail: "no route" }];
    return new Response(JSON.stringify(data), { status });
  }));
  return paths;
};

beforeEach(() => {
  savePlace({ id: "h1", server: "https://pp.example", label: "pp.example", email: "a@example.org" });
  setActive("h1");
  setPlaceToken("h1", "hosted.sig", false);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setPlaceToken("h1", null, false); localStorage.clear(); useStore.setState(useStore.getInitialState()); });

describe("deleting the account, in the desktop app", () => {
  it("is not offered there, whichever place is open, and the deletion is not even read", async () => {
    const paths = serve({
      "GET /api/account/passkeys": () => [200, { passkeys: [] }],
      "GET /api/account/confirm": () => [200, { needed: true, password: true, passkeys: 0 }],
      "GET /api/account/delete": () => [200, { deletion: null, waits: 7 * 86400 }],
      "GET /api/ade/teammate/custody": () => [200, { as_owner: null, as_custodian: [], tasks: [] }],
    });
    useStore.setState({ user: { id: "u", email: "a@example.org", can_sign_in_again: true } });
    render(<AccountDialog onClose={() => {}} />);
    await screen.findByText(/Sign out everywhere/);                 // a hosted place: its account's controls are shown
    expect(screen.queryByRole("heading", { name: "Delete this account" })).toBeNull();
    expect(paths).not.toContain("/api/account/delete");
  });
});
