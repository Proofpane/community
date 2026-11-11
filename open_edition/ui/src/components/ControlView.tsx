import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Cloud, Laptop, Loader2, Network, Plus, Puzzle, SquareArrowOutUpRight } from "lucide-react";
import {
  ApiError, DESKTOP, linkGrantAsks, linkWaiting, OLD_SIGN_IN, type LinkSessions, type SessionRecord,
} from "../api";
import { AGENT_ID, type PopOut } from "../popOut";
import {
  accountGroups, agentName, agentsMoreWords, agentsReadWords, agentStatus, effortWords, EVERY, MapApi, mapState,
  mayPopOut, openPopOut, ranSeconds, ranWords, readWords, tokenWords, useTicking, useVisibleEvery,
  type MapAgent, type MapCalls, type MapSession, type MapTurn, type SessionCalls, type WorkspaceMap,
} from "../sessionMap";
import { agentOf, agentOfProgram, sessionState, SIGN_IN_PROGRAMS, toSession, useStore } from "../store";
import type { SessionState } from "../types";
import { useMedia } from "../useMedia";
import { AgentBadge, agentLabel, StatusDot, stateLabel } from "./bits";
import { ControllerPanel } from "./ControllerPanel";
import { SkillsPanel } from "./Skills";
import { Tool } from "./RunTree";

// The control view: the main panel's default where no session is open. It draws the
// map of the workspace's sessions, grouped by the account each runs on, each session a node with a line to each of its
// agents, each agent a row as Claude Code's own agent map draws it. A session opened in the map shows its calls under
// each agent. The map, a session's map or an agent opens in a window or tab of its own (a pop-out), where it draws that
// one panel. What it shows is names, times and counts, and a session's title as the list shows it (the first line of
// its first prompt where it was never named); never a
// reply, and an agent's name nowhere but here (never in a notification, a push or the page's title). The owner's
// linked sessions are drawn on it too, each with its count of what waits for the owner,
// which opens Approvals, the one list: its open prompts, its questions to the owner, the proposals it made and its asks
// for more in a task's grant; never the sessions' words.

const KIND_WORDS: Record<string, string> = { claude: "Claude", codex: "Codex" };

/** What the map's reads say when one fails: the server's own words, or where the server itself has no route for the map
 *  (a page newer than the server it talks to, whose framework says only "Not Found"), that it has none yet. A workspace
 *  with no map yet is said so by the server (workspace_routes.MAP_NOT_YET). */
function failedWords(e: unknown): string {
  if (e instanceof ApiError && e.status === 404 && e.message === "Not Found") {
    return "This server does not have the map yet: it comes with a later version of Proofpane Community.";
  }
  if (e instanceof ApiError && e.status === 401) return "This page is not signed in: sign in again, then reload it.";
  return e instanceof Error ? e.message : String(e);
}
const aborted = (e: unknown) => e instanceof DOMException && e.name === "AbortError";
// a 401 answered to the sign-in the page had before it signed in again ("Sign out everywhere" gives it a new one): no
// sign-out, and the next read carries the new one
const staleSignIn = (e: unknown) => e instanceof ApiError && e.status === 401 && e.message === OLD_SIGN_IN;
const signedOut = (e: unknown) => e instanceof ApiError && e.status === 401 && !staleSignIn(e);
// where a pop-out opens: a window of its own in the desktop app, a tab of its own in a browser
const OWN = DESKTOP ? "a window of its own" : "a tab of its own";
// how far behind what was counted on the workspace's time in an answer (its `at`) can be, in seconds, from its whole
// seconds and the time the answer took to come back
const ROUNDING = 2;

