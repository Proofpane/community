import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionRecord } from "../api";
import { arrowBytes, DeskKeys, KeyBar, stateWords, terminalStateOf } from "./TerminalPane";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// WS-BUTTONS, part one (queue section 4.310, its design's sections 2 and 7): the phone's key row under a terminal on a
// desktop too, with Shift+Tab on the desktop's row only; Stop as one Esc, never ^C; and a line saying what a Claude
// Code session is doing, from the activity its hooks write today. Nothing here changes the workspace: every key is a
// byte typed, as the keyboard's.
const names = (bar: HTMLElement) => within(bar).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
type Activity = NonNullable<SessionRecord["activity"]>;
const act_ = (a: Partial<Activity>): Activity => ({ state: "working", at: 1, ...a });

describe("the keys under a terminal on a desktop", () => {
  it("are the phone's keys with Shift+Tab after Tab, then Attach, and for a Claude Code session Stop at the row's end",
    () => {
      render(<DeskKeys onKey={() => {}} onAttach={() => {}} agent={{ activity: null }} />);
      expect(names(screen.getByRole("toolbar", { name: "Keys" }))).toEqual(["Escape", "Tab", "Shift Tab", "Control C",
        "Up arrow", "Down arrow", "Left arrow", "Right arrow", "Return", "Attach a file", "Stop"]);
    });

  it("draw no Stop and no state line for a pane that is not a Claude Code session's, a shell's", () => {
    render(<DeskKeys onKey={() => {}} />);
    expect(names(screen.getByRole("toolbar", { name: "Keys" }))).toEqual(["Escape", "Tab", "Shift Tab", "Control C",
      "Up arrow", "Down arrow", "Left arrow", "Right arrow", "Return"]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("send each key's bytes, Shift+Tab as xterm.js sends it", () => {
    const sent = vi.fn();
    render(<DeskKeys onKey={sent} />);
    for (const name of ["Escape", "Tab", "Shift Tab", "Control C", "Return"]) press(name);
    expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b", "\t", "\x1b[Z", "\x03", "\r"]);
  });

  it("keep the focus in the terminal: a press's mousedown does nothing of its own", () => {
    render(<DeskKeys onKey={() => {}} onAttach={() => {}} agent={{ activity: null }} />);
    for (const b of within(screen.getByRole("toolbar", { name: "Keys" })).getAllByRole("button")) {
      expect(fireEvent.mouseDown(b), b.getAttribute("aria-label") || "").toBe(false);
    }
  });
});

describe("the phone's row", () => {
  it("is as it was: no Shift+Tab and no Stop, nine keys with Attach and eight without", () => {
    render(<KeyBar onKey={() => {}} onAttach={() => {}} />);
    const keys = names(screen.getByRole("toolbar", { name: "Keys" }));
    expect(keys).toEqual(["Escape", "Tab", "Control C", "Up arrow", "Down arrow", "Left arrow", "Right arrow", "Return",
      "Attach a file"]);
    cleanup();
    render(<KeyBar onKey={() => {}} />);
    expect(within(screen.getByRole("toolbar")).getAllByRole("button")).toHaveLength(8);
  });
});

describe("the arrows, by the program's cursor-key mode", () => {
  it("are ESC [ and their letter, or ESC O and it while the program has set application mode, as xterm.js sends them",
    () => {
      expect(["A", "B", "D", "C"].map((x) => arrowBytes(x, false))).toEqual(["\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C"]);
      expect(["A", "B", "D", "C"].map((x) => arrowBytes(x, true))).toEqual(["\x1bOA", "\x1bOB", "\x1bOD", "\x1bOC"]);
    });

  it("follow the mode at the press, on the desktop's row and on the phone's", () => {
    let application = false;
    const sent = vi.fn();
    const arrows = ["Up arrow", "Down arrow", "Left arrow", "Right arrow"];
    for (const bar of [
      <DeskKeys key="desk" onKey={sent} appCursor={() => application} />,
      <KeyBar key="phone" onKey={sent} appCursor={() => application} />,
    ]) {
      sent.mockClear();
      application = false;
      render(bar);
      arrows.forEach(press);
      application = true;
      arrows.forEach(press);
      expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C", "\x1bOA", "\x1bOB",
        "\x1bOD", "\x1bOC"]);
      cleanup();
    }
  });
});

describe("Stop", () => {
  it("sends one Esc and never ^C, and is not offered again for 2 s after a press", () => {
    vi.useFakeTimers();
    const sent = vi.fn();
    render(<DeskKeys onKey={sent} agent={{ activity: act_({ state: "working" }) }} />);
    const stop = screen.getByRole("button", { name: "Stop" });
    fireEvent.click(stop);
    expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b"]);
    expect(stop.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(stop);
    expect(sent).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1999));
    expect(stop.getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(1));
    expect(stop.getAttribute("aria-disabled")).toBeNull();
    fireEvent.click(stop);
    expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b", "\x1b"]);
  });

  it("keeps the focus in the terminal when the held Stop or the line is pressed, and a held Stop sends nothing", () => {
    vi.useFakeTimers();
    const sent = vi.fn();
    render(<DeskKeys onKey={sent} agent={{ activity: act_({ state: "working" }) }} />);
    const stop = screen.getByRole("button", { name: "Stop" });
    fireEvent.click(stop);
    expect(stop.getAttribute("aria-disabled")).toBe("true");
    expect(fireEvent.mouseDown(stop)).toBe(false);                 // its default prevented: the focus stays
    expect(fireEvent.mouseDown(screen.getByRole("status"))).toBe(false);
    fireEvent.click(stop);
    expect(sent).toHaveBeenCalledTimes(1);
  });

  it("is held after an Esc only: Return, Tab, Shift+Tab, ^C and an arrow leave it offered", () => {
    vi.useFakeTimers();
    render(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "working" }) }} />);
    for (const name of ["Return", "Tab", "Shift Tab", "Control C", "Up arrow"]) {
      press(name);
      expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled"), name).toBeNull();
    }
  });

  it("is not offered for 2 s after the row's own Esc either, since two Esc in a row clear the draft", () => {
    vi.useFakeTimers();
    render(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "working" }) }} />);
    press("Escape");
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("is held 2 s from the last Esc the row sent, each one starting the 2 s again", () => {
    vi.useFakeTimers();
    render(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "working" }) }} />);
    press("Escape");
    act(() => vi.advanceTimersByTime(1500));
    press("Escape");
    act(() => vi.advanceTimersByTime(1999));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  // an activity's time is in seconds; the page's clock here is 100.5 s after the epoch, so 100 is before the press
  const at100 = () => { vi.useFakeTimers(); vi.setSystemTime(100_500); };

  it("says 'Esc sent', not stopped, until an activity written after the press reaches the page", () => {
    at100();
    const working = act_({ state: "working", at: 100 });
    const { rerender } = render(<DeskKeys onKey={() => {}} agent={{ activity: working }} />);
    expect(screen.getByRole("status").textContent).toContain("Claude is working");
    press("Stop");
    expect(screen.getByRole("status").textContent).toContain("Esc sent");
    expect(screen.getByRole("status").textContent).not.toMatch(/stopped/i);
    rerender(<DeskKeys onKey={() => {}} agent={{ activity: { ...working } }} />);
    expect(screen.getByRole("status").textContent).toContain("Esc sent");
    rerender(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "idle", at: 104 }) }} />);
    expect(screen.getByRole("status").textContent).toContain("Claude waits for your prompt");
  });

  it("keeps 'Esc sent' through a read that brings an activity written before the press, changed or not", () => {
    at100();
    const { rerender } = render(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "working", at: 96 }) }} />);
    press("Stop");
    // the page's copy was 4 s behind: the next read brings what the hook wrote at 100, before the Esc reached it
    rerender(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "working", tool: "Bash", at: 100 }) }} />);
    expect(screen.getByRole("status").textContent).toContain("Esc sent");
    rerender(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "idle", at: 100 }) }} />);
    expect(screen.getByRole("status").textContent).toContain("Esc sent");
    // a write after the press, its state the same as before it
    rerender(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "idle", at: 101 }) }} />);
    expect(screen.getByRole("status").textContent).toContain("Claude waits for your prompt");
  });

  it("keeps 'Esc sent' where the hooks have written nothing, and says it of no press before there was one", () => {
    at100();
    render(<DeskKeys onKey={() => {}} agent={{ activity: null }} />);
    expect(screen.getByRole("status").textContent).toBe("");
    press("Stop");
    expect(screen.getByRole("status").textContent).toBe("Esc sent");
  });

  it("says that nothing was sent where the terminal's connection was not open, and holds nothing back", () => {
    at100();
    const sent = vi.fn((_bytes: string) => false);
    render(<DeskKeys onKey={sent} agent={{ activity: act_({ state: "working", at: 96 }) }} />);
    press("Stop");
    expect(sent.mock.calls.map((c) => c[0])).toEqual(["\x1b"]);
    expect(screen.getByRole("status").textContent).toBe("Esc not sent: the terminal was not connected");
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
    press("Escape");
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
    // for 5 s, then the session's state again, which nothing changed
    act(() => vi.advanceTimersByTime(4999));
    expect(screen.getByRole("status").textContent).toBe("Esc not sent: the terminal was not connected");
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status").textContent).toBe("Claude is working");
  });

  it("keeps 'Esc sent' when the terminal is drawn again, as switching tabs and back does", () => {
    at100();
    const agent = { id: "drawn-again", activity: act_({ state: "working", at: 96 }) };
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    press("Stop");
    cleanup();
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    expect(screen.getByRole("status").textContent).toBe("Esc sent");
    cleanup();
    render(<DeskKeys onKey={() => {}} agent={{ ...agent, activity: act_({ state: "idle", at: 101 }) }} />);
    expect(screen.getByRole("status").textContent).toBe("Claude waits for your prompt");
  });

  it("keeps each session's press to itself: Stop under one leaves another's line its own", () => {
    at100();
    const activity = act_({ state: "working", at: 96 });
    render(<DeskKeys onKey={() => {}} agent={{ id: "two-a", activity }} />);
    press("Stop");
    cleanup();
    render(<DeskKeys onKey={() => {}} agent={{ id: "two-b", activity }} />);
    expect(screen.getByRole("status").textContent).toBe("Claude is working");
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("holds Stop for the rest of its 2 s when the terminal is drawn again, since two Esc clear the draft", () => {
    at100();
    const agent = { id: "hold-redraw", activity: act_({ state: "working", at: 96 }) };
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    press("Stop");
    act(() => vi.advanceTimersByTime(500));
    cleanup();
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(1499));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("holds Stop for the rest of the 2 s after the row's Esc key too, when the terminal is drawn again", () => {
    at100();
    const agent = { id: "esc-redraw", activity: act_({ state: "working", at: 96 }) };
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    press("Escape");
    cleanup();
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe("true");
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("offers Stop when the 2 s ran out between the first drawing and its effect, the clock read twice", () => {
    at100();
    const agent = { id: "race", activity: act_({ state: "working", at: 96 }) };
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    press("Stop");                                      // its Esc at 100.5 s
    cleanup();
    // the first drawing reads 10 ms of the 2 s left; by its effect, 20 ms later, they have run out
    vi.spyOn(Date, "now").mockReturnValueOnce(102_490).mockReturnValueOnce(102_510);
    render(<DeskKeys onKey={() => {}} agent={agent} />);
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBeNull();
  });

  it("ends 'Esc not sent' before its 5 s on an activity written after the press", () => {
    at100();
    const working = { activity: act_({ state: "working", at: 96 }) };
    const { rerender } = render(<DeskKeys onKey={() => false} agent={working} />);
    press("Stop");
    expect(screen.getByRole("status").textContent).toBe("Esc not sent: the terminal was not connected");
    rerender(<DeskKeys onKey={() => false} agent={{ activity: act_({ state: "idle", at: 101 }) }} />);
    expect(screen.getByRole("status").textContent).toBe("Claude waits for your prompt");
  });

  it("after a press that sent nothing, says again what it said before it, an earlier Esc's 'Esc sent' among it", () => {
    at100();
    const agent = { id: "before-unsent", activity: act_({ state: "working", at: 96 }) };
    const { rerender } = render(<DeskKeys onKey={() => true} agent={agent} />);
    press("Stop");
    expect(screen.getByRole("status").textContent).toBe("Esc sent");
    act(() => vi.advanceTimersByTime(2000));
    rerender(<DeskKeys onKey={() => false} agent={agent} />);
    press("Stop");
    expect(screen.getByRole("status").textContent).toBe("Esc not sent: the terminal was not connected");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByRole("status").textContent).toBe("Esc sent");
  });
});

describe("the state line, from the session's activity", () => {
  it.each([
    [{ state: "working" }, "Claude is working"],
    [{ state: "working", tool: "Bash" }, "Claude is working: Bash"],
    [{ state: "working", tool: "rm -rf /" }, "Claude is working"],
    [{ state: "waiting", kind: "permission_prompt" }, "Claude asks your permission in the terminal"],
    [{ state: "waiting", kind: "elicitation_dialog" }, "Claude waits for your answer in the terminal"],
    [{ state: "waiting", kind: "elicitation_url_dialog" }, "Claude waits for your answer in the terminal"],
    [{ state: "waiting", kind: "agent_needs_input" }, "Claude waits for your answer in the terminal"],
    [{ state: "waiting", kind: "quota_auto_resume_stale" }, "Claude waits for your answer in the terminal"],
    [{ state: "waiting", kind: "idle_prompt" }, "Claude waits for your prompt"],
    [{ state: "waiting" }, "Claude waits for you in the terminal"],
    [{ state: "idle" }, "Claude waits for your prompt"],
  ] as [Partial<Activity>, string][])("%o reads %s", (a, words) => {
    expect(stateWords(act_(a))).toBe(words);
  });

  it("says nothing it cannot tell: no activity yet, or a notification that waits on no one", () => {
    expect(stateWords(null)).toBeUndefined();
    expect(stateWords(undefined)).toBeUndefined();
    for (const kind of ["auth_success", "elicitation_complete", "elicitation_response", "agent_completed",
      "quota_auto_resume_fired", "plugin_notification"]) {
      expect(stateWords(act_({ state: "waiting", kind })), kind).toBeUndefined();
    }
  });

  it("never shows the program's own words, which can carry a command, whatever the state", () => {
    const message = "Allow curl -H 'token: x'";
    // every path through the words: each state, each kind's branch, a tool named or not
    for (const a of [{ state: "waiting", kind: "permission_prompt" }, { state: "waiting" }, { state: "working" },
      { state: "working", tool: "Bash" }, { state: "idle" }, { state: "waiting", kind: "elicitation_dialog" },
      { state: "waiting", kind: "idle_prompt" }, { state: "waiting", kind: "auth_success" }] as Partial<Activity>[]) {
      expect(stateWords(act_({ ...a, message })) || "", JSON.stringify(a)).not.toMatch(/curl|token/);
    }
  });

  it("names the session as the page does: another provider's model that runs in Claude Code by its own name", () => {
    expect(stateWords(act_({ state: "working", tool: "Bash" }), "DeepSeek")).toBe("DeepSeek is working: Bash");
    expect(stateWords(act_({ state: "waiting", kind: "permission_prompt" }), "Kimi K3"))
      .toBe("Kimi K3 asks your permission in the terminal");
    render(<DeskKeys onKey={() => {}} agent={{ activity: act_({ state: "idle" }), who: "MiniMax M3" }} />);
    expect(screen.getByRole("status").textContent).toBe("MiniMax M3 waits for your prompt");
    expect(screen.getByRole("button", { name: "Stop" }).getAttribute("title"))
      .toMatch(/stops what MiniMax M3 is doing/);
  });
});

describe("which panes get Stop and the line", () => {
  const rec = (program: string, more: Record<string, unknown> = {}) =>
    ({ id: "s1", program, running: true, activity: { state: "working", at: 1 }, ...more }) as unknown as SessionRecord;

  it("a running Claude Code session's, with its activity; not a shell's, a chat's, Codex's or an ended one's", () => {
    expect(terminalStateOf(rec("claude"))).toEqual({ id: "s1", activity: { state: "working", at: 1 }, who: "Claude" });
    for (const p of ["shell", "chat", "codex", "custom"]) expect(terminalStateOf(rec(p)), p).toBeUndefined();
    expect(terminalStateOf(rec("claude", { running: false }))).toBeUndefined();
    expect(terminalStateOf(rec("claude", { activity: undefined })))
      .toEqual({ id: "s1", activity: null, who: "Claude" });
  });

  it("names a session on another provider's model, which runs in Claude Code, as the page names it", () => {
    expect(terminalStateOf(rec("claude", { model_provider: "deepseek" }))?.who).toBe("DeepSeek");
    expect(terminalStateOf(rec("claude", { model_provider: "kimi" }))?.who).toBe("Kimi K3");
    expect(terminalStateOf(rec("claude", { model_provider: "minimax" }))?.who).toBe("MiniMax M3");
    expect(terminalStateOf(rec("claude", { model_provider: "other" }))?.who).toBe("Claude");
  });
});
