import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The sign-in screen's device sign-in: Proofpane Tray's sign-in is not offered
// in the desktop app, since every hosted account refuses an approval the tray can give, and a device whose
// approval was withdrawn before it signed in is told so, not that the other device refused it.

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
  localStorage.clear();
  delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
});

describe("the desktop app's sign-in at a hosted place", () => {
  it("offers no sign-in with Proofpane Tray, and asks the tray nothing, even with one linked to that server", async () => {
    vi.stubEnv("MODE", "desktop");
    vi.resetModules();
    const places = await import("../places");
    places.savePlace({ id: "hosted", server: "https://pp.example", label: "pp.example" });
    places.setActive("hosted");
    const invoked: string[] = [];
    (window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string) => {
        invoked.push(cmd);
        return cmd === "tray_status" ? { reachable: true, linked: true, email: "a@example.org" } : null;
      },
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
    const { SignIn } = await import("./SignIn");
    render(<SignIn />);
    await new Promise((r) => setTimeout(r, 400));          // past the moment the tray was asked
    expect(screen.queryByRole("button", { name: /Proofpane Tray/ })).toBeNull();
    expect(invoked).not.toContain("tray_status");
    expect(screen.getByRole("button", { name: /Sign in with another device/ })).toBeTruthy();
  });
});

describe("a device waiting to be signed in by another", () => {
  const answering = (polled: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).endsWith("/api/auth/device/start")) {
      return new Response(JSON.stringify({ id: "r1", code: "ABCD-EFGH", poll: "p", expires_at: 9e9, interval: 0 }), { status: 200 });
    }
    return new Response(JSON.stringify(polled), { status: 200 });
  }));

  it("says an approval withdrawn before it signed in was withdrawn, not refused on the other device", async () => {
    answering({ status: "refused", withdrawn: true });
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    expect(await screen.findByText(/Its approval was withdrawn before this device signed in/)).toBeTruthy();
    expect(screen.queryByText("It was refused on the other device.")).toBeNull();
    expect(screen.getByRole("button", { name: "A new code" })).toBeTruthy();
  });

  it("says a refusal on the other device as before", async () => {
    answering({ status: "refused" });
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    await waitFor(() => expect(screen.getByText("It was refused on the other device.")).toBeTruthy());
    expect(screen.queryByText(/withdrawn/)).toBeNull();
  });
});
