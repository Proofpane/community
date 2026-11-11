import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Improvement, Improvements } from "../agents";
import { proposalOf, recordStateOf, type SkillProposal } from "../skills";
import { useStore } from "../store";
import { AgentImprovements, budgetWords, BusinessProposal, daysLeft, evidenceWords, HeldRecord } from "./AgentImprovements";
import { AgentSkills } from "./AgentSkills";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const NOW = Date.now() / 1000;
const proposal = (over: Partial<Improvement> = {}): Improvement => ({
  id: "p1p2p3p4p5p6", agent_id: "a1a2a3a4a5a6", review_run_id: "r1r2r3r4r5r6", kind: "change", target: "brief", name: null,
  current: "Label the issues. BAD", proposed: "Label the issues. GOOD", hypothesis: "Saying GOOD passes.",
  evidence: { runs: ["aaaaaaaaaaaa", "bbbbbbbbbbbb"], metric: "judge task_fit" }, edits: 1, state: "eligible", reason: null,
  result: { wins: 5, losses: 0, p: 0.03125, regressions: 0, cost_control: 0.01, cost_treatment: 0.011 },
  created_at: NOW - 86400, decided_at: NOW, approved_at: null, rolls_back: null, expires_at: NOW + 13 * 86400,
  diff: ["@@ -1 +1 @@", "-Label the issues. BAD", "+Label the issues. GOOD"], ...over,
});

const serve = (got: Improvements, calls: { method: string; url: string; body?: Record<string, unknown> }[] = []) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method || "GET", url: String(url), body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(String(url).endsWith("/improvements") ? got : proposal({ state: "approved" })), { status: 200 });
  }));
  return calls;
};
const budget = { sessions: 12, usd: 3.2, max_sessions: 40, max_usd: 20, spent: false };
const names = new Map([["a1a2a3a4a5a6", "Triage"]]);

