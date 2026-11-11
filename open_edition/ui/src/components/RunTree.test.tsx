import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContextLine, RunTree } from "./RunTree";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const TREE = {
  turns: [
    { at: 1, text: "Label the new issues", requests: 3, tokens: { input_tokens: 1000, output_tokens: 40 }, tools: [
      { id: "t1", name: "Read", target: "src/rules.py", ms: 1000, error: false },
      { id: "t2", name: "Task", target: "check the labels", ms: 60000, error: false, agent: {
        type: "general-purpose", description: "check", depth: 1, requests: 2, tokens: { input_tokens: 500 },
        tools: [{ id: "t3", name: "Grep", target: "label", ms: 2000, error: false }] } },
      { id: "t4", name: "Bash", target: "npm test", ms: 4000, error: true },
    ], more: 12 },
    { at: 2, text: "The owner says: go ahead.", requests: 1, tokens: { output_tokens: 5 }, tools: [] },
  ],
  requests: 4,
};

describe("a run's tree", () => {
  it("shows each message's calls, a sub-agent under the call that started it, and what was cut", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ tree: TREE, copy: true }), { status: 200 })));
    render(<RunTree runId="aaaaaaaaaaaa" />);
    const tree = await screen.findByLabelText("Its tree");
    expect(within(tree).getByText("Label the new issues")).toBeTruthy();
    expect(within(tree).getByText("src/rules.py")).toBeTruthy();
    expect(within(tree).getByText(/a sub-agent \(general-purpose\): check/)).toBeTruthy();
    expect(within(tree).getByText("Grep")).toBeTruthy();
    expect(within(tree).getByText("Bash").className).toContain("text-fail");
    expect(within(tree).getByText("and 12 more calls")).toBeTruthy();
    expect(within(tree).getByText("Kept at the run's end.")).toBeTruthy();
  });

  it("says why there is none", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "This run's tree is kept at its end, and its machine is stopped: start it to read it." }), { status: 409 })));
    render(<RunTree runId="aaaaaaaaaaaa" />);
    expect(await screen.findByText(/its machine is stopped/)).toBeTruthy();
  });
});

describe("a run's context", () => {
  it("shows its peak against the compaction point, and a fragmented run's losses", () => {
    render(<ContextLine context={{ model_window: 1000000, compaction_window: 300000, peak_tokens: 255000, peak_pct: 0.85,
      compactions: 3, fragmented: true, retention: 0.5, lost: ["docs/labels.md", "#7"] }} />);
    const line = screen.getByLabelText("Its context");
    expect(line.textContent).toContain("context peak 85% of 300k (the model's window 1.0M)");
    expect(line.textContent).toContain("3 compactions: fragmented, it finished on summaries of its own past");
    expect(line.textContent).toContain("kept 50% of its first message's names");
    expect(line.textContent).toContain("lost: docs/labels.md, #7");
  });
});
