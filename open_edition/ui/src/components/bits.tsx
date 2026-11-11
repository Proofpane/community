import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Loader2 } from "lucide-react";
import { useTheme } from "../theme";
import type { AgentKind, SessionState } from "../types";

const STATE: Record<SessionState, { color: string; label: string; pulse?: boolean }> = {
  starting: { color: "var(--color-run)", label: "Starting", pulse: true },
  working: { color: "var(--color-run)", label: "Working", pulse: true },
  running: { color: "var(--color-run)", label: "Running" },
  waiting: { color: "var(--color-wait)", label: "Needs you" },
  idle: { color: "var(--color-idle)", label: "Idle" },
  done: { color: "var(--color-done)", label: "Done" },
  failed: { color: "var(--color-fail)", label: "Failed" },
  stopped: { color: "var(--color-faint)", label: "Stopped" },
};

export function stateLabel(state: SessionState) {
  return STATE[state].label;
}

export function StatusDot({ state, size = 7 }: { state: SessionState; size?: number }) {
  const s = STATE[state];
  return (
    <span
      className={`dot ${s.pulse ? "animate-pulse-dot" : ""}`}
      style={{ background: s.color, width: size, height: size, boxShadow: state === "waiting" ? `0 0 0 3px color-mix(in srgb, ${s.color} 22%, transparent)` : undefined }}
      title={s.label}
      aria-label={s.label}
    />
  );
}

// each agent's mark, and its colours on the dark theme and the light one
const AGENT: Record<AgentKind, { label: string; mark: string; bg: string; fg: string; lightBg: string; lightFg: string }> = {
  claude: { label: "Claude Code", mark: "C", bg: "#3a2219", fg: "#f0a17f", lightBg: "#fbe3d6", lightFg: "#9a3412" },
  chat: { label: "Claude chat", mark: "Ch", bg: "#3a2219", fg: "#f0a17f", lightBg: "#fbe3d6", lightFg: "#9a3412" },
  codex: { label: "Codex", mark: "X", bg: "#1f2430", fg: "#d6dbe4", lightBg: "#e5e7eb", lightFg: "#1f2937" },
  gemini: { label: "Gemini CLI", mark: "G", bg: "#16243d", fg: "#8ab4f8", lightBg: "#dbeafe", lightFg: "#1d4ed8" },
  opencode: { label: "OpenCode", mark: "O", bg: "#1d2a22", fg: "#86efac", lightBg: "#dcfce7", lightFg: "#15803d" },
  grok: { label: "Grok Build", mark: "K", bg: "#2a1f3a", fg: "#d8b4fe", lightBg: "#f3e8ff", lightFg: "#7e22ce" },
  qwen: { label: "Qwen Code", mark: "Q", bg: "#1e1f3d", fg: "#a5b4fc", lightBg: "#e0e7ff", lightFg: "#4338ca" },
  kimi: { label: "Kimi K3", mark: "Ki", bg: "#123030", fg: "#5eead4", lightBg: "#ccfbf1", lightFg: "#0f766e" },
  minimax: { label: "MiniMax M3", mark: "M", bg: "#3a1e1e", fg: "#fca5a5", lightBg: "#fee2e2", lightFg: "#b91c1c" },
  deepseek: { label: "DeepSeek", mark: "D", bg: "#0f2a3d", fg: "#7dd3fc", lightBg: "#e0f2fe", lightFg: "#0369a1" },
  kimicode: { label: "Kimi Code", mark: "Kc", bg: "#0d2626", fg: "#2dd4bf", lightBg: "#ccfbf1", lightFg: "#115e59" },
  mcode: { label: "MiniMax Code", mark: "Mc", bg: "#2e1717", fg: "#f87171", lightBg: "#fee2e2", lightFg: "#991b1b" },
  shell: { label: "Shell", mark: "$", bg: "#1c2026", fg: "#9aa4b1", lightBg: "#e5e7eb", lightFg: "#374151" },
  custom: { label: "Your agent", mark: "+", bg: "#262626", fg: "#d4d4d4", lightBg: "#e5e5e5", lightFg: "#262626" },
};

