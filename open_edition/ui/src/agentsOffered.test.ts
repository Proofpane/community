import { describe, expect, it } from "vitest";
import type { AccountRow, AccountsView } from "./api";
import { agentsOffered, type Known } from "./agentsOffered";

const row = (id: string, signed_in: boolean | null): AccountRow =>
  ({ id, name: id, created_at: null, sessions: 0, usage: null, status: signed_in === null ? null : { installed: true, signed_in } });
const accounts = (claude: AccountRow[], codex: AccountRow[]): AccountsView =>
  ({ claude: { active: "default", accounts: claude }, codex: { active: "default", accounts: codex } });
const known = (over: Partial<Known> = {}): Known =>
  ({ programs: { claude: true, codex: true }, claude: { installed: true, signed_in: true }, accounts: null, modelProviders: null, ...over });
const of = (st: Known, kind: string) => agentsOffered(st).find((a) => a.kind === kind)!;

describe("what New session offers", () => {
  it("offers Codex until every account of it is known to be signed out", () => {
    expect(of(known(), "codex").available).toBe(true);
    expect(of(known({ accounts: accounts([], [row("default", null)]) }), "codex").available).toBe(true);
    expect(of(known({ accounts: accounts([], [row("default", false), row("work", true)]) }), "codex").available).toBe(true);
    expect(of(known({ accounts: accounts([], [row("default", false), row("work", null)]) }), "codex").available).toBe(true);
    // one whose sign-in the workspace could not read (its program slow to answer) is not taken for signed out
    const unread: AccountRow = { ...row("work", null), status: { installed: true, signed_in: null } };
    expect(of(known({ accounts: accounts([], [row("default", false), unread]) }), "codex").available).toBe(true);
    const out = of(known({ accounts: accounts([], [row("default", false), row("work", false)]) }), "codex");
    expect(out).toEqual({ kind: "codex", available: false, note: "Sign in to Codex first (Connections)" });
  });

  it("says Codex is not installed before it says it is signed out", () => {
    const st = known({ programs: { claude: true, codex: false }, accounts: accounts([], [row("default", false)]) });
    expect(of(st, "codex")).toEqual({ kind: "codex", available: false, note: "Not installed in this workspace" });
  });

  it("holds Claude Code and the chat back while the account new sessions start on is signed out and no other is in", () => {
    const signedOut = { installed: true, signed_in: false };
    for (const kind of ["claude", "chat"]) {
      expect(of(known({ claude: signedOut }), kind).note).toBe("Sign in to Claude first (Connections)");
      expect(of(known({ claude: signedOut, accounts: accounts([row("default", false), row("work", true)], []) }), kind).available).toBe(true);
    }
    expect(of(known({ claude: signedOut }), "codex").available).toBe(true);
    expect(of(known({ claude: { installed: true, signed_in: null } }), "claude").available).toBe(true);
  });
});
