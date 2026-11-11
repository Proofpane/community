import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewSession } from "./NewSession";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("an agent of the person's own, in New session", () => {
  it("is offered after the known agents, by its name, and goes by its id, on its own or beside others", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    const onStart = vi.fn();
    render(<NewSession repos={["demo"]} onStart={onStart} onClose={() => {}} onClone={() => {}}
      agents={[{ kind: "claude", available: true }, { kind: "shell", available: true }]}
      customAgents={[{ id: "0123456789ab", name: "Aider", command: "aider --model sonnet", task: "argument" }]} />);
    const own = screen.getByTitle(/Aider, your own program: aider --model sonnet/);
    expect(own.textContent).toContain("Aider");
    fireEvent.click(own);
    fireEvent.change(screen.getByPlaceholderText(/What should the agent do/), { target: { value: "Fix the bug" } });
    expect(screen.getByRole("button", { name: /^Start 2 sessions$/ })).toBeTruthy();       // Claude Code and Aider
    fireEvent.click(screen.getByRole("button", { name: /^Start 2 sessions$/ }));
    await vi.waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0][0]).toMatchObject({ agents: ["claude"], customs: ["0123456789ab"], task: "Fix the bug" });
  });

  it("alone needs a task, as an agent does", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ set: false }), { status: 200 })));
    render(<NewSession repos={["demo"]} onStart={() => {}} onClose={() => {}} onClone={() => {}}
      agents={[{ kind: "claude", available: false }, { kind: "shell", available: true }]}
      customAgents={[{ id: "0123456789ab", name: "Aider", command: "aider", task: "typed" }]} />);
    fireEvent.click(screen.getByTitle("A shell in the worktree"));         // the first choice, a shell, taken off
    fireEvent.click(screen.getByTitle(/Aider, your own program/));
    expect((screen.getByRole("button", { name: /^Start$/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/What should the agent do/), { target: { value: "Go" } });
    expect((screen.getByRole("button", { name: /^Start$/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});