describe("the improve loop's proposals", () => {
  it("shows a change for the owner with its diff, what its sandbox found and its cost, and makes it on a tap", async () => {
    const calls = serve({ proposals: [proposal(), proposal({ id: "q1q2q3q4q5q6", state: "pruned", reason: "it bundles 4 edits" })],
                          budget, review_prompt: { version: 1, prompt: "Review" } });
    const changed = vi.fn(async () => {});
    render(<AgentImprovements names={names} onChanged={changed} />);
    const card = await screen.findByLabelText("Triage: a change to its brief");
    expect(within(card).getByText("Saying GOOD passes.")).toBeTruthy();
    const lines = within(card).getByLabelText("The change").querySelectorAll("div");
    expect(Array.from(lines).map((l) => [l.textContent, l.className])).toEqual([
      ["@@ -1 +1 @@", "text-faint"], ["-Label the issues. BAD", "text-fail"], ["+Label the issues. GOOD", "text-done"]]);
    expect(card.textContent).toContain("5 passed with it where they failed without, 0 the other way (p 0.031)");
    expect(card.textContent).toContain("A run costs $0.0110 est. with it, $0.0100 without.");
    expect(card.textContent).toContain("Drawn from 2 runs, for judge task_fit");
    expect(screen.getByText("This week: 12 of 40 sessions, $3.20 of $20.00")).toBeTruthy();
    expect(screen.getByText("What became of the others (1)")).toBeTruthy();
    expect(screen.getByText("it bundles 4 edits")).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: /Make the change/ }));
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/ade/agents/improvements/p1p2p3p4p5p6/approve"))).toBe(true);
  });

  it("says no with why, offers a rollback and a prompt change in their own words, and shows nothing with no proposal", async () => {
    const calls = serve({ proposals: [proposal({ kind: "rollback", result: { after: 10, before: 19, p: 0.0017 } }),
                                      proposal({ id: "m1m2m3m4m5m6", kind: "meta", target: "review_prompt", agent_id: null, result: null })],
                          budget, review_prompt: { version: 1, prompt: "Review" } });
    render(<AgentImprovements names={names} onChanged={async () => {}} />);
    const undo = await screen.findByLabelText("Triage: undo its last change to its brief?");
    expect(undo.textContent).toContain("its first 20 graded runs passed 10 times; the 20 before it, 19 (p 0.002)");
    expect(within(undo).getByRole("button", { name: /Undo it/ })).toBeTruthy();
    const meta = screen.getByLabelText("A change to the review prompt");
    expect(meta.textContent).toContain("it is not tried in a sandbox");
    fireEvent.change(within(meta).getByLabelText("Why not"), { target: { value: "too vague" } });
    fireEvent.click(within(meta).getByRole("button", { name: /No/ }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const no = calls.find((c) => c.method === "POST")!;
    expect(no.url).toMatch(/\/improvements\/m1m2m3m4m5m6\/reject$/);
    expect(no.body).toEqual({ reason: "too vague" });
    cleanup();
    serve({ proposals: [], budget, review_prompt: { version: 1, prompt: "Review" } });
    const { container } = render(<AgentImprovements names={names} onChanged={async () => {}} />);
    await vi.waitFor(() => expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });          // its answer read
    expect(container.firstChild).toBeNull();
  });

  it("says why the app refused a tap and, the proposal refused for it, lists it among the others with the reason", async () => {
    // refused at the owner's tap for the agent's skills, the proposal is refused, not left
    // eligible; the panel reads it again after the refusal, so its card is no longer offered and says so
    const full = "it adds a skill to an agent holding 20; an agent holds at most 20";
    let refused = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).endsWith("/approve")) {
        refused = true;
        return new Response(JSON.stringify({ detail: "It adds a skill to an agent holding 20; an agent holds at most 20." }), { status: 409 });
      }
      const p = proposal({ target: "skill", name: "sorting", ...(refused ? { state: "refused", reason: full, expires_at: null, decided_at: null } : {}) });
      return new Response(JSON.stringify({ proposals: [p], budget, review_prompt: { version: 1, prompt: "" } }), { status: 200 });
    }));
    render(<AgentImprovements names={names} onChanged={async () => {}} />);
    const card = await screen.findByLabelText("Triage: a change to its skill sorting");
    fireEvent.click(within(card).getByRole("button", { name: /Make the change/ }));
    // the error line says the proposal is now refused, not only the limit
    await vi.waitFor(() => expect(screen.queryByText(`Refused: ${full}.`)).not.toBeNull());
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });          // read again, or not
    expect(screen.queryByLabelText("Triage: a change to its skill sorting")).toBeNull();
    expect(screen.queryByText("What became of the others (1)")).not.toBeNull();
    expect(screen.queryByText(full)).not.toBeNull();
  });

  it("says a tap the app refused for another reason as the app said it, the proposal still offered", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).endsWith("/approve")) return new Response(JSON.stringify({ detail: "Its agent is retired." }), { status: 409 });
      return new Response(JSON.stringify({ proposals: [proposal()], budget, review_prompt: { version: 1, prompt: "" } }), { status: 200 });
    }));
    render(<AgentImprovements names={names} onChanged={async () => {}} />);
    const card = await screen.findByLabelText("Triage: a change to its brief");
    fireEvent.click(within(card).getByRole("button", { name: /Make the change/ }));
    await vi.waitFor(() => expect(screen.queryByText("Its agent is retired.")).not.toBeNull());
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(screen.queryByLabelText("Triage: a change to its brief")).not.toBeNull();
    expect(screen.queryByText(/^Refused:/)).toBeNull();
  });

  it("words a proposal's days left and what its sandbox found", () => {
    expect(daysLeft(NOW + 13.5 * 86400, NOW)).toBe("expires in 14 days");
    expect(daysLeft(NOW + 3600, NOW)).toBe("expires within a day");
    expect(evidenceWords(proposal({ result: { wins: 6, losses: 1, p: 0.0625, regressions: 2, cost_control: null, cost_treatment: null } })))
      .toBe("On the runs it was drawn from, 6 passed with it where they failed without, 1 the other way (p 0.063). 2 of the runs that passed before failed with it.");
  });
});

