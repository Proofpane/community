import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type SessionRecord } from "./api";
import { SessionActions, Stopped } from "./App";
import { useStore } from "./store";

// A Claude Code session an agent asked for, started again: when the installed Claude Code no longer
// offers the effort it was started with, the workspace starts it at its default and the start's answer says so
// (`effort_notice`, which the app passes back); each of the page's two ways to start a session again says it.

afterEach(() => { cleanup(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it",
     created_at: 1, running: false, finished: null, exit_code: 0, ended_at: null, chat: null, ...over }) as SessionRecord;
const SAID = "Claude Code no longer offers effort max: the session started at its default.";

describe("a session started again", () => {
  it("from its header's button, says an effort the start did not give", async () => {
    const started = vi.spyOn(Api, "startSession").mockResolvedValue(rec({ running: true, effort_notice: SAID }));
    useStore.setState({ refresh: vi.fn(async () => {}) });
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByTitle("Start the program again"));
    await vi.waitFor(() => expect(useStore.getState().notice?.text).toBe(SAID));
    expect(useStore.getState().notice?.tone).toBe("wait");
    expect(started).toHaveBeenCalledWith("0123456789ab", 120, 32);
  });

  it("from the stopped session's own button, says it too", async () => {
    vi.spyOn(Api, "startSession").mockResolvedValue(rec({ running: true, effort_notice: SAID }));
    useStore.setState({ refresh: vi.fn(async () => {}) });
    render(<Stopped rec={rec()} />);
    fireEvent.click(screen.getByText("Start it again"));
    await vi.waitFor(() => expect(useStore.getState().notice?.text).toBe(SAID));
  });

  it("says a start's words for 15 seconds, as a wait, from either button", async () => {
    // the words stay 15 seconds, as a start's notice of an effort not taken does
    vi.spyOn(Api, "startSession").mockResolvedValue(rec({ running: true, effort_notice: SAID }));
    const say = vi.fn();
    useStore.setState({ refresh: vi.fn(async () => {}), say });
    render(<Stopped rec={rec()} />);
    fireEvent.click(screen.getByText("Start it again"));
    await vi.waitFor(() => expect(say).toHaveBeenCalledWith(SAID, "wait", 15000));
    cleanup();
    say.mockClear();
    render(<SessionActions rec={rec()} grouped={false} onShip={() => {}} />);
    fireEvent.click(screen.getByTitle("Start the program again"));
    await vi.waitFor(() => expect(say).toHaveBeenCalledWith(SAID, "wait", 15000));
  });

  it("says nothing when the start says nothing of an effort", async () => {
    vi.spyOn(Api, "startSession").mockResolvedValue(rec({ running: true }));
    const refresh = vi.fn(async () => {});
    useStore.setState({ refresh });
    render(<Stopped rec={rec()} />);
    fireEvent.click(screen.getByText("Start it again"));
    await vi.waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(useStore.getState().notice).toBeNull();
  });
});
