import { useCallback, useEffect, useRef, useState } from "react";
import { Check, CircleDot, ExternalLink, GitMerge, GitPullRequest, GitPullRequestClosed, Loader2, MessageSquareReply, X } from "lucide-react";
import { Api, type PrCheck, type PullRequest, type ReviewEvent, type SessionRecord } from "../api";
import { Modal } from "./Modal";
import { useStore } from "../store";
import { FeedbackDialog } from "./Feedback";
import { dayTime } from "../when";

type Method = "squash" | "merge" | "rebase";
const METHODS: [Method, string][] = [["squash", "Squash and merge"], ["merge", "Create a merge commit"], ["rebase", "Rebase and merge"]];
const REVIEW: Record<string, string> = { APPROVED: "Approved", CHANGES_REQUESTED: "Changes requested", REVIEW_REQUIRED: "Review required" };
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The repository a pull request is in, from its address: owner/name on github.com, host/owner/name on another
 *  (GitHub Enterprise), so the person sees whose pull request they merge or review. */
export function repoOf(url: string): string {
  const m = /^https:\/\/([^/]+)\/([^/]+)\/([^/]+)\/pull\/\d+$/.exec(url);
  if (!m) return url;
  return m[1] === "github.com" ? `${m[2]}/${m[3]}` : `${m[1]}/${m[2]}/${m[3]}`;
}

/** What keeps GitHub from merging it now, in words; null when nothing does. */
export function blocker(pr: PullRequest): string | null {
  if (pr.state !== "OPEN") return null;
  if (pr.draft || pr.merge_state === "DRAFT") return "It is a draft: mark it ready on GitHub first.";
  if (pr.mergeable === "CONFLICTING" || pr.merge_state === "DIRTY") return `It conflicts with ${pr.base || "its base"}: resolve that in the session, then ship again.`;
  if (pr.merge_state === "BLOCKED") {
    if (pr.review === "CHANGES_REQUESTED") return "A reviewer asked for changes.";
    if (pr.review === "REVIEW_REQUIRED") return "It needs an approving review.";
    if (pr.checks.running) return "Required checks are still running.";
    if (pr.checks.failed) return "Required checks failed.";
    return "The repository's rules do not allow merging it yet.";
  }
  return null;
}

function CheckIcon({ state, size = 13 }: { state: PrCheck["state"]; size?: number }) {
  if (state === "passed") return <Check size={size} className="flex-none text-done" />;
  if (state === "failed") return <X size={size} className="flex-none text-fail" />;
  return <CircleDot size={size} className="flex-none animate-pulse-dot text-wait" />;
}

/** A session started from a pull request and not shipped yet: that pull request's feedback (what its reviewers wrote
 *  and why its checks failed), to choose what goes to the agent carrying it on. */
export function PullFeedbackButton({ rec }: { rec: SessionRecord }) {
  const [open, setOpen] = useState(false);
  if (!rec.pull || rec.shipped?.url) return null;
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)}
        title={`What #${rec.pull}'s reviewers wrote and why its checks failed, to choose what goes to the agent`}>
        <MessageSquareReply size={13} /> <span className="font-mono">#{rec.pull}</span><span className="btn-label"> feedback</span>
      </button>
      {open && <FeedbackDialog rec={rec} onClose={() => setOpen(false)} />}
    </>
  );
}

const REVIEWED: Record<ReviewEvent, string> = {
  approve: "Approved on GitHub.", request_changes: "Changes requested on GitHub.", comment: "Commented on GitHub.",
};

/** A session started from a pull request: the person's own review of it, on GitHub (approve it, ask for changes, or
 *  comment), in their words, with the workspace's GitHub sign-in. The pull request is shown as GitHub has it now, and
 *  the review goes to that one only: if the session's pull request is another by then, the workspace refuses. */