/** An agent of the person's own shows by its name, and its mark is the name's first letters (two words, two). */
export function customMark(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const first = (w: string) => Array.from(w)[0] || "";
  return (words.length > 1 ? first(words[0]) + first(words[1]) : Array.from(words[0] || "+").slice(0, 2).join("")).toUpperCase() || "+";
}

export function agentLabel(kind: AgentKind, name?: string) {
  return kind === "custom" && name ? name : AGENT[kind].label;
}

export function AgentBadge({ kind, size = 18, name }: { kind: AgentKind; size?: number; name?: string }) {
  const a = AGENT[kind];
  const light = useTheme((t) => t.theme) === "light";
  return (
    <span
      title={agentLabel(kind, name)}
      role="img"
      aria-label={agentLabel(kind, name)}
      className="inline-flex flex-none items-center justify-center font-mono font-semibold"
      style={{ width: size, height: size, borderRadius: 4, background: light ? a.lightBg : a.bg, color: light ? a.lightFg : a.fg, fontSize: size * 0.58 }}
    >
      {kind === "custom" && name ? customMark(name) : a.mark}
    </span>
  );
}

export function timeAgo(iso?: string, now = Date.now()) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 45) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function usd(n?: number) {
  if (n == null) return "";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <span className="kbd">{children}</span>;
}

export interface Because { id?: string; resolver?: string | null; outcome?: string | null; reason?: string | null }
export interface Decision { verify?: string | null; claims?: string | null; rule?: string | null; because?: Because[] }

export const passed = (d: Decision) => d.verify === "success" && d.claims === "success";

const NO_CHECKS = "no expected check applies to this change";
export const TONE = { done: "text-done", wait: "text-wait", fail: "text-fail" } as const;

/** What a verdict means for the person, in a sentence, by the rule that decided it (oss/core's gate): verified, needs
 *  their review, or not verified. */
export function verdictWords(d: Decision): { text: string; tone: "done" | "wait" | "fail" } {
  switch (d.rule) {
    case "all-pass": return { text: "Verified: every check passed", tone: "done" };
    case "advisory-not-vouched": return { text: "Verified; an advisory claim was not confirmed", tone: "done" };
    case "needs-person":
      return (d.because || []).length > 0 && (d.because || []).every((b) => b.reason === NO_CHECKS)
        ? { text: "Needs your review: no checks are set up for this change", tone: "wait" }
        : { text: "Needs your review: something required could not be checked here", tone: "wait" };
    case "nothing-blocking": return { text: "Needs your review: nothing here could be checked", tone: "wait" };
    case "any-fail": return { text: "Not verified: a required check failed", tone: "fail" };
    default: return passed(d) ? { text: "Verified", tone: "done" } : { text: "Not verified", tone: "fail" };
  }
}

/** What Verify decided about a change this deployment signed: whether it passed, and what kept it from passing. A
 *  signature says who decided and that nothing changed since; this says what was decided. */
export function Verdict({ verify, claims, rule, because = [] }: Decision) {
  const said = verdictWords({ verify, claims, rule, because });
  // a check that did not pass because it waits for the person (no checks set up, say) is amber, as its sentence is:
  // rose is for what failed
  const tone = (v?: string | null) => (v === "success" ? "text-done" : v === "failure" ? TONE[said.tone === "wait" ? "wait" : "fail"] : "text-muted");
  return (
    <div className="space-y-1">
      <div className={`text-[12.5px] font-medium ${TONE[said.tone]}`}>{said.text}</div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-fg-dim">
        <span>Verify <b className={`font-medium ${tone(verify)}`}>{verify || "not decided"}</b></span>
        <span>Claims <b className={`font-medium ${tone(claims)}`}>{claims || "not decided"}</b></span>
        {rule && <span className="text-muted">rule <span className="font-mono">{rule}</span></span>}
      </div>
      {because.slice(0, 3).map((b, i) => (
        <div key={i} className="text-[11.5px] text-muted">
          <span className="font-mono text-fg-dim">{b.id}</span>{b.resolver ? <> · <span className="font-mono">{b.resolver}</span></> : null}{b.reason ? `: ${b.reason}` : ""}
        </div>
      ))}
      {because.length > 3 && <div className="text-[11.5px] text-faint">and {because.length - 3} more</div>}
    </div>
  );
}

