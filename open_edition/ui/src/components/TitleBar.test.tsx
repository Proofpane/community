import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PlaceMenu, TitleBar } from "./TitleBar";

const base = { workspace: { provider: "fly" as const, running: true }, pendingApprovals: 0, onSearch: () => {}, onApprovals: () => {} };

describe("the title bar's quota meter", () => {
  it("shows each window the status line recorded, with its share used", () => {
    render(<TitleBar {...base} quotas={{ claude: { at: 1_800_000_000, five_hour: { used_percentage: 42.4, resets_at: 1_800_003_600 }, seven_day: { used_percentage: 96, resets_at: null } } }} />);
    expect(screen.getByText("Claude")).toBeTruthy();
    expect(screen.getByText("5h")).toBeTruthy();
    expect(screen.getByText("42%")).toBeTruthy();
    expect(screen.getByText("96%")).toBeTruthy();
  });

  it("shows Codex's windows beside Claude's, each named", () => {
    render(<TitleBar {...base} quotas={{
      claude: { at: 1_800_000_000, five_hour: { used_percentage: 10, resets_at: null } },
      codex: { at: 1_800_000_100, five_hour: { used_percentage: 42.5, resets_at: 1_800_003_600 }, seven_day: { used_percentage: 7, resets_at: null } },
    }} />);
    expect(screen.getByText("Claude")).toBeTruthy();
    expect(screen.getByText("Codex")).toBeTruthy();
    expect(screen.getByText("43%")).toBeTruthy();
    expect(screen.getByText("7%")).toBeTruthy();
    expect(screen.getByTitle(/Codex, 5h window: 43% used/)).toBeTruthy();
  });

  it("says nothing when no window was recorded", () => {
    render(<TitleBar {...base} quotas={{ claude: { at: 1_800_000_000, model: "Opus" } }} />);
    expect(screen.queryByText("5h")).toBeNull();
    expect(screen.queryByText("7d")).toBeNull();
  });

  it("counts what waits for the person on the bell, in the page's own colour on the amber", () => {
    render(<TitleBar {...base} pendingApprovals={3} />);
    expect(screen.getByText("3").className).toContain("bg-wait");
    expect(screen.getByText("3").className).toContain("text-bg");      // contrast.test.ts holds the pair readable
  });

  it("carries the spend today inside the usage windows, and alone when there are none", () => {
    const spend = /^Spend today/;
    render(<TitleBar {...base} spentTodayUsd={1.5} quotas={{ claude: { at: 1_800_000_000, five_hour: { used_percentage: 42.4, resets_at: null } } }} />);
    const inside = screen.getByTitle(spend);
    expect(inside.textContent).toBe("$1.50");
    expect(inside.parentElement?.textContent).toContain("42%");      // one control, not a second pill
    // shown from a narrow screen on, the windows from a wide one: the spend never hides with them
    const pill = inside.closest("span.rounded-full")!;
    expect(pill.className).toContain("sm:inline-flex");
    expect(inside.className).not.toContain("hidden");
    expect(screen.getByText("Claude").parentElement!.className).toContain("lg:flex");
    cleanup();
    render(<TitleBar {...base} spentTodayUsd={1.5} />);
    expect(screen.getByTitle(spend).textContent).toBe("$1.50");
    cleanup();
    render(<TitleBar {...base} />);
    expect(screen.queryByTitle(spend)).toBeNull();
  });
});

describe("the desktop app's workspace menu", () => {
  it("says whether the workspace it shows runs", () => {
    render(<PlaceMenu workspace={{ provider: "local", running: false }} />);
    expect(screen.getByTitle(/^The workspace this window shows: stopped$/)).toBeTruthy();
    cleanup();
    render(<PlaceMenu workspace={{ provider: "local", running: true }} />);
    expect(screen.getByTitle(/^The workspace this window shows: running$/)).toBeTruthy();
    cleanup();
    render(<PlaceMenu workspace={null} />);
    expect(screen.getByTitle(/^The workspace this window shows: connecting$/)).toBeTruthy();
  });
});

