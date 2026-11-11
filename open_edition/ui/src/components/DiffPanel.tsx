import { useCallback, useEffect, useRef, useState } from "react";
import { GitMerge, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { Api, ApiError, sendToTerminal, type DiffReport, type UpdateStatus } from "../api";
import { useStore } from "../store";
import { escaped } from "../text";
import { DiffView, reviewText, type ReviewComment } from "./DiffView";

/** What the session's agent is asked when bringing it up to date stopped on conflicts. The files are not named in
 *  it: their names came in with the merged branch, and git allows any text in one, so the agent lists them itself,
 *  and nothing of a name is ever said as the person's own words (or typed into a terminal). */
export function resolvePrompt(ref: string): string {
  return `Bringing this branch up to date with ${escaped(ref)} stopped on conflicts. ` +
    "List the files in conflict with git diff --name-only --diff-filter=U, resolve each conflict keeping what both sides meant, " +
    "then finish the merge: git add the files and git commit --no-edit.";
}

/** A session's changes against where it started, reviewed in place; comments go to a chat as one message. Above them,
 *  the branch it started from: how many commits it is behind, bringing it up to date, and a merge stopped on
 *  conflicts, for its agent to resolve or for the person to give up. */
export function DiffPanel({ session, reviewTo }: { session: string; reviewTo: "chat" | "terminal" | null }) {
  const [report, setReport] = useState<DiffReport | null>(null);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [updating, setUpdating] = useState<"" | "update" | "abort" | "ask">("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const toast = useStore((s) => s.setError);
  // each read numbered: one begun before another (the 8-second poll, say, before an update) is dropped when it ends
  // after it, so the bar never goes back to what was true before
  const reads = useRef(0);

  const load = useCallback(async () => {
    const mine = ++reads.current;
    setLoading(true);
    try {
      const got = await Api.diff(session);
      // an older workspace agent has no updates: the changes show as before
      const now = await Api.updateStatus(session).catch(() => null);
      if (mine !== reads.current) return;
      setReport(got);
      setStatus(now);
      setError("");
    } catch (e) {
      if (mine !== reads.current) return;
      setError(e instanceof ApiError && e.status === 404 ? "This workspace does not report changes yet: it needs the newer workspace agent." : e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === reads.current) setLoading(false);
    }
  }, [session]);

  useEffect(() => {
    void load();
    const t = setInterval(() => document.visibilityState === "visible" && load(), 8000);
    return () => clearInterval(t);
  }, [load]);

  const sendReview = async (comments: ReviewComment[]) => {
    const text = reviewText(comments);
    try {
      if (reviewTo === "terminal") await sendToTerminal(session, text);
      else await Api.chatSend(session, text);
      return true;
    } catch (e) {
      toast(e);
      return false;
    }
  };

  const act = async (what: "update" | "abort" | "ask") => {
    setUpdating(what);
    setSaid("");
    try {
      if (what === "update") {
        const r = await Api.update(session);
        setSaid(r.updated ? `Merged ${r.merged} commit${r.merged === 1 ? "" : "s"} from ${r.ref}.` : r.merging ? "" : `Already up to date with ${r.ref}.`);
        if (r.note) setSaid((x) => `${x} ${r.note}.`.trim());
      } else if (what === "abort") {
        const r = await Api.updateAbort(session);
        if (!r.aborted) setSaid(r.finished ? "Its agent had finished the merge already: it stays." : "The merge was no longer under way.");
      } else if (status?.merging) {
        const text = resolvePrompt(status.merging.ref);
        if (reviewTo === "terminal") await sendToTerminal(session, text);
        else await Api.chatSend(session, text);
        setSaid("Its agent was asked to resolve them.");
      }
    } catch (e) {
      toast(e);
    } finally {
      // the bar and the banner stay as they were, their buttons held, until what they show is read again
      await load();
      setUpdating("");
    }
  };

  const merging = status?.merging;
  const banner = merging ? (
    <div className="flex flex-wrap items-center gap-2 border-b border-wait/40 bg-wait/10 px-3 py-2 text-[12px]" role="status">
      <TriangleAlert size={13} className="flex-none text-wait" />
      <span className="min-w-0 flex-1">
        Bringing it up to date with <span className="font-mono">{merging.ref}</span> stopped on conflicts in{" "}
        {merging.conflicts.length ? merging.conflicts.map((c, i) => <span key={c}>{i ? ", " : ""}<span className="font-mono">{escaped(c)}</span></span>) : "its files"}.
        {reviewTo ? null : " Resolve them in a shell, then git add and git commit --no-edit."}
        {said && <span className="ml-2 text-done">{said}</span>}
      </span>
      {reviewTo && merging.conflicts.length > 0 && (
        <button className="btn btn-sm btn-outline" disabled={!!updating} onClick={() => act("ask")}>
          {updating === "ask" ? <Loader2 size={12} className="animate-spin" /> : null} Ask its agent to resolve them
        </button>
      )}
      <button className="btn btn-sm" disabled={!!updating} onClick={() => act("abort")}>
        {updating === "abort" ? <Loader2 size={12} className="animate-spin" /> : null} Give up the merge
      </button>
    </div>
  ) : null;

  // the branch it started from, always in sight (the changes view shows its own header only beside changes, and
  // not when narrow): how far behind it is, as last known, and the update, which fetches a clone's origin first
  const bar = status?.ref && !merging ? (
    <div className="flex min-w-0 items-center gap-2 border-b border-line px-3 py-1 text-[12px] text-muted">
      <GitMerge size={12} className="flex-none text-faint" />
      <span className="min-w-0 flex-1 truncate">
        {status.behind > 0
          ? <span className="text-wait">{status.ref} has {status.behind} new commit{status.behind === 1 ? "" : "s"}</span>
          : <>From <span className="font-mono text-fg-dim">{status.ref}</span></>}
        {said && <span className="ml-2 text-done">{said}</span>}
      </span>
      <button className="btn btn-sm whitespace-nowrap" disabled={!!updating} onClick={() => act("update")}
        title={`Merge ${status.ref} into this session${status.ref.startsWith("origin/") ? ", fetched first" : ""}; what it has not committed is committed first`}>
        {updating === "update" ? <Loader2 size={12} className="animate-spin" /> : null} Update from {status.ref}
      </button>
    </div>
  ) : null;

  if (error) return <div className="flex h-full items-center justify-center px-6 text-center text-[12.5px] text-muted">{error}</div>;
  if (!report) return <div className="flex h-full items-center justify-center text-[12.5px] text-muted">Reading the changes…</div>;
  return (
    <div className="flex h-full flex-col">
      {banner}
      {bar}
      <div className="min-h-0 flex-1">
        {report.too_large ? (
          <div className="flex h-full items-center justify-center text-[12.5px] text-muted">The change is too large to show here ({report.files.length} files).</div>
        ) : (
          <DiffView
            diff={report.diff || ""}
            onSendReview={reviewTo ? sendReview : undefined}
            session={session}
            onDiscard={async (paths) => {
              try {
                for (const p of paths) await Api.discard(session, p).catch((e) => { if (!(e instanceof ApiError && e.status === 404)) throw e; });
              } catch (e) {
                toast(e);
              }
              await load();
            }}
            header={
              <span className="flex min-w-0 items-center gap-2 text-[12px] text-muted">
                <span className="truncate">Since <span className="font-mono text-fg-dim">{report.base.slice(0, 7)}</span>, committed or not</span>
                <button className="icon-btn !h-6 !w-6" onClick={load} title="Refresh"><RefreshCw size={12} className={loading ? "animate-spin" : ""} /></button>
              </span>
            }
          />
        )}
      </div>
    </div>
  );
}