export function ControlView({ pop, empty, onOpenSession, onNew, onApprovals }: {
  /** In a pop-out window: the one panel it draws. */
  pop?: PopOut;
  /** What the main panel shows where the map has no session (Get started, or a word to start one). */
  empty?: ReactNode;
  /** Open a session's own panel, as the list does (the main page's). */
  onOpenSession?: (id: string) => void;
  onNew?: () => void;
  /** Open Approvals, the one list, where a linked session's waiting items are read and answered (the main page's). */
  onApprovals?: () => void;
}) {
  const [read, setRead] = useState<{ map: WorkspaceMap; at: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skills, setSkills] = useState(false);              // the Skills view, opened from here
  const phone = useMedia("(max-width: 767px)");
  const popOuts = !pop && mayPopOut(phone);
  useVisibleEvery(true, EVERY, async (signal) => {
    try {
      const map = await MapApi.map(signal);
      setRead({ map, at: Date.now() / 1000 });
      setError(null);
      return true;
    } catch (e) {
      if (staleSignIn(e)) return true;
      if (!aborted(e)) setError(failedWords(e));
      if (signedOut(e)) setRead(null);              // what a sign-in that ended had shown is not left drawn
      return false;
    }
  });
  const accounts = useStore((s) => s.accounts);
  const links = useStore((s) => s.links);
  const records = useStore((s) => s.records);
  const byId = useMemo(() => new Map(records.map((r) => [r.id, r])), [records]);
  const signIns = useStore((s) => s.signIns);
  // the sessions it draws are the list's: its sign-ins left out, as the list leaves them out (store.ts isSignIn), and
  // one run by a sign-in's program that the page has no record of with them;
  // and one the list holds closed, which it leaves out at once, before the workspace does
  const sessions = useMemo(() => {
    if (!read) return [];
    const signedIn = new Set(signIns.map((r) => r.id));
    return read.map.sessions.filter((s) => !signedIn.has(s.id) && !(SIGN_IN_PROGRAMS.has(s.program) && !byId.has(s.id))
      && !byId.get(s.id)?.closed);
  }, [read, signIns, byId]);
  const running = sessions.some((s) => s.agents.some((a) => a.status === "running"));
  const now = useTicking(running);
  // a running agent's time is counted to the workspace's now, from its own last answer: no clock of this device's; and
  // it never steps back by what an answer's `at` can be behind what was counted on from the one before, while a
  // workspace whose own time went back further is followed, not waited for
  const counted = useRef<number | null>(null);
  const fromRead = read ? read.map.at + Math.max(0, now - read.at) : null;
  const held = counted.current;
  const wsNow = fromRead === null ? now : held !== null && held > fromRead && held - fromRead <= ROUNDING ? held : fromRead;
  counted.current = fromRead === null ? null : wsNow;
  const groups = useMemo(() => accountGroups(sessions, accounts), [sessions, accounts]);
  const popOut = (p: PopOut) => { openPopOut(p).catch((e) => useStore.getState().setError(e)); };

  if (pop && pop.view !== "map") {
    const s = sessions.find((x) => x.id === pop.session);
    const agent = pop.view === "agent" ? s?.agents.find((a) => a.id === pop.agent) : undefined;
    return (
      <section className="px-4 py-3" aria-label={pop.view === "agent" ? "An agent" : "A session's map"}>
        {error && <p role="alert" className="mb-2 text-[12.5px] text-fail">{error}</p>}
        {!read && !error && <Waiting />}
        {read && !s && <p className="text-[12.5px] text-muted">{byId.get(pop.session)?.closed
          ? "This session is closed." : "The workspace no longer has this session."}</p>}
        {s && pop.view === "session" && (
          <ul><SessionNode s={s} rec={byId.get(s.id)} listed={false} wsNow={wsNow} openAtFirst /></ul>
        )}
        {s && pop.view === "agent" && (agent
          ? <ul><AgentAlone session={s.id} a={agent} wsNow={wsNow} /></ul>
          : <p className="text-[12.5px] text-muted">This session no longer shows this agent.</p>)}
      </section>
    );
  }

  return (
    <section className="flex h-full flex-col overflow-y-auto" aria-label="The control view">
      <div className="flex flex-none items-center gap-2 border-b border-line px-4 py-2.5">
        <Network size={15} className="text-accent" aria-hidden />
        <h2 className="text-[13.5px] font-semibold">Every session and its agents</h2>
        <span className="ml-auto" />
        {onNew && <button className="btn btn-sm" onClick={onNew} title="New session (⌘N)"><Plus size={13} /> New session</button>}
        {!pop && (
          <button className="btn btn-sm" onClick={() => setSkills(!skills)} aria-expanded={skills} aria-label="Skills"
            title="The skills the sessions called: by day, by version, and how the tasks that used them ended">
            <Puzzle size={13} /> <span className="hidden sm:inline">Skills</span>
          </button>
        )}
        {popOuts && (
          <button className="icon-btn" onClick={() => popOut({ view: "map" })} aria-label={`Open the map in ${OWN}`}
            title={`Open the map in ${OWN}: it keeps itself current there`}>
            <SquareArrowOutUpRight size={14} />
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 px-4 py-3">
        {/* the controller's own panel, above the map of every session: it reads the controller's routes itself, and is
            not drawn in a pop-out, which draws one panel */}
        {!pop && <ControllerPanel onOpenSession={onOpenSession} />}
        {!pop && skills && <SkillsPanel onClose={() => setSkills(false)} />}
        {!pop && onApprovals && links && links.links.length > 0 && <LinkedSessions links={links} onApprovals={onApprovals} />}
        {error && <p role="alert" className="mb-3 text-[12.5px] text-fail">{error}</p>}
        {!read && !error && <Waiting />}
        {read && sessions.length === 0 && (empty ?? <p className="py-6 text-center text-[12.5px] text-muted">No sessions yet.</p>)}
        {!read && error && empty}
        {groups.map((g) => (
          <section key={g.key} className="mb-4" aria-label={g.kind ? `${g.name}, a ${KIND_WORDS[g.kind]} account` : "Sessions on no account"}>
            <h3 className="mb-1.5 flex items-baseline gap-2 text-[12.5px] font-medium text-fg-dim">
              {g.name}
              {g.kind && <span className="text-[11px] font-normal text-muted">{KIND_WORDS[g.kind]}</span>}
              <span className="text-[11px] font-normal text-faint">{g.sessions.length} {g.sessions.length === 1 ? "session" : "sessions"}</span>
            </h3>
            <ul className="space-y-2">
              {g.sessions.map((s) => (
                <SessionNode key={s.id} s={s} rec={byId.get(s.id)} listed={!pop} wsNow={wsNow}
                  onOpenSession={byId.has(s.id) ? onOpenSession : undefined} onPopOut={popOuts ? popOut : undefined} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

/** The owner's linked sessions (the open edition's ade/link.py), each a node with its count of what waits for the owner
 *  from it, which opens Approvals: its open prompts (those to look at too), its questions to the owner, the proposals
 *  it made and its asks for more in a task's grant. A count and a name, never what any of them says. */
function LinkedSessions({ links, onApprovals }: { links: LinkSessions; onApprovals: () => void }) {
  const widenings = useStore((s) => s.grantWidenings);
  return (
    <section className="mb-4" aria-label="Linked sessions">
      <h3 className="mb-1.5 flex items-baseline gap-2 text-[12.5px] font-medium text-fg-dim">
        Linked sessions
        <span className="text-[11px] font-normal text-faint">{links.links.length} linked</span>
      </h3>
      <ul className="space-y-2">
        {links.links.map((l) => {
          // with its asks for more in a task's grant, which wait in Approvals too
          const n = linkWaiting(links, l.id) + linkGrantAsks(widenings, l.id);
          return (
            <li key={l.id} aria-label={l.name} className="flex items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-2">
              {l.kind === "cloud" ? <Cloud size={14} className="flex-none text-muted" aria-hidden /> : <Laptop size={14} className="flex-none text-muted" aria-hidden />}
              <span className="min-w-0 flex-1">
                <span className="block truncate whitespace-pre text-[13px] font-medium">{l.name}</span>
                <span className="block truncate text-[11.5px] text-muted">
                  {l.kind === "cloud" ? "in the cloud" : "on a machine"}{l.silent ? " · silent" : ""}{l.stopped_at ? " · stopped" : ""}
                </span>
              </span>
              <button className={`btn btn-sm flex-none ${n ? "btn-primary" : ""}`} onClick={onApprovals}
                aria-label={`${n} waiting from ${l.name}: open Approvals`} title="Open Approvals, where what waits for you from it is read and answered">
                {n} waiting
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** A session's state as the list shows it: on a page with the list, from the session's own record where the page has
 *  it (store.ts sessionState), so the two never disagree; else, and in a pop-out, which has no list and reads its
 *  sessions' records less often than its map, the workspace's word for it. */
function stateOf(s: MapSession, rec: SessionRecord | undefined, listed: boolean): SessionState {
  return listed && rec ? sessionState(rec) : mapState(s.state);
}

/** The agent a session shows as, as the list shows it (store.ts agentOf): another provider's model by its name, an
 *  agent of the person's own by the name they gave it, where the page has the session's record; else its program's. */
function programOf(s: MapSession, rec: SessionRecord | undefined): { kind: ReturnType<typeof agentOf>; name?: string } {
  return rec ? { kind: agentOf(rec), name: rec.custom?.name || undefined } : { kind: agentOfProgram(s.program) };
}

function Waiting() {
  return <div className="flex justify-center py-8 text-muted"><Loader2 size={16} className="animate-spin" aria-label="Reading the map" /></div>;
}

/** A session's calls and turns while `on`: asked for at once and every 3 seconds while the page is visible. */
function useSessionCalls(session: string, on: boolean): { calls: SessionCalls | null; error: string | null } {
  const [got, setGot] = useState<{ calls: SessionCalls | null; error: string | null }>({ calls: null, error: null });
  useEffect(() => { if (!on) setGot({ calls: null, error: null }); }, [on]);
  useVisibleEvery(on, EVERY, async (signal) => {
    try {
      const calls = await MapApi.session(session, signal);
      setGot({ calls, error: null });
      return true;
    } catch (e) {
      if (staleSignIn(e)) return true;
      if (!aborted(e)) setGot((g) => ({ calls: signedOut(e) ? null : g.calls, error: failedWords(e) }));
      return false;
    }
  });
  return got;
}

function SessionNode({ s, rec, listed, wsNow, onOpenSession, onPopOut, openAtFirst = false }: {
  s: MapSession; rec?: SessionRecord; listed: boolean; wsNow: number; onOpenSession?: (id: string) => void;
  onPopOut?: (p: PopOut) => void; openAtFirst?: boolean;
}) {
  const [open, setOpen] = useState(openAtFirst);
  const got = useSessionCalls(s.id, open);
  const state = stateOf(s, rec, listed);
  const agent = programOf(s, rec);
  // its title as the list shows it where the page has its record (store.ts toSession), else the workspace's, else its
  // program's name: never none
  const title = (rec ? toSession(rec).title : s.title) || agentLabel(agent.kind, agent.name);
  const details = [agentLabel(agent.kind, agent.name), s.model, effortWords(s.effort), stateLabel(state),
    s.context_tokens != null ? `${tokenWords(s.context_tokens)} in context` : null].filter(Boolean).join(" · ");
  const why = agentsReadWords(s.agents_read);
  const more = agentsMoreWords(s.agents_more, s.agents.length);
  return (
    <li className="rounded-lg border border-line bg-panel" aria-label={title}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        <button className="icon-btn !h-6 !w-6 flex-none" aria-expanded={open} onClick={() => setOpen(!open)}
          aria-label={open ? "Hide its calls" : "Show its calls"} title={open ? "Hide its tool calls" : "Show its tool calls, under each agent"}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <StatusDot state={state} />
        <AgentBadge kind={agent.kind} name={agent.name} size={16} />
        <span className="min-w-0 flex-1">
          {onOpenSession
            ? <button className="block max-w-full truncate text-left text-[13px] font-medium hover:underline" onClick={() => onOpenSession(s.id)} title="Open this session">{title}</button>
            : <span className="block truncate text-[13px] font-medium">{title}</span>}
          <span className="block truncate text-[11.5px] text-muted">{details}</span>
          {(s.repo || s.branch) && <span className="block truncate font-mono text-[10.5px] text-faint">{[s.repo, s.branch].filter(Boolean).join(" · ")}</span>}
        </span>
        {onPopOut && (
          <button className="icon-btn !h-6 !w-6 flex-none" onClick={() => onPopOut({ view: "session", session: s.id })}
            aria-label={`Open this session's map in ${OWN}`} title={`Open this session's map in ${OWN}`}>
            <SquareArrowOutUpRight size={13} />
          </button>
        )}
      </div>
      {why && <p className="px-3 pb-2 pl-11 text-[11.5px] text-muted">{why}</p>}
      {open && <TheSessionsOwn got={got} agents={s.agents} />}
      {s.agents.length > 0 && (
        <ul className="pb-2 pl-6 pr-2.5" aria-label="Its agents">
          {s.agents.map((a) => (
            <AgentRow key={a.id} a={a} wsNow={wsNow}
              onPopOut={onPopOut && AGENT_ID.test(a.id) ? () => onPopOut({ view: "agent", session: s.id, agent: a.id }) : undefined}
              calls={open && got.calls ? <AgentCalls calls={got.calls} agent={a.id} /> : undefined} />
          ))}
        </ul>
      )}
      {/* the agents the workspace left out past those it gave (its running ones first, then its newest): said after
          the list, as a session's calls past the cut are, never as one of its items */}
      {more && <p className="-mt-1 pb-2 pl-10 pr-2.5 text-[11px] text-faint">{more}</p>}
    </li>
  );
}

/** An agent in a window of its own: its row, and its calls, asked for every 3 seconds while the window is visible. */
function AgentAlone({ session, a, wsNow }: { session: string; a: MapAgent; wsNow: number }) {
  const got = useSessionCalls(session, true);
  // a read that failed leaves the last calls read drawn below why the next did not come, as under a session's node
  const failed = got.error ? <p role="alert" className="pl-4 text-[11.5px] text-fail">{got.error}</p> : null;
  return (
    <AgentRow a={a} wsNow={wsNow} calls={got.calls ? <>{failed}<AgentCalls calls={got.calls} agent={a.id} /></>
      : failed ?? <Loader2 size={13} className="ml-4 animate-spin text-muted" aria-label="Reading its calls" />} />
  );
}

/** One agent, as Claude Code's map draws it: a status dot, its name, how long it ran or has run, its tokens. */
function AgentRow({ a, wsNow, onPopOut, calls }: { a: MapAgent; wsNow: number; onPopOut?: () => void; calls?: ReactNode }) {
  const st = agentStatus(a.status);
  const ran = ranSeconds(a, wsNow);
  const name = agentName(a);
  const effort = effortWords(a.effort);
  // a budget's words are the row's longest: on a narrow width they give way, cut short, before the name does (its whole
  // words stay in the page for a screen reader, and on hover), so that the name keeps its room; a level's are as short
  // as they were and stay whole (the budget's would leave the name no room on a phone's width)
  const budget = typeof a.effort === "number";
  // an agent with no name is named by its type ("An agent (Plan)"): its type is not said twice
  const named = !!a.name?.trim();
  return (
    <li className="relative pl-4">
      <span aria-hidden className="absolute bottom-0 left-0 top-0 border-l border-line" />
      <span aria-hidden className="absolute left-0 top-[13px] w-3 border-t border-line" />
      <div className="flex min-h-7 items-center gap-2 text-[12.5px]">
        <span className="dot flex-none" role="img" aria-label={st.label} title={st.label}
          style={{ background: st.color, width: 7, height: 7 }} />
        <span className="min-w-0 flex-auto truncate" title={name}>{name}</span>
        {a.agent_type && named && <span className="flex-none text-[11px] text-muted">{a.agent_type}</span>}
        {effort && (budget
          ? <span className="min-w-0 truncate text-[11px] text-faint" style={{ flexShrink: 100 }} title={effort}>{effort}</span>
          : <span className="flex-none text-[11px] text-faint">{effort}</span>)}
        {ran != null && <span className="flex-none font-mono text-[11.5px] text-fg-dim">{ranWords(ran)}</span>}
        {a.tokens != null && <span className="flex-none font-mono text-[11.5px] text-muted">{tokenWords(a.tokens)}</span>}
        {onPopOut && (
          <button className="icon-btn !h-6 !w-6 flex-none" onClick={onPopOut} aria-label={`Open this agent in ${OWN}`}
            title={`Open this agent in ${OWN}`}>
            <SquareArrowOutUpRight size={12} />
          </button>
        )}
      </div>
      {calls}
    </li>
  );
}

/** What an opened session read of itself: why a part was not read, the session's own calls and turns, the calls of
 *  agents its node does not show (no longer, or left out past those the workspace gave), and whether the answer was
 *  cut. Each agent's are under its row (AgentCalls). */
function TheSessionsOwn({ got, agents }: { got: { calls: SessionCalls | null; error: string | null }; agents: MapAgent[] }) {
  const failed = got.error ? <p role="alert" className="px-3 pb-2 pl-11 text-[11.5px] text-fail">{got.error}</p> : null;
  if (!got.calls) return failed ?? <div className="px-3 pb-2 pl-11"><Loader2 size={13} className="animate-spin text-muted" aria-label="Reading its calls" /></div>;
  // a read that failed leaves the last calls read drawn, here as under each agent's row, below why the next did not come
  const c = got.calls;
  const known = new Set(agents.map((a) => a.id));
  // the agents its node does not show, each named by its id, so that two are never
  // drawn alike, with its turns and its calls, where the second route gives either
  const holders = new Map(c.agents.map((a) => [a.id, a]));
  const elsewhere = Array.from(new Set([...c.agents.filter((a) => a.calls.length || a.more).map((a) => a.id),
    ...c.turns.map((t) => t.agent).filter((id): id is string => !!id)])).filter((id) => !known.has(id));
  const notRead = [readWords(c.calls_read, "calls"), readWords(c.turns_read, "turns")].filter(Boolean);
  return (
    <div className="px-3 pb-2 pl-11" aria-label="Its calls">
      {failed}
      {notRead.map((w) => <p key={w} className="text-[11.5px] text-muted">{w}</p>)}
      <div className="text-[11.5px] text-fg-dim">The session itself<TurnsLine turns={c.turns.filter((t) => t.agent === null)} /></div>
      <Calls calls={c.main} />
      {elsewhere.map((id) => {
        const holder = holders.get(id);
        return (
          <div key={id} className="mt-1">
            <div className="text-[11.5px] text-fg-dim">An agent the map does not show <span className="font-mono">({id})</span>
              <TurnsLine turns={c.turns.filter((t) => t.agent === id)} /></div>
            {holder && <Calls calls={holder} />}
          </div>
        );
      })}
      {c.cut && <p className="mt-1 text-[11px] text-faint">Cut to 64 KB: the calls left out are counted, not shown.</p>}
    </div>
  );
}

/** One agent's calls and turns, under its row. */
function AgentCalls({ calls, agent }: { calls: SessionCalls; agent: string }) {
  const mine = calls.agents.find((a) => a.id === agent);
  return (
    <div className="pb-1 pl-4">
      <TurnsLine turns={calls.turns.filter((t) => t.agent === agent)} />
      {mine ? <Calls calls={mine} /> : <p className="text-[11px] text-faint">No calls of its own read yet.</p>}
    </div>
  );
}

function Calls({ calls }: { calls: MapCalls }) {
  return (
    <>
      <ul>{calls.calls.map((c, i) => <Tool key={i} tool={{ name: c.tool, target: c.target ?? "", ms: null, error: c.failed }} depth={0} />)}</ul>
      {calls.more > 0 && <div className="text-[11px] text-faint">and {calls.more} more {calls.more === 1 ? "call" : "calls"}</div>}
    </>
  );
}

function TurnsLine({ turns }: { turns: MapTurn[] }) {
  if (!turns.length) return null;
  const requests = turns.reduce((n, t) => n + (t.requests || 0), 0);
  const tokens = turns.reduce((n, t) => n + (t.input_tokens || 0) + (t.output_tokens || 0), 0);
  return (
    <span className="font-mono text-[11px] text-faint">
      {": "}{turns.length} {turns.length === 1 ? "turn" : "turns"}, {requests} {requests === 1 ? "request" : "requests"}, {tokenWords(tokens)}
    </span>
  );
}
