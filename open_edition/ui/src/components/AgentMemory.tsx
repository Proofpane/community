import { useCallback, useEffect, useState } from "react";
import { Brain, Loader2, RotateCcw } from "lucide-react";
import { ago, AgentsApi, type Agent, type MemoryNow, type MemoryVersion, type MemoryVersionFull } from "../agents";
import { useStore } from "../store";
import { Confirm } from "./bits";

/** An agent's memory: Claude Code's own, kept by its runs. Each run that changed it left a version, with what it
 *  changed; any version can be made the memory again (itself a new version, so nothing is lost). A file unused for
 *  the agent's runs is moved to the archive by the daily sweep, and comes back from here. */
export function AgentMemory({ agent }: { agent: Agent }) {
  const setError = useStore((s) => s.setError);
  const [versions, setVersions] = useState<MemoryVersion[] | null>(null);
  const [now, setNow] = useState<MemoryNow | null>(null);
  const [open, setOpen] = useState<MemoryVersionFull | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const got = await AgentsApi.memory(agent.id);
      setVersions(Array.isArray(got?.versions) ? got.versions : []);
      setNow(got?.now && Array.isArray(got.now.files) ? got.now : null);
    } catch (e) { setError(e); }
  }, [agent.id, setError]);
  useEffect(() => { void load(); }, [load]);
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await load(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const show = (v: number) => go(async () => setOpen(await AgentsApi.memoryVersion(agent.id, v)));

  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s memory`}>
      <div className="flex items-center gap-2">
        <Brain size={12} className="text-muted" />
        <span className="font-medium">Memory</span>
        <span className="text-[11.5px] text-faint">{agent.decay_runs ? `a file unused for ${agent.decay_runs} runs is archived` : "never archived"}</span>
      </div>
      {versions === null && <div className="flex justify-center py-2 text-muted"><Loader2 size={13} className="animate-spin" /></div>}
      {versions?.length === 0 && <div className="mt-1 text-[11.5px] text-faint">No run has written to it yet.</div>}
      {versions && versions.length > 0 && (
        <ul className="mt-1 space-y-0.5" aria-label="Its versions">
          {versions.map((v) => (
            <li key={v.version} className="flex items-center gap-2">
              <button className={`underline decoration-dotted underline-offset-2 ${open?.version === v.version ? "font-medium" : ""}`}
                onClick={() => void show(v.version)}>v{v.version}</button>
              <span className="text-[11.5px] text-muted">{v.kind === "rollback" ? `back to v${v.from_version}` : "a run"}, {ago(v.made_at)}</span>
              <span className="font-mono text-[11px] text-faint">{v.files} {v.files === 1 ? "file" : "files"}</span>
              {v.version !== versions[0].version && !agent.retired && (
                <Confirm label="Back to this" icon={<RotateCcw size={11} />} again={`Make the memory v${v.version} again?`}
                  onConfirm={() => go(() => AgentsApi.rollbackMemory(agent.id, v.version))} className="!h-6 ml-auto text-[11px]" />
              )}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div className="mt-1.5 rounded-md border border-line p-1.5" aria-label={`Version ${open.version}'s changes`}>
          {open.changes.added.length > 0 && <div className="text-[11.5px] text-done">added: {open.changes.added.join(", ")}</div>}
          {open.changes.removed.length > 0 && <div className="text-[11.5px] text-fail">removed: {open.changes.removed.join(", ")}</div>}
          {open.changes.changed.map((c) => (
            <pre key={c.path} className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{c.diff ?? `${c.path}: not text`}</pre>
          ))}
          {!open.changes.added.length && !open.changes.removed.length && !open.changes.changed.length && <div className="text-[11.5px] text-faint">As the version before.</div>}
        </div>
      )}
      {now && now.archived.length > 0 && (
        <div className="mt-1.5" aria-label="Its archive">
          <div className="text-[11.5px] text-muted">Archived, not loaded by its runs:</div>
          <ul className="mt-0.5 space-y-0.5">
            {now.archived.map((a) => (
              <li key={a.path} className="flex items-center gap-2 font-mono text-[11.5px]">
                {a.path}
                <button className="btn btn-sm !h-6 ml-auto text-[11px]" disabled={busy || agent.retired}
                  onClick={() => void go(() => AgentsApi.unarchiveMemory(agent.id, a.path))}>Bring back</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