describe("an agent's skills", () => {
  it("lists each with its SKILL.md and takes one away on a second tap", async () => {
    const calls = serve({ proposals: [], budget, review_prompt: { version: 1, prompt: "" } });
    const changed = vi.fn(async () => {});
    const agent = { id: "a1a2a3a4a5a6", name: "Triage", skills: [{ name: "labels", content: "---\ndescription: d\n---\nDo it." }] } as unknown as Agent;
    render(<AgentSkills agent={agent} onChanged={changed} />);
    const group = screen.getByRole("group", { name: "Triage's skills" });
    expect(within(group).getByText("pp-labels")).toBeTruthy();
    fireEvent.click(within(group).getByRole("button", { name: "Take away" }));
    fireEvent.click(within(group).getByRole("button", { name: "Take it away?" }));
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "DELETE")!.url).toMatch(/\/api\/ade\/agents\/a1a2a3a4a5a6\/skills\/labels$/);
    cleanup();
    const { container } = render(<AgentSkills agent={{ ...agent, skills: [] }} onChanged={changed} />);
    expect(container.firstChild).toBeNull();
  });
});

// one business proposal, as the Skills view and the loop's list draw
// it, and the record held.
const business = (over: Record<string, unknown> = {}): SkillProposal => proposalOf({
  id: "p1p2p3p4p5p6", repo: "alpha", folder: "deploy", path: ".claude/skills/deploy/SKILL.md", kind: "change",
  branch: "refs/heads/main", base: "a".repeat(40), head: "b".repeat(40), heads: [{ base: "c".repeat(40), head: "d".repeat(40), at: 1 }],
  current_version: null, before_version: null, fresh: true, hypothesis: "Name the target first.",
  counts: { whole: { calls: 12, failed: 3, typed: 0, tasks_done: 2, tasks_failed: 1, tasks_stopped: 0, tasks_open: 0,
    tasks_unknown: 0, tasks_checks_failed: 0 }, current: null, before: null },
  state: "open", reason: null, pr_url: null, found: null, rolls_back: null, ended: null, created_at: 1, decided_at: 2,
  merged_at: null, expires_at: NOW + 13.5 * 86400,
  notes: { keys: [], builds: [], newer: false, linked: true, url: null, critic: null },
  diff: ["@@ -1 +1 @@", "-Deploy it.", "+Name the target, then deploy it."], ways: ["merge", "remake", "reject"], bars_now: null, ...over,
}) as SkillProposal;