describe("the account menu", () => {
  it("takes the focus to its first item, moves along with the arrows, and gives it back on Escape", () => {
    render(<TitleBar {...base} account="you@example.org" onAccount={() => {}} onSignOut={() => {}} />);
    const avatar = screen.getByRole("button", { name: "Your account, you@example.org" });
    fireEvent.click(avatar);
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(window, { key: "ArrowUp" });
    fireEvent.keyDown(window, { key: "ArrowUp" });                 // past the first, round to the last
    expect(document.activeElement).toBe(items[items.length - 1]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(avatar);
  });

  it("opens the account's Encryption, its Connected apps and the Linked sessions", () => {
    const onAccount = vi.fn();
    const onChannel = vi.fn();
    render(<TitleBar {...base} account="you@example.org" onAccount={onAccount} onChannel={onChannel} />);
    const avatar = screen.getByRole("button", { name: "Your account, you@example.org" });
    const pick = (name: string) => { fireEvent.click(avatar); fireEvent.click(screen.getByRole("menuitem", { name })); };
    pick("Encryption");
    expect(onAccount).toHaveBeenLastCalledWith("encryption");
    pick("Connected apps");
    expect(onAccount).toHaveBeenLastCalledWith("apps");
    pick("Linked sessions");
    expect(onChannel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("the workspace's state beside the product's name", () => {
  it("never calls a workspace never started stopped, and shows none where the deployment gives the account none", () => {
    const pill = (workspace: Parameters<typeof TitleBar>[0]["workspace"]) => {
      cleanup();
      render(<TitleBar {...base} workspace={workspace} />);
      return screen.getByRole("banner").querySelector(".pill")?.textContent ?? null;
    };
    expect(pill({ provider: "fly", running: false, made: false })).toBe("Workspace not started");
    expect(pill({ provider: "fly", running: false, made: true })).toBe("Workspace stopped");
    expect(pill({ provider: "fly", running: false, made: false, starting: true })).toBe("Workspace starting");
    expect(pill({ provider: "fly", running: true, made: true })).toBe("Workspace");
    expect(pill(null)).toBe("Connecting…");
    expect(pill(false)).toBeNull();
  });
});

describe("the title bar's buttons, to a screen reader", () => {
  it("are named with what waits behind each, and their badges, which say it again, are hidden", () => {
    render(<TitleBar {...base} pendingApprovals={3} channelAlerts={1} onChannel={() => {}} agentsWaiting={2} onAgents={() => {}} />);
    expect(screen.getByRole("button", { name: "Approvals, 3 waiting" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Linked sessions, 1 needs you" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Agents, 2 waiting for you" })).toBeTruthy();
    for (const n of ["3", "1", "2"]) expect(screen.getByText(n).getAttribute("aria-hidden"), n).toBe("true");
    cleanup();
    render(<TitleBar {...base} channelAlerts={4} onChannel={() => {}} />);
    expect(screen.getByRole("button", { name: "Linked sessions, 4 need you" })).toBeTruthy();
    cleanup();
    // nothing waiting: the names alone; Linked sessions is there with nothing linked
    render(<TitleBar {...base} onChannel={() => {}} />);
    expect(screen.getByRole("button", { name: "Approvals" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Linked sessions" })).toBeTruthy();
  });
});

describe("the title bar on a touch screen", () => {
  it("makes each of its controls a 44-pixel target that does not shrink, the Sessions button among them", () => {
    render(<TitleBar {...base} account="you@example.org" onChannel={() => {}} onAgents={() => {}} onMenu={() => {}} />);
    const controls = within(screen.getByRole("banner")).getAllByRole("button");
    expect(controls.map((b) => b.getAttribute("aria-label") || b.textContent)).toContain("Sessions");
    for (const b of controls) {
      const name = b.getAttribute("aria-label") || b.textContent || "";
      expect(b.className, name).toMatch(/(^| )touch-target( |$)/);
      // each but the search field, which fills the bar's middle on a wide screen (and is not shown on a narrow one)
      if (!name.startsWith("Search sessions")) expect(b.className, name).toMatch(/(^| )flex-none( |$)/);
    }
    // a touch target is 44 by 44 pixels on a touch screen
    const css = readFileSync(join(__dirname, "..", "index.css"), "utf8");
    expect(css).toContain("@media (pointer: coarse) { .touch-target { min-width: 44px; min-height: 44px; } }");
    // the bar holds them below a phone's status bar, its height its content's (box-content), and gives the product's
    // name and edition way on a narrow screen
    const bar = screen.getByRole("banner");
    expect(bar.className).toMatch(/(^| )box-content( |$)/);
    expect(screen.getByText("Proofpane").className).toMatch(/(^| )hidden( |$).*sm:inline/);
    expect(screen.getByText("Community").className).toMatch(/(^| )hidden( |$).*sm:inline/);
  });

  it("says whether the phone's list of sessions is open", () => {
    render(<TitleBar {...base} onMenu={() => {}} menuOpen />);
    const menu = screen.getByRole("button", { name: "Sessions" });
    expect(menu.getAttribute("aria-haspopup")).toBe("dialog");
    expect(menu.getAttribute("aria-expanded")).toBe("true");
  });
});

