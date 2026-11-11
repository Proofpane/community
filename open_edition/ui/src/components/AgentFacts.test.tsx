import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Fact, Facts } from "../agents";
import { useStore } from "../store";
import { AgentFacts } from "./AgentFacts";
import { day } from "../when";       // the reader's own zone: the test machine's, wherever it runs

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const agent = { id: "a1a2a3a4a5a6", name: "Triage", retired: false, relations: { version: "functional" } } as unknown as Agent;
const fact = (over: Partial<Fact>): Fact => ({
  id: "f1f2f3f4f5f6", subject: "service api", relation: "version", object: "1.5.0", valid_at: 1790000000, invalid_at: null,
  recorded_at: 1790000100, expired_at: null, superseded_by: null, source: "the status page", basis: "observed", run_id: null,
  contradicts: null, retracted: null, ...over,
});
const facts: Facts = {
  current: [fact({}), fact({ id: "c1c2c3c4c5c6", object: "9.9.9", basis: "told", contradicts: "f1f2f3f4f5f6" }),
            fact({ id: "d1d2d3d4d5d6", relation: "maintainer", object: "Ana", basis: "told" })],
  contradictions: [], retired: [fact({ id: "e1e2e3e4e5e6", object: "1.4.2", invalid_at: 1790000000, expired_at: 1790000100, superseded_by: "f1f2f3f4f5f6" })],
};

describe("an agent's facts", () => {
  it("shows each current one with how it is known, the contradiction, the retired ones; retracts; sets a relation", async () => {
    const calls: { method: string; url: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify(String(url).endsWith("/facts") ? facts : {}), { status: 200 });
    }));
    const changed = vi.fn(async () => {});
    render(<AgentFacts agent={agent} onChanged={changed} />);
    const current = await screen.findByRole("list", { name: "Its current facts" });
    const rows = within(current).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows[0]).toContain(`service apiversion1.5.0observedsince ${day(1790000000)}`);
    expect(rows[1]).toContain("9.9.9told");
    expect(rows[1]).toContain("contradicts 1.5.0");
    expect(screen.getByText("1 retired")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Its retired facts" }).textContent).toContain(`1.4.2 (${day(1790000000)} to ${day(1790000000)}): superseded`);
    fireEvent.click(screen.getByRole("button", { name: "Retract service api version 9.9.9" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Why it is wrong" }), { target: { value: "a guess" } });
    fireEvent.click(screen.getByRole("button", { name: "Retract" }));
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")).toEqual({ method: "POST", url: "/api/ade/agents/a1a2a3a4a5a6/facts/c1c2c3c4c5c6/retract", body: { reason: "a guess" } });
    const maintainer = screen.getByRole("checkbox", { name: "maintainer: one at a time" }) as HTMLInputElement;
    expect((screen.getByRole("checkbox", { name: "version: one at a time" }) as HTMLInputElement).checked).toBe(true);
    await vi.waitFor(() => expect(maintainer.disabled).toBe(false));
    fireEvent.click(maintainer);
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ relations: { version: "functional", maintainer: "functional" } });
  });
});
