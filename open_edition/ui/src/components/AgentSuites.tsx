import { useCallback, useEffect, useState } from "react";
import { ListChecks, Loader2, Play, Plus, Trash2 } from "lucide-react";
import { ago, AgentsApi, type Agent, type Suite, type SuiteItem } from "../agents";
import { useStore } from "../store";

const blank = (): SuiteItem => ({ question: "", sources: [], group: "rag", held_out: false });
const score = (v: number | null) => (v == null ? "none" : v.toFixed(2));

/** An agent's suites: questions, each with the sources that answer it. An eval asks each question alone, as a fresh
 *  run of the agent, and scores where those sources first appeared in what its tools showed: hit@5 and MRR, an order
 *  of appearance and not a retriever's ranking. The suites stay in the app; a run never sees the answers. */
export function AgentSuites({ agent }: { agent: Agent }) {
  const setError = useStore((s) => s.setError);
  const [suites, setSuites] = useState<Suite[] | null>(null);
  const [editing, setEditing] = useState<{ id: string | null; name: string; items: SuiteItem[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const got = await AgentsApi.suites(agent.id);
      setSuites(Array.isArray(got) ? got : []);
    } catch (e) { setError(e); }
  }, [agent.id, setError]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!suites?.some((s) => s.evals.some((e) => e.open > 0))) return;
    const t = setInterval(() => void load(), 10000);       // an eval works: its scores come in as its runs end
    return () => clearInterval(t);
  }, [suites, load]);
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await load(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const save = () => editing && go(async () => {
    const items = editing.items.filter((i) => i.question.trim());
    if (editing.id) await AgentsApi.changeSuite(editing.id, { name: editing.name, items });
    else await AgentsApi.newSuite(agent.id, editing.name, items);
    setEditing(null);
  });
  const item = (n: number, change: Partial<SuiteItem>) => editing && setEditing({ ...editing, items: editing.items.map((x, i) => (i === n ? { ...x, ...change } : x)) });

  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s suites`}>
      <div className="flex items-center gap-2">
        <ListChecks size={12} className="text-muted" />
        <span className="font-medium">Suites</span>
        <span className="text-[11.5px] text-faint">questions it should answer from the right sources</span>
        {!agent.retired && !editing && (
          <button className="btn btn-sm !h-6 ml-auto text-[11px]" onClick={() => setEditing({ id: null, name: "", items: [blank()] })}>
            <Plus size={11} /> Suite
          </button>
        )}
      </div>
      {suites === null && <div className="flex justify-center py-2 text-muted"><Loader2 size={13} className="animate-spin" /></div>}
      {suites?.length === 0 && !editing && <div className="mt-1 text-[11.5px] text-faint">None yet.</div>}
      <ul className="mt-1 space-y-1">
        {suites?.map((s) => {
          const last = s.evals[0];
          return (
            <li key={s.id} className="rounded-md border border-line px-2 py-1">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium">{s.name}</span>
                <span className="text-[11px] text-faint">{s.items.length} {s.items.length === 1 ? "question" : "questions"}
                  {s.items.some((i) => i.held_out) ? `, ${s.items.filter((i) => i.held_out).length} held out` : ""}</span>
                <span className="ml-auto flex items-center gap-1">
                  <button className="btn btn-sm !h-6 text-[11px]" disabled={busy || agent.retired || agent.paused} aria-label={`Run ${s.name}`}
                    onClick={() => void go(() => AgentsApi.runEval(s.id))}><Play size={11} /> Run</button>
                  {s.items.some((i) => i.held_out) && (
                    <button className="btn btn-sm !h-6 text-[11px]" disabled={busy || agent.retired || agent.paused}
                      onClick={() => void go(() => AgentsApi.runEval(s.id, true))}>Held out</button>
                  )}
                  <button className="btn btn-sm !h-6 text-[11px]" disabled={busy} onClick={() => setEditing({ id: s.id, name: s.name, items: s.items.map((i) => ({ ...i })) })}>Edit</button>
                  <button className="icon-btn !h-6 !w-6" disabled={busy} aria-label={`Remove ${s.name}`} title="Remove"
                    onClick={() => void go(() => AgentsApi.removeSuite(s.id))}><Trash2 size={12} /></button>
                </span>
              </div>
              {last && (
                <div className="text-[11.5px]" aria-label={`${s.name}'s last eval`}>
                  <span className="text-muted">Last {last.held_out ? "held-out " : ""}eval {ago(last.started_at)}: </span>
                  {last.scored > 0 ? (
                    <span className="font-mono">
                      {last.hit_at_5 != null && `hit@5 ${score(last.hit_at_5)}, MRR ${score(last.mrr)}`}
                      {last.hit_at_5 != null && last.stale_accuracy != null && "; "}
                      {last.stale_accuracy != null && `stale ${score(last.stale_accuracy)}`}
                    </span>
                  ) : <span className="text-faint">no score yet</span>}
                  <span className="text-faint"> ({last.scored} of {last.items} scored{last.open ? `, ${last.open} to go` : ""}{last.failed ? `, ${last.failed} failed` : ""})</span>
                  {Object.keys(last.by_group).length > 1 && (
                    <span className="text-faint"> · {Object.entries(last.by_group).map(([g, v]) => `${g} ${score(v.hit_at_5)}`).join(", ")}</span>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {editing && (
        <form className="mt-2 space-y-2 rounded-md border border-line p-2" aria-label={editing.id ? "Edit the suite" : "A new suite"}
          onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <input aria-label="The suite's name" className="input !h-7 w-full text-[12px]" placeholder="Where things are" maxLength={80}
            value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          {editing.items.map((x, n) => (
            <div key={n} className="space-y-1 rounded-md border border-line p-1.5">
              <textarea aria-label={`Question ${n + 1}`} className="input min-h-[44px] w-full !py-1 text-[12px]" maxLength={2000} placeholder="Where are the labels decided?"
                value={x.question} onChange={(e) => item(n, { question: e.target.value })} />
              {x.group === "stale" ? (
                <div className="grid gap-1 sm:grid-cols-2">
                  <input aria-label={`The current answer to question ${n + 1}`} className="input !h-7 text-[12px]" maxLength={500}
                    placeholder="the current value" value={x.answer ?? ""} onChange={(e) => item(n, { answer: e.target.value })} />
                  <input aria-label={`The superseded values for question ${n + 1}, comma separated`} className="input !h-7 text-[12px]"
                    placeholder="values it should no longer give" value={(x.superseded ?? []).join(", ")}
                    onChange={(e) => item(n, { superseded: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
                </div>
              ) : (
                <textarea aria-label={`The sources that answer question ${n + 1}, one a line`} className="input min-h-[36px] w-full !py-1 font-mono text-[11.5px]"
                  placeholder={"src/triage/rules.py\ndocs/labels.md"} value={x.sources.join("\n")}
                  onChange={(e) => item(n, { sources: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
              )}
              <div className="flex items-center gap-3 text-[11.5px] text-muted">
                <label className="flex items-center gap-1">Group
                  <select className="input !h-6 !py-0 text-[11.5px]" value={x.group} onChange={(e) => item(n, { group: e.target.value as SuiteItem["group"] })}>
                    <option value="rag">rag</option><option value="stale">stale</option>
                  </select>
                </label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={x.held_out} onChange={(e) => item(n, { held_out: e.target.checked })} /> held out</label>
                <button type="button" className="icon-btn !h-6 !w-6 ml-auto" aria-label={`Remove question ${n + 1}`}
                  onClick={() => setEditing({ ...editing, items: editing.items.filter((_, i) => i !== n) })}><Trash2 size={11} /></button>
              </div>
            </div>
          ))}
          <div className="flex items-center gap-2">
            <button type="button" className="btn btn-sm !h-6 text-[11px]" disabled={editing.items.length >= 50}
              onClick={() => setEditing({ ...editing, items: [...editing.items, blank()] })}><Plus size={11} /> Question</button>
            <span className="ml-auto flex gap-1">
              <button type="button" className="btn btn-sm !h-6 text-[11px]" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn btn-sm btn-primary !h-6 text-[11px]" disabled={busy || !editing.name.trim()}>Save</button>
            </span>
          </div>
        </form>
      )}
    </div>
  );
}
