import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Ban, Bot, CircleAlert, Download, FileCheck2, GitPullRequest, Loader2, Play, ShieldAlert, ShieldCheck, ShieldX, SquareTerminal, User, Wallet } from "lucide-react";
import { Api, api, type ChatEvent, type SessionRecord, type Usage } from "../api";
import { count, tokensOf } from "../usage";
import { checkFinish, type Checked } from "../records";
import { Result } from "./AccountDialog";
import { useStore } from "../store";
import { passed, usd, Verdict, type Because } from "./bits";
import { clock, full } from "../when";

interface Finish {
  id: string; status: string; reason?: string | null; digest?: string | null; created_at: string | number; head?: string;
  verify?: string | null; claims?: string | null; rule?: string | null; because?: Because[];
}
interface Refusal { at: number; tool: string | null; reason: string }
/** What the session's agent asked for (the agent's asks.py), from the session's own record; and the person's answer,
 *  from the app's record of it, made as they gave it (workspace_routes.py), never from the session's folder. */
interface AskEntry { at: number; event: "asked" | "started" | "refused"; ask: string; program: string; task?: string | null; base?: string | null; session?: string | null }
interface Item { at: number; icon: ReactNode; tone: "plain" | "good" | "warn" | "bad"; title: ReactNode; detail?: ReactNode; source: string }

const s = (v: unknown) => (typeof v === "string" ? v : "");
const toMs = (v: unknown) => (typeof v === "number" ? v * 1000 : typeof v === "string" ? Date.parse(v) : 0);

/** What happened in a session that governance cares about, in order, each line saying where it comes from. */
export function ActivityPanel({ rec }: { rec: SessionRecord }) {
  const events = useStore((st) => st.chat[rec.id]) || [];
  const loadChat = useStore((st) => st.loadChat);
  const [finishes, setFinishes] = useState<Finish[]>([]);
  const [refusals, setRefusals] = useState<Refusal[]>([]);
  const [asks, setAsks] = useState<AskEntry[]>([]);
  const [used, setUsed] = useState<Usage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [f, g] = await Promise.all([
          api<{ finishes: Finish[] }>(`/api/ade/workspace/finishes?session=${rec.id}`),
          rec.repo ? api<{ refusals: Refusal[]; asks?: AskEntry[] }>(`/api/ade/workspace/sessions/${rec.id}/gate`).catch(() => ({ refusals: [], asks: [] })) : Promise.resolve({ refusals: [], asks: [] }),
        ]);
        if (live) { setFinishes(f.finishes); setRefusals(g.refusals); setAsks(g.asks || []); setError(""); }
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
      // the tokens its claude or codex wrote down; a session of another program has none to show
      if (["claude", "chat", "codex"].includes(rec.program)) {
        try { const u = await Api.usage(rec.id); if (live) setUsed(u); } catch { /* shown when it can be read */ }
      }
    };
    void load();
    if (rec.program === "chat") void loadChat(rec.id);
    const t = setInterval(() => document.visibilityState === "visible" && load(), 10000);
    return () => { live = false; clearInterval(t); };
  }, [rec.id, rec.repo, rec.program, loadChat]);

  const records = useStore((st) => st.records);
  const items = useMemo(() => build(rec, events, finishes, refusals, asks, records), [rec, events, finishes, refusals, asks, records]);
  const asked = events.filter((e) => e.kind === "ask").length;
  const denied = events.filter((e) => e.kind === "answered" && (e.decision === "deny" || e.decision === "stop")).length;
  const signed = finishes.filter((f) => f.status === "verified");
  const passes = signed.filter(passed).length;
  const cost = rec.chat?.cost;
  const tokens = tokensOf(used);

  return (
    <div className="@container h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[820px] px-5 py-5">
        <div className={`mb-5 grid grid-cols-2 gap-2 ${tokens ? "@xl:grid-cols-3 @3xl:grid-cols-5" : "@xl:grid-cols-4"}`}>
          <Stat label="Approvals asked" value={asked} sub={denied ? `${denied} denied` : undefined} />
          <Stat label="Gate refusals" value={refusals.length} tone={refusals.length ? "warn" : undefined} />
          <Stat label="Signed finishes" value={signed.length} sub={signed.length ? `${passes} passed Verify` : undefined} tone={signed.length ? (passes === signed.length ? "good" : "warn") : undefined} />
          <Stat label="Spend" value={cost != null ? usd(cost) : "—"} sub={cost != null ? "Claude's estimate" : "not reported"} />
          {tokens && (
            <Stat label="Tokens" value={count(tokens.total)} sub={`${count(tokens.in)} in · ${count(tokens.out)} out`}
              title={`${tokens.in.toLocaleString()} in (${tokens.cached.toLocaleString()} of them from the cache), ${tokens.out.toLocaleString()} out, as ${rec.program === "codex" ? "Codex" : "Claude Code"} wrote them down`} />
          )}
        </div>
        {error && <div className="mb-3 text-[12.5px] text-fail">{error}</div>}
        <ol className="relative border-l border-line pl-5">
          {items.map((it, i) => (
            <li key={i} className="mb-4">
              <span className={`absolute -left-[11px] flex h-[22px] w-[22px] items-center justify-center rounded-full border bg-bg ${TONE[it.tone]}`}>{it.icon}</span>
              <div className="flex items-baseline gap-2">
                <div className="min-w-0 flex-1 text-[13px] text-fg">{it.title}</div>
                <time className="flex-none text-[11px] text-faint" title={full(new Date(it.at))}>{clock(new Date(it.at))}</time>
              </div>
              {it.detail && <div className="mt-1 text-[12px] text-muted">{it.detail}</div>}
              <div className="mt-0.5 text-[10.5px] uppercase tracking-wide text-faint">{it.source}</div>
            </li>
          ))}
        </ol>
        <p className="mt-6 text-[11.5px] text-faint">
          Signed records come from this deployment, which checks a finished change and signs what it decided, passed or
          not; the rest is what the workspace and the session recorded. A session runs as the user that writes its own
          records, so those are its word.
        </p>
      </div>
    </div>
  );
}

