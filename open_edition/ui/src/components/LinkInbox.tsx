// The owner's inbox from their linked sessions: what a linked session
// reports waits for them in Approvals, beside the held commands, and in that session's own conversation, with the same
// buttons in both. A prompt is the session's report of a prompt Claude Code showed it, never Claude Code's own: its
// sealed text is opened with the account's keys (e2e.ts) and shown as a question's words are, never raw, and it is
// answered with a passkey: "Approve", "Deny", the numbers of the options chosen, or their own words, each of which goes
// in the clear, as the page says first. Close takes the sign-in alone, carries no words and decides nothing; a prompt's
// Close hides it: it stays open, answerable until its deadline among the
// hidden and closed prompts the inbox lists below the open ones, and at its deadline its session's hook refuses it
// unanswered, where that hook is this version's and can check that with the ADE then, so that its session goes on,
// which the page never says happened: the server knows only that the deadline passed. An item to look at is decided
// where the session shows it, and a Close closes it, as before. A proposal is a session's words for another of their
// sessions, which reach it only as their own signed instruction, its first line naming the session that proposed it.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, ExternalLink, KeyRound, Loader2, Radio, X } from "lucide-react";
import { Api, ApiError, APP, server, toOwner, type LinkClosedPrompt, type LinkEvent, type LinkPrompt, type LinkProposal } from "../api";
import { useE2E, useE2EStore } from "../e2e";
import * as K from "../e2eKeys";
import { prepareSigned, signPrepared, textDigest, type Prepared, type Signed } from "../passkey";
import { hostLabel } from "../places";
import { unreachable, useStore } from "../store";
import { eventText, questionRule, refusalWords, type Checked } from "../textRules";
import { clock, dayTime, full } from "../when";
import { ago, closeKey, pinCheck, PinNotice, Question, SignBox, SIGN_ELSEWHERE, SIGN_OFFLINE, signsHere, UNAVAILABLE, useNeedsServer, visible, type PinWarning } from "./Channel";
import { eventSlotOf, shown, ShownText, useAccount, useOpened, type Opener, type Shown } from "./Sealed";
import { useSignerKnown } from "./SignOnMac";
import { CameBack, useAct } from "./Acting";

// ---- an answer's words, as the server takes them (link_commands.prompt_answer_form) --------------------------------

export const PROMPT_APPROVE = "Approve";
export const PROMPT_DENY = "Deny";
const NUMBERS = /^[1-9][0-9]{0,2}(,[1-9][0-9]{0,2})*$/;

/** Option numbers in the one form an answer takes them: each 1 to 999 with no leading zero, strictly ascending,
 *  joined by "," with no space ("1,4"). */
export function canonicalNumbers(text: string): boolean {
  if (!NUMBERS.test(text)) return false;
  const ns = text.split(",").map(Number);
  return ns.every((n, i) => i === 0 || n > ns[i - 1]);
}

/** The server's reads_as_fixed: words that read as Approve, Deny or a choice of options without being exactly one.
 *  Case folded with every space and . , ; : ! ? ' " taken out, approve, approved, deny or denied; or nothing but
 *  digits, spaces and , ; . without being option numbers in their one form. */
