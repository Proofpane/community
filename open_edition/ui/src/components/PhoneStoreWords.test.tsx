import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// What the phone app says of itself, for its App Store listing (queue 4.328, STORE-READY): its privacy policy linked on
// the sign-in screen and in the account menu, opened in the phone's browser (phone/src-tauri/src/lib.rs,
// open_outside), and linked nowhere in a server's own pages, whose operator's policy is not this one; and no words
// written for a browser where the app shows its notifications and its voice input's notes.

const PRIVACY = "https://proofpane.com/community/privacy/";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
  localStorage.clear();
});

async function asPhone<T>(load: () => Promise<T>): Promise<T> {
  vi.stubEnv("MODE", "phone");
  vi.resetModules();
  return load();
}

const nothingServed = () => vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));

function openMenu(TitleBar: typeof import("./TitleBar").TitleBar) {
  render(<TitleBar workspace={null} pendingApprovals={0} onSearch={() => {}} onApprovals={() => {}} account="you@example.org" onLicenses={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Your account, you@example.org" }));
}

describe("the phone app's privacy policy", () => {
  it("is linked on the sign-in screen, and opens in the phone's browser", async () => {
    nothingServed();
    const { SignIn } = await asPhone(() => import("./SignIn"));
    render(<SignIn />);
    const link = screen.getByRole("link", { name: "Privacy" });
    expect(link.getAttribute("href")).toBe(PRIVACY);
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("is linked in the account menu", async () => {
    const { TitleBar } = await asPhone(() => import("./TitleBar"));
    openMenu(TitleBar);
    const item = screen.getByRole("menuitem", { name: "Privacy" });
    expect(item.getAttribute("href")).toBe(PRIVACY);
    expect(item.getAttribute("target")).toBe("_blank");
  });

  it("is linked nowhere in a server's own pages", async () => {
    nothingServed();
    const { SignIn } = await import("./SignIn");
    render(<SignIn />);
    expect(screen.queryByRole("link", { name: "Privacy" })).toBeNull();
    cleanup();
    const { TitleBar } = await import("./TitleBar");
    openMenu(TitleBar);
    expect(screen.queryByRole("menuitem", { name: "Privacy" })).toBeNull();
  });
});

describe("the phone app's own words where a browser's were", () => {
  it("its notifications say the app shows none, with nothing of a Home Screen or a browser", async () => {
    nothingServed();
    const { useStore } = await asPhone(() => import("../store"));
    const { AccountDialog } = await import("./AccountDialog");
    useStore.setState({ user: { id: "u", email: "you@example.org", can_sign_in_again: false } });
    render(<AccountDialog tab="notifications" onClose={() => {}} />);
    expect(screen.getByText("This app shows no notifications.")).toBeTruthy();
    expect(document.body.textContent || "").not.toMatch(/Home Screen|browser/i);
  });

  it("its voice input's notes name Apple's speech recognition, and no browser", async () => {
    const voice = await asPhone(() => import("../voice"));
    const errors = (voice as unknown as { ERRORS?: Record<string, string> }).ERRORS ?? {};
    expect(voice.NOTE).toBe("Apple's speech recognition hears what you say, not Proofpane.");
    expect([voice.NOTE, ...Object.values(errors)].join(" ")).not.toMatch(/browser|Safari|Chrome/i);
    expect(Object.keys(errors)).toEqual(["not-allowed", "service-not-allowed", "no-speech", "audio-capture", "network",
      "language-not-supported"]);
  });

  it("a browser's own voice notes stay as they were", async () => {
    const voice = await import("../voice");
    expect(voice.NOTE).toBe("The browser's speech service (Apple's in Safari, Google's in Chrome) hears what you say, not Proofpane.");
  });
});
