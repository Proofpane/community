import { useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";
import { AgentsApi, duration, RULE_WORDS, type Agent, type AgentWindow, type AnomalyRule, type JudgeTrust, type QualityWindow } from "../agents";
import { usd } from "./bits";

const ms = (v: number | null) => (v == null ? "" : v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}s` : `${Math.round(v)}ms`);
const pct = (v: number | null) => (v == null ? "none yet" : `${Math.round(v * 100)}%`);
const secs = (v: number | null) => (v == null ? "" : duration(Math.round(v)) || "0s");

/** An agent's numbers over its last 7 or 30 days, from the days the app added up as its runs ended: where its time
 *  went, its tool calls by tool, its work with you and with other agents, its cache, and its retrieval (a proxy). */
export function AgentNumbersPanel({ agent }: { agent: Agent }) {
  const [days, setDays] = useState<7 | 30>(7);
  const [got, setGot] = useState<AgentWindow | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let gone = false;
    setGot(null);
    setError("");
    AgentsApi.numbers(agent.id, days).then((w) => { if (!gone) setGot(w && w.runs && w.time ? w : null); })
      .catch((e) => { if (!gone) setError(e instanceof Error ? e.message : String(e)); });
    return () => { gone = true; };
  }, [agent.id, days, agent.totals.runs]);
  const t = got?.time;
  return (
    <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]" role="group" aria-label={`${agent.name}'s numbers`}>
      <div className="flex items-center gap-2">
        <BarChart3 size={12} className="text-muted" />
        <span className="font-medium">Numbers</span>
        <span className="ml-auto flex gap-1" role="tablist" aria-label="Over">
          {([7, 30] as const).map((d) => (
            <button key={d} role="tab" aria-selected={days === d} className={`btn btn-sm !h-6 text-[11px] ${days === d ? "btn-primary" : ""}`}
              onClick={() => setDays(d)}>{d} days</button>
          ))}
        </span>
      </div>
      {error && <div className="mt-1 text-[11.5px] text-fail">{error}</div>}
      {!got && !error && <div className="flex justify-center py-3 text-muted"><Loader2 size={14} className="animate-spin" /></div>}
      {got && got.runs.runs === 0 && <div className="mt-1 text-[11.5px] text-faint">No run ended in the last {days} days.</div>}
      {got && t && got.runs.runs > 0 && (
        <div className="mt-1.5 space-y-2">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3" aria-label="Its time">
            <Pair k="Runs" v={`${got.runs.runs}: ${got.runs.done} done, ${got.runs.failed} failed${got.runs.blocked ? `, ${got.runs.blocked} blocked` : ""}${got.runs.stopped ? `, ${got.runs.stopped} stopped` : ""}`} />
            <Pair k="Success" v={`${pct(got.success_rate)}${got.cost_per_success != null ? `, ${usd(got.cost_per_success)} a success` : ""}`} />
            <Pair k="A run, p50 / p95" v={`${secs(t.wall_p50_s)} / ${secs(t.wall_p95_s)}`} title="Read from the days' counts on fixed buckets" />
            <Pair k="Working" v={secs(t.active_s)} title="Its turns' own time" />
            <Pair k="The model" v={secs(t.model_s)} />
            <Pair k="Its tools" v={secs(t.tool_s)} title="Calls at once counted once, less the time you were asked" />
            <Pair k="Waiting for you" v={secs(t.waiting_owner_s)} />
            <Pair k="In the queue" v={secs(t.queue_wait_s)} />
            <Pair k="Est. cost" v={usd(got.cost_usd)} />
          </dl>
          {got.tools.length > 0 && (
            <table className="w-full text-[11.5px]" aria-label="Its tool calls">
              <thead className="text-faint"><tr><th className="text-left font-normal">Tool</th><th className="text-right font-normal">calls</th>
                <th className="text-right font-normal">errors</th><th className="text-right font-normal">refused</th>
                <th className="text-right font-normal">p50</th><th className="text-right font-normal">p95</th></tr></thead>
              <tbody className="font-mono">
                {got.tools.map((x) => (
                  <tr key={x.name}><td className="max-w-[180px] truncate" title={x.name}>{x.name}</td><td className="text-right">{x.calls}</td>
                    <td className={`text-right ${x.errors ? "text-fail" : ""}`}>{x.errors}</td><td className={`text-right ${x.refused ? "text-wait" : ""}`}>{x.refused}</td>
                    <td className="text-right">{ms(x.p50_ms)}</td><td className="text-right">{ms(x.p95_ms)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3" aria-label="With you and other agents">
            <Pair k="Your turns" v={String(got.together.owner_turns)} />
            <Pair k="Its questions" v={`${got.together.questions}, ${got.together.answered} answered`} />
            <Pair k="Permission asks" v={`${got.together.permission_asks}: ${got.together.granted} yes, ${got.together.denied} no`} />
            <Pair k="Runs it started" v={String(got.together.dispatches_sent)} />
            <Pair k="Started by a run" v={String(got.together.dispatches_received)} />
            <Pair k="Messages sent / received" v={`${got.together.messages_sent} / ${got.together.messages_received}`} />
            <Pair k="Agents worked with" v={String(got.together.collaborators)} />
            <Pair k="Cache" v={pct(got.cache.ratio)} title="Cache reads over all input tokens, as the status line counts them" />
            <Pair k="Requests reading no cache" v={`${got.cache.cold_requests} of ${got.cache.requests}`} />
          </dl>
          <div className="text-[11px] text-faint" aria-label="Its retrieval">
            Retrieval, a proxy: {got.retrieval.calls} calls surfaced {got.retrieval.surfaced} sources; its last words named
            {" "}{got.retrieval.cited} of them{got.retrieval.read_then_cited != null ? ` (${pct(got.retrieval.read_then_cited)})` : ""}.
          </div>
          {got.quality && <QualityPart q={got.quality} judging={!!agent.judge_runs} />}
        </div>
      )}
    </div>
  );
}

const BAND_WORDS: Record<NonNullable<JudgeTrust["band"]>, string> = {
  trusted: "trusted: its pass rate tells drift",
  tentative: "tentative: shown, drives nothing",
  greyed: "not trusted: it disagrees with you too often",
  unvaried: "not weighed yet: every run you labelled, and its every grade, went one way",
};
const two = (v: number | null | undefined) => (v == null ? "?" : v.toFixed(2));

/** The agent's quality over the window: the judge's verdicts and how far they agree
 *  with your thumbs, drift once that judge is trusted, its acceptance checks, the live rules it met, its words'
 *  checks, how often it needed you, its servers' health, the runs it led, its replays, and what measuring it cost. */
function QualityPart({ q, judging }: { q: QualityWindow; judging: boolean }) {
  const [trust, setTrust] = useState<JudgeTrust | null>(null);
  useEffect(() => {
    let gone = false;
    if (!q.judge_version) { setTrust(null); return; }
    AgentsApi.judgeTrust().then((all) => { if (!gone) setTrust(all[q.judge_version!] ?? null); }).catch(() => { /* shown without it */ });
    return () => { gone = true; };
  }, [q.judge_version]);
  const rules = Object.entries(q.anomalies.by_rule) as [AnomalyRule, number][];
  const health = q.mcp_health;
  const servers = [health.needs_auth && `${health.needs_auth} needing a sign-in`, health.failed && `${health.failed} failed`,
                   health.disabled && `${health.disabled} off`, health.tool_changes && `${health.tool_changes} tool changes`].filter(Boolean);
  const judgeTitle = trust && trust.labelled
    ? `Over ${trust.labelled} runs you labelled: it agrees ${trust.agreement != null ? pct(trust.agreement) : "?"}` +
      `${trust.agreement_interval ? ` (95%: ${pct(trust.agreement_interval[0])} to ${pct(trust.agreement_interval[1])})` : ""}; AC1 ${two(trust.ac1)}, ` +
      `alpha ${two(trust.alpha)}${trust.alpha_interval ? ` (95%: ${two(trust.alpha_interval[0])} to ${two(trust.alpha_interval[1])})` : ""}`
    : "Weighed against your thumbs once you have labelled 20 of its graded runs";
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-line pt-2 sm:grid-cols-3" aria-label="Its quality">
      <Pair k="Judged" v={q.judged ? `${q.judged}, ${pct(q.judge_pass_rate)} passed` : judging ? "none yet" : "off"}
        title={judging ? "A judge grades a tenth of its runs and each one you label" : "Turn on the judge in its settings: each graded run costs a call"} />
      {(q.judged > 0 || q.judge_band) && <Pair k="The judge" v={q.judge_band ? BAND_WORDS[q.judge_band] : "too few of your labels yet"} title={judgeTitle} />}
      {q.drift.level && q.drift.current != null && q.drift.baseline != null && (
        <Pair k="Drift" v={`${q.drift.level}: ${pct(q.drift.current)}, from ${pct(q.drift.baseline)}`}
          title="Its last 20 graded runs against the 60 before them" />
      )}
      <Pair k="Its checks" v={q.acceptance.checked ? `${q.acceptance.passed} of ${q.acceptance.checked} passed` : "none set"} />
      <Pair k="Rules met" v={q.anomalies.warning + q.anomalies.critical ? `${q.anomalies.warning} warning, ${q.anomalies.critical} critical` : "none"}
        title={rules.map(([r, n]) => `${RULE_WORDS[r] ?? r}: ${n}`).join("\n") || undefined} />
      <Pair k="Claims unbacked" v={String(q.claims_flagged)} title="Runs whose last words claim a status (validated, approved, ...) no tool result of the run backs" />
      <Pair k="Assumed premises" v={String(q.premise_flagged)} title="Runs asked to assume a status and write the record of it" />
      <Pair k="Unattended" v={pct(q.autonomy.unattended_share)} title="Runs with no question, no permission ask and no turn of yours" />
      <Pair k="You, a run" v={q.autonomy.interventions_per_run != null ? q.autonomy.interventions_per_run.toFixed(2) : ""}
        title="Its questions, its permission asks and your other turns, each run" />
      <Pair k="Its servers" v={servers.length ? servers.join(", ") : "all well"} title="Counted in runs, from what Claude Code said at each run's start" />
      {q.dispatch.coordinating_runs > 0 && (
        <Pair k="Runs it led" v={`${q.dispatch.coordinating_runs}: up to ${q.dispatch.most_fan_out} started, ${q.dispatch.deepest} deep, ${secs(q.dispatch.waited_s)} waiting`} />
      )}
      <Pair k="Replays" v={q.replays.replays ? `${q.replays.replays}${q.replays.agreement != null ? `, ${pct(q.replays.agreement)} agree` : ""}` : "none"}
        title="A lower bound on its consistency: a replay differs from its run in the model's chance and in all outside its worktree" />
      <Pair k="Measuring it" v={`${usd(q.cost_of_improvement.usd)}${q.cost_of_improvement.share != null ? ` (${pct(q.cost_of_improvement.share)})` : ""}`}
        title="Its judge's calls, evals, replays and re-verify runs, as a share of all it cost" />
    </dl>
  );
}

function Pair({ k, v, title }: { k: string; v: string; title?: string }) {
  return (
    <div className="flex items-baseline gap-1.5" title={title}>
      <dt className="text-faint">{k}</dt>
      <dd className="font-mono">{v}</dd>
    </div>
  );
}
