import { useEffect, useMemo, useState } from "react";
import { Check, FolderGit2, GitFork, Loader2, Lock, Search } from "lucide-react";
import { Api, DESKTOP } from "../api";
import { tauriInvoke } from "../desktop";
import { LOCAL, activeId } from "../places";
import { useStore } from "../store";
import { Modal } from "./Modal";

/** Clone into the workspace: from the signed-in GitHub account's list, or any https address; or, where the workspace
 *  is on the person's own machine (the local provider), add a repository already there, used where it is. Once the
 *  workspace has it, the dialog closes and `onCloned` is given its name (the app opens a new session in it), while the
 *  account's list, which can take a while, is read again behind. */
const LINKING = "\u0000link";       // what `busy` names while a folder is being added (never an address)
const PICKING = "\u0000pick";       // and while the shell's dialog is open

/** The desktop app's shell picks a folder in the system's own dialog (pick_folder): its path, or null when cancelled. */
const pickInShell = () => tauriInvoke<string | null>("pick_folder");

// the shell's dialog picks a folder on this Mac: only where the window shows this Mac's own workspace
export function CloneDialog({ onClose, onConnections, onCloned, pickFolder = DESKTOP && activeId() === LOCAL ? pickInShell : undefined }: {
  onClose: () => void; onConnections: () => void; onCloned?: (name: string) => void;
  /** Where the page can ask for a folder in a dialog (the desktop app): it is added as soon as one is picked. */
  pickFolder?: () => Promise<string | null>;
}) {
  const github = useStore((s) => s.github);
  const refreshGithub = useStore((s) => s.refreshGithub);
  const refresh = useStore((s) => s.refresh);
  const local = useStore((s) => s.ws?.provider === "local");
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { void refreshGithub(); }, [refreshGithub]);

  const repos = useMemo(() => {
    const all = github?.repos || [];
    const needle = q.trim().toLowerCase();
    return needle ? all.filter((r) => r.full_name.toLowerCase().includes(needle) || r.description.toLowerCase().includes(needle)) : all;
  }, [github, q]);

  const clone = (address: string) => add(address, () => Api.clone(address));
  const link = (path: string) => add(LINKING, () => Api.linkRepo(path));

  const add = async (what: string, make: () => Promise<{ name: string }>) => {
    setBusy(what);
    setError("");
    try {
      const made = await make();
      await refresh();
      void refreshGithub();
      if (onCloned) onCloned(made.name);
      else onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal title={local ? "Add a repository" : "Clone a repository"} onClose={onClose} width={640}>
      <div className="space-y-4">
        {local && (
          <div>
            <label className="label mb-1.5 block">From a folder on this Mac</label>
            <div className="flex gap-2">
              <input
                autoFocus
                className="input font-mono text-[12px]"
                placeholder="/Users/you/code/project"
                aria-label="The repository's folder"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && folder.trim() && !busy && link(folder.trim())}
              />
              {pickFolder && (
                <button className="btn whitespace-nowrap" disabled={!!busy} onClick={async () => {
                  setError("");
                  setBusy(PICKING);           // one dialog at a time: a second click waits for the first
                  let picked: string | null = null;
                  try {
                    picked = await pickFolder();
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(null);
                  }
                  if (picked) { setFolder(picked); await link(picked); }
                }}>{busy === PICKING ? <Loader2 size={13} className="animate-spin" /> : null} Choose…</button>
              )}
              {/* filled once its field has something in it: the one filled action is the one being taken */}
              <button className={`btn ${folder.trim() ? "btn-primary" : ""}`} disabled={!folder.trim() || !!busy} onClick={() => link(folder.trim())}>
                {busy === LINKING ? <Loader2 size={13} className="animate-spin" /> : <FolderGit2 size={13} />} Add
              </button>
            </div>
            <div className="mt-1.5 text-[11.5px] text-muted">
              Used where it is: sessions make their worktrees and <span className="font-mono">pp/…</span> branches in it, as <span className="font-mono">git worktree</span> does, and leave your own branches, files and settings as they are.{pickFolder ? null : <> In the Finder, <span className="font-mono">⌥⌘C</span> copies a folder's path.</>}
            </div>
          </div>
        )}
        <div>
          <label className="label mb-1.5 block">{local ? "Clone from an address" : "From an address"}</label>
          <div className="flex gap-2">
            <input aria-label="The repository's address"
              autoFocus={!local}
              className="input font-mono text-[12px]"
              placeholder="https://github.com/owner/name"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && url.trim() && clone(url.trim())}
            />
            <button className={`btn ${url.trim() ? "btn-primary" : ""}`} disabled={!url.trim() || !!busy} onClick={() => clone(url.trim())}>
              {busy === url.trim() ? <Loader2 size={13} className="animate-spin" /> : <GitFork size={13} />} Clone
            </button>
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="label">{local ? "Clone from GitHub" : "From GitHub"}{github?.user ? ` · ${github.user}` : ""}</label>
            {github && !github.user && (
              <button className="text-[12px] text-accent hover:underline" onClick={onConnections}>Sign in to GitHub</button>
            )}
          </div>
          {!github ? (
            <div className="flex items-center gap-2 py-3 text-[12.5px] text-muted"><Loader2 size={13} className="animate-spin" /> Reading your repositories…</div>
          ) : !github.user ? (
            <div className="rounded-md border border-line px-3 py-3 text-[12.5px] text-muted">Sign in to GitHub to pick from your own repositories, private ones included.</div>
          ) : (
            <>
              <div className="relative mb-2">
                <Search size={13} className="absolute left-2.5 top-2.5 text-faint" />
                <input aria-label="Filter your repositories" className="input pl-8" placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <div className="max-h-[300px] overflow-y-auto rounded-md border border-line">
                {repos.length === 0 && <div className="px-3 py-4 text-center text-[12.5px] text-muted">No repository matches.</div>}
                {repos.map((r) => (
                  <div key={r.full_name} className="flex items-center gap-3 border-b border-line px-3 py-2 last:border-b-0 hover:bg-hover">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-[12.5px]">
                        <span className="truncate font-medium">{r.full_name}</span>
                        {r.private && <Lock size={11} className="flex-none text-faint" />}
                      </div>
                      <div className="truncate text-[11.5px] text-muted">{r.description || (r.pushed ? `pushed ${r.pushed}` : "")}</div>
                    </div>
                    {r.cloned ? (
                      <span className="flex items-center gap-1 text-[11.5px] text-done"><Check size={12} /> In the workspace</span>
                    ) : (
                      <button className="btn btn-sm" disabled={!!busy} onClick={() => clone(r.url)}>
                        {busy === r.url ? <Loader2 size={12} className="animate-spin" /> : null} Clone
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        {error && <div className="text-[12.5px] text-fail">{error}</div>}
      </div>
    </Modal>
  );
}
