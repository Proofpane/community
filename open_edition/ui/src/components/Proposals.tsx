import { useState } from "react";
import { Hand, KeyRound, Square } from "lucide-react";
import { Api, type HookRequest, type Proposal } from "../api";
import { isSealed } from "../e2e";
import { confirmWays, textDigest, type ConfirmWays } from "../passkey";
import { useStore } from "../store";
import { DRAWN_OVER, MARKS_MAX, stackedMarks } from "../textRules";
import { ConfirmIt } from "./AccountDialog";
import { AppName } from "./AppName";
import { Confirm } from "./bits";
import { NextStep, SignBox, SIGN_ELSEWHERE, signsHere, visible } from "./Channel";
import { CameBack, TakenLine, useAct, useActs } from "./Acting";
import { dayTime, full } from "../when";

// What connected apps proposed through the Claude app connector (the server's ade/proposals.py), at the top of
// Approvals. Nothing proposed happens until the owner does it here: an instruction, an answer or a resume signed with a
// passkey as the command it proposes (the payload's id the proposal's, so the server knows which); a stop accepted with
// two clicks; an answer to a held command given on the command's own item, approving as proposed with the account's
// confirmation. Each may be refused, with words its app reads back. Its words are the app's, its reason the app's own
// word, until the owner signs them.

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** What a proposal is, in words: "An instruction for mac". */
export function proposalTitle(p: Proposal): string {
  const s = p.session ?? "a session";
  switch (p.kind) {
    case "instruction": return `An instruction for ${s}`;
    case "answer": return `An answer to question [${p.ref}] from ${s}`;
    case "stop": return `A stop of ${s}`;
    case "resume": return `A resume of ${s}`;
    default: return "An answer to a held command";
  }
}

/** The app that proposed it, by its own word. */
const appWord = (p: Proposal) => p.app ?? "an app that is gone";

/** The proposals grouped by the app that made them, newest first, the app whose newest is newest first; an answer to
 *  a held command that Approvals lists is shown on the command's own item instead. */
export function byApp(ps: Proposal[], requests: Pick<HookRequest, "id">[] = []): { grant: string; app: string; items: Proposal[] }[] {
  const listed = new Set(requests.map((q) => q.id));
  const groups = new Map<string, { grant: string; app: string; items: Proposal[] }>();
  for (const p of [...ps].sort((a, b) => b.made_at - a.made_at || (a.id < b.id ? -1 : 1))) {
    if (p.state !== "proposed" || (p.kind === "approval" && p.request_id && listed.has(p.request_id))) continue;
    const g = groups.get(p.grant_id) ?? { grant: p.grant_id, app: appWord(p), items: [] };
    g.items.push(p);
    groups.set(p.grant_id, g);
  }
  return [...groups.values()];
}

/** Refuse a proposal, with one line of words its app reads back if the owner writes any. Refuse it shows itself
 *  taken at once, and brings the form back with its words and the reason if the server does not take it;
 *  `disabled` while another press of the proposal's card goes. */
