import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LinkSessions } from "./api";
import type { Session } from "./types";
import { useStore } from "./store";

const listened: Record<string, (e: { payload: unknown }) => void> = {};
vi.mock("@tauri-apps/api/event", () => ({
  listen: async (name: string, fn: (e: { payload: unknown }) => void) => { listened[name] = fn; return () => { delete listened[name]; }; },
}));

import { MENU_BAR_EMPTY, MENU_BAR_MOST, menuBarSummary, useMenuBar, type MenuBarOpen, type MenuBarSummary } from "./menuBar";

afterEach(cleanup);

const session = (n: number, over: Partial<Session> = {}): Session => ({
  id: n.toString(16).padStart(12, "0"), title: `Task ${n}`, repo: "demo", agent: "claude", state: "waiting",
  updatedAt: `2026-10-02T00:00:${String(n).padStart(2, "0")}Z`, note: "Run rm -rf build?", alert: "Wants to use Bash", ...over,
});

describe("what the menu bar shows", () => {
  it("is the sessions that wait for the person, newest first, by name and never by command, and the approvals", () => {
    const s = menuBarSummary("This Mac", [session(1), session(2, { state: "working" }), session(3), session(4, { alert: undefined })], 2);
    expect(s).toEqual({
      place: "This Mac", approvals: 2, count: 5,
      waiting: [
        { session: "000000000004", title: "Task 4", note: "" },
        { session: "000000000003", title: "Task 3", note: "Wants to use Bash" },
        { session: "000000000001", title: "Task 1", note: "Wants to use Bash" },
      ],
    });
    expect(JSON.stringify(s)).not.toContain("rm -rf");
  });

  it("lists at most a few, and counts all of them", () => {
    const s = menuBarSummary("proofpane-oss.fly.dev", Array.from({ length: 14 }, (_, i) => session(i + 1)), 0);
    expect(s.waiting.length).toBe(MENU_BAR_MOST);
    expect(s.count).toBe(14);
    expect(menuBarSummary("x".repeat(100), [], 0)).toEqual({ place: "x".repeat(60), waiting: [], approvals: 0, count: 0 });
  });

  it("cuts a name between characters, never inside one, so the desktop shell can read it", () => {
    const emoji = String.fromCodePoint(0x1f680);                 // two UTF-16 units
    const s = menuBarSummary("This Mac", [session(1, { title: "x".repeat(79) + emoji + "tail" })], 0);
    expect(s.waiting[0].title).toBe("x".repeat(79));            // the emoji would be cut in half: left out whole
    expect(menuBarSummary("This Mac", [session(1, { title: "x".repeat(78) + emoji })], 0).waiting[0].title).toBe("x".repeat(78) + emoji);
    // no half of a pair, which JSON gives as a lone escape that a strict reader refuses
    expect(JSON.stringify(s)).not.toMatch(/\\ud[89ab][0-9a-f]{2}(?!\\ud[c-f])/i);
  });
});

function Bar(props: { summary: MenuBarSummary | null; onOpen: (w: MenuBarOpen) => void; invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>; on?: boolean }) {
  useMenuBar(props.summary, props.onOpen, props.invoke as never, props.on ?? true);
  return null;
}

describe("what the menu bar counts of the linked sessions (the owner's inbox)", () => {
  // the link channel's items the one list holds: its open prompts (those to look at too), its proposals, and its
  // questions to the owner (or to all); a question to another session is that session's, not the owner's
  const links = {
    links: [], public_url: "https://pp.example",
    questions: [
      { seq: 5, time: "t", kind: "question", from: "mac", to: "owner", ref: null, text: "Push?", link_id: "L1" },
      { seq: 6, time: "t", kind: "question", from: "mac", to: "planner", ref: null, text: "Done?", link_id: "L1" },
    ],
    prompts: [
      { seq: 7, time: "t", kind: "prompt", from: "mac", to: "owner", ref: null, text: "e2e1.x", link_id: "L1", deadline_at: 2, look: false },
      { seq: 8, time: "t", kind: "prompt", from: "mac", to: "owner", ref: null, text: "Session paused", link_id: "L1", deadline_at: 2, look: true },
    ],
    proposals: [
      { seq: 9, time: "t", kind: "proposal", from: "planner", to: "owner", ref: null, text: "e2e1.y", link_id: "L2", for_id: "L1", for: "mac", deadline_at: 3 },
    ],
  } as unknown as LinkSessions;

  it("counts its open prompts, look ones too, its proposals and its questions to the owner beside the approvals", () => {
    const s = menuBarSummary("This Mac", [session(1)], 2, links);
    expect(s.approvals).toBe(2 + 4);
    expect(s.count).toBe(1 + 2 + 4);
    // names only, as ever: no word any of them carries
    expect(JSON.stringify(s)).not.toMatch(/Push\?|Session paused|e2e1\./);
    expect(menuBarSummary("This Mac", [], 0, null)).toEqual({ place: "This Mac", waiting: [], approvals: 0, count: 0 });
  });

  it("is kept current while the window is closed to the menu bar: the linked sessions are read again with the rest", () => {
    const refresh = vi.fn(async () => {});
    const refreshApprovals = vi.fn(async () => {});
    const refreshLinks = vi.fn(async () => {});
    useStore.setState({ refresh, refreshApprovals, refreshLinks });
    const w = window as unknown as { __ppMenuBarTick?: () => void };
    render(<Bar summary={null} onOpen={() => {}} invoke={async () => null} />);
    w.__ppMenuBarTick!();
    expect(refreshLinks).toHaveBeenCalledTimes(1);
  });
});

