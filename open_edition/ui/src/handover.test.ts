import { describe, expect, it } from "vitest";
import type { AccountsView, SessionRecord } from "./api";
import { accountKind, continuable, handoverCases, startingAccount, type AccountGroup } from "./handover";

// What the page decides before the owner's press of Continue in a new session: whether it offers it, the
// account a program starts on, and which parts of risk point 17's text a choice brings.

const rec = (over: Partial<SessionRecord> = {}) => ({ program: "claude", repo: "demo", account: null, ...over }) as SessionRecord;
const accounts: AccountsView = {
  claude: { active: "aaaaaaaaaaaa", accounts: [] },
  codex: { active: "cccccccccccc", accounts: [] },
};
const PERSONAL: AccountGroup = { grp: "personal", grp_kind: "personal" };
const TEAM: AccountGroup = { grp: "org:acme", grp_kind: "team" };
const ENTERPRISE: AccountGroup = { grp: "org:big", grp_kind: "enterprise" };

describe("Continue in a new session, before the press", () => {
  it("is offered for a Claude Code, Codex or chat session with a repository, and for nothing else", () => {
    for (const program of ["claude", "codex", "chat"]) expect(continuable(rec({ program })), program).toBe(true);
    for (const program of ["shell", "gemini", "custom", "claude-sign-in"]) expect(continuable(rec({ program })), program).toBe(false);
    expect(continuable(rec({ repo: null }))).toBe(false);                     // an agent's run has no repository
  });

  it("starts on the session's own account where the program runs on the same kind, else on that kind's active one", () => {
    expect(accountKind("chat")).toBe("claude");                                // a chat runs Claude Code
    expect(accountKind("codex")).toBe("codex");
    expect(startingAccount(rec({ account: "bbbbbbbbbbbb" }), "claude", accounts)).toBe("bbbbbbbbbbbb");
    expect(startingAccount(rec({ account: "bbbbbbbbbbbb" }), "chat", accounts)).toBe("bbbbbbbbbbbb");
    expect(startingAccount(rec({ account: null }), "claude", accounts)).toBe("default");
    expect(startingAccount(rec({ account: "bbbbbbbbbbbb" }), "codex", accounts)).toBe("cccccccccccc");
    expect(startingAccount(rec({ program: "codex", account: "dddddddddddd" }), "claude", accounts)).toBe("aaaaaaaaaaaa");
    expect(startingAccount(rec({ account: "bbbbbbbbbbbb" }), "codex", null)).toBe("default");
  });

  it.each([
    // [from program, from account, to program, to account, groups, cases]
    ["claude", null, "claude", "default", {}, []],
    ["claude", "a", "claude", "a", { a: TEAM }, ["onto_seat"]],               // the same account: only the seat's own text
    ["claude", "a", "claude", "b", { a: PERSONAL, b: PERSONAL }, ["other_account"]],
    ["claude", "a", "claude", "b", { a: PERSONAL, b: TEAM }, ["other_account", "other_group", "onto_seat"]],
    ["claude", "a", "claude", "b", { a: TEAM, b: TEAM }, ["other_account", "onto_seat"]],
    ["claude", "a", "claude", "b", { a: TEAM, b: ENTERPRISE }, ["other_account", "other_group", "onto_seat"]],
    ["claude", "a", "claude", "b", { a: PERSONAL }, ["other_account", "other_group"]],      // b's group not known
    ["claude", "a", "claude", "b", {}, ["other_account", "other_group"]],
    ["chat", "a", "claude", "b", { a: PERSONAL, b: PERSONAL }, ["other_account"]],
    ["claude", "a", "chat", "default", { a: PERSONAL, default: PERSONAL }, ["other_account"]],
    ["claude", "a", "codex", "c", { a: PERSONAL }, ["codex"]],               // OpenAI's: none of Anthropic's parts
    ["codex", "c", "codex", "d", {}, ["codex"]],
    ["codex", "c", "claude", "b", { b: PERSONAL }, []],                      // no several Anthropic accounts in turn
    ["codex", "c", "claude", "b", { b: TEAM }, ["onto_seat"]],
  ] as [string, string | null, "claude" | "codex" | "chat", string, Record<string, AccountGroup>, string[]][])(
    "from %s on %s to %s on %s: its parts", (fromProgram, fromAccount, toProgram, toAccount, groups, cases) => {
      expect(handoverCases(rec({ program: fromProgram, account: fromAccount }), { program: toProgram, account: toAccount }, groups)).toEqual(cases);
    });
});