export function RefuseIt({ p, onDone, disabled = false }: { p: Proposal; onDone: () => Promise<void>; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [words, setWords] = useState("");
  const refused = useAct(`proposal:${p.id}`, useStore((s) => s.setError));
  if (refused.taken) return <TakenLine said={refused.taken} sure={refused.sure} />;
  if (!open) return <button className="btn btn-sm" disabled={disabled} onClick={() => setOpen(true)}>Refuse</button>;
  const go = () => void refused.act("Refused", () => Api.refuseProposal(p.id, words.trim() || undefined), onDone);
  return (
    <form className="flex w-full flex-wrap items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); go(); }}>
      <input aria-label="Words for the app (it reads them back)" className="input h-7 min-w-0 flex-1 text-[12px]" maxLength={300}
        placeholder="Words for the app, if you want" value={words} onChange={(e) => setWords(e.target.value)} />
      <button className="btn btn-sm btn-danger" disabled={disabled}>Refuse it</button>
      <button type="button" className="btn btn-sm" onClick={() => setOpen(false)}>Cancel</button>
      <div className="w-full"><CameBack why={refused.failed} /></div>
    </form>
  );
}

/** Words shaped as an encrypted text, which this page never shows as text: an answer's question sealed since the
 *  proposal was made (encryption turned on), or an app's words written to look sealed. */
export const ENVELOPE_WORDS = "These words are shaped as an encrypted text, so they are not shown here.";
export const QUESTION_SEALED = "Its question is encrypted now: answer it on its session's card, where it opens.";

/** What the review of a proposal's words says beside them, which it shows whole: how many characters and lines they
 *  are, and what in them breaks the rules the server holds an app's words to (one made before a rule may hold it): a
 *  character not shown as itself, a mark drawn over or around another, a character with more than MARKS_MAX marks
 *  on it, more than the 4,000 characters a command may be. */
export function wordsFacts(text: string): { size: string; flags: string[] } {
  const chars = [...text].length;
  const lines = text.split(/\r\n|\r|\n/).length;
  const flags: string[] = [];
  if (visible(text) !== text) flags.push("It holds characters not shown as themselves: each is written here as [U+XXXX].");
  if (DRAWN_OVER.test(text)) flags.push("It holds a mark drawn over or around a character, which could change how it reads.");
  if (stackedMarks(text) !== null) flags.push(`It holds a character with more than ${MARKS_MAX} marks on it, which are drawn over the lines around it.`);
  if (chars > 4000) flags.push("It is longer than the 4,000 characters a command may be.");
  const size = `${chars.toLocaleString("en-US")} character${chars === 1 ? "" : "s"} on ${lines.toLocaleString("en-US")} line${lines === 1 ? "" : "s"}, all shown`;
  return { size, flags };
}

/** A proposal's words for review: whole, never in a box that scrolls, with what wordsFacts says of them. */
function Words({ text }: { text: string }) {
  const facts = wordsFacts(text);
  return (
    <div>
      <div className="text-[11px] text-muted">Its words, the app&apos;s until you sign them: <span>{facts.size}.</span></div>
      {facts.flags.map((f) => <div key={f} role="note" className="text-[11.5px] text-wait">{f}</div>)}
      <pre aria-label="Its words, whole" className="whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-1.5 font-mono text-[11.5px] text-fg">{visible(text)}</pre>
    </div>
  );
}

/** The app's reason, as its own word. */
function Reason({ p }: { p: Proposal }) {
  if (!p.why) return null;
  if (isSealed(p.why)) return <div className="mt-1 text-[12px] text-fg-dim">Its reason: {ENVELOPE_WORDS}</div>;
  return <div className="mt-1 text-[12px] text-fg-dim">Its reason, its own word: <span className="whitespace-pre-wrap break-words">{visible(p.why)}</span></div>;
}

function Head({ p }: { p: Proposal }) {
  return (
    <div className="mb-1 text-[11.5px] text-muted">
      <span title={full(p.made_at)}>Proposed {dayTime(p.made_at)}</span>; it waits until <span title={full(p.expires_at)}>{dayTime(p.expires_at)}</span>
    </div>
  );
}

/** One proposal that is signed (an instruction, an answer, a resume) or accepted (a stop). */
function Item({ p, onChanged }: { p: Proposal; onChanged: () => Promise<void> }) {
  const [signing, setSigning] = useState(false);
  // its stop, once confirmed, shows itself taken at once, and comes back with the reason if the server does not take
  // it; the card's buttons wait while its stop or its Refuse goes
  const acts = useActs(useStore((s) => s.setError));
  const stopKey = `stop-proposal:${p.id}`;
  const pressed = !!acts.taken(stopKey) || !!acts.taken(`proposal:${p.id}`);
  const signed = p.kind === "instruction" || p.kind === "answer" || p.kind === "resume";
  const by = appWord(p);
  const done = async () => { setSigning(false); await onChanged(); };
  // the page never shows an envelope, and nothing reviewed from one is signed
  const questionSealed = p.kind === "answer" && (p.question_encrypted === true || isSealed(p.question));
  const wordsSealed = isSealed(p.text);
  const questionGone = p.kind === "answer" && p.question == null && !questionSealed;
  return (
    <div aria-label={proposalTitle(p)} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
      <div className="text-[12.5px] font-medium">{proposalTitle(p)}</div>
      <Head p={p} />
      {p.kind === "answer" && p.question != null && !questionSealed && (
        <div className="mb-1.5">
          <div className="text-[11px] text-muted">The question, as {p.session ?? "the session"} asked it:</div>
          <pre className="whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-1.5 font-mono text-[11.5px] text-fg-dim">{visible(p.question)}</pre>
        </div>
      )}
      {(p.kind === "instruction" || p.kind === "answer") && !signing && (wordsSealed
        ? (
          <div>
            <div className="text-[11px] text-muted">Its words, the app&apos;s until you sign them:</div>
            <div className="text-[12px] text-fg-dim">{ENVELOPE_WORDS}</div>
          </div>
        )
        : <Words text={p.text ?? ""} />)}
      <Reason p={p} />
      {signing && p.link_id && (
        <SignBox purpose={p.kind as "instruction" | "answer" | "resume"} to={p.link_id} toName={p.session ?? undefined} start={p.text ?? ""}
          proposal={{ id: p.id, by }} after={p.kind === "resume" ? p.stopped_at ?? undefined : undefined}
          placeholder={p.kind === "answer" ? "Your answer" : `What ${p.session ?? "the session"} should do`}
          submit={p.kind === "resume" ? "Review the resume" : p.kind === "answer" ? "Review the answer" : "Review"}
          extra={p.kind === "answer" ? async () => ({ ref: p.ref ?? undefined, digest: await textDigest(p.question ?? "") }) : undefined}
          onDone={done} onCancel={() => setSigning(false)} />
      )}
      {!signing && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {signed && (signsHere()
            ? <button className="btn btn-sm btn-primary" disabled={(p.kind === "answer" && p.question == null) || questionSealed || wordsSealed || pressed} onClick={() => setSigning(true)}><KeyRound size={12} /> Review and sign</button>
            : <span className="text-[12px] text-muted">{SIGN_ELSEWHERE()}</span>)}
          {p.kind === "stop" && (
            <Confirm label={`Stop ${p.session ?? "the session"}`} again={`Click again to stop ${p.session ?? "it"}`} icon={<Square size={12} />}
              disabled={pressed}
              onConfirm={async () => { await acts.act(stopKey, "Stopped", () => Api.acceptStop(p.id), onChanged); }} />
          )}
          <RefuseIt p={p} onDone={onChanged} disabled={!!acts.taken(stopKey)} />
        </div>
      )}
      {acts.taken(stopKey) && <TakenLine said={acts.taken(stopKey)!} sure={acts.sure(stopKey)} />}
      <CameBack why={acts.failed(stopKey)} />
      {questionGone && <div className="mt-1 text-[12px] text-muted">Its question has gone, so it cannot be signed.</div>}
      {questionSealed && <div className="mt-1 text-[12px] text-muted">{QUESTION_SEALED}</div>}
    </div>
  );
}