export function PullReviewButton({ rec }: { rec: SessionRecord }) {
  const [open, setOpen] = useState(false);
  if (!rec.pull || rec.shipped?.url) return null;
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)} title={`Approve #${rec.pull}, ask for changes, or comment, on GitHub`}>
        <Check size={13} /> <span className="btn-label">Review </span><span className="font-mono">#{rec.pull}</span>
      </button>
      {open && <ReviewDialog rec={rec} onClose={() => setOpen(false)} />}
    </>
  );
}

export function ReviewDialog({ rec, onClose }: { rec: SessionRecord; onClose: () => void }) {
  const [pr, setPr] = useState<PullRequest | null>(null);
  const [error, setError] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState<ReviewEvent | "">("");
  const [done, setDone] = useState<ReviewEvent | "">("");
  const load = useCallback(async () => {
    try { setPr(await Api.pullRequest(rec.id)); } catch (e) { setError(msg(e)); }
  }, [rec.id]);
  useEffect(() => { void load(); }, [load]);
  const review = async (event: ReviewEvent) => {
    if (!pr) return;
    setBusy(event); setError(""); setDone("");
    try {
      await Api.reviewPr(rec.id, event, body.trim(), pr.url);
      setDone(event);
      setBody("");
      await load();
    } catch (e) {
      setError(msg(e));
    } finally {
      setBusy("");
    }
  };
  const words = body.trim().length > 0;
  return (
    <Modal title={pr?.number ? `Review #${pr.number}` : "Review the pull request"} onClose={onClose} width={560}>
      <div className="space-y-3 text-[12.5px]">
        {!pr ? (
          <div className="text-muted">{error ? <span className="text-fail">{error}</span> : <><Loader2 size={13} className="mr-1.5 inline animate-spin" /> Asking GitHub…</>}</div>
        ) : (
          <>
            <a className="group flex items-start gap-2 text-fg hover:text-accent" href={pr.url} target="_blank" rel="noreferrer noopener">
              <span className="min-w-0 flex-1 font-medium leading-snug">{pr.title || "Pull request"} <span className="font-mono font-normal text-muted">#{pr.number}</span></span>
              <ExternalLink size={13} className="mt-0.5 flex-none text-muted group-hover:text-accent" />
            </a>
            <div className="text-[11.5px] text-muted">
              <span className="font-mono">{repoOf(pr.url)}</span>
              {pr.author ? <> · by {pr.author}</> : null}
              {pr.state && pr.state !== "OPEN" ? <> · {pr.state === "MERGED" ? "merged" : "closed"}</> : null}
              {pr.review && REVIEW[pr.review] && <> · <span className={pr.review === "APPROVED" ? "text-done" : "text-wait"}>{REVIEW[pr.review]}</span></>}
            </div>
            <textarea className="input min-h-[120px] w-full font-mono text-[12px]" aria-label="Your review" value={body} onChange={(e) => setBody(e.target.value)}
              placeholder="Your review, in Markdown: what you checked, and what to change. An approval may say nothing." />
            {error && <div className="text-fail">{error}</div>}
            {done && <div className="text-done">{REVIEWED[done]}</div>}
            <div className="flex flex-wrap justify-end gap-2">
              <button className="btn" disabled={!!busy || !words} onClick={() => review("comment")}>{busy === "comment" ? <Loader2 size={13} className="animate-spin" /> : null} Comment</button>
              <button className="btn" disabled={!!busy || !words} onClick={() => review("request_changes")}>{busy === "request_changes" ? <Loader2 size={13} className="animate-spin" /> : null} Request changes</button>
              <button className="btn btn-primary" disabled={!!busy} onClick={() => review("approve")}>{busy === "approve" ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Approve</button>
            </div>
            <div className="text-[11.5px] text-faint">With this workspace's GitHub sign-in, as yourself: GitHub shows the review as that account's.</div>
          </>
        )}
      </div>
    </Modal>
  );
}

/** A shipped session's pull request in its header: its state and checks at a glance, and, opened, its review, each
 *  check, and merging it. Read again every 30 seconds while it is open and the page is in view. */
export function PullRequestButton({ rec }: { rec: SessionRecord }) {
  const refresh = useStore((s) => s.refresh);
  const [pr, setPr] = useState<PullRequest | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [feedback, setFeedback] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const state = pr?.state;

  const load = useCallback(async () => {
    try { setPr(await Api.pullRequest(rec.id)); setError(""); } catch (e) { setError(msg(e)); }
  }, [rec.id]);
  useEffect(() => {
    void load();
    if (state && state !== "OPEN") return;
    const t = setInterval(() => document.visibilityState === "visible" && void load(), 30000);
    return () => clearInterval(t);
  }, [load, state, rec.shipped?.at]);
  useEffect(() => {
    if (!open) return;
    void load();
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc); };
  }, [open, load]);

  const icon = state === "MERGED" ? <GitMerge size={13} className="text-accent" /> : state === "CLOSED" ? <GitPullRequestClosed size={13} className="text-fail" /> : <GitPullRequest size={13} className="text-done" />;
  const c = pr?.checks;
  return (
    <div className="relative" ref={box}>
      <button className="btn btn-sm" onClick={() => setOpen((o) => !o)} title={error || rec.shipped?.url} aria-haspopup="dialog" aria-expanded={open}>
        {icon}
        <span className="font-mono">{pr?.number ? `#${pr.number}` : "PR"}</span>
        {c && c.items.length > 0 && state === "OPEN" && (
          c.failed ? <X size={12} className="text-fail" /> : c.running ? <CircleDot size={12} className="animate-pulse-dot text-wait" /> : <Check size={12} className="text-done" />
        )}
        <span className="btn-label">{state === "MERGED" ? "Merged" : state === "CLOSED" ? "Closed" : pr?.draft ? "Draft" : "Pull request"}</span>
      </button>
      {open && (
        <div role="dialog" aria-label="Pull request" className="absolute right-0 top-9 z-50 w-[400px] max-w-[calc(100vw-24px)] rounded-xl border border-line-strong bg-raised shadow-2xl shadow-black/60">
          <Panel rec={rec} pr={pr} error={error} onMerged={async (next) => { setPr(next); await refresh(); }}
            onFeedback={() => { setOpen(false); setFeedback(true); }} />
        </div>
      )}
      {feedback && <FeedbackDialog rec={rec} onClose={() => setFeedback(false)} />}
    </div>
  );
}

