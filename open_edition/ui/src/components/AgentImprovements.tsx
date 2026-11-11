import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader2, Sparkles, X } from "lucide-react";
import { AgentsApi, type Improvement, type Improvements } from "../agents";
import {
  PROPOSAL_STATE_WORDS, proposalCountWords, proposalNotes, SkillImproveApi, startKindWords, WAY_WORDS, type LoopBudget,
  type ProposalState, type ProposalWay, type RecordState, type SkillLoop, type SkillProposal,
} from "../skills";
import { usd } from "./bits";

const STATE_WORDS: Record<Improvement["state"], string> = {
  refused: "refused", pruned: "pruned", critic: "with the critic", waiting: "waiting for its sandbox",
  sandbox: "in its sandbox", not_evaluated: "not evaluated", not_eligible: "not better", eligible: "for you to decide",
  approved: "approved", rejected: "rejected", expired: "expired", rolled_back: "rolled back",
};
const TARGET_WORDS: Record<Improvement["target"], string> = {
  brief: "its brief", skill: "a skill", effort: "its effort", autocompact_window: "its compaction point", model: "its model",
  review_prompt: "the review prompt",
};

/** A run's est. cost, to four places: the card compares two of them. */
const perRun = (n: number) => `$${n.toFixed(4)}`;

/** How long a proposal waits for the owner before it expires. */
export function daysLeft(expiresAt: number, now = Date.now() / 1000): string {
  const days = Math.ceil((expiresAt - now) / 86400);
  return days <= 1 ? "expires within a day" : `expires in ${days} days`;
}

function what(p: Improvement, names: Map<string, string>): string {
  const target = p.target === "skill" ? `its skill ${p.name}` : TARGET_WORDS[p.target];
  if (p.kind === "meta") return "A change to the review prompt";
  const agent = (p.agent_id && names.get(p.agent_id)) || "An agent";
  return p.kind === "rollback" ? `${agent}: undo its last change to ${target}?` : `${agent}: a change to ${target}`;
}

/** What the sandbox found, or why a rollback is proposed, in words. */
export function evidenceWords(p: Improvement): string {
  const r = p.result;
  if (!r) return p.kind === "meta" ? "A change to how reviews are asked: it is not tried in a sandbox." : "";
  if (p.kind === "rollback") {
    return `Since the change, its first 20 graded runs passed ${r.after} times; the 20 before it, ${r.before}` +
      (r.p != null ? ` (p ${r.p.toFixed(3)}).` : ".");
  }
  const parts = [`On the runs it was drawn from, ${r.wins ?? 0} passed with it where they failed without, ` +
    `${r.losses ?? 0} the other way` + (r.p != null ? ` (p ${r.p.toFixed(3)})` : "") + "."];
  parts.push(`${r.regressions ?? 0} of the runs that passed before failed with it.`);
  if (r.cost_control != null && r.cost_treatment != null) {
    parts.push(`A run costs ${perRun(r.cost_treatment)} est. with it, ${perRun(r.cost_control)} without.`);
  }
  return parts.join(" ");
}

/** The improve loop's proposals: the ones for the owner to decide, each with its change, what its sandbox found and
 *  what it cost; then what became of the others, and the week's budget. Nothing changes without the owner's tap. */
