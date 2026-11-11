import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import type { Agent } from "../agents";
import { Api, ApiError } from "../api";
import { confirmWays, type ConfirmWays } from "../passkey";
import { liftDigest, TaskOffersApi, type HandOffTask, type OfferHold, type OfferPacket, type OfferView, type SecretFinding, type SecretHold } from "../taskOffersApi";
import { visible } from "../textRules";
import { clock, dayTime } from "../when";
import { ConfirmIt } from "./AccountDialog";
import { RiskTerms } from "./RiskTerms";

// The page parts for one task and for the offers a teammate made, rendered by name by AgentTask.tsx and Agents.tsx.
// Each of the owner's offer and custody controls opens risk point 12's text over it, and a teammate's acceptance point
// 13's (its cases "offer", "adopted_check" where the teammate adopts the owner's check, and "other_group" for an agent
// of the offer's other_group_agents; its "{owner}" the owner as the offer names them), every time and holding nothing,
// through RiskTerms; custody's hand-off by itself opens point 12's text from the move line. Each part reads the track
// first, and offers nothing until a read answers. While the track is off on the deployment the read says why (its
// `off`): the part shows it, in the server's words, in place of every control that starts or advances a hand-off, and
// beside it what ends or undoes one that stands, whose routes still answer. Any other failure keeps what was last read,
// says the error, and offers Try again.

