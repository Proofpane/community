// A chat's settings, as the Claude app has them: the model, the effort and
// the permission mode. The levels and the modes are the installed Claude Code's own, read by the workspace
// (GET /api/ade/workspace/capabilities: "when claude updates, our UI's content updates too"); until they come, the
// image's own claude's. The agent checks every value; an empty one is Claude Code's default.

export interface Capabilities { claude_version: string | null; model_aliases: string[]; efforts: string[]; permission_modes: string[] }
export type Chosen = { model?: string; effort?: string; permission_mode?: string };
/** A chat's settings as the workspace keeps them: the three, and whether it may start the agents' runs. */
export type ChatChosen = Chosen & { may_dispatch?: boolean };

export const MODEL_NAMES: Record<string, string> = { fable: "Fable", opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" };
export const EFFORT_NAMES: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultracode: "Ultracode" };
export const MODE_NAMES: Record<string, string> = { default: "Ask before acting", acceptEdits: "Accept edits", plan: "Plan first", auto: "Auto", bypassPermissions: "Allow everything" };
/** What each permission mode means, beside its choice: the two that ask less say so plainly. */
export const MODE_NOTES: Record<string, string> = {
  "": "Claude asks you before each action that needs your permission.",
  acceptEdits: "File edits go ahead without asking; commands still ask you.",
  plan: "Claude plans first and changes nothing until you accept its plan.",
  auto: "Claude Code decides which actions are safe to run without asking, and asks you for the rest.",
  bypassPermissions: "Nothing asks you in this chat: Claude acts on its own. The workspace's gates still refuse what they refuse.",
};
export const EFFORT_NOTES: Record<string, string> = { ultracode: "Extra high effort, and Claude may fan out sub-agents on its own." };

/** The floor: what the image's own claude offers (claude_cli.FLOOR_EFFORTS, FLOOR_MODES). */
export const FLOOR: Capabilities = {
  claude_version: null,
  model_aliases: ["fable", "opus", "sonnet", "haiku"],
  efforts: ["low", "medium", "high", "xhigh", "max", "ultracode"],
  permission_modes: ["default", "acceptEdits", "plan", "auto", "bypassPermissions"],
};

const strings = (x: unknown): x is string[] => Array.isArray(x) && x.length > 0 && x.every((v) => typeof v === "string" && v);

/** The installed claude's lists, when the workspace gave usable ones, else the floor. */
export function offered(caps: unknown): Capabilities {
  const c = (caps || {}) as Record<string, unknown>;
  if (!strings(c.efforts) || !strings(c.permission_modes) || !strings(c.model_aliases)) return FLOOR;
  return { claude_version: typeof c.claude_version === "string" ? c.claude_version : null, model_aliases: c.model_aliases, efforts: c.efforts, permission_modes: c.permission_modes };
}

/** Each setting's choices, [value, name]; "" is Claude Code's default. A chosen value no longer offered (or set some
 *  other way, as a model's full name) is kept as itself, so it is shown and not silently changed. */
export function choices(caps: Capabilities, chosen: Chosen = {}): { key: keyof Chosen; label: string; list: [string, string][] }[] {
  const keep = (list: [string, string][], value?: string): [string, string][] =>
    value && !list.some(([v]) => v === value) ? [...list, [value, value]] : list;
  return [
    { key: "model", label: "Model", list: keep([["", "Default model"], ...caps.model_aliases.map((m): [string, string] => [m, MODEL_NAMES[m] || m])], chosen.model) },
    { key: "effort", label: "Effort", list: keep([["", "Default effort"], ...caps.efforts.map((e): [string, string] => [e, `${EFFORT_NAMES[e] || e} effort`])], chosen.effort) },
    { key: "permission_mode", label: "Permissions", list: keep([["", MODE_NAMES.default], ...caps.permission_modes.filter((m) => m !== "default").map((m): [string, string] => [m, MODE_NAMES[m] || m])], chosen.permission_mode) },
  ];
}

/** The settings in a few words, for the chat's log. */
export function words(chosen: Chosen): string {
  const bits: string[] = [];
  if (chosen.model) bits.push(MODEL_NAMES[chosen.model] || chosen.model);
  if (chosen.effort) bits.push(`${EFFORT_NAMES[chosen.effort] || chosen.effort} effort`);
  if (chosen.permission_mode) bits.push(MODE_NAMES[chosen.permission_mode] || chosen.permission_mode);
  return bits.length ? bits.join(" · ") : "Claude Code's defaults";
}