export function AgentImprovements({ names, onChanged }: { names: Map<string, string>; onChanged: () => Promise<void> }) {
  const [got, setGot] = useState<Improvements | null>(null);
  const [busy, setBusy] = useState("");
  const [why, setWhy] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const load = useCallback(async (): Promise<Improvements | null> => {
    try {
      const read = await AgentsApi.improvements();
      setGot(read);
      return read;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (!got) return null;
  const open = got.proposals.filter((p) => p.state === "eligible");
  const rest = got.proposals.filter((p) => p.state !== "eligible");
  if (!open.length && !rest.length) return null;
  const decide = async (p: Improvement, approve: boolean) => {
    setBusy(p.id + (approve ? ":yes" : ":no"));
    setError("");
    try {
      if (approve) await AgentsApi.approveImprovement(p.id);
      else await AgentsApi.rejectImprovement(p.id, why[p.id] ?? "");
      await load();
      await onChanged();
    } catch (e) {
      const said = e instanceof Error ? e.message : String(e);
      // a tap the app refused can still decide the proposal (refused for the agent's skills): read again, say so
      const after = (await load())?.proposals.find((x) => x.id === p.id);
      setError(approve && after?.state === "refused" && after.reason ? `Refused: ${after.reason}.` : said);
    } finally {
      setBusy("");
    }
  };
  const b = got.budget;
  return (
    <div className="border-b border-line px-3 py-2.5 text-[12px]" role="region" aria-label="Improvements">
      <div className="flex items-center gap-2">
        <Sparkles size={13} className="text-accent" />
        <span className="font-medium">Improvements</span>
        {open.length > 0 && <span className="text-wait">{open.length} for you to decide</span>}
        <span className="ml-auto text-[11px] text-faint" title="The loop's own budget for the week: its reviews, critics and replays, and their est. cost">
          This week: {b.sessions} of {b.max_sessions} sessions, {usd(b.usd)} of {usd(b.max_usd)}
        </span>
      </div>
      {error && <div className="mt-1 text-[11.5px] text-fail">{error}</div>}
      {open.map((p) => (
        <div key={p.id} className="mt-2 rounded-md border border-wait/50 bg-wait/[0.04] px-3 py-2" aria-label={what(p, names)}>
          <div className="font-medium">{what(p, names)}</div>
          {p.hypothesis && <div className="mt-0.5 text-muted">{p.hypothesis}</div>}
          <pre className="mt-1.5 max-h-[220px] overflow-auto rounded bg-black/20 px-2 py-1 font-mono text-[11px] leading-[1.45]" aria-label="The change">
            {p.diff.map((line, n) => (
              <div key={n} className={line.startsWith("+") ? "text-done" : line.startsWith("-") ? "text-fail" : line.startsWith("@") ? "text-faint" : ""}>{line || " "}</div>
            ))}
          </pre>
          <div className="mt-1 text-[11.5px] text-muted">{evidenceWords(p)}</div>
          {(p.evidence.runs ?? []).length > 0 && (
            <div className="mt-0.5 font-mono text-[11px] text-faint">Drawn from {(p.evidence.runs ?? []).length} runs{p.evidence.metric ? `, for ${p.evidence.metric}` : ""}</div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <button className="btn btn-sm btn-primary" disabled={!!busy} onClick={() => void decide(p, true)}>
              {busy === p.id + ":yes" ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} {p.kind === "rollback" ? "Undo it" : "Make the change"}
            </button>
            <input className="input !h-7 min-w-[120px] flex-1 text-[12px]" placeholder="Why not, for the record" maxLength={300}
              aria-label="Why not" value={why[p.id] ?? ""} onChange={(e) => setWhy({ ...why, [p.id]: e.target.value })} />
            <button className="btn btn-sm" disabled={!!busy} onClick={() => void decide(p, false)}>
              {busy === p.id + ":no" ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />} No
            </button>
            {p.expires_at && <span className="text-[11px] text-faint">{daysLeft(p.expires_at)}</span>}
          </div>
        </div>
      ))}
      {rest.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11.5px] text-muted">What became of the others ({rest.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {rest.map((p) => (
              <li key={p.id} className="flex flex-wrap gap-x-2 text-[11.5px]">
                <span>{what(p, names)}</span>
                <span className={p.state === "approved" ? "text-done" : p.state === "sandbox" || p.state === "waiting" || p.state === "critic" ? "text-wait" : "text-faint"}>
                  {STATE_WORDS[p.state]}</span>
                {p.reason && <span className="text-faint">{p.reason}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// --- the business skills' proposals ------------------------------------

const DECIDE: ProposalState[] = ["open", "pull_request", "found", "outdated", "changed"];
const KIND_WORDS: Record<string, string> = { review: "reviews", critic: "critics", replay: "replays", skill_review: "skill reviews", skill_critic: "skill critics" };

/** The week's budget both loops share, by kind. */
export function budgetWords(b: LoopBudget): string {
  const parts = Object.entries(b.by_kind).map(([k, n]) => `${n} ${KIND_WORDS[k] ?? k}`);
  return `This week: ${b.sessions} of ${b.max_sessions} sessions (${parts.join(", ")}), ${usd(b.usd)} of ${usd(b.max_usd)}`;
}

function proposalTitle(p: SkillProposal): string {
  const what = p.kind === "back" ? "go back to the version before" : "a change";
  return `${p.repo}: ${what} to the skill ${p.folder}`;
}

/** One business proposal, as the list draws it: its repository, folder and branch, its ids and those it had before
 *  it was brought up to date, its whole diff from the two texts whose blobs the app checked, its counts at the current
 *  version and the one before, its hypothesis, its notes, and the taps its state allows, each the owner's own. */
export function BusinessProposal({ p, onDone }: { p: SkillProposal; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [why, setWhy] = useState("");
  const [error, setError] = useState("");
  const tap = async (way: ProposalWay) => {
    setBusy(way);
    setError("");
    try {
      await SkillImproveApi.act(p.id, way, way === "reject" ? { reason: why } : undefined);
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };
  const label = (way: ProposalWay) => (way === "merge" && p.state !== "pull_request" && p.branch
    ? `Merge into ${p.branch.replace(/^refs\/heads\//, "")}` : WAY_WORDS[way]);
  return (
    <div className="mt-2 rounded-md border border-wait/50 bg-wait/[0.04] px-3 py-2" aria-label={proposalTitle(p)}>
      <div className="font-medium">{proposalTitle(p)}</div>
      <div className="mt-0.5 text-[11.5px] text-muted">{PROPOSAL_STATE_WORDS[p.state]}{p.ended ? ` (its bar ended: ${p.ended})` : ""}</div>
      <div className="mt-0.5 break-all font-mono text-[11px] text-faint">
        {p.path} on {p.branch ?? "its branch"}; base {p.base?.slice(0, 12) ?? "none"}, head {p.head?.slice(0, 12) ?? "none"}
        {p.heads.length > 0 && `; before it was brought up to date: ${p.heads.map((h) => `${(h.base ?? "").slice(0, 12)}..${(h.head ?? "").slice(0, 12)}`).join(", ")}`}
      </div>
      {p.hypothesis && <div className="mt-1 text-muted">{p.hypothesis}</div>}
      <pre className="mt-1.5 max-h-[320px] overflow-auto rounded bg-black/20 px-2 py-1 font-mono text-[11px] leading-[1.45]" aria-label="The change">
        {p.diff.map((line, n) => (
          <div key={n} className={line.startsWith("+") ? "text-done" : line.startsWith("-") ? "text-fail" : line.startsWith("@") ? "text-faint" : ""}>{line || " "}</div>
        ))}
      </pre>
      {p.counts && (
        <ul className="mt-1 text-[11.5px] text-muted" aria-label="Its counts">
          <li>All its calls kept: {proposalCountWords(p.counts.whole)}</li>
          <li>At the current version: {proposalCountWords(p.counts.current)}</li>
          <li>At the version before: {proposalCountWords(p.counts.before)}</li>
        </ul>
      )}
      {p.pr_url && <div className="mt-1 break-all text-[11.5px]">Its pull request: {p.pr_url}</div>}
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-faint" aria-label="What to know about it">
        {proposalNotes(p).map((n) => <li key={n}>{n}</li>)}
      </ul>
      {error && <div className="mt-1 text-[11.5px] text-fail" role="alert">{error}</div>}
      {p.ways.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {p.ways.filter((w) => w !== "reject").map((w) => (
            <button key={w} className={`btn btn-sm ${w === "merge" ? "btn-primary" : ""}`} disabled={!!busy} onClick={() => void tap(w)}>
              {busy === w ? <Loader2 size={12} className="animate-spin" /> : w === "merge" ? <Check size={12} /> : null} {label(w)}
            </button>
          ))}
          {p.ways.includes("reject") && (
            <>
              <input className="input !h-7 min-w-[120px] flex-1 text-[12px]" placeholder="Why not, for the record" maxLength={300}
                aria-label="Why not" value={why} onChange={(e) => setWhy(e.target.value)} />
              <button className="btn btn-sm" disabled={!!busy} onClick={() => void tap("reject")}>
                {busy === "reject" ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />} Reject
              </button>
            </>
          )}
          {p.expires_at && <span className="text-[11px] text-faint">{daysLeft(p.expires_at)}</span>}
        </div>
      )}
    </div>
  );
}

/** The record of proposals held: every start the workspace makes unattended is refused, in every repository, until the
 *  owner restores it from the app's copy; the starts it refused are listed. */
export function HeldRecord({ record, onDone }: { record: RecordState; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const r = record.record;
  if (!r || r.state !== "held") return null;
  const restore = async () => {
    setBusy(true);
    setError("");
    try { await SkillImproveApi.restore(); await onDone(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="mt-2 rounded-md border border-fail/50 px-3 py-2 text-[11.5px]" role="alert" aria-label="The record of skill proposals is held">
      <div>The workspace's record of skill proposals was removed or changed by something else{r.held ? ` (${r.held})` : ""}: every start it makes unattended, in every repository, is refused until you restore it. A program a session ran can do this, since it runs as the workspace's own user.</div>
      <button className="btn btn-sm mt-1.5" disabled={busy} onClick={() => void restore()}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : null} Restore it from the app's copy
      </button>
      {error && <div className="mt-1 text-fail">{error}</div>}
      {r.refused.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-faint" aria-label="The starts refused">
          {r.refused.map((s, n) => <li key={n} className="break-words">{startKindWords(s.kind)} in {s.repo ?? "a repository"}: {s.why}</li>)}
        </ul>
      )}
    </div>
  );
}

/** The starts the bar refused since the workspace started (for a proposed change, or for want of the app's word), listed
 *  whenever there are any; while the record is held, HeldRecord lists them with the restore. */
export function RefusedStarts({ record }: { record: RecordState | null }) {
  const r = record?.record;
  if (!r || r.state === "held" || !r.refused.length) return null;
  return (
    <div className="mt-2 text-[11.5px]" role="region" aria-label="Starts the bar refused">
      <div className="text-muted">Starts the workspace made unattended and refused since it started:</div>
      <ul className="mt-1 space-y-0.5 text-faint">
        {r.refused.map((s, n) => <li key={n} className="break-words">{startKindWords(s.kind)} in {s.repo ?? "a repository"}: {s.why}</li>)}
      </ul>
    </div>
  );
}

/** The loop's list of business proposals, as the Skills view read it (with each of its rounds, and after each tap): every
 *  state, newest first, with the week's budget by kind; the ones for the owner to decide open, the rest under "What
 *  became of the others"; where the workspace holds its record, the restore; and the starts the bar refused. Each
 *  repository with a proposal for the owner to decide is viewed once while the list is shown (a proposal's own view,
 *  which looks at the branch's head first), its word on whether a start from that head is refused
 *  now kept for its proposals; what the look moved shows at the list's next round. Drawn nothing while there is no
 *  proposal, the record is not held and no start was refused. */
export function BusinessProposals({ loop: got, record, onDone }: { loop: SkillLoop; record: RecordState | null; onDone: () => Promise<void> }) {
  const [bars, setBars] = useState<Record<string, SkillProposal["bars_now"]>>({});
  const viewed = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const open = got.proposals.filter((p) => DECIDE.includes(p.state) && p.ended === null);
  const newest = new Map<string, string>();               // each repository's newest proposal to decide (the list is newest first)
  for (const p of open) if (!newest.has(p.repo)) newest.set(p.repo, p.id);
  const toView = [...newest.entries()].filter(([repo]) => !viewed.current.has(repo));
  const key = toView.map(([repo, id]) => `${repo}:${id}`).join(",");
  useEffect(() => {
    if (!toView.length) return;
    for (const [repo] of toView) viewed.current.add(repo);
    void (async () => {
      const views = await Promise.allSettled(toView.map(async ([repo, id]) => [repo, (await SkillImproveApi.proposal(id))?.bars_now ?? null] as const));
      if (!mounted.current) return;
      const seen = Object.fromEntries(views.flatMap((r) => (r.status === "fulfilled" ? [r.value] : [])));
      setBars((b) => ({ ...b, ...seen }));
    })();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const held = record?.record?.state === "held";
  if (!got.proposals.length && !held && !record?.record?.refused.length) return null;
  const rest = got.proposals.filter((p) => !open.includes(p));
  return (
    <div className="border-b border-line px-3 py-2.5 text-[12px]" role="region" aria-label="Business skill proposals">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles size={13} className="text-accent" />
        <span className="font-medium">Business skills</span>
        {open.length > 0 && <span className="text-wait">{open.length} for you to decide</span>}
        <span className="ml-auto text-[11px] text-faint" title="One budget for both loops, by kind">{budgetWords(got.budget)}</span>
      </div>
      {record && <HeldRecord record={record} onDone={onDone} />}
      <RefusedStarts record={record} />
      {open.map((p) => <BusinessProposal key={p.id} p={p.repo in bars ? { ...p, bars_now: bars[p.repo] } : p} onDone={onDone} />)}
      {rest.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11.5px] text-muted">What became of the others ({rest.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {rest.map((p) => (
              <li key={p.id} className="flex flex-wrap gap-x-2 text-[11.5px]">
                <span>{proposalTitle(p)}</span>
                <span className={p.state === "merged" ? "text-done" : p.state === "critic" ? "text-wait" : "text-faint"}>{PROPOSAL_STATE_WORDS[p.state]}</span>
                {p.reason && <span className="break-words text-faint">{p.reason}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
