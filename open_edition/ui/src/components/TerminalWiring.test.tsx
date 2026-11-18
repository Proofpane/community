import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { useStore } from "../store";
import { browser, shellServer } from "../shellTestkit";
import { TerminalPane, type TerminalLink } from "./TerminalPane";

// The terminal pane as the app draws it, xterm.js stood in for (jsdom draws no terminal): which row it puts under the
// terminal, desktop's or phone's, that each key goes through the pane's own link as the bytes typed, that the arrows
// read the cursor-key mode from the terminal itself, that Stop and the line come for a session's agent only, and that a
// file dropped on the row is the pane's, not the browser's to open (WS-BUTTONS, part one).
const xterm = vi.hoisted(() => ({ terms: [] as { modes: { applicationCursorKeysMode: boolean } }[] }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    modes = { applicationCursorKeysMode: false };
    options: Record<string, unknown>;
    cols = 100;
    rows = 30;
    constructor(options: Record<string, unknown>) { this.options = options; xterm.terms.push(this); }
    loadAddon() {}
    attachCustomKeyEventHandler() {}
    open() {}
    onData() { return { dispose() {} }; }
    write() {}
    focus() {}
    paste() {}
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {
    onDidChangeResults() { return { dispose() {} }; }
    findNext() {}
    findPrevious() {}
    clearDecorations() {}
  },
}));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  xterm.terms.length = 0;
});

/** A stand-in for the pane's link: what each press sends, and whether the link takes it. */
function standIn(open = true) {
  const sent: string[] = [];
  const link: TerminalLink = {
    attach: () => ({ send: (d: string) => { sent.push(d); return open; }, resize() {}, close() {} }),
  };
  return { link, sent };
}
const keys = () => screen.getByRole("toolbar", { name: "Keys" });
const names = () => within(keys()).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
const press = (name: string) => fireEvent.click(within(keys()).getByRole("button", { name }));

describe("the terminal pane's row", () => {
  it("on a desktop is the desktop's, each key sent through the pane's link as the bytes typed", () => {
    browser();
    const { link, sent } = standIn();
    render(<TerminalPane link={link} />);
    expect(names()).toContain("Shift Tab");
    expect(names()).not.toContain("Stop");
    press("Shift Tab");
    press("Escape");
    press("Up arrow");
    expect(sent).toEqual(["\x1b[Z", "\x1b", "\x1b[A"]);
  });

  it("sends the arrows by the cursor-key mode the terminal holds at the press", () => {
    browser();
    const { link, sent } = standIn();
    render(<TerminalPane link={link} />);
    press("Down arrow");
    xterm.terms[0].modes.applicationCursorKeysMode = true;
    press("Down arrow");
    expect(sent).toEqual(["\x1b[B", "\x1bOB"]);
  });

  it("on a touch screen is the phone's, its arrows by the same mode", () => {
    browser({ touch: true });
    const { link, sent } = standIn();
    render(<TerminalPane link={link} agent={{ activity: { state: "working", at: 1 }, who: "Claude" }} />);
    expect(names()).not.toContain("Shift Tab");
    expect(names()).not.toContain("Stop");
    xterm.terms[0].modes.applicationCursorKeysMode = true;
    press("Left arrow");
    expect(sent).toEqual(["\x1bOD"]);
  });

  it("draws Stop and the line for a session's agent, Stop's Esc through the link", () => {
    browser();
    const { link, sent } = standIn();
    const agent = { activity: { state: "working" as const, tool: "Bash", at: 1 }, who: "DeepSeek" };
    render(<TerminalPane link={link} agent={agent} />);
    expect(screen.getByRole("status").textContent).toBe("DeepSeek is working: Bash");
    press("Stop");
    expect(sent).toEqual(["\x1b"]);
    expect(screen.getByRole("status").textContent).toBe("Esc sent");
  });

  it("says so when the link took nothing, its connection not open", () => {
    browser();
    const { link } = standIn(false);
    render(<TerminalPane link={link} agent={{ activity: null, who: "Claude" }} />);
    press("Stop");
    expect(screen.getByRole("status").textContent).toBe("Esc not sent: the terminal was not connected");
  });

  it("is not drawn for a terminal that takes no input", () => {
    browser();
    render(<TerminalPane link={standIn().link} readOnly />);
    expect(screen.queryByRole("toolbar", { name: "Keys" })).toBeNull();
  });

  it("carries the session's id to its row: Stop's press kept when the pane is drawn again", () => {
    browser();
    const agent = { id: "pane-id", activity: { state: "working" as const, at: 1 }, who: "Claude" };
    render(<TerminalPane link={standIn().link} agent={agent} />);
    press("Stop");
    cleanup();
    render(<TerminalPane link={standIn().link} agent={agent} />);
    expect(within(keys()).getByRole("status").textContent).toBe("Esc sent");
  });

  it("takes a file dropped on it as the pane's, so the browser does not open it, once, as on the terminal",
    async () => {
      browser();
      const onFiles = vi.fn(async (_files: File[]) => [] as string[]);
      const { container } = render(<TerminalPane link={standIn().link} onFiles={onFiles} />);
      const file = new File(["x"], "shot.png");
      const dataTransfer = { types: ["Files"], files: [file], dropEffect: "none" };
      expect(fireEvent.dragOver(keys(), { dataTransfer })).toBe(false);       // its default prevented: a drop is taken
      let taken = true;
      await act(async () => { taken = fireEvent.drop(keys(), { dataTransfer }); });
      expect(taken).toBe(false);
      expect(onFiles.mock.calls.map((c) => c[0])).toEqual([[file]]);
      // the terminal's own area: taken there too, and once, not by the area and the pane both
      const area = container.querySelector(".relative.min-h-0") as HTMLElement;
      await act(async () => { taken = fireEvent.drop(area, { dataTransfer }); });
      expect(taken).toBe(false);
      expect(onFiles.mock.calls.map((c) => c[0])).toEqual([[file], [file]]);
    });
});

describe("a running Claude Code session's terminal in the app", () => {
  // the app's own link opens a websocket; a stand-in one stays connecting, so nothing leaves the test
  class Socket { static OPEN = 1; readyState = 0; binaryType = ""; send() {} close() {} }
  const RUNNING = { enabled: true, allowed: true, provider: "fly", running: true, made: true } as const;
  const rec = (id: string, task: string, more: Record<string, unknown> = {}) => ({ id, repo: "demo", program: "claude",
    task, base: null, branch: `pp/${id}`, created_at: 1, finished: null, running: true, exit_code: null, ended_at: null,
    chat: null, activity: { state: "waiting" as const, kind: "permission_prompt", at: 1 }, ...more });

  it("has Stop and the line under it on a desktop, from the session's own activity and name", async () => {
    vi.useFakeTimers();
    localStorage.setItem("pp-open-token", "test-token.sig");
    try {
      browser();
      vi.stubGlobal("WebSocket", Socket);
      shellServer({ ws: RUNNING, repos: [{ name: "demo" }],
        sessions: [rec("aaaaaaaaaaaa", "Plain"), rec("bbbbbbbbbbbb", "On DeepSeek", { model_provider: "deepseek" })] });
      render(<App />);
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      fireEvent.click(within(screen.getByRole("complementary", { name: "Sessions" })).getByText("On DeepSeek"));
      await vi.waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy(), { timeout: 15000 });
      expect(within(screen.getByRole("toolbar", { name: "Keys" })).getByRole("status").textContent)
        .toBe("DeepSeek asks your permission in the terminal");
    } finally {
      vi.useRealTimers();
      localStorage.clear();
      useStore.setState(useStore.getInitialState());
    }
  }, 30000);
});
