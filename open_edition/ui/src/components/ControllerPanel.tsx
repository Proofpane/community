import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  Bot, ChevronDown, ChevronRight, Loader2, MessageSquare, OctagonX, Play, Power, ScrollText, Send, SlidersHorizontal,
  Square,
} from "lucide-react";
import { Api, ApiError, OLD_SIGN_IN, type AccountOwnerView } from "../api";
import { EFFORT_NAMES, MODEL_NAMES, type Capabilities } from "../chatSettings";
import {
  ControllerApi, type Attempt, type Check, type CheckResult, type ControllerSettings, type ControllerView, type Decision,
  type Holder, type HolderLink, type HolderOffer, type Line, type Manager, type ManagerState, type Protected,
  type Question, type Request, type Requirement, type RoundOutput, type SettingsPatch, type Task,
} from "../controller";
import { prepareHolder, prepareSigned, signPrepared, type HoldSettings, type Prepared } from "../passkey";
import { EVERY, useVisibleEvery } from "../sessionMap";
import { useStore } from "../store";
import { dayTime } from "../when";
import { Confirm } from "./bits";
import { ManagerAccountSetup } from "./ManagerAccountSetup";
import { RiskTerms, TERMS_IN_FLOW } from "./RiskTerms";

// The controller's panel in the control view, on the app's controller routes
// (controller.ts). It reads the controller's whole view every 3 seconds while the page is visible (useVisibleEvery, as
// the map does), and shows: the controller's state with its On, Off, Stop and End all; its settings (the one account it
// runs on, how many sessions at once it may hold, the models and efforts its tasks may take, its own chat's model and
// effort, and whether it waits for the owner's go on each plan); the goals the owner sent, each with its sub-goals; each
// sub-goal with the owner's words and the model's title kept apart, its state, a Hold and a Close, and its tasks, a task
// started outside its bounds flagged with why; the questions the controller asked, answered as the owner's decisions,
// those decisions, and the log. The controller's own chat opens from here as a session's panel does, the session the
// workspace's record names, none with a workspace older than that. Every word the app, the
// owner or a model wrote is shown as it is, as text, never as markup, and the view marks which are whose.

// Risk point 7: turning the controller on is an automated use of the owner's account,
// so its notice opens over the On control, as a New task's Auto-resume opens it (AgentTask.tsx), each time the owner
// comes to turn it on; it holds nothing.

/** What a failed read of the controller says: the server's own words, or, where the server is older than the controller
 *  routes (its framework answering only "Not Found"), that it has none yet; a page not signed in says to sign in. */
function failedWords(e: unknown): string {
  if (e instanceof ApiError && e.status === 404 && e.message === "Not Found") {
    return "This server does not have the Manager yet: it comes with a later version of Proofpane Community.";
  }
  if (e instanceof ApiError && e.status === 401) return "This page is not signed in: sign in again, then reload it.";
  return e instanceof Error ? e.message : String(e);
}
const aborted = (e: unknown) => e instanceof DOMException && e.name === "AbortError";
// a 401 answered to the sign-in the page had before it signed in again ("Sign out everywhere" mints a new one): no
// sign-out, and the next read carries the new one (as ControlView does)
const staleSignIn = (e: unknown) => e instanceof ApiError && e.status === 401 && e.message === OLD_SIGN_IN;
const signedOut = (e: unknown) => e instanceof ApiError && e.status === 401 && !staleSignIn(e);
const errorWords = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
const when = (t: number | null) => (t ? dayTime(t) : "");

// A goal's checks. What a check is, beside the goal form's checks; the
// words on protected paths, which one component gives beside every set of checks (ProtectedNote); and the notes on a
// task's row
const CHECKS_NOTICE = "A check is a command in a program the session committed, which the workspace runs unattended and " +
  "ungated as its own user, in a fresh checkout of the commit it judges. Its checkout is git's own, of a clone of the " +
  "session's branch that each round makes, made just before the check runs and only while that clone is as it was " +
  "made: it holds the commit as git checks it out, with that branch's history, and none of the repository's " +
  "settings, hooks or attributes file, and a check after one that changed the clone is not run; a repository that is a " +
  "shallow or partial clone gives no clone, so its checks never run. It is given PATH, " +
  "HOME, USER, LOGNAME, SHELL, LANG, " +
  "LC_ALL, LC_CTYPE, LC_MESSAGES, TZ and TMPDIR where the workspace has them, the terminal's type, on macOS bash's " +
  "notice silenced, and a mark of its own (PP_AGENT_RUN_ID) by which the workspace ends what it leaves; never " +
  "SSH_AUTH_SOCK. Leaving that out closes that one way: with HOME, and as the workspace's user, a check can still read " +
  "every account's folder and the GitHub sign-in.";
export const PROTECTED_WORDS = "Protected paths are judged on the commits alone: a check can still be passed by changing " +
  "a file it runs that no protected path covers, or by a program still running, the session's own or another " +
  "session's, changing its checkout's files as they are made or while it runs, or writing the round's clone as it is " +
  "made, whose filter the workspace's own git would then run.";
const OUTPUT_WORDS = "A program's output, as it wrote it: the check's, not the workspace's words";
const FINISH_WORDS = "Finish commits what the session left uncommitted, after its checks: they did not judge that commit. " +
  "Made while the session's program still runs, it can be the head that program ends on, and the task then fails.";
const UPDATE_WORDS = "Update brings the branch it follows into its branch: a change there to a protected path is a change " +
  "since its base, and fails every later round.";
const CHECKS_MOST = 10;                                                     // as the route takes them

/** The words on protected paths, beside every set of checks: on the goal form, a goal's row, a sub-goal's card and a
 *  task's round. */
function ProtectedNote() {
  return <p className="text-[10.5px] text-faint">{PROTECTED_WORDS}</p>;
}

/** The owner's words, or a model's, shown as text: newlines kept, never markup, and never empty (a blank shows as a
 *  dash so the row is not read as missing). */
function Words({ text, whose }: { text: string; whose: string }) {
  return (
    <div>
      <div className="text-[10.5px] text-faint">{whose}</div>
      <p className="whitespace-pre-wrap break-words text-[12px] text-fg-dim">{text.trim() || "—"}</p>
    </div>
  );
}

/** A note the app leaves on a model's text it made plain: that it shortened it, or removed characters it hides. */
function Cleaned({ cut, removed }: { cut: boolean; removed: number }) {
  const bits = [cut ? "shortened" : "", removed > 0 ? `${removed} hidden ${removed === 1 ? "character" : "characters"} removed` : ""].filter(Boolean);
  return bits.length ? <span className="text-[10.5px] text-faint"> ({bits.join(", ")})</span> : null;
}

