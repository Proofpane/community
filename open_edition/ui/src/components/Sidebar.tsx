import { ChevronDown, ChevronRight, Cloud, EyeOff, FolderGit2, HardDrive, Laptop, Layers, Lock, Network, PanelLeftClose, Plus, Settings, Unlink, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { HistorySession } from "../api";
import type { Session, SessionState } from "../types";
import { AgentBadge, Kbd, StatusDot, sizeShort, sizeWords, timeAgo } from "./bits";
import { CLIENT_WORDS, liveWord } from "./History";
import { InClear, SealWords, Unlock, useShownRows, type ShownRow } from "./Sealed";

const NO_MACHINE: HistorySession[] = [];
// the group of the sealed rows not open, apart from every repository's: no repository's name holds a NUL
const ENCRYPTED = "\u0000encrypted";

type Filter = "all" | "active" | "waiting" | "done";
const FILTERS: { id: Filter; label: string; match: (s: SessionState) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "active", label: "Active", match: (s) => s === "working" || s === "running" || s === "starting" || s === "waiting" },
  { id: "waiting", label: "Needs you", match: (s) => s === "waiting" },
  { id: "done", label: "Done", match: (s) => s === "done" || s === "failed" || s === "stopped" },
];

/** What the person should look at first: whatever needs them, then what is working, then the rest by recency. */
function urgency(s: SessionState) {
  return s === "waiting" ? 0 : s === "working" || s === "starting" ? 1 : s === "failed" ? 2 : 3;
}

/** The order of a repository's groups by account, the same whatever their sessions are doing: the
 *  program's own sign-in first, then by name, an account the page does not know (store.ts accountNameOf) last. */
function byAccount(a: string, b: string) {
  const rank = (name: string) => (name === "Default" ? 0 : name === "another account" ? 2 : 1);
  return rank(a) - rank(b) || a.localeCompare(b);
}

/** The group a machine's session is listed in: the folder name of the repository it worked in first (its own folder's
 *  where its machine named none), so it sits with the workspace's sessions of a repository of that name. */
export function machineRepo(s: Pick<HistorySession, "repositories" | "project">): string {
  const top = (s.repositories?.[0] ?? s.project ?? "").replace(/\/+$/, "");
  return top.split("/").pop() || top || "~";
}

