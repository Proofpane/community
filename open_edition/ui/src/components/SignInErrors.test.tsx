import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// An error on the sign-in screen, and on a device waiting to be signed in, is announced to a
// screen reader as it appears (role alert), not only drawn in red.

// an error line, or a line of a refused, withdrawn, failed or expired device sign-in: `{error && <div ...`,
// `{(here || local) && error && <div ...` or `{state === "refused" && <div ...`
const ERROR_LINE = /\{(?:\([^)]*\)\s*&&\s*)?(?:error|state === "(?:refused|withdrawn|failed|expired)")\s*&&\s*\(?\s*<div\b([^>]*)>/g;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  localStorage.clear();
});

describe("the sign-in screen's errors", () => {
  it("announces a refused sign-in with the server's own words", async () => {
    const signIn = vi.fn(async () => { throw new Error("That email and password do not match an account."); });
    // the store the screen will import, fresh, whatever ran before
    vi.resetModules();
    const { useStore } = await import("../store");
    useStore.setState({ signIn } as unknown as Partial<ReturnType<typeof useStore.getState>>);
    const { SignIn } = await import("./SignIn");
    render(<SignIn />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.org" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "not it" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That email and password do not match an account.");
  });
});

describe("the sign-in screen's and the account's error lines", () => {
  it("are each announced: every line shown on an error, or on a refused, withdrawn, failed or expired device sign-in, carries role alert", () => {
    const unannounced: string[] = [];
    for (const file of ["SignIn.tsx", "AccountDialog.tsx"]) {
      const text = readFileSync(join(__dirname, file), "utf8");
      for (const m of text.matchAll(ERROR_LINE)) {
        if (!/role="alert"/.test(m[1])) unannounced.push(`${file}: ${m[0]}`);
      }
    }
    expect(unannounced).toEqual([]);
  });

  it("are found by the check above (it reads at least the lines these two screens draw)", () => {
    const text = readFileSync(join(__dirname, "SignIn.tsx"), "utf8") + readFileSync(join(__dirname, "AccountDialog.tsx"), "utf8");
    expect([...text.matchAll(ERROR_LINE)].length).toBeGreaterThanOrEqual(13);
  });

  it("announces an invitation that cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "That invitation has expired or was used." }), { status: 400 })));
    const { SignIn } = await import("./SignIn");
    render(<SignIn invite="tok" onInviteDone={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toBe("That invitation has expired or was used.");
  });
});

describe("a device waiting to be signed in by another", () => {
  const answering = (polled: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).endsWith("/api/auth/device/start")) {
      return new Response(JSON.stringify({ id: "r1", code: "ABCD-EFGH", poll: "p", expires_at: 9e9, interval: 0 }), { status: 200 });
    }
    return new Response(JSON.stringify(polled), { status: 200 });
  }));

  it("announces a refusal on the other device", async () => {
    answering({ status: "refused" });
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toBe("It was refused on the other device.");
  });

  it("announces an approval withdrawn before it signed in", async () => {
    answering({ status: "refused", withdrawn: true });
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/^\s*Its approval was withdrawn before this device signed in/);
  });

  it("announces a code that expired before it was approved, which needs a new one", async () => {
    answering({ status: "expired" });
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toBe("The code expired before it was approved.");
  });

  it("announces a start that failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Too many devices asked from this network." }), { status: 429 })));
    const { DeviceSignIn } = await import("./SignIn");
    render(<DeviceSignIn onDone={async () => {}} onBack={() => {}} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Too many devices asked from this network.");
  });
});
