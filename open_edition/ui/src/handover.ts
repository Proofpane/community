// Continue in a new session: what the page decides before the
// owner's press, kept out of the dialog's own module so that the page's first chunk can ask it without the dialog's
// code (mainChunk.test.ts).
import type { AccountsView, HandoverProgram, SessionRecord } from "./api";

/** The programs a session is continued in, in the order offered. */
export const HANDOVER_PROGRAMS: { id: HandoverProgram; label: string }[] = [
  { id: "claude", label: "Claude Code" },
  { id: "codex", label: "Codex" },
  { id: "chat", label: "Chat" },
];

/** Whether the page offers Continue in a new session for a session: a Claude Code, Codex or chat session with a
 *  repository (an agent's run has none). The app refuses the rest on its own (412), whatever the page offers. */
export function continuable(rec: Pick<SessionRecord, "program" | "repo">): boolean {
  return !!rec.repo && (rec.program === "claude" || rec.program === "codex" || rec.program === "chat");
}

/** The program's accounts: Codex's for Codex, Claude Code's for Claude Code and a chat, which runs Claude Code. */
export function accountKind(program: string): "claude" | "codex" {
  return program === "codex" ? "codex" : "claude";
}

/** The account a choice of program starts on before the owner picks one: the session's own where the new program runs
 *  on the same kind of account, else the one that program's new sessions start on. */
export function startingAccount(rec: Pick<SessionRecord, "program" | "account">, program: HandoverProgram, accounts: AccountsView | null): string {
  const kind = accountKind(program);
  if (accountKind(rec.program) === kind) return rec.account || "default";
  return accounts?.[kind].active || "default";
}

/** An account's group as the owner's accounts page last read it (the tasks' account rows): its plan's group, and
 *  whether it is a personal plan or a Team or Enterprise organisation's seat; none where the page has not read it. */
export interface AccountGroup { grp: string | null; grp_kind: "personal" | "team" | "enterprise" | null }

/** Which parts of risk point 17's text a choice brings: "other_account" for a Claude Code
 *  account other than the one the session ran on, from a Claude Code session or a chat; with it "other_group" where the
 *  two accounts' groups differ or either is not known; "onto_seat" onto a Team or Enterprise seat; "codex" on Codex.
 *  The handover's own words show always. */
export function handoverCases(from: Pick<SessionRecord, "program" | "account">, to: { program: HandoverProgram; account: string }, groups: Record<string, AccountGroup>): string[] {
  const cases: string[] = [];
  const fromAccount = from.account || "default";
  const toAccount = to.account || "default";
  const claudeFrom = accountKind(from.program) === "claude";
  const claudeTo = accountKind(to.program) === "claude";
  if (claudeFrom && claudeTo && fromAccount !== toAccount) {
    cases.push("other_account");
    const a = groups[fromAccount]?.grp;
    const b = groups[toAccount]?.grp;
    if (!a || !b || a !== b) cases.push("other_group");
  }
  const seat = groups[toAccount]?.grp_kind;
  if (claudeTo && (seat === "team" || seat === "enterprise")) cases.push("onto_seat");
  if (to.program === "codex") cases.push("codex");
  return cases;
}

/** What the owner is told the press does, before it does it. */
export const WHAT_IT_DOES = "This session is finished first, as Finish does: its worktree is committed to its branch, and the change verified and signed. Then a new session starts from that commit, on the program and account you choose, with your task below and what the workspace observed of this session's work: the ids of its commits and the names of the files they changed, marked as data. It gets none of this session's conversation, and this session is left as a Finish leaves it.";
/** The one check, said where it is made. */
export const ONE_CHECK = "The one check is that the new session's worktree is that commit: a terminal program or a chat has no tool to say it read what it was given, as a task's attempt has.";
