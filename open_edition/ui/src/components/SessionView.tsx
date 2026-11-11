import { Columns2, FileCode2, GitBranch, GitCommitHorizontal, Layers, PanelRightClose, PanelRightOpen, ShieldCheck, SquareTerminal, Terminal, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Session, Tab } from "../types";
import { AgentBadge, StatusDot, agentLabel, sizeShort, sizeWords, stateLabel, usd } from "./bits";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "agent", label: "Agent", icon: <SquareTerminal size={14} /> },
  { id: "changes", label: "Changes", icon: <GitCommitHorizontal size={14} /> },
  { id: "files", label: "Files", icon: <FileCode2 size={14} /> },
  { id: "shell", label: "Shell", icon: <Terminal size={14} /> },
  { id: "activity", label: "Activity", icon: <ShieldCheck size={14} /> },
];

export function SessionView(props: {
  session: Session;
  tab: Tab;
  onTab: (t: Tab) => void;
  onClose?: () => void;
  onBeside?: () => void;
  /** Split: the agent on the left, and the tab chosen beside it; `tab` is then the one beside it. */
  split?: boolean;
  onSplit?: () => void;
  onCompare?: () => void;
  groupSize?: number;
  actions?: ReactNode;
  compact?: boolean;
  /** Give the session a name; an empty one puts its task's first line back. */
  onRename?: (title: string) => Promise<unknown>;
  /** Asked from elsewhere (the palette) to start renaming. */
  renaming?: boolean;
  onRenamed?: () => void;
  children: ReactNode;
}) {
  const s = props.session;
  const [naming, setNaming] = useState(false);
  useEffect(() => { if (props.renaming) setNaming(true); }, [props.renaming]);
  const done = () => { setNaming(false); props.onRenamed?.(); };
  return (
    <section className="@container flex h-full min-w-0 flex-col bg-bg">
      <div className="flex min-h-11 flex-none flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-line px-3 @max-lg:py-1.5">
        <AgentBadge kind={s.agent} name={s.agentName} size={20} />
        <div className="min-w-[110px] flex-1 @max-lg:basis-[calc(100%-34px)]">
          <div className="flex items-center gap-2">
            {naming && props.onRename ? (
              <TitleInput value={s.title} onDone={done} onSave={props.onRename} />
            ) : (
              <span
                className={`truncate text-[13.5px] font-semibold ${props.onRename ? "cursor-text" : ""}`}
                title={props.onRename ? "Double-click to rename" : undefined}
                onDoubleClick={() => props.onRename && setNaming(true)}
              >
                {s.title}
              </span>
            )}
            <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
              <StatusDot state={s.state} size={6} />
              {stateLabel(s.state)}
            </span>
          </div>
          <div className="flex items-center gap-1.5 truncate text-[11px] text-muted">
            <span>{agentLabel(s.agent, s.agentName)}</span>
            {s.accountName && <span className="text-fg-dim" title="The account it runs on">on {s.accountName}</span>}
            <span className="text-faint">·</span>
            <span>{s.repo}</span>
            {s.branch && (
              <>
                <span className="text-faint">·</span>
                <GitBranch size={11} className="text-faint" />
                <span className="font-mono">{s.branch}</span>
                {s.base && (
                  <span className="text-faint @max-md:hidden" title={`Started from ${s.baseRef ? `${s.baseRef}, at commit ` : "commit "}${s.base}`}>
                    from {s.baseRef ? <>{s.baseRef} · </> : null}{s.base}
                  </span>
                )}
              </>
            )}
            {s.costUsd != null && (
              <>
                <span className="text-faint">·</span>
                <span className="font-mono">{usd(s.costUsd)}</span>
              </>
            )}
          </div>
        </div>
        <div className="ml-auto flex flex-none items-center gap-1.5 [&_.btn-label]:hidden @3xl:[&_.btn-label]:inline @max-lg:ml-0 @max-lg:w-full">
          {s.diff && s.diff.files > 0 && (
            <button className="btn btn-sm" onClick={() => props.onTab("changes")} title={`Review the changes: ${sizeWords(s.diff)}`}
              aria-label={`Review the changes: ${sizeWords(s.diff)}`}>
              <span className="font-mono">{sizeShort(s.diff)}</span>
              <span className="btn-label text-muted">{s.diff.files} {s.diff.files === 1 ? "file" : "files"}</span>
            </button>
          )}
          {props.onCompare && (props.groupSize || 0) > 1 && (
            <button className="btn btn-sm" onClick={props.onCompare} title="Open every session started with this one on the same task, side by side, on their changes">
              <Layers size={13} /> <span className="btn-label">Compare</span> {props.groupSize}
            </button>
          )}
          {props.actions}
          {props.onSplit && (
            <button
              className={`icon-btn ${props.split ? "text-accent" : ""}`}
              onClick={props.onSplit}
              title={props.split ? "Close the split: the agent alone" : "Split: the agent, and beside it its changes, files or shell"}
              aria-pressed={!!props.split}
            >
              {props.split ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            </button>
          )}
          {props.onBeside && (
            <button className="icon-btn" onClick={props.onBeside} title="Open beside">
              <Columns2 size={15} />
            </button>
          )}
          {props.onClose && (
            <button className="icon-btn" onClick={props.onClose} title="Close pane"><X size={15} /></button>
          )}
        </div>
      </div>

      <nav role="tablist" aria-label="The session's tabs" className="flex h-9 flex-none items-end gap-0.5 border-b border-line px-2"
        onKeyDown={(e) => {
          // the arrows move along the tabs, as tabs do; Home and End to the first and the last. In a split the agent
          // is on the left already and the arrows go along what is beside it: choosing the agent would end the split
          const along = props.split ? TABS.filter((t) => t.id !== "agent") : TABS;
          const at = Math.max(0, along.findIndex((t) => t.id === props.tab));
          const to = e.key === "ArrowRight" ? at + 1 : e.key === "ArrowLeft" ? at - 1 : e.key === "Home" ? 0 : e.key === "End" ? along.length - 1 : null;
          if (to === null) return;
          e.preventDefault();
          const next = along[(to + along.length) % along.length];
          props.onTab(next.id);
          (e.currentTarget.querySelector(`[data-tab="${next.id}"]`) as HTMLElement | null)?.focus();
        }}>
        {TABS.map((t) => {
          const on = props.tab === t.id;
          const left = !!props.split && t.id === "agent";     // shown on the left of a split; chosen, it is shown alone
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={on}
              tabIndex={on ? 0 : -1}
              data-tab={t.id}
              onClick={() => props.onTab(t.id)}
              title={left ? "On the left of the split; click to show the agent alone" : t.label}
              // its name whatever shows: a narrow pane hides the label of a tab not open, and the count alone is no name
              aria-label={t.id === "changes" && s.diff && s.diff.files > 0
                ? `${t.label}, ${s.diff.files} ${s.diff.files === 1 ? "file" : "files"}`
                : t.id === "shell" && s.shell ? `${t.label}, a shell open` : t.label}
              className={`relative flex h-8 flex-none items-center gap-1.5 rounded-t-md px-3 text-[12.5px] @max-md:px-2.5 ${
                on ? "bg-panel text-fg" : left ? "bg-panel/40 text-fg-dim hover:text-fg" : "text-muted hover:text-fg-dim"
              }`}
            >
              {t.icon}
              {/* a narrow pane (a phone) names the open tab only; the others are their icons */}
              <span className={on ? "" : "@max-md:hidden"}>{t.label}</span>
              {t.id === "changes" && s.diff && s.diff.files > 0 && (
                <span className="rounded bg-active px-1 font-mono text-[10px] text-fg-dim">{s.diff.files}</span>
              )}
              {t.id === "shell" && s.shell && <span className="h-1.5 w-1.5 rounded-full bg-done" title="A shell is open" />}
              {on && <span className="absolute inset-x-2 -bottom-px h-[2px] rounded bg-accent" />}
              {left && <span className="absolute inset-x-2 -bottom-px h-[2px] rounded bg-line-strong" />}
            </button>
          );
        })}
      </nav>

      <div className="min-h-0 flex-1">{props.children}</div>
    </section>
  );
}

/** The session's name, edited in place: Enter or leaving the field keeps it, Esc does not. */
function TitleInput({ value, onSave, onDone }: { value: string; onSave: (t: string) => Promise<unknown>; onDone: () => void }) {
  const [text, setText] = useState(value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  const ended = useRef(false);
  useEffect(() => { ref.current?.select(); }, []);
  const save = async () => {
    if (ended.current) return;
    const t = text.trim();
    if (t === value.trim()) { ended.current = true; onDone(); return; }
    setBusy(true); setError("");
    try { await onSave(t); ended.current = true; onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2">
      <input
        ref={ref}
        autoFocus
        aria-label="The session's name"
        className="input h-7 min-w-0 flex-1 !py-0 text-[13px] font-semibold"
        value={text}
        maxLength={120}
        disabled={busy}
        placeholder="Its task's first line"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void save(); }
          else if (e.key === "Escape") { e.preventDefault(); ended.current = true; onDone(); }
        }}
        onBlur={() => void save()}
      />
      {error && <span className="truncate text-[11.5px] text-fail">{error}</span>}
    </span>
  );
}