export function Sidebar(props: {
  repos: string[];
  sessions: Session[];
  selected: string | null;
  onSelect: (id: string) => void;
  /** Close a session: its program stopped and itself taken out of the list, nothing of it removed. A
   *  closed one is not listed: search finds it, and opening it there puts it back. */
  onCloseSession?: (id: string) => void;
  onNew: (repo?: string) => void;
  /** Clone a repository: with none yet, the first thing to do. */
  onClone?: () => void;
  /** The workspace is on the person's own machine (the local provider): a repository may be added from a folder. */
  local?: boolean;
  /** The repositories added from a folder on this machine, by name: the folder (the local provider's). */
  linked?: Record<string, string>;
  /** Those added from a folder that is no longer there. */
  missing?: string[];
  /** Take one added from a folder out of the workspace: its link, never the folder. */
  onUnlink?: (repo: string) => void;
  onSettings: () => void;
  onOrder?: (ids: string[]) => void;    // the rows as shown, top to bottom: what ⌥1–9 and ⌥↑/↓ move through
  /** Each linked machine's Claude Code sessions (the History list), newest first: each listed in the group of the
   *  repository it worked in first, marked where it ran; one opens its conversation and the command to resume it.
   *  Those that came sealed are listed under "Encrypted" until the keys are unlocked and they are open. `onMachine`
   *  is given the row as served. */
  machine?: HistorySession[];
  onMachine?: (s: HistorySession) => void;
  /** Whether the workspace runs: its sessions, New and Connections are offered while it does. The linked machines'
   *  sessions are listed whether it runs or not. */
  workspace?: boolean;
  /** Nothing is read yet: no empty state is said, which may not be so. */
  loading?: boolean;
  /** Whether a Mac of the owner's is linked, and linking one (opened from the list's empty state). */
  macLinked?: boolean;
  onLink?: () => void;
  /** The list is the phone's drawer: its Close. */
  onClose?: () => void;
  /** Hide the list, on a computer: the page keeps it hidden until it is shown again. */
  onHide?: () => void;
  /** The control view: the main panel with no session open, where it draws every session's map; `control`
   *  says it is what the main panel shows now. */
  onControl?: () => void;
  control?: boolean;
}) {
  const workspace = props.workspace ?? true;
  const shownMachine = useShownRows(props.machine ?? NO_MACHINE);
  const [filter, setFilter] = useState<Filter>("all");
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [alt, setAlt] = useState(false);       // ⌥ held: each of the first nine rows shows its number
  useEffect(() => {
    const down = (e: KeyboardEvent) => e.key === "Alt" && setAlt(true);
    const up = (e: KeyboardEvent) => e.key === "Alt" && setAlt(false);
    const away = () => setAlt(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", away);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", away); };
  }, []);
  const match = FILTERS.find((f) => f.id === filter)!.match;
  // the sessions listed: all but the closed
  const listed = useMemo(() => props.sessions.filter((s) => !s.closed), [props.sessions]);
  const waiting = listed.filter((s) => s.state === "waiting").length;
  const groupSizes = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of props.sessions) if (s.group) m.set(s.group, (m.get(s.group) || 0) + 1);
    return m;
  }, [props.sessions]);

  // a machine's session is shown under All, and under Active while its machine's word says it runs: it has no other
  // state here to filter by
  const machine = useMemo(() => shownMachine.filter((s) => filter === "all" || (filter === "active" && !!liveWord(s))),
    [shownMachine, filter]);
  // a sealed row not open names no repository: those are listed apart, under Encrypted
  const sealed = useMemo(() => machine.filter((s) => s.seal), [machine]);
  const groups = useMemo(() => {
    const opened = machine.filter((s) => !s.seal);
    const names = Array.from(new Set([...props.repos, ...listed.map((s) => s.repo), ...opened.map(machineRepo)]));
    return names.flatMap((repo) => {
      const all = listed
        .filter((s) => s.repo === repo && match(s.state))
        .sort((a, b) => urgency(a.state) - urgency(b.state) || (b.updatedAt || "").localeCompare(a.updatedAt || ""));
      // a group for each account the repository's sessions name: with more than one account in the
      // workspace, "grove · Default" and "grove · Alma", in an order that stays whatever the sessions are doing
      // (byAccount). They come after the repository's own group, which holds its sessions that name no account and the
      // linked machines' (whose account the page does not know), where it has either; its controls are on its first
      const mine = opened.filter((s) => machineRepo(s) === repo);
      const named = Array.from(new Set(all.map((s) => s.accountName).filter((a): a is string => !!a))).sort(byAccount);
      const plain = all.filter((s) => !s.accountName);
      const parts: { account?: string; sessions: Session[]; machine: typeof mine }[] = [
        ...(plain.length || mine.length || !named.length ? [{ sessions: plain, machine: mine }] : []),
        ...named.map((account) => ({ account, sessions: all.filter((s) => s.accountName === account), machine: [] })),
      ];
      return parts.map((p, i) => ({
        key: p.account ? `${repo}\u0000${p.account}` : repo,
        repo,
        account: p.account,
        first: i === 0,
        sessions: p.sessions,
        machine: p.machine,
        ours: props.repos.includes(repo),
      }));
    });
  }, [props.repos, listed, match, machine]);
  const shown = useMemo(() => groups.flatMap((g) => (closed[g.key] ? [] : g.sessions.map((s) => s.id))), [groups, closed]);
  const onOrder = props.onOrder;
  useEffect(() => { onOrder?.(shown); }, [shown, onOrder]);

  return (
    <aside className="@container flex h-full flex-col bg-panel" aria-label="Sessions">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        {props.onClose && (
          <button className="icon-btn touch-target -ml-1 flex-none" onClick={props.onClose} title="Close the list" aria-label="Close the list" data-modal-close>
            <X size={16} />
          </button>
        )}
        <span className="label flex-1">Sessions</span>
        {props.onControl && (
          <button className={`icon-btn touch-target flex-none ${props.control ? "text-accent" : ""}`} onClick={props.onControl}
            aria-pressed={!!props.control} aria-label="Control view" title="Control view: every session and its agents, on a map">
            <Network size={14} />
          </button>
        )}
        {/* a plain button: the one filled action on a screen is the screen's own (Get started's next step, Ship) */}
        {workspace && (
          <button className="btn btn-sm touch-target" onClick={() => props.onNew()} title="New session (⌘N)">
            <Plus size={13} strokeWidth={2.4} /> New
          </button>
        )}
        {props.onHide && (
          <button className="icon-btn flex-none" onClick={props.onHide} aria-label="Hide the list of sessions" title="Hide the list of sessions">
            <PanelLeftClose size={14} />
          </button>
        )}
      </div>

      {/* one line at any width: a narrow list gives the filters less room around their words, never a second line */}
      <div className="flex gap-1 px-2 pb-2 @max-[16rem]:gap-0.5 @max-[16rem]:px-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            aria-pressed={filter === f.id}
            className={`h-6 flex-none whitespace-nowrap rounded px-2 text-[11.5px] font-medium @max-[16rem]:px-1.5 [@media(pointer:coarse)]:h-11 ${
              filter === f.id ? "bg-active text-fg" : "text-muted hover:bg-hover hover:text-fg-dim"
            }`}
          >
            {f.label}
            {f.id === "waiting" && waiting > 0 && <span className="ml-1 text-wait">{waiting}</span>}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {groups.length === 0 && !props.loading && sealed.length === 0 && (
          workspace ? (
            <div className="px-3 py-8 text-center text-[12.5px] text-muted">
              No repositories yet.
              <div className="mt-3">
                <button className="btn btn-sm" onClick={() => (props.onClone ? props.onClone() : props.onNew())}>{props.local ? "Add a repository" : "Clone a repository"}</button>
              </div>
            </div>
          ) : (
            <div className="px-3 py-8 text-center text-[12.5px] text-muted">
              {props.macLinked
                ? "Your Mac is linked: its Claude Code sessions are listed here once its link sends their list."
                : "Your Mac's Claude Code sessions are listed here once it is linked."}
              {!props.macLinked && props.onLink && (
                <div className="mt-3"><button className="btn btn-sm touch-target" onClick={props.onLink}>Link your Mac</button></div>
              )}
            </div>
          )
        )}
        {groups.map(({ key, repo, account, first, sessions, machine: elsewhere, ours }) => {
          const isClosed = closed[key];
          return (
            <div key={key} className="mb-1.5" aria-label={account ? `${repo}, on ${account}` : undefined}>
              <div className="group flex h-7 items-center gap-1.5 rounded px-1.5 text-fg-dim hover:bg-hover">
                <button className="flex min-w-0 flex-1 items-center gap-1.5" onClick={() => setClosed({ ...closed, [key]: !isClosed })}
                  title={account ? `${repo}: its sessions on the account ${account}` : undefined}>
                  {isClosed ? <ChevronRight size={13} className="text-faint" /> : <ChevronDown size={13} className="text-faint" />}
                  <FolderGit2 size={13} className="text-muted" />
                  <span className="truncate text-[12.5px] font-medium">{repo}</span>
                  {account && <span className="flex-none truncate text-[12px] text-muted">· {account}</span>}
                  {first && props.linked?.[repo] && (
                    <span className={`flex-none ${props.missing?.includes(repo) ? "text-fail" : "text-faint"}`}
                      title={props.missing?.includes(repo) ? `Its folder is gone from ${props.linked[repo]}: take it out, and add it again from where it is now` : `On this Mac, used where it is: ${props.linked[repo]}`}>
                      <HardDrive size={11} aria-label="on this Mac" />
                    </span>
                  )}
                </button>
                {first && props.linked?.[repo] && props.onUnlink && !props.sessions.some((s) => s.repo === repo) && (
                  <button className="icon-btn !h-5 !w-5 opacity-0 group-hover:opacity-100 focus:opacity-100" title={`Take ${repo} out of the workspace: its folder stays as it is`}
                    onClick={() => props.onUnlink!(repo)}>
                    <Unlink size={12} />
                  </button>
                )}
                {first && ours && (
                  <button className="icon-btn !h-5 !w-5 opacity-0 group-hover:opacity-100 focus-visible:opacity-100" title={`New session in ${repo}`} onClick={() => props.onNew(repo)}>
                    <Plus size={13} />
                  </button>
                )}
              </div>
              {!isClosed && sessions.map((s) => {
                const n = shown.indexOf(s.id) + 1;
                return <SessionRow key={s.id} s={s} number={alt && n > 0 && n <= 9 ? n : undefined} grouped={!!s.group && (groupSizes.get(s.group) || 0) > 1} active={props.selected === s.id} onClick={() => props.onSelect(s.id)}
                  onCloseSession={props.onCloseSession ? () => props.onCloseSession!(s.id) : undefined} />;
              })}
              {!isClosed && elsewhere.map((s) => <MachineRow key={`${s.link_id}/${s.id}`} s={s} onClick={() => props.onMachine?.(s.raw)} />)}
              {!isClosed && sessions.length === 0 && elsewhere.length === 0 && (() => {
                // a repository whose sessions are all closed says so: they are still there, and still keep it in the
                // workspace (Unlink is not offered while it has any)
                const shut = first ? props.sessions.filter((s) => s.closed && s.repo === repo && match(s.state)).length : 0;
                return <div className="py-1 pl-8 text-[11.5px] text-faint">{shut ? `${shut} closed: search (⌘K) finds ${shut === 1 ? "it" : "them"}` : "No sessions"}</div>;
              })()}
            </div>
          );
        })}
        {sealed.length > 0 && (
          <div className="mb-1.5" aria-label="Encrypted sessions">
            <div className="flex min-h-7 flex-wrap items-center gap-1.5 rounded px-1.5 text-fg-dim hover:bg-hover">
              <button className="flex min-w-0 flex-1 items-center gap-1.5" onClick={() => setClosed({ ...closed, [ENCRYPTED]: !closed[ENCRYPTED] })}
                title="Sessions whose machine sent their titles and folders encrypted">
                {closed[ENCRYPTED] ? <ChevronRight size={13} className="text-faint" /> : <ChevronDown size={13} className="text-faint" />}
                <Lock size={13} className="text-muted" />
                <span className="truncate text-[12.5px] font-medium">Encrypted</span>
              </button>
              <Unlock />
            </div>
            {!closed[ENCRYPTED] && sealed.map((s) => <MachineRow key={`${s.link_id}/${s.id}`} s={s} onClick={() => props.onMachine?.(s.raw)} />)}
          </div>
        )}
        {/* the workspace's own sessions are read from it, so they are not listed while it does not run */}
        {!workspace && !props.loading && (groups.length > 0 || sealed.length > 0) && (
          <div className="px-3 pt-2 text-[11.5px] text-faint">The workspace's sessions are listed here while it runs.</div>
        )}
      </div>

      {/* the keys, where there is a keyboard (⌘N while the workspace runs); with neither them nor Connections, no bar */}
      <div className={`flex min-h-10 items-center justify-between border-t border-line px-3 py-2 text-[11.5px] text-muted ${workspace ? "" : "[@media(pointer:coarse)]:hidden"}`}>
        <span className="flex min-w-0 items-center gap-1.5 whitespace-nowrap [@media(pointer:coarse)]:hidden"
          title={workspace ? "⌘N: new session · ⌘K: search · ⌥1–9: the session in that row · ⌥↑ ⌥↓: the one above or below" : "⌘K: search"}>
          {workspace && <span className="contents @max-[16rem]:hidden"><Kbd>⌘N</Kbd> new ·</span>} <Kbd>⌘K</Kbd> search{" "}
          {workspace && <span className="contents @max-[16rem]:hidden">· <Kbd>⌥1–9</Kbd> go</span>}
        </span>
        {workspace && <button className="icon-btn touch-target ml-auto !h-6 !w-6" onClick={props.onSettings} title="Connections" aria-label="Connections"><Settings size={14} /></button>}
      </div>
    </aside>
  );
}