export function ControllerPanel({ onOpenSession }: { onOpenSession?: (id: string) => void }) {
  const [view, setView] = useState<ControllerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const caps = useStore((s) => s.caps);

  const read = useCallback(async (signal?: AbortSignal) => {
    try {
      const v = await ControllerApi.view(signal);
      setView(v);
      setError(null);
      setReady(true);
      return true;
    } catch (e) {
      if (aborted(e)) return true;
      if (staleSignIn(e)) return true;                                  // the page signed in again mid-read: keep drawing
      setError(failedWords(e));
      if (signedOut(e)) setView(null);                                  // a sign-in that ended: draw none of it
      return false;
    }
  }, []);
  useVisibleEvery(open, EVERY, read);
  const reload = useCallback(() => { void read(); }, [read]);

  // every action: the app's call, then an immediate re-read so the owner sees it without the 3-second wait; a refusal's
  // own words are shown and the view is left as it was. Returns whether it went through, so a form clears its text only
  // when the send succeeded, never on a refusal the owner must re-send
  const run = useCallback(async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      reload();
      return true;
    } catch (e) {
      if (!aborted(e)) setActionError(errorWords(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, [reload]);

  return (
    <section className="mb-4 rounded-lg border border-line bg-panel" aria-label="The Manager">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <button className="icon-btn !h-6 !w-6 flex-none" aria-expanded={open} onClick={() => setOpen(!open)}
          aria-label={open ? "Hide the Manager" : "Show the Manager"}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <Bot size={15} className="text-accent" aria-hidden />
        <h3 className="text-[13px] font-semibold">The Manager</h3>
        {view?.controller && <StateBadge state={view.controller.state} />}
      </div>
      {open && (
        <div className="px-3 py-2.5">
          {error && <p role="alert" className="mb-2 text-[12px] text-fail">{error}</p>}
          {!ready && !error && <div className="flex justify-center py-6 text-muted"><Loader2 size={15} className="animate-spin" aria-label="Reading the Manager" /></div>}
          {ready && view && !view.controller && (
            <SetUp caps={caps} busy={busy} run={run} actionError={actionError} />
          )}
          {ready && view && view.controller && (
            <LinkNames.Provider value={view.holder?.name ? { [view.holder.link]: view.holder.name } : {}}>
            <div className="space-y-3">
              {actionError && <p role="alert" className="text-[12px] text-fail">{actionError}</p>}
              <Controls c={view.controller} busy={busy} run={run} onOpenSession={onOpenSession} />
              <Settings c={view.controller} caps={caps} busy={busy} run={run} />
              <LinkedManager holder={view.holder} busy={busy} run={run} />
              <Goals view={view} busy={busy} run={run} />
              <SubGoals view={view} busy={busy} run={run} />
              <OtherSessions sessions={view.other_sessions} />
              <QuestionsAndDecisions view={view} busy={busy} run={run} />
              <Log />
            </div>
            </LinkNames.Provider>
          )}
        </div>
      )}
    </section>
  );
}

const STATE_WORDS: Record<ControllerSettings["state"], { label: string; cls: string }> = {
  off: { label: "Off", cls: "text-muted" },
  on: { label: "On", cls: "text-done" },
  stopped: { label: "Stopped", cls: "text-fail" },
};

function StateBadge({ state }: { state: ControllerSettings["state"] }) {
  const s = STATE_WORDS[state];
  return <span className={`ml-auto text-[11.5px] font-medium ${s.cls}`}>{s.label}</span>;
}

/** Before the owner has set the controller up (its view's controller is null): the one thing to do is choose the account
 *  it runs on, which sets it up; the rest of the settings are then the Settings section's. */
function SetUp({ caps, busy, run, actionError }: { caps: Capabilities; busy: boolean; run: Run; actionError: string | null }) {
  return (
    <div className="space-y-2">
      <p className="text-[12px] text-muted">
        The Manager is not set up yet. Choose the account it runs on, or a linked session below, to set it up; it stays
        off until you turn it on.
      </p>
      {actionError && <p role="alert" className="text-[12px] text-fail">{actionError}</p>}
      <AccountPicker account={null} busy={busy} run={run} />
      <LinkedManager holder={null} busy={busy} run={run} />
      <p className="text-[11px] text-faint">Once it has an account you can set how many sessions it may hold at once, the models and efforts its tasks may take, and its own chat, below.</p>
      <SettingsNote caps={caps} />
    </div>
  );
}

// ---- a linked session as the Manager (ade/manager_link.py) --------------------------------------------------------

export const LINKED_WORDS = "A session linked to the ADE may hold the Manager's role in an account's place: no " +
  "workspace, sign-in or clone is needed. You sign the choice with a passkey; it holds the role for a week at most, " +
  "under the settings it is made with, and a change of those settings ends its hold.";
// where its tasks run in this build
export const OWN_MACHINE_WORDS = "A linked Manager's tasks run on its own machine, in its folder, each its own " +
  "report: the workspace starts none for it.";
export const SIGNED_GOAL_WORDS = "A linked session is the Manager: each goal you send it is signed with your " +
  "passkey, its words alone, so that no one but you can make one up. Checks and protected paths are no part of what " +
  "you sign.";
const REVIEW_LIFE_MS = 4 * 60 * 1000;            // the server takes a nonce for five minutes: sign within four of it
// how long a goal signed to a linked Manager waits for its session to take it: a day (the most a command lives,
// link_commands.LIFE_MAX), within the hold's own end
const GOAL_LIFE = 86400;
export const HOLD_ENDED_GOAL_WORDS = "Its hold has ended: the linked session that was the Manager holds nothing now. " +
  "Sign it again, choose another session or an account, or take the role back, before you send a goal.";

/** The settings a linked session would hold the role under, in words, as the review names them before the owner
 *  signs their digest (passkey.settingsDigest). */
function holdWords(s: HoldSettings): string {
  const names = (xs: string[], map: Record<string, string>) => (xs.length ? xs.map((x) => map[x] ?? x).join(", ")
    : "none");
  const go = s.confirm_plans ? "your go on each plan" : "no wait for your go";
  return `the cap ${s.cap}, the models ${names(s.models, MODEL_NAMES)}, the efforts ${names(s.efforts, EFFORT_NAMES)}` +
    `, and ${go}`;
}

/** The words of the holder now: which linked session holds the role, since and until when, whether its hold ended (it
 *  then holds nothing, and the account's chat stays off until the owner acts), whether it is silent, and the notice
 *  where its own calls are none of Proofpane's. */
function HolderNow({ holder, busy, run }: { holder: Holder; busy: boolean; run: Run }) {
  const since = holder.since ? `, since ${dayTime(holder.since)}` : "";
  const until = holder.live && holder.until ? `, until ${dayTime(holder.until)}` : "";
  const ended = `Its hold ended${holder.until ? ` at ${dayTime(holder.until)}` : ""}: it holds nothing now, and ` +
    "the account's chat stays off. Sign it again, choose another session or an account, or take the role back.";
  const heard = holder.last_heard_at ? `since ${dayTime(holder.last_heard_at)}` : "yet";
  return (
    <div className="space-y-1 rounded border border-line px-2 py-1.5">
      <p className="text-[12px]">
        The Manager is the linked session <span className="font-medium">{holder.name ?? "no longer linked"}</span>
        {since}{until}.
      </p>
      {!holder.live && <p className="text-[11.5px] text-wait">{ended}</p>}
      {holder.live && holder.silent && (
        <p className="text-[11.5px] text-wait">
          {`Not heard from ${heard}: goals sent wait for it, each a day at most, and you may give the role to another.`}
        </p>
      )}
      {holder.notice && <p className="text-[11.5px] text-wait">{holder.notice}</p>}
      <p className="text-[11px] text-faint">{OWN_MACHINE_WORDS}</p>
      <button className="btn btn-sm" disabled={busy} onClick={() => run(() => ControllerApi.dropHolder())}>
        Take the role back
      </button>
    </div>
  );
}

/** One of the owner's live links, offered as the Manager: a silent one shown and not offered, one whose own calls are
 *  none of Proofpane's with the words that say so. */
function HolderChoice({ l, chosen, busy, choose }: {
  l: HolderLink; chosen: boolean; busy: boolean; choose: (l: HolderLink) => void;
}) {
  const heard = l.last_heard_at ? `last heard ${dayTime(l.last_heard_at)}` : "not heard from yet";
  return (
    <li className="flex flex-wrap items-center gap-2 text-[12px]">
      <span className="font-medium">{l.name}</span>
      <span className="text-faint">({l.kind})</span>
      <span className="text-faint">{heard}</span>
      {l.silent && <span className="text-wait">silent: not offered</span>}
      {l.notice && <span className="basis-full text-[11px] text-wait">{l.notice}</span>}
      <button className="btn btn-sm" disabled={busy || !l.offered || chosen} onClick={() => choose(l)}>
        Make it the Manager
      </button>
    </li>
  );
}

/** The owner's choice of a linked session as the Manager, and the holder now (the view's own, read with it). The
 *  linked sessions are read only while the owner has the chooser open; choosing one reviews what is signed, then signs
 *  it with a passkey, the prompt first, nothing awaited before it. */
function LinkedManager({ holder, busy, run }: { holder: Holder | null; busy: boolean; run: Run }) {
  const [choosing, setChoosing] = useState(false);
  const [offer, setOffer] = useState<HolderOffer | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [review, setReview] = useState<{ prepared: Prepared; name: string; settings: HoldSettings; at: number } | null>(
    null);
  const [error, setError] = useState<string | null>(null);
  useVisibleEvery(choosing, 15000, async (signal) => {
    try {
      const got = await ControllerApi.holders(signal);
      if (signal.aborted) return true;
      setOffer(got);
      setFailed(null);
      return true;
    } catch (e) {
      if (aborted(e)) return true;
      setFailed(errorWords(e));
      return false;
    }
  });
  const choose = async (l: HolderLink) => {
    if (!offer) return;
    setError(null);
    const settings = offer.settings;
    if (!settings) {
      setError("The settings the role would be held under could not be read just now: choose the session again.");
      return;
    }
    try {
      setReview({ prepared: await prepareHolder(l.id, settings), name: l.name, settings, at: Date.now() });
    } catch (e) {
      setError(errorWords(e));
    }
  };
  const sign = () => {
    if (!review) return;
    if (Date.now() - review.at > REVIEW_LIFE_MS) {
      setReview(null);
      setError("This review is more than four minutes old: choose the session again.");
      return;
    }
    const p = review.prepared;
    void run(async () => { await ControllerApi.setHolder(await signPrepared(p)); })
      .then((ok) => { if (ok) setReview(null); });
  };
  const until = review ? Number(review.prepared.payload.expires_at) : null;
  const key = review?.prepared.allow.length ? "hardware key" : "passkey";
  return (
    <div className="space-y-2" role="group" aria-label="A linked session as the Manager">
      <h4 className="text-[12px] font-semibold">A linked session as the Manager</h4>
      {holder && <HolderNow holder={holder} busy={busy} run={run} />}
      <p className="text-[11px] text-faint">{LINKED_WORDS}</p>
      <button className="btn btn-sm" aria-expanded={choosing}
        onClick={() => { setChoosing(!choosing); setReview(null); }}>
        {choosing ? "Hide the linked sessions" : "Choose a linked session\u2026"}
      </button>
      {choosing && failed && (
        <p className="text-[11px] text-faint">The linked sessions could not be read just now: {failed}</p>
      )}
      {choosing && offer && offer.links.length === 0 && (
        <p className="text-[11px] text-faint">No session is linked to the ADE yet.</p>
      )}
      {choosing && offer && offer.links.length > 0 && (
        <ul className="space-y-1">
          {offer.links.map((l) => (
            <HolderChoice key={l.id} l={l} busy={busy} choose={(x) => void choose(x)}
              chosen={holder?.live === true && holder.link === l.id} />
          ))}
        </ul>
      )}
      {review && (
        <div className="space-y-1 rounded border border-line px-2 py-1.5" role="group"
          aria-label="Sign the Manager's holder">
          <p className="text-[12px]">
            {`You are making the linked session ${review.name} the Manager` +
              `${until ? ` until ${dayTime(until)}` : ""}, under ${holdWords(review.settings)}, as set now. Sign ` +
              `with your ${key} to make it so.`}
          </p>
          <div className="flex gap-2">
            <button className="btn btn-sm" disabled={busy} onClick={sign}>Sign</button>
            <button className="btn btn-sm" disabled={busy} onClick={() => setReview(null)}>Cancel</button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="text-[11.5px] text-fail">{error}</p>}
    </div>
  );
}

// ---- the controls: On, Off, Stop, End all, and the chat ------------------------------------------------------------

function Controls({ c, busy, run, onOpenSession }: { c: ControllerSettings; busy: boolean; run: Run; onOpenSession?: (id: string) => void }) {
  // risk point 7 opens over On as the owner comes to it — on hover or focus (before the press), and on the press itself
  // so a touch tap, which fires neither, still opens it (AgentTask's boundPress pattern). Keyed so it opens each time; it
  // holds nothing, and On works whether or not it is open. Reset to 0 once the controller is on, so that turning it off
  // again does not reopen the notice with no interaction with the On control (the owner must come to On afresh).
  const [arm, setArm] = useState(0);
  const arming = () => setArm((n) => n + 1);
  useEffect(() => { if (c.state === "on") setArm(0); }, [c.state]);
  return (
    <div className={`space-y-2 ${TERMS_IN_FLOW}`}>
      <div className="flex flex-wrap items-center gap-2">
        {c.state !== "on" && (
          <button className="btn btn-sm" disabled={busy} onMouseEnter={arming} onFocus={arming} onClickCapture={arming}
            onClick={() => run(() => ControllerApi.on())} title="Turn the Manager on">
            <Play size={13} /> Turn it on
          </button>
        )}
        {c.state === "on" && (
          <>
            <button className="btn btn-sm" disabled={busy} onClick={() => run(() => ControllerApi.off())} title="Turn the Manager off: it starts nothing new">
              <Power size={13} /> Turn it off
            </button>
            <Confirm label="Stop" again="Stop the Manager" danger icon={<Square size={12} />}
              onConfirm={async () => { await run(() => ControllerApi.stop()); }} />
          </>
        )}
        <Confirm label="End all" again="End every session" danger icon={<OctagonX size={12} />}
          onConfirm={async () => { await run(() => ControllerApi.endAll()); }} />
        {busy && <Loader2 size={13} className="animate-spin text-muted" aria-label="Working" />}
      </div>
      {/* the notice opens over On, under the row, as the owner comes to turn it on */}
      {c.state !== "on" && arm > 0 && <RiskTerms key={`on-${arm}`} point={7} kind="control" open />}
      {/* the view says whether the controller's account is still the owner's own; surfaced here, not only in the
          collapsed Settings, since an account no longer confirmed cannot start a session (the On would be refused) */}
      {c.account && !c.account_confirmed && (
        <p className="text-[11.5px] text-wait">The Manager's account is no longer confirmed as your own: confirm it in Connections, or it cannot start sessions.</p>
      )}
      <p className="text-[11.5px] text-muted">
        On, the Manager plans your goals into sub-goals and starts sessions for them on its account, up to {c.cap} at
        once; off, it starts nothing new. Stop ends its planning. End all ends every session it has open
        {c.open_sessions > 0 ? ` (${c.open_sessions} open now)` : ""}.
      </p>
      <Health c={c} />
      {/* the controller's own chat opens as a session's panel does, the session the workspace's record names; a
          workspace older than that names none, which the line below says */}
      {c.session && onOpenSession ? (
        <button className="btn btn-sm" onClick={() => onOpenSession(c.session!)} title="Open the Manager's chat">
          <MessageSquare size={13} /> Open the Manager's chat
        </button>
      ) : (
        <p className="text-[11px] text-faint">The Manager's own chat opens here once this workspace serves it.</p>
      )}
    </div>
  );
}

/** What reached the controller's account, and what did not: the app's own words for a packet, a read or a mirror that
 *  failed, and how many actions or other entries it refused; shown only where there is something to say. */
function Health({ c }: { c: ControllerSettings }) {
  const lines = [
    c.packet_error ? { k: "packet", w: c.packet_error, at: c.packet_error_at } : null,
    c.read_error ? { k: "read", w: c.read_error, at: c.read_error_at } : null,
    c.mirror_error ? { k: "mirror", w: c.mirror_error, at: c.mirror_error_at } : null,
    c.managers_error ? { k: "managers", w: c.managers_error, at: c.managers_error_at } : null,
    c.refused > 0 ? { k: "refused", w: `${c.refused} ${c.refused === 1 ? "action" : "actions"} refused`, at: c.refused_at } : null,
  ].filter((x): x is { k: string; w: string; at: number | null } => !!x);
  // a version mismatch is itself something to say; a mirrored version that matches is not, so the healthy state (no lines,
  // the account's version equal to the workspace's) draws nothing rather than an empty bordered box
  const mismatch = c.mirrored_version != null && c.mirrored_version !== c.version;
  if (!lines.length && !mismatch) return null;
  return (
    <div className="rounded-md border border-line px-2 py-1.5 text-[11px]">
      {mismatch && (
        <p className="text-wait">The workspace has version {c.mirrored_version} of the Manager's plan; the Manager is now at version {c.version}.</p>
      )}
      {lines.map((l) => (
        <p key={l.k} className="text-wait">{l.w}{l.at ? <span className="text-faint"> · {when(l.at)}</span> : null}</p>
      ))}
    </div>
  );
}

// ---- the settings --------------------------------------------------------------------------------------------------

function SettingsNote({ caps }: { caps: Capabilities }) {
  return (
    <p className="text-[10.5px] text-faint">
      The models and efforts are the ones this workspace's Claude Code offers{caps.claude_version ? ` (version ${caps.claude_version})` : ""}.
    </p>
  );
}

function Settings({ c, caps, busy, run }: { c: ControllerSettings; caps: Capabilities; busy: boolean; run: Run }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-line">
      <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <SlidersHorizontal size={13} className="text-muted" aria-hidden />
        <span className="text-[12px] font-medium">Settings</span>
        <span className="ml-auto text-[11px] text-muted">{c.cap} at once · {c.models.length} {c.models.length === 1 ? "model" : "models"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-2.5 py-2.5">
          <AccountPicker account={c.account} busy={busy} run={run} />
          <Cap cap={c.cap} busy={busy} run={run} />
          <ModelsEfforts c={c} caps={caps} busy={busy} run={run} />
          <Chat c={c} caps={caps} busy={busy} run={run} />
          <WaitForGo confirm={c.confirm_plans} busy={busy} run={run} />
          <SettingsNote caps={caps} />
        </div>
      )}
    </div>
  );
}

/** The one account the controller runs on: an account the owner has confirmed as their own in Connections (its state
 *  there is "confirmed"), by its name or address; "default" is the account new sessions start on. The confirmed accounts
 *  are read from the app when the picker opens; where that read fails, the picker keeps the account it has and says so. */
/** The account list's last option, Sign in another account: its value is no account's, an account being hex or
 *  "default". */
const SIGN_IN_ANOTHER = ":sign-in";

function AccountPicker({ account, busy, run }: { account: string | null; busy: boolean; run: Run }) {
  const [accounts, setAccounts] = useState<AccountOwnerView[] | null>(null);
  const [failed, setFailed] = useState(false);
  // the place to sign an account in and come back with it chosen (ManagerAccountSetup), open or not; and what the
  // place left the list to say when it closed (MANY_CONFIRMED), until the owner picks
  const [signingIn, setSigningIn] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useVisibleEvery(true, 60000, async (signal) => {
    try {
      const got = await Api.taskAccounts();
      if (signal.aborted) return true;
      setAccounts(got.accounts.filter((a) => a.state === "confirmed"));
      setFailed(false);
      return true;
    } catch (e) {
      if (aborted(e)) return true;
      setFailed(true);
      return false;
    }
  });
  // "default" is the account new sessions start on; it is offered only when it is among the confirmed accounts, like any
  // other, since the controller's account must be one the owner has confirmed as their own: the route
  // refuses an unconfirmed account. So no account is hardcoded; the picker offers what the owner has confirmed.
  const nameOf = (id: string) => (id === "default" ? "The account new sessions start on (default)" : id);
  const label = (a: AccountOwnerView) => (a.account === "default" ? nameOf("default") : (a.name || a.report?.email || a.account));
  const options = accounts ?? [];
  const inOptions = account != null && options.some((a) => a.account === account);
  return (
    <div className="space-y-1">
      <label className="block space-y-1">
        <span className="text-[11.5px] font-medium">Account</span>
        {/* picking a real account sets it; picking the "Choose an account…" placeholder does nothing, so the account is
            not cleared (which would turn the controller off) by a stray selection — the owner turns it off with Off */}
        <select className="input input-sm w-full" disabled={busy} value={account ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            setNote(null);
            if (v === SIGN_IN_ANOTHER) setSigningIn(true);
            else if (v) void run(() => ControllerApi.save({ account: v }));
          }}>
          <option value="">Choose an account…</option>
          {options.map((a) => <option key={a.account} value={a.account}>{label(a)}</option>)}
          {/* the controller's account, no longer among the confirmed ones, is kept as itself rather than silently changed */}
          {account && !inOptions && <option value={account}>{nameOf(account)}</option>}
          <option value={SIGN_IN_ANOTHER}>{"Sign in another account\u2026"}</option>
        </select>
      </label>
      {/* the notes sit outside the label, so the field's name is "Account" and not the notes too */}
      {/* the "no longer confirmed" warning waits for the confirmed accounts to be read: while that read is loading or has
          failed (accounts === null) the account is not yet known to be missing, so the warning holds rather than flash */}
      {accounts !== null && account && !inOptions && <p className="text-[11px] text-wait">This account is no longer among your confirmed accounts.</p>}
      {failed && <p className="text-[11px] text-faint">Your confirmed accounts could not be read just now; the account it has is kept.</p>}
      {note && <p className="text-[11px] text-wait">{note}</p>}
      {accounts !== null && options.length === 0 && (
        <div className="space-y-1">
          <p className="text-[11px] text-faint">
            No account is confirmed as your own yet, and the Manager runs on one.
          </p>
          <button className="btn btn-sm" disabled={busy} onClick={() => { setNote(null); setSigningIn(true); }}>
            Sign in an account
          </button>
        </div>
      )}
      {/* the place hands the list each read it makes, so the account it chose is named here at once, and takes
          what the list showed as what was there before */}
      {signingIn && (
        <ManagerAccountSetup shown={accounts === null ? null : accounts.map((a) => a.account)}
          onRead={(got) => { setAccounts(got.filter((a) => a.state === "confirmed")); setFailed(false); }}
          onChosen={(a) => run(() => ControllerApi.save({ account: a }))}
          onClose={(said) => { setSigningIn(false); setNote(said ?? null); }} />
      )}
    </div>
  );
}

