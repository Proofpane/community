import { useEffect, useRef, useState } from "react";
import { ShieldAlert, ShieldCheck, X } from "lucide-react";
import { ALLOW_TOOL_CALL, Api, linkWaiting, type GrantWideningView, type HookRequest, type Proposal, type SessionAsk, type SessionRecord } from "../api";
import { agentOfProgram, useStore } from "../store";
import { agentLabel, useEscape } from "./bits";
import { useFocusInto } from "./Modal";
import { byApp, ProposedAnswer, Proposals } from "./Proposals";
import { ConfirmIt } from "./AccountDialog";
import { NextStep } from "./Channel";
import { LinkInbox } from "./LinkInbox";
import { GrantWidening } from "./TaskGrant";
import { confirmWays, type ConfirmWays } from "../passkey";
import { ago } from "../agents";
import { choices, EFFORT_NAMES, EFFORT_NOTES, type Capabilities } from "../chatSettings";
import { visible } from "../textRules";
import { CameBack, TakenLine, useActs } from "./Acting";
import { chatAskKey } from "./ChatPane";
import { confirmPresence } from "../presence";
import { clock, day, dayTime, full } from "../when";

/** The efforts a Claude Code ask may start with: the installed Claude Code's own, as a chat's are offered,
 *  "" being its default, and the one the ask names kept as itself when it is not among them. */
function askEfforts(caps: Capabilities, ask: SessionAsk): [string, string][] {
  const effort = choices(caps, { effort: ask.effort || undefined }).find((c) => c.key === "effort");
  return effort ? effort.list : [["", "Default effort"]];
}

/** What a Claude Code ask names of its effort, said before the choice of it: a request, which the person may change. */
function askedEffort(ask: SessionAsk): string {
  return ask.effort ? `It asks for ${EFFORT_NAMES[ask.effort] || ask.effort} effort, which you may change:`
    : "It names no effort: Claude Code's default, which you may change:";
}

/** Which session a held command came from and when, so the owner knows what they approve:
 *  the hook token's name (the owner's own, given as it was made), then the session's id and folder, said as its hook's
 *  word, since only the hook says them; each isolated (bdi), so a right-to-left letter in one cannot reorder the line,
 *  and a character not shown as itself written out. Then when it asked, and until when it waits for an answer, by the
 *  server's times, the day too when not the day of `now`; a request not answered by then is refused. */
export function HeldFrom({ q, now = Date.now() }: { q: HookRequest; now?: number }) {
  const asked = typeof q.created_at === "number" ? q.created_at : undefined;
  const until = typeof q.until === "number" ? q.until : undefined;
  const name = typeof q.token_name === "string" && q.token_name ? visible(q.token_name) : "";
  const said = [q.session ? ["session ", visible(q.session)] : null, q.cwd ? ["folder ", visible(q.cwd)] : null]
    .filter((x): x is string[] => x !== null);
  const today = new Date(now);
  return (
    <>
      <div className="mb-0.5 break-words text-[11.5px] text-muted">
        A held command{name ? <> from “<bdi>{name}</bdi>”</> : null}
        {said.length > 0 && <>; its hook says {said.map(([what, value], i) => <span key={what}>{i ? ", " : ""}{what}<bdi>{value}</bdi></span>)}</>}
      </div>
      {Boolean(asked || until) && (
        <div className="mb-1 text-[11px] text-faint">
          {asked ? <span title={full(asked)}>asked {ago(asked, now)}</span> : null}
          {asked && until ? "; " : null}
          {until ? <span title={full(until)}>{asked ? "w" : "W"}aits for an answer until {day(until, undefined, today) === day(today, undefined, today) ? clock(until) : dayTime(until, undefined, today)}</span> : null}
        </div>
      )}
    </>
  );
}

/** How long a start may take before its card comes back: the server gives a start up to 240 seconds
 *  (workspace_routes.py's ASK_START_WAIT, FINISH_WAIT and a minute), so the page waits a little longer than that. */
const START_WITHIN = 250_000;

/** What a chat's tool ask was answered, as its card says it from the press. */
const ANSWERED: Record<string, string> = { allow: "Allowed once", allow_session: "Allowed for this session", deny: "Denied" };

/** A card's press, as the card shows it: what it decided while it goes, or why it came back; only why, `said` false,
 *  where a part of the card draws what was decided itself (an app's proposed answer, whose Deny is the command's). */
