import { describe, expect, it } from "vitest";
import { accountWords, BOARD_EVERY, boardDelay, keepRows, parts, rowKey, SESSION_WORDS, stateColor, stateWords, TASK_WORDS, type BoardRow, type BoardView } from "./board";
import { OFFLINE_MOST } from "./shellPoll";
import { STATE_WORDS } from "./components/AgentTask";
import { stateLabel } from "./components/bits";
import type { SessionState } from "./types";

const boardRow = (over: Partial<BoardRow> = {}): BoardRow => ({
  kind: "task", id: "t1", title: "Ship the parser", parent: null, phase: "now", state: "running", order: null,
  progress: { done: 2, of: 5 }, account: { kind: "claude", id: "default" }, session: null, created_at: 1_760_700_000,
  started_at: 1_760_700_100, ended_at: null, waits_until: null, events: [], more_events: 0, ...over,
});

describe("the board's parts", () => {
  it("are History and Now as the server sends them, and Next in its order", () => {
    const rows = [
      boardRow({ id: "h1", phase: "history", state: "done" }), boardRow({ id: "n1" }),
      boardRow({ id: "x3", phase: "next", state: "ready", order: 3 }), boardRow({ id: "h2", phase: "history", state: "stopped" }),
      boardRow({ id: "x1", phase: "next", state: "ready", order: 1 }), boardRow({ id: "n2" }),
      boardRow({ id: "x2", phase: "next", state: "ready", order: 2 }),
    ];
    const got = parts(rows);
    expect(got.history.map((r) => r.id)).toEqual(["h1", "h2"]);
    expect(got.now.map((r) => r.id)).toEqual(["n1", "n2"]);
    expect(got.next.map((r) => r.id)).toEqual(["x1", "x2", "x3"]);
  });
});

describe("a read of the board", () => {
  const board = (rows: BoardRow[], at = 100): BoardView => ({ at, rows });
  const copy = (b: BoardView): BoardView => JSON.parse(JSON.stringify(b));

  it("that changes nothing keeps the board the page holds, every row where it was", () => {
    const held = board([boardRow({ id: "a" }), boardRow({ id: "b" })]);
    expect(keepRows(held, copy(held))).toBe(held);
    const later = keepRows(held, { ...copy(held), at: 103 });
    expect(later.at).toBe(103);
    expect(later.rows).toBe(held.rows);                        // the same rows, the same order: nothing moves
  });

  it("that changes a row takes the read's order, each row it did not change the very one the page held", () => {
    const a = boardRow({ id: "a" }), b = boardRow({ id: "b" }), c = boardRow({ id: "c" });
    const held = board([a, b, c]);
    const read = copy(board([b, a, c], 103));
    read.rows[0].events = [{ at: 102, kind: "task.switched", words: "Relay turned on" }];
    const got = keepRows(held, read);
    expect(got.rows.map(rowKey)).toEqual(["task:b", "task:a", "task:c"]);
    expect(got.rows[0]).not.toBe(b);
    expect(got.rows[1]).toBe(a);
    expect(got.rows[2]).toBe(c);
  });

  it("with a row more or a row fewer is the read's", () => {
    const held = board([boardRow({ id: "a" })]);
    expect(keepRows(held, board([boardRow({ id: "a" }), boardRow({ id: "b" })])).rows.map(rowKey)).toEqual(["task:a", "task:b"]);
    expect(keepRows(held, board([])).rows).toEqual([]);
    expect(keepRows(null, held)).toBe(held);
  });
});

describe("a row's words", () => {
  it("say a task's state as the agents' panel says it, and a session's as the list says it", () => {
    expect(TASK_WORDS).toEqual(STATE_WORDS);
    const states: SessionState[] = ["starting", "working", "running", "waiting", "idle", "done", "failed", "stopped"];
    expect(Object.keys(SESSION_WORDS).sort()).toEqual([...states].sort());
    for (const s of states) expect(SESSION_WORDS[s]).toBe(stateLabel(s));
    expect(stateWords({ kind: "task", state: "needs_owner" })).toBe("Needs you");
    expect(stateWords({ kind: "session", state: "waiting" })).toBe("Needs you");
    expect(stateWords({ kind: "requirement", state: "waiting_for_reset" })).toBe("Waiting for reset");
  });

  it("name the account as the list of sessions does: its program, then its name", () => {
    const accounts = {
      claude: { active: "default", accounts: [{ id: "a".repeat(12), name: "Work", created_at: 1, sessions: 0, status: null, usage: null }] },
      codex: { active: "default", accounts: [] },
    };
    expect(accountWords({ kind: "claude", id: "a".repeat(12) }, accounts)).toBe("Claude · Work");
    expect(accountWords({ kind: "claude", id: "default" }, accounts)).toBe("Claude · Default");
    expect(accountWords({ kind: "codex", id: "b".repeat(12) }, accounts)).toBe("Codex · another account");
    expect(accountWords(null, accounts)).toBe("");
  });

  it("colour a row by what it needs: the person, running, done, failed, stopped or waiting", () => {
    expect(stateColor({ phase: "now", state: "needs_owner" })).toBe("var(--color-wait)");
    expect(stateColor({ phase: "now", state: "waiting" })).toBe("var(--color-wait)");
    // a sub-goal's own manager needing the person, or done, in now
    expect(stateColor({ phase: "now", state: "manager_needs_you" })).toBe("var(--color-wait)");
    expect(stateColor({ phase: "now", state: "manager_done" })).toBe("var(--color-done)");
    expect(stateWords({ kind: "requirement", state: "manager_needs_you" })).toBe("Manager needs you");
    expect(stateColor({ phase: "now", state: "running" })).toBe("var(--color-run)");
    expect(stateColor({ phase: "now", state: "suspended" })).toBe("var(--color-idle)");
    expect(stateColor({ phase: "history", state: "done" })).toBe("var(--color-done)");
    expect(stateColor({ phase: "history", state: "failed" })).toBe("var(--color-fail)");
    expect(stateColor({ phase: "history", state: "stopped" })).toBe("var(--color-faint)");
    expect(stateColor({ phase: "next", state: "ready" })).toBe("var(--color-idle)");
  });
});

describe("the board's pace", () => {
  it("is every 3 seconds, and after reads that failed in a row twice as long after each, up to the shell's most", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(boardDelay)).toEqual([BOARD_EVERY, 3000, 6000, 12000, 24000, OFFLINE_MOST, OFFLINE_MOST]);
    expect(OFFLINE_MOST).toBe(30000);
  });
});