/** The section at the top of Approvals: each app's proposals, under the app's own word, with Refuse all. */
export function Proposals({ proposals, requests }: { proposals: Proposal[]; requests: HookRequest[] }) {
  const refreshApprovals = useStore((s) => s.refreshApprovals);
  // Refuse all, once confirmed, shows itself taken at once, and comes back with the reason if the server does not take
  // it
  const acts = useActs(useStore((s) => s.setError));
  const groups = byApp(proposals, requests);
  if (!groups.length) return null;
  return (
    <section aria-label="Proposals" className="space-y-2">
      {groups.map((g) => (
        <div key={g.grant} className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-[11.5px] text-muted">
            <span className="flex items-center gap-1.5">
              <Hand size={12} /> Proposed by &ldquo;<AppName name={g.app} className="text-fg" />&rdquo; (its own word), through the Claude app connector
            </span>
            {g.items.length > 1 && (
              <Confirm label="Refuse all" again={`Click again to refuse all ${g.items.length}`} disabled={!!acts.taken(`refuse-all:${g.grant}`)}
                onConfirm={async () => { await acts.act(`refuse-all:${g.grant}`, "Refused all", () => Api.refuseAllProposals(g.grant), refreshApprovals); }} />
            )}
          </div>
          {acts.taken(`refuse-all:${g.grant}`) && <TakenLine said={acts.taken(`refuse-all:${g.grant}`)!} sure={acts.sure(`refuse-all:${g.grant}`)} />}
          <CameBack why={acts.failed(`refuse-all:${g.grant}`)} />
          {g.items.map((p) => <Item key={p.id} p={p} onChanged={refreshApprovals} />)}
        </div>
      ))}
    </section>
  );
}

