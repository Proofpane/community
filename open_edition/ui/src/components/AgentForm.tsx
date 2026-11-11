import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  AgentsApi, draftBrief, parsePaths, pathsText, UNATTENDED_WORDS, type AcceptanceSpec, type Agent, type AgentInput, type AgentLimits,
  type RiskTier, type Unattended,
} from "../agents";
import { EFFORT_NAMES, MODE_NAMES, MODEL_NAMES } from "../chatSettings";
import { useStore } from "../store";
import { Modal } from "./Modal";

export type FormMode = { mode: "new" } | { mode: "edit"; agent: Agent } | { mode: "keep"; session: string };

const DEFAULT_LIMITS: AgentLimits = { max_run_seconds: 3600, max_runs_per_day: 24, max_steps: 200, queue_max: 10, storage_mb: 200 };
const RULE = /^[A-Za-z_][A-Za-z0-9_.*-]{0,99}(\(.{0,190}\))?$/;

/** An agent's brief and settings: a new one, one kept from a chat (what the chat had, filled in), or one changed. */
export function AgentForm({ mode, onClose, onSaved }: { mode: FormMode; onClose: () => void; onSaved: (a: Agent) => void }) {
  const setError = useStore((s) => s.setError);
  // the store gives the same list until it changes; a list made here in the selector would be new at each read, and
  // React would render again without end
  const records = useStore((s) => s.repos);
  const repos = useMemo(() => records.filter((r) => !r.missing).map((r) => r.name), [records]);
  const caps = useStore((s) => s.caps);
  const editing = mode.mode === "edit" ? mode.agent : null;
  const [loading, setLoading] = useState(mode.mode === "keep");
  const [name, setName] = useState(editing?.name ?? "");
  const [group, setGroup] = useState(editing?.group ?? "");
  const [brief, setBrief] = useState(editing?.brief ?? "");
  const [repo, setRepo] = useState<string>(editing ? editing.repo ?? "" : repos[0] ?? "");
  const [base, setBase] = useState(editing?.base_ref ?? "");
  const [keep, setKeep] = useState(editing?.keep_worktree ?? true);
  const [model, setModel] = useState(editing?.chat_settings.model ?? "");
  const [effort, setEffort] = useState(editing?.chat_settings.effort ?? "");
  const [permission, setPermission] = useState(editing?.chat_settings.permission_mode ?? "");
  // the Claude Code account its runs use, or "" for none: then its runs start on the account new sessions start on at
  // each run, and a task of its begins on the one they start on when it is made, which its New task form names (the
  // app keeps none, "" and "default" alike as none). Offered where
  // the workspace has more than one account, or where the agent is on one already, so that it can be moved back
  const [account, setAccount] = useState(editing?.account ?? "");
  const accountRows = useStore((s) => s.accounts?.claude.accounts);
  const activeName = useStore((s) => { const v = s.accounts?.claude; return v?.accounts.find((a) => a.id === v.active)?.name; });
  const [candidates, setCandidates] = useState<string[]>([]);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [rules, setRules] = useState((editing?.allow ?? []).join("\n"));
  const [limits, setLimits] = useState<AgentLimits>(editing?.limits ?? DEFAULT_LIMITS);
  const [unattended, setUnattended] = useState<Unattended>(editing?.unattended_mode ?? "dontAsk");
  const [precheck, setPrecheck] = useState(editing?.precheck ?? "");
  const [budget, setBudget] = useState(editing?.max_budget_usd != null ? String(editing.max_budget_usd) : "");
  const [dayCost, setDayCost] = useState(editing?.est_usd_per_day != null ? String(editing.est_usd_per_day) : "");
  const [compactAt, setCompactAt] = useState(editing?.autocompact_window != null ? String(editing.autocompact_window) : "");
  const [compactions, setCompactions] = useState(editing?.max_compactions ?? 2);
  const [mayDispatch, setMayDispatch] = useState(editing?.may_dispatch ?? false);
  const [acceptsDispatch, setAcceptsDispatch] = useState(editing?.accepts_dispatch ?? false);
  const [width, setWidth] = useState(editing?.max_dispatch_per_run ?? 5);
  const [decay, setDecay] = useState(editing?.decay_runs ?? 30);
  const [reverify, setReverify] = useState(editing?.reverify ?? false);
  const [judgeRuns, setJudgeRuns] = useState(editing?.judge_runs ?? false);
  const [anomalyPause, setAnomalyPause] = useState(editing?.anomaly_pause ?? false);
  const [improve, setImprove] = useState(editing?.improve ?? false);
  const spec = editing?.acceptance ?? null;
  const [keys, setKeys] = useState((spec?.required_keys ?? []).join(", "));
  const [paths, setPaths] = useState(pathsText(spec?.json_path_equals));
  const [tier, setTier] = useState<RiskTier | "">(spec?.max_risk_tier ?? "");
  const [forbidden, setForbidden] = useState((spec?.forbidden_substrings ?? []).join("\n"));
  const [onFail, setOnFail] = useState<AcceptanceSpec["on_fail"]>(spec?.on_fail ?? "fail");
  const [fallbackModel, setFallbackModel] = useState(spec?.fallback_model ?? "");
  const [fromSession, setFromSession] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setLocalError] = useState("");

  useEffect(() => {
    if (mode.mode !== "keep") return;
    AgentsApi.draft(mode.session).then((d) => {
      setName(d.name); setBrief(d.brief); setRepo(d.repo ?? ""); setBase(d.base_ref ?? "");
      setModel(d.chat_settings.model ?? ""); setEffort(d.chat_settings.effort ?? ""); setPermission(d.chat_settings.permission_mode ?? "");
      setCandidates(d.candidates); setFromSession(d.from_session);
    }).catch((e) => { setError(e); onClose(); }).finally(() => setLoading(false));
  }, []);       // eslint-disable-line react-hooks/exhaustive-deps

  const draft = async () => {
    if (mode.mode !== "keep") return;
    setDrafting(true);
    setLocalError("");
    try { setBrief(await draftBrief(mode.session)); }
    catch (e) { setLocalError(e instanceof Error ? e.message : String(e)); }
    finally { setDrafting(false); }
  };
  const typed = rules.split("\n").map((r) => r.trim()).filter(Boolean);
  const badRule = typed.find((r) => !RULE.test(r));
  const save = async () => {
    setLocalError("");
    if (badRule) { setLocalError(`"${badRule}" is not an allow rule: a tool's name, with its pattern in brackets, such as Bash(npm test:*).`); return; }
    const dollars = (text: string) => (text.trim() ? Number(text) : null);
    const point = compactAt.trim() ? Number(compactAt) : null;
    if (point !== null && !(Number.isInteger(point) && point >= 100000 && point <= 1000000)) {
      setLocalError("A run's compaction point is from 100,000 to 1,000,000 tokens."); return;
    }
    for (const [text, what] of [[budget, "A run's budget"], [dayCost, "A day's estimated cost"]] as const) {
      const v = dollars(text);
      if (v !== null && !(v > 0 && v <= 1000)) { setLocalError(`${what} is a number of US dollars, more than 0 and at most 1000.`); return; }
    }
    let atPaths: Record<string, unknown>;
    try { atPaths = parsePaths(paths); } catch (e) { setLocalError(e instanceof Error ? e.message : String(e)); return; }
    const required = keys.split(",").map((k) => k.trim()).filter(Boolean);
    const words = forbidden.split("\n").map((w) => w.trim()).filter(Boolean);
    const checks: AcceptanceSpec | null = required.length || Object.keys(atPaths).length || tier || words.length
      ? { required_keys: required, json_path_equals: atPaths, max_risk_tier: tier || null, forbidden_substrings: words,
          on_fail: onFail, fallback_model: onFail === "fallback" ? fallbackModel.trim() || null : null }
      : null;
    if (checks?.on_fail === "fallback" && !checks.fallback_model) { setLocalError("A fallback needs its model, as Claude Code names it, such as sonnet."); return; }
    const body: AgentInput = {
      name: name.trim(), brief: brief.trim(), group: group.trim() || null, repo: repo || null, base_ref: base.trim() || null,
      keep_worktree: keep, account: account || null,
      chat_settings: { ...(model ? { model } : {}), ...(effort ? { effort } : {}), ...(permission ? { permission_mode: permission } : {}) },
      allow: Array.from(new Set([...typed, ...ticked])), limits,
      unattended_mode: unattended, precheck: precheck.trim() || null,
      max_budget_usd: budget.trim() ? Number(budget) : null, est_usd_per_day: dayCost.trim() ? Number(dayCost) : null,
      autocompact_window: compactAt.trim() ? Number(compactAt) : null, max_compactions: compactions,
      may_dispatch: mayDispatch, accepts_dispatch: acceptsDispatch, max_dispatch_per_run: width,
      decay_runs: decay, reverify, judge_runs: judgeRuns, anomaly_pause: anomalyPause, acceptance: checks, improve,
      ...(fromSession ? { from_session: fromSession } : {}),
    };
    setSaving(true);
    try {
      onSaved(editing ? await AgentsApi.update(editing.id, body) : await AgentsApi.create(body));
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  const title = editing ? `Edit ${editing.name}` : mode.mode === "keep" ? "Keep as an agent" : "New agent";
  const number = (key: keyof AgentLimits, label: string, scale = 1, unit = "") => (
    <label className="flex flex-col gap-1 text-[12px] text-muted">
      {label}
      <span className="flex items-center gap-1">
        <input type="number" className="input !h-8 w-24 text-[12.5px]" min={1} value={Math.round(limits[key] / scale)}
          onChange={(e) => setLimits({ ...limits, [key]: Math.max(1, Math.round(Number(e.target.value) || 1)) * scale })} />
        {unit && <span>{unit}</span>}
      </span>
    </label>
  );

  return (
    <Modal title={title} onClose={onClose} width={640}
      footer={
        <div className="flex items-center justify-end gap-2">
          {error && <span className="mr-auto text-[12px] text-fail">{error}</span>}
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={saving || loading || !name.trim() || !brief.trim()} onClick={() => void save()}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : null} {editing ? "Save" : "Keep it"}
          </button>
        </div>
      }>
      {loading ? (
        <div className="flex justify-center py-10 text-muted"><Loader2 size={16} className="animate-spin" /></div>
      ) : (
        <div className="space-y-4 text-[12.5px]">
          {mode.mode === "keep" && (
            <p className="text-[12px] text-muted">
              Each run is a fresh chat in the agent's own worktree, cut from this chat's branch, with its brief as the first
              message; the chat itself goes on as it is. Its memory and its worktree carry over from run to run.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
            <label className="flex flex-col gap-1 text-[12px] text-muted">Name
              <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Release notes" />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted">Group
              <input className="input" value={group} maxLength={40} onChange={(e) => setGroup(e.target.value)} placeholder="Agents" />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-[12px] text-muted">Brief: its standing instruction, the first thing each run reads
            <textarea className="input min-h-[120px] !py-2 font-normal text-fg" value={brief} maxLength={4000} onChange={(e) => setBrief(e.target.value)}
              placeholder="Read the issues opened since the last run, label each, and ask me about any you cannot place." />
            <span className="self-end text-[11px] text-faint">{brief.length} / 4000{editing ? ` · a changed brief is version ${editing.brief_version + 1}` : ""}</span>
          </label>
          {mode.mode === "keep" && (
            <button className="btn btn-sm" disabled={drafting} onClick={() => void draft()}
              title="One more turn of the chat, asking for the standing instruction it would give a successor: its reply becomes the brief, to edit">
              {drafting ? <Loader2 size={12} className="animate-spin" /> : null} {drafting ? "Asking the chat…" : "Draft the brief from this chat"}
            </button>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-[12px] text-muted">Repository
              <select className="input" value={repo} disabled={!!editing} onChange={(e) => setRepo(e.target.value)}>
                {repos.map((r) => <option key={r} value={r}>{r}</option>)}
                <option value="">None: a scratch repository of its own</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted">Starts from
              <input className="input font-mono" value={base} disabled={!!editing} onChange={(e) => setBase(e.target.value)} placeholder="the repository's branch" />
            </label>
          </div>
          <label className="flex items-start gap-2 text-[12px]">
            <input type="checkbox" className="mt-0.5" checked={keep} disabled={!!editing} onChange={(e) => setKeep(e.target.checked)} />
            <span>Keep its worktree between runs<span className="block text-[11.5px] text-muted">Off: each run starts from a fresh worktree. Its memory lasts either way.</span></span>
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-[12px] text-muted">Model
              <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Claude Code's own</option>
                {caps.model_aliases.map((m) => <option key={m} value={m}>{MODEL_NAMES[m] ?? m}</option>)}
                {model && !caps.model_aliases.includes(model) && <option value={model}>{model}</option>}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted">Effort
              <select className="input" value={effort} onChange={(e) => setEffort(e.target.value)}>
                <option value="">Claude Code's own</option>
                {caps.efforts.map((x) => <option key={x} value={x}>{EFFORT_NAMES[x] ?? x}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted">When it would act
              <select className="input" value={permission} onChange={(e) => setPermission(e.target.value)}>
                <option value="">Ask before acting</option>
                {caps.permission_modes.filter((m) => m !== "default").map((m) => <option key={m} value={m}>{MODE_NAMES[m] ?? m}</option>)}
              </select>
            </label>
          </div>
          {((accountRows?.length ?? 0) > 1 || !!account) && (
            <label className="flex flex-col gap-1 text-[12px] text-muted">Claude Code account its runs use
              <select className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">None: the account new sessions start on, at each run{activeName ? ` (now ${activeName})` : ""}</option>
                {(accountRows ?? []).filter((r) => r.id !== "default").map((r) => (
                  <option key={r.id} value={r.id}>{r.name}{r.status?.signed_in === false ? " (not signed in)" : ""}</option>
                ))}
                {account && !(accountRows ?? []).some((r) => r.id === account) && (
                  // the page knows the workspace's accounts only once it has read them (it runs, and the read landed)
                  <option value={account}>{account}: {accountRows ? "no longer in the workspace" : "the workspace's accounts are not read yet"}</option>
                )}
              </select>
              <span className="text-[11.5px] text-faint">Its runs, and a task of its to begin with, sign in as the account chosen here (Connections lists them). With none, each run starts on the account new sessions start on when it starts, and a task of its begins on the one they start on when the task is made, which its New task form names. A task's relay may move on from either.</span>
            </label>
          )}
          <div className="space-y-1.5">
            <div className="text-[12px] text-muted">Allowed without asking</div>
            {candidates.length > 0 && (
              <div className="space-y-1 rounded-md border border-line p-2">
                <div className="text-[11.5px] text-muted">What you allowed in the chat: tick what its runs may do without asking.</div>
                {candidates.map((c) => (
                  <label key={c} className="flex items-center gap-2 font-mono text-[12px]">
                    <input type="checkbox" checked={ticked.has(c)} onChange={(e) => setTicked((s) => { const n = new Set(s); if (e.target.checked) n.add(c); else n.delete(c); return n; })} />
                    {c}
                  </label>
                ))}
              </div>
            )}
            <textarea aria-label="Allow rules, one a line" className="input min-h-[60px] !py-2 font-mono text-[12px]" value={rules} onChange={(e) => setRules(e.target.value)}
              placeholder={"Bash(npm test:*)\nBash(git log:*)"} />
            <div className="text-[11.5px] text-faint">One a line, as Claude Code writes them. What none allows asks you, in the run's chat.</div>
          </div>
          <label className="flex flex-col gap-1 text-[12px] text-muted">When nobody watches it (a run a schedule or a hook started)
            <select className="input" value={unattended} onChange={(e) => setUnattended(e.target.value as Unattended)}>
              {(Object.keys(UNATTENDED_WORDS) as Unattended[]).map((m) => <option key={m} value={m}>{UNATTENDED_WORDS[m]}</option>)}
            </select>
            <span className="text-[11.5px] text-faint">A run you start by hand acts as "When it would act" says.</span>
          </label>
          <fieldset className="space-y-2 rounded-md border border-line p-2">
            <legend className="px-1 text-[12px] text-muted">With your other agents</legend>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={mayDispatch} onChange={(e) => setMayDispatch(e.target.checked)} />
              <span>Its runs may start other agents' runs
                <span className="block text-[11.5px] text-muted">They list the agents, start a run with a message, wait for it and write to it. Two deep at most; a run a hook started never may.</span>
              </span>
            </label>
            <label className="flex items-center gap-2 pl-6 text-[12px] text-muted">Runs one run may start
              <input type="number" min="1" max="20" className="input !h-7 w-20 text-[12.5px]" value={width} disabled={!mayDispatch}
                onChange={(e) => setWidth(Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 1))))} />
            </label>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={acceptsDispatch} onChange={(e) => setAcceptsDispatch(e.target.checked)} />
              <span>Take another run's message as an instruction
                <span className="block text-[11.5px] text-muted">Off: a message from another agent's run, or a chat, reaches its run as information, never an instruction.</span>
              </span>
            </label>
          </fieldset>
          <fieldset className="space-y-2 rounded-md border border-line p-2">
            <legend className="px-1 text-[12px] text-muted">What it remembers and knows</legend>
            <label className="flex items-center gap-2 text-[12px] text-muted">A memory file unused for
              <input type="number" min="0" max="1000" className="input !h-7 w-20 text-[12.5px]" value={decay}
                onChange={(e) => setDecay(Math.max(0, Math.min(1000, Math.round(Number(e.target.value) || 0))))} />
              runs goes to its archive (0: never)
            </label>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={reverify} onChange={(e) => setReverify(e.target.checked)} />
              <span>Re-verify what goes stale, weekly
                <span className="block text-[11.5px] text-muted">A run reads the source of each knowledge file past its check and each old told fact, and updates, retracts or marks it. Off: you are told when a fifth of its knowledge is stale.</span>
              </span>
            </label>
          </fieldset>
          <fieldset className="space-y-2 rounded-md border border-line p-2">
            <legend className="px-1 text-[12px] text-muted">Checking its work</legend>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={judgeRuns} onChange={(e) => setJudgeRuns(e.target.checked)} />
              <span>A judge grades its runs
                <span className="block text-[11.5px] text-muted">A tenth of them, and each one you thumb up or down: one more call on its account each. What it says counts once it agrees with your thumbs.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={anomalyPause} onChange={(e) => setAnomalyPause(e.target.checked)} />
              <span>Pause a run that goes wrong
                <span className="block text-[11.5px] text-muted">The same call 8 times, or 15 calls that changed nothing (a shell command counts as a change): it waits for you at once; 6 times its usual run's cost: at its turn's end. Off: you are told, and it goes on.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-[12px]">
              <input type="checkbox" className="mt-0.5" checked={improve} onChange={(e) => setImprove(e.target.checked)} />
              <span>Let the loop improve it
                <span className="block text-[11.5px] text-muted">When its runs go wrong, a review proposes changes to its brief, skills, effort or model, each tried on replays of its runs against the current one. Nothing changes until you approve it; a change that makes it worse is offered back. At most one review a day, and 40 sessions or $20 est. a week.</span>
              </span>
            </label>
            <div className="text-[12px] text-muted">Its final message must:</div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-[12px] text-muted">Hold JSON with these keys
                <input className="input !h-8 font-mono text-[12px]" value={keys} onChange={(e) => setKeys(e.target.value)} placeholder="labels, risk_tier" />
              </label>
              <label className="flex flex-col gap-1 text-[12px] text-muted">Name a risk tier no higher than
                <select className="input !h-8" value={tier} onChange={(e) => setTier(e.target.value as RiskTier | "")}>
                  <option value="">No check</option>
                  {(["low", "medium", "high", "critical"] as const).map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
            </div>
            <label className="flex flex-col gap-1 text-[12px] text-muted">Have these values, a line each
              <textarea aria-label="Values at paths, a line each" className="input min-h-[44px] !py-1.5 font-mono text-[12px]" value={paths}
                onChange={(e) => setPaths(e.target.value)} placeholder={'status = "ok"\nissues.0.state = "labelled"'} />
            </label>
            <label className="flex flex-col gap-1 text-[12px] text-muted">Never contain, a line each
              <textarea aria-label="Words it must not contain, a line each" className="input min-h-[44px] !py-1.5 font-mono text-[12px]" value={forbidden}
                onChange={(e) => setForbidden(e.target.value)} placeholder="TODO" />
            </label>
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
              <label className="flex items-center gap-2">When a check fails
                <select className="input !h-8 w-auto" value={onFail} onChange={(e) => setOnFail(e.target.value as AcceptanceSpec["on_fail"])}>
                  <option value="fail">the run fails</option>
                  <option value="hitl">it waits for you</option>
                  <option value="fallback">it tries once more, on another model</option>
                </select>
              </label>
              {onFail === "fallback" && (
                <input aria-label="The fallback model" className="input !h-8 w-32 font-mono text-[12px]" value={fallbackModel}
                  onChange={(e) => setFallbackModel(e.target.value)} placeholder="sonnet" />
              )}
            </div>
          </fieldset>
          <details className="rounded-md border border-line p-2">
            <summary className="cursor-pointer text-[12px] text-muted">Limits</summary>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              {number("max_run_seconds", "A run at most", 60, "minutes")}
              {number("max_steps", "Steps a run")}
              {number("max_runs_per_day", "Runs a day")}
              {number("queue_max", "Runs queued")}
              {number("storage_mb", "Its runs' folders", 1, "MB")}
              <label className="flex flex-col gap-1 text-[12px] text-muted">A run's budget
                <span className="flex items-center gap-1"><input type="number" step="0.01" min="0" className="input !h-8 w-24 text-[12.5px]" value={budget}
                  onChange={(e) => setBudget(e.target.value)} placeholder="none" /> USD</span>
              </label>
              <label className="flex flex-col gap-1 text-[12px] text-muted">Compaction point
                <span className="flex items-center gap-1"><input type="number" step="1000" min="100000" max="1000000" className="input !h-8 w-28 text-[12.5px]"
                  value={compactAt} onChange={(e) => setCompactAt(e.target.value)} placeholder="Claude Code's" /> tokens</span>
              </label>
              <label className="flex flex-col gap-1 text-[12px] text-muted">Compactions before it is fragmented
                <input type="number" min="0" max="20" className="input !h-8 w-24 text-[12.5px]" value={compactions}
                  onChange={(e) => setCompactions(Math.max(0, Math.min(20, Math.round(Number(e.target.value) || 0))))} />
              </label>
              <label className="flex flex-col gap-1 text-[12px] text-muted">A day's est. cost
                <span className="flex items-center gap-1"><input type="number" step="0.01" min="0" className="input !h-8 w-24 text-[12.5px]" value={dayCost}
                  onChange={(e) => setDayCost(e.target.value)} placeholder="none" /> USD</span>
              </label>
            </div>
            <label className="mt-3 flex flex-col gap-1 text-[12px] text-muted">Precheck: a command in its worktree before each run; a run whose precheck exits other than 0 is skipped
              <input className="input !h-8 font-mono text-[12px]" value={precheck} maxLength={2000} onChange={(e) => setPrecheck(e.target.value)}
                placeholder="git fetch -q && test -n &quot;$(git log HEAD..@{u} --oneline)&quot;" />
            </label>
          </details>
        </div>
      )}
    </Modal>
  );
}
