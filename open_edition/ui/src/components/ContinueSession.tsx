import { useEffect, useRef, useState } from "react";
import { ArrowRightLeft, Loader2 } from "lucide-react";
import { Api, type HandoverProgram, type SessionRecord } from "../api";
import { accountKind, handoverCases, HANDOVER_PROGRAMS, ONE_CHECK, startingAccount, WHAT_IT_DOES, type AccountGroup } from "../handover";
import { useStore } from "../store";
import { Modal } from "./Modal";
import { RiskTerms, TERMS_IN_FLOW } from "./RiskTerms";

// Continue in a new session: by the owner's press alone, a
// workspace session (a terminal Claude Code or Codex, or a chat) is finished, its worktree committed to its branch as
// Finish does, and a new session is cut from that commit on any of the owner's own accounts of either program, its
// first prompt the task as the owner gives it here and what the workspace observed of the old session's work (its
// commits by id and the names of the files they changed, marked as data), never the old session's conversation. The
// one check is the worktree's: a terminal program or a chat has no tool to say it read what it was given, as a task's
// attempt has, and this dialog says so. Risk point 17's text opens over it, its parts as the choices make them apply.

export function ContinueSession({ rec, onClose }: { rec: SessionRecord; onClose: () => void }) {
  const accounts = useStore((s) => s.accounts);
  const refresh = useStore((s) => s.refresh);
  const select = useStore((s) => s.select);
  const say = useStore((s) => s.say);
  const from: HandoverProgram = rec.program === "codex" ? "codex" : rec.program === "chat" ? "chat" : "claude";
  const [program, setProgram] = useState<HandoverProgram>(from);
  const [account, setAccount] = useState(() => startingAccount(rec, from, accounts));
  const [task, setTask] = useState("");
  const typed = useRef(false);            // the owner wrote in the task before the kept one came back
  const [kept, setKept] = useState<"reading" | "kept" | "none">("reading");
  const [groups, setGroups] = useState<Record<string, AccountGroup>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    Api.handover(rec.id).then((h) => {
      if (!live) return;
      if (h.task && !typed.current) setTask(h.task);
      setKept(h.task ? "kept" : "none");
    }).catch(() => { if (live) setKept("none"); });
    // the accounts' groups, for which parts of the text apply: a page that cannot read them treats every other
    // account's group as not known, so it shows the organisation's part rather than leave it out
    Api.taskAccounts().then((r) => {
      if (!live) return;
      const g: Record<string, AccountGroup> = {};
      for (const a of r.accounts) g[a.account] = { grp: a.grp, grp_kind: a.grp_kind };
      setGroups(g);
    }).catch(() => { /* no groups known: the cases say so */ });
    return () => { live = false; };
  }, [rec.id]);

  const kind = accountKind(program);
  const rows = accounts?.[kind].accounts ?? [];
  const choose = (p: HandoverProgram) => {
    setProgram(p);
    if (accountKind(p) !== kind) setAccount(startingAccount(rec, p, accounts));
  };
  const cases = handoverCases(rec, { program, account }, groups);

  const go = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await Api.continueSession(rec.id, { program, account, task: task.trim(), cols: 120, rows: 32 });
      await refresh();
      select(r.session.id);
      const n = r.handover.commits;
      say(`Finished and continued in a new session from ${r.handover.head.slice(0, 12)}: its first prompt names ${n} ${n === 1 ? "commit" : "commits"} and ${r.handover.files} ${r.handover.files === 1 ? "file" : "files"}.`, "done", 8000);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Continue in a new session"
      onClose={onClose}
      width={600}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={go} disabled={busy || !task.trim()}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <ArrowRightLeft size={13} />}
            {busy ? "Finishing, then starting the new session…" : "Finish and continue"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-[12.5px] text-muted">{WHAT_IT_DOES}</p>
        <p className="text-[12.5px] text-muted">{ONE_CHECK}</p>
        <div className={`grid ${TERMS_IN_FLOW}`}>
          <RiskTerms point={17} kind="control" open cases={cases} />
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="label mb-1.5 block">Program</label>
            <div role="radiogroup" aria-label="Program" className="flex gap-1">
              {HANDOVER_PROGRAMS.map((p) => (
                <button key={p.id} role="radio" aria-checked={program === p.id}
                  className={`btn btn-sm ${program === p.id ? "btn-primary" : ""}`} onClick={() => choose(p.id)}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="min-w-[12rem] flex-1">
            <label className="label mb-1.5 block" htmlFor="continue-account">Account</label>
            <select id="continue-account" aria-label="Account" className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
              {rows.length === 0 && <option value={account}>{account === "default" ? "Default" : account}</option>}
              {rows.map((a) => <option key={a.id} value={a.id}>{a.id === "default" ? `${a.name || "Default"} (the program's own)` : a.name || a.id}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="label mb-1.5 block" htmlFor="continue-task">Task</label>
          <textarea id="continue-task" aria-label="Task" className="input" rows={5} maxLength={4000} value={task}
            onChange={(e) => { typed.current = true; setTask(e.target.value); }}
            placeholder={kept === "none" ? "What the new session is for: this session's task was not kept" : "What the new session is for"} />
          <div className="mt-1 text-[11.5px] text-muted">
            {kept === "kept" ? "Your task as you gave this session, to change as you like." : kept === "none" ? "This session's task was not kept (a session made before the app kept them, or one an agent asked for): say it again." : "Reading the task you gave this session…"}
          </div>
        </div>
        {error && <div className="rounded-md border border-fail/40 bg-fail/[0.07] px-3 py-2 text-[12.5px] text-fail">{error}</div>}
      </div>
    </Modal>
  );
}