describe("a business skill's proposal", () => {
  it("shows its repository, folder and ids, those before it was brought up to date, its whole diff, counts and hypothesis", () => {
    render(<BusinessProposal p={business()} onDone={async () => {}} />);
    const card = screen.getByLabelText("alpha: a change to the skill deploy");
    expect(card.textContent).toContain(`.claude/skills/deploy/SKILL.md on refs/heads/main; base ${"a".repeat(12)}, head ${"b".repeat(12)}`);
    expect(card.textContent).toContain(`before it was brought up to date: ${"c".repeat(12)}..${"d".repeat(12)}`);
    const lines = within(card).getByLabelText("The change").querySelectorAll("div");
    expect(Array.from(lines).map((l) => [l.textContent, l.className])).toEqual([
      ["@@ -1 +1 @@", "text-faint"], ["-Deploy it.", "text-fail"], ["+Name the target, then deploy it.", "text-done"]]);
    expect(within(card).getByLabelText("Its counts").textContent).toContain("All its calls kept: 12 calls, 3 failed, 1 task failed");
    expect(within(card).getByLabelText("Its counts").textContent).toContain("At the version before: no such version");
    expect(card.textContent).toContain("Name the target first.");
    expect(within(card).getByLabelText("What to know about it").textContent).toContain("It was not tried");
    expect(card.textContent).toContain("expires in 14 days");
  });

  it("offers the taps its state allows, each its own words: merging into his copy names its branch, on GitHub it does not", () => {
    render(<BusinessProposal p={business()} onDone={async () => {}} />);
    const card = screen.getByLabelText("alpha: a change to the skill deploy");
    expect(within(card).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Merge into main", "Bring it up to date", "Reject"]);
    cleanup();
    render(<BusinessProposal p={business({ state: "pull_request", pr_url: "https://example.test/pr/1", ways: ["merge", "reject"] })}
      onDone={async () => {}} />);
    const pr = screen.getByLabelText("alpha: a change to the skill deploy");
    expect(within(pr).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Merge", "Reject"]);
    expect(pr.textContent).toContain("Its pull request: https://example.test/pr/1");
    cleanup();
    render(<BusinessProposal p={business({ state: "found", kind: "change", ways: ["take", "go_back"], ended: null })} onDone={async () => {}} />);
    expect(within(screen.getByLabelText("alpha: a change to the skill deploy")).getAllByRole("button").map((b) => b.textContent?.trim()))
      .toEqual(["Take it as mine", "Go back"]);
    cleanup();
    render(<BusinessProposal p={business({ kind: "back", ways: [] })} onDone={async () => {}} />);
    const back = screen.getByLabelText("alpha: go back to the version before to the skill deploy");
    expect(within(back).queryAllByRole("button")).toEqual([]);
  });

  it("says the workspace's words where a tap is refused, and calls nothing else", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push(`${init.method || "GET"} ${url}`);
      return new Response(JSON.stringify({ detail: "The proposal's branch moved: bring it up to date first." }), { status: 409 });
    }));
    const done = vi.fn(async () => {});
    render(<BusinessProposal p={business()} onDone={done} />);
    fireEvent.click(screen.getByRole("button", { name: /Merge into main/ }));
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toContain("The proposal's branch moved"));
    expect(calls.map((c) => c.replace(/^(\w+) .*(\/api\/.*)$/, "$1 $2"))).toEqual(["POST /api/ade/skill-improve/proposals/p1p2p3p4p5p6/merge"]);
    expect(done).not.toHaveBeenCalled();
  });

  it("words the week's budget by kind", () => {
    expect(budgetWords({ sessions: 3, usd: 1.5, max_sessions: 40, max_usd: 20, spent: false,
      by_kind: { review: 1, critic: 0, replay: 0, skill_review: 1, skill_critic: 1 } }))
      .toBe("This week: 3 of 40 sessions (1 reviews, 0 critics, 0 replays, 1 skill reviews, 1 skill critics), $1.50 of $20.00");
  });

  it("draws the held record only where it is held", () => {
    const held = recordStateOf({ running: true, copy: null, record: { state: "held", held: "its signature does not verify", word: true,
      generation: 3, error: null, refused: [] } });
    const { container } = render(<HeldRecord record={held} onDone={async () => {}} />);
    expect(screen.getByRole("alert", { name: "The record of skill proposals is held" }).textContent).toContain("(its signature does not verify)");
    // the page names the denial of service a session's program can cause
    expect(screen.getByRole("alert", { name: "The record of skill proposals is held" }).textContent)
      .toContain("A program a session ran can do this, since it runs as the workspace's own user.");
    expect(within(container).queryByRole("list")).toBeNull();                         // no start was refused
    cleanup();
    for (const state of ["record", "waiting"]) {
      const r = recordStateOf({ running: true, copy: null, record: { state, held: null, word: true, generation: 3, error: null, refused: [] } });
      const { container: c } = render(<HeldRecord record={r} onDone={async () => {}} />);
      expect(c.firstChild).toBeNull();
      cleanup();
    }
    const { container: none } = render(<HeldRecord record={recordStateOf({ running: false, record: null, copy: null })} onDone={async () => {}} />);
    expect(none.firstChild).toBeNull();
  });
});