export function readsAsFixed(text: string): boolean {
  const bare = text.toLowerCase().replace(/[\s.,;:!?'"]/gu, "");
  if (["approve", "approved", "deny", "denied"].includes(bare)) return true;
  return /^[\p{Nd}\s,;.]+$/u.test(text) && !canonicalNumbers(text);
}

export type PromptAnswerForm = "approve" | "deny" | "numbers" | "words";
/** What an answer to a prompt is, as the server reads it, or null where it refuses it: an answer carries no field
 *  saying which button made it, only its text, so their own words may not read as a button's. */
export function promptAnswerForm(text: string): PromptAnswerForm | null {
  if (text === PROMPT_APPROVE) return "approve";
  if (text === PROMPT_DENY) return "deny";
  if (canonicalNumbers(text)) return "numbers";
  return readsAsFixed(text) ? null : "words";
}

export const OWN_WORDS_REFUSED = "Your own words may not read as Approve, Deny or a choice of options: use the buttons for those.";
/** Why their own words will not do as an answer to a prompt, said before anything is signed; null when they will. */
export const ownWordsProblem = (text: string) => (promptAnswerForm(text) === "words" ? null : OWN_WORDS_REFUSED);

// ---- a prompt's text, once opened -----------------------------------------------------------------------------------

export interface PromptOption { n: number; label: string; description: string }
export interface PromptQuestion { question: string; header: string; multiSelect: boolean; options: PromptOption[] }
/** A prompt's text once opened, as its session's client writes it (JSON): the tool, its whole input, and for a
 *  question (AskUserQuestion) its questions, their options numbered as the item gives them, 1 to N in order across all
 *  of them. `asks`: a question, answered by option numbers, never the word Approve; `questions` null where they are
 *  not in that form. */
export interface PromptItem { tool: string; input: unknown; asks: boolean; questions: PromptQuestion[] | null }

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The questions as numbered: each option by the number the item gives it (`n` or `number`), else the next one; null
 *  unless every question has options and the numbers rise from 1 to at most 999. */
function readQuestions(v: unknown): PromptQuestion[] | null {
  if (!Array.isArray(v) || v.length === 0) return null;
  const out: PromptQuestion[] = [];
  let last = 0;
  for (const q of v) {
    if (!isRecord(q) || typeof q.question !== "string" || !Array.isArray(q.options) || q.options.length === 0) return null;
    const options: PromptOption[] = [];
    for (const o of q.options) {
      const rec = isRecord(o) ? o : null;
      const label = typeof o === "string" ? o : rec && typeof rec.label === "string" ? rec.label : null;
      if (label === null) return null;
      const given = rec ? rec.n ?? rec.number : undefined;
      const n = given === undefined || given === null ? last + 1 : given;
      if (typeof n !== "number" || !Number.isInteger(n) || n <= last || n > 999) return null;
      last = n;
      options.push({ n, label, description: rec && typeof rec.description === "string" ? rec.description : "" });
    }
    out.push({ question: q.question, header: typeof q.header === "string" ? q.header : "", multiSelect: q.multiSelect === true, options });
  }
  return out;
}

/** A prompt's opened text as an item, or null where it is not one (its words are then shown as they are). */
export function parsePrompt(text: string): PromptItem | null {
  let v: unknown;
  try { v = JSON.parse(text); } catch { return null; }
  if (!isRecord(v) || typeof v.tool !== "string" || !v.tool) return null;
  const asks = (v.questions !== undefined && v.questions !== null) || v.tool === "AskUserQuestion";
  const given = v.questions ?? (isRecord(v.input) ? v.input.questions : undefined);
  return { tool: v.tool, input: v.input, asks, questions: asks ? readQuestions(given) : null };
}

/** An item to look at that Claude Code raised itself, not a tool call (a notification, a turn an API error ended, a
 *  model switch), as its session's client seals it: {event, words: {its own fields}, session_id, agent_id, deadline},
 *  or {event, deadline} alone where its words broke the rules. `words`: its words' string fields, key and value, in
 *  the order given, those that are empty left out. */
export interface RaisedItem { event: string; words: [string, string][] }

/** A look item's opened text as an item Claude Code raised, or null where it is not one. */
export function parseRaised(text: string): RaisedItem | null {
  let v: unknown;
  try { v = JSON.parse(text); } catch { return null; }
  if (!isRecord(v) || typeof v.event !== "string" || !v.event) return null;
  const words = isRecord(v.words) ? Object.entries(v.words).filter((w): w is [string, string] => typeof w[1] === "string" && w[1] !== "") : [];
  return { event: v.event, words };
}

/** One of its words as a question's words are shown: held to the rules a question's words are held to, and what is not
 *  shown as itself written out; where it breaks them, why, in its place. */
function Held({ text }: { text: string }) {
  const broken = questionRule(text);
  return broken ? <span className="italic text-muted">{refusalWords(broken)}</span> : <>{visible(text)}</>;
}

/** What Claude Code raised: its event, then each of its own words by name, never as JSON. */
function Raised({ r }: { r: RaisedItem }) {
  return (
    <>
      <div className="text-[12.5px]">{`Claude Code: ${visible(r.event)}`}</div>
      {r.words.length > 0 && (
        <dl className="mt-1 space-y-0.5 text-[12px]">
          {r.words.map(([key, value]) => (
            <div key={key} className="flex flex-wrap gap-x-1.5">
              <dt className="text-muted"><Held text={key} /></dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words text-fg"><Held text={value} /></dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

/** A prompt's or a proposal's text as the page shows it: sealed by the session that posted it, opened, and held to the
 *  rules a question's words are held to, since the owner's answer carries its digest; or in the clear as it came. */
export function itemShown(ev: LinkEvent, linkId: string, account: string, opener: Opener): Shown<string> {
  const check = (value: unknown): Checked<string> => {
    const t = eventText(value, ev);
    if (!t.ok) return t;
    const broken = questionRule(t.value);
    return broken ? { ok: false, refusal: broken } : t;
  };
  return shown(ev.text, eventSlotOf(ev, linkId, account), check, true, opener);
}

const REPORTED: Record<string, string> = {
  PostToolUse: "the session reported that its tool ran",
  PostToolUseFailure: "the session reported that its tool failed",
  Stop: "the session reported that its turn ended",
  StopFailure: "the session reported that its turn ended on an error",
  UserPromptSubmit: "the session reported a new prompt in its window",
};

/** What a session's report of its prompt says happened (closed_reason report:EVENT), as the page says it. */
const reportedWords = (reason: string) => REPORTED[reason.slice(7)] ?? "the session reported it";

/** How a prompt or a proposal was closed: by whom, never which answer won where the session shows it too. "owner" is a
 *  proposal's Close, an item to look at's, or a prompt's from before a Close hid prompts. */
export function closedWords(reason: string | null | undefined): string {
  if (!reason) return "Closed.";
  if (reason === "answer") return "Closed by your signed answer.";
  if (reason === "owner") return "Closed by you on the page, which decided nothing.";
  if (reason === "deadline") return "Closed at its deadline.";
  if (reason === "signed") return "Closed: you signed it as your instruction.";
  if (reason.startsWith("report:")) return `Closed: ${reportedWords(reason)}.`;
  return `Closed (${visible(reason)}).`;
}

/** A prompt out of the open list whose record names no close, as its session's conversation shows it: hidden by a
 *  Close, it is still open, answered among the hidden prompts in Approvals until its deadline. */
export const STILL_OPEN = "Still open: it waits for your answer in Approvals until its deadline.";
export const STILL_OPEN_LOOK = "Still open: it is answered where the session shows it.";

/** What happens at a hidden prompt's deadline, said as its session's hook's act, never the list's, and only as far as
 *  it holds: the hook refuses it only on the ADE's word that the deadline passed, so a hook from an older version, one
 *  that cannot reach the ADE in the half minute after the deadline, and one whose ADE's clock runs more than that
 *  behind the session's print nothing then (SETUP_A.md: "anything the hook cannot check"). */
export const UNANSWERED_THEN = "Unanswered by then, its session's hook refuses it and the session goes on, where that hook "
  + "is this version's and can check that with the ADE then; otherwise it stays in the session's window.";

/** The note under a prompt's Close, as Close now works: it hides the prompt, which stays open. `answers`: its text is a
 *  prompt's, or is not opened yet, so the page may answer it; `inbox`: in Approvals, above the hidden ones; elsewhere
 *  (its session's conversation) the note points to Approvals. What happens at the deadline is its session's hook's act,
 *  never the list's. An item to look at, which a Close closes as before, keeps the note it had then (its card says
 *  where it is answered). */
export function closeNoteWords(look: boolean, answers: boolean, inbox: boolean): string {
  if (look) return "Closing decides nothing: it takes this out of your list, and the prompt stays in the session's window.";
  const hidden = inbox ? "the hidden ones below" : "the hidden prompts in Approvals";
  const after = UNANSWERED_THEN;
  if (!answers) {
    // its text is not a call this version's hook posted, which posts a call whole: nothing is promised of its deadline
    return `Close decides nothing: it takes this prompt out of your open list, to ${hidden}; it is answered where the session shows it.`;
  }
  return `Close decides nothing: it takes this prompt out of your open list, and the session keeps waiting for your answer until its deadline; you can still answer it among ${hidden}. ${after}`;
}

/** How a prompt in the hidden and closed list ended, and when, as that list gives it (LinkClosedPrompt). */
export type LinkPromptEnd = Pick<LinkClosedPrompt, "ended" | "hidden_at" | "closed_at" | "closed_reason">;

/** A time in an item's words, its full time on hover: its day and clock, or (`clockOnly`) its clock alone. */
function At({ t, clockOnly = false }: { t: number; clockOnly?: boolean }) {
  return <span title={full(t)}>{clockOnly ? clock(t) : dayTime(t)}</span>;
}

/** How a prompt in the hidden and closed list ended, and when, as GET /api/ade/link/prompts/closed says (its `ended`):
 *  the owner's signed answer, their Close that hid it, its deadline, its session's report or a close on the page; never
 *  that the list decided what the session did. At its deadline the server knows only that the deadline passed with no
 *  answer of theirs: an older ade_link.py, a clock out of step, an ADE its session could not reach or a window that
 *  answered first all leave no refusal, and the record never says what the hook did, so the page
 *  says it went unanswered and no more. `asks`: a question, answered with its options, never
 *  the word Approve; `deadline`: its deadline, by the server's clock. */
function Ended({ end, look, form, deadline, asks }: { end: LinkPromptEnd; look: boolean; form: boolean; deadline: number; asks: boolean }) {
  const at = (t: number | null) => (t ? <> at <At t={t} /></> : null);
  const when = end.closed_at;
  let said: ReactNode;
  switch (end.ended) {
    case "waiting":
      // an item to look at, and a text not in a prompt's form, which no hook of this version posted: nothing promised
      said = look || !form
        ? <>Hidden{at(end.hidden_at)}: it is answered where the session shows it, and nothing here decides it.</>
        : <>Hidden{at(end.hidden_at)}, and waiting for your answer until its deadline{deadline ? <>, <At t={deadline} clockOnly /></> : null}{`: ${UNANSWERED_THEN.charAt(0).toLowerCase()}${UNANSWERED_THEN.slice(1)}`}</>;
      break;
    case "approved":
      said = asks ? <>Answered with your chosen options{at(when)}.</> : <>Approved by your signed answer{at(when)}.</>;
      break;
    case "refused":
      said = <>Refused by your signed Deny{at(when)}.</>;
      break;
    case "words":
      said = <>Answered in your own words{at(when)}.</>;
      break;
    case "deadline": {
      const t = when ?? deadline;
      said = <>{look ? "Closed at its deadline" : "Unanswered by its deadline"}{t ? <>, <At t={t} /></> : null}.</>;
      break;
    }
    case "report":
      said = <>Closed by its session{at(when)}{end.closed_reason?.startsWith("report:") ? `: ${reportedWords(end.closed_reason)}` : ""}.</>;
      break;
    case "closed":
      said = <>Closed by you on the page{at(when)}, which decided nothing.</>;
      break;
    default:
      // a way of ending this page does not know: how its record says it closed
      said = <>{closedWords(end.closed_reason)}{when ? <>{" "}(<At t={when} />)</> : null}</>;
  }
  return <div role="note" aria-label="How it ended" className="mt-1 text-[11.5px] text-muted">{said}</div>;
}

/** What the page says when the server refuses an answer because the session's own report closed its prompt first. */
export const ANSWERED_THERE = "This prompt was answered where the session shows it: the session reported that before your answer came, so your answer was not stored.";

/** Why the server refused an answer to a prompt (409), from the link's own record of it: answered where the session
 *  shows it, its deadline, closed on the page, or answered already; null where that record does not say. */
async function refusedWords(e: unknown, p: LinkEvent & { link_id: string }): Promise<string | null> {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  try {
    let events = (await Api.linkTimeline(p.link_id)).events;
    if (!events.some((x) => x.seq === p.seq)) events = (await Api.linkTimeline(p.link_id, p.seq + 1)).events;
    const reason = events.find((x) => x.seq === p.seq)?.closed_reason ?? null;
    const reported = events.some((x) => x.kind === "note" && x.ref === p.seq && x.client_id === `report:${p.seq}`);
    if (reason?.startsWith("report:") || (!reason && reported)) return ANSWERED_THERE;
    if (reason === "deadline") return "This prompt's deadline passed before your answer came: your answer was not stored.";
    if (reason === "owner") return "This prompt was closed on the page before your answer came: your answer was not stored.";
    if (reason === "answer") return "This prompt was answered already: your answer was not stored.";
  } catch { /* the server's own words, then */ }
  return null;
}

// ---- a prompt ------------------------------------------------------------------------------------------------------

type Answering = { kind: "approve" | "deny" | "words" } | { kind: "numbers"; text: string };
const SUBMIT: Record<Answering["kind"], string> = {
  approve: "Review the approval", deny: "Review the denial", numbers: "Review the choice", words: "Review your answer",
};

/** What an answer sends in the clear, said before it is signed. */
function ClearNote({ a }: { a: Answering }) {
  const what = a.kind === "approve" ? `the word “${PROMPT_APPROVE}”. It goes`
    : a.kind === "deny" ? `the word “${PROMPT_DENY}”. It goes`
      : a.kind === "numbers" ? `the option numbers “${a.text}”. They go`
        : "your words, as you write them. They go";
  const after = a.kind === "deny" ? " Deny ends the session's turn, as a No without words in its window does."
    : a.kind === "words" ? " The session's model reads them as why you refused, and its turn goes on." : "";
  return (
    <div role="note" aria-label="In the clear" className="mb-1 rounded-md border border-wait/40 bg-wait/[0.06] px-2.5 py-1.5 text-[11.5px] text-fg-dim">
      {`Your answer is ${what} in the clear: the server keeps a signed answer as it reads it, and only the prompt's own text is sealed.${after}`}
    </div>
  );
}

/** A question prompt's options, numbered as the item gives them, chosen here: one for each question, or one or more
 *  where a question allows several. */
function Choices({ seq, questions, chosen, onChange }: { seq: number; questions: PromptQuestion[]; chosen: number[]; onChange: (next: number[]) => void }) {
  const pick = (q: PromptQuestion, n: number) => {
    const mine = new Set(q.options.map((o) => o.n));
    if (q.multiSelect) onChange(chosen.includes(n) ? chosen.filter((x) => x !== n) : [...chosen, n]);
    else onChange([...chosen.filter((x) => !mine.has(x)), n]);
  };
  return (
    <div className="mt-2 space-y-2">
      {questions.map((q, qi) => (
        <fieldset key={qi} className="rounded-md border border-line px-2.5 py-1.5">
          <legend className="px-1 text-[11px] text-muted">{`${q.header ? `${visible(q.header)}: ` : ""}${q.multiSelect ? "choose one or more" : "choose one"}`}</legend>
          <div className="whitespace-pre-wrap break-words text-[12.5px]">{visible(q.question)}</div>
          {q.options.map((o) => (
            <label key={o.n} className="mt-1 flex cursor-pointer items-start gap-2 text-[12.5px]">
              <input type={q.multiSelect ? "checkbox" : "radio"} name={`prompt-${seq}-${qi}`} className="mt-0.5 accent-[var(--color-accent)]"
                checked={chosen.includes(o.n)} onChange={() => pick(q, o.n)} />
              <span className="min-w-0 break-words">
                <span>{`${o.n}. ${visible(o.label)}`}</span>
                {o.description && <span className="block text-[11.5px] text-muted">{visible(o.description)}</span>}
              </span>
            </label>
          ))}
        </fieldset>
      ))}
    </div>
  );
}

/** The questions as numbered, where nothing is chosen here (an item to look at, a closed prompt). */
function Numbered({ questions }: { questions: PromptQuestion[] }) {
  return (
    <ul className="mt-1.5 space-y-1 text-[12px]">
      {questions.map((q, qi) => (
        <li key={qi}>
          <div className="whitespace-pre-wrap break-words">{visible(q.question)}</div>
          <ul className="pl-3 text-fg-dim">{q.options.map((o) => <li key={o.n} className="break-words">{`${o.n}. ${visible(o.label)}`}</li>)}</ul>
        </li>
      ))}
    </ul>
  );
}

/** A prompt's whole input as JSON, where its text has one (null, {} and [] included). */
const inputWords = (input: unknown) => JSON.stringify(input, null, 2);

/** Whether every question has its options chosen: one each, or at least one where it allows several. */
const chosenFully = (questions: PromptQuestion[], chosen: number[]) =>
  questions.every((q) => {
    const k = q.options.filter((o) => chosen.includes(o.n)).length;
    return q.multiSelect ? k >= 1 : k === 1;
  });

// ---- one press on Approve ------------------------------------------------------------------------------------------

/** How long a signed approval waits, its words shown, before it is sent: Undo in that time sends nothing. */
export const UNDO_SECONDS = 5;
/** How often an approval waiting for its press is prepared again: a nonce lives five minutes (Channel's REVIEW_LIFE). */
const PREPARE_EVERY = 240;
/** The oldest preparation a press signs, counted from when its nonce was asked for: the nonce's 300 seconds, less a
 *  margin for the touch, the unlock and Undo's window. What is signed is sent only within SEND_LIFE of the same moment. */
const PRESS_LIFE = 270;
/** The oldest a signed answer is sent, counted from when its nonce was asked for: past it the nonce has seconds left,
 *  and a phone that slept in Undo's window may come back long after. */
const SEND_LIFE = 295;
/** How soon a preparation that failed is tried again. */
export const RETRY_SECONDS = 15;
/** How long the words a locked tab's touch unlocked may take to open before the answer it signed is dropped. */
const OPEN_WAIT = 10;
export const ASKS_WORDS = "It asks you to choose: nothing was sent. Choose its options above, then answer.";
export const ASKS_THERE_WORDS = "It asks you to choose, in the session's window: nothing was sent.";
export const NOT_A_PROMPT_WORDS = "Its text is not in the form a prompt takes: nothing was sent.";
export const NOT_OPENED_WORDS = "Its words did not open here: nothing was sent.";
export const CHANGED_WORDS = "Its text changed after this approval was prepared: nothing was sent.";
export const STALE_WORDS = "Nothing was signed: this approval was prepared too long ago for its nonce to last until it is sent. It is prepared again now: press Approve once more.";
export const UNDONE_WORDS = "Not sent: you undid it.";
export const TOO_LATE_WORDS = "Too late to undo: it was already on its way.";
export const LATE_WORDS = "Not sent: it was signed too long ago for the server to take it. Press Approve again.";
export const RELOCKED_WORDS = "Its words were locked again before you read them: nothing was sent.";
export const LEFT_WORDS = "Your approval was not sent: its card closed before it went.";
export const HIDDEN_WORDS = "Not sent: this page was hidden before Undo's window ended. Press Approve again.";
/** A send the server never answered (the connection failed, or a gateway between): it may have been stored all the same. */
export const UNREACHABLE_SEND_WORDS = "It may not have reached the server: the connection failed before an answer came. If the prompt still waits once the list is read again, press Approve again.";
/** While the account's signers are read (useSignerKnown), said once it takes a moment. */
export const SIGNERS_WAIT_WORDS = "Approve waits: your account's Mac signers are being read.";
/** Where a link names an item (App.tsx's #item=<seq>) that the inbox does not hold among those still waiting. */
export const ITEM_GONE_WORDS = "The item the link names is not waiting here: it was answered or closed, or it is another account's.";
/** Beside Approve in a locked tab, before the press: what the one touch does, and that the words are read before it goes. */
export const LOCKED_PRESS_WORDS = `One press unlocks your keys and signs this approval; its words then show for ${UNDO_SECONDS} seconds with Undo before it is sent. Closing this view before then cancels it.`;

/** Words ended as a sentence. */
const sentence = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** A signed answer, the stored text it binds (its digest is in the payload), and when its nonce was asked for. */
type Held = { signed: Signed; text: string; at: number };

/** One press on Approve: the answer "Approve" prepared while `on` (its nonce, its payload naming the prompt and the
 *  stored text's digest, the link's pins), again every PREPARE_EVERY seconds and whenever the tab is shown again, so the
 *  press reaches the passkey's prompt with nothing awaited (Safari asks for a click per ceremony). A preparation that
 *  fails says why (`failed`) and is tried again after RETRY_SECONDS. A press signs only the preparation of the text the
 *  card holds now, and none older than PRESS_LIFE; once signed, it is not signed again (a later press gets a new one).
 *  While the keys are locked (`locked`), the same touch gives the PRF outputs that unlock them (K.signAndTouch), and
 *  the signed answer waits for the words to open (`opened`); then the words show for UNDO_SECONDS with Undo before it
 *  is sent (`send`, which answers why it was not, or null), the card busy until the send settles. Nothing is sent
 *  where the words do not open, ask for choices, are not a prompt, are no longer the text it binds, or are locked again
 *  before the window ends; where the page is hidden in the window; where the answer would reach the server past
 *  SEND_LIFE; or where the card goes during the touch or before the window ends (`left` says so). The signature binds
 *  the stored text's digest, the envelope where it is sealed, so it can be made before the words are read: the Undo
 *  window is the reading. */
function useOnePress(p: LinkPrompt, on: boolean, locked: boolean,
                     opened: { ok: boolean; failed: boolean; asks: boolean | null; numbered: boolean },
                     send: (signed: Signed) => Promise<string | null>, left: () => void) {
  const e2e = useE2E();
  const keys = useE2EStore((s) => s.view);
  // the prepared answer, the stored text it binds, and when its nonce was asked for
  const [ready, setReady] = useState<{ prepared: Prepared; warning: PinWarning | null; text: string; at: number } | null>(null);
  const [failed, setFailed] = useState("");
  const [again, setAgain] = useState(0);
  const [busy, setBusy] = useState(false);
  const [undoing, setUndoing] = useState<Held | null>(null);
  const [count, setCount] = useState(UNDO_SECONDS);
  const [said, setSaid] = useState("");
  // the answer a locked tab's touch signed, waiting for the words that touch opened, and how long they may take
  const waiting = useRef<Held | null>(null);
  const openWait = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // a touch under way; the signed answer in Undo's window, and whether the last one went; the text the card holds now,
  // and whether its words are open; the newest ways to send it, and to say it was not
  const pressing = useRef(false);
  const pending = useRef<Held | null>(null);
  const gone = useRef(false);
  const textNow = useRef(p.text);
  textNow.current = p.text;
  const openNow = useRef(opened.ok);
  openNow.current = opened.ok;
  const sendNow = useRef(send);
  sendNow.current = send;
  const leftNow = useRef(left);
  leftNow.current = left;
  useEffect(() => {
    if (!on) { setReady(null); setFailed(""); return; }
    let live = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let asked = 0;
    let done = 0;
    const make = async () => {
      clearTimeout(retry);
      const n = ++asked;
      const text = p.text;
      const at = Date.now();                              // before the nonce is asked for: its age counts from here
      try {
        const digest = await textDigest(text);
        if (!live) return;                                // its card is gone, or its text: no nonce is asked for
        const [prepared, warning] = await Promise.all([
          prepareSigned("answer", p.link_id, { text: PROMPT_APPROVE, ref: p.seq, digest }), pinCheck(p.link_id)]);
        if (live && n > done) { done = n; setReady({ prepared, warning, text, at }); setFailed(""); }
      } catch (e) {
        if (!live || n < asked) return;                   // a newer preparation is on its way
        setReady(null);
        setFailed(e instanceof Error ? e.message : String(e));
        retry = setTimeout(() => void make(), RETRY_SECONDS * 1000);
      }
    };
    void make();
    const t = setInterval(() => void make(), PREPARE_EVERY * 1000);
    // a tab shown again (a phone woken) prepares again at once: its interval may not have run while it was hidden
    const shownAgain = () => { if (document.visibilityState !== "hidden") void make(); };
    document.addEventListener("visibilitychange", shownAgain);
    return () => { live = false; clearInterval(t); clearTimeout(retry); document.removeEventListener("visibilitychange", shownAgain); };
  }, [on, p.link_id, p.seq, p.text, again]);
  // Undo's window begins: the signed answer is pending from now, set here and never by the window's effect, which may
  // run only after an Undo already cleared it
  // (a page already hidden opens none: its words would not be on the screen)
  const startUndo = (h: Held) => {
    if (document.visibilityState === "hidden") { setSaid(HIDDEN_WORDS); return; }
    pending.current = h; gone.current = false; setCount(UNDO_SECONDS); setUndoing(h);
  };
  // whether Undo's window, begun now, would end past SEND_LIFE: then no window opens, and nothing is sent
  const late = (h: Held) => Date.now() + UNDO_SECONDS * 1000 - h.at > SEND_LIFE * 1000;
  // the answer a locked tab's touch signed, once the words that touch opened are read: Undo's window where they are the
  // text it binds and a prompt that asks no choice, else nothing is sent
  useEffect(() => {
    const h = waiting.current;
    if (!h || (!opened.ok && !opened.failed)) return;
    waiting.current = null;
    clearTimeout(openWait.current);
    if (h.text !== p.text) setSaid(CHANGED_WORDS);
    else if (!opened.ok) setSaid(NOT_OPENED_WORDS);
    else if (opened.asks === false) { if (late(h)) setSaid(LATE_WORDS); else startUndo(h); }
    else setSaid(opened.asks ? (opened.numbered ? ASKS_WORDS : ASKS_THERE_WORDS) : NOT_A_PROMPT_WORDS);
  }, [opened.ok, opened.failed, opened.asks, opened.numbered, p.text]);   // eslint-disable-line react-hooks/exhaustive-deps
  // the card's text changed in Undo's window: what was signed binds the text before, so nothing is sent
  useEffect(() => {
    if (!pending.current || pending.current.text === p.text) return;
    pending.current = null;
    setUndoing(null);
    setSaid(CHANGED_WORDS);
  }, [p.text]);
  // the words locked again in Undo's window (the tab locked): what was not read is not sent
  useEffect(() => {
    if (!pending.current || opened.ok) return;
    pending.current = null;
    setUndoing(null);
    setSaid(RELOCKED_WORDS);
  }, [opened.ok]);
  // the page hidden in Undo's window (its words off the screen): nothing is sent. Listened for from the card's mount,
  // so no moment of the window goes unheard
  useEffect(() => {
    const hidden = () => {
      if (document.visibilityState !== "hidden" || !pending.current) return;
      pending.current = null;
      setUndoing(null);
      setSaid(HIDDEN_WORDS);
    };
    document.addEventListener("visibilitychange", hidden);
    return () => document.removeEventListener("visibilitychange", hidden);
  }, []);
  // Undo's window, counted down: the pending answer sent when it ends, the card busy until that settles, unless Undo
  // cleared it, its text changed, its words were locked again, or it would reach the server too late; a send that
  // fails says so, and asks for another press
  useEffect(() => {
    if (!undoing) return;
    const tick = setInterval(() => setCount((n) => Math.max(n - 1, 1)), 1000);
    const t = setTimeout(() => {
      const h = pending.current;
      pending.current = null;
      setUndoing(null);
      if (!h) return;
      if (h.text !== textNow.current) { setSaid(CHANGED_WORDS); return; }
      if (!openNow.current) { setSaid(RELOCKED_WORDS); return; }
      if (Date.now() - h.at > SEND_LIFE * 1000) { setSaid(LATE_WORDS); return; }
      gone.current = true;
      setBusy(true);
      void sendNow.current(h.signed).then((why) => {
        if (why === null) return;
        gone.current = false;
        setSaid(why);
      }).finally(() => setBusy(false));
    }, UNDO_SECONDS * 1000);
    return () => { clearTimeout(t); clearInterval(tick); };
  }, [undoing]);
  // a card gone before the window ends (the panel closed, the view left, its prompt gone from the list), while the
  // passkey's prompt is up, or while a locked tab's touch waits for its words, sends nothing, and says so
  useEffect(() => () => {
    clearTimeout(openWait.current);
    const left = pending.current !== null || waiting.current !== null || pressing.current;
    pending.current = null;
    waiting.current = null;
    if (left) leftNow.current();
  }, []);
  // a preparation is pressed only for the text the card holds now; in a locked tab, only one a passkey that unlocks the
  // keys may sign (`blocked` where none may: high security, whose hardware keys unlock nothing)
  const fresh = ready !== null && ready.text === p.text;
  const unlocking = fresh && locked ? K.approveAllowed(keys, ready.prepared.allow).length > 0 : false;
  const offered = fresh && (!locked || unlocking);
  const blocked = fresh && locked && !unlocking;
  const press = () => {
    if (!ready || busy || waiting.current || pending.current || ready.text !== p.text) return;
    if (Date.now() - ready.at > PRESS_LIFE * 1000) {
      // its nonce would not last until it is sent: prepared again, and nothing signed
      setReady(null);
      setSaid(STALE_WORDS);
      setAgain((n) => n + 1);
      return;
    }
    const { prepared, text, at } = ready;
    setBusy(true);
    setSaid("");
    pressing.current = true;
    // the passkey's prompt first, nothing awaited before it
    const touched = locked ? K.signAndTouch(prepared, keys)
      : signPrepared(prepared).then((s) => ({ credential: s.credential, touch: null as K.Touch | null, noPrf: undefined as string | undefined }));
    // whatever the touch gave, its preparation is not pressed again: a later press gets one of its own
    const renew = () => { setReady(null); setAgain((n) => n + 1); };
    void touched.then((t) => { renew(); return t; }, (e: unknown) => { renew(); throw e; }).then(async ({ credential, touch, noPrf }) => {
      const h: Held = { signed: { payload: prepared.payload, credential }, text, at };
      if (!locked) {
        if (text !== textNow.current) setSaid(CHANGED_WORDS);
        else if (!openNow.current) setSaid(RELOCKED_WORDS);
        else if (late(h)) setSaid(LATE_WORDS);
        else startUndo(h);
        return;
      }
      // nothing unlocks, so nothing is sent: said in the words for what answered (K.signAndTouch's noPrf)
      if (!touch) { setSaid(`${noPrf ?? K.NO_PRF} Nothing was sent.`); return; }
      waiting.current = h;
      await e2e.unlockFrom(touch);
      if (waiting.current !== h) return;
      clearTimeout(openWait.current);
      openWait.current = setTimeout(() => {
        if (waiting.current !== h) return;
        waiting.current = null;
        setSaid(NOT_OPENED_WORDS);
      }, OPEN_WAIT * 1000);
    }).catch((e: unknown) => {
      waiting.current = null;
      setSaid(`${sentence(e instanceof Error ? e.message : String(e))} Nothing was sent.`);
    }).finally(() => { pressing.current = false; setBusy(false); });
  };
  // nothing pending: sent already (said), or dropped (its own words said why)
  const undo = () => {
    if (!pending.current) { if (gone.current) setSaid(TOO_LATE_WORDS); return; }
    pending.current = null;
    setUndoing(null);
    setSaid(UNDONE_WORDS);
  };
  return { offered, blocked, failed: on ? failed : "", busy, undoing: undoing !== null, count, said, warning: ready?.warning ?? null, press, undo };
}

/** One prompt a linked session reported, in Approvals or in its conversation. `live`: open, as the sessions view lists
 *  it, or as the hidden list lists one hidden that still waits (one not live, from its conversation, says how it
 *  closed, or that it is still open where it was hidden, and offers nothing). `end`: in the list of hidden and closed
 *  prompts, how it ended, said in place of how it closed; a card there has no Close, being out of the open list
 *  already. `inbox`: in Approvals, whose hidden list the note under Close points to. `onNotice`: what to say once the
 *  card itself may be gone (an answer sent, one the server refused because the session closed it first). */
export function PromptCard({ p, name, live = true, unlock = true, onNotice, end, inbox = false, shown = false, onSending, onSent }: {
  p: LinkPrompt; name: string; live?: boolean; unlock?: boolean; onNotice?: (text: string) => void;
  end?: LinkPromptEnd; inbox?: boolean; shown?: boolean;
  /** an answer signed on this card, from its signature to the server's answer: the place the card is in (a
   *  conversation) shows it as sending; and what that place reads again once it was taken */
  onSending?: (words: string | null) => void;
  onSent?: () => Promise<void>;
}) {
  const box = useShown(shown);
  const account = useAccount();
  const refresh = useStore((s) => s.refreshLinks);
  const opener = useOpened([{ envelope: p.text, slot: eventSlotOf(p, p.link_id, account) }]);
  const words = itemShown(p, p.link_id, account, opener);
  const item = words.ok ? parsePrompt(words.value) : null;
  // an item to look at that is no tool call: what Claude Code raised itself
  const raised = words.ok && !item && p.look ? parseRaised(words.value) : null;
  const [answering, setAnswering] = useState<Answering | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  // Close takes the card away at once, and brings it back with the reason if the server does not take it
  const closed = useAct(closeKey(p.seq), useStore((s) => s.setError));
  // an answer the server refused because the prompt closed first, said here once the card shows it closed
  const [refused, setRefused] = useState("");
  const closeNote = useId();
  const answerable = live && !p.look && item !== null;
  const choosable = answerable && item.asks && item.questions !== null;
  // one press on Approve (useOnePress): an open prompt that asks no choice, or a sealed one while the keys are locked,
  // for an account with no signer paired. With one, or where that cannot be read, Approve opens the review, which
  // offers Sign on my Mac: a session pinned to sign there refuses what a browser signs.
  const locked = live && !p.look && !words.ok && words.seal.at === "locked";
  const paired = useSignerKnown(signsHere() && live && !p.look);
  const pressHere = paired === false;
  const signing = useNeedsServer(SIGN_OFFLINE);
  // one press's send: null where it went, or where the server refused it because the prompt closed first (said as the
  // card says that); else why it was not sent, said in the card
  const sendApproval = async (signed: Signed): Promise<string | null> => {
    try {
      await Api.sendLinkCommand(signed);
      onNotice?.(`Signed: your answer went to ${name}. Its prompt hook takes it while the prompt still waits in its window; whichever answers first there decides.`);
      await refresh();
      return null;
    } catch (e) {
      const said = await refusedWords(e, p);
      if (said) { setRefused(said); onNotice?.(said); await refresh(); return null; }
      if (unreachable(e)) { await refresh(); return UNREACHABLE_SEND_WORDS; }   // the list read again before Approve comes back
      return `Not sent: ${sentence(e instanceof Error ? e.message : String(e))} Press Approve again.`;
    }
  };
  // the account's signers still being read: said once it takes a moment
  const [slowSigners, setSlowSigners] = useState(false);
  useEffect(() => {
    if (paired !== null) { setSlowSigners(false); return; }
    const t = setTimeout(() => setSlowSigners(true), 1500);
    return () => clearTimeout(t);
  }, [paired]);
  const one = useOnePress(p, pressHere && signsHere() && !answering && ((answerable && !item.asks) || locked), locked,
                          { ok: words.ok, failed: !words.ok && words.seal.at !== "locked" && words.seal.at !== "opening",
                            asks: item ? item.asks : null, numbered: !!item?.questions }, sendApproval, () => onNotice?.(LEFT_WORDS));
  const asking = answerable && signsHere() && !answering && !one.undoing;
  // the one-press Approve is offered: what it sends in the clear is said beside it, before the press
  const approveOffered = pressHere && !one.undoing && !answering && signsHere() && ((answerable && !item.asks) || (locked && one.offered));
  const pressed = () => { if (!signing.off) one.press(); };
  const close = () => void closed.act("Closed", () => Api.closeLinkQuestion(p.seq), refresh);
  const numbers = [...chosen].sort((a, b) => a - b).join(",");
  // closed: gone from where it waits at once; where it is kept once closed (its conversation), said closed once the
  // server's word is read, as before
  if (closed.taken && live) return null;
  return (
    <div ref={box} role="group" aria-label={`Reported by the session ${name}`} className={`rounded-lg border border-wait/50 bg-wait/[0.05] p-3 ${shown ? "ring-2 ring-accent/60" : ""}`}>
      <div className="mb-0.5 break-words text-[11.5px] text-muted">
        <span>{`Reported by the session ${name}`}</span>
        <span title={full(p.time)}>{` · ${ago(p.time)}`}</span>
        {live && p.deadline_at ? <span title={full(p.deadline_at)}>{`; it waits until ${clock(p.deadline_at)}`}</span> : null}
      </div>
      <div className="mb-1 text-[11px] text-faint">The session's report of a prompt in its window, not Claude Code's own: whatever holds its link can post one.</div>
      {!words.ok ? (
        <div className="text-[12.5px]"><ShownText shown={words} unlock={unlock}>{() => null}</ShownText></div>
      ) : item ? (
        <>
          <div className="text-[12.5px]">
            {p.look ? "To look at: " : item.asks ? "A question, asked with " : "Permission to use "}
            <code className="font-mono">{visible(item.tool)}</code>
            {words.inClear && <span className="pill ml-1 text-wait">sent in the clear</span>}
          </div>
          {item.input === undefined ? (
            // no `input` key: the tool's name alone, which its session's client posts where the call's text may not go
            // to the list; its input was withheld, not empty
            <div role="note" aria-label="Its input, not posted" className="mt-1 text-[11.5px] text-muted">Its input was not posted to the list: the session&apos;s window shows the whole call.</div>
          ) : (
            <pre aria-label="Its input, whole" className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg">{visible(inputWords(item.input))}</pre>
          )}
          {item.asks && item.questions && (choosable && asking
            ? <Choices seq={p.seq} questions={item.questions} chosen={chosen} onChange={setChosen} />
            : <Numbered questions={item.questions} />)}
          {answerable && item.asks && !item.questions && (
            <div className="mt-1 text-[11.5px] text-wait">Its options are not numbered as a prompt&apos;s are: choose them in the session&apos;s window, or refuse it here.</div>
          )}
        </>
      ) : raised ? (
        <Raised r={raised} />
      ) : (
        <div className="whitespace-pre-wrap break-words text-[12.5px]">{visible(words.value)}{words.inClear && <span className="pill ml-1 text-wait">sent in the clear</span>}</div>
      )}
      {live && p.look && (
        <div className="mt-1 text-[11.5px] text-muted">It is answered where the session shows it, its window or its page: nothing here decides it.</div>
      )}
      {live && !p.look && words.ok && !item && (
        <div className="mt-1 text-[11.5px] text-wait">Its text is not in the form a prompt takes, so nothing here answers it: it is answered where the session shows it.</div>
      )}
      {end ? <Ended end={end} look={p.look} form={!(words.ok && !item)} deadline={p.deadline_at} asks={item?.asks ?? false} /> : !live && (
        // how it closed; where no close is recorded, it was hidden by a Close, which leaves it open
        <div className="mt-1 text-[11.5px] text-muted">{p.closed_reason ? closedWords(p.closed_reason) : p.look || (words.ok && !item) ? STILL_OPEN_LOOK : STILL_OPEN}</div>
      )}
      {!live && refused && <div role="alert" className="mt-1.5 text-[12px] text-fail">{refused}</div>}
      {answerable && !signsHere() && !APP && <div className="mt-2 text-[12px] text-muted">{SIGN_ELSEWHERE()}</div>}
      {answering && live && (
        <div className="mt-2">
          <ClearNote a={answering} />
          <SignBox key={answering.kind === "numbers" ? `numbers:${answering.text}` : answering.kind} purpose="answer" answers="prompt"
            to={p.link_id} toName={name} focus placeholder="Your words" submit={SUBMIT[answering.kind]}
            fixed={answering.kind === "words" ? undefined : answering.kind === "numbers" ? answering.text : answering.kind === "approve" ? PROMPT_APPROVE : PROMPT_DENY}
            check={answering.kind === "words" ? ownWordsProblem : undefined}
            // the digest is of the prompt's text exactly as the server holds it: for a sealed prompt, its envelope
            extra={async () => ({ ref: p.seq, digest: await textDigest(p.text) })}
            onFailed={async (e) => {
              const said = await refusedWords(e, p);
              if (said) { setRefused(said); onNotice?.(said); await refresh(); }
              return said;
            }}
            onDone={async () => {
              setAnswering(null); setChosen([]);
              onNotice?.(`Signed: your answer went to ${name}. Its prompt hook takes it while the prompt still waits in its window; whichever answers first there decides.`);
              await onSent?.();
              onSending?.(null);
              await refresh();
            }}
            onCancel={() => setAnswering(null)} onSending={onSending} />
        </div>
      )}
      {approveOffered && (
        // one press signs: what it sends in the clear, in a locked tab what the touch does, and which of their passkeys
        // the session refuses with the pin line that makes it take them, said before it
        <div className="mt-2">
          <ClearNote a={{ kind: "approve" }} />
          {locked && <div role="note" aria-label="One press" className="mb-1 text-[11.5px] text-fg-dim">{LOCKED_PRESS_WORDS}</div>}
          {one.warning && <PinNotice w={one.warning} name={name} />}
        </div>
      )}
      {pressHere && !one.undoing && !answering && (signing.off ? signing.note : one.failed ? (
        <div role="status" aria-label="Approve waits" className="mt-1.5 text-[12px] text-wait">
          {`Approve waits: ${sentence(one.failed)} It is tried again in ${RETRY_SECONDS} seconds.`}
        </div>
      ) : one.blocked && !one.said ? <div className="mt-1.5 text-[12px] text-wait">{K.NO_SIGNER_UNLOCKS}</div> : null)}
      {!pressHere && asking && !item.asks && signing.off && signing.note}
      {paired === null && slowSigners && signsHere() && !signing.off && ((asking && !item.asks) || locked) && (
        <div className="mt-1.5 text-[12px] text-wait">{SIGNERS_WAIT_WORDS}</div>
      )}
      {one.undoing && (
        <div role="status" aria-label="Approving" className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-wait">
          <span>{`Signed with your passkey: it is sent in ${one.count} s unless you press Undo. Closing this view cancels it.`}</span>
          <button className="btn btn-sm" onClick={one.undo}>Undo</button>
        </div>
      )}
      {one.said && <div className="mt-1.5 text-[12px] text-muted">{one.said}</div>}
      {APP && live && <InBrowserItem seq={p.seq} what="prompt" />}
      {live && (asking || !end) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {asking && !item.asks && (pressHere ? (
            <button className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={!one.offered || one.busy} {...signing.attrs} onClick={pressed}>
              {one.busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />} Approve
            </button>
          ) : (
            // a signer paired, or the list unread: the review, which offers Sign on my Mac where one is paired
            <button className="btn btn-sm btn-primary" disabled={paired === null} onClick={() => setAnswering({ kind: "approve" })}>
              <KeyRound size={12} /> Approve
            </button>
          ))}
          {approveOffered && locked && (
            <button className={`btn btn-sm btn-primary ${UNAVAILABLE}`} disabled={one.busy} {...signing.attrs} onClick={pressed}>
              {one.busy ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />} Approve
            </button>
          )}
          {asking && choosable && (
            <button className="btn btn-sm btn-primary" disabled={!chosenFully(item.questions!, chosen)}
              title="One option for each question, or one or more where a question allows several"
              onClick={() => setAnswering({ kind: "numbers", text: numbers })}>
              Answer with the chosen options
            </button>
          )}
          {asking && <button className="btn btn-sm btn-danger" onClick={() => setAnswering({ kind: "deny" })}>Deny</button>}
          {asking && <button className="btn btn-sm" onClick={() => setAnswering({ kind: "words" })}>Answer in your own words</button>}
          {!end && !one.undoing && (
            <button className="btn btn-sm" aria-describedby={closeNote} onClick={close}>Close</button>
          )}
        </div>
      )}
      {live && !end && (
        // what Close does now: the prompt is hidden, not given up (a text not in a prompt's form is not answered here)
        <div id={closeNote} className="mt-1 text-[11px] text-muted">{closeNoteWords(p.look, !(words.ok && !item), inbox)}</div>
      )}
      <CameBack why={closed.failed} />
    </div>
  );
}

// ---- a proposal ----------------------------------------------------------------------------------------------------

/** One proposal a linked session made for another of the owner's sessions: its words, sealed to them, signed as their
 *  own instruction to the session it names and no other, its first line naming the proposer; or closed, which decides
 *  nothing and sends nothing. */
export function ProposalCard({ p, live = true, unlock = true, onNotice, shown = false }: {
  p: LinkProposal; live?: boolean; unlock?: boolean; onNotice?: (text: string) => void; shown?: boolean;
}) {
  const box = useShown(shown);
  const account = useAccount();
  const refresh = useStore((s) => s.refreshLinks);
  const opener = useOpened([{ envelope: p.text, slot: eventSlotOf(p, p.link_id, account) }]);
  const words = itemShown(p, p.link_id, account, opener);
  const [signing, setSigning] = useState(false);
  // Close takes the card away at once, and brings it back with the reason if the server does not take it
  const closed = useAct(closeKey(p.seq), useStore((s) => s.setError));
  const closeNote = useId();
  const target = p.for || "another session";
  const title = `Proposed by the session ${p.from}, for ${target}`;
  const close = () => void closed.act("Closed", () => Api.closeLinkQuestion(p.seq), refresh);
  // closed: gone from where it waits at once; said closed in its conversation once the server's word is read
  if (closed.taken && live) return null;
  return (
    <div ref={box} role="group" aria-label={title} className={`rounded-lg border border-wait/50 bg-wait/[0.05] p-3 ${shown ? "ring-2 ring-accent/60" : ""}`}>
      <div className="mb-0.5 break-words text-[11.5px] text-muted">
        <span>{title}</span>
        <span title={full(p.time)}>{` · ${ago(p.time)}`}</span>
        {live && p.deadline_at ? <span title={full(p.deadline_at)}>{`; it waits until ${dayTime(p.deadline_at)}`}</span> : null}
      </div>
      <div className="mb-1 text-[11px] text-faint">{`Its words reach ${target} only as your own instruction, signed with your passkey, its first line naming ${p.from}.`}</div>
      {!signing && (
        <div className="whitespace-pre-wrap break-words text-[12.5px]"><ShownText shown={words} unlock={unlock}>{(t) => visible(t)}</ShownText></div>
      )}
      {!live && <div className="mt-1 text-[11.5px] text-muted">{closedWords(p.closed_reason)}</div>}
      {signing && words.ok && (
        <SignBox purpose="instruction" to={p.for_id} toName={target} head={`Proposed by ${p.from}:\n`} start={words.value}
          proposal={{ id: p.client_id ?? "", by: p.from }} placeholder="Its words, which you may change" submit="Review the instruction" focus
          onDone={async () => { setSigning(false); onNotice?.(`Signed: your instruction went to ${target}.`); await refresh(); }}
          onCancel={() => setSigning(false)} />
      )}
      {APP && live && <InBrowserItem seq={p.seq} what="proposal" />}
      {live && !signing && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {words.ok && p.for_id && (signsHere()
            ? <button className="btn btn-sm btn-primary" onClick={() => setSigning(true)}><KeyRound size={12} /> Sign it as your instruction</button>
            : !APP && <span className="text-[12px] text-muted">{SIGN_ELSEWHERE()}</span>)}
          <button className="btn btn-sm" aria-describedby={closeNote} onClick={close}>Close</button>
        </div>
      )}
      <CameBack why={closed.failed} />
      {live && (
        <div id={closeNote} className="mt-1 text-[11px] text-muted">{`Closing decides nothing: it takes this out of your list, and nothing reaches ${target}.`}</div>
      )}
    </div>
  );
}

// ---- in Approvals --------------------------------------------------------------------------------------------------

/** In the desktop and phone apps, which hold no keys and sign nothing: the browser tab at the server's address, where
 *  what the linked sessions sent is opened with the account's keys and answered with a passkey. */
function InBrowser() {
  const here = server();
  return (
    <div className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted">
      {`Your linked sessions' prompts and proposals are opened with your account's keys and answered with one of your passkeys, in a browser at ${hostLabel(here) || "the ADE's address"}.`}
      {here && (
        <div className="mt-1.5">
          <a className="btn btn-sm" href={`${here}/app/#approvals`} target="_blank" rel="noopener noreferrer"><ExternalLink size={12} /> Open Approvals in your browser</a>
        </div>
      )}
    </div>
  );
}

/** In the desktop and phone apps, which sign nothing: one tap that opens this item in a browser at the server's
 *  address (App.tsx's #item=<seq>), where it is opened with the account's keys and answered with a passkey. */
function InBrowserItem({ seq, what }: { seq: number; what: "prompt" | "proposal" | "question" }) {
  const here = server();
  if (!here) return null;
  return (
    <div className="mt-2">
      <a className="btn btn-sm" href={`${here}/app/#item=${seq}`} target="_blank" rel="noopener noreferrer"
        aria-label={`Open this ${what} in your browser`}><ExternalLink size={12} /> Open it in your browser</a>
    </div>
  );
}

/** The item a link opened (App.tsx's #item=<seq>): brought into view once it is shown. */
function useShown(shown: boolean) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (shown) box.current?.scrollIntoView?.({ block: "center" }); }, [shown]);
  return box;
}

/** A linked session's question to the owner, as Question shows it, with the apps' link to it, and marked where a link
 *  opened it (`shown`). */
function QuestionItem({ q, linkId, shown }: { q: LinkEvent & { link_id: string }; linkId?: string; shown: boolean }) {
  const box = useShown(shown);
  return (
    <div ref={box} className={shown ? "rounded-lg ring-2 ring-accent/60" : undefined}>
      <Question q={q} linkId={linkId} />
      {APP && <InBrowserItem seq={q.seq} what="question" />}
    </div>
  );
}

/** What GET /api/ade/link/prompts/closed last answered for the account signed in: its hidden prompts that still wait
 *  and those closed in the last 24 hours, newest first, as the route gives them (the newest 50), and `more`, how many
 *  of those closed it left out (closed_more). Read whenever the inbox is (the store's `links`, which a Close and an
 *  answer read again). The reads are numbered: an answer is shown unless a read issued after it has been shown
 *  already, or another account has signed in since, or the inbox is gone. A later read of the inbox drops no read
 *  under way: a heartbeat moves its stamp about once a minute, and on a slow phone every answer would be dropped. A
 *  server from before the route answers 404: one answering the newest read sent stops the reads while the inbox is
 *  shown; one a later read overtook stops nothing, the later read's answer deciding. */
function useClosedPrompts(): { prompts: LinkClosedPrompt[]; more: number; known: boolean } {
  const account = useAccount();
  const links = useStore((s) => s.links);
  const [read, setRead] = useState<{ account: string; prompts: LinkClosedPrompt[]; more: number }>({ account: "", prompts: [], more: 0 });
  const [absent, setAbsent] = useState(false);
  const issued = useRef(0);          // the number of the last read sent
  const applied = useRef(0);         // the number of the newest read whose answer is shown
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!account || absent) return;
    const n = ++issued.current;
    Api.closedLinkPrompts().then(
      (r) => {
        if (!mounted.current || n <= applied.current || (useStore.getState().user?.id ?? "") !== account) return;
        applied.current = n;
        const more = r?.closed_more;
        setRead({ account, prompts: Array.isArray(r?.prompts) ? r.prompts : [],
          more: typeof more === "number" && Number.isInteger(more) && more > 0 ? more : 0 });
      },
      (e) => {
        // an older server's 404 counts only from the newest read sent, for the account still signed in: a read a later
        // one followed stops nothing, and the later read's own answer decides
        if (!mounted.current || n !== issued.current || (useStore.getState().user?.id ?? "") !== account) return;
        if (e instanceof ApiError && e.status === 404) setAbsent(true);
      },
    );
    // `links` on purpose: each read of the inbox reads this list again, and drops no read still under way
  }, [account, links, absent]);
  // `known`: a read for this account has come back, or the server has no such route
  return read.account === account ? { prompts: read.prompts, more: read.more, known: true } : { prompts: [], more: 0, known: absent };
}

/** The link channel's part of the one list: its open prompts (those to look at too), its open questions to the owner,
 *  and its open proposals, as the sessions view lists them; below them, the prompts hidden by a Close that still wait,
 *  shown and answerable there, then those closed in the last 24 hours, folded under one line naming how many so that
 *  a long day does not push the held commands below out of sight, each saying how and when it ended. Read again with
 *  the linked sessions; a decision made elsewhere takes an item out at the next read. */
export function LinkInbox({ item }: { item?: number }) {
  const links = useStore((s) => s.links);
  const { prompts: closed, more, known } = useClosedPrompts();
  const [unfolded, setUnfolded] = useState(false);
  const [notices, setNotices] = useState<{ id: number; text: string }[]>([]);
  const notice = (text: string) => setNotices((n) => [...n.slice(-2), { id: Date.now() + Math.random(), text }]);
  const prompts = links?.prompts ?? [];
  const proposals = links?.proposals ?? [];
  const questions = (links?.questions ?? []).filter(toOwner);
  const live = new Set((links?.links ?? []).map((l) => l.id));
  const any = prompts.length + proposals.length + questions.length > 0;
  const waiting = closed.filter((c) => c.open);
  const ended = closed.filter((c) => !c.open);
  // those closed in the last 24 hours: the ones listed, and those the server left out (it lists the newest 50)
  const endedCount = ended.length + more;
  // a hidden one that still waits is answered here as an open one is; an item to look at, never
  const card = (c: LinkClosedPrompt) => <PromptCard key={c.seq} p={c} name={c.from} live={c.open && !c.look} end={c} onNotice={notice} shown={c.seq === item} />;
  // a link that names an item none of these lists holds as waiting: said once the open and the hidden lists are read,
  // and not of one shown here since (answered or closed on this page)
  const present = item !== undefined && [...prompts, ...waiting, ...proposals, ...questions].some((x) => x.seq === item);
  const [seenItem, setSeenItem] = useState<number | undefined>(undefined);
  useEffect(() => { if (present) setSeenItem(item); }, [present, item]);
  const itemGone = item !== undefined && links !== null && known && !present && seenItem !== item;
  if (!any && !closed.length && !more && !notices.length && !itemGone) return null;
  return (
    <section aria-label="From your linked sessions" className="space-y-2">
      <div className="flex items-center gap-1.5 px-1 text-[11.5px] text-muted"><Radio size={12} aria-hidden /> From your linked sessions</div>
      <div role="status" aria-live="polite" className="space-y-2">
        {notices.map((n) => (
          <div key={n.id} className="flex items-start gap-2 rounded-lg border border-line px-3 py-2 text-[12px]">
            <span className="min-w-0 flex-1">{n.text}</span>
            <button className="icon-btn !h-6 !w-6 flex-none" title="Put away" aria-label="Put away"
              onClick={() => setNotices((all) => all.filter((x) => x.id !== n.id))}><X size={13} /></button>
          </div>
        ))}
      </div>
      {APP && (any || closed.length > 0 || more > 0) && <InBrowser />}
      {prompts.map((p) => <PromptCard key={p.seq} p={p} name={p.from} onNotice={notice} inbox shown={p.seq === item} />)}
      {itemGone && <div role="note" className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted">{ITEM_GONE_WORDS}</div>}
      {questions.map((q) => <QuestionItem key={q.seq} q={q} linkId={live.has(q.link_id) ? q.link_id : undefined} shown={q.seq === item} />)}
      {proposals.map((p) => <ProposalCard key={p.seq} p={p} onNotice={notice} shown={p.seq === item} />)}
      {(closed.length > 0 || more > 0) && (
        <section aria-label="Hidden and closed prompts" className="space-y-2">
          {waiting.length > 0 && <div className="px-1 pt-1 text-[11.5px] text-muted">Hidden prompts that still wait</div>}
          {waiting.map(card)}
          {endedCount > 0 && (
            <button className="touch-target flex items-center gap-1 px-1 pt-1 text-left text-[11.5px] text-muted hover:text-fg"
              aria-expanded={unfolded} onClick={() => setUnfolded(!unfolded)}>
              {unfolded ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
              {`Closed in the last 24 hours (${endedCount})`}
            </button>
          )}
          {unfolded && ended.map(card)}
          {unfolded && more > 0 && <div className="px-1 text-[11.5px] text-muted">{`And ${more} more, not listed here: a linked session's conversation shows its own.`}</div>}
        </section>
      )}
    </section>
  );
}
