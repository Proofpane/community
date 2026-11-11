import { useEffect, useState } from "react";
import { Bot, CornerDownRight, Loader2 } from "lucide-react";
import { AgentsApi, compact, duration, tokenCount, type RunContext, type TreeAgent, type TreeTool, type TreeTurn } from "../agents";

/** A run's context against its compaction point: its peak, its compactions, and what its last summary lost. */
export function ContextLine({ context }: { context: RunContext }) {
  const pct = context.peak_pct != null ? Math.round(context.peak_pct * 100) : null;
  return (
    <div className="mt-1.5 text-[11.5px]" aria-label="Its context">
      {pct != null && (
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-28 overflow-hidden rounded-full bg-line" aria-hidden="true">
            <div className="h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: pct >= 80 ? "var(--color-wait)" : "var(--color-accent)" }} />
          </div>
          <span className="text-muted">context peak {pct}% of {compact(context.compaction_window ?? 0)}
            {context.model_window && context.compaction_window !== context.model_window ? ` (the model's window ${compact(context.model_window)})` : ""}</span>
        </div>
      )}
      {(context.compactions ?? 0) > 0 && (
        <div className={context.fragmented ? "text-wait" : "text-muted"}>
          {context.compactions} {context.compactions === 1 ? "compaction" : "compactions"}
          {context.fragmented ? ": fragmented, it finished on summaries of its own past" : ""}
          {context.retention != null && `; its last summary kept ${Math.round(context.retention * 100)}% of its first message's names`}
          {context.lost && context.lost.length > 0 && <span className="text-faint"> (lost: {context.lost.slice(0, 6).join(", ")}{context.lost.length > 6 ? ", ..." : ""})</span>}
        </div>
      )}
    </div>
  );
}

/** One call of a run's tree: its tool, what it names, how long it took, and the sub-agent it started under it; the
 *  control view's map draws a session's calls with it too (ControlView.tsx). */
export function Tool({ tool, depth }: { tool: TreeTool; depth: number }) {
  return (
    <li>
      <div className="flex items-baseline gap-1.5 text-[12px]" style={{ paddingLeft: depth * 14 }}>
        <CornerDownRight size={10} className="flex-none text-faint" />
        <span className={`font-mono ${tool.error ? "text-fail" : ""}`}>{tool.name}</span>
        {tool.error && <span className="sr-only">(failed)</span>}
        <span className="min-w-0 flex-1 truncate text-faint" title={tool.target}>{tool.target}</span>
        {tool.ms != null && <span className="flex-none font-mono text-[11px] text-faint">{duration(tool.ms / 1000) || `${tool.ms} ms`}</span>}
      </div>
      {tool.agent && <Agent agent={tool.agent} depth={depth + 1} />}
    </li>
  );
}

function Agent({ agent, depth }: { agent: TreeAgent; depth: number }) {
  return (
    <div style={{ paddingLeft: depth * 14 }} className="my-0.5">
      <div className="flex items-center gap-1.5 text-[11.5px] text-muted">
        <Bot size={11} className="text-accent" /> a sub-agent{agent.type ? ` (${agent.type})` : ""}{agent.description ? `: ${agent.description}` : ""}
        <span className="font-mono text-faint">{agent.requests} req, {compact(tokenCount(agent.tokens))} tok</span>
      </div>
      <ul>{agent.tools.map((t, i) => <Tool key={t.id ?? i} tool={t} depth={1} />)}</ul>
      {agent.more ? <div className="pl-4 text-[11px] text-faint">and {agent.more} more calls</div> : null}
    </div>
  );
}

/** A run's tree: each message to it, the calls it made, and the sub-agents it started under the call that started
 *  them. The app's copy, kept at the run's end, else read from the running workspace. */
export function RunTree({ runId }: { runId: string }) {
  const [turns, setTurns] = useState<TreeTurn[] | null>(null);
  const [copy, setCopy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let gone = false;
    AgentsApi.runTree(runId).then((got) => { if (!gone) { setTurns(got.tree.turns); setCopy(got.copy); } })
      .catch((e) => { if (!gone) setError(e instanceof Error ? e.message : String(e)); });
    return () => { gone = true; };
  }, [runId]);
  if (error) return <div className="m-auto max-w-[380px] px-6 py-10 text-center text-[12.5px] text-muted">{error}</div>;
  if (!turns) return <div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>;
  return (
    <div className="h-full overflow-y-auto px-4 py-3" aria-label="Its tree">
      {turns.map((turn, i) => (
        <div key={i} className="mb-3">
          <div className="flex items-baseline gap-2 text-[12.5px]">
            <span className="font-medium">{i === 0 ? "Its first message" : "A message"}</span>
            <span className="min-w-0 flex-1 truncate text-muted">{turn.text}</span>
            <span className="flex-none font-mono text-[11px] text-faint">{turn.requests} req, {compact(tokenCount(turn.tokens))} tok</span>
          </div>
          <ul className="mt-0.5">{turn.tools.map((t, j) => <Tool key={t.id ?? j} tool={t} depth={0} />)}</ul>
          {turn.more ? <div className="text-[11px] text-faint">and {turn.more} more calls</div> : null}
        </div>
      ))}
      <div className="text-[11px] text-faint">{copy ? "Kept at the run's end." : "Read from its transcript as it runs."}</div>
    </div>
  );
}
