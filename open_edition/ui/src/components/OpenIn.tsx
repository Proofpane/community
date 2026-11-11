import { useEffect, useRef, useState } from "react";
import { FolderOpen } from "lucide-react";
import { DESKTOP } from "../api";
import { tauriInvoke } from "../desktop";
import { LOCAL, activeId } from "../places";
import { useStore } from "../store";
import { useMenu } from "./bits";

/** The editors installed on this Mac, asked of the desktop app's shell once (its `editors` command). */
let installed: Promise<string[]> | null = null;
const editorsHere = () => (installed ??= tauriInvoke<string[]>("editors").catch(() => []));

/** Whether a session's worktree is on this machine: this Mac's sessions, in the desktop app. */
export const worktreeHere = () => DESKTOP && activeId() === LOCAL;

/** A session's worktree in the person's own tools: shown in the Finder, or opened in an editor installed here. The
 *  shell finds the worktree by the session's id under its own data folder; the page never names a path. */
export function OpenIn({ session, run = tauriInvoke, editors = editorsHere }: {
  session: string;
  run?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  editors?: () => Promise<string[]>;
}) {
  const setError = useStore((s) => s.setError);
  const [open, setOpen] = useState(false);
  const [names, setNames] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { void editors().then(setNames); }, [editors]);
  useMenu(open, () => setOpen(false), box);
  const go = (editor: string | null) => {
    setOpen(false);
    run("open_worktree", { session, with: editor }).catch(setError);
  };
  const item = "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[12.5px] text-fg-dim hover:bg-hover hover:text-fg";
  return (
    <div className="relative" ref={box}>
      <button className="icon-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        title="Open the worktree in the Finder or an editor">
        <FolderOpen size={14} />
      </button>
      {open && (
        <div role="menu" aria-label="Open the worktree" className="absolute right-0 top-8 z-50 w-56 rounded-lg border border-line-strong bg-raised p-1 shadow-2xl shadow-black/60">
          <button role="menuitem" className={item} onClick={() => go(null)}>Show in Finder</button>
          {names.map((n) => <button key={n} role="menuitem" className={item} onClick={() => go(n)}>Open in {n}</button>)}
        </div>
      )}
    </div>
  );
}
