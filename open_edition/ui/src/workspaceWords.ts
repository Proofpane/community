import type { WorkspaceState } from "./api";

// What the page says of a workspace that does not run, App's main panel and the Manager's place to sign an account in
// alike; moved here from App.tsx, which keeps exporting it.

/** What the main panel says of the workspace while it does not run: started for the first time, stopped with what it
 *  keeps, or starting; and why a start failed. A workspace never started is not called stopped (nothing of it is kept
 *  yet); a server that does not say whether it was (no `made`) is told in words true either way. */
export function workspaceWords(ws: WorkspaceState): { title: string; body: string } {
  if (ws.starting) {
    return { title: "Starting your workspace\u2026", body: ws.made === false && ws.provider === "fly"
      ? "A machine of its own, made the first time it starts: this takes a minute or two."
      : ws.made === false ? "A machine of its own for your repositories and sessions. This takes a few seconds."
      : "Its repositories and sessions come back as they were. This takes a few seconds." };
  }
  if (ws.made === false) {
    return { title: "Start your workspace",
      body: "A machine of its own where agents work on your repositories, each session in a worktree of its own. It stops itself when idle." };
  }
  if (ws.made) {
    return { title: "Your workspace is stopped", body: "It stops itself when idle. Its repositories and sessions are kept, and come back when it starts." };
  }
  return { title: "Your workspace is not running", body: "Start it to work on your repositories here. It stops itself when idle." };
}