const CAP_MIN = 1;
// the app's CAP_MOST: what the workspace's twelve programs at once leave beside the controller's own chat and one
// attempt of a requirement's manager
const CAP_MAX = 10;

function Cap({ cap, busy, run }: { cap: number; busy: boolean; run: Run }) {
  const [draft, setDraft] = useState(String(cap));
  const ref = useRef<HTMLInputElement>(null);
  // the cap can change from elsewhere (the poll brings a new value) while Settings stays open; sync the draft to it so
  // the field never disagrees with the header, but only while the owner is not in the field, so a sync never overwrites
  // what they are typing
  useEffect(() => {
    if (ref.current !== document.activeElement) setDraft(String(cap));
  }, [cap]);
  const save = () => {
    const n = Math.round(Number(draft));
    if (Number.isFinite(n) && n >= CAP_MIN && n <= CAP_MAX && n !== cap) run(() => ControllerApi.save({ cap: n }));
    else setDraft(String(cap));
  };
  return (
    <div className="space-y-1">
      <label className="block space-y-1">
        <span className="text-[11.5px] font-medium">Sessions at once</span>
        <input ref={ref} type="number" className="input input-sm w-24" min={CAP_MIN} max={CAP_MAX} disabled={busy} value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={save}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </label>
      {/* the hint sits outside the label, so the field's name is "Sessions at once" and not the hint too */}
      <p className="text-[11px] text-faint">From {CAP_MIN} to {CAP_MAX}: how many sessions the Manager may hold open at one time.</p>
    </div>
  );
}

