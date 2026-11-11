import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, Loader2, X } from "lucide-react";
import { AgentsApi, type Agent, type Fact, type Facts } from "../agents";
import { useStore } from "../store";
import { day as dayOf } from "../when";

const day = (unix: number | null) => (unix != null ? dayOf(unix) : "");

/** An agent's facts: what its runs recorded, each with when it became true and whether it was seen (observed) or only
 *  said (told). A later value of a one-at-a-time relation retires the earlier; a told value never retires an observed
 *  one, and the two are shown as contradicting until a run or the owner decides. Nothing is deleted. */
export function AgentFacts({ agent, onChanged }: { agent: Agent; onChanged: () => Promise<void> }) {
  const setError = useStore((s) => s.setError);
  const [facts, setFacts] = useState<Facts | null>(null);
  const [retracting, setRetracting] = useState<Fact | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const got = await AgentsApi.facts(agent.id);
      setFacts(got && Array.isArray(got.current) ? got : { current: [], contradictions: [], retired: [] });
    } catch (e) { setError(e); }
  }, [agent.id, setError]);
  useEffect(() => { void load(); }, [load]);
  const relations = useMemo(() => Array.from(new Set((facts?.current ?? []).concat(facts?.retired ?? []).map((f) => f.relation))).sort(), [facts]);
  const kinds = agent.relations ?? {};
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await load(); await onChanged(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const byId = new Map((facts?.current ?? []).map((f) => [f.id, f]));

  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s facts`}>
      <div className="flex items-center gap-2">
        <BookOpen size={12} className="text-muted" />
        <span className="font-medium">Facts</span>
        <span className="text-[11.5px] text-faint">what its runs recorded, observed or told</span>
      </div>
      {facts === null && <div className="flex justify-center py-2 text-muted"><Loader2 size={13} className="animate-spin" /></div>}
      {facts?.current.length === 0 && facts.retired.length === 0 && <div className="mt-1 text-[11.5px] text-faint">None yet.</div>}
      {facts && facts.current.length > 0 && (
        <ul className="mt-1 space-y-0.5" aria-label="Its current facts">
          {facts.current.map((f) => (
            <li key={f.id} className={`flex flex-wrap items-baseline gap-x-1.5 ${f.contradicts ? "text-wait" : ""}`}>
              <span className="text-muted">{f.subject}</span><span className="text-faint">{f.relation}</span>
              <span className="font-medium">{f.object}</span>
              <span className={`rounded px-1 text-[10.5px] ${f.basis === "told" ? "bg-wait/10 text-wait" : "bg-done/10 text-done"}`}
                title={f.basis === "told" ? "Said, not seen in a tool result: a claim" : "Seen in a tool result of the run"}>{f.basis}</span>
              <span className="text-[11px] text-faint">since {day(f.valid_at)}</span>
              {f.contradicts && byId.get(f.contradicts) && <span className="text-[11px]">contradicts {byId.get(f.contradicts)!.object}</span>}
              {!agent.retired && (
                <button className="icon-btn !h-5 !w-5 ml-auto" disabled={busy} aria-label={`Retract ${f.subject} ${f.relation} ${f.object}`}
                  title="Retract it" onClick={() => { setRetracting(f); setReason(""); }}><X size={11} /></button>
              )}
            </li>
          ))}
        </ul>
      )}
      {retracting && (
        <form className="mt-1 flex gap-1" aria-label="Retract a fact"
          onSubmit={(e) => { e.preventDefault(); void go(async () => { await AgentsApi.retractFact(agent.id, retracting.id, reason); setRetracting(null); }); }}>
          <input className="input !h-7 flex-1 text-[12px]" aria-label="Why it is wrong" placeholder={`Why ${retracting.object} is wrong`}
            value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
          <button className="btn btn-sm !h-7" disabled={busy}>Retract</button>
          <button type="button" className="btn btn-sm !h-7" onClick={() => setRetracting(null)}>Cancel</button>
        </form>
      )}
      {relations.length > 0 && !agent.retired && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted" aria-label="Its relations">
          {relations.map((r) => (
            <label key={r} className="flex items-center gap-1" title="One value at a time: a later one retires the earlier">
              <input type="checkbox" checked={kinds[r] === "functional"} disabled={busy}
                onChange={(e) => void go(() => AgentsApi.update(agent.id, { relations: { ...kinds, [r]: e.target.checked ? "functional" : "multi" } }))} />
              {r}: one at a time
            </label>
          ))}
        </div>
      )}
      {facts && facts.retired.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11.5px] text-muted">{facts.retired.length} retired</summary>
          <ul className="mt-0.5 space-y-0.5 text-[11.5px] text-faint" aria-label="Its retired facts">
            {facts.retired.map((f) => (
              <li key={f.id}>
                {f.subject} {f.relation} <span className="line-through">{f.object}</span> ({day(f.valid_at)} to {day(f.invalid_at ?? f.expired_at)})
                {f.retracted ? `: retracted, ${f.retracted}` : f.superseded_by ? ": superseded" : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
