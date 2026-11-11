// The ADE's view of its workspace. Field names follow the server's JSON where one exists; a field the server
// does not send stays optional, and the UI says nothing rather than guess.

export type AgentKind = "claude" | "codex" | "gemini" | "opencode" | "grok" | "qwen" | "kimi" | "minimax" | "deepseek" | "kimicode" | "mcode" | "shell" | "chat" | "custom";
/** Other providers' models, each run in Claude Code with the person's own key (the workspace's providers.py). */
export const MODEL_PROVIDERS = ["kimi", "minimax", "deepseek"] as const;
export type ModelProvider = (typeof MODEL_PROVIDERS)[number];

/** What a session is doing, as far as the workspace knows. "waiting" means it needs the person. */
/** "working": a chat in the middle of a turn (known). "running": a terminal program that is alive, which says
 *  nothing about whether it is busy, so it is never shown as working. */
export type SessionState = "starting" | "working" | "running" | "waiting" | "idle" | "done" | "failed" | "stopped";

export interface Session {
  id: string;
  title: string;
  /** An agent of the person's own (custom): its name, shown in place of a known agent's. */
  agentName?: string;
  /** The Claude Code or Codex account it runs on, when not the program's own (accounts.py): its name. */
  accountName?: string;
  /** Closed: stopped and left out of the list, nothing of it removed; the palette still finds it, and
   *  opening it there, or starting it again, puts it back. */
  closed?: boolean;
  repo: string;            // the repository's name in the workspace
  branch?: string;
  base?: string;
  /** The branch (or pull request) it started from, by its short name: what its Changes tab says it follows. */
  baseRef?: string;
  agent: AgentKind;
  state: SessionState;
  updatedAt?: string;      // ISO time of the last activity
  costUsd?: number;        // this session's own spend, when the agent reports one
  unread?: number;         // events since the person last looked
  diff?: { files: number; added: number; removed: number; uncommitted?: boolean };
  shell?: boolean;         // a shell of the person's own runs in its worktree
  note?: string;           // why it needs the person, when it does: what it asked, in its own words
  alert?: string;          // the same for a notification, from names only: never a command (store.ts waitingAlert)
  group?: string;          // started together with others on the same task
}

export interface Repo {
  name: string;
  url?: string;
  defaultBranch?: string;
}

export interface Approval {
  id: string;
  session?: string;
  tool: string;
  command: string;
  createdAt: string;
}

export interface WorkspaceStatus {
  provider: "local" | "fly";
  running: boolean;
  /** ever started (false: never, so not "stopped"); a server that does not say leaves it out */
  made?: boolean;
  starting?: boolean;
  region?: string;
  claudeVersion?: string;
  signedIn?: boolean;
}

/** A workspace's state in a word, for the title bar: running, starting, stopped, or not started (never was). */
export function workspaceWord(w: WorkspaceStatus): "running" | "starting" | "stopped" | "not started" {
  return w.running ? "running" : w.starting ? "starting" : w.made === false ? "not started" : "stopped";
}

export type Tab = "agent" | "changes" | "files" | "shell" | "activity";
