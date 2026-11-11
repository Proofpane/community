import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { Api } from "../api";
import type { Session } from "../types";
import { CommandPalette } from "./CommandPalette";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeAll(() => {
  // cmdk scrolls the chosen item into view; the test's DOM has none of that
  Element.prototype.scrollIntoView ??= () => {};
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

const session = (id: string, title: string): Session =>
  ({ id, title, repo: "todo-app", agent: "claude", state: "running", branch: `pp/${id}` }) as Session;
const SESSIONS = [session("aaaaaaaaaaaa", "Fix the parser"), session("bbbbbbbbbbbb", "Write notes for the release")];
const actions = (run = vi.fn()) => [
  { id: "search", label: "Search the text of this session's files", withQuery: (q: string) => `Search this session's files for “${q}”`, run },
  { id: "notify", label: "Notifications…", run: vi.fn() },
  { id: "invite", label: "Invite someone", run: vi.fn() },
  { id: "stop", label: "Stop the workspace", run: vi.fn() },
];
const items = () => [...document.querySelectorAll("[cmdk-item]")].map((i) => i.textContent || "");

describe("the command palette", () => {
  it("puts a session or a file a name means before actions whose letters it merely has", async () => {
    vi.spyOn(Api, "paths").mockResolvedValue({ paths: ["docs/my notes.txt", "src/app.ts"] } as never);
    render(<CommandPalette sessions={SESSIONS} actions={actions()} onPick={() => {}} onClose={() => {}}
      filesOf={{ id: "aaaaaaaaaaaa", title: "Fix the parser" }} />);
    fireEvent.change(screen.getByPlaceholderText(/Go to a session or a file/), { target: { value: "notes" } });
    await vi.waitFor(() => expect(items().some((t) => t.includes("my notes.txt"))).toBe(true));
    const shown = items();
    expect(shown[0]).toContain("Write notes for the release");
    expect(shown[1]).toContain("my notes.txt");
    expect(shown.some((t) => t.includes("Invite someone"))).toBe(false);
  });

  it("offers a search for whatever was typed, on top of the actions, whose own letters it need not have", () => {
    const run = vi.fn();
    render(<CommandPalette sessions={[]} actions={actions(run)} onPick={() => {}} onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/Go to a session/), { target: { value: "parseDate" } });
    expect(items()[0]).toContain("Search this session's files for “parseDate”");
    fireEvent.click(screen.getByText("Search this session's files for “parseDate”"));
    expect(run).toHaveBeenCalledWith("parseDate");
  });

  it("offers the search even when nothing else matches what was typed", () => {
    const run = vi.fn();
    render(<CommandPalette sessions={[]} actions={actions(run)} onPick={() => {}} onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/Go to a session/), { target: { value: "subtract" } });
    const item = screen.getByText("Search this session's files for “subtract”");
    expect(item.closest("[cmdk-group]")?.hasAttribute("hidden")).toBe(false);
    expect(screen.queryByText("Nothing matches.")).toBeNull();
    fireEvent.click(item);
    expect(run).toHaveBeenCalledWith("subtract");
  });

  it("starts an action picked by its own name with nothing typed in it", () => {
    const run = vi.fn();
    render(<CommandPalette sessions={[]} actions={actions(run)} onPick={() => {}} onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/Go to a session/), { target: { value: "search the text" } });
    fireEvent.click(screen.getByText("Search the text of this session's files"));
    expect(run).toHaveBeenCalledWith("");
  });

  it("chooses a session to open beside, with nothing else in it", () => {
    const pick = vi.fn();
    render(<CommandPalette sessions={SESSIONS} actions={actions()} onPick={pick} onClose={() => {}} beside
      filesOf={{ id: "aaaaaaaaaaaa", title: "Fix the parser" }} />);
    expect(screen.getByPlaceholderText("Open a session beside the ones shown…")).toBeTruthy();
    expect(items()).toHaveLength(2);
    fireEvent.click(screen.getByText("Write notes for the release"));
    expect(pick).toHaveBeenCalledWith("bbbbbbbbbbbb");
  });
});

describe("a closed session in the palette", () => {
  it("is listed after the others, said to be closed, found by the word, and picked as any other", () => {
    const onPick = vi.fn();
    render(<CommandPalette sessions={[{ ...session("cccccccccccc", "An old one"), closed: true }, ...SESSIONS]} actions={[]}
      onPick={onPick} onClose={() => {}} />);
    const shown = items();
    expect(shown[0]).toContain("Fix the parser");
    expect(shown[0]).not.toContain("Closed");
    expect(shown[2]).toContain("An old one");
    expect(shown[2]).toContain("Closed");
    fireEvent.change(screen.getByPlaceholderText(/Go to a session/), { target: { value: "closed" } });
    expect(items()).toEqual([expect.stringContaining("An old one")]);
    fireEvent.click(screen.getByText("An old one"));
    expect(onPick).toHaveBeenCalledWith("cccccccccccc");
  });
});