function Acted({ acts, at, said = true }: { acts: ReturnType<typeof useActs>; at: string; said?: boolean }) {
  const taken = said ? acts.taken(at) : null;
  return (
    <>
      {taken && <TakenLine said={taken} sure={acts.sure(at)} />}
      <CameBack why={acts.failed(at)} />
    </>
  );
}

/** Everything waiting for the owner, in one place: what connected apps proposed (first: Proposals.tsx), tool calls a
 *  chat asks about, the sessions agents asked for (the agent's asks.py), which start only when the owner starts them,
 *  the asks for more in a task's grant (TaskGrant.tsx), which change nothing until the owner signs them with a passkey,
 *  what his linked sessions report waits for him (LinkInbox.tsx: their prompts, questions and proposals), and commands
 *  a hook held (with an app's proposed answer on its own item). */
export function Approvals(props: { records: SessionRecord[]; requests: HookRequest[]; sessionAsks?: SessionAsk[]; proposals?: Proposal[]; grantWidenings?: GrantWideningView[]; onClose: () => void; onOpen: (id: string) => void;
  /** the linked sessions' item a link opened (App.tsx's #item=<seq>), brought into view */
  item?: number }) {
  const setError = useStore((s) => s.setError);
  const refresh = useStore((s) => s.refresh);
  const refreshApprovals = useStore((s) => s.refreshApprovals);
  const say = useStore((s) => s.say);
  const caps = useStore((s) => s.caps);
  // each card's answer shows itself taken at once, its buttons disabled, and comes back with the reason if the server
  // does not take it; keyed by the card
  const acts = useActs(setError);
  const busy = (key: string) => !!acts.taken(key);
  // each Claude Code ask's effort as the person has it now: what the ask names until they change it, "" for none
  const [efforts, setEfforts] = useState<Record<string, string>>({});
  const effortOf = (a: SessionAsk) => efforts[a.id] ?? (a.effort || "");
  const asks = props.records.flatMap((r) => (r.chat?.asks || []).map((a) => ({ r, a })));
  const wanted = props.sessionAsks || [];
  // a terminal claude asks in its own terminal: the drawer says which, and opens it
  const terminals = props.records.filter((r) => r.running && r.activity?.state === "waiting");
  const proposals = props.proposals || [];
  const proposedFor = (q: HookRequest) => proposals.find((p) => p.state === "proposed" && p.kind === "approval" && p.request_id === q.id);
  // the linked sessions' prompts, questions to the owner and proposals, as the store last read them
  const linked = useStore((s) => linkWaiting(s.links));
  // a prompt a Close hid that still waits: never counted (he hid it), but the empty state says it waits below
  const hiddenWaiting = useStore((s) => { const h = s.links?.hidden_waiting; return Array.isArray(h) && h.length > 0; });
  const widenings = props.grantWidenings || [];
  const none = asks.length === 0 && props.requests.length === 0 && terminals.length === 0 && wanted.length === 0
    && byApp(proposals, props.requests).length === 0 && linked === 0 && widenings.length === 0;
  useEscape(props.onClose);
  const panel = useRef<HTMLElement>(null);
  useFocusInto(panel);

  // allowing asks the phone for the person first (presence.ts): a press the person does not confirm changes nothing on
  // the card, and is said as the page's error, as before; the server's time starts once they are confirmed
  const answer = async (session: string, id: string, decision: string) => {
    const allow = decision !== "deny";
    if (allow) {
      try { await confirmPresence(ALLOW_TOOL_CALL); } catch (e) { setError(e); return; }
    }
    void acts.act(chatAskKey(session, id), ANSWERED[decision] ?? decision,
                  () => (allow ? Api.chatAnswer(session, id, decision, { confirmed: true }) : Api.chatAnswer(session, id, decision)), refresh);
  };
  // approving a held command takes the person's own word, made for that command (ConfirmIt); denying, the sign-in alone
  const [asked, setApproving] = useState<{ id: string; ways: ConfirmWays } | null>(null);
  // a prompt asked for one request holds only while that request waits with no app's proposal on it: once it is gone,
  // or a proposal takes its place, the prompt is dropped, never shown again later with what it read then
  const approving = asked && props.requests.some((q) => q.id === asked.id && !proposedFor(q)) ? asked : null;
  useEffect(() => { if (asked && !approving) setApproving(null); }, [asked, approving]);
  const askApprove = async (id: string) => {
    try { setApproving({ id, ways: await confirmWays() }); } catch (e) { setError(e); }
  };
  const deny = (id: string) => void acts.act(`request:${id}`, "Denied", () => Api.decide(id, false), refreshApprovals);
  // a start waits as long as the server gives one (START_WITHIN), and opens the session it started whenever it answers;
  // one ask starts at a time, as before, so every ask's buttons wait while one goes; a refusal reads the list again, as
  // before
  const asking = acts.anyGoing("ask:");
  const answerAsk = (a: SessionAsk, start: boolean) => {
    let made: SessionRecord | null = null;
    void acts.act(`ask:${a.id}`, start ? "Starting it" : "Refused", async () => {
      if (start) {        // a Claude Code ask starts with the effort chosen here, "" being Claude Code's default; any other with none
        made = a.program === "claude" ? await Api.startAsk(a.id, effortOf(a)) : await Api.startAsk(a.id);
        // the effort chosen and not taken, said: a workspace agent older than the app takes none,
        // and the claude that starts may not offer it. Said before the refresh that follows, as startAgain
        // says a start's, so the session's start is said whatever that refresh does
        if (made.effort_notice) say(made.effort_notice, "wait", 15000);
      } else {
        await Api.refuseAsk(a.id);
      }
    }, async () => {
      // the list read again whatever the refresh does, and a refresh that fails said as the page's error, as before
      try {
        if (made) { await refresh(); props.onOpen(made.id); }
      } finally { await refreshApprovals(); }
    }, { within: START_WITHIN, done: start ? "Started" : "Refused", refused: refreshApprovals });
  };

  return (
    <div className="fixed inset-0 z-40" onMouseDown={props.onClose}>
      <aside ref={panel} tabIndex={-1} aria-label="Approvals" className="absolute right-2 top-12 flex max-h-[80vh] w-[min(440px,calc(100vw-1rem))] outline-none flex-col overflow-hidden rounded-xl border border-line-strong bg-panel shadow-2xl shadow-black/60" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex h-11 flex-none items-center justify-between border-b border-line px-4">
          <span className="flex items-center gap-2 text-[13px] font-semibold">
            {none ? <ShieldCheck size={15} className="text-done" /> : <ShieldAlert size={15} className="text-wait" />}
            Approvals
          </span>
          <button className="icon-btn" onClick={props.onClose} title="Close (Esc)"><X size={15} /></button>
        </div>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {none && (
            <div className="px-2 py-8 text-center text-[12.5px] text-muted">
              {hiddenWaiting ? "Nothing is waiting in your open list: a prompt you hid waits for your answer below, until its deadline." : "Nothing is waiting for you."}
            </div>
          )}
          {typeof Notification !== "undefined" && Notification.permission === "default" && (
            <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-[12px] text-muted">
              <span className="flex-1">Get a desktop notification when a session needs you, even in another tab.</span>
              <button className="btn btn-sm" onClick={() => void Notification.requestPermission()}>Turn on</button>
            </div>
          )}
          <Proposals proposals={proposals} requests={props.requests} />
          {asks.map(({ r, a }) => (
            <div key={`${r.id}-${a.id}`} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
              <button className="mb-1 block max-w-full truncate text-left text-[11.5px] text-muted hover:text-fg" onClick={() => props.onOpen(r.id)}>
                {(r.task || "").split("\n")[0]} · {r.repo}
              </button>
              <div className="mb-1.5 text-[12.5px]">Claude wants to use <span className="font-mono">{String(a.name || a.tool || "a tool")}</span></div>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg-dim">{String(a.summary || "")}</pre>
              <div className="mt-2 flex gap-2">
                <button className="btn btn-sm btn-primary" disabled={busy(chatAskKey(r.id, a.id))} onClick={() => void answer(r.id, a.id, "allow")}>Allow once</button>
                {Boolean(a.for_session) && <button className="btn btn-sm" disabled={busy(chatAskKey(r.id, a.id))} onClick={() => void answer(r.id, a.id, "allow_session")}>For this session</button>}
                <button className="btn btn-sm btn-danger" disabled={busy(chatAskKey(r.id, a.id))} onClick={() => void answer(r.id, a.id, "deny")}>Deny</button>
              </div>
              <Acted acts={acts} at={chatAskKey(r.id, a.id)} />
            </div>
          ))}
          {terminals.map((r) => (
            <div key={`t-${r.id}`} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
              <div className="mb-1 truncate text-[11.5px] text-muted">{(r.task || "").split("\n")[0]} · {r.repo}</div>
              <div className="mb-2 text-[12.5px]">{r.activity?.message || "Claude Code is waiting for you in its terminal."}</div>
              <button className="btn btn-sm btn-primary" onClick={() => props.onOpen(r.id)}>Open its terminal</button>
            </div>
          ))}
          {wanted.map((a) => (
            <div key={`ask-${a.id}`} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
              <button className="mb-1 block max-w-full truncate text-left text-[11.5px] text-muted hover:text-fg" onClick={() => props.onOpen(a.by.id)}
                title="Open the session that asked">
                {a.by.title || "A session"} · {a.label}
              </button>
              <div className="mb-1.5 text-[12.5px]">
                Its agent asks for a new {agentLabel(agentOfProgram(a.program))} session{a.base ? <> from <span className="font-mono">{a.base}</span></> : null}, to do this:
              </div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg">{a.task}</pre>
              {a.task.split("\n").length > 12 && (
                <div className="mt-1 text-[11px] text-wait">{a.task.split("\n").length} lines: read them all before you start it.</div>
              )}
              {a.program === "claude" && (
                // its effort: what it asks for is a request; the session starts with what is chosen here
                <div className="mt-2">
                  <label className="flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                    {askedEffort(a)}
                    <select aria-label="Effort" title="The effort the session starts with" disabled={asking}
                      value={effortOf(a)} className="input h-7 w-auto max-w-[180px] !py-0 text-[12px] text-fg"
                      onChange={(e) => {
                        const value = e.target.value;
                        setEfforts((was) => ({ ...was, [a.id]: value }));
                      }}>
                      {askEfforts(caps, a).map(([v, name]) => <option key={v} value={v}>{name}</option>)}
                    </select>
                  </label>
                  {EFFORT_NOTES[effortOf(a)] && (
                    <div className="mt-1 text-[11.5px] text-muted">{EFFORT_NOTES[effortOf(a)]}</div>
                  )}
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button className="btn btn-sm btn-primary" disabled={asking} onClick={() => answerAsk(a, true)}>
                  Start it
                </button>
                <button className="btn btn-sm btn-danger" disabled={asking} onClick={() => answerAsk(a, false)}>
                  Refuse
                </button>
              </div>
              <Acted acts={acts} at={`ask:${a.id}`} />
            </div>
          ))}
          {widenings.map((w) => <GrantWidening key={`grant-${w.id}`} w={w} withTask onChanged={refreshApprovals} />)}
          <LinkInbox item={props.item} />
          {props.requests.map((q) => (
            <div key={q.id} className="rounded-lg border border-wait/50 bg-wait/[0.05] p-3">
              <HeldFrom q={q} />
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2.5 py-2 font-mono text-[11.5px] text-fg">{q.command}</pre>
              {/* keyed by the proposal, so a prompt opened for one is never shown under another that took its place */}
              {proposedFor(q) ? <ProposedAnswer key={proposedFor(q)!.id} q={q} p={proposedFor(q)!} /> : approving?.id === q.id ? (
                // the next step takes the keyboard and is said
                <NextStep say="Approving this command: confirm it to approve it once.">
                  <ConfirmIt change={{ what: "approve", label: "Approving this command", action: "Approve once", subject: `request-approve:${q.id}`, ways: approving.ways }}
                    onConfirmed={async (confirmation) => { await Api.decide(q.id, true, confirmation); setApproving(null); await refreshApprovals(); }}
                    onCancel={() => setApproving(null)} />
                </NextStep>
              ) : (
                <div className="mt-2 flex gap-2">
                  <button className="btn btn-sm btn-primary" disabled={busy(`request:${q.id}`)} onClick={() => void askApprove(q.id)}>Approve once</button>
                  <button className="btn btn-sm btn-danger" disabled={busy(`request:${q.id}`)} onClick={() => deny(q.id)}>Deny</button>
                </div>
              )}
              <Acted acts={acts} at={`request:${q.id}`} said={!proposedFor(q)} />
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