const TONE = {
  plain: "border-line-strong text-muted",
  good: "border-done/60 text-done",
  warn: "border-wait/60 text-wait",
  bad: "border-fail/60 text-fail",
};

function Stat({ label, value, sub, tone, title }: { label: string; value: ReactNode; sub?: string; tone?: "good" | "warn"; title?: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-3 py-2.5" title={title}>
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-[18px] ${tone === "good" ? "text-done" : tone === "warn" ? "text-wait" : "text-fg"}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-faint">{sub}</div>}
    </div>
  );
}

export function build(rec: SessionRecord, events: ChatEvent[], finishes: Finish[], refusals: Refusal[], asks: AskEntry[] = [], records: SessionRecord[] = []): Item[] {
  const out: Item[] = [];
  const named = (id: string | null | undefined) => {
    const r = records.find((x) => x.id === id);
    return r ? (r.title || r.task.split("\n")[0]) : "a session since removed";
  };
  out.push({
    at: rec.created_at * 1000, icon: <Play size={11} />, tone: "plain", source: "workspace",
    title: <>Started <b className="font-medium">{rec.program === "chat" ? "a chat" : rec.program}</b>{rec.repo ? <> in <span className="font-mono">{rec.repo}</span> on <span className="font-mono">{rec.branch}</span></> : null}{rec.asked_by ? <>, by you, as the agent of &ldquo;{named(rec.asked_by)}&rdquo; asked</> : null}</>,
    detail: <span className="whitespace-pre-wrap">{rec.task}</span>,
  });
  for (const a of asks) {
    if (a.event === "asked") {
      out.push({ at: a.at * 1000, icon: <Bot size={11} />, tone: "plain", source: "the session's record",
        title: <>Its agent asked for a new <b className="font-medium">{a.program}</b> session{a.base ? <> from <span className="font-mono">{a.base}</span></> : null}</>,
        detail: <span className="whitespace-pre-wrap">{a.task || ""}</span> });
    } else {
      out.push({ at: a.at * 1000, icon: a.event === "started" ? <ShieldCheck size={11} /> : <ShieldX size={11} />,
        tone: a.event === "started" ? "good" : "bad", source: "you · the app's record",
        title: a.event === "started" ? <>You started the {a.program} session it asked for: &ldquo;{named(a.session)}&rdquo;</> : <>You refused the {a.program} session it asked for</> });
    }
  }
  const decisions = new Map<string, string>();
  for (const e of events) if (e.kind === "answered") decisions.set(s(e.id), s(e.decision));
  for (const e of events) {
    const at = toMs(e.time);
    if (e.kind === "user" && e.seq > 1) out.push({ at, icon: <User size={11} />, tone: "plain", source: "chat", title: "You wrote", detail: <span className="line-clamp-3 whitespace-pre-wrap">{s(e.text)}</span> });
    else if (e.kind === "ask") {
      const d = decisions.get(s(e.id));
      const tone = !d ? "warn" : d === "deny" || d === "stop" ? "bad" : "good";
      out.push({
        at, tone, source: "chat · Claude Code's permission ask",
        icon: !d ? <ShieldAlert size={11} /> : tone === "bad" ? <ShieldX size={11} /> : <ShieldCheck size={11} />,
        title: <>Asked to use <span className="font-mono">{s(e.name)}</span>: {!d ? "waiting for you" : d === "allow" ? "allowed once" : d === "allow_session" ? "allowed for the session" : "denied"}</>,
        detail: <span className="font-mono text-[11.5px]">{s(e.summary)}</span>,
      });
    } else if (e.kind === "error") out.push({ at, icon: <CircleAlert size={11} />, tone: "bad", source: "chat", title: "An error", detail: s(e.text) });
    else if (e.kind === "result" && typeof e.cost === "number" && e.cost > 0) out.push({ at, icon: <Wallet size={11} />, tone: "plain", source: "chat · Claude Code's estimate", title: <>A turn ended: <span className="font-mono">{usd(e.cost as number)}</span></> });
    else if (e.kind === "mcp_change") out.push({ at, icon: <ShieldCheck size={11} />, tone: "plain", source: "chat · MCP", title: <>MCP server {s(e.name)}: {s(e.change)}</> });
  }
  for (const r of refusals) {
    out.push({ at: r.at * 1000, icon: <Ban size={11} />, tone: "bad", source: "the workspace's gate", title: <>Refused {r.tool ? <span className="font-mono">{r.tool}</span> : "a tool call"}</>, detail: r.reason });
  }
  for (const f of finishes) {
    const ok = f.status === "verified";
    const pass = ok && passed(f);
    out.push({
      at: toMs(f.created_at), source: ok ? "this deployment · signed" : "this deployment",
      icon: ok ? (pass ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />) : <SquareTerminal size={11} />,
      tone: ok ? (pass ? "good" : "warn") : f.status === "error" ? "bad" : "plain",
      title: ok ? finishTitle(f.rule, pass) : f.status === "skipped" ? "Finished: nothing to verify" : "Finished, but not verified",
      detail: ok ? (
        <div className="space-y-2">
          <Verdict verify={f.verify} claims={f.claims} rule={f.rule} because={f.because} />
          <Signed id={f.id} digest={f.digest} />
        </div>
      ) : f.reason || undefined,
    });
  }
  if (rec.shipped?.url) {
    out.push({
      at: rec.shipped.at * 1000, icon: <GitPullRequest size={11} />, tone: "good", source: "workspace · gh",
      title: "Shipped as a pull request",
      detail: <a className="text-accent hover:underline" href={rec.shipped.url} target="_blank" rel="noreferrer noopener">{rec.shipped.url}</a>,
    });
  }
  return out.sort((a, b) => a.at - b.at);
}

