import { useState } from "react";
import { Check, ExternalLink, GitPullRequest, Loader2, ShieldCheck } from "lucide-react";
import { Api, type SessionRecord } from "../api";
import { useStore } from "../store";
import { Modal } from "./Modal";
import { passed, Verdict, type Decision } from "./bits";

type Step = "idle" | "finishing" | "shipping" | "done";

/** Ship a session: Finish commits its worktree and has this deployment verify and sign the change; then its branch is
 *  pushed and a pull request opened, with the workspace's own GitHub sign-in. */
export function ShipDialog({ rec, others = [], onClose, onConnections }: { rec: SessionRecord; others?: SessionRecord[]; onClose: () => void; onConnections: () => void }) {
  const refresh = useStore((s) => s.refresh);
  // Ship pushes and opens the pull request with the workspace's GitHub sign-in: without it, said before anything runs
  const github = useStore((s) => s.github);
  const signedOut = !!github && !github.user;
  const [first, ...rest] = (rec.task || "").split("\n");
  const [title, setTitle] = useState(first.trim());
  const [body, setBody] = useState(rest.join("\n").trim());
  const [base, setBase] = useState("");
  const [draft, setDraft] = useState(false);
  const [step, setStep] = useState<Step>("idle");
  const [error, setError] = useState("");
  const [verified, setVerified] = useState<(Decision & { status?: string }) | null>(null);
  const [url, setUrl] = useState<string | null>(rec.shipped?.url || null);
  const [removed, setRemoved] = useState<"no" | "busy" | "done">("no");
  const removeOthers = async () => {
    setRemoved("busy");
    try {
      for (const o of others) await Api.removeSession(o.id);
      setRemoved("done");
      await refresh();
    } catch (e) {
      setRemoved("no");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const go = async () => {
    setError("");
    try {
      // a second try after the push (the pull request not opened) only opens the pull request: the change is
      // committed and verified already
      if (!verified) {
        setStep("finishing");
        setVerified((await Api.finish(rec.id)) as Decision & { status?: string });
      }
      setStep("shipping");
      const r = await Api.ship(rec.id, { title: title.trim() || undefined, body, base: base.trim() || undefined, draft });
      setUrl(r.url);
      setStep("done");
      await refresh();
    } catch (e) {
      setStep("idle");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const signIn = signedOut || /credential|authenticat|could not read Username|403|Permission|GH_TOKEN|gh auth login/i.test(error);
  const pushed = error.startsWith("Pushed ");
  const busy = step === "finishing" || step === "shipping";

  return (
    <Modal
      title="Ship this session"
      onClose={onClose}
      width={600}
      footer={
        step === "done" && url ? (
          <>
            <a className="btn btn-primary" href={url} target="_blank" rel="noreferrer noopener"><ExternalLink size={13} /> Open the pull request</a>
            <button className="btn" onClick={onClose}>Close</button>
          </>
        ) : (
          <>
            <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="btn btn-primary" onClick={go} disabled={busy || !title.trim() || signedOut}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <GitPullRequest size={13} />}
              {step === "finishing" ? "Committing and verifying…" : step === "shipping" ? "Pushing, opening the pull request…"
                : pushed ? "Open the pull request" : "Commit, push and open a pull request"}
            </button>
          </>
        )
      }
    >
      {step === "done" && url ? (
        <div className="space-y-3 text-[13px]">
          <div className="flex items-center gap-2 text-done"><Check size={15} /> Pushed <span className="font-mono text-[12px]">{rec.branch}</span> and opened its pull request.</div>
          {verified?.status === "verified" ? (
            <div className="space-y-1.5 rounded-lg border border-line px-3 py-2.5">
              <div className="flex items-center gap-2 text-fg-dim">
                <ShieldCheck size={15} className={passed(verified) ? "text-done" : "text-wait"} />
                {passed(verified) ? "Verify passed the change, and this deployment signed it." : "This deployment signed the change; Verify did not pass it."}
              </div>
              <div className="pl-[23px]"><Verdict {...verified} /></div>
            </div>
          ) : verified?.status ? (
            <div className="flex items-center gap-2 text-fg-dim"><ShieldCheck size={15} className="text-muted" /> Not verified: {verified.status}.</div>
          ) : null}
          <a className="block truncate text-accent hover:underline" href={url} target="_blank" rel="noreferrer noopener">{url}</a>
          {others.length > 0 && (
            <div className="mt-2 rounded-lg border border-line p-3">
              <div className="mb-2 text-fg-dim">
                {removed === "done" ? "The other sessions are removed, with their worktrees; a branch of theirs with commits stays in the repository." :
                  `${others.length} other ${others.length === 1 ? "session" : "sessions"} worked on the same task.`}
              </div>
              {removed !== "done" && (
                <button className="btn btn-sm" disabled={removed === "busy"} onClick={removeOthers}>
                  {removed === "busy" ? <Loader2 size={12} className="animate-spin" /> : null} Remove the others (a branch with commits stays)
                </button>
              )}
              {error && <div className="mt-2 text-[12px] text-fail">{error}</div>}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-[12.5px] text-muted">
            Commits everything in the worktree, has this deployment verify and sign the change, pushes{" "}
            <span className="font-mono text-fg-dim">{rec.branch}</span>{" "}
            and opens a pull request for it.
          </p>
          {signedOut && !error && (
            <div role="status" className="rounded-md border border-wait/40 bg-wait/[0.07] px-3 py-2 text-[12.5px] text-fg-dim">
              GitHub is not signed in here: Ship pushes and opens the pull request with that sign-in. Sign in first.
              <button className="ml-2 text-accent underline" onClick={onConnections}>Sign in to GitHub</button>
            </div>
          )}
          <div>
            <label className="label mb-1.5 block">Title</label>
            <input aria-label="Title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
          </div>
          <div>
            <label className="label mb-1.5 block">Description</label>
            <textarea aria-label="Description" className="input" rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder="What changed and why" />
          </div>
          <div className="flex items-end gap-4">
            <div className="flex-1">
              <label className="label mb-1.5 block">Into</label>
              <input aria-label="Into: the branch it goes into" className="input font-mono text-[12px]" value={base} onChange={(e) => setBase(e.target.value)} placeholder="the repository's default branch" />
            </div>
            <label className="flex h-[30px] items-center gap-2 text-[12.5px] text-fg-dim">
              <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> Draft
            </label>
          </div>
          {error && (
            <div className="rounded-md border border-fail/40 bg-fail/[0.07] px-3 py-2 text-[12.5px] text-fail">
              {error}
              {signIn && <button className="ml-2 text-accent underline" onClick={onConnections}>Sign in to GitHub</button>}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