/** The models and efforts the controller's tasks may take: a set, each chosen from what this workspace's Claude Code
 *  offers; a value set before and no longer offered is kept, so it is shown and not dropped. One must stay chosen. */
function ModelsEfforts({ c, caps, busy, run }: { c: ControllerSettings; caps: Capabilities; busy: boolean; run: Run }) {
  const modelOpts = [...new Set([...caps.model_aliases, ...c.models])];
  const effortOpts = [...new Set([...caps.efforts, ...c.efforts])];
  const toggle = (key: "models" | "efforts", list: string[], v: string) => {
    const next = list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
    if (next.length === 0) return;                                        // one must stay chosen (the route takes 1..8)
    run(() => ControllerApi.save({ [key]: next } as SettingsPatch));
  };
  return (
    <div className="space-y-2">
      <fieldset className="space-y-1">
        <legend className="text-[11.5px] font-medium">Models its tasks may take</legend>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {modelOpts.map((m) => (
            <label key={m} className="flex items-center gap-1 text-[12px]">
              <input type="checkbox" disabled={busy} checked={c.models.includes(m)} onChange={() => toggle("models", c.models, m)} />
              {MODEL_NAMES[m] || m}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-[11.5px] font-medium">Efforts its tasks may take</legend>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {effortOpts.map((e) => (
            <label key={e} className="flex items-center gap-1 text-[12px]">
              <input type="checkbox" disabled={busy} checked={c.efforts.includes(e)} onChange={() => toggle("efforts", c.efforts, e)} />
              {EFFORT_NAMES[e] || e}
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

/** The controller's own chat: the model and effort it runs at (Opus at extra-high by default). */
function Chat({ c, caps, busy, run }: { c: ControllerSettings; caps: Capabilities; busy: boolean; run: Run }) {
  const models = [...new Set([...caps.model_aliases, c.chat_model].filter(Boolean))];
  const efforts = [...new Set([...caps.efforts, c.chat_effort].filter(Boolean))];
  return (
    <div className="grid grid-cols-2 gap-2">
      <label className="block space-y-1">
        <span className="text-[11.5px] font-medium">Its chat's model</span>
        <select className="input input-sm w-full" disabled={busy} value={c.chat_model}
          onChange={(e) => run(() => ControllerApi.save({ chat_model: e.target.value }))}>
          {models.map((m) => <option key={m} value={m}>{MODEL_NAMES[m] || m}</option>)}
        </select>
      </label>
      <label className="block space-y-1">
        <span className="text-[11.5px] font-medium">Its chat's effort</span>
        <select className="input input-sm w-full" disabled={busy} value={c.chat_effort}
          onChange={(e) => run(() => ControllerApi.save({ chat_effort: e.target.value }))}>
          {efforts.map((e) => <option key={e} value={e}>{EFFORT_NAMES[e] || e}</option>)}
        </select>
      </label>
    </div>
  );
}

function WaitForGo({ confirm, busy, run }: { confirm: boolean; busy: boolean; run: Run }) {
  return (
    <label className="flex items-start gap-1.5">
      <input type="checkbox" className="mt-0.5" disabled={busy} checked={confirm}
        onChange={(e) => run(() => ControllerApi.save({ confirm_plans: e.target.checked }))} />
      <span className="text-[12px]"><span className="font-medium">Wait for my go</span>: the Manager holds each sub-goal's plan until you give it the go, rather than starting it by itself.</span>
    </label>
  );
}

// ---- the goals (the owner's requests) ------------------------------------------------------------------------------

// the server's per-request character cap (a request is text of at most 4000 characters); its 8,192
// packet-byte cap is a second, server-side limit over the escaped bytes the controller is given, which the client
// cannot cheaply replicate, so it is left to the server and shown as a refusal with the text kept
const REQUEST_CHARS = 4000;

type CheckDraft = { repo: string; name: string; command: string };

function Goals({ view, busy, run }: { view: ControllerView; busy: boolean; run: Run }) {
  const [text, setText] = useState("");
  const [checks, setChecks] = useState<CheckDraft[]>([]);
  const [paths, setPaths] = useState<Record<string, string>>({});
  // while a linked session holds the role, each goal is the owner's signed instruction to it: reviewed,
  // then signed with a passkey, the prompt first, nothing awaited before it
  const holder = view.holder?.live ? view.holder : null;
  // a hold kept that ended: no goal is sent, signed or not, until the owner acts on it
  const ended = view.holder && !view.holder.live ? view.holder : null;
  const [prepared, setPrepared] = useState<{ p: Prepared; at: number } | null>(null);
  const [signError, setSignError] = useState<string | null>(null);
  const review = async () => {
    if (!holder || !text.trim()) return;
    setSignError(null);
    const left = holder.until ? holder.until - Math.floor(Date.now() / 1000) : GOAL_LIFE;
    try {
      const p = await prepareSigned("instruction", holder.link, { text: text.trim().normalize("NFC"),
        life: Math.max(60, Math.min(GOAL_LIFE, left)) });
      setPrepared({ p, at: Date.now() });
    } catch (e) {
      setSignError(errorWords(e));
    }
  };
  const signAndSend = () => {
    if (!prepared) return;
    if (Date.now() - prepared.at > REVIEW_LIFE_MS) {
      setPrepared(null);
      setSignError("This review is more than four minutes old: review the goal again.");
      return;
    }
    const p = prepared.p;
    void run(async () => { await ControllerApi.postSignedGoal(await signPrepared(p)); }).then((ok) => {
      if (ok) { setText(""); setPrepared(null); }
    });
  };
  // a check's words go as the owner wrote them (the route refuses what is not of its form, in its own words); a row
  // left wholly empty is not sent, and protected paths go one a line for a repository a check names
  const send = () => {
    if (!text.trim()) return;
    const given = checks.filter((c) => c.repo || c.name || c.command);
    const named = new Set(given.map((c) => c.repo));
    const groups: Protected[] = Object.entries(paths).filter(([repo]) => named.has(repo))
      .map(([repo, v]) => ({ repo, paths: v.split("\n").map((x) => x.trim()).filter(Boolean) }))
      .filter((g) => g.paths.length > 0);
    run(() => ControllerApi.postRequest(text.trim(), given, groups)).then((ok) => {
      if (ok) { setText(""); setChecks([]); setPaths({}); }
    });
  };
  const chars = [...text.trim()].length;                                   // count what is sent (trimmed), as the server does
  return (
    <section className="space-y-2" aria-label="Goals">
      <h4 className="text-[12px] font-semibold">Goals</h4>
      <div className="space-y-1">
        <textarea className="input input-sm min-h-[56px] w-full" aria-label="A goal for the Manager" placeholder={"Tell the Manager a goal\u2026"} value={text} disabled={busy}
          onChange={(e) => { setText(e.target.value); setPrepared(null); }} />
        {ended ? <p className="text-[11.5px] text-wait">{HOLD_ENDED_GOAL_WORDS}</p>
          : holder ? <p className="text-[11px] text-faint">{SIGNED_GOAL_WORDS}</p>
          : <ChecksForm checks={checks} setChecks={setChecks} paths={paths} setPaths={setPaths} busy={busy} />}
        <div className="flex items-center gap-2">
          {!holder && !ended && (
            <button className="btn btn-sm" disabled={busy || !text.trim() || chars > REQUEST_CHARS} onClick={send}>
              <Send size={12} /> Send
            </button>
          )}
          {holder && !prepared && (
            <button className="btn btn-sm" disabled={busy || !text.trim() || chars > REQUEST_CHARS}
              onClick={() => void review()}>
              <Send size={12} /> Review
            </button>
          )}
          {chars > REQUEST_CHARS && <span className="text-[11px] text-fail">Too long by {chars - REQUEST_CHARS} characters.</span>}
        </div>
        {holder && prepared && (
          <div className="space-y-1 rounded border border-line px-2 py-1.5" role="group" aria-label="Sign the goal">
            <p className="text-[12px]">
              {`You are sending the linked session ${holder.name ?? "that holds the role"} this goal, word for word, ` +
                "as your own instruction to it. Sign with your " +
                `${prepared.p.allow.length ? "hardware key" : "passkey"} to send it.`}
            </p>
            <div className="flex gap-2">
              <button className="btn btn-sm" disabled={busy} onClick={signAndSend}>Sign and send</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => setPrepared(null)}>Cancel</button>
            </div>
          </div>
        )}
        {signError && <p role="alert" className="text-[11.5px] text-fail">{signError}</p>}
      </div>
      {view.requests.length === 0 && <p className="text-[11.5px] text-muted">No goals yet.</p>}
      <ul className="space-y-2">
        {view.requests.map((r) => (
          <GoalRow key={r.id} request={r} subGoals={view.requirements.filter((q) => q.request.id === r.id)} busy={busy} run={run} />
        ))}
      </ul>
    </section>
  );
}

/** The goal form's checks: a row for each, at most ten, each a repository chosen
 *  from the workspace's, a name and a command, and the protected paths of each repository a check names, one a line;
 *  what a check is and the words on protected paths beside them. The workspace's repositories are read when the owner
 *  opens it. */
function ChecksForm({ checks, setChecks, paths, setPaths, busy }: {
  checks: CheckDraft[]; setChecks: (c: CheckDraft[]) => void; paths: Record<string, string>;
  setPaths: (p: Record<string, string>) => void; busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [repos, setRepos] = useState<string[] | null>(null);
  useEffect(() => {
    if (!open || repos !== null) return;
    let live = true;
    Api.repos().then((r) => { if (live) setRepos(r.repos.filter((x) => !x.missing).map((x) => x.name)); })
      .catch(() => { if (live) setRepos([]); });
    return () => { live = false; };
  }, [open, repos]);
  const change = (i: number, key: keyof CheckDraft, v: string) => setChecks(checks.map((c, j) => (j === i ? { ...c, [key]: v } : c)));
  const named = [...new Set(checks.map((c) => c.repo).filter(Boolean))];
  return (
    <div className="rounded-md border border-line">
      <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="text-[12px] font-medium">Checks (optional)</span>
        {checks.length > 0 && <span className="ml-auto text-[11px] text-muted">{checks.length} {checks.length === 1 ? "check" : "checks"}</span>}
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-2.5 py-2" aria-label="The goal's checks">
          <p className="text-[10.5px] text-faint">{CHECKS_NOTICE}</p>
          <ProtectedNote />
          {checks.map((c, i) => (
            <div key={i} className="space-y-1 rounded border border-line px-2 py-1.5" aria-label={`Check ${i + 1}`}>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium">Check {i + 1}</span>
                <select className="input input-sm" aria-label={`Check ${i + 1}'s repository`} disabled={busy} value={c.repo}
                  onChange={(e) => change(i, "repo", e.target.value)}>
                  <option value="">Its repository…</option>
                  {(repos ?? []).map((r) => <option key={r} value={r}>{r}</option>)}
                  {c.repo && !(repos ?? []).includes(c.repo) && <option value={c.repo}>{c.repo}</option>}
                </select>
                <button className="btn btn-sm ml-auto" disabled={busy} onClick={() => setChecks(checks.filter((_, j) => j !== i))}>Remove</button>
              </div>
              <input className="input input-sm w-full" aria-label={`Check ${i + 1}'s name`} placeholder="Its name" value={c.name} disabled={busy}
                onChange={(e) => change(i, "name", e.target.value)} />
              <textarea className="input input-sm min-h-[36px] w-full font-mono" aria-label={`Check ${i + 1}'s command`} placeholder="Its command, run in a checkout of the commit"
                value={c.command} disabled={busy} onChange={(e) => change(i, "command", e.target.value)} />
            </div>
          ))}
          <button className="btn btn-sm" disabled={busy || checks.length >= CHECKS_MOST}
            onClick={() => setChecks([...checks, { repo: "", name: "", command: "" }])}>Add a check</button>
          {repos !== null && repos.length === 0 && <p className="text-[11px] text-faint">The workspace's repositories could not be read, or it has none.</p>}
          {named.map((repo) => (
            <label key={repo} className="block space-y-1">
              <span className="text-[11.5px] font-medium">Protected paths in {repo}, one a line</span>
              <textarea className="input input-sm min-h-[36px] w-full font-mono" disabled={busy} value={paths[repo] ?? ""}
                onChange={(e) => setPaths({ ...paths, [repo]: e.target.value })} />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** A goal's checks and protected paths as the owner sent them, and those no sub-goal chose. */
function GoalChecks({ request }: { request: Request }) {
  const kept = request.checks;
  if (!kept || kept.commands.length === 0) return null;
  return (
    <div className="mt-1 space-y-1" aria-label={`Goal ${request.n}'s checks`}>
      <div className="text-[10.5px] text-faint">the owner's checks</div>
      <ul className="space-y-1">
        {kept.commands.map((c) => (
          <li key={c.id} className="text-[11px]">
            <CheckName c={c} />
            {request.unchosen.includes(c.id) && <span className="text-wait"> · chosen by no sub-goal</span>}
            <p className="whitespace-pre-wrap break-words font-mono text-[10.5px] text-fg-dim">{c.command}</p>
          </li>
        ))}
      </ul>
      <ProtectedPaths groups={kept.protected} />
      <ProtectedNote />
    </div>
  );
}

function CheckName({ c }: { c: Pick<Check, "id" | "repo" | "name"> }) {
  return <span><span className="font-medium">Check {c.id}</span> <span className="font-mono text-faint">{c.repo}</span> {c.name}</span>;
}

function ProtectedPaths({ groups }: { groups: Protected[] }) {
  return (
    <>
      {groups.map((g) => (
        <p key={g.repo} className="text-[10.5px] text-faint">
          Protected in <span className="font-mono">{g.repo}</span>: <span className="font-mono">{g.paths.join("  ")}</span>
        </p>
      ))}
    </>
  );
}

function GoalRow({ request, subGoals, busy, run }: { request: Request; subGoals: Requirement[]; busy: boolean; run: Run }) {
  const waiting = subGoals.some((q) => q.waits_for_go);
  return (
    <li className="rounded-md border border-line px-2.5 py-2" aria-label={`Goal ${request.n}`}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[11px] font-medium text-muted">Goal {request.n}</span>
        <span className="text-[10.5px] text-faint">{when(request.at)}</span>
        {request.withdrawn_at && <span className="text-[10.5px] text-faint">· withdrawn {when(request.withdrawn_at)}</span>}
        {request.go_at && <span className="text-[10.5px] text-done">· you gave the go {when(request.go_at)}</span>}
        <span className="ml-auto flex gap-1.5">
          {/* the go covers the sub-goals that wait for it when it is given; a sub-goal planned later waits for a go of
              its own, so the button shows whenever any sub-goal of this goal waits, not only before
              the first go — go_at is the last go, which stays set */}
          {waiting && (
            <button className="btn btn-sm" disabled={busy} onClick={() => run(() => ControllerApi.go(request.id))} title="Give the Manager the go on this goal's plan">Give the go</button>
          )}
          {/* withdraw is for a goal the controller has not planned from yet; once it has (requirements), the route
              refuses it ("close its requirements instead"), so the button is offered only while there are none */}
          {!request.withdrawn_at && request.requirements.length === 0 && (
            <Confirm label="Withdraw" again="Withdraw it" onConfirm={async () => { await run(() => ControllerApi.withdraw(request.id)); }} />
          )}
        </span>
      </div>
      <Words text={request.text} whose="the owner's words" />
      <GoalChecks request={request} />
      {subGoals.length > 0 && (
        <p className="mt-1 text-[11px] text-faint">{subGoals.length} {subGoals.length === 1 ? "sub-goal" : "sub-goals"} below.</p>
      )}
    </li>
  );
}

// ---- the sub-goals (the controller's requirements) ---------------------------------------------------------------

function SubGoals({ view, busy, run }: { view: ControllerView; busy: boolean; run: Run }) {
  return (
    <section className="space-y-2" aria-label="Sub-goals">
      <h4 className="text-[12px] font-semibold">Sub-goals</h4>
      {view.requirements.length === 0 && <p className="text-[11.5px] text-muted">No sub-goals yet.</p>}
      <ul className="space-y-2">
        {view.requirements.map((r) => <SubGoalCard key={r.id} r={r} busy={busy} run={run} />)}
      </ul>
    </section>
  );
}

const ACTIVITY_WORDS: Record<string, string> = {
  held: "Held", waiting: "Waiting", running: "Running", "waiting for a reset": "Waiting for a reset", idle: "Idle",
  planned: "Planned", open: "Open", done: "Done", stopped: "Stopped",
};

function SubGoalCard({ r, busy, run }: { r: Requirement; busy: boolean; run: Run }) {
  return (
    <li className="rounded-md border border-line px-2.5 py-2" aria-label={r.title.text || "A sub-goal"}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-muted">from goal {r.request.n}</span>
        <span className="text-[11px] font-medium">{ACTIVITY_WORDS[r.activity] || r.activity}</span>
        {r.hold && <span className="text-[10.5px] text-wait">on hold</span>}
        {r.waits_for_go && <span className="text-[10.5px] text-wait">waiting for your go</span>}
        <span className="ml-auto flex flex-wrap gap-1.5">
          {r.state === "open" && (
            <>
              <button className="btn btn-sm" disabled={busy} onClick={() => run(() => ControllerApi.hold(r.id, !r.hold))}>
                {r.hold ? "Release" : "Hold"}
              </button>
              <Confirm label="Close done" again="Close as done" onConfirm={async () => { await run(() => ControllerApi.close(r.id, "done")); }} />
              <Confirm label="Close stopped" again="Close as stopped" danger onConfirm={async () => { await run(() => ControllerApi.close(r.id, "stopped")); }} />
            </>
          )}
        </span>
      </div>
      <div className="space-y-1.5">
        <div>
          <div className="text-[10.5px] text-faint">a model's words (the title)<Cleaned cut={r.title.cut} removed={r.title.hidden_removed} /></div>
          <p className="whitespace-pre-wrap break-words text-[12.5px] font-medium">{r.title.text.trim() || "—"}</p>
        </div>
        <Words text={r.words} whose="the owner's words" />
        {r.acceptance != null && <Words text={r.acceptance} whose="the owner's words (what done means)" />}
        {r.repos.length > 0 && <p className="font-mono text-[10.5px] text-faint">{r.repos.join(" · ")}</p>}
        {(r.checks.length > 0 || r.left_out.length > 0) && (
          <div className="space-y-0.5" aria-label="Its checks">
            <div className="text-[10.5px] text-faint">the owner's checks it chose</div>
            {r.checks.map((c) => <p key={c.id} className="text-[11px]"><CheckName c={c} /></p>)}
            {r.left_out.length > 0 && (
              <p className="text-[11px] text-wait">Left out: {r.left_out.map((c) => `check ${c.id} (${c.name})`).join(", ")}</p>
            )}
            <ProtectedPaths groups={r.protected} />
            <ProtectedNote />
          </div>
        )}
      </div>
      {r.manager && <ManagerBlock r={r} m={r.manager} busy={busy} run={run} />}
      {r.tasks.length > 0 && (
        <ul className="mt-1.5 space-y-1" aria-label="Its tasks">
          {/* each task's names are its repository's checks alone: a task's checks are those its plan chose there */}
          {r.tasks.map((t) => <TaskRow key={t.id} t={t}
            names={Object.fromEntries(r.checks.filter((c) => c.repo === t.repo).map((c) => [c.id, c.name]))} />)}
          {r.more_tasks > 0 && <li className="text-[11px] text-faint">and {r.more_tasks} more {r.more_tasks === 1 ? "task" : "tasks"}, not shown</li>}
        </ul>
      )}
    </li>
  );
}

// ---- a sub-goal's own manager --------------------------------------------------------------------------------------

const MANAGER_WORDS: Record<ManagerState, string> = {
  ready: "Ready: its next attempt starts once the workspace can take it", running: "Running an attempt",
  waiting: "Waiting for a change of its own", needs_owner: "Needs you", done: "Done: its last attempt found the sub-goal met",
  stopped: "Stopped: the sub-goal is closed",
};
const WAITS_WORDS: Record<string, string> = {
  on: "the Manager to be on", release: "your Release", window: "its account's window to reset",
  cap: "a place in the cap", sessions: "a place among the workspace's sessions", place: "a place for its attempt's chat",
  pace: "the pacing to allow a start", budget: "the day's attempts to fall below their most",
};
const FOR_OWNER_WORDS: Record<string, string> = {
  done: "Its last attempt ended done: Resume it to go on.", asked: "Its last attempt needs you: read its note, then Resume it.",
  failures: "Three attempts in a row failed or were cut short: Resume it once you have looked.",
  packet: "Its packet could not be built: why is said on the app's standard error, not on this page. Resume it once that is put right.",
  refused: "The workspace would not take its attempt: the two sides disagree on it. Resume it, or, where the workspace no longer holds its plan, close it.",
};
// a manager is never given its note, so the owner's words must say all they mean on their own; and a place
// freed wakes no manager, so the owner frees one and Resumes it
export const RESUME_WORDS = "Its manager is never given its note: write your words so that they say all they mean on their own.";
const PLACE_WORDS = "A place freed wakes no manager: stop a task you are done with, or raise the cap, then Resume it.";

function ManagerBlock({ r, m, busy, run }: { r: Requirement; m: Manager; busy: boolean; run: Run }) {
  const [words, setWords] = useState("");
  const [attempts, setAttempts] = useState<Attempt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chars = [...words.trim()].length;
  const may = m.state === "done" || m.state === "needs_owner" || m.state === "waiting" || m.state === "ready";
  const resume = () => run(() => ControllerApi.resume(r.id, words.trim() || null)).then((ok) => { if (ok) setWords(""); });
  const showAttempts = async () => {
    setError(null);
    try { setAttempts(await ControllerApi.attempts(r.id)); } catch (e) { if (!aborted(e)) setError(errorWords(e)); }
  };
  return (
    <div className="mt-1.5 space-y-1 rounded border border-line px-2 py-1.5 text-[11px]" aria-label="Its manager">
      <div className="flex flex-wrap items-center gap-1.5">
        <Bot size={12} aria-hidden />
        <span className="font-medium">Its own manager: {MANAGER_WORDS[m.state]}</span>
        <span className="text-faint">· {r.share} of the cap's sessions at most · {m.attempts} {m.attempts === 1 ? "attempt" : "attempts"}</span>
        {m.failures > 0 && <span className="text-wait">· {m.failures} in a row failed or cut short</span>}
      </div>
      {m.waits && (
        <p className="text-wait">Waits for {WAITS_WORDS[m.waits] ?? m.waits}{m.waits_until ? ` (${when(m.waits_until)})` : ""}.
          {["cap", "sessions", "place", "pace"].includes(m.waits) ? ` ${PLACE_WORDS}` : ""}</p>
      )}
      {m.for_owner && <p className="text-wait">{FOR_OWNER_WORDS[m.for_owner] ?? m.for_owner}</p>}
      {m.last && (
        <p className="text-faint">Its last attempt ({m.last.n}) ended {m.last.outcome ?? ""}{m.last.cause ? `: ${m.last.cause}` : ""}
          {m.last.ended_at ? ` · ${when(m.last.ended_at)}` : ""}</p>
      )}
      {m.note && (
        <div>
          <div className="text-[10.5px] text-faint">a model's words (its manager's note, for you alone)<Cleaned cut={m.note.cut} removed={m.note.hidden_removed} /></div>
          <p className="whitespace-pre-wrap break-words">{m.note.text.trim() || "—"}</p>
        </div>
      )}
      {may && (
        <div className="space-y-1">
          <textarea className="input input-sm min-h-[40px] w-full" aria-label="Your words for its manager's next attempt"
            placeholder="Your words for its next attempt (optional)…" value={words} disabled={busy}
            onChange={(e) => setWords(e.target.value)} />
          <p className="text-[10.5px] text-faint">{RESUME_WORDS}</p>
          <div className="flex items-center gap-2">
            <button className="btn btn-sm" disabled={busy || chars > DECISION_CHARS} onClick={() => void resume()}>
              <Play size={12} /> Resume
            </button>
            {chars > DECISION_CHARS && <span className="text-fail">Too long by {chars - DECISION_CHARS} characters.</span>}
          </div>
        </div>
      )}
      <button className="btn btn-sm" onClick={() => void showAttempts()}>Its attempts</button>
      {error && <p role="alert" className="text-fail">{error}</p>}
      {attempts && (
        <ul aria-label="Its attempts" className="space-y-0.5">
          {attempts.length === 0 && <li className="text-faint">None yet.</li>}
          {attempts.map((a) => (
            <li key={a.id}>
              Attempt {a.n}: {a.state}{a.outcome ? `, ${a.outcome}` : ""}{a.cause ? ` (${a.cause})` : ""}
              {a.refused_count > 0 ? ` · ${a.refused_count} of its calls refused (${a.refused.join(", ")})` : ""}
              {a.late ? " · its own end came after the app had ended it" : ""}
              {a.ended_at ? <span className="text-faint"> · {when(a.ended_at)}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const TASK_STATE_WORDS: Record<Task["state"], string> = {
  starting: "Starting", working: "Working", running: "Running", waiting: "Waiting", idle: "Idle", done: "Done",
  failed: "Failed", stopped: "Stopped",
};

// the linked sessions' names the view gives (its holder's), so a task a linked Manager reported is named by who
// reported it
const LinkNames = createContext<Record<string, string>>({});

function TaskRow({ t, names = {} }: { t: Task; names?: Record<number, string> }) {
  const linkNames = useContext(LinkNames);
  const parts = [t.repo, t.branch, t.model, t.effort ? EFFORT_NAMES[t.effort] || t.effort : null].filter(Boolean);
  return (
    <li className="rounded border border-line px-2 py-1 text-[11px]">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium">{TASK_STATE_WORDS[t.state]}</span>
        {parts.length > 0 && <span className="font-mono text-faint">{parts.join(" · ")}</span>}
        {t.continues > 0 && <span className="text-faint">· continued {t.continues}×</span>}
        {t.checking && <span className="text-faint">· its checks are queued or running</span>}
      </div>
      {/* the task as its sub-goal's manager wrote it: a model's words, never the owner's */}
      {t.text && (
        <div className="mt-0.5">
          <div className="text-[10.5px] text-faint">a model's words (the task, as its manager wrote it)<Cleaned cut={t.text.cut} removed={t.text.hidden_removed} /></div>
          <p className="whitespace-pre-wrap break-words">{t.text.text.trim() || "—"}</p>
        </div>
      )}
      {/* a linked Manager's own task: its state is that session's report, never the workspace's view */}
      {t.reported_by && (
        <p className="mt-0.5 text-[10.5px] text-faint">
          {`As ${linkNames[t.reported_by] || "a linked session"} reported it, from its own machine: its own word, ` +
            "not the workspace's."}
        </p>
      )}
      {/* a start outside the bounds the owner set: the app's own words for what was broken, shown as they are */}
      {t.flag && <p className="mt-0.5 whitespace-pre-wrap break-words text-wait">Outside its bounds: {t.flag}</p>}
      {/* an end its checks decided without a round: the workspace's own words */}
      {t.why && <p className="mt-0.5 text-fail">Ended {t.state}: {t.why}</p>}
      {t.checks && <TaskRound t={t} names={names} />}
      {(t.checks || Object.keys(names).length > 0) && (
        <>
          <p className="mt-0.5 text-[10.5px] text-faint">{FINISH_WORDS}</p>
          <p className="text-[10.5px] text-faint">{UPDATE_WORDS}</p>
        </>
      )}
    </li>
  );
}

function outcomeWords(r: CheckResult): string {
  if (r.outcome === "failed") return r.exit != null ? `failed (exit ${r.exit})` : "failed";
  if (r.outcome === "protected") {
    const n = r.protected ?? 0;
    return `failed: ${n} protected ${n === 1 ? "path" : "paths"} changed since its base`;
  }
  if (r.outcome === "not run") return r.why ? `not run: ${r.why}` : "not run";
  return r.outcome;
}

/** A task's last round of checks, each outcome by its check's name; its outputs on the owner's press, read from the
 *  task's session's own read and shown as a program's, as text, never markup. */
function TaskRound({ t, names }: { t: Task; names: Record<number, string> }) {
  const [out, setOut] = useState<RoundOutput | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const round = t.checks!;
  const show = async () => {
    if (!t.session) return;
    setLoading(true);
    setError(null);
    try {
      setOut(await ControllerApi.checksOutput(t.session));
    } catch (e) {
      if (!aborted(e)) setError(errorWords(e));
    } finally {
      setLoading(false);
    }
  };
  const nameOf = (id: number) => names[id] ?? `check ${id}`;
  return (
    <div className="mt-1 space-y-0.5" aria-label="Its checks' last round">
      <p className={round.passed ? "text-done" : "text-fail"}>
        Its checks, round {round.round}: {round.passed ? "every check passed" : "not every check passed"}
        <span className="text-faint"> · {when(round.at)}</span>
      </p>
      <ul>
        {round.results.map((r) => <li key={r.id}><span className="font-medium">{nameOf(r.id)}</span>: {outcomeWords(r)}</li>)}
      </ul>
      <ProtectedNote />
      {t.session && (
        <button className="btn btn-sm" disabled={loading} onClick={() => void show()}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : null} Show output
        </button>
      )}
      {error && <p role="alert" className="text-fail">{error}</p>}
      {out && out.round !== round.round && <p className="text-faint">The session's own read has round {out.round} now.</p>}
      {out && out.results.map((r) => (
        <div key={r.id} className="space-y-0.5">
          <div className="text-[10.5px] text-faint">{nameOf(r.id)} · {OUTPUT_WORDS}</div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-1.5 py-1 font-mono text-[10.5px]">
            {r.output ?? "(none kept)"}
          </pre>
        </div>
      ))}
    </div>
  );
}

/** Sessions the controller started that are not under a sub-goal of the owner's (the view's `other_sessions`:
 *  started for no requirement the owner has, open or ended in the last week): the oversight list where a start outside
 *  the controller's bounds surfaces, each task's flag naming what it broke. Drawn only when there are any. */
function OtherSessions({ sessions }: { sessions: Task[] }) {
  if (sessions.length === 0) return null;
  return (
    <section className="space-y-2" aria-label="Other sessions">
      <h4 className="text-[12px] font-semibold">Other sessions</h4>
      <p className="text-[11px] text-muted">Sessions the Manager started that are not under a sub-goal; a start outside its bounds is flagged with why.</p>
      <ul className="space-y-1" aria-label="Sessions not under a sub-goal">
        {sessions.map((t) => <TaskRow key={t.id} t={t} />)}
      </ul>
    </section>
  );
}

// ---- questions, decisions, and the log ---------------------------------------------------------------------------

// the server's per-decision/answer character cap (at most 2000 characters); its 6,144 packet-byte cap is
// the server's second limit, left to it as the request's byte cap is
const DECISION_CHARS = 2000;

function QuestionsAndDecisions({ view, busy, run }: { view: ControllerView; busy: boolean; run: Run }) {
  const open = view.questions.filter((q) => q.state === "open");
  return (
    <section className="space-y-2" aria-label="Questions and decisions">
      <h4 className="text-[12px] font-semibold">Questions</h4>
      {view.questions.length === 0 && <p className="text-[11.5px] text-muted">No questions.</p>}
      <ul className="space-y-2">
        {view.questions.map((q) => <QuestionRow key={q.id} q={q} busy={busy} run={run} />)}
      </ul>
      {open.length === 0 && view.questions.length > 0 && <p className="text-[11px] text-faint">Nothing is waiting on you.</p>}
      <Decisions decisions={view.decisions} busy={busy} run={run} />
    </section>
  );
}

function QuestionRow({ q, busy, run }: { q: Question; busy: boolean; run: Run }) {
  const [text, setText] = useState("");
  const chars = [...text.trim()].length;                                   // count what is sent (trimmed), as the server does
  const answer = () => { if (text.trim()) run(() => ControllerApi.answer(q.id, text.trim())).then((ok) => { if (ok) setText(""); }); };
  return (
    <li className="rounded-md border border-line px-2.5 py-2">
      <div className="text-[10.5px] text-faint">a model's words (a question)<Cleaned cut={q.cut} removed={q.hidden_removed} /></div>
      <p className="mb-1 whitespace-pre-wrap break-words text-[12px]">{q.text.trim() || "—"}</p>
      {q.state === "answered" ? (
        <p className="text-[11px] text-done">Answered{q.answered_at ? ` ${when(q.answered_at)}` : ""}.</p>
      ) : (
        <div className="space-y-1">
          <textarea className="input input-sm min-h-[44px] w-full" aria-label="Your decision on this question" placeholder="Your decision…" value={text} disabled={busy}
            onChange={(e) => setText(e.target.value)} />
          <div className="flex items-center gap-2">
            <button className="btn btn-sm" disabled={busy || !text.trim() || chars > DECISION_CHARS} onClick={answer}>Record the decision</button>
            {chars > DECISION_CHARS && <span className="text-[11px] text-fail">Too long by {chars - DECISION_CHARS} characters.</span>}
          </div>
        </div>
      )}
    </li>
  );
}

function Decisions({ decisions, busy, run }: { decisions: Decision[]; busy: boolean; run: Run }) {
  const [text, setText] = useState("");
  const chars = [...text.trim()].length;                                   // count what is sent (trimmed), as the server does
  const decide = () => { if (text.trim()) run(() => ControllerApi.decide(text.trim())).then((ok) => { if (ok) setText(""); }); };
  return (
    <div className="space-y-2">
      <h4 className="text-[12px] font-semibold">Decisions</h4>
      <div className="space-y-1">
        <textarea className="input input-sm min-h-[44px] w-full" aria-label="A decision of your own to record" placeholder="Record a decision of your own…" value={text} disabled={busy}
          onChange={(e) => setText(e.target.value)} />
        <div className="flex items-center gap-2">
          <button className="btn btn-sm" disabled={busy || !text.trim() || chars > DECISION_CHARS} onClick={decide}>Record a decision</button>
          {chars > DECISION_CHARS && <span className="text-[11px] text-fail">Too long by {chars - DECISION_CHARS} characters.</span>}
        </div>
      </div>
      {decisions.length === 0 && <p className="text-[11.5px] text-muted">No decisions yet.</p>}
      <ul className="space-y-1.5">
        {decisions.map((d) => (
          <li key={d.id} className="rounded-md border border-line px-2.5 py-1.5">
            <div className="text-[10.5px] text-faint">the owner's words (decision {d.n}){d.question ? " · answering a question" : ""}{d.requirement ? " · given with a Resume, to that sub-goal's manager alone" : ""} · {when(d.at)}</div>
            <p className="whitespace-pre-wrap break-words text-[12px]">{d.text.trim() || "—"}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

const LOG_PAGE = 50;

/** The log (the app's journal): the app's own words for each thing that happened, newest first, more loaded on ask. */
function Log() {
  const [lines, setLines] = useState<Line[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const load = useCallback(async (before: number | null) => {
    setLoading(true);
    setError(null);
    try {
      const got = await ControllerApi.journal(before, LOG_PAGE);
      setLines((prev) => (before == null ? got : [...(prev ?? []), ...got]));
      if (got.length < LOG_PAGE) setDone(true);
    } catch (e) {
      setError(errorWords(e));
    } finally {
      setLoading(false);
    }
  }, []);

  if (lines === null) {
    return (
      <div className="space-y-1">
        <h4 className="text-[12px] font-semibold">Log</h4>
        {error && <p role="alert" className="text-[11px] text-fail">{error}</p>}
        <button className="btn btn-sm" disabled={loading} onClick={() => load(null)}><ScrollText size={12} /> Show the log</button>
      </div>
    );
  }
  const oldest = lines.length ? lines[lines.length - 1].seq : null;
  return (
    <div className="space-y-1">
      <h4 className="text-[12px] font-semibold">Log</h4>
      {error && <p role="alert" className="text-[11px] text-fail">{error}</p>}
      {lines.length === 0 && <p className="text-[11.5px] text-muted">The log is empty.</p>}
      <ul className="space-y-1">
        {lines.map((l) => (
          <li key={l.seq} className="flex items-baseline gap-2 text-[11px]">
            <span className="flex-none font-mono text-faint">{when(l.at)}</span>
            <span className="whitespace-pre-wrap break-words text-fg-dim">{l.words.trim() || l.kind}</span>
          </li>
        ))}
      </ul>
      {!done && lines.length > 0 && (
        <button className="btn btn-sm" disabled={loading} onClick={() => load(oldest)}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : null} Show more
        </button>
      )}
    </div>
  );
}

type Run = (fn: () => Promise<unknown>) => Promise<boolean>;