/** What a connected app proposed for a held command, on the command's own item: its decision and reason, and the
 *  owner's answer with the command's own buttons. Approving takes the account's confirmation made for this command,
 *  whatever the app proposed, as the command's own Approve does; denying takes the sign-in alone. The proposal is told
 *  how it was answered. */
export function ProposedAnswer({ q, p }: { q: HookRequest; p: Proposal }) {
  const refreshApprovals = useStore((s) => s.refreshApprovals);
  const [ways, setWays] = useState<ConfirmWays | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Deny shows itself taken at once, its buttons disabled, and comes back with the reason if the server does not take
  // it, keyed by its held command as the command's own Deny is; the card's buttons wait while its Deny
  // or the proposal's own Refuse goes. Approve once asks for its confirmation first, which
  // is its own step
  const acts = useActs(useStore((s) => s.setError));
  const denyKey = `request:${q.id}`;
  const denied = acts.taken(denyKey);
  const pressed = !!denied || !!acts.taken(`proposal:${p.id}`);
  const answer = async (approve: boolean) => {
    if (!approve) { setError(""); void acts.act(denyKey, "Denied", () => Api.decideProposed(q.id, false, p.id), refreshApprovals); return; }
    setBusy(true); setError("");
    try { setWays(await confirmWays()); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  return (
    <div>
      <div className="mt-2 rounded-md border border-line px-2.5 py-2 text-[12px]">
        &ldquo;<AppName name={appWord(p)} className="font-medium" />&rdquo; (its own word) proposes: <span className="font-medium">{p.decision === "approve" ? "approve it once" : "deny it"}</span>.
        <Reason p={p} />
      </div>
      {!ways && (
        <div className="mt-2 flex flex-wrap gap-2">
          <button className="btn btn-sm btn-primary" disabled={busy || pressed} onClick={() => void answer(true)}>Approve once</button>
          <button className="btn btn-sm btn-danger" disabled={busy || pressed} onClick={() => void answer(false)}>Deny</button>
          <RefuseIt p={p} onDone={refreshApprovals} disabled={!!denied} />
        </div>
      )}
      {denied && <TakenLine said={denied} sure={acts.sure(denyKey)} />}
      <CameBack why={acts.failed(denyKey)} />
      {ways && (
        // the next step takes the keyboard and is said
        <NextStep say="Approving this command: confirm it to approve it once.">
          <ConfirmIt change={{ what: "approve", label: p.decision === "approve" ? `Approving this command as “${visible(appWord(p))}” proposed` : "Approving this command", action: "Approve once", subject: `request-approve:${q.id}`, ways }}
            onConfirmed={async (confirmation) => { await Api.decideProposed(q.id, true, p.id, confirmation); setWays(null); await refreshApprovals(); }}
            onCancel={() => setWays(null)} />
        </NextStep>
      )}
      {error && <div role="alert" className="mt-1.5 text-[12px] text-fail">{error}</div>}
    </div>
  );
}