function Panel({ rec, pr, error, onMerged, onFeedback }: { rec: SessionRecord; pr: PullRequest | null; error: string; onMerged: (pr: PullRequest) => Promise<void>; onFeedback: () => void }) {
  const [method, setMethod] = useState<Method>("squash");
  const [armed, setArmed] = useState<"" | "now" | "auto">("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  if (!pr) {
    return <div className="p-4 text-[12.5px] text-muted">{error ? <span className="text-fail">{error}</span> : <><Loader2 size={13} className="mr-1.5 inline animate-spin" /> Asking GitHub…</>}</div>;
  }
  const why = blocker(pr);
  const merge = async (auto: boolean) => {
    const which = auto ? "auto" : "now";
    if (armed !== which) { setArmed(which); setTimeout(() => setArmed((a) => (a === which ? "" : a)), 4000); return; }
    setBusy(true); setFailed("");
    try { await onMerged(await Api.merge(rec.id, method, auto)); setArmed(""); } catch (e) { setFailed(msg(e)); } finally { setBusy(false); }
  };
  const merged = rec.shipped?.merge;
  return (
    <div className="text-[12.5px]">
      <div className="border-b border-line px-4 py-3">
        <a className="group flex items-start gap-2 text-fg hover:text-accent" href={pr.url} target="_blank" rel="noreferrer noopener">
          <span className="min-w-0 flex-1 font-medium leading-snug">{pr.title || "Pull request"} <span className="font-mono font-normal text-muted">#{pr.number}</span></span>
          <ExternalLink size={13} className="mt-0.5 flex-none text-muted group-hover:text-accent" />
        </a>
        <div className="mt-1 text-[11.5px] text-muted">
          <span className="font-mono">{repoOf(pr.url)}</span> · {pr.state === "MERGED" ? <>Merged{pr.merged_at ? ` ${dayTime(pr.merged_at)}` : ""}{merged ? ` from here, by ${merged.method}` : ""}</>
            : pr.state === "CLOSED" ? "Closed without merging" : <>Open{pr.draft ? ", a draft" : ""}{pr.base ? <> · into <span className="font-mono">{pr.base}</span></> : null}</>}
          {pr.review && REVIEW[pr.review] && <> · <span className={pr.review === "APPROVED" ? "text-done" : "text-wait"}>{REVIEW[pr.review]}</span></>}
        </div>
      </div>
      <div className="border-b border-line px-4 py-3">
        <div className="mb-1.5 flex items-center gap-2 text-[11.5px] text-muted">
          <span>Checks</span>
          {pr.checks.items.length > 0 ? (
            <span className="ml-auto">
              {pr.checks.passed > 0 && <span className="text-done">{pr.checks.passed} passed</span>}
              {pr.checks.failed > 0 && <span className="text-fail">{pr.checks.passed ? " · " : ""}{pr.checks.failed} failed</span>}
              {pr.checks.running > 0 && <span className="text-wait">{pr.checks.passed + pr.checks.failed ? " · " : ""}{pr.checks.running} running</span>}
            </span>
          ) : <span className="ml-auto">none reported</span>}
        </div>
        <ul className="max-h-48 space-y-0.5 overflow-y-auto">
          {pr.checks.items.map((c, i) => (
            <li key={i} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-hover">
              <CheckIcon state={c.state} />
              {c.url ? <a className="min-w-0 flex-1 truncate text-fg-dim hover:text-accent" href={c.url} target="_blank" rel="noreferrer noopener">{c.name}</a>
                : <span className="min-w-0 flex-1 truncate text-fg-dim">{c.name}</span>}
            </li>
          ))}
        </ul>
      </div>
      {pr.state === "OPEN" && (
        <div className="border-b border-line px-4 py-2.5">
          <button className="btn btn-sm w-full justify-center" onClick={onFeedback}
            title="What its reviewers wrote and why its checks failed, to choose what goes to the session's agent">
            <MessageSquareReply size={12} /> Review feedback for the agent{pr.checks.failed > 0 ? ` (${pr.checks.failed} failed ${pr.checks.failed === 1 ? "check" : "checks"})` : ""}
          </button>
        </div>
      )}
      {pr.state === "OPEN" && (
        <div className="space-y-2.5 px-4 py-3">
          {why && <div className="rounded-md border border-wait/40 bg-wait/[0.06] px-2.5 py-1.5 text-[12px] text-wait">{why}</div>}
          <div className="flex items-center gap-2">
            <select aria-label="How to merge" className="input h-8 flex-1 !py-0 text-[12px]" value={method} onChange={(e) => setMethod(e.target.value as Method)} disabled={busy}>
              {METHODS.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
            </select>
            {why && pr.merge_state === "BLOCKED" ? (
              <button className={`btn btn-sm h-8 ${armed === "auto" ? "btn-danger" : "btn-primary"}`} disabled={busy} onClick={() => merge(true)} title="GitHub merges it once what the repository requires has passed (the repository must allow auto-merge)">
                {busy ? <Loader2 size={12} className="animate-spin" /> : <GitMerge size={12} />} {armed === "auto" ? "Click again to confirm" : "Merge when ready"}
              </button>
            ) : (
              <button className={`btn btn-sm h-8 ${armed === "now" ? "btn-danger" : "btn-primary"}`} disabled={busy || (!!why && pr.merge_state !== "BLOCKED")} onClick={() => merge(false)}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <GitMerge size={12} />} {armed === "now" ? "Click again to merge" : "Merge"}
              </button>
            )}
          </div>
          {failed && <div className="text-[12px] text-fail">{failed}</div>}
          <div className="text-[11px] text-faint">Merges in <span className="font-mono text-muted">{repoOf(pr.url)}</span>, with the workspace&apos;s GitHub sign-in. The branches stay.</div>
        </div>
      )}
    </div>
  );
}