/** A button that asks to be clicked again before it does something that cannot be undone. `disabled`: while another
 *  press of its card goes, it is not offered. */
export function Confirm({ label, again, onConfirm, danger, icon, className = "", disabled = false }: { label: string; again: string; onConfirm: () => Promise<void>; danger?: boolean; icon?: ReactNode; className?: string; disabled?: boolean }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      className={`btn btn-sm ${armed || danger ? "btn-danger" : ""} ${className}`}
      disabled={busy || disabled}
      onClick={async () => {
        if (!armed) { setArmed(true); return; }
        setBusy(true);
        try { await onConfirm(); } finally { setBusy(false); setArmed(false); }
      }}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : icon} {armed ? again : label}
    </button>
  );
}

/** Whether `el` is a field the person types into and it holds something typed: a text area, a text input, or an
 *  element they edit in place. Escape pressed there never closes what it is in: what was typed would go with it. */
export function holdsText(el: EventTarget | Element | null): boolean {
  if (el instanceof HTMLTextAreaElement) return el.value !== "";
  if (el instanceof HTMLInputElement) {
    return ["text", "search", "url", "email", "tel", "password", ""].includes(el.type) && el.value !== "";
  }
  return el instanceof HTMLElement && el.isContentEditable && (el.textContent ?? "") !== "";
}

/** Escape closes a drawer, as it closes a dialog (Modal). While `on` (a drawer shown only sometimes). Not when the key
 *  was already used, by a field that ends its editing with it, nor when it was pressed in a field that holds something
 *  typed (holdsText), nor while `held` says the drawer holds something typed elsewhere, nor when a dialog or the
 *  palette is open over the drawer: Escape closes what is on top, one layer at a time, and never takes words with it. */
export function useEscape(onClose: () => void, on = true, held?: () => boolean) {
  const close = useRef(onClose);
  close.current = onClose;
  const holding = useRef(held);
  holding.current = held;
  useEffect(() => {
    if (!on) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || document.querySelector('[role="dialog"], [cmdk-root]')) return;
      if (holdsText(e.target) || holdsText(document.activeElement) || holding.current?.()) return;
      close.current();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [on]);
}

/** A change's size, in words: "12 lines added and 3 removed, in 4 files". */
export function sizeWords(d: { files: number; added: number; removed: number }): string {
  const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;
  // a binary file, a mode, a rename, an empty file: files changed, and no line of text added or removed
  if (!d.added && !d.removed) return `${n(d.files, "file", "files")} changed, with no lines added or removed`;
  return `${n(d.added, "line", "lines")} added and ${d.removed} removed, in ${n(d.files, "file", "files")}`;
}

/** A change's size, short: "+12 −3"; or, with no line added or removed (a binary file, say), how many files. */
export function sizeShort(d: { files: number; added: number; removed: number }): ReactNode {
  if (!d.added && !d.removed) return <span className="text-muted">{d.files} {d.files === 1 ? "file" : "files"}</span>;
  return <><span className="text-done">+{d.added}</span> <span className="text-fail">−{d.removed}</span></>;
}

/** A menu that opens from a button, as menus do: open, the focus goes to its first item; the arrows (and Home and End)
 *  move between its items; Escape closes it and gives the focus back to its button; a click away closes it. */
export function useMenu(open: boolean, close: () => void, box: RefObject<HTMLElement | null>) {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]') ?? []);
    const opener = box.current?.querySelector<HTMLElement>('[aria-haspopup="menu"]');
    items()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      const all = items();
      const at = all.indexOf(document.activeElement as HTMLElement);
      const to = e.key === "ArrowDown" ? at + 1 : e.key === "ArrowUp" ? (at < 0 ? all.length - 1 : at - 1)
        : e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : null;
      if (e.key === "Escape") { closeRef.current(); opener?.focus(); return; }
      if (to === null || !all.length) return;
      e.preventDefault();
      all[(to + all.length) % all.length].focus();
    };
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) closeRef.current(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", away);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", away); };
  }, [open, box]);
}