/** A signed finish's headline, by the gate's rule that decided it (oss/core/proofpane_core/gate.py): a change Verify
 *  failed, one it could not check by itself and leaves to a person, and one with nothing it could vouch for are told
 *  apart, though Verify passes none of them. */
export function finishTitle(rule: string | null | undefined, pass: boolean): string {
  if (pass) return "Finished: Verify passed the change, and this deployment signed it";
  if (rule === "needs-person") return "Finished and signed: a person has to look at it, as Verify could not check it all by itself";
  if (rule === "nothing-blocking") return "Finished and signed: there was nothing for Verify to vouch for";
  return "Finished and signed: Verify did not pass the change";
}

/** A verified finish: its digest, its signed record to download, and a check of its signature here and now. */
function Signed({ id, digest }: { id: string; digest?: string | null }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Checked | null>(null);
  const [error, setError] = useState("");
  const check = async () => {
    setBusy(true); setError("");
    try { setResult(await checkFinish(id)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div>
      <span className="flex flex-wrap items-center gap-2">
        {digest && <span className="font-mono text-[11px]">{digest.slice(0, 28)}…</span>}
        <button className="btn btn-sm" onClick={() => download(id)}><Download size={12} /> The signed record</button>
        <button className="btn btn-sm" onClick={check} disabled={busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : <FileCheck2 size={12} />} Check its signature</button>
      </span>
      {error && <div className="mt-2 text-fail">{error}</div>}
      {result && <Result r={result} />}
    </div>
  );
}

async function download(id: string) {
  const full = await api<unknown>(`/api/ade/workspace/finishes/${id}`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(full, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `proofpane-finish-${id}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

