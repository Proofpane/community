import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cloudProblem, NewSession } from "./NewSession";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const open = (onStart = vi.fn(), agents = [{ kind: "claude" as const, available: true }, { kind: "codex" as const, available: true }]) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
  render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}} agents={agents} />);
  return onStart;
};

describe("continuing a cloud session from claude.ai", () => {
  it("starts one claude session with teleport and the cloud session named, from the repository's head", async () => {
    const onStart = open();
    fireEvent.click(screen.getByText("Codex").closest("button")!);           // another agent chosen: not sent
    fireEvent.click(screen.getByRole("button", { name: /From claude.ai/ }));
    expect(screen.getByText(/Claude Code's own teleport, in a fresh worktree of demo/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("A cloud session's id or claude.ai/code link"),
      { target: { value: " https://claude.ai/code/session_abc123?from=app " } });
    fireEvent.click(screen.getByRole("button", { name: /Continue from claude.ai/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ repo: "demo", agents: ["claude"], customs: [],
      teleport: "https://claude.ai/code/session_abc123?from=app", task: "Continued from claude.ai" });
    expect(onStart.mock.calls[0][0].base).toBeUndefined();
    expect(onStart.mock.calls[0][0].pull).toBeUndefined();
  });

  it("left blank, sends none, for Claude Code's own picker of the account's cloud sessions", async () => {
    const onStart = open();
    fireEvent.click(screen.getByRole("button", { name: /From claude.ai/ }));
    fireEvent.click(screen.getByRole("button", { name: /Continue from claude.ai/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0].teleport).toBe("");
  });

  it("says what is wrong with what it is given before anything starts, and needs Claude Code here", async () => {
    const onStart = open();
    fireEvent.click(screen.getByRole("button", { name: /From claude.ai/ }));
    fireEvent.change(screen.getByLabelText("A cloud session's id or claude.ai/code link"), { target: { value: "not an id" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue from claude.ai/ }));
    expect(await screen.findByText(/A cloud session is named by its id/)).toBeTruthy();
    expect(onStart).not.toHaveBeenCalled();
    cleanup();
    open(vi.fn(), [{ kind: "claude", available: false }]);
    fireEvent.click(screen.getByRole("button", { name: /From claude.ai/ }));
    expect((screen.getByRole("button", { name: /Continue from claude.ai/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("takes an id or a claude.ai/code link, as the workspace does", () => {
    for (const ok of ["", "session_abc", "cse_A-b_1", "https://claude.ai/code/session_abc", "https://claude.ai/code/session_abc/?x=1#y"]) {
      expect(cloudProblem(ok), ok).toBeNull();
    }
    for (const bad of ["abc", "session_", "session_a b", "--teleport", "https://claude.ai/code/session_a/../x", "http://claude.ai/code/session_a",
      "https://evil.example/code/session_a"]) {
      expect(cloudProblem(bad), bad).not.toBeNull();
    }
  });
});
