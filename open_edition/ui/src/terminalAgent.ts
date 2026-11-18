import type { SessionRecord } from "./api";
import { agentLabel } from "./components/bits";
import { MODEL_PROVIDERS, type ModelProvider } from "./types";

// What a Claude Code session is doing, for the line under its terminal on a desktop (TerminalPane's DeskKeys), and
// which sessions' terminals are told it: apart from TerminalPane, which loads xterm.js, so that the app's own bundle
// can ask without it.

// the notifications' kinds that wait on the person's answer, of those Claude Code's documentation lists; of the rest,
// idle_prompt says the session waits at its prompt, and the others (auth_success, agent_completed and more) say
// nothing of what it is doing
const ASKS = new Set(["permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input",
  "quota_auto_resume_stale"]);
const TOOL = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/;

/** What a Claude Code session is doing, in the page's words, from the activity its hooks write (activity.py), the
 *  program called `who`: Claude, or the model of another provider it runs on, as the page names that model; never
 *  the program's own words, which can carry a command. Nothing where it cannot tell: no activity yet, or a
 *  notification of a kind not named here, whose state the hooks leave unsaid. As fresh as the page's last read of the
 *  sessions, every 5 s while it is shown. */
export function stateWords(a: SessionRecord["activity"] | undefined, who = "Claude"): string | undefined {
  if (!a) return undefined;
  if (a.state === "working") return typeof a.tool === "string" && TOOL.test(a.tool) ? `${who} is working: ${a.tool}`
    : `${who} is working`;
  if (a.state === "idle") return `${who} waits for your prompt`;
  if (a.state !== "waiting") return undefined;
  if (!a.kind) return `${who} waits for you in the terminal`;
  if (a.kind === "permission_prompt") return `${who} asks your permission in the terminal`;
  if (ASKS.has(a.kind)) return `${who} waits for your answer in the terminal`;
  return a.kind === "idle_prompt" ? `${who} waits for your prompt` : undefined;
}

/** What a session's terminal is told for Stop and the state line: a running Claude Code session's id and activity,
 *  and what to call it, Claude, or the model of another provider it runs on as the page names that model (Kimi K3,
 *  MiniMax M3 and DeepSeek each run in Claude Code); nothing for any other program (a shell, Codex, a custom one) or a
 *  session that has ended. */
export function terminalStateOf(rec: SessionRecord):
  { id: string; activity: SessionRecord["activity"] | null; who: string } | undefined {
  if (rec.program !== "claude" || !rec.running) return undefined;
  const p = rec.model_provider;
  const who = p && (MODEL_PROVIDERS as readonly string[]).includes(p) ? agentLabel(p as ModelProvider) : "Claude";
  return { id: rec.id, activity: rec.activity ?? null, who };
}
