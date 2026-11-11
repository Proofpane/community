import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Suite } from "../agents";
import { useStore } from "../store";
import { AgentSuites } from "./AgentSuites";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const agent = { id: "a1a2a3a4a5a6", name: "Triage", retired: false, paused: false } as unknown as Agent;
const suite: Suite = {
  id: "s1s2s3s4s5s6", agent_id: "a1a2a3a4a5a6", name: "Where", created_at: 1, updated_at: 1,
  items: [{ id: "i1", question: "Where are labels set?", sources: ["src/rules.py"], group: "rag", held_out: false },
          { id: "i2", question: "Kept back?", sources: ["src/h.py"], group: "stale", held_out: true }],
  evals: [{ id: "e1", suite_id: "s1s2s3s4s5s6", agent_id: "a1a2a3a4a5a6", started_at: Math.floor(Date.now() / 1000) - 60,
            brief_version: 1, held_out: false, items: 5, scored: 4, open: 1, failed: 0, hit_at_5: 0.75, mrr: 0.5833,
            by_group: { rag: { items: 3, hit_at_5: 1, mrr: 0.7778 }, stale: { items: 1, hit_at_5: 0, mrr: 0 } }, results: [] }],
};

const serve = (suites: Suite[], calls: { method: string; url: string; body?: unknown }[] = []) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(init.method && init.method !== "GET" ? suites[0] ?? {} : suites), { status: 200 });
  }));
  return calls;
};

describe("an agent's suites", () => {
  it("makes a suite of questions with the sources that answer each, one a line", async () => {
    const calls = serve([]);
    render(<AgentSuites agent={agent} />);
    const group = screen.getByRole("group", { name: "Triage's suites" });
    expect(await within(group).findByText("None yet.")).toBeTruthy();
    fireEvent.click(within(group).getByRole("button", { name: /Suite/ }));
    const form = within(group).getByRole("form", { name: "A new suite" });
    fireEvent.change(within(form).getByRole("textbox", { name: "The suite's name" }), { target: { value: "Where" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Question 1" }), { target: { value: "Where are labels set?" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /The sources that answer question 1/ }), { target: { value: "src/rules.py\n\n docs/labels.md " } });
    fireEvent.click(within(form).getByRole("button", { name: /Question/ }));
    fireEvent.change(within(form).getByRole("textbox", { name: "Question 2" }), { target: { value: "Kept back?" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /The sources that answer question 2/ }), { target: { value: "src/h.py" } });
    fireEvent.change(within(form).getAllByRole("combobox")[1], { target: { value: "stale" } });
    fireEvent.click(within(form).getAllByRole("checkbox")[1]);
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ url: "/api/ade/agents/a1a2a3a4a5a6/suites", body: { name: "Where", items: [
      { question: "Where are labels set?", sources: ["src/rules.py", "docs/labels.md"], group: "rag", held_out: false },
      { question: "Kept back?", sources: ["src/h.py"], group: "stale", held_out: true }] } });
  });

  it("asks a stale question's current answer and the values it should no longer give, and shows its score", async () => {
    const stale: Suite = { ...suite, evals: [{ ...suite.evals[0], hit_at_5: null, mrr: null, stale_accuracy: 0.5, by_group: { stale: { items: 2, hit_at_5: 0.5, mrr: null } } }] };
    const calls = serve([stale]);
    render(<AgentSuites agent={agent} />);
    expect((await screen.findByLabelText("Where's last eval")).textContent).toContain("stale 0.50");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit the suite" });
    fireEvent.change(within(form).getAllByRole("combobox")[0], { target: { value: "stale" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "The current answer to question 1" }), { target: { value: "1.5.0" } });
    fireEvent.change(within(form).getByRole("textbox", { name: /The superseded values for question 1/ }), { target: { value: "1.4.2, 1.4.1" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect((calls.find((c) => c.method === "PUT")!.body as { items: unknown[] }).items[0]).toMatchObject(
      { question: "Where are labels set?", group: "stale", answer: "1.5.0", superseded: ["1.4.2", "1.4.1"] });
  });

  it("runs an eval, its held-out questions apart, and shows the last one's scores", async () => {
    const calls = serve([suite]);
    render(<AgentSuites agent={agent} />);
    const last = await screen.findByLabelText("Where's last eval");
    expect(last.textContent).toContain("hit@5 0.75, MRR 0.58");
    expect(last.textContent).toContain("(4 of 5 scored, 1 to go)");
    expect(last.textContent).toContain("rag 1.00, stale 0.00");
    expect(screen.getByText("2 questions, 1 held out")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Run Where" }));
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(1));
    const run = screen.getByRole("button", { name: "Run Where" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(run.disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Held out" }));
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(2));
    expect(calls.filter((c) => c.method === "POST").map((c) => [c.url, c.body])).toEqual([
      ["/api/ade/agents/suites/s1s2s3s4s5s6/evals", { held_out: false }], ["/api/ade/agents/suites/s1s2s3s4s5s6/evals", { held_out: true }]]);
  });
});
