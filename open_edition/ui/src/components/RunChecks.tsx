import { RULE_WORDS, type AgentRun } from "../agents";

const pct = (v: number) => `${Math.round(v * 100)}%`;
const DIMENSION_WORDS: Record<string, string> = {
  task_fit: "did what was asked", grounding: "claims backed", completeness: "says what is left",
  hygiene: "no secrets, nothing outside its brief", actionable: "you can act on it",
};

/** What was checked of a run at its end: the judge's verdict, its acceptance checks,
 *  the live rules it met, and its words' checks (a claim with nothing behind it, an assumed premise). Nothing when
 *  none of them has anything to say. */
export function RunChecksLine({ run }: { run: AgentRun }) {
  const j = run.judge;
  const a = run.acceptance;
  const met = run.anomalies ?? [];
  const claims = run.metrics?.claims;
  const premise = run.metrics?.premise;
  const critical = met.filter((x) => x.level === "critical");
  if (!j && !run.judge_error && !a && !met.length && !claims?.flagged && !premise?.flagged) return null;
  const rules = Array.from(new Set(met.map((x) => x.rule)));
  return (
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px]" aria-label="Its checks">
      {j && (
        <span className={j.pass ? "text-done" : "text-fail"}
          title={Object.entries(j.scores).map(([k, v]) => `${DIMENSION_WORDS[k] ?? k}: ${pct(v)}`).join("\n") + (j.why ? `\n\n${j.why}` : "")}>
          Judge: {j.pass ? "passed" : "failed"}, {pct(j.score)} at its lowest
        </span>
      )}
      {!j && run.judge_error && (
        <span className="text-muted" title={run.judge_error}>The judge gave no verdict: {run.judge_error}</span>
      )}
      {a && (
        <span className={a.pass ? "text-done" : "text-fail"} title={a.failures.join("\n") || undefined}>
          {a.pass ? "Its checks passed" : `Its checks failed: ${a.failures.slice(0, 2).join("; ")}${a.failures.length > 2 ? "…" : ""}`}
          {a.retried ? (a.pass ? ", on its second try" : ", twice") : ""}
        </span>
      )}
      {met.length > 0 && (
        <span className={critical.length ? "text-fail" : "text-wait"} title={met.map((x) => `${x.level}: ${x.evidence ?? x.rule}`).join("\n")}>
          {critical.length ? "Met a rule's critical level" : "Met a rule"}: {rules.map((r) => RULE_WORDS[r] ?? r).join(", ")}
        </span>
      )}
      {claims?.flagged && (
        <span className="text-wait" title={(claims.standing ?? []).map((s) => s.text).join("\n") || undefined}>
          It claims {(claims.standing ?? []).map((s) => `"${s.word}"`).slice(0, 2).join(", ") || "a status"} with nothing behind it in the run
        </span>
      )}
      {premise?.flagged && (
        <span className="text-wait" title="Its first message asked it to assume a status and write the record of it">
          Asked to assume a status and write it up
        </span>
      )}
    </div>
  );
}