export interface TaskHandOffProps {
  /** the task pane's TaskView itself, which carries every member of HandOffTask */
  task: HandOffTask;
  /** called after any change this part made, so the task's pane reads the task again */
  onChanged(): void;
  /** told whether this part shows the task's standing offer with its own Cancel or Withdraw, and false once it no
   *  longer does: while it does, the pane's list of holds leaves the offer's hold out, so that hold has one Cancel */
  onOfferShown?(shown: boolean): void;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Why the track is off on the deployment, from a read's answer: its `off`, while it is; null while it is on. */
const offWords = (got: { off?: string } | null | undefined) =>
  (typeof got?.off === "string" && got.off ? got.off : null);

/** A confirmed change refused for good: what its confirmation names is gone (404), out of date or confirmed already
 *  (409), or the task can no longer be offered (412), so the same again cannot succeed. A refused confirmation (403),
 *  or a server not reached, can, and ConfirmIt offers it again. */
const deadEnd = (e: unknown) => e instanceof ApiError && [404, 409, 412].includes(e.status);

/** When a hold ends, as the page says it: the clock within the hour, with the day beyond it. */
const holdAt = (t: number) => (t - Date.now() / 1000 > 3600 ? dayTime(t) : clock(t));

/** What the owner reads at once after naming a teammate, whatever the app answers: the same words whether or not the
 *  address is someone's here, so the page tells nobody who has an account. */
const SEEN = "If that address is someone's here, they will see your request.";

/** The states in which a task may be offered: one that waits, or one working now, whose offer then begins at its
 *  attempt's end. Within them the route refuses, with why, what else forbids it (an unknown
 *  effect, say), and the page says that refusal. */
const HANDABLE = ["suspended", "running"];
/** The states in which the owner may change a task's custody: any but those in which it is with a teammate or on its
 *  way to or from one, which the routes refuse, and an ended task's. */
const CUSTODY_STATES = ["ready", "running", "suspended", "needs_owner"];

/** An offer that stands: in its hold, or offered and waiting for the teammate's answer. */
const STANDING = ["held", "offered"];
/** A custodian is set while the owner's request or custody stands (Confirm for custody is shown when a custodian is
 *  set). */
const CUSTODIAN_SET = ["asked", "accepted", "suspended"];
/** What a confirmation for custody covers, as the server's digest of it does. */
const COVERS = "Confirming covers this task's title, goal, steps and every decision, your answers among them, its checks, " +
  "external commands and protected paths, and its agent's brief's version, as they stand now. A change to any of them " +
  "leaves it unconfirmed.";

/** The owner's last offer of a task that ended without the teammate taking it, in words; null for any other end. */
function endedWords(o: OfferView): string | null {
  const why = o.why ? ` ${visible(o.why)}` : "";
  if (o.kind === "custody") return o.state === "failed" ? `The last hand-off of this task to your custodian failed.${why}` : null;
  if (o.state === "declined") return "Your teammate declined your last offer of this task.";
  // the same words whether or not the address was someone's: nothing tells the owner which
  if (o.state === "expired") return "Your last offer of this task expired before it was answered.";
  if (o.state === "failed") return `Your last offer of this task failed.${why}`;
  return null;
}

/** How often the offers are read again while the task is with the teammate and the page is shown. */
const THEIRS_EVERY_MS = 15000;

/** The teammate's task as the owner's accepted offer says it: running or waiting, on its
 *  step, or done; null where the offer says nothing, before an acceptance, and once their task is stopped or gone. */
function theirWords(o: OfferView | null): string | null {
  if (!o?.their_state) return null;
  if (o.their_state === "done") return "Their task is done.";
  return `Their task is ${o.their_state}${o.their_step != null ? `, on step ${o.their_step}` : ""}.`;
}

/** An offer of the owner's that stands, with what it waits for, and Cancel (in its hold) or Withdraw: both withdraw
 *  it, which the route allows until it is accepted. The words are the same whether or not the address is someone's,
 *  so none says the offer is sent or seen: for an address that is no one's, nothing is. */
function StandingOffer({ offer, state, busy, onWithdraw }: { offer: OfferView; state: string; busy: boolean; onWithdraw: () => void }) {
  const to = offer.to_email ? visible(offer.to_email) : "your teammate";
  const hold = offer.hold;
  let words: string;
  if (offer.kind === "custody") {
    // a custody hand-off is held as an offer is, and taken by the custodian's standing consent once its hold ends
    words = hold ? `This task goes to your custodian at ${holdAt(hold.takes_effect_at)} unless you cancel it.`
      : state === "offering" ? "This task goes to your custodian: its hold has ended." : "Its hold has ended.";
  } else if (offer.state === "held") {
    // held with no hold: its hold has ended, and it goes ahead while the task is offering; or it was asked for on a
    // running task and begins only once that attempt ends, and not at all where the end did not begin it, which the
    // server then ends within a round, saying why as the end left the task
    words = hold ? `Your offer to ${to} goes ahead at ${holdAt(hold.takes_effect_at)} unless you cancel it.`
      : state === "offering" ? `Your offer to ${to} goes ahead: its hold has ended.`
      : state === "running" ? `Your offer to ${to} waits for the running attempt to end.`
      : `Your offer to ${to} did not begin when the attempt ended: it ends shortly, saying why.`;
  } else {
    words = `Your offer to ${to} waits for an answer until ${dayTime(offer.expires_at)}.`;
  }
  return (
    <div className="flex flex-wrap items-center gap-2" role="status">
      <span>{words}</span>
      <button className="btn btn-sm" disabled={busy} onClick={onWithdraw}>{hold ? "Cancel" : "Withdraw"}</button>
    </div>
  );
}

/** Where a finding of a push's secret scan is, in the page's words: never its value. Its column tells two values on
 *  one line apart, and its line two of one shape in one file and commit: a
 *  line's number in the file, or a line's in the commit object; and a finding in a file, or of a file's name,
 *  says its path's place among the paths the commit changes, where the workspace gave one. Its commit is said whole:
 *  two commits whose ids share their first 12 characters, which a minute of one core's work can make, read alike cut
 *  there. */
export function findingWords(f: SecretFinding): string {
  const at = f.commit ? ` (commit ${f.commit})` : "";
  const col = typeof f.column === "number" ? `, at character ${f.column}` : "";
  const n = typeof f.line === "number" ? f.line : null;
  const p = typeof f.place === "number" ? f.place : null;
  if (f.kind === "commit") return `${f.shape}, in ${f.commit ? `commit ${f.commit}` : "a commit"}: its message or a header, such as its author${n === null ? "" : `, on line ${n} of the commit object`}${col}`;
  if (f.kind === "name") return `${f.shape}, in ${p === null ? "a file's name" : `the name of path ${p} of the commit`}: ${visible(f.file ?? "")}${col}${at}`;
  // a path may read as another once shown: a part hidden, a character written out or dropped, a path cut short, one
  // spelled in another Unicode form, or two spaces drawn as one; its place tells them apart, so it is always said
  //
  const file = f.file ?? "a file";
  const which = p !== null ? ` (path ${p} of the commit)` : "";
  return `${f.shape}, in ${visible(file)}${which}${n === null ? "" : `, on line ${n}`}${col}${at}`;
}

/** Why the page asks before a lift: a lift is for what is no secret, and a real one goes out of every commit instead. */
const LIFT_WHY = {
  owner: "Tick only what is not a secret. If one of these is a real secret, do not tick it: take it out of every " +
    "commit since the task began, since a later commit that deletes it still carries it. A lift covers that value at " +
    "that place on a line that reads the same, on any line of that file, in your later pushes to this repository, " +
    "and nothing else.",
  teammate: "Tick only what is not a secret. If one of these is a real secret, do not tick it: take it out of every " +
    "commit of your work on this task, since a later commit that deletes it still carries it. A lift covers that " +
    "value at that place on a line that reads the same, on any line of that file, in your later pushes to this " +
    "repository, and nothing else; the owner cannot lift it for you.",
};

/** A push of this task's that its secret scan held, with each finding,
 *  never a value, and, while the track is on, a tick for each one not lifted, none ticked at first, and Lift the ones
 *  ticked with a confirmation made for those findings on the task (liftDigest): on the owner's task its newest
 *  offer's push, on a task received the teammate's own push of their work. A real secret beside a made-up one is a
 *  finding of its own, left unticked. Once each is lifted, what
 *  happens next is said: the owner hands the task again; the teammate's work goes back at the next round. */
function SecretHoldPart({ hold, task, off, busy, onLifted }: {
  hold: SecretHold; task: HandOffTask; off: string | null; busy: boolean; onLifted(): void;
}) {
  const [asking, setAsking] = useState<{ ways: ConfirmWays | null; chosen: string[]; digest: string } | null>(null);
  const [ticked, setTicked] = useState<string[]>([]);
  const [error, setError] = useState("");
  const open = hold.findings.filter((f) => !f.lifted);
  const chosen = ticked.filter((id) => open.some((f) => f.finding === id));
  const tick = (id: string, on: boolean) => setTicked((t) => (on ? [...t.filter((x) => x !== id), id] : t.filter((x) => x !== id)));
  const ask = async () => {
    setError("");
    const these = chosen;
    try {
      const digest = await liftDigest(these);
      setAsking({ ways: null, chosen: these, digest });
      setAsking({ ways: await confirmWays(), chosen: these, digest });
    } catch (e) { setError(message(e)); setAsking(null); }
  };
  // the findings ticked when it was asked, and no other: a refusal is said where it was asked, and the pane reads the
  // hold again
  const lifted = async (confirmation?: string) => {
    try {
      await TaskOffersApi.liftSecret(task.id, asking?.chosen ?? [], confirmation ?? "");
    } catch (e) {
      if (deadEnd(e)) { setAsking(null); setError(message(e)); onLifted(); return; }
      throw e;
    }
    setAsking(null);
    setTicked([]);
    onLifted();
  };
  const own = hold.side === "owner";
  return (
    <div role="group" aria-label="Held by the secret scan" className="space-y-1">
      <div>
        {own ? "This task's hand-off was held: what its push would carry holds something shaped like a secret."
          : "Your work on this task is not going back to its owner: what your push would carry holds something shaped like a secret."}
      </div>
      <ul className="space-y-0.5 pl-1">
        {hold.findings.map((f) => (
          <li key={f.finding}>
            {f.lifted || off !== null ? <span>{findingWords(f)}{f.lifted ? " (lifted)" : ""}</span> : (
              <label className="flex items-start gap-1.5">
                <input type="checkbox" className="mt-1" checked={chosen.includes(f.finding)} disabled={busy || asking !== null}
                  onChange={(e) => tick(f.finding, e.target.checked)} />
                <span>{findingWords(f)}</span>
              </label>
            )}
          </li>
        ))}
      </ul>
      {open.length > 0 ? <div className="text-[12px] text-muted">{LIFT_WHY[hold.side]}</div> : (
        <div className="text-muted">{own ? "Each is lifted: hand the task again to push it." : "Each is lifted: your work goes back at the next round."}</div>
      )}
      {off === null && open.length > 0 && asking === null && (
        <button className="btn btn-sm" disabled={busy || chosen.length === 0} onClick={() => void ask()}>
          {chosen.length > 0 ? `Lift the ${chosen.length === 1 ? "one" : chosen.length} ticked` : "Tick what is not a secret"}
        </button>
      )}
      {asking?.ways && (
        <ConfirmIt key={asking.digest} change={{ what: "task-secret-lift", label: `Lifting ${asking.chosen.length === 1 ? "one finding" : `${asking.chosen.length} findings`} that held this task's push`, action: "Lift them", subject: `task-secret-lift:${task.id}:${asking.digest}`, ways: asking.ways }}
          onConfirmed={lifted} onCancel={() => setAsking(null)} />
      )}
      {error && <div role="alert" className="text-fail">{error}</div>}
    </div>
  );
}

/** On a task's pane: Hand to a teammate (an address, the teammate notice, a confirmation, then the hold with Cancel);
 *  on a handed task, Take back; Confirm for custody, and custody's crossing tick, not offered on a task whose crossings
 *  a press ended (task.crossings_ended). While the track is off, why, and beside it Take back, Withdraw or Cancel on a
 *  hand-off of the task that stands. While the task is with the teammate, their task's state in three words and its
 *  step, as the owner's accepted offer carries them. What a return brings is not shown here: no shape carries it to
 *  this part, and the task's pane shows the task's steps, their evidence and its moves. */
export function TaskHandOff(props: TaskHandOffProps) {
  // each task's part keeps a state of its own: a form, a draft or a confirmation opened on one task never shows on
  // another's pane, where it would confirm the first task's change
  return (
    <TaskPart key={props.task.id} task={props.task} onChanged={props.onChanged} onOfferShown={props.onOfferShown} />
  );
}

function TaskPart({ task, onChanged, onOfferShown }: TaskHandOffProps) {
  const [made, setMade] = useState<OfferView[] | null>(null);
  // the holds of this user's pushes a secret scan found something in, from the offers read (absent from an app before
  // them); this task's, if any, with a tick for each finding and Lift for those ticked
  const [secretHolds, setSecretHolds] = useState<SecretHold[]>([]);
  // the track off on the deployment: the read's `off`, in the server's words, shown in place of every control that
  // starts or advances a hand-off, beside what ends or undoes one of the task's that stands
  const [off, setOff] = useState<string | null>(null);
  // any other failure of the read, said with Try again, over what was last read
  const [failed, setFailed] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");               // what the last change answered, said once
  const [busy, setBusy] = useState(false);
  // Hand to a teammate: its form, open from the press until it is confirmed or closed (point 12's text over it all
  // that time), the address typed, and the draft its confirmation names, with the account's ways to confirm
  const [formOpen, setFormOpen] = useState(false);
  const [address, setAddress] = useState("");
  const [draft, setDraft] = useState<{ id: string; ways: ConfirmWays } | null>(null);
  // Confirm for custody, and custody's crossing tick: each asked from the press until it is confirmed or cancelled
  // (point 12's text over it all that time), its ways to confirm once read; then the tick's hold, said until the pane
  // shows the tick, and whether its time has come
  const [confirming, setConfirming] = useState<{ ways: ConfirmWays | null } | null>(null);
  const [ticking, setTicking] = useState<{ ways: ConfirmWays | null } | null>(null);
  const [crossHold, setCrossHold] = useState<OfferHold | null>(null);
  const [crossDue, setCrossDue] = useState(false);
  // whether the owner has a custodian set, from the custody read; a read that fails keeps what it last said
  const [custodian, setCustodian] = useState(false);

  const load = useCallback(async () => {
    try {
      const got = await TaskOffersApi.offers();
      setMade(Array.isArray(got?.made) ? got.made : []);
      setSecretHolds(Array.isArray(got?.secret_holds) ? got.secret_holds : []);
      setOff(offWords(got));
      setFailed(null);
    } catch (e) {
      setFailed(message(e));
      return;
    }
    try {
      const got = await TaskOffersApi.custody();
      if (offWords(got) !== null) setOff(offWords(got));
      const own = got?.as_owner ?? null;
      setCustodian(own !== null && CUSTODIAN_SET.includes(own.state));
    } catch { /* a custody read that fails keeps what the last one said */ }
  }, []);
  // read again whenever the pane shows the task in another state: an offer's hold ending or its answer moves it
  useEffect(() => { void load(); }, [load, task.state, task.offer?.id, task.offer?.state]);
  // and as the owner comes back to the page: a custodian set in the account dialog meanwhile, or an answer
  useEffect(() => {
    const again = () => { if (document.visibilityState === "visible") void load(); };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => { window.removeEventListener("focus", again); document.removeEventListener("visibilitychange", again); };
  }, [load]);
  // and, while the task is with the teammate, every THEIRS_EVERY_MS the page is shown: its own state does not move
  // meanwhile, and the teammate's task's state said with it does
  useEffect(() => {
    if (task.state !== "handed_over") return;
    const every = setInterval(() => { if (document.visibilityState === "visible") void load(); }, THEIRS_EVERY_MS);
    return () => clearInterval(every);
  }, [load, task.state]);
  // the tick's hold as the task's view lists it, with its Cancel, on the pane (TaskHolds); this part's own copy,
  // from the route's answer, is let go once the view carries it, so the hold has one Cancel, and is not listed here
  // again once the pane no longer lists it, cancelled or ended
  const viewCross = (task.holds ?? []).find((h) => h.kind === "task.crossing") ?? null;
  const carried = !!crossHold && (task.holds ?? []).some((h) => h.id === crossHold.id);
  useEffect(() => { if (carried) setCrossHold(null); }, [carried]);
  // the tick's hold reaches its time, whichever lists it: once, the offers are read, which settles the user's holds,
  // then the pane reads the task, which carries the tick once the app applied it
  const dueAt = (crossHold ?? viewCross)?.takes_effect_at ?? null;
  useEffect(() => {
    if (dueAt === null || crossDue) return;
    const wait = setTimeout(() => { setCrossDue(true); void load().then(onChanged); },
      Math.max(0, dueAt * 1000 - Date.now()) + 1000);
    return () => clearTimeout(wait);
  }, [dueAt, crossDue, load, onChanged]);

  const mine = (made ?? []).filter((o) => o.task_id === task.id);
  const standing = mine.find((o) => o.id === task.offer?.id && STANDING.includes(o.state))
    ?? mine.find((o) => STANDING.includes(o.state)) ?? null;
  // whether this part shows the task's standing offer with its own Cancel or Withdraw (below), the track on or off
  const showsOffer = made !== null && !task.continued_from && task.state !== "handed_over" && task.state !== "returning"
    && (standing !== null || (task.state === "offering" && !!task.offer?.id));
  useEffect(() => {
    onOfferShown?.(showsOffer);
    return () => onOfferShown?.(false);
  }, [showsOffer, onOfferShown]);

  /** One of the owner's changes made with the sign-in alone. A refusal's detail is said; done or refused, the offers
   *  are read again and the pane reads the task again, since a refusal can say the task moved on (its version differs)
   *  or the track went off. */
  const change = async (fn: () => Promise<string>) => {
    setBusy(true); setError(""); setSaid("");
    try { setSaid(await fn()); } catch (e) { setError(message(e)); }
    finally { setBusy(false); await load(); onChanged(); }
  };
  /** After a refused change asked through a form or ConfirmIt, which says the refusal where it was asked: the offers
   *  and the task read again, for the same reasons. */
  const reread = () => { void load(); onChanged(); };

  const openForm = () => { setFormOpen(true); setAddress(""); setDraft(null); setError(""); setSaid(""); };
  const closeForm = () => { setFormOpen(false); setDraft(null); };
  const makeDraft = async () => {
    setBusy(true); setError("");
    try {
      // the draft's answer is the same whether or not the address is someone's: the page reads its id alone
      const { offer } = await TaskOffersApi.offer(task.id, task.state_version, address.trim());
      setDraft({ id: offer.id, ways: await confirmWays() });
    } catch (e) { setError(message(e)); reread(); } finally { setBusy(false); }
  };
  // a refusal of the confirmation itself shows in ConfirmIt, which stays open to try again; one that can never succeed
  // (the draft gone, the task changed) goes back to the form, the address as typed, saying why
  const offerConfirmed = async (confirmation?: string) => {
    if (!draft) return;
    try {
      await TaskOffersApi.confirmOffer(draft.id, confirmation ?? "");
    } catch (e) {
      reread();
      if (!deadEnd(e)) throw e;
      setDraft(null);
      setError(message(e));
      return;
    }
    // the offer as the offers read lists it says the rest: its hold's time, or what it waits for
    closeForm();
    await load();
    onChanged();
  };

  const askCustodyConfirm = async () => {
    setError(""); setSaid(""); setConfirming({ ways: null });
    try { setConfirming({ ways: await confirmWays() }); } catch (e) { setError(message(e)); setConfirming(null); }
  };
  // a version that differs is read again, so ConfirmIt's next try is made against the task as it stands
  const custodyConfirmed = async (confirmation?: string) => {
    try { await TaskOffersApi.confirmForCustody(task.id, task.state_version, confirmation ?? ""); } catch (e) { reread(); throw e; }
    setConfirming(null);
    onChanged();
  };

  const tick = async () => {
    setError(""); setSaid(""); setCrossHold(null); setCrossDue(false); setTicking({ ways: null });
    try { setTicking({ ways: await confirmWays() }); } catch (e) { setError(message(e)); setTicking(null); }
  };
  const crossConfirmed = async (confirmation?: string) => {
    let answer: { hold: OfferHold | null };
    try { answer = await TaskOffersApi.custodyCross(task.id, task.state_version, confirmation ?? ""); } catch (e) { reread(); throw e; }
    setTicking(null);
    setCrossHold(answer.hold ?? null);
    onChanged();
  };

  const retry = failed !== null && (
    <div role="alert" className="flex flex-wrap items-center gap-2 text-fail">
      <span>{failed}</span>
      <button className="btn btn-sm" onClick={() => void load()}>Try again</button>
    </div>
  );
  // nothing is offered before a read answers
  if (made === null) return retry ? <section aria-label="Teammate" className="text-[12.5px]">{retry}</section> : null;
  // the track off: why, in the server's words; a refusal that only repeats it is not said again beneath it
  const offNote = off !== null && <div role="status" className="text-muted">{off}</div>;
  const notes = (
    <>
      {said && <div role="status" className="text-muted">{said}</div>}
      {error && error !== off && <div role="alert" className="text-fail">{error}</div>}
      {retry}
    </>
  );
  const held = secretHolds.find((h) => h.task_id === task.id) ?? null;
  const heldPart = held && (
    <SecretHoldPart hold={held} task={task} off={off} busy={busy} onLifted={() => { void load(); onChanged(); }} />
  );
  if (task.continued_from) {
    return (
      <section aria-label="Teammate" className="space-y-2 text-[12.5px]">
        {offNote || (
          <div className="text-muted">
            This task came from a teammate, so it can only go back to them: it cannot be handed on or confirmed for
            custody.
          </div>
        )}
        {heldPart}
        {notes}
      </section>
    );
  }

  const last = [...mine].sort((a, b) => b.created_at - a.created_at)[0];
  const ended = !standing && last ? endedWords(last) : null;
  const custodyOpen = CUSTODY_STATES.includes(task.state);
  // the tick's hold, said here until the pane's view of the task carries it, or shows the tick
  const crossWaits = crossHold && !carried && !task.custody_cross ? crossHold : null;
  // the tick on its way: asked, held by this part's copy, or held as the task's view lists it
  const crossPending = ticking !== null || crossWaits !== null || (viewCross !== null && !task.custody_cross);
  const withdraw = (id: string) => change(async () => { await TaskOffersApi.withdraw(id); return ""; });
  const takeBack = () => change(async () => { await TaskOffersApi.takeBack(task.id); return ""; });
  // what the app says of a hand-off while the task is with the teammate or on its way back, as the accepted offer's
  // own why: that the custodian keeps it, or that the teammate's workspace does not answer, so Take back waits
  const accepted = mine.find((o) => o.state === "accepted") ?? null;
  const handNote = accepted?.why ? <div className="text-muted">{visible(accepted.why)}</div> : null;
  const theirs = theirWords(accepted);

  // what ends or undoes a hand-off of the task that stands, or says where it is, the track on or off; then, only while
  // it is on, what starts one
  let handOff: ReactNode = null;
  if (task.state === "handed_over") {
    handOff = (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <span>Handed to a teammate.</span>
          {theirs && <span>{theirs}</span>}
          <button className="btn btn-sm" disabled={busy} onClick={() => void takeBack()}>Take back</button>
        </div>
        {handNote}
      </>
    );
  } else if (task.state === "returning") {
    handOff = <><div>Coming back from your teammate: your workspace checks their work before the task goes on.</div>{handNote}</>;
  } else if (standing) {
    handOff = <StandingOffer offer={standing} state={task.state} busy={busy} onWithdraw={() => void withdraw(standing.id)} />;
  } else if (task.state === "offering") {
    // an offer stands that the read did not list (it lists the owner's newest offers): withdrawn by its id
    const id = task.offer?.id;
    handOff = (
      <div className="flex flex-wrap items-center gap-2">
        <span>An offer of this task stands.</span>
        {id && <button className="btn btn-sm" disabled={busy} onClick={() => void withdraw(id)}>Withdraw</button>}
      </div>
    );
  } else if (off !== null) {
    // nothing that starts a hand-off while the track is off: its routes refuse then
  } else if (!task.repo_address) {
    handOff = <div className="text-muted">A task with no repository address cannot be handed to a teammate.</div>;
  } else if (HANDABLE.includes(task.state)) {
    handOff = (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-sm" aria-expanded={formOpen} disabled={busy} onClick={() => (formOpen ? closeForm() : openForm())}>Hand to a teammate</button>
        </div>
        <RiskTerms point={12} kind="control" open={formOpen} />
        {formOpen && (
          <div className="rounded-lg border border-line p-3">
            {task.state === "running" && (
              <div className="mb-2 text-muted">This task is working now. Confirming the offer stops that attempt, and the offer can begin only once it ends.</div>
            )}
            {draft === null ? (
              <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void makeDraft(); }}>
                <input type="email" aria-label="Your teammate's email address" autoComplete="off" className="input h-7 w-64"
                  placeholder="Your teammate's email address" value={address} onChange={(e) => setAddress(e.target.value)} />
                <button className="btn btn-sm btn-primary" disabled={busy || !address.trim()}>
                  {busy ? <Loader2 size={12} className="animate-spin" /> : null} Offer it
                </button>
                <button type="button" className="btn btn-sm" disabled={busy} onClick={closeForm}>Close</button>
              </form>
            ) : (
              <>
                <div role="status">{SEEN}</div>
                <ConfirmIt key={draft.id} change={{ what: "task-offer", label: "Handing this task to a teammate", action: "Hand it over", subject: `task-offer:${draft.id}`, ways: draft.ways }}
                  onConfirmed={offerConfirmed} onCancel={closeForm} />
              </>
            )}
          </div>
        )}
      </>
    );
  }

  // custody's controls, on the owner's own task with an address, while nothing of the task is with a teammate and the
  // track is on: shown when a custodian is set, or where the task is confirmed or ticked already, to
  // say so
  const custodyShown = custodian || task.custody_confirmed || task.custody_cross;
  const custody = off === null && custodyOpen && !!task.repo_address && !standing && custodyShown && (
    <>
      <div role="group" aria-label="Confirm for custody" className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span>{task.custody_confirmed ? "Confirmed for custody, as it stands." : "Not confirmed for custody, so no custodian takes it."}</span>
          {!task.custody_confirmed && confirming === null && (
            <button className="btn btn-sm" disabled={busy} onClick={() => void askCustodyConfirm()}>Confirm for custody</button>
          )}
        </div>
        {!task.custody_confirmed && <div className="text-[12px] text-muted">{COVERS}</div>}
        <RiskTerms point={12} kind="control" open={confirming !== null} />
        {confirming?.ways && (
          <ConfirmIt key="custody-confirm" change={{ what: "task-custody-confirm", label: "Confirming this task for custody", action: "Confirm it", subject: `task-custody-confirm:${task.id}`, ways: confirming.ways }}
            onConfirmed={custodyConfirmed} onCancel={() => setConfirming(null)} />
        )}
      </div>
      {task.crossings_ended ? (
        <div className="text-muted">A press ended this task's crossings, so custody keeps it in the group it is on.</div>
      ) : (
        <div role="group" aria-label="Custody's crossing" className="space-y-1">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={task.custody_cross || crossPending}
              disabled={busy || task.custody_cross || crossPending} onChange={() => void tick()} />
            Let custody take this task between your personal plans and a Team organisation's seats
          </label>
          <div className="text-[12px] text-muted">
            Only the organisation this task is bound to, if it is bound, and never into or out of an Enterprise
            organisation's seats. Once ticked, it stays ticked.
          </div>
          <RiskTerms point={12} kind="control" open={ticking !== null} cases={["custody_cross"]} />
          {ticking?.ways && (
            <ConfirmIt key="custody-cross" change={{ what: "task-custody-cross", label: "Letting custody take this task across groups", action: "Tick it", subject: `task-custody-cross:${task.id}`, ways: ticking.ways }}
              onConfirmed={crossConfirmed} onCancel={() => setTicking(null)} />
          )}
          {crossWaits && (
            <div className="flex flex-wrap items-center gap-2" role="status">
              {crossDue ? <span>Its hold has ended: the tick takes effect once the app applies it.</span> : (
                <>
                  <span>It takes effect at {holdAt(crossWaits.takes_effect_at)} unless you cancel it.</span>
                  <button className="btn btn-sm" disabled={busy}
                    onClick={() => void change(async () => { await Api.cancelHold(crossWaits.id); setCrossHold(null); return "Cancelled: the tick does not take effect."; })}>
                    Cancel
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );

  // a task done or stopped, with nothing of the track to say, gives the pane nothing
  if (!handOff && !ended && !custody && !said && !error && !retry && !offNote && !heldPart) return null;
  return (
    <section aria-label="Teammate" className="space-y-2 text-[12.5px]">
      {offNote}
      {handOff && <div role="group" aria-label="Hand to a teammate" className="space-y-1">{handOff}</div>}
      {ended && <div className="text-muted">{ended}</div>}
      {heldPart}
      {custody}
      {notes}
    </section>
  );
}

export interface OffersWaitingProps {
  /** the teammate's own agents, of which Accept into... offers those on the offer's repository with no open task */
  agents: Agent[];
  onChanged(): void;
}

/** The name an offer's repository (`owner/name`) has in a workspace, which names a clone by the last part of its
 *  address (the workspace's check_url): what an agent's `repo` is compared with; null when the offer names none. */
function cloneName(slug: string | null): string | null {
  return slug ? slug.slice(slug.lastIndexOf("/") + 1) : null;
}

/** The agents Accept into... offers for an offer: those not retired whose repository has the offer's repository's
 *  name, its case aside (every one with a repository where the offer names none). The server compares each with the
 *  task's own repository, which the offer does not carry, and refuses the rest with why, as it does an agent with an
 *  open task, which the page cannot see. */
function fitting(agents: Agent[], offer: OfferView): Agent[] {
  const name = cloneName(offer.repo_slug)?.toLowerCase() ?? null;
  return agents.filter((a) => !a.retired && !!a.repo && (name === null || a.repo.toLowerCase() === name));
}

/** What the packet kept with a received offer holds, as the offer's digest covers it: the owner's decisions, the paths its runs must not touch and the
 *  commands it knows reach outside the machine, as the owner's words; then, as information, the steps the owner did
 *  before the hand-off and what reached outside, each as recorded. An acceptance makes the received task from the
 *  owner's task itself, refused where that task is no longer offering at the state_version the offer was made at (its
 *  fence). A part with nothing in it is not shown; every text goes through visible(). */
function PacketParts({ packet }: { packet: OfferPacket }) {
  const { decisions, protected: paths, external, done_steps: done, outward_effects: effects } = packet;
  const info = done.length > 0 || effects.length > 0;
  if (!decisions.length && !paths.length && !external.length && !info) return null;
  return (
    <div aria-label="What the task's packet holds" className="mt-2 space-y-1.5 text-[12px]">
      {decisions.length > 0 && (
        <div>
          <div className="text-muted">The owner's decisions, which go with the task:</div>
          <ul className="mt-0.5 space-y-0.5">
            {decisions.map((d) => <li key={d.n} className="whitespace-pre-wrap break-words">{d.n}. {visible(d.text)}</li>)}
          </ul>
        </div>
      )}
      {paths.length > 0 && (
        <div className="break-words"><span className="text-muted">Paths its runs must not touch: </span>
          <span className="font-mono">{paths.map((p) => visible(p)).join(", ")}</span></div>
      )}
      {external.length > 0 && (
        <div className="break-words"><span className="text-muted">Commands it knows reach outside the machine: </span>
          <span className="font-mono">{external.map((x) => visible(x)).join(", ")}</span></div>
      )}
      {info && (
        <div className="text-muted">
          <div>As information, from the owner's task:</div>
          {done.length > 0 && <div>Done before the hand-off: step{done.length > 1 ? "s" : ""} {done.join(", ")}.</div>}
          {effects.length > 0 && (
            <ul aria-label="What reached outside the machine" className="list-disc pl-5">
              {effects.map((e, i) => (
                <li key={i} className="break-words">
                  <span className="font-mono">{visible([e.tool, e.kind, e.target].filter((v) => !!v).join(" "))}</span>
                  {e.state ? `: ${visible(e.state.replace(/_/g, " "))}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** One offer waiting for this teammate: what it holds, each of the owner's checks to adopt or leave, the packet's
 *  decisions, paths, commands and information (PacketParts), the files a next run would load that its commit changes,
 *  then Accept into... (point 13's text over it each time it opens) and Decline. No confirmation is asked for an
 *  acceptance: the contract names none. */
function WaitingOffer({ offer, agents, busy, onAccept, onDecline }: {
  offer: OfferView; agents: Agent[]; busy: boolean; onAccept: (agent: Agent, checks: number[]) => void; onDecline: () => void;
}) {
  const [adopted, setAdopted] = useState<number[]>([]);
  const [accepting, setAccepting] = useState(false);
  const [agentId, setAgentId] = useState("");
  const fit = fitting(agents, offer);
  const chosen = fit.find((a) => a.id === agentId) ?? null;
  const name = cloneName(offer.repo_slug);
  // the text's parts for this acceptance as it stands: an adopted check, and an agent whose account is in another group
  // than the task's, which the server names
  const cases = ["offer", ...(adopted.length ? ["adopted_check"] : []),
    ...(chosen && offer.other_group_agents.includes(chosen.id) ? ["other_group"] : [])];
  const from = offer.from_email ? visible(offer.from_email) : null;
  const adopt = (n: number) => setAdopted((a) => (a.includes(n) ? a.filter((x) => x !== n) : [...a, n].sort((x, y) => x - y)));
  return (
    <div role="group" aria-label={`Offer of ${visible(offer.title)}`} className="mb-3 rounded-lg border border-line p-3 last:mb-0">
      <div><span className="font-medium">{from ?? "A teammate"}</span> offers you a task: <span className="font-medium">{visible(offer.title)}</span></div>
      <div className="mt-1 whitespace-pre-wrap break-words text-fg-dim">{visible(offer.goal)}</div>
      <div className="mt-1 break-all text-[12px] text-muted">
        Repository <span className="font-mono">{offer.repo_slug ? visible(offer.repo_slug) : "not named"}</span>, branch{" "}
        <span className="font-mono">{visible(offer.branch)}</span>
        {offer.commit ? <>, commit <span className="font-mono">{offer.commit}</span></> : null}.
      </div>
      <ol className="mt-2 space-y-1.5">
        {offer.steps.map((s) => (
          <li key={s.n}>
            <div className="break-words">{s.n}. {visible(s.objective)}</div>
            {s.check !== null && (
              <div className="ml-4 mt-1">
                <div className="text-[12px] text-muted">Its check, word for word:</div>
                <pre aria-label={`Step ${s.n}'s check`} className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg">{visible(s.check)}</pre>
                <label className="mt-1 flex items-center gap-2 text-[12px]">
                  <input type="checkbox" aria-label={`Adopt step ${s.n}'s check`} checked={adopted.includes(s.n)} onChange={() => adopt(s.n)} /> Adopt this check
                </label>
              </div>
            )}
          </li>
        ))}
      </ol>
      {offer.steps.some((s) => s.check !== null) && (
        <div className="mt-1 text-[12px] text-muted">A check is your teammate's command. One you adopt runs in your workspace, as your user.</div>
      )}
      {offer.packet && <PacketParts packet={offer.packet} />}
      {offer.loaded_changes.length > 0 ? (
        <div role="note" aria-label="Files a next run would load" className="mt-2 rounded border border-wait/50 bg-wait/[0.05] p-2">
          <div>The offered commit changes files a next run would load:</div>
          <ul className="mt-1 list-disc pl-5 font-mono text-[12px]">
            {offer.loaded_changes.map((f) => <li key={f} className="break-all">{visible(f)}</li>)}
          </ul>
        </div>
      ) : !offer.why && (
        // an empty list says none only with nothing beside it: the server sends one empty, with why, while this user's
        // workspace has not read the files yet
        <div className="mt-2 text-[12px] text-muted">The offered commit changes no file a next run would load.</div>
      )}
      {offer.why && <div className="mt-1 text-[12px] text-wait">{visible(offer.why)}</div>}
      <RiskTerms point={13} kind="control" open={accepting} owner={from ?? "your teammate"} cases={cases} />
      {accepting ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {fit.length === 0 ? (
            <span className="text-muted">
              {name ? <>No agent of yours that can take it has a repository named <span className="font-mono">{visible(name)}</span>.</>
                : "No agent of yours that can take it has a repository."}
            </span>
          ) : (
            <select aria-label="The agent to accept it into" className="input !h-7 !py-0 text-[12px]" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              <option value="">Choose an agent</option>
              {fit.map((a) => <option key={a.id} value={a.id}>{visible(a.name)}</option>)}
            </select>
          )}
          <button className="btn btn-sm btn-primary" disabled={busy || !chosen} onClick={() => { if (chosen) onAccept(chosen, adopted); }}>Accept</button>
          <button className="btn btn-sm" onClick={() => setAccepting(false)}>Cancel</button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => { setAccepting(true); setAgentId(""); }}>Accept into...</button>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={onDecline}>Decline</button>
        </div>
      )}
    </div>
  );
}

/** How often the offers are read again while the server says it has not read an offer's files yet (its `why`): it
 *  reads them once the teammate's workspace runs. */
const UNREAD_EVERY_MS = 5000;

/** In the Agents panel: the offers that wait for this user, each with the owner's checks word for word (to adopt or
 *  leave) and the changes the offered commit makes to files a next run would load, with Accept into... and Decline.
 *  While the track is off: why, and each offer that waits with Decline alone, since an acceptance is refused then. */
export function OffersWaiting({ agents, onChanged }: OffersWaitingProps) {
  const [received, setReceived] = useState<OfferView[] | null>(null);
  // the track off on the deployment: the read's `off`, in the server's words, shown in place of every acceptance
  const [off, setOff] = useState<string | null>(null);
  // any other failure of the read, said with Try again, over what was last read
  const [failed, setFailed] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const got = await TaskOffersApi.offers();
      setReceived(Array.isArray(got?.received) ? got.received : []);
      setOff(offWords(got));
      setFailed(null);
    } catch (e) {
      setFailed(message(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  // an offer is listed while it waits for an answer; one that ended is no longer this teammate's to answer
  const waiting = (received ?? []).filter((o) => o.state === "offered");
  // read again every few seconds while the server says why it has not read an offer's files yet, and stop once it has
  const unread = waiting.some((o) => !!o.why);
  useEffect(() => {
    if (!unread) return;
    const every = setInterval(() => { void load(); }, UNREAD_EVERY_MS);
    return () => clearInterval(every);
  }, [unread, load]);
  // and as the teammate comes back to the page
  useEffect(() => {
    const again = () => { void load(); };
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [load]);
  const act = async (fn: () => Promise<string>) => {
    setBusy(true); setError(""); setSaid("");
    try { setSaid(await fn()); } catch (e) { setError(message(e)); }
    finally { setBusy(false); await load(); onChanged(); }
  };

  const retry = failed !== null && (
    <div role="alert" className="flex flex-wrap items-center gap-2 text-fail">
      <span>{failed}</span>
      <button className="btn btn-sm" onClick={() => void load()}>Try again</button>
    </div>
  );
  if (off !== null) {
    const decline = (o: OfferView) => act(async () => { await TaskOffersApi.decline(o.id); return "Declined."; });
    return (
      <div role="region" aria-label="Offers from teammates" className="border-b border-line px-3 py-2 text-[12px]">
        <div role="status" className="text-muted">{off}</div>
        {waiting.map((o) => (
          <div key={o.id} role="group" aria-label={`Offer of ${visible(o.title)}`}
            className="mt-1.5 flex flex-wrap items-center gap-2">
            <span>
              <span className="font-medium">{o.from_email ? visible(o.from_email) : "A teammate"}</span>
              {" offers you a task: "}
              <span className="font-medium">{visible(o.title)}</span>
            </span>
            <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void decline(o)}>Decline</button>
          </div>
        ))}
        {said && <div role="status" className="text-muted">{said}</div>}
        {error && error !== off && <div role="alert" className="text-fail">{error}</div>}
        {retry}
      </div>
    );
  }
  if (!waiting.length && !said && !error && !retry) return null;
  return (
    <div role="region" aria-label="Offers from teammates" className="border-b border-line px-3 py-2.5 text-[12.5px]">
      {waiting.map((o) => (
        <WaitingOffer key={o.id} offer={o} agents={agents} busy={busy}
          onAccept={(agent, checks) => void act(async () => { await TaskOffersApi.accept(o.id, agent.id, checks); return `Accepted into ${visible(agent.name)}.`; })}
          onDecline={() => void act(async () => { await TaskOffersApi.decline(o.id); return "Declined."; })} />
      ))}
      {said && <div role="status" className="text-muted">{said}</div>}
      {error && <div role="alert" className="text-fail">{error}</div>}
      {retry}
    </div>
  );
}
