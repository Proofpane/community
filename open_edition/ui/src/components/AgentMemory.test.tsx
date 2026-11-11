import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "../agents";
import { useStore } from "../store";
import { AgentMemory } from "./AgentMemory";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const agent = { id: "a1a2a3a4a5a6", name: "Triage", retired: false, decay_runs: 30 } as unknown as Agent;
const now = Math.floor(Date.now() / 1000);

describe("an agent's memory", () => {
  it("lists its versions, shows what one changed, rolls back after a second tap, and brings an archived file back", async () => {
    const calls: { method: string; url: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
      const u = String(url);
      if (u.endsWith("/memory")) return new Response(JSON.stringify({
        versions: [{ version: 2, run_id: "r2", kind: "run", from_version: null, made_at: now - 60, files: 2, bytes: 40 },
                   { version: 1, run_id: "r1", kind: "run", from_version: null, made_at: now - 3600, files: 1, bytes: 10 }],
        now: { runs: 5, files: [{ path: "labels.md", bytes: 9, runs_ago: 0 }], archived: [{ path: "old.md", bytes: 3 }] } }), { status: 200 });
      if (u.endsWith("/memory/2")) return new Response(JSON.stringify({
        version: 2, run_id: "r2", kind: "run", from_version: null, made_at: now - 60, files: 2, bytes: 40, contents: {},
        changes: { added: ["notes.md"], removed: [], changed: [{ path: "labels.md", diff: "--- a/labels.md\n+++ b/labels.md\n-bug\n+bug, docs\n" }] } }), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));
    render(<AgentMemory agent={agent} />);
    expect(await screen.findByText("a file unused for 30 runs is archived")).toBeTruthy();
    await screen.findByRole("list", { name: "Its versions" });
    fireEvent.click(screen.getByRole("button", { name: "v2" }));
    const changes = await screen.findByLabelText("Version 2's changes");
    expect(changes.textContent).toContain("added: notes.md");
    expect(changes.textContent).toContain("+bug, docs");
    const back = screen.getByRole("button", { name: /Back to this/ });
    fireEvent.click(back);
    expect(calls.some((c) => c.method === "POST")).toBe(false);                   // one tap arms it
    fireEvent.click(screen.getByRole("button", { name: /Make the memory v1 again/ }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/memory/1/rollback"))).toBe(true));
    const bring = screen.getByRole("button", { name: "Bring back" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(bring.disabled).toBe(false));                // done with the rollback
    fireEvent.click(bring);
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/memory/unarchive"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/memory/unarchive"))!.body).toEqual({ path: "old.md" });
  });
});