/** One of a linked machine's Claude Code sessions in the list: running or not, where it ran, its title (kept on its
 *  machine when withheld; for one that came sealed, what it opened to, or where it stands), marked where it came in the
 *  clear while encryption is on. */
function MachineRow({ s, onClick }: { s: ShownRow; onClick: () => void }) {
  const running = liveWord(s);
  const cloud = s.kind === "cloud";
  const ran = s.client ? CLIENT_WORDS[s.client] ?? null : null;
  const kept = s.withheld || s.title === null;
  return (
    <button onClick={onClick} className="relative flex w-full items-center gap-2 rounded-md py-1.5 pl-6 pr-2 text-left hover:bg-hover"
      title={`${cloud ? "In the cloud" : `On ${s.link}'s machine`}${ran ? `, in ${ran}` : ""}: its conversation, and the command that opens it again there`}>
      <span className={`inline-block h-1.5 w-1.5 flex-none rounded-full ${running === "working" ? "bg-run" : running ? "bg-idle" : "bg-line-strong"}`}
        title={running ? `${running}, when ${s.link}'s machine last listed its sessions` : undefined} />
      {cloud ? <Cloud size={14} className="flex-none text-accent" /> : <Laptop size={14} className="flex-none text-fg-dim" />}
      <span className="min-w-0 flex-1">
        {s.seal
          ? <span className="block truncate text-[12.5px]"><SealWords seal={s.seal} unlock={false} /></span>
          : <span className={`block truncate text-[12.5px] ${kept ? "italic text-muted" : "text-fg-dim"}`}>{kept ? `Title kept on ${s.link}'s machine` : s.title}</span>}
        <span className="block truncate text-[10.5px] text-muted">{[cloud ? "Cloud" : s.link, ran, s.branch].filter(Boolean).join(" · ")}</span>
        {s.inClear && <span className="block"><InClear /></span>}
      </span>
      <span className="flex-none text-[10.5px] text-muted">{s.last_at ? timeAgo(new Date(s.last_at * 1000).toISOString()) : ""}</span>
    </button>
  );
}

function SessionRow({ s, active, grouped, number, onClick, onCloseSession }: { s: Session; active: boolean; grouped: boolean; number?: number; onClick: () => void; onCloseSession?: () => void }) {
  return (
    <div className="group/row relative">
      <button
        onClick={onClick}
        aria-current={active ? "true" : undefined}
        className={`relative flex w-full items-center gap-2 rounded-md py-1.5 pl-6 pr-2 text-left ${
          active ? "bg-active" : "hover:bg-hover"
        }`}
      >
        {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded bg-accent" />}
        <StatusDot state={s.state} />
        <AgentBadge kind={s.agent} name={s.agentName} size={16} />
        <span className="min-w-0 flex-1">
          <span className={`block truncate text-[12.5px] ${active || s.unread ? "text-fg" : "text-fg-dim"} ${s.unread ? "font-semibold" : ""}`}>{s.title}</span>
          {s.state === "waiting" && s.note ? (
            <span className="block truncate text-[11px] text-wait" title={s.note}>{s.note}</span>
          ) : s.branch && (
            <span className="flex items-center gap-1 truncate font-mono text-[10.5px] text-muted">
              {grouped && <Layers size={10} className="flex-none text-accent/70" aria-label="Started with others on the same task" />}
              <span className="truncate">{s.branch}</span>
            </span>
          )}
        </span>
        {/* where its close shows (a pointer over the row, or the keyboard in it), what is said there steps aside */}
        <span className={`flex flex-none flex-col items-end gap-0.5 ${onCloseSession ? "[@media(pointer:fine)]:group-hover/row:invisible [@media(pointer:fine)]:group-has-[:focus-visible]/row:invisible" : ""}`}>
          {number ? <span className="kbd !text-[10.5px] text-fg">⌥{number}</span> : <span className="text-[10.5px] text-muted">{timeAgo(s.updatedAt)}</span>}
          {s.diff && s.diff.files > 0 && (
            <span className="font-mono text-[10px]" title={sizeWords(s.diff)}>
              {sizeShort(s.diff)}
            </span>
          )}
          {!!s.unread && <span className="h-1.5 w-1.5 rounded-full bg-accent" title="Unread: it did something since you last looked" />}
        </span>
      </button>
      {/* its own button beside the row's, never in it; a phone closes a session from its header, the list there being
          a drawer */}
      {onCloseSession && (
        <button className="icon-btn absolute right-1 top-1/2 !h-6 !w-6 -translate-y-1/2 opacity-0 group-hover/row:opacity-100 group-has-[:focus-visible]/row:opacity-100 [@media(pointer:coarse)]:hidden"
          onClick={onCloseSession} aria-label={`Close ${s.title}`}
          title="Close: stop it and take it out of the list. Nothing of it is removed: search (⌘K) finds it, and opening it there puts it back">
          <EyeOff size={13} />
        </button>
      )}
    </div>
  );
}
