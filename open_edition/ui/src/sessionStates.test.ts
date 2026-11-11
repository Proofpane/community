import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { sessionState } from "./store";
import type { SessionRecord } from "./api";
import type { SessionState } from "./types";

// The progress board's server reads a session's state from its record as this page's store.ts sessionState does
// (ade/progress_board.py's session_state, a field's truth read as JavaScript reads it), so that the board and the list
// of sessions agree. Both are held to one set of cases, tests/fixtures/session-states.json, among them records where
// JavaScript's truth and Python's part (an empty object, an empty list, false): tests/test_ade_progress_board.py holds
// the server's port to it, and this file holds the page's own function to it.
describe("a session's state on the board", () => {
  it("is store.ts's sessionState for every case the server's port is held to (tests/fixtures/session-states.json)", () => {
    const cases: { name: string; record: Partial<SessionRecord>; state: SessionState }[] =
      JSON.parse(readFileSync(join(__dirname, "..", "..", "tests", "fixtures", "session-states.json"), "utf8"));
    expect(cases.length).toBeGreaterThanOrEqual(26);
    for (const c of cases) expect([c.name, sessionState(c.record as SessionRecord)]).toEqual([c.name, c.state]);
  });
});
