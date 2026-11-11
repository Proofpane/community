import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentRun } from "../agents";
import { RunChecksLine } from "./RunChecks";

afterEach(cleanup);

const run = (over: Partial<AgentRun> = {}) => ({ run_id: "r1r2r3r4r5r6", state: "done", trigger: {}, ...over }) as unknown as AgentRun;
const scores = (v: number) => ({ task_fit: v, grounding: v, completeness: v, hygiene: v, actionable: v });

describe("a run's checks", () => {
  it("say why the judge gave no verdict, when it gave none", () => {
    render(<RunChecksLine run={run({ judge_error: "its reply was not a verdict" })} />);
    expect(screen.getByLabelText("Its checks").textContent).toBe("The judge gave no verdict: its reply was not a verdict");
  });

  it("say nothing when nothing was checked or found", () => {
    const { container } = render(<RunChecksLine run={run({ metrics: { claims: { flagged: false }, premise: { flagged: false } } })} />);
    expect(container.firstChild).toBeNull();
  });

  it("show the judge's verdict, its acceptance, the rules it met and its words' flags", () => {
    render(<RunChecksLine run={run({
      judge: { scores: { ...scores(0.8), grounding: 0.4 }, pass: false, score: 0.4, version: "a".repeat(16), why: "It claims what no call shows.", cost_est_usd: 0.01 },
      acceptance: { pass: false, failures: ["no key labels", "it contains 'TODO'", "no status"], retried: true },
      anomalies: [{ rule: "tool_repetition", level: "warning", at: 1, evidence: "Bash called 5 times" },
                  { rule: "tool_repetition", level: "critical", at: 2, evidence: "Bash called 8 times" },
                  { rule: "spend", level: "warning", at: 3, evidence: null }],
      metrics: { claims: { flagged: true, standing: [{ word: "validated", text: "The migration is validated." }] },
                 premise: { flagged: true } },
    })} />);
    const line = screen.getByLabelText("Its checks");
    const judge = screen.getByText("Judge: failed, 40% at its lowest");
    expect(judge.getAttribute("title")).toContain("claims backed: 40%");
    expect(judge.getAttribute("title")).toContain("It claims what no call shows.");
    expect(line.textContent).toContain("Its checks failed: no key labels; it contains 'TODO'…, twice");
    expect(screen.getByText(/Met a rule's critical level/).textContent).toBe(
      "Met a rule's critical level: the same call again and again, spending well past its usual run");
    expect(line.textContent).toContain('It claims "validated" with nothing behind it in the run');
    expect(line.textContent).toContain("Asked to assume a status and write it up");
  });

  it("show a pass on a second try, and warnings alone as warnings", () => {
    render(<RunChecksLine run={run({ acceptance: { pass: true, failures: [], retried: true },
                                     anomalies: [{ rule: "context_budget", level: "warning", at: 1, evidence: "81%" }],
                                     metrics: { claims: { flagged: false }, premise: { flagged: false } } })} />);
    expect(screen.getByText("Its checks passed, on its second try")).toBeTruthy();
    expect(screen.getByLabelText("Its checks").textContent).not.toMatch(/claims|assume/);
    expect(screen.getByText("Met a rule: near its compaction point").className).toContain("text-wait");
  });
});
