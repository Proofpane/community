// What New session offers: every agent, and for one that cannot start here, why. An agent is offered until the
// workspace says it is not installed; Claude Code and Codex until it says no account of theirs is signed in, as a
// session of either that starts signed out asks for a sign-in its terminal cannot finish in a hosted workspace.

import type { AccountsView, ClaudeStatus, ModelProviderView } from "./api";
import { MODEL_PROVIDERS, type AgentKind } from "./types";

export interface Offered { kind: AgentKind; available: boolean; note?: string }

export interface Known {
  programs: Record<string, boolean> | null;
  claude: ClaudeStatus | null;
  accounts: AccountsView | null;
  modelProviders: ModelProviderView[] | null;
}

export function agentsOffered(st: Known): Offered[] {
  // unknown until the workspace says: offered, and the workspace refuses one it does not have
  const has = (k: string) => st.programs?.[k] !== false;
  const missing = "Not installed in this workspace";
  // signed in on the account new sessions start on, or on another New session can pick
  const elsewhere = !!st.accounts?.claude.accounts.some((a) => a.status?.signed_in === true);
  const claude = has("claude") ? (st.claude?.signed_in !== false || elsewhere ? null : "Sign in to Claude first (Connections)") : missing;
  // Codex by its accounts alone: held back only once each one is known to be signed out
  const codexRows = st.accounts?.codex.accounts || [];
  const codexOut = codexRows.length > 0 && codexRows.every((a) => a.status?.signed_in === false);
  const codex = has("codex") ? (codexOut ? "Sign in to Codex first (Connections)" : null) : missing;
  return [
    { kind: "claude", available: !claude, note: claude || undefined },
    { kind: "codex", available: !codex, note: codex || undefined },
    { kind: "chat", available: !claude, note: claude || undefined },
    { kind: "shell", available: true },
    { kind: "gemini", available: has("gemini"), note: missing },
    { kind: "opencode", available: has("opencode"), note: missing },
    { kind: "grok", available: has("grok"), note: missing },
    { kind: "qwen", available: has("qwen"), note: missing },
    { kind: "kimicode", available: has("kimi"), note: missing },
    { kind: "mcode", available: has("mcode"), note: missing },
    // other providers' models in Claude Code: each once the person's own key for it is kept (Connections)
    ...MODEL_PROVIDERS.map((kind) => {
      const known = st.modelProviders?.find((p) => p.id === kind);
      const note = !has("claude") ? missing : known?.set ? undefined : `Add your ${known?.label || kind} API key in Connections`;
      return { kind, available: !note, note };
    }),
  ];
}
