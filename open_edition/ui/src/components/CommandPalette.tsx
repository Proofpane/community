import { Command } from "cmdk";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { File, Loader2 } from "lucide-react";
import { Api } from "../api";
import type { Session } from "../types";
import { AgentBadge, StatusDot, stateLabel } from "./bits";
import { useFocusInto } from "./Modal";

const FILES_SHOWN = 40;

/** How well a path matches what was typed: the letters in order, more for each in the file's own name, at the start
 *  of a word, or right after the one before; 0 when they are not all there in order. */
export function pathScore(path: string, query: string): number {
  const p = path.toLowerCase();
  const q = query.toLowerCase().replace(/\s+/g, "");
  if (!q) return 0;
  const name = p.lastIndexOf("/") + 1;
  let score = 0;
  let from = 0;
  let run = 0;
  for (const ch of q) {
    const at = p.indexOf(ch, from);
    if (at < 0) return 0;
    run = at === from && from > 0 ? run + 1 : 0;
    score += 1 + run * 2 + (at >= name ? 2 : 0) + (at === 0 || "/._- ".includes(p[at - 1]) ? 3 : 0);
    from = at + 1;
  }
  const own = p.slice(name);
  return score + (own.startsWith(q) ? 12 : own.includes(q) ? 6 : 0);
}

/** The best matches among a worktree's paths, best first; shorter paths first among equals. */
export function rankPaths(paths: string[], query: string, limit = FILES_SHOWN): string[] {
  const scored: [number, string][] = [];
  for (const path of paths) {
    const s = pathScore(path, query);
    if (s > 0) scored.push([s, path]);
  }
  scored.sort((a, b) => b[0] - a[0] || a[1].length - b[1].length || (a[1] < b[1] ? -1 : 1));
  return scored.slice(0, limit).map(([, path]) => path);
}

export interface PaletteAction {
  id: string;
  label: string;
  icon?: ReactNode;
  hint?: string;
  /** An action that takes what was typed (a search): with something typed, it is offered as this label says, on
   *  top of the actions, whatever its own label's letters; its own label, picked, starts it with nothing. */
  withQuery?: (query: string) => string;
  /** run with what was typed for an action that takes it (withQuery, picked as offered), else with nothing */
  run: (query: string) => void;
}

/** ⌘K: jump to any session, or run an action, from the keyboard. */
export function CommandPalette(props: {
  sessions: Session[];
  actions: PaletteAction[];
  onPick: (id: string) => void;
  onClose: () => void;
  filesOf?: { id: string; title: string } | null;     // the session whose worktree's files the palette searches
  onFile?: (session: string, path: string) => void;
  /** Opened to choose a session to open beside the ones shown: sessions only, and its own words. */
  beside?: boolean;
}) {
  const [query, setQuery] = useState("");
  const layer = useRef<HTMLDivElement>(null);
  useFocusInto(layer);
  const [paths, setPaths] = useState<string[] | null>(null);
  const [pathsError, setPathsError] = useState("");
  const of = props.beside ? undefined : props.filesOf?.id;
  const searchable = !props.beside && !!query.trim() && props.actions.some((a) => a.withQuery);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);
  useEffect(() => {
    if (!of) return;
    let live = true;
    Api.paths(of).then((r) => live && setPaths(r.paths)).catch((e) => live && setPathsError(e instanceof Error ? e.message : String(e)));
    return () => { live = false; };
  }, [of]);
  const files = useMemo(() => (paths && query.trim() ? rankPaths(paths, query) : []), [paths, query]);
  // the closed sessions after the rest: found here, though the list leaves them out
  const sessions = useMemo(() => [...props.sessions.filter((s) => !s.closed), ...props.sessions.filter((s) => s.closed)], [props.sessions]);
  return (
    <div ref={layer} tabIndex={-1} className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[14vh] outline-none" onMouseDown={props.onClose}>
      <Command
        label="Command palette"
        className="w-full max-w-[600px] overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60"
        onMouseDown={(e) => e.stopPropagation()}
        loop
      >
        <Command.Input
          autoFocus
          value={query}
          onValueChange={setQuery}
          placeholder={props.beside ? "Open a session beside the ones shown…"
            : of ? "Go to a session or a file, or run a command…" : "Go to a session, or run a command…"}
          className="h-12 w-full border-b border-line bg-transparent px-4 text-[14px] text-fg outline-none placeholder:text-faint"
        />
        <Command.List className="max-h-[52vh] overflow-y-auto p-1.5">
          {/* with a search for what was typed on offer, something always matches */}
          {!searchable && <Command.Empty className="px-3 py-6 text-center text-[12.5px] text-muted">Nothing matches.</Command.Empty>}
          {sessions.length > 0 && (
            <Command.Group heading="Sessions" className="palette-group">
              {sessions.map((s) => (
                <Command.Item key={s.id} value={`${s.title} ${s.repo} ${s.branch || ""} ${s.id}${s.closed ? " closed" : ""}`} onSelect={() => { props.onClose(); props.onPick(s.id); }} className="palette-item">
                  <AgentBadge kind={s.agent} name={s.agentName} size={16} />
                  <span className="min-w-0 flex-1 truncate">{s.title}</span>
                  <span className="truncate font-mono text-[11px] text-muted">{s.repo}</span>
                  {s.closed
                    ? <span className="text-[11px] text-faint" title="Closed: not in the list. Opened here, it is put back">Closed</span>
                    : <span className="flex items-center gap-1.5 text-[11px] text-muted"><StatusDot state={s.state} size={6} />{stateLabel(s.state)}</span>}
                </Command.Item>
              ))}
            </Command.Group>
          )}
          {of && query.trim() && (files.length > 0 || paths === null) && (
            <Command.Group heading={`Files in ${props.filesOf?.title}`} className="palette-group">
              {paths === null && !pathsError && (
                <Command.Item value={`file-loading ${query}`} disabled className="palette-item text-muted"><Loader2 size={13} className="animate-spin" /> Reading the worktree…</Command.Item>
              )}
              {files.map((path) => {
                const cut = path.lastIndexOf("/");
                return (
                  <Command.Item key={path} value={path} onSelect={() => { props.onClose(); props.onFile?.(of, path); }} className="palette-item">
                    <File size={14} className="flex-none text-muted" />
                    <span className="truncate">{path.slice(cut + 1)}</span>
                    {cut > 0 && <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">{path.slice(0, cut)}</span>}
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}
          {/* what was typed, searched for: its own group, shown whatever else matches or not (cmdk hides a group
              none of whose items it matched, a forced item included) */}
          {searchable && (
            <Command.Group heading="Search" className="palette-group" forceMount>
              {props.actions.filter((a) => a.withQuery).map((a) => (
                <Command.Item key={`${a.id}-with`} value={`${a.id}-with ${query}`} forceMount
                  onSelect={() => { props.onClose(); a.run(query.trim()); }} className="palette-item">
                  <span className="flex w-5 justify-center text-muted">{a.icon}</span>
                  <span className="flex-1 truncate">{a.withQuery!(query.trim())}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
          {!props.beside && props.actions.length > 0 && (
            <Command.Group heading="Actions" className="palette-group">
              {props.actions.map((a) => (
                <Command.Item key={a.id} value={a.label} onSelect={() => { props.onClose(); a.run(""); }} className="palette-item">
                  <span className="flex w-5 justify-center text-muted">{a.icon}</span>
                  <span className="flex-1">{a.label}</span>
                  {a.hint && <span className="kbd">{a.hint}</span>}
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
    </div>
  );
}
