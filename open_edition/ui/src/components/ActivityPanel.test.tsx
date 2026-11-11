import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SessionRecord } from "../api";
import { build } from "./ActivityPanel";

afterEach(cleanup);

const rec = (over: Partial<SessionRecord>): SessionRecord =>
  ({ id: "0123456789ab", program: "codex", repo: "demo", branch: "pp/0123456789ab", task: "Fix the parser", created_at: 100, running: true, ...over }) as SessionRecord;

const text = (items: ReturnType<typeof build>) => {
  const { container } = render(<ul>{items.map((i, n) => <li key={n}>{i.title} | {i.detail}</li>)}</ul>);
  return Array.from(container.querySelectorAll("li")).map((li) => li.textContent);
};

describe("a session's activity, with what its agent asked for", () => {
  it("lists each ask, with its task, and the person's answer, in order", () => {
    const asker = rec({});
    const started = rec({ id: "fedcba987654", program: "claude", task: "Write the tests", created_at: 300, asked_by: asker.id });
    const items = build(asker, [], [], [], [
      { at: 200, event: "asked", ask: "a1", program: "claude", task: "Write the tests", base: "main" },
      { at: 210, event: "asked", ask: "a2", program: "codex", task: "Refuse this", base: null },
      { at: 300, event: "started", ask: "a1", program: "claude", session: started.id },
      { at: 310, event: "refused", ask: "a2", program: "codex", session: null },
    ], [asker, started]);
    expect(text(items)).toEqual([
      "Started codex in demo on pp/0123456789ab | Fix the parser",
      "Its agent asked for a new claude session from main | Write the tests",
      "Its agent asked for a new codex session | Refuse this",
      "You started the claude session it asked for: “Write the tests” | ",
      "You refused the codex session it asked for | ",
    ]);
    expect(items.map((i) => i.tone)).toEqual(["plain", "plain", "plain", "good", "bad"]);
    // the asks are the session's own record; the answers, the app's record of what the person did
    expect(items.map((i) => i.source)).toEqual(["workspace", "the session's record", "the session's record",
      "you · the app's record", "you · the app's record"]);
  });

  it("says, of a session started from an ask, which session asked", () => {
    const asker = rec({ title: "Parser work" });
    const items = build(rec({ id: "fedcba987654", asked_by: asker.id }), [], [], [], [], [asker]);
    expect(text(items)[0]).toBe("Started codex in demo on pp/0123456789ab, by you, as the agent of “Parser work” asked | Fix the parser");
    expect(text(build(rec({ asked_by: "gone00000000" }), [], [], []))[0]).toContain("the agent of “a session since removed” asked");
  });
});