describe("the menu bar, in the desktop app", () => {
  it("is told when what needs the person changes, and only then", async () => {
    const calls: [string, unknown][] = [];
    const invoke = async (cmd: string, args?: Record<string, unknown>) => { calls.push([cmd, args]); return null; };
    const a = menuBarSummary("This Mac", [session(1)], 0);
    const { rerender } = render(<Bar summary={a} onOpen={() => {}} invoke={invoke} />);
    rerender(<Bar summary={{ ...a }} onOpen={() => {}} invoke={invoke} />);
    const b = menuBarSummary("This Mac", [], 0);
    rerender(<Bar summary={b} onOpen={() => {}} invoke={invoke} />);
    expect(calls).toEqual([["menu_bar", { summary: a }], ["menu_bar", { summary: b }]]);
  });

  it("is emptied when the person signs out, so no session's name stays in it", async () => {
    const calls: [string, unknown][] = [];
    const invoke = async (cmd: string, args?: Record<string, unknown>) => { calls.push([cmd, args]); return null; };
    const a = menuBarSummary("This Mac", [session(1)], 1);
    const { rerender } = render(<Bar summary={a} onOpen={() => {}} invoke={invoke} />);
    rerender(<Bar summary={null} onOpen={() => {}} invoke={invoke} />);
    rerender(<Bar summary={null} onOpen={() => {}} invoke={invoke} />);
    expect(calls).toEqual([["menu_bar", { summary: a }], ["menu_bar", { summary: MENU_BAR_EMPTY }]]);
  });

  it("opens what is chosen in its menu", async () => {
    const opened: MenuBarOpen[] = [];
    render(<Bar summary={null} onOpen={(w) => opened.push(w)} invoke={async () => null} />);
    await vi.waitFor(() => expect(listened["menu-bar-open"]).toBeDefined());
    act(() => listened["menu-bar-open"]({ payload: { session: "000000000003" } }));
    act(() => listened["menu-bar-open"]({ payload: { approvals: true } }));
    expect(opened).toEqual([{ session: "000000000003" }, { approvals: true }]);
    cleanup();
    expect(listened["menu-bar-open"]).toBeUndefined();
  });

  it("is not asked for outside the desktop app, and an older app without one is no error", async () => {
    const calls: string[] = [];
    render(<Bar summary={menuBarSummary("This Mac", [session(1)], 0)} onOpen={() => {}} invoke={async (cmd) => { calls.push(cmd); return null; }} on={false} />);
    expect(calls).toEqual([]);
    cleanup();
    const failing = vi.fn(async () => { throw new Error("command menu_bar not found"); });
    render(<Bar summary={menuBarSummary("This Mac", [session(1)], 0)} onOpen={() => {}} invoke={failing} />);
    await vi.waitFor(() => expect(failing).toHaveBeenCalledTimes(1));
  });
});

describe("the page, while the desktop window is closed to the menu bar", () => {
  it("gives the shell a way to have it read again, and takes it away when it goes", async () => {
    const refresh = vi.fn(async () => {});
    const refreshApprovals = vi.fn(async () => {});
    useStore.setState({ refresh, refreshApprovals });
    const w = window as unknown as { __ppMenuBarTick?: () => void };
    const { unmount } = render(<Bar summary={null} onOpen={() => {}} invoke={async () => null} />);
    expect(typeof w.__ppMenuBarTick).toBe("function");
    w.__ppMenuBarTick!();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refreshApprovals).toHaveBeenCalledTimes(1);
    unmount();
    expect(w.__ppMenuBarTick).toBeUndefined();
    render(<Bar summary={null} onOpen={() => {}} invoke={async () => null} on={false} />);
    expect(w.__ppMenuBarTick).toBeUndefined();               // not in a browser
  });
});
