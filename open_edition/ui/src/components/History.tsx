import { useState } from "react";
import { Check, Cloud, Copy, FolderGit2, Laptop, Lock, MessagesSquare } from "lucide-react";
import { type HistorySession } from "../api";
import { dayTime, full } from "../when";
import { CLIENT, inForm, LINK_NAME } from "../shellForms";
import { InClear, SealWords, Unlock, useShownRows, type Seal, type ShownRow } from "./Sealed";

/** The command that opens a session again on the machine that listed it, in its folder: Claude Code's own, the folder
 *  and the session's id each quoted. */
export function resumeCommand(s: Pick<HistorySession, "id" | "project">): string {
  const quote = (v: string) => `'${v.replace(/'/g, `'\\''`)}'`;
  const folder = s.project ? (s.project.startsWith("~/") ? `~/${quote(s.project.slice(2))}` : s.project === "~" ? "~" : quote(s.project)) : null;
  return `${folder ? `cd ${folder} && ` : ""}claude --resume ${quote(s.id)}`;
}

/** Where a session ran, as its transcript records it. */
export const CLIENT_WORDS: Record<string, string> = { vscode: "VS Code", terminal: "Terminal", desktop: "Claude app", sdk: "SDK", other: "Other" };

/** How long a machine's word that a session is running, or registered, is shown: its list is sent when a session
 *  starts and at most every five minutes as sessions work, so an older one may no longer be true. */
export const LIVE_FRESH = 15 * 60;

/** Whether a session was running when its machine last listed it, said only while that list is fresh. */
export function liveWord(s: Pick<HistorySession, "live" | "listed_at">, now = Date.now() / 1000): string | null {
  if (!s.live || now - s.listed_at > LIVE_FRESH) return null;
  return s.live === "working" ? "working" : "open";
}

/** Whether a session ran its machine's start hook in the process that runs it, said only while its machine's list is
 *  fresh: one that has ended, or lost its hooks, may still be named so in an older list. */
export function registeredNow(s: Pick<HistorySession, "registered" | "listed_at">, now = Date.now() / 1000): boolean {
  return !!s.registered && now - s.listed_at <= LIVE_FRESH;
}

/** A machine session's repository as the list puts it with the workspace's (the Sidebar's machineRepo, the server's
 *  moves.repository_of): the folder of the repository it worked in first, else its own folder, as that folder's name
 *  and the folder; null where it names none (no folder, or the home folder). A move is held to it. */
export function sessionRepository(s: Pick<HistorySession, "repositories" | "project">): { name: string; folder: string } | null {
  const folder = (s.repositories?.[0] || s.project || "").replace(/\/+$/, "");
  const name = folder.split("/").pop() ?? "";
  return name && name !== "~" ? { name, folder } : null;
}

/** Where a session's newest move into the workspace leaves it, for its row: there (started), or on its way. */
export type Moved = "started" | "moving";

// the group of the sealed rows not open: no repository or folder can be named so, as neither holds a NUL
const ENCRYPTED = "\u0000encrypted";

/** A machine's sessions grouped by repository, newest first: each under the first repository it worked in (its folder
 *  where its machine named none), a group as new as its newest session. A row that came sealed and is not open
 *  (`seal`: the keys are locked, or it cannot be shown) names no repository: those go in a group of their own,
 *  `encrypted`. */
export function byRepository<T extends Pick<HistorySession, "repositories" | "project"> & { seal?: Seal | null }>(sessions: T[]): { repo: string; encrypted: boolean; sessions: T[] }[] {
  const groups = new Map<string, T[]>();
  for (const s of sessions) {
    const repo = s.seal ? ENCRYPTED : s.repositories?.[0] ?? s.project ?? "";
    const group = groups.get(repo);
    if (group) group.push(s);
    else groups.set(repo, [s]);
  }
  return [...groups].map(([repo, ss]) => (repo === ENCRYPTED ? { repo: "", encrypted: true, sessions: ss } : { repo, encrypted: false, sessions: ss }));
}

/** One of a machine's Claude Code sessions: where it ran, whether it is running and registered, its title (kept on its
 *  machine when withheld), folder and branch, and the other repositories it worked in. Open, it offers its
 *  conversation (what its machine sent of it, and its mirrored turns) and the command that opens it again there. A row
 *  that came sealed shows what it opened to, or where it stands in its title's place; one that came in the clear while
 *  encryption is on is marked so. */
export function SessionRow({ s, onRead, moved }: { s: ShownRow; onRead?: (s: HistorySession) => void; moved?: Moved }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const cloud = s.kind === "cloud";
  const command = resumeCommand(s);
  const running = liveWord(s);
  const registered = registeredNow(s);
  const ran = s.client ? CLIENT_WORDS[s.client] ?? null : null;
  const others = (s.repositories ?? []).slice(1);
  const readable = !!(s.talk_at || s.notes) && !!onRead;
  return (
    <li className="rounded-lg border border-line">
      <button className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-hover" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <span className="mt-0.5 flex flex-none flex-col items-start gap-1">
          <span className={`pill ${cloud ? "text-accent" : "text-fg-dim"}`} title={cloud ? "In the cloud" : `On ${s.link}'s machine`}>
            {cloud ? <Cloud size={11} /> : <Laptop size={11} />} {cloud ? "Cloud" : "Local"} · {s.link}
          </span>
          {ran && <span className="pill text-muted" title="Where it ran">{ran}</span>}
        </span>
        <span className="min-w-0 flex-1">
          {s.seal
            ? <span className="block truncate text-[12.5px]"><SealWords seal={s.seal} unlock={false} /></span>
            : s.withheld || s.title === null
              ? <span className="block truncate text-[12.5px] italic text-muted">Title kept on {s.link}'s machine</span>
              : <span className="block truncate text-[12.5px] font-medium text-fg">{s.title}</span>}
          <span className="block truncate text-[11px] text-muted">{[s.project, s.branch].filter(Boolean).join(" · ")}</span>
          {others.length > 0 && <span className="block truncate text-[11px] text-muted" title="The other repositories it worked in">also in {others.join(", ")}</span>}
          {s.inClear && <span className="mt-0.5 block"><InClear /></span>}
        </span>
        <span className="flex flex-none flex-col items-end gap-1 text-[11px] text-muted">
          <span title={s.last_at ? full(s.last_at) : ""}>{s.last_at ? dayTime(s.last_at) : ""}</span>
          {running && (
            <span className={`flex items-center gap-1 ${running === "working" ? "text-run" : "text-fg-dim"}`}
              title={`Running when ${s.link}'s machine last listed its sessions, ${dayTime(s.listed_at)}`}>
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${running === "working" ? "bg-run" : "bg-idle"}`} /> {running}
            </span>
          )}
          {registered && (
            <span className="text-accent" title={`It ran ${s.link}'s start hook in the process that runs it, as of ${dayTime(s.listed_at)}: its turns are mirrored under it`}>registered</span>
          )}
          {moved && (
            <span className={`pill ${moved === "started" ? "text-done" : "text-wait"}`}
              title={moved === "started" ? "Moved into your workspace: it continues there" : "Its move into your workspace is under way"}>
              {moved === "started" ? "In the workspace" : "Moving"}
            </span>
          )}
        </span>
      </button>
      {open && (
        <div className="border-t border-line px-3 py-2 text-[11.5px] text-fg-dim">
          {readable ? (
            <button className="btn btn-sm mb-2" onClick={() => onRead?.(s.raw)}><MessagesSquare size={13} /> Read the conversation</button>
          ) : (
            <div className="mb-1">
              {s.withheld
                ? `It touched a project ${s.link}'s history.json excepts, so its title and conversation stay on that machine.`
                : `${cloud ? "In the cloud" : `On ${s.link}'s machine`}${s.started_at ? `, started ${dayTime(s.started_at)}` : ""}. Its conversation is not sent here: to send it, `
                  + `add "conversations": true to ~/.proofpane-ade/${s.link}/history.json on ${s.link}'s machine.`}
            </div>
          )}
          <div>To open it again there:</div>
          <div className="mt-1 flex items-start gap-1.5">
            <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2 py-1 font-mono text-[11px] text-fg">{command}</pre>
            <button className="icon-btn !h-6 !w-6 flex-none" title="Copy" aria-label="Copy the command"
              onClick={() => { void navigator.clipboard?.writeText(command); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

/** The line that has a machine list its Claude Code sessions here, run on that machine: a history.json in its link's
 *  folder naming every project (its titles and folders; "conversations": true sends their conversations too, and
 *  "except" keeps folders off it), then the History list sent at once. Null when the link's name is not in the form
 *  link.py holds a name to, which has no quote or $ (shellForms): no line is given then. */
export function listLine(link: string): string | null {
  if (!inForm(LINK_NAME, link)) return null;
  const dir = `"$HOME/.proofpane-ade/${link}"`;
  return `printf '%s\\n' '{"projects": "all"}' > ${dir}/history.json && ${CLIENT} --dir ${dir} history`;
}

/** A machine's Claude Code sessions, newest first, grouped by repository (those that came sealed and are not open
 *  under "Encrypted", with Unlock while the keys are locked); or, where none is listed, how to list them, in place:
 *  `link`, the machine's link, whose folder the history.json goes in, and `cloud`, a session in the cloud. */
export function MachineSessions({ sessions, onRead, moved, link, cloud = false }: {
  sessions: HistorySession[]; onRead?: (s: HistorySession) => void; moved?: ReadonlyMap<string, Moved>; link?: string; cloud?: boolean;
}) {
  const shown = useShownRows(sessions);
  const [copied, setCopied] = useState(false);
  if (!sessions.length) {
    if (cloud) {
      return (
        <div className="px-1 text-[11.5px] text-muted">
          Your other claude.ai sessions are not listed here: claude.ai lists them. This one&apos;s conversation is under
          Open conversation, above.
        </div>
      );
    }
    const line = link ? listLine(link) : null;
    return (
      <div className="px-1 text-[11.5px] text-muted">
        <div>
          This machine&apos;s Claude Code sessions, from VS Code, the terminal and the Claude app, are listed here once its
          link&apos;s folder holds a history.json naming the projects to list.{line && <> To list them all, run this on {link}&apos;s machine, then Read again:</>}
        </div>
        {line && (
          <div className="mt-1 flex items-start gap-1.5">
            <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-bg px-2 py-1 font-mono text-[11px] text-fg">{line}</pre>
            <button className="icon-btn !h-6 !w-6 flex-none" title="Copy" aria-label="Copy the command that lists them"
              onClick={() => { void navigator.clipboard?.writeText(line); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
        )}
        {line && <div className="mt-1">To send their conversations too, put &quot;conversations&quot;: true beside &quot;projects&quot;; to keep folders off the list, name them in &quot;except&quot;.</div>}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {byRepository(shown).map((g) => (
        <div key={g.encrypted ? ENCRYPTED : g.repo}>
          {g.encrypted ? (
            <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted" title="Sessions whose machine sent their titles and folders encrypted">
              <Lock size={11} /> <span>Encrypted</span> <Unlock />
            </div>
          ) : (
            <div className="mb-1 flex items-center gap-1.5 px-1 text-[11px] text-muted" title="The repository its sessions worked in first">
              <FolderGit2 size={11} /> <span className="truncate">{g.repo || "No folder"}</span>
            </div>
          )}
          <ul className="space-y-1">
            {g.sessions.map((s) => <SessionRow key={`${s.link_id}/${s.id}`} s={s} onRead={onRead} moved={moved?.get(s.id)} />)}
          </ul>
        </div>
      ))}
    </div>
  );
}
